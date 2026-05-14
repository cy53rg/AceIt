# Creates a virtualenv with Python 3.12+ and installs AceIt dependencies.
# You still need Tesseract OCR installed on Windows — see INSTALL_WINDOWS.txt.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = $null
foreach ($ver in @("3.12", "3.13", "3.11")) {
    try {
        $cand = & py "-$ver" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $cand) { $py = $cand.Trim(); break }
    } catch { }
}

if (-not $py) {
    Write-Host "Could not find Python 3.12 (or 3.13 / 3.11) via the 'py' launcher."
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/ and tick 'py launcher'."
    exit 1
}

Write-Host "Using: $py"
& $py -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -U pip
& .\.venv\Scripts\pip.exe install -r requirements.txt
Write-Host ""
Write-Host "Done. Activate then run:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python main.py"
