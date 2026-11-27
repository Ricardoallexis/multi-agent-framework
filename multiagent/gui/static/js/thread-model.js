// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure run-thread state. A poll overlaps the last seq because synthetic entries may share it.

import { routeHref } from "./route.js";
import { t } from "./i18n.js";
import { TASK_STATE_TEXT } from "./plan-card-model.js";

export const POLL_MS = 4000;
export const createThread = () => ({ entries: [], cursor: 0, status: "", loaded: false });
export const pollAfter = (state) => Math.max(0, state.cursor - 1);

export function entryKey(entry) {
  return [entry.seq, entry.kind, entry.step_id || "", entry.attempt ?? "", entry.message_id || "",
    entry.kind === "final" ? entry.status || "" : ""].join("|");
}

export function mergeThread(state, page) {
  const byKey = new Map(state.entries.map((entry) => [entryKey(entry), entry]));
  for (const entry of page.entries || []) byKey.set(entryKey(entry), entry);
  // Array.sort is stable: synthetic entries that share a seq keep the order in which they arrived.
  const entries = [...byKey.values()].sort((a, b) => a.seq - b.seq);
  const cursor = Math.max(state.cursor, page.cursor || 0);
  const status = page.status || state.status;
  // A terminal run always ends with its closing message, even if the page that brought the status did not repeat it.
  if (isTerminal(status) && !entries.some((entry) => entry.kind === "final")) {
    entries.push({ seq: cursor, kind: "final", status, text: "Run " + status, output: null, error: "" });
  }
  // A historic stop remains in the conversation, but only the current stop can offer actions.
  const active = page.status === "waiting_human"
    ? [...entries].reverse().find((entry) => entry.kind === "waiting" && entry.decision) : null;
  return { entries: entries.map((entry) => entry.kind === "waiting" && entry !== active
    ? { ...entry, decision: null } : entry), cursor, status, loaded: true };
}

export const isTerminal = (status) => ["completed", "failed", "cancelled", "rejected"].includes(status);
export const actionsFor = (entry, status) => entry.kind === "waiting" && status === "waiting_human"
  ? (entry.decision?.actions || []).filter((action) => ["approve", "changes", "reject"].includes(action)) : [];
export const detailHref = (runId) => "#runs/" + encodeURIComponent(runId);

// The current decision identifies one attempt; never substitute another attempt's artifact.
export const reviewArtifact = (view, entry) => (view?.artifacts || []).find((item) =>
  item.step_id === entry.step_id && Number(item.attempt) === Number(entry.attempt)) || null;

export function previousStepIds(view, entry) {
  const order = (view?.steps || []).map((step) => step.id);
  const stop = order.indexOf(entry.step_id);
  if (stop < 0) return [];
  const earlier = new Set(order.slice(0, stop));
  return [...new Set((view.artifacts || []).map((item) => item.step_id).filter((id) => earlier.has(id)))];
}

// SIM-T03: what the thread shows in each mode. Advanced shows everything, as it always did; simple hides the raw JSON, the model
// calls, the earlier steps, the «Ver detalle» link and the links to the advanced views (plan, team). Only «advanced» shows them.
export const showRawOutput = (mode) => mode === "advanced";
export const threadLinksFor = (entry, mode) => ({
  detail: showRawOutput(mode),
  related: showRawOutput(mode) && Boolean(entry && (entry.task_href || entry.team_href)),
});

// EJC-T01: the run reads as one conversation.
// A review that is no longer the active stop folds away; the current one (the entry that still carries a decision) stays open.
export const REVIEW_REASONS = ["review", "content_review", "budget_exhausted", "function_review"];
export const isEarlierReview = (entry, status) => entry.kind === "waiting" && REVIEW_REASONS.includes(entry.reason)
  && !(status === "waiting_human" && entry.decision);
// Consecutive earlier reviews share one fold; every other entry stays where it is.
export function groupThread(entries, status) {
  const items = [];
  for (const entry of entries) {
    const last = items[items.length - 1];
    if (!isEarlierReview(entry, status)) items.push({ type: "entry", entry });
    else if (last && last.type === "earlier") last.entries.push(entry);
    else items.push({ type: "earlier", entries: [entry], key: "earlier|" + entryKey(entry) });
  }
  return items;
}

