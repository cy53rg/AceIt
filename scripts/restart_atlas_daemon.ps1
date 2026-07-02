# Free port 17847 and start atlas_daemon.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\restart_atlas_daemon.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Port = if ($env:ATLAS_DAEMON_PORT) { $env:ATLAS_DAEMON_PORT } else { "17847" }

$lines = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
foreach ($line in $lines) {
    $procId = ($line -split '\s+')[-1]
    if ($procId -match '^\d+$') {
        Write-Host "Stopping PID $procId on port $Port..."
        taskkill /PID $procId /F | Out-Null
    }
}

Start-Sleep -Seconds 2

$still = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
if ($still) {
    Write-Error "Port $Port is still in use. Close other Atlas/python processes and retry."
}

Set-Location $ProjectRoot
Write-Host "Starting atlas_daemon on 127.0.0.1:$Port (leave this window open)..."
python -m atlas_daemon
