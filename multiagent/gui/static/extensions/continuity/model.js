// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers of the continuity screens (CNT-T06): the words of the decision card, the local time with its source, the body
// of a decision and the rows of the policy. No DOM and no Preact in here, so the tests run it under Node.
// The data is what GET /gui/api/continuity/{decisions,session,policy} answers (multiagent/gui/continuity_api.py). Every text
// that comes from the server (a task title, an executor name, a model) is shown as plain text and never as markup.

export const LOCALE = "es";

export const SOURCE_TEXT = {
  app_epoch: "aviso de la app, hora exacta",
  app_text: "aviso de la app",
  rule: "regla del plan",
  configured: "pausa configurada en el framework",
};
export const WINDOW_TEXT = {
  five_hour: "de sesión (5 horas)", weekly: "semanal", model_weekly: "semanal de su modelo", monthly: "mensual",
  spend: "de gasto", unknown: "de uso",
};
export const PATCH_REASON_TEXT = {
  not_git: "la carpeta de trabajo no es un repositorio git", empty: "no había cambios", too_large: "era demasiado grande",
  git_error: "git no pudo leer la carpeta",
};
export const MODE_TEXT = {
  manual: { label: "Manual", explanation: "Siempre te pregunta antes de esperar o de pasar la tarea a otro agente." },
  semi_auto: { label: "Semiautomático", explanation: "Espera solo si el límite se restablece pronto; si no, te propone otro agente y te pregunta." },
  auto: { label: "Automático", explanation: "Decide solo dentro de los límites fijos y te avisa; se activa por proyecto." },
};
export const POLICY_TEXT = { no_ask: "Sin preguntar", ask: "Pregunta antes", never: "Nunca" };

const isDate = (date) => date instanceof Date && !Number.isNaN(date.getTime());
export function toDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(value);
  return isDate(date) ? date : null;
}

const hhmm = (date, locale) => new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
const dayText = (date, locale) => new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short" }).format(date).replace(",", "");
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

// "16:10" today, "mañana 03:40" the next day, "jue 5 nov 03:40" later; null when the value is not a time.
export function clockText(value, now = new Date(), locale = LOCALE) {
  const date = toDate(value);
  if (!date) return null;
  const time = hhmm(date, locale);
  if (sameDay(date, now)) return time;
  const tomorrow = new Date(now.getTime());
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (sameDay(date, tomorrow)) return "mañana " + time;
  return dayText(date, locale) + " " + time;
}

// «a las 16:10», «mañana a las 03:40» or «el jue 5 nov a las 03:40»; null when the value is not a time.
export function whenText(value, now = new Date(), locale = LOCALE) {
  const date = toDate(value);
  if (!date) return null;
  const time = hhmm(date, locale);
  if (sameDay(date, now)) return "a las " + time;
  const tomorrow = new Date(now.getTime());
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (sameDay(date, tomorrow)) return "mañana a las " + time;
  return "el " + dayText(date, locale) + " a las " + time;
}

// The time the card shows as the reset: what the app said, else the moment the wait ends (a short retry has no reset).
export const shownReset = (card) => (card && card.reset ? card.reset.at || card.reset.resume_after || null : null);
export const sourceText = (source) => SOURCE_TEXT[source] || "fuente sin identificar";
export const isEstimate = (card) => Boolean(card && card.reset && ["configured", "rule"].includes(card.reset.source));

const who = (card) => (card.model ? card.app + " · " + card.model : card.app);

// «Claude · modelo alcanzó su límite de sesión (5 horas)», or the wording of the other classes.
export function limitText(card) {
  const name = who(card);
  if (card.reason === "credits_exhausted") return name + " se quedó sin créditos.";
  if (card.reason === "rate_limited") return name + " recibió demasiadas solicitudes seguidas.";
  if (card.reason === "limit_unknown") return name + " alcanzó un límite de uso.";
  return name + " alcanzó su límite " + (WINDOW_TEXT[card.window] || WINDOW_TEXT.unknown) + ".";
}

// «Se restablece a las 16:10 (hora local · fuente: aviso de la app).» or «Restablecimiento no disponible…».
export function resetText(card, now = new Date()) {
  const reset = card.reset || {};
  const when = whenText(shownReset(card), now);
  if (reset.open_ended) {
    return (reset.at && when ? "La aplicación indicó que se restablece " + when + ", pero esta espera no termina sola: " : "Restablecimiento no disponible: ")
      + "solo tú puedes reanudar la tarea.";
  }
  if (!when) return "Restablecimiento no disponible.";
  if (card.reason === "rate_limited" && !reset.at) return "Se reintenta solo " + when + " (hora local).";
  return "Se restablece " + when + " (hora local · fuente: " + sourceText(reset.source) + (isEstimate(card) ? " · hora estimada" : "") + ").";
}

