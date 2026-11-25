// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of opening a session in its app (Claude, Codex, Copilot).
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
  "open.claude": {
    "title": "Abrir en Claude",
    "hint": "Abre esta sesión en la app de escritorio de Claude.",
    "more": [
      "Lanza una consola nueva de Windows con «claude --desktop --resume» y el identificador de la sesión; antes te pide confirmar.",
      "La consola se abre en tu carpeta personal, nunca en la de la sesión, por seguridad.",
      "Claude muestra lo nuevo solo después de cerrar y volver a abrir la app.",
      "Si el lanzamiento está desactivado (opción gui_allow_launch) o no usas Windows, copia el comando y ejecútalo tú."
    ]
  },
  "open.codex": {
    "title": "Abrir en Codex",
    "hint": "Abre este hilo en la app de Codex mediante un enlace.",
    "more": [
      "El botón es un enlace que abre la app de Codex ya instalada; no lanza ningún programa desde aquí.",
      "Si hace falta, Codex pide confirmación antes de abrir la sesión.",
      "Mientras la app tiene el hilo abierto bloquea las escrituras, pero este visor solo lee y no le afecta."
    ]
  },

  "open.copilot": {
    "title": "Abrir en Copilot",
    "hint": "Abre esta sesión en la app de Copilot mediante un enlace.",
    "more": [
      "El botón es un enlace que abre la app de Copilot ya instalada; no lanza ningún programa desde aquí.",
      "Si hace falta, Copilot pide confirmación antes de abrir la sesión.",
      "Copilot muestra lo nuevo de la sesión solo después de cerrar y volver a abrir la app."
    ]
  },
  "open.copy": {
    "title": "Copiar comando",
    "hint": "Copia el comando para ejecutarlo tú en una terminal.",
    "more": [
      "Se usa cuando el visor no puede lanzar la app por sí mismo.",
      "Ejecútalo dentro de la carpeta que se indica junto al comando, si aparece."
    ]
  }
};
