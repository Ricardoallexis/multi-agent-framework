@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist .venv\Scripts\multiagent.exe (
  echo ERROR: Run setup_windows.bat first.
  exit /b 1
)
echo ================================================
echo  Multi-Agent Framework - Real Ollama test
echo ================================================
.venv\Scripts\multiagent.exe doctor --deep
