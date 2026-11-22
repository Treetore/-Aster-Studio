@echo off
setlocal
set ROOT=%~dp0..
set APP=%ROOT%\..\mian\ComfyUI_windows_portable
set PY=%APP%\python_embeded\pythonw.exe
if not exist "%PY%" set PY=%APP%\python_embeded\python.exe
if not exist "%PY%" set PY=pythonw.exe
cd /d "%ROOT%"
start "" "%PY%" "%ROOT%\launcher\run.py"
exit /b 0
