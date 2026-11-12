# 宿主端假电台（tests/sim）

把 `固件源码/uvk1-v6.3.0/App/app/doppler.c` **原样**编到 PC 上跑：硬件全部打桩成
事件记录器，虚拟时钟把整趟过境压缩到毫秒级，断言口径只有一条 —— **芯片被写成了什么**。

看屏幕字符串不算通过条件。屏幕永远是对的，芯片才是错的（交接-26 的三条 P0 全是这样）。

## 一条命令

```bash
# 从仓库根（多普勒/）跑：
bash tests/sim/build.sh            # = m0，装表
bash tests/sim/build.sh s1         # 预听页 PTT 松手（交接-26 ①）
bash tests/sim/build.sh s2         # 就绪页 PTT 松手（交接-26 ②）
bash tests/sim/build.sh s3         # 进页顶功率、退页还原（交接-26 ③）
bash tests/sim/build.sh s4         # 正面用例：整趟 600 s 过境 + 中途 PTT 30 s
bash tests/sim/build.sh s5         # 侧键一起发（E6/E7）
bash tests/sim/build.sh s6         # 纯接收星上侧键二（E8）
bash tests/sim/build.sh s7         # ▲▼ 改步进（E5/方向/钳位）
bash tests/sim/build.sh s8         # 预听·尾音页 TX 频率（E4）
bash tests/sim/build.sh s9         # 校准页写 ppm + 断电回读（B4/CRC）
bash tests/sim/build.sh s10        # 音调键控：亚音必须早于 PA 落地（TONE-KEYUP）
bash tests/sim/build.sh s11        # 长按 M 武装：到点自己进 RUN；武装不了必须双响拒绝（AUTO-ARM）
bash tests/sim/build.sh all        # 两个静态守卫 + s1..s11 全跑（s11 会把时钟置真，必须排最后）
bash tests/sim/build.sh script 脚本 [表]     # 逐行跑按键脚本，只打事件流（按键名必须写 KEY_0…KEY_9）
bash tests/sim/negctl.sh 1..20     # 阳性对照：在副本上破坏一条修复，场景应变红（19/20 守 AUTO-ARM）
```

宿主 C 编译器用 `zig cc`（本机没有 gcc/clang/MSVC）。zig 路径先读 `/tmp/zigpath`，
没有就用 `python -c "import ziglang"` 定位；也可 `ZIG=/path/to/zig bash tests/sim/build.sh`。

## 目录

| 路径 | 干什么 |
|---|---|
| `build.sh` | 编译 doppler.c + 桩 + 场景，跑一个场景 |
| `mktable.py` | 造一张 v5 合规表到 `data/table_sim.bin`（65,536 B，0x162000..0x172000） |
| `negctl.sh` | 阳性对照：复制一份 doppler.c 到 `out/neg/` 打补丁，用 `DOPPLER_SRC` 编它 |
| `include/sim.h` | 事件枚举、虚拟时钟、flash/芯片模型、断言查询 API |
| `include/scheduler.h` | shim：`SCHEDULER_*`（真头引 MCU） |
| `include/driver/gpio.h` | shim：`GPIO_PIN_*` + 函数声明 |
| `include/apps/app_overlay.h` | shim：只留 `APP_REGION_*` / `APP_SLOT_*` |
| `include/audio.h` | 真实 `App/audio.h` 的整份副本（它 include `driver/gpio.h`，必须同级才解析到 shim） |
| `src/sim_hw.c` | 46 个未定义符号的桩：BK4819 / PY25Q16 / RADIO / GUI / ST7565 / UI / CRC / FREQUENCY … |
| `src/sim_main.c` | 场景运行器 + 按键脚本解释器 + 断言 |

其余头文件（doppler.h / settings.h / radio.h / frequencies.h / misc.h / dcs.h …）全部
用上游真头，`-I tests/sim/include` 只抢在 `-I App` 前面顶掉上面那 4 个。

**固件源码零改动**：`git -C 固件源码/uvk1-v6.3.0 diff --stat -- App/app/doppler.c`
至今仍是交接-26 留下的 `49 insertions(+), 11 deletions(-)`。

## 事件流

```
t=    680ms  TX_FREQ   14599320
t=    780ms  RADIO     0  (1)
```