// Scroll: a new message follows the end only when the person was already reading the end.
export const NEAR_END_PX = 80;
export const isNearEnd = ({ top, height, total }, slack = NEAR_END_PX) => total - (top + height) <= slack;
// A smooth scroll that the page itself started fires scroll events on the way; they must not read as «the person left the end».
export const SMOOTH_MS = 700;
export const nextFollowing = (current, guardUntil, now, position) => now < guardUntil ? current : isNearEnd(position);
export const tailKey = (entries) => entries.length + "|" + (entries.length ? entryKey(entries[entries.length - 1]) : "");
export const shouldFollow = (wasNearEnd, before, after) => Boolean(wasNearEnd) && before !== after;

// The message box needs a policy that answered and said «enabled». A failed read is an error, never «disabled».
export function composerState({ policy, error, status }) {
  if (error) return "error";
  if (!policy) return "loading";
  if (!policy.enabled) return "hidden";
  return isTerminal(status) ? "ended" : "ready";
}

// «Citar»: the selection becomes quoted plain text in front of what the person already wrote.
export const QUOTE_LIMIT = 1500;
export function quoteInto(current, selected) {
  const text = String(selected || "").trim();
  if (!text) return current || "";
  const clipped = text.length > QUOTE_LIMIT ? text.slice(0, QUOTE_LIMIT) + "…" : text;
  return clipped.split(/\r?\n/).map((line) => "> " + line).join("\n") + "\n\n" + (current || "");
}

// «Pedir cambios»: free text is the main road. Structured comments (advanced mode) are sent only when they hold something;
// an empty one is dropped, a half-filled one is reported. There is no limit on how many corrections a person makes.
export function changesBody(feedback, comments, { structured = false } = {}) {
  const text = String(feedback || "").trim();
  const kept = [];
  for (const item of structured ? comments || [] : []) {
    const body = String(item.text || "").trim();
    const quote = String(item.quote || "").trim();
    if (!body && !quote) continue;
    const target = String(item.target || "").trim();
    if (!body || !target) return { error: "incomplete" };
    kept.push({ target, kind: item.kind || "fix", text: body, quote: quote || null });
  }
  if (!text && !kept.length) return { error: "required" };
  return { body: kept.length ? { feedback: text, comments: kept } : { feedback: text } };
}

// The LIM notice uses only the data the run detail sent (`limit`); nothing is estimated here and it never switches the model.
export function modelLimitNotice(limit) {
  if (!limit || typeof limit !== "object" || limit.code !== "limit_reached" || !limit.target) return null;
  const first = Array.isArray(limit.suggestions) && limit.suggestions.length ? limit.suggestions[0] : "";
  const cost = limit.context_cost;
  const notice = { model: String(limit.target) };
  if (first) notice.suggested_model = String(first);
  if (first && cost !== undefined && cost !== null && String(cost).trim() !== "") notice.context_cost = String(cost);
  return notice;
}

// GUI4-T14: the row of shortcuts in the header of a conversation (a session and a run thread draw the same row).
// Each shortcut is a link with its address. One that has no destination, or whose view does not exist in the person's mode, is
// not in the list at all (never drawn dimmed): «Árbol» and «Mapa» are views of advanced mode only, «Uso» and «Proyecto» exist in both.
//   workers   number of agents under the conversation; null when the source does not say (a framework run), which keeps «Árbol»
//             as it always was in advanced mode, and 0 hides it because there is nothing to see
//   project   { id, name } of the project the conversation belongs to, or null: «Proyecto» needs the id, and no query is made to find it
export const SHORTCUT_IDS = ["tree", "project", "usage", "map"];
export const projectOf = (source) => {
  const id = source && typeof source.project_id === "string" ? source.project_id.trim() : "";
  if (!id) return null;
  const name = [source.project_title, source.project_name].find((value) => typeof value === "string" && value.trim());
  return { id, name: name ? name.trim() : "" };
};
export function shortcutsFor({ mode = "advanced", workers = null, project = null, sessionId = "" } = {}) {
  const advanced = mode !== "simple";
  const list = [];
  if (advanced && sessionId && (workers === null || workers > 0)) {
    list.push({ id: "tree", href: routeHref("tree", sessionId), label: "sessions.agent_tree", data: {} });
  }
  if (project && project.id) {
    list.push({ id: "project", href: routeHref("project", project.id), data: { name: project.name || "" },
      label: project.name ? "thread.shortcut.project_named" : "thread.shortcut.project" });
  }
  list.push({ id: "usage", href: routeHref("usage"), label: "thread.shortcut.usage", data: {} });
  if (advanced) list.push({ id: "map", href: routeHref("map"), label: "thread.shortcut.map", data: {} });
  return list;
}