// «Avance guardado: parche de 3 archivos.» / «Avance guardado sin parche (…).» / «Sin avance guardado.»
export function checkpointText(card) {
  const saved = card.checkpoint || {};
  if (saved.patch) {
    const files = saved.files_total;
    return "Avance guardado: parche" + (Number.isInteger(files) && files > 0 ? " de " + files + (files === 1 ? " archivo" : " archivos") : "") + ".";
  }
  if (saved.saved) return "Avance guardado sin parche" + (PATCH_REASON_TEXT[saved.reason] ? " (" + PATCH_REASON_TEXT[saved.reason] + ")" : "") + ".";
  return "Sin avance guardado.";
}

export function handoffText(card) {
  const handoffs = card.handoffs || { used: 0, max: 0 };
  return "Traspasos " + handoffs.used + "/" + handoffs.max;
}

export const taskText = (card) => "Tarea " + card.task_id + (card.title && card.title !== card.task_id ? " · " + card.title : "");

// The label of «Esperar»: «Esperar hasta 16:10» (the time of the reset). null when there is no time to wait for.
export function waitLabel(card, now = new Date()) {
  const clock = card.can_wait ? clockText(shownReset(card), now) : null;
  return clock ? "Esperar hasta " + clock : null;
}

// «Delegar a Codex · gpt-6.1 · T3» (the model and the level only when the server knows them).
export function candidateLabel(candidate) {
  return ["Delegar a " + candidate.app, candidate.model, candidate.level].filter(Boolean).join(" · ");
}

export function candidateNote(candidate, now = new Date()) {
  const parts = ["Se envía " + candidate.sends + "."];
  if (!candidate.model) parts.push("El modelo se elige al traspasar.");
  if (candidate.policy === "ask") parts.push("Esta aplicación pregunta antes de recibir tareas.");
  if (!candidate.available) {
    const until = clockText(candidate.paused_until, now);
    parts.push(until ? "En pausa hasta " + until + "." : "No está disponible ahora.");
  }
  return parts.join(" ");
}

export const HISTORY_NOTE = "El historial de la conversación no viaja a otra aplicación.";

// What the card says once a decision is recorded. null while it is pending.
export function decisionText(card, now = new Date()) {
  const decided = clockText(card.decided_at, now);
  if (card.decision === "wait") {
    const clock = clockText(shownReset(card), now);
    return "Decidiste esperar" + (clock ? " hasta " + clock : "") + ". La tarea se reanuda sola poco después de esa hora.";
  }
  if (card.decision === "delegate") {
    const found = (card.candidates || []).find((item) => item.executor_id === card.candidate);
    const target = found ? found.app : card.candidate || "otro agente";
    return "Pediste delegar a " + target + (decided ? " (" + decided + ")" : "") +
      ". Todavía no hay traspaso automático: la tarea no se reanudará sola hasta que se traspase o vuelvas a esperar.";
  }
  return null;
}

export function blockedText(card) {
  if (card.blocked === "failed_resumes") return "La reanudación falló varias veces seguidas: decide tú cómo seguir.";
  if (card.blocked === "condition_expired") return "La espera venció: decide tú cómo seguir.";
  return null;
}

// Everything the card draws, as plain strings.
export function describeCard(card, now = new Date()) {
  const reset = shownReset(card);
  return {
    title: limitText(card),
    task: taskText(card),
    reset: resetText(card, now),
    resetIso: toDate(reset) ? toDate(reset).toISOString() : null,
    resetClock: clockText(reset, now),
    checkpoint: checkpointText(card),
    handoffs: handoffText(card),
    decision: decisionText(card, now),
    blocked: blockedText(card),
    wait: waitLabel(card, now),
    candidates: (card.candidates || []).map((candidate) => ({
      id: candidate.executor_id, label: candidateLabel(candidate), note: candidateNote(candidate, now), available: candidate.available,
    })),
    exhausted: Boolean(card.handoffs_exhausted),
  };
}

// «En espera · hasta 16:10» for the agent tree (the same words as js/agent-tree-model.js).
export function treeText(card, now = new Date()) {
  const clock = clockText(shownReset(card), now);
  return clock ? "En espera · hasta " + clock : "En espera · hora de restablecimiento no disponible";
}

