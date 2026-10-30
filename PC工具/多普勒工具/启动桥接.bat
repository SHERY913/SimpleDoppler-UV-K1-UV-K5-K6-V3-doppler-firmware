@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 多普勒 串口桥接  COM口 -> 127.0.0.1:7878
echo ============================================================
echo  多普勒 串口桥接（口自动选，模拟器隧道自动维持）
echo  这个窗口不要关：App 的"网络桥"就是连这里。
echo  按着 PTT 时看下面有没有"串口掉线"或"client gone"。
echo ============================================================
echo.
python -u serial_bridge.py --port auto --listen 7878 --adb
echo.
echo 桥接已退出（退出码 %ERRORLEVEL%）
echo 上面的信息请整段复制发回。
pause
