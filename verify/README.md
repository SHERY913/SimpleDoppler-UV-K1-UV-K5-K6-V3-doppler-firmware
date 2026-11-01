# 安卓端验证记录（2026-10-25 17:47–17:51）

设备：MuMu 模拟器 Android 15.0（`127.0.0.1:16384`，1920×1080 横屏，density 280）
包：`com.f4hwn.doppler` · apk 15,688,645 B · md5 见交付目录

## 一、四页渲染（模块化改动后首次验证）

| 文件 | 页 | 结果 |
|---|---|---|
| `f_data.png` | 数据 | ISS / NORAD 25544 / 仰角 -41.2° / 方位 213.8° / 距离 9002.7 km / 速度 +1.34 km/s / 多普勒 -1961 Hz / 下行 437.5500 / 上行 145.9900 哑音 67.0 —— 全部有值，随秒实时变化 |
| `f_sky.png` | 指向 | 罗盘刻度、红色三角（手机朝向）、红色卫星方位短线、圆心白圈 + 红点、底部提示条、左下手机方位、右下手机俯仰，全部绘制正常 |
| `f_list.png` | 列表 | ISS 置顶高亮，其后 AO-07 / AO-73 / AO-91 / AO-95 / FO-29 / HO-113 / JO-97，NORAD 与频率两行对齐正常 |
| `n3_radio_page.png` | 电台 | 7 个按钮 + 状态行 + note 行，新增「测通知」在第二行第三列 |

## 二、通知链路（本轮新增）

1. **首次启动弹权限框** → `n1_perm_dialog.png`：Android 15 上正常弹出「要允许"多普勒"向您发送通知吗？」
   点允许后 `dumpsys package` 确认 `POST_NOTIFICATIONS: granted=true`。
2. **已授权发通知** → `n4_notif_sent.png`：点「测通知」，右上角横幅弹出
   「ISS 马上过境 · 现在 / 还有 45 秒，把天线举起来」；`dumpsys notification` 记录
   `pkg=com.f4hwn.doppler id=1001 importance=4 category=alarm channel=pass seen=true`，
   通道「过境提醒」`mImportance=4`。
3. **假过境时刻** → `n5_fake_aos.png`：
   `adb shell settings put global doppler_fake_aos 30` 后不点任何按钮，冷启动 6 秒内自动发
   「还有 30 秒」——说明真实过境触发那条路径（AOS 前 60 秒）逻辑可跑，只是以前没等到过境时刻。
   关掉：`adb shell settings delete global doppler_fake_aos`
4. **无权限降级** → `n6_noperm_button.png` + `n7_open_settings.png`：
   `pm revoke` 后按钮文案变「开通知权限」，点击直达系统
   `com.android.settings/.Settings$AppNotificationSettingsActivity`（多普勒的通知开关页）。
   且重装后**不会重复弹权限框**（`perm/asked_notif` 只问一次）。

## 三、复现命令

```bash
# 连接（必须设 TMP，否则 adb 写 adb.log 被拒）
export TMP="$PWD/.gtmp" TEMP="$TMP"
ADB="C:/Program Files/Netease/MuMu/nx_main/adb.exe"
"$ADB" connect 127.0.0.1:16384

# 起模拟器（若未启动）
"C:/Program Files/Netease/MuMu/nx_main/MuMuManager.exe" control -v 0 launch

# 构建 + 装
cd android-app && JAVA_HOME="C:\Users\13766\.jdk\jdk-21.0.2" ../tc/gradle-8.7/bin/gradle assembleDebug -q
"$ADB" -s 127.0.0.1:16384 install -r app/build/outputs/apk/debug/app-debug.apk

# 验证通知
"$ADB" -s 127.0.0.1:16384 shell settings put global doppler_fake_aos 30
"$ADB" -s 127.0.0.1:16384 shell dumpsys notification --noredact | grep com.f4hwn.doppler
```

