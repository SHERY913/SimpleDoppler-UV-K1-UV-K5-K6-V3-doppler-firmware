# TLE 数据源说明

本文解释 `refresh_tle.py` 的多源抓取链路：有哪些源、各自覆盖什么、合并规则、以及每个坑。
代码：`refresh_tle.py`（流程/合并/落盘）+ `tle_sources.py`（源表/HTTP/解析器），只用标准库。

跑法：

```bash
python refresh_tle.py            # 默认：常规源（satnogs 不跑），约 13~20 s
python refresh_tle.py --check    # 只读缓存，离线看老化分档
python refresh_tle.py --list     # 离线打印源表
python refresh_tle.py --stale    # 只补历元 >14 天的星（tleapi + SatNOGS 单星），秒级
python refresh_tle.py --stale 7  # 同上，门槛改成 7 天
python refresh_tle.py --extra    # 加跑 SatNOGS 大库，只补已知星（约 2 min）
python refresh_tle.py --extra-all# 加跑 SatNOGS 并全量并入（96 → 1700 颗，慎用）
python refresh_tle.py --only amsat_nasabare        # 只跑指定源
python refresh_tle.py --skip celestrak_active      # 跳过某些源
```

---

## 1. 源表（按优先级）

优先级数字小 = 先跑、冲突时优先。表在 `tle_sources.py: SOURCES`。

| 优先级 | 源名 | URL | 格式 | 覆盖 | 实测（2026-10-27，本机） |
|---|---|---|---|---|---|
| 10 | `amsat_nasabare` | `https://www.amsat.org/tle/current/nasabare.txt` | 三行 | AMSAT 收录的业余星，**97 颗** | ✅ 200 / 97 条 / 2.4~15.3 s |
| 20 | `celestrak_gp` | `...gp.php?GROUP=amateur&FORMAT=json` | JSON | amateur 分组，字段最全 | ❌ TCP 连不上（5 s 握手超时即判死） |
| 30 | `celestrak_tle` | `...GROUP=amateur&FORMAT=tle` | 三行 | 同 20，纯 TLE | ❌ 主机已判死，直接跳过（0.0 s） |
| 40 | `celestrak_satnogs` | `...GROUP=satnogs&FORMAT=tle` | 三行 | 业余/科研立方星，覆盖最广 | ❌ 同上 |
| 50 | `celestrak_active` | `...GROUP=active&FORMAT=tle` | 三行 | 全在轨上万条，仅兜底 | ❌ 同上 |
| 60 | `celestrak_stations` | `...GROUP=stations&FORMAT=tle` | 三行 | 空间站（ISS/天宫） | ❌ 同上 |
| 70 | `satnogs` | `https://db.satnogs.org/api/tle/?format=json` | JSON 数组 | **全库 1700 条 / 537 KB** | ✅ 200 / 1700 条（过滤后 95）/ **40~119 s** |
| 90 | `tleapi` | `https://tle.ivanstanojevic.me/api/tle/{norad}` | JSON | **单颗**补充 | ✅ 200 / 2.5~4.6 s（批量 508） |

CelesTrak 五个源全指向 `celestrak.org`，本机 80/443 都 TCP 连不上（不是 403/429）。所以当前真正干活的是 `amsat_nasabare`（默认）+ `satnogs`（`--extra` / 兜底 / `--stale` 单星）。等 celestrak 恢复，20/30/40 会自动开始贡献条目，无需改代码。

### 各源的坑

- **AMSAT `nasabare.txt`**（主源）
  - 文件头有说明文字，`parse_three_line` 只认「`1 ` 开头 + `2 ` 开头」的成对行，容忍 `SB KEPS @ AMSAT` 公告头。
  - curl 取 https 必须带 `--ssl-no-revoke`：本机 schannel 撤销检查离线，否则 `curl: (35) schannel: CRYPT_E_REVOCATION_OFFLINE (0x80092013)`，看着像源挂了其实是 TLS 环境问题。已在 `_curl_get()` 内建。
  - 97 颗含 43879 `ISAT`（已再入），`validate()` 用 sgp4 剔除，落盘 96 颗，正常。
