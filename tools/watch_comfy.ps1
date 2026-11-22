<#
  ComfyUI live monitor (Windows PowerShell 5.1 compatible)
  ========================================================
  用法 / Usage:
    # 只监控（ComfyUI 需已运行）
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\watch_comfy.ps1
    # 若 ComfyUI 没运行则先启动，再监控
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\watch_comfy.ps1 -Start
    # 只刷新一屏（脚本自检用）
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\watch_comfy.ps1 -Once

  按 Ctrl+C 退出。刷新间隔用 -RefreshSeconds（默认 2 秒），日志行数 -LogLines（默认 6）。
#>
param(
    [switch]$Start,
    [switch]$Once,
    [int]$RefreshSeconds = 2,
    [int]$LogLines = 6
)

$ErrorActionPreference = 'SilentlyContinue'

$App      = 'K:\CONFI\mian\ComfyUI_windows_portable'
$Py       = Join-Path $App 'python_embeded\python.exe'
$MainPy   = Join-Path $App 'ComfyUI\main.py'
$LogDir   = 'K:\CONFI\comfy_mcp\logs'
$ErrLog   = Join-Path $LogDir 'comfy_stderr.log'
$OutLog   = Join-Path $LogDir 'comfy_stdout.log'
$Url      = 'http://127.0.0.1:8188'
$Settings = 'K:\CONFI\comfy_mcp\data\settings.json'

function Get-ComfyStats {
    try { return Invoke-RestMethod -Uri "$Url/system_stats" -TimeoutSec 2 } catch { return $null }
}

