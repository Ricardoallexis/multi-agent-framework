// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure model of the limits chip of the sidebar footer (GUI4-T10): rows from GET /gui/api/connections, the app closest to its
// limit, the apps of an office, and the texts of each row. No DOM or network; it only reads, it never switches an app or a model.
import { t } from "./i18n.js";
import { percentText, resetText } from "./quota-text.js";

// connection id -> the words that name that app in a seat of an office scene.
const APPS = {
  claude_cli: { name: "limits.app_claude", words: ["claude"] },
  codex_cli: { name: "limits.app_codex", words: ["codex"] },
  copilot_cli: { name: "limits.app_copilot", words: ["copilot"] },
  antigravity_cli: { name: "limits.app_agy", words: ["agy", "antigravity"] },
};
const LIMIT_REASONS = new Set(["limit_reached", "quota_paused", "quota_below_pause_floor"]);

export const appName = (id) => (Object.hasOwn(APPS, id) ? t(APPS[id].name) : String(id));

const finite = (value) => typeof value === "number" && Number.isFinite(value);

// The window that is closest to its limit; null when the app reports no percentage.
function worstWindow(quota) {
  const windows = Array.isArray(quota?.windows) ? quota.windows : [];
  let worst = null;
  for (const item of windows) {
    if (finite(item?.used_percent) && (worst === null || item.used_percent > worst.used_percent)) worst = item;
  }
  return worst;
}

// One row per executor app that reports a percentage: { id, used, remaining, resetsAt, checkedAt, limitReached }.
// The name is resolved when painting (appName), so a language change repaints it.
// Apps without a measurement (no quota, no percentage) are left out: the chip does not invent a bar for them.
export function appLimitRows(payload) {
  const connections = Array.isArray(payload?.connections) ? payload.connections : [];
  const rows = [];
  for (const item of connections) {
    if (!item || !Object.hasOwn(APPS, item.id)) continue;
    const window = worstWindow(item.quota);
    if (window === null) continue;
    const used = Math.min(Math.max(window.used_percent, 0), 100);
    rows.push({
      id: item.id,
      used,
      remaining: Math.round((100 - used) * 10) / 10,
      resetsAt: window.resets_at || null,
      checkedAt: item.checked_at || null,
      limitReached: used >= 100 || Boolean(item.limit) || LIMIT_REASONS.has(item.reason),
    });
  }
  return rows;
}

// The row the chip shows: the app closest to its limit (the first one on a tie); null when no app reports a percentage.
export function chipRow(rows) {
  let best = null;
  for (const row of Array.isArray(rows) ? rows : []) if (best === null || row.used > best.used) best = row;
  return best;
}

// «4 min», «2 h», «3 d»; "" when the time is unknown.
export function ageText(iso, now = Date.now()) {
  const when = typeof iso === "string" ? Date.parse(iso) : NaN;
  if (Number.isNaN(when)) return "";
  const minutes = Math.max(0, Math.floor((now - when) / 60000));
  if (minutes < 1) return t("limits.checked_now");
  const age = minutes < 60 ? t("limits.age_minutes", { n: minutes })
    : minutes < 1440 ? t("limits.age_hours", { n: Math.floor(minutes / 60) }) : t("limits.age_days", { n: Math.floor(minutes / 1440) });
  return t("limits.checked_ago", { age });
}

// «72 % used · 28 % left · resets … · checked 4 min ago»; at the limit it says «Limit reached» instead of the counts.
export function rowDetail(row, now = Date.now()) {
  const parts = row.limitReached
    ? [t("limits.limit_reached")]
    : [t("limits.detail_used", { percent: percentText(row.used) }), t("limits.detail_left", { percent: percentText(row.remaining) })];
  const reset = resetText(row.resetsAt);
  if (reset) parts.push(reset);
  const age = ageText(row.checkedAt, now);
  if (age) parts.push(age);
  return parts.join(" · ");
}

// The words a screen reader gets for a bar: «Copilot CLI: 72 % used».
export const rowAria = (row) => t("limits.used_aria", { app: appName(row.id), percent: percentText(row.used) });
