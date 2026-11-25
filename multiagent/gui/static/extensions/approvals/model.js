// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import "./i18n.js";
import { t } from "../../js/i18n.js";
// Pure view-model and one-use challenge/decision protocol for the «Aprobaciones» extension (SEC-T04).
// No DOM, no Preact, and no network calls: callers inject `post` so Node tests can run without fetch.

export const sessionUnavailableNotice = () => t("ext.approvals.session_unavailable");
export const expiredCodeNotice = () => t("ext.approvals.expired_code");

const KIND_INFO = {
  secret_send: {
    label: "ext.approvals.kind.secret_send",
    reason: "ext.approvals.reason.secret_send",
    approveLabel: "ext.approvals.approve_send",
    rejectLabel: "ext.approvals.reject",
  },
  osv_query: {
    label: "ext.approvals.kind.osv_query",
    reason: "ext.approvals.reason.osv_query",
    approveLabel: "ext.approvals.approve_send",
    rejectLabel: "ext.approvals.reject",
  },
  bundle_load: {
    label: "ext.approvals.kind.bundle_load",
    reason: "ext.approvals.reason.bundle_load",
    approveLabel: "ext.approvals.approve_send",
    rejectLabel: "ext.approvals.reject",
  },
  memory_trust: {
    label: "ext.approvals.kind.memory_trust",
    reason: "ext.approvals.reason.memory_trust",
    approveLabel: "ext.approvals.approve",
    rejectLabel: "ext.approvals.reject",
  },
  memory_delete: {
    label: "ext.approvals.kind.memory_delete",
    reason: "ext.approvals.reason.memory_delete",
    approveLabel: "ext.approvals.approve",
    rejectLabel: "ext.approvals.reject",
  },
};

export const MEMORY_KINDS = Object.freeze(["memory_trust", "memory_delete"]);

// The title of a memory entry is data written by someone else: it is only ever used as text.
export function memoryEntryTitle(location) {
  const title = location && typeof location === "object" && !Array.isArray(location) ? location.title : "";
  const clean = typeof title === "string" ? title.trim() : "";
  return clean || t("ext.approvals.untitled_note");
}

// MEM-T02: a memory_trust that clashes with an entry the person confirmed carries both texts, already cut by the server.
// They are data written by someone else: only ever shown as text, and cut again here so a large answer cannot flood the card.
const clashReason = () => t("ext.approvals.clash_reason");
const CLASH_TITLE_MAX = 80;
const CLASH_BODY_MAX = 240;
const cutText = (value, max) => {
  const text = typeof value === "string" ? value.trim() : "";
  return text.length > max ? text.slice(0, max - 1).trimEnd() + "…" : text;
};
export function memoryClash(row) {
  const pair = row && typeof row === "object" ? row.conflict : null;
  if (!pair || typeof pair !== "object" || Array.isArray(pair)) return null;
  const side = (entry) => entry && typeof entry === "object" && !Array.isArray(entry)
    ? { title: cutText(entry.title, CLASH_TITLE_MAX) || t("ext.approvals.untitled_note"), body: cutText(entry.body, CLASH_BODY_MAX) }
    : null;
  const fresh = side(pair.new);
  const existing = side(pair.existing);
  return fresh && existing ? { new: fresh, existing } : null;
}

const SECRET_TYPE_LABELS = {
  private_key: "ext.approvals.secret.private_key",
  secret_key: "ext.approvals.secret.secret_key",
  bearer: "ext.approvals.secret.bearer",
  long_token: "ext.approvals.secret.long_token",
};

const LOCATION_LABELS = {
  new_session: "ext.approvals.location.new_session",
  planner: "ext.approvals.location.planner",
  planning: "ext.approvals.location.planner",
  executor: "ext.approvals.location.executor",
};

const MASK_MARKERS = /[*…•]/;

export const challengePath = (id) => "/gui/api/approvals/" + encodeURIComponent(id) + "/challenge";
export const decisionPath = (id) => "/gui/api/approvals/" + encodeURIComponent(id) + "/decision";

