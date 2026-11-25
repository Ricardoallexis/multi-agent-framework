// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { t } from "../../js/i18n.js";
// The project view's data rules (PRY-T03) as pure functions: no DOM, no Preact, so Node tests run them. Everything shown
// comes from the answers of /gui/api/projects (which are the core's own views); a value the core did not give is shown as
// «sin dato», never invented.

export const list = (value) => (Array.isArray(value) ? value : []);

// The order the labels of the core are shown in (multiagent/tasks/labels.json): what waits for the user first.
export const LABEL_ORDER = ["review", "changes", "doing", "ready", "blocked", "todo", "done", "failed", "cancelled"];

export const requiresText = (requires) => requires === "submitted" ? t("ext.project.requires.submitted")
  : requires === "accepted" || !requires ? t("ext.project.requires.accepted") : String(requires);

// «INV-T01» for «snt-INV-T01»: the project key prefix is hidden (PRY-T01 section 2).
export function shortId(id, projectId) {
  const text = String(id || "");
  const prefix = projectId ? projectId + "-" : "";
  return prefix && text.startsWith(prefix) ? text.slice(prefix.length) : text;
}

// StatusChip props for the label the core gives a task or a stage ({ key, text, tone, detail? }): the chip shows the core's
// own words and tone, so no copy of the label table lives in the page.
export function chipOf(label) {
  const known = label && typeof label === "object" && label.key;
  const entry = known ? label : { key: "none", text: t("ext.project.status.none"), tone: "neutral" };
  return { status: entry.key, labels: { [entry.key]: { text: entry.text, tone: entry.tone } }, detail: known && label.detail ? label.detail : "" };
}

// The bar of the project: done out of the tasks that count (the cancelled ones do not, PRY-T01 section 3).
export function progressOf(progress) {
  const done = Number(progress?.done) || 0;
  const counted = (Number(progress?.total) || 0) - (Number(progress?.cancelled) || 0);
  if (counted <= 0) return { value: null, max: 100, valueText: t("ext.project.progress.none") };
  const percent = Number.isFinite(progress?.percent) ? progress.percent : Math.round((100 * done) / counted);
  return { value: done, max: counted, valueText: t("ext.project.progress.summary", { done, total: counted, percent }) };
}

// Every task of the stages, each with its stage; activities are not in stages (PRY-T09) and are not here.
export function taskIndex(project) {
  const index = new Map();
  for (const stage of list(project?.stages)) {
    for (const task of list(stage.tasks)) index.set(task.id, { task, stage });
  }
  return index;
}

// The labels present in the project, in LABEL_ORDER, with how many tasks carry each (counts come from the core).
export function labelSummary(project) {
  const found = new Map();
  for (const { task } of taskIndex(project).values()) {
    if (task.label?.key && !found.has(task.label.key)) found.set(task.label.key, task.label);
  }
  const counts = project?.counts && typeof project.counts === "object" ? project.counts : {};
  const order = (key) => (LABEL_ORDER.includes(key) ? LABEL_ORDER.indexOf(key) : LABEL_ORDER.length);
  return [...found.values()]
    .sort((a, b) => order(a.key) - order(b.key))
    .map((label) => ({ label, count: Number(counts[label.key]) || 0 }));
}

// Who a task belongs to, from the core's actor view (PRY-T01 section 4); «sugerido» when only a preferred actor is set.
export function actorText(info) {
  if (!info || typeof info !== "object" || !info.raw) return "";
  if (info.kind === "agent") return info.model ? info.app + " / " + info.model : String(info.app || info.raw);
  if (info.kind === "user" || info.kind === "person") return String(info.name || info.raw);
  return String(info.raw);
}

export function whoText(task) {
  const name = actorText(task?.assignee_info);
  if (!name) return t("ext.project.owner.unassigned");
  return task.assignee ? name : t("ext.project.owner.suggested", { name });
}

// A date without a time must not move with the time zone.
export function dayText(value, fallback = null) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
  if (!match) return fallback || t("ext.project.date.none");
  const month = Number(match[2]);
  const monthNames = [
    t("ext.project.month.jan"), t("ext.project.month.feb"), t("ext.project.month.mar"),
    t("ext.project.month.apr"), t("ext.project.month.may"), t("ext.project.month.jun"),
    t("ext.project.month.jul"), t("ext.project.month.aug"), t("ext.project.month.sep"),
    t("ext.project.month.oct"), t("ext.project.month.nov"), t("ext.project.month.dec"),
  ];
  return month >= 1 && month <= 12
    ? t("ext.project.date.short", { day: Number(match[3]), month: monthNames[month - 1], year: match[1] })
    : fallback || t("ext.project.date.none");
}

export function planText(stage) {
  if (!stage?.planned_start && !stage?.planned_end) return t("ext.project.date.no_plan");
  return t("ext.project.date.planned", { start: dayText(stage.planned_start, "?"), end: dayText(stage.planned_end, "?") });
}

