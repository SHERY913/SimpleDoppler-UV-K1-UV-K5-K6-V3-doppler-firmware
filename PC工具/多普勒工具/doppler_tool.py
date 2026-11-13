"""Doppler tool - window.

Left: satellite list (favourites on top, search box). Right: the bird's card,
the passes over your site, the detail of the selected pass, and the button
that pushes the table into the radio.

Run:  python doppler_tool.py
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timezone, timedelta
from tkinter import ttk, messagebox

import doppler_core as core
import i18n
import radio_proto
from i18n import tr

# ---------------------------------------------------------------- 路径的唯一出处
# 源码运行和打包成 exe 运行，"程序在哪"和"用户数据该写哪"是两回事：
# PyInstaller 单文件模式每次启动都把内嵌资源解到一个随机命名的临时目录
# （sys._MEIPASS），退出即删。__file__ 在那里指向的就是这个临时窝，
# 把配置/缓存/日志写进去等于每次开机失忆，还会在 %TEMP% 里留一堆垃圾。
# 所以拆成两个：RES_DIR 只读（星图这类随包走的资源），
# DATA_DIR 可写（配置、TLE 缓存、日志），exe 模式落到 %LOCALAPPDATA%。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", SCRIPT_DIR)
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "DopplerPass")
    os.makedirs(DATA_DIR, exist_ok=True)
else:
    DATA_DIR = SCRIPT_DIR

BASE = DATA_DIR
CONFIG = os.path.join(DATA_DIR, "doppler_tool.json")
PIC_DIR = os.path.join(RES_DIR, "sat_pics")
import sat_freq
from sgp4.api import Satrec

try:
    import serial
    from serial.tools import list_ports
    HAVE_SERIAL = True
except Exception:
    HAVE_SERIAL = False

APP_DIR = DATA_DIR       # 兼容旧引用；路径的唯一出处在上面

BG = "#1e2224"
FG = "#d9ded9"
DIM = "#8a978d"
ACC = "#7fc25c"
HOT = "#d8b45a"
CARD = "#262b2d"
SIDE = "#22272a"
LINE = "#39423f"
BAD = "#5a2a2a"       # 输入框标红：框里的数给不出可用频率

# 转发器下拉最后一项。它只是个显示项，不在 sat.transponders 里：
# 索引 == len(transponders) 时选中就是它（见 sel_trans）。
CUSTOM_TRANS_LABEL = sat_freq.CUSTOM_TRANS_LABEL

# 时区唯一真值：手动填写 > 系统时区（系统时区自带各国政策与夏令时）。
# 界面显示、表头 aos2、对时推的钟，三处都走 Tool._tz_hours()。
# 历史：最早写死 +8（U-P-19）→ 系统时区 → 短暂试过纯经度太阳时（不认政策，
# 新疆会显示成 UTC+6），最终定稿"系统 + 可手动覆盖"。


# ------------------------------------------------------------------ radio link
MASK = bytes([0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
              0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80])


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def xor_mask(data):
    return bytes(b ^ MASK[i % 16] for i, b in enumerate(data))


def frame(msg):
    """Same layout as tools/serialtool/msg.py: the CRC covers the clear
    message, afterwards message and CRC are XOR-obfuscated together. Getting
    this wrong is silent - the radio simply never answers."""
    if len(msg) & 1:
        msg += b"\x00"
    n = len(msg)
    buf = bytearray(8 + n)
    buf[0:2] = bytes([0xAB, 0xCD])
    buf[2:4] = struct.pack("<H", n)
    buf[4:4 + n] = msg
    buf[4 + n:6 + n] = struct.pack("<H", crc16(msg))
    buf[6 + n:8 + n] = b"\xdc\xba"
    for i in range(n + 2):
        buf[4 + i] ^= MASK[i % 16]
    return bytes(buf)


def msg_of(cmd, payload=b""):
    return struct.pack("<HH", cmd, len(payload)) + payload


# 产品名与版本号（与安卓 App「简单多普勒」同一套叫法；BG0FPJ 是作者，不删）。
APP_NAME = "简单多普勒"
APP_VERSION = "2.1"

# 能读写多普勒过境表的最低固件 proto（0x076A 回包第一字节）。
# 6 = 带第二套逻辑页 + 表族命令；比它老的固件没有 0x0760/0x0762/0x0764。
MIN_PROTO = 6

# 0x0776 的解锁令牌，必须与固件 doppler.h 的 DPH_UNLOCK_TOKEN 一模一样
# （"DPU1" 的小端整数）。比这一条的是第 8 层 verify_table_fit.py（它读 doppler.h
# 取常量再比这里）；第 2 层 verify_link_ask.py 再拿假电台实测一次能不能借到额度。
# 以前这里写的 verify_three_way.py 一个字都没比过令牌 —— 注释替不存在的检查背书。
UNLOCK_TOKEN = 0x31555044


def log_line(tag: str, text: str) -> None:
    """往 logs/pc-YYYYMMDD.log 落一行（跟着 exe/脚本所在目录走）。

    为什么要有：写表失败时界面上只有最后一句话，而"擦到第几个扇区、第几遍
    重写、最后在哪个地址不符"这些前因全在滚动发生的瞬间，人一回头就没了。
    这个文件永不覆盖、永不抛异常 —— 记录失败不能反过来把主流程搞挂。
    """
    try:
        d = os.path.join(DATA_DIR, "logs")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "pc-" + datetime.now().strftime("%Y%m%d") + ".log")
        try:
            if os.path.getsize(path) > 4 * 1024 * 1024:      # 4 MB 滚动一档
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%m-%d %H:%M:%S")
                    + f"  {tag:5s} {text}{chr(10)}")
    except Exception:
        pass


class Link:
    def __init__(self, port, timeout=1.0):
        self.ser = serial.Serial(port, 38400, timeout=timeout)
        self.buf = bytearray()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _read(self, n, deadline):
        while len(self.buf) < n and time.time() < deadline:
            chunk = self.ser.read(max(1, n - len(self.buf)))
            if chunk:
                self.buf += chunk
        if len(self.buf) < n:
            raise TimeoutError(tr("电台没有响应"))
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def ask(self, cmd, payload=b"", expect=0, timeout=1.5):
        """Send one command, return the reply body (ID + paylen + payload).

        The radio frames replies as 0xABCD + len + obfuscated body + footer,
        so the transport header, the body and the footer all have to be peeled
        off here; everything above this line only deals with plain bytes.

        FIX(PROTO): `expect` 是**回包 body（内层帧）的字节数**，不是载荷长度。
        body = ID(2) + paylen(2) + payload，所以 body = 4 + 载荷。
        以前 erase/write/read 三处传的是载荷长度，各短 4 字节：擦除读出来的
        r[8] 越界抛 IndexError、写完的状态字节 r[10] 永远取不到（状态检查成了
        死代码）、回读每轮少 4 字节 —— n 退到 4 之后 r[11:15] 恒空，
        `while len(out) < length` 就再也出不来，整个"写入电台"流程卡死在
        busy、UI 永久禁用。详见 proto 报告 A3。

        回包现在还做三件事（以前只验 AB CD 和长度，坏帧会被当成好数据）：
        ID 必须是 cmd+1、paylen 与 body 长度自洽、CRC16 相符（固件恒填
        0xFFFF 时按协议跳过，见 radio_proto.py 顶部说明）。
        """
        # Resynchronise before every command. A reply that arrived late (or a
        # few extra bytes left over from the previous one) would otherwise be
        # parsed as the head of this reply, which is how the header check ended
        # up reading "01e30845" instead of "DPT1".
        self.buf.clear()
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass

        self.ser.write(frame(msg_of(cmd, payload)))
        if not expect:
            return b""

        deadline = time.time() + timeout
        try:
            hdr = self._read(4, deadline)
            if hdr[0:2] != bytes([0xAB, 0xCD]):
                raise RuntimeError(tr("回包头不对: {h}").format(h=hdr.hex()))
            n = hdr[2] | (hdr[3] << 8)          # body 长度 = 内层 msg 长度
            # 掩码覆盖 msg + CRC(2)，脚标 DC BA 不在掩码内 —— 这就是以前
            # "读 n 再读 4 字节脚标"为什么字节数没错、却永远校验不了 CRC 的原因。
            raw = xor_mask(self._read(n + 2, deadline))
            body, crc_rx = raw[:n], raw[n] | (raw[n + 1] << 8)
            foot = self._read(2, deadline)
            if foot != bytes([0xDC, 0xBA]):
                raise RuntimeError(tr("回包脚标不对: {f}").format(f=foot.hex()))
            if n < 4:
                raise RuntimeError(tr("回包 body 太短: {n} 字节 {b}").format(n=n, b=body.hex()))
            rid = body[0] | (body[1] << 8)
            if rid != ((cmd + 1) & 0xFFFF):
                raise RuntimeError(tr(
                    "回包 ID 0x{rid:04X} 不是 0x{want:04X}（发的 0x{cmd:04X}）body={body}"
                ).format(rid=rid, want=(cmd + 1) & 0xFFFF, cmd=cmd, body=body.hex()))
            paylen = body[2] | (body[3] << 8)
            if 4 + paylen > n:
                raise RuntimeError(tr(
                    "回包 paylen={paylen} 与 body 长度 {n} 矛盾 body={body}"
                ).format(paylen=paylen, n=n, body=body.hex()))
            if crc_rx != 0xFFFF and crc_rx != crc16(body):
                raise RuntimeError(tr(
                    "回包 CRC 不符 收 0x{rx:04X} 算 0x{calc:04X}"
                ).format(rx=crc_rx, calc=crc16(body)))
            if n != expect:
                # 0x0764 的"拒绝"回包：固件回 11 字节（ID 2 + Size 2 +
                # Addr 4 + Size=0 + Status=1），Status=1 表示它不认这个地址
                # ——uart.c 里是 `len > PY25Q16_RAW_CHUNK_SIZE || !DPH_TableRangeOk()`。
                # 这不是链路坏了，最常见的原因是**电台固件比 PC 工具老**：
                # 表区 32 KiB（止于 0x16A000）的固件读到 0x16A000 就拒不认账，
                # 而新工具一上来就整块探测 64 KiB。以前这里只说"回包长度 11
                # 少于预期 139"，谁看了都以为是串口掉字节。
                if cmd == 0x0764 and n == 11 and body[10]:
                    raddr = struct.unpack("<I", body[4:8])[0]
                    rst = body[10]
                    raise RuntimeError(tr(
                        "电台拒读 0x{addr:06X}（Status={st}，回包 Size=0）："
                        "这个地址不在电台认识的表区里。多半是电台固件还是旧版"
                        "（表区 32 KiB，止于 0x16A000），先用 104,288 B / "
                        "md5 b48ffb57 那个 f4hwn.sstv.doppler.bin 刷新固件再写表。"
                    ).format(addr=raddr, st=rst))
                # 只在"比预期短"时算错：固件若多给字段（向前兼容）不该判失败。
                raise RuntimeError(tr(
                    "回包长度 {n} 少于预期 {want}（0x{cmd:04X}）body={body}"
                ).format(n=n, want=expect, cmd=cmd, body=body.hex()))
            return body
        except Exception:
            # A late or partial reply must never be parsed as the next one.
            self.buf.clear()
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            raise

    # Reply layouts (see App/app/uart.c): Header(4) + Address(4) + [Size(2)] + Status(1) + [Data]
    # FIX(PROTO): 下面三处 expect 一律写成 body 长度 = 4 + 载荷长度。
    def hello(self):
        """0x076A/0x076B → (proto, state, phase)。

        写表前必须先握一次手：不握手就擦，等于拿"串口能打开"当"这台机器
        是带多普勒表固件"的证据。回包 body 恒 7 字节（uart.c 的 0x076B，
        App 侧 fwInfo 的 expect 也是 7，动它会连连接都保不住）。
        """
        r = self.ask(0x076A, b"", 7)
        return r[4], r[5], r[6]

    def fw_tag(self):
        """0x0772/0x0773 → 固件标识 dict（老固件不认这条命令，返回 None）。"""
        try:
            # 载荷 12 字节（Ver[8]+Ppm+PpmMax+Proto+Phase，uart.c:1529-1542），
            # 回包 body = 4 + 12 = 16，载荷从 r[4:] 起。
            r = self.ask(0x0772, b"", 16)
        except Exception:
            return None
        return radio_proto.parse_fw_tag(r[4:])

    def unlock_region(self):
        """0x0776/0x0777 → 借出"允许擦除表区里别人的扇区"的额度。

        返回 (回显的令牌, 剩余额度)。额度为 0 表示固件没认（老固件不实现
        这条命令、或令牌不对）。额度在固件 RAM 里，掉电清零 —— 每次上电
        都要重新借一次，这是故意的：不搞"开一次永久畅通"。
        """
        r = self.ask(0x0776, struct.pack("<I", UNLOCK_TOKEN), 9)  # Token(4)+Credits(1)
        token = struct.unpack_from("<I", r, 4)[0]
        return token, r[8]

    def erase(self, addr):
        r = self.ask(0x0760, struct.pack("<I", addr), 9)      # Addr(4)+Status(1) -> body 9
        if r[8]:
            raise RuntimeError(tr("擦除失败 0x{addr:06X} code={code}").format(addr=addr, code=r[8]))
        time.sleep(0.06)                 # sector erase needs ~50 ms internally

    def write(self, addr, data):
        """Write in 128-byte chunks, confirming each one before moving on.

        The flash part needs a moment per page; sending the next chunk too soon
        silently drops it, which showed up as "回读不一致 @0x162080" (the second
        chunk of the table).
        """
        for off in range(0, len(data), self.CHUNK):
            chunk = data[off:off + self.CHUNK]
            here = addr + off
            last = None
            for attempt in range(4):
                try:
                    r = self.ask(0x0762,
                                 struct.pack("<IH", here, len(chunk)) + chunk,
                                 expect=11)      # Addr(4)+Size(2)+Status(1) -> body 11
                    # 固件 uart.c 把这块的编程结果放在回包 body[10]：以前只等
                    # 体长、不看状态字节，写失败只能靠"最后回读不符"暴露，现场
                    # 看到的是"地址对不上"而不是"这块没写进去"（报告 第六部分·回归项 13）。
                    if len(r) > 10 and r[10]:
                        last = tr("写块失败 0x{here:06X} code={code}").format(here=here, code=r[10])
                        time.sleep(0.04 * (attempt + 1))
                        continue
                    time.sleep(0.012)
                    back = self.read(here, len(chunk))
                    if back == chunk:
                        last = None
                        break
                    last = tr("回读不符 @0x{here:06X}").format(here=here)
                except Exception as e:
                    last = str(e)
                time.sleep(0.04 * (attempt + 1))
            if last:
                raise TimeoutError(tr("写入 0x{here:06X} 失败：{why}").format(here=here, why=last))

    def set_clock(self, when=None):
        """Push the wall clock so the radio does not need six keypad digits."""
        when = when or datetime.now()
        secs = when.hour * 3600 + when.minute * 60 + when.second
        days = (when.date() - datetime(2000, 1, 1).date()).days
        try:
            r = self.ask(0x0766, struct.pack("<II", days, secs), 9)
        except TimeoutError:
            # 回退分支也必须发满 8 字节：固件 uart.c 里 Size != 8 直接 break、
            # 不回包，以前这里只发 4 字节，于是第二次 ask 又超时（报告 第六部分·回归项 10）。
            r = self.ask(0x0766, struct.pack("<HIH", days, secs, 0), 9)
        if r[8]:
            raise RuntimeError(tr("对时失败 code={code}").format(code=r[8]))
        # 回包里 Seconds 是固件**真正收下**的秒数（uart.c 的 0x0767 载荷）。
        # 只验 Status 会漏掉两种现场：串口线上字节被吃掉，固件按收到的字节
        # 算出一个别的秒数，照样返回 Status=0；或者时钟内部钳位没生效。
        # 两种都会让表里的绝对时间戳整体偏移，而电台屏幕上完全看不出来。
        # 回显必须等于我们发下去的值。固件 Reply.Seconds 就是入参 secs 原样回显
        # （uart.c:1647），以前这里只看 Status，于是"电台收下了但时钟没生效"
        # （例如 DOPPLER_SetClock 内部按年/日钳位、或中途被别的串口命令打断）
        # 完全看不出来 —— 表面 ✓ 对时成功，实际整张过境表的时间轴整体偏移。
        back = struct.unpack("<I", r[4:8])[0]
        if back != secs:
            raise RuntimeError(tr(
                "对时回显不符：发了 {want} s，电台回 {got} s（差 {diff:+d} s）——电台时钟没跟着走"
            ).format(want=secs, got=back, diff=back - secs))
        return secs

    CHUNK = 128      # the radio only ever moves one flash chunk per command

    def read(self, addr, length):
        out = bytearray()
        while len(out) < length:
            n = min(self.CHUNK, length - len(out))
            payload = struct.pack("<I", addr + len(out)) + struct.pack("<H", n)
            r = None
            last = None
            for attempt in range(3):
                try:
                    r = self.ask(0x0764, payload, 11 + n)   # Addr(4)+Size(2)+Status(1)+Data(n)
                except Exception as e:
                    last = e
                    time.sleep(0.05 * (attempt + 1))
                    continue
                break
            if r is None:
                # FIX(PROTO): 以前这里一律说"三次都没回包"。真机上最常见的原因
                # 恰恰是**回了**但字节不对（长度/CRC/ID），把它说成"没回包"会
                # 把排查方向指错。现在把最后一次的真实原因带上。
                raise TimeoutError(tr(
                    "回读 0x{addr:06X} 三次都失败：{last}"
                ).format(addr=addr + len(out), last=last))
            if r[10]:
                raise RuntimeError(tr("回读失败 0x{addr:06X} code={code}").format(
                    addr=addr + len(out), code=r[10]))
            out += r[11:11 + n]
        return bytes(out)


def manual_tz_hours(text):
    """手动时区输入：-12..14 的小时数（允许 5.5 / 5.75 这种半小时制）。
    空/越界/非数返回 None —— 由调用方退回系统时区并在界面上标出来。"""
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    if -12.0 <= v <= 14.0:
        return v
    return None


def local(dt, hours=None):
    """UTC → 台站本地时刻。

    hours 必须由界面侧显式传（单一真值 Tool._tz_hours()）；不传只服务
    命令行老路径，退回系统时区。"""
    return dt + timedelta(hours=core.local_tz_hours() if hours is None else hours)


def tz_label(hours):
    """UTC 偏移的显示串。

    负偏移必须拼成 "-3:30"，不能直接 f"UTC{int(h):+d}"
    ——那会拼出 "UTC+-3" 这种非法字符串。"""
    sign = "-" if hours < 0 else "+"
    total = abs(int(round(hours * 60)))
    return f"UTC{sign}{total // 60}:{total % 60:02d}"


# TLE 超过这个天数就算"老"了，界面上要提示（低轨元素 2 周后位置误差已上百公里）。
TLE_TTL_DAYS = 14


def cache_file_time():
    """tle_cache.json 的修改时间，当作"这批数据什么时候抓的"。

    老缓存只有 3 个字段、没有时间戳，光看内容判不出新旧；文件 mtime 是个
    足够好的近似，比"时间未知"有用。"""
    try:
        return os.path.getmtime(os.path.join(BASE, "tle_cache.json"))
    except OSError:
        return 0.0


def tle_stamp(v):
    """缓存条目的时间戳（epoch 秒），取不到就 0。

    4 字段 = 本文件写的抓取时间；5+ 字段 = refresh_tle.py 的新格式
    （第 4 项抓取时间、第 5 项 TLE 历元）。都拿不到时返回 0，由调用方
    决定当"未知"处理，而不是当成"新鲜"。"""
    for i in (3, 4):
        if len(v) > i:
            try:
                x = float(v[i])
                if x > 1e8:  # 1990-01-01 之后，排除占位 0
                    return x
            except (TypeError, ValueError):
                pass
    return 0.0


def age_label(stamp):
    """把 epoch 时间说成人话：0 分钟 / 3 小时 / 6 天。"""
    if not stamp:
        return tr("时间未知")
    d = max(0.0, time.time() - stamp)
    if d < 3600:
        return tr("{n} 分钟前").format(n=int(d // 60))
    if d < 86400:
        return tr("{n} 小时前").format(n=int(d // 3600))
    return tr("{n} 天前").format(n=int(d // 86400))


# ------------------------------------------------------------------ app
def _res(name):
    """随包资源路径：exe 模式在 _MEIPASS，源码模式在脚本目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def table_stats(table):
    """表长 / 扇区 / 条目 / 样点 / 步长 / 数据偏移 —— 与 App 的 TableBuilder 同口径。

    这六个数只在写表时算一次，GUI 日志和命令行都从这里取，避免两处各算一遍
    时口径漂移（表长是 16 + 40×条目 + 6×样点，扇区数是向上取整）。
    """
    magic, ver, count, hdr_size, data_off, _res = struct.unpack_from(
        "<4sBBHII", table, 0)
    pts = 0
    step_ms = 0
    for i in range(count):
        f = struct.unpack_from("<16sIIIHHHHHBB", table,
                              core.DPH_HDR + i * core.DPH_ENTRY)
        pts += f[6]
        if i == 0:
            step_ms = f[7]
    entry_area = core.DPH_HDR + count * core.DPH_ENTRY
    return {"magic": magic, "ver": ver, "count": count, "hdr": hdr_size,
            "data_off": data_off, "pts": pts, "step_ms": step_ms,
            "len": len(table), "entry_area": entry_area,
            "sectors": (len(table) + core.DPH_SECTOR - 1) // core.DPH_SECTOR}


def probe_region(link):
    """把整块表区读回来分三类。64 KiB / 512 B 一帧 = 128 次往返。"""
    blob = bytearray()
    step = 512
    for off in range(0, core.DPH_REGION, step):
        n = min(step, core.DPH_REGION - off)
        blob += link.read(core.TABLE_START + off, n)
    return core.classify_region(bytes(blob))


def verify_table(link, table, tick):
    """整表回读比对。None = 全对；否则 (首个不符偏移, 设备字节)。"""
    step = 512
    for off in range(0, len(table), step):
        want = table[off:off + step]
        got = link.read(core.TABLE_START + off, len(want))
        if got == want:
            continue
        n = min(len(got), len(want))
        for i in range(n):
            if got[i] != want[i]:
                return off + i, got[i]
        return off + n, 0xFF
    tick(tr("回读 {n} B 一致").format(n=len(table)))
    return None


def radio_write(port, table, allow_unlock=False, tick=None, notes=None,
                tz_hours=None):
    """把一张表写进电台。GUI 与命令行共用这一条路径。

    tick(text, done, total)：进度回调，命令行传 None 就只走日志。
    notes：输出参数，回填 fw 标识与对时备注。失败抛 RuntimeError。
    tz_hours：台站时区。表里的 aos2 和对时推的钟必须同一个基准，
    所以这里收的是界面算好的那一个数；命令行不传=系统时区（旧行为）。
    """
    tick = tick or (lambda text, done, total: None)
    notes = notes if notes is not None else {}
    sectors = core.erase_plan(len(table))
    link = None
    done = [0]
    total = [1]

    def step_text(text):
        done[0] += 1
        tick(text, done[0], total[0])

    try:
        link = Link(port)

        # ── 0. 握手：先证明对面是"带多普勒表的固件"，再动一个字节 ──
        # 旧流程拿"串口能打开 + 能读 16 字节"当证据。碰到没刷多普勒固件的
        # 机器，它是在**擦完扇区之后**才发现 0x0762 没人应答 —— 表已经没了。
        try:
            proto, state, phase = link.hello()
        except Exception as e:
            raise RuntimeError(tr(
                "电台没回应握手（0x076A）：{e} —— 检查 USB 线、端口号，"
                "或者这台机器刷的不是带多普勒功能的固件").format(e=e))
        if proto < MIN_PROTO:
            raise RuntimeError(tr(
                "固件 proto={p} 太老（要 ≥{m}，带过境表读写），"
                "先刷 v6.3.0 以上的多普勒固件；电台里的旧表没动"
            ).format(p=proto, m=MIN_PROTO))
        # 固件标识只用来"说清是哪台机器"，不参与放行：老固件不认 0x0772
        # 是常态，不能因为读不到版本就拒绝写表。
        try:
            tag = link.fw_tag()
        except Exception:
            tag = None
        notes["fw"] = (tr(" · {v} ppm={p:+d} 修正").format(v=tag['ver'], p=tag['ppm']) if tag
                       else tr(" · 固件标识读不到（老固件）"))
        log_line("HELLO", tr("{port} proto={proto} state={state} phase={phase}{fw} 表={tb}B 擦={s}扇区").format(
            port=port, proto=proto, state=state, phase=phase, fw=notes['fw'],
            tb=len(table), s=len(sectors)))
        st = table_stats(table)
        # D2：与 App 同口径的六个数字，一次写入打一行。len 必须等于
        # 16 + 40×entries + 6×pts，这是三端锁死的公式。
        log_line("TABLE", f"len={st['len']} sectors={st['sectors']} "
                          f"entries={st['count']} pts={st['pts']} "
                          f"step_ms={st['step_ms']} data_off=0x{st['data_off']:06X}")
        # 表长 = 0x1000（表头+条目区，固定预留 4 KiB）+ 6 B × 总样点。
        # 条目区里 16 + 40×entries 只是"用到的"部分，其余补 0——所以
        # 16 + 40×entries + 6×Σpts 只在 entries 占满 4 KiB（=102 条）时
        # 才等于文件长度，这也是 App TableBuilder 的 total 口径。
        log_line("FORMULA", f"len=0x{core.DPH_SAMPLE_OFF:X}+6×{st['pts']}={st['len']} "
                            f"entry_area=16+40×{st['count']}={st['entry_area']} "
                            f"pad={core.DPH_SAMPLE_OFF - st['entry_area']}")

        errs = core.validate_table(table)
        if errs:
            raise RuntimeError(tr("表格自检没通过，已中止（电台里的旧表没动）：{errs}")
                               .format(errs="；".join(errs[:3])))

        # ── 0.5 归属探测：擦第一个扇区之前，先把整块表区读回来认主 ──
        # 固件在擦除命令里也会逐个扇区判（DPH_TableErase），但那个判断
        # 发生在"已经擦掉前面几个扇区之后"——一旦中途拒写，电台里留下的是
        # 半张表。这里先整块读回来（表区 16 个扇区、64 KiB，
        # 512 B 一帧是 128 次往返，16 秒上下 —— 比擦完再发现的代价小得多），
        # 有问题就一个字节都不动地退出，并说清是谁占着。
        classes = probe_region(link)
        why = core.occupied_reason(classes)
        if why:
            # 用户显式勾了"允许擦除表区"才走这条：先借额度，
            # 借不到（老固件/令牌不对）就原样退出，一个字节不动。
            if not allow_unlock:
                raise RuntimeError(tr(
                    "表区被人占着，已中止（一个字节都没动）：{why}"
                    "\n\n要在这个位置装表，勾上写表按钮左边的"
                    "「允许擦除表区」再点一次（那块数据不可恢复），"
                    "或者先跑 clear_table.py --yes。").format(why=why))
            token, credits = link.unlock_region()
            if credits == 0:
                raise RuntimeError(tr(
                    "固件不认这次解锁（0x0776 没应答或令牌不符），已中止："
                    "一个字节都没动。\n这台机器的固件不支持受控清地，"
                    "先刷本轮固件，或用旧版 clear_table.py --yes。"))
            log_line("UNLOCK", tr("token=0x{tok:08X} credits={c} 表区将被清空后重写").format(
                tok=token, c=credits))
        log_line("PROBE", " ".join(f"0x{a:06X}:{c}" for a, c in classes))

        # 对时失败就不再往下写：表里的时间戳是相对电台时钟的绝对秒，
        # 时钟差多少，整张表的过境就整体偏多少 —— 而电台屏幕上看不出
        # 这件事（它照着自己的钟倒计时）。以前这里只记一句备注继续写，
        # 结果是"写入成功、过境全错"。宁可在擦除前退出，旧表一根不动。
        try:
            if tz_hours is None:
                link.set_clock()
            else:
                link.set_clock(datetime.now(timezone.utc) + timedelta(hours=tz_hours))
        except Exception as e:
            raise RuntimeError(tr(
                "对时失败，已中止（电台里的旧表没动）：{e}"
                "\n先把电台的日期/时间校准，或在按「写入电台」前确认 "
                "{port} 只被本程序占用").format(e=e, port=port))
        body_len = max(0, len(table) - core.DPH_HDR)
        total[0] = len(sectors) + (body_len + 127) // 128 + 3

        # ── 1. 擦 + 写 + 整表回读，最多两遍 ────────────────────────
        # 一页一页写、一页一页回读只能保证"这一页当场对了"。真机上出现过
        # 40 KB 写完、表头也对，整段样点却是 0xFF（擦除与写入之间的 WIP
        # 竞争）—— 逐页回读全过，电台照样按垃圾样点乱调频。
        # 所以收尾必须**整表**回读比对；不一致就整表重写一遍（擦除幂等），
        # 仍不一致才报错，并报出第一个不同的地址。
        #
        # 写入顺序：先样点、最后 16 字节表头。表头没落盘之前那 16 字节是
        # 擦除后的 0xFF，固件只会报 NO TABLE，不会装作有一张好表；反过来
        # 就会留下"magic/count 全合法 + 样点全是垃圾"的半表。
        attempt = 0
        while True:
            attempt += 1
            # 擦除顺序：**从高地址往低地址**，表头扇区（最低）留到最后。
            # 固件 DPH_TableErase 有一道归属闸门：扇区自己不是全 FF、而表头
            # 已经不是 DPT1 时判 OCCUPIED(4) 拒擦。先擦表头 → 后面每个数据
            # 扇区都变成"别人的数据"，第 2 个扇区就回 code=4，表只剩半张
            # （2026-11-08 真机上踩到过）。反过来擦就一路 own=OURS 放行。
            for a in sorted(sectors, reverse=True):
                link.erase(a)
                step_text(tr("擦除 0x{a:06X}").format(a=a)
                          + (tr(" · 第 {n} 遍").format(n=attempt) if attempt > 1 else ""))
            step_text(tr("擦除完成"))
            if body_len:
                link.write(core.TABLE_START + core.DPH_HDR, table[core.DPH_HDR:])
                step_text(tr("写入 {n} B").format(n=body_len))
            link.write(core.TABLE_START, table[:core.DPH_HDR])
            step_text(tr("表头 {n} B").format(n=core.DPH_HDR))

            bad = verify_table(link, table, step_text)
            if bad is None:
                break
            if attempt >= 2:
                off, got = bad
                raise RuntimeError(tr(
                    "整表回读两遍都不符，最后在 0x{addr:06X}"
                    "（设备 0x{dev:02X} ≠ 表 0x{want:02X}）——"
                    "电台里现在是一张不可信的表，再写一次；"
                    "连续出现就换 USB 线、换口，或降低串口抖动"
                ).format(addr=core.TABLE_START + off, dev=got, want=table[off]))
            log_line("RETRY", tr("整表回读不符 @0x{addr:06X}（设备 0x{dev:02X}），第 {n} 遍重写").format(
                addr=core.TABLE_START + bad[0], dev=bad[1], n=attempt))
            step_text(tr("回读不符 @0x{addr:06X}，重写").format(addr=core.TABLE_START + bad[0]))

        log_line("OK", tr("写入完成 {n}B / 擦 {s} 扇区{fw}").format(
            n=len(table), s=len(sectors), fw=notes['fw']))
        return st
    finally:
        if link:
            link.close()


# ------------------------------------------------------------------ 搜索与显示名
# TLE 里这颗星叫 "LUCA (RS90S)"，操作员搜的是 "RS90"：名字砍成第一段就把
# 业余编号砍没了，AMSAT 编号表里它又叫 RS90S。所以搜索键覆盖每一个别名段，
# 显示名保留完整名字，放不下才退回第一段。
_NAME_SEP = re.compile(r"[()/,]|·")
_NONKEY = re.compile(r"[^0-9a-z\u4e00-\u9fff]")


def name_parts(name):
    """'LUCA (RS90S)' → ['LUCA', 'RS90S']；'JO-97 / JY1SAT' → ['JO-97', 'JY1SAT']。"""
    return [p.strip() for p in _NAME_SEP.split(name or "") if p.strip()]


def norm_key(text):
    """搜索归一化：小写，丢掉空格与连字符 —— 'RS-44'、'rs 44'、'RS44' 同一个键。"""
    return _NONKEY.sub("", str(text or "").lower())


def name_keys(sat):
    """一颗星的名字类搜索键：国际名、每个别名段、中文名（整串与分段）。"""
    keys = {norm_key(sat.name)}
    keys.update(norm_key(p) for p in name_parts(sat.name))
    cn = sat_freq.CNNAME.get(sat.norad)
    if cn:
        keys.add(norm_key(cn))
        keys.update(norm_key(p) for p in name_parts(cn))
    return {k for k in keys if k}


def sat_match(sat, query):
    """模糊匹配。查询按空格切成词，这些词必须**一起出现在同一个键里**：
    分开判会让 "rs 90" 命中 TDRS 11（NORAD 49013 里正好有 90）。
    NORAD 只在整串查询时单独比，它是编号不是名字。"""
    words = [w for w in (norm_key(x) for x in query.split()) if w]
    if not words:
        return True
    if any(all(w in k for w in words) for k in name_keys(sat)):
        return True
    return len(words) == 1 and words[0] in norm_key(sat.norad)


def wide_len(text):
    """列宽按字符宽度算：汉字占两格。"""
    return sum(2 if ord(c) > 127 else 1 for c in text)


# 业余身份的唯一真源是 数据/amateur_allow.json（和 App/生成器共用同一份）。
# exe 里这份文件不随包走，读不到就退回频点表 —— 默认视图少几颗，但不会空。
_ALLOW_PATHS = [
    os.path.normpath(os.path.join(DATA_DIR, os.pardir, os.pardir, "数据", "amateur_allow.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, os.pardir, os.pardir, "数据", "amateur_allow.json")),
    os.path.join(DATA_DIR, "amateur_allow.json"),
    os.path.join(RES_DIR, "amateur_allow.json"),
]


def amateur_base_norads():
    """白名单 keep 里的 NORAD，加上频点表收录的星和 EXTRA 名单。"""
    out = set(sat_freq.FREQ) | set(sat_freq.EXTRA)
    for p in _ALLOW_PATHS:
        try:
            with open(p, encoding="utf-8") as f:
                keep = json.load(f).get("keep") or []
            for e in keep:
                try:
                    out.add(int(e["norad"]))
                except (TypeError, ValueError, KeyError):
                    continue
            break
        except Exception:
            continue
    return out


def merge_cached(sats, cache):
    """把本地全量缓存里**没被联网结果覆盖**的星补进池子。

    为什么必须并：celestrak 的 GROUP=amateur 只回 96 颗，**不含 67287
    (RS90S)**；AMSAT 的 nasabare 是 97 颗，也不含它。这些星只活在本地的
    tle_cache.json（15,985 条）里。以前联网分支直接 `sats = []` 重建，
    点一次「更新 TLE」就把缓存池整个盖掉 —— 之后在 exe 里怎么模糊都搜不到
    RS90S，而界面上明明写着"已更新"。

    联网那份优先：同一个 NORAD 两边都有时，用联网的新星历，缓存只补缺。
    返回新的列表，不改入参。
    """
    have = {s.norad for s in sats}
    out = list(sats)
    for norad_s, v in (cache or {}).items():
        try:
            norad = int(norad_s)
        except Exception:
            continue
        if norad in have:
            continue
        try:
            name, l1, l2 = v[0], v[1], v[2]
        except Exception:
            continue
        st = core.Sat(norad, sat_freq.EXTRA.get(norad) or name, 145.800, "未知", "")
        st.tle = (name, l1, l2)
        if norad in sat_freq.EXTRA:
            sat_freq.fill_extra(st)
        else:
            sat_freq.fill(st)
        out.append(st)
        have.add(norad)
    return out


def _trans_freqs(tr):
    """一条转发器的 (freq10, up10, ctcss10)，10 Hz / 0.1 Hz 单位。"""
    def pick(prop, mhz_attr, scale):
        v = getattr(tr, prop, None)
        if v is None:
            v = float(getattr(tr, mhz_attr, 0.0) or 0.0) * scale
        return int(round(float(v)))

    c10 = getattr(tr, "ctcss10", None)
    if c10 is None:
        tone = getattr(tr, "tone_hz", None)
        if tone is None:
            tone = getattr(tr, "tone", 0.0) or 0.0
        c10 = float(tone) * 10
    return (pick("freq10", "down_mhz", 1e5),
            pick("up10", "up_mhz", 1e5),
            int(round(float(c10))))


def match_transponder(sat, name):
    """在这颗星上按名字找转发器：先精确，再忽略大小写/空白，最后比 label。"""
    trs = list(getattr(sat, "transponders", None) or [])
    key = str(name or "").strip()
    if not key:
        return None
    for tr in trs:
        if str(getattr(tr, "name", "")) == key:
            return tr
    low = key.lower()
    for tr in trs:
        if str(getattr(tr, "name", "")).strip().lower() == low:
            return tr
    for tr in trs:
        if str(getattr(tr, "label", "")).strip().lower() == low:
            return tr
    # 最后一层宽松：只比字母数字（"RS 90"/"RS-90"/"rs90" 认为是同一条）。
    # 数据里同一条转发器在 PC 与手机两端名字写法不一致时靠这条兜住。
    squash = re.sub(r"[\s\-_]+", "", low)
    if squash:
        for tr in trs:
            cand = re.sub(r"[\s\-_]+", "", str(getattr(tr, "name", "")).strip().lower())
            if cand == squash:
                return tr
    return None


def refresh_entry_freqs(sats, entries, current_sat=None):
    """写表前，按每条过境自己的星 + 转发器重取一遍下行/上行/哑音。

    为什么必须重取：待写队列里的条目是 `queue_add()` **那一刻**的快照
    （`name/freq10/up10/ctcss10` 复制进去）。之后操作员在界面上换转发器、
    改「下行 MHz」、甚至换一颗星，队列里那几条的数一个都不会跟着变，而
    `write_radio()` 又优先用队列 —— 结果每次写进电台的都是第一次入队时的
    那张表。现场表现就是"写什么频率，电台上都是同几趟、同一个频率"。

    这里以星 + 转发器名为准重新算，队列只提供"哪一颗星、哪一条转发器"。
    找不到星的条目保留原值并记进 `missing`，由调用方提示操作员。

    返回 (changed, missing)：changed 是值真的变了的条数；missing 是找不到
    对应卫星的条目描述（"星名 MM-DD HH:MM"）。
    """
    by_norad = {}
    for st in sats or []:
        try:
            by_norad[int(getattr(st, "norad", 0) or 0)] = st
        except (TypeError, ValueError):
            continue

    changed = 0
    missing = []
    for e in entries or []:
        sat = None
        try:
            sat = by_norad.get(int(getattr(e, "norad", 0) or 0))
        except (TypeError, ValueError):
            sat = None
        if sat is None and current_sat is not None:
            if str(getattr(e, "name", "")) == str(getattr(current_sat, "name", ""))[:16]:
                sat = current_sat
        if sat is None:
            when = getattr(e, "aos", None)
            try:
                stamp = when.strftime("%m-%d %H:%M") if when else ""
            except Exception:
                stamp = ""
            missing.append(f"{str(getattr(e, 'name', '?'))[:16]} {stamp}".strip())
            continue

        tr = match_transponder(sat, getattr(e, "trans_name", None))
        if tr is not None:
            f10, u10, c10 = _trans_freqs(tr)
            tname = str(getattr(tr, "name", "") or "")
        else:
            f10 = int(round(float(getattr(sat, "freq_mhz", 0.0) or 0.0) * 1e5))
            u10 = int(round(float(getattr(sat, "up_mhz", 0.0) or 0.0) * 1e5))
            c10 = int(round(float(getattr(sat, "up_tone", 0.0) or 0.0) * 10))
            tname = str(getattr(sat, "mode", "") or "")
        sname = str(getattr(sat, "name", ""))[:16]

        before = (getattr(e, "name", None), getattr(e, "freq10", None),
                  getattr(e, "up10", None), getattr(e, "ctcss10", None))
        e.name = sname
        e.freq10 = f10
        e.up10 = u10
        e.ctcss10 = c10
        if tname:
            e.trans_name = tname
        if before != (e.name, e.freq10, e.up10, e.ctcss10):
            changed += 1
    return changed, missing


class Tool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = self.load_cfg()
        i18n.set_language(self.cfg.get("lang", "zh"))
        # 切换语言时要重画的东西：build() 里每处登记一条闭包。
        self._lang_hooks = []
        # 标题只放程序名和开发者，基座致谢挪到左栏底部（见下）。
        # 原来把"（固件基座 F4HWN SSTV）"塞进标题栏，任务栏一缩就变成
        # "多普勒过境表 · BG0…"，两个名字都读不全 —— 致谢不该靠标题栏承载。
        self.title(tr("{app} · 写表").format(app=tr(APP_NAME)))
        try:
            self.iconbitmap(_res("app.ico"))
        except Exception as e:
            print("iconbitmap failed:", e, file=sys.stderr)
        # 任务栏不吃 iconbitmap 的单帧 ico：它要 16/32 的小图，给大图它会自己缩成一团。
        # iconphoto 可以一次给一串尺寸，Windows 挑最接近的那个。
        self._icons = []
        for _sz in (16, 32, 48, 256):
            _p = _res(os.path.join("ico", f"app_{_sz}.png"))
            if os.path.exists(_p):
                self._icons.append(tk.PhotoImage(file=_p))
        if self._icons:
            self.iconphoto(True, *self._icons)
        self.geometry("1240x840")
        try:
            self.state("zoomed")
        except Exception:
            self.attributes("-zoomed", True)
        self.minsize(980, 700)
        self.configure(bg=BG)

        self.sats = []            # Sat objects, favourites first
        self._list = []           # fill_list 现在摆在列表里的那几行
        self._am_base = None      # 业余身份 NORAD 集合（白名单 ∪ 频点表）
        self.station_unset = False
        self.np_err = []
        self.tle_at = 0.0
        self.tle_offline = False  # 以前只在 except 分支里才创建，命中缓存时反而炸
        self.tle_err = ""
        self.tle_from_cache = False
        self.by_norad = {}
        self.sat = None
        self.satrec = None
        self.custom_tr = None     # "自定义…"上一次算出来的有效转发器
        self._last_trans_i = 0    # 进"自定义…"时用它的 up 当上行默认值
        self.passes = []
        self.nextp = {}
        self._loaded = None
        self.low_el = 10.0
        self.queue = []
        self.rows = []
        self.checked = set()
        self.busy = False
        self.sort_key = "time"
        self.sort_rev = False
        self.group_ok = False

        self.build()
        self.load_group()

    # --- config -----------------------------------------------------
    def load_cfg(self):
        # 台站默认留空 = 未设置。原来这里写死 43.83 / 87.62（乌鲁木齐），
        # 后果不是"显示不好看"：所有下次过境、仰角、斜距、多普勒样点都按
        # 那个坐标算，别人装上直接拿一张按别人城市算的表写进电台，
        # 而界面一路报成功。App 侧同一个 bug 已经修过并加了自检
        # （Geo.StationStore 用 NaN 表示未设、verify_layout 禁止硬编码坐标），
        # 这条把 PC 工具纳进同一条口径。
        base = {"lat": "", "lon": "", "days": 3, "min_el": 10.0,
                "step": 10.0, "norad": 25544, "port": "", "fav": [25544],
                "freq": {}, "lang": "zh", "tz_mode": "auto", "tz": ""}
        try:
            with open(CONFIG, encoding="utf-8") as f:
                base.update(json.load(f))
        except Exception:
            pass
        # 旧配置里存着 step=1.0 那种"密采样"的值。表区收到 32 KiB 以后，
        # 固件对 <10 s 的表是整张拒收的，所以这里把老值就地抬到合法下限，
        # 而不是等他点"重新计算"才弹一个看不懂的错。
        try:
            floor_s = core.DPH_STEP_MS_MIN / 1000.0
            if float(base.get("step", floor_s)) < floor_s:
                base["step"] = max(floor_s, core.DPH_STEP_S_DEFAULT)
        except (TypeError, ValueError):
            base["step"] = core.DPH_STEP_S_DEFAULT
        return base

    def save_cfg(self):
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception:
            pass

    # --- language ----------------------------------------------------
    def _tx(self, w, key):
        """静态控件文案：现在设一次，切语言时按登记的键重设。"""
        def apply():
            w.config(text=tr(key))
        apply()
        self._lang_hooks.append(apply)
        return w

    # --- timezone ----------------------------------------------------
    def _tz_hours(self):
        """时区唯一真值：手动填写 > 系统时区（系统自带各国政策与夏令时）。
        界面显示、build_table 的 aos2、对时推的钟，三处必须都从这里取。"""
        if self.cfg.get("tz_mode") == "manual":
            v = manual_tz_hours(self.e_tz.get() if hasattr(self, "e_tz")
                                else self.cfg.get("tz", ""))
            if v is not None:
                return v
        return core.local_tz_hours()

    def cur_tz(self):
        """当前界面时区（小时）。过境结果缓存的生成代里记着它，
        改了时区没点「计算」时，旧结果仍按旧时区显示，不会新旧混排。"""
        return getattr(self, "_pass_tz", None)

    def _tz_row_refresh(self):
        self.cb_tz_mode.config(values=[tr("自动（系统）"), tr("手动")])
        self.cb_tz_mode.current(1 if self.cfg.get("tz_mode") == "manual" else 0)
        manual_on = self.cfg.get("tz_mode") == "manual"
        self.e_tz.config(state="normal" if manual_on else "disabled",
                         disabledbackground=SIDE,
                         bg=BAD if (manual_on and manual_tz_hours(self.e_tz.get()) is None
                                   and self.e_tz.get()) else (CARD if manual_on else SIDE))
        if self.e_tz.get() != str(self.cfg.get("tz", "")):
            self.e_tz.delete(0, "end")
            self.e_tz.insert(0, str(self.cfg.get("tz", "")))
        self.lbl_tz.config(text=self.tz_text(),
                           fg="#d98a5a" if (manual_on and self.e_tz.get()
                                            and manual_tz_hours(self.e_tz.get()) is None) else DIM)

    def on_tz_mode(self, ev=None):
        self.cfg["tz_mode"] = "manual" if self.cb_tz_mode.current() == 1 else "auto"
        self.save_cfg()
        self._tz_row_refresh()
        if self.cfg["tz_mode"] == "manual":
            self.e_tz.focus_set()      # 切手动就跳进框里，不用猜点哪

    def on_tz_edit(self, ev=None):
        self.cfg["tz"] = self.e_tz.get()
        self.save_cfg()
        bad = manual_tz_hours(self.e_tz.get()) is None and self.e_tz.get().strip() != ""
        self.e_tz.config(bg=BAD if bad else CARD)
        self.lbl_tz.config(text=self.tz_text(), fg="#d98a5a" if bad else DIM)

    def tz_text(self):
        if self.cfg.get("tz_mode") == "manual":
            v = manual_tz_hours(self.e_tz.get())
            if v is not None:
                return tr("时区 {tz} · 手动").format(tz=tz_label(v))
            if not self.e_tz.get().strip():
                return tr("手动：填 -12~14 的数字，如 5.5")
            return tr("手动值无效，退回 {tz}").format(tz=tz_label(core.local_tz_hours()))
        return tr("时区 {tz} · 按系统").format(tz=tz_label(core.local_tz_hours()))

    def set_language(self, code):
        code = i18n.set_language(code)
        self.cfg["lang"] = code
        self.save_cfg()
        self.title(tr("{app} · 写表").format(app=tr(APP_NAME)))
        for fn in self._lang_hooks:
            fn()
        self.btn_lang.config(text="中" if code == "zh" else "EN")
        self.fill_list()
        self._render_count()
        self._render_tle_line()
        if self.passes:
            self.show_passes(self.passes)
        if getattr(self, "sat", None):
            self._rebuild_trans_values()
            self._show_freq()
            sel = self.tree.selection()
            if sel and self.rows:
                try:
                    self.show_detail(self.rows[int(sel[0])])
                except (ValueError, IndexError):
                    pass

    def _render_count(self):
        """左栏计数行：切语言时要按当前状态重画（set_sats/compute_next 也走这里）。
        报的是"列表里现在摆了几行"，不是缓存里存了几条 TLE —— 后者写在下面那行。"""
        n = len(self._list) if getattr(self, "_list", None) else sum(
            1 for s in self.sats if s.norad in self._amateur_norads())
        if getattr(self, "station_unset", False):
            self.lbl_count.config(text=tr("{n} 颗 未设台站").format(n=n))
        else:
            self.lbl_count.config(text=tr("{n} 颗").format(n=n))

    def _render_tle_line(self):
        """TLE 来源行：联网/缓存/离线三种说法，按当前状态重画。"""
        if not self.sats:
            return
        if getattr(self, "tle_offline", False):
            self.lbl_tle.config(text=tr("离线 · 缓存 {n} 颗 · {age}").format(
                n=len(self.sats), age=age_label(self.tle_at)), fg="#d9a95a")
        elif getattr(self, "tle_from_cache", False):
            self.lbl_tle.config(text=tr("缓存 {n} 颗 · {age}").format(
                n=len(self.sats), age=age_label(self.tle_at)), fg=DIM)
        elif self.tle_at:
            when = time.strftime("%m-%d %H:%M", time.localtime(self.tle_at))
            self.lbl_tle.config(text=tr("已更新 · {n} 颗 · {when}").format(
                n=len(self.sats), when=when), fg=ACC)
        else:
            self.lbl_tle.config(text=tr("已更新 · {n} 颗").format(n=len(self.sats)), fg=ACC)

    def _trans_values(self):
        s = self.sat
        trs = list(getattr(s, "transponders", []) or [])
        return [f"{t.down_mhz:.4f} MHz · {tr(t.name)}" for t in trs] + [tr(CUSTOM_TRANS_LABEL)]

    def _rebuild_trans_values(self):
        i = self.cb_trans.current()
        self.cb_trans.config(values=self._trans_values())
        if 0 <= i < len(self.cb_trans["values"]):
            self.cb_trans.current(i)

    # --- layout -----------------------------------------------------
    def build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=FG, arrowcolor=FG, borderwidth=0)
        # clam 主题的 readonly 态走单独一套 element color：只 configure 上面那条
        # 时输入区还是白底，配上 FG 的浅色字就是"看起来空着"。这里把 readonly /
        # disabled 的底色和字色一起钉住。
        style.map("TCombobox",
                  fieldbackground=[("readonly", CARD), ("disabled", CARD)],
                  foreground=[("readonly", FG), ("disabled", DIM)],
                  selectbackground=[("readonly", CARD)],
                  selectforeground=[("readonly", FG)],
                  arrowcolor=[("readonly", FG), ("disabled", DIM)])
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        foreground=FG, rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", background="#2f3538", foreground=DIM,
                        borderwidth=0)
        style.map("Treeview.Heading", background=[("active", "#3a4448")])
        style.map("Treeview", background=[("selected", "#3d5340")])

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True)

        # ---------------- left: satellite list
        left = tk.Frame(wrap, bg=SIDE, width=324)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        # 基座致谢：电台固件是 F4HWN 的 SSTV v6.3.0，多普勒这一层是本工具加的。
        # 侧栏底部常驻一行，不靠标题栏承载（标题栏会被任务栏截断）。
        # 这里同时是「关于」：产品名 + 版本 + 作者呼号都在这一行里。
        _lbl_about = tk.Label(left, bg=SIDE, fg=DIM, font=("Microsoft YaHei UI", 8),
                              justify="left", anchor="w")
        def _about():
            _lbl_about.config(text=tr("{app} v{v}\nF4HWN SSTV v6.3.0 · BG0FPJ").format(
                app=tr(APP_NAME), v=APP_VERSION))
        _about()
        self._lang_hooks.append(_about)
        _lbl_about.pack(side="bottom", fill="x", padx=10, pady=(0, 8))

        # 语言开关：中/EN 一键切换，选择存进 cfg，重启保持。
        self.btn_lang = tk.Button(left, text="中" if i18n.get_language() == "zh" else "EN",
                                  command=lambda: self.set_language(
                                      "en" if i18n.get_language() == "zh" else "zh"),
                                  bg="#2f3538", fg=DIM, bd=0, padx=8, pady=2,
                                  activebackground="#3a4448", activeforeground=FG,
                                  font=("Consolas", 9))
        self.btn_lang.pack(side="bottom", anchor="w", padx=10, pady=(0, 4))

        self._tx(tk.Label(left, text="卫星", bg=SIDE, fg=DIM,
                 font=("Microsoft YaHei UI", 9), anchor="w"), "卫星").pack(fill="x", padx=10, pady=(10, 2))

        row_tle = tk.Frame(left, bg=SIDE)
        row_tle.pack(fill="x", padx=10, pady=(4, 0))

        # force=True：手动点这个按钮就必须真联网刷一次，哪怕缓存里已经有 96 条
        # （以前有缓存就直接画缓存，一次网络都没发却报"已更新"，U-P-2）。
        self.btn_tle = tk.Button(row_tle, text="更新 TLE",
                                 command=lambda: self.load_group(force=True),
                                 bg="#2f3538", fg=FG, bd=0, padx=10, pady=3,
                                 activebackground="#3a4448", activeforeground=FG,
                                 font=("Microsoft YaHei UI", 9))
        self._lang_hooks.append(lambda: self.btn_tle.config(text=tr("更新 TLE")))
        self.btn_tle.pack(side="left")

        self.lbl_tle = tk.Label(row_tle, text="", bg=SIDE, fg=DIM,
                                font=("Microsoft YaHei UI", 8), anchor="w")
        self.lbl_tle.pack(side="left", padx=(8, 0))

        # 时区口径写在界面上：默认系统（自带政策与夏令时），可手动覆盖（U-P-19 终版）。
        # 输入框必须带可见边框并自动聚焦——上一版跟背景同色，没人看出那是能打的框。
        row_tz = tk.Frame(left, bg=SIDE)
        row_tz.pack(fill="x", padx=10, pady=(2, 0))
        self.cb_tz_mode = ttk.Combobox(row_tz, width=10, state="readonly")
        self.cb_tz_mode.bind("<<ComboboxSelected>>", self.on_tz_mode)
        self.cb_tz_mode.pack(side="left")
        self.e_tz = tk.Entry(row_tz, width=7, bd=0,
                             bg=CARD, fg=FG, insertbackground=FG,
                             justify="center", font=("Consolas", 10),
                             highlightthickness=1, highlightbackground=LINE,
                             highlightcolor=ACC)
        self.e_tz.bind("<KeyRelease>", self.on_tz_edit)
        self.e_tz.pack(side="left", padx=(6, 0), ipady=2)
        self.lbl_tz = tk.Label(left, text="", bg=SIDE, fg=DIM,
                               font=("Microsoft YaHei UI", 8), anchor="w",
                               justify="left")
        self.lbl_tz.pack(fill="x", padx=10, pady=(2, 0))
        self._lang_hooks.append(self._tz_row_refresh)
        self._tz_row_refresh()

        self._tx(tk.Label(left, text="搜索：名字/别名/NORAD", bg=SIDE, fg=DIM,
                          font=("Microsoft YaHei UI", 9), anchor="w"),
                 "搜索：名字/别名/NORAD").pack(fill="x", padx=10, pady=(10, 2))
        self.e_search = tk.Entry(left, bg=CARD, fg=FG, bd=0, insertbackground=FG)
        self.e_search.pack(fill="x", padx=10, ipady=4)
        self.e_search.insert(0, "")
        self.e_search.bind("<KeyRelease>", lambda e: self.fill_list())
        # Esc 清空重列：搜完想回到"只看业余星"那一屏，不用一段段删字。
        self.e_search.bind("<Escape>", lambda e: (self.e_search.delete(0, "end"),
                                                  self.fill_list()))

        sortrow = tk.Frame(left, bg=SIDE)
        sortrow.pack(fill="x", padx=10, pady=(6, 0))
        self.list_sort = "time"
        self.sort_btns = {}
        for key, txt in (("time", "过境"), ("elev", "仰角"), ("name", "名称")):
            b = tk.Button(sortrow, text=tr(txt), bd=0, padx=6, pady=2,
                          bg=CARD, fg=DIM, activebackground="#3a4448",
                          activeforeground=FG, font=("Microsoft YaHei UI", 8),
                          command=lambda k=key: self.set_sort(k))
            b.pack(side="left", padx=(0, 3))
            self._lang_hooks.append(lambda b=b, t=txt: b.config(text=tr(t)))
            self.sort_btns[key] = b

        lb_frame = tk.Frame(left, bg=SIDE)
        lb_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))
        style = ttk.Style()
        style.configure("Sat.Treeview", background=CARD, fieldbackground=CARD,
                        foreground=FG, rowheight=22, borderwidth=0,
                        font=("Consolas", 9))
        style.configure("Sat.Treeview.Heading", background="#2b3134",
                        foreground=DIM, borderwidth=0,
                        font=("Microsoft YaHei UI", 8))
        style.map("Sat.Treeview", background=[("selected", "#3d5340")],
                  foreground=[("selected", FG)])
        style.layout("Sat.Treeview", [("Sat.Treeview.treearea", {"sticky": "nswe"})])
        cols = ("name", "time", "el")
        self.tv = ttk.Treeview(lb_frame, columns=cols, show="headings",
                               style="Sat.Treeview", selectmode="browse")
        for c, w, a in (("name", 196, "w"), ("time", 58, "center"),
                        ("el", 28, "center")):
            self.tv.heading(c, text="")
            self.tv.column(c, width=w, anchor=a, stretch=(c == "name"))
        sb = ttk.Scrollbar(lb_frame, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.tv.tag_configure("hot", foreground=HOT)
        self.tv.tag_configure("dim", foreground=DIM)
        self.tv.bind("<<TreeviewSelect>>", self.on_pick)
        self.tv.bind("<Double-Button-1>", lambda e: self.toggle_fav())
        self.set_sort("time")

        rowf = tk.Frame(left, bg=SIDE)
        rowf.pack(fill="x", padx=10, pady=(0, 10))
        self._tx(tk.Button(rowf, text="★ 收藏", command=self.toggle_fav,
                  bg="#2f3538", fg=HOT, bd=0, padx=8, pady=3,
                  activebackground="#3a4448", activeforeground=HOT), "★ 收藏").pack(side="left")
        self.lbl_count = tk.Label(rowf, text="", bg=SIDE, fg=DIM,
                                  font=("Consolas", 8))
        self.lbl_count.pack(side="right")

        # ---------------- right: content
        right = tk.Frame(wrap, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # card
        card = tk.Frame(right, bg=CARD)
        card.pack(fill="x", padx=14, pady=(12, 6))

        self.canvas = tk.Canvas(card, width=106, height=106, bg=CARD,
                                highlightthickness=0)
        self.canvas.pack(side="left", padx=(12, 10), pady=10)

        info = tk.Frame(card, bg=CARD)
        info.pack(side="left", fill="both", expand=True, padx=(0, 14), pady=10)

        r1 = tk.Frame(info, bg=CARD)
        r1.pack(fill="x")
        self.lbl_name = tk.Label(r1, text="", bg=CARD, fg=FG,
                                 font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        self.lbl_name.pack(side="left")
        self.lbl_norad = tk.Label(r1, text="", bg=CARD, fg=DIM,
                                  font=("Consolas", 9), anchor="e")
        self.lbl_norad.pack(side="right")

        r2 = tk.Frame(info, bg=CARD)
        r2.pack(fill="x", pady=(2, 0))
        self.lbl_freq = tk.Label(r2, text="—", bg=CARD, fg=ACC,
                                 font=("Consolas", 22, "bold"), anchor="w")
        self.lbl_freq.pack(side="left")
        self.lbl_tone = tk.Label(r2, text="", bg=CARD, fg=HOT,
                                 font=("Consolas", 10), anchor="w")
        self.lbl_tone.pack(side="left", padx=(12, 0), pady=(8, 0))
        self.lbl_mode = tk.Label(r2, text="", bg=CARD, fg=DIM,
                                 font=("Consolas", 10), anchor="w")
        self.lbl_mode.pack(side="left", padx=(10, 0), pady=(8, 0))

        # 同一颗星有好几条频点（ISS：中继 / SSTV / 语音 / APRS）。表里写哪条
        # 由这里选，默认第 0 条（中继），界面上的大字也跟着换 —— 否则操作员
        # 想写 SSTV，写进去的还是 437.800，屏幕上还看不出来。
        r2b = tk.Frame(info, bg=CARD)
        r2b.pack(fill="x", pady=(0, 2))
        self._tx(tk.Label(r2b, text="转发器", bg=CARD, fg=DIM,
                 font=("Microsoft YaHei UI", 8)), "转发器").pack(side="left")
        self.cb_trans = ttk.Combobox(r2b, values=[], state="readonly", width=32)
        self.cb_trans.pack(side="left", padx=(6, 0))
        self.cb_trans.bind("<<ComboboxSelected>>", self.on_trans_change)

        # 「自定义…」的两个输入框。默认不显示（pack_forget），选中下拉最后
        # 一项时才露出来：标称频率 = AOS 那一刻的频率，不是当前频率，所以
        # 文案里把"标称"写死，免得有人拿屏幕上的实时读数往里填。
        self.frm_custom = tk.Frame(info, bg=CARD)
        self._tx(tk.Label(self.frm_custom, text="下行 MHz（标称）", bg=CARD, fg=DIM,
                 font=("Microsoft YaHei UI", 8)), "下行 MHz（标称）").pack(side="left")
        self.ent_custom_down = tk.Entry(self.frm_custom, width=11, bg=CARD, fg=ACC,
                                        bd=0, insertbackground=FG, justify="center",
                                        font=("Consolas", 9))
        self.ent_custom_down.pack(side="left", padx=(4, 12), ipady=3)
        self._tx(tk.Label(self.frm_custom, text="上行 MHz（空=仅接收）", bg=CARD, fg=DIM,
                 font=("Microsoft YaHei UI", 8)), "上行 MHz（空=仅接收）").pack(side="left")
        self.ent_custom_up = tk.Entry(self.frm_custom, width=11, bg=CARD, fg=FG,
                                      bd=0, insertbackground=FG, justify="center",
                                      font=("Consolas", 9))
        self.ent_custom_up.pack(side="left", padx=(4, 12), ipady=3)
        self._tx(tk.Label(self.frm_custom, text="AOS 时刻的值", bg=CARD, fg=DIM,
                 font=("Microsoft YaHei UI", 8)), "AOS 时刻的值").pack(side="left")
        for _e in (self.ent_custom_down, self.ent_custom_up):
            _e.bind("<KeyRelease>", lambda ev: self.recalc())

        r3 = tk.Frame(info, bg=CARD)
        r3.pack(fill="x")
        self.lbl_extra = tk.Label(r3, text="", bg=CARD, fg=DIM,
                                  font=("Consolas", 9), anchor="w")
        self.lbl_extra.pack(side="left")

        self.lbl_blurb = tk.Label(info, text="", bg=CARD, fg=DIM, justify="left",
                                  anchor="nw", wraplength=640,
                                  font=("Microsoft YaHei UI", 9))
        self.lbl_blurb.pack(fill="x", expand=False, pady=(3, 0))

        # passes table
        mid = tk.Frame(right, bg=BG)
        mid.pack(fill="both", expand=True, padx=14, pady=4)

        fbar = tk.Frame(mid, bg=BG)
        fbar.pack(fill="x", pady=(0, 4))
        self._tx(tk.Label(fbar, text="筛选", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 8)), "筛选").pack(side="left", padx=(0, 6))
        self.el_filter = 0.0
        self.only_checked = False
        self.fbtns = {}
        for val, txt in ((0.0, "全部"), (20.0, "≥20°"), (40.0, "≥40°"),
                         (60.0, "≥60°")):
            b = tk.Button(fbar, text=tr(txt), bd=0, padx=8, pady=1,
                          bg=CARD, fg=DIM, activebackground="#3a4448",
                          activeforeground=FG, font=("Microsoft YaHei UI", 8),
                          command=lambda v=val: self.set_filter(v))
            b.pack(side="left", padx=(0, 4))
            self._lang_hooks.append(lambda b=b, t=txt: b.config(text=tr(t)))
            self.fbtns[val] = b
        self.btn_checked = tk.Button(fbar, text="只看勾选", bd=0, padx=8, pady=1,
                                     bg=CARD, fg=DIM, activebackground="#3a4448",
                                     activeforeground=FG,
                                     font=("Microsoft YaHei UI", 8),
                                     command=self.toggle_only_checked)
        self._lang_hooks.append(lambda: self.btn_checked.config(text=tr("只看勾选")))
        self.btn_checked.pack(side="left", padx=(8, 0))
        self.set_filter(0.0, redraw=False)

        cols = ("date", "up", "down", "dur", "el", "az")
        heads = [("date", "日期", 88, "date"), ("up", "开始", 76, "time"),
                 ("down", "结束", 76, "down"), ("dur", "时长", 68, "dur"),
                 ("el", "最高仰角", 96, "el"), ("az", "方位", 64, "az")]
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=7)
        for c, t, w, k in heads:
            self.tree.heading(c, text=tr(t), command=lambda k=k: self.sort_by(k))
            # stretch=False: with 6 columns ttk spreads the spare width evenly,
            # which used to shove 最高仰角 / 方位 past the right edge of the
            # window on a 1536 px desktop.
            self.tree.column(c, width=w, minwidth=w - 20, anchor="w",
                             stretch=False)
        self.tree.tag_configure("hot", foreground=HOT)
        self.tree.tag_configure("picked", background="#33413a")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_row)
        self.tree.bind("<Button-1>", self.on_click)

        self.lbl_hint = tk.Label(
            mid, text="点表头排序",
            bg=BG, fg=DIM, anchor="w", font=("Microsoft YaHei UI", 8))
        self._lang_hooks.append(lambda: self.lbl_hint.config(text=tr("点表头排序")))
        self.lbl_hint.pack(fill="x", pady=(3, 0))

        # detail
        det = tk.Frame(right, bg=CARD)
        det.pack(fill="x", padx=14, pady=(6, 4))

        self.detail = tk.Text(det, height=10, bg=CARD, fg=FG, bd=0,
                              highlightthickness=0, wrap="none",
                              font=("Consolas", 11), padx=12, pady=10,
                              insertbackground=CARD)
        self.detail.pack(fill="x")
        self.detail.tag_configure("k", foreground=DIM)
        self.detail.tag_configure("v", foreground=FG)
        self.detail.tag_configure("h", foreground=ACC)
        self.detail.tag_configure("o", foreground=HOT)
        self.detail.config(state="disabled")

        # params
        bot = tk.Frame(right, bg=BG)
        bot.pack(fill="x", padx=14, pady=(2, 0))

        self._tx(tk.Label(bot, text="纬度", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)), "纬度").pack(side="left")
        self.e_lat = self.entry(bot, str(self.cfg["lat"]), 8)
        self._tx(tk.Label(bot, text="经度", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)), "经度").pack(side="left")
        self.e_lon = self.entry(bot, str(self.cfg["lon"]), 8)

        self._tx(tk.Label(bot, text="天数", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)), "天数").pack(side="left", padx=(12, 0))
        self.e_days = self.entry(bot, str(self.cfg["days"]), 4)
        self._tx(tk.Label(bot, text="仰角°", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)), "仰角°").pack(side="left")
        self.e_el = self.entry(bot, str(self.cfg["min_el"]), 4)
        self._tx(tk.Label(bot, text="间隔s", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)), "间隔s").pack(side="left")
        self.e_step = self.entry(bot, str(self.cfg["step"]), 4)

        self._tx(tk.Button(bot, text="计算", command=self.recalc,
                  bg="#2f3538", fg=FG, bd=0, padx=10, pady=4,
                  activebackground="#3a4448", activeforeground=FG), "计算").pack(side="left", padx=10)

        # write row
        line2 = tk.Frame(right, bg=BG)
        line2.pack(fill="x", padx=14, pady=(8, 12))

        self.cb_port = ttk.Combobox(line2, values=self.ports(), width=12)
        self.cb_port.set(self.cfg.get("port") or "")
        self.cb_port.pack(side="left")

        self._tx(tk.Button(line2, text="端口", command=self.reload_ports,
                  bg="#2f3538", fg=FG, bd=0, padx=8, pady=4,
                  activebackground="#3a4448", activeforeground=FG), "端口").pack(side="left", padx=6)

        # 顺序改了：「加入待写 / 清空」是一组，中间隔一条竖线再把「写入电台」
        # 放到最右边。以前两个按钮紧挨着，手一滑点到大绿按钮就直接擦 8+ 个
        # 扇区（U-P-12）；现在点下去还会先弹二次确认。
        self.btn_queue = tk.Button(line2, text="＋ 待写", command=self.queue_add,
                                   bg="#2f3538", fg=FG, bd=0, padx=10, pady=4,
                                   activebackground="#3a4448", activeforeground=FG)
        self._lang_hooks.append(lambda: self.btn_queue.config(text=tr("＋ 待写")))
        self.btn_queue.pack(side="left", padx=(2, 2))

        self.btn_qclear = tk.Button(line2, text="清空", command=self.queue_clear,
                                    bg="#2f3538", fg=FG, bd=0, padx=8, pady=4,
                                    activebackground="#3a4448", activeforeground=FG)
        self._lang_hooks.append(lambda: self.btn_qclear.config(text=tr("清空")))
        self.btn_qclear.pack(side="left")

        tk.Frame(line2, bg=LINE, width=1, height=24).pack(side="left", padx=18)

        # 表区在原厂镜像上切在语音包中间，第一次装表必然撞上固件的防呆。
        # 这个勾选框就是"我明确知道要清掉那块地"的唯一入口：默认不勾，
        # 勾了才允许发 0x0776 借额度。放在按钮左边，不与按钮连点。
        self.var_unlock = tk.BooleanVar(value=False)
        self.chk_unlock = tk.Checkbutton(
            line2, text="允许擦除表区", variable=self.var_unlock,
            bg=BG, fg=DIM, selectcolor="#2f3538", bd=0, highlightthickness=0,
            activebackground=BG, activeforeground=FG,
            font=("Microsoft YaHei UI", 9))
        self._lang_hooks.append(lambda: self.chk_unlock.config(text=tr("允许擦除表区")))
        self.chk_unlock.pack(side="left", padx=(0, 2))

        self.btn_write = tk.Button(line2, text="写入电台", command=self.write_radio,
                                   bg="#3d6b30", fg="#eaf5e0", bd=0, padx=18, pady=5,
                                   font=("Microsoft YaHei UI", 10, "bold"),
                                   activebackground="#4a7f3a", activeforeground="#eaf5e0")
        self._lang_hooks.append(lambda: self.btn_write.config(text=tr("写入电台")))
        self.btn_write.pack(side="left", padx=12)

        self.prog = ttk.Progressbar(line2, length=150, mode="determinate")
        self.prog.pack(side="left", padx=6)

        self.lbl_status = tk.Label(line2, text="", bg=BG, fg=DIM,
                                   font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(side="left", padx=10)

        # 启动时界面就是英文（cfg 里存着 lang=en）的话，上面"创建时先写中文、
        # 钩子负责切换时重画"的那批控件必须当场补画一遍，否则按钮保持中文。
        for fn in self._lang_hooks:
            fn()

        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, ev):
        """Rewrap the description to the window width.

        It used to be pinned at 640 px, so on a narrow window the right-hand
        frames were laid out wider than the screen and the last table column
        ended up off-screen."""
        if ev.widget is not self:
            return
        w = max(360, ev.width - 720)
        if getattr(self, "_wrap_last", 0) == w:
            return
        self._wrap_last = w
        self.lbl_blurb.configure(wraplength=w)

    def entry(self, parent, value, width):
        e = tk.Entry(parent, width=width, bg=CARD, fg=FG, bd=0,
                     insertbackground=FG, justify="center",
                     font=("Consolas", 9))
        e.insert(0, value)
        e.pack(side="left", padx=(4, 8), ipady=3)
        return e

    def ports(self):
        if not HAVE_SERIAL:
            return []
        return [p.device for p in list_ports.comports()]

    def reload_ports(self):
        self.cb_port["values"] = self.ports()

    # --- satellite list ---------------------------------------------

    def _tle_for(self, norad):
        """TLE for one extra satellite - never blocks on the network.

        Order: disk cache (generous TTL, offline must still work), then the
        in-memory bulk file which is already downloaded for the group request.
        A per-satellite HTTP request here used to cost ~8 s each and made the
        whole list look stuck while celestrak was down.
        """
        cache = self._tle_cache()
        hit = cache.get(str(norad))
        if hit and len(hit) >= 3:
            # 缓存可能是 refresh_tle.py 写的 3 字段，也可能是本文件写的 4 字段
            # （第 4 个是抓取时间）。老代码直接取 hit[3]，三字段时 IndexError，
            # 结果整条"走本地缓存"的快速路径必崩，联网成功后也一直显示离线。
            stamp = tle_stamp(hit) or cache_file_time()
            # 老化判定以前是 `fresh or ... or not stamp`，三字段缓存 stamp 恒 0，
            # 于是 "not stamp" 永远为真 → 判定彻底失效，过期元素也照用（报告 第六部分·回归项 1 / U-P-3）。
            # 现在按 epoch 真判 14 天。
            fresh = bool(stamp) and (time.time() - stamp < TLE_TTL_DAYS * 86400)
            if fresh or self.tle_offline:
                return hit[0], hit[1], hit[2]
        try:
            bulk = core._fetch_bulk()
            got = bulk.get(int(norad))
            if got:
                name, l1, l2 = got
                cache[str(norad)] = [name, l1, l2, time.time()]
                with open(os.path.join(BASE, "tle_cache.json"), "w",
                          encoding="utf-8") as f:
                    json.dump(cache, f, ensure_ascii=False, indent=1)
                return name, l1, l2
        except Exception:
            pass
        return (hit[0], hit[1], hit[2]) if hit else None

    def _tle_cache(self):
        try:
            with open(os.path.join(BASE, "tle_cache.json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def load_group(self, force=False):
        """载入业余卫星表。

        force=True 用于「更新 TLE」按钮：手动点了就必须真联网刷一次，不再被
        "缓存里已有 ≥50 条" 的快速路径短路（以前点下去一次 HTTP 都没发，界面上
        却写"已更新"，U-P-2）。启动时的自动载入仍然走缓存优先，避免无网时
        等 celestrak 超时。
        """
        self.lbl_count.config(text=tr("载入…"))
        self.btn_tle.config(state="disabled")
        self.lbl_tle.config(text=tr("联网刷新 TLE…") if force else tr("载入 TLE…"), fg=DIM)
        self.tle_offline = False
        self.tle_from_cache = False
        self.tle_at = 0.0

        def work():
            try:
                # A cache that already holds the whole group is enough to build
                # the list, and it costs milliseconds. Going online first means
                # waiting out the celestrak timeout whenever that site is down,
                # which is what made this look like it had hung.
                cache = self._tle_cache()

                if (not force) and len(cache) >= 50:
                    sats = []

                    for norad_s, v in cache.items():
                        try:
                            norad = int(norad_s)
                            name, l1, l2 = v[0], v[1], v[2]
                            nm = sat_freq.EXTRA.get(norad) or name
                            st = core.Sat(norad, nm, 145.800, "未知", "")
                            st.tle = (name, l1, l2)
                            if norad in sat_freq.EXTRA:
                                sat_freq.fill_extra(st)
                            else:
                                sat_freq.fill(st)
                            sats.append(st)
                        except Exception:
                            continue

                    if sats:
                        # Only top up from the bulk file when it is already in
                        # memory - downloading it here would put the delay back
                        # that this whole branch exists to avoid.
                        have = {x.norad for x in sats} if core._BULK else set()
                        for norad in ([n for n in sat_freq.EXTRA
                                       if n not in have] if have else []):
                            got_one = self._tle_for(norad)
                            if not got_one:
                                continue
                            name, l1, l2 = got_one
                            st = core.Sat(norad, sat_freq.EXTRA[norad],
                                          145.800, "未知", "")
                            st.tle = (name, l1, l2)
                            sat_freq.fill_extra(st)
                            sats.append(st)
                        # 用最老的一条做 tle_at：界面提示的是"这批里最新的
                        # 数据有多旧"，报最差值才有意义（U-P-3）。
                        stamps = [tle_stamp(v) for v in cache.values()]
                        stamps = [s for s in stamps if s]
                        self.tle_at = min(stamps) if stamps else cache_file_time()
                        self.tle_from_cache = True
                        self._loaded = (sats, None)
                        return

                got = core.fetch_group("amateur")
                sats = []
                for name, l1, l2 in got:
                    try:
                        norad = int(l1[2:7])
                    except Exception:
                        continue
                    s = core.Sat(norad, name.strip(), 145.800, "未知", "")
                    s.tle = (name, l1, l2)
                    sat_freq.fill(s)
                    sats.append(s)
                have = {x.norad for x in sats}
                miss = [n for n in sat_freq.EXTRA if n not in have]
                for norad in miss:
                    got_one = self._tle_for(norad)
                    if not got_one:
                        continue
                    name, l1, l2 = got_one
                    s = core.Sat(norad, sat_freq.EXTRA[norad], 145.800, "未知", "")
                    s.tle = (name, l1, l2)
                    sat_freq.fill_extra(s)
                    sats.append(s)
                # 并池：联网那 96 颗盖不住缓存里的 15,985 条（RS90S 这类
                # 只在本地缓存里的星全靠这一步留下来）。
                sats = merge_cached(sats, self._tle_cache())
                self.tle_at = time.time()
                self.tle_offline = False
                self._loaded = (sats, None)
            except Exception as e:
                # No network: the cache already holds everything needed to
                # build and write a table, so load that instead of failing.
                sats = []
                stamps = []
                for norad_s, v in self._tle_cache().items():
                    try:
                        norad = int(norad_s)
                        name, l1, l2 = v[0], v[1], v[2]
                        nm = sat_freq.EXTRA.get(norad) or name
                        st = core.Sat(norad, nm, 145.800, "未知", "")
                        st.tle = (name, l1, l2)
                        if norad in sat_freq.EXTRA:
                            sat_freq.fill_extra(st)
                        else:
                            sat_freq.fill(st)
                        sats.append(st)
                        stamps.append(tle_stamp(v))
                    except Exception:
                        continue
                if sats:
                    stamps = [s for s in stamps if s]
                    self.tle_at = min(stamps) if stamps else cache_file_time()
                    self._loaded = (sats, None)
                    self.tle_offline = True
                    self.tle_err = str(e)
                else:
                    self._loaded = (None, str(e))
        self._loaded = None
        threading.Thread(target=work, daemon=True).start()
        self._poll_load()

    def _poll_load(self):
        got = self._loaded
        if got is None:
            self.lbl_count.config(text=tr("载入…"))
            self.after(300, self._poll_load)
            return
        sats, err = got
        if sats is None:
            self.lbl_count.config(text=tr("离线"))
            self.lbl_tle.config(text=tr("取失败"), fg="#d98a5a")
            self.btn_tle.config(state="normal")
            self.lbl_status.config(text=tr("卫星表失败：{err}").format(err=err), fg="#d98a5a")
            return
        # 离线提示交给 set_sats 统一出（它会带上"最老数据多久前"），这里不再
        # 单独写一遍再被覆盖。
        self.set_sats(sats, True)


    def compute_next(self, redraw=True):
        """让"下次过境"这一列作废：台站一改，之前算的每一行都不再成立。

        真正的计算挪进 fill_list 的 _fill_nextp —— 只算列表里现在摆着的几十行。
        整份缓存全算一遍是 15984 次 48 h 扫描（≈8 s），默认视图却只有 228 行。
        """
        # 台站没填就不算，也不假装算过。原来这里 float() 失败是静默 return，
        # 列表空着但一句提示都没有，新装上的人只会得出"这软件打不开数据"的结论。
        try:
            self._ground()
        except (TypeError, ValueError):
            self.nextp = {}
            self.station_unset = True
            try:
                self._render_count()
            except Exception:
                pass
            if redraw:
                self.fill_list()
            return
        self.station_unset = False
        self.nextp = {}
        self.np_err = []
        if redraw:
            self.fill_list()

    def _ground(self):
        return core.Ground(float(self.cfg.get("lat", "")),
                           float(self.cfg.get("lon", "")))

    def _fill_nextp(self, items):
        """给列表里这几行星补"下次过境"；算过的不重算（nextp 就是缓存）。"""
        if self.station_unset:
            return
        try:
            g = self._ground()
        except (TypeError, ValueError):
            return
        for s in items:
            if s.norad in self.nextp:
                continue
            tle = getattr(s, "tle", None)
            if not tle:
                self.np_err.append((s.norad, "no tle"))
                continue
            try:
                sr = Satrec.twoline2rv(tle[1], tle[2])
                r = core.next_pass_fast(sr, g, min_el=0.0, horizon_h=48.0)
            except Exception as e:
                self.np_err.append((s.norad, repr(e)))
                continue
            if r:
                self.nextp[s.norad] = r
            else:
                self.np_err.append((s.norad, "no pass"))

    def set_sats(self, sats, ok):
        self.group_ok = ok
        self.sats = sats
        self.by_norad = {s.norad: s for s in sats}
        self.nextp = {}
        self._render_count()
        self.btn_tle.config(state="normal")
        # 这里以前无条件写"TLE 已更新"，把 _poll_load 刚设置的"离线 · 用本地
        # 缓存"立刻盖掉，于是断网时界面也报"已更新"（U-P-2/U-P-3）。现在按来源分支：
        # 联网成功 / 命中缓存 / 离线回退，三种说法不一样（见 _render_tle_line）。
        self._render_tle_line()

        # Two stages on purpose: the list is usable the instant it is drawn,
        # and the "next pass" column fills in a beat later instead of the
        # whole window sitting blank while 96 orbits are scanned.
        self.compute_next(redraw=False)
        self.fill_list()
        want = self.cfg.get("norad", 25544)
        target = self.by_norad.get(want) or (sats[0] if sats else None)
        if target:
            self.select_sat(target)

    def set_sort(self, key):
        self.list_sort = key
        for k, b in self.sort_btns.items():
            b.config(bg="#3d5340" if k == key else CARD,
                     fg=FG if k == key else DIM)
        self.fill_list()

    def _grade(self, s):
        np_ = self.nextp.get(s.norad)
        if not np_:
            return 2
        return 0 if np_[1] >= self.low_el else 1

    def _next_key(self, s):
        np_ = self.nextp.get(s.norad)
        return np_[0].timestamp() if np_ else 9e18

    def _amateur_norads(self):
        """默认视图的名单：白名单 ∪ 频点表 ∪ 手动填过频点的星 ∪ 收藏。
        缓存里上千条商业星座靠这道闸挡在默认视图外，搜索时才放开。"""
        if self._am_base is None:
            self._am_base = amateur_base_norads()
        try:
            manual = {int(k) for k in (self.cfg.get("freq") or {})}
        except (TypeError, ValueError):
            manual = set()
        return self._am_base | manual | set(self.cfg.get("fav", []))

    def _label(self, s, fav):
        """列表里的名字：别名一起留着，放不下才退回第一段。
        "LUCA (RS90S)" 砍成 "LUCA"，等于把操作员搜的那串业余编号砍没了 ——
        行就在眼前，他以为这颗星没有。"""
        full = (s.name or "").strip()
        parts = name_parts(full)
        short = parts[0] if parts else full
        cn = None if i18n.get_language() == "en" else sat_freq.CNNAME.get(s.norad)
        if cn:
            if norm_key(short) and norm_key(short) in norm_key(cn):
                base = cn
            elif wide_len(f"{cn} · {full}") <= 25:
                base = f"{cn} · {full}"
            else:
                base = cn
        else:
            base = full if wide_len(full) <= 25 else short
        # 列只有 196 px，名字再长也只能裁尾巴：宁可省略号，别把别名整段砍没。
        if wide_len(base) > 25:
            while base and wide_len(base) + 2 > 25:      # "…" 本身占两格
                base = base[:-1]
            base += "…"
        return ("★" if s.norad in fav else " ") + base

    def fill_list(self):
        q = self.e_search.get().strip()
        fav = set(self.cfg.get("fav", []))
        tz = self.cur_tz()
        if q:
            items = [s for s in self.sats if sat_match(s, q)]
        else:
            am = self._amateur_norads()
            items = [s for s in self.sats if s.norad in am]
        # 排序键里有"下次过境"，所以这一列必须先在当前这几行上补齐再排。
        self._fill_nextp(items)
        key = self.list_sort
        if key == "time":
            items.sort(key=lambda s: (s.norad not in fav, self._grade(s),
                                      self._next_key(s)))
        elif key == "elev":
            items.sort(key=lambda s: (s.norad not in fav, self._grade(s), -(
                self.nextp.get(s.norad)[1] if self.nextp.get(s.norad) else -1)))
        else:
            items.sort(key=lambda s: (s.norad not in fav, self._grade(s), s.name))
        self._list = items
        self.tv.delete(*self.tv.get_children())
        for s in items:
            np_ = self.nextp.get(s.norad)
            if np_:
                aos, el = np_
                # aos 是 UTC 的裸 datetime；astimezone() 对裸时间按本机时区解释，
                # 等于又加了一遍偏移。统一走 local()，和表格/详情页同一个口径
                # （U-P-19）。
                t = local(aos, tz)
                txt = t.strftime("%H:%M") if t.date() == datetime.now().date() else t.strftime("%d %H:%M")
                els = f"{el:.0f}°"
            else:
                txt, els = "--:--", ""
            if self._grade(s) >= 1 or not s.known:
                tag = ("dim",)
            elif np_ and np_[1] >= 60:
                tag = ("hot",)
            else:
                tag = ()
            self.tv.insert("", "end", iid=str(s.norad),
                           values=(self._label(s, fav), txt, els), tags=tag)
        if self.sat:
            iid = str(self.sat.norad)
            if self.tv.exists(iid):
                self.tv.selection_set(iid)
                self.tv.see(iid)

    def on_pick(self, ev=None):
        sel = self.tv.selection()
        if not sel:
            return
        try:
            norad = int(sel[0])
        except ValueError:
            return
        for st in self._list:
            if st.norad == norad:
                self.select_sat(st)
                return

    def toggle_fav(self):
        if not self.sat:
            return
        fav = set(self.cfg.get("fav", []))
        if self.sat.norad in fav:
            fav.discard(self.sat.norad)
        else:
            fav.add(self.sat.norad)
        self.cfg["fav"] = sorted(fav)
        self.save_cfg()
        self.fill_list()

    def select_sat(self, s):
        self.sat = s
        self.cfg["norad"] = s.norad
        self.save_cfg()

        self.lbl_name.config(text=s.name)
        self.lbl_norad.config(text=f"NORAD {s.norad}")
        trs = list(getattr(s, "transponders", []) or [])
        # 最后一项永远是「自定义…」：星上一条预置都没有时，它就是唯一可选
        # 的那条（以前这里把下拉 disable 掉，等于这颗星根本写不进去）。
        self.cb_trans.config(values=self._trans_values())
        self.cb_trans.config(state="readonly")
        self.cb_trans.current(0)
        self.custom_tr = None
        self._last_trans_i = 0
        self._show_custom(not trs)
        self._fill_custom_defaults(0)
        self._show_freq()
        extra = []
        if s.aprs:
            extra.append(f"APRS {s.aprs:g} MHz")
        if s.sstv:
            sstv_mhz = getattr(s, "sstv_mhz", 0.0) or 0.0
            extra.append((f"SSTV {sstv_mhz:.3f} MHz {s.sstv}".strip()
                          if sstv_mhz else f"SSTV {s.sstv}"))
        self.lbl_extra.config(text="   ".join(extra))
        self.lbl_blurb.config(text=tr(s.blurb or "业余卫星"))
        self.draw_pic()

        try:
            self.satrec = core.Satrec.twoline2rv(s.tle[1], s.tle[2])
        except Exception:
            self.satrec = None
            return
        self.recalc()

    def _trs(self):
        """该星可选频点（不含下拉里的「自定义…」那一项）。"""
        s = self.sat
        return list(getattr(s, "transponders", []) or []) if s else []

    def _show_custom(self, show):
        # 塞在 r3（"APRS/SSTV" 那一行）前面，也就是 cb_trans 正下方；
        # pack_forget 之后不留空行。
        if show:
            self.frm_custom.pack(fill="x", pady=(1, 0), before=self.lbl_extra.master)
        else:
            self.frm_custom.pack_forget()

    def _custom_bad(self, bad):
        """下行框标红：框里的数现在给不出可用频率（用的是上一个有效值）。"""
        self.ent_custom_down.config(bg=BAD if bad else CARD)

    def _manual_freq(self, norad):
        """操作员为这颗星手动填过的 (下行, 上行)；没填过返回 None。
        频点表里没有它，就绝不替它编一个默认值。"""
        v = (self.cfg.get("freq") or {}).get(str(norad))
        if not isinstance(v, dict):
            return None
        try:
            down = float(v["down"])
            up = float(v.get("up") or 0.0)
        except (KeyError, TypeError, ValueError):
            return None
        return (down, up) if down > 0 else None

    def _remember_manual(self, norad, tr0):
        """手动频点算得出有效转发器就记住，下次重开还在（cfg["freq"]）。
        只在数值真的变了时落盘，免得每敲一个键写一次配置文件。"""
        table = self.cfg.setdefault("freq", {})
        want = {"down": round(tr0.down_mhz, 4), "up": round(tr0.up_mhz, 4)}
        if table.get(str(norad)) != want:
            table[str(norad)] = want
            self.save_cfg()

    def _fill_custom_defaults(self, up_from_index=0):
        """切星/切转发器时刷新两个框。

        下行默认是该星的标称下行；上行默认是"刚选中的那条转发器"的 up，
        没有就留空（= 仅接收）。进「自定义…」之前记着 _last_trans_i 就是为了
        这个：下拉已经停在最后一项时，current() 已经取不到刚才那条了。

        频点表里没有的星（known=False）两个框都从 cfg["freq"] 回填：它的
        freq_mhz 是载入时塞的 145.800 占位值，拿去当默认就是把假频点写进电台。
        """
        s = self.sat
        trs = self._trs()
        down = up = ""
        if s is not None:
            if s.known:
                down = f"{s.freq_mhz:.4f}"
                if 0 <= up_from_index < len(trs) and trs[up_from_index].up_mhz:
                    up = f"{trs[up_from_index].up_mhz:.4f}"
            else:
                saved = self._manual_freq(s.norad)
                if saved:
                    down = f"{saved[0]:.4f}"
                    up = f"{saved[1]:.4f}" if saved[1] else ""
        for e, v in ((self.ent_custom_down, down), (self.ent_custom_up, up)):
            e.delete(0, "end")
            if v:
                e.insert(0, v)
        self._custom_bad(False)

    def sel_trans(self):
        """当前选中的那条转发器；没有列表就返回 None（=仍按老字段走）。

        下拉最后一项是「自定义…」（索引 == len(transponders)）。它不是
        s.transponders 里的对象，每次取值都按两个输入框的内容重建一次，
        这样改了框里的数（还没按回车）也立刻生效。框里给不出可用频率时
        返回上一次有效的 custom_tr（首次为 None）并标红，绝不退化成 0。
        """
        s = self.sat
        if not s:
            return None
        trs = self._trs()
        i = self.cb_trans.current()
        if i == len(trs):
            # 亚音沿用该星主转发器（列表第 0 条）的 ctcss；没有列表就用老字段。
            base_tone = trs[0].tone_hz if trs else (getattr(s, "up_tone", 0.0) or 0.0)
            tr = sat_freq.make_custom_transponder(
                self.ent_custom_down.get(), self.ent_custom_up.get(), base_tone)
            if tr is None:
                self._custom_bad(True)
                return self.custom_tr
            self._custom_bad(False)
            self.custom_tr = tr
            return tr
        if not (0 <= i < len(trs)):
            if not trs:
                return None
            i = 0
        return trs[i]

    def _show_freq(self):
        """卡片上的频率大字/上行/模式，全部按当前选中的转发器显示。"""
        s = self.sat
        if not s:
            return
        tr0 = self.sel_trans()
        manual = False
        if not s.known:
            if tr0 is None:
                # 无频点又没填下行：不拿 145.800 冒充这颗星的频点。
                self.lbl_freq.config(text=tr("频率未知"))
                self.lbl_tone.config(text="")
                self.lbl_mode.config(text=tr("手动填下行频点"))
                return
            manual = True
            self._remember_manual(s.norad, tr0)
        if tr0 is None:
            tr0 = sat_freq.Transponder(s.mode or "下行", s.freq_mhz,
                                      getattr(s, "up_mhz", 0.0) or 0.0,
                                      getattr(s, "up_tone", 0.0) or 0.0)
        # 名字一起显示：只写"437.5500 MHz"时，操作员看不出这是 SSTV 还是中继。
        self.lbl_freq.config(text=f"{tr0.down_mhz:.4f} MHz · {tr(tr0.name)}")
        if tr0.up_mhz:
            t = tr(" · 亚音 {t} Hz").format(t=f"{tr0.tone_hz:g}") if tr0.tone_hz else ""
            self.lbl_tone.config(text=tr("上行 {m} MHz{t}").format(m=f"{tr0.up_mhz:.4f}", t=t))
        else:
            self.lbl_tone.config(text=tr("哑音 {t} Hz").format(t=f"{tr0.tone_hz:g}") if tr0.tone_hz else "")
        # 模式名已经在频率大字里了（"437.5500 MHz · SSTV"），右边再写一遍是叠字。
        # 只有手动填出来的频点要在这里留个记号：这数值不在频点表里，
        # 下次 AMSAT 改了频率没人会发现。
        self.lbl_mode.config(text=tr("手动") if manual else "")

    def on_trans_change(self, ev=None):
        trs = self._trs()
        i = self.cb_trans.current()
        if i == len(trs):
            # 进「自定义…」：上行默认值取刚才选中的那条，然后露出两个框。
            self._fill_custom_defaults(self._last_trans_i)
            self._show_custom(True)
        else:
            self._show_custom(False)
            self._custom_bad(False)     # 框藏起来了，别把红色留给下次
            if 0 <= i < len(trs):
                self._last_trans_i = i
        self._show_freq()
        # 换转发器 = 换中心频率，多普勒摆幅跟着变，过境要按新频率重算。
        if self.satrec:
            self.recalc()

    def draw_pic(self):
        c = self.canvas
        c.delete("all")
        png = os.path.join(PIC_DIR, f"{self.sat.norad}.png")
        if os.path.exists(png):
            try:
                from PIL import Image, ImageTk
                im = Image.open(png).convert("RGB").resize((106, 106))
                c.img = ImageTk.PhotoImage(im)
                c.create_image(0, 0, anchor="nw", image=c.img)
                return
            except Exception:
                pass
        c.create_oval(4, 4, 102, 102, outline="#3a4640", width=2)
        c.create_arc(10, 28, 96, 80, start=200, extent=140,
                     style="arc", outline=ACC, width=2)
        c.create_rectangle(44, 44, 62, 62, outline=ACC, width=2)
        c.create_line(32, 53, 44, 53, fill=ACC, width=2)
        c.create_line(62, 53, 74, 53, fill=ACC, width=2)
        c.create_text(53, 90, text=str(self.sat.norad), fill=DIM,
                      font=("Consolas", 8))

    # --- passes -----------------------------------------------------
    def params(self):
        try:
            lat = float(self.e_lat.get())
            lon = float(self.e_lon.get())
            days = int(float(self.e_days.get()))
            min_el = float(self.e_el.get())
            step = float(self.e_step.get())
        except ValueError:
            # 台站默认是空的（见 load_cfg），所以新装的人第一次点任何按钮都会走到
            # 这里。原来一句"都填数字"分不清是"没填"还是"填错了"，
            # 而这两件事的下一步动作完全不同。
            if not (self.e_lat.get().strip() and self.e_lon.get().strip()):
                messagebox.showerror(tr("台站"), tr(
                    "先填经纬度（十进制度，如 31.2304 / 121.4737，"
                    "西经南纬填负数）"))
            else:
                messagebox.showerror(tr("参数"), tr("经纬度/天数/仰角/间隔 填数字"))
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            messagebox.showerror(tr("参数"), tr("纬度 -90..90，经度 -180..180"))
            return None
        if step < core.DPH_STEP_MS_MIN / 1000.0:
            messagebox.showerror(tr("参数"),
                                 tr("间隔最小 {n} 秒").format(n=core.DPH_STEP_MS_MIN // 1000))
            return None
        if not (1 <= days <= 10):
            messagebox.showerror(tr("参数"), tr("天数 1..10"))
            return None
        self.cfg.update(lat=lat, lon=lon, days=days, min_el=min_el, step=step)
        self.save_cfg()
        return lat, lon, days, min_el, step

    def recalc(self):
        if not self.satrec:
            return
        p = self.params()
        if not p:
            return
        lat, lon, days, min_el, step = p
        gnd = core.Ground(lat, lon)
        # 把这一代的星抓成局部变量：计算期间用户点了别的星，后台线程不能去读
        # self.sat（那样旧星的过境会被打上新星的名字和频率）。
        sat_now = self.sat
        satrec_now = self.satrec
        # 中心频率取当前选中的那条转发器：选 SSTV 就按 437.550 算，默认仍是中继。
        tr_now = self.sel_trans()
        if tr_now is None and not sat_now.known:
            # 频点表里没有它、下行框又还填不出数：不拿 145.8 假装算过一遍过境。
            # 上一颗星的过境必须一起清掉，否则屏幕上是 A 星的时间配 B 星的名字。
            self.show_passes([])
            self.lbl_status.config(text=tr("先填下行频点"), fg="#d9a95a")
            return
        freq = (tr_now.down_mhz if tr_now else sat_now.freq_mhz) * 1e6
        self._recalc_gen = getattr(self, "_recalc_gen", 0) + 1
        gen = self._recalc_gen
        self.lbl_status.config(text=tr("计算中…"), fg=DIM)

        def work():
            ps = core.find_passes(satrec_now, gnd, freq, days=days,
                                  min_el=min_el, step=step,
                                  max_passes=core.DPH_PASS_MAX)
            for one in ps:
                one.name = sat_now.name[:16]
                one.norad = sat_now.norad
                one.step_ms = int(round(step * 1000))   # 样点自己的步长，写表时用它
                if tr_now:                              # 这趟是按哪条频点算的，写表就写哪条
                    one.freq10 = tr_now.freq10
                    one.trans_name = tr_now.name
                    one.up10 = tr_now.up10
                    one.ctcss10 = tr_now.ctcss10

            def apply():
                if gen != self._recalc_gen:
                    return          # 已经有更新一代的结果了，这份丢弃
                self._pass_tz = self._tz_hours()   # 这批样点用的时区，显示同基准
                self.show_passes(ps)
            self.after(0, apply)
        threading.Thread(target=work, daemon=True).start()


    def set_filter(self, val, redraw=True):
        self.el_filter = val
        for v, b in self.fbtns.items():
            b.config(bg="#3d5340" if v == val else CARD,
                     fg=FG if v == val else DIM)
        if redraw:
            self.show_passes(self.passes)

    def toggle_only_checked(self):
        self.only_checked = not self.only_checked
        self.btn_checked.config(bg="#3d5340" if self.only_checked else CARD,
                                fg=FG if self.only_checked else DIM)
        self.show_passes(self.passes)

    def sort_by(self, key):
        if key == "down":
            key = "time"
        if self.sort_key == key:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_key = key
            self.sort_rev = key in ("el", "dur")
        self.show_passes(self.passes)

    def show_passes(self, ps):
        self.passes = ps
        key = self.sort_key
        if key == "el":
            ordered = sorted(ps, key=lambda p: p.max_el, reverse=self.sort_rev)
        elif key == "dur":
            ordered = sorted(ps, key=lambda p: p.duration, reverse=self.sort_rev)
        elif key == "az":
            ordered = sorted(ps, key=lambda p: p.tca_az, reverse=self.sort_rev)
        else:
            ordered = sorted(ps, key=lambda p: p.aos, reverse=self.sort_rev)

        if self.el_filter > 0:
            ordered = [p for p in ordered if p.max_el >= self.el_filter]
        if self.only_checked:
            ordered = [p for p in ordered if id(p) in self.checked]

        self.rows = ordered
        ids = {id(p) for p in ps}
        # nothing is pre-selected any more: the operator picks what to write
        self.checked &= ids

        self.tree.delete(*self.tree.get_children())
        arrow = " ▼" if self.sort_rev else " ▲"
        base = {"date": "日期", "up": "开始", "down": "结束", "dur": "时长",
                "el": "最高仰角", "az": "方位"}
        sk = "time" if key == "time" else key
        for c, t in base.items():
            mark = arrow if (c == sk or (c == "up" and sk == "time")) else ""
            self.tree.heading(c, text=tr(t) + mark)

        for i, p in enumerate(ordered):
            tz = self.cur_tz()
            up, dn = local(p.aos, tz), local(p.los, tz)
            mark = "✓" if id(p) in self.checked else " "
            tags = []
            if p.max_el >= 60:
                tags.append("hot")
            if id(p) in self.checked:
                tags.append("picked")
            self.tree.insert("", "end", iid=str(i), tags=tags, values=(
                f"{mark} {up.strftime('%m-%d')}",
                up.strftime("%H:%M"),
                dn.strftime("%H:%M"),
                f"{p.duration // 60}:{p.duration % 60:02d}",
                f"{p.max_el:.0f}°",
                f"{p.tca_az:.0f}°",
            ))

        hot = sum(1 for p in ps if p.max_el >= 60)
        self.lbl_status.config(
            text=tr("{n} 个过境 · {hot} 个 60°+").format(n=len(ps), hot=hot), fg=DIM)
        if ordered:
            self.tree.selection_set("0")
            self.show_detail(ordered[0])

    def on_click(self, ev):
        row = self.tree.identify_row(ev.y)
        if not row:
            return
        p = self.rows[int(row)]
        if id(p) in self.checked:
            self.checked.discard(id(p))
        else:
            self.checked.add(id(p))
        tags = [t for t in self.tree.item(row, "tags") if t != "picked"]
        val = list(self.tree.item(row, "values"))
        if id(p) in self.checked:
            tags.append("picked")
            val[0] = "✓" + val[0][1:]
        else:
            val[0] = " " + val[0][1:]
        self.tree.item(row, tags=tags, values=val)

    def on_row(self, ev=None):
        sel = self.tree.selection()
        if sel:
            self.show_detail(self.rows[int(sel[0])])

    def show_detail(self, p):
        d = self.detail
        d.config(state="normal")
        d.delete("1.0", "end")

        def put(k, v, tag="v"):
            d.insert("end", f"{k}  ", "k")
            d.insert("end", f"{v}\n", tag)

        tr0 = self.sel_trans()
        down_mhz = tr0.down_mhz if tr0 else self.sat.freq_mhz
        up_mhz = tr0.up_mhz if tr0 else (getattr(self.sat, "up_mhz", 0.0) or 0.0)
        if tr0:
            # 选中条目自带哑音：SSTV 那条没有哑音，不能再回退去借中继的 67 Hz。
            tone_txt = f"{tr0.tone_hz:g} Hz" if tr0.tone_hz else ""
        else:
            tone_txt = self.sat.tone or ""
        mode_txt = tr(tr0.name if tr0 else self.sat.mode)
        # 频率取这趟样点自己带的值（算过境时用的就是它），不再按 known 现挑：
        # 手动频点的星同样要能看详情，而 145.8 那个占位值谁都不该看见。
        freq = (getattr(p, "freq10", 0) or round(down_mhz * 1e5)) * 10
        tz = self.cur_tz()
        up, tc, dn = local(p.aos, tz), local(p.tca, tz), local(p.los, tz)
        put(tr("升起"), tr("{t}   仰角 {el}°   方位 {az}°").format(
            t=up.strftime('%m-%d %H:%M:%S'), el=f"{max(0.0, p.aos_el):5.1f}", az=f"{p.aos_az:5.0f}"))
        put(tr("最高"), tr("{t}   仰角 {el}°   方位 {az}°").format(
            t=tc.strftime('%m-%d %H:%M:%S'), el=f"{p.max_el:5.1f}", az=f"{p.tca_az:5.0f}"), "h")
        put(tr("落下"), tr("{t}   仰角 {el}°   方位 {az}°").format(
            t=dn.strftime('%m-%d %H:%M:%S'), el=f"{max(0.0, p.los_el):5.1f}", az=f"{p.los_az:5.0f}"))
        d.insert("end", "\n")

        lo, hi = p.span
        put(tr("直线距离"), tr("{r} km   径向速度 {v} km/s").format(
            r=f"{p.tca_range:.0f}", v=f"{p.tca_rate:+.2f}"))
        put(tr("多普勒"), f"{hi:+.0f} … {lo:+.0f} Hz   @ {freq/1e6:.4f} MHz", "o")
        sstv_hz = getattr(self.sat, "sstv_mhz", 0.0) or 0.0
        sstv_txt = (f"{sstv_hz:.3f} MHz {self.sat.sstv or ''}".strip()
                    if sstv_hz > 0 else (self.sat.sstv or "—"))
        put(tr("收发"), tr("{m} {d} MHz ↓ / {u} MHz ↑{tone}").format(
            m=mode_txt, d=f"{down_mhz:.3f}", u=f"{up_mhz:.3f}",
            tone=(tr("  哑音 {t}").format(t=tone_txt) if tone_txt else "")))
        vr = getattr(self.sat, "voice_rx_mhz", 0.0) or 0.0
        if vr > 0:
            vt = getattr(self.sat, "voice_tx_mhz", 0.0) or 0.0
            put(tr("语音"), tr("{v} MHz ↓ / {u} MHz ↑（欧洲上行 145.200）   直通，不走中继").format(
                v=f"{vr:.3f}", u=f"{vt:.3f}"))
        put("SSTV", tr("{s}（只收）   APRS {a}").format(
            s=sstv_txt, a=f"{self.sat.aprs or '—'}{' MHz' if self.sat.aprs else ''}"))

        d.config(state="disabled")

    # --- write queue -------------------------------------------------
    def _all_sats(self):
        """池子里所有卫星对象（当前星 + 全量列表 + 列表里显示的那几行），按 norad 去重。

        队列可能攒了好几个星，刷新频率时必须能从 norad 找回各自的星对象；
        只拿 self.sat 的话，剩下那几颗就全成"找不到星"了。
        """
        out, seen = [], set()
        pool = [self.sat] + list(getattr(self, "sats", None) or []) \
            + list(getattr(self, "_list", None) or [])
        for st in pool:
            if st is None:
                continue
            try:
                key = int(getattr(st, "norad", 0) or 0) or id(st)
            except (TypeError, ValueError):
                key = id(st)
            if key in seen:
                continue
            seen.add(key)
            out.append(st)
        return out

    def queue_add(self):
        sel = [p for p in self.rows if id(p) in self.checked]
        if not sel:
            messagebox.showerror(tr("待写"), tr("先勾选过境"))
            return
        sat = self.sat
        tr = self.sel_trans()
        for p in sel:
            if not any(q is p for q in self.queue):
                # remember this pass's own satellite plan so a mixed queue
                # still writes the right frequency / uplink / tone per entry
                p.name = sat.name[:16]
                if tr:
                    p.freq10 = tr.freq10
                    p.up10 = tr.up10
                    p.ctcss10 = tr.ctcss10
                    p.trans_name = tr.name
                else:
                    p.freq10 = sat.freq10
                    p.up10 = int(round((getattr(sat, "up_mhz", 0.0) or 0.0) * 1e6 / 10))
                    p.ctcss10 = int(round((getattr(sat, "up_tone", 0.0) or 0.0) * 10))
                    p.trans_name = sat.mode or ""
                self.queue.append(p)
        self.queue_update()

    def queue_clear(self):
        self.queue = []
        self.queue_update()

    def queue_update(self):
        if not self.queue:
            self.lbl_status.config(text="", fg=DIM)
            return
        names = {}
        for p in self.queue:
            names[getattr(p, "name", "SAT")] = names.get(getattr(p, "name", "SAT"), 0) + 1
        parts = " ".join(f"{k}×{v}" for k, v in names.items())
        self.lbl_status.config(
            text=tr("待写 {n} 段：{parts}").format(n=len(self.queue), parts=parts), fg=ACC)

    # --- write ------------------------------------------------------
    def write_radio(self):
        if self.busy:
            return
        if not HAVE_SERIAL:
            messagebox.showerror(tr("串口"), tr("没装 pyserial：pip install pyserial"))
            return
        port = self.cb_port.get().strip()
        if not port:
            messagebox.showerror(tr("串口"), tr("先选串口"))
            return
        sel = list(self.queue) if self.queue else [p for p in self.rows if id(p) in self.checked]
        if not sel:
            messagebox.showerror(tr("过境"), tr("没勾选过境"))
            return
        sel.sort(key=lambda q: q.aos)

        # 队列非空时勾选是被忽略的（这是"攒多颗星一起写"的代价），但不能再
        # 静默：操作员换了星、勾了别的趟、点写入，结果写进去的还是队列里那几条。
        checked = [p for p in self.rows if id(p) in self.checked]
        if self.queue and checked and not all(any(c is q for q in self.queue) for c in checked):
            log_line("QUEUE", tr("写入用待写队列 {n} 段；{m} 段勾选未参与（要换请先清队列）")
                     .format(n=len(self.queue), m=len(checked)))

        # 队列里每条都是入队那一刻的快照；写之前按它自己的星 + 转发器重取一次，
        # 否则改完频率直接写入，落盘还是第一次入队的数（本次 bug 的根因）。
        try:
            changed, missing = refresh_entry_freqs(self._all_sats(), sel, self.sat)
        except Exception as e:                      # 刷新失败不能挡住写入
            log_line("QUEUE", f"refresh failed: {type(e).__name__}: {e}")
            changed, missing = 0, []

        if missing:
            messagebox.showwarning(
                tr("频率"), tr("这 {n} 段找不到对应的卫星，沿用原频率：\n\n{p}")
                .format(n=len(missing), p="\n".join(f"    {m}" for m in missing[:8])))
        for q in sel:
            log_line("QUEUE", "  {n:<16} {d:.4f} ↓  {u:.4f} ↑  {c}".format(
                n=str(getattr(q, "name", ""))[:16],
                d=(getattr(q, "freq10", 0) or 0) / 1e5,
                u=(getattr(q, "up10", 0) or 0) / 1e5,
                c=(f"{q.ctcss10 / 10:.1f} Hz" if getattr(q, "ctcss10", 0) else tr("无哑音"))))
        if changed:
            log_line("QUEUE", tr("已按当前转发器刷新 {n} 段频率").format(n=changed))

        # 自定义频点没有落在 sat.transponders 里，上面只能按星级默认兜底；
        # 对"当前这颗星 + 当前这条转发器"的条目，以界面上的值为准。
        try:
            tr_now = self.sel_trans()
            cur_norad = getattr(self.sat, "norad", None)
            if tr_now is not None and cur_norad is not None:
                f10, u10, c10 = _trans_freqs(tr_now)
                for q in sel:
                    if (getattr(q, "norad", None) == cur_norad
                            and str(getattr(q, "trans_name", "")) == str(getattr(tr_now, "name", ""))):
                        q.freq10, q.up10, q.ctcss10 = f10, u10, c10
        except Exception as e:
            log_line("QUEUE", f"custom trans refresh skipped: {type(e).__name__}: {e}")

        # 闸门按"这几段有没有下行频点"判，不按 sat.known 判：频点表里的星和
        # 操作员手动填出来的星走同一条路，两种都要有真数值才放行；0 写进表里
        # 固件会按 0 Hz 拍，等于发一张废表。
        nofreq = [q for q in sel if not getattr(q, "freq10", 0)]
        if nofreq:
            messagebox.showerror(
                tr("频率"), tr("{n} 段没有下行频点。\n\n"
                              "在这颗星的卡片里填「下行 MHz（标称）」，"
                              "重算过境后再写。").format(n=len(nofreq)))
            return

        # 条数上限是条目区的物理上限（16 + 102×40 = 0x1000），真正决定
        # "装不装得下"的是字节数，不是趟数：10 s 一条 946 B、5 s 一条
        # 1,846 B，同一张 64 KiB 表区能装的趟数差一倍。所以这里按
        # table_used() 的**实际**占用判，超了就从末尾（最晚的那趟）开始
        # 去掉，并把具体去掉哪几趟摆给操作员看。
        if len(sel) > core.DPH_PASS_MAX:
            messagebox.showerror(
                tr("过境"), tr("超过 {m} 条：条目区只放 {m} 条").format(m=core.DPH_PASS_MAX))
            return
        keep = core.fit_keep(sel)
        if keep < len(sel):
            used = core.table_used(sel)
            drop = sel[keep:]
            lines = "\n".join(
                f"    {q.aos.strftime('%m-%d %H:%M')}  {getattr(q, 'duration', 0)} s"
                for q in drop[:6])
            if len(drop) > 6:
                lines += "\n" + tr("…… 还有 {n} 趟").format(n=len(drop) - 6)
            msg = tr("这张表 {used} B，表区 {cap} B，\n超出 {over} B。\n\n"
                     "去掉最后 {d} 趟：剩 {left} 趟 / {fits} B\n{lines}\n\n"
                     "去掉这 {d} 趟？").format(
                used=f"{used:,}", cap=f"{core.DPH_REGION:,}", over=f"{used - core.DPH_REGION:,}",
                d=len(drop), left=len(sel) - len(drop),
                fits=f"{core.table_used(sel[:keep]):,}", lines=lines)
            if not messagebox.askyesno(tr("表格超了"), msg):
                return
            for q in drop:
                self.checked.discard(id(q))
                self.queue = [x for x in self.queue if x is not q]
            self.queue_update()
            self.show_passes(self.passes)   # 树里的勾会跟着消掉
            sel = sel[:keep]
            self.lbl_status.config(
                text=tr("去掉 {d} 趟 · {s} 趟 {b} B").format(
                    d=len(drop), s=len(sel), b=f"{core.table_used(sel):,}"), fg=ACC)
            if not sel:
                messagebox.showerror(tr("过境"), tr("一趟都装不下：调大间隔"))
                return

        p = self.params()
        if not p:
            return

        try:
            # 上行/哑音也按选中的那条走：选 SSTV（无上行）时别把中继的 145.990
            # 和 67 Hz 一起写进表，选语音直通时也别写成中继的上行。
            tr0 = self.sel_trans()
            if tr0:
                up10, ctcss10 = tr0.up10, tr0.ctcss10
            else:
                up_mhz = getattr(self.sat, "up_mhz", 0.0) or 0.0
                up_tone = getattr(self.sat, "up_tone", 0.0) or 0.0
                up10 = int(round(up_mhz * 1e6 / 10)) if up_mhz else 0
                ctcss10 = int(round(up_tone * 10)) if up_tone else 0

            # 频率三元组已经在上面 refresh_entry_freqs() 里逐条重取过了
            # （留在这里的只有"空表兜底"用的 up10/ctcss10 默认值）。

            # 样点是按 recalc 那一刻的 step 算出来的；这里如果直接用输入框当前值，
            # 用户改完间隔没重新计算就写表 → 表里 step 字段和样点实际间隔不符，
            # 固件按错误步长外推，整条多普勒曲线时间轴翻倍。优先用样点自带的。
            step_ms = sel[0].step_ms if getattr(sel[0], "step_ms", None) else int(round(p[4] * 1000))
            if any(getattr(q, "step_ms", step_ms) != step_ms for q in sel):
                messagebox.showwarning(tr("采样间隔"), tr("过境不是同一次算的，先点「计算」"))
                return
            tz = self._tz_hours()
            table = core.build_table(sel, tr0.freq10 if tr0 else self.sat.freq10,
                                     step_ms / 1000.0,
                                     name=self.sat.name[:16],
                                     up10=up10, ctcss10=ctcss10,
                                     tz_hours=tz)
        except Exception as e:
            # 以前只接 ValueError：struct.error / 索引错 / 内存错全都会冒进
            # Tk 回调里被打包吞掉，表现是"按钮点了没反应，一个字节没写"。
            messagebox.showerror(tr("表格"), f"{type(e).__name__}: {e}")
            return

        # guard 之前裸写在 Tk 回调里：上一版 FLASH_VOICE_PACK 盖住了表区，
        # guard 抛 ValueError 直接进 Tk 回调被吞掉 —— 现场表现是"点写入电台
        # 毫无反应，一个字节没写、也没有任何弹窗"。现在两层：常量已改对，
        # 这里再兜住，万一以后再有人把禁区写歪，至少能看见话。
        try:
            sectors = core.erase_plan(len(table))
            for a in sectors:
                core.guard(a, core.DPH_SECTOR)
            # 向上取整，不是"商加一"：表长 = 0x1000 + 6×总样点数，所以它真的
            # 可能正好是 4 KiB 的整数倍（总样点 10240 → 整 64 KiB，而 fit_keep
            # 用的是"超过才丢"，等号放行）。原来那句 //4096+1 在这种表上多数出
            # 一个扇区，guard(0x172000) 判越界，满容表被自己的地址自检拒写，
            # 弹出来的还是"地址自检没通过，一个字节都没写"这种看不出原因的话。
            for i in range((len(table) + core.DPH_SECTOR - 1) // core.DPH_SECTOR):
                core.guard(core.TABLE_START + i * core.DPH_SECTOR, core.DPH_SECTOR)
        except Exception as e:
            messagebox.showerror(tr("表格"), tr("地址自检失败，未写入：{e}").format(e=e))
            return

        # 二次确认：这个按钮一按下去就是擦掉若干个扇区再重写表头，以前它和
        # 「加入待写」紧挨着，误点等于直接毁掉电台里现有的表（U-P-12）。
        # 文案里写清要擦几个扇区、写几段，长过境的插值告警也一并带出来。
        warn = core.validate_warnings(table)
        # 写之前把每段要写的频率摆出来：以前只有一个"卫星 ISS"，操作员看不出
        # 写进去的是中继 437.800 还是 SSTV 437.550，选错了也不会有任何提示。
        heads = []
        for q in sel[:12]:
            heads.append(tr("写入：{name} · {f} MHz · {t} · {h}（10 Hz 单位）").format(
                name=str(getattr(q, 'name', 'SAT')).strip(),
                f=f"{q.freq10 / 1e6:.4f}",
                t=getattr(q, 'trans_name', '') or '—',
                h=f"{q.freq10:,}"))
        if len(sel) > 12:
            heads.append(tr("…… 另有 {n} 段，频率同上表").format(n=len(sel) - 12))
        # 以前这里写的是 len(table) —— 那是**字节数**，184 段表会显示成
        # "写入 6328 段过境"，谁看了都以为算错了。段数是 len(sel)，字节数
        # 单独标出来；擦除量写"整块"，因为 erase_plan 就是恒擦满表区。
        msg = tr("擦除 {s} 扇区 / {k} KiB，写入 {n} 段 / {b} B\n卫星 {sat}    串口 {port}    时区 {tz}\n\n{heads}\n\n原有表整块替换，过程中不要断电。\n\n写入？").format(
            s=len(sectors), k=len(sectors) * 4, n=len(sel), b=f"{len(table):,}",
            sat=self.sat.name[:16], port=port, tz=tz_label(tz), heads="\n".join(heads))
        if self.var_unlock.get():
            msg += tr("\n\n已勾选「允许擦除表区」：先借擦除额度，整块清空后再写。不可恢复。")
        if warn:
            msg = tr("注意：\n· {w}\n\n{m}").format(w="\n· ".join(warn), m=msg)
        if not messagebox.askyesno(tr("写入电台 · 确认"), msg, default="no"):
            self.lbl_status.config(text=tr("已取消"), fg=DIM)
            return

        self.cfg["port"] = port
        self.save_cfg()

        self.busy = True
        self.btn_write.config(state="disabled")
        threading.Thread(target=self._writer,
                         args=(port, table, sectors, bool(self.var_unlock.get()), tz),
                         daemon=True).start()

    def _writer(self, port, table, sectors, allow_unlock=False, tz_hours=None):
        notes = {}
        def tick(text, done, total):
            self.after(0, lambda: (self.prog.configure(value=100 * done / max(1, total)),
                                   self.lbl_status.config(text=text, fg=DIM)))
        try:
            st = radio_write(port, table, allow_unlock=allow_unlock,
                             tick=tick, notes=notes, tz_hours=tz_hours)
            self.after(0, lambda: self.lbl_status.config(
                text=tr("✓ 完成 · {n} 字节 · {s} 扇区{fw}").format(
                    n=st['len'], s=st['sectors'], fw=notes.get('fw', '')), fg=ACC))
        except Exception as e:
            msg = str(e)
            log_line("FAIL", msg.replace(chr(10), " / "))
            self.after(0, lambda: self.lbl_status.config(text=tr("✗ {msg}").format(msg=msg), fg="#d98a5a"))
        finally:
            self.after(0, lambda: (setattr(self, "busy", False),
                                   self.btn_write.config(state="normal"),
                                   self.prog.configure(value=0)))

if __name__ == "__main__":
    # 打包成 exe 之后这是窗口程序：起不来时屏幕上什么都不会留，
    # "双击没反应"分不清是没启动、启动后崩了、还是被杀软拦了。
    # 所以先落一行"我起来了"（带 Python 版本与数据目录），再把崩溃的
    # 完整 traceback 写进同一个文件 —— 日志里没有这一行，就等于进程
    # 从没起来过，对方只需要把 logs\pc-*.log 发回来即可定位。
    import traceback

    log_line("start", tr("{app} v{v} BG0FPJ · F4HWN SSTV · 打包={frozen} · py={py} · 数据目录={dir}").format(
        app=tr(APP_NAME), v=APP_VERSION, frozen=getattr(sys, 'frozen', False),
        py=sys.version.split()[0], dir=DATA_DIR))
    # 任务栏按 AppUserModelID 认人：不给的话 Windows 把窗口归到 python.exe / 解包器
    # 名下，任务栏图标就退回默认那只。显式报一个身份，分组和图标都归自己。
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "BG0FPJ.SimpleDoppler.PC")
        except Exception as e:
            log_line("warn", tr("AppUserModelID 设置失败：{e}").format(e=e))
    try:
        Tool().mainloop()
    except SystemExit:
        raise
    except BaseException:                       # noqa: BLE001 - 就是要兜住所有
        log_line("fatal", traceback.format_exc())
        raise