## 四、解算层：SGP4/SDP4 对真值（2026-10-25 18:00）

`多普勒工具/verify_sgp4.sh` 用 python-sgp4 2.27 生成的真值逐点核对 Kotlin 实现：

```
43700  QO-100   deep=true   53 点  最大偏差 0.0000125 km
25544  ISS      deep=false  53 点  最大偏差 0.0000034 km
7530   AO-07    deep=false  53 点  最大偏差 0.0000026 km
43017  AO-91    deep=false  53 点  最大偏差 0.0000021 km
```

（t = 0 → 10080 min，即 7 天；`_sdp4_truth.tsv` 是校验数据，`CheckSgp4.kt` 是比对程序）

本轮修掉的真 bug：`Sgp4.kt` 里 J2 项用了 `(1/a)²`，参考实现是 `pinvsq = 1/(a(1-e²))²`。
偏心率为 e 的星会有 `(1-e²)²` 的系统性速率误差——AO-91（e=0.0144）7 天累积 2.5 km，
ISS（e=0.0007）只有 3 m，所以这个 bug 一直没被发现。修完四颗星全部进 2 cm。

同时补了 SDP4 深空分支（`dscom` / `dsinit` / `dspace` / `dpper`），周期 ≥225 min 的星
走这条路：QO-100 是 24h 同步轨道，之前的近地分支会静默算错位置。

端到端抽查（18:02:49，台站 43.83N/87.62E/800m）：

| 量 | App | 独立 Python | 差 |
|---|---|---|---|
| 仰角 | -46.4° | -46.4° | 0 |
| 方位 | 172.5° | 172.5° | 0 |
| 距离 | 9775.4 km | 9774.8 km | 0.6 km（≈0.7 s 时间对齐误差） |
| 速度 | +0.82 km/s | +0.82 km/s | 0 |
| 多普勒 | -1193 Hz | -1194 Hz | 1 Hz |

## 五、仍未验的（要真机）

- 录音音质（模拟器无麦克风）
- 真机 USB 写表全流程（`0x0760/62/64/66/68`，之前只走 TCP 桥验过）
- 通知在真机锁屏/免打扰下的表现
- 传感器（模拟器无磁力计/加速度计，指向页数字是假的）

## 六、2026-10-25 第二轮：审查修复后的复验

三份只读审查（Kotlin / Python / 固件 C）出的 62 条问题，修复台账在根目录 `审查与修复清单.md`。修复后的复验证据：

### 6.1 深空页（QO-100）

`j8_qo100_data.png`：QO-100 NORAD 43700，仰角 **+11.4°**、方位 **249.7°**、距离 **40430.0 km**、速度 **-0.00 km/s**、多普勒 **+0 Hz**、模式 FM。

手算复核：台站 43.83N/87.62E，QO-100 定点 26°E，Δlon = 61.6° →
`cos(el) 项 = cos(Δlon)·cos(lat) - 1/6.61 = 0.3429 - 0.1513 = 0.1916`，`el = atan(0.1916/0.9394) = +11.5°`，
`|sat-station| = 40421 km`。与 App 差 0.1° / 9 km（TLE 精度范围内），SDP4 深空分支正常。

注意：QO-100 在 Xinjiang 是**低仰角常年可见**（不是过境），所以 `PassCalc` 的"未来 24h 无过境"对它成立 —— 见清单待办第 16 条。

### 6.2 录音链路（此前完全没验过）

| 证据 | 说明 |
|---|---|
| `n1_perm_dialog.png` 里的 `RECORD_AUDIO` 弹框 | 修复后首次进页面会真的申请麦克风权限 |
| `dumpsys package` → `RECORD_AUDIO: granted=true` | 授权成功 |
| `rec_live.wav` | **录音进行中**用 `exec-out cat` 拉下来的文件头：`RIFF ffff ffff / WAVE / fmt 16 / PCM / mono / 48000 / 96000 B/s / data ffff ffff` —— 流式头生效，进程被杀也能播 |
| `rec_stopped.wav` | 停止后头被改成真实长度：48 kHz / 16 bit / mono / **38.0 s**（`wave` 模块可正常解析） |
| 旧版对照 | `rec_qo100.wav`（修复前被 force-stop 打断的文件）头是 44 个 0，`wave.open` 报 `file does not start with RIFF id` |

