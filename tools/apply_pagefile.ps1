<#
  需要管理员权限：把 C:\pagefile.sys 设为固定 16GB-32GB，并关闭“自动管理页文件大小”。
  修改写入注册表，重启后生效。
  本脚本由 run_pagefile_admin.bat 或手动提权调用。
#>
param([int]$InitialMB = 16384, [int]$MaxMB = 32768, [string]$PageFile = 'C:\pagefile.sys')

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '[ERR] 需要管理员权限' -ForegroundColor Red
    exit 1
}
$mm = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management'

Write-Host ('[1] 当前 PagingFiles: ' + ((Get-ItemProperty $mm -ErrorAction SilentlyContinue).PagingFiles -join '; '))
Write-Host ('[2] 当前自动管理: ' + (Get-CimInstance Win32_ComputerSystem).AutomaticManagedPagefile)

# 关闭“自动管理”（若开启）
try {
    $cs = Get-CimInstance Win32_ComputerSystem
    if ($cs.AutomaticManagedPagefile) {
        Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false }
        Write-Host '[3] 已关闭自动管理页文件' -ForegroundColor Yellow
    } else {
        Write-Host '[3] 自动管理本来就是关闭状态'
    }
} catch {
    Write-Host ('[3] CIM 关闭自动管理失败（将仅写注册表）: ' + $_.Exception.Message) -ForegroundColor Yellow
}

# 写注册表 PagingFiles
$target = "$PageFile $InitialMB $MaxMB"
Set-ItemProperty -Path $mm -Name PagingFiles -Value @($target) -Type MultiString
Write-Host ('[4] 已写入注册表 PagingFiles = ' + $target) -ForegroundColor Green

# 校验
$after = (Get-ItemProperty $mm).PagingFiles
Write-Host ('[5] 校验 PagingFiles: ' + ($after -join '; '))
Write-Host ''
Write-Host '修改已写入，重启系统后生效。' -ForegroundColor Yellow
Write-Host '重启后确认：Get-CimInstance Win32_PageFileUsage | Select Name,AllocatedBaseSize'
exit 0
