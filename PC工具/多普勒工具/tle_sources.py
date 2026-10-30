#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TLE 多源抓取层 —— 被 refresh_tle.py 调用，不单独运行。

设计目标：一个源挂掉不能拖垮整次刷新，多个源按 NORAD 合并，同一颗星留历元最新的那条。

组成：
  1. SOURCES：源表（名称 / URL / 解析器 / 优先级 / 是否批量 / 备注）。
  2. 抓取层 fetch()：urllib 优先，失败退回 curl（本机 urllib 有时会卡 TLS 握手）。
     Windows 的 schannel 会做证书吊销检查，离线时直接报
     CRYPTO_E_REVOCATION_OFFLINE(0x80092013)，所以 curl 一定带 --ssl-no-revoke；
     celestrak / tleapi 会 301 跳转，所以带 -L。
  3. 解析层：三行 TLE、CelesTrak gp.php 的 JSON、tle.ivanstanojevic.me 的 hydra JSON。
     三种都收敛到同一个记录格式（见 build_record）。
  4. fetch_all()：按优先级逐源抓，返回每条源的状态（HTTP 码 / 条目数 / 耗时 / 失败原因）。

记录格式（前 3 项位置固定，老读者只认前 3 项）：
    [名称, line1, line2, 抓取时间, 历元, 倾角, RAAN, 偏心率,
     近地点幅角, 平近点角, 平均运动, B*]

