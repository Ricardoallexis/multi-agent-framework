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

// The only two places that touch localStorage; every stored choice goes through them.
function readStored(key) {
  try { return window.localStorage.getItem(key); } catch (_) { /* storage blocked: no stored choice */ }
  return null;
}

function writeStored(key, value) {
  try { window.localStorage.setItem(key, value); } catch (_) { /* storage blocked: not remembered */ }
}

export function readPanelOpen(fallback) {
  const value = readStored(PANEL_KEY);
  if (value === "open") return true;
  if (value === "closed") return false;
  return fallback;
}

export function writePanelOpen(open) {
  writeStored(PANEL_KEY, open ? "open" : "closed");
}

// RWD-T03: whether the pending inbox may raise browser notifications. null means "never chosen" (the caller keeps its default).
const NOTIFICATIONS_KEY = "multiagent.gui.pending-notifications";

export function readNotificationsEnabled() {
  const value = readStored(NOTIFICATIONS_KEY);
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

export function writeNotificationsEnabled(enabled) {
  writeStored(NOTIFICATIONS_KEY, enabled ? "true" : "false");
}
