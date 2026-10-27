"""Doppler tool - window.

Left: satellite list (favourites on top, search box). Right: the bird's card,
the passes over your site, the detail of the selected pass, and the button
that pushes the table into the radio.

Run:  python doppler_tool.py
"""
from __future__ import annotations

import json
import os
import struct
import threading
import time
import tkinter as tk
from datetime import datetime, timezone, timedelta
from tkinter import ttk, messagebox

import doppler_core as core

BASE = os.path.dirname(os.path.abspath(__file__))
import sat_freq
from sgp4.api import Satrec

try:
    import serial
    from serial.tools import list_ports
    HAVE_SERIAL = True
except Exception:
    HAVE_SERIAL = False

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(APP_DIR, "doppler_tool.json")
PIC_DIR = os.path.join(APP_DIR, "sat_pics")

BG = "#1e2224"
FG = "#d9ded9"
DIM = "#8a978d"
ACC = "#7fc25c"
HOT = "#d8b45a"
CARD = "#262b2d"
SIDE = "#22272a"
LINE = "#39423f"

# 时区不再在这里写死 +8：界面、表头、对时统一走 core.local_tz_hours()，
# 见下面的 local() / tz_label()（修 U-P-19）。


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
            raise TimeoutError("电台没有响应")
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def ask(self, cmd, payload=b"", expect=0, timeout=1.5):
        """Send one command, return the de-obfuscated reply body.

        The radio frames replies as 0xABCD + len + obfuscated body + footer,
        so the transport header, the body and the footer all have to be peeled
        off here; everything above this line only deals with plain bytes."""
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
                raise RuntimeError(f"回包头不对: {hdr.hex()}")
            n = hdr[2] | (hdr[3] << 8)
            body = xor_mask(self._read(n, deadline))
            self._read(4, deadline)      # footer, ignored
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
    def erase(self, addr):
        r = self.ask(0x0760, struct.pack("<I", addr), 5)
        if r[8]:
            raise RuntimeError(f"擦除失败 0x{addr:06X} code={r[8]}")
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
                                 expect=7)
                    # 固件 uart.c 把这块的编程结果放在回包 body[10]：以前只等
                    # 体长、不看状态字节，写失败只能靠"最后回读不符"暴露，现场
                    # 看到的是"地址对不上"而不是"这块没写进去"（报告 第六部分·回归项 13）。
                    if len(r) > 10 and r[10]:
                        last = f"写块失败 0x{here:06X} code={r[10]}"
                        time.sleep(0.04 * (attempt + 1))
                        continue
                    time.sleep(0.012)
                    back = self.read(here, len(chunk))
                    if back == chunk:
                        last = None
                        break
                    last = f"回读不符 @0x{here:06X}"
                except Exception as e:
                    last = str(e)
                time.sleep(0.04 * (attempt + 1))
            if last:
                raise TimeoutError(f"写入 0x{here:06X} 失败：{last}")

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
            raise RuntimeError(f"对时失败 code={r[8]}")
        return secs

    CHUNK = 128      # the radio only ever moves one flash chunk per command

    def read(self, addr, length):
        out = bytearray()
        while len(out) < length:
            n = min(self.CHUNK, length - len(out))
            payload = struct.pack("<I", addr + len(out)) + struct.pack("<H", n)
            r = None
            for attempt in range(3):
                try:
                    r = self.ask(0x0764, payload, 7 + n)
                except Exception:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                break
            if r is None:
                raise TimeoutError(f"回读 0x{addr + len(out):06X} 三次都没回包")
            if r[10]:
                raise RuntimeError(f"回读失败 0x{addr + len(out):06X} code={r[10]}")
            out += r[11:11 + n]
        return bytes(out)


def local(dt):
    """UTC → 本机本地时刻。

    以前硬编码 +8，而写表用的 core.local_tz_hours() 取系统时区；非 UTC+8
    的机器上"界面看到的"和"真正写进电台的"差 8−tz 小时（U-P-19）。
    现在两边同源。"""
    return dt + timedelta(hours=core.local_tz_hours())


def tz_label():
    """UTC 偏移的显示串。

    负时区（如 UTC-3:30）必须拼成 "-3:30"，不能直接 f"UTC{int(h):+d}"
    ——那会拼出 "UTC+-3" 这种非法字符串。"""
    h = core.local_tz_hours()
    sign = "-" if h < 0 else "+"
    total = int(round(abs(h) * 60))
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
        return "时间未知"
    d = max(0.0, time.time() - stamp)
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    return f"{int(d // 86400)} 天前"


