# 外部参考：Look4Sat（rt-bishop）源码研读

- 仓库：<https://github.com/rt-bishop/Look4Sat>
- 实际读到的版本：`a471379f07eee32febc24b6824f1de304b26222a`，tag **v4.4.9**（2026-10-11，"Per-pass AOS/LOS timers, pass search, various fixes"）
- 获取方式：`git clone --depth 1 https://gh-proxy.com/https://github.com/rt-bishop/Look4Sat.git`（github.com:443 直连失败、ghproxy.net 报 `fetch-pack: invalid index-pack output`，gh-proxy.com 可用）
- 代码规模：多模块 Gradle（`app` / `core:{data,domain,presentation}` / `feature:{map,passes,radar,satellites,settings,status}`），117 个 `.kt`
- 本文验证程度：**全部结论均为源码实读**（行号为该 commit 下的真实行号），另加一节我自己用 `sgp4 2.27` 算出来的数值锚点（跑过）。凡引用我方固件行为的，标注了来源是既有审计文档还是本次实读。

---

## 1 概述

Look4Sat 是一个纯粹的**"跟踪器 + 电台遥控器"**：它算卫星位置、算多普勒、把频率写进 Kenwood/Yaesu/Icom 商品电台，**不控制任何自研固件**。它和我们最大的结构差异是：

| | Look4Sat | 我们（UV-K1/K5 + App） |
|---|---|---|
| 执行机构 | 商品电台的 CAT/CI-V 文本命令，10 Hz 分辨率，200 ms/条 | 自定义二进制帧 `AB CD len｜掩码body｜掩码crc｜DC BA`，10 Hz 单位，5 Hz 推帧 |
| 频率权威 | App 每轮把绝对频率写下去（电台是"哑执行器"） | App 推数、**固件自己**调 `BK4819_SetFrequency()`（芯片状态机不被绕过，`MainScreen.kt:415-418` 的注释说明了为什么） |
| 校正节拍 | 1 Hz（`delay(1000)`） | 200 ms 推帧；固件 1 Hz 写 PLL |
| 晶振校准 | **完全没有 ppm 概念** | 有 `clk_ppm`（固件 `App/app/doppler.c`），但目前只修墙钟 |

它值得我们学的地方集中在四块：**多普勒公式的符号与"卫星域基准频率"这个单一状态量**、**"用户拧旋钮"的检测与反算**、**台站位置的存取与 UI 回显**、**多源 TLE 刷新与失败兜底**。

同时要明确：**它没有 ppm，也没有任何"变化率平滑/滤波"**——这两条我们不能从它身上抄，只能抄它的替代方案或自己补。

---

## 2 逐条对比表

