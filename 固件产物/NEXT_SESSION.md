# 明天接着干的存档（2026-10-24 02:16）

## 现状
- 固件：`f4hwn.sstv.fusion.bin` 115,784 B，md5 0416548c，Flash 95.09%，余 5,048 B
- 功能已跑通：F+0 进多普勒页 → 选过境 → M 开始 → 自动拉频率 → 播完还原
- 已刷进电台测试，用户反馈"初步成功"

## 源码位置
- 界面与逻辑：`_src/uvk1-v6.3.0/App/app/doppler.c`
- 表结构：`_src/uvk1-v6.3.0/App/app/doppler.h`
- 构建脚本：`_src/uvk1-v6.3.0/.gfw.py`（官方 arm-none-eabi-gcc 13.3.0）
- 工具链：`tc/root/mingw64/bin/arm-none-eabi-gcc.exe`
- PC 上传：`多普勒工具/doppler_upload.py`（sgp4 + pyserial，擦 0x162000 起 5 扇区写表）
- 预览渲染：`preview_true.py` —— **已在 2026-10-25 清理时误删，需要时重写**（解 App/font.c 真实字模出 PNG）

## 明天待办
1. 性能：DPH_Tune 每 100 ms 一次，可评估 50 ms / 自适应
2. UI：结束页(DONE)还没细调；空表页(EMPTY)只是占位
3. 配套软件：doppler_upload.py 现在要命令行参数，可做成有界面的
4. 体积只剩 5 KB，再加功能要先看 Flash 余量

## 已知约束（改代码前必读）
- `gFrameBuffer` 只有 7 行 × 8 像素 = y 0..55，任何绘制 y>55 都是越界写内存
- 屏幕 64 像素高 = 状态栏 1 行 + 帧缓冲 7 行；本页把状态栏换成一条细线
- 大字 `UI_PrintString(s, 0, 127, Line, 13)` 占两行，Line 最大 5
- 小字步进 7 像素，一行最多 18 字符

---

## 2026-10-25 工作区整理（相对路径都以工作区根目录为基准）
- 本文件与 6 个固件 bin、`table.bin`/`table2.bin`、`f4hwn_v630_notes.md`、`_fw/` 已收进 `固件改造/`；
  `_src/`（源码）、`tc/`（工具链）仍在根目录，上面的路径照旧可用。
- 多普勒/排版器相关（`doppler_*.py`、`sat_freq.py`、`sstv_doppler.py`、`layout_editor.html`、`启动多普勒工具.bat`、
  `打开排版器.bat`、`多普勒使用说明.md`、`sat_pics/`）已收进 `多普勒工具/`。桌面「多普勒过境表」快捷方式已同步指向新路径。
- 清理删掉的一次性文件：`_fix_*.py`、`preview_*.py/png`、`selftest.py`、`verify_*.py`、各种缓存 json、一次性 FastGithub 探测脚本等（共 75 项 / 61.2 MB）。