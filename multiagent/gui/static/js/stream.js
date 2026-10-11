// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// SSE client over fetch (EventSource cannot send the Authorization header).
import { authHeaders } from "./auth.js";

const MIN_DELAY = 1000;
const MAX_DELAY = 30000;

export function parseBlock(block) {
  const event = { id: null, type: "message", data: "" };
  const data = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const split = line.indexOf(":");
    const field = split < 0 ? line : line.slice(0, split);
    const value = split < 0 ? "" : line.slice(split + 1).replace(/^ /, "");
    if (field === "id") event.id = value;
    else if (field === "event") event.type = value;
    else if (field === "data") data.push(value);
  }
  event.data = data.join("\n");
  return data.length ? event : null;
}

async function failureMessage(response) {
  try {
    const body = await response.json();
    const detail = body && body.detail;
    if (detail && typeof detail === "object" && detail.message) return String(detail.message);
    if (typeof detail === "string") return detail;
  } catch (_) { /* no JSON body */ }
  return "HTTP " + response.status;
}

// Starts the stream; returns a stop function. onStatus(value, info) gets connected|reconnecting|offline|
// unauthorized, and failed for a session stream the server refuses for good (4xx; info = { status, message }).
// Without `session` it carries every run event of the system store. With `session` ("<source>:<id>") it follows
// that one session from `after`, the cursor of the last item the caller already holds.
export function startStream({ token, session = null, after = "0", onEvent, onStatus }) {
  let cursor = after;
  let delay = MIN_DELAY;
  let stopped = false;
  let controller = null;

  async function run() {
    while (!stopped) {
      controller = new AbortController();
      try {
        const query = (session ? "session=" + encodeURIComponent(session) + "&" : "") + "after=" + encodeURIComponent(cursor);
        const response = await fetch("/gui/api/stream?" + query, {
          headers: { ...authHeaders(token), Accept: "text/event-stream", "Last-Event-ID": cursor },
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) { onStatus("unauthorized"); return; }
        if (session && response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          onStatus("failed", { status: response.status, message: await failureMessage(response) });
          return;
        }
        if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
        onStatus("connected");
        delay = MIN_DELAY;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          let end;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const event = parseBlock(buffer.slice(0, end));
            buffer = buffer.slice(end + 2);
            if (!event) continue;
            if (event.id) cursor = event.id;
            try { onEvent(event); } catch (_) { /* a bad handler must not drop the stream */ }
          }
        }
      } catch (_) {
        if (stopped) return;
      }
      if (stopped) return;
      onStatus(navigator.onLine === false ? "offline" : "reconnecting");
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, MAX_DELAY);
    }
  }

  run();
  return () => { stopped = true; if (controller) controller.abort(); };
}
