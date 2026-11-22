$ErrorActionPreference = 'Stop'
$root = 'K:\CONFI\comfy_mcp'
$target = Join-Path $root 'launcher\Aster启动器.bat'
if (-not (Test-Path $target)) { throw "target not found: $target" }
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Aster启动器.lnk'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = $target
$sc.WorkingDirectory = $root
$sc.IconLocation = 'C:\WINDOWS\System32\imageres.dll,109'
$sc.Description = 'Aster启动器 - ComfyUI 本地工作台'
$sc.Save()
Write-Host "shortcut created: $lnk"