// ACP-T04: the live thread of a Codex ACP conversation (#new/acp/<id>). Pure state; everything the agent sends is untrusted text.
// The server numbers every event (`cursor`, 1, 2, 3...). A page (GET ?after=) and the stream (item events) bring the same events:
// they merge by cursor, so a reconnect from the last cursor never repeats or loses one.
export const ACP_KINDS = ["prompt", "message", "thought", "tool", "diff", "plan", "usage", "end_turn", "error"];
export const ACP_TOOL_STATUSES = ["pending", "in_progress", "completed", "failed"];
export const ACP_TEXT_LIMIT = 8000;
// ACP-T05: a conversation is `saved` when the server read it from its file (read only, no agent session behind it). `reload` is
// set for one read, when a conversation that was followed live turned out to be a saved file whose events number from 1 again.
export const createAcpThread = () => ({ events: [], cursor: 0, active: false, consentConfigured: null, loaded: false, lost: false,
  saved: false, redacted: false, incomplete: false, historyWarning: null, reload: false });
export const acpPath = (id, rest = "") => "/gui/api/threads/acp/" + encodeURIComponent(id) + rest;
export const acpStreamPath = (id, cursor) => acpPath(id, "/stream?after=" + Math.max(0, Number(cursor) || 0));

const validEvent = (event) => Boolean(event) && Number.isInteger(event.cursor) && event.cursor > 0 && ACP_KINDS.includes(event.kind);

export function mergeAcpEvents(state, incoming) {
  const byCursor = new Map(state.events.map((event) => [event.cursor, event]));
  for (const event of Array.isArray(incoming) ? incoming : []) {
    if (validEvent(event) && !byCursor.has(event.cursor)) byCursor.set(event.cursor, event);
  }
  const events = [...byCursor.values()].sort((a, b) => a.cursor - b.cursor);
  return { ...state, events, cursor: Math.max(state.cursor, events.length ? events[events.length - 1].cursor : 0) };
}

export const ACP_HISTORY_WARNINGS = ["history_not_saved", "history_size_limit"];

// A page of GET /threads/acp/{id}?after=: events, next_cursor, active, consent_configured, and for ACP-T05 `saved`,
// `history_warning`, `redacted` and `incomplete`. A conversation followed live that now comes back `saved` has the cursors of the
// file, not the ones it holds: it starts again from nothing and asks for one more read from the first event (`reload`).
export function mergeAcpPage(state, page) {
  if (page && page.saved === true && !state.saved && state.cursor > 0) {
    return { ...createAcpThread(), saved: true, reload: true, consentConfigured: state.consentConfigured };
  }
  const merged = mergeAcpEvents(state, page && page.events);
  const next = Number.isInteger(page && page.next_cursor) ? Math.max(merged.cursor, page.next_cursor) : merged.cursor;
  const saved = Boolean(page) && page.saved === true;
  return { ...merged, cursor: next, loaded: true, reload: false, saved,
    active: typeof (page && page.active) === "boolean" ? page.active : merged.active,
    consentConfigured: typeof (page && page.consent_configured) === "boolean" ? page.consent_configured : merged.consentConfigured,
    redacted: saved && page.redacted === true, incomplete: saved && page.incomplete === true,
    historyWarning: !saved && page && ACP_HISTORY_WARNINGS.includes(page.history_warning) ? page.history_warning : null };
}

