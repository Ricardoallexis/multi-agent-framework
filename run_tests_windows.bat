@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo ERROR: Run setup_windows.bat first.
  exit /b 1
)
rem Same order as CI: consistency checks first, then the test suite.
.venv\Scripts\python.exe scripts\check_version_consistency.py || exit /b 1
.venv\Scripts\python.exe scripts\update_progress.py --check || exit /b 1
.venv\Scripts\python.exe -m pytest -q
