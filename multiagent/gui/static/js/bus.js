// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Fan-out of the single SSE stream: the shell publishes each event once, views subscribe.
const listeners = new Set();

// event is what stream.js gives: { id, type, data } where data is the JSON of the run event.
export function publishEvent(event) {
  let body = null;
  try { body = JSON.parse(event.data); } catch (_) { return; }
  if (!body || typeof body !== "object") return;
  for (const listener of [...listeners]) {
    try { listener(body); } catch (_) { /* one broken view must not silence the others */ }
  }
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// Runs fn at most once per wait window after the last call; cancel() drops a pending call.
export function debounce(fn, wait) {
  let timer = 0;
  const run = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => { timer = 0; fn(...args); }, wait);
  };
  run.cancel = () => { clearTimeout(timer); timer = 0; };
  return run;
}
