// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure run-thread state. A poll overlaps the last seq because synthetic entries may share it.

import { routeHref } from "./route.js";

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
export const ACP_KINDS = ["message", "thought", "tool", "diff", "plan", "usage", "end_turn", "error"];
export const ACP_TOOL_STATUSES = ["pending", "in_progress", "completed", "failed"];
export const ACP_TEXT_LIMIT = 8000;
export const createAcpThread = () => ({ events: [], cursor: 0, active: false, consentConfigured: null, loaded: false, lost: false });
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

// A page of GET /threads/acp/{id}?after=: events, next_cursor, active and consent_configured.
export function mergeAcpPage(state, page) {
  const merged = mergeAcpEvents(state, page && page.events);
  const next = Number.isInteger(page && page.next_cursor) ? Math.max(merged.cursor, page.next_cursor) : merged.cursor;
  return { ...merged, cursor: next, loaded: true,
    active: typeof (page && page.active) === "boolean" ? page.active : merged.active,
    consentConfigured: typeof (page && page.consent_configured) === "boolean" ? page.consent_configured : merged.consentConfigured };
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

// A session that no longer exists: a 404 of any call. It lives in memory, so a restart of the app loses it.
export const isAcpLost = (failure) => Boolean(failure) && failure.status === 404;

const clip = (text) => (text.length > ACP_TEXT_LIMIT ? text.slice(0, ACP_TEXT_LIMIT) + "…" : text);
const asText = (value) => (typeof value === "string" ? clip(value) : "");
const plain = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

// The text blocks and the diffs of a `content` field, which is an object, a list of blocks or a plain `text` of the event.
function readContent(data) {
  const texts = [];
  const diffs = [];
  const visit = (block, depth) => {
    if (depth > 3 || !block) return;
    if (Array.isArray(block)) { block.forEach((item) => visit(item, depth + 1)); return; }
    if (typeof block === "string") { texts.push(clip(block)); return; }
    if (typeof block !== "object") return;
    if (block.type === "diff" || (typeof block.path === "string" && (block.newText !== undefined || block.oldText !== undefined))) {
      diffs.push({ path: asText(block.path), oldText: asText(block.oldText), newText: asText(block.newText) });
      return;
    }
    if (typeof block.text === "string") texts.push(clip(block.text));
    else if (block.content) visit(block.content, depth + 1);
  };
  visit(data.text, 0);
  visit(data.content, 0);
  return { texts, diffs };
}

const pathsOf = (data) => (Array.isArray(data.locations) ? data.locations : [])
  .map((location) => (typeof location === "string" ? location : plain(location).path))
  .filter((path) => typeof path === "string" && path).map(clip);

const planRows = (data) => (Array.isArray(data.entries) ? data.entries : []).map((entry) => {
  const row = plain(entry);
  return { text: asText(row.content), status: asText(row.status), priority: asText(row.priority) };
}).filter((row) => row.text);

// The shape a card draws. Nothing here is HTML: every field is a string the view puts in as text.
export function acpEventView(event) {
  const data = plain(event && event.data);
  const base = { kind: event.kind, cursor: event.cursor, status: ACP_TOOL_STATUSES.includes(event.tool_status) ? event.tool_status : null,
    text: "", title: "", paths: [], diffs: [], rows: [], used: null, size: null, cost: null, stop: "" };
  const { texts, diffs } = readContent(data);
  if (event.kind === "message" || event.kind === "thought") return { ...base, text: texts.join("\n") };
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

// Consecutive message chunks (and thought chunks) read as one block; every other event keeps its own place.
export function groupAcpEvents(events) {
  const groups = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if ((event.kind === "message" || event.kind === "thought") && last && last.kind === event.kind) last.events.push(event);
    else groups.push({ kind: event.kind, events: [event], key: "acp-" + event.cursor });
  }
  return groups.map((group) => ({ kind: group.kind, key: group.key, cursor: group.events[group.events.length - 1].cursor,
    view: group.events.length === 1 ? acpEventView(group.events[0])
      : { ...acpEventView(group.events[0]), text: group.events.map((event) => acpEventView(event).text).join("") } }));
}

// The notice about permissions is one line for the whole thread, only while the area has no consent configured.
export const acpNeedsConsentNotice = (state) => state.consentConfigured === false;
export const acpComposerState = (state) => (state.lost ? "lost" : !state.loaded ? "loading" : state.active ? "active" : "idle");

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
