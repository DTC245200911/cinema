Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Error "Chua co .venv. Hay tao moi: python -m venv .venv"
    exit 1
}

.\.venv\Scripts\python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --reload
