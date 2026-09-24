@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ================================================
echo  Multi-Agent Framework - Windows Setup
echo ================================================

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: The Python "py" launcher was not found.
  echo Install Python 3.12 x64 from python.org and run this script again.
  exit /b 1
)

py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 (
  set "PY_CMD=py -3"
  echo Python 3.12 was not detected; the default compatible Python 3 version will be used.
) else (
  set "PY_CMD=py -3.12"
)

if not exist .venv (
  echo [1/7] Creating virtual environment...
  %PY_CMD% -m venv .venv
  if errorlevel 1 exit /b 1
) else (
  echo [1/7] Virtual environment already exists.
)

echo [2/7] Updating pip...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b 1

echo [3/7] Installing dependencies, tests, and build tools...
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1

echo [4/7] Installing Multi-Agent Framework in editable mode...
.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
if errorlevel 1 exit /b 1

if not exist .local mkdir .local
if not exist .local\.env (
  echo [5/7] Creating .local\.env from .env.example...
  copy /Y .env.example .local\.env >nul
) else (
  echo [5/7] .local\.env already exists; leaving it unchanged.
)

echo [6/7] Initializing or migrating the local database...
.venv\Scripts\multiagent.exe init
if errorlevel 1 exit /b 1

echo [7/7] Running initial diagnostics...
.venv\Scripts\multiagent.exe doctor
if errorlevel 1 exit /b 1

echo.
echo ================================================
echo Setup completed.
echo ================================================
echo 1. Configure .local\.env if you need cloud APIs.
echo 2. If you use Ollama, run configure_ollama_windows.bat.
echo 3. Run initial checks with first_tests_windows.bat.
echo 4. Start the backend with run_backend.bat.
echo.
exit /b 0
