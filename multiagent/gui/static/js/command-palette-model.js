// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// What Ctrl+K finds and how the keys move through it (GUI3-T07). Pure functions: no DOM, no Preact, no fetch. The palette only
// navigates and explains: an entry is a screen (a route), a session or run (a route) or a help text (shown as plain text). There
// is no entry that approves, allows or launches anything, and nothing here talks to the server.
import { matches as itemMatches } from "./sidebar-model.js";
import { visibleIn } from "./gui-mode.js";
import { t } from "./i18n.js";

export const MAX_RESULTS = 30;
export const KINDS = {
  get section() { return t("palette.kind_section"); },
  get tab() { return t("palette.kind_tab"); },
  get session() { return t("palette.kind_session"); },
  get run() { return t("palette.kind_run"); },
  get help() { return t("palette.kind_help"); },
};

// Lower case without accents, so "ejecucion" finds "Ejecución".
const plain = (text) => String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// Ctrl+K (Cmd+K on a Mac) and nothing else: the modified and composed combinations keep their meaning.
export function isPaletteKey(event) {
  return Boolean(event) && String(event.key || "").toLowerCase() === "k" && Boolean(event.ctrlKey || event.metaKey)
    && !event.altKey && !event.shiftKey && !event.isComposing;
}

// The screens: every navigation section (the rows of the sidebar and of «Más») plus the tabs of «Pendientes». `tabs` are the
// tabs of pending-model.js (pendingTabs) with their route already resolved by `tabRoute`; «Todo» is the section itself.
// SIM-T02: only what the mode shows. The «Ayuda» row of simple mode is not a screen (it has an `action`), and a tab with no mode (the
// whole list, «Todo») is in both modes.
export function sectionEntries(navigation, tabs = [], mode = "advanced") {
  const entries = [];
  const seen = new Set();
  const add = (entry) => {
    const id = entry.route[0] + "/" + (entry.route[1] || "");
    if (seen.has(id)) return;
    seen.add(id);
    entries.push(entry);
  };
  for (const section of [...navigation.primary, ...navigation.more]) {
    if (section.action || !visibleIn(section, mode)) continue;
    add({ key: "section:" + section.id, kind: "section", label: section.label, route: [section.id, null] });
  }
  for (const tab of tabs) {
    if (tab.mode !== undefined && !visibleIn(tab, mode)) continue;
    add({ key: "tab:" + tab.id, kind: "tab", label: t("palette.pending_prefix") + tab.label, route: tab.route });
  }
  return entries;
}

// Sessions and runs, from sidebar-model.js items; the match is the one the sidebar used.
export function itemEntries(items, mode = "advanced") {
  return (Array.isArray(items) ? items : []).filter((item) => item.mode === undefined || visibleIn(item, mode)).map((item) => ({
    key: "item:" + item.key, kind: item.kind === "run" ? "run" : "session", label: item.title, route: item.route, item,
    hint: item.project,
  }));
}

// The help texts: a title, a hint and an explanation, shown as text.
export function helpEntries(help) {
  return Object.entries(help || {})
    .map(([key, entry]) => [key, t("help." + key) === "help." + key ? entry : t("help." + key)])
    .filter(([, entry]) => entry && typeof entry.title === "string")
    .map(([key, entry]) => ({
      key: "help:" + key, kind: "help", label: entry.title, route: null, hint: entry.hint || "",
      help: { title: entry.title, hint: entry.hint || "", more: Array.isArray(entry.more) ? entry.more : [entry.more].filter(Boolean) },
    }));
}

// Every word of the query must appear (no case, no accents). An empty query keeps the screens and the latest items only.
function hit(entry, words) {
  if (entry.item) return itemMatches(entry.item, words.join(" "));
  const haystack = plain(entry.label + " " + KINDS[entry.kind] + " " + (entry.kind === "help" ? entry.hint : ""));
  return words.every((word) => haystack.includes(word));
}

const ORDER = ["section", "tab", "session", "run", "help"];

export function searchPalette(entries, query, limit = MAX_RESULTS) {
  const words = plain(query).split(/\s+/).filter(Boolean);
  const list = Array.isArray(entries) ? entries : [];
  if (!words.length) {
    const screens = list.filter((entry) => entry.kind === "section" || entry.kind === "tab");
    return screens.concat(list.filter((entry) => entry.kind === "session" || entry.kind === "run").slice(0, 5)).slice(0, limit);
  }
  const found = list.filter((entry) => hit(entry, words));
  const rank = (entry) => ORDER.indexOf(entry.kind);
  return found.map((entry, index) => ({ entry, index })).sort((a, b) => rank(a.entry) - rank(b.entry) || a.index - b.index)
    .map((row) => row.entry).slice(0, limit);
}

// Arrow keys move one row and stop at both ends (no wrap).
export function moveIndex(index, key, length) {
  if (!length) return -1;
  const at = index < 0 ? 0 : Math.min(index, length - 1);
  if (key === "ArrowDown") return Math.min(at + 1, length - 1);
  if (key === "ArrowUp") return Math.max(at - 1, 0);
  return at;
}

// What a key does in the open palette: "move", "open" (Intro), or null. Esc is not here: the native dialog closes itself.
export function keyAction(event) {
  if (!event || event.isComposing) return null;
  if (["ArrowDown", "ArrowUp"].includes(event.key)) return "move";
  if (event.key === "Enter") return "open";
  return null;
}

// Opening an entry: a route to go to, or the help to show; never anything else.
export function openEntry(entry) {
  if (!entry) return null;
  if (entry.kind === "help") return { show: entry.help };
  return { route: entry.route };
}
