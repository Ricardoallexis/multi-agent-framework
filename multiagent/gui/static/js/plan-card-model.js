// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CHT-T06: pure text of the plan thread (no DOM, no network). The server sends the proposal with its tasks, their inputs,
// the `revision` a decision must send, and, when a bundle used by the plan has roles without an approved team, the reason
// why approving is blocked. Everything here returns plain text or plain data; the view draws it as text.

export const TASK_STATE_TEXT = {
  blocked: "Bloqueada", ready: "Lista", claimed: "Tomada", running: "En marcha", paused: "En pausa", validating: "En validación",
  needs_changes: "Pide cambios", submitted: "Entregada", accepted: "Aceptada", failed: "Fallida", cancelled: "Cancelada",
};
export const RUN_STATUS_TEXT = {
  queued: "en cola", running: "en marcha", waiting_human: "espera a una persona", completed: "terminada", failed: "fallida",
  cancelled: "cancelada", rejected: "rechazada",
};

// The inputs of one task as a list of [name, value] with only text values; an empty or missing object gives [].
export const inputPairs = (inputs) => inputs && typeof inputs === "object" && !Array.isArray(inputs)
  ? Object.entries(inputs).map(([name, value]) => [name, value === null || value === undefined ? "" : String(value)]) : [];

// What each task of a proposal shows: its inputs as a readable object (or null when it has none).
export const inputsValue = (inputs) => inputPairs(inputs).length ? Object.fromEntries(inputPairs(inputs)) : null;

export const canDecide = (entry) => Boolean(entry && entry.decision && (entry.decision.actions || []).length);
export const decisionActions = (entry) => canDecide(entry) ? entry.decision.actions : [];

// The revision the page shows is the one the decision sends as expected_revision; never a later one.
export const decisionRevision = (entry) => entry && entry.decision && Number.isInteger(entry.decision.revision)
  ? entry.decision.revision : (entry && entry.proposal && Number.isInteger(entry.proposal.revision) ? entry.proposal.revision : null);

export const rolesText = (roles) => Array.isArray(roles) && roles.length ? roles.join(", ") : "sin puestos";

// «Falta el equipo aprobado»: the message and where it leads (a proposed version of the team, or Equipos).
export function missingTeam(item) {
  const bundle = item && item.bundle ? String(item.bundle) : "";
  const versionId = item && item.team_version_id ? String(item.team_version_id) : "";
  const text = "Falta el equipo aprobado" + (bundle ? " del paquete «" + bundle + "»" : "") +
    ": los pasos con puestos (" + rolesText(item && item.roles) + ") no pueden correr sin él.";
  if (versionId) return { text, href: "#new/team/" + encodeURIComponent(versionId), label: "Abrir la versión propuesta del equipo", kind: "version" };
  return { text, href: "#teams" + (bundle ? "/" + encodeURIComponent(bundle) : ""), label: "Ir a Equipos", kind: "teams" };
}

// Why «Aprobar» is disabled, or "" when it is not.
export function approveBlock(entry) {
  const blocked = entry && entry.decision && entry.decision.approve_blocked;
  if (!blocked) return "";
  const bundles = (blocked.blockers || []).map((item) => item.bundle ? "«" + item.bundle + "»" : "sin paquete");
  return "No se puede aprobar: falta el equipo aprobado de " + (bundles.join(", ") || "un paquete") + ".";
}

// One message per task of an approved plan: its state, its run and where the run opens.
export function taskRunLine(entry) {
  const state = TASK_STATE_TEXT[entry && entry.state] || (entry && entry.state ? String(entry.state) : "Sin estado");
  const runId = entry && entry.run_id ? String(entry.run_id) : "";
  const status = entry && entry.run_status ? (RUN_STATUS_TEXT[entry.run_status] || String(entry.run_status)) : "";
  const title = (entry && (entry.title || entry.task_id)) || "";
  return {
    text: "Tarea " + (entry && entry.task_id ? entry.task_id : "") + (title && title !== entry.task_id ? " · " + title : "") + ": " + state + ".",
    runText: runId ? "Run " + runId + (status ? " (" + status + ")" : "") : "Todavía no tiene run.",
    href: runId ? "#new/run/" + encodeURIComponent(runId) : "",
  };
}

// The text of a failed decision: a stale revision says so and asks to read again; anything else shows the service text.
export function decisionFailure(error) {
  const status = error && error.status;
  const code = error && error.code;
  const details = (error && error.details) || {};
  if (status === 409 && details.reason === "stale_revision") {
    return { text: "La propuesta cambió mientras la mirabas (revisión " + (details.revision ?? "nueva") + "). Se volvió a leer; revisa y decide otra vez.", reload: true };
  }
  if (status === 409 && details.reason === "team_not_approved") {
    return { text: "Falta el equipo aprobado de un paquete del plan. Se volvió a leer.", reload: true };
  }
  if (status === 409) return { text: (error.message || "Conflicto") + (code ? " (" + code + ")" : "") + ". Se volvió a leer.", reload: true };
  return { text: (error && error.message ? error.message : "Error desconocido") + (code ? " (" + code + ")" : ""), reload: status === 404 };
}
