# K1 离线多普勒固件

在泉盛（QUANSHENG）K1 / UV-K5 的 **F4HWN v6.3.0** 基座上，加一个"过境表"页面：把一整趟过境的频偏曲线预先写进电台的外部 flash，电台按表每 10 ms 改一次 BK4819 频率字，**脱离手机也能跟完一趟过境**。

## 它解决什么

卫星过境时多普勒频偏能到 ±3 kHz 以上，手拧频率拧不过来。常见做法是手机算轨道、蓝牙/USB 实时喂频；一旦线掉了或手机没电，就跟丢。

这个方案把"算"提前到写表那一刻：PC 或手机算好整趟的偏移，写进电台 flash，电台自己跑。**表在电台里，手机可以关机。**

## 结构

| 部分 | 位置 | 说明 |
|---|---|---|
| 固件 | 外部仓库 | F4HWN v6.3.0（Fusion preset）+ DOPPLER 页，`doppler.c` 约 5,700 行 |
| 表区 | 电台外部 flash `0x162000..0x172000` | 64 KiB，格式 v5（proto 7），102 条目 + 10,240 样点 |
| PC 工具 | `PC工具/多普勒工具/` | PyQt 界面 + 过境计算 + 写表 + 版面编辑器 |
| 安卓 App | 独立仓库 `doppler-ng` | 同样的写表链 + 卫星目录 |
| 假电台仿真 | `tests/sim/` | 把 `doppler.c` 编到 PC，桩掉孔芯片，断言频合/功率/相位事件流 |

表区容量约 **170 趟过境**（按每趟 600 s / 6 s 一个样点算），一颗星一周、两颗星三到四天。

## 功能

| 功能 | 行为 |
|---|---|
| 离线跟踪 | 表写进电台后独立运行，不依赖手机 |
| 频偏补偿 | 样点存 int16 偏移（Hz），10 ms 一拍改频率字 |
| 侧键 2 改亚音 | 67.0 → 74.4 → 141.3 → 关 → 表值，循环 |
| 长按 M 武装 | AOS 前 60 s 自动进预听，到点自动进跟踪页 |
| 自定义标称频率 | 写表时除选转发器外，可手填下行/上行 |
| 版面可换 | 9 个页面坐标表由脚本从 JSON 生成，不手改头文件 |

## 编译

固件（工具链 `arm-none-eabi`）：

```bash
cd 固件源码/uvk1-v6.3.0
PYTHONIOENCODING=utf-8 python .gfw.py     # → .gobj/f4hwn.sstv.bin
```

PC 工具自检：

```bash
cd PC工具/多普勒工具
bash verify_all.sh --fast
```

仿真（不需要硬件）：

```bash
bash tests/sim/build.sh all
bash tests/sim/negctl.sh 9      # 阳性对照：删掉某条修复，看对应场景变红
```

## 数据链

卫星频点唯一真源是 `数据/sat_freq.json`，由 `tools/` 下的脚本生成 App 的 `freqs.json` 与 `transponders.json`。业余卫星白名单在 `数据/amateur_allow.json`（AMSAT 在轨列表判定），三处生成链都会过滤掉名单外的卫星。

改动任何一处数据后：

```bash
python tools/build_amateur_allow.py --check
python gen_sat_freq.py --check
python embed_transponders.py --check
```

## 致谢

- **F4HWN** —— 基座固件作者，本项目的起点
- **Look4Sat** —— 轨道计算与界面参考，见 [CREDITS.md](CREDITS.md)
- **AMSAT** —— 在轨业余卫星列表与频率
- **SatNOGS** —— 卫星频率数据库

## 许可

固件部分基于 F4HWN 的 GPL 授权发布，本仓库同样以 GPL-3.0 发布。第三方来源与引用方式见 [CREDITS.md](CREDITS.md)。
