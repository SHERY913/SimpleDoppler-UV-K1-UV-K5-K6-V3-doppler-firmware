#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线假电台 —— 固件 UART 协议的设备侧参考模型。

每条回包的字节布局、每个状态位、每次判死都对着 `App/app/uart.c`、
`App/app/doppler.c` 逐行抄，出处写在对应代码行的注释里（行号是 2026-10-27
的 `737ed3a`）。两个用途：

  1. 没有电台硬件时，把「App → 网络桥 → TCP 7878」整条链路跑起来；
  2. 给 `verify_radio.py` 当被测对象，覆盖
     接管 0x0768 → 推帧 0x076C → PTT → 交还 → 看门狗 这条四层网盖不住的路径。

跑法：
    python fakeradio.py                       # 数据口 7878，控制口 7879
    python fakeradio.py --events ev.jsonl     # 事件流落盘，测试按它断言
    python fakeradio.py --watchdog-ms 800     # 只验机制、不验 5 s 时长

控制口是行协议（一条一行，回车换行），可以 telnet 上去手点：
    ptt 1|0        按下/松开 PTT          tx 1|0        强制发射态
    arm 1|0        武装自动启动           table 1|0     有没有过境表
    phase <n>      直接改相位             flash 1|0     强制"正在刷写"窗口
    stall <ms>     下一条回包延迟         no-reply <hexcmd>  该命令不再回包
    watchdog <ms>  运行中改看门狗         hang <n>      第 n 条命令后整条链路僵死
    drop           断开与客户端的连接（模拟拔线 / App 被杀）
    state          打印当前状态           quit          退出
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time

# ── 协议层：与 uart.c 的 Header_t/Footer_t、RadioLink.kt 的 RadioProto 一致 ──
MASK = [0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
        0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80]


def crc16(data: bytes) -> int:
    """poly 0x1021 / init 0 / MSB-first / 无反射（driver/crc.c:24-47）。"""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def mask_xor(data: bytes) -> bytes:
    return bytes(b ^ MASK[i % 16] for i, b in enumerate(data))


def encode_reply(msg: bytes) -> bytes:
    """回包 = AB CD len(2 LE) | 掩码(msg) | 掩码(FF FF) | DC BA。

    uart.c:302-303 / :296-299（VCP 同一段）：固件对回包**根本不计算 CRC**，
    那两字节恒是 `Obfuscation[(Size+i)%16] ^ 0xFF`。App 侧 RadioLink.kt:129-136
    就是照这个实测结果放过它的。模型要是不一样，就等于测了个假电台。
    """
    n = len(msg)
    body = mask_xor(msg + bytes([0xFF, 0xFF]))
    return bytes([0xAB, 0xCD, n & 0xFF, n >> 8]) + body + bytes([0xDC, 0xBA])


def msg(cmd: int, payload: bytes = b"") -> bytes:
    return bytes([cmd & 0xFF, cmd >> 8, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF]) + payload


class Request:
    """一条已解掩的请求帧。uart.c:748-892 的接收状态机在流上重放。"""

    def __init__(self, cmd: int, paylen: int, payload: bytes):
        self.cmd = cmd
        self.paylen = paylen
        # uart.c:1427 把 paylen 当 uint8 用，>255 时按截断处理
        self.payload = payload[:paylen & 0xFF] if paylen > 255 else payload[:paylen]


