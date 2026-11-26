// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// What Ctrl+K finds and how the keys move through it (GUI3-T07). Pure functions: no DOM, no Preact, no fetch. The palette only
// navigates and explains: entries are screens, offices, areas, projects, sessions, runs, actions, or help text. Nothing approves,
// allows, launches, or talks to the server.
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
  get office() { return t("palette.kind_office"); },
  get area() { return t("palette.kind_area"); },
  get project() { return t("palette.kind_project"); },
  get action() { return t("palette.kind_action"); },
};

// Lower case without accents, so "ejecucion" finds "Ejecución".
const plain = (text) => String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

// Ctrl+K (Cmd+K on a Mac) and nothing else: the modified and composed combinations keep their meaning.
export function isPaletteKey(event) {
  return Boolean(event) && String(event.key || "").toLowerCase() === "k" && Boolean(event.ctrlKey || event.metaKey)
    && !event.altKey && !event.shiftKey && !event.isComposing;
}

// The screens: every navigation section (the rows of the sidebar and of «Más») plus the tabs of «Pendientes». `tabs` are the
// tabs of pending-model.js (pendingTabs) with their route already resolved by `tabRoute`; «Todo» is the section itself. An
// optional palette payload adds offices, projects, recent items, and office actions to the same searchable set.
// SIM-T02: only what the mode shows. The «Ayuda» row of simple mode is not a screen (it has an `action`), and a tab with no mode (the
// whole list, «Todo») is in both modes.
export function sectionEntries(navigation, tabs = [], mode = "advanced", paletteData = null) {
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
    add({
      key: "section:" + section.id, kind: "section", label: section.label, route: [section.id, null],
      ...(paletteData ? { group: "actions" } : {}),
    });
  }
  for (const tab of tabs) {
    if (tab.mode !== undefined && !visibleIn(tab, mode)) continue;
    add({
      key: "tab:" + tab.id, kind: "tab", label: t("palette.pending_prefix") + tab.label, route: tab.route,
      ...(paletteData ? { group: "actions" } : {}),
    });
  }
  if (paletteData) {
    entries.push(
      ...officeEntries(paletteData.offices, paletteData.areasByOffice),
      ...projectEntries(paletteData.projects),
      ...paletteData.items,
      ...officeActionEntries(paletteData.offices),
    );
  }
  return entries;
}

// Sessions and runs, from sidebar-model.js items; the match is the one the sidebar used.
export function itemEntries(items, mode = "advanced", options = null) {
  const entries = (Array.isArray(items) ? items : []).filter((item) => item.mode === undefined || visibleIn(item, mode)).map((item) => ({
    key: "item:" + item.key, kind: item.kind === "run" ? "run" : "session", label: item.title, route: item.route, item,
    hint: item.project, attention: Boolean(item.attention), updatedAt: item.updatedAt || null,
  }));
  return options && options.recent ? groupRecentEntries(entries, options.now) : entries;
}

export function officeEntries(offices, areasByOffice = {}) {
  const entries = [];
  for (const office of Array.isArray(offices) ? offices : []) {
    if (!office || typeof office.id !== "string") continue;
    const name = String(office.name || office.id);
    entries.push({
      key: "office:" + office.id, kind: "office", group: office.attention ? "waiting" : "offices",
      label: name, route: ["offices", null], attention: Boolean(office.attention),
    });
    const areas = Array.isArray(areasByOffice[office.id]) ? areasByOffice[office.id] : [];
    for (const area of areas) {
      if (!area || typeof area.id !== "string") continue;
      entries.push({
        key: "area:" + JSON.stringify([office.id, area.id]), kind: "area", group: "offices",
        label: String(area.name || area.id), hint: name,
        route: ["new/office/" + encodeURIComponent(office.id), area.id],
      });
    }
  }
  return entries;
}

export function projectEntries(projects) {
  return (Array.isArray(projects) ? projects : [])
    .filter((project) => project && typeof project.id === "string")
    .map((project) => ({
      key: "project:" + project.id, kind: "project", group: "projects",
      label: String(project.name || project.id), route: ["project", project.id],
    }));
}

export function officeActionEntries(offices) {
  return (Array.isArray(offices) ? offices : [])
    .filter((office) => office && typeof office.id === "string")
    .map((office) => ({
      key: "new-office:" + office.id, kind: "action", group: "actions",
      label: t("palette.new_conversation_in", { name: String(office.name || office.id) }),
      route: ["new/office", office.id],
    }));
}

const RECENT_SUBGROUP_ORDER = { waiting: 0, today: 1, yesterday: 2, week: 3, older: 4 };
const RECENT_SUBGROUP_TITLES = {
  waiting: "palette.recent_waiting", today: "palette.recent_today", yesterday: "palette.recent_yesterday",
  week: "palette.recent_week", older: "palette.recent_older",
};
const GROUP_TITLES = {
  waiting: "palette.recent_waiting", offices: "palette.group_offices", projects: "palette.group_projects",
  recent: "palette.group_recent", actions: "palette.group_actions",
};
const GROUP_ORDER = ["waiting", "offices", "projects", "recent", "actions"];

