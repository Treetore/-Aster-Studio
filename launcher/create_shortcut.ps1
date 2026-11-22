$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $PSScriptRoot 'AsterLauncher.bat'
if (-not (Test-Path $target)) { throw "target not found: $target" }
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Aster Studio.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $target
$sc.WorkingDirectory = $root
$sc.IconLocation = 'C:\WINDOWS\System32\imageres.dll,109'
$sc.Description = 'Aster Studio'
$sc.Save()
Write-Host "shortcut created: $lnk"
