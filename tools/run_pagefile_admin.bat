@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo   Set Windows pagefile (requires admin / UAC)
echo   Target: C:\pagefile.sys  16384 MB - 32768 MB
echo ============================================
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0set_pagefile_admin.ps1\"'"
echo.
echo Done (if UAC was accepted). REBOOT required for it to take effect.
pause
endlocal