function Start-ComfyUI {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Start-Process -FilePath $Py `
        -ArgumentList @('-s', $MainPy, '--windows-standalone-build', '--listen', '127.0.0.1', '--port', '8188', '--fast', '--cuda-malloc', '--disable-auto-launch') `
        -WorkingDirectory $App `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -WindowStyle Hidden | Out-Null
}

function Get-GuardSettings {
    if (Test-Path $Settings) {
        try {
            $j = Get-Content $Settings -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($j.safety) { return $j.safety }
        } catch {}
    }
    return $null
}

function Get-Jobs {
    try {
        $r = Invoke-RestMethod -Uri "$Url/api/jobs?limit=5" -TimeoutSec 2
        if ($r.jobs) { return @($r.jobs) }
        if ($r -is [array]) { return $r }
        return @()
    } catch { return @() }
}

function Get-SamplingProgress {
    if (-not (Test-Path $ErrLog)) { return '' }
    $tail = Get-Content $ErrLog -Tail 500 -Encoding UTF8
    for ($i = $tail.Count - 1; $i -ge 0; $i--) {
        $line = $tail[$i]
        if ($line -match '(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[([^\]]+)\]') {
            return ("{0}/{1} steps   [{2}]" -f $matches[2], $matches[3], $matches[4])
        }
    }
    return ''
}

function Get-LogTail([int]$n) {
    if (-not (Test-Path $ErrLog)) { return @() }
    $lines = Get-Content $ErrLog -Tail $n -Encoding UTF8
    return @($lines | ForEach-Object { $_ -replace '\x1b\[[0-9;]*m', '' })
}

# ---- 可选：自动启动 ----
if ($Start -and -not (Get-ComfyStats)) {
    Write-Host 'ComfyUI is not running. Starting it ...' -ForegroundColor Cyan
    Start-ComfyUI
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline -and -not (Get-ComfyStats)) { Start-Sleep -Seconds 2 }
}

$gpuQ    = 'name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw'
$guard   = Get-GuardSettings
$loop    = $true
while ($loop) {
    $now    = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $stats  = Get-ComfyStats
    $os     = Get-CimInstance Win32_OperatingSystem
    $ramFree  = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    $ramTotal = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    $commitUsed  = [math]::Round(($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1MB, 1)
    $commitLimit = [math]::Round($os.TotalVirtualMemorySize / 1MB, 1)
    $pf = Get-CimInstance Win32_PageFileUsage
    $pfUsed = 0; $pfTotal = 0
    if ($pf) {
        $pfUsed  = [math]::Round($pf.CurrentUsage / 1024, 1)
        $pfTotal = [math]::Round($pf.AllocatedBaseSize / 1024, 1)
    }

    $gpuRaw = (& nvidia-smi --query-gpu=$gpuQ --format=csv,noheader,nounits) -join ' '
    $g = $gpuRaw -split ',\s*'

    $jobs = Get-Jobs
    $jobLine = 'none'
    $runningJob = $false
    if ($jobs.Count -gt 0) {
        $j = $jobs[0]
        if ($j.status -eq 'in_progress' -or $j.status -eq 'pending') { $runningJob = $true }
        $elapsed = ''
        if ($j.execution_start_time) {
            $nowMs = [int64]([DateTime]::UtcNow - [DateTime]::new(1970,1,1,0,0,0,[DateTimeKind]::Utc)).TotalMilliseconds
            $secs = [int](($nowMs - [int64]$j.execution_start_time) / 1000)
            $elapsed = ('{0:hh\:mm\:ss}' -f ([TimeSpan]::FromSeconds([math]::Max(0,$secs))))
        }
        $jobLine = ('{0}  status={1}  elapsed={2}' -f ($j.id.Substring(0,8)), $j.status, $elapsed)
    }
    $progress = ''
    if ($runningJob) { $progress = Get-SamplingProgress }
    if (-not $progress) { $progress = '-' }

    Clear-Host
    Write-Host '==================== ComfyUI LIVE MONITOR ====================' -ForegroundColor Cyan
    Write-Host ("time        : {0}" -f $now)
    if ($stats) {
        Write-Host ("comfy       : RUNNING v{0}" -f $stats.system.comfyui_version) -ForegroundColor Green
        try {
            $q = Invoke-RestMethod -Uri "$Url/queue" -TimeoutSec 2
            Write-Host ("comfy queue : running={0} pending={1}" -f @($q.queue_running).Count, @($q.queue_pending).Count) -ForegroundColor Green
        } catch {}
    } else {
        Write-Host 'comfy       : DOWN (start with: watch_comfy.ps1 -Start)' -ForegroundColor Red
    }
    Write-Host ("GPU         : {0}  VRAM {1}/{2} MB  util {3}%  {4}C  {5}W" -f $g[0], $g[1], $g[2], $g[3], $g[4], $g[5]) -ForegroundColor Yellow
    Write-Host ("RAM         : free {0}/{1} GB" -f $ramFree, $ramTotal)
    Write-Host ("commit      : {0}/{1} GB   pagefile {2}/{3} GB" -f $commitUsed, $commitLimit, $pfUsed, $pfTotal)
    if ($guard) {
        $sf = 'single_flight={0}' -f $guard.single_flight
        Write-Host ("guard       : min_ram={0}GB trip_ram={1}GB  {2}" -f $guard.min_ram_free_gb, $guard.trip_ram_free_gb, $sf) -ForegroundColor DarkGray
    }
    Write-Host '-------------------------------------------------------------'
    if ($stats) {
        Write-Host ("job         : {0}" -f $jobLine)
        Write-Host ("progress    : {0}" -f $progress)
    }
    Write-Host '-------------------------------------------------------------'
    Write-Host 'log tail (comfy_stderr.log):' -ForegroundColor DarkGray
    Get-LogTail $LogLines | ForEach-Object { Write-Host ("  {0}" -f $_) }
    Write-Host '-------------------------------------------------------------'
    Write-Host 'Ctrl+C to exit   |   生成任务状态来自 /api/jobs，进度解析自 tqdm' -ForegroundColor DarkGray

    if ($Once) { break }
    Start-Sleep -Seconds $RefreshSeconds
}
