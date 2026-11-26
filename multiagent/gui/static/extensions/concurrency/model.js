// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure helpers of the extension «concurrency» (PAR-T04): no DOM and no network. They turn what
// GET /gui/api/settings/concurrency, GET /gui/api/settings and GET /gui/api/usage/costs answer into a draft the person edits,
// the smallest PUT body that applies it, and the keys of the fixed sentences the card shows (index.js translates them).
// Nothing here decides a number: the cap comes from services.concurrency on the server and the card only shows it.

export const MODES = ["eco", "normal", "performance", "maximum"];
export const MARGINS = [3, 5, 10, 15, 20, 30, 40];
export const DURATIONS = ["next_five_hour", "two_hours", "tasks"];
export const TEMPORARY_MODES = ["performance", "maximum"];
export const MANUAL_MAX = 16;                      // the simple screen offers 1 to 16 (the server accepts up to 256)
export const APPS = ["codex", "claude", "copilot", "antigravity"];   // the apps whose own windows set an own cap
export const LEVELS = ["T1", "T2", "T3", "T4"];

const isInt = (value) => Number.isInteger(value) && !(typeof value === "boolean");

export function clampManual(value) {
  const number = Math.trunc(Number(value));
  if (!Number.isFinite(number)) return 1;
  return Math.min(MANUAL_MAX, Math.max(1, number));
}

// The manual cap this screen starts from: the saved one when it is in range, else 1 (the server default 256 is not a choice
// of the person). A manual cap the server already holds above the range is kept as it is by manualShown().
const manualOf = (config) => (isInt(config.manual) && config.manual >= 1 && config.manual <= MANUAL_MAX ? config.manual : 1);
const manualSaved = (config) => (config.selection === "manual" && isInt(config.manual) && config.manual >= 1 ? config.manual : null);

// A whole number from 1 to 256 typed as digits, else null (empty, 0, 257, 1.5 and text are not caps).
export function parseCap(text) {
  const raw = String(text === undefined || text === null ? "" : text).trim();
  if (!/^\d+$/.test(raw)) return null;
  const number = Number(raw);
  return number >= 1 && number <= 256 ? number : null;
}

// The manual value a save would send, which is also the one the effect sentence shows. The box text unchanged from what the
// server holds is that value as it is (even above 16); an empty or broken text keeps the last good value; anything else is
// clamped to 1..16.
export function manualShown(config, draft) {
  const saved = manualSaved(config || {});
  const text = String(draft.manualText === undefined ? draft.manual : draft.manualText).trim();
  if (saved !== null && text === String(saved)) return saved;
  if (text === "" || !Number.isFinite(Number(text))) return saved !== null ? saved : manualOf(config || {});
  return clampManual(text);
}

// True when the server already holds a manual cap above this screen's 1..16 range.
export const manualAboveRange = (config) => {
  const saved = manualSaved(config || {});
  return saved !== null && saved > MANUAL_MAX;
};

export const isTemporary = (mode) => TEMPORARY_MODES.includes(mode);

export function marginOf(config, mode) {
  const margins = config && config.margins ? config.margins : {};
  return isInt(margins[mode]) ? margins[mode] : (isInt(config && config.margin) ? config.margin : 10);
}

// The draft starts as a copy of what the server saved, so «nothing changed» means «nothing is sent».
export function draftFromConfig(config) {
  const saved = config || {};
  const mode = MODES.includes(saved.mode) ? saved.mode : "normal";
  return {
    selection: saved.selection === "manual" ? "manual" : "auto",
    manual: manualSaved(saved) !== null ? saved.manual : manualOf(saved),
    manualText: String(manualSaved(saved) !== null ? saved.manual : manualOf(saved)),
    mode,
    margin: marginOf(saved, mode),
    duration: DURATIONS.includes(saved.duration) ? saved.duration : "next_five_hour",
    allowT4: saved.allow_t4 === true,
    perApp: { ...(saved.per_app || {}) },
    perModel: { ...(saved.per_model || {}) },
  };
}

// Changing the mode brings that mode's own margin (each mode remembers its own, round 3 of the design).
export function withMode(config, draft, mode) {
  return { ...draft, mode, margin: marginOf(config, mode) };
}

const sameMap = (a, b) => {
  const left = Object.keys(a || {}).sort();
  const right = Object.keys(b || {}).sort();
  return left.length === right.length && left.every((key, index) => key === right[index] && a[key] === b[key]);
};

