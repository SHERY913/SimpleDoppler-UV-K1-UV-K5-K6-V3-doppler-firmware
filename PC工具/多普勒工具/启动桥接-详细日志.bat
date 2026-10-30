@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 多普勒 串口桥接（详细日志）
echo ============================================================
echo  详细模式：每个收发的字节都会打出来。
echo  发射时刷得很快是正常的；要抓的是"串口掉线"和"打不开"。
echo ============================================================
echo.
python -u serial_bridge.py --port auto --listen 7878 --adb --log
echo.
echo 桥接已退出（退出码 %ERRORLEVEL%）
pause
