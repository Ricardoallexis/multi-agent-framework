@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo ERROR: Run setup_windows.bat first.
  exit /b 1
)
echo ================================================
echo  Multi-Agent Framework - Backend DEBUG
echo ================================================
echo WARNING: Saving source files causes Uvicorn to restart the process.
echo Runs persist under .local, but avoid editing during a real inference request.
echo.
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
