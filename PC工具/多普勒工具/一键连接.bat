@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 多普勒 一键连接（桥接 + 模拟器端口映射）

set ADB="C:\Program Files\Netease\MuMu\nx_main\adb.exe"

echo ============================================================
echo  1/2  把模拟器的 7878 转到这台电脑
echo ============================================================
%ADB% connect 127.0.0.1:16384 >nul 2>&1
%ADB% reverse tcp:7878 tcp:7878
if errorlevel 1 (
  echo.
  echo  [失败] 模拟器没连上。先打开 MuMu，再双击本文件。
  echo         或者用 adb 的端口：MuMu 设置里能看到 5555 或 16384。
  pause
  exit /b 1
)
echo  完成。
echo.
echo ============================================================
echo  2/2  串口桥接（COM7 -^> 127.0.0.1:7878）
echo  这个窗口不要关。App 里点"网络桥"就能连。
echo ============================================================
echo.
python -u serial_bridge.py --port COM7 --listen 7878
echo.
echo 桥接已退出（退出码 %ERRORLEVEL%）
pause
