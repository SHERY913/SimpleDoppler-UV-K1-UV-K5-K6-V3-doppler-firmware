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
DPH_ENTRY_MAX = 255        # 表头 count 是 u8，256 会直接 struct.error
DPH_SAMPLE_OFF = 0x3000        # past 256 entries x 40 B, so a big table cannot overwrite samples
DPH_SECTOR = 0x1000
DPH_REGION = 0x9D000          # 0x162000..0x1FF000 (~630 KiB)

# Where things live on the 2 MiB external flash. Nothing here is allowed to
# touch anything but TABLE region - the firmware slots (0x020000..0x0C0000) and
# the overlay app slots (0x102000..0x162000) are strictly off limits.
FLASH_FIRMWARE = (0x020000, 0x0A0000)
FLASH_APP_SLOTS = (0x102000, 0x060000)
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
                coarse=20.0, step=2.0, max_passes=DPH_ENTRY_MAX,
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
    if not (1 <= count <= DPH_ENTRY_MAX):
        errs.append(f"条目数 {count}，必须在 1~{DPH_ENTRY_MAX} 之间")
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
        # 固件上限是 7200 s（doppler.c 里 dur 的 abs 上限），以前这里卡 3600，
        # 比固件还严，70° 以上的长过境会被自己的校验拦下来（报告 第六部分·回归项 15）。
        if not (10 <= dur <= 7200):
            errs.append(f"{tag}：时长 {dur} 秒不合理（固件允许 10~7200）")
        if not (2 <= pts <= 20000):
            errs.append(f"{tag}：采样点 {pts} 个不合理")
            continue
        if not (100 <= step <= 10000):
            errs.append(f"{tag}：采样间隔 {step} 毫秒不合理")
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
        if magic != b"DPT1" or not (1 <= count <= DPH_ENTRY_MAX):
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

    entries = []
    blob = bytearray()
    first = passes[0].aos

    base = passes[0].aos + timedelta(hours=tz_hours)

    for p in passes:
        # A queue may mix satellites, so every pass can carry its own plan.
        e_freq = int(getattr(p, "freq10", freq10))
        e_up   = int(getattr(p, "up10", up10))
        e_ct   = int(getattr(p, "ctcss10", ctcss10))
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
            int(step_s * 1000),
            e_ct,
            # day offset must follow the local calendar, not a 24-hour bucket
            # from the first pass: a pass at 02:18 local can sit in the same UTC
            # day as one at 20:41 and would otherwise show the wrong date
            ((p.aos + timedelta(hours=tz_hours)).date() - base.date()).days & 0x0F,
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
    if len(entries) > DPH_ENTRY_MAX:
        raise ValueError(f"过境条目 {len(entries)} 条，表头 count 只有一字节，最多 {DPH_ENTRY_MAX} 条")
    header = struct.pack("<4sBBHII", b"DPT1", DPH_VERSION, len(entries), DPH_HDR,
                         DPH_BASE + DPH_SAMPLE_OFF, hdr_date)

    out = bytearray(header) + b"".join(entries)
    if len(out) > DPH_SAMPLE_OFF:
        raise ValueError(f"条目太多，超出 {DPH_SAMPLE_OFF} 字节的头部空间")
    out += bytes(DPH_SAMPLE_OFF - len(out))
    out += blob

    if len(out) > DPH_REGION:
        raise ValueError(
            f"表格 {len(out)} 字节，超出 {DPH_REGION} 字节的表区；"
            "减少过境数 / 缩短天数 / 把采样间隔调大")
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
    for (lo, ln) in (FLASH_FIRMWARE, FLASH_APP_SLOTS):
        if addr < lo + ln and addr + length > lo:
            raise ValueError(f"拒绝写入：与固件/APP 区 0x{lo:06X} 重叠")
    return addr


def erase_plan(table_size):
    """How many sectors to wipe before writing.

    Always wipe the whole span the previous table could have occupied, so a
    shorter new table never leaves stale pass entries behind. Capped at the
    region size, so it can never creep into the firmware area.
    """
    sectors = table_sectors(DPH_SAMPLE_OFF + table_size)
    sectors = max(sectors, 8)                  # covers the previous run
    sectors = min(sectors, DPH_REGION // DPH_SECTOR)
    return [(DPH_BASE + i * DPH_SECTOR) for i in range(sectors)]