### 6.3 三层自检（修复后）

```
数据层  WARN(rc=2)  —— 只有上游停更 TLE 告警 + 死数据 P2
解算层  OK          —— QO-100 1.25e-5 km / ISS 3.4e-6 / AO-07 2.6e-6 / AO-91 2.1e-6
显示层  OK          —— 8 个 tick 全部 Δ=0.00000
```

## 七、控频仲裁（A 方案：接管期间固件退让，M 键失效）

### 7.1 判定表

| 场景 | 固件 | 谁写 PLL |
|---|---|---|
| 没插线 / App 没连 | 正常：短按 M 立刻跟、长按 M 武装倒计时 | 固件 |
| App 已接管（`0x0768` = 1） | 取消武装、`phase` 停 READY、不调频、不响 beep | App，每 200 ms 一次 |
| App 崩了 / 拔线 / 被冻结 | 15 s 收不到 `0x0602` → 自动交还，按当前偏移接着跟 | 固件（双 beep 提示） |
| 接管中按 M | 无效（A 方案） | — |

### 7.2 真实电台联调（COM7，2016 端口 = MuMu 的 adb reverse）

链路：App（MuMu 模拟器）→ `adb reverse tcp:7878` → `serial_bridge.py --port COM7 --listen 7878 --log` → 电台 USB CDC。

`verify/bridge.log` 里解出来的报文（掩码 XOR 已还原）：

| 方向 | 报文 | 含义 |
|---|---|---|
| App→电台 | `cmd=0x0766 plen=8 payload=42 26 00 00 90 07 01 00` | 对时：9794 天 / 67472 s（18:44:32）✅ |
| 电台→App | `cmd=0x0767` 13 字节 | 对时回包，电台**确实在回话** |
| App→电台 | `cmd=0x0768 plen=1 payload=01` | **接管控频** ✅ |
| App→电台 | `cmd=0x0602 reg=0x38 val=0x07DE` / `reg=0x39 val=0x029C` 交替 | 频率字 43 780 000（437.8 MHz，10 Hz 单位）✅ |
| App→电台 | `cmd=0x0768 plen=1 payload=00` | **交还固件** ✅（按钮 + 离开页面各发一次，重复无害） |

结论：**虚拟机里能连上**，不用等真机。前提是 `adb reverse tcp:7878 tcp:7878`。

踩到的坑：`setExtControl` 原来在 Compose 的 LaunchedEffect / 点击回调里调用，那是主线程，
网络模式下写 `java.net.Socket` 会抛 `NetworkOnMainThreadException`，被 `catch (_: Exception) {}`
吞掉——表面上"接管了"，日志里一个字节都没有。现在 `takeControl()` / `releaseControl()`
自己起线程并 join，保证 `disconnect()` 关句柄之前 14 个字节已经写完。

### 7.3 仍未验的（要刷固件上机）

固件侧的看门狗行为（15 s 自动交还）、READY/RUN 页的 `APP` 角标、交还时刻频率不跳变，
都只能刷了 `固件改造/f4hwn.sstv.fusion.bin`（96,304 B / md5 `d75ec1d8188651a9c237c3d8f0e75aec`）之后看。


## 8. 操控台（指向页接管态）+ 接管确认弹窗

### 8.1 交互

1. 连上电台（USB 或「网络模式」）→ **弹窗**「电台已连接 / 卫星 ISS / 下行 / 上行 /
   接管说明」，两个选择：`手机接管` 或 `只看不动`。接管不再是偷偷发生的。
