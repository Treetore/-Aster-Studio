<#
  一键启动：ComfyUI (8188) -> Web UI (FastAPI, 默认 8190)。
  只启动这两个服务；Harness/剪贴板/隧道等附加动作默认关闭，需要时用对应开关。

  可移植性：
    - 项目根 = $PSScriptRoot（无写死盘符）
    - ComfyUI 根 = 环境变量 COMFYUI_ROOT / COMFY_MCP_COMFY_ROOT，否则按同级目录
      （..\mian\ComfyUI_windows_portable\ComfyUI 等）自动探测
    - Python = 环境变量 COMFY_MCP_PYTHON，否则便携版 python_embeded，最后退回 PATH 上的 python

  用法：
    powershell -ExecutionPolicy Bypass -File .\start_all.ps1
    powershell -ExecutionPolicy Bypass -File .\start_all.ps1 -Port 8191 -NoComfyUI
    powershell -ExecutionPolicy Bypass -File .\start_all.ps1 -ComfyRoot D:\ComfyUI_windows_portable\ComfyUI
    powershell -ExecutionPolicy Bypass -File .\start_all.ps1 -WithHarness      # 可选：额外启动 Harness
#>
param(
    [int]$Port = 8190,
    [int]$ComfyPort = 8188,
    [switch]$NoComfyUI,
    [string]$ComfyRoot = "",
    [string]$ComfyPython = "",
    # 只打印解析结果与将要执行的命令，不启动任何进程
    [switch]$DryRun,
    # 可选附加动作（默认全部关闭，传参才生效）
    [switch]$WithHarness,
    [switch]$Tunnel,
    # 兼容旧的调用方式：不改变默认行为
    [switch]$NoHarness,
    [string]$PromptFile = ""
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path -LiteralPath $Root).Path
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

# ---- UTF-8 环境（ComfyUI/uvicorn 日志中文不乱码） ----
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
try {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
} catch { }

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        if ($t -match '^export\s+(.+)$') { $t = $Matches[1] }  # -match 默认不区分大小写
        $i = $t.IndexOf('=')
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
        if (-not $k) { continue }
        if (-not [Environment]::GetEnvironmentVariable($k, 'Process')) {
            [Environment]::SetEnvironmentVariable($k, $v, 'Process')
        }
    }
}

Import-DotEnv (Join-Path $Root '.env')

function Normalize-PathText([string]$v) {
    if (-not $v) { return '' }
    return $v.Trim().Trim('"').Trim("'").Trim()
}

