# Install Atlas background daemon as a Windows Scheduled Task (runs at logon).
# Run from an elevated PowerShell in the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\install_atlas_daemon.ps1

param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = "AtlasDaemon"
)

$ErrorActionPreference = "Stop"

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Error "python not found on PATH"
}

$daemonScript = Join-Path $ProjectRoot "atlas_daemon.py"
if (-not (Test-Path $daemonScript)) {
    Write-Error "atlas_daemon.py not found at $daemonScript"
}

$dataDir = Join-Path $env:LOCALAPPDATA "Atlas"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$logFile = Join-Path $dataDir "daemon.log"

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument "-m atlas_daemon" `
    -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Atlas background daemon (scheduler, connectors, state)" `
    -Force | Out-Null

Write-Host "Installed scheduled task '$TaskName'."
Write-Host "  Python:  $python"
Write-Host "  Workdir: $ProjectRoot"
Write-Host "  Log:     $logFile (redirect manually if needed)"
Write-Host ""
Write-Host "Start now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "Uninstall:  powershell -ExecutionPolicy Bypass -File scripts\uninstall_atlas_daemon.ps1"
