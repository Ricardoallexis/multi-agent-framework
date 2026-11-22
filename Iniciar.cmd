@echo off
rem Copyright 2026 Ricardoallexis and contributors
rem SPDX-License-Identifier: Apache-2.0
rem
rem Doble clic: prepara el entorno la primera vez y abre la interfaz.
rem Siempre usa "python -m ...": nunca un ejecutable generado por pip. Solo toca esta carpeta y la carpeta local.
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "DOWNLOAD_URL=https://www.python.org/downloads/"
set "VENV_PY=.venv\Scripts\python"
set "MARK=.venv\.maf-ready"
set "LOCAL_DIR_USED=.local"
if defined LOCAL_DIR set "LOCAL_DIR_USED=%LOCAL_DIR%"
set "START_FILE=%LOCAL_DIR_USED%\primer-recorrido-inicio.txt"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PY_CMD="

echo Multi-Agent Framework: inicio

if exist "%VENV_PY%.exe" goto have_python
if exist "%VENV_PY%.cmd" goto have_python

where py >nul 2>nul
if errorlevel 1 goto no_python
for %%V in (3.14 3.13 3.12) do (
  if not defined PY_CMD (
    call py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -%%V"
  )
)
if not defined PY_CMD goto no_python

:have_python
rem 1. La hora de inicio, una sola vez (no se pisa).
if not exist "%START_FILE%" (
  if not exist "%LOCAL_DIR_USED%" mkdir "%LOCAL_DIR_USED%"
  "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "Get-Date -Format o" > "%START_FILE%"
  if errorlevel 1 (
    set "FAIL=no pude anotar la hora de inicio."
    goto fail
  )
)

rem 3. El entorno.
if exist "%VENV_PY%.exe" goto have_venv
if exist "%VENV_PY%.cmd" goto have_venv
echo [1/4] Creando el entorno (solo la primera vez)...
call %PY_CMD% -m venv .venv
if errorlevel 1 (
  set "FAIL=paso 1 de 4, crear el entorno (.venv)."
  goto fail
)
:have_venv

rem 4 a 6. Dependencias, base de datos y marca.
set "WANTED="
for /f "skip=1 delims=" %%H in ('certutil -hashfile requirements.txt SHA256 2^>nul') do if not defined WANTED set "WANTED=%%H"
if not defined WANTED (
  set "FAIL=no pude calcular la huella de requirements.txt."
  goto fail
)
set "WANTED=%WANTED: =%"
set "CURRENT="
if exist "%MARK%" set /p CURRENT=<"%MARK%"
if "%CURRENT%"=="%WANTED%" goto start_gui

echo [2/4] Instalando las dependencias (puede tardar unos minutos)...
call "%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  set "FAIL=paso 2 de 4, instalar las dependencias."
  goto fail
)
echo [3/4] Preparando la base de datos...
call "%VENV_PY%" -m multiagent init
if errorlevel 1 (
  set "FAIL=paso 3 de 4, preparar la base de datos."
  goto fail
)
>"%MARK%" echo %WANTED%
if errorlevel 1 (
  set "FAIL=no pude guardar la marca de instalación."
  goto fail
)

:start_gui
rem 7. La interfaz.
echo [4/4] Abriendo la interfaz. Para cerrarla, cierra esta ventana o pulsa Ctrl+C.
call "%VENV_PY%" -m multiagent gui --launcher
if errorlevel 1 (
  set "FAIL=paso 4 de 4, abrir la interfaz."
  goto fail
)
exit /b 0

:no_python
echo No encontré Python en este equipo.
echo Voy a abrir la página de descargas: %DOWNLOAD_URL%
echo Descarga "Python 3.13" (el instalador de 64 bits) y, al instalar, marca "py launcher".
echo Instálalo y vuelve a abrir este archivo. No se creó ni se cambió nada.
call explorer %DOWNLOAD_URL%
echo.
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
exit /b 1

:fail
echo No se pudo completar: %FAIL%
echo.
echo Pulsa una tecla para cerrar esta ventana.
pause >nul
exit /b 1
