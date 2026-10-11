// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// What the shell remembers between visits (GUI2-T02): whether the right panel was left open. It is a convenience of
// this browser only, so every access to localStorage sits in try/catch (private windows, blocked site data and the
// like throw or return nothing) and the page works the same without it.
const PANEL_KEY = "multiagent.gui.right-panel";

// Below this width the right panel stacks under the content and the sidebar becomes a drawer (app.css agrees).
export const WIDE_QUERY = "(min-width: 900px)";
export const NARROW_QUERY = "(max-width: 899.98px)";

// Wide screens start with the panel open; narrow ones start with it closed, so the content comes first.
export function defaultPanelOpen() {
  try { return window.matchMedia(WIDE_QUERY).matches; } catch (_) { return true; }
}

export function readPanelOpen(fallback) {
  try {
    const value = window.localStorage.getItem(PANEL_KEY);
    if (value === "open") return true;
    if (value === "closed") return false;
  } catch (_) { /* storage blocked: use the default */ }
  return fallback;
}

export function writePanelOpen(open) {
  try { window.localStorage.setItem(PANEL_KEY, open ? "open" : "closed"); } catch (_) { /* storage blocked: not remembered */ }
}