| 主题 | Look4Sat 怎么做（file:line） | 我们现状（file:line） | 建议（改哪、改成什么） |
|---|---|---|---|
| 多普勒公式 | `core/domain/.../predict/OrbitalPos.kt:43-49`：`rx = f·(c−v)/c`，`tx = f·(c+v)/c`，v = `distanceRate`×1000（km/s→m/s），**上下行符号相反** | `orbit/Geo.kt:113-114`：`dopplerHz = -freqHz * rangeRateKmS / C_KMS`；`MainScreen.kt:413` 收 `rx = rxHz + dop`，`:434,459` 发 `tx = upHz - upDopp` | 已经等价（我们用 `-v` 表示收、`-(-v)` 表示发）。**不加改动**，但把 `MainScreen.kt:428-433` 那段注释里的推导固化成单测断言（见 §3.2） |
| 距离变化率算法 | `OrbitalObject.kt:198`：`dot(rangeVector, rgvelVector) / rangeVector.w` —— 视线方向速度投影，**无平滑、无滤波、无外推** | `Geo.kt:96-102`：对距离做 ±0.5 s 中心差分（每次 3 次 SGP4 传播） | 两者数学等价（我们的更"笨"但更难写错）。**可优化**：`MainScreen.kt:400` 每 200 ms 调一次 `rangeRate`（3 次传播）；改成复用同一时刻的 `range(t)` 与上一帧缓存值，可省 1/3 传播量。**不必改精度** |
| 校正节拍 | 1 Hz（`RadioTrackingService.kt:290`、`:474`），雷达页刷新也是 1 Hz（`RadarViewModel.kt:176`） | App 5 Hz 推帧（`MainScreen.kt:507 delay(200)`）；固件 1 Hz 写 PLL | 5 Hz 对我们有利（10 Hz 量化单位下，ISS 峰值也才 **59 Hz/s**，见 §3.2 —— 5 Hz 一次只有 ~12 Hz 步进）。**不改节拍**；要改的是"三处节拍不一致"（见下表 TLE/推帧条） |
| 变化率是否需要平滑 | 无。1 Hz 采样 `distanceRate` 直接乘 | 无 | **两边都不需要**：SGP4 是解析解，没有抖动；抖动来自"时间取整"和"量化"。我们的抖动源是 `nowS = System.currentTimeMillis()/1000.0`（`MainScreen.kt:399`，**毫秒精度**，好）——保持 |
| 转发器/频带映射 | `DopplerFrequencyCalculator.kt:45-124`：先去下行多普勒 → **在"卫星域"做 passband 映射** → 再上上行多普勒（`:52-56`）；映射规则 `TransponderMapper.kt:24-70`，倒置转发器 `downlinkHigh - offset` | `Freqs.kt:1-43` 是**固定上下行频点表**，没有"转发器频带"概念（ISS 收 437.800 / 发 145.990 定死） | **暂不需要抄**（我们支持的星都是固定频点中继）。但若以后加线性转发器（JAS-2、FUNcube-1 我们有表项），必须按它这个顺序做，否则带内偏移会被多普勒污染 |
| 单一状态量 | `txBaseFreq`（卫星域基准）是唯一状态，rx/tx 都由它派生（`RadioTrackingService.kt:262-266`） | App 每帧用 `Freqs` 里的标称 `downMhz/upMhz` 重新算（`MainScreen.kt:408-434`），等于"每帧重置基准" | 我们没有"用户微调后保持"的能力：用户拧旋钮，下一帧 200 ms 就被推回去。想支持"手调"，得引入 `txBaseFreq` 这个状态（见下一行） |
| 用户手调（替代"发射冻结"） | **没有"发射时冻结频率"**。做法是"读回频率与上次写入值比，差 ≥20 Hz 判定人在拧"（`:244`、`:253`），停推；连续 2 次读数差 ≤20 Hz 判稳定（`stableCount >= 2`，`:214-216`），再反算基准：TX `newBase = read·c/(c+v)`（`:218`），RX `rxNominal = read·c/(c−v)` → `mapDownlinkToUplink`（`:225-226`） | 无。我们固件接管后 App 每 200 ms 覆盖，旋钮必被夺回（依据 `MainScreen.kt:455-472` 实读：无条件 `pushExt`） | 这是**最值得抄的一条**，但必须先有"频率回读"。我们协议里 `RadioSession.fwBitsLine()/stateLine()`（`RadioSession.kt:702,803`）已能回读固件状态，需确认有没有回报当前 VFO 频率；有就照抄 20 Hz/2 次阈值 + 反算公式 |
| ppm / 晶振 | **全仓库无 ppm**（`grep -rn ppm` 只命中 `Sources.kt:41` 调制方式字符串 "PPM"）。替代物是**绝对 Hz 偏移** `RCSettings.frequencyOffsetHz`（`model/Settings.kt:44`），钳位 ±50 kHz（`model/Constants.kt:21-22`），在 `NetworkReporter.kt:75-79` 作 `correctedFreq = frequency + clampedOffset` 加到**推给电台的频率上**；UI 是一个输入框 + 一键归零（`SettingsDialog.kt:479-540`） | 固件有 `clk_ppm`（`App/app/doppler.c:349-369` 读、`:371-392` 存、`:280-293` 用时只修墙钟），频率链上没有任何一处乘 (1−ppm/1e6)（依据 `文档/固件核查-显示与刷新-2026-10-27.md` Q4，本次未重读固件） | **ppm 本身不能抄（他们没有）**，能抄的是"UI 层一个绝对 Hz 微调 + 一键归零 + ±50 kHz 钳位"这套**末端微调**的形态，用在"ppm 生效之前"的过渡期；真正的修法在固件（§3.6） |
| ppm 自动标定 | 无。没有信标测量流程 | 固件有校准页（手动输"真实时长"，`doppler.c:1416-1424` 算 ppm） | 抄不了。我们的手动标定已经比它强，问题只在"算出来没接到频率链" |
| 台站位置存储 | `GeoPos`（`predict/GeoPos.kt:20-26`）：`latitude/longitude/altitude/qthLocator/timestamp`；`SettingsRepo.kt:224-249` 逐字段存 SharedPreferences **字符串**，写入前 `lat.round(4)`、`lon.round(4)`、`alt.round(1)`；默认 `"JJ00aa"`；`positionToQth` 失败则整次保存返回 false | `Geo.kt:130-172` `StationStore`：存 **Float**（`putFloat`），`parse()` 有范围校验（±90/±180/−500~9000 m），未设时用 NaN 而不是默认坐标（这点我们做得比它好） | 我们的 `putFloat` 在 31.2304° 处只有 ~1e-5°≈1 m 精度，够用但**没必要损失**：改 `Geo.kt:156-158` 为 `putString(key, value.toString())` + 读侧 `toDoubleOrNull()`，与 Look4Sat 的 round(4) 对齐（4 位小数 ≈ 11 m，足够） |
| 台站 UI 回显（我们的 bug c） | `SettingsScreen.kt:342-348` **直接把已存值渲染成文本行**（`prefs_lat_prefix`/`prefs_qth_prefix`/`formatUpdateTime(timestamp)`）；输入框 `SettingsDialog.kt:88-117` 用 `rememberSaveable { mutableStateOf(lat.toString()) }` **从当前已存值初始化**，保存时 lat 夹 ±90、lon 夹 ±180（`:119-125`） | 我们的输入框显示不出已存值、必须点"存"（用户报告） | 抄它这两点即可：**①详情行直接渲染 `StationStore.load()` 的结果 + 时间戳；②对话框初值 = 已存值，而不是空串**。见 §3.7 的具体改法 |
| 位置校验 | `QthConverter.kt:22-55`：6 位 Maidenhead，正则 `[a-xA-X][a-xA-X]\d\d[a-xA-X][a-xA-X]`，`qthToPosition` 取格心（`+1/24`、`+1/48`）再 round(4) | `Geo.kt:163-171` 只收经纬度，校验给的是**人话错误信息**（"纬度得在 -90 ~ 90 之间"） | 校验强度我们够；QTH 网格**不抄**（我们协议传 lat/lon/alt，多一套网格只会多一个 bug 源） |
| 电台命令节拍 | FT-817：每条命令后固定 `commandDelayMs = 200`（`Ft817Controller.kt:42`）；IC-705：`WRITE_SETTLE_MS = 50` 写后静置、`ACK_TIMEOUT_MS = 500` 等 ACK、`POLL_INTERVAL_MS = 20` 轮询读缓冲（`Ic705Controller.kt:52-56`，`drainWithTimeout` `:302-320`）；多步初始化每步 200 ms（`RadioTrackingService.kt:51`） | 我们推帧 200 ms 一次、固件 1 Hz 写 PLL | 数值巧合但**语义不同**：它是"命令必须串行且慢"，我们是"帧可以丢、下一帧再来"。**不抄节拍**，抄它的"ACK 失败计数"（下一行） |
| 失败/断连恢复 | FT-817：ACK 读异常累计 `maxAckReadFailures = 3` 才置 `isConnected = false`（`:170-176`），`ack == -1`/流关闭立即断开（`:159-164`）；**没有自动重连**，靠 UI 重新 `connectRadios()`；所有 IO 用 `Mutex` 串行 | `RadioSession.kt` 有 `markLost()`（`:92`）+ 连续三帧无回包判掉线（`MainScreen.kt:483-498` 注释）+ 自动重连（`shouldAutoReconnect()` `:586`、`tryReconnect()` `:273`） | **我们这块比它强**（有自动重连、有 `probeAlive`）。可抄的只有"3 次计数才判死"这个**去抖阈值**：`RadioSession` 的判死阈值建议保持 3 帧不变，并在 `stateLine()` 里显示"连续失败 n/3" |
| SGP4 来源 | 自带 PREDICT v2.2.5 移植：`predict/NearEarthObject.kt` 的 `calculateSGP4`、`predict/DeepSpaceObject.kt` 的 `calculateSDP4`，常量 XKE/CK2/CK4 在 `predict/Constants.kt`。**不是** predict4java/orekit，无第三方卫星库 | `orbit/Sgp4.kt`（1137 行，自研） | **不抄**。但可借它的"深空 SDP4"思路做未来扩展（我们只做 LEO，暂不需要） |
| TLE 刷新 | 6 个源并发抓（`source/Sources.kt:22-31`：ariss / r4uab / mmccants / amsat nasabare / celestrak CSV / satnogs 3le）+ 2 个转发器源；`MainApplication.kt:38-53` **每 48 h** 一次（`timeDelta > 172_800_000L`），每次启动也 `initRepository()`；`DatabaseRepo.kt:66-101` 每源独立上报 HTTP 状态码到 UI，按 `epochDaynum` 取最新、名字取第一个有名字的源（`:141-158`），**全失败时保留旧 `updateTimestamp`**（`:97-100`）以便下次启动重试 | 我们的 TLE 从哪来、多久刷一次，本次未审（`PassCalc`/`MainScreen` 未见刷新代码，可能是 PC 工具生成过境表写 Flash） | 抄它两件事：**①"全源失败不清时间戳"**（否则会陷入"每次都以为刚更新过"）；**②每源独立状态上报**（我们能显示"哪家火了、哪家 404"）。48 h 这个数对我们没意义（我们靠表，不靠实时算） |

