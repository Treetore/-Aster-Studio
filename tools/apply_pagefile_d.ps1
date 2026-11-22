<#
  需要管理员权限：把主页文件改到 D 盘，C 盘保留 2GB 供内核崩溃转储。
  - D:\pagefile.sys  初始 16384MB / 最大 32768MB（主导）
  - C:\pagefile.sys  2048MB 固定（崩溃转储用，可选）
  修改写入注册表，重启后生效。
#>
param(
    [int]$DInitialMB = 16384,
    [int]$DMaxMB = 32768,
    [int]$CMB = 2048
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '[ERR] 需要管理员权限' -ForegroundColor Red
    exit 1
}
$mm = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'

Write-Host ('[1] 当前 PagingFiles: ' + ((Get-ItemProperty $mm -ErrorAction SilentlyContinue).PagingFiles -join '; '))
$dFree = (Get-PSDrive D).Free / 1GB
Write-Host ('[2] D 盘空闲: {0:N1} GB' -f $dFree)
if ($dFree -lt ($DInitialMB / 1024.0 + 2)) {
    Write-Host '[ERR] D 盘空间不足，无法创建页文件' -ForegroundColor Red
    exit 2
}

# 关闭自动管理（幂等）
try {
    $cs = Get-CimInstance Win32_ComputerSystem
    if ($cs.AutomaticManagedPagefile) {
        Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false }
    }
} catch { }

$targets = @(
    "C:\pagefile.sys $CMB $CMB",
    "D:\pagefile.sys $DInitialMB $DMaxMB"
)
Set-ItemProperty -Path $mm -Name PagingFiles -Value $targets -Type MultiString
Write-Host ('[3] 已写入: ' + ($targets -join '; ')) -ForegroundColor Green
Write-Host ('[4] 校验: ' + ((Get-ItemProperty $mm).PagingFiles -join '; '))
Write-Host ''
Write-Host '重启后生效：D 盘主页文件 + C 盘 2GB 转储页文件。' -ForegroundColor Yellow
exit 0
