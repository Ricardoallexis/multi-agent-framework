// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure quota text helpers (ARR2-T02): window labels, local reset time, and formatted lines. No DOM or network.

const WINDOW_LABELS = {
  five_hour: "Cinco horas",
  weekly: "Semana",
  model_weekly: "Semana por modelo",
  monthly: "Mes",
  spend: "Gasto",
};

const RESET_FORMAT = new Intl.DateTimeFormat("es", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export function windowLabel(window) {
  return Object.hasOwn(WINDOW_LABELS, window) ? WINDOW_LABELS[window] : "Uso";
}

export function resetText(iso) {
  if (typeof iso !== "string" && !(iso instanceof Date)) return "";
  if (typeof iso === "string" && !iso.trim()) return "";
  const date = iso instanceof Date ? iso : new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return "reinicia el " + RESET_FORMAT.format(date);
}

export function percentText(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? String(Math.round(value * 10) / 10).replace(".", ",") + " %"
    : "desconocido";
}

export function windowTitle(item) {
  const group = item?.group || "";
  const label = item?.window ? windowLabel(item.window) : (group ? "" : windowLabel(item?.window));
  return [group, label].filter(Boolean).join(" · ");
}

export function formatQuotaLines(quota, { remaining = false, emptyWhenMissing = false } = {}) {
  if (!quota && emptyWhenMissing) return [];
  const windows = Array.isArray(quota?.windows) ? quota.windows : [];
  if (!windows.length) return [remaining ? "Cuota restante: sin datos." : "Cuota: sin datos."];
  return windows.map((item) => {
    const name = windowTitle(item);
    const raw = typeof item?.used_percent === "number" && Number.isFinite(item.used_percent)
      ? (remaining ? 100 - item.used_percent : item.used_percent)
      : null;
    const reset = resetText(item?.resets_at);
    return name + ": " + percentText(raw) + (remaining ? " restante" : " usado") + (reset ? " · " + reset : "");
  });
}