2. 接管后切到「指向」页，那就是操控台：
   - 罗盘缩小上移，右上角出现**插头图标**（绿色实心 = 手机控频；灰色 = 固件自主）；
   - 罗盘下方三列实时数据：左列 下行/上行/亚音，中列 仰角/方位/距离，
     右列 多普勒（绿色）/变化率/剩余（过境中显示 `m:ss`，否则 `未过境`）；
   - 行首一行小字：「ISS · 手机在算多普勒，电台只负责发」+ 手机方位/俯仰。
3. 手机**不**提供发射和功率，PTT 和功率仍按电台上的键操作。

### 8.2 关键实现

- `CtrlState.kt`（新）：`takeover` / `note` 两个全局状态。原来 `liveTune` 只活在
  `RadioPage` 里，换页就丢；现在 `liveTune` 提到 `MainScreen`，用
  `LaunchedEffect(liveTune) { CtrlState.takeover = liveTune }` 镜像给其它页。
- `MainScreen.kt`：`PlugBadge()`（Canvas 画插头）、`CtrlRows()`（三列数据）、
  `SkyPage(..., takeover)`；接管时把 `hint/el/phoneAz/phoneEl` 四项从布局表里屏蔽，
  否则右上角的仰角正好压在插头图标上。
- `Live` 新增 `losIn`（距 LOS 秒数）、`inPass`：`tick%10` 那段扫描原来只找 AOS，
  现在记录"升起来过又掉下去"的时刻，作为 LOS 倒计时。
- 确认弹窗 `AlertDialog` 必须显式 `containerColor = Color(0xFF161B22)`：用 Material3
  默认浅色表面时，白色正文直接看不见（第一次截图就是这个效果）。
- 调试开关：`adb shell settings put global doppler_fake_ctrl 1` → 不插电台也能看操控台
  （截 `verify/q2_ctrl_sky.png` 用的就是它）；关掉 `settings delete global doppler_fake_ctrl`。
- 接管循环的 `finally` 里 `liveInfo = ""`：不然交还之后界面上还挂着
  「控频：手机接管 · 437.80127 MHz」，看着像还在控。

### 8.3 「测试跳动」——不依赖新固件也能证明手机能改电台频率

电台页新增 `btn:sweep`（`测试跳动`，坐标 [1182,440][1324,524]）：先要求停止控频，
然后在 **437.80000 / 437.81000 MHz** 之间每 1.2 s 跳一次、共 3 轮，最后回 437.80000。

桥日志实测（`verify/bridge.log`，掩码已还原）：

| reg | 取值 | 说明 |
|---|---|---|
| `0x0602 reg=0x38` | `1952` ↔ `2952` 交替 | 差 1000 个 10 Hz 单位 = 10 kHz |
| `0x0602 reg=0x39` | `668` 恒定 | 低位不变，符合 10 kHz 步进 |

也就是说 App 真的把两个不同频率字写进电台了。旧固件也认 `0x0602`，所以这一步**不需要刷机**
就能验——电台屏幕（或旁边那台手台）会跟着跳。这是判断"手机到底能不能控电台"的第一道硬证据。

截图：`verify/q4_confirm.png`（确认弹窗）、`verify/q6_sky_ctrl.png`（接管态操控台，
跨页仍保持接管）、`verify/q10_sweep_run.png`、`verify/q11_sweep.png`（测试跳动完成）。

apk：15,710,971 B，md5 `e378d4f40f9dd417defc2cb1e8dff8be`（`安卓App/F4HWN-Doppler.apk`）。

## 9. 固件补的三处「谁在控」提示（需刷机）

刷 `固件改造/f4hwn.sstv.fusion.bin`：96,420 B，md5 `a59f746c9e7c0ed6a9bf08ad3315ff1e`，
RAM 12,704 B / 16 KB = 77.54%，FLASH 95,532 B / 118 KB = 79.06%。