export function formatSecretTypes(secretTypes, kind) {
  const list = Array.isArray(secretTypes)
    ? secretTypes.map((item) => SECRET_TYPE_LABELS[item] ? t(SECRET_TYPE_LABELS[item]) : String(item || "").trim()).filter(Boolean)
    : [];
  if (list.length > 0) return list;
  if (kind === "osv_query") return [t("ext.approvals.kind.osv_query")];
  if (kind === "bundle_load") return [t("ext.approvals.kind.bundle_load")];
  return [t("ext.approvals.no_types")];
}

export function formatDestination(destination) {
  if (typeof destination === "string") return destination.trim() || t("ext.approvals.no_destination");
  if (!destination || typeof destination !== "object" || Array.isArray(destination)) return t("ext.approvals.no_destination");
  if (destination.executor || destination.executor_id) {
    const parts = [destination.executor || destination.executor_id, destination.model].filter(Boolean);
    if (parts.length > 0) return parts.join(" · ");
  }
  if (destination.provider || destination.model) {
    const parts = [destination.provider, destination.model, destination.tier].filter(Boolean);
    if (parts.length > 0) return parts.join(" · ");
  }
  if (destination.host || destination.url) return String(destination.host || destination.url);
  const pairs = Object.entries(destination)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .map(([key, value]) => key + ": " + String(value));
  return pairs.length > 0 ? pairs.join(" · ") : t("ext.approvals.no_destination");
}

export function formatLocation(location) {
  if (typeof location === "string") {
    const clean = location.trim();
    return LOCATION_LABELS[clean] ? t(LOCATION_LABELS[clean]) : clean || t("ext.approvals.no_origin");
  }
  if (!location || typeof location !== "object" || Array.isArray(location)) return t("ext.approvals.no_origin");
  const parts = [];
  const runId = location.run || location.run_id;
  const stepId = location.step || location.step_id;
  if (runId) parts.push(t("ext.approvals.location.run", { id: runId }) + (stepId ? t("ext.approvals.location.step_suffix", { id: stepId }) : ""));
  else if (stepId) parts.push(t("ext.approvals.location.step", { id: stepId }));
  if (location.task || location.task_id) parts.push(t("ext.approvals.location.task", { id: location.task || location.task_id }));
  if (location.plan || location.plan_id) parts.push(t("ext.approvals.location.plan", { id: location.plan || location.plan_id }));
  if (location.bundle) parts.push(t("ext.approvals.location.bundle", { name: location.bundle }));
  const surface = location.surface || location.source;
  if (surface) parts.push(LOCATION_LABELS[surface] ? t(LOCATION_LABELS[surface]) : String(surface));
  if (parts.length > 0) return parts.join(" · ");
  const pairs = Object.entries(location)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .map(([key, value]) => key + ": " + String(value));
  return pairs.length > 0 ? pairs.join(" · ") : t("ext.approvals.no_origin");
}

// Never display an unmasked string, even if malformed data arrives.
export function safeMaskedSample(sample) {
  if (typeof sample !== "string" || !sample.trim()) return t("ext.approvals.no_sample");
  const text = sample.trim();
  if (!MASK_MARKERS.test(text)) return t("ext.approvals.unmasked_sample");
  return text.length > 160 ? text.slice(0, 159) + "…" : text;
}

