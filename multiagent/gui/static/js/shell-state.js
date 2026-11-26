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

// A function, not a const: i18n.js reads the preference while this module is still being linked (import cycle).
function languageKey() { return "multiagent.gui.language"; }

export function readLanguagePreference() {
  const value = readStored(languageKey());
  return value === "en" || value === "es" ? value : "auto";
}

export function writeLanguagePreference(choice) {
  writeStored(languageKey(), choice);
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

/* GUI4-T03 */
// B1: the width of the sidebar and of the «Contexto» panel is the person's. Both are stored as whole pixels (the panel's
// «hidden» choice is the one it always had, readPanelOpen; the sidebar's is readSideHidden). Without storage the widths
// come back as the defaults. A width dragged below HIDE_BELOW px hides the pane; shell.js applies the numbers through
// element.style.setProperty (CSSOM, which `style-src 'self'` does not block), never through a style attribute.
export const HIDE_BELOW = 18;
export const PANES = Object.freeze({
  side: Object.freeze({ min: 220, max: 340, def: 264, steps: Object.freeze([220, 264, 340]), key: "multiagent.gui.side-width" }),
  panel: Object.freeze({ min: 240, max: 400, def: 300, steps: Object.freeze([240, 300, 400]), key: "multiagent.gui.panel-width" }),
});
const SIDE_HIDDEN_KEY = "multiagent.gui.side-hidden";
export const KEY_STEP = 16;

// px (a drag) -> { hidden } when it is dragged to the edge, otherwise { width } kept inside the pane's range.
export function resolvePaneWidth(kind, px) {
  const pane = PANES[kind];
  const value = Number(px);
  if (!pane || !Number.isFinite(value)) return { hidden: false, width: pane ? pane.def : 0 };
  if (value < HIDE_BELOW) return { hidden: true, width: pane.def };
  return { hidden: false, width: Math.min(pane.max, Math.max(pane.min, Math.round(value))) };
}

// The arrow keys of the separator: ArrowLeft/ArrowRight move the edge, Home and End go to the ends, nothing else is used.
// `edge` is "right" for the sidebar (its edge moves with the arrow) and "left" for the panel (opposite direction).
export function keyWidth(kind, current, key, edge) {
  const pane = PANES[kind];
  if (!pane) return current;
  const grow = (edge === "left" ? key === "ArrowLeft" : key === "ArrowRight");
  const shrink = (edge === "left" ? key === "ArrowRight" : key === "ArrowLeft");
  if (key === "Home") return pane.min;
  if (key === "End") return pane.max;
  if (grow) return Math.min(pane.max, current + KEY_STEP);
  if (shrink) return Math.max(pane.min, current - KEY_STEP);
  return current;
}

export function readPaneWidth(kind) {
  const pane = PANES[kind];
  if (!pane) return null;
  const value = Number(readStored(pane.key));
  return Number.isFinite(value) && value >= pane.min && value <= pane.max ? Math.round(value) : null;
}

export function writePaneWidth(kind, width) {
  const pane = PANES[kind];
  if (!pane) return;
  if (width === null) writeStored(pane.key, "");   // «restablecer»: the default again
  else writeStored(pane.key, String(resolvePaneWidth(kind, width).width));
}

export function readSideHidden() {
  return readStored(SIDE_HIDDEN_KEY) === "hidden";
}

export function writeSideHidden(hidden) {
  writeStored(SIDE_HIDDEN_KEY, hidden ? "hidden" : "shown");
}

// The sections of the sidebar that the person folded ({ id: false }); an id that was never folded is open.
const SIDE_SECTIONS_KEY = "multiagent.gui.side-sections";

export function readSectionOpen(id) {
  try {
    const value = JSON.parse(readStored(SIDE_SECTIONS_KEY) || "{}");
    return !(value && typeof value === "object" && value[id] === false);
  } catch (_) { return true; }
}

export function writeSectionOpen(id, open) {
  let value = {};
  try { value = JSON.parse(readStored(SIDE_SECTIONS_KEY) || "{}") || {}; } catch (_) { value = {}; }
  if (typeof value !== "object" || Array.isArray(value)) value = {};
  value[String(id)] = Boolean(open);
  writeStored(SIDE_SECTIONS_KEY, JSON.stringify(value));
}

// Ctrl+B (⌘B on a Mac) hides and shows the sidebar. A browser may keep that key for itself; the buttons do the same.
export function isSidebarKey(event) {
  return Boolean(event) && String(event.key).toLowerCase() === "b" && Boolean(event.ctrlKey || event.metaKey)
    && !event.altKey && !event.shiftKey && !event.isComposing;
}

/* MAP-T01 */
// The layout of the work map ("list" | "tree") that the person chose last; the map opens as a list without it.
const MAP_LAYOUT_KEY = "multiagent.gui.work-map-layout";

export function readMapLayout() {
  const value = readStored(MAP_LAYOUT_KEY);
  return value === "tree" || value === "list" ? value : null;
}

export function writeMapLayout(layout) {
  writeStored(MAP_LAYOUT_KEY, layout === "tree" ? "tree" : "list");
}
