@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

rem 修 U-P-1：以前直接 start pythonw，doppler_tool.py 顶层裸 import 缺包时
rem 窗口一闪全无，用户什么都看不到。现在先用 python 在控制台里试导入，
rem 缺什么就把 Python 原话和 pip 命令打出来，并 pause 等用户看完。
rem 连自己的模块一起导入：doppler_core / sat_freq / doppler_tool 里的语法或
rem 版本问题也能在这里暴露出来，而不是等 pythonw 静默死掉。

echo 正在检查运行环境（tkinter / numpy / sgp4 / pyserial / 本工具模块）……
python -c "import tkinter, numpy, sgp4.api, serial, doppler_core, sat_freq, doppler_tool" 2>"%~dp0_start_err.tmp"
if not errorlevel 1 goto ok

echo.
echo [启动失败] 下面是 Python 的原话：
echo ---------------------------------------------------------------
if exist "%~dp0_start_err.tmp" type "%~dp0_start_err.tmp"
echo ---------------------------------------------------------------
echo.
echo 多半是缺 Python 包。按提示补装（已装的包不会重装）：
echo     python -m pip install numpy sgp4 pyserial
echo.
echo 如果上面报的是 "python" 不是内部或外部命令，那是 Python 没装、
echo 或者装了但没勾 "Add python.exe to PATH"。
echo.
del "%~dp0_start_err.tmp" >nul 2>nul
pause
exit /b 1

:ok
del "%~dp0_start_err.tmp" >nul 2>nul
rem 环境正常：用 pythonw 无控制台起 GUI，本窗口自己退出。
start "" pythonw "%~dp0doppler_tool.py"
exit /b 0
