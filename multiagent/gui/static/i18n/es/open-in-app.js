// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
export default {
  "open.error.launch_disabled": "El visor tiene desactivado el lanzamiento (gui_allow_launch). Copia el comando y ejecútalo tú.",
  "open.error.claude_not_found": "No se encontró el comando «claude» en el PATH. Instala Claude Code o añádelo al PATH y reintenta.",
  "open.error.launch_unsupported": "Abrir la consola solo es posible en Windows. Copia el comando y ejecútalo tú.",
  "open.error.launch_failed": "No se pudo iniciar el comando «claude».",
  "open.error.too_many_launches": "Se acaba de abrir otra sesión. Espera un par de segundos y reintenta.",
  "open.error.unsupported_source": "Esta sesión no es de Claude; se abre con su propio enlace.",
  "open.error.invalid_open_target": "Esta sesión no tiene un identificador válido para abrirla.",
  "open.error.not_found": "La sesión ya no está disponible.",
  "open.note_reopen": "{app} muestra lo nuevo de la sesión solo después de cerrar y volver a abrir la app.",
  "open.copy.instructions": "Ejecuta este comando en una terminal",
  "open.copy.folder": "dentro de la carpeta",
  "open.copy.button": "Copiar comando",
  "open.copy.copied": "Comando copiado.",
  "open.copy.failed": "No se pudo copiar: selecciónalo y cópialo a mano.",
  "open.link.button": "Abrir en {app}",
  "open.link.confirm": "{app} pedirá confirmación antes de abrir la sesión.",
  "open.codex.read_only": "Codex bloquea las escrituras mientras la app tiene el hilo abierto; este visor solo lee, así que no le afecta.",
  "open.claude.disabled": "Abrir la app desde el visor está desactivado o no es posible aquí (opción gui_allow_launch, solo Windows).",
  "open.claude.button": "Abrir en Claude",
  "open.claude.confirm_label": "Confirmar la apertura en Claude",
  "open.claude.confirm_before": "Se abrirá una consola nueva de Windows con",
  "open.claude.confirm_after": ", en tu carpeta personal (nunca en la carpeta de la sesión, por seguridad). ¿Continuar?",
  "open.claude.opening": "Abriendo…",
  "open.claude.open": "Abrir",
  "open.claude.cancel": "Cancelar",
  "open.claude.launched_before": "Se lanzó",
  "open.claude.launched_after": " en una consola nueva (carpeta: tu carpeta personal). Si la app ya estaba abierta, ciérrala y vuelve a abrirla para ver lo nuevo.",
  "open.subagent": "Un subagente no se abre por separado: abre su sesión principal.",
  "open.invalid_session": "Esta sesión no tiene un identificador válido para abrirla en su app.",
  "help.open.claude": {
    "title": "Abrir en Claude",
    "hint": "Abre esta sesión en la app de escritorio de Claude.",
    "more": [
      "Lanza una consola nueva de Windows con «claude --desktop --resume» y el identificador de la sesión; antes te pide confirmar.",
      "La consola se abre en tu carpeta personal, nunca en la de la sesión, por seguridad.",
      "Claude muestra lo nuevo solo después de cerrar y volver a abrir la app.",
      "Si el lanzamiento está desactivado (opción gui_allow_launch) o no usas Windows, copia el comando y ejecútalo tú."
    ]
  },
  "help.open.codex": {
    "title": "Abrir en Codex",
    "hint": "Abre este hilo en la app de Codex mediante un enlace.",
    "more": [
      "El botón es un enlace que abre la app de Codex ya instalada; no lanza ningún programa desde aquí.",
      "Si hace falta, Codex pide confirmación antes de abrir la sesión.",
      "Mientras la app tiene el hilo abierto bloquea las escrituras, pero este visor solo lee y no le afecta."
    ]
  },
  "help.open.copilot": {
    "title": "Abrir en Copilot",
    "hint": "Abre esta sesión en la app de Copilot mediante un enlace.",
    "more": [
      "El botón es un enlace que abre la app de Copilot ya instalada; no lanza ningún programa desde aquí.",
      "Si hace falta, Copilot pide confirmación antes de abrir la sesión.",
      "Copilot muestra lo nuevo de la sesión solo después de cerrar y volver a abrir la app."
    ]
  },
  "help.open.copy": {
    "title": "Copiar comando",
    "hint": "Copia el comando para ejecutarlo tú en una terminal.",
    "more": [
      "Se usa cuando el visor no puede lanzar la app por sí mismo.",
      "Ejecútalo dentro de la carpeta que se indica junto al comando, si aparece."
    ]
  }
};
