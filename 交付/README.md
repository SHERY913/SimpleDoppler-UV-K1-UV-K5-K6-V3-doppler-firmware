# 交付物指纹

APK 和 bin 都是可再生产物，`.gitignore` 故意把它们排除在库外——所以版本化的是本文件，
不是它们本身。每次替换交付件都要在这里留下对应的源码 commit。

| 文件 | 大小 | md5 | 构建自 |
|---|---|---|---|
| `多普勒App-debug.apk` | 15,783,147 | `62525358258067c5bb816ef9ad6425a1` | `安卓App` HEAD `e3cb862`，工作树干净 |
| `f4hwn.sstv.doppler.bin` | 101,040 | `2536d838ae8e2ec0e18143a8c403983f` | `固件源码/uvk1-v6.3.0` HEAD `737ed3a` |
| `F4HWN-Doppler.apk` | 15,670,767 | `68c2c1bd7d63b12dffabed58a70c4fab` | 2026-10-25 15:25 改名前的旧发布包，**不等于当前源码**，仅留作追溯 |

## 本轮（2026-10-27 16:42）验证记录

- App：`gradle --offline assembleDebug` 从干净工作树重建，与 10-27 01:54 的旧交付包对比，
  140 个 zip 条目中 3 个 dex 内容不同 → 旧包确实不含本轮修复。旧包已移到
  `历史-20261027/多普勒App-debug-20261027-0154.apk`（它从未进过库，直接覆盖就找不回来了）。
  新包已装到 MuMu 并跑通显示层自检（仰角/方位/距离/速度/多普勒偏差全部 0.00000）。
- 固件：新构建的 `.gobj/f4hwn.sstv.bin` 与本目录 bin 逐字节比对，101040 字节里只差 4 个，
  位置 `0x18591`–`0x18597`，内容是构建时间戳 `13:45:39` → `16:25:28`。其余逐字节相同。
- 包内版本号仍是 `versionName 0.1 / versionCode 1`，线上包无法追溯——见交接文档第五节第 5 条。

## 重建命令

```bash
# App（JDK 与 gradle 都在 tc 下，路径硬编码不可挪）
export JAVA_HOME=C:/Users/13766/.jdk/jdk-21.0.2
cd 安卓App/android-app
../../tc/gradle-8.7/bin/gradle --offline assembleDebug
#   → app/build/outputs/apk/debug/app-debug.apk

# 固件（只能在 default-workspace 根下写文件，见交接文档铁律 2）
cd 固件源码/uvk1-v6.3.0 && python .gfw.py
#   → .gobj/f4hwn.sstv.bin
```
