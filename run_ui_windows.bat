@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo ERROR: Run setup_windows.bat first.
  exit /b 1
)
rem Starts the local operator GUI (/gui/) in Mock mode and opens the browser.
rem Extra options pass through, for example: run_ui_windows.bat --real   or   --port 8010
.venv\Scripts\python.exe -m multiagent gui %*
