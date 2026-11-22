<#
  停止本项目启动的 Web UI（以及可选的 ComfyUI）。
  用法: powershell -ExecutionPolicy Bypass -File .\stop_all.ps1 [-Comfy]
  说明: 项目根 = $PSScriptRoot，无写死盘符；ComfyUI 进程按 main.py 命令行匹配。
#>
param(
    [switch]$Comfy,
    [switch]$Launcher,
    [int]$Port = 8190
)

$ErrorActionPreference = 'SilentlyContinue'
$Root = $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$Root = (Resolve-Path -LiteralPath $Root).Path

function Stop-Matching([string]$Label, [string[]]$Patterns, [switch]$Tree) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $cmd = $_.CommandLine
            if (-not $cmd) { return $false }
            foreach ($p in $Patterns) { if ($cmd -like $p) { return $true } }
            return $false
        }
    foreach ($p in $procs) {
        Write-Host "stop $Label pid $($p.ProcessId)"
        if ($Tree) { taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Null }
        else { taskkill /PID $p.ProcessId /F 2>&1 | Out-Null }
    }
}

# Web UI / 旧版监控进程（与启动脚本对应）
Stop-Matching 'web/ui' @('*uvicorn*webapp.app*', '*monitor_server.py*')

# 本项目的桌面启动器后端（可选，默认不动）
if ($Launcher) {
    Stop-Matching 'launcher' @('*launcher.app*', '*launcher\run.py*', '*launcher/run.py*')
}

if ($Comfy) {
    Stop-Matching 'comfyui' @('*ComfyUI\main.py*', '*ComfyUI/main.py*') -Tree
}

Write-Host 'done'
