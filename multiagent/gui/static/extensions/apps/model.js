// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model for «Apps y agentes» (#apps): transforms /gui/api/connections and /gui/api/setup/status without DOM or network.
import "./i18n.js";
import { t } from "../../js/i18n.js";
import { formatQuotaLines } from "../../js/quota-text.js";
import { limitReachedText, quotaWarningText } from "../../js/new-session-model.js";
import { activationView, manualEnableText } from "./activation-model.js";   // EJE-T02

export const EXECUTOR_IDS = ["claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"];
export const MODEL_IDS = ["gemini", "openai", "ollama"];

const LABELS = {
  claude_cli: "Claude Code",
  codex_cli: "Codex CLI",
  copilot_cli: "GitHub Copilot CLI",
  antigravity_cli: "Antigravity (agy)",
  gemini: "Gemini",
  openai: "OpenAI",
  ollama: "Ollama",
};

const REASONS = {
  not_installed: "ext.apps.reason.not_installed",
  disabled: "ext.apps.reason.disabled",
  notice_not_accepted: "ext.apps.reason.notice_not_accepted",
  session_missing: "ext.apps.reason.session_missing",
  key_missing: "ext.apps.reason.key_missing",
  key_invalid: "ext.apps.reason.key_invalid",
  no_access: "ext.apps.reason.no_access",
  limit_reached: "ext.apps.reason.limit_reached",
  status_unknown: "ext.apps.reason.status_unknown",
  version_check_failed: "ext.apps.reason.version_check_failed",
  session_unverifiable: "ext.apps.reason.session_unverifiable",
  reachability_unknown: "ext.apps.reason.reachability_unknown",
  unreachable: "ext.apps.reason.unreachable",
  tls_certificate: "ext.apps.reason.tls_certificate",
  quota_paused: "ext.apps.reason.quota_paused",
  quota_below_pause_floor: "ext.apps.reason.quota_below_pause_floor",
  quota_below_launch_floor: "ext.apps.reason.quota_below_launch_floor",
  usage_unavailable: "ext.apps.reason.usage_unavailable",
};
const reasonText = (reason) => REASONS[reason] ? t(REASONS[reason]) : t("ext.apps.reason.unavailable");

export const agyNotice = () => t("ext.apps.agy_notice");

// EJE-T02: the person turns an executor on with «Activar» (one by one, after the fixed note of the server).
export const executorEnableNotice = () => t("ext.apps.executor_enable_notice");

const VERSION_FAILURES = {
  timed_out: "ext.apps.version_failure.timed_out",
  probe_failed: "ext.apps.version_failure.probe_failed",
  uninterpretable: "ext.apps.version_failure.uninterpretable",
};

export function quotaLines(quota) {
  return formatQuotaLines(quota, { emptyWhenMissing: true });
}

function installedLabel(row, kind) {
  if (kind === "model") return row.id === "ollama" ? t("ext.apps.installed.local") : t("ext.apps.installed.not_applicable");
  if (row.installed === true) {
    if (row.detection_context === "packaged_process" && row.windows_package_path === true) {
      return t("ext.apps.installed.packaged");
    }
    return t("ext.apps.yes");
  }
  if (row.installed === false) return t("ext.apps.no");
  return t("ext.apps.unknown");
}

