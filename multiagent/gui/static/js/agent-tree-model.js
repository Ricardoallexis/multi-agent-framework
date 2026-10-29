// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers of the agent tree (GUI2-T08): grouping by level, counts, the «Solo activos» filter, the static «En espera»
// state, durations and the log of status changes. No DOM in here, so the tests run it under Node.
// The status vocabulary is the one of the subagents panel (subagents-text.js), so both screens always agree.
import {
  modelsText, relativeTime, sessionTitle, sourceLabel, statusInfo as sessionStatus, surfaceLabel, tokensText,
} from "./session-text.js";
import { childTitle, compactTokens, groupOf, statusInfo } from "./subagents-text.js";
import { fmtDate } from "./text.js";

export const LEVELS = ["T1", "T2", "T3", "T4"];
export const UNKNOWN_LEVEL = "unknown";
export const LEVEL_NAMES = { T1: "económico", T2: "estándar", T3: "avanzado", T4: "máximo" };
export const ROOT_ID = "root";

const isLevel = (level) => LEVELS.includes(level);

// { T1: [], T2: [], T3: [], T4: [], unknown: [] }: a child whose model the catalog does not map has no level.
export function groupByLevel(children = []) {
  const groups = { T1: [], T2: [], T3: [], T4: [], [UNKNOWN_LEVEL]: [] };
  for (const child of children) groups[isLevel(child.level) ? child.level : UNKNOWN_LEVEL].push(child);
  return groups;
}

export const levelText = (level) => (isLevel(level) ? level + " · " + LEVEL_NAMES[level] : "Sin nivel");

// «En espera por límite de uso»: the reader marks it with note == "waiting_limit", and may give reset_at. The data may not
// exist yet; a child without either simply is not in this state. A finished child is never waiting.
export const isLimitWait = (child) => Boolean(child && child.status !== "done" && (child.note === "waiting_limit" || child.reset_at));
// Waiting for a limit to reset, or for a person (a framework step that waits for an approval).
export const isWaiting = (child) => isLimitWait(child) || Boolean(child && child.status !== "done" && child.note === "waiting_human");