export function dayNumber(value) {
  if (value == null || value === "") return null;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
}

function timeNumber(value) {
  const stamp = Date.parse(value || "");
  return Number.isNaN(stamp) ? 0 : stamp;
}

function groupRecentEntries(entries, now = Date.now()) {
  const currentDay = dayNumber(now);
  return entries.map((entry, index) => {
    const itemDay = dayNumber(entry.updatedAt);
    const age = itemDay === null ? Infinity : Math.max(0, currentDay - itemDay);
    const subgroup = entry.attention ? "waiting" : age === 0 ? "today" : age === 1 ? "yesterday" : age <= 7 ? "week" : "older";
    return { ...entry, group: entry.attention ? "waiting" : "recent", subgroup, originalIndex: index };
  }).sort((a, b) => RECENT_SUBGROUP_ORDER[a.subgroup] - RECENT_SUBGROUP_ORDER[b.subgroup]
    || timeNumber(b.updatedAt) - timeNumber(a.updatedAt) || a.originalIndex - b.originalIndex)
    .map(({ originalIndex, ...entry }) => entry);
}

export function groupPaletteResults(results) {
  const entries = Array.isArray(results) ? results : [];
  if (!entries.some((entry) => entry.group)) {
    return entries.length ? [{ key: "ungrouped", titleKey: null, subtitleKey: null,
      items: entries.map((entry, position) => ({ entry, position })) }] : [];
  }
  const groups = [];
  for (const [position, entry] of entries.entries()) {
    const groupKey = entry.attention ? "waiting" : GROUP_TITLES[entry.group] ? entry.group : "actions";
    const subgroupKey = groupKey === "recent" && RECENT_SUBGROUP_TITLES[entry.subgroup] ? entry.subgroup : null;
    const key = subgroupKey ? groupKey + ":" + subgroupKey : groupKey;
    let group = groups[groups.length - 1];
    if (!group || group.key !== key) {
      group = {
        key,
        titleKey: GROUP_TITLES[groupKey],
        subtitleKey: subgroupKey ? RECENT_SUBGROUP_TITLES[subgroupKey] : null,
        items: [],
      };
      groups.push(group);
    }
    group.items.push({ entry, position });
  }
  return groups;
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

// Every word of the query must appear (no case, no accents). Empty grouped searches reserve space across categories; callers
// without groups keep the original screen and latest-item list.
function hit(entry, words) {
  if (entry.item) return itemMatches(entry.item, words.join(" "));
  const haystack = plain(entry.label + " " + KINDS[entry.kind] + " " + (entry.hint || ""));
  return words.every((word) => haystack.includes(word));
}

const ORDER = ["section", "tab", "session", "run", "help", "office", "area", "project", "action"];

export function searchPalette(entries, query, limit = MAX_RESULTS) {
  const words = plain(query).split(/\s+/).filter(Boolean);
  const list = Array.isArray(entries) ? entries : [];
  const grouped = list.some((entry) => GROUP_TITLES[entry.group]);
  const groupOf = (entry) => entry.attention ? "waiting" : GROUP_TITLES[entry.group] ? entry.group : "actions";
  const rank = (entry) => {
    if (grouped) {
      const group = groupOf(entry);
      return GROUP_ORDER.indexOf(group) * 10
        + (group === "recent" ? RECENT_SUBGROUP_ORDER[entry.subgroup] ?? RECENT_SUBGROUP_ORDER.older : 0);
    }
    return ORDER.indexOf(entry.kind);
  };
  const ordered = (items) => items.map((entry, index) => ({ entry, index }))
    .sort((a, b) => rank(a.entry) - rank(b.entry) || a.index - b.index).map((row) => row.entry);
  if (!words.length) {
    if (grouped) {
      const buckets = new Map();
      for (const entry of ordered(list.filter((item) => item.kind !== "help"))) {
        const group = groupOf(entry);
        if (!buckets.has(group)) buckets.set(group, []);
        buckets.get(group).push(entry);
      }
      const availableGroups = GROUP_ORDER.filter((group) => buckets.has(group));
      if (!availableGroups.length || limit <= 0) return [];
      const waiting = buckets.get("waiting") || [];
      const selected = waiting.slice(0, limit);
      const remainingGroups = availableGroups.filter((group) => group !== "waiting");
      if (selected.length >= limit || remainingGroups.length === 0) return selected;
      const perGroup = Math.max(1, Math.floor((limit - selected.length) / remainingGroups.length));
      for (const group of remainingGroups) {
        const cap = group === "recent" ? Math.min(5, perGroup) : perGroup;
        selected.push(...buckets.get(group).slice(0, cap));
      }
      return ordered(selected).slice(0, limit);
    }
    const screens = list.filter((entry) => entry.kind === "section" || entry.kind === "tab");
    return screens.concat(list.filter((entry) => entry.kind === "session" || entry.kind === "run").slice(0, 5)).slice(0, limit);
  }
  const found = list.filter((entry) => hit(entry, words));
  return ordered(found).slice(0, limit);
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
