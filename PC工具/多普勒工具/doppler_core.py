"""Doppler tool - core: TLE, passes, table packing, radio link.

No GUI here on purpose: every function below is testable from the CLI and the
window is a thin shell on top of it.
"""
from __future__ import annotations

import io
import json
import math

import numpy as np
import os
import struct
import time
import urllib.request
from datetime import datetime, timezone, timedelta

from sgp4.api import Satrec, jday

# ------------------------------------------------------------------ constants
EARTH_R = 6378.137          # km
FLAT = 1 / 298.257223563
OMEGA_E = 7.292115e-5       # rad/s
MU = 398600.4418            # km^3/s^2

DPH_BASE = 0x00162000
DPH_VERSION = 5
DPH_HDR = 16
DPH_ENTRY = 40
DPH_PT = 6                 # 一个样点：int16 偏移 + u8 仰角 + u16 斜距 + int8 变化率
DPH_ENTRY_MAX = 255        # 表头 count 是 u8，256 会直接 struct.error
DPH_SAMPLE_OFF = 0x1000        # 表头区留一整个扇区：样点从第二扇区起
DPH_SECTOR = 0x1000
# 64 KiB：0x162000..0x172000。下沿是 APP_SLOT_BASE(48) = 0x102000 + 48*0x2000
# （app_overlay.h），上沿离汉化包 16x16 字库（0x180000）还留 56 KiB 缓冲，
# 这一段在汉化镜像里实测全 0xFF（0/122,880 个非 FF 字节）。原厂镜像上这
# 64 KiB 正落在 A-law 语音包中段 —— 那段语音是用户决定放弃的（密度换精度）。
DPH_REGION = 0x10000         # 0x162000..0x172000 (64 KiB)，见下面的断言
SONIC_RESERVE_BASE = 0x1DE000  # mb_flash.h：这以上归 Sonic 4K 互操作保留区
# 一次最多写几趟。这个数是**条目区**的物理上限，不是"64 KiB 装得下几趟"：
# 条目从 +16 起、样点从 +0x1000 起，中间 4,080 B，102 条 × 40 B + 16 B
# 恰好填满那一扇区（16 + 102*40 = 4,096 = DPH_SAMPLE_OFF）。
#
# 上一版这里是 30，而且是被"最坏情况"反推出来的：30 条 × (40 B + 301 个
# 5 s 样点 × 6 B) = 55,380 B 塞进 64 KiB —— 可 10 s 栅格一条只有 946 B，
# 30 条才 32,492 B，一半表区白扔。条数上限不该由一个最坏步长决定。
#
# 现在"这张表装不装得下"按**真实字节**算：table_used() 给出实际占用，
# fit_keep() 给出从末尾往前最多留几趟；装不下时 GUI 让操作员去掉几趟。
# 与固件 DPH_PASS_MAX、App PassCalc.PASS_MAX 同一个数，verify_layout.py 当场比。
DPH_PASS_MAX = 102
# 样点栅格。固件侧 DPH_OffsetAt() 本来就是线性插值，所以把 1 s 改成 10 s
# 只是少存点、不改算法：437.8 MHz 多普勒二阶导峰值约 0.5 Hz/s²，
# 10 s 栅格插值误差 ≈ |f''|h²/8 ≈ 6 Hz，比未校准 76 ppm 的 33 kHz 小三个数量级。
# 样点栅格下限。固件 DPH_LoadEntry 拒收比这更密的表：5 s 栅格插值误差
# ≈ |f''|h²/8 ≈ 1.6 Hz（10 s 时是 6 Hz），都远小于 2.5 kHz 的 NFM 信道，
# 所以下限放开到 5 s，让 64 KiB 表区能用密度换精度；默认仍按 10 s 采样。
DPH_STEP_MS_MIN = 5000         # 固件 DPH_LoadEntry 拒收比这更密的表
DPH_STEP_S_DEFAULT = 10.0
DPH_DUR_S_MAX = 1500           # 固件容量断言里的那个上限，两边必须一致
# 一张表最多覆盖几个"本地日"。条目里那个日偏移是 flags 的低 4 bit
# （固件 dph_entry_t.flags 的 [3:0]），所以只能表达 0..15 —— 第 16 天会被
# 静默折回 +0：屏幕上日期少 16 天、到 AOS 的倒计时按折回后的日子算、
# "定时开始"直接判"时钟丢了"而不自杀。表本身不会写坏 flash，所以固件读不出
# 来（折回后的 +0 和合法的 +0 是一模一样的字节），只能由写表的一方拦住。
# 与 App 的 PassCalc.DAY_MAX 同一个数，verify_table_fit.py 当场比。
DPH_DAY_MAX = 15

# 擦除范围是从 DPH_REGION 推导的（erase_plan 的上限就是表区扇区数），
# 所以表区声明越界 = 真的会去擦保留区，而扇区擦除不可恢复。
# 原来这里到 0x1FF000，越进 Sonic 保留区 132 KiB。这条断言在每个 import
# 本模块的工具里都会跑一遍，等价于固件里那条 _Static_assert：
# 改错了当场炸，而不是等某个人的电台少一个 app 才发现。
assert DPH_BASE + DPH_REGION <= SONIC_RESERVE_BASE, (
    f"表区 0x{DPH_BASE:06X}+0x{DPH_REGION:X} 越进 Sonic 4K 保留区 "
    f"(0x{SONIC_RESERVE_BASE:06X})；收缩 DPH_REGION，别动保留区那条线")
assert DPH_SAMPLE_OFF % DPH_SECTOR == 0 and DPH_REGION % DPH_SECTOR == 0, (
    "表区/表头区必须是整扇区，否则擦除计划会在扇区中间切一刀")