export function waitingText(child, locale = "es") {
  if (!isLimitWait(child)) return null;
  const date = child.reset_at ? new Date(child.reset_at) : null;
  if (!date || Number.isNaN(date.getTime())) return "En espera · hora de restablecimiento no disponible";
  return "En espera · hasta " + date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

// active | waiting | done. "Activos" is the server's group (running, testing, idle); waiting ones are counted apart.
export function bucketOf(child) {
  if (child.status === "done") return "done";
  if (isWaiting(child)) return "waiting";
  return groupOf(child.status) === "active" ? "active" : "done";
}

export function countStatuses(children = []) {
  const counts = { active: 0, done: 0, waiting: 0 };
  for (const child of children) counts[bucketOf(child)] += 1;
  return counts;
}

// «Solo activos» keeps what is still alive: working, idle, and waiting.
export const filterChildren = (children = [], onlyActive = false) => (onlyActive ? children.filter((child) => bucketOf(child) !== "done") : children);

// The status words now carry the shared tones of ui/labels.js (GUI2-T11); the tree keeps its own until it adopts
// StatusChip, so both vocabularies map to run | test | quiet here.
const TREE_TONES = { ok: "run", run: "run", success: "run", active: "run", test: "test", warning: "test" };
const treeTone = (tone) => TREE_TONES[tone] || "quiet";

// { label, tone, help }: the label is always written (the dot is only a hint). tone: run | test | wait | hold | quiet.
export function statusView(child) {
  const limit = waitingText(child);
  if (limit) return { label: limit, tone: "hold", help: "Espera a que se restablezca el límite de uso de su aplicación." };
  const info = statusInfo(child.status, child.note);
  return child.note === "waiting_human" && child.status !== "done" ? { ...info, tone: "wait" } : { ...info, tone: treeTone(info.tone) };
}

export function rootStatusView(summary) {
  const info = sessionStatus(summary ? summary.status : "unknown");
  return { label: info.label, tone: treeTone(info.tone) === "run" ? "run" : "quiet", help: info.help };
}

export function durationText(milliseconds) {
  const minutes = Math.max(0, Math.floor(milliseconds / 60000));
  if (minutes < 1) return "menos de 1 min";
  if (minutes < 60) return minutes + " min";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? hours + " h " + (minutes % 60) + " min" : hours + " h";
  const days = Math.floor(hours / 24);
  return hours % 24 ? days + " d " + (hours % 24) + " h" : days + " d";
}

// A live child "lleva" the time since it started; a finished one "duró" from its start to its last record.
export function elapsedText(child, now = Date.now()) {
  const start = Date.parse(child.started_at || "");
  if (!Number.isFinite(start)) return "Tiempo no disponible";
  if (bucketOf(child) !== "done") return "Lleva " + durationText(now - start);
  const end = Date.parse(child.last_activity_at || "");
  return Number.isFinite(end) && end >= start ? "Duró " + durationText(end - start) : "Duración no disponible";
}

// Everything a node shows, as plain strings.
export function describeNode(child, now = Date.now()) {
  const title = child.name || childTitle({ ...child, label: null });
  return {
    id: child.id,
    title,
    model: child.model || "modelo sin indicar",
    level: isLevel(child.level) ? child.level : null,
    tier: isLevel(child.level) ? child.level : "none",
    levelText: levelText(child.level),
    status: statusView(child),
    what: child.label && child.label !== title ? child.label : "Sin descripción",
    elapsed: elapsedText(child, now),
    tokens: compactTokens(child.tokens),
  };
}

export function describeRoot(summary, now = Date.now()) {
  const created = summary && summary.created_at ? relativeTime(summary.created_at, now) : "—";
  return {
    id: ROOT_ID,
    title: summary ? sessionTitle(summary) : "Sesión principal",
    model: summary ? modelsText(summary.models) : "sin indicar",
    level: null,
    tier: "root",
    levelText: "Orquestador",
    status: rootStatusView(summary),
    what: summary && summary.cwd ? summary.cwd : "Sesión principal",
    elapsed: created === "—" ? "Inicio no disponible" : "Iniciada " + created,
    tokens: summary && summary.tokens ? tokensText(summary.tokens, { brief: true }) : "tokens: desconocido",
  };
}

const KIND_NAMES = { subagent: "Subagente", thread: "Hilo", step: "Paso de una ejecución del framework", task: "Tarea del framework en espera" };

// [label, value] rows of the detail of a child and of the orchestrator: plain text, "No consta" when the source said nothing.
export function detailFacts(child, now = Date.now()) {
  const view = describeNode(child, now);
  return [
    ["Aplicación", sourceLabel(child.source)],
    ["Tipo", KIND_NAMES[child.kind] || "Agente"],
    ["Modelo", view.model],
    ["Nivel", view.levelText],
    ["Estado", view.status.label],
    ["En qué trabaja", view.what],
    ["Tiempo", view.elapsed],
    ["Inicio", child.started_at ? fmtDate(child.started_at) : "No consta"],
    ["Última actividad", child.last_activity_at ? relativeTime(child.last_activity_at, now) : "No consta"],
    ["Tokens", view.tokens],
  ];
}

export function rootFacts(summary, now = Date.now()) {
  const view = describeRoot(summary, now);
  if (!summary) return [["Estado", view.status.label]];
  return [
    ["Aplicación", sourceLabel(summary.source)],
    ["Superficie", surfaceLabel(summary.surface)],
    ["Modelos", view.model],
    ["Estado", view.status.label],
    ["Carpeta", summary.cwd || "No consta"],
    ["Creada", summary.created_at ? fmtDate(summary.created_at) : "No consta"],
    ["Agentes lanzados", String(summary.child_count || 0)],
    ["Tokens", view.tokens],
  ];
}

// The name a screen reader reads for a node (its own text is also on screen, in pieces).
export const nodeAriaLabel = (view) =>
  [view.title, view.level ? "nivel " + view.level : view.levelText, view.status.label, view.model, view.what, view.elapsed].join(". ");

// Keyboard order = visual order: the orchestrator, then T1..T4 and the children without a level.
export function visibleOrder(grouped) {
  return [ROOT_ID, ...[...LEVELS, UNKNOWN_LEVEL].flatMap((level) => grouped[level].map((child) => child.id))];
}

// Next focused id for a key; null when the key does not move. ids is visibleOrder(...).
export function moveFocus(ids, current, key) {
  const at = Math.max(0, ids.indexOf(current));
  if (key === "ArrowDown") return ids[Math.min(ids.length - 1, at + 1)];
  if (key === "ArrowUp") return ids[Math.max(0, at - 1)];
  if (key === "Home") return ids[0];
  if (key === "End") return ids[ids.length - 1];
  return null;
}

// Where «Abrir» goes: the child's own conversation, or (a framework step) the run that holds it.
export function childLink(child, sessionId) {
  if (!child) return null;
  if (child.link_id) return { section: "sessions", id: child.link_id, label: "Abrir conversación" };
  if (child.source === "framework" && String(sessionId).startsWith("framework:")) {
    return { section: "runs", id: String(sessionId).slice("framework:".length), label: "Ver la ejecución" };
  }
  return null;
}

// What the log remembers of each child: id -> { title, state }.
export function snapshot(children = []) {
  return new Map(children.map((child) => [child.id, { title: describeNode(child).title, state: statusView(child).label }]));
}

// Log lines (without the time) for what changed between two snapshots: new children and status changes.
export function diffSnapshots(before, after) {
  const lines = [];
  for (const [id, now] of after) {
    const old = before.get(id);
    if (!old) lines.push(now.title + " · aparece · " + now.state);
    else if (old.state !== now.state) lines.push(now.title + " · " + old.state + " → " + now.state);
  }
  return lines;
}