| 改动 | 位置 | 表现 |
|---|---|---|
| 接管响一声 | `DOPPLER_SetExtControl(true)`：`gBeepToPlay = BEEP_1KHZ_60MS_OPTIONAL` | 手机接管瞬间电台单声 beep，不用看屏幕就知道拿到了 |
| 交还响双声 | 原有的 watchdog / 主动交还路径 | 双 beep |
| `APP` 角标多留 3 秒 | 新增 `ext_shown_until = now + 3000` | 握手成功后即使链路立刻断，`APP` 也不会瞬间消失 |
| `LOCK` 闪 2 秒 | `KEY_MENU` 新增分支：`sub == 0 && ext_ctrl` → `ext_lock_until = now + 200`（10 ms tick），并单声 beep | 接管中按 M：屏幕 `$J` 位置显示 `LOCK`，不启动过境，键位不再像坏了 |
| `$J` 分支重写 | `case 'J'`：LOCK（2 s）> APP（接管中或角标期）> 空 | READY (126,21) / RUN (122,22) 两处右对齐位置 |

`$J` 的优先级用有符号差值比较（`(int32_t)(ext_lock_until - now) > 0`），
10 ms 计数器回绕后不会误判成"永远 LOCK"。

**验证前提**：必须刷这版。旧固件不认 `0x0768`，屏幕上不会出现 `APP`，按 M 也照旧启动——
用户看到的"电台完全不知道手机在控它"就是这个原因，不是设计问题。

## 十、固件自检：两边的状态终于能对账

**为什么加这个**：用户反馈"软件显示连接成功，电台那边一点接管迹象都没有"。查下来两边从来没对账——
App 只知道自己把 `0x0768` 发出去了，不知道固件认不认；`0x0768` 原来还是单向命令，固件连回包都不发。

**新增命令**

| 命令 | 方向 | 载荷 | 说明 |
|---|---|---|---|
| `0x0768` | App → 固件 | 1 字节（1 接管 / 0 交还） | 现在会回一帧 `0x0769`，载荷 1 字节 = 状态位 |
| `0x0769` | 固件 → App | 状态位 | `0x0768` 的回显 |
| `0x076A` | App → 固件 | 空 | 固件自检查询 |
| `0x076B` | 固件 → App | proto(1) + state(1) + phase(1) | 载荷**恒 3 字节**（App 侧 `expect=7` 是整帧长），别往里加字段 |

**proto 现在是 7**（`DOPPLER_Proto()`）。历史：2 = 只有接管；6 = 编进了第二套逻辑页；
7 = `0x0775` 改回"真实生效值 + 亚音自己的四位状态"。**7 与"有没有第二套页"无关**，
`DPH_EXT_PAGE=0` 编出来的版本直接是 2。旧固件对 `0x076A` 根本不回包——这本身就是分辨新旧的判据。

**状态位**（`DOPPLER_ExtState()`，八位全列，与代码逐位对齐）

| bit | 含义 |
|---|---|
| 0 | App 正在接管（`ext_ctrl`，**电台自己的状态**，App 对账只看这一位） |
| 1 | 正在过境：`phase==RUN`，或**接管中且手机自报 in-pass**（`ext_flags&0x02`）。后者是手机自己说的话的回声，不是电台的测量——要独立确认"在不在过境"，这一位给不了 |
| 2 | 过境表已加载 |
| 3 | 武装倒计时中 |
| 4 | 第二套逻辑页开（`ext_page_on`） |
| 5 | 本机 PTT 正被按下（`tx_hold`） |
| 6 | 芯片正在发射（`gCurrentFunction==FUNCTION_TRANSMIT`） |
| 7 | 手机正在刷写 flash/eeprom，这 6 秒里 PTT 是**故意**被吞掉的 |

**App 侧**：电台页新增「固件自检」按钮（`UiLayout.kt` 80%,24%），点一下问 `0x076A`，1.5 s 超时：

