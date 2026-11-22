# 安全启动 ComfyUI（便携版）
# 用法：
#   .\start_comfyui_safe.ps1                 # 前台启动
#   .\start_comfyui_safe.ps1 -Guard          # 前台启动 + RAM 低于阈值时自动 /interrupt
#   .\start_comfyui_safe.ps1 -Port 8189      # 换端口
#
# 为什么要这个脚本：
#   1) 便携版在 GBK 控制台下遇到 emoji 日志（rgthree 的 🎉）会 UnicodeEncodeError 直接崩溃，
#      所以必须 PYTHONUTF8=1 / PYTHONIOENCODING=utf-8。
#   2) 8GB VRAM / 32GB RAM 的机器上跑 MiniMax-H3（约 40GB 权重）容易把内存打满，
#      可选 -Guard 在空闲 RAM 低于 -MinRamGb 时调用 /interrupt 中止当前任务。
param(
    [int]$Port = 8188,
    [switch]$Guard,
    [double]$MinRamGb = 2.5,
    [int]$PollSeconds = 5
)

$ErrorActionPreference = 'Stop'
$App = 'K:\CONFI\mian\ComfyUI_windows_portable'
$Python = Join-Path $App 'python_embeded\python.exe'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

if (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $Port }) {
    Write-Host "端口 $Port 已在监听，ComfyUI 可能已运行。直接打开 http://127.0.0.1:$Port" -ForegroundColor Yellow
    exit 0
}

$log = Join-Path $App 'mcp_run_comfy.log'
$err = Join-Path $App 'mcp_run_comfy.err.log'
Write-Host "启动 ComfyUI: http://127.0.0.1:$Port  (日志: $log)" -ForegroundColor Cyan
$argsList = @('-s', 'ComfyUI\main.py', '--windows-standalone-build',
              '--listen', '127.0.0.1', '--port', "$Port",
              '--fast', '--cuda-malloc', '--disable-auto-launch')
$proc = Start-Process -FilePath $Python -ArgumentList $argsList -WorkingDirectory $App `
    -RedirectStandardOutput $log -RedirectStandardError $err -PassThru -WindowStyle Hidden

if (-not $Guard) {
    Write-Host "ComfyUI PID=$($proc.Id)。按 Ctrl+C 退出本脚本不会停止 ComfyUI；停止请用 comfy_mcp 的 stop 工具或 taskkill /PID $($proc.Id)。"
    $proc.WaitForExit()
    exit $proc.ExitCode
}

Write-Host "守卫模式：每 $PollSeconds 秒检查空闲 RAM，低于 $MinRamGb GB 时中断当前任务。" -ForegroundColor Yellow
try {
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds $PollSeconds
        try {
            $os = Get-CimInstance Win32_OperatingSystem
            $freeGb = [math]::Round($os.FreePhysicalMemory / 1MB, 2)
            if ($freeGb -lt $MinRamGb) {
                Write-Host "[guard] 空闲 RAM ${freeGb}GB < $MinRamGb GB，发送 /interrupt" -ForegroundColor Red
                try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port/interrupt" -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 10 | Out-Null } catch {}
                try { Invoke-RestMethod -Uri "http://127.0.0.1:$Port/free" -Method Post -Body '{"unload_models":true,"free_memory":true}' -ContentType 'application/json' -TimeoutSec 30 | Out-Null } catch {}
                Start-Sleep -Seconds 15
            }
        } catch {
            Write-Host "[guard] 检查失败: $_" -ForegroundColor DarkYellow
        }
    }
} finally {
    Write-Host "ComfyUI 进程结束（exit=$($proc.ExitCode)）。"
}
