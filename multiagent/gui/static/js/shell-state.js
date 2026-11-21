// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// What the shell remembers between visits (GUI2-T02): whether the right panel was left open. It is a convenience of
// this browser only, so every access to localStorage sits in try/catch (private windows, blocked site data and the
// like throw or return nothing) and the page works the same without it.
const PANEL_KEY = "multiagent.gui.right-panel";

// Below this width the right panel stacks under the content and the sidebar becomes a drawer (app.css agrees).
export const WIDE_QUERY = "(min-width: 900px)";
export const NARROW_QUERY = "(max-width: 899.98px)";

// GUI3-T04: «Contexto» starts closed at every width, so the content comes first; the person opens it and the choice is
// remembered (readPanelOpen).
export function defaultPanelOpen() {
  return false;
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

// GUI3-T02: help mode. Off by default: the "?" blocks stay hidden until the user asks for them (button or F1). Without
// storage the mode comes back off.
const HELP_MODE_KEY = "multiagent.gui.help-mode";

export function readHelpMode() {
  return readStored(HELP_MODE_KEY) === "on";
}

export function writeHelpMode(on) {
  writeStored(HELP_MODE_KEY, on ? "on" : "off");
}

// F1 toggles help mode and nothing else is captured; the modified combinations keep their browser meaning.
export function isHelpKey(event) {
  return Boolean(event) && event.key === "F1" && !event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey && !event.isComposing;
}

// GUI3-T03: navigation choices and the one-time introduction to Más.
const PINNED_KEY = "multiagent.gui.pinned-sections";
const MORE_NOTICE_KEY = "multiagent.gui.more-notice";

export function readPinnedSections() {
  try {
    const value = JSON.parse(readStored(PINNED_KEY) || "[]");
    return Array.isArray(value) ? value.filter((id) => typeof id === "string") : [];
  } catch (_) { return []; }
}

export function readMoreNoticeSeen() {
  return readStored(MORE_NOTICE_KEY) === "seen";
}

export function writeMoreNoticeSeen() {
  writeStored(MORE_NOTICE_KEY, "seen");
}

// GUI3-T04: the mode of each card of the «Contexto» panel ({ card name: "auto" | "always" | "never" }). Without storage, or
// with a stored value that is not valid, the cards keep the mode they registered (context-model.js). The screen that changes
// them is GUI3-T07's.
import { normalizeModes } from "./context-model.js";

const CONTEXT_CARDS_KEY = "multiagent.gui.context-cards";

export function readCardModes() {
  try { return normalizeModes(JSON.parse(readStored(CONTEXT_CARDS_KEY) || "{}")); } catch (_) { return {}; }
}

export function writeCardModes(modes) {
  writeStored(CONTEXT_CARDS_KEY, JSON.stringify(normalizeModes(modes)));
}