- 有回包 → `固件应答 proto 2 · 接管中 · 表已加载（phase 1）`
- 无回包 → `固件没应答：还是旧固件，或电台没开机 / 线不对`

**实测**（2026-10-25 19:52，连着真电台）：返回 `固件没应答：还是旧固件，或电台没开机 / 线不对` —— 证明当时电台里跑的还不是支持接管的那版固件，用户"看不到接管迹象"的直接原因就在这里。

## 十一、第二套逻辑：手机推全部数据，电台只做执行者

**用户定义**：第一套＝电台自己读表算多普勒；第二套＝手机实时算，电台只显示和执行。

| 项 | 实现 |
|---|---|
| 进入 | `0x0768=1` 或收到第一帧 `0x076C` 且 `phase ∈ {READY,RUN,DONE}` → `phase = DPH_EXT` |
| 界面 | `DPH_EXT` 直接 `DPH_DrawPage(1)`，与 RUN 页**同一份 layout**，像素一致 |
| 数据 | `$F $Y $E $G $H $D $P $L $C $J` 全部取手机帧；没收到帧显示 `--`，绝不回退固件值 |
| 频率 | RX 与 TX 都由固件从 `0x076C` 帧写：`DPH_RxShow()`/`DPH_TxStore()` 改 VFO 副本 + `BK4819_SetFrequency()` 点芯片。**App 不再用 `0x0602` 直写 REG_38/39**（5 Hz 怼芯片把 RSSI/功率回读全搞乱了；`RadioLink.setRxHz()` 已无调用点），`0x0602` 现在也被挡住不许写 `REG_3B` |
| 退出 | `0x0768=0` → `DPH_AudioRestore()` 还频还亚音 → **`phase = DPH_READY`（有表）或 `DPH_EMPTY`（无表）** → 页面若还开着则重存基线、装回表里的亚音与上行。~~走 73 页~~ 那条已经撤掉：73 页印的是过境表的 DUR/MAX EL/SW，与刚结束这次手机会话无关，实测跳出 `DUR 0:56 MAX EL:12` 属于张冠李戴 |

**0x076C 载荷**（小端）：`u32 rx10, u32 tx10, i16 dopp, u16 dist, i16 remain, i16 el01, i16 rate01, u8 progress, u8 flags, u8 nameLen, name[nameLen]` —— **定长部分 21 字节**，星名最多 **8** 个字符（固件 `ext_name[9]`、`nameLen>8` 截断；App 同样 `copyOf(8)`），所以整帧 21…29 字节。固件门槛是 `len < 21` 丢弃；App 仍补零到 28 以兼容只认 28 的旧固件。回包 `0x076D` 1 字节状态位。
（这行以前写"载荷 31 B"，是按名字段没截断的假想长度写的，三端谁照它实现都对不上。）

**防呆（回还控制权的三条）**：① 串口掉线 `SerialConfigInProgress()==0`，≤6 s；② **5 s 帧静默**（`0x076C` 断流）；③ 心跳（`0x0602` 写寄存器）只在"最近 5 s 内真收到过数据帧"时才续命。以前还有的一条"15 s 总超时"是死代码，同一拍里 5 s 那条先把它干掉了，已连常量一起删除——**别再新增第二个时长常量**。交还动作本身**全程不写 Flash**（拔线时掉电不会留下半截数据）；正在发射时先 `APP_EndTransmission()` 紧跟 `APP_HandleEndTransmission()` 放掉载波，页面不动（否则操作员只会得出"一发射就断线"）。

**20:06 实测桥日志**：下行 `0x0602 ×2974 / 0x0766 ×8 / 0x0768 ×18 / 0x076A ×3 / 0x076C ×25`，上行 `0x0767 ×8 / 0x0769 ×8 / 0x076B ×2` —— 说明电台当时跑的是 19:50 那版（认 0x0768/0x076A），`0x076C` 无回包是因为 20:04 这版还没刷。