---

## 3 可直接抄的实现细节（带代码）

### 3.1 上下行多普勒：符号与常量

`core/domain/src/main/java/com/rtbishop/look4sat/core/domain/predict/OrbitalPos.kt:43-49`

```kotlin
fun getDownlinkFreq(freq: Long): Long {
    return (freq.toDouble() * (SPEED_OF_LIGHT - distanceRate * 1000.0) / SPEED_OF_LIGHT).toLong()
}

fun getUplinkFreq(freq: Long): Long {
    return (freq.toDouble() * (SPEED_OF_LIGHT + distanceRate * 1000.0) / SPEED_OF_LIGHT).toLong()
}
```

- `distanceRate` 单位 **km/s**，所以乘 1000 变 m/s；`SPEED_OF_LIGHT = 2.99792458E8`（`predict/Constants.kt`）。
- 关键是**同一个 `distanceRate`，收用减、发用加**：卫星远离（v>0）时收到的频率变低，地面要发得更高才能被收到。
- 他的 `.toLong()` 是**向零截断**，单边最多丢 1 Hz、且恒偏低；我们 `MainScreen.kt:458-459` 用 `Math.round(.../10.0)`，**已经比它好**，不要在抄的时候把它抄回去。

我们对应的代码（`安卓App/android-app/app/src/main/java/com/f4hwn/doppler/orbit/Geo.kt:113-114`）：

