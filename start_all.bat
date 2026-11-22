@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
echo ============================================
echo    Aster Studio - Start ComfyUI + Web UI
echo ============================================
echo   [1/2] ComfyUI        http://127.0.0.1:8188
echo   [2/2] Web UI         http://127.0.0.1:8190/
echo.
echo   Optional flags: -NoComfyUI  -Port 8191  -WithHarness  -Tunnel
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_all.ps1" %*
set RC=%ERRORLEVEL%
echo.
echo ============================================
if "%RC%"=="0" (
  echo   DONE. Open http://127.0.0.1:8190/
) else (
  echo   Some steps failed ^(exit %RC%^). Check the logs folder.
)
echo ============================================
rem 有人传参时视为脚本/自动化调用，不阻塞
if "%~1"=="" (
  pause
)
endlocal