## 十二、接管期间的上行亚音：`0x0774` → `0x0775`

`0x076C` 那一帧里没有亚音字段，所以接管期间按 PTT 出去的亚音一直是固件里残留的那一个
（上一颗星的 67.0 Hz，或者干脆 OFF）——有亚音的星（SO-50 / AO-27 这类）根本不开门。
`0x0774` 就是让 App 把它选中的那颗星的亚音显式写进来（10 Hz 单位，0 = 明确关掉）。

| 命令 | 方向 | 载荷 | 说明 |
|---|---|---|---|
| `0x0774` | App → 固件 | `u16 tone10` | 上行亚音，10 Hz 单位，0 = 关掉 |
| `0x0775` | 固件 → App | `u16 Tone10 + u8 State` | `0x0774` 的回执，固件先回 `0x076D` 那一套里的 `gSerialConfigCountDown`，再发这一帧 |

**`Tone10` 是"真正装进发射配置"的那颗亚音，不是 App 发来的请求值。**
固件是从 `FREQ_Config_t`（radio 发射时用的那份）反查出来的，所以它与射频状态不可能走散。

**`State` 位定义**（代码里只在 `App/app/doppler.h` 的 `DOPPLER_TONE_ST_*` 定义一次）

| bit | 掩码 | 含义 |
|---|---|---|
| 0 | `0x01` | 值被接受（在 `CTCSS_Options[]` 表内，或 `tone10 == 0` 明确关掉） |
| 1 | `0x02` | 已立即生效（接管中且 `radio_saved == true`，已写进发射配置） |
| 2 | `0x04` | 值被拒（不在表内 —— 固件按 OFF 出去，别当它设上了） |
| 3 | `0x08` | 暂存待接管（`radio_saved == false`，等接管动作发生时才装） |

`bit1` 与 `bit3` 互斥（接管了就立即生效，没接管就只是暂存）；`bit0` 与 `bit2` 互斥。

**为什么要有这四位**：改之前 `0x0775` 把请求值原样抄回去、`State` 回的是 `0x076A`
那套整机状态位，于是"值不在表里，固件悄悄按 OFF 出去"这件事在协议上根本表达不出来，
App 只能把请求值当已生效值显示。现在 App 侧按 `bit2` 就能提示"这个亚音电台不支持，
换一个"，按 `bit3` 就能显示"已记住，开始接管后生效"。

**对账办法**：发 `0x0774`，看 `0x0775`。

| 场景 | 期望回包 |
|---|---|
| 发 `670`（67.0 Hz，在表内），已接管 | `Tone10 = 670`，`State = 0x03` |
| 发 `670`，还没接管 | `State = 0x09`；`Tone10` 是**此刻**发射配置里本来就有的那颗（可能是操作员自己信道的亚音，也可能是 0），不是你刚发的 670 |
| 发 `0`（关掉），已接管 | `Tone10 = 0`，`State = 0x03` |
| 发 `0`（关掉），还没接管 | `State = 0x09`；`Tone10` 同上一行——没接管时固件不动发射配置，回的是**信道自己原来带的音**，不是 0 |
| 发 `1230`（表里没有） | `Tone10 = 0`，`State` 带 `0x04` |

**App 怎么读这张表**：proto 7 起必须**先看 `State` 四位**，不能拿"回显不等于请求"当被拒——
上表第 2、4 行就是合法请求但回显不等（`0x09` 暂存）。App 0.8 及以前正是把回显放在第一位，
于是把"已记住、接管后生效"报成"这颗星的亚音电台不支持"；0.9 改回先听 `State`。

## 十三、帧校验（CRC）与 `0x0528` 的单位