依赖：只用标准库（urllib / subprocess / json / datetime / socket）。
"""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
DEFAULT_TIMEOUT = 25          # 单次请求超时（秒），任务口径
RETRIES = 1                   # 失败后重试次数（总尝试 = 1 + RETRIES）
FAILED_HOST_TIMEOUT = 8       # 同一主机已经连不上时，后续源用这个短超时，别把整次刷新拖成几分钟
CONNECT_TIMEOUT = 5           # TCP 连接（握手）超时：连不上就 5 s 认输，不陪它等整段 body 超时
MIN_BODY = 200                # 小于这个字节数基本是错误页，当失败

CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT={fmt}"
TLEAPI = "https://tle.ivanstanojevic.me/api/tle"
# SatNOGS 全库 TLE：本机实测 1700 条 / 537 KB / 114.9 s（connect 只要 0.97 s，
# 慢在服务端吐 body），所以单独给 90~180 s 的超时，别塞进 25 s 的通用口径。
SATNOGS_TLE = "https://db.satnogs.org/api/tle/?format=json"
SATNOGS_ONE = "https://db.satnogs.org/api/tle/?norad_cat_id={norad}&format=json"

_HOST_OK = {}                 # host -> True/False，本次进程内只探一次


# ── 记录构造 ──────────────────────────────────────────────────────────
def _f(s, default=0.0):
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return default


def epoch_dt(line1):
    """TLE 第 1 行的历元 → 带 UTC 的 datetime（2000 年前用 19xx）。"""
    yy = int(line1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    doy = float(line1[20:32])
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)


def epoch_of(rec):
    """取记录里的历元（epoch 秒）。优先用现算的 l1（权威），第 5 项只做兜底。"""
    try:
        return epoch_dt(rec[1]).timestamp()
    except Exception:  # noqa: BLE001
        try:
            v = float(rec[4])
            return v if v > 1e8 else None
        except (TypeError, ValueError, IndexError):
            return None


def norad_of(line1):
    return line1[2:7].strip().lstrip("0") or "0"


def build_record(name, l1, l2, fetched_at=None):
    """(名称, line1, line2) → 12 项记录。字段切片沿用老实现，不要动位置。"""
    now = time.time() if fetched_at is None else fetched_at
    try:
        epoch = epoch_dt(l1).timestamp()
    except Exception:  # noqa: BLE001
        epoch = None
    return [
        str(name).strip(), l1, l2, now, epoch,
        _f(l2[8:16]),                       # 倾角 °
        _f(l2[17:25]),                      # RAAN °
        _f("0." + l2[26:33].strip()),       # 偏心率（隐含前导 0.）
        _f(l2[34:42]),                      # 近地点幅角 °
        _f(l2[43:51]),                      # 平近点角 °
        _f(l2[52:63]),                      # 平均运动 rev/day
        l1[53:61].strip(),                  # B*
    ]


# ── 解析器 ────────────────────────────────────────────────────────────
def parse_three_line(text, fetched_at=None):
    """三行一组（名称 / 1 行 / 2 行）→ {norad: 记录}。

    容忍 AMSAT 公告块（SB KEPS ... / 2Line Orbital Elements）和空行：
    不是以 '1 '/'2 ' 开头的行整体往后挪一格，不会把公告行当成卫星名。
    """
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = {}
    i = 0
    n = len(lines)
    while i + 2 < n:
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            i += 1
            continue
        out[norad_of(l1)] = build_record(name, l1, l2, fetched_at)
        i += 3
    return out


def parse_celestrak_json(text, fetched_at=None):
    """CelesTrak gp.php?FORMAT=json → {norad: 记录}。

    字段名在不同年代/不同镜像里叫法不一：NGCAT / NORAD_CAT_ID / SAT_ID 都见过，
    但 TLE_LINE1 / TLE_LINE2 是稳的，所以编号和卫星名优先从 TLE 行里取，
    JSON 字段只做兜底 —— 少踩一次改名坑。
    """
    data = json.loads(text)
    if isinstance(data, dict):                     # 出错时是 {"message": "..."}
        data = data.get("data") or data.get("member") or []
    if not isinstance(data, list):
        return {}
    out = {}
    for it in data:
        if not isinstance(it, dict):
            continue
        l1 = str(it.get("TLE_LINE1") or it.get("line1") or "").strip()
        l2 = str(it.get("TLE_LINE2") or it.get("line2") or "").strip()
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue
        name = (it.get("OBJECT_NAME") or it.get("name")
                or it.get("NGCAT") or norad_of(l1))
        out[norad_of(l1)] = build_record(name, l1, l2, fetched_at)
    return out


def parse_hydra_json(text, fetched_at=None):
    """tle.ivanstanojevic.me → {norad: 记录}。

    单颗是 {"satelliteId":..,"name":..,"line1":..}，搜索是 hydra 集合
    {"member": [ {...}, ... ]}。两种都吃。
    """
    data = json.loads(text)
    items = []
    if isinstance(data, dict):
        if isinstance(data.get("member"), list):
            items = data["member"]
        elif data.get("line1"):
            items = [data]
    elif isinstance(data, list):
        items = data
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        l1 = str(it.get("line1") or it.get("TLE_LINE1") or "").strip()
        l2 = str(it.get("line2") or it.get("TLE_LINE2") or "").strip()
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue
        name = it.get("name") or it.get("OBJECT_NAME") or norad_of(l1)
        out[norad_of(l1)] = build_record(name, l1, l2, fetched_at)
    return out


def parse_satnogs_json(text, fetched_at=None, keep=None):
    """db.satnogs.org/api/tle/?format=json → {norad: 记录}。

    实测返回**裸 JSON 数组**（不是 DRF 的 {count,results}），实测字段：
        tle0（带 "0 " 前缀的名字）/ tle1 / tle2 / tle_source / sat_id /
        norad_cat_id / updated
    1710 条里有 1700 条 tle1/tle2 可用。`updated` 不是历元（是入库时间），
    历元一律从 tle1 解，别拿 updated 比较新旧。
    keep 给一个 NORAD 集合时只收这些星（缓存里已有的），避免把 1600 多颗
    非业余星灌进界面列表。
    """
    data = json.loads(text)
    if isinstance(data, dict):                      # 容错：万一以后改成 DRF 分页
        data = data.get("results") or data.get("data") or []
    if not isinstance(data, list):
        return {}
    out = {}
    for it in data:
        if not isinstance(it, dict):
            continue
        l1 = str(it.get("tle1") or it.get("TLE_LINE1") or "").strip()
        l2 = str(it.get("tle2") or it.get("TLE_LINE2") or "").strip()
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            continue
        norad = norad_of(l1)
        if keep is not None and norad not in keep:
            continue
        name = str(it.get("tle0") or it.get("name") or "").strip()
        if name[:2] == "0 ":                        # tle0 带 "0 " 前导，去掉
            name = name[2:].strip()
        out[norad] = build_record(name or norad, l1, l2, fetched_at)
    return out


PARSERS = {
    "tle3": parse_three_line,
    "celestrak_json": parse_celestrak_json,
    "hydra_json": parse_hydra_json,
    "satnogs_json": parse_satnogs_json,
}


# ── 源表 ──────────────────────────────────────────────────────────────
class Source:
    """一条数据源。

    name     源名（进缓存 _sources、进状态表）
    url      批量源的 URL；单星源的模板，用 {norad} 占位
    parser   PARSERS 的键
    priority 数字小的先抓，同时作为"历元打平时谁赢"的裁决顺序
    batch    True=一次抓一批；False=只能按需查单颗
    note     覆盖范围 / 坑，写进报告和状态表
    timeout  这条源的请求超时（秒）。慢源（SatNOGS 全库）单独放宽，别拖累别人。
    """

    __slots__ = ("name", "url", "parser", "priority", "batch", "note", "timeout")

    def __init__(self, name, url, parser, priority, batch=True, note="",
                 timeout=DEFAULT_TIMEOUT):
        self.name = name
        self.url = url
        self.parser = parser
        self.priority = priority
        self.batch = batch
        self.note = note
        self.timeout = timeout

    def host(self):
        try:
            return urllib.parse.urlsplit(self.url).netloc.lower()
        except Exception:  # noqa: BLE001
            return ""

    def __repr__(self):
        return f"<Source {self.name} p{self.priority}{'' if self.batch else ' single'}>"


SOURCES = [
    # 优先级 10：本机实测唯一稳定可达的批量源，先抓它保证"至少一个源成功"。
    Source("amsat_nasabare",
           "https://www.amsat.org/tle/current/nasabare.txt",
           "tle3", 10, True,
           "AMSAT nasabare.txt，三行一组约 97 颗业余星；只列当前活跃的，覆盖窄但稳"),
    Source("celestrak_gp",
           CELESTRAK_GP.format(group="amateur", fmt="json"),
           "celestrak_json", 20, True,
           "CelesTrak amateur 分组 JSON，字段最全（含 BSTAR/历元）；本机实测连不上"),
    Source("celestrak_tle",
           CELESTRAK_GP.format(group="amateur", fmt="tle"),
           "tle3", 30, True,
           "CelesTrak amateur 分组纯 TLE，和 celestrak_gp 同源不同格式，互为备份"),
    Source("celestrak_satnogs",
           CELESTRAK_GP.format(group="satnogs", fmt="tle"),
           "tle3", 40, True,
           "CelesTrak satnogs 分组，业余/科研立方星覆盖比 amateur 广得多（上千条）"),
    Source("celestrak_active",
           CELESTRAK_GP.format(group="active", fmt="tle"),
           "tle3", 50, True,
           "CelesTrak active 分组，全在轨约上万条；只当兜底，条目多会拖慢校验"),
    Source("celestrak_stations",
           CELESTRAK_GP.format(group="stations", fmt="tle"),
           "tle3", 60, True,
           "CelesTrak stations 分组（含 ISS/天宫），条目少但历元通常最新"),
    # 优先级 70：唯一实测可达的"大库"批量源（1700 条），但一次要 40~180 s，
    # 所以默认不跑，只在大源全挂时兜底、或 --extra 显式开启。
    Source("satnogs",
           SATNOGS_TLE,
           "satnogs_json", 70, True,
           "SatNOGS 全库 TLE（1700 条 / 537 KB，实测 40~180 s）；覆盖远超 amateur，"
           "但慢，默认只兜底或 --extra 开启",
           timeout=180),
    Source("tleapi",
           TLEAPI + "/{norad}",
           "hydra_json", 90, False,
           "tle.ivanstanojevic.me 单星查询，只给缺失的星补一条；批量查询会撞 508 资源限制"),
]


def source_by_name(name):
    for s in SOURCES:
        if s.name == name:
            return s
    return None


# ── 抓取层 ────────────────────────────────────────────────────────────
class FetchError(RuntimeError):
    def __init__(self, msg, http=None, kind="error"):
        super().__init__(msg)
        self.http = http
        self.kind = kind          # connect / http / empty / decode


def probe_host(url, connect=CONNECT_TIMEOUT):
    """TCP 连通性预探（同一个 host 每次进程只探一次）。

    为什么值得多这一跳：celestrak 的失败是"TCP 连不上"，不是 HTTP 报错，
    靠 urllib/curl 的超时判定要陪它等满整段 body 超时（实测单源 21~87 s）。
    先花最多 5 s 探一下握手，不通就直接判死，整次刷新从分钟级掉到 20 s 上下。
    """
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return True                       # 怪 URL，交给真正的请求去报错
    if host in _HOST_OK:
        return _HOST_OK[host]
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        sock = socket.create_connection((host, port), timeout=connect)
        sock.close()
        _HOST_OK[host] = True
    except OSError:
        _HOST_OK[host] = False
    return _HOST_OK[host]


def _urllib_get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), getattr(r, "status", 200)


def _curl_get(url, timeout):
    """curl 兜底。--ssl-no-revoke 是这台 Windows 的关键：schannel 吊销检查
    在离线/受限网络下会直接 0x80092013 失败（实测 rc=35），加上才拿得到数据。
    -L 吃 301（tleapi 就 301 到 api/v2 之类）。--connect-timeout 让"连不上"
    5 s 就认输，而不是陪 TCP 握手耗到 -m 的整段时间。"""
    cmd = ["curl", "-sS", "-L", "--ssl-no-revoke", "--compressed",
           "--connect-timeout", str(int(CONNECT_TIMEOUT)),
           "-m", str(int(timeout)), "-A", UA,
           "-w", "\n@@HTTP%{http_code}@@", url]
    out = subprocess.run(cmd, capture_output=True, timeout=timeout + 20)
    if out.returncode != 0:
        raise FetchError(f"curl rc={out.returncode} "
                         f"{out.stderr.decode('utf-8', 'replace').strip()[:160]}",
                         kind="connect")
    body = out.stdout
    code = 0
    idx = body.rfind(b"\n@@HTTP")
    if idx >= 0:
        tail = body[idx:].decode("utf-8", "replace").strip()
        body = body[:idx]
        try:
            code = int(tail.strip("@HTTP").strip("@@").strip())
        except ValueError:
            code = 0
    if code and not (200 <= code < 300):
        raise FetchError(f"HTTP {code}", http=code, kind="http")
    return body, code or 200


def fetch(url, timeout=DEFAULT_TIMEOUT, retries=RETRIES, connect=True):
    """取一段内容 → (bytes, http_code)。

    先 urllib（本机实测 5~6 s 能拿到 amsat），失败换 curl（更稳，但子进程开销大）。
    每一次尝试内部先 urllib 再 curl，整体最多 1 + retries 次。
    connect=True 时先做 TCP 预探：连不上就立刻失败，不陪超时干等。
    """
    if connect and not probe_host(url):
        raise FetchError(f"TCP 连不上（{CONNECT_TIMEOUT}s 握手超时，已跳过）",
                         kind="connect")
    last = None
    for attempt in range(1 + max(0, retries)):
        try:
            body, code = _urllib_get(url, timeout)
            if body and len(body) >= MIN_BODY:
                return body, code
            last = FetchError(f"urllib 只拿到 {0 if not body else len(body)} B",
                              kind="empty")
        except urllib.error.HTTPError as e:
            last = FetchError(f"HTTP {e.code}", http=e.code, kind="http")
            if e.code in (404, 403, 400):      # 客户端错，重试没意义
                break
        except (socket.timeout, TimeoutError):
            last = FetchError(f"urllib 超时 {int(timeout)} s", kind="connect")
        except urllib.error.URLError as e:
            last = FetchError(f"urllib 连接失败 {e.reason}", kind="connect")
        except Exception as e:  # noqa: BLE001  （ssl 握手失败、解析前的一切）
            last = FetchError(f"urllib {type(e).__name__}: {e}", kind="connect")
        try:
            body, code = _curl_get(url, timeout)
            if body and len(body) >= MIN_BODY:
                return body, code
            last = FetchError(f"curl 只拿到 {len(body) if body else 0} B",
                              kind="empty")
        except FetchError as e:
            last = e
            if e.kind == "http" and e.http in (404, 403, 400):
                break
        except FileNotFoundError:
            last = FetchError("没有 curl 可用", kind="connect")
            break
        except subprocess.TimeoutExpired:
            last = FetchError(f"curl 超时 {int(timeout)} s", kind="connect")
        except Exception as e:  # noqa: BLE001
            last = FetchError(f"curl {type(e).__name__}: {e}", kind="connect")
        if attempt < retries:
            time.sleep(1.5)
    raise last or FetchError("未知失败")


# ── 单源抓取 ──────────────────────────────────────────────────────────
def fetch_source(src, timeout=None, fetched_at=None, retries=RETRIES, keep=None):
    """抓一个批量源 → 状态 dict。

    timeout 为 None 时用该源自己的 Source.timeout（慢源单独放宽）。
    keep 只在 satnogs 这类"大库"源上有意义：只收 NORAD 在集合里的星。
    返回：{"name","url","priority","ok","http","count","elapsed","error",
           "records","fetched_at","note"}
    绝不抛异常：任何失败都折叠成 ok=False + error 文本，让上层自己决定。
    """
    if timeout is None:
        timeout = getattr(src, "timeout", DEFAULT_TIMEOUT)
    if fetched_at is None:
        fetched_at = time.time()
    st = {"name": src.name, "url": src.url, "priority": src.priority,
          "ok": False, "http": 0, "count": 0, "elapsed": 0.0, "error": "",
          "records": {}, "fetched_at": fetched_at, "note": src.note,
          "batch": src.batch, "filtered": 0}
    t0 = time.time()
    try:
        body, code = fetch(src.url, timeout=timeout, retries=retries)
        st["http"] = code
        text = body.decode("utf-8", "replace")
        parser = PARSERS[src.parser]
        if parser is parse_satnogs_json:
            recs = parser(text, fetched_at, keep=keep)
        else:
            recs = parser(text, fetched_at)
        st["records"] = recs
        st["count"] = len(recs)
        if keep is not None and parser is parse_satnogs_json:
            st["filtered"] = 1          # 标记这次走了"只留已知星"的过滤
        if recs:
            st["ok"] = True
        else:
            st["error"] = "解析出 0 条（格式变了或分组为空）"
            if st["filtered"]:
                st["error"] += "；或过滤后一颗都没有"
    except FetchError as e:
        st["http"] = e.http or 0
        st["error"] = str(e)
    except Exception as e:  # noqa: BLE001
        st["error"] = f"{type(e).__name__}: {e}"
    st["elapsed"] = round(time.time() - t0, 1)
    return st


def fetch_one(norad, timeout=DEFAULT_TIMEOUT, fetched_at=None):
    """单星补抓（tleapi）：拿不到就返回 None，不抛异常。"""
    src = source_by_name("tleapi")
    url = src.url.format(norad=str(norad))
    if fetched_at is None:
        fetched_at = time.time()
    try:
        body, _code = fetch(url, timeout=timeout)
        recs = parse_hydra_json(body.decode("utf-8", "replace"), fetched_at)
        return recs.get(str(norad).lstrip("0") or "0")
    except Exception:  # noqa: BLE001
        return None


def fetch_one_satnogs(norad, timeout=60, fetched_at=None):
    """单星补抓（SatNOGS，?norad_cat_id=单值）。

    实测：单值过滤可用（HTTP 200 / 5.9 s / 1 条），但它**不支持多值**——
    `?norad_cat_id=25544,7530` 会 400 {"norad_cat_id":["Enter a number."]}，
    所以只能一颗一颗问。拿不到同样返回 None。
    """
    if fetched_at is None:
        fetched_at = time.time()
    url = SATNOGS_ONE.format(norad=str(norad).lstrip("0"))
    try:
        body, _code = fetch(url, timeout=timeout, retries=0)
        recs = parse_satnogs_json(body.decode("utf-8", "replace"), fetched_at)
        return recs.get(str(norad).lstrip("0") or "0")
    except Exception:  # noqa: BLE001
        return None


def fetch_all(sources=None, timeout=None, on_status=None,
              skip=(), only=(), retries=RETRIES, keep=None):
    """按优先级逐源抓批量源 → [状态 dict]，顺序即优先级顺序。

    同一主机第一次就"连不上"时，后面属于该主机的源**直接跳过**（连探都不探）：
    否则 celestrak 不通时一次刷新要等 5×(5 s 探活 + 重试)，用户会以为程序死了。
    skip 里的源名不进 results；on_skip 回调通过 on_status 收到一条 ok=False 的记录。
    """
    if sources is None:
        sources = SOURCES
    pool = [s for s in sources if s.batch]
    if only:
        pool = [s for s in pool if s.name in only]
    pool = [s for s in pool if s.name not in skip]
    pool.sort(key=lambda s: (s.priority, s.name))

    failed_hosts = {}
    results = []
    for src in pool:
        host = src.host()
        if host in failed_hosts:
            st = {"name": src.name, "url": src.url, "priority": src.priority,
                  "ok": False, "http": 0, "count": 0, "elapsed": 0.0,
                  "error": f"跳过：{host} 本次已确认不可达",
                  "records": {}, "fetched_at": time.time(),
                  "note": src.note, "batch": src.batch, "filtered": 0}
            results.append(st)
            if on_status:
                on_status(st)
            continue
        rtry = retries
        st = fetch_source(src, timeout=timeout, retries=rtry, keep=keep)
        if not st["ok"] and st["http"] == 0:
            failed_hosts[host] = st["error"]
        results.append(st)
        if on_status:
            on_status(st)
    return results


def format_status_table(results):
    """源状态表：源名 / HTTP / 条目 / 耗时 / 失败原因。"""
    if not results:
        return "（没有批量源）"
    w = max(len(r["name"]) for r in results)
    lines = [f"{'源'.ljust(w)}  {'HTTP':>4}  {'条目':>5}  {'耗时':>6}  结果"]
    for r in results:
        http = str(r["http"]) if r["http"] else "-"
        lines.append(f"{r['name'].ljust(w)}  {http:>4}  {r['count']:>5}  "
                     f"{r['elapsed']:>5.1f}s  "
                     f"{'ok' if r['ok'] else (r['error'] or '失败')}")
    return "\n".join(lines)
