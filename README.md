
# UV-K1 UV-K5/K6 V3 离线/有线多普勒固件

基于F4HWN固件的一个自动多普勒偏移固件，用于业余卫星通联或UV段SSTV接收。本项目编程内容由AI编写，欢迎各位大佬批评指点！支持中英双语
<img width="1920" height="1013" alt="5" src="https://github.com/user-attachments/assets/7af596f6-f281-4aa7-af35-4a0ff32bbc14" />
<img width="3024" height="4032" alt="1" src="https://github.com/user-attachments/assets/f412515d-a44d-49e6-9d56-9a2db5a36156" />
<img width="3024" height="4032" alt="3" src="https://github.com/user-attachments/assets/414a0980-91f5-4b1c-98ad-fa7f93c22653" />
<img width="1280" height="1707" alt="4" src="https://github.com/user-attachments/assets/1e76cb4b-d3e4-47a4-9464-a85f94430252" />
<img width="3024" height="4032" alt="2 (1)" src="https://github.com/user-attachments/assets/ce359c17-816d-451d-9c26-6d8d858b6cc5" />
## 它解决什么

我们都知道，在卫星通联以及SSTV中多普勒效应会降低通联质量以及图像清晰度，传统的五信道方式精度只能说勉强够用。而社区中大部分方案都是通过蓝牙以及USB连接，实测下来，会对电台灵敏度产生一定的影响。于是我便想到提前把卫星的过境数据写入外部FLASH中，在卫星过境时播放频偏，这样便能尽量减少来自手机的传导干扰或者是蓝牙模块的射频干扰，同时实现多普勒频偏。

## 结构

<img width="2400" height="2850" alt="firmware-flowchart-EN" src="https://github.com/user-attachments/assets/1823309f-feef-4177-a289-3b6417e07537" />
<img width="2400" height="2850" alt="固件流程图-中文" src="https://github.com/user-attachments/assets/eb1bdf43-ed0c-4ba3-b01b-1e95353b265b" />

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
| 离线跟踪 | 表写进电台后独立运行，输入时间后自动计算剩余过境时间，到点自动开启频偏，不依赖手机 |
| 有线连接|，可通过C TO C（支持OTG）的方式连接手机，由手机接管电台频率|
| PRE与POST|在卫星上升至地平线前以及刚落下地平线时，因大气折射以及电磁波绕射，仍有一定通联窗口|
| 多转发器选择 | PC工具以及Android APP中均支持选择卫星多种转发器 |
| 内存不冲突 |写表空间不与F4多系统以及白头佬汉化文件重叠|
|64KB写表空间|一次写入几十次过境，支持不同卫星组合写入。（写表数量随不同过境数据而上下浮动）|

##按键映射<img width="2400" height="2340" alt="按键映射-中文" src="https://github.com/user-attachments/assets/9adf3072-63fc-4d37-80b1-fc8c42532d11" />
<img width="2400" height="2340" alt="keymap-EN" src="https://github.com/user-attachments/assets/ede32535-7894-490a-a194-33ec91e6fc6d" />
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

仿真电台：

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
本项目借鉴多款开源项目。站着巨人的肩膀上，让我一个编程小白也可以凭借AI工具编写出一个完成度较高的个人项目
- **F4HWN** —— 基座固件作者，本项目的起点
- **BG0FLP**—— 内测及想法提供
- **Look4Sat** —— 轨道计算与界面参考，见 [CREDITS.md](CREDITS.md)
- **AMSAT** —— 在轨业余卫星列表与频率
- **SatNOGS** —— 卫星频率数据库

## 许可

固件部分基于 F4HWN 的 GPL 授权发布，本仓库同样以 GPL-3.0 发布。第三方来源与引用方式见 [CREDITS.md](CREDITS.md)。
