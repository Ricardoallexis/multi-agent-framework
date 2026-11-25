// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts and formatters of the sessions screen. The server sends codes and facts; the wording lives here.
import { legacyInfo } from "./ui/labels.js";
import { t, currentLanguage } from "./i18n.js";

export const SOURCES = [
  { id: "claude", label: "Claude" },
  { id: "codex", label: "Codex" },
  { id: "copilot", label: "Copilot" },
  { id: "antigravity", label: "Antigravity" },
  { id: "framework", label: "Framework" },
];

export const SURFACES = [
  { id: "desktop", get label() { return t("sessions.desktop"); } },
  { id: "cli", get label() { return t("sessions.cli"); } },
  { id: "framework", label: "Framework" },
  { id: "unknown", get label() { return t("sessions.unknown_surface"); } },
];

const label = (list, id, fallback) => (list.find((entry) => entry.id === id) || { label: fallback || String(id) }).label;

export const sourceLabel = (id) => label(SOURCES, id);
export const surfaceLabel = (id) => label(SURFACES, id);
// The words of the statuses live in ui/labels.js (the single label map); this is its {label, tone, help} view.
export const statusInfo = (id) => legacyInfo("session", id);

// A subagent has no title of its own: it is named after its id ("agent-xxxx"); the others show the start of theirs.
export function sessionTitle(session) {
  if (session.title) return session.title;
  const native = String(session.native_id || session.id);
  if (session.parent_id) return t("sessions.subagent") + " " + native.slice(native.lastIndexOf(":") + 1);
  return t("sessions.no_title") + native.slice(0, 8) + ")";
}

const number = (value) => value.toLocaleString(currentLanguage());
const known = (value) => typeof value === "number" && Number.isFinite(value);
const percent = (value) => (value * 100).toLocaleString(currentLanguage(), { maximumFractionDigits: 2 }) + "%";

export function contextEventText(type, payload = {}) {
  if (!payload || typeof payload !== "object") payload = {};
  const step = typeof payload.step_id === "string" && payload.step_id ? t("sessions.step") + payload.step_id + t("sessions.step_end") : "";
  if (type === "context_warning") {
    const facts = [t("sessions.context_warning"), step];
    if (known(payload.ratio)) facts.push(t("sessions.measured_fraction") + percent(payload.ratio));
    if (known(payload.warning_at)) facts.push(t("sessions.warning_at") + percent(payload.warning_at));
    if (known(payload.threshold)) facts.push(t("sessions.compaction_threshold") + percent(payload.threshold));
    return facts.filter(Boolean).join(" · ");
  }
  if (type === "context_compacted") {
    const facts = [t("sessions.context_compacted"), step];
    if (known(payload.tokens_before)) facts.push(t("sessions.before") + number(payload.tokens_before) + " tokens");
    if (known(payload.tokens_after)) facts.push(t("sessions.after") + number(payload.tokens_after) + " tokens");
    if (typeof payload.summary_ref === "string" && payload.summary_ref) facts.push(t("sessions.snapshot") + payload.summary_ref);
    return facts.filter(Boolean).join(" · ");
  }
  return null;
}

// {input, output, cache_read, cache_write, total?} or null (unknown: never shown as 0).
export function tokensText(tokens, { brief = false } = {}) {
  if (!tokens) return t("sessions.unknown");
  const parts = [];
  if (known(tokens.input)) parts.push(t("sessions.input") + number(tokens.input));
  if (known(tokens.output)) parts.push(t("sessions.output") + number(tokens.output));
  if (!brief) {
    if (known(tokens.cache_read)) parts.push(t("sessions.cache_read") + number(tokens.cache_read));
    if (known(tokens.cache_write)) parts.push(t("sessions.cache_write") + number(tokens.cache_write));
  }
  if (known(tokens.total) && !parts.length) parts.push(t("sessions.total") + number(tokens.total));
  return parts.length ? parts.join(" · ") : t("sessions.unknown");
}

export const modelsText = (models) => (models && models.length ? models.join(", ") : t("sessions.not_reported"));

export function relativeTime(value, now = Date.now()) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return String(value);
  const seconds = (then - now) / 1000;
  const abs = Math.abs(seconds);
  if (abs < 45) return t("sessions.just_now");
  const units = [["minute", 60, 60], ["hour", 3600, 24], ["day", 86400, 30], ["month", 2592000, 12], ["year", 31536000, Infinity]];
  let size = 60;
  let unit = "minute";
  for (const [name, perUnit, limit] of units) {
    unit = name;
    size = perUnit;
    if (abs / perUnit < limit) break;
  }
  const amount = Math.round(seconds / size);
  const relative = typeof Intl !== "undefined" && Intl.RelativeTimeFormat
    ? new Intl.RelativeTimeFormat(currentLanguage(), { numeric: "auto" }) : null;
  return relative ? relative.format(amount, unit) : t("sessions.ago", { amount: Math.abs(amount), unit });
}

const KIND = {
  user_message: "sessions.user",
  assistant_message: "sessions.assistant",
  reasoning_omitted: "sessions.reasoning_omitted",
  tool_call: "sessions.tool",
  tool_result: "sessions.tool_result",
  system: "sessions.system",
  subagent: "sessions.subagent",
  compaction: "sessions.compaction",
  usage: "sessions.token_usage",
  unknown: "sessions.unknown_record",
};
export const kindLabel = (kind) => t(KIND[kind] || KIND.unknown);

const WARNINGS = {
  no_allowed_roots: () => t("sessions.no_roots"),
  source_unavailable: (rest) => t("sessions.source_unavailable") + (rest ? " (" + rest + ")" : "") + t("sessions.other_sources"),
  cursor_reset: () => t("sessions.cursor_reset"),
  broken_json_lines: (rest) => t("sessions.broken_lines") + rest + ".",
  oversized_lines_skipped: (rest) => t("sessions.oversized_lines") + rest + ".",
  unknown_record_types: (rest) => t("sessions.unknown_types") + rest + ".",
  session_file_unavailable: () => t("sessions.file_unavailable"),
  no_local_history: () => t("sessions.no_history"),
};

// The reader sends "<code>: <detail>" or just "<code>".
export function warningText(warning) {
  const text = String(warning);
  const split = text.indexOf(":");
  const code = split < 0 ? text : text.slice(0, split);
  const rest = split < 0 ? "" : text.slice(split + 1).trim();
  const describe = WARNINGS[code];
  return describe ? describe(rest) : text;
}

export const ROOTS_HELP = {
  get title() { return t("sessions.roots_title"); },
  get intro() { return t("sessions.roots_intro_1") + t("sessions.roots_intro_2"); },
  get steps() { return [t("sessions.roots_step_1"), t("sessions.roots_step_2")]; },
  get example() { return t("sessions.roots_example"); },
};
