@echo off
chcp 936 >nul
rem 断开桥接：杀掉占着 7878 的进程，并清掉 adb reverse。
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "ROOT=%CD%"
set "TMP=%ROOT%\.gtmp"
set "TEMP=%ROOT%\.gtmp"
set "ADB=%ROOT%\tc\sdk\platform-tools\adb.exe"

echo [1/2] 关掉 7878 上的桥接 ...
set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:"TCP.*:7878 .*LISTENING"') do (
    if not "%%P"=="0" if not defined FOUND (
        set "FOUND=%%P"
        taskkill /F /PID %%P >nul 2>&1
        echo       已结束进程 %%P
    )
)
if not defined FOUND echo       没有在跑

echo [2/2] 清掉 adb reverse ...
if not exist "%ADB%" goto end
for /f "skip=1 tokens=1" %%D in ('"%ADB%" devices') do (
    if not "%%D"=="" "%ADB%" -s %%D reverse --remove tcp:7878 >nul 2>&1
)
echo       完成
:end
echo.
echo 电台的 USB 口空出来了，可以直接用 PC 端「多普勒工具」。
pause
exit /b 0
