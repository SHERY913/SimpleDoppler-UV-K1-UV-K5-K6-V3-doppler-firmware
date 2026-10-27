@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo ==========================================
echo  电台连通性 / 固件版本检查
echo ==========================================
echo.
echo 一：先看电脑上哪个口在跟电台说话（MuMu 的虚拟口要排除）
echo.
python find_port.py
echo.
echo 二：问固件要版本、状态、当前页（不动频率，只读）
echo.
python probe_fw.py
echo.
echo ------------------------------------------
echo 判读：
echo   proto=2 state bit4=关 phase=1(READY)  好：机子在跑，新页还没开
echo   proto=2 state bit4=开 phase=5(EXT)    好：已经在第二套逻辑页
echo   连 0x0766 都不回                      电台没在听（关机/刷机界面/线）
echo   没有可用真串口                        设备被 MuMu 占了，先断开或关模拟器
echo ------------------------------------------
echo.
echo 三（可选）：打开第二套逻辑页并推一帧测试数据
echo   看到上面是 READY 之后，跑这一行才算测新页：
echo       python probe_fw.py --take --ext
echo   跑完会自动关掉，不留后遗症。
echo.
pause