# 表区上沿还有一道"别人的资产"：汉化包 16x16 字库起于 0x180000
# （doppler.h DPH_CN_ASSET_BASE）。这条是固件那条 _Static_assert 的镜像。
_CN_ASSET_BASE = 0x180000
assert DPH_BASE + DPH_REGION <= _CN_ASSET_BASE, (
    f"表区 0x{DPH_BASE:06X}+0x{DPH_REGION:X} 越过汉化字库起点 "
    f"0x{_CN_ASSET_BASE:06X}；收缩 DPH_REGION")

# 容量拆成两条结构性断言，不再拿"最坏情况"反推条数上限
# （FIX(CAP-BY-BYTES)，与固件 doppler.c 那两条 _Static_assert 一一对应）：
#   1. 条目区必须正好填满表头那一扇区，多一条就越界写进样点区；
#   2. 至少一趟满长过境要装得下，否则这套常量根本不可用。
# 至于"某张表几趟装得下"，按真实字节算 —— 见下面的 table_used() / fit_keep()。
_ENTRY_AREA = DPH_SAMPLE_OFF - DPH_HDR
assert DPH_HDR + DPH_PASS_MAX * DPH_ENTRY == DPH_SAMPLE_OFF, (
    f"{DPH_PASS_MAX} 条 {DPH_ENTRY} B 条目 + {DPH_HDR} B 表头 = "
    f"{DPH_HDR + DPH_PASS_MAX * DPH_ENTRY} B，不等于样点区起点 "
    f"0x{DPH_SAMPLE_OFF:X}（{DPH_SAMPLE_OFF} B）；条目区只有 {_ENTRY_AREA} B，"
    f"最多 {(DPH_SAMPLE_OFF - DPH_HDR) // DPH_ENTRY} 条")
_WORST_ONE = DPH_SAMPLE_OFF + DPH_ENTRY + (
    DPH_DUR_S_MAX * 1000 // DPH_STEP_MS_MIN + 1) * DPH_PT
assert _WORST_ONE <= DPH_REGION, (
    f"最坏情况的一趟（{DPH_DUR_S_MAX} s @ {DPH_STEP_MS_MIN} ms，"
    f"{(DPH_DUR_S_MAX * 1000 // DPH_STEP_MS_MIN + 1)} 个点）就要 {_WORST_ONE} B，"
    f"表区只有 {DPH_REGION} B；加大 DPH_REGION 或降低 DPH_DUR_S_MAX")


def table_used(passes):
    """这张表实际占多少字节，与 build_table 的排版一致（含 4 KiB 样点偏移）。"""
    return DPH_SAMPLE_OFF + sum(DPH_ENTRY + len(p.samples) * DPH_PT for p in passes)


def fit_keep(passes):
    """从末尾往前丢，最多能留下几趟（至少 1）。0 表示连一趟都装不下。"""
    used = DPH_SAMPLE_OFF
    keep = 0
    for p in passes:
        cost = DPH_ENTRY + len(p.samples) * DPH_PT
        if used + cost > DPH_REGION:
            break
        used += cost
        keep += 1
    return keep

# Where things live on the 2 MiB external flash. Nothing here is allowed to
# touch anything but TABLE region - the firmware slots (0x020000..0x0C0000),
# and everything from 0x102000 up to our table base. That stretch is the stock
# A-law voice pack (0x102000..0x196510, 8 kHz mono, 607,504 B ≈ 76 s) on a
# factory image, or 40 overlay apps + fonts + pinyin index on the Chinese
# build - either way this tool never owns a byte of it.
FLASH_FIRMWARE = (0x020000, 0x0A0000)
FLASH_APP_SLOTS = (0x102000, 0x060000)   # 48 个 app 槽声明区 0x102000..0x162000
# 原厂 A-law 语音包 0x102000..0x196510 (607,504 B) 被表区从中间切成两段。
# 原来写成一段 (0x102000, 0x095000)，于是表区整个落在自己声明的"别人的地盘"
# 里，guard() 把表区自己的每个地址都拒了 —— 这就是"点写入电台毫无反应"的
# 真因（异常从 Tk 回调里抛出，窗口程序里没地方印 traceback）。
# 现在只声明表区两侧的那两段，表区本身不属于任何禁区。
FLASH_VOICE_HEAD = (0x102000, DPH_BASE - 0x102000)                # ..0x162000
FLASH_VOICE_TAIL = (DPH_BASE + DPH_REGION, 0x197000 - (DPH_BASE + DPH_REGION))
TABLE_START = DPH_BASE
TABLE_END = DPH_BASE + DPH_REGION

TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad}&FORMAT=TLE"
TLE_GROUP = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=TLE"


# ------------------------------------------------------------------ satellite
class Sat:
    def __init__(self, norad, name, freq_mhz, mode, blurb, downlink=None, tone="",
                 aprs=0.0, sstv="", known=True):
        self.norad = norad
        self.name = name
        self.freq_mhz = freq_mhz          # what the radio tunes to
        self.mode = mode
        self.blurb = blurb
        self.tone = tone                 # subaudible tone, "" when not needed
        self.aprs = aprs                 # MHz, 0 when the bird has none
        self.sstv = sstv                 # "" or e.g. "PD120"
        self.known = known               # False = frequency not in the table
        self.downlink = downlink or f"{freq_mhz:.3f} MHz"
        self.tle = None                   # (name, line1, line2)
        self.tle_age = None

    @property
    def freq10(self):
        return int(round(self.freq_mhz * 1e6 / 10))   # 10 Hz units


