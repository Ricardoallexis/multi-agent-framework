// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// What the left sidebar lists (GUI2-T02), as plain functions with no DOM: sessions, framework runs and open activities
// become items; items become project groups with a status dot and a count per kind. Only API fields are read.
import { sessionTitle, statusInfo as sessionStatus } from "./session-text.js";
import { statusInfo as runStatus } from "./text.js";
import { DEFAULT_LABELS, labelEntry, labelSet, statusText } from "./ui/labels.js";

// The kinds counted next to each project. "workflow" is a framework run; the three app ids are their own sessions.
export const APPS = [
  { id: "claude", label: "Claude" },
  { id: "codex", label: "Codex" },
  { id: "copilot", label: "Copilot" },
  { id: "workflow", label: "Workflows" },
  { id: "activity", label: "Actividades" },
];
const APP_LABEL = Object.fromEntries(APPS.map((app) => [app.id, app.label]));
export const appLabel = (id) => APP_LABEL[id] || String(id);

// The three generic dot states used by sessions and runs; activity dots use their API-provided task labels.
export const DOTS = Object.fromEntries(Object.keys(DEFAULT_LABELS.dot).map((dot) => [dot, DEFAULT_LABELS.dot[dot].text]));

// A session of an app is "active" when it had activity lately; anything else is finished (the apps do not report errors).
export const sessionDot = (status) => (status === "active" ? "working" : "finished");

// Runs: waiting for a person or paused still count as work in progress; failed and interrupted runs are errors.
const RUN_DOT = {
  queued: "working", running: "working", waiting_human: "working", paused: "working",
  failed: "error", interrupted: "error",
};
export const runDot = (status) => RUN_DOT[status] || "finished";

export const NO_PROJECT = "Sin proyecto";

// Last part of a folder path, for both kinds of separator; "" when there is none.
export function folderName(path) {
  const parts = String(path || "").split(/[\\/]+/).filter(Boolean);
  return parts.length ? parts[parts.length - 1] : "";
}

// Text a screen reader (and the tooltip) gets for a dot: "Trabajando (en ejecución)".
export const dotText = (dot, detail) => statusText(labelEntry(labelSet("dot"), dot), detail);

// sessions: the "sessions" array of GET /gui/api/sessions. Framework runs that appear there are left out (the runs
// list has them with their real status), and so are subagents (they belong to their parent session).
export function sessionItems(sessions) {
  return (sessions || [])
    .filter((session) => session.source !== "framework" && !session.parent_id)
    .map((session) => {
      const dot = sessionDot(session.status);
      return {
        key: session.id, kind: "session", app: session.source, title: sessionTitle(session),
        project: folderName(session.cwd) || NO_PROJECT, folder: session.cwd || "",
        dot, detail: sessionStatus(session.status).label, updatedAt: session.updated_at || session.created_at || null,
        route: ["sessions", session.id],
      };
    });
}

// runs: the array of GET /gui/api/runs.
export function runItems(runs) {
  return (runs || []).map((run) => {
    const dot = runDot(run.status);
    return {
      key: "run:" + run.id, kind: "run", app: "workflow", title: run.workflow_id || run.id,
      project: String(run.project_name || "").trim() || NO_PROJECT, folder: "",
      dot, detail: runStatus(run.status).label, updatedAt: run.updated_at || run.created_at || null,
      route: ["runs", run.id],
    };
  });
}

// Open activities from GET /gui/api/activities, grouped under the project's real name and linked to its board.
export function activityItems(projects) {
  const closed = new Set(["done", "failed", "cancelled"]);
  return (projects || []).flatMap((project) => (project.activities || [])
    .filter((activity) => !closed.has(activity.label?.key))
    .map((activity) => {
      const key = activity.label.key;
      return {
        key: "activity:" + activity.id, kind: "activity", app: "activity", title: activity.title,
        project: project.title || project.name, projectId: project.project_id, folder: "",
        dot: key, dotLabels: { [key]: activity.label }, detail: activity.label.text,
        who: activity.assignee_info?.name || activity.assignee_info?.app || activity.assignee || activity.preferred || "Sin asignar",
        updatedAt: activity.updated_at || activity.created_at || null,
        route: ["project", project.project_id],
      };
    }));
}

const stamp = (value) => {
  const ms = value ? Date.parse(value) : NaN;
  return Number.isNaN(ms) ? 0 : ms;
};

// Lower case without accents, so "ejecucion" finds "Ejecución".
const plain = (text) => String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// Every word of the query must appear in the title, project, app, state or detail of the item.
export function matches(item, query) {
  const words = plain(query).split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = plain([item.title, item.project, appLabel(item.app), DOTS[item.dot], item.detail, item.who].join(" "));
  return words.every((word) => haystack.includes(word));
}

// items -> groups by project (the folder name of a session, the project name of a run; same name = same group),
// newest first, with the count of each app among the items that match the query.
export function buildGroups(items, query = "") {
  const byProject = new Map();
  for (const item of items) {
    if (!matches(item, query)) continue;
    const id = plain(item.project);
    if (!byProject.has(id)) {
      byProject.set(id, { key: id, name: item.project, folders: [], items: [], counts: {}, latest: 0 });
    }
    const group = byProject.get(id);
    group.items.push(item);
    if (item.folder && !group.folders.includes(item.folder)) group.folders.push(item.folder);
    group.counts[item.app] = (group.counts[item.app] || 0) + 1;
    group.latest = Math.max(group.latest, stamp(item.updatedAt));
  }
  const groups = [...byProject.values()];
  for (const group of groups) group.items.sort((a, b) => stamp(b.updatedAt) - stamp(a.updatedAt));
  groups.sort((a, b) => b.latest - a.latest || a.name.localeCompare(b.name));
  return groups;
}

// Is this item the one the address points at?
export const isCurrent = (item, route) => route.section === item.route[0] && route.id === item.route[1];

// Counts of one group in display order, only the apps that have items: [{ app, label, count }].
export function countsOf(group) {
  const known = APPS.filter((app) => group.counts[app.id]).map((app) => ({ app: app.id, label: app.label, count: group.counts[app.id] }));
  const others = Object.keys(group.counts).filter((id) => !APP_LABEL[id]).map((id) => ({ app: id, label: appLabel(id), count: group.counts[id] }));
  return known.concat(others);
}
