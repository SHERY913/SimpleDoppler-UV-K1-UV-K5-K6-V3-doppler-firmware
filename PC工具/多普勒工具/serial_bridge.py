"""串口 <-> TCP 桥。

用途：让模拟器里的安卓 App 也能测 USB 协议，不必等 OTG 线。
App 连 127.0.0.1:7878（配合 `adb reverse tcp:7878 tcp:7878`），
这里把这路 TCP 原样转发到串口。

    python serial_bridge.py --port COM7 --listen 7878
    python serial_bridge.py --port auto --adb     # 模拟器：口和隧道都自己管

加 `--adb` 后桥自己维持 adb reverse，每 3 分钟补一次。非加不可：隧道不是
持久设置，adb 服务一重启或模拟器一冷启动它就没了，而且哪儿都不报错，
App 那边只表现为"连不上 127.0.0.1:7878（连接被拒绝）"。

协议是无状态的字节流，桥上不做任何解析，丢包/时序与原样一致。

为什么要有重连：手台发射的瞬间，USB 转串口那头的电源会被拉一下，
便宜芯片会掉线（Windows 上表现为设备消失 / SerialException）。
以前一掉线就 `client gone`，App 那边看到的正好是"一发射连接就断"。
现在串口掉了会自己重开，TCP 不动，App 不用重连。
"""
import argparse
import os
import socket
import subprocess
import sys
import threading
import time

import serial


class Port:
    """串口句柄 + 自愈。所有访问都过 lock：读线程写线程并发。"""

    def __init__(self, name: str, baud: int):
        self.name = name
        self.baud = baud
        self.lock = threading.RLock()
        self.ser = None
        self.drops = 0
        self.open()

    def open(self) -> bool:
        try:
            self.ser = serial.Serial(self.name, self.baud, timeout=0.01, write_timeout=0.5)
            print("[bridge] %s @ %d 已打开" % (self.name, self.baud), flush=True)
            return True
        except Exception as e:
            self.ser = None
            # 刚刷完固件最容易碰到这两种：设备先消失（FileNotFound）再回来被
            # 刷机工具/别的串口程序独占（PermissionError）。把话说清楚，
            # 否则用户只看到一屏英文异常，不知道该关哪个程序。
            hint = ""
            if isinstance(e, PermissionError):
                hint = "（端口被别的程序独占：关掉刷机工具/串口助手，或拔插一次 USB）"
            elif isinstance(e, FileNotFoundError):
                hint = "（串口不存在：手台是不是还在重启/线松了）"
            print("[bridge] 打不开 %s: %s%s" % (self.name, e, hint), flush=True)
            return False

    def dead(self, why: str) -> None:
        """串口没了：关掉句柄，标记为 None，之后由 ensure() 重开。"""
        with self.lock:
            self.drops += 1
            print("[bridge] 串口掉线 #%d（%s）" % (self.drops, why), flush=True)
            try:
                if self.ser is not None:
                    self.ser.close()
            except Exception:
                pass
            self.ser = None

    def ensure(self, tries: int = 60) -> bool:
        """重开串口，最多等 tries*200 ms（默认 12 s；刷完固件走 300 次 = 60 s）。

        手台发射瞬间 USB 掉线多半会重枚举：同一个物理口换一个 COM 号回来，
        光按原名重开永远失败。所以每 20 次尝试就扫一遍端口，认 VID 36B7
        或描述里带 USB 串行的口，跳过 MuMu 那几个虚拟口。
        """
        for i in range(tries):
            with self.lock:
                if self.ser is not None:
                    return True
                if self.open():
                    return True
            if i and i % 20 == 0:
                self.rescan()
            time.sleep(0.2)
        return False

    def rescan(self) -> bool:
        try:
            from serial.tools import list_ports
        except Exception:
            return False
        for p in list_ports.comports():
            desc = "%s %s" % (p.description or "", p.hwid or "")
            low = desc.lower()
            if "mumu" in low or "16384" in low or "127.0.0.1" in low:
                continue
            if p.device == self.name:
                continue
            if ("36b7" in low) or ("usb" in low and ("串行" in desc or "serial" in low)):
                print("[bridge] 端口号变了，试 %s（%s）" % (p.device, desc.strip()), flush=True)
                self.name = p.device
                if self.open():
                    return True
        return False

    def read_some(self):
        with self.lock:
            s = self.ser
        if s is None:
            raise OSError("port closed")
        # 有多少读多少：pyserial 的 read(n) 会等凑够 n 字节，一个 13 字节回包
        # 要干等满 timeout 才转发，一问一答的协议会被拖成 50 ms/帧。
        n = s.in_waiting
        return s.read(n if n else 1)

    def write(self, data: bytes) -> None:
        with self.lock:
            s = self.ser
            if s is None:
                raise OSError("port closed")
            s.write(data)
            s.flush()


def pump_from_port(port: Port, conn: socket.socket, stop: threading.Event, log: bool) -> None:
    while not stop.is_set():
        try:
            data = port.read_some()
        except (serial.SerialTimeoutException,):
            continue
        except OSError as e:
            port.dead(str(e))
            if not port.ensure(300):
                print("[bridge] 串口没能回来，断开这个 client", flush=True)
                stop.set()
                return
            continue
        if not data:
            continue
        if log:
            print("radio->app %d %s" % (len(data), data.hex(" ")), flush=True)
        try:
            conn.sendall(data)
        except OSError:
            stop.set()
            return


