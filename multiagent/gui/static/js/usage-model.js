// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers of the «Uso» screen (USO-T07): no DOM, no Preact, no network, so a Node test can run them.
//   - the address of a tab (#usage/plans, #usage/global, #usage/costs, #usage/continuity);
//   - the sentence of each app, worked out from two percentages (used and elapsed) that plans() already gives. There is no
//     model and no prediction of a date: it only compares «lo usado» with «el tiempo transcurrido»;
//   - the budget form: one row per API provider, an empty amount is «sin presupuesto», 0 is «no gastar».
import { currentLanguage, t } from "./i18n.js";

export const USAGE_TABS = ["plans", "global", "costs", "continuity"];
export const NEAR_LIMIT_PERCENT = 90;      // from here on the sentence is «Queda poco», whatever the time elapsed
export const BUDGET_MODES = ["warn", "cut"];
export const MAX_BUDGET = 1000000000;      // the server's bound (USD per month)

// «#usage/costs» -> "costs"; «#usage» (or anything else) -> "". The tab is whatever follows the slash, so a tab an extension adds works too.
export function usageTabFromHash(hash) {
  const found = /^#\/?usage\/([a-z][a-z0-9_]*)$/.exec(String(hash || ""));
  return found ? found[1] : "";
}

export const usageHref = (tab) => "#usage" + (tab ? "/" + tab : "");

const WINDOW_PERIOD = {
  five_hour: "usage.pace.period.five_hour", weekly: "usage.pace.period.week", model_weekly: "usage.pace.period.week",
  monthly: "usage.pace.period.month", spend: "usage.pace.period.other",
};
const finite = (value) => typeof value === "number" && Number.isFinite(value);

function usableWindow(app, window) {
  if (!window || !finite(window.used_percent)) return false;
  if (window.state !== "current" && window.state !== "limit_reached") return false;
  if (app.app === "copilot" && (!app.credits || app.credits.cap == null)) return false;   // the PlanCard shows no percent either
  return true;
}

// The case of one app: { app, kind: "ok" | "fast" | "low" | "no_data", ... }. It looks at the window that is closest to its limit.
export function paceOf(app) {
  const base = { app: app && app.app ? app.app : "", kind: "no_data" };
  if (!app || app.status === "disabled" || (app.app === "claude" && !app.source)) return base;
  const windows = (Array.isArray(app.windows) ? app.windows : []).filter((window) => usableWindow(app, window));
  if (!windows.length) return base;
  const window = windows.reduce((worst, item) => (item.used_percent > worst.used_percent ? item : worst));
  const found = { ...base, window: window.window, used: window.used_percent, elapsed: finite(window.elapsed_percent) ? window.elapsed_percent : null,
    resets_at: window.resets_at || null };
  if (window.state === "limit_reached" || window.used_percent >= NEAR_LIMIT_PERCENT) return { ...found, kind: "low" };
  if (found.elapsed === null) return base;
  return { ...found, kind: window.used_percent > found.elapsed ? "fast" : "ok" };
}

export const usagePace = (plans) => (Array.isArray(plans && plans.apps) ? plans.apps : [])
  .filter((app) => app && app.status !== "disabled").map(paceOf);

const locale = () => currentLanguage() === "es" ? "es-MX" : "en";
const whole = (value) => new Intl.NumberFormat(locale(), { maximumFractionDigits: 0 }).format(Math.round(value));

function whenText(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.valueOf())
    ? new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(date) : null;
}

// The sentence of one case, in the language of the page.
export function paceText(item) {
  if (item.kind === "ok") {
    return t("usage.pace.ok", { used: whole(item.used), elapsed: whole(item.elapsed), period: t(WINDOW_PERIOD[item.window] || WINDOW_PERIOD.spend) });
  }
  if (item.kind === "fast") return t("usage.pace.fast");
  if (item.kind === "low") {
    const when = whenText(item.resets_at);
    return when ? t("usage.pace.low", { when }) : t("usage.pace.low_no_date");
  }
  return t("usage.pace.no_data");
}

// --- the budget form ------------------------------------------------------------------------------------------------

