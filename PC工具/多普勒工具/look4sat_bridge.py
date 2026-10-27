#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让 Look4Sat 直接控我们的电台（不改固件、不搞蓝牙）。

原理
----
Look4Sat 4.4.9 的「设置 → 数据输出 → 网络」是一个 **TCP 客户端**：
它把每帧算好的绝对频率按模板（默认 `F $FREQ`）拼成一行，末尾补 `\\n`，
连到用户填的 `IP:端口` 上发出去（`NetworkReporter.kt:75-85`，格式默认值在
`SettingsRepo.kt:333`）。它本来是用来喂 rigctld / Hamlib 的，而 rigctl 的
"设频率"命令恰好就是这个 `F <hz>`。

于是只要在能碰到电台的那台机器上跑一个假的 rigctl 服务端：收 `F <hz>`，
翻译成我们自己的 `0x0768`（接管）+ `0x076C`（推帧），电台就跟上了。

用法
----
    # 电台在 COM7（USB 直连那边必须先关掉，串口只能一个进程开）
    python look4sat_bridge.py --port COM7

    # 或者复用已经在跑的 serial_bridge（它守着 7878）
    python look4sat_bridge.py --tcp 127.0.0.1:7878

Look4Sat 那边：
    设置 → 数据输出 → 网络 → 打开「启用多普勒频率输出」
    IP:端口    = <本机 IP>:7890        （MuMu 模拟器里填 10.0.2.2:7890）
    数据格式   = F $FREQ               （默认值，别改）

限制（写清楚免得误判）
----
* Look4Sat 只发"下行绝对频率"这一个数：没有上行、没有仰角、没有卫星名。
  ``tx10`` 我们固定填 0 —— 固件那边"接管了但没上行"会退回表内上行，
  也就是**接收跟着 Look4Sat 走，发射仍是电台自己的那套**。
* 固件的接管看门狗是 5 s：手机一停推就交还。Look4Sat 只在频率变化时发
  （`Channel(CONFLATED)`），过境里频率一直在变没问题，但停下来看星星时
  它就不发了 —— 所以这里每 1 s 补发一次最后一帧保活。
