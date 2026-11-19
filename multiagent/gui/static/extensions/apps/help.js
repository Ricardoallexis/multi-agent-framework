// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of «Apps y agentes» (INS-T05): strict JSON under "apps." (see js/help-text.js).

export const HELP = {
  "apps.view": {
    "title": "Apps y agentes",
    "hint": "Estado de cada modelo y ejecutor, y solo el paso que falta.",
    "more": [
      "Muestra si cada herramienta está instalada, con sesión o clave, habilitada y sus plantillas de permisos.",
      "Nunca pide ni escribe claves secretas ni habilita ejecutores por ti."
    ]
  },
  "apps.refresh": {
    "title": "Actualizar lista",
    "hint": "Vuelve a leer el estado guardado de conexiones y plantillas.",
    "more": [
      "Esta lectura no gasta cuota de modelos ni abre consolas.",
      "Para volver a medir una herramienta concreta usa su botón de comprobar."
    ]
  },
  "apps.executors": {
    "title": "Apps y ejecutores",
    "hint": "Aplicaciones de línea de comandos y estado de sus plantillas.",
    "more": [
      "Incluye Claude, Codex, Copilot y Antigravity (agy) con su versión, sesión y permisos.",
      "Solo muestra la acción del primer paso que falte en cada app."
    ]
  },
  "apps.models": {
    "title": "Modelos",
    "hint": "Proveedores Gemini, OpenAI y Ollama con el mismo esquema.",
    "more": [
      "Para Gemini y OpenAI indica el nombre del ajuste en el archivo local, nunca el valor de la clave.",
      "Para Ollama muestra si el servicio local responde y su versión."
    ]
  },
  "apps.recheck": {
    "title": "Volver a comprobar",
    "hint": "Vuelve a medir el estado actual de esta herramienta.",
    "more": [
      "Comprueba instalación, sesión, respuesta local o cuota sin generar con ningún modelo.",
      "Úsalo después de instalar, iniciar sesión o reiniciar tras cambiar la configuración."
    ]
  },
  "apps.open_console": {
    "title": "Abrir consola",
    "hint": "Abre una consola visible en este equipo con el comando oficial.",
    "more": [
      "Pide confirmación antes de abrir y solo permite una consola a la vez.",
      "Dentro de la consola debes pulsar Enter para ejecutar el comando; el servidor nunca corre instalaciones ocultas."
    ]
  },
  "apps.copy": {
    "title": "Copiar líneas",
    "hint": "Copia estas líneas al portapapeles para pegarlas tú mismo.",
    "more": [
      "El servidor nunca edita el catálogo ni el archivo local por su cuenta.",
      "Pégalas donde corresponda si decides habilitar esta herramienta, luego reinicia la aplicación."
    ]
  }
};