# 用字符串拼接代替 Join-Path：后者会在不存在的盘符（如 Z:\）上抛错
function Join-Dir([string]$dir, [string]$child) {
    if (-not $dir) { return $child }
    return $dir.TrimEnd('\', '/') + '\' + $child
}

function Test-MainPy([string]$dir) {
    if (-not $dir) { return $false }
    try { return (Test-Path -LiteralPath (Join-Dir $dir 'main.py') -PathType Leaf) } catch { return $false }
}

function Resolve-ComfyRoot([string]$Explicit) {
    $raw = @()
    $e1 = Normalize-PathText $Explicit
    $e2 = Normalize-PathText $env:COMFYUI_ROOT
    $e3 = Normalize-PathText $env:COMFY_MCP_COMFY_ROOT
    foreach ($candidate in @($e1, $e2, $e3)) { if ($candidate) { $raw += $candidate } }
    foreach ($r in $raw) {
        $d = $r.TrimEnd('\', '/')
        if (Test-MainPy $d) { return $d }
        if (Test-MainPy (Join-Dir $d 'ComfyUI')) { return (Join-Dir $d 'ComfyUI') }
        try {
            if (Test-Path -LiteralPath $d) { return $d }
        } catch { }
        Write-Host "  ! '$r' 不存在或不含 main.py，继续按同级目录探测" -ForegroundColor Yellow
    }
    $parent = Split-Path -Parent $Root
    $grand = Split-Path -Parent $parent
    $candidates = @(
        (Join-Dir $parent 'mian\ComfyUI_windows_portable\ComfyUI'),
        (Join-Dir $parent 'ComfyUI_windows_portable\ComfyUI'),
        (Join-Dir $Root 'mian\ComfyUI_windows_portable\ComfyUI'),
        (Join-Dir $Root 'ComfyUI_windows_portable\ComfyUI'),
        (Join-Dir $parent 'ComfyUI'),
        (Join-Dir $Root 'ComfyUI'),
        (Join-Dir $grand 'mian\ComfyUI_windows_portable\ComfyUI'),
        (Join-Dir $grand 'ComfyUI_windows_portable\ComfyUI')
    )
    foreach ($c in $candidates) {
        if (Test-MainPy $c) { return $c }
    }
    return $null
}

function Resolve-ComfyPython([string]$Comfy, [string]$Explicit) {
    $ex = Normalize-PathText $Explicit
    if ($ex -and (Test-Path -LiteralPath $ex -PathType Leaf)) { return $ex }
    $envPy = Normalize-PathText $env:COMFY_MCP_PYTHON
    if ($envPy -and (Test-Path -LiteralPath $envPy -PathType Leaf)) { return $envPy }
    if ($Comfy) {
        $portable = Split-Path -Parent $Comfy
        foreach ($c in @((Join-Dir $portable 'python_embeded\python.exe'),
                         (Join-Dir $Comfy 'python_embeded\python.exe'),
                         (Join-Dir $Comfy '.venv\Scripts\python.exe'))) {
            try { if (Test-Path -LiteralPath $c -PathType Leaf) { return $c } } catch { }
        }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Test-Comfy([int]$P) {
    try { Invoke-RestMethod "http://127.0.0.1:$P/system_stats" -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

function Test-Web([int]$P) {
    try { Invoke-RestMethod "http://127.0.0.1:$P/api/status" -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

$fail = 0
$Comfy = Resolve-ComfyRoot $ComfyRoot
$Py = Resolve-ComfyPython $Comfy $ComfyPython

Write-Host "Project : $Root" -ForegroundColor DarkGray
Write-Host "Logs    : $Logs" -ForegroundColor DarkGray

if ($DryRun) {
    $settingsFile = Join-Path $Root 'data\settings.json'
    Write-Host ''
    Write-Host '[dry-run] 仅解析路径，不启动任何进程' -ForegroundColor Yellow
    Write-Host "  ComfyUI root : $Comfy"
    Write-Host "  Python       : $Py"
    Write-Host "  Web 端口     : $Port"
    Write-Host "  Comfy 端口   : $ComfyPort"
    Write-Host "  settings     : $settingsFile ($(if (Test-Path -LiteralPath $settingsFile) { '存在' } else { '缺失' }))"
    Write-Host "  Comfy 运行中 : $(Test-Comfy $ComfyPort)"
    Write-Host "  Web   运行中 : $(Test-Web $Port)"
    Write-Host ("  Comfy 命令   : {0} -s {1} ..." -f $Py, (Join-Path $Comfy 'main.py'))
    Write-Host ("  Web   命令   : {0} -m uvicorn webapp.app:app --host 127.0.0.1 --port {1}" -f $Py, $Port)
    exit 0
}

# ---------------- 1) ComfyUI ----------------
if ($NoComfyUI) {
    Write-Host '[1/2] ComfyUI: 跳过 (-NoComfyUI)' -ForegroundColor DarkGray
} elseif (Test-Comfy $ComfyPort) {
    Write-Host "[1/2] ComfyUI: 已在运行 (:$ComfyPort)" -ForegroundColor DarkGray
} elseif (-not $Comfy) {
    Write-Host '[1/2] ComfyUI: 未找到安装位置。' -ForegroundColor Yellow
    Write-Host '      请设置环境变量 COMFYUI_ROOT（指向含 main.py 的 ComfyUI 目录，' -ForegroundColor Yellow
    Write-Host '      或含 ComfyUI\ 与 python_embeded\ 的便携版根目录），或运行 setup\install.py。' -ForegroundColor Yellow
    $fail = 1
} elseif (-not $Py) {
    Write-Host '[1/2] ComfyUI: 未找到可用 Python（COMFY_MCP_PYTHON）' -ForegroundColor Yellow
    $fail = 1
} else {
    Write-Host "[1/2] 启动 ComfyUI (:$ComfyPort) ..." -ForegroundColor Cyan
    Write-Host "      root   : $Comfy" -ForegroundColor DarkGray
    Write-Host "      python : $Py" -ForegroundColor DarkGray
    # 启动参数以 data/settings.json 为准（读取失败时用安全默认值）
    $startArgs = @('--windows-standalone-build', '--listen', '127.0.0.1', '--port', "$ComfyPort",
        '--fast', '--cuda-malloc', '--disable-auto-launch')
    $settingsFile = Join-Path $Root 'data\settings.json'
    if (Test-Path -LiteralPath $settingsFile) {
        try {
            $cfg = Get-Content -LiteralPath $settingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.comfy.start_args) {
                $startArgs = @($cfg.comfy.start_args | ForEach-Object { "$_" })
                # 端口与本次调用保持一致
                for ($i = 0; $i -lt $startArgs.Count - 1; $i++) {
                    if ($startArgs[$i] -eq '--port') { $startArgs[$i + 1] = "$ComfyPort" }
                }
            }
        } catch { }
    }
    # 首次启动向导选择 low_vram 时，自动追加低显存启动参数
    try {
        $hostModeFile = Join-Path $Root 'data\host_mode.json'
        if (Test-Path -LiteralPath $hostModeFile) {
            $hm = Get-Content -LiteralPath $hostModeFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($hm.mode -eq 'low_vram' -and ($startArgs -notcontains '--lowvram')) {
                $startArgs += @('--lowvram', '--cache-none', '--reserve-vram', '0.8')
            }
        }
    } catch { }
    $portableRoot = Split-Path -Parent $Comfy
    $workDir = if (Test-Path -LiteralPath $portableRoot) { $portableRoot } else { $Comfy }
    try {
        Start-Process -FilePath $Py `
            -ArgumentList (@('-s', (Join-Path $Comfy 'main.py')) + $startArgs) `
            -WorkingDirectory $workDir `
            -RedirectStandardOutput (Join-Path $Logs 'comfy_stdout.log') `
            -RedirectStandardError (Join-Path $Logs 'comfy_stderr.log') `
            -WindowStyle Hidden | Out-Null
    } catch {
        Write-Host "      启动失败：$($_.Exception.Message)" -ForegroundColor Red
        $fail = 1
    }
    if (-not $fail) {
        $deadline = (Get-Date).AddSeconds(180)
        while ((Get-Date) -lt $deadline -and -not (Test-Comfy $ComfyPort)) { Start-Sleep -Seconds 2 }
        if (Test-Comfy $ComfyPort) { Write-Host '      ComfyUI 就绪' -ForegroundColor Green }
        else {
            Write-Host "      ComfyUI 未就绪，请查看 $(Join-Path $Logs 'comfy_stderr.log')" -ForegroundColor Yellow
            $fail = 1
        }
    }
}

# ---------------- 2) Web UI ----------------
# 停掉旧的 Web UI / 监控进程，避免端口占用
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*uvicorn*webapp.app*' -or $_.CommandLine -like '*monitor_server.py*' } |
    ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }

if (-not $Py) {
    Write-Host '[2/2] Web UI: 跳过（无可用 Python）' -ForegroundColor Yellow
    $fail = 1
} else {
    Write-Host "[2/2] 启动 Web UI : http://127.0.0.1:$Port/" -ForegroundColor Cyan
    try {
        Start-Process -FilePath $Py `
            -ArgumentList @('-m', 'uvicorn', 'webapp.app:app', '--host', '127.0.0.1', '--port', "$Port") `
            -WorkingDirectory $Root `
            -RedirectStandardOutput (Join-Path $Logs 'webapp_stdout.log') `
            -RedirectStandardError (Join-Path $Logs 'webapp_stderr.log') `
            -WindowStyle Hidden | Out-Null
    } catch {
        Write-Host "      启动失败：$($_.Exception.Message)" -ForegroundColor Red
        $fail = 1
    }
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and -not (Test-Web $Port)) { Start-Sleep -Seconds 1 }
    if (Test-Web $Port) { Write-Host '      Web UI 就绪' -ForegroundColor Green }
    else {
        Write-Host "      Web UI 启动失败，请查看 $(Join-Path $Logs 'webapp_stderr.log')" -ForegroundColor Red
        $fail = 1
    }
}

# ---------------- 可选：Harness / 提示词 ----------------
if ($WithHarness -and -not $NoHarness) {
    $promptPath = Join-Path $Root 'data\harness_prompt.txt'
    if (Test-Path -LiteralPath $promptPath) {
        try {
            $text = Get-Content -LiteralPath $promptPath -Raw -Encoding UTF8
            if ($PromptFile -and (Test-Path -LiteralPath $PromptFile)) {
                $text = Get-Content -LiteralPath $PromptFile -Raw -Encoding UTF8
            }
            Set-Clipboard -Value $text
            Write-Host "提示词 : 已复制到剪贴板（$promptPath）" -ForegroundColor DarkGray
        } catch { }
    }
    if (-not (Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue)) {
        $harness = $env:HARNESS_EXE
        if ($harness -and (Test-Path -LiteralPath $harness)) {
            Start-Process $harness
            Write-Host '      已启动 DeepSeek Harness' -ForegroundColor Green
        } else {
            Write-Host '      未找到 Harness，可设置 HARNESS_EXE 指向可执行文件' -ForegroundColor Yellow
        }
    } else {
        Write-Host '      Harness 已在运行' -ForegroundColor DarkGray
    }
}

# ---------------- 可选：隧道 ----------------
if ($Tunnel) {
    $tunnelScript = Join-Path $Root 'tools\tunnel.ps1'
    if (Test-Path -LiteralPath $tunnelScript) {
        try {
            $tunnelUrl = & $tunnelScript start 2>$null
            if ($tunnelUrl) { Write-Host "Tunnel  : $tunnelUrl" -ForegroundColor Green }
        } catch {
            Write-Host "Tunnel  : 启动失败（$($_.Exception.Message)）" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Tunnel  : 未找到 $tunnelScript" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host "Web UI : http://127.0.0.1:$Port/" -ForegroundColor Green
Write-Host "API    : http://127.0.0.1:$Port/docs" -ForegroundColor Green
if (Test-Comfy $ComfyPort) { Write-Host "Comfy  : http://127.0.0.1:$ComfyPort/" -ForegroundColor Green }
exit $fail
