# Atlas Electron + React shell (Phase 7)
# Run from repo root after: cd atlas_shell && npm install

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ShellDir = Join-Path $Root "atlas_shell"

if (-not (Test-Path (Join-Path $ShellDir "node_modules"))) {
    Write-Host "Installing atlas_shell dependencies..."
    Push-Location $ShellDir
    npm install
    Pop-Location
}

Write-Host "Ensure atlas_daemon is running: python -m atlas_daemon"
Push-Location $ShellDir
npm run dev
Pop-Location
