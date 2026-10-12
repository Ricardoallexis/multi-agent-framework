// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Data of the right-panel sections «Cambios», «Tokens» and «Fuentes» (GUI2-T05). The three sections read the same
// session or run, so one loader per subject is shared between them (reference counted: it starts with the first section
// that asks for it and stops with the last). It only uses the token-protected /gui/api routes that already exist:
//   a session of an app  /sessions/{id}, /sessions/{id}/children and /sessions/{id}/items (read page by page from a
//                        cursor, so a live session is followed by asking only for what is new);
//   a framework run      /runs/{id} and /runs/{id}/usage.
// It reads from the server only while the panel can be seen, and slowly (every 10 s, every 30 s for a session that is
// not active, every 60 s for a finished run).
import { useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, runPath } from "./api.js";
import { MAX_ITEMS, PAGES_PER_TICK, PAGE_ITEMS, addItems, newDigest, snapshotDigest } from "./panel-model.js";

export const POLL_MS = 10000;
export const IDLE_POLL_MS = 30000;
export const FINISHED_POLL_MS = 60000;
const NEXT_PAGE_MS = 250;     // between rounds while the first read of a long session is still going

const sessionPath = (id, tail = "") => "/gui/api/sessions/" + encodeURIComponent(id) + tail;
const asApiError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
const isCursorReset = (warnings) => (warnings || []).some((warning) => String(warning).startsWith("cursor_reset"));

// False while the browser tab is in the background or the right panel is collapsed: nothing is asked then.
export function panelVisible() {
  if (typeof document === "undefined" || document.hidden) return false;
  const aside = document.getElementById("right-panel");
  return !(aside && aside.hidden);
}

// --- the shared loader ---------------------------------------------------------------------------------------------

export const LOADING = { status: "loading" };
const registry = new Map();

// Snapshot of the loader of `key` (null: nothing to load). start(publish) begins loading and returns the function that stops it.
export function useShared(key, start) {
  const [snapshot, setSnapshot] = useState(() => (key && registry.has(key) ? registry.get(key).snapshot : LOADING));
  useEffect(() => {
    if (!key) return undefined;
    let entry = registry.get(key);
    if (!entry) {
      entry = { snapshot: LOADING, listeners: new Set(), stop: () => {} };
      registry.set(key, entry);
      const owner = entry;
      owner.stop = start((next) => {
        owner.snapshot = next;
        for (const listener of [...owner.listeners]) listener(next);
      });
    }
    const listener = (next) => setSnapshot(next);
    entry.listeners.add(listener);
    setSnapshot(entry.snapshot);
    const owner = entry;
    return () => {
      owner.listeners.delete(listener);
      if (owner.listeners.size === 0) {
        owner.stop();
        if (registry.get(key) === owner) registry.delete(key);
      }
    };
  }, [key]);
  return snapshot;
}

// A poller: runs `step()` now and then again after the delay it returns. reload() asks for another round at once.
function poller(step) {
  let alive = true;
  let busy = false;
  let timer = 0;
  const run = async () => {
    if (!alive || busy) return;
    busy = true;
    let delay = POLL_MS;
    try {
      delay = await step();
    } catch (_) {
      delay = POLL_MS;
    } finally {
      busy = false;
    }
    if (alive) timer = setTimeout(run, delay);
  };
  run();
  return {
    stop: () => { alive = false; clearTimeout(timer); },
    reload: () => { clearTimeout(timer); run(); },
    isAlive: () => alive,
  };
}

// --- a session of an app -------------------------------------------------------------------------------------------

function startSession(token, subject, publish) {
  const id = subject.sessionId;
  const state = { status: "loading", error: null, summary: null, children: null, digest: newDigest(), cursor: null, done: false, complete: false, capped: false };
  let handle = null;
  // `loading` is true only until the first read of the session has reached its end; the later rounds only add what is new.
  const emit = () => publish({
    status: state.status, error: state.error, summary: state.summary, children: state.children,
    view: snapshotDigest(state.digest), loading: !state.complete, capped: state.capped,
    loaded: state.digest.items, reload: () => handle && handle.reload(),
  });

  // Reads the pages that follow the cursor (at most PAGES_PER_TICK now; the next round goes on) until there is nothing new.
  async function readItems() {
    state.done = false;
    for (let pages = 0; handle.isAlive() && !state.done && pages < PAGES_PER_TICK; pages += 1) {
      if (state.digest.items >= MAX_ITEMS) { state.capped = true; state.done = true; state.complete = true; return; }
      const query = "?limit=" + PAGE_ITEMS + (state.cursor ? "&after=" + encodeURIComponent(state.cursor) : "");
      const result = await apiGet(sessionPath(id, "/items") + query, token);
      if (!handle.isAlive()) return;
      if (state.cursor && isCursorReset(result.warnings)) state.digest = newDigest(); // the file was rewritten: start over
      addItems(state.digest, result.items);
      state.cursor = result.next_cursor;
      state.done = Boolean(result.done);
      if (state.done) state.complete = true;
      emit();
    }
  }

  async function step() {
    if (state.status === "ready" && !panelVisible()) return POLL_MS;
    try {
      const summary = apiGet(sessionPath(id), token);
      const children = apiGet(sessionPath(id, "/children"), token).catch(() => null);
      state.summary = await summary;
      state.children = await children;
      if (!handle.isAlive()) return POLL_MS;
      state.status = "ready";
      state.error = null;
      if (!state.capped) emit(); // the tokens are there before the items have all been read
      if (!state.capped) await readItems();
    } catch (failure) {
      if (!handle.isAlive()) return POLL_MS;
      state.error = asApiError(failure);
      if (!state.summary) state.status = "error";
    }
    if (!handle.isAlive()) return POLL_MS;
    emit();
    if (state.error) return POLL_MS;
    if (!state.done) return NEXT_PAGE_MS;
    return state.summary && state.summary.status === "active" ? POLL_MS : IDLE_POLL_MS;
  }

  handle = poller(step);
  return handle.stop;
}

// --- a framework run -----------------------------------------------------------------------------------------------

function startRun(token, subject, publish) {
  const state = { status: "loading", error: null, run: null, usage: null, usageError: null };
  let handle = null;
  const emit = () => publish({ status: state.status, error: state.error, run: state.run, usage: state.usage,
    usageError: state.usageError, reload: () => handle && handle.reload() });

  async function step() {
    if (state.status === "ready" && !panelVisible()) return POLL_MS;
    try {
      const run = apiGet(runPath(subject.runId), token);
      const usage = apiGet(runPath(subject.runId, "usage"), token).then((value) => ({ value }), (failure) => ({ failure }));
      state.run = await run;
      const used = await usage;
      if (!handle.isAlive()) return POLL_MS;
      state.usage = used.failure ? state.usage : used.value;
      state.usageError = used.failure ? asApiError(used.failure) : null;
      state.status = "ready";
      state.error = null;
    } catch (failure) {
      if (!handle.isAlive()) return POLL_MS;
      state.error = asApiError(failure);
      if (!state.run) state.status = "error";
    }
    if (!handle.isAlive()) return POLL_MS;
    emit();
    if (state.error) return POLL_MS;
    return state.run && state.run.terminal ? FINISHED_POLL_MS : POLL_MS;
  }

  handle = poller(step);
  return handle.stop;
}

// What the sections read: the snapshot of the loader of this session or run (see the fields in startSession / startRun).
export function usePanelData(token, subject) {
  return useShared(subject ? subject.key : null, (publish) => (subject.kind === "run" ? startRun : startSession)(token, subject, publish));
}
