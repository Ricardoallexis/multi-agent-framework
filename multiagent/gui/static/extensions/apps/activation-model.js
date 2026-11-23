// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model of «Activar» for the executor cards of «Apps y agentes» (EJE-T02): the five states, the fixed note and the
// texts of the answers. No DOM and no network. Everything here is plain text; the note of the server is never HTML.

export const STATES = ["not_installed", "no_session", "ready", "active", "paused"];

export const STATE_LABELS = {
  not_installed: "No instalado",
  no_session: "Instalado, sin sesión",
  ready: "Listo para activar",
  active: "Activo",
  paused: "En pausa",
};

// The names the server uses in the note (``services/executor_activations.py``).
export const EXECUTOR_NAMES = {
  codex_cli: "Codex",
  claude_cli: "Claude",
  copilot_cli: "Copilot",
  antigravity_cli: "Antigravity (agy)",
};

export const STOP_ALL_CONFIRM = "Detiene lo que esté corriendo y pone todos en pausa";
export const STOP_ALL_PAUSED_TEXT = "En pausa: la apagaste con \"Apagar todos\"";
export const FOREIGN_KILL_SWITCH_TEXT =
  "agy está apagado por un archivo que no puso la app; quítalo tú para activarlo";
export const MANUAL_ENABLE_TEXT = "También puedes encenderlo a mano en catalog/models.yaml";

export function executorName(executorId) {
  return EXECUTOR_NAMES[executorId] || String(executorId || "el ejecutor");
}

const FIELD_TEXT = {
  path: (change) => "Cambió la ruta de la aplicación",
  major: (change) => `Cambió la versión ${change.from} → ${change.to}`,
  account: (change) => "Cambió la cuenta",
};

// «Cambió la versión 2 → 3»; several changes are joined with « · ».
export function changesText(changes) {
  const list = Array.isArray(changes) ? changes : [];
  const parts = [];
  for (const change of list) {
    if (!change || typeof change !== "object") continue;
    const make = FIELD_TEXT[change.field];
    if (make) parts.push(make(change));
  }
  return parts.join(" · ");
}

// «2026-11-22T03:03:00+00:00» -> «2026-11-22 03:03»: no locale and no timezone maths in a pure module.
function shortDate(value) {
  const match = typeof value === "string" ? /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value) : null;
  return match ? `${match[1]} ${match[2]}` : null;
}

// The state of an executor when the server did not answer «activations»: what the connection row already says.
function stateFromConnection(row) {
  if (!row) return "not_installed";
  if (row.installed === false || row.reason === "not_installed" || row.missing_step === "install") return "not_installed";
  if (row.missing_step === "session" || row.session === "signed_out") return "no_session";
  return "ready";
}

// -> {state, label, actions, detail}. ``row`` is the connection row, ``activation`` the row of
// GET /gui/api/executors/activations (or null). ``actions`` are among "activate", "deactivate", "review" and "login".
export function activationView(row, activation) {
  const known = activation && STATES.includes(activation.state);
  const state = known ? activation.state : stateFromConnection(row);
  const label = STATE_LABELS[state];
  const foreign = Boolean(activation && activation.kill_switch_foreign);
  let actions = [];
  let detail = "";
  if (state === "no_session") {
    actions = ["login"];
  } else if (state === "ready") {
    actions = ["activate"];
  } else if (state === "active") {
    actions = ["deactivate"];
    const when = shortDate(activation && activation.activated_at);
    detail = when ? `Activado por ti el ${when}` : "Activado por ti";
  } else if (state === "paused") {
    actions = ["review"];
    const paused = activation && activation.paused;
    if (paused && paused.reason === "stopped_by_person") {
      detail = STOP_ALL_PAUSED_TEXT;
    } else {
      const changes = changesText((paused && paused.changes) || (activation && activation.changes));
      detail = changes || "En pausa: la aplicación cambió desde que la activaste";
    }
  }
  if (foreign && (state === "ready" || state === "paused")) {
    // A switch a person made by hand: «Activar» would not start anything, so it is not offered.
    actions = actions.filter((action) => action !== "activate" && action !== "review");
    detail = detail ? `${detail}. ${FOREIGN_KILL_SWITCH_TEXT}` : FOREIGN_KILL_SWITCH_TEXT;
  }
  return { state, label, actions, detail, foreignKillSwitch: foreign };
}

// The fixed note of the server, as text, and the button that accepts it: «Activar Codex».
export function activationNote(payload, executorId) {
  const name = executorName(executorId);
  const raw = payload && typeof payload === "object" ? (payload.text ?? payload.note) : "";
  const text = typeof raw === "string" ? raw : "";
  return { title: `Activar ${name}`, text, lines: text.split("\n").filter((line) => line.trim() !== ""), confirmLabel: `Activar ${name}` };
}

export function stopAllText(result) {
  const stopped = Array.isArray(result?.stopped_tasks) ? result.stopped_tasks.length : 0;
  const paused = Array.isArray(result?.paused) ? result.paused.length : 0;
  const parts = [
    stopped === 1 ? "Se detuvo 1 tarea" : `Se detuvieron ${stopped} tareas`,
    paused === 1 ? "1 ejecutor quedó en pausa" : `${paused} ejecutores quedaron en pausa`,
  ];
  let text = parts.join("; ") + ".";
  if (result?.kill_switch === "foreign") {
    text += " El archivo de apagado de agy no lo puso la app; no se tocó.";
  } else if (result?.kill_switch === "failed") {
    text += " No se pudo escribir el apagado de agy.";
  }
  return text;
}
