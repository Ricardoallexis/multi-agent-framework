// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers for the pending inbox: ordering, counting and notification candidates.
import { PENDING_TYPES } from "./extensions.js";

export function sortPending(items) {
  return [...(Array.isArray(items) ? items : [])].sort((a, b) => {
    const left = Date.parse(a?.since || "");
    const right = Date.parse(b?.since || "");
    const leftTime = Number.isNaN(left) ? Number.POSITIVE_INFINITY : left;
    const rightTime = Number.isNaN(right) ? Number.POSITIVE_INFINITY : right;
    return leftTime - rightTime || String(a?.id || "").localeCompare(String(b?.id || ""));
  });
}

export function countPending(items) {
  return Array.isArray(items) ? items.length : 0;
}

export function pendingIds(items) {
  return (Array.isArray(items) ? items : [])
    .filter((item) => item && typeof item.id === "string")
    .map((item) => item.id);
}

export function newlyPending(items, seenIds, enabled, permission) {
  if (!enabled || permission !== "granted" || !Array.isArray(seenIds)) return [];
  const seen = new Set(seenIds);
  return (Array.isArray(items) ? items : [])
    .filter((item) => item && typeof item.id === "string" && !seen.has(item.id));
}

// GUI3-T05: «Pendientes» is one screen (#pending) with a tab for the whole list and one per view registered with group "pending".
export const PENDING_SECTION = "pending";
export const ALL_TAB = "all";
const VISIBLE_MS = 5000;
const HIDDEN_MS = 30000;

// How many items of each type the API gave: { approval: 2, task: 1 }.
export function countsByType(items) {
  const counts = {};
  for (const item of Array.isArray(items) ? items : []) {
    if (item && typeof item.type === "string") counts[item.type] = (counts[item.type] || 0) + 1;
  }
  return counts;
}

// The tabs, from what is registered: «Todo» first, then each pending view in its order. A view without a pendingType has no
// count (the API does not count it). `counts` is null until the first answer, so no tab claims a number before it is known.
export function pendingTabs(views, items, loaded = true) {
  const counts = loaded ? countsByType(items) : null;
  const tabs = [{ id: ALL_TAB, label: "Todo", ...(counts ? { count: countPending(items) } : {}) }];
  for (const view of Array.isArray(views) ? views : []) {
    if (!view || view.group !== "pending") continue;
    const tab = { id: view.id, label: view.label };
    if (counts && PENDING_TYPES.includes(view.pendingType)) tab.count = counts[view.pendingType] || 0;
    tabs.push(tab);
  }
  return tabs;
}

// The route behind a tab: «Todo» is #pending; a view is its own hash, so the old addresses keep working.
export const tabRoute = (tabId) => (tabId === ALL_TAB ? [PENDING_SECTION, null] : [tabId, null]);
export function tabHash(tabId, id) {
  if (tabId === ALL_TAB) return "#" + PENDING_SECTION;
  return "#" + tabId + (id ? "/" + encodeURIComponent(id) : "");
}

// Which tab a route selects; null when the route is not part of «Pendientes».
export function routeTab(route, tabs) {
  const section = route && route.section;
  if (section === PENDING_SECTION) return ALL_TAB;
  return (Array.isArray(tabs) ? tabs : []).some((tab) => tab.id === section && tab.id !== ALL_TAB) ? section : null;
}

// One poller for the whole page (the sidebar row and the «Todo» tab read it). `load()` answers { answer, background, policy };
// `onNew(items, seenIds)` is told of every later answer; the timer, the clock of visibility and the visibility listener are
// parameters, so this runs without a page. It polls while someone is subscribed: 5 s visible, 30 s hidden.
export function createPendingStore({ load, setTimer, clearTimer, isHidden, watchVisibility, onNew }) {
  const listeners = new Set();
  let state = { items: [], background: [], policy: { enabled: false, reason: "" }, loading: true, error: null };
  let timer = null;
  let busy = false;
  let seen = null;
  let stopWatching = null;
  let polls = 0;

  const publish = (next) => {
    state = next;
    for (const listener of [...listeners]) listener(state);
  };
  const refresh = async () => {
    if (busy || !listeners.size) return;
    busy = true;
    polls += 1;
    try {
      const { answer, background, policy } = await load();
      const items = sortPending(answer && answer.items);
      if (seen !== null && onNew) onNew(items, seen);
      seen = pendingIds(items);
      publish({ items, background: sortPending(background && background.items), policy, loading: false, error: null });
    } catch (error) {
      publish({ ...state, loading: false, error });
    } finally {
      busy = false;
    }
  };
  const schedule = () => {
    if (timer !== null) clearTimer(timer);
    timer = setTimer(refresh, isHidden() ? HIDDEN_MS : VISIBLE_MS);
  };
  const start = () => {
    stopWatching = watchVisibility ? watchVisibility(() => { schedule(); if (!isHidden()) refresh(); }) : null;
    refresh();
    schedule();
  };
  const stop = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
    if (stopWatching) stopWatching();
    stopWatching = null;
    seen = null;
  };
  return {
    get: () => state,
    refresh,
    polls: () => polls,
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) start();
      return () => {
        listeners.delete(listener);
        if (!listeners.size) stop();
      };
    },
  };
}