// The List view (PRY-T05): every task of the stages as one flat row, with its stage and the stage's planned dates (null when the
// plan has none: nothing is invented). Same shape as the rows of ProjectServices.task_list.
export function listRows(project) {
  return [...taskIndex(project).values()].map(({ task, stage }) => ({
    ...task, stage_code: stage.code, stage_title: stage.title, planned_start: stage.planned_start || null, planned_end: stage.planned_end || null,
  }));
}

export const ownerOf = (task) => task?.assignee || task?.preferred || "";

// The owners present (as stored, with their shown name) and the filters of the List view. A date filter leaves out the tasks with
// no due date, like the service does.
export function listOwners(rows) {
  const found = new Map();
  for (const row of list(rows)) if (ownerOf(row) && !found.has(ownerOf(row))) found.set(ownerOf(row), actorText(row.assignee_info) || ownerOf(row));
  return [...found].map(([value, text]) => ({ value, text })).sort((a, b) => a.text.localeCompare(b.text));
}

export const NO_LIST_FILTER = { owner: "", label: "", from: "", to: "" };

export function matchesList(row, { owner = "", label = "", from = "", to = "" } = {}) {
  if (owner && ownerOf(row) !== owner) return false;
  if (label && row.label?.key !== label) return false;
  if (from || to) {
    const due = String(row.due_at || "").slice(0, 10);
    if (!due || (from && due < from) || (to && due > to)) return false;
  }
  return true;
}

// Does the task match the filter? `text` looks in title, id and phase; `label` is a label key or "" for all.
export function matchesFilter(task, { text = "", label = "" } = {}) {
  if (label && task.label?.key !== label) return false;
  const term = String(text || "").trim().toLowerCase();
  if (!term) return true;
  return [task.title, task.id, task.phase].some((field) => String(field || "").toLowerCase().includes(term));
}

// The groups of a stage's tasks to draw: one per phase of the stage in its order, then «Sin fase» for the tasks whose phase is
// empty or not one of the stage's (none may disappear). With no phases at all there is a single unnamed group.
// `visible` is the filtered list; a phase that has tasks but none visible is still shown, with the count of both.
export function phaseGroups(stage, visible) {
  const phases = list(stage?.phases);
  const all = list(stage?.tasks);
  const shown = new Set(list(visible).map((task) => task.id));
  const groups = phases.map((name) => ({ key: name, name, all: all.filter((t) => t.phase === name) }));
  const loose = all.filter((task) => !phases.includes(task.phase));
  if (loose.length || !phases.length) groups.push({ key: "", name: phases.length ? t("ext.project.phase.none") : "", all: loose });
  return groups.map((group) => ({ ...group, tasks: group.all.filter((task) => shown.has(task.id)) }));
}

// The task's own answer does not carry its phase, date or actor view; the project's line of the same task does. The page shows the
// task's answer (it is the newer one) over that line, so nothing is missing and nothing is invented.
export function detailOf(project, task) {
  if (!task) return null;
  const found = taskIndex(project).get(task.id);
  return found ? { ...found.task, ...task } : task;
}

// Only a task waiting for the user's review can be approved or sent back (label «review» = submitted).
export const canReview = (task) => Boolean(task) && task.state === "submitted" && task.label?.key === "review";

// A dependency as a line: «T01 · Estudio de audiencia», using the project's tasks for the title when it knows them.
export function taskName(id, project) {
  const found = taskIndex(project).get(id);
  const short = shortId(id, project?.id);
  return found ? short + " · " + found.task.title : short;
}

// The result envelope's deliverables as plain lists (summary, files, decisions, tests not run, requests). Empty parts are left out.
export function deliverables(result) {
  if (!result || typeof result !== "object") return null;
  const texts = (value) => list(value).filter((item) => typeof item === "string" && item.trim());
  return {
    summary: typeof result.summary === "string" ? result.summary : "",
    files: texts(result.patch?.files),
    decisions: texts(result.decisions),
    testsNotRun: texts(result.tests_not_run),
    contractChanges: typeof result.contract_changes === "string" ? result.contract_changes : "",
    requests: list(result.requests).filter((item) => item && item.to && item.what),
    attempt: Number(result.attempt) || null,
    agent: typeof result.agent === "string" ? result.agent : "",
  };
}

// The first project of a list the page should open: the one in the address, else the first that needs the user, else the first.
export function pickProject(projects, wanted = "") {
  const all = list(projects);
  if (wanted && all.some((p) => p.id === wanted)) return wanted;
  const needing = all.find((p) => Object.values(p.needs_you || {}).some((n) => n > 0));
  return (needing || all[0] || {}).id || "";
}

// What waits for the user in a project, as a sentence for the panel card.
export function needsText(needs) {
  const parts = [
    Number(needs?.reviews) > 0 ? t("ext.project.needs.reviews", { count: needs.reviews }) : "",
    Number(needs?.verifications) > 0 ? t("ext.project.needs.verifications", { count: needs.verifications }) : "",
    Number(needs?.mine) > 0 ? t("ext.project.needs.mine", { count: needs.mine }) : "",
    Number(needs?.blocked) > 0 ? t("ext.project.needs.blocked", { count: needs.blocked }) : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : t("ext.project.needs.none");
}