// One block of the stream, already parsed ({ type, data }): an `item` is an event, an `error` says the session is gone for good.
export function applyAcpStreamBlock(state, block) {
  if (!block) return state;
  if (block.type === "error") return { ...state, lost: true, active: false };
  if (block.type !== "item") return state;
  let event = null;
  try { event = JSON.parse(block.data); } catch (_) { return state; }
  return mergeAcpEvents(state, [event]);
}

// A conversation that no longer exists: a 404 of any call (it was deleted, expired, or was never saved).
export const isAcpLost = (failure) => Boolean(failure) && failure.status === 404;

const clip = (text, limit = ACP_TEXT_LIMIT) => (text.length > limit ? text.slice(0, limit) + "…" : text);
const asText = (value) => (typeof value === "string" ? clip(value) : "");
const plain = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

// The text blocks and the diffs of a `content` field, which is an object, a list of blocks or a plain `text` of the event.
function readContent(data, limit = ACP_TEXT_LIMIT) {
  const texts = [];
  const diffs = [];
  const visit = (block, depth) => {
    if (depth > 3 || !block) return;
    if (Array.isArray(block)) { block.forEach((item) => visit(item, depth + 1)); return; }
    if (typeof block === "string") { texts.push(clip(block, limit)); return; }
    if (typeof block !== "object") return;
    if (block.type === "diff" || (typeof block.path === "string" && (block.newText !== undefined || block.oldText !== undefined))) {
      diffs.push({ path: asText(block.path), oldText: asText(block.oldText), newText: asText(block.newText) });
      return;
    }
    if (typeof block.text === "string") texts.push(clip(block.text, limit));
    else if (block.content) visit(block.content, depth + 1);
  };
  visit(data.text, 0);
  visit(data.content, 0);
  return { texts, diffs };
}

const pathsOf = (data) => (Array.isArray(data.locations) ? data.locations : [])
  .map((location) => (typeof location === "string" ? location : plain(location).path))
  .filter((path) => typeof path === "string" && path).map((path) => clip(path));

const planRows = (data) => (Array.isArray(data.entries) ? data.entries : []).map((entry) => {
  const row = plain(entry);
  return { text: asText(row.content), status: asText(row.status), priority: asText(row.priority) };
}).filter((row) => row.text);

// The shape a card draws. Nothing here is HTML: every field is a string the view puts in as text. `limit` is the clip of one text:
// a group of chunks reads each chunk whole and clips the joined text once, the way the server does before it saves a turn.
export function acpEventView(event, limit = ACP_TEXT_LIMIT) {
  const data = plain(event && event.data);
  const base = { kind: event.kind, cursor: event.cursor, status: ACP_TOOL_STATUSES.includes(event.tool_status) ? event.tool_status : null,
    text: "", title: "", paths: [], diffs: [], rows: [], used: null, size: null, cost: null, stop: "" };
  const { texts, diffs } = readContent(data, limit);
  if (event.kind === "prompt" || event.kind === "message" || event.kind === "thought") return { ...base, text: texts.join("\n") };
  if (event.kind === "tool") return { ...base, title: asText(data.title) || asText(data.kind), text: texts.join("\n"), paths: pathsOf(data), diffs };
  if (event.kind === "diff") return { ...base, title: asText(data.title), paths: pathsOf(data), diffs: diffs.length ? diffs
    : [{ path: asText(data.path), oldText: asText(data.oldText), newText: asText(data.newText) }].filter((item) => item.path), text: texts.join("\n") };
  if (event.kind === "plan") return { ...base, rows: planRows(data) };
  if (event.kind === "usage") {
    const cost = plain(data.cost);
    return { ...base, used: Number.isFinite(data.used) ? data.used : null, size: Number.isFinite(data.size) ? data.size : null,
      cost: Number.isFinite(cost.amount) ? { amount: cost.amount, currency: asText(cost.currency) } : null };
  }
  if (event.kind === "end_turn") return { ...base, stop: asText(data.stop_reason) };
  return { ...base, text: asText(data.message) || asText(data.status) || asText(data.code) || texts.join("\n") };   // error
}

