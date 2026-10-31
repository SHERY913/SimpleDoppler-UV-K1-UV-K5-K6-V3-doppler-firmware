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
