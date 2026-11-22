# capture_stress_telemetry.ps1  (ASCII only)
# Samples GPU + memory every 2s until the stop file appears.
$ErrorActionPreference = 'SilentlyContinue'
$out  = 'K:\CONFI\comfy_mcp\logs\stress_telemetry.csv'
$stop = 'K:\CONFI\comfy_mcp\logs\telemetry.stop'
$smi  = 'C:\Windows\System32\nvidia-smi.exe'
Remove-Item -LiteralPath $stop -Force
"time,temp_c,power_w,sm_mhz,mem_mhz,util_pct,thermal,powercap,brake,ram_free_mb,commit_mb,queue_running" |
    Set-Content -LiteralPath $out -Encoding ASCII
while (-not (Test-Path -LiteralPath $stop)) {
    $g = & $smi --query-gpu=temperature.gpu,power.draw,clocks.sm,clocks.mem,utilization.gpu,clocks_throttle_reasons.hw_thermal_slowdown,clocks_throttle_reasons.sw_power_cap,clocks_throttle_reasons.hw_power_brake_slowdown --format=csv,noheader,nounits 2>$null
    $g = ($g -join ' ').Trim()
    $os = Get-CimInstance Win32_OperatingSystem
    $ram = [math]::Round($os.FreePhysicalMemory / 1KB)
    $commit = [math]::Round(($os.TotalVirtualMemorySize - $os.FreeVirtualMemory) / 1KB)
    $q = ''
    try { $qq = Invoke-RestMethod 'http://127.0.0.1:8188/queue' -TimeoutSec 3; $q = @($qq.queue_running).Count } catch { $q = 'NA' }
    $line = "$(Get-Date -Format 'HH:mm:ss'),$g,$ram,$commit,$q"
    Add-Content -LiteralPath $out -Value $line -Encoding ASCII
    Start-Sleep -Seconds 2
}