def drain_frames(buf: bytearray) -> tuple[list[Request | None], bytearray]:
    """返回 (完整帧列表, 剩余字节)。坏帧按固件处理：静默丢弃，不回错误码。

    None 表示"消费掉了一段坏帧"（CRC 不符 / 长度不合理），模型用它验证
    "客户端发的东西到底有没有被电台认下来"。
    """
    out: list[Request | None] = []
    while True:
        i = buf.find(0xAB)
        while i >= 0 and i + 1 < len(buf) and buf[i + 1] != 0xCD:
            i = buf.find(0xAB, i + 1)
        if i < 0:
            # 整窗找不到 AB：固件把读指针同步到末尾，积压丢掉（uart.c:800-805）
            return out, bytearray(buf[-1:] if buf and buf[-1] == 0xAB else b"")
        if i + 4 > len(buf):
            return out, buf[i:]
        size = buf[i + 2] | (buf[i + 3] << 8)
        if size < 4 or size > 252:                       # uart.c:834 Size+8>256 丢
            return out, buf[i + 4:]
        need = size + 8
        if i + need > len(buf):
            return out, buf[i:]                          # 帧没到齐，留着等下一包
        body = mask_xor(bytes(buf[i + 4:i + 4 + size + 2]))
        foot = buf[i + 4 + size + 2:i + need]
        if list(foot) != [0xDC, 0xBA]:                   # uart.c:846-850 脚标不符→丢
            return out, buf[i + 4:]
        crc_rx = body[size] | (body[size + 1] << 8)
        if crc_rx != crc16(body[:size]):
            out.append(None)
            return out, buf[i + need:]
        m = body[:size]
        paylen = m[2] | (m[3] << 8)
        if size < 4 or paylen > size - 4:                # uart.c:889-890 越界
            out.append(None)
            return out, buf[i + need:]
        out.append(Request(m[0] | (m[1] << 8), paylen, m[4:4 + paylen]))
        buf = bytearray(buf[i + need:])


# ── 状态机：doppler.c 的第二套逻辑 ──
OFF, READY, RUN, DONE, EMPTY, EXT = 0, 1, 2, 3, 4, 5
PHASE_NAMES = ["OFF", "READY", "RUN", "DONE", "EMPTY", "EXT"]
REGION_START, REGION_SIZE = 0x162000, 0x9D000     # doppler.h:20-25
FREQ_MIN10, FREQ_MAX10 = 2_900_000, 130_000_000   # doppler.c:932（单位 10 Hz）