* 一次只能有一个客户端。退出（或客户端断开）时发 `0x0768 off` 交还电台。
"""

import argparse
import socket
import sys
import threading
import time

import radio_proto as rp

EXT_PAGE = 0x076E          # 第二套逻辑页开关
TAKEOVER_ON = 0x0768
PUSH_EXT = 0x076C

KEEPALIVE_S = 1.0          # 固件看门狗 5 s，1 s 补一帧足够
MAX_HZ = 1300 * 1000 * 1000


def log(*a):
    print("[l4s]", *a, flush=True)


def parse_hz(line: str):
    """从 `F 437800123` 里取频率。别的命令返回 None（我们不是真的 rigctld）。"""
    line = line.strip()
    if not line:
        return None
    parts = line.split()
    if len(parts) == 2 and parts[0].upper() in ("F", "FA"):
        v = parts[1].rstrip(";")
        if v.isdigit():
            hz = int(v)
            if 0 < hz < MAX_HZ:
                return hz
    return None


class Radio:
    """串口或 TCP 二选一，对外只暴露 send_request(cmd, payload) -> 回包字节。"""

    def __init__(self, port=None, baud=38400, tcp=None):
        self._lock = threading.Lock()
        self._buf = bytearray()
        self._tcp = None
        self._ser = None

        if tcp:
            host, _, p = tcp.partition(":")
            self._tcp = socket.create_connection((host, int(p or 7878)), timeout=3)
            self._tcp.settimeout(0.4)
            log("电台链路：TCP", tcp)
        else:
            import serial  # 只有走串口时才需要 pyserial

            self._ser = serial.Serial(port, baud, timeout=0.4)
            log("电台链路：串口 %s @ %d" % (port, baud))

    def send_request(self, cmd, payload=b"", timeout=0.6):
        frame = rp.request_bytes(cmd, payload)
        with self._lock:
            if self._ser is not None:
                self._ser.reset_input_buffer()
                self._ser.write(frame)
                self._ser.flush()
            else:
                self._tcp.sendall(frame)

            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._ser is not None:
                    chunk = self._ser.read(256)
                else:
                    try:
                        chunk = self._tcp.recv(256)
                    except socket.timeout:
                        chunk = b""
                if chunk:
                    self._buf += chunk
                    replies, self._buf = rp.parse_replies(self._buf)
                    for r in replies:
                        if r is not None and r.cmd == cmd + 1:
                            return r.payload
        return None

    def close(self):
        try:
            if self._ser is not None:
                self._ser.close()
            if self._tcp is not None:
                self._tcp.close()
        except Exception:
            pass


class Pusher:
    """把"一个频率"变成固件认识的推帧。"""

    NAME = "L4S"

    def __init__(self, radio):
        self.radio = radio
        self.owned = False
        self.n_sent = 0
        self.last_hz = 0
        self.last_sent = 0.0

    def take(self):
        if self.owned:
            return
        # 必须先开页再接管。实测（_tmp_verify/probe_ext2.py，真机 COM7）：
        #   只发 0x0768     → 回包 01，但 200 ms 后 0x076A 已经变回 state 00
        #   先 0x076E=1 再发 → 回包 11，随后稳定 state 15（页|表|接管）、phase 5
        # 原因在固件 Tick10ms 里：ext_ctrl 为真但 ext_page_on 为假时，"手机接管
        # 了却什么都没发生"那条防呆路径会把控制权收回去。App 每次接管都带页，
        # 所以一直没暴露；桥是裸发命令，先踩了这个坑。
        p = self.radio.send_request(EXT_PAGE, bytes([1]))
        r = self.radio.send_request(TAKEOVER_ON, bytes([1]))
        self.owned = True
        log("已接管电台", "(页 %s / 接管 %s)" % (
            p.hex() if p else "无", r.hex() if r else "无"))

    def release(self):
        if not self.owned:
            return
        self.radio.send_request(TAKEOVER_ON, bytes([0]))
        self.radio.send_request(EXT_PAGE, bytes([0]))
        self.owned = False
        log("已交还电台")

    def frame(self, hz):
        """推一帧。只带接收频率，其他字段填 0/1（bit0 = 数据有效）。"""
        rx10 = hz // 10
        payload = bytearray()
        payload += int(rx10 & 0xFFFFFFFF).to_bytes(4, "little")   # rx10
        payload += (0).to_bytes(4, "little")                      # tx10：没有上行
        payload += (0).to_bytes(2, "little", signed=True)         # dopp
        payload += (0).to_bytes(2, "little")                      # dist
        payload += (-1).to_bytes(2, "little", signed=True)        # remain
        payload += (0).to_bytes(2, "little", signed=True)         # el01
        payload += (0).to_bytes(2, "little", signed=True)         # rate01
        payload += bytes([0, 1])                                  # progress, flags
        name = self.NAME.encode()
        payload += bytes([len(name)]) + name
        if len(payload) < 28:
            payload += bytes(28 - len(payload))
        r = self.radio.send_request(PUSH_EXT, bytes(payload), timeout=0.4)
        self.last_sent = time.time()
        if self.n_sent < 3:
            self.n_sent += 1
            log("帧 → %.5f MHz，回包 %s" % (hz / 1e6, r.hex() if r else "无"))

    def set_hz(self, hz):
        self.take()
        self.last_hz = hz
        self.frame(hz)

    def tick(self):
        """保活：Look4Sat 只在频率变化时发，但固件 5 s 不见帧就交还。"""
        if not self.owned or not self.last_hz:
            return
        if time.time() - self.last_sent >= KEEPALIVE_S:
            self.frame(self.last_hz)


def serve(radio, host, port):
    pusher = Pusher(radio)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    srv.settimeout(0.5)
    log("监听 %s:%d —— 在 Look4Sat 里把「网络输出 → 多普勒频率」指到这里" % (host, port))

    conn = None
    buf = b""
    while True:
        if conn is None:
            try:
                conn, peer = srv.accept()
                conn.settimeout(0.3)
                buf = b""
                log("Look4Sat 连入", peer)
            except socket.timeout:
                conn = None
        if conn is not None:
            try:
                chunk = conn.recv(256)
                if not chunk:
                    log("客户端断开")
                    pusher.release()
                    conn.close()
                    conn = None
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", "replace")
                    hz = parse_hz(text)
                    if hz is None:
                        log("忽略：%r" % text)
                        continue
                    pusher.set_hz(hz)
                    log("→ %.5f MHz" % (hz / 1e6))
            except socket.timeout:
                pass
            except OSError as e:
                log("连接出错：%s" % e)
                pusher.release()
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
        pusher.tick()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="串口，例如 COM7")
    ap.add_argument("--baud", type=int, default=38400)
    ap.add_argument("--tcp", help="复用串口桥：host:port，例如 127.0.0.1:7878")
    ap.add_argument("--listen", default="0.0.0.0:7890")
    a = ap.parse_args()

    if not a.port and not a.tcp:
        ap.error("要么给 --port（直连电台），要么给 --tcp（复用 serial_bridge）")

    host, _, p = a.listen.partition(":")
    radio = Radio(port=a.port, baud=a.baud, tcp=a.tcp)
    try:
        serve(radio, host, int(p or 7890))
    except KeyboardInterrupt:
        print()
    finally:
        radio.close()


if __name__ == "__main__":
    sys.exit(main())
