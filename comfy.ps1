# comfy_mcp 命令行包装（不依赖 MCP 客户端，直接调用工具）
# 用法：
#   .\comfy.ps1 status
#   .\comfy.ps1 templates
#   .\comfy.ps1 plan --args '{"template":"minimax_h3_r2v","params":{"prompt":"a cat","profile":"draft"}}'
#   .\comfy.ps1 generate --args '{"template":"minimax_h3_t2v","params":{"prompt":"a cat","profile":"draft"}}'
#   .\comfy.ps1 job --args '{"job_id":"..."}'
#   .\comfy.ps1 edit --args '{"action":"trim","src":"...","start_s":0,"duration_s":2}'
#   .\comfy.ps1 list
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Tool,
    [string]$Args = '{}',
    [ValidateSet('call', 'list')]
    [string]$Mode = 'call'
)

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Py = 'K:\CONFI\mian\ComfyUI_windows_portable\python_embeded\python.exe'
$Server = 'K:\CONFI\comfy_mcp\server.py'

if ($Mode -eq 'list' -or $Tool -eq 'list') {
    & $Py $Server --list-tools
    exit $LASTEXITCODE
}

& $Py $Server --call $Tool --args $Args
exit $LASTEXITCODE