CATALOG = [
    Sat(25544, "ISS (ZARYA)", 437.550, "FM / SSTV",
        "国际空间站。业余无线电业务由 ARISS 组织，舱内有 NA1SS 呼号。"
        "437.550 MHz 是 SSTV 常用的下行频点，过境时用 PD120 发图。"
        "信号强、过境多，是最适合练手的目标。", tone=""),
    Sat(43017, "AO-91 (Fox-1B)", 145.960, "FM",
        "AMSAT 的 Fox-1B，2017 年发射。FM 转发器，上行 435.250 MHz（67 Hz 亚音），"
        "下行 145.960 MHz。天线朝南时最容易在傍晚和凌晨听到。", tone="67.0 Hz"),
    Sat(27607, "SO-50 (SaudiSat-1C)", 436.795, "FM",
        "2002 年发射的老卫星，至今仍在工作。上行 145.850 MHz（67 Hz 亚音），"
        "下行 436.795 MHz。需要先发一个亚音唤醒，之后有十分钟窗口。", tone="67.0 Hz"),
    Sat(7530, "AO-7", 145.975, "SSB/CW",
        "1974 年发射，比很多操作员的年龄都大。模式 A/B 会自己切换，"
        "下行 145.975 MHz，是唯一还能用的老古董。"),
    Sat(24278, "FO-29 (JAS-2)", 435.800, "SSB/CW",
        "日本 JARL 的线性转发器，上行 145.900–146.000 MHz，下行 435.800–435.900 MHz。"
        "线性转发器能同时通很多人，但需要 SSB 设备和多普勒跟踪。"),
    Sat(44387, "AO-95 / RS-44", 435.610, "SSB/CW",
        "俄罗斯 RS-44，线性转发器，下行 435.610 MHz。仰角高时信号很扎实。"),
]


def find_sat(norad):
    for s in CATALOG:
        if s.norad == norad:
            return s
    return None


# ------------------------------------------------------------------ TLE
def _parse_tle(raw):
    lines = [x.strip() for x in raw if x.strip()]
    out = []
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name = lines[i - 1] if i > 0 else "UNKNOWN"
            out.append((name, line, lines[i + 1]))
    return out


