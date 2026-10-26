@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Chua co .venv. Hay tao moi: python -m venv .venv
    pause
    exit /b 1
)

.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
pause
