// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of «Apps y agentes» (INS-T05): strict JSON under "apps." (see js/help-text.js).

export const HELP = {
  "apps.view": {
    "title": "Apps y agentes",
    "hint": "Estado de cada modelo y ejecutor, y solo el paso que falta.",
    "more": [
      "Muestra si cada herramienta está instalada, con sesión o clave, habilitada y sus plantillas de permisos.",
      "Las filas con un paso pendiente empiezan abiertas y las demás se despliegan al pulsarlas.",
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
      "Dentro de la consola debes pulsar Enter para ejecutar el comando; el servidor nunca corre instalaciones ocultas.",
      "La página no se queda esperando: dice «Consola abierta…» y consulta el estado cada pocos segundos. Al cerrar la consola, vuelve a medir y actualiza la fila."
    ]
  },
  "apps.probe": {
    "title": "Probar conexión",
    "hint": "Hace una consulta que no genera texto para ver si el proveedor responde.",
    "more": [
      "Pide la lista de modelos de Gemini u OpenAI, o la salud de Ollama. No gasta cuota de generación.",
      "Solo corre cuando la pulsas: «Volver a comprobar» no sale a internet para Gemini ni OpenAI.",
      "Si falla por el certificado, la tarjeta ofrece el botón de certificados del sistema."
    ]
  },
  "apps.tls_fix": {
    "title": "Certificados de los proveedores",
    "hint": "Elige qué certificados usan Gemini, OpenAI y Ollama para verificar la conexión.",
    "more": [
      "Un antivirus o un proxy que inspecciona TLS hace fallar la verificación. Con los certificados del sistema, la conexión usa el almacén de este equipo.",
      "Es una decisión tuya y queda registrada. Vale para todos los proveedores; no cambia a los ejecutores.",
      "«Volver a los certificados de siempre» deja los que traen los clientes. Elegir un archivo .pem propio es del modo avanzado, en .local/.env."
    ]
  },
  "apps.advanced_step": {
    "title": "Pasos del modo avanzado",
    "hint": "Este paso edita un archivo de configuración, y eso se hace en modo avanzado.",
    "more": [
      "Habilitar un ejecutor o un modelo y poner una clave de API son cambios en archivos de este equipo.",
      "Activa el modo avanzado en «Personalizar» para ver las líneas exactas. El visor nunca edita esos archivos por ti."
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