export function newDecisionId() {
  const crypto = globalThis.crypto;
  if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "d-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

export const decisionPath = (taskId) => "/gui/api/tasks/" + encodeURIComponent(taskId) + "/continuity/decision";

export function decisionBody(card, decision, executorId, decisionId) {
  const body = { decision_id: decisionId, decision, expected_revision: card.revision };
  if (decision === "delegate") body.executor_id = executorId;
  return body;
}

// The text of the confirmation box of one choice: what is going to be saved, and what will not happen.
export function confirmText(card, choice, now = new Date()) {
  if (choice.kind === "wait") {
    const clock = clockText(shownReset(card), now);
    return {
      title: "¿Esperar hasta " + (clock || "la hora indicada") + "?",
      lines: ["La tarea sigue pausada y se reanuda sola poco después de esa hora.", "No se envía nada a otra aplicación."],
      yes: "Sí, esperar",
    };
  }
  const candidate = (card.candidates || []).find((item) => item.executor_id === choice.executor_id);
  return {
    title: "¿Delegar a " + (candidate ? [candidate.app, candidate.model, candidate.level].filter(Boolean).join(" · ") : "otro agente") + "?",
    lines: [
      candidate ? "Se enviaría " + candidate.sends + "." : "Se enviaría el sobre de la tarea.",
      HISTORY_NOTE,
      "Hoy solo se guarda tu decisión: la tarea no se reanuda sola hasta que se traspase o vuelvas a esperar.",
    ],
    yes: "Sí, delegar",
  };
}

// --- the policy (section «Continuidad» of Uso) --------------------------------------------------------------------------

export function pausedText(candidate, now = new Date()) {
  if (candidate.enabled === false) return "Desactivado";
  if (candidate.paused_until) return "En pausa hasta " + (clockText(candidate.paused_until, now) || "una hora desconocida");
  if (candidate.paused_open_ended) return "En pausa sin hora de fin";
  return candidate.available === false ? "No disponible" : "Disponible";
}

export function policyRows(policy) {
  const mode = MODE_TEXT[policy.mode] || { label: String(policy.mode || "Sin dato"), explanation: "" };
  const hours = policy.wait_threshold_hours;
  return [
    ["Modo", mode.label + (mode.explanation ? ". " + mode.explanation : "")],
    ["Umbral de espera", Number.isFinite(hours) ? (hours === 1 ? "1 hora" : hours + " horas") + ": si el límite tarda más en restablecerse, se propone otro agente." : "Sin dato"],
    ["Traspasos máximos", Number.isFinite(policy.max_handoffs) ? String(policy.max_handoffs) + " por tarea" : "Sin dato"],
    ["Durante una integración", "Nunca se traspasa una tarea mientras se integra."],
    ["Modo automático", policy.auto_scope === "project" ? "Solo se activa por proyecto, nunca para todo el framework." : "Sin dato"],
  ];
}

export function policyCandidates(policy, now = new Date()) {
  return (policy.candidates || []).map((candidate) => ({
    id: candidate.executor_id, app: candidate.app, policy: POLICY_TEXT[candidate.policy] || "Sin dato",
    state: pausedText(candidate, now),
  }));
}

// What a failed decision says. The reasons are those of POST /gui/api/tasks/<id>/continuity/decision.
export const ERROR_TEXT = {
  stale_revision: "La tarea cambió desde que se dibujó la tarjeta. Se actualizó: revisa y decide de nuevo.",
  not_held: "La tarea ya no espera por un límite de uso. Se actualizó la lista.",
  no_reset_time: "No hay una hora de restablecimiento a la que esperar.",
  candidate_not_available: "Ese agente ya no puede recibir la tarea. Se actualizó la lista.",
  handoffs_exhausted: "Esta tarea ya usó todos sus traspasos.",
  blocked: "Esta tarea necesita que la reanudes tú.",
  condition_hold: "Esta espera termina por una condición, no por una decisión.",
  decision_id_reused: "Esa decisión ya se usó con otra tarea. Vuelve a intentarlo.",
};

export function decisionErrorText(error) {
  if (!error) return "No se pudo guardar la decisión.";
  if (error.code === "network_error") return "No se pudo contactar con el servidor local. No se guardó nada.";
  if (error.code === "unauthorized" || error.status === 401) return "El token de acceso ya no es válido. Vuelve a abrir el enlace que imprime python -m multiagent.gui.";
  const reason = error.details && error.details.reason;
  if (ERROR_TEXT[reason]) return ERROR_TEXT[reason];
  if (error.status === 404) return "La tarea ya no existe. Se actualizó la lista.";
  return "No se pudo guardar la decisión" + (error.message ? ": " + error.message : ".");
}

// Errors after which the card is out of date and the page reads the list again.
export const isStale = (error) => Boolean(error) && (error.status === 404 || error.status === 409);
