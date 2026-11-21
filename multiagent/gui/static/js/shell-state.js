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
  announceChoice({ help: Boolean(on) });
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
  announceChoice({ modes: normalizeModes(modes) });
}

// GUI3-T06: the entry box starts in the last chosen mode, even when storage is blocked.
const NEW_SESSION_MODE_KEY = "multiagent.gui.new-session-mode";

export function readNewSessionMode() {
  return readStored(NEW_SESSION_MODE_KEY) === "single" ? "single" : "orchestrator";
}

export function writeNewSessionMode(mode) {
  writeStored(NEW_SESSION_MODE_KEY, mode === "single" ? "single" : "orchestrator");
}

/* GUI3-T07 */
// «Personalizar» changes three stored choices (the pinned sections, the modes of the cards, help mode). Whoever draws one of
// them hears the change through onChoiceChange as { pinned } | { modes } | { help }: the value travels with the message, so the
// choice applies to the open page even when storage is blocked (it is then just not remembered).
const choiceListeners = new Set();

function announceChoice(change) {
  for (const listener of [...choiceListeners]) {
    try { listener(change); } catch (_) { /* one listener must not stop the others */ }
  }
}

export function onChoiceChange(listener) {
  choiceListeners.add(listener);
  return () => choiceListeners.delete(listener);
}

export function writePinnedSections(ids) {
  const list = [...new Set(Array.isArray(ids) ? ids.filter((id) => typeof id === "string") : [])];
  writeStored(PINNED_KEY, JSON.stringify(list));
  announceChoice({ pinned: list });
}

// «Restablecer»: the sidebar, the cards, help mode and (GUI3-T09) the accent go back to their defaults.
export function resetChoices() {
  writePinnedSections([]);
  writeCardModes({});
  writeHelpMode(false);
  writeAccent(DEFAULT_ACCENT);
}

// Does this browser remember a choice? It writes and reads back a probe through the same two functions as everything else.
const PROBE_KEY = "multiagent.gui.storage-probe";

export function storageRemembers() {
  writeStored(PROBE_KEY, "1");
  return readStored(PROBE_KEY) === "1";
}

/* GUI3-T09 */
// The accent palette ("neutral", "azul", "verde" or "violeta"; customize-model.js lists them, app.css holds the colours).
// Without storage, or with a stored id that does not exist, it is "neutral". The change is announced as { accent }.
import { DEFAULT_ACCENT, normalizeAccent } from "./customize-model.js";

const ACCENT_KEY = "multiagent.gui.accent";

export function readAccent() {
  return normalizeAccent(readStored(ACCENT_KEY));
}

export function writeAccent(id) {
  const accent = normalizeAccent(id);
  writeStored(ACCENT_KEY, accent);
  announceChoice({ accent });
}
