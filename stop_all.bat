@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
echo ============================================
echo    ComfyUI H3 Studio - Stop Services
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_all.ps1" -Comfy
echo.
echo Stopped (Web UI + ComfyUI).
if "%~1"=="" (
  pause
)
endlocal
