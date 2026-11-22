<#
  停止本项目启动的 Web UI 与 ComfyUI（可选 -Comfy）。
  用法: powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\stop_all.ps1 [-Comfy]
#>
param([switch]$Comfy)
$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*uvicorn*webapp.app*' -or $_.CommandLine -like '*monitor_server.py*' } |
    ForEach-Object { Write-Host "stop web/ui pid $($_.ProcessId)"; taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
if ($Comfy) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*ComfyUI\main.py*' } |
        ForEach-Object { Write-Host "stop comfyui pid $($_.ProcessId)"; taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null }
}
Write-Host 'done'
