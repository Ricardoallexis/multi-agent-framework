#!/bin/sh
# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
#
# Doble clic (o `sh iniciar.sh`): prepara el entorno la primera vez y abre la interfaz.
# Siempre usa `python -m ...`: nunca un ejecutable generado por pip. Solo toca esta carpeta y la carpeta local.

cd "$(dirname "$0")" || exit 1

DOWNLOAD_URL="https://www.python.org/downloads/"
VENV_PY=".venv/bin/python"
MARK=".venv/.maf-ready"
LOCAL_DIR_USED="${LOCAL_DIR:-.local}"
START_FILE="$LOCAL_DIR_USED/primer-recorrido-inicio.txt"
export PIP_DISABLE_PIP_VERSION_CHECK=1

wait_key() {
    printf 'Pulsa Enter para cerrar esta ventana. '
    read -r _ || true
}

fail() {
    echo "No se pudo completar: $1"
    wait_key
    exit 1
}

sha_of_requirements() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum requirements.txt | cut -d ' ' -f 1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 requirements.txt | cut -d ' ' -f 1
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 requirements.txt | sed 's/^.*= *//'
    else
        return 1
    fi
}

find_python() {
    for candidate in python3.14 python3.13 python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1 &&
            "$candidate" -c 'import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)' >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

no_python() {
    echo "No encontré Python en este equipo."
    echo "Voy a abrir la página de descargas: $DOWNLOAD_URL"
    echo "Descarga «Python 3.13» (la versión estable más reciente entre 3.12 y 3.14)."
    echo "Instálalo y vuelve a abrir este archivo. No se creó ni se cambió nada."
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
        open "$DOWNLOAD_URL" >/dev/null 2>&1 || echo "Abre esta dirección en el navegador: $DOWNLOAD_URL"
    else
        xdg-open "$DOWNLOAD_URL" >/dev/null 2>&1 || echo "Abre esta dirección en el navegador: $DOWNLOAD_URL"
    fi
    wait_key
    exit 1
}

echo "Multi-Agent Framework: inicio"

if [ ! -f "$VENV_PY" ]; then
    SYSTEM_PY="$(find_python)" || no_python
fi

# 1. La hora de inicio, una sola vez (no se pisa).
if [ ! -f "$START_FILE" ]; then
    mkdir -p "$LOCAL_DIR_USED" || fail "no pude crear la carpeta local ($LOCAL_DIR_USED)."
    date +%Y-%m-%dT%H:%M:%S%z | sed 's/\([0-9][0-9]\)$/:\1/' >"$START_FILE" ||
        fail "no pude anotar la hora de inicio."
fi

# 3. El entorno.
if [ ! -f "$VENV_PY" ]; then
    echo "[1/4] Creando el entorno (solo la primera vez)..."
    "$SYSTEM_PY" -m venv .venv || fail "paso 1 de 4, crear el entorno (.venv)."
    [ -f "$VENV_PY" ] || fail "paso 1 de 4, el entorno no quedó listo."
fi

# 4 a 6. Dependencias, base de datos y marca.
WANTED="$(sha_of_requirements)" || fail "no pude calcular la huella de requirements.txt."
CURRENT=""
[ -f "$MARK" ] && CURRENT="$(cat "$MARK")"
if [ "$CURRENT" != "$WANTED" ]; then
    echo "[2/4] Instalando las dependencias (puede tardar unos minutos)..."
    "$VENV_PY" -m pip install -r requirements.txt || fail "paso 2 de 4, instalar las dependencias."
    echo "[3/4] Preparando la base de datos..."
    "$VENV_PY" -m multiagent init || fail "paso 3 de 4, preparar la base de datos."
    printf '%s\n' "$WANTED" >"$MARK" || fail "no pude guardar la marca de instalación."
fi

# 7. La interfaz.
echo "[4/4] Abriendo la interfaz. Para cerrarla, cierra esta ventana o pulsa Ctrl+C."
"$VENV_PY" -m multiagent gui --launcher || fail "paso 4 de 4, abrir la interfaz."