// A warning of the history (a turn that was not saved, the size limit) is one line for the whole thread, not a card in the list.
const isHistoryWarning = (event) => event.kind === "error" && ACP_HISTORY_WARNINGS.includes(plain(event.data).status);

// Consecutive message chunks (and thought chunks) read as one block; every other event keeps its own place. The joined text of a
// block is clipped to ACP_TEXT_LIMIT with the same mark as the server, so the screen shows what the saved file holds.
export function groupAcpEvents(events) {
  const groups = [];
  for (const event of events) {
    if (isHistoryWarning(event)) continue;
    const last = groups[groups.length - 1];
    if ((event.kind === "message" || event.kind === "thought") && last && last.kind === event.kind) last.events.push(event);
    else groups.push({ kind: event.kind, events: [event], key: "acp-" + event.cursor });
  }
  return groups.map((group) => ({ kind: group.kind, key: group.key, cursor: group.events[group.events.length - 1].cursor,
    view: group.events.length === 1 ? acpEventView(group.events[0])
      : { ...acpEventView(group.events[0]), text: clip(group.events.map((event) => acpEventView(event, Infinity).text).join("")) } }));
}

// The notice about permissions is one line for the whole thread, only while the area has no consent configured.
export const acpNeedsConsentNotice = (state) => state.consentConfigured === false;
export const acpComposerState = (state) => (state.lost ? "lost" : !state.loaded ? "loading" : state.saved ? "saved" : state.active ? "active" : "idle");

// ACP-T05: the saved conversations, their deletion and the line that says a turn was not saved. The server numbers the file by itself.
export const ACP_LIST_PATH = "/gui/api/threads/acp";
export const ACP_DELETE_ALL_PATH = "/gui/api/threads/acp/delete-all";
export const acpDeletePath = (id) => acpPath(id, "/delete");
export const acpHistoryWarningKey = (state) => (state.historyWarning === "history_size_limit" ? "thread.acp.history.size_limit"
  : state.historyWarning === "history_not_saved" ? "thread.acp.history.not_saved" : "");
// The package of a conversation, read from the list (a page does not carry it): the link to start another one in the same area.
export function acpBundleOf(answer, id) {
  const found = (answer && Array.isArray(answer.conversations) ? answer.conversations : []).find((item) => item && item.id === id);
  return found && typeof found.bundle === "string" ? found.bundle : "";
}

// ACP-T04: the first message of a conversation is written in the thread view, in the package the route carries.
export const ACP_START_PATH = "/gui/api/threads/acp";
export const ACP_CONSENT_PATH = "/gui/api/connections/codex_acp/consent";
export const acpConsentReadPath = (bundle) => ACP_CONSENT_PATH + "?bundle=" + encodeURIComponent(bundle);
export const acpStartBody = (bundle, message) => {
  const body = typeof message === "string" ? message.trim() : "";
  return bundle && body ? { bundle, text: body } : null;
};
export const acpStartedId = (answer) => (answer && typeof answer.id === "string" && answer.id ? answer.id : "");
const ACP_UNAVAILABLE = ["acp_unavailable", "acp_activation_required", "acp_probe_required", "acp_area_required", "acp_session_limit",
  "executor_paused_changed", "executor_stopped_by_person"];
// A 409 reads «ACP no disponible» with its reason and keeps CLI as an option. Nothing starts CLI by itself. Keys, not texts.
export function acpStartFailure(failure) {
  const reason = failure && failure.details && typeof failure.details === "object" ? failure.details.reason : "";
  if (failure && failure.status === 409 && ACP_UNAVAILABLE.includes(reason)) {
    return { unavailable: true, reason, titleKey: "thread.acp.unavailable", textKey: "thread.acp.reason." + reason, message: "" };
  }
  if (failure && failure.status === 404) return { unavailable: false, reason: "not_found", titleKey: "", textKey: "thread.acp.start.not_found", message: "" };
  return { unavailable: false, reason: "failed", titleKey: "", textKey: "thread.acp.start.failed", message: (failure && failure.message) || "" };
}