function sessionLabel(row, kind) {
  if (kind === "model") {
    if (row.id === "ollama") {
      if (row.reachable === true) return t("ext.apps.session.reachable");
      if (row.reachable === false) return t("ext.apps.session.unreachable");
      return t("ext.apps.unknown");
    }
    const setting = row.key_setting || (row.id === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY");
    const file = row.key_file || ".local/.env";
    return row.key_configured
      ? t("ext.apps.session.key_configured", { setting, file })
      : t("ext.apps.session.key_missing", { setting, file });
  }
  if (row.session === "signed_in") return row.id === "antigravity_cli" ? t("ext.apps.session.signed_in_usage") : t("ext.apps.session.signed_in");
  if (row.session === "signed_out") return t("ext.apps.session.signed_out");
  if (row.id === "copilot_cli") return t("ext.apps.session.unverifiable");
  if (row.id === "antigravity_cli") return t("ext.apps.session.unknown_usage");
  return t("ext.apps.session.unknown");
}

function enabledLabel(row) {
  if (row.id === "antigravity_cli") {
    if (row.enabled && row.notice_accepted) return t("ext.apps.enabled.notice_accepted");
    if (row.enabled && !row.notice_accepted) return t("ext.apps.enabled.notice_missing");
    return t("ext.apps.disabled");
  }
  return row.enabled ? t("ext.apps.enabled") : t("ext.apps.disabled");
}

export function permissionSummary(id, perm, kind) {
  if (kind === "model") return t("ext.apps.not_applicable");
  if (!perm) return t("ext.apps.permissions.unknown");
  if (perm.mode === "template") {
    if (perm.active) {
      return t("ext.apps.permissions.active", { path: perm.active_path }) +
        (perm.local_present ? t("ext.apps.permissions.local", { path: perm.local_path }) : "");
    }
    return t("ext.apps.permissions.inactive", { path: perm.template_path, guide: perm.guide });
  }
  if (perm.mode === "per_run") return t("ext.apps.permissions.per_run", { guide: perm.guide });
  if (perm.mode === "supervised_modes") return t("ext.apps.permissions.supervised", { guide: perm.guide });
  return t("ext.apps.no_data");
}

function consoleAction(row, step, title, detail, setupStatus, { optional = false } = {}) {
  const plan = setupStatus?.plans?.[row.id]?.[step] || null;
  const commandText = Array.isArray(plan?.command) && plan.command.length ? plan.command.join(" ") : null;
  return {
    kind: "console",
    step,
    optional,
    title,
    detail,
    commandText,
    source: plan?.source || null,
    sourceDate: plan?.source_date || null,
  };
}

// SIM-T03: simple mode never shows a line to paste or a command to copy. The steps that edit a file (enable an executor or a model,
// an API key) say that they belong to advanced mode; the console steps keep their button and lose the command text.
export const advancedStepText = () => t("ext.apps.advanced_step");
// EJE-T02: «Activar» is not a file edit, so an executor keeps its button in both modes.
const ADVANCED_STEP_KINDS = ["enable_model", "key"];

export function missingAction(row, kind, setupStatus, mode = "advanced") {
  const action = advancedAction(row, kind, setupStatus);
  if (mode !== "simple" || !action) return action;
  if (ADVANCED_STEP_KINDS.includes(action.kind)) return { kind: "advanced_only", title: action.title, text: advancedStepText() };
  if (action.kind === "console") {
    const { commandText, ...rest } = action;
    return rest;
  }
  if (action.kind === "enable_executor") {
    const { manualText, ...rest } = action;
    return rest;
  }
  return action;
}

function advancedAction(row, kind, setupStatus) {
  if (row.available === true) return null;
  const step = row.missing_step;
  if (step === "install") {
    if (row.id === "antigravity_cli") {
      return {
        kind: "info",
        title: t("ext.apps.install.agy.title"),
        detail: t("ext.apps.install.agy.detail"),
      };
    }
    return consoleAction(
      row,
      "install",
      t("ext.apps.install.title"),
      t("ext.apps.install.detail"),
      setupStatus,
    );
  }
  if (step === "enable") {
    if (kind === "executor") {
      const isAgy = row.id === "antigravity_cli";
      // EJE-T02: no line to paste and no «Copiar»; the button «Activar» (activation.js) does it. The manual way is a sentence.
      return {
        kind: "enable_executor",
        title: t("ext.apps.activation.state.ready"),
        notice: isAgy ? agyNotice() : executorEnableNotice(),
        manualText: manualEnableText(),
      };
    }
    return {
      kind: "enable_model",
      title: t("ext.apps.enable_model.title"),
      detail: t("ext.apps.enable_model.detail"),
      file: ".local/.env",
      line: "OPENAI_ENABLED=true",
      copyText: "OPENAI_ENABLED=true",
    };
  }
  if (step === "session") {
    if (row.id === "antigravity_cli") {
      return {
        kind: "info",
        title: t("ext.apps.login.agy.title"),
        detail: t("ext.apps.login.agy.detail"),
      };
    }
    return consoleAction(
      row,
      "login",
      t("ext.apps.login.title"),
      t("ext.apps.login.detail"),
      setupStatus,
    );
  }
  if (step === "key") {
    const setting = row.key_setting || (row.id === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY");
    const file = row.key_file || ".local/.env";
    return {
      kind: "key",
      title: t("ext.apps.key_missing.title"),
      setting,
      file,
      detail: t("ext.apps.key_missing.detail", { setting, file }),
    };
  }
  if (row.id === "copilot_cli" && row.reason === "session_unverifiable") {
    return consoleAction(
      row,
      "login",
      t("ext.apps.login.unverified.title"),
      t("ext.apps.login.unverified.detail"),
      setupStatus,
      { optional: true },
    );
  }
  if (row.reason === "reachability_unknown") {
    return {
      kind: "info",
      title: t("ext.apps.reachability_unknown.title"),
      detail: t("ext.apps.reachability_unknown.detail"),
    };
  }
  if (row.reason === "tls_certificate") {
    return {
      kind: "info",
      title: t(REASONS.tls_certificate),
      detail: t("ext.apps.tls_certificate.detail"),
    };
  }
  if (row.reason === "unreachable") {
    return {
      kind: "info",
      title: t(REASONS.unreachable),
      detail: t("ext.apps.unreachable.detail"),
    };
  }
  if (row.reason === "key_invalid") {
    const setting = row.key_setting || (row.id === "gemini" ? "GEMINI_API_KEY" : "OPENAI_API_KEY");
    const file = row.key_file || ".local/.env";
    return {
      kind: "key",
      title: t(REASONS.key_invalid),
      setting,
      file,
      detail: t("ext.apps.key_invalid.detail", { setting, file }),
    };
  }
  if (row.reason === "no_access") {
    return {
      kind: "info",
      title: t(REASONS.no_access),
      detail: t("ext.apps.no_access.detail"),
    };
  }
  if (row.limit || row.reason === "limit_reached" || row.reason === "quota_paused") {
    const limitText = limitReachedText(row.limit || row.resets_at || row.paused_until);
    return {
      kind: "info",
      title: t(REASONS.limit_reached),
      detail: t("ext.apps.limit_reached.detail", { limit: limitText }),
    };
  }
  return {
    kind: "info",
    title: reasonText(row.reason),
    detail: row.paused_until
      ? t("ext.apps.paused_until", { when: row.paused_until })
      : t("ext.apps.recheck_detail"),
  };
}

// EJE-T02: the chip of an executor card is the state of «Activar» (the five states of activationView).
const ACTIVATION_CHIPS = {
  not_installed: "not_installed",
  no_session: "no_session",
  ready: "ready",
  active: "active",
  paused: "paused",
};

function statusChipFor(row, view = null) {
  if (row.id === "gemini") {
    if (row.available === true) {
      return row.verification === "verified"
        ? { status: "connected", label: t("ext.apps.status.connected") }
        : { status: "unverified", label: t("ext.apps.status.unverified") };
    }
    if (row.reason === "key_invalid") return { status: "key_invalid", label: t("ext.apps.status.key_invalid") };
    if (row.reason === "no_access") return { status: "no_access", label: t("ext.apps.status.no_access") };
    if (row.reason === "limit_reached") return { status: "limit_reached", label: t("ext.apps.status.limit_reached") };
  }
  if (row.available !== true && (row.limit || row.reason === "limit_reached" || row.reason === "quota_paused")) {
    return { status: "limit_reached", label: t("ext.apps.status.limit_reached") };
  }
  if (view?.state === "conflict") {
    return { status: "conflict", label: t("ext.apps.status.conflict") };
  }
  if (view && ACTIVATION_CHIPS[view.state]) {
    return { status: ACTIVATION_CHIPS[view.state], label: view.label };
  }
  if (row.available === true) return { status: "ok", label: t("ext.apps.status.ready") };
  if (row.missing_step) return { status: "waiting", label: t("ext.apps.status.missing_step") };
  if (row.reason === "session_unverifiable" || row.reason === "reachability_unknown") {
    return { status: "unknown", label: t("ext.apps.status.unknown") };
  }
  return { status: "error", label: t("ext.apps.reason.unavailable") };
}

export function isPendingStep(row) {
  return Boolean(row && row.available !== true && row.missing_step);
}

export function missingSummaryLine(row, action, chip, reasonText) {
  if (!row || row.available === true) return null;
  if (action && action.title && action.title !== chip.label) return action.title;
  if (reasonText && reasonText !== chip.label) return reasonText;
  return null;
}

export function canShowAllReady(item) {
  return item.available === true && !item.action && (!item.activation || item.activation.state === "active");
}

export function groupModelsByType(models, composerOptionsPayload = null) {
  const optionKinds = new Map((Array.isArray(composerOptionsPayload?.options) ? composerOptionsPayload.options : [])
    .filter((option) => option && typeof option.id === "string")
    .map((option) => [option.id, option.kind]));
  const modelKind = (item) => {
    const kind = optionKinds.get(item.id);
    if (kind === "local" || kind === "cloud") return kind;
    return item.id === "ollama" ? "local" : "cloud";
  };
  return {
    localModels: models.filter((item) => modelKind(item) === "local"),
    cloudModels: models.filter((item) => modelKind(item) === "cloud"),
  };
}

// ARR3-T07: the server decides when a card may offer the certificate button; here it is only read.
function tlsButton(button) {
  if (!button || typeof button.trust !== "string" || typeof button.label !== "string" || !button.label) return null;
  return { trust: button.trust, label: button.label };
}

function buildEntry(row, kind, setupStatus, mode, activation = null) {
  const perm = setupStatus?.permissions?.[row.id] || null;
  const activationRow = kind === "executor" ? activation : null;
  const activationViewModel = kind === "executor" ? activationView(row, activationRow) : null;
  const conflict = activationViewModel?.state === "ready" &&
    (row.installed === false || ["probe_failed", "timed_out", "uninterpretable"].includes(row.version_result));
  const view = conflict
    ? { ...activationViewModel, state: "conflict", label: t("ext.apps.status.conflict"), actions: [] }
    : activationViewModel;
  // Without the answer of the server the chip stays what it was: an executor enabled by hand is «Listo», not «Listo para activar».
  const chip = statusChipFor(row, activationRow ? view : null);
  const limited = row.available !== true && (Boolean(row.limit) || row.reason === "limit_reached" || row.reason === "quota_paused");
  const reasonText = row.available
    ? null
    : limited
      ? limitReachedText(row.limit || row.resets_at || row.paused_until)
      : (REASONS[row.reason] ? t(REASONS[row.reason]) : row.reason || t("ext.apps.reason.unavailable") + ".");
  const action = missingAction(row, kind, setupStatus, mode);
  return {
    id: row.id,
    kind,
    label: LABELS[row.id] || row.id,
    available: row.available === true,
    defaultOpen: isPendingStep(row),
    missingLine: missingSummaryLine(row, action, chip, reasonText),
    chip,
    installed: installedLabel(row, kind),
    version: row.version || (kind === "model" && row.id !== "ollama"
      ? t("ext.apps.not_applicable")
      : ["probe_failed", "timed_out", "uninterpretable"].includes(row.version_result)
        ? t("ext.apps.version_unchecked", { reason: t(VERSION_FAILURES[row.version_result]) })
        : t("ext.apps.version.no_data")),
    session: sessionLabel(row, kind),
    enabled: enabledLabel(row),
    permissions: permissionSummary(row.id, perm, kind),
    quota: row.id === "gemini" ? [t("ext.apps.gemini.no_quota")] : quotaLines(row.quota),
    warning: quotaWarningText(row.warning),
    canProbe: kind === "model",
    activation: view,
    tlsFix: tlsButton(row.tls_fix),
    tlsRevert: tlsButton(row.tls_revert),
    reasonText,
    checkedAt: row.detection_observed_at || row.checked_at || null,
    action,
  };
}

export function buildAppsView(connectionsPayload, setupStatus = null, mode = "advanced", activationsPayload = null) {
  const rows = Array.isArray(connectionsPayload?.connections) ? connectionsPayload.connections : [];
  const byId = new Map(rows.map((row) => [row.id, row]));
  const activationRows = Array.isArray(activationsPayload?.executors) ? activationsPayload.executors : [];
  const activationById = new Map(activationRows.map((item) => [item.id, item]));
  const executors = EXECUTOR_IDS.map((id) => byId.get(id)).filter(Boolean)
    .map((row) => buildEntry(row, "executor", setupStatus, mode, activationById.get(row.id) || null));
  const models = MODEL_IDS.map((id) => byId.get(id)).filter(Boolean).map((row) => buildEntry(row, "model", setupStatus, mode));
  const modelGroups = groupModelsByType(models);
  return {
    executors,
    models,
    ...modelGroups,
    allowLaunch: setupStatus ? Boolean(setupStatus.allow_launch) : true,
    consoleSupported: setupStatus ? Boolean(setupStatus.console_supported) : true,
    empty: executors.length === 0 && models.length === 0,
    canStopAll: activationRows.length > 0,
  };
}