class Model:
    def __init__(self, opt):
        self.opt = opt
        self.t0 = time.monotonic()
        self.table_ok = opt.table
        self.auto_armed = False
        self.ext_page_on = False
        self.ext_ctrl = False
        self.was_on = False               # doppler.c:3381 那个 static bool
        self.phase = opt.phase
        self.tx_hold = False          # PTT 按着
        self.transmit = False         # 芯片真在发（gCurrentFunction）
        self.flash_until = 0.0        # SerialFlashInProgress 的 6 s 窗口
        self.last_rx = self.t0        # 任何串口流量：SerialConfigInProgress
        self.hb_tick = self.t0        # ext_ctrl_tick
        self.frame = None
        self.flash = bytearray(b"\xFF" * REGION_SIZE)   # 表区镜像，擦过的地方才是 0xFF
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self.reply_stall_ms = 0
        self.no_reply: set[int] = set()
        self.hang_after = None
        self.cmd_count = 0
        self.client = None
        self.emit("boot", proto=self.proto, phase=self.phase, table=self.table_ok)

    @property
    def proto(self) -> int:
        return 6 if self.opt.ext_page else 2       # doppler.c:3185 DPH_EXT_PAGE

    def now(self) -> float:
        return time.monotonic()

    def emit(self, ev: str, **kv):
        rec = {"t_ms": round((self.now() - self.t0) * 1000), "ev": ev}
        rec.update(kv)
        with self.lock:
            self.events.append(rec)
        if self.opt.events:
            with open(self.opt.events, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # 状态位表：doppler.c:3188-3210 DOPPLER_ExtState() 逐位对齐
    def state(self) -> int:
        f = 0
        if self.ext_ctrl:
            f |= 0x01
        if self.phase == RUN or (self.phase == EXT and self.frame and (self.frame["flags"] & 0x02)):
            f |= 0x02
        if self.table_ok:
            f |= 0x04
        if self.auto_armed:
            f |= 0x08
        if self.ext_page_on:
            f |= 0x10
        if self.tx_hold:
            f |= 0x20
        if self.transmit:
            f |= 0x40
        if self.now() < self.flash_until:
            f |= 0x80
        return f

    # ── 交还 / 接管：doppler.c:3378-3420 ──
    def set_ext_control(self, on: bool, why: str = "cmd") -> bool:
        """固件这段没有"状态没变就直接返回"的早退，别自作聪明加。

        `released = !on && was_on` 用的是上一次调用留下的静态 `was_on`
        （:3381/:3386/:3412），不是当前 `ext_ctrl`。App 收尾要先关页再交还，
        两次都走进来；靠这个判定，第二次才不会把刚恢复好的操作员信道又调走。
        """
        released = (not on and self.was_on)
        changed = self.ext_ctrl != on
        if released:
            # :3395 只看此刻的 phase，跟 ext_page_on 已经被清成假没关系
            if self.phase == EXT:
                # :3396-3405 交还时不弹 73 页，直接回 READY（有表）或 EMPTY
                self.phase = READY if self.table_ok else EMPTY
        self.was_on = on
        self.ext_ctrl = on
        self.hb_tick = self.now()          # :3414 无论开关都刷心跳
        if on and self.ext_page_on and self.phase in (READY, RUN, DONE, EMPTY):
            self.phase = EXT               # :3429-3437 接管即切页，音频打开
        if changed:
            self.emit("ext_ctrl", on=on, why=why, phase=PHASE_NAMES[self.phase],
                      state=self.state())
        return changed

    def set_page(self, on: bool) -> bool:
        self.ext_page_on = on
        # :3166-3179 关页时如果正停在 EXT 页，连控制权一起交还
        if not on and self.phase == EXT:
            self.set_ext_control(False, why="page-off")
        self.emit("ext_page", on=on, state=self.state())
        return on

    # ── 看门狗：doppler.c:1650-1670 ──
    def tick(self):
        if not self.ext_ctrl:
            return
        silent = (self.now() - self.hb_tick) * 1000.0 > self.opt.watchdog_ms
        gone = (self.now() - self.last_rx) * 1000.0 > self.opt.serial_gone_ms
        if not (silent or gone):
            return
        if self.transmit:
            # 发射中只收功率不动页面：QSO 到一半换成结束页，操作员只会认定
            # "一发射就掉线"。下一拍还不有心跳才真的交还。
            self.transmit = False
            self.emit("tx_stopped", why="watchdog", silent_ms=round((self.now() - self.hb_tick) * 1000))
            return
        self.set_ext_control(False, why="watchdog-silent" if silent else "watchdog-gone")
        self.emit("beep", name="DOUBLE_BEEP")

    # ── 命令处理：uart.c:1315-1475 的十条 case ──
    def handle(self, rq: Request) -> bytes | None:
        self.cmd_count += 1
        if self.hang_after is not None and self.cmd_count > self.hang_after:
            self.emit("hang", after_cmd=self.cmd_count)
            raise Hang()
        c = rq.cmd
        if c == 0x0602:                       # 写 BK4819 寄存器，无回包
            if len(rq.payload) < 3 or rq.payload[0] > 0x7F:
                return None                   # uart.c:739-741 地址越界直接丢
            if self.ext_ctrl:
                self.hb_tick = self.now()     # :742 DOPPLER_ExtHeartbeat()
            return None
        if c == 0x0760:                       # 擦扇区 → 0x0761（9 B）
            addr = int.from_bytes(rq.payload[:4], "little")
            ok = (addr % 4096 == 0 and REGION_START <= addr < REGION_START + REGION_SIZE
                  and rq.paylen == 4)
            if ok:
                for o in range(addr - REGION_START, addr - REGION_START + 4096):
                    if o < len(self.flash):
                        self.flash[o] = 0xFF
                self.flash_until = self.now() + self.opt.flash_ms / 1000.0   # :1319
            self.emit("erase", addr=hex(addr), ok=ok)
            return msg(0x0761, int.to_bytes(addr, 4, "little") + bytes([0 if ok else 1]))
        if c == 0x0762:                       # 写一块 → 0x0763（11 B，status 在 r[10]）
            addr = int.from_bytes(rq.payload[:4], "little")
            ln = int.from_bytes(rq.payload[4:6], "little")
            data = rq.payload[6:6 + ln]
            self.flash_until = self.now() + self.opt.flash_ms / 1000.0       # :1341
            bad = not (1 <= ln <= 128 and ln == rq.paylen - 6) or not (
                REGION_START <= addr and addr + ln <= REGION_START + REGION_SIZE)
            if not bad:
                for i, b in enumerate(data):
                    self.flash[addr - REGION_START + i] = b
            written = 0 if bad else ln
            self.emit("write", addr=hex(addr), want=ln, wrote=written)
            return msg(0x0763, int.to_bytes(addr, 4, "little")
                       + int.to_bytes(written, 2, "little") + bytes([1 if bad else 0]))
        if c == 0x0764:                       # 读一块 → 0x0765（11+size B）
            addr = int.from_bytes(rq.payload[:4], "little")
            ln = int.from_bytes(rq.payload[4:6], "little")
            bad = ln == 0 or ln > 128 or not (REGION_START <= addr and addr + ln
                                              <= REGION_START + REGION_SIZE)
            data = b"\x00" * ln if bad else bytes(self.flash[addr - REGION_START:
                                                             addr - REGION_START + ln])
            if not bad:
                self.flash_until = 0.0        # FIX(F-P3-17) :1373 读不算刷写
            return msg(0x0765, int.to_bytes(addr, 4, "little") + int.to_bytes(ln, 2, "little")
                       + bytes([1 if bad else 0]) + data)
        if c == 0x0766:                       # 对时 → 0x0767（9 B）
            secs = int.from_bytes(rq.payload[4:8], "little")
            return msg(0x0767, int.to_bytes(secs, 4, "little") + bytes([0]))
        if c == 0x0768:                       # 接管 / 交还 → 0x0769（5 B）
            on = bool(rq.payload[0]) if rq.paylen >= 1 else False
            self.set_ext_control(on)
            return msg(0x0769, bytes([self.state()]))
        if c == 0x076A:                       # 自检 → 0x076B（7 B：proto/state/phase）
            return msg(0x076B, bytes([self.proto, self.state(), self.phase]))
        if c == 0x076C:                       # 推帧 → 0x076D（5 B）
            self.push(rq.payload, rq.paylen)
            return msg(0x076D, bytes([self.state()]))
        if c == 0x076E:                       # 第二套页开关 → 0x076F（5 B）
            on = bool(rq.payload[0]) if rq.paylen >= 1 else False
            self.set_page(on)
            return msg(0x076F, bytes([self.state()]))
        self.emit("unknown_cmd", cmd=hex(c))
        return None                           # uart.c 没有 default 分支：静默

    def push(self, p: bytes, paylen: int):
        # doppler.c:3238 `len < 28` 直接 return —— 状态不改、心跳也不刷
        if paylen < 28 or len(p) < 28:
            self.emit("frame_short", paylen=paylen)
            return
        rx10 = int.from_bytes(p[0:4], "little")
        tx10 = int.from_bytes(p[4:8], "little")
        # doppler.c:3247-3252 越界就当坏帧，字段清零交给下游的 !=0 判断
        if not (FREQ_MIN10 <= rx10 <= FREQ_MAX10):
            rx10 = 0
        if not (FREQ_MIN10 <= tx10 <= FREQ_MAX10):
            tx10 = 0
        nl = min(p[20], 8)
        if 21 + nl > paylen:
            nl = paylen - 21
        self.frame = {
            "rx10": rx10, "tx10": tx10,
            "dopp": int.from_bytes(p[8:10], "little", signed=True),
            "dist": int.from_bytes(p[10:12], "little"),
            "remain": int.from_bytes(p[12:14], "little", signed=True),
            "el01": int.from_bytes(p[14:16], "little", signed=True),
            "rate01": int.from_bytes(p[16:18], "little", signed=True),
            "progress": p[18], "flags": p[19],
            "name": p[21:21 + nl].decode("ascii", "replace").rstrip("\x00"),
        }
        self.hb_tick = self.now()             # :3342 推帧本身就是心跳
        self.emit("frame", **{k: v for k, v in self.frame.items() if k != "name"},
                  name=self.frame["name"], state=self.state())


class Hang(Exception):
    pass


def request_bytes(cmd: int, payload: bytes = b"") -> bytes:
    """客户端→电台的一帧：msg 补齐偶数长度，尾部带真 CRC（RadioLink.kt:50-64）。"""
    m = msg(cmd, payload)
    if len(m) % 2:
        m += b"\x00"
    body = mask_xor(m + crc16(m).to_bytes(2, "little"))
    return bytes([0xAB, 0xCD, len(m) & 0xFF, len(m) >> 8]) + body + bytes([0xDC, 0xBA])


def parse_replies(buf: bytearray) -> tuple[list[Request | None], bytearray]:
    """电台→客户端的流式解析，规则抄 RadioLink.kt:117-140。

    和固件侧 `drain_frames` 唯一的区别就在 CRC：回包那两字节固件恒填 0xFF，
    所以客户端只在"真填了别的值"时才比对。把这两套判据混用，模型会拒掉自己
    发出的每一个回包。
    """
    out: list[Request | None] = []
    while True:
        i = buf.find(bytes([0xAB, 0xCD]))
        if i < 0:
            return out, buf[-3:]
        if i + 4 > len(buf):
            return out, buf[i:]
        size = buf[i + 2] | (buf[i + 3] << 8)
        if size < 4 or size > 1024:                   # RadioLink.kt:124 的长度上限
            buf = bytearray(buf[i + 4:])
            continue
        if i + size + 8 > len(buf):
            return out, buf[i:]
        body = mask_xor(bytes(buf[i + 4:i + 4 + size + 2]))
        foot = bytes(buf[i + 4 + size + 2:i + size + 8])
        crc_rx = body[size] | (body[size + 1] << 8)
        if foot != bytes([0xDC, 0xBA]) or (crc_rx != 0xFFFF and crc_rx != crc16(body[:size])):
            out.append(None)
            buf = bytearray(buf[i + size + 8:])
            continue
        paylen = body[2] | (body[3] << 8)
        out.append(Request(body[0] | (body[1] << 8), paylen, body[4:4 + paylen]))
        buf = bytearray(buf[i + size + 8:])


# ── 传输：数据口一个客户端，控制口随时能连 ──
class Server:
    def __init__(self, model: Model, opt) -> None:
        self.m = model
        self.opt = opt

    def serve(self):
        data = self._listen(self.opt.port, self.on_client)
        ctl = self._listen(self.opt.ctl_port, self.on_control)
        threads = [threading.Thread(target=s.accept_loop, daemon=True)
                   for s in (data, ctl)]
        for t in threads:
            t.start()
        threading.Thread(target=self.wdog, daemon=True).start()
        print(f"fakeradio  proto={self.m.proto} 数据口 {self.opt.port} "
              f"控制口 {self.opt.ctl_port} 看门狗 {self.opt.watchdog_ms} ms", flush=True)
        for t in threads:
            t.join()

    def _listen(self, port, handler):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(4)
        srv = Serverlike(s, handler, self)
        srv.port = port
        return srv

    def wdog(self):
        while True:
            time.sleep(0.01)          # 与固件一致的 10 ms 节拍
            self.m.tick()

    def on_client(self, conn: socket.socket):
        conn.settimeout(0.2)
        self.m.client = conn
        self.m.last_rx = self.m.now()
        self.m.emit("client_connected", peer=conn.getpeername())
        buf = bytearray()
        try:
            while True:
                try:
                    b = conn.recv(4096)
                except socket.timeout:
                    continue
                except (ConnectionResetError, OSError):
                    break
                if not b:
                    break
                self.m.last_rx = self.m.now()
                buf += b
                frames, buf = drain_frames(buf)
                for rq in frames:
                    if rq is None:
                        self.m.emit("frame_dropped", why="crc/payload 越界")
                        continue
                    self.m.emit("rx", cmd=hex(rq.cmd), paylen=rq.paylen)
                    try:
                        rep = self.m.handle(rq)
                    except Hang:
                        self.m.emit("link_hung")
                        return
                    if rep is None or rq.cmd in self.m.no_reply:
                        continue
                    if self.m.reply_stall_ms:
                        time.sleep(self.m.reply_stall_ms / 1000.0)
                        stall, self.m.reply_stall_ms = self.m.reply_stall_ms, 0
                        self.m.emit("reply_stalled", ms=stall)
                    conn.sendall(encode_reply(rep))
                    self.m.emit("reply", cmd=hex(int.from_bytes(rep[:2], "little")),
                                state=self.m.state())
        finally:
            self.m.client = None
            self.m.emit("client_closed")
            try:
                conn.close()
            except OSError:
                pass

    def on_control(self, conn: socket.socket):
        conn.settimeout(5)
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        try:
            while True:
                line = f.readline()
                if not line:
                    break
                ok, out = self.command(line.strip())
                f.write(("OK " if ok else "ERR ") + out + "\n")
                f.flush()
                if out == "bye":
                    break
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def command(self, line: str) -> tuple[bool, str]:
        m = self.m
        parts = line.split()
        if not parts:
            return True, ""
        op, arg = parts[0], parts[1] if len(parts) > 1 else ""
        try:
            if op == "ptt":
                on = arg == "1"
                if on and m.now() < m.flash_until:      # generic.c:105 刷写期吞 PTT
                    m.emit("ptt_swallowed", flash_left_ms=round((m.flash_until - m.now()) * 1000))
                    return True, "刷写窗口里，PTT 被吞"
                m.tx_hold = on
                m.transmit = on
                m.emit("ptt", on=on, state=m.state())
            elif op == "tx":
                m.transmit = arg == "1"
                m.emit("tx", on=m.transmit, state=m.state())
            elif op == "arm":
                m.auto_armed = arg == "1"
                m.emit("arm", on=m.auto_armed, state=m.state())
            elif op == "table":
                m.table_ok = arg == "1"
                m.emit("table", ok=m.table_ok, state=m.state())
            elif op == "phase":
                m.phase = int(arg)
                m.emit("phase", phase=PHASE_NAMES[m.phase])
            elif op == "flash":
                m.flash_until = m.now() + (m.opt.flash_ms / 1000.0 if arg == "1" else 0.0)
                m.emit("flash", on=arg == "1", state=m.state())
            elif op == "stall":
                m.reply_stall_ms = int(arg)
            elif op == "no-reply":
                m.no_reply.add(int(arg, 16))
            elif op == "watchdog":
                m.opt.watchdog_ms = int(arg)
                m.emit("watchdog_ms", value=m.opt.watchdog_ms)
            elif op == "hang":
                m.hang_after = m.cmd_count + int(arg)
            elif op == "drop":
                if m.client:
                    m.client.close()
                m.emit("dropped_client")
            elif op == "state":
                return True, json.dumps({"proto": m.proto, "state": m.state(),
                                         "phase": PHASE_NAMES[m.phase],
                                         "silence_ms": round((m.now() - m.hb_tick) * 1000)})
            elif op == "quit":
                return True, "bye"
            else:
                return False, f"未知控制命令 {op}"
        except (ValueError, IndexError):
            return False, f"参数不对：{line}"
        return True, ""


class Serverlike:
    def __init__(self, sock, handler, srv):
        self.sock, self.handler, self.srv = sock, handler, srv

    def accept_loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handler, args=(conn,), daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7878, help="App/桥接连的数据口")
    ap.add_argument("--ctl-port", type=int, default=7879, help="控制口")
    ap.add_argument("--events", default="", help="JSONL 事件流落盘路径")
    ap.add_argument("--watchdog-ms", type=float, default=5000.0,
                    help="帧静默判死，固件是 500 tick×10 ms（doppler.c:1657）")
    ap.add_argument("--serial-gone-ms", type=float, default=6000.0,
                    help="串口流量消失判死，固件是 gSerialConfigCountDown_500ms=12")
    ap.add_argument("--flash-ms", type=float, default=6000.0,
                    help="写 flash 后 0x80 位窗口时长，固件同样 12×500 ms")
    ap.add_argument("--phase", type=int, default=READY, help="起始相位")
    ap.add_argument("--no-table", action="store_true")
    ap.add_argument("--no-ext-page", action="store_true", help="模拟老固件：proto=2")
    a = ap.parse_args()
    a.table = not a.no_table
    a.ext_page = not a.no_ext_page
    m = Model(a)
    try:
        Server(m, a).serve()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
