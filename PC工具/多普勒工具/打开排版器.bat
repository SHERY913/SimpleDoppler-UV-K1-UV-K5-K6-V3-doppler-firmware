@echo off
cd /d "%~dp0"
start "layout-server" /min cmd /c "python -m http.server 8777 --bind 127.0.0.1"
timeout /t 2 >nul
start "" http://127.0.0.1:8777/layout_editor.html
