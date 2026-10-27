@echo off
setlocal
cd /d "%~dp0"
echo ===  second-logic test  ===
echo.
echo 1) Enter the doppler page on the radio first: press F then 0.
echo 2) Then this window pushes frames for 24 s.
echo 3) Watch the radio screen: it should jump to the phone page.
echo.
python -c "import serial; s=serial.Serial('COM7',38400,timeout=0.1); s.close()" 2>nul
if errorlevel 1 goto :noport
python test_ext.py %*
goto :done

:noport
echo.
echo [ERROR] COM7 cannot be opened. Close MuMu / other python windows, then replug USB.

:done
echo.
pause