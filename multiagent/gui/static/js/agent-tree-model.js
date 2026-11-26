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
import { currentLanguage, t } from "./i18n.js";

export const LEVELS = ["T1", "T2", "T3", "T4"];
export const UNKNOWN_LEVEL = "unknown";
export const LEVEL_NAMES = {
  get T1() { return t("agent_tree.level.T1"); },
  get T2() { return t("agent_tree.level.T2"); },
  get T3() { return t("agent_tree.level.T3"); },
  get T4() { return t("agent_tree.level.T4"); },
};
export const ROOT_ID = "root";

const isLevel = (level) => LEVELS.includes(level);

// { T1: [], T2: [], T3: [], T4: [], unknown: [] }: a child whose model the catalog does not map has no level.
export function groupByLevel(children = []) {
  const groups = { T1: [], T2: [], T3: [], T4: [], [UNKNOWN_LEVEL]: [] };
  for (const child of children) groups[isLevel(child.level) ? child.level : UNKNOWN_LEVEL].push(child);
  return groups;
}

export const levelText = (level) => (isLevel(level)
  ? t("agent_tree.level.name", { level, name: LEVEL_NAMES[level] })
  : t("agent_tree.level.none"));

// «En espera por límite de uso»: the reader marks it with note == "waiting_limit", and may give reset_at. The data may not
// exist yet; a child without either simply is not in this state. A finished child is never waiting.
export const isLimitWait = (child) => Boolean(child && child.status !== "done" && (child.note === "waiting_limit" || child.reset_at));
// Waiting for a limit to reset, or for a person (a framework step that waits for an approval).
export const isWaiting = (child) => isLimitWait(child) || Boolean(child && child.status !== "done" && child.note === "waiting_human");

export function waitingText(child, locale = currentLanguage()) {
  if (!isLimitWait(child)) return null;
  const date = child.reset_at ? new Date(child.reset_at) : null;
  if (!date || Number.isNaN(date.getTime())) return t("agent_tree.waiting.reset_unavailable");
  return t("agent_tree.waiting.until", {
    time: date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }),
  });
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
  if (limit) return { label: limit, tone: "hold", help: t("agent_tree.waiting.help") };
  const info = statusInfo(child.status, child.note);
  return child.note === "waiting_human" && child.status !== "done" ? { ...info, tone: "wait" } : { ...info, tone: treeTone(info.tone) };
}

export function rootStatusView(summary) {
  const info = sessionStatus(summary ? summary.status : "unknown");
  return { label: info.label, tone: treeTone(info.tone) === "run" ? "run" : "quiet", help: info.help };
}

