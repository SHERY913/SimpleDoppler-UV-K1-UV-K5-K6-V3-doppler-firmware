# 交付物指纹

APK 和 bin 都是可再生产物，`.gitignore` 故意把它们排除在库外——所以版本化的是本文件，
不是它们本身。每次替换交付件都要在这里留下对应的源码 commit。

| 文件 | 大小 | md5 | 构建自 |
|---|---|---|---|
| `多普勒App-debug.apk` | 15,990,362 | `7a47e8d6f504c6179b5de923e5deacfe` | `安卓App` HEAD `127b1bb`，工作树干净 |
| `f4hwn.sstv.doppler.bin` | 101,040 | `73344cd1ee1583d011974baf4654d7b2` | `固件源码/uvk1-v6.3.0` HEAD `13a0f65` |
| `F4HWN-Doppler.apk` | 15,670,767 | `68c2c1bd7d63b12dffabed58a70c4fab` | 2026-10-25 15:25 改名前的旧发布包，**不等于当前源码**，仅留作追溯 |

## APK 不能按字节复现，别拿 md5 判"同代"

10-27 16:42 那份是 `62525358258067c5bb816ef9ad6425a1` / 15,783,147 B，被 17:0x 这次重建
取代（只改了"断链 15 s"这句文案和三处注释）。两包大小差 20 万字节，但 zip 条目级比对
结果是：**140 个条目一个不多一个不少，assets 逐字节相同，只有 3 个 `classes*.dex`
内容不同，而未压缩总量只差 1104 字节** —— 差别是 D8 把那个 9.1 MB 的 dex 从 `classes4`
挪到了 `classes5`（分包编号换了位置），压缩后大小因此抖动。

结论：判"交付包等于源码同代"要用**条目 + CRC**，md5 只能用来确认"是不是同一个文件"。
固件的 `.bin` 没这个问题，所以它是逐字节比对的（见下）。

## 本轮（2026-10-27 下午）验证记录

- App：`gradle --offline assembleDebug` 从干净工作树重建，与 10-27 01:54 的旧交付包对比，
  140 个 zip 条目中 3 个 dex 内容不同 → 旧包确实不含本轮修复。旧包已移到
  `历史-20261027/多普勒App-debug-20261027-0154.apk`（它从未进过库，直接覆盖就找不回来了）。
  新包已装到 MuMu 并跑通显示层自检（仰角/方位/距离/速度/多普勒偏差全部 0.00000）。
- 固件：13a0f65（删掉 1500 tick 死常量 + 两处过期注释）之后重编一次，新构建的
  `.gobj/f4hwn.sstv.bin` 与本目录 bin 逐字节只差 3 个，位置 `0x18591`–`0x18597`，
  全在构建时间戳字符串里（`13:45:39` → `17:35:34`），size 同为 101040。
  也就是说那次改动对产物是惰性的，而现在本目录 bin 里嵌的时间戳确实出自 13a0f65。
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