// Rows of the form: every provider the server knows plus any provider already saved. An empty text is «sin presupuesto».
export function budgetRows(data) {
  const saved = data && typeof data.providers === "object" && data.providers ? data.providers : {};
  const known = Array.isArray(data && data.known_providers) ? data.known_providers : [];
  const names = [...new Set([...known, ...Object.keys(saved)])].sort();
  return names.map((provider) => {
    const entry = saved[provider];
    return { provider, amount: entry && finite(entry.amount) ? String(entry.amount) : "", mode: entry && BUDGET_MODES.includes(entry.mode) ? entry.mode : "warn" };
  });
}

// "" -> { valid: true, value: null }; "20", "20.5" and "20,5" -> a number; anything else (negative, text, too big) -> { valid: false }.
export function parseAmount(text) {
  const raw = String(text == null ? "" : text).trim();
  if (raw === "") return { valid: true, value: null };
  const normal = raw.replace(",", ".");
  if (!/^\d{1,10}(\.\d{1,6})?$/.test(normal)) return { valid: false, value: null };
  const value = Number(normal);
  return value <= MAX_BUDGET ? { valid: true, value } : { valid: false, value: null };
}

export const budgetValid = (rows) => rows.every((row) => parseAmount(row.amount).valid);

// The body of PUT /gui/api/settings/budget: every row goes, so the saved file always says what the screen says.
export function budgetPayload(rows) {
  const providers = {};
  for (const row of rows) providers[row.provider] = { amount: parseAmount(row.amount).value, mode: row.mode };
  return { providers };
}

// True only when what the screen holds differs from what the server holds: the form does not send otherwise.
export function budgetChanged(rows, original) {
  const now = budgetPayload(rows).providers;
  const before = budgetPayload(original).providers;
  return Object.keys(now).some((name) => !before[name] || now[name].amount !== before[name].amount || now[name].mode !== before[name].mode);
}

export const SOURCE_KEYS = { gui: "usage.source.gui", environment: "usage.source.environment", default: "usage.source.default" };
export const sourceText = (source) => t(SOURCE_KEYS[source] || SOURCE_KEYS.default);

// --- the continuity form (the section «Continuidad» of Uso, extensions/continuity/policy.js) -------------------------------
// Two modes only: «manual» asks every time and «semi_auto» proposes the hand-off and waits for the person's yes. There is no
// automatic mode here: the server does not know one (USO-T08).
export const CONTINUITY_MODES = ["manual", "semi_auto"];
export const CONTINUITY_HOURS = [1, 3, 5, 24];
export const CONTINUITY_APPS = ["claude_cli", "codex_cli", "copilot_cli"];
export const CONTINUITY_POLICIES = ["no_ask", "ask", "never"];

// What the form holds, from GET /gui/api/settings/continuity. A value the form cannot offer falls back to the safest one.
export function continuityDraft(data) {
  const source = data && typeof data === "object" ? data : {};
  const candidates = source.candidates && typeof source.candidates === "object" ? source.candidates : {};
  return {
    mode: CONTINUITY_MODES.includes(source.mode) ? source.mode : "manual",
    wait_threshold_hours: CONTINUITY_HOURS.includes(source.wait_threshold_hours) ? source.wait_threshold_hours : 5,
    candidates: Object.fromEntries(CONTINUITY_APPS.map((app) => [app, CONTINUITY_POLICIES.includes(candidates[app]) ? candidates[app] : "ask"])),
  };
}

// The body of PUT /gui/api/settings/continuity: exactly these three keys, no more.
export const continuityPayload = (draft) => ({
  mode: draft.mode, wait_threshold_hours: draft.wait_threshold_hours, candidates: { ...draft.candidates },
});

export function continuityChanged(draft, original) {
  const now = continuityPayload(draft);
  const before = continuityPayload(original);
  return now.mode !== before.mode || now.wait_threshold_hours !== before.wait_threshold_hours
    || CONTINUITY_APPS.some((app) => now.candidates[app] !== before.candidates[app]);
}

// The tab that is open. The advanced mode always has one (the first when the address names none); the simple mode keeps
// every detail folded until the address or a click names one.
export function openTab(mode, addressed, ids) {
  if (ids.includes(addressed)) return addressed;
  return mode === "advanced" && ids.length ? ids[0] : "";
}