```kotlin
fun dopplerHz(freqHz: Double, rangeRateKmS: Double): Double =
    -freqHz * rangeRateKmS / C_KMS
```

`rx = rxHz + dop`（`MainScreen.kt:458`）、`tx = upHz - upDopp`（`:459`）。两边数学一致，**无需改动**。

### 3.2 "变化速度是否合物理"的数值锚点（我算过，可直接当验收断言）

Look4Sat 自己**没有任何变化率校验**（它只算瞬时值）。所以这一条只能我们自己造基准。我用 `sgp4 2.27` + 与 Look4Sat 完全相同的公式复算了一次 ISS 天顶过境（脚本：`_tmp_look4sat/doppler_rate_check.py`，本仓库临时目录，未提交）：

TLE（合成但元素真实，仅用于量级校验）：

```
1 25544U 98067A   26290.50000000  .00016717  00000-0  30242-3 0  9993
2 25544  51.6410 120.0000 0005000  90.0000 270.0000 15.50377579 00000
```

台站 39.9°N / 116.4°E，0 m，下行 145.8 MHz：

| t(s) | 斜距 km | 仰角° | rdot m/s | rx Hz |
|---|---|---|---|---|
| 4760 | 2166 | 1.7 | −6913 | 145803362 |
| 4820 | 1752 | 6.3 | −6875 | 145803343 |
| 4880 | 1342 | 12.5 | −6753 | 145803284 |
| 4940 | 946 | 22.4 | −6395 | 145803110 |
| 5000 | 593 | 42.8 | −5074 | 145802468 |
| 5030 | 465 | 62.6 | −3182 | 145801548 |
| **5060** | **417** | **87.8** | **+112** | **145799945** ← TCA |
| 5090 | 471 | 61.0 | +3340 | 145798376 |
| 5150 | 771 | 29.8 | +5997 | 145797084 |
| 5210 | 1153 | 16.3 | +6631 | 145796775 |
| 5330 | 1970 | 3.6 | +6897 | 145796646 |

由此得到的**硬指标**（可直接写进单测/自检）：

| 指标 | 值 | 说明 |
|---|---|---|
| 最大 \|rdot\| | **6.92 km/s** | ISS 圆轨道 7.66 km/s 相对速度的视线投影上限 |
| 145.8 MHz 多普勒摆幅 | **±3.37 kHz**（总 6.73 kHz） | `f·v/c`，v=6.92 km/s |
| 435 MHz 多普勒摆幅 | **±10.04 kHz** | 同上，1:3 关系 |
| 峰值 \|df/dt\| | **3.56 kHz/min = 59 Hz/s** | 出现在 TCA 前约 5 s、仰角 ~85° 处 |
| 1 s 内的最大频率步进 | **59 Hz**（85° 时）；55° 时 34 Hz/s；71° 时 51 Hz/s | 直接回答"1 Hz 节拍够不够"：够，且 5 Hz 推帧更宽裕 |
| TCA 处符号翻转 | 10 s 内 rdot 从 −498 → +112 → +720 m/s | 固件必须支持**双向**跳变，不能假设单调 |
| 地面距离 2166 km（仰角 1.7°）时 rdot | 已接近满值 −6913 m/s | 也就是说 **AOS 那一刻多普勒就是满的**，不是从 0 开始涨——这是最常见的心智错误 |

这组数与 `MainScreen.kt:432` 注释里写的 "145.99 MHz 在 AOS/LOS 两端差到 ±3.4 kHz"完全吻合（145.99e6×6920/2.998e8 = 3369 Hz），说明**我们 App 的多普勒量级是对的**。真正要核对的是"时间轴"：把 `MainScreen.kt:321` 那行 `doppler=...` 的调试图输出成 CSV，按上表逐行对斜距/仰角/rdot，就能定位是"公式错"还是"时间戳错"。

**建议加的自检（`verify/` 下新增一个 JVM 测试，或复用现有 python 自检）**：