// AVZ-T04: the data of the progress bar the run thread, the plan thread and the «En curso» group of «Contexto» share. Everything
// comes from what the view already loaded (the steps of a run, the task runs of a plan, the counts of a project); a bar with
// fewer than two pieces says nothing and is not drawn, and a missing list gives no bar, never an invented percentage. Each
// builder returns null or { segments, summary, line, total, done }: `segments` go to SegmentBar (segment-bar.js), `summary` is
// its sentence for a screen reader and `line` the text under the bar. The state is always written in words.
const STEP_TONE = { done: "success", skipped: "muted", will_skip: "warning", waiting_review: "warning", waiting_human: "warning",
  running: "active", queued: "muted", pending: "muted", failed: "danger", not_run: "muted" };
const STEP_ACTIVE = ["running", "queued", "waiting_review", "waiting_human", "failed"];
const STEP_PASSIVE = ["done", "skipped", "will_skip"];
const MIN_PIECES = 2;
const MAX_PIECES = 60;

// The keys are held here, not written in the calls, so the model stays a pure module whose source holds no dictionary text.
const KEYS = { step: "thread.progress.step", stepsDone: "thread.progress.done", tasksDone: "plans.thread.progress.done",
  noState: "plans.taskRun.noState", taskState: "panel.progress.task.", stepState: "common.status.step." };
const stepText = (state) => t(KEYS.stepState + state + ".label");
const extrasOf = (pieces, skip) => {
  const found = new Map();
  for (const piece of pieces) if (!skip.includes(piece.state)) found.set(piece.state, { text: piece.text, count: (found.get(piece.state)?.count || 0) + 1 });
  return [...found.values()].map(({ text, count }) => count + " " + text);
};