export function durationText(milliseconds) {
  const minutes = Math.max(0, Math.floor(milliseconds / 60000));
  if (minutes < 1) return t("agent_tree.duration.less_than_minute");
  if (minutes < 60) return t("agent_tree.duration.minutes", { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60
    ? t("agent_tree.duration.hours_minutes", { hours, minutes: minutes % 60 })
    : t("agent_tree.duration.hours", { count: hours });
  const days = Math.floor(hours / 24);
  return hours % 24
    ? t("agent_tree.duration.days_hours", { days, hours: hours % 24 })
    : t("agent_tree.duration.days", { count: days });
}

// A live child "lleva" the time since it started; a finished one "duró" from its start to its last record.
export function elapsedText(child, now = Date.now()) {
  const start = Date.parse(child.started_at || "");
  if (!Number.isFinite(start)) return t("agent_tree.time.unavailable");
  if (bucketOf(child) !== "done") return t("agent_tree.time.elapsed", { duration: durationText(now - start) });
  const end = Date.parse(child.last_activity_at || "");
  return Number.isFinite(end) && end >= start
    ? t("agent_tree.time.duration", { duration: durationText(end - start) })
    : t("agent_tree.time.duration_unavailable");
}

// Everything a node shows, as plain strings.
export function describeNode(child, now = Date.now()) {
  const title = child.name || childTitle({ ...child, label: null });
  return {
    id: child.id,
    title,
    model: child.model || t("agent_tree.fallback.model"),
    level: isLevel(child.level) ? child.level : null,
    tier: isLevel(child.level) ? child.level : "none",
    levelText: levelText(child.level),
    status: statusView(child),
    what: child.label && child.label !== title ? child.label : t("agent_tree.fallback.description"),
    elapsed: elapsedText(child, now),
    tokens: compactTokens(child.tokens),
  };
}

export function describeRoot(summary, now = Date.now()) {
  const created = summary && summary.created_at ? relativeTime(summary.created_at, now) : "—";
  return {
    id: ROOT_ID,
    title: summary ? sessionTitle(summary) : t("agent_tree.root.title"),
    model: summary ? modelsText(summary.models) : t("agent_tree.fallback.not_indicated"),
    level: null,
    tier: "root",
    levelText: t("agent_tree.root.orchestrator"),
    status: rootStatusView(summary),
    what: summary && summary.cwd ? summary.cwd : t("agent_tree.root.title"),
    elapsed: created === "—" ? t("agent_tree.time.start_unavailable") : t("agent_tree.time.started", { time: created }),
    tokens: summary && summary.tokens ? tokensText(summary.tokens, { brief: true }) : t("agent_tree.tokens.unknown"),
  };
}

const KIND_KEYS = {
  subagent: "agent_tree.kind.subagent", thread: "agent_tree.kind.thread",
  step: "agent_tree.kind.step", task: "agent_tree.kind.task",
};

// [label, value] rows of the detail of a child and of the orchestrator: plain text, "No consta" when the source said nothing.
export function detailFacts(child, now = Date.now()) {
  const view = describeNode(child, now);
  return [
    [t("agent_tree.fact.application"), sourceLabel(child.source)],
    [t("agent_tree.fact.kind"), t(KIND_KEYS[child.kind] || "agent_tree.kind.agent")],
    [t("agent_tree.fact.model"), view.model],
    [t("agent_tree.fact.level"), view.levelText],
    [t("agent_tree.fact.status"), view.status.label],
    [t("agent_tree.fact.working_on"), view.what],
    [t("agent_tree.fact.time"), view.elapsed],
    [t("agent_tree.fact.started"), child.started_at ? fmtDate(child.started_at) : t("agent_tree.fallback.not_recorded")],
    [t("agent_tree.fact.last_activity"), child.last_activity_at ? relativeTime(child.last_activity_at, now) : t("agent_tree.fallback.not_recorded")],
    [t("agent_tree.fact.tokens"), view.tokens],
  ];
}

export function rootFacts(summary, now = Date.now()) {
  const view = describeRoot(summary, now);
  if (!summary) return [[t("agent_tree.fact.status"), view.status.label]];
  return [
    [t("agent_tree.fact.application"), sourceLabel(summary.source)],
    [t("agent_tree.fact.surface"), surfaceLabel(summary.surface)],
    [t("agent_tree.fact.models"), view.model],
    [t("agent_tree.fact.status"), view.status.label],
    [t("agent_tree.fact.folder"), summary.cwd || t("agent_tree.fallback.not_recorded")],
    [t("agent_tree.fact.created"), summary.created_at ? fmtDate(summary.created_at) : t("agent_tree.fallback.not_recorded")],
    [t("agent_tree.fact.launched_agents"), String(summary.child_count || 0)],
    [t("agent_tree.fact.tokens"), view.tokens],
  ];
}

// The name a screen reader reads for a node (its own text is also on screen, in pieces).
export const nodeAriaLabel = (view) =>
  [view.title, view.level ? t("agent_tree.accessibility.level", { level: view.level }) : view.levelText,
    view.status.label, view.model, view.what, view.elapsed].join(". ");

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
  if (child.link_id) return { section: "sessions", id: child.link_id, label: t("agent_tree.link.open_conversation") };
  if (child.source === "framework" && String(sessionId).startsWith("framework:")) {
    return { section: "runs", id: String(sessionId).slice("framework:".length), label: t("agent_tree.link.view_run") };
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
    if (!old) lines.push(now.title + " · " + t("agent_tree.log.appears") + " · " + now.state);
    else if (old.state !== now.state) lines.push(now.title + " · " + old.state + " → " + now.state);
  }
  return lines;
}

// --- header figures and level bars (ARB-T01) ---
// Presentation only: everything is computed from the children already loaded. A sum with gaps is «partial», never an exact total.
const isKnown = (value) => typeof value === "number" && Number.isFinite(value);

// One member's tokens, counted like compactTokens does: the source's total, else input + output; null when it says nothing.
export function tokenTotal(tokens) {
  if (!tokens) return null;
  if (isKnown(tokens.total)) return tokens.total;
  if (isKnown(tokens.input) || isKnown(tokens.output)) return (isKnown(tokens.input) ? tokens.input : 0) + (isKnown(tokens.output) ? tokens.output : 0);
  return null;
}

// Whole numbers that add up to exactly 100 (largest remainder); null when there is nothing to share.
export function sharesOf(values) {
  const sum = values.reduce((all, value) => all + value, 0);
  if (!(sum > 0)) return values.map(() => null);
  const exact = values.map((value) => (value * 100) / sum);
  const shares = exact.map(Math.floor);
  let left = 100 - shares.reduce((all, value) => all + value, 0);
  const order = exact.map((value, index) => [value - Math.floor(value), index]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (const [, index] of order) {
    if (left <= 0) break;
    shares[index] += 1;
    left -= 1;
  }
  return shares;
}

// The class that gives a bar its width: steps of 5 %, so the stylesheet needs no inline style.
export const barStep = (percent) => (isKnown(percent) ? Math.min(100, Math.max(0, Math.round(percent / 5) * 5)) : 0);

// Sources whose `input` already counts the cached part (services/usage.py CONVENTIONS); for the others (claude, antigravity,
// copilot) the cache is reported apart from `input`.
const INPUT_INCLUDES_CACHE = ["codex", "framework"];

// { active, total, tokens: { total, partial }, cache: { percent, partial } }. Orchestrator plus workers, except for a
// framework session: its tokens are already the sum of its steps, so they count only when there are no children to add.
// Claude, Codex and Antigravity keep a child's usage in its own record, apart from the session's, so for them it is a sum.
// Tokens: partial when any member reports none. Cache: the share of the input that came from cache; the denominator
// follows each member's source (input, bounded below by cache_read, or input + cache_read). Only members that report
// both count, and null («no data») when none does.
export function headerStats(children = [], summary = null) {
  const counts = countStatuses(children);
  const own = summary && !(summary.source === "framework" && children.length > 0) ? [{ tokens: summary.tokens, source: summary.source }] : [];
  const members = [...(summary ? own : [{ tokens: null, source: null }]), ...children.map((child) => ({ tokens: child.tokens, source: child.source }))];
  let sum = 0;
  let known = 0;
  let denominator = 0;
  let cached = 0;
  let cacheMembers = 0;
  for (const { tokens, source } of members) {
    const total = tokenTotal(tokens);
    if (total !== null) { sum += total; known += 1; }
    if (tokens && isKnown(tokens.cache_read) && isKnown(tokens.input)) {
      denominator += INPUT_INCLUDES_CACHE.includes(source) ? Math.max(tokens.input, tokens.cache_read) : tokens.input + tokens.cache_read;
      cached += tokens.cache_read;
      cacheMembers += 1;
    }
  }
  return {
    active: counts.active,
    total: children.length,
    tokens: { total: known ? sum : null, partial: known > 0 && known < members.length },
    cache: { percent: cacheMembers && denominator > 0 ? Math.round((cached * 100) / denominator) : null, partial: cacheMembers > 0 && cacheMembers < members.length },
  };
}

// One entry per level that has workers: { level, percent, step, partial }. The shares of the workers' tokens add up to 100;
// percent is null (no bar) when no worker reports tokens, and partial when some worker of the level reports none.
export function levelShares(children = []) {
  const grouped = groupByLevel(children);
  const levels = [...LEVELS, UNKNOWN_LEVEL].filter((level) => grouped[level].length > 0);
  const sums = levels.map((level) => grouped[level].reduce((all, child) => all + (tokenTotal(child.tokens) || 0), 0));
  const shares = sharesOf(sums);
  return levels.map((level, index) => {
    // a level where no worker reports tokens has no share (never 0 %), even when other levels do
    const reports = grouped[level].some((child) => tokenTotal(child.tokens) !== null);
    const percent = reports ? shares[index] : null;
    return {
      level,
      percent,
      step: barStep(percent),
      partial: grouped[level].some((child) => tokenTotal(child.tokens) === null),
    };
  });
}

const RUN_STATUS = { running: "running", queued: "idle", pending: "idle", waiting_human: "idle", waiting_review: "idle", done: "done", failed: "stopped" };
const WAITING_STATES = ["waiting_human", "waiting_review"];

// The steps of a run (the answer of /gui/api/runs/{id}) as tree nodes. Steps that did not run or will not (skipped,
// not_run, will_skip) are left out. The run carries no tokens per step: they are unknown, never 0.
export function runStepChildren(run) {
  const steps = run && Array.isArray(run.steps) ? run.steps : [];
  return steps.filter((step) => step && step.state in RUN_STATUS).map((step) => ({
    id: "step:" + step.id,
    source: "framework",
    kind: "step",
    name: step.id,
    label: step.agent || step.function || null,
    model: step.executor && step.executor.model ? step.executor.model : null,
    level: null,
    status: RUN_STATUS[step.state],
    note: WAITING_STATES.includes(step.state) ? "waiting_human" : null,
    started_at: null,
    last_activity_at: null,
    tokens: null,
    link_id: null,
  }));
}

// Text of the figures: «2 of 4 active», tokens (or «partial»), cache share (or «no data»).
export function headerTexts(stats) {
  const tokens = stats.tokens.total === null
    ? t("agent_tree.stats.no_data")
    : compactTokens({ total: stats.tokens.total }) + (stats.tokens.partial ? " · " + t("agent_tree.stats.partial") : "");
  const cache = stats.cache.percent === null
    ? t("agent_tree.stats.no_data")
    : t("agent_tree.stats.percent", { value: stats.cache.percent }) + (stats.cache.partial ? " · " + t("agent_tree.stats.partial") : "");
  return { active: t("agent_tree.stats.active_of", { active: stats.active, total: stats.total }), tokens, cache };
}
