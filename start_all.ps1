<#
  一键链式启动：ComfyUI -> Web UI(FastAPI) -> DeepSeek Harness，并把内置提示词写入文件/剪贴板。
  用法：
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\start_all.ps1
    powershell -ExecutionPolicy Bypass -File ...start_all.ps1 -NoHarness -Port 8191
    powershell -ExecutionPolicy Bypass -File ...start_all.ps1 -PromptFile K:\CONFI\my_prompt.txt
#>
param(
    [int]$Port = 8190,
    [switch]$NoComfyUI,
    [switch]$NoHarness,
    [string]$PromptFile = ""
)

$ErrorActionPreference = 'SilentlyContinue'
$Root = 'K:\CONFI\comfy_mcp'
$App = 'K:\CONFI\mian\ComfyUI_windows_portable'
$Py = Join-Path $App 'python_embeded\python.exe'
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force $Logs | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Test-Comfy {
    try { Invoke-RestMethod 'http://127.0.0.1:8188/system_stats' -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

# 1) ComfyUI
if (-not $NoComfyUI -and -not (Test-Comfy)) {
    Write-Host '[1/3] 启动 ComfyUI (8188) ...' -ForegroundColor Cyan
    # 启动参数以 data/settings.json 为准（读取失败时退回调优后的默认值）
    $startArgs = @('--windows-standalone-build', '--listen', '127.0.0.1', '--port', '8188',
        '--fast', 'fp16_accumulation', '--cuda-malloc', '--disable-auto-launch', '--vram-headroom', '1.0')
    try {
        $cfg = Get-Content (Join-Path $Root 'data\settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.comfy.start_args) { $startArgs = @($cfg.comfy.start_args) }
    } catch { }
    Start-Process -FilePath $Py -ArgumentList (@('-s', (Join-Path $App 'ComfyUI\main.py')) + $startArgs) `
        -WorkingDirectory $App -RedirectStandardOutput (Join-Path $Logs 'comfy_stdout.log') `
        -RedirectStandardError (Join-Path $Logs 'comfy_stderr.log') -WindowStyle Hidden | Out-Null
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline -and -not (Test-Comfy)) { Start-Sleep -Seconds 2 }
    if (Test-Comfy) { Write-Host '      ComfyUI 就绪' -ForegroundColor Green }
    else { Write-Host '      ComfyUI 未就绪，请查看 logs\comfy_stderr.log' -ForegroundColor Yellow }
} else {
    Write-Host '[1/3] ComfyUI 已运行或跳过' -ForegroundColor DarkGray
}

# 2) 停旧监控/旧 Web UI，启动 FastAPI Web UI
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*monitor_server.py*' -or $_.CommandLine -like '*uvicorn*webapp.app*' } |
    ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
Write-Host "[2/3] 启动 Web UI : http://127.0.0.1:$Port/" -ForegroundColor Cyan
Start-Process -FilePath $Py -ArgumentList @('-m', 'uvicorn', 'webapp.app:app', '--host', '127.0.0.1', '--port', "$Port") `
    -WorkingDirectory $Root -RedirectStandardOutput (Join-Path $Logs 'webapp_stdout.log') `
    -RedirectStandardError (Join-Path $Logs 'webapp_stderr.log') -WindowStyle Hidden | Out-Null
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 }
}
$webOk = $false
try { Invoke-RestMethod "http://127.0.0.1:$Port/api/status" -TimeoutSec 2 | Out-Null; $webOk = $true } catch {}
if ($webOk) { Write-Host '      Web UI 就绪' -ForegroundColor Green }
else { Write-Host '      Web UI 启动失败，请查看 logs\webapp_stderr.log' -ForegroundColor Red }

# 3) 内置提示词 + Harness
$builtin = @'
你是本机 ComfyUI 视频项目（K:\CONFI\comfy_mcp）的 agent。先读 README.md、docs/ARCHITECTURE.md、SPEC.md、docs/OPTIMIZATION.md。
可用接口：MCP 工具 mcp__comfy__*；Web UI http://127.0.0.1:8190/ ；REST 文档 http://127.0.0.1:8190/docs 。
常用流程：status -> plan -> generate（5s/short 或 draft）-> job/wait -> 超分（可选）-> edit 剪辑 -> 交付。
资源约束：8GB VRAM / 32GB RAM，单飞；生成前看 status，保持资源守卫；ComfyUI 卡在 Model Initializing 超过 10 分钟时 kill 重启再提交。
数据库/任务记录：data/jobs.json、data/upscale_jobs.json；日志：logs/。
'@
$prompt = if ($PromptFile -and (Test-Path $PromptFile)) { Get-Content $PromptFile -Raw -Encoding UTF8 } else { $builtin }
$promptPath = Join-Path $Root 'data\harness_prompt.txt'
New-Item -ItemType Directory -Force (Split-Path $promptPath) | Out-Null
$prompt | Set-Content -Path $promptPath -Encoding UTF8
Set-Clipboard -Value $prompt
Write-Host "[3/3] 提示词已写入 $promptPath 并复制到剪贴板" -ForegroundColor Cyan

if (-not $NoHarness) {
    $h = Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue
    if (-not $h) {
        Start-Process 'K:\DSH\DeepSeek Harness.exe'
        Write-Host '      已启动 DeepSeek Harness' -ForegroundColor Green
    } else {
        Write-Host '      Harness 已在运行' -ForegroundColor DarkGray
    }
}


# 启动 cloudflared 端口映射（仅在本次项目启动时）
try {
    $tunnelUrl = & (Join-Path $PSScriptRoot 'tools\tunnel.ps1') start 2>$null
    if ($tunnelUrl) { Write-Host "Tunnel  : $tunnelUrl" -ForegroundColor Green }
} catch {
    Write-Host "Tunnel  : 启动失败（$($_.Exception.Message)）" -ForegroundColor Yellow
}

Write-Host ''
Write-Host "Web UI : http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "API    : http://127.0.0.1:$Port/docs" -ForegroundColor Green
Write-Host '提示词 : 已复制到剪贴板，粘贴到 Harness 对话框即可开始' -ForegroundColor Yellow
