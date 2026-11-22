@echo off
chcp 65001 >nul
cd /d "%~dp0"
"K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe" -u tools\auto_film.py %*
if "%~1"=="" pause
