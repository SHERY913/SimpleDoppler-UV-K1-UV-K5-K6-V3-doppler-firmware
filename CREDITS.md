# 致谢与来源

本项目的代码是自己写的。下面这些是它站着的地方。

## 固件基座
- **F4HWN** — UV-K5/K1 SSTV 固件 v6.3.0（Fusion preset）。DOPPLER 页是在其之上加的一层，
  基座代码未改动；编译宏与分区表见 `文档/项目说明.md`。
  上游：[F4HWN 仓库地址]

## 汉化与字库
- **白头佬（F4HWN CN 汉化）** — 中文字库与汉化包。外部 flash 表区选址避开了它的
  16×16 字库（0x180000）、8×8 字库（0x1C0000）与拼音检索表（0x1D0000）。

## 卫星数据
- **SatNOGS** — 频率与发射机目录（`数据/sat_freq.json` 的主要来源）
- **AMSAT** — 在役业余卫星名单与频率，用于白名单（`数据/amateur_allow.json`，222 颗）
- **CelesTrak** — TLE 轨道根数

## 算法
- **SGP4/SDP4** — Vallado 等《Revisiting Spacetrack Report #3》(2006)；实现基于 python-sgp4

## 参考实现（只读，未复制）
- **Look4Sat**（rt-bishop，GPL-3.0）— commit `a471379` / v4.4.9。读过它的过境搜索、
  AOS/LOS 判定与多普勒补偿的组织方式。本项目未包含其任何一行 Kotlin 代码。
  `文档/外部参考-Look4Sat.md` 只记录"哪个文件做了什么"。

## 工具
- **BK4819** 寄存器定义与驱动来自 F4HWN 基座
- **androguard** — 仅用于早期调研，产物未入库
