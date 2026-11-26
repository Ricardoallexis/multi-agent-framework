// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Connection facts -> the ordered, selectable choices of Nueva sesión. No DOM or network.
import { formatQuotaLines } from "./quota-text.js";
import { limitReachedText, quotaWarningText } from "./new-session-model.js";
import { t } from "./i18n.js";

export const MODEL_IDS = ["gemini", "openai", "ollama"];
export const EXECUTOR_IDS = ["claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"];

const LABELS = {
  gemini: "Gemini", openai: "OpenAI", ollama: "Ollama",
  claude_cli: "Claude", codex_cli: "Codex", copilot_cli: "Copilot", antigravity_cli: "Antigravity (agy)",
};
const STEPS = {
  install: "model_picker.step_install", session: "model_picker.step_session", key: "model_picker.step_key",
  enable: "model_picker.step_enable",
};
const REASONS = {
  disabled: "model_picker.disabled", notice_not_accepted: "model_picker.notice_not_accepted",
  quota_paused: "model_picker.quota_paused", quota_below_pause_floor: "model_picker.quota_below_pause_floor",
  quota_below_launch_floor: "model_picker.quota_below_launch_floor", usage_unavailable: "model_picker.usage_unavailable",
  session_unverifiable: "model_picker.session_unverifiable",
  status_unknown: "model_picker.status_unknown", reachability_unknown: "model_picker.reachability_unknown",
  unreachable: "model_picker.unreachable", tls_certificate: "model_picker.tls_certificate",
  not_installed: "model_picker.not_installed", session_missing: "model_picker.session_missing", key_missing: "model_picker.key_missing",
  key_invalid: "model_picker.key_invalid", no_access: "model_picker.no_access", limit_reached: "model_picker.quota_paused",
  local_only: "model_picker.local_only",
};

export function quotaLines(quota) {
  return formatQuotaLines(quota);
}

function choice(row) {
  const id = row.id;
  const contextWindow = row.context_window;
  const selectable = row.enabled === true && row.available === true;
  const limited = !selectable && (Boolean(row.limit) || row.reason === "limit_reached" || row.reason === "quota_paused");
  const reason = selectable
    ? null
    : limited
      ? limitReachedText(row.limit || row.resets_at || row.paused_until)
      : (STEPS[row.missing_step] || REASONS[row.reason] ? t(STEPS[row.missing_step] || REASONS[row.reason]) : t("model_picker.not_connected"));
  const verificationLabel = id === "gemini" && selectable
    ? (row.verification === "verified" ? t("model_picker.connected") : t("model_picker.not_checked"))
    : null;
  const quota = id === "gemini"
    ? [t("model_picker.gemini_quota_unavailable")]
    : quotaLines(row.quota);
  return {
    id, kind: MODEL_IDS.includes(id) ? "model" : "executor", label: LABELS[id] || id,
    selectable, checkedAt: row.checked_at || null, quota,
    verificationLabel,
    warning: quotaWarningText(row.warning),
    reason,
    contextWindow: typeof contextWindow === "number" ? contextWindow : contextWindow?.tokens ?? null,
    agyNotice: id === "antigravity_cli"
      ? t("model_picker.agy_notice")
      : null,
  };
}

export function buildModelPicker(payload, selectedId = null) {
  const rows = Array.isArray(payload?.connections) ? payload.connections : [];
  const byId = new Map(rows.map((row) => [row.id, row]));
  const ordered = [...MODEL_IDS, ...EXECUTOR_IDS].map((id) => byId.get(id)).filter(Boolean).map(choice);
  const connected = ordered.filter((item) => item.selectable);
  const disconnected = ordered.filter((item) => !item.selectable);
  return {
    models: connected.filter((item) => item.kind === "model"),
    executors: connected.filter((item) => item.kind === "executor"),
    disconnected, selectedId: connected.some((item) => item.id === selectedId) ? selectedId : null,
    empty: connected.length ? null : t("model_picker.empty"),
  };
}

// GUI4-T05: one set of facts for the popover and the Apps & agents view.
export function pickerGroups(payload, composerOptions = null, query = "") {
  const rows = Array.isArray(payload?.connections) ? [...payload.connections] : [];
  const byId = new Map(rows.map((row) => [row.id, row]));
  const currentOptions = Array.isArray(composerOptions?.options) ? composerOptions.options : [];
  for (const option of currentOptions) {
    if (!option?.id || byId.has(option.id)) continue;
    rows.push({
      ...option,
      enabled: option.enabled ?? option.selectable,
      available: option.available ?? option.selectable,
      kind: option.kind === "app" ? "executor" : "model",
    });
  }
  const picker = buildModelPicker({ connections: rows });
  const options = new Map(currentOptions.map((row) => [row.id, row]));
  const legacyOptions = new Map((composerOptions?.executors || []).map((row) => [row.executor_id, row]));
  const connected = [...picker.models, ...picker.executors, ...picker.disconnected];
  const groups = { apps: [], local: [], cloud: [] };
  for (const item of connected) {
    const group = item.kind === "executor" ? "apps" : item.id === "ollama" ? "local" : "cloud";
    const option = options.get(item.id) || legacyOptions.get(item.id);
    const models = option?.models || [];
    const selectable = typeof option?.selectable === "boolean"
      ? option.selectable
      : option?.status === "available" ? true
        : typeof option?.available === "boolean" && item.kind === "executor"
          ? item.selectable && option.available
          : item.selectable;
    const reasonKey = option?.reason;
    const reason = reasonKey && (reasonKey === "local_only" || !item.reason)
      ? t(REASONS[reasonKey] || "model_picker.not_connected")
      : item.reason || (selectable ? null : t("model_picker.not_connected"));
    const contextWindow = option?.context_window ?? item.contextWindow;
    groups[group].push({ ...item, selectable, reason, models, modelVariants: item.id === "antigravity_cli" ? groupAgyModels(models) : [],
      efforts: option?.efforts || [],
      contextWindow: typeof contextWindow === "number" ? contextWindow : contextWindow?.tokens ?? null });
  }
  const needle = String(query || "").trim().toLocaleLowerCase();
  for (const key of Object.keys(groups)) {
    groups[key] = groups[key].filter((item) => !needle || [item.label, item.id, ...item.models]
      .some((part) => String(part).toLocaleLowerCase().includes(needle)));
  }
  return groups;
}

export function groupAgyModels(models) {
  const grouped = new Map();
  for (const model of models || []) {
    const match = /^(.*)-(low|medium|high|xhigh|max)$/.exec(model);
    const base = match ? match[1] : model;
    if (!grouped.has(base)) grouped.set(base, []);
    if (match) grouped.get(base).push(match[2]);
  }
  return [...grouped].map(([base, efforts]) => ({ base, efforts }));
}

export const AUTO_EFFORTS = ["medium", "high", "max"];