// The steps of a run as the thread knows them. `steps` is the last list of the run view; the thread only re-reads it while the run waits
// or fails, so what the thread heard since brings it up to date. A step that was waiting counts as done in two cases only:
//   - the thread heard a completed message of that step AFTER its last wait (the first attempt of a step reports completed before its
//     review wait, so an older one proves nothing; a wait without a step id, such as the budget one, belongs to the step the list had
//     waiting, and when that is not one step the answer is unknown);
//   - the person approved, in this thread and in that very wait, a review of what the step had already produced («waiting_review»).
// Approving a wait that comes before the step runs («waiting_human») leaves it running until its completed message arrives; asking
// for changes leaves it running too. `decisions` maps a step id to { wait_seq, choice } (the seq of the wait that was decided,
// "approve" or "changes"); a decision about an older wait says nothing about a later one. When the step may be running again and
// nothing says so, the list is empty: no bar says less than a wrong one.
export function liveSteps(steps, entries = [], status = "", decisions = {}) {
  if (!Array.isArray(steps)) return [];
  const lastWait = new Map();
  const completedAt = new Map();
  const anonymous = [];
  const waitReason = new Map();   // AVZ-T05: the reason of the last wait of each step
  const moves = [];               // AVZ-T05: every wait or completed message of a named step: { id, seq }
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (!entry) continue;
    const seq = Number(entry.seq) || 0;
    if (entry.kind === "waiting" && !entry.step_id) anonymous.push(seq);
    if (!entry.step_id) continue;
    if (entry.kind === "waiting" || (entry.kind === "step" && entry.status === "completed")) moves.push({ id: entry.step_id, seq });
    if (entry.kind === "waiting") {
      if (seq >= (lastWait.get(entry.step_id) ?? -1)) waitReason.set(entry.step_id, entry.reason);
      lastWait.set(entry.step_id, Math.max(lastWait.get(entry.step_id) ?? -1, seq));
    } else if (entry.kind === "step" && entry.status === "completed") completedAt.set(entry.step_id, Math.max(completedAt.get(entry.step_id) ?? -1, seq));
  }
  const listed = steps.filter((step) => step && typeof step.id === "string" && step.id);
  const waitingIds = listed.filter((step) => step.state === "waiting_review" || step.state === "waiting_human").map((step) => step.id);
  let unknown = false;
  const anonymousLast = anonymous.length ? Math.max(...anonymous) : -1;
  if (waitingIds.length > 1 && anonymous.length && status !== "waiting_human") unknown = true;   // whose wait is it? nobody knows
  const waitOf = (id) => {
    const named = lastWait.get(id) ?? -1;
    return waitingIds.length === 1 && waitingIds[0] === id && status !== "waiting_human" ? Math.max(named, anonymousLast) : named;
  };
  const proven = (id) => completedAt.has(id) && completedAt.get(id) > waitOf(id);
  const choice = decisions && typeof decisions === "object" ? decisions : {};
  const ended = ["cancelled", "rejected"].includes(status);
  // AVZ-T05: the earlier wait of a step holds only while nothing later goes past it: the run stopped again (another wait, a failure), or the
  // thread heard a wait or a completed message of ANOTHER step after it. Then the fresh list says it all for that step.
  const outrun = (id) => !["running", "queued"].includes(status) || moves.some((move) => move.id !== id && move.seq > lastWait.get(id));
  const REVIEW_STOPS = ["review", "approval_pending", "budget_exhausted"];
  const live = listed.map((step) => {
    let state = Object.prototype.hasOwnProperty.call(STEP_TONE, step.state) ? step.state : "pending";
    const waiting = state === "waiting_review" || state === "waiting_human";
    if (state === "will_skip") {
      if (ended) state = "not_run";
      else if (status === "completed") state = "skipped";
    } else if (waiting && status !== "waiting_human") {
      const decision = choice[step.id];
      const valid = Boolean(decision) && lastWait.has(step.id) && decision.wait_seq === waitOf(step.id);   // about this very wait
      if (proven(step.id) || status === "completed") state = "done";
      else if (ended) state = "not_run";
      else if (valid && decision.choice === "approve" && state === "waiting_review") state = "done";
      else if (valid && decision.choice === "approve") state = status === "queued" ? "queued" : "running";   // approved to run: not finished
      else if (valid && decision.choice === "changes") state = "running";
      else unknown = true;
    } else if (!waiting && !ended && status !== "completed" && ["done", "running", "queued"].includes(state) && lastWait.has(step.id) && !proven(step.id) && !outrun(step.id)) {
      // AVZ-T05: the list is fresh on every page, so it can say done, running or queued for a step that had a wait in this thread. The same
      // rule holds: only this person's decision about that very wait says what happened; a decision taken elsewhere hides the bar.
      const decision = choice[step.id];
      const valid = Boolean(decision) && decision.wait_seq === lastWait.get(step.id);
      const before = waitReason.get(step.id) && !REVIEW_STOPS.includes(waitReason.get(step.id));   // a wait before the step runs: the list's done is an older attempt
      if (valid && decision.choice === "approve") state = before ? (status === "queued" ? "queued" : "running") : state;   // approved here
      else if (valid && decision.choice === "changes") state = "running";
      else unknown = true;
    } else if (!waiting && !STEP_PASSIVE.includes(state) && state !== "failed" && proven(step.id)) {
      state = "done";
    } else if (ended && !waiting && !STEP_PASSIVE.includes(state) && state !== "failed") {
      state = "not_run";
    }
    return { id: step.id, state };
  });
  if (unknown) return [];
  if ((status === "running" || status === "queued") && !live.some((step) => ["running", "queued", "failed"].includes(step.state))) {
    const next = live.find((step) => !STEP_PASSIVE.includes(step.state) && step.state !== "not_run");
    if (next) next.state = status;
  }
  return live;
}

// The wait a decision is about: the seq of the last wait the thread heard for that step (null when it heard none).
export function waitSeqOf(entries, stepId) {
  const seqs = (Array.isArray(entries) ? entries : []).filter((entry) => entry && entry.kind === "waiting" && entry.step_id === stepId)
    .map((entry) => Number(entry.seq) || 0);
  return seqs.length ? Math.max(...seqs) : null;
}