```kotlin
// 伪代码：用我们自己的 Geo + Sgp4 复算，断言落在物理区间内
val f0 = 145.8e6
var peak = 0.0
val samples = (0 until 1200).map { i ->
    val t = aosS + i * 1.0
    val rr = Geo.rangeRate(sat.sgp4, sta, t)          // km/s，正=远离
    Triple(t, rr, -f0 * rr / Geo.C_KMS)               // dopplerHz，正=频率升高
}
for (i in 60 until samples.size - 60) {
    val dfPerMin = (samples[i + 60].third - samples[i].third)   // Hz/min
    if (abs(dfPerMin) > abs(peak)) peak = dfPerMin
}
check(abs(peak) in 3.0e3..4.0e3) { "多普勒变化率 $peak Hz/min 不在 ISS@145.8M 的物理区间" }
check(abs(Geo.rangeRate(sat.sgp4, sta, aosS)) in 6.5e0..7.2e0) { "AOS 处 rdot 应接近 7 km/s" }
```

### 3.3 转发器映射放在"卫星域"（`DopplerFrequencyCalculator.kt:45-56`）

```kotlin
// 1) 用户选的下行频率 → 去掉下行多普勒 → 卫星上的真实频率
// 2) 用转发器映射把"卫星收到的频率"换成"卫星发出的频率"（含倒置）
// 3) 再叠上行多普勒 → 地面该发的频率
```

`TransponderMapper.kt:24-44` 的核心三行：

```kotlin
val offset = txFreqHz - uplinkLow
return if (transponder.isInverted) downlinkHigh - offset else downlinkLow + offset
```

我们目前**不需要**，但如果要把 JAS-2 / FUNcube-1 / DOSAAF-85（我们 `Freqs.kt` 里已经列了这几颗 SSB/CW 星）做成"带内可调"，就必须按这个顺序：**先多普勒→再映射→再多普勒**。反例（常见错法）是"先映射再多普勒"，在 20 kHz 带宽的线性转发器上会引入与偏移成正比的误差。

### 3.4 只有一个状态量：`txBaseFreq`（`RadioTrackingService.kt:262-266`）

```kotlin
val txRadioFreq = txBaseFreq?.let { pos.getUplinkFreq(it) }
val rxBaseFreq  = if (txBaseFreq != null) {
    TransponderMapper.mapUplinkToDownlink(txBaseFreq, xpdr)
} else xpdr.downlinkLow
val rxRadioFreq = rxBaseFreq?.let { pos.getDownlinkFreq(it) }
```

好处：**用户只调一个量（发什么），收什么自动跟着走**，永远不会出现"上下行各自漂移、越差越远"。我们现在的等价物是 `Freqs` 表里的 `downMhz/upMhz` 一对常量（`Freqs.kt:30-40`），因为是固定频点中继，所以没暴露问题——但一旦支持带内调谐就必须收口成一个状态量。

### 3.5 "用户在拧旋钮"的检测与反算（`RadioTrackingService.kt:210-253`）——比"发射冻结"更好

Look4Sat **没有**"发射时冻结频率"这种防自激处理（它推的是绝对频率，电台自己不产生反馈）。它解决的是另一个问题：**用户手调之后，App 别每 1 s 把手调结果覆盖掉**。

```kotlin
// 检测（:244、:253）
if (read != null && abs(read.first - lastSetTxFreq) >= 20.0) {
    tuningRadio = "tx"; lastReadFreq = read.first; stableCount = 0
}

// 判定稳定（:214-216）：连续 2 次读数差 ≤20 Hz
if (abs(freq - lastReadFreq) <= 20) stableCount++ else { stableCount = 0; lastReadFreq = freq }
if (stableCount >= 2) {
    // 反算基准（:218 TX / :225-226 RX）
    val newBase   = (freq * SPEED_OF_LIGHT / (SPEED_OF_LIGHT + v)).toLong()   // TX
    val rxNominal = (freq * SPEED_OF_LIGHT / (SPEED_OF_LIGHT - v)).toLong()   // RX
    val newTxBase = TransponderMapper.mapDownlinkToUplink(rxNominal, xpdr)
}
```

要点：**20 Hz 阈值 > 我们 10 Hz 的量化步长**，所以量化噪声不会误判；**`stableCount >= 2` 避开按键弹跳**；反算用的是"当前多普勒量"，所以手调后的基准是"扣掉多普勒的卫星域频率"，下一轮推频继续从这个新基准走。

对我们的意义：这是解决"固件接管后旋钮被夺回"（用户体感"旋钮没反应"）的标准做法，但**前提是能回读 VFO 频率**。我们协议里已有状态回读通道（`RadioSession.kt:702` `fwBitsLine()`、`:803` `stateLine()`），需要固件在心跳帧里带上"当前 VFO 频率"字段——这是一次协议加字段（和 `项目交接-5.md:120` 说的"ppm 补偿同一档"）。

### 3.6 ppm：他们没有，但有一个可抄的"末端微调"形态

Look4Sat 的替代物是**绝对 Hz 偏移**（`model/Settings.kt:44`）：

```kotlin
// NetworkReporter.kt:75-79
val correctedFreq = frequency + clampedOffset
// model/Constants.kt:21-22
const val FREQ_OFFSET_MIN_HZ = -50_000L
const val FREQ_OFFSET_MAX_HZ =  50_000L
```

