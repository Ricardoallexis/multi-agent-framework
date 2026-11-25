// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure run-thread state. A poll overlaps the last seq because synthetic entries may share it.

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