export function describeApproval(item) {
  const row = item && typeof item === "object" ? item : {};
  const kind = String(row.kind || "secret_send");
  const info = KIND_INFO[kind] || {
    label: kind,
    reason: "ext.approvals.reason.unknown",
    approveLabel: "ext.approvals.approve_send",
    rejectLabel: "ext.approvals.reject",
  };
  const types = formatSecretTypes(row.secret_types, kind);
  const memory = MEMORY_KINDS.includes(kind);
  const conflict = kind === "memory_trust" ? memoryClash(row) : null;
  return {
    memory,
    conflict,
    entryTitle: memory ? memoryEntryTitle(row.location) : "",
    id: String(row.id || ""),
    kind,
    title: info.label.startsWith("ext.approvals.") ? t(info.label) : info.label,
    reason: conflict ? clashReason() : t(info.reason),
    destination: formatDestination(row.destination),
    location: formatLocation(row.location),
    types,
    typesText: types.join(", "),
    maskedSample: safeMaskedSample(row.masked_sample),
    createdAt: typeof row.created_at === "string" ? row.created_at : "",
    sha256: typeof row.sha256 === "string" ? row.sha256 : "",
    state: String(row.state || "pending"),
    approveLabel: t(info.approveLabel),
    rejectLabel: t(info.rejectLabel),
  };
}

export function orderApprovals(items, selectedId = null) {
  const list = (Array.isArray(items) ? items : []).filter((item) => item && item.id);
  if (!selectedId) return list;
  const hit = list.find((item) => item.id === selectedId);
  if (!hit) return list;
  return [hit, ...list.filter((item) => item.id !== selectedId)];
}

export function hasSelectedApproval(items, selectedId) {
  if (!selectedId) return true;
  return (Array.isArray(items) ? items : []).some((item) => item && item.id === selectedId);
}

export function isSessionUnavailableError(error) {
  if (!error || typeof error !== "object") return false;
  if (error.code === "approval_session_unavailable") return true;
  const details = error.details && typeof error.details === "object" ? error.details : {};
  return details.reason === "approval_session_unavailable" || details.cause === "missing_or_invalid_session";
}

export function isExpiredChallengeError(error) {
  if (!error || typeof error !== "object") return false;
  if (error.code === "invalid_or_expired_code") return true;
  const details = error.details && typeof error.details === "object" ? error.details : {};
  return details.cause === "invalid_or_expired_code";
}

export function approvalErrorInfo(error) {
  if (isSessionUnavailableError(error)) {
    return { sessionUnavailable: true, retryable: false, stale: false, message: sessionUnavailableNotice() };
  }
  if (isExpiredChallengeError(error)) {
    return { sessionUnavailable: false, retryable: true, stale: false, message: expiredCodeNotice() };
  }
  const details = error && error.details && typeof error.details === "object" ? error.details : {};
  if (details.reason === "already_decided" || (error && error.status === 404)) {
    return {
      sessionUnavailable: false,
      retryable: false,
      stale: true,
      message: t("ext.approvals.already_decided"),
    };
  }
  if (error && error.status === 409 && error.code === "invalid_state") {
    return {
      sessionUnavailable: false,
      retryable: false,
      stale: true,
      message: error.message || t("ext.approvals.not_pending"),
    };
  }
  return {
    sessionUnavailable: false,
    retryable: false,
    stale: false,
    message: (error && error.message) || t("ext.approvals.action_failed"),
  };
}

export async function submitApprovalDecision({ item, decision, post, riskAck = false }) {
  if (decision !== "approve" && decision !== "reject") {
    throw new Error("Decision must be approve or reject");
  }
  if (!item || !item.id) throw new Error("Missing approval id");
  if (typeof post !== "function") throw new Error("Missing post function");
  try {
    const challenge = await post(challengePath(item.id), {});
    if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
      return {
        ok: false,
        sessionUnavailable: false,
        retryable: true,
        stale: false,
        message: t("ext.approvals.code_missing"),
      };
    }
    if (item.sha256 && challenge.sha256 && challenge.sha256 !== item.sha256) {
      return {
        ok: false,
        sessionUnavailable: false,
        retryable: false,
        stale: true,
        message: t("ext.approvals.content_changed"),
      };
    }
    const body = { code: challenge.code, decision };
    if (item.kind === "bundle_load" && decision === "approve") body.risk_ack = riskAck === true;
    const result = await post(decisionPath(item.id), body);
    return { ok: true, result };
  } catch (failure) {
    return { ok: false, ...approvalErrorInfo(failure) };
  }
}
