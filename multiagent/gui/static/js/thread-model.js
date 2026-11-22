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
  const entries = [...byKey.values()].sort((a, b) => a.seq - b.seq);
  // A historic stop remains in the conversation, but only the current stop can offer actions.
  const active = page.status === "waiting_human"
    ? [...entries].reverse().find((entry) => entry.kind === "waiting" && entry.decision) : null;
  return { entries: entries.map((entry) => entry.kind === "waiting" && entry !== active
    ? { ...entry, decision: null } : entry), cursor: Math.max(state.cursor, page.cursor || 0),
  status: page.status || state.status, loaded: true };
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
