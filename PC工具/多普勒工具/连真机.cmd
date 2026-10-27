@echo off
chcp 936 >nul
rem ============================================================
rem  电台 到 MuMu 里的 App，一条命令把链路架好
rem    电台 -USB-> COMx -serial_bridge-> 127.0.0.1:7878 -adb reverse-> App
rem  用法：双击本文件。用完关掉那个 doppler bridge 黑窗口就是断开。
rem  本文件必须保存成 ANSI/GBK。存成 UTF-8 的话 cmd 按 GBK 解码，中文变乱码，
rem  乱码里的符号会把命令行拆碎（之前就是这么炸的）。
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "ROOT=%CD%"
set "TMP=%ROOT%\.gtmp"
set "TEMP=%ROOT%\.gtmp"
set "ADB=%ROOT%\tc\sdk\platform-tools\adb.exe"
set "PYTHONIOENCODING=utf-8"
set "MODE=%~1"
set "DRY=%MODE%"
set "QUIET=%MODE%"
if not exist "%TMP%" mkdir "%TMP%"

echo [1/5] 找电台串口 VID 36B7 ...
set "PORT="
python -c "import serial.tools.list_ports as l;[print(p.device) for p in l.comports() if p.vid==0x36b7]" > "%TMP%\port.txt" 2>nul
if exist "%TMP%\port.txt" for /f "usebackq delims=" %%P in ("%TMP%\port.txt") do if not "%%P"=="" if not defined PORT set "PORT=%%P"
if not defined PORT goto noport
echo       %PORT%

echo [2/5] 起桥接 %PORT% 到 127.0.0.1:7878 ...
if /i "%DRY%"=="dry" goto dryskip
if /i "%QUIET%"=="quiet" (
  start "" pythonw "%ROOT%\多普勒工具\serial_bridge.py" --port %PORT% --listen 7878 > "%TMP%\bridge.log" 2>&1
) else (
  start "doppler bridge" cmd /k python "%ROOT%\多普勒工具\serial_bridge.py" --port %PORT% --listen 7878
)
for /l %%I in (1,1,16) do (
  python -c "import socket,sys;s=socket.socket();sys.exit(0 if s.connect_ex(('127.0.0.1',7878))==0 else 1)" && goto ready
  ping -n 2 127.0.0.1 >nul
)
goto nobridge
:ready
echo       7878 已监听
goto finddev

:dryskip
echo       [dry] 跳过启动桥接

:finddev
echo [3/5] 找模拟器 ...
if not exist "%ADB%" goto noadb
"%ADB%" devices > "%TMP%\dev.txt" 2>nul
set "DEV="
for /f "skip=1 tokens=1" %%D in (%TMP%\dev.txt) do if not "%%D"=="" if not defined DEV set "DEV=%%D"
if not defined DEV goto nodev
echo       %DEV%

echo [4/5] adb reverse tcp:7878 ...
if /i "%DRY%"=="dry" goto dryend
"%ADB%" -s %DEV% reverse tcp:7878 tcp:7878
if errorlevel 1 goto noreverse
"%ADB%" -s %DEV% reverse --list

echo [5/5] 打开 App ...
"%ADB%" -s %DEV% shell am start -n com.f4hwn.doppler/.MainActivity >nul 2>&1

echo.
echo ============================================================
echo  好了。App 里点：设置 - 网络桥。应看到
echo    网络已连通 127.0.0.1:7878 固件有回包
echo  然后「更新电台时间」「固件自检」都能点出结果。
echo.
echo  不要点「USB 连接」：那是手机 OTG 直连，模拟器没有 USB Host。
echo  桥接窗口一关，电台 15 秒后自动回到自己的跟踪，这是固件看门狗，正常。
echo ============================================================
if /i "%QUIET%"=="quiet" exit /b 0
pause
exit /b 0

:dryend
echo       [dry] 检查全部通过，未启动桥接、未做 reverse
exit /b 0

:noport
echo.
echo [错误] 没找到 VID 36B7 的串口。
echo   1. 电台是不是关了，开机后再插 USB。
echo   2. 线是纯充电线吗，换一根能传数据的。
echo   3. 设备管理器里有没有 USB 串行设备 COMx。
echo   看全部串口：python 多普勒工具\find_port.py
if /i not "%QUIET%"=="quiet" pause
exit /b 1

:nobridge
echo.
echo [错误] 桥接没起来，7878 没人监听。
echo   多半是 %PORT% 被别的程序占着：多普勒工具、另一个桥接窗口、串口助手。
echo   全关掉再试。
if /i not "%QUIET%"=="quiet" pause
exit /b 1

:noadb
echo.
echo [错误] 找不到 adb：%ADB%
echo   模拟器没装在这台机器，或者 tc\sdk 不在了。
if /i not "%QUIET%"=="quiet" pause
exit /b 1

:nodev
echo.
echo [错误] adb 看不到设备。先把 MuMu 打开、进到桌面，再跑这个脚本。
"%ADB%" devices
if /i not "%QUIET%"=="quiet" pause
exit /b 1

:noreverse
echo.
echo [错误] adb reverse 失败。
"%ADB%" devices
if /i not "%QUIET%"=="quiet" pause
exit /b 1
