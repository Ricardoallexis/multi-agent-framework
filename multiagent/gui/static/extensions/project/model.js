// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { currentLanguage, t } from "../../js/i18n.js";
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

// --- The overview of AVZ-T03: figures, bar by pieces, what to do, stages that can be hidden ---------------------------------------
// Everything here reads what the core already gives (progress, progress_weighted, remaining, counts, attention, last_change) and
// writes nothing; hiding a stage changes what is drawn, never a figure.

// More tasks than this and the bar of the project has one piece per stage instead of one per task.
export const SEGMENT_LIMIT = 60;

const SEGMENT_EXTRAS = ["review", "changes", "doing", "blocked", "failed", "verify"];

const countedOf = (progress) => Math.max(0, (Number(progress?.total) || 0) - (Number(progress?.cancelled) || 0));

// The figures of the strip. `percent` is the weighted one (AVZ-T01) and `leftTasks`/`leftSteps` the tasks still to do and the steps
// they need at best; a value the core did not give is null and the page shows it as «sin dato», never as a number.
export function overviewFigures(project) {
  const percent = project?.progress_weighted?.percent;
  const remaining = project?.remaining && typeof project.remaining === "object" ? project.remaining : {};
  const counts = project?.counts && typeof project.counts === "object" ? project.counts : {};
  return {
    stages: list(project?.stages).length,
    done: Number(project?.progress?.done) || 0,
    counted: countedOf(project?.progress),
    percent: Number.isFinite(percent) ? percent : null,
    review: Number(counts.review) || 0,
    blocked: Number(counts.blocked) || 0,
    leftTasks: Number.isFinite(remaining.tasks) ? remaining.tasks : null,
    leftSteps: Number.isFinite(remaining.layers) ? remaining.layers : null,
  };
}

const pieceOf = (id, label) => ({ id, state: label?.key || "none", tone: label?.tone || "neutral", text: label?.text || "" });

// Keeps at most `limit` pieces by giving each state a share of them in proportion to how many it had (at least one each).
export function condense(segments, limit = SEGMENT_LIMIT) {
  const pieces = list(segments);
  if (pieces.length <= limit) return pieces;
  const groups = new Map();
  for (const piece of pieces) {
    if (!groups.has(piece.state)) groups.set(piece.state, []);
    groups.get(piece.state).push(piece);
  }
  const shares = [...groups.values()].map((group) => ({ group, slots: Math.max(1, Math.floor((group.length * limit) / pieces.length)) }));
  let total = shares.reduce((sum, share) => sum + share.slots, 0);
  while (total > limit) {
    const biggest = shares.reduce((a, b) => (b.slots > a.slots ? b : a));
    biggest.slots -= 1;
    total -= 1;
  }
  return shares.flatMap(({ group, slots }) => group.slice(0, slots).map((piece, index) => ({ ...piece, id: piece.id + "#" + index })));
}

// «7 of 12 tasks done, 2 Waiting for your review»: the sentence of a screen reader.
function sentenceOf(textKey, total, done, pieces) {
  const extras = [];
  for (const key of SEGMENT_EXTRAS) {
    const found = pieces.filter((piece) => piece.state === key);
    if (found.length) extras.push(found.length + " " + found[0].text);
  }
  return [t(textKey, { done, total }), ...extras].join(", ");
}

// The bar of the whole project: a piece per task (cancelled ones do not count), or per stage when there are more than `limit` tasks.
export function projectSegments(project, limit = SEGMENT_LIMIT) {
  const stages = list(project?.stages);
  const tasks = stages.flatMap((stage) => list(stage.tasks)).filter((task) => task.label?.key !== "cancelled");
  if (tasks.length > limit) {
    const segments = stages.map((stage) => pieceOf(stage.group_id, stage.label));
    const done = segments.filter((piece) => piece.state === "closed").length;
    return { unit: "stages", segments, total: segments.length, done, summary: sentenceOf("ext.project.segments.stages", segments.length, done, segments) };
  }
  const segments = tasks.map((task) => pieceOf(task.id, task.label));
  const done = segments.filter((piece) => piece.state === "done").length;
  return { unit: "tasks", segments, total: segments.length, done, summary: sentenceOf("ext.project.segments.tasks", segments.length, done, segments) };
}

