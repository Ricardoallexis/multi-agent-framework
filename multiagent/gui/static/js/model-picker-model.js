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
};

export function quotaLines(quota) {
  return formatQuotaLines(quota);
}

function choice(row) {
  const id = row.id;
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
    id, kind: MODEL_IDS.includes(id) ? "model" : "executor", label: LABELS[id],
    selectable, checkedAt: row.checked_at || null, quota,
    verificationLabel,
    warning: quotaWarningText(row.warning),
    reason,
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
