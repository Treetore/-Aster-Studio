# apply_tdr_windows.ps1  (ASCII only - Windows PowerShell 5.1 safe)
# Purpose: raise Windows GPU TDR timeout so long kernels / big copies are not
#          treated as hung by the default 2s threshold.
# Requires administrator. Run:
#   powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\apply_tdr_windows.ps1
# Takes effect after the display driver reloads, i.e. after a reboot.
$ErrorActionPreference = 'Stop'
$log = 'K:\CONFI\comfy_mcp\logs\tdr_apply_result.txt'
$lines = @()
$lines += "time=" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$lines += "admin=$isAdmin"
$lines | Set-Content -Path $log -Encoding UTF8

if (-not $isAdmin) {
    $lines += "ERROR: administrator required, nothing changed"
    $lines | Set-Content -Path $log -Encoding UTF8
    exit 1
}

$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers'
try {
    New-ItemProperty -Path $key -Name 'TdrDelay' -PropertyType DWord -Value 60 -Force | Out-Null
    New-ItemProperty -Path $key -Name 'TdrDdiDelay' -PropertyType DWord -Value 60 -Force | Out-Null
    $v1 = (Get-ItemProperty -Path $key -Name 'TdrDelay').TdrDelay
    $v2 = (Get-ItemProperty -Path $key -Name 'TdrDdiDelay').TdrDdiDelay
    $lines += "TdrDelay=$v1"
    $lines += "TdrDdiDelay=$v2"
}
catch {
    $lines += "REG_ERROR: " + $_.Exception.Message
}

try {
    if (-not (Test-Path 'C:\Windows\Minidump')) {
        New-Item -ItemType Directory -Path 'C:\Windows\Minidump' -Force | Out-Null
    }
    $lines += "MinidumpDirExists=" + (Test-Path 'C:\Windows\Minidump')
}
catch {
    $lines += "MINIDUMP_ERROR: " + $_.Exception.Message
}

try {
    $cc = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
    $lines += "CrashDumpEnabled=" + $cc.CrashDumpEnabled
    $lines += "AutoReboot=" + $cc.AutoReboot
}
catch {
    $lines += "CRASHCTL_ERROR: " + $_.Exception.Message
}

$lines += "done=1"
$lines | Set-Content -Path $log -Encoding UTF8
$lines | ForEach-Object { Write-Host $_ }
exit 0