// The small bar of one stage: a piece per task, condensed when it has more than `limit`.
export function stageSegments(stage, limit = SEGMENT_LIMIT) {
  const tasks = list(stage?.tasks).filter((task) => task.label?.key !== "cancelled");
  const all = tasks.map((task) => pieceOf(task.id, task.label));
  const done = all.filter((piece) => piece.state === "done").length;
  return { segments: condense(all, limit), total: all.length, done, summary: sentenceOf("ext.project.segments.tasks", all.length, done, all) };
}

// One line of the table of stages.
export function stageRow(stage, limit = SEGMENT_LIMIT) {
  const bar = stageSegments(stage, limit);
  const percent = stage?.progress_weighted?.percent;
  return {
    id: stage.group_id, code: stage.code, title: stage.title, label: stage.label,
    done: Number(stage.progress?.done) || 0, counted: countedOf(stage.progress),
    percent: Number.isFinite(percent) ? percent : null,
    segments: bar.segments, summary: bar.summary,
    planned: planText(stage), changedAt: stage.last_change?.created_at || null,
  };
}

// «What you have to do»: every row of `attention` with the verb it starts with, in the order the page draws them. A row keeps
// `hidden` to say its stage is hidden, but it stays: hiding a stage never hides something that waits for the person.
export function attentionRows(project, hidden = []) {
  const off = new Set(list(hidden));
  const attention = project?.attention && typeof project.attention === "object" ? project.attention : {};
  const rows = [];
  const add = (kind, items, idOf, stageOf) => {
    for (const item of list(items)) {
      rows.push({ kind, verb: t("ext.project.verb." + kind), id: idOf(item), stageId: stageOf(item), hidden: off.has(stageOf(item)), item });
    }
  };
  add("review", attention.reviews, (task) => task.id, (task) => task.group_id);
  add("verify", attention.verifications, (stage) => stage.group_id, (stage) => stage.group_id);
  add("mine", attention.mine, (task) => task.id, (task) => task.group_id);
  add("blocked", attention.blocked, (task) => task.id, (task) => task.group_id);
  return rows;
}

// Hidden stages. `hidden` is the list of stage ids the browser remembers; an id the project does not have any more is ignored.
export const stageIds = (project) => list(project?.stages).map((stage) => stage.group_id);

export function hiddenIn(project, hidden) {
  const off = new Set(list(hidden));
  return stageIds(project).filter((id) => off.has(id));
}

export function visibleStages(project, hidden) {
  const off = new Set(list(hidden));
  return list(project?.stages).filter((stage) => !off.has(stage.group_id));
}

export function hideStage(project, hidden, id) {
  return stageIds(project).includes(id) ? [...new Set([...hiddenIn(project, hidden), id])] : hiddenIn(project, hidden);
}

// «Hide the finished ones»: adds the closed stages to the hidden ones and nothing else.
export function hideFinished(project, hidden) {
  const closed = list(project?.stages).filter((stage) => stage.label?.key === "closed").map((stage) => stage.group_id);
  return [...new Set([...hiddenIn(project, hidden), ...closed])];
}

export function hasFinished(project, hidden) {
  const off = new Set(list(hidden));
  return list(project?.stages).some((stage) => stage.label?.key === "closed" && !off.has(stage.group_id));
}

// PRY-T07: tokens and API cost of the project (the answer of GET /gui/api/projects/<id>/costs). Every figure is a sum of records the
// server stored: a value it did not give is «sin dato», a call without tokens makes the figure «partial» and a call without a price is
// «sin precio conocido». Money is the API cost only and each currency is shown on its own, never added to another or to tokens.
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const locale = () => (currentLanguage() === "es" ? "es-MX" : "en");
const moneyIn = (currency, value) => {
  try {
    return new Intl.NumberFormat(locale(), { style: "currency", currency, maximumFractionDigits: 4 }).format(value);
  } catch (_) {
    return currency + " " + value;
  }
};

