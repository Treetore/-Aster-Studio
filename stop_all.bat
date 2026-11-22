@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
echo ============================================
echo    Aster Studio - Stop Services
echo ============================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_all.ps1" -Comfy
echo.
echo Stopped (Web UI + ComfyUI).
echo Tip: pass -Launcher to also stop the desktop launcher backend.
rem 有人传参时视为脚本/自动化调用，不阻塞
if "%~1"=="" (
  pause
)
endlocal
