// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure quota text helpers (ARR2-T02): window labels, local reset time, and formatted lines. No DOM or network.
import { currentLanguage, t } from "./i18n.js";

const WINDOW_LABELS = {
  five_hour: "shell.quota_five_hours",
  weekly: "shell.quota_week",
  model_weekly: "shell.quota_model_week",
  monthly: "shell.quota_month",
  spend: "shell.quota_spend",
};

const RESET_FORMATS = Object.fromEntries(["en", "es"].map((language) => [language, new Intl.DateTimeFormat(language, {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
})]));

export function windowLabel(window) {
  return Object.hasOwn(WINDOW_LABELS, window) ? t(WINDOW_LABELS[window]) : t("shell.quota_usage");
}

export function resetText(iso) {
  if (typeof iso !== "string" && !(iso instanceof Date)) return "";
  if (typeof iso === "string" && !iso.trim()) return "";
  const date = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return t("shell.quota_reset", { date: RESET_FORMATS[currentLanguage()].format(date) });
}

export function percentText(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? String(Math.round(value * 10) / 10).replace(".", currentLanguage() === "es" ? "," : ".") + " %"
    : t("shell.unknown");
}

export function windowTitle(item) {
  const group = item?.group || "";
  const label = item?.window ? windowLabel(item.window) : (group ? "" : windowLabel(item?.window));
  return [group, label].filter(Boolean).join(" · ");
}

export function formatQuotaLines(quota, { remaining = false, emptyWhenMissing = false } = {}) {
  if (!quota && emptyWhenMissing) return [];
  const windows = Array.isArray(quota?.windows) ? quota.windows : [];
  if (!windows.length) return [remaining ? t("shell.remaining_quota_no_data") : t("shell.quota_no_data")];
  return windows.map((item) => {
    const name = windowTitle(item);
    const raw = typeof item?.used_percent === "number" && Number.isFinite(item.used_percent)
      ? (remaining ? 100 - item.used_percent : item.used_percent)
      : null;
    const reset = resetText(item?.resets_at);
    return t("shell.quota_line", {
      name, percent: percentText(raw), used: remaining ? t("shell.remaining") : t("shell.used"),
      reset: reset ? " · " + reset : "",
    });
  });
}
