@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ================================================
echo  Multi-Agent Framework - Windows Setup
echo ================================================

rem Supported Python versions, in order of preference, separated by spaces.
rem Keep this list in sync with requires-python in pyproject.toml.
set "SUPPORTED_PYTHON=3.12 3.13 3.14"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: The Python "py" launcher was not found.
  echo Install Python 3.12 x64 from python.org and run this script again.
  exit /b 1
)

set "PY_CMD="
set "PY_VERSION="
for %%V in (%SUPPORTED_PYTHON%) do (
  if not defined PY_CMD (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set "PY_CMD=py -%%V"
      set "PY_VERSION=%%V"
    )
  )
)

if not defined PY_CMD (
  echo ERROR: No supported Python was found.
  echo This project needs Python %SUPPORTED_PYTHON: = or % ^(see requires-python in pyproject.toml^).
  echo Newer versions, such as the default "py -3" on some machines, are not supported yet.
  echo Install Python 3.12 x64 from python.org, then run this script again.
  echo Nothing was created or changed.
  exit /b 1
)

if not exist .venv (
  echo [1/7] Creating virtual environment with Python %PY_VERSION%...
  %PY_CMD% -m venv .venv
  if errorlevel 1 exit /b 1
) else (
  echo [1/7] Virtual environment already exists.
  set "VENV_VERSION="
  for /f "usebackq tokens=1,2" %%A in (`.venv\Scripts\python.exe -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2^>nul`) do set "VENV_VERSION=%%A.%%B"
  set "VENV_OK="
  for %%S in (%SUPPORTED_PYTHON%) do if "%%S"=="!VENV_VERSION!" set "VENV_OK=1"
  if not defined VENV_OK (
    echo ERROR: The existing .venv uses an unsupported Python ^(!VENV_VERSION!^), or it cannot be run.
    echo It was left untouched. To recreate it with Python %PY_VERSION%, delete the .venv folder and run this script again.
    exit /b 1
  )
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
