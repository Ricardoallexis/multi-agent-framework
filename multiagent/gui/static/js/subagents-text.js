// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts and formatters of the subagents panel (Spanish). The server sends codes and facts; the wording lives here.
import { relativeTime, sourceLabel, warningText } from "./session-text.js";

export const PAGE_SIZE = 10; // children shown per tab before "Ver más"

export const GROUPS = [
  { id: "active", label: "Activos", empty: "No hay subagentes activos ahora mismo." },
  { id: "done", label: "Terminados", empty: "Todavía no ha terminado ningún subagente." },
];

// Same split as the server (sessions/children.py): active = running, testing, idle; the rest is "Terminados".
const ACTIVE = ["running", "testing", "idle"];
export const groupOf = (status) => (ACTIVE.includes(status) ? "active" : "done");

// The dot is only a hint: the label is always written next to it. tone drives the colour and the animation.
const STATUS = {
  running: { label: "Trabajando", tone: "run", help: "Tuvo actividad hace menos de 15 minutos." },
  testing: { label: "Ejecutando prueba", tone: "test",
    help: "Tuvo actividad hace menos de 15 minutos y su última herramienta es una prueba (pytest, probar o integrar) que aún no devolvió resultado." },
  idle: { label: "Sin actividad", tone: "quiet", help: "Entre 15 y 60 minutos sin actividad." },
  done: { label: "Terminado", tone: "quiet", help: "Terminó su trabajo." },
  stopped: { label: "Detenido", tone: "quiet", help: "Más de 60 minutos sin actividad, o falló." },
  unknown: { label: "Estado desconocido", tone: "quiet", help: "La fuente no dice cuándo tuvo actividad." },
};
const NOTES = { waiting_human: "Esperando a una persona" };

export function statusInfo(status, note) {
  const base = STATUS[status] || { label: String(status), tone: "quiet", help: "" };
  return NOTES[note] ? { ...base, label: NOTES[note] } : base;
}

const KIND_NAME = { subagent: "Subagente", thread: "Hilo", step: "Paso" };

function shortId(child) {
  const tail = String(child.id).split(/[:#]/).pop() || "";
  return tail.replace(/^agent-/, "").slice(0, 8);
}

// What names the child: the description the app gave it, else its type or nickname, else a generic name.
export function childTitle(child) {
  if (child.label) return child.label;
  if (child.name) return child.name;
  return (KIND_NAME[child.kind] || "Agente") + " " + shortId(child);
}

// Two letters for the avatar: the initials of the title, or the app's own when there is nothing to take them from.
export function initials(child) {
  const words = String(childTitle(child)).split(/[\s\-_·:]+/).filter((word) => /^[\p{L}\p{N}]/u.test(word));
  const letters = words.slice(0, 2).map((word) => word.charAt(0).toUpperCase()).join("");
  return letters || sourceLabel(child.source).slice(0, 2);
}

const number = (value, digits) => value.toLocaleString("es", { maximumFractionDigits: digits });

// "1,2 M tokens"; null (not reported) is never shown as 0.
export function compactTokens(tokens) {
  if (!tokens) return "tokens: desconocido";
  const known = (value) => typeof value === "number" && Number.isFinite(value);
  let total = null;
  if (known(tokens.total)) total = tokens.total;
  else if (known(tokens.input) || known(tokens.output)) total = (known(tokens.input) ? tokens.input : 0) + (known(tokens.output) ? tokens.output : 0);
  if (total === null) return "tokens: desconocido";
  if (total >= 1e6) return number(total / 1e6, 1) + " M tokens";
  if (total >= 1e3) return number(total / 1e3, 1) + " k tokens";
  return number(total, 0) + " tokens";
}

// Second line: the type (when the title is the description), the model and the level.
export function detailText(child) {
  const parts = [];
  if (child.label && child.name && child.name !== child.label) parts.push(child.name);
  parts.push(child.model || "modelo sin indicar");
  if (child.level) parts.push("nivel " + child.level);
  return parts.join(" · ");
}

// Third line, after the status label: when it last did something and how many tokens it used.
export function activityText(child, now = Date.now()) {
  const when = child.last_activity_at ? "actividad " + relativeTime(child.last_activity_at, now) : "sin hora registrada";
  return when + " · " + compactTokens(child.tokens);
}

export function countsText(counts) {
  const active = counts ? counts.active : 0;
  const done = counts ? counts.done : 0;
  return active + (active === 1 ? " activo" : " activos") + " y " + done + (done === 1 ? " terminado" : " terminados");
}

export const NO_CONVERSATION = "Sin conversación propia: aparece como aviso dentro de la sesión.";

// Reader warnings: the ones of this panel first, the generic ones of the sessions view otherwise.
export function childrenWarning(warning) {
  const text = String(warning);
  if (text.startsWith("children_truncated")) return "Solo se muestran los subagentes más recientes.";
  return warningText(text);
}
