// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers for the pending inbox: ordering, counting and notification candidates.

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
