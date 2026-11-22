@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
echo ============================================
echo    ComfyUI H3 Studio - Start All Services
echo ============================================
echo   [1/3] ComfyUI        http://127.0.0.1:8188
echo   [2/3] Web UI         http://127.0.0.1:8190/
echo   [3/3] DeepSeek Harness (optional)
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
if "%~1"=="" (
  pause
)
endlocal