**回包 CRC 现在是真的。** 帧格式 `AB CD Size(2) | 掩码(内层头+载荷) | 掩码(CRC-16 两字节) | DC BA`，
CRC 是 CCITT-FALSE 去掉反射、**初值 0、多项式 0x1021、MSB-first、无最终异或**，算在**去掩码前的
内层头+载荷**上（也就是 `Size` 那一段），三端同一把尺：固件 `driver/crc.c`、App
`RadioLink.crc16`、PC `radio_proto.crc16`。

历史上固件**只在校验请求**，回包那两字节恒填 `0xFFFF`；App 为了兼容写成"`0xFFFF` 才跳过校验"。
两边各自都算合理，合起来的实际效果是**电台说的话被听错了没有任何一层能发现**——而 5 Hz 推帧、
发射时 USB 与 BK4819 抢总线，掉的恰好是这条方向。现在 `SendReply`/`SendReply_VCP` 真算，
App 那个 `0xFFFF` 跳过分支**保留**（接老固件仍需能收），但它不再是唯一路径。
守住这件事的是 `verify_crc.py`（已进 `verify_all.sh` 第 2 层）：它会验"改坏 1 bit 必须被丢"，
所以谁把固件改回恒 `0xFFFF`，那一层立刻红。

**`0x0527 → 0x0528` 的 RSSI 是裸寄存器值，不是 dBm。** 它回 `REG_67 & 0x1FF`（0…511），
没有 `(x/2 − 160)` 换算、没有 `dBmCorrTable[band]` 频段修正、也没有 −125/−53 钳位。
屏幕（主界面与过境页 `$D`）用的是换算+修正+钳位+8 次平均之后的值。**两者不同尺是故意的**：
`0x0528` 是上游原厂命令，原厂 PC 工具按裸寄存器解释，擅自改成 dBm 会砸了别人的工具。
要自己换算成屏幕口径：`dBm = REG_67/2 − 160 + dBmCorrTable[band]`（表在 `App/misc.c`，
`{-15,-16,-10,-4,-7,-6,-1}`），再钳 [`RADIO_DBM_FLOOR`, `RADIO_DBM_CEIL`] = **[-139, -53]**。`hw_rssi_probe.py` 就是这么算的。
（下限以前是 -125，实测这台机器静默本底加完频段修正是 -129/-132，地板压在本底之上 → 没信号时整个弱信号区被夹成一个字符串，看着就是"表坏了"。）

## 十四、受控清地：`0x0776` → `0x0777`

表区 `0x162000..0x172000` 在原厂机器上是 A-law 语音包的中段，固件 `DPH_TableErase`
判 `DPH_SECTOR_FOREIGN` 就返回 `DPH_TBL_OCCUPIED`(4) —— 这是设计，不是 bug。要让
"主人决定在这里装表"这件事能被表达，加一条**一次性解锁**：

| 命令 | 方向 | 载荷 | 说明 |
|---|---|---|---|
| `0x0776` | PC/App → 固件 | `u32 Token` | 令牌 `0x31555044`（`DPU1` 小端） |
| `0x0777` | 固件 → PC/App | `u32 Token + u8 Credits` | 原样回显令牌 + 剩余擦除额度 |

- 令牌对 → 借出 **64** 次擦除额度（表区 16 扇区，4 倍余量）；令牌错或帧长 < 4 → `Credits = 0`。
- **额度在 RAM 里，掉电清零**：每次上电都要重新解锁，不存在"开一次永久畅通"。
- 额度只在闸门判 `foreign` 时扣一次；扣到 0 立刻回到"一律拒绝"。
- 它不是安全边界（能发 VCP 帧的人本来就能擦 flash），挡的是误帧、串口噪声和
  "忘了自己发过什么"的旧工具。

**对账办法**：先发一个错令牌，`Credits` 必须是 `0`；再发 `0x31555044`，`Credits` 必须是 `64`。
`verify_link_ask.py` 用假电台把这两条都测了，且它快照的是**构造时的** `UNLOCK_TOKEN`
—— 否则改模块常量会把"固件认的值"一起改掉，永远测不出错令牌。
