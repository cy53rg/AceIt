# Atlas Windows installer (personal / beta)
# Run from an elevated PowerShell if you need machine-wide Python paths.

param(
    [switch]$SkipShell
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Atlas install — $Root"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.11+ and re-run."
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPython -m pip install -U pip
& $venvPython -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example — add your GROQ_API_KEY."
}

Write-Host "Running pytest..."
& $venvPython -m pytest -q
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Some tests failed. Fix before daily use."
}

if (-not $SkipShell) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Host "Installing Electron shell dependencies..."
        Push-Location (Join-Path $Root "atlas_shell")
        npm install
        Pop-Location
    } else {
        Write-Host "npm not found — skipping atlas_shell (PySide6 UI still works)."
    }
}

Write-Host ""
Write-Host "Done. Start Atlas:"
Write-Host "  .\.venv\Scripts\python.exe -m atlas_daemon"
Write-Host "  .\.venv\Scripts\python.exe atlas_ui.py"
Write-Host "  powershell -File scripts\start_atlas_shell.ps1   # optional Electron shell"