def fetch_group(group="amateur", timeout=4):
    """All satellites in a group -> [(name, line1, line2), ...].

    celestrak first; when it is down the amsat bulk file answers with the whole
    amateur set in a single request.
    """
    flag = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "celestrak_down.flag")
    if os.path.exists(flag) and time.time() - os.path.getmtime(flag) < 600:
        return list(_fetch_bulk().values())

    try:
        url = TLE_GROUP.format(group=group)
        req = urllib.request.Request(url, headers={"User-Agent": "doppler-tool/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace").splitlines()
        got = _parse_tle(raw)
        if got:
            try:
                os.remove(flag)
            except OSError:
                pass
            return got
    except Exception:
        try:
            io.open(flag, "w").close()
        except OSError:
            pass

    return list(_fetch_bulk(force=True).values())


TLE_ALT = "https://tle.ivanstanojevic.me/api/tle/{norad}"
TLE_FILE = "https://www.amsat.org/tle/current/nasabare.txt"
_BULK = {}
_BULK_AT = 0.0


def _fetch_celestrak(norad, timeout):
    url = TLE_URL.format(norad=norad)
    req = urllib.request.Request(url, headers={"User-Agent": "doppler-tool/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace").strip().splitlines()
    lines = [x.strip() for x in raw if x.strip()]
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            name = lines[i - 1] if i > 0 else str(norad)
            return name, line, lines[i + 1]
    raise RuntimeError("TLE 格式不对：" + " / ".join(lines[:3]))


def _fetch_alt(norad, timeout):
    # This mirror answers 508 Loop Detected to anything that is not a browser
    # user agent, so pretend to be one.
    req = urllib.request.Request(
        TLE_ALT.format(norad=norad),
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        js = json.loads(r.read().decode("utf-8", "replace"))
    l1, l2 = js.get("line1", ""), js.get("line2", "")
    if not (l1.startswith("1 ") and l2.startswith("2 ")):
        raise RuntimeError("备用源返回格式不对")
    return js.get("name") or str(norad), l1, l2




def _curl_text(url, timeout=40):
    """Fetch a small text file with the system curl (chunked reads are flaky
    through urllib on this network)."""
    import subprocess
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        subprocess.run(
            ["curl", "-sSL", "--max-time", str(timeout),
             "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
             "-o", path, url],
            check=True, timeout=timeout + 10)
        with io.open(path, encoding="utf-8", errors="replace") as f:
            out = f.read()
        if not out.strip():
            raise RuntimeError("curl 拿到空内容")
        return out
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _fetch_bulk(force=False):
    """Every amateur TLE in one request (name / line1 / line2 triples)."""
    global _BULK, _BULK_AT

    if _BULK and not force and time.time() - _BULK_AT < 3600:
        return _BULK

    text = None
    for attempt in range(3):
        try:
            text = _curl_text(TLE_FILE, 40)
            break
        except Exception:
            time.sleep(2 + attempt * 3)
    if text is None:
        req = urllib.request.Request(
            TLE_FILE,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=45) as r:
            text = r.read().decode("utf-8", "replace")

    raw = text.splitlines()

    lines = [x.strip() for x in raw if x.strip()]
    out = {}
    for i in range(len(lines) - 1):
        if lines[i].startswith("1 ") and lines[i + 1].startswith("2 "):
            try:
                norad = int(lines[i][2:7])
            except Exception:
                continue
            name = lines[i - 1] if i > 0 else str(norad)
            if name.startswith("0 ") or name.startswith("1 ") or name.startswith("2 "):
                name = str(norad)
            out[norad] = (name, lines[i], lines[i + 1])

    if not out:
        raise RuntimeError("批量 TLE 文件解析不出任何条目")

    _BULK, _BULK_AT = out, time.time()
    return out


def fetch_tle(norad, timeout=20):
    """celestrak -> amsat bulk file -> per-satellite mirror."""
    errors = []
    try:
        return _fetch_celestrak(norad, timeout)
    except Exception as e:
        errors.append(f"celestrak: {e}")
    try:
        hit = _fetch_bulk().get(int(norad))
        if hit:
            return hit
        errors.append("amsat: 文件里没有这颗")
    except Exception as e:
        errors.append(f"amsat: {e}")
    try:
        return _fetch_alt(norad, timeout)
    except Exception as e:
        errors.append(f"mirror: {e}")
    raise RuntimeError("取不到 TLE，三个源都失败 -> " + " | ".join(errors))


def epoch_of(line1):
    """Year + day-of-year out of TLE line 1 -> aware datetime."""
    yy = int(line1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    doy = float(line1[20:32])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)


# ------------------------------------------------------------------ geometry
def _gmst(dt):
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    t = ((jd - 2451545.0) + fr) / 36525.0
    g = 280.46061837 + 360.98564736629 * ((jd - 2451545.0) + fr) \
        + 0.000387933 * t * t - t * t * t / 38710000.0
    return math.radians(g % 360.0)


class Ground:
    def __init__(self, lat_deg, lon_deg, alt_m=0.0):
        self.lat = math.radians(lat_deg)
        self.lon = math.radians(lon_deg)
        e2 = FLAT * (2 - FLAT)
        n = EARTH_R / math.sqrt(1 - e2 * math.sin(self.lat) ** 2)
        h = alt_m / 1000.0
        self.x = (n + h) * math.cos(self.lat) * math.cos(self.lon)
        self.y = (n + h) * math.cos(self.lat) * math.sin(self.lon)
        self.z = (n * (1 - e2) + h) * math.sin(self.lat)

    def up(self):
        return (math.cos(self.lat) * math.cos(self.lon),
                math.cos(self.lat) * math.sin(self.lon),
                math.sin(self.lat))


def _tememap(dt):
    g = _gmst(dt)
    c, s = math.cos(g), math.sin(g)
    return c, s


def _sat_eci(satrec, dt):
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute,
                  dt.second + dt.microsecond * 1e-6)
    e, r, v = satrec.sgp4(jd, fr)
    if e != 0:
        return None
    return r, v


def _to_ecef(r, dt):
    c, s = _tememap(dt)
    x = c * r[0] + s * r[1]
    y = -s * r[0] + c * r[1]
    return (x, y, r[2])


def look(satrec, gnd, dt):
    """(elevation_deg, azimuth_deg, range_km, range_rate_km_s).

    Range rate is positive when the satellite is moving away, which is why the
    Doppler offset downstream is written as -rate/c*f.
    """
    out = _sat_eci(satrec, dt)
    if out is None:
        return -90.0, 0.0, 0.0, 0.0
    r, v = out

    rr = _to_ecef(r, dt)
    c, s = _tememap(dt)
    vx = c * v[0] + s * v[1] + OMEGA_E * rr[1]
    vy = -s * v[0] + c * v[1] - OMEGA_E * rr[0]
    vz = v[2]

    dx, dy, dz = rr[0] - gnd.x, rr[1] - gnd.y, rr[2] - gnd.z
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    ux, uy, uz = gnd.up()
    sin_el = (dx * ux + dy * uy + dz * uz) / dist
    el = math.degrees(math.asin(max(-1.0, min(1.0, sin_el))))

    sl, cl = math.sin(gnd.lon), math.cos(gnd.lon)
    east = dx * (-sl) + dy * cl
    north = (-dx * math.sin(gnd.lat) * cl - dy * math.sin(gnd.lat) * sl
             + dz * math.cos(gnd.lat))
    az = math.degrees(math.atan2(east, north)) % 360.0

    rate = (dx * vx + dy * vy + dz * vz) / dist
    return el, az, dist, rate


def doppler_hz(freq_hz, rate_km_s):
    return -rate_km_s / 299792.458 * freq_hz


# ------------------------------------------------------------------ passes
class Pass:
    def __init__(self):
        self.aos = None
        self.tca = None
        self.los = None
        self.max_el = 0.0
        self.samples = []      # [(t_seconds_from_aos, offset_hz, elevation_deg)]
        self.name = "SAT"
        self.aos_el = 0.0
        self.aos_az = 0.0
        self.tca_az = 0.0
        self.los_el = 0.0
        self.los_az = 0.0
        self.tca_range = 0.0   # km
        self.tca_rate = 0.0    # km/s

    @property
    def duration(self):
        return int((self.los - self.aos).total_seconds())

    @property
    def span(self):
        return (min(s[1] for s in self.samples), max(s[1] for s in self.samples))


def find_passes(satrec, gnd, freq_hz, days=3, min_el=10.0,
                coarse=20.0, step=DPH_STEP_S_DEFAULT, max_passes=DPH_PASS_MAX,
                start=None):
    start = start or datetime.now(timezone.utc)
    end = start + timedelta(days=days)
    passes = []

    t = start
    el = look(satrec, gnd, t)[0]
    prev_el, prev_t = el, t
    stepd = timedelta(seconds=coarse)

    while t < end and len(passes) < max_passes:
        t += stepd
        el = look(satrec, gnd, t)[0]

        if prev_el < 0 <= el:                       # rising through horizon
            aos = _bisect(satrec, gnd, prev_t, t, rising=True)
            los = _find_los(satrec, gnd, t, end)
            if los:
                p = _measure(satrec, gnd, aos, los, freq_hz, step)
                if p.max_el >= min_el:
                    passes.append(p)
                t = los
                el = look(satrec, gnd, t)[0]
        prev_el, prev_t = el, t

    return passes


def _bisect(satrec, gnd, t0, t1, rising=True):
    for _ in range(18):
        mid = t0 + (t1 - t0) / 2
        el = look(satrec, gnd, mid)[0]
        if (el >= 0) == rising:
            t1 = mid
        else:
            t0 = mid
    return t0 + (t1 - t0) / 2


def _find_los(satrec, gnd, t0, end):
    step = timedelta(seconds=10)
    t = t0
    prev_el = look(satrec, gnd, t)[0]
    while t < end:
        t += step
        el = look(satrec, gnd, t)[0]
        if prev_el >= 0 > el:
            return _bisect(satrec, gnd, t - step, t, rising=False)
        prev_el = el
    return None


def _measure(satrec, gnd, aos, los, freq_hz, step):
    # FIX(采样原点三端对齐): 表里的 aos2 只存得下"当天秒数 / 2"，也就是 2 秒
    # 一格；原来这里从**真实 AOS**（带小数秒）起采，而 build_table 的 aos2 是
    # 向下取整 —— 于是整条曲线比表记的时刻提前最多 2 s，437.8 MHz 上是 248 Hz。
    # App 的 PassCalc 早就改成"AOS 四舍五入到偶数秒、样点也从那一秒起采"
    # （PassCalc.kt:121-124 那段注释就是这条），PC 这一份一直漏着：
    # 同一颗星同一台站，手机写的表和电脑写的表对不上，谁也不报错。
    #
    # 对齐到偶数秒用 UTC 秒数就够 —— 所有时区偏移都是整分钟（偶数秒），
    # 所以"偶数秒"这个性质与本地/UTC 无关，不必把 tz 传进来。
    # 挪完再算 aos2 就是精确值，build_table 里那句 //2 不用再改。
    s = aos.hour * 3600 + aos.minute * 60 + aos.second
    aos = aos.replace(microsecond=0) + timedelta(seconds=(s % 2))

    p = Pass()
    p.aos, p.los = aos, los
    total = (los - aos).total_seconds()
    n = max(2, int(total / step) + 1)

    best = -1.0
    best_i = 0
    for i in range(n):
        when = aos + timedelta(seconds=i * step)
        el, az, rng, rate = look(satrec, gnd, when)
        if el > best:
            best, best_i = el, i
        p.samples.append((i * step, doppler_hz(freq_hz, rate),
                          max(0, min(90, int(round(el)))),
                          int(round(rng)), max(-70, min(70, int(round(rate * 10))))))

    # peak: parabola through the two neighbours of the best sample
    if 0 < best_i < n - 1:
        y0 = p.samples[best_i - 1][2]
        y1 = p.samples[best_i][2]
        y2 = p.samples[best_i + 1][2]
        den = (y0 - 2 * y1 + y2)
        off = 0.5 * (y0 - y2) / den if den else 0.0
        off = max(-1.0, min(1.0, off))
        t_peak = (best_i + off) * step
    else:
        t_peak = best_i * step

    p.tca = aos + timedelta(seconds=t_peak)
    el, az, rng, rate = look(satrec, gnd, p.tca)
    p.max_el, p.tca_az, p.tca_range, p.tca_rate = el, az, rng, rate

    el, az, _, _ = look(satrec, gnd, aos)
    p.aos_el, p.aos_az = el, az
    el, az, _, _ = look(satrec, gnd, los)
    p.los_el, p.los_az = el, az
    return p


def _pad16(s):
    b = s.encode("ascii", "replace")[:16]
    return b + bytes(16 - len(b))



def validate_table(table):
    """Return a list of problems; empty means the blob is safe to write.

    Everything the firmware would otherwise discover halfway through a scan is
    checked here, while the radio still holds its previous contents.
    """
    errs = []
    n = len(table)

    if n < DPH_HDR:
        return [f"表只有 {n} 字节，连表头都不够"]

    magic, ver, count, hdr_size, data_off, _res = struct.unpack_from(
        "<4sBBHII", table, 0)

    if magic != b"DPT1":
        errs.append(f"表头标识是 {magic!r}，应为 b'DPT1'")
    if ver != DPH_VERSION:
        errs.append(f"表格式版本 {ver}，本工具写的是 {DPH_VERSION}")
    if hdr_size != DPH_HDR:
        errs.append(f"表头长度 {hdr_size}，应为 {DPH_HDR}")
    if not (1 <= count <= DPH_PASS_MAX):
        errs.append(f"条目数 {count}，必须在 1~{DPH_PASS_MAX} 之间")
        return errs

    if data_off != TABLE_START + DPH_SAMPLE_OFF:
        errs.append(f"样点区起点 0x{data_off:06X}，"
                    f"应为 0x{TABLE_START + DPH_SAMPLE_OFF:06X}")

    need = DPH_HDR + count * DPH_ENTRY
    if n < need:
        errs.append(f"长度 {n} 字节装不下 {count} 条条目（需要 {need}）")
        return errs

    for i in range(count):
        off = DPH_HDR + i * DPH_ENTRY
        (name, f10, doff, up10, aos2, dur, pts, step, ctc, flags,
         el) = struct.unpack_from("<16sIIIHHHHHBB", table, off)

        tag = f"第 {i + 1} 条"
        if not name.strip(b"\x00 "):
            errs.append(f"{tag}：卫星名是空的")
        if not (1_000_000 <= f10 <= 1_000_000_000):
            errs.append(f"{tag}：下行频率 {f10} 超出范围")
        if up10 and not (1_000_000 <= up10 <= 1_000_000_000):
            errs.append(f"{tag}：上行频率 {up10} 超出范围")
        # 固件上限是 DPH_DUR_S_MAX（1500 s）：原来那个 7200 是按 496 KiB 表区
        # 算出来的"防坏线"，32 KiB 里装不下，所以三端一起收到 1500（25 分钟）。
        # 437.8 MHz 上最长的过境也就 660 s 上下，1500 s 有两倍余量。
        if not (10 <= dur <= DPH_DUR_S_MAX):
            errs.append(f"{tag}：时长 {dur} 秒不合理（固件允许 10~{DPH_DUR_S_MAX}）")
        if not (2 <= pts <= 20000):
            errs.append(f"{tag}：采样点 {pts} 个不合理")
            continue
        # 上界只受 u16 限制；装不装得下由下面的样点越界检查算。原来这里把
        # 下限 10000 顺手写成了上界，等价于"只允许恰好 10 s"，而 build_table
        # 允许到 65535 —— 于是"构表成功、写表被自己拒"，报错文案还说"要求 ≥"。
        if not (DPH_STEP_MS_MIN <= step <= 65535):
            errs.append(f"{tag}：采样间隔 {step} 毫秒不合理"
                        f"（固件要求 {DPH_STEP_MS_MIN}~65535 ms，即 "
                        f"{DPH_STEP_MS_MIN / 1000:g}~65.535 s）")
        if el > 90:
            errs.append(f"{tag}：最高仰角 {el}° 不合理")

        body = pts * 6  # int16 offset + u8 elev + u16 range + i8 rate
        if doff < TABLE_START + DPH_SAMPLE_OFF:
            errs.append(f"{tag}：样点地址 0x{doff:06X} 落在表头/条目区")
        elif doff + body > TABLE_START + DPH_REGION:
            errs.append(f"{tag}：样点越界，"
                        f"结束于 0x{doff + body:06X}")
        elif n < (doff - TABLE_START) + body:
            errs.append(f"{tag}：样点没写全（需要到第 {doff - TABLE_START + body} 字节）")

    return errs


def validate_warnings(table):
    """不阻止写入、但必须让人知道的提醒（空列表 = 没有）。

    目前只有一条：固件用 16 位定点插值累计多普勒偏移，4290 s 以上的时长
    会溢出、曲线尾部翻符号。固件侧正在修，工具这边先提示（报告 第六部分·回归项 15）。
    """
    out = []
    n = len(table)
    if n < DPH_HDR:
        return out
    try:
        magic, _ver, count, _hdr, _off, _res = struct.unpack_from(
            "<4sBBHII", table, 0)
        if magic != b"DPT1" or not (1 <= count <= DPH_PASS_MAX):
            return out
        if n < DPH_HDR + count * DPH_ENTRY:
            return out
        for i in range(count):
            off = DPH_HDR + i * DPH_ENTRY
            f = struct.unpack_from("<16sIIIHHHHHBB", table, off)
            dur = f[5]
            if dur > 4290:
                out.append(f"第 {i + 1} 条时长 {dur} s 超过 4290 s，"
                           "固件插值会溢出，曲线尾部失真")
    except struct.error:
        pass
    return out


def local_tz_hours():
    """本机时区偏移（小时）。表里的 aos2 是"本地日的秒数"，
    set_clock() 发的也是本机本地时间，两边必须用同一个时区。"""
    off = datetime.now().astimezone().utcoffset()
    return off.total_seconds() / 3600.0 if off else 8.0


def build_table(passes, freq10, step_s, tz_hours=None, name="SAT",
                up10=0, ctcss10=0):
    """Binary table the firmware expects (version 5).

    header  16 B   "DPT1" ver count hdr_size data_off reserved
    entry   40 B   name freq10 data_off up10 aos2 dur_s pts step_ms
                   ctcss10 flags max_el
    samples at DPH_SAMPLE_OFF, 6 B each: int16 offset Hz + uint8 elevation
    + uint16 slant range km + int8 range rate in 0.1 km/s

    freq10 / up10 are in 10 Hz units, not Hz: 437.55 MHz -> 43_755_000.
    up10 = 0 means the entry is receive only, and the firmware keeps the
    transmit frequency untouched.
    """
    if tz_hours is None:
        tz_hours = local_tz_hours()

    if not passes:
        raise ValueError("没有过境可写")

    # 固件会拒收比 DPH_STEP_MS_MIN 更密的表（DPH_LoadEntry 的闸门），
    # 在这里先拦住，而不是写进去让电台整表读不出来。
    step_ms = int(round(step_s * 1000))
    if step_ms < DPH_STEP_MS_MIN:
        raise ValueError(
            f"采样间隔 {step_s:g} s 太密：固件要求 ≥ {DPH_STEP_MS_MIN / 1000:g} s"
            f"（表区只有 {DPH_REGION // 1024} KiB，密采样装不下几条过境）")
    if step_ms > 65535:
        raise ValueError(f"采样间隔 {step_s:g} s 超出 u16 的 step_ms 范围")

    entries = []
    blob = bytearray()
    first = passes[0].aos

    base = passes[0].aos + timedelta(hours=tz_hours)

    for p in passes:
        # A queue may mix satellites, so every pass can carry its own plan.
        e_freq = int(getattr(p, "freq10", freq10))
        e_up   = int(getattr(p, "up10", up10))
        e_ct   = int(getattr(p, "ctcss10", ctcss10))
        # 超过 DPH_DUR_S_MAX 的"过境"一定是算错了（固件也按同一个数拒收），
        # 而且它会把一条条目的样点数顶穿容量断言。
        if p.duration > DPH_DUR_S_MAX:
            raise ValueError(
                f"{getattr(p, 'name', name)} 的过境时长 {p.duration} s 超过 "
                f"{DPH_DUR_S_MAX} s；检查 TLE 与地面站坐标")

        # 本地日偏移只有 4 bit（见 DPH_DAY_MAX）。这里以前直接 `& 0x0F`，
        # 第 16 天那趟被折成"今天"写进表里，构表和校验双双放行。
        day_idx = ((p.aos + timedelta(hours=tz_hours)).date() - base.date()).days
        if day_idx < 0:
            raise ValueError(
                f"第 {len(entries) + 1} 趟（{(p.aos + timedelta(hours=tz_hours)):%m-%d %H:%M} "
                f"本地）比表头那一天（{base:%m-%d}）还早。条目只能表达"
                f"表头之后的第几天，倒着排电台会读出完全错误的日期。"
                f"过境必须按时间从早到晚排。")
        if day_idx > DPH_DAY_MAX:
            raise ValueError(
                f"第 {len(entries) + 1} 趟（{getattr(p, 'name', name)}，"
                f"{(p.aos + timedelta(hours=tz_hours)):%m-%d %H:%M} 本地）已经在"
                f"表头那一天之后 {day_idx} 天了，而条目里的"
                f"日期偏移只有 4 bit，最多表达 {DPH_DAY_MAX} 天 —— 再往后会被折回"
                f"成第 0 天，电台上显示的日期和倒计时都是错的。"
                f"把天数收窄，或者先写掉前面这批再写后面那批。")

        entries.append(struct.pack(
            "<16sIIIHHHHHBB",
            _pad16(getattr(p, "name", name)),
            e_freq,
            DPH_BASE + DPH_SAMPLE_OFF + len(blob),
            e_up,
            (((p.aos.hour * 3600 + p.aos.minute * 60 + p.aos.second
               + int(tz_hours * 3600)) % 86400) // 2),
            p.duration,
            len(p.samples),
            step_ms,
            e_ct,
            # day offset must follow the local calendar, not a 24-hour bucket
            # from the first pass: a pass at 02:18 local can sit in the same UTC
            # day as one at 20:41 and would otherwise show the wrong date
            day_idx,
            int(round(p.max_el)),
        ))

        for (_, off, el, rng, rate) in p.samples:
            off = int(round(off))
            if off < -32768 or off > 32767:
                raise ValueError(
                    f"多普勒偏移 {off} Hz 超出 int16 范围（±32767）："
                    f"这颗星的下行频率或采样范围不适合这张表")
            blob += struct.pack("<hBHb", off, max(0, min(255, int(el))),
                                max(0, min(65535, int(rng))),
                                max(-128, min(127, int(rate))))

    hdr_date = (((base.year - 2000) & 0x7F) << 9) | ((base.month & 0x0F) << 5) | (base.day & 0x1F)
    # 版本号只能用常量：这里以前写死 5，改 DPH_VERSION 时两边不一致，
    # 写出来的是"头 ver=5 + 新排版条目"，固件按旧格式解析 → 整表错位。
    if len(entries) > DPH_PASS_MAX:
        raise ValueError(
            f"过境条目 {len(entries)} 条，超过 {DPH_PASS_MAX} 条 —— 这是条目区的"
            f"物理上限（{DPH_HDR} B 表头 + {DPH_PASS_MAX}×{DPH_ENTRY} B 条目 = "
            f"0x{DPH_SAMPLE_OFF:X}），再多一条就压进样点区了。少选几趟。")
    header = struct.pack("<4sBBHII", b"DPT1", DPH_VERSION, len(entries), DPH_HDR,
                         DPH_BASE + DPH_SAMPLE_OFF, hdr_date)

    out = bytearray(header) + b"".join(entries)
    if len(out) > DPH_SAMPLE_OFF:
        raise ValueError(f"条目太多，超出 {DPH_SAMPLE_OFF} 字节的头部空间")
    out += bytes(DPH_SAMPLE_OFF - len(out))
    out += blob

    if len(out) > DPH_REGION:
        over = len(out) - DPH_REGION
        one = DPH_ENTRY + len(getattr(passes[-1], "samples", [])) * DPH_PT
        raise ValueError(
            f"表格 {len(out):,} B，超过表区 {DPH_REGION:,} B"
            f"（{DPH_REGION // 1024} KiB）{over:,} B。少选 1 趟大约能省 {one:,} B；"
            f"或者把采样间隔调大（10 s 的表只有 5 s 的一半）、把天数/仰角收窄。")
    return bytes(out)


def next_pass(satrec, gnd, min_el=0.0, horizon_h=12.0, coarse=60.0):
    """First pass that gets above min_el within the next horizon_h hours.

    Returns (aos_datetime, max_elevation_deg) or None. Coarse 30 s scan is
    enough to find the rise; the peak is refined with a short bisection."""
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=horizon_h)
    t = start
    prev_el = look(satrec, gnd, t)[0]
    best = prev_el if prev_el > min_el else None
    t_best = t
    stepd = timedelta(seconds=coarse)

    while t < end:
        t2 = t + stepd
        el2 = look(satrec, gnd, t2)[0]
        if prev_el <= min_el < el2:
            aos = _bisect(satrec, gnd, t, t2, rising=True)
            peak, peak_t = _peak(satrec, gnd, aos, min(end, aos + timedelta(minutes=15)))
            if peak is not None and peak >= min_el:
                return aos, peak
        prev_el, t = el2, t2
    return None


def _peak(satrec, gnd, t0, t1):
    """Rough peak elevation between t0 and t1 (10 s steps)."""
    best, best_t = -90.0, t0
    t = t0
    stepd = timedelta(seconds=10)
    while t <= t1:
        el = look(satrec, gnd, t)[0]
        if el > best:
            best, best_t = el, t
        t += stepd
    return best, best_t



def elevations_batch(satrec, gnd, t0, count, step_s):
    """Elevation (deg) for `count` samples spaced `step_s` apart, vectorised.

    One SGP4 array call instead of `count` scalar calls: ~7000x faster, which
    is what makes "next pass for every satellite" affordable in the GUI."""
    j0, f0 = jday(t0.year, t0.month, t0.day, t0.hour, t0.minute,
                  t0.second + t0.microsecond * 1e-6)
    k = np.arange(count, dtype=np.float64) * (step_s / 86400.0)
    fr = f0 + k
    jd = j0 + np.floor(fr)
    fr = fr - np.floor(fr)
    err, r, v = satrec.sgp4_array(jd, fr)
    if np.any(err != 0):
        return None
    tt = ((jd - 2451545.0) + fr) / 36525.0
    g = 280.46061837 + 360.98564736629 * ((jd - 2451545.0) + fr)         + 0.000387933 * tt * tt - tt * tt * tt / 38710000.0
    g = np.radians(np.mod(g, 360.0))
    cs, sn = np.cos(g), np.sin(g)
    x = cs * r[:, 0] + sn * r[:, 1] - gnd.x
    y = -sn * r[:, 0] + cs * r[:, 1] - gnd.y
    z = r[:, 2] - gnd.z
    ux, uy, uz = gnd.up()
    rng = np.sqrt(x * x + y * y + z * z)
    el = np.degrees(np.arcsin(np.clip((x * ux + y * uy + z * uz) / rng, -1, 1)))
    return el


def next_pass_fast(satrec, gnd, min_el=0.0, horizon_h=24.0, coarse=60.0):
    """Fast (aos, max_el) for the first pass in the next `horizon_h` hours."""
    start = datetime.now(timezone.utc)
    count = int(horizon_h * 3600.0 / coarse)
    el = elevations_batch(satrec, gnd, start, count, coarse)
    if el is None:
        return None
    above = el > min_el
    if not above.any():
        return None
    i = int(np.argmax(above)) if False else int(above.argmax())
    if i == 0:
        return start, float(el[:5].max())
    t = start + timedelta(seconds=coarse * i)
    return t, float(el[i:i + 12].max())


def table_sectors(size):
    return (size + DPH_SECTOR - 1) // DPH_SECTOR


def guard(addr, length):
    """Refuse to touch anything outside the table region."""
    if addr < TABLE_START or addr + length > TABLE_END:
        raise ValueError(
            f"拒绝写入：0x{addr:06X}+{length} 超出表格区 "
            f"0x{TABLE_START:06X}..0x{TABLE_END:06X}")
    for (lo, ln) in (FLASH_FIRMWARE, FLASH_APP_SLOTS,
                     FLASH_VOICE_HEAD, FLASH_VOICE_TAIL):
        if addr < lo + ln and addr + length > lo:
            raise ValueError(f"拒绝写入：与固件/APP/语音包区 0x{lo:06X} 重叠")
    return addr


def erase_plan(table_size):
    """How many sectors to wipe before writing.

    表区是 16 个扇区（64 KiB）、擦一遍一两秒，所以不做"按需少擦"：整块擦掉
    最干净，不会留下上一轮的旧样点。上限就是表区本身（16），够不着别人的地盘。
    原来这里是 max(需要扇区, 8) —— 上一版表区只有 4 个扇区时，那个 8 就是
    "擦到表区外面"的直接来源（先擦后判、越界擦，都是它）。"""
    sectors = table_sectors(DPH_SAMPLE_OFF + table_size)
    sectors = max(sectors, DPH_REGION // DPH_SECTOR)
    sectors = min(sectors, DPH_REGION // DPH_SECTOR)
    return [(DPH_BASE + i * DPH_SECTOR) for i in range(sectors)]


# ------------------------------------------------------------------ ownership
# 写表之前先问一句"这块地现在是谁的"。三个状态与固件 doppler.c 的
# dph_sector_class_t 一一对应：固件在擦除命令里逐个扇区再判一次（那是最后
# 一道闸），这里判是为了**在擦第一个扇区之前就把人话错误弹出来**，而不是
# 擦到一半才发现第三扇区是别人的语音包。
SECTOR_BLANK = "blank"        # 全 0xFF，没人用过
SECTOR_OURS = "ours"         # 表头扇区的前 4 字节是 DPT1
SECTOR_FOREIGN = "foreign"    # 既不是空白也不是我们的表


def classify_sector(buf, addr):
    """Classify one 4 KiB sector. Mirrors DPH_ClassifySector() in the firmware."""
    if len(buf) != DPH_SECTOR:
        raise ValueError(f"扇区长度 {len(buf)}，应为 {DPH_SECTOR}")
    if addr == DPH_BASE and buf[:4] == b"DPT1":
        return SECTOR_OURS
    return SECTOR_BLANK if buf.count(0xFF) == len(buf) else SECTOR_FOREIGN


def classify_region(blob):
    """[(addr, class), ...] for the whole table region."""
    n = DPH_REGION // DPH_SECTOR
    if len(blob) != DPH_REGION:
        raise ValueError(f"表区回读 {len(blob)} 字节，应为 {DPH_REGION}")
    return [(DPH_BASE + i * DPH_SECTOR,
             classify_sector(blob[i * DPH_SECTOR:(i + 1) * DPH_SECTOR],
                             DPH_BASE + i * DPH_SECTOR))
            for i in range(n)]


def occupied_reason(classes):
    """None = 可以写；否则返回一句给人看的拒绝理由。

    与固件同一条规则：表头是我们的 → 整段数据都是我们的旧样点，放行；
    表头空白 → 其它扇区必须也空白（全新的一块地）；表头是别人的 → 一个字节
    都不许动。第三条是这次真正要防的：表区曾经就压在 A-law 语音包上。
    """
    head = classes[0][1]
    if head == SECTOR_OURS:
        return None
    if head == SECTOR_FOREIGN:
        return (f"0x{DPH_BASE:06X} 现在不是多普勒表，是被别的东西占着的"
                f"（可能是汉化语音包 / Sonic 镜像 / 别人的固件数据）。"
                f"这里一擦就不可恢复，所以没有动它一个字节。")
    for addr, cls in classes[1:]:
        if cls == SECTOR_FOREIGN:
            return (f"表头扇区是空的，但 0x{addr:06X} 里有数据 —— 说明这块地"
                    f"被别的东西切走了一部分，不能整块擦。"
                    f"没有动它一个字节。")
    return None
