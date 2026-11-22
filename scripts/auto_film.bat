@echo off
chcp 65001 >nul
set ROOT=%~dp0..
set PY=%ROOT%\..\mian\ComfyUI_windows_portable\python_embeded\python.exe
if not exist "%PY%" set PY=python
cd /d "%ROOT%"
"%PY%" -u tools\auto_film.py %*
if "%~1"=="" pause
