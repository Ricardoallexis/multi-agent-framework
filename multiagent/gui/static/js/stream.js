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

// Starts the stream; returns a stop function. onStatus gets connected|reconnecting|offline|unauthorized.
export function startStream({ token, onEvent, onStatus }) {
  let cursor = "0";
  let delay = MIN_DELAY;
  let stopped = false;
  let controller = null;

  async function run() {
    while (!stopped) {
      controller = new AbortController();
      try {
        const response = await fetch("/gui/api/stream?after=" + encodeURIComponent(cursor), {
          headers: { ...authHeaders(token), Accept: "text/event-stream", "Last-Event-ID": cursor },
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) { onStatus("unauthorized"); return; }
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