export function costSummary(group) {
  const noData = t("ext.project.figures.no_data");
  const empty = { tokens: noData, money: noData, tokensNote: "", moneyNote: "", state: "none", tokenCount: null };
  if (!group || typeof group !== "object" || !finite(group.records) || group.records === 0) return empty;
  const tokens = finite(group.tokens_total) ? new Intl.NumberFormat(locale()).format(group.tokens_total) : noData;
  const other = group.cost_other_currencies && typeof group.cost_other_currencies === "object" ? group.cost_other_currencies : {};
  const amounts = [];
  if (finite(group.estimated_cost_usd)) amounts.push(moneyIn("USD", group.estimated_cost_usd));
  for (const currency of Object.keys(other).sort()) if (finite(other[currency])) amounts.push(moneyIn(currency, other[currency]));
  const state = ["complete", "partial", "unknown"].includes(group.cost_state) ? group.cost_state : "unknown";
  return {
    tokens,
    tokenCount: finite(group.tokens_total) ? group.tokens_total : null,
    money: amounts.length ? amounts.join(" · ") : t("ext.project.cost.no_price"),
    tokensNote: group.tokens_partial ? t("ext.project.cost.tokens_partial") : "",
    moneyNote: state === "partial" && amounts.length ? t("ext.project.cost.partial") : "",
    state,
  };
}

// The costs of each stage by the id of the stage (the table of stages looks its row up here).
export const stageCosts = (costs) => new Map(list(costs?.stages).map((stage) => [stage.group_id, stage]));

// PRY-T07: the timeline, a row per visible stage with its planned start and end and its real closing. Positions are whole steps from
// 0 to TIMELINE_STEPS (the page turns a step into a class), so no style attribute is needed. It promises no end date: a stage with
// no plan only shows what is real, and «late» is only said of a stage that closed after its planned end.
export const TIMELINE_STEPS = 20;
const DAY_MS = 86400000;
const dayNumber = (value) => {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || ""));
  if (!match) return null;
  const moment = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return Number.isFinite(moment) ? Math.round(moment / DAY_MS) : null;
};
const isoOf = (day) => new Date(day * DAY_MS).toISOString().slice(0, 10);

export function timelineModel(project, hidden = []) {
  const found = visibleStages(project, hidden).map((stage) => ({
    stage, start: dayNumber(stage.planned_start), end: dayNumber(stage.planned_end), closed: dayNumber(stage.closed_at),
  }));
  const days = found.flatMap((row) => [row.start, row.end, row.closed]).filter((day) => day !== null);
  const first = days.length ? Math.min(...days) : null;
  const last = days.length ? Math.max(...days) : null;
  const span = days.length ? last - first : 0;
  const stepOf = (day) => (day === null ? null : span ? Math.round((TIMELINE_STEPS * (day - first)) / span) : 0);
  const rows = found.map(({ stage, start, end, closed }) => {
    const planText = start !== null && end !== null ? t("ext.project.timeline.planned", { start: dayText(stage.planned_start), end: dayText(stage.planned_end) })
      : end !== null ? t("ext.project.timeline.planned_end", { end: dayText(stage.planned_end) })
      : start !== null ? t("ext.project.timeline.planned_start", { start: dayText(stage.planned_start) })
      : t("ext.project.timeline.no_plan");
    const realText = closed !== null ? t("ext.project.timeline.closed", { date: dayText(stage.closed_at) }) : t("ext.project.timeline.not_closed");
    const late = closed !== null && end !== null && closed > end ? closed - end : 0;
    const status = closed === null || end === null ? "none" : late > 0 ? "late" : "ontime";
    const statusText = status === "late" ? t(late === 1 ? "ext.project.timeline.late_one" : "ext.project.timeline.late_other", { count: late })
      : status === "ontime" ? t("ext.project.timeline.ontime") : "";
    const startStep = stepOf(start);
    const endStep = stepOf(end);
    const mark = startStep !== null && endStep !== null ? { kind: "range", at: startStep, width: Math.max(0, endStep - startStep) }
      : endStep !== null ? { kind: "tick", at: endStep, width: 0 }
      : startStep !== null ? { kind: "tick", at: startStep, width: 0 }
      : null;
    return {
      id: stage.group_id, code: stage.code, title: stage.title, label: stage.label, status, mark, closedStep: stepOf(closed),
      planText, realText, statusText,
      text: [stage.code + " · " + stage.title, planText, realText, statusText].filter(Boolean).join(". ") + ".",
    };
  });
  return {
    rows, hasPlan: rows.some((row) => row.mark !== null), hasReal: rows.some((row) => row.closedStep !== null),
    from: first === null ? "" : dayText(isoOf(first)), to: last === null ? "" : dayText(isoOf(last)),
  };
}
