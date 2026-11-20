// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model and one-use challenge/decision protocol for the «Aprobaciones» extension (SEC-T04).
// No DOM, no Preact, and no network calls: callers inject `post` so Node tests can run without fetch.

export const SESSION_UNAVAILABLE_NOTICE =
  "Las aprobaciones se activan en la pestaña que abrió «multiagent gui». Si no es esta, reinicia la GUI.";
export const EXPIRED_CODE_NOTICE =
  "El código de un solo uso venció o ya no es válido. Inténtalo de nuevo.";

const KIND_INFO = {
  secret_send: {
    label: "Envío con forma de secreto",
    reason: "Se detectó contenido con forma de secreto antes de enviarlo.",
    approveLabel: "Aprobar este envío",
    rejectLabel: "Rechazar",
  },
  osv_query: {
    label: "Consulta a OSV.dev",
    reason: "Se consultarán los paquetes declarados en api.osv.dev antes de escanear el bundle.",
    approveLabel: "Aprobar este envío",
    rejectLabel: "Rechazar",
  },
  bundle_load: {
    label: "Carga de bundle externo",
    reason: "El resultado del escaneo requiere confirmación antes de cargar el bundle.",
    approveLabel: "Aprobar este envío",
    rejectLabel: "Rechazar",
  },
};

const SECRET_TYPE_LABELS = {
  private_key: "Clave privada",
  secret_key: "Clave secreta",
  bearer: "Token Bearer",
  long_token: "Token largo",
};

const LOCATION_LABELS = {
  new_session: "Nueva sesión",
  planner: "Planificador",
  planning: "Planificador",
  executor: "Ejecutor",
};

const MASK_MARKERS = /[*…•]/;

export const challengePath = (id) => "/gui/api/approvals/" + encodeURIComponent(id) + "/challenge";
export const decisionPath = (id) => "/gui/api/approvals/" + encodeURIComponent(id) + "/decision";

export function formatSecretTypes(secretTypes, kind) {
  const list = Array.isArray(secretTypes)
    ? secretTypes.map((item) => SECRET_TYPE_LABELS[item] || String(item || "").trim()).filter(Boolean)
    : [];
  if (list.length > 0) return list;
  if (kind === "osv_query") return ["Consulta a OSV.dev"];
  if (kind === "bundle_load") return ["Carga de bundle externo"];
  return ["Sin tipos indicados"];
}

export function formatDestination(destination) {
  if (typeof destination === "string") return destination.trim() || "Sin destino indicado";
  if (!destination || typeof destination !== "object" || Array.isArray(destination)) return "Sin destino indicado";
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
  return pairs.length > 0 ? pairs.join(" · ") : "Sin destino indicado";
}

export function formatLocation(location) {
  if (typeof location === "string") {
    const clean = location.trim();
    return LOCATION_LABELS[clean] || clean || "Sin origen indicado";
  }
  if (!location || typeof location !== "object" || Array.isArray(location)) return "Sin origen indicado";
  const parts = [];
  const runId = location.run || location.run_id;
  const stepId = location.step || location.step_id;
  if (runId) parts.push("Ejecución " + runId + (stepId ? " · paso " + stepId : ""));
  else if (stepId) parts.push("Paso " + stepId);
  if (location.task || location.task_id) parts.push("Tarea " + (location.task || location.task_id));
  if (location.plan || location.plan_id) parts.push("Plan " + (location.plan || location.plan_id));
  if (location.bundle) parts.push("Bundle " + location.bundle);
  const surface = location.surface || location.source;
  if (surface) parts.push(LOCATION_LABELS[surface] || String(surface));
  if (parts.length > 0) return parts.join(" · ");
  const pairs = Object.entries(location)
    .filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== "")
    .map(([key, value]) => key + ": " + String(value));
  return pairs.length > 0 ? pairs.join(" · ") : "Sin origen indicado";
}

// Never display an unmasked string, even if malformed data arrives.
export function safeMaskedSample(sample) {
  if (typeof sample !== "string" || !sample.trim()) return "Sin muestra";
  const text = sample.trim();
  if (!MASK_MARKERS.test(text)) return "[muestra omitida: sin enmascarar]";
  return text.length > 160 ? text.slice(0, 159) + "…" : text;
}

export function describeApproval(item) {
  const row = item && typeof item === "object" ? item : {};
  const kind = String(row.kind || "secret_send");
  const info = KIND_INFO[kind] || {
    label: kind,
    reason: "Esta acción requiere tu confirmación en Aprobaciones.",
    approveLabel: "Aprobar este envío",
    rejectLabel: "Rechazar",
  };
  const types = formatSecretTypes(row.secret_types, kind);
  return {
    id: String(row.id || ""),
    kind,
    title: info.label,
    reason: info.reason,
    destination: formatDestination(row.destination),
    location: formatLocation(row.location),
    types,
    typesText: types.join(", "),
    maskedSample: safeMaskedSample(row.masked_sample),
    createdAt: typeof row.created_at === "string" ? row.created_at : "",
    sha256: typeof row.sha256 === "string" ? row.sha256 : "",
    state: String(row.state || "pending"),
    approveLabel: info.approveLabel,
    rejectLabel: info.rejectLabel,
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
    return { sessionUnavailable: true, retryable: false, stale: false, message: SESSION_UNAVAILABLE_NOTICE };
  }
  if (isExpiredChallengeError(error)) {
    return { sessionUnavailable: false, retryable: true, stale: false, message: EXPIRED_CODE_NOTICE };
  }
  const details = error && error.details && typeof error.details === "object" ? error.details : {};
  if (details.reason === "already_decided" || (error && error.status === 404)) {
    return {
      sessionUnavailable: false,
      retryable: false,
      stale: true,
      message: "Esta solicitud ya fue decidida o ya no está pendiente.",
    };
  }
  if (error && error.status === 409 && error.code === "invalid_state") {
    return {
      sessionUnavailable: false,
      retryable: false,
      stale: true,
      message: error.message || "Esta solicitud ya no está pendiente.",
    };
  }
  return {
    sessionUnavailable: false,
    retryable: false,
    stale: false,
    message: (error && error.message) || "No se pudo completar la acción.",
  };
}

export async function submitApprovalDecision({ item, decision, post }) {
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
        message: "El servidor no entregó un código de un solo uso válido.",
      };
    }
    if (item.sha256 && challenge.sha256 && challenge.sha256 !== item.sha256) {
      return {
        ok: false,
        sessionUnavailable: false,
        retryable: false,
        stale: true,
        message: "El contenido de la solicitud cambió; actualiza la lista antes de decidir.",
      };
    }
    const result = await post(decisionPath(item.id), { code: challenge.code, decision });
    return { ok: true, result };
  } catch (failure) {
    return { ok: false, ...approvalErrorInfo(failure) };
  }
}
