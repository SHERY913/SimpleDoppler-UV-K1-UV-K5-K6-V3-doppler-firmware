> 产品名：**简单多普勒**（PC 工具与安卓 App 同一套叫法）。本文下面引用的 2026-11-01
> 那份旧日志/旧窗口标题是当时的实测原文，保留不改 —— 改了就等于改历史记录。

# 打一个能发给别人的单文件 exe

源码版要对方自己装 Python + numpy/sgp4/pyserial，`启动多普勒工具.bat` 只检查不安装。
要发给不折腾的ham，就打这个包。

## 命令（在本目录下执行）

```bash
PICS="C:/Users/13766/Documents/deepseek-harness/default-workspace/多普勒/PC工具/多普勒工具/sat_pics"
OUT="…/临时/exe-build"          # 产物目录，别落在仓库里
python -m PyInstaller --noconfirm --clean --onefile --windowed \
    --name F4HWN-Doppler-Tool \
    --add-data "$PICS;sat_pics" \
    --hidden-import sgp4.api --hidden-import serial \
    --hidden-import serial.tools.list_ports \
    --distpath "$OUT/dist" --workpath "$OUT/build" --specpath "$OUT" \
    doppler_tool.py
```

产物约 34 MB（含 Pillow 与 numpy）。**exe 不进库**，只作为交付件放桌面。

## 两个坑，都踩过

1. **`--add-data` 的源路径必须是 Windows 绝对路径。** 写成 `sat_pics;sat_pics`
   会按 `--specpath` 而不是当前目录解析，报 `Unable to find …\exe-build\sat_pics`；
   用 Git Bash 的 `$PWD` 更糟，会变成 `C:\c\Users\…`。
2. **在本机沙箱里，从工作区内直接双击/启动会静默失败。** onefile 要先解包到
   临时目录，得把 `TMP`/`TEMP` 指到工作区外再跑：
   `cmd //c "set TMP=C:\F4HWN\tmp&& set TEMP=C:\F4HWN\tmp&& start F4HWN-Doppler-Tool.exe"`
   对方在自己机器上双击没有这一层，不用管。

## 源码侧为打包做的两处改动（不是可选的）

- **路径分家**（`doppler_tool.py` 顶部）：`RES_DIR = sys._MEIPASS`（只读资源，
  星图随包走），`DATA_DIR` 可写（配置、TLE 缓存、日志），exe 模式落到
  `%LOCALAPPDATA%\F4HWN-Doppler-Tool`。原来 `CONFIG`/`tle_cache.json`/`logs`
  全写 `os.path.dirname(__file__)` —— 打包后那是**每次随机命名、退出即删**的
  临时目录，等于每次开机失忆。源码运行两者都还是脚本目录，行为不变。
- **启动与崩溃落日志**（文件末尾）：窗口程序起不来时屏幕上什么都不留，
  "双击没反应"分不清是没启动、崩了、还是被杀软拦。现在先写一行
  `start … 打包=True · py=… · 数据目录=…`，再用 `try` 把 traceback 落到同一个文件。
  **日志里没有 start 那一行 = 进程从没起来过**，这是远程定位的唯一依据。

## 顺带修掉的一个真 bug

`load_cfg` 的默认台站原来写死 `43.83 / 87.62`（乌鲁木齐），`compute_next` 里还有
第二份同样的字面量。别人装上这个 exe，所有过境、仰角、多普勒样点都按别人的城市算，
还能把这张错表写进他的电台，而界面一路报成功。App 侧同一个 bug 早修过
（`Geo.StationStore` 用 NaN 表示未设，`verify_layout.py` 还有一条禁止硬编码坐标的检查），
只是那条检查只管 App。现在 PC 工具默认空、未填时挡住并明说，与 App 同口径。

## 验到哪一步

2026-10-31 19:04 那版实测：进程起来、窗口标题正确、卫星列表渲染、
`%LOCALAPPDATA%` 下自动建目录、生成的配置 `lat="" lon=""`（没带作者坐标）、
启动日志落盘。**没验**：连真机写表（要手台）、火绒是否弹窗拦这个 exe
（本机装了火绒但测试是用显式 TMP 起的，没触发拦截观察）。

## 2026-11-01 17:52 重打记录（交接-25 那一轮）

用的是**入库的 spec**（比长命令行稳，路径写死在里面）：

```bash
OUT="C:/Users/13766/Documents/deepseek-harness/default-workspace/临时/exe-build"
python -m PyInstaller --noconfirm --clean --distpath "$OUT/dist" --workpath "$OUT/build" F4HWN-Doppler-Tool.spec
```

产物 `F4HWN-Doppler-Tool.exe` 34,883,445 B / md5 `fa1c14fd418e111e4b08373c253efedb`，
复制成桌面 `无线电\DopplerPass.exe`（这个文件名不随构建变，桌面上只留它一份）。包的是 11-01 14:24 那份 `doppler_tool.py`。
构件原名件归档在 `无线电\_旧版exe-20261101\DopplerPass-1752.exe`。

**「坑 2」这次换了形态**：`cmd //c "set TMP=… && set TEMP=… && start …"` 那条路直接回
`拒绝访问。`，进程根本没起来（`tasklist` 里查不到，日志也没新增）。可行做法是
**直接在 exe 目录里执行它** `./DopplerPass.exe`（onefile 会起两个进程：
父进程 + 真正的 UI 子进程），验完 `taskkill //F //IM DopplerPass.exe` 收工。

验收判据（照旧，两条都要有）：

- `%LOCALAPPDATA%\DopplerPass\logs\pc-YYYYMMDD.log` 里有
  `11-01 17:54:46  start 多普勒过境表 BG0FPJ（固件基座 F4HWN SSTV） · 打包=True · py=3.13.14 · 数据目录=…`，
  且**没有 traceback**；
- `Get-Process DopplerPass | Select MainWindowTitle` 得到 `多普勒过境表 · BG0FPJ`
  —— 标题栏去掉「（固件基座 F4HWN SSTV）」那处改动确实进了包，能反证 pack 的是新源码。

zip 重打（zipfile, `ZIP_DEFLATED` compresslevel 6，与旧包同构：flat，两份文件）：

| 项 | 大小 | md5 |
|---|---|---|
| `DopplerPass.exe`（= 新 exe 改名） | 34,883,445 | `fa1c14fd418e111e4b08373c253efedb` |
| `f4hwn.sstv.doppler.bin`（= `交付/` 那份） | 106,480 | `8c31eb1e7495aafba156be5aab79349e` |
| `多普勒过境表-win单文件.zip` | 34,617,748 | `31519dae1e68a3560ca296f7611735b6` |

旧 zip 已删（02:00 版那个里面的 `DopplerPass-0200.exe` 与 `441908c5…` 都另有独立留存）；
18:32 因为固件换成 `8c31eb1e…` 又重打过一次 zip，**exe 一行没动**。