UI（`SettingsDialog.kt:479-540`）：一个数字输入框 + 一个"归零"按钮，字符串说明写得很直白（`strings.xml:255-256`）："Range: -50000 to 50000 Hz. Positive values increase reported frequency; negative values decrease it."

**能抄的**：过渡期在 App 上加一个"绝对 Hz 微调"（钳 ±50 kHz、一键归零、显示当前值），乘在 `MainScreen.kt:458-459` 的推帧值上——这样在固件 ppm 接通之前，操作员能手动把频差补掉，而且**不影响已有字段语义**：

```kotlin
// MainScreen.kt:458 附近
val trim = OffsetStore.load(ctx)          // Int，Hz，钳 ±50_000
rx10 = Math.round((rxHz + dop + trim) / 10.0)
tx10 = if (upHz > 0.0) Math.round((upHz - upDopp + trim) / 10.0) else 0L
```

**不能抄的**：它没有 ppm → 没有"按比例"的校正 → 145 MHz 和 435 MHz 上同一个 ppm 对应不同 Hz，绝对偏移只能对一个频段有效。所以这只是**应急**，真正的修法在固件里：

依据既有审计（`文档/固件核查-显示与刷新-2026-10-27.md` Q4），我们固件的断点在"算完频率直接写芯片"。两个候选改法（那篇文档已经写了，这里只做与 Look4Sat 的对照结论）：

1. **接到硬件微调通道**：`DPH_SaveCal()`（`App/app/doppler.c:371-392`）里紧跟 `clk_ppm = ppm;` 之后，把 ppm 折算成 `BK4819_REG_3B` 的步数写下去 —— 每步 ≈ 0.1923 ppm（`App/apps/cwdecode/cwdecode_app.c:60` 注释），一次校准全频段生效。
2. **接到频率链**：在 `DPH_Rx10WithOff` / `DPH_Tx10WithOff` 输出处乘 `(1 − ppm/1e6)`，用 `int64_t`（`文档/审查与修复清单.md:74` 已指出 int32 在 2147 s 以上会溢出）。

方案 1 更接近 Look4Sat 的"单一全局修正值"哲学（一个数管所有频点），方案 2 更贴近"我们已经在算的东西"。**建议 1**，因为它同时修好了"屏显 vs 空中载波"那条差（屏显是命令值，REG_3B 是载波基准）。

### 3.7 台站位置：存储、校验、UI 回显（我们的 bug c）

**Look4Sat 的三层结构（`core/data/.../repository/SettingsRepo.kt:224-249`）**：

```kotlin
// 存：逐字段字符串 + 精度收敛
putString(KEY_LAT, latitude.round(4).toString())
putString(KEY_LON, longitude.round(4).toString())
putString(KEY_ALT, altitude.round(1).toString())
putString(KEY_QTH, qthLocator)
putLong(KEY_TS, System.currentTimeMillis())
// 经度 >180 归一（:193）；positionToQth 失败则整次保存返回 false（:194）
```

**UI 回显（`feature/settings/.../SettingsScreen.kt:342-348`）** —— 这是直接治我们 bug c 的那一段：

```kotlin
Text(text = stringResource(R.string.prefs_lat_prefix, settings.stationPos.latitude))
Text(text = stringResource(R.string.prefs_lon_prefix, settings.stationPos.longitude))
Text(text = stringResource(R.string.prefs_qth_prefix, settings.stationPos.qthLocator))
Text(text = formatUpdateTime(settings.stationPos.timestamp))
```

**输入框初值（`SettingsDialog.kt:88-117`）**：

```kotlin
@Composable
fun PositionDialog(lat: Double, lon: Double, ...) {
    var latText by rememberSaveable { mutableStateOf(lat.toString()) }   // ← 从已存值初始化
    var lonText by rememberSaveable { mutableStateOf(lon.toString()) }
    ...
}
// 保存时钳位（:119-125）
val safeLat = latValue.coerceIn(-90.0, 90.0)
val safeLon = lonValue.coerceIn(-180.0, 180.0)
```

**我们该怎么改**（`Geo.kt:130-172` + 台站设置页所在的 `MainScreen.kt`）：

1. `StationStore.save()`（`Geo.kt:154-160`）：`putFloat` → `putString(..., value.toString())`；`load()`（`:146-151`）对应改成 `getString(...)?.toDoubleOrNull()`，避免 Float 在第 7 位有效数字上打滑。
2. 设置页加一行"当前台站"：直接读 `StationStore.load(ctx)`，`isUnset` 时显示"未设置（算不了过境）"，已设时显示 `"%.4f, %.4f, %.0f m"` + 保存时间戳 —— 对应它的 `SettingsScreen.kt:342-348`。
3. 输入对话框初值 = `StationStore.load()` 的值（不是空串、不是上次输入残留）。我们 `parse()`（`Geo.kt:163-171`）已经会给人话错误，保持；只补一个 `coerceIn` 兜底（它 `:119-125` 的做法）。

