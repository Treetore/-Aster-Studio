<#
  软件层修复：增大 Windows 页文件（pagefile），提高 commit 上限，避免 2026-11-10 式虚拟内存耗尽。
  必须以管理员身份运行（右键 → 使用 PowerShell 运行，或管理员终端执行）。

  用法（管理员 PowerShell）：
    powershell -ExecutionPolicy Bypass -File K:\CONFI\comfy_mcp\tools\set_pagefile_admin.ps1
    powershell -ExecutionPolicy Bypass -File ...set_pagefile_admin.ps1 -InitialMB 16384 -MaxMB 32768

  注意：修改后需重启系统生效。C: 盘需有足够空闲空间（建议 32GB+）。
#>
param(
    [int]$InitialMB = 16384,
    [int]$MaxMB = 32768,
    [string]$Drive = 'C:'
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '[ERR] 需要管理员权限。请右键 PowerShell 选择“以管理员身份运行”，再执行本脚本。' -ForegroundColor Red
    exit 1
}

$cs = Get-CimInstance Win32_ComputerSystem
Write-Host ("当前自动管理页文件: {0}" -f $cs.AutomaticManagedPagefile)
if ($cs.AutomaticManagedPagefile) {
    Set-CimInstance -InputObject $cs -Property @{ AutomaticManagedPagefile = $false }
    Write-Host '已关闭“自动管理页文件大小”。' -ForegroundColor Yellow
}

$name = "$Drive\pagefile.sys"
$pf = Get-CimInstance Win32_PageFileSetting -Filter "Name='$($name.Replace("'","''"))'" -ErrorAction SilentlyContinue
if ($pf) {
    Set-CimInstance -InputObject $pf -Property @{ InitialSize = $InitialMB; MaximumSize = $MaxMB }
    Write-Host ("已更新页文件: {0} -> 初始 {1}MB / 最大 {2}MB" -f $name, $InitialMB, $MaxMB) -ForegroundColor Green
} else {
    New-CimInstance -ClassName Win32_PageFileSetting -Property @{
        Name = $name; InitialSize = $InitialMB; MaximumSize = $MaxMB
    } | Out-Null
    Write-Host ("已创建页文件: {0} -> 初始 {1}MB / 最大 {2}MB" -f $name, $InitialMB, $MaxMB) -ForegroundColor Green
}

Write-Host ''
Write-Host '修改已写入注册表，**需要重启系统后生效**。' -ForegroundColor Yellow
Write-Host '重启后可用以下命令确认：' -ForegroundColor Cyan
Write-Host '  Get-CimInstance Win32_PageFileUsage | Select Name,AllocatedBaseSize,PeakUsage'
exit 0