def pump_from_tcp(port: Port, conn: socket.socket, stop: threading.Event, log: bool) -> None:
    while not stop.is_set():
        try:
            data = conn.recv(4096)
        except socket.timeout:
            continue
        except OSError:
            stop.set()
            return
        if not data:
            stop.set()
            return
        if log:
            print("app->radio %d %s" % (len(data), data.hex(" ")), flush=True)
        try:
            port.write(data)
        except (serial.SerialTimeoutException,):
            continue
        except OSError as e:
            port.dead(str(e))
            if not port.ensure(300):
                print("[bridge] 串口没能回来，断开这个 client", flush=True)
                stop.set()
                return


def resolve_adb(explicit: str) -> str:
    """adb 只认一个，别混用。

    混用两个 adb.exe 本身不会踢掉服务（实测 37.0.1 与 36.0.0 共存没问题），
    但 reverse 是记在"某一台 adb 服务"上的：脚本用 A 建隧道、另一个工具用 B
    起了第二个服务或 kill-server，隧道就没了，App 侧只看得见一句
    "连不上 127.0.0.1:7878"。所以这里固定选一个，并把隧道当成长期要维持的东西。
    """
    if explicit:
        return explicit if os.path.exists(explicit) else ""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    for cand in (
        os.path.join(root, "tc", "sdk", "platform-tools", "adb.exe"),
        r"C:\Program Files\Netease\MuMu\nx_main\adb.exe",
    ):
        if os.path.exists(cand):
            return cand
    return ""


def tunnel_keeper(adb: str, port: int) -> None:
    """每 3 分钟补一次 adb reverse。

    隧道在两种情况下会静悄悄消失：adb 服务被重启（另一个工具 kill-server、
    版本不一致、IDE 插手），或模拟器冷启动。两种都不报错，App 只会一直
    "连接被拒绝"，而桥接窗口上看不出任何异常 —— 这正是"昨天还能连"的来源。
    补做是幂等的，所以直接轮询。
    """
    known = None
    while True:
        alive = []
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                                 timeout=10, creationflags=flags()).stdout
            for line in out.splitlines()[1:]:
                f = line.split()
                if len(f) >= 2 and f[1] == "device":
                    subprocess.run([adb, "-s", f[0], "reverse",
                                    "tcp:%d" % port, "tcp:%d" % port],
                                   capture_output=True, timeout=10,
                                   creationflags=flags())
                    alive.append(f[0])
            if not alive:
                # MuMu 的 adb 端口（16384）；有的版本只认这个,不认 emulator-5554。
                subprocess.run([adb, "connect", "127.0.0.1:16384"],
                               capture_output=True, timeout=10, creationflags=flags())
        except Exception as e:
            print("[bridge] adb reverse 补做失败：%s" % e, flush=True)
        if alive != known:
            print("[bridge] 隧道 tcp:%d -> %s：%s" % (
                port, adb, " ".join(alive) if alive else "无设备"), flush=True)
            known = alive
        time.sleep(180)


def flags():
    # 别给辅助调用弹控制台窗口
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def serve(args):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows 上 SO_REUSEADDR 不是"允许 TIME_WAIT 复用"，而是"允许别人抢占同一个端口"：
    # 之前一个测试用的假电台进程占着 7878，桥接照样 bind 成功，于是 App 一半请求被假电台
    # 吃掉（只回 0x076A），表现就是"电台没有响应"。Windows 用独占语义，冲突要立刻炸出来。
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((args.listen_host, args.listen))
    except OSError as e:
        print("端口 %s:%d 已被占用（%s）。先关掉占它的程序：多普勒工具、另一个桥接窗口、"
              "串口助手、或残留的测试脚本。" % (args.listen_host, args.listen, e), flush=True)
        raise SystemExit(2)
    srv.listen(1)
    print("bridge listening on %s:%d -> %s @ %d" % (
        args.listen_host, args.listen, args.port, args.baud), flush=True)

    while True:
        conn, addr = srv.accept()
        conn.settimeout(0.05)
        # Nagle 会把"小包 + 等对端 ACK"叠成 40 ms 级延迟，一问一答的协议最吃亏。
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        print("client %s" % (addr,), flush=True)

        port = Port(args.port, args.baud)
        if port.ser is None:
            conn.close()
            continue

        stop = threading.Event()
        t1 = threading.Thread(target=pump_from_tcp,
                              args=(port, conn, stop, args.log), daemon=True)
        t2 = threading.Thread(target=pump_from_port,
                              args=(port, conn, stop, args.log), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        stop.set()
        try:
            port.ser.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
        print("client gone（串口掉线 %d 次）" % port.drops, flush=True)


def resolve_port(name):
    if name and name.lower() != "auto":
        return name
    import find_port
    p = find_port.pick()
    if p is None:
        print("[bridge] 没有可用的真串口：MuMu 把设备抢走了。")
        for c in find_port.candidates():
            print(f"[bridge]   {c['device']}  {c['why']}  {c['desc']}")
        raise SystemExit(2)
    print(f"[bridge] 自动选中 {p}")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="auto", help="COMx 或 auto（自动跳过模拟器虚拟口）")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--listen", type=int, default=7878)
    ap.add_argument("--listen-host", default="127.0.0.1")
    ap.add_argument("--log", action="store_true", help="把两个方向的字节流打成 hex，用来确认 App 到底发了什么")
    ap.add_argument("--adb", default="", nargs="?", const="auto",
                    help="顺手维持 adb reverse（模拟器用）。不给路径就自己找，且只用这一个 adb")
    a = ap.parse_args()
    a.port = resolve_port(a.port)
    if a.adb:
        exe = resolve_adb("" if a.adb == "auto" else a.adb)
        if not exe:
            print("[bridge] 找不到 adb，跳过隧道维持；模拟器需要先自己 adb reverse", flush=True)
        else:
            threading.Thread(target=tunnel_keeper, args=(exe, a.listen),
                             daemon=True).start()
    serve(a)


if __name__ == "__main__":
    main()
