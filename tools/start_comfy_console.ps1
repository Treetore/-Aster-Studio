<#
  Start ComfyUI in a VISIBLE console window (UTF-8), so you can watch the raw log live.
  ==============================================================================
  用法 / Usage:
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\start_comfy_console.ps1
    # 低内存机型（更省内存、更慢）:
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\start_comfy_console.ps1 -LowMem

  另一个窗口再开实时仪表盘：
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\watch_comfy.ps1
#>
param(
    [switch]$LowMem,
    [int]$Port = 8188
)

$ErrorActionPreference = 'Stop'
$App  = 'K:\CONFI\mian\ComfyUI_windows_portable'
$Py   = Join-Path $App 'python_embeded\python.exe'
$Main = Join-Path $App 'ComfyUI\main.py'

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
chcp 65001 | Out-Null

if (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port }) {
    Write-Host "Port $Port is already in use; ComfyUI may already be running." -ForegroundColor Yellow
    Write-Host "Open http://127.0.0.1:$Port  or run watch_comfy.ps1"
    exit 0
}

$args = @('-s', $Main, '--windows-standalone-build', '--listen', '127.0.0.1', '--port', "$Port",
          '--fast', '--cuda-malloc', '--disable-auto-launch')
if ($LowMem) { $args += @('--lowvram', '--cache-none', '--reserve-vram', '0.8') }

Write-Host '============================================================' -ForegroundColor Cyan
Write-Host " ComfyUI console  |  http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host 'ComfyUI 原始日志会持续滚动；另开一个窗口运行 watch_comfy.ps1 可看 GPU/内存/任务进度。'
Write-Host ''

Set-Location $App
& $Py @args