这三条做完，bug c（"输入框显示不出已存值、要手动点存"）在结构上就不可能再出现——因为**显示的值就是存储的值**，没有第二份草稿状态。

### 3.8 CAT 通信：节拍、ACK、断连（只抄语义）

FT-817（`Ft817Controller.kt`）：

```kotlin
private val commandDelayMs = 200L          // :42 每条命令后固定 200 ms
private val maxAckReadFailures = 3         // :43
// :166 ACK 读 1 字节，0x00 为成功
// :170-176 读异常累计 3 次 → isConnected = false
// :159-164 ack == -1 或流关闭 → 立即断开
// 全程 ioMutex.withLock 串行
```

IC-705（`Ic705Controller.kt:52-56`）：`ACK_TIMEOUT_MS = 500`、`POLL_INTERVAL_MS = 20`、`WRITE_SETTLE_MS = 50`；`drainWithTimeout`（`:302-320`）在 deadline 内每 20 ms 读一次缓冲，攒够 FD 结尾的帧就返回。

**没有自动重连**——只是把 `isConnected = false`，等 UI 再调 `connectRadios()`。

对我们：**我们的链路管理比它完善**（`RadioSession.tryReconnect()` `:273`、`shouldAutoReconnect()` `:586`、`probeAlive()` `:601`）。唯一值得移植的是"**3 次计数才判死**"这个去抖（我们已经是 3 帧，保持不变），以及"**写后静置再读**"的顺序（我们推帧后立刻读回包，若固件回包有延迟就会误判失败——建议照它 `WRITE_SETTLE_MS` 的思路，在 `RadioSession` 读线程里给"最后一帧发出后 xx ms 内不算超时"）。

### 3.9 TLE 刷新：48 h + 多源并发 + 全失败保留时间戳

`app/src/main/java/com/rtbishop/look4sat/MainApplication.kt:38-53`：

```kotlin
if (System.currentTimeMillis() - lastUpdate > 172_800_000L) {   // 48 h
    satelliteRepo.updateRepository()
}
satelliteRepo.initRepository()
```

`core/data/.../repository/DatabaseRepo.kt:66-101`：所有源 `async` 并发，逐个把 HTTP 状态码报给 UI；按 `epochDaynum` 取最新元素、名字取第一个有名字的源（`:141-158`）；**全部源失败时保留旧 `updateTimestamp`**（`:97-100`）。

第 3 点是最容易漏的：如果失败也把时间戳刷成"现在"，用户下次启动会以为刚更新过，48 h 内不再重试。我们如果做在线 TLE 刷新，务必照抄"**成功才写时间戳**"。

---

## 4 明确"不适用"的部分及原因

| Look4Sat 的做法 | 为什么我们不抄 |
|---|---|
| Kenwood/Yaesu/Icom 的文本 CAT 协议、Hamlib NET rigctl（`NetworkReporter.kt`） | 我们的执行机构是自研固件 + 二进制帧，没有 CAT 语义。抄帧格式毫无意义；抄"命令串行 + ACK 计数"的语义就够（§3.8） |
| 1 Hz 校正节拍（`delay(1000)`） | 它受限于 CAT 的 200 ms/命令；我们推帧 5 Hz、固件 1 Hz 写 PLL，且 10 Hz 量化下 ISS 峰值步进只有 59 Hz/s。改慢只会变差 |
| `frequencyOffsetHz` 绝对值偏移（±50 kHz） | 只作**应急**（§3.6）。理由：它是"一个数管所有频段"，在 145 MHz 和 435 MHz 上同一个 Hz 偏移对应的 ppm 差 3 倍，无法替代 ppm |
| QTH / Maidenhead 网格（`QthConverter.kt`） | 我们协议传 lat/lon/alt，多一套网格换算只会多一个精度损失源（它 `qthToPosition` 也只到格心 ±(1/48)°）。除非要做"网格口令"这种玩法 |
| 深空 SDP4（`DeepSpaceObject.kt`） | 我们只做 LEO。真要做（比如 AO-10 之类），建议直接引第三方库而不是移植，自研 1137 行 `Sgp4.kt` 的维护成本已经够高 |
| Compose 多模块架构（7 个 module、`core/domain/presentation` 分层） | 我们的 App 是 13 个文件的单体（`com.f4hwn.doppler`）。按它拆模块的收益在团队协作，不在功能 |
| 手动调谐检测（`>= 20 Hz` 判人拧） | **想抄但暂时抄不了**：需要固件在心跳里回报当前 VFO 频率。这是协议加字段的活（与 ppm 同一档），做完才谈得上 |
| "发射时冻结频率" | 它压根没有这个逻辑，也不需要一个（它推绝对频率）。我们如果要防"自激/回灌"，那是固件侧 PTT 期间抑制推帧的问题，Look4Sat 提供不了参考 |

---

## 5 落地清单（按"改哪一行、改成什么"排优先级）