export function stepBar(steps, entries = [], status = "", decisions = {}) {
  const live = liveSteps(steps, entries, status, decisions);
  if (live.length < MIN_PIECES) return null;
  const segments = live.map((step) => ({ id: step.id, state: step.state, tone: STEP_TONE[step.state], text: stepText(step.state) }));
  const done = live.filter((step) => step.state === "done").length;
  const at = live.findIndex((step) => STEP_ACTIVE.includes(step.state));
  const doneLine = t(KEYS.stepsDone, { done, total: live.length });
  const line = at < 0 ? doneLine : t(KEYS.step, { current: at + 1, total: live.length })
    + (["running", "queued"].includes(live[at].state) ? "" : " · " + segments[at].text);
  return { segments, summary: [doneLine, ...extrasOf(segments, ["done", "pending"])].join(", "), line, total: live.length, done };
}

// A plan: a piece per task once the plan is approved (its «task_run» messages carry the state). A cancelled task does not count.
const PLAN_STATE = { accepted: ["done", "success"], failed: ["failed", "danger"], running: ["doing", "active"], claimed: ["doing", "active"],
  validating: ["doing", "active"], paused: ["paused", "warning"], submitted: ["review", "attention"], needs_changes: ["changes", "warning"],
  blocked: ["blocked", "warning"], ready: ["ready", "info"] };

export function planBar(entries) {
  const runs = (Array.isArray(entries) ? entries : []).filter((entry) => entry && entry.kind === "task_run" && entry.state !== "cancelled");
  if (runs.length < MIN_PIECES) return null;
  const segments = runs.map((entry) => {
    const [state, tone] = PLAN_STATE[entry.state] || ["todo", "neutral"];
    return { id: entry.task_id || entry.seq, state, tone, text: TASK_STATE_TEXT[entry.state] || t(KEYS.noState) };
  });
  const done = segments.filter((piece) => piece.state === "done").length;
  const doneLine = t(KEYS.tasksDone, { done, total: segments.length });
  const extras = extrasOf(segments, ["done", "doing", "ready", "todo"]);
  return { segments, summary: [doneLine, ...extras].join(", "), line: extras.length ? doneLine + " · " + extras.join(", ") : doneLine,
    total: segments.length, done };
}

// A project of the list of «Proyectos»: it only carries how many tasks have each label, so the pieces are drawn in groups, finished
// first. A cancelled task does not count. Above MAX_PIECES the groups keep their share of the pieces (at least one each).
const COUNT_ORDER = [["done", "success"], ["doing", "active"], ["review", "attention"], ["changes", "warning"], ["failed", "danger"],
  ["blocked", "warning"], ["ready", "info"], ["todo", "neutral"]];

export function countPieces(counts) {
  const source = counts && typeof counts === "object" ? counts : {};
  const groups = COUNT_ORDER.map(([key, tone]) => ({ key, tone, count: Number.isInteger(source[key]) && source[key] > 0 ? source[key] : 0 }))
    .filter((group) => group.count > 0);
  const total = groups.reduce((sum, group) => sum + group.count, 0);
  if (total < MIN_PIECES) return null;
  const slots = groups.map((group) => (total <= MAX_PIECES ? group.count : Math.max(1, Math.floor((group.count * MAX_PIECES) / total))));
  for (let over = slots.reduce((sum, count) => sum + count, 0) - MAX_PIECES; over > 0; over -= 1) {
    slots[slots.indexOf(Math.max(...slots))] -= 1;   // the biggest group gives the pieces back, as condense() does in «Proyectos»
  }
  const segments = [];
  groups.forEach((group, at) => {
    const text = t(KEYS.taskState + group.key);
    for (let index = 0; index < slots[at]; index += 1) segments.push({ id: group.key + index, state: group.key, tone: group.tone, text });
  });
  const done = groups.find((group) => group.key === "done")?.count || 0;
  const doneLine = t(KEYS.tasksDone, { done, total });
  const extras = groups.filter((group) => ["review", "changes", "failed", "blocked"].includes(group.key))
    .map((group) => group.count + " " + t(KEYS.taskState + group.key));
  return { segments, summary: [doneLine, ...extras].join(", "), line: doneLine, total, done };
}
