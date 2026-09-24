@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist .venv\Scripts\multiagent.exe (
  echo ERROR: The multiagent command is not installed. Run setup_windows.bat first.
  exit /b 1
)
echo ================================================
echo  Multi-Agent Framework - Initial checks
echo ================================================
.venv\Scripts\python.exe -m compileall -q multiagent tests
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 exit /b 1
.venv\Scripts\multiagent.exe --version
.venv\Scripts\multiagent.exe doctor
if errorlevel 1 exit /b 1
.venv\Scripts\multiagent.exe dry-run --project "Example Quick" --objective "Create an educational test post" --topic "home automation" --platform Instagram
if errorlevel 1 exit /b 1
.venv\Scripts\multiagent.exe dry-run --project "Example Full" --objective "Create test content and a visual brief" --topic "interoperability" --platform LinkedIn --pipeline full
if errorlevel 1 exit /b 1
echo Initial checks completed.
