// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts of the runs screen. The server sends codes and facts; the wording lives in the dictionaries.

import { currentLanguage, t } from "./i18n.js";
import { labelEntry, labelSet, legacyInfo } from "./ui/labels.js";

export const MODE_LABELS = {
  auto: "",
  local: "",
  cloud: "",
  human_guided: "",
};
for (const mode of Object.keys(MODE_LABELS)) {
  Object.defineProperty(MODE_LABELS, mode, { enumerable: true, get: () => t("common.mode." + mode) });
}

// The words of every status live in ui/labels.js (the single label map); these are its {label, tone, help} views.
export const statusInfo = (status) => legacyInfo("run", status);

export const stepStateLabel = (state) => labelEntry(labelSet("step"), state).text;

export function skipReason(skip) {
  const condition = skip && skip.condition;
  if (condition === "requires_web") return t("common.skip.requires_web");
  if (condition === "not_requires_web") return t("common.skip.not_requires_web");
  if (condition) return t("common.skip.condition", { condition });
  return t("common.skip.unrecorded");
}

export function executorText(executor) {
  if (!executor || !executor.kind) return "";
  if (executor.kind === "human") {
    return t("common.executor.human", {
      provider: executor.provider || t("common.value.unspecified"),
      model: executor.model ? t("common.executor.model_suffix", { model: executor.model }) : "",
    });
  }
  const parts = [executor.model, executor.provider].filter(Boolean);
  return t("common.executor.model", { model: parts.join(" · ") || t("common.value.unknown") });
}

export function waitingText(waiting) {
  if (!waiting) return "";
  const step = waiting.step_id
    ? t("common.waiting.step", { step_id: waiting.step_id, attempt: waiting.attempt ? t("common.waiting.attempt", { attempt: waiting.attempt }) : "" })
    : t("common.waiting.run");
  switch (waiting.reason) {
    case "review": return t("common.waiting.review", { step });
    case "budget_exhausted": return t("common.waiting.budget", { step });
    case "execute_step": return t("common.waiting.execute_step", { step });
    case "research": return t("common.waiting.research", { step });
    default: return t("common.waiting.default", { step });
  }
}

export function cursorText(view) {
  if (!view.definition_available) return t("common.cursor.definition_missing");
  if (view.status === "completed") return "";   // the summary above already says all steps are done
  if (view.cursor.finished) return t("common.cursor.all_done");
  if (view.terminal) return t("common.cursor.terminal");
  return t("common.cursor.at_step", { current: view.cursor.index + 1, total: view.cursor.total });
}

export function historyText(entry) {
  const step = entry.step_id
    ? t("common.history.step", { step_id: entry.step_id, attempt: entry.attempt ? t("common.history.attempt", { attempt: entry.attempt }) : "" })
    : "";
  const withStep = (connector) => step ? t("common.history." + connector) + step : "";
  switch (entry.kind) {
    case "prompt_delivered": return t("common.history.prompt_delivered", { step: withStep("step_prompt") });
    case "response_submitted":
      return t("common.history.response_submitted", {
        step: withStep("step_response"),
        provider: entry.provider || t("common.value.unspecified"),
        model: entry.model ? t("common.history.model_suffix", { model: entry.model }) : t("common.history.no_model"),
      });
    case "response_rejected": return t("common.history.response_rejected", { step: withStep("step_rejected") });
    case "approved": return t("common.history.approved", { step: withStep("step_approved") });
    case "changes_requested": return t("common.history.changes_requested", { step: withStep("step_changes") });
    case "regenerated": return t("common.history.regenerated", { step: withStep("step_regenerated") });
    case "rejected": return t("common.history.rejected");
    case "cancel_requested": return t("common.history.cancel_requested");
    case "cancelled": return entry.forced ? t("common.history.cancelled_forced") : t("common.history.cancelled");
    default: return entry.kind;
  }
}

export function actorText(entry) {
  if (entry.actor === "system") return t("common.actor.system");
  if (entry.actor === "declared") return t("common.actor.declared");
  if (entry.actor === "unknown") return t("common.actor.unknown");
  // An approval shows who its event says gave it and the door it came through; the text is declared, not verified.
  if (entry.actor && entry.actor !== "operator" && entry.actor !== "user:operator") {
    return entry.actor + (entry.channel ? " · " + entry.channel : "");
  }
  return entry.channel ? t("common.actor.operator_channel", { channel: entry.channel }) : t("common.actor.operator");
}

// Example shown as placeholder text; a choice field has no free text, so it shows none.
export function exampleText(item) {
  return item.example && item.type !== "choice" ? t("common.example", { example: item.example }) : null;
}

// Where the form's inputs come from, in one sentence.
export function inputsNote(spec) {
  const derived = spec.inputs.filter((item) => item.source !== "declared").length;
  if (spec.inputs.length === 0) return t("common.inputs.none");
  if (derived === 0) return t("common.inputs.declared");
  const rule = t("common.inputs.derived_rule");
  if (derived === spec.inputs.length) return t("common.inputs.all_derived", { rule });
  return t("common.inputs.some_derived", { rule });
}

export function inputHelp(item) {
  const parts = [];
  if (item.skipped_only) {
    const steps = item.used_in.join("», «");
    parts.push(t(item.used_in.length > 1 ? "common.input_help.skipped_many" : "common.input_help.skipped_one", { steps }));
  } else if (item.used_in.length) {
    parts.push(t("common.input_help.used_in", { steps: item.used_in.join(", ") }));
  }
  // A declared input explains itself (description and example); the prompt line is for the ones that do not.
  if (item.source !== "declared" && item.prompt_line) parts.push(t("common.input_help.prompt", { prompt_line: item.prompt_line }));
  return parts.join(" ");
}

export function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(currentLanguage());
}

export function fmtJson(value) {
  try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
}

export function errorText(error) {
  const details = error.details || {};
  if (details.missing_inputs && details.missing_inputs.length) {
    return t("common.error.missing_inputs", { names: details.missing_inputs.join(", ") });
  }
  switch (error.code) {
    case "unauthorized":
      return t("common.error.unauthorized");
    case "network_error": return t("common.error.network");
    case "invalid_state":
    case "invalid_operation":
      return t("common.error.invalid_state", { message: error.message });
    case "budget_exceeded": return t("common.error.budget_exceeded", { message: error.message });
    case "human_submission_invalid": return t("common.error.human_submission_invalid", { message: error.message });
    case "human_step_unavailable": return t("common.error.human_step_unavailable");
    case "not_found": return t("common.error.not_found", { message: error.message });
    case "idempotency_conflict": return t("common.error.idempotency_conflict");
    default: {
      const issues = [];
      for (const item of details.errors || []) if (item && item.msg) issues.push(item.msg);
      for (const issue of details.issues || []) if (issue && issue.message) issues.push(issue.message);
      return t("common.error.unknown", {
        message: error.message,
        issues: issues.length ? t("common.error.issue_suffix", { issues: issues.join("; ") }) : "",
        code: error.code,
      });
    }
  }
}
