param(
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'start',
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
$exe = 'C:\Tools\cloudflared\cloudflared.exe'
$data = 'K:\CONFI\comfy_mcp\data'
$logDir = 'K:\CONFI\comfy_mcp\logs'
$cfgFile = Join-Path $data 'tunnel.json'
$pidFile = Join-Path $data 'tunnel.pid'
$urlFile = Join-Path $data 'tunnel_url.txt'
$logFile = Join-Path $logDir 'cloudflared_tunnel.log'

function Read-Cfg {
    $cfg = @{ mode = 'quick'; token = ''; domain = ''; local_port = 8190 }
    if (Test-Path $cfgFile) {
        try {
            $raw = Get-Content $cfgFile -Raw | ConvertFrom-Json
            foreach ($k in 'mode', 'token', 'domain', 'local_port') {
                if ($null -ne $raw.$k -and "$($raw.$k)" -ne '') { $cfg[$k] = $raw.$k }
            }
        } catch { }
    }
    return $cfg
}

function Get-TunnelPid {
    if (Test-Path $pidFile) {
        $text = (Get-Content $pidFile -Raw).Trim()
        if ($text -match '^\d+$') {
            $p = Get-Process -Id ([int]$text) -ErrorAction SilentlyContinue
            if ($p -and $p.ProcessName -like 'cloudflared*') { return [int]$text }
        }
    }
    return 0
}

function Get-TunnelUrl {
    if (Test-Path $urlFile) {
        $u = (Get-Content $urlFile -Raw).Trim()
        if ($u) { return $u }
    }
    return ''
}

function Out-Status {
    $cfg = Read-Cfg
    $pidValue = Get-TunnelPid
    $url = Get-TunnelUrl
    $state = @{
        running = ($pidValue -gt 0)
        pid     = $pidValue
        url     = $url
        mode    = $cfg.mode
        domain  = $cfg.domain
    }
    if ($Json) { $state | ConvertTo-Json -Compress }
    else {
        if ($pidValue -gt 0) { Write-Output "running pid=$pidValue mode=$($cfg.mode) url=$url" }
        else { Write-Output 'stopped' }
    }
}

if ($Action -eq 'restart') { $Action = 'stop'; & $PSCommandPath -Action stop | Out-Null; $Action = 'start' }

switch ($Action) {
    'status' { Out-Status; break }
    'stop' {
        $pidValue = Get-TunnelPid
        if ($pidValue -gt 0) { Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue }
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        Remove-Item $urlFile -Force -ErrorAction SilentlyContinue
        if ($Json) { @{ running = $false; pid = 0; url = '' } | ConvertTo-Json -Compress } else { 'stopped' }
        break
    }
    'start' {
        if (-not (Test-Path $exe)) { throw "cloudflared not found: $exe" }
        $cfg = Read-Cfg
        $existing = Get-TunnelPid
        if ($existing -gt 0) { Out-Status; break }
        Remove-Item $logFile -Force -ErrorAction SilentlyContinue
        Remove-Item $urlFile -Force -ErrorAction SilentlyContinue
        if ($cfg.mode -eq 'named') {
            $cfgy = Join-Path $env:USERPROFILE '.cloudflared\config.yml'
            if (-not (Test-Path $cfgy)) { throw "config.yml not found: $cfgy" }
            $argsList = @('--no-autoupdate', '--protocol', 'http2', '--config', $cfgy,
                          '--logfile', $logFile, '--loglevel', 'info', 'tunnel', 'run')
        } elseif ($cfg.mode -eq 'token') {
            if (-not $cfg.token) { throw 'tunnel.json mode=token but token is empty' }
            $argsList = @('--no-autoupdate', '--protocol', 'http2', '--logfile', $logFile, '--loglevel', 'info', 'tunnel', 'run', '--token', $cfg.token)
        } else {
            $quickCfg = Join-Path $data 'cloudflared_quick.yml'
            $argsList = @('--no-autoupdate', '--protocol', 'http2', '--config', $quickCfg,
                          '--logfile', $logFile, '--loglevel', 'info', 'tunnel', '--url', "http://127.0.0.1:$($cfg.local_port)")
        }
        $proc = Start-Process -FilePath $exe -ArgumentList $argsList -WindowStyle Hidden -PassThru
        Set-Content -Path $pidFile -Value $proc.Id -Encoding ASCII
        $url = ''
        if ($cfg.mode -eq 'token' -or $cfg.mode -eq 'named') {
            if ($cfg.domain) { $url = if ($cfg.domain -match '^https?://') { $cfg.domain } else { "https://$($cfg.domain)" } }
            Start-Sleep -Seconds 4
        } else {
            for ($i = 0; $i -lt 40; $i++) {
                Start-Sleep -Seconds 1
                if (Test-Path $logFile) {
                    $m = Select-String -Path $logFile -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue |
                         Select-Object -First 1
                    if ($m) {
                        $url = [regex]::Match($m.Line, 'https://[a-z0-9-]+\.trycloudflare\.com').Value
                        if ($url) { break }
                    }
                }
                if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { break }
            }
        }
        if ($url) { Set-Content -Path $urlFile -Value $url -Encoding ASCII }
        if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) { throw 'cloudflared exited immediately; check logs' }
        Out-Status
        break
    }
}