# ------------------------------------------------------------------ app
class Tool(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("多普勒过境表 · F4HWN SSTV · R6")
        self.geometry("1240x840")
        try:
            self.state("zoomed")
        except Exception:
            self.attributes("-zoomed", True)
        self.minsize(980, 700)
        self.configure(bg=BG)
        self.cfg = self.load_cfg()

        self.sats = []            # Sat objects, favourites first
        self.tle_at = 0.0
        self.tle_offline = False  # 以前只在 except 分支里才创建，命中缓存时反而炸
        self.tle_err = ""
        self.tle_from_cache = False
        self.by_norad = {}
        self.sat = None
        self.satrec = None
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
        base = {"lat": 43.83, "lon": 87.62, "days": 3, "min_el": 10.0,
                "step": 1.0, "norad": 25544, "port": "", "fav": [25544],
                "freq": {}}
        try:
            with open(CONFIG, encoding="utf-8") as f:
                base.update(json.load(f))
        except Exception:
            pass
        return base

    def save_cfg(self):
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception:
            pass

    # --- layout -----------------------------------------------------
    def build(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=FG, arrowcolor=FG, borderwidth=0)
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

        tk.Label(left, text="卫星", bg=SIDE, fg=DIM,
                 font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", padx=10, pady=(10, 2))

        row_tle = tk.Frame(left, bg=SIDE)
        row_tle.pack(fill="x", padx=10, pady=(4, 0))

        # force=True：手动点这个按钮就必须真联网刷一次，哪怕缓存里已经有 96 条
        # （以前有缓存就直接画缓存，一次网络都没发却报"已更新"，U-P-2）。
        self.btn_tle = tk.Button(row_tle, text="更新 TLE",
                                 command=lambda: self.load_group(force=True),
                                 bg="#2f3538", fg=FG, bd=0, padx=10, pady=3,
                                 activebackground="#3a4448", activeforeground=FG,
                                 font=("Microsoft YaHei UI", 9))
        self.btn_tle.pack(side="left")

        self.lbl_tle = tk.Label(row_tle, text="", bg=SIDE, fg=DIM,
                                font=("Microsoft YaHei UI", 8), anchor="w")
        self.lbl_tle.pack(side="left", padx=(8, 0))

        # 时区口径写在界面上：用户不用猜"界面这个 8 是从哪来的"（U-P-19）。
        tk.Label(left, text=f"本机时区 {tz_label()}", bg=SIDE, fg=DIM,
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x", padx=10)

        self.e_search = tk.Entry(left, bg=CARD, fg=FG, bd=0, insertbackground=FG)
        self.e_search.pack(fill="x", padx=10, ipady=4)
        self.e_search.insert(0, "")
        self.e_search.bind("<KeyRelease>", lambda e: self.fill_list())

        sortrow = tk.Frame(left, bg=SIDE)
        sortrow.pack(fill="x", padx=10, pady=(6, 0))
        self.list_sort = "time"
        self.sort_btns = {}
        for key, txt in (("time", "过境"), ("elev", "仰角"), ("name", "名称")):
            b = tk.Button(sortrow, text=txt, bd=0, padx=6, pady=2,
                          bg=CARD, fg=DIM, activebackground="#3a4448",
                          activeforeground=FG, font=("Microsoft YaHei UI", 8),
                          command=lambda k=key: self.set_sort(k))
            b.pack(side="left", padx=(0, 3))
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
        tk.Button(rowf, text="★ 收藏/取消", command=self.toggle_fav,
                  bg="#2f3538", fg=HOT, bd=0, padx=8, pady=3,
                  activebackground="#3a4448", activeforeground=HOT).pack(side="left")
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
        tk.Label(fbar, text="筛选", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(0, 6))
        self.el_filter = 0.0
        self.only_checked = False
        self.fbtns = {}
        for val, txt in ((0.0, "全部"), (20.0, "≥20°"), (40.0, "≥40°"),
                         (60.0, "≥60°")):
            b = tk.Button(fbar, text=txt, bd=0, padx=8, pady=1,
                          bg=CARD, fg=DIM, activebackground="#3a4448",
                          activeforeground=FG, font=("Microsoft YaHei UI", 8),
                          command=lambda v=val: self.set_filter(v))
            b.pack(side="left", padx=(0, 4))
            self.fbtns[val] = b
        self.btn_checked = tk.Button(fbar, text="只看勾选", bd=0, padx=8, pady=1,
                                     bg=CARD, fg=DIM, activebackground="#3a4448",
                                     activeforeground=FG,
                                     font=("Microsoft YaHei UI", 8),
                                     command=self.toggle_only_checked)
        self.btn_checked.pack(side="left", padx=(8, 0))
        self.set_filter(0.0, redraw=False)

        cols = ("date", "up", "down", "dur", "el", "az")
        heads = [("date", "日期", 88, "date"), ("up", "开始", 76, "time"),
                 ("down", "结束", 76, "down"), ("dur", "时长", 68, "dur"),
                 ("el", "最高仰角", 96, "el"), ("az", "方位", 64, "az")]
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=7)
        for c, t, w, k in heads:
            self.tree.heading(c, text=t, command=lambda k=k: self.sort_by(k))
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
            mid, text="点表头排序 · 点一行勾选 · 金色 = 仰角 60° 以上",
            bg=BG, fg=DIM, anchor="w", font=("Microsoft YaHei UI", 8))
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

        tk.Label(bot, text="纬度", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.e_lat = self.entry(bot, str(self.cfg["lat"]), 8)
        tk.Label(bot, text="经度", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.e_lon = self.entry(bot, str(self.cfg["lon"]), 8)

        tk.Label(bot, text="往后算几天", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(12, 0))
        self.e_days = self.entry(bot, str(self.cfg["days"]), 4)
        tk.Label(bot, text="最低仰角°", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.e_el = self.entry(bot, str(self.cfg["min_el"]), 4)
        tk.Label(bot, text="采样间隔s", bg=BG, fg=DIM,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.e_step = self.entry(bot, str(self.cfg["step"]), 4)

        tk.Button(bot, text="重新计算", command=self.recalc,
                  bg="#2f3538", fg=FG, bd=0, padx=10, pady=4,
                  activebackground="#3a4448", activeforeground=FG).pack(side="left", padx=10)

        # write row
        line2 = tk.Frame(right, bg=BG)
        line2.pack(fill="x", padx=14, pady=(8, 12))

        self.cb_port = ttk.Combobox(line2, values=self.ports(), width=12)
        self.cb_port.set(self.cfg.get("port") or "")
        self.cb_port.pack(side="left")

        tk.Button(line2, text="刷新端口", command=self.reload_ports,
                  bg="#2f3538", fg=FG, bd=0, padx=8, pady=4,
                  activebackground="#3a4448", activeforeground=FG).pack(side="left", padx=6)

        # 顺序改了：「加入待写 / 清空」是一组，中间隔一条竖线再把「写入电台」
        # 放到最右边。以前两个按钮紧挨着，手一滑点到大绿按钮就直接擦 8+ 个
        # 扇区（U-P-12）；现在点下去还会先弹二次确认。
        self.btn_queue = tk.Button(line2, text="＋ 加入待写", command=self.queue_add,
                                   bg="#2f3538", fg=FG, bd=0, padx=10, pady=4,
                                   activebackground="#3a4448", activeforeground=FG)
        self.btn_queue.pack(side="left", padx=(2, 2))

        self.btn_qclear = tk.Button(line2, text="清空", command=self.queue_clear,
                                    bg="#2f3538", fg=FG, bd=0, padx=8, pady=4,
                                    activebackground="#3a4448", activeforeground=FG)
        self.btn_qclear.pack(side="left")

        tk.Frame(line2, bg=LINE, width=1, height=24).pack(side="left", padx=18)

        self.btn_write = tk.Button(line2, text="写入电台", command=self.write_radio,
                                   bg="#3d6b30", fg="#eaf5e0", bd=0, padx=18, pady=5,
                                   font=("Microsoft YaHei UI", 10, "bold"),
                                   activebackground="#4a7f3a", activeforeground="#eaf5e0")
        self.btn_write.pack(side="left", padx=12)

        self.prog = ttk.Progressbar(line2, length=150, mode="determinate")
        self.prog.pack(side="left", padx=6)

        self.lbl_status = tk.Label(line2, text="", bg=BG, fg=DIM,
                                   font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(side="left", padx=10)

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
        self.lbl_count.config(text="载入中…")
        self.btn_tle.config(state="disabled")
        self.lbl_tle.config(text="联网刷新 TLE…" if force else "载入 TLE…", fg=DIM)
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
            self.lbl_count.config(text="载入中…")
            self.after(300, self._poll_load)
            return
        sats, err = got
        if sats is None:
            self.lbl_count.config(text="离线")
            self.lbl_tle.config(text="取失败", fg="#d98a5a")
            self.btn_tle.config(state="normal")
            self.lbl_status.config(text=f"取卫星表失败：{err}", fg="#d98a5a")
            return
        # 离线提示交给 set_sats 统一出（它会带上"最老数据多久前"），这里不再
        # 单独写一遍再被覆盖。
        self.set_sats(sats, True)


    def compute_next(self, redraw=True):
        """Next pass for every satellite, vectorised SGP4 (95 sats in ~0.05 s).

        Called from a worker thread by set_sats(): building 96 Satrec objects
        plus a 48 h scan is fast, but doing it inline still froze the window
        for a moment on every TLE refresh."""
        try:
            g = core.Ground(float(self.cfg.get("lat", 43.83)),
                            float(self.cfg.get("lon", 87.62)))
        except Exception:
            return
        np_ = {}
        self.np_err = []
        for st in self.sats:
            tle = getattr(st, "tle", None)
            if not tle:
                self.np_err.append((st.norad, "no tle"))
                continue
            try:
                sr = Satrec.twoline2rv(tle[1], tle[2])
                r = core.next_pass_fast(sr, g, min_el=0.0, horizon_h=48.0)
            except Exception as e:
                self.np_err.append((st.norad, repr(e)))
                continue
            if r:
                np_[st.norad] = r
            else:
                self.np_err.append((st.norad, "no pass"))
        self.nextp = np_
        if redraw:
            self.fill_list()

    def set_sats(self, sats, ok):
        self.group_ok = ok
        self.sats = sats
        self.by_norad = {s.norad: s for s in sats}
        self.nextp = {}
        self.lbl_count.config(text=f"{len(sats)} 颗")
        self.btn_tle.config(state="normal")
        # 这里以前无条件写"TLE 已更新"，把 _poll_load 刚设置的"离线 · 用本地
        # 缓存"立刻盖掉，于是断网时界面也报"已更新"（U-P-2/U-P-3）。现在按来源分支：
        # 联网成功 / 命中缓存 / 离线回退，三种说法不一样。
        if getattr(self, "tle_offline", False):
            self.lbl_tle.config(text=f"离线 · 本地缓存 {len(sats)} 颗 · 最老 "
                                     f"{age_label(self.tle_at)}", fg="#d9a95a")
        elif getattr(self, "tle_from_cache", False):
            self.lbl_tle.config(text=f"用缓存 {len(sats)} 颗 · 最老 "
                                     f"{age_label(self.tle_at)}（点「更新 TLE」联网刷新）",
                                fg=DIM)
        elif self.tle_at:
            when = time.strftime("%m-%d %H:%M", time.localtime(self.tle_at))
            self.lbl_tle.config(text=f"TLE 已更新 · {len(sats)} 颗 · 抓取 {when}",
                                fg=ACC)
        else:
            self.lbl_tle.config(text=f"TLE 已更新 · {len(sats)} 颗", fg=ACC)

        # Two stages on purpose: the list is usable the instant it is drawn,
        # and the "next pass" column fills in a beat later instead of the
        # whole window sitting blank while 96 orbits are scanned.
        self.fill_list()
        want = self.cfg.get("norad", 25544)
        target = self.by_norad.get(want) or (sats[0] if sats else None)
        if target:
            self.select_sat(target)

        def work():
            try:
                self.compute_next(redraw=False)
            except Exception:
                return
            self.after(0, self.fill_list)

        threading.Thread(target=work, daemon=True).start()

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

    def _label(self, s, fav):
        cn = sat_freq.CNNAME.get(s.norad)
        if cn:
            short = s.name.split(" (")[0]
            combo = f"{cn} · {short}"
            wide = sum(2 if ord(c) > 127 else 1 for c in combo)
            if short.upper() in cn.upper():
                base = cn
            else:
                base = combo if wide <= 25 else cn
        else:
            base = s.name.split(" (")[0]
        return ("★" if s.norad in fav else " ") + base

    def fill_list(self):
        q = self.e_search.get().strip().lower()
        fav = set(self.cfg.get("fav", []))
        items = []
        for s in self.sats:
            if q and q not in s.name.lower() and q not in str(s.norad):
                continue
            items.append(s)
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
                t = local(aos)
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
        self.lbl_freq.config(text=f"{s.freq_mhz:.4f} MHz" if s.known else "频率未知")
        up = getattr(s, "up_mhz", 0.0) or 0.0
        up_tone = getattr(s, "up_tone", 0.0) or 0.0
        if up:
            t = f" · 亚音 {up_tone:g}" if up_tone else ""
            self.lbl_tone.config(text=f"上行 {up:.3f} MHz{t}")
        else:
            self.lbl_tone.config(text=f"哑音 {s.tone}" if s.tone else "")
        self.lbl_mode.config(text=s.mode)
        extra = []
        if s.aprs:
            extra.append(f"APRS {s.aprs:g} MHz")
        if s.sstv:
            extra.append(f"SSTV {s.sstv}")
        self.lbl_extra.config(text="   ".join(extra))
        self.lbl_blurb.config(text=s.blurb or ("celestrak 目录里的业余卫星，"
                                               "频率没在本地表里，可以自己在 sat_freq.py 补。"))
        self.draw_pic()

        try:
            self.satrec = core.Satrec.twoline2rv(s.tle[1], s.tle[2])
        except Exception:
            self.satrec = None
            return
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
            messagebox.showerror("参数", "经纬度 / 天数 / 仰角 / 间隔 都填数字")
            return None
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            messagebox.showerror("参数", "纬度 -90..90，经度 -180..180")
            return None
        if step < 0.5:
            messagebox.showerror("参数", "采样间隔最小 0.5 秒")
            return None
        if not (1 <= days <= 10):
            messagebox.showerror("参数", "天数 1..10")
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
        freq = sat_now.freq_mhz * 1e6 if sat_now.known else 145.8e6
        self._recalc_gen = getattr(self, "_recalc_gen", 0) + 1
        gen = self._recalc_gen
        self.lbl_status.config(text="计算中…", fg=DIM)

        def work():
            ps = core.find_passes(satrec_now, gnd, freq, days=days,
                                  min_el=min_el, step=step,
                                  max_passes=core.DPH_ENTRY_MAX)
            for one in ps:
                one.name = sat_now.name[:16]
                one.norad = sat_now.norad
                one.step_ms = int(round(step * 1000))   # 样点自己的步长，写表时用它

            def apply():
                if gen != self._recalc_gen:
                    return          # 已经有更新一代的结果了，这份丢弃
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
            self.tree.heading(c, text=t + mark)

        for i, p in enumerate(ordered):
            up, dn = local(p.aos), local(p.los)
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
            text=f"{len(ps)} 个过境（{hot} 个 60° 以上）", fg=DIM)
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

        freq = self.sat.freq_mhz * 1e6 if self.sat.known else 145.8e6
        up, tc, dn = local(p.aos), local(p.tca), local(p.los)
        put("升起", f"{up.strftime('%m-%d %H:%M:%S')}   仰角 {max(0.0, p.aos_el):5.1f}°   方位 {p.aos_az:5.0f}°")
        put("最高", f"{tc.strftime('%m-%d %H:%M:%S')}   仰角 {p.max_el:5.1f}°   方位 {p.tca_az:5.0f}°", "h")
        put("落下", f"{dn.strftime('%m-%d %H:%M:%S')}   仰角 {max(0.0, p.los_el):5.1f}°   方位 {p.los_az:5.0f}°")
        d.insert("end", "\n")

        lo, hi = p.span
        put("直线距离", f"{p.tca_range:.0f} km   径向速度 {p.tca_rate:+.2f} km/s")
        put("多普勒", f"{hi:+.0f} … {lo:+.0f} Hz   @ {freq/1e6:.4f} MHz", "o")
        sstv_hz = getattr(self.sat, "sstv_mhz", 0.0) or 0.0
        sstv_txt = (f"{sstv_hz:.3f} MHz {self.sat.sstv or ''}".strip()
                    if sstv_hz > 0 else (self.sat.sstv or "—"))
        put("收发", f"中继 {freq/1e6:.3f} MHz ↓ / "
                    f"{(getattr(self.sat, 'up_mhz', 0.0) or 0.0):.3f} MHz ↑"
                    f"{(('  哑音 ' + self.sat.tone) if self.sat.tone else '')}"
                    f"   模式 {self.sat.mode}")
        vr = getattr(self.sat, "voice_rx_mhz", 0.0) or 0.0
        if vr > 0:
            vt = getattr(self.sat, "voice_tx_mhz", 0.0) or 0.0
            put("语音", f"{vr:.3f} MHz ↓ / {vt:.3f} MHz ↑（欧洲上行 145.200）"
                        f"   直通，不走中继")
        put("SSTV", f"{sstv_txt}（只收）   APRS {self.sat.aprs or '—'}"
                    f"{' MHz' if self.sat.aprs else ''}")

        d.config(state="disabled")

    # --- write queue -------------------------------------------------
    def queue_add(self):
        sel = [p for p in self.rows if id(p) in self.checked]
        if not sel:
            messagebox.showerror("待写", "先勾选几段过境")
            return
        sat = self.sat
        for p in sel:
            if not any(q is p for q in self.queue):
                # remember this pass's own satellite plan so a mixed queue
                # still writes the right frequency / uplink / tone per entry
                p.name = sat.name[:16]
                p.freq10 = sat.freq10
                p.up10 = int(round((getattr(sat, "up_mhz", 0.0) or 0.0) * 1e6 / 10))
                p.ctcss10 = int(round((getattr(sat, "up_tone", 0.0) or 0.0) * 10))
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
            text=f"待写 {len(self.queue)} 段：{parts}", fg=ACC)

    # --- write ------------------------------------------------------
    def write_radio(self):
        if self.busy:
            return
        if not self.sat.known:
            messagebox.showerror("频率", "这颗星没有已知下行频率，先在 sat_freq.py 里补上")
            return
        if not HAVE_SERIAL:
            messagebox.showerror("串口", "没装 pyserial：pip install pyserial")
            return
        port = self.cb_port.get().strip()
        if not port:
            messagebox.showerror("串口", "先选串口（插上电台、开机，再看下拉框）")
            return
        sel = list(self.queue) if self.queue else [p for p in self.rows if id(p) in self.checked]
        if not sel:
            messagebox.showerror("过境", "一个都没勾选，也还没有待写队列")
            return
        if len(sel) > core.DPH_ENTRY_MAX:
            messagebox.showerror("过境", f"最多 {core.DPH_ENTRY_MAX} 段，现在 {len(sel)} 段")
            return
        sel.sort(key=lambda q: q.aos)

        p = self.params()
        if not p:
            return

        try:
            up_mhz = getattr(self.sat, "up_mhz", 0.0) or 0.0
            up_tone = getattr(self.sat, "up_tone", 0.0) or 0.0
            up10 = int(round(up_mhz * 1e6 / 10)) if up_mhz else 0
            ctcss10 = int(round(up_tone * 10)) if up_tone else 0

            for q in sel:
                if not hasattr(q, "freq10"):
                    q.name = self.sat.name[:16]
                    q.freq10 = self.sat.freq10
                    q.up10 = up10
                    q.ctcss10 = ctcss10

            # 样点是按 recalc 那一刻的 step 算出来的；这里如果直接用输入框当前值，
            # 用户改完间隔没重新计算就写表 → 表里 step 字段和样点实际间隔不符，
            # 固件按错误步长外推，整条多普勒曲线时间轴翻倍。优先用样点自带的。
            step_ms = sel[0].step_ms if getattr(sel[0], "step_ms", None) else int(round(p[4] * 1000))
            if any(getattr(q, "step_ms", step_ms) != step_ms for q in sel):
                messagebox.showwarning("采样间隔", "勾选的过境不是同一次计算出来的，"
                                       "先点「重新计算」再写表")
                return
            table = core.build_table(sel, self.sat.freq10, step_ms / 1000.0,
                                     name=self.sat.name[:16],
                                     up10=up10, ctcss10=ctcss10)
        except Exception as e:
            # 以前只接 ValueError：struct.error / 索引错 / 内存错全都会冒进
            # Tk 回调里被打包吞掉，表现是"按钮点了没反应，一个字节没写"。
            messagebox.showerror("表格", f"{type(e).__name__}: {e}")
            return

        sectors = core.erase_plan(len(table))
        for a in sectors:
            core.guard(a, core.DPH_SECTOR)
        for i in range(len(table) // core.DPH_SECTOR + 1):
            core.guard(core.TABLE_START + i * core.DPH_SECTOR, core.DPH_SECTOR)

        # 二次确认：这个按钮一按下去就是擦掉若干个扇区再重写表头，以前它和
        # 「加入待写」紧挨着，误点等于直接毁掉电台里现有的表（U-P-12）。
        # 文案里写清要擦几个扇区、写几段，长过境的插值告警也一并带出来。
        warn = core.validate_warnings(table)
        msg = (f"将擦除 {len(sectors)} 个扇区（{len(sectors) * 4} KB），"
               f"写入 {len(table)} 段过境。\n"
               f"卫星：{self.sat.name[:16]}    串口：{port}\n\n"
               "电台里原有的过境表会被整块替换，写入过程中不要断电、不要拔线。\n\n"
               "确定现在写入？")
        if warn:
            msg = "注意：\n· " + "\n· ".join(warn) + "\n\n" + msg
        if not messagebox.askyesno("写入电台 · 确认", msg, default="no"):
            self.lbl_status.config(text="已取消写入", fg=DIM)
            return

        self.cfg["port"] = port
        self.save_cfg()

        self.busy = True
        self.btn_write.config(state="disabled")
        threading.Thread(target=self._writer, args=(port, table, sectors),
                         daemon=True).start()

    def _writer(self, port, table, sectors):
        link = None
        self._clock_note = ""
        try:
            link = Link(port)

            # Prove the radio is listening before anything is erased: a silent
            # cable or a COM port held by another program otherwise shows up as
            # a half-erased table.
            try:
                link.read(core.TABLE_START, 16)
            except Exception as e:
                raise RuntimeError(
                    f"电台没响应（{e}）——检查 USB 线、端口号，"
                    f"或者关掉别的占用 {port} 的软件")

            errs = core.validate_table(table)
            if errs:
                raise RuntimeError("表格自检没通过，已中止（电台里的旧表没动）："
                                   + "；".join(errs[:3]))

            # keep the failure visible: a silent skip meant a stale clock and a
            # countdown that quietly measured the wrong day
            try:
                link.set_clock()
                self._clock_note = ""
            except Exception as e:
                self._clock_note = f"（对时失败：{e}）"
            body_len = max(0, len(table) - core.DPH_HDR)
            total = len(sectors) + (body_len + 127) // 128 + 3
            done = [0]

            def tick(text):
                done[0] += 1
                self.after(0, lambda: (self.prog.configure(value=100 * done[0] / total),
                                       self.lbl_status.config(text=text, fg=DIM)))

            for a in sectors:
                link.erase(a)
                tick(f"擦除 0x{a:06X}")
            tick("擦除完成")

            # link.write() chunks in 128-byte pieces and reads each one back
            # before moving on, so a failure raises right here with the address.
            #
            # 顺序很重要：先样点、最后 16 字节表头。以前从偏移 0 顺序写，
            # 表头先落盘、样点最后，40 KB 中途掉线就留下一张
            # "magic/版本/count 全合法 + 样点全是垃圾"的半表 —— 电台会
            # 认下这张表，然后按 count 去读垃圾样点，整场都在乱调频。
            # 现在表头没落盘之前，那 16 字节是擦除后的 0xFF，固件只会报
            # NO TABLE，不会装作有一张好表。
            if body_len:
                link.write(core.TABLE_START + core.DPH_HDR, table[core.DPH_HDR:])
                tick(f"写入样点 {body_len} 字节")
            link.write(core.TABLE_START, table[:core.DPH_HDR])
            tick(f"写入表头 {core.DPH_HDR} 字节")

            hdr = link.read(core.TABLE_START, 16)
            if hdr[:4] != b"DPT1":
                raise RuntimeError(
                    f"写入完成但表头不对（读到 {hdr[:4].hex()}）："
                    f"电台里现在是半新半旧的状态，重新写一次即可")

            self.after(0, lambda: self.lbl_status.config(
                text=f"✓ 完成 · {len(table)} 字节 · {len(sectors)} 扇区"
                     f"{self._clock_note}", fg=ACC))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self.lbl_status.config(text=f"✗ {msg}", fg="#d98a5a"))
        finally:
            if link:
                link.close()
            self.after(0, lambda: (setattr(self, "busy", False),
                                   self.btn_write.config(state="normal"),
                                   self.prog.configure(value=0)))


if __name__ == "__main__":
    Tool().mainloop()