- **CelesTrak `gp.php`**（20/30/40/50/60）
  - 连不上时状态表显示 HTTP `-`、原因 `TCP 连不上（5s 握手超时，已跳过）`；从第二个 celestrak 源起直接 0.0 s 跳过（同一主机本次已判死）。
  - `FORMAT=json` 的编号字段改版会变（`NORAD_CAT_ID` ↔ `NGCAT`），解析器都认；**NORAD 一律从 TLE 行解析**（第 3~7 位）。
  - `GROUP=active` 上万条、单次数 MB，慢；只想补业余星就 `--skip celestrak_active`。
  - 项目里另有 `celestrak_down.flag` 哨兵（`doppler_core.py:117` 用它让单星抓取跳过 celestrak）。`refresh_tle.py` **不读这个 flag**，每次都会真探测（状态表要如实）；想省那 5 s 就自己 `--skip=celestrak_*`。
- **SatNOGS `db.satnogs.org`**（70，唯一实测可达的大库）
  - 返回**裸 JSON 数组**（不是 DRF 的 `{count,results}`），实测字段：`tle0`（名字带 `"0 "` 前缀）/ `tle1` / `tle2` / `tle_source` / `sat_id` / `norad_cat_id` / `updated`；1735 条里 1700 条 tle1/tle2 可用。
  - `updated` 是**入库时间**，不是历元 —— 历元一律从 `tle1` 解（有个别星很旧：实测 ISS 那条是 2023-01-06，会被 AMSAT 的新历元自然顶掉）。
  - 慢：connect 只要 0.97 s，**body 要 40~119 s**（537 KB，服务端吐得慢）。所以单独给 `timeout=180`，绝不塞进 25 s 通用口径；默认也不跑，只在大源全死时兜底或 `--extra` / `--extra-all` 开启。
  - **不支持多值过滤**：`?norad_cat_id=25544,7530` → `400 {"norad_cat_id":["Enter a number."]}`；单值 `?norad_cat_id=25544` 可用（200 / ~6 s / 1 条）。故批量只能整库拉，单星补抓靠 `fetch_one_satnogs()`。
  - 覆盖取舍：1700 条里 **1605 条不在 AMSAT 的 96 颗里**，绝大多数是非业余星。全量并入会把界面列表从 96 撑到 1701，所以默认用 `keep` 过滤成"缓存里已有的 NORAD"（命中 95/96），只有 `--extra-all` 才全量并入。
- **`tleapi`（tle.ivanstanojevic.me）**
  - 单颗 `/api/tle/{norad}` 200，2.5~4.6 s；`?search=` 批量返回 **HTTP 508 Resource Limit Is Reached**，不能当批量源。
  - 历元常比 AMSAT 旧（ISS：2026-10-26 14:18 vs 10-27 04:14），排 90 位靠历元比较自然落选。
- **试过没用**：`r.jina.ai`/allorigins 等代理（超时）、`api.github.com` 上的 TLE 镜像（没找到稳定覆盖业余星的）、AMSAT `daily-bulletin.txt` / `nasa.all`（可通，但同样 97 颗，无增益）、`api.wheretheiss.at`（curl rc=28）。

---

## 2. 合并规则

1. 按优先级 10 → 90 依次抓，**单源失败不影响其它源**。
2. 以 **NORAD 编号**为键合并。
3. 同一颗星多源都有时：**比较 TLE 历元**（`l1[18:32]` 的 `YYDDD.DDDDDDDD` 解出的 UTC 时刻），**不是抓取时间**；历元更新者胜。打平（差 < 1 s）保留高优先级那条。胜负写进 `conflicts` 并打印。
4. 只在一个源出现的星直接带入。
5. **防回退**：合并后如果旧缓存里某颗星的历元比这次抓到的还新（例：昨晚 `--stale` 补过 IO-86，今天只跑 AMSAT，而 AMSAT 那条还是三个月前的），保留缓存里的旧条，打印「保留旧缓存 N 颗」。否则会出现"补一次、下次刷新又被打回原形"。
6. 最后过 `validate()`（sgp4 判据，剔除解算异常/已再入），再写盘。

**失败语义**：至少一个源成功 → 写盘，exit 0（有源失败也写盘，但状态表标 FAIL）；全部源失败 → **不动旧缓存**，非 0 退出。常规源全灭时会自动用 SatNOGS 兜底（此时不过滤、全量并入），兜底也失败才放弃。

---

## 3. `--stale`（只刷陈旧星）

```bash
python refresh_tle.py --stale        # 门槛 14 天（与 PC 侧 tle_stamp TTL 对齐）
python refresh_tle.py --stale 7
```