| 事件 | 含义 |
|---|---|
| `RX_FREQ a` | `BK4819_SetFrequency` 且当时没有发射意图 |
| `TX_FREQ a` | 同上，但有发射意图（`sim_ptt_intent`） |
| `CTCSS a` / `CTCSS_OFF` | `BK4819_SetCTCSSFrequency` / `BK4819_ExitSubAu` |
| `SQUELCH a` | `BK4819_SetupSquelch` 的门限 |
| `POWER a (b)` | `RADIO_ConfigureSquelchAndOutputPower`，a = OUTPUT_POWER_*，b = 该 VFO 频率 |
| `FRONTEND a` | `BK4819_PickRXFilterPathBasedOnFrequency`，a = UHF(1)/VHF(0) |
| `AUDIO a` | 音频通路开/关 |
| `DISPLAY a` | `GUI_SelectNextDisplay` |
| `FLASH a b` | `PY25Q16_*` 读写，a = 地址，b = 长度 |
| `RADIO a` | `GENERIC_Key_PTT` / `APP_EndTransmission`，a = 1 起发 / 0 收摊 |

RX/TX 的分流**只看 `sim_ptt_intent`，不看 `gCurrentFunction`**：`DPH_PttEvent` 里
先写频点（`doppler.c:3312`）后调 `GENERIC_Key_PTT`（`:3318`），松手时也先把芯片调回
收频点（`:3344`）再切 FUNCTION_RECEIVE。按 gCurrentFunction 判会把收频写成发射频点。

## 已仿真、必须仿真、但属于 app.c 的东西

`doppler.c:3040` 在 `gScreenToDisplay != DISPLAY_DOPPLER` 时会 `DPH_Leave(); return;`，
所以 tick 必须按 `App/app/app.c:1917` 的门来调：

```c
if (gScreenToDisplay == DISPLAY_DOPPLER || DOPPLER_IsActive() || DOPPLER_ExtActive())
    DOPPLER_Tick10ms();
```

外加 `app.c:2963` 那条"页在活动、却请求回主界面 → 改回多普勒页"。少了这两句，
退页后每拍都会再进一次 `DPH_Leave()`（现象：刷出 7 条重复 DISPLAY）。这是**仿真缺陷**，
不是固件缺陷 —— 真机 app.c 替你挡了。

## 按键派发三档

| 函数 | 派发顺序 | 用途 |
|---|---|---|
| `key_tap` | `ProcessKeys(k,true,false)` → 30 ms → `(…,false,false)` | 真机短按（app.c:1869 的去抖档） |
| `key_hold` | 同上 → 20 ms → `(k,true,true)` → 剩余 → 抬起 | 真机长按（app.c:1884 的重复档） |
| `key_held_only` | 只发 `(k,true,true)` | **唯一一处绕过点**，用来把 doppler.c 摆进它自己的长按分支 |

`key_held_only` 存在的原因：真机顺序下预听页（`DPH_PRE`）根本进不去 ——
短按 MENU 在 `doppler.c:3800` 就直接 `DPH_BeginRun()`，等到 400 ms 的 held 事件到达时
phase 已经是 `DPH_RUN`，`doppler.c:3555` 那个 `phase == DPH_READY || phase == DPH_PRE`
的长按分支不再成立。`s1h` 场景专门复现这一条。

## 表镜像

`data/table_sim.bin` 是 `mktable.py` 生成的，不是真机 dump：

- 头 `HDR_FMT='<4sBBHII'`（16 B），条目 `'<16sIIIHHHHHBB'`（40 B），样点 `'<hBHb'`（6 B）
- `data_off` 必须是 `0x163000`（= `DPH_BASE_ADDR` + `DPH_SAMPLE_OFF`），否则
  `doppler.c:1246` 判 `dph_tbl_err = 5`（BAD LAYOUT）
- 条目 0 `SIM-PASS-A`：145.80000 下行 / 145.99000 上行 / 600 s / 120 点 / 5000 ms 一点 /
  亚音 74.4 Hz，偏移走 -3200 → +3150 → -3200 Hz 的抛物线
- 条目 1 `SIM-PASS-B`：145.85000，无上行

真机的 `PC工具/多普勒工具/table_backup.bin` 是 0x165000 版式的旧表，现固件拒收；
`交付/f4hwn.sstv.doppler.bin` 是固件镜像（106,480 B），里面没有表区，都不能直接喂。

## 脚本语法

```
KEY_MENU            # 短按
KEY_MENU HOLD 600   # 真机顺序长按 600 ms
KEY_UP / KEY_DOWN / KEY_EXIT / KEY_0..9 / KEY_F / KEY_STAR
KEY_SIDE1 [HOLD 800] / KEY_SIDE2
PTT_ON / PTT_OFF
ADVANCE 200         # 推进虚拟时钟（每 10 ms 一拍，每拍调一次 DOPPLER_Tick10ms）
```