// The body of PUT /gui/api/settings/concurrency: only the fields the person changed. `advanced` unlocks the fields
// that only the advanced screen shows. An empty object means there is nothing to save.
export function buildPatch(config, draft, { advanced = false } = {}) {
  const saved = config || {};
  const wasManual = saved.selection === "manual";
  const patch = {};
  if (draft.selection === "manual") {
    if (!wasManual) patch.selection = "manual";
    const value = manualShown(saved, draft);
    if (!wasManual || value !== saved.manual) patch.manual = value;
  } else {
    if (wasManual) patch.selection = "auto";
    const modeChanged = draft.mode !== saved.mode;
    if (modeChanged) patch.mode = draft.mode;
    if (draft.margin !== marginOf(saved, draft.mode)) patch.margin = draft.margin;
    if (isTemporary(draft.mode) && (modeChanged || draft.duration !== saved.duration)) patch.duration = draft.duration;
  }
  if (advanced) {
    if (draft.allowT4 !== (saved.allow_t4 === true)) patch.allow_t4 = draft.allowT4;
    if (!sameMap(draft.perApp, saved.per_app)) patch.per_app = { ...draft.perApp };
    if (!sameMap(draft.perModel, saved.per_model)) patch.per_model = { ...draft.perModel };
  }
  return patch;
}

export const isDirty = (config, draft, options) => Object.keys(buildPatch(config, draft, options)).length > 0;

// A cap typed in a box: empty removes it, a whole number from 1 to 256 sets it, anything else is kept out.
export function withLimit(map, key, text) {
  const next = { ...(map || {}) };
  const raw = String(text === undefined || text === null ? "" : text).trim();
  if (raw === "") {
    delete next[key];
    return next;
  }
  const number = parseCap(raw);
  if (number !== null) next[key] = number;
  return next;
}

// A cap of a row that already exists: only a valid whole number 1..256 changes it. An empty or half-typed box leaves the draft
// as it was and the row stays; the key goes away only through withoutKey (the «Quitar» button).
export function setCap(map, key, text) {
  const number = parseCap(text);
  return number === null ? (map || {}) : { ...(map || {}), [key]: number };
}

export function withoutKey(map, key) {
  const next = { ...(map || {}) };
  delete next[key];
  return next;
}

// «Add a model cap»: a model id and a whole number from 1 to 256 (the id is only a name, the server matches it).
export function addModelLimit(map, id, text) {
  const key = String(id || "").trim();
  if (!key || key.length > 200) return map || {};
  return withLimit(map, key, text);
}

// The sentence of the chosen mode, with its margin.
export function effectNote(draft, config) {
  if (draft.selection === "manual") return { key: "ext.concurrency.effect.manual", params: { limit: manualShown(config, draft) } };
  return { key: "ext.concurrency.effect." + draft.mode, params: { margin: draft.margin } };
}

// What the cap in force is and why, from the `code` the server answers (never the server's English sentence).
export function limitNote(effective) {
  const data = effective || {};
  if (!isInt(data.limit)) return null;
  if (data.selection === "manual") return { key: "ext.concurrency.now.manual", params: { limit: data.limit } };
  const params = { limit: data.limit, margin: data.margin, app: data.app || "", window: data.window || "" };
  if (data.code === "quota_window") return { key: "ext.concurrency.now.quota_window", params };
  if (data.code === "quota_margin_reached") return { key: "ext.concurrency.now.margin_reached", params };
  if (data.code === "no_quota_data") return { key: "ext.concurrency.now.no_quota_data", params };
  return { key: "ext.concurrency.now.other", params };
}

// The warning shown before Performance or Maximum is saved: which window it spends, when the server says it.
export function warningNote(draft, effective) {
  if (draft.selection === "manual" || !isTemporary(draft.mode)) return null;
  const data = effective || {};
  const named = data.code === "quota_window" && data.app;
  return {
    key: "ext.concurrency.warning." + draft.mode + (named ? ".named" : ".generic"),
    params: { app: data.app || "", window: data.window || "" },
  };
}

// A temporary mode that ran out is Normal again: the server says so in `mode_reason`.
export const modeExpired = (config) => Boolean(config && config.mode_reason === "temporary_mode_expired");

// The read-only budget line. The costs answer has no budget field today, so «none» is only said when a `budgets` list is
// there and empty; with no field at all the line says it is not reported here and the person goes to «Usage».
export function budgetLine(costs) {
  if (!costs || typeof costs !== "object") return { key: "ext.concurrency.budget.unavailable", params: {} };
  const list = Array.isArray(costs.budgets) ? costs.budgets : null;
  const monthly = list ? list.filter((item) => item && item.scope === "month" && Number.isFinite(Number(item.amount))) : [];
  if (monthly.length > 0) {
    const first = monthly[0];
    return { key: "ext.concurrency.budget.amount", params: { amount: Number(first.amount), currency: String(first.currency || "USD") } };
  }
  if (list) return { key: "ext.concurrency.budget.none", params: {} };
  return { key: "ext.concurrency.budget.unavailable", params: {} };
}

// The levels the person already confirmed (GET /gui/api/settings → model_levels), sorted by model id. Read only here.
export function confirmedLevels(settings) {
  const levels = settings && typeof settings.model_levels === "object" && settings.model_levels ? settings.model_levels : {};
  return Object.keys(levels).sort().map((model) => ({ model, level: levels[model].level, source: levels[model].source }))
    .filter((row) => LEVELS.includes(row.level));
}