1. **多普勒变化率自检**（治我们的 bug b）
   - 位置：`verify/` 下新增自检；或临时用 `_tmp_look4sat/doppler_rate_check.py` 的算法在 App 里跑一次导出 CSV
   - 断言：`|peak df/dt| @145.8 MHz ∈ [3.0, 4.0] kHz/min`；AOS 处 `|rdot| ∈ [6.5, 7.2] km/s`；TCA 前后 rdot 变号
   - 验收：把 `MainScreen.kt:321` 那行调试输出改成 CSV（`t,range,el,rdot,doppler`），逐行对本文件 §3.2 的表

2. **台站位置 UI 回显**（治 bug c）
   - `Geo.kt:154-160`：`putFloat`→`putString`；`:146-151` 读侧 `toDoubleOrNull`
   - 台站设置页加一行"当前：`%.4f, %.4f, %d m` + 更新时间"；输入框初值取自 `StationStore.load()`
   - 验收：冷启动 → 打开设置页 → 不点任何按钮，值就在那

3. **ppm 接通到载波**（治 bug a）
   - `App/app/doppler.c:371-392` `DPH_SaveCal()`：`clk_ppm = ppm;` 之后加 `BK4819_WriteRegister(BK4819_REG_3B, 22656 + (int32_t)(((int64_t)ppm * 10000) / 1923));`（每步 0.1923 ppm，见 `cwdecode_app.c:60`）
   - 验收：设 ppm=+10 → 145.99 MHz 上实测载波应低 ~1.46 kHz

4. **App 侧应急频偏**（ppm 生效前的过渡）
   - `MainScreen.kt:458-459`：`rx10 = Math.round((rxHz + dop + trim)/10.0)`，`trim` 钳 ±50 000 Hz，UI 给输入框 + 归零（照 `SettingsDialog.kt:479-540` 的形态）

5. **TLE 刷新"成功才写时间戳"**
   - 若做在线刷新：只有至少一个源成功才更新 `lastUpdate`，全失败保留旧值（照 `DatabaseRepo.kt:97-100`）

6. **协议加"当前 VFO 频率"回报**（为"用户手调"铺路）
   - 固件心跳帧加字段 → App `RadioSession.stateLine()`（`:803`）解析 → 照 `RadioTrackingService.kt:244` 的 20 Hz 阈值 + `:214-216` 的 2 次稳定判据做手调检测与基准反算

---

### 附：复算脚本

`_tmp_look4sat/doppler_rate_check.py`（临时目录，未提交；要复用请挪到 `verify/`）。核心是"**距离对时间中心差分**"而不是"速度向量点乘"——两者等价，但差分法不受坐标系旋转符号写错的影响，更适合当独立校验手段（我第一版就是栽在 GMST 旋转方向上了）。

---

## §6 发行版 APK 反查（2026-10-27，用户提供 `look4sat.apk`）

用户给了发行包，反查目的只有一个：**验证上面那些行号与结论确实属于装机的那一版**，
而不是只在某个分支上成立。

做法：`unzip classes.dex` → `dexdump -d`（build-tools 34.0.0）→ 全量反汇编 3.6 MB →
91 MB 文本，4137 个类。发行包被 R8 混淆（类名 `La00;`、`Lhi0;`），所以按名字找不到
`DopplerFrequencyCalculator`，只能按**字符串和常量**核对。

| 核对项 | 结果 |
|---|---|
| 包名 / 版本 | `com.rtbishop.look4sat`，`versionCode 449`，`versionName 4.4.9` —— 与分析的 tag `v4.4.9` 一致 |
| 目标 SDK | `compileSdk 37` / `targetSdk 37` / `minSdk 24` |
| 有没有 ppm | `grep -i ppm` 只命中一张 Unicode 兼容字符表（形近字合并用的长字符串），**没有 ppm 功能**，与源码结论一致 |
| 有没有绝对 Hz 偏移 | `frequencyOffset` 出现 3 次，与 `Constants.kt` 的 `frequencyOffsetHz` 一致 |
| TLE 源 | `celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=csv`（本机实测**连不上**，23 s 超时） |
| 另有接口 | `www.amsat.org/status/api/v1/catalog.php`、`reports.php` —— 这是 AMSAT 的"在轨状态报告"，不是 TLE 源 |
| 原生库 | 只有 `libandroidx.graphics.path.so`（Compose 的路径库），SGP4 全在 Kotlin 里 |

结论：**发行包就是分析的那一版**，§1–§5 的行号与结论可以直接当依据用。
另外两条副作用情报：
1. celestrak 的这个 gp.php 端点在本机同样不通（不是"老 URL 坏了"而是整站到不了），
   所以我们继续用 amsat nasobare 是对的，不必再找捷径。
2. 它申请了 `ACCESS_LOCAL_NETWORK`（Android 16 新权限）。我们 App 走
   `127.0.0.1:7878` 的网络桥在 targetSdk 34 上还不需要，但一旦哪天升 targetSdk，
   这条权限就得补上，否则桥会连不上。
