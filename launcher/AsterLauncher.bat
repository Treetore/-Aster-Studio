@echo off
setlocal
set ROOT=K:\CONFI\comfy_mcp
set PY=K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\pythonw.exe
if not exist "%PY%" set PY=K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe
cd /d "%ROOT%"
start "" "%PY%" "%ROOT%\launcher\run.py"
exit /b 0