- 只挑缓存里历元超过门槛的星，逐颗问 **tleapi**（8 s）→ **SatNOGS 单星**（24 s），并发 4。
- 只有历元确实更新才替换；两个接口都拿不到就明确打 `✗ 源里也没有`，并保留旧值。
- 实测（本次）：2 颗超龄 → `40931 IO-86/LAPAN-A2` 被 SatNOGS 从 07-18 刷到 10-17（+2174 h，101 天 → 11 天）；`71922 LILIUM-4` 两个接口都没有，仍是 211 天。exit 2（因为有未解决的）。
- 适用场景：日常只补几颗，别为了两颗星跑 2 分钟全量。

---

## 4. 输出与向后兼容

写两份：

- `多普勒/PC工具/多普勒工具/tle_cache.json`：`{ "norad": [name, l1, l2, 抓取时间, 历元, 倾角, RAAN, 偏心率, 近地点幅角, 平近点角, 平均运动, B*], ... , "_sources": [...] }`
- `多普勒/安卓App/android-app/app/src/main/assets/tles.json`：`{ "norad": [name, l1, l2] }`（App 只要前 3 项）

主结构不变，老代码按 `cache[norad]` 取值完全不受影响；前 3 项固定 `name / line1 / line2`，后 9 项位置也不变（记录恒为 12 项）。

`_sources` 是新增的**顶层元数据键**，list of dict，每条 `{source, priority, url, http, count, ok, elapsed_s, fetched_at, fetched_iso, error}` = 每个源最后一次实测的抓取时间与条目数。没跑的源（`--skip`、默认不跑 satnogs）**保留上次记录**，所以表不会被一次 `--skip` 冲成一行。

### 为什么 `_sources` 是「列表」而不是「字典」

- `doppler_tool.py:761` `stamps = [tle_stamp(v) for v in cache.values()]`；`tle_stamp()` 只 catch `TypeError/ValueError`，遇 dict 会 KeyError 崩 GUI。列表走 `float(v[3])` → TypeError → 被吞掉返回 0。
- `doppler_tool.py:728/798` 的 `int(norad_s)` 在 `try` 内，非数字键被跳过；App 侧 `Model.kt:70` `optJSONArray(key) ?: continue` 也安全（且 assets 根本不写 `_sources`）。
- `verify_data.py` 现在**显式忽略 `_` 开头的顶层键**（否则每次刷新都会多一条 P1「只在工具侧有 `['_sources']`」）。这条忽略只为元数据而加，卫星条目仍然逐条严格校验。

---

## 5. 老化口径（`--check`）

完全离线，只读 `tle_cache.json`：

- 顶部：`_sources` 里每个源的最后状态、条目总数。
- 分档（按**每颗星 TLE 历元距今天数**）：`>7 天` 关注；`>14 天` PC 侧 `tle_stamp` TTL 到期、界面提示；`>30 天` App 侧 `tleStale` 标红、位置明显偏。
- 逐颗明细（NORAD / 名称 / 历元天数 / 倾角 / 轨道类型），按天数降序。

当前状态：96 颗，`>7 天` 2 颗、`>14 天` 1 颗、`>30 天` 1 颗 —— `71922 LILIUM-4`（211 天，所有可达源里都没有更新元素）。

---

## 6. HTTP 行为

- `User-Agent`：`Mozilla/5.0 (Windows NT 10.0; Win64; x64)`。
- **TCP 预探**：每个 host 本次进程内先做一次 5 s 握手探测（`probe_host`）。连不上就直接判死，不再发请求 —— 这把 celestrak 的失败成本从 87 s 压到 5 s。后续同主机源**直接跳过（0.0 s）**。
- 请求超时：默认 25 s（任务口径）；`satnogs` 单独 180 s；curl 另带 `--connect-timeout 5`。
- 失败重试 1 次；404/403/400 不重试；同主机判死后的源不重试。
- 双路取数：先 `urllib`，失败退 `curl` 子进程（本机 urllib 会卡 TLS 握手，这条后路必须保留）。
- 响应体 < 200 字节视为失败（防错误页当 TLE）。

### 耗时（实测）

| 场景 | 耗时 |
|---|---|
| 默认（常规源，celestrak 全灭） | **13~20 s** |
| `--skip=celestrak_*` | **13.3 s** |
| `--stale`（2 颗） | ~8 s |
| `--extra`（含 SatNOGS 全库 537 KB） | **106~119 s** |

改动前：默认要 166.7 s（celestrak_gp 单源就 87 s）。压缩来自"预探 + 同主机直接跳过"。
