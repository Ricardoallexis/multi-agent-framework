// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Connection facts -> the ordered, selectable choices of Nueva sesión. No DOM or network.
import { formatQuotaLines } from "./quota-text.js";

export const MODEL_IDS = ["gemini", "openai", "ollama"];
export const EXECUTOR_IDS = ["claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"];

const LABELS = {
  gemini: "Gemini", openai: "OpenAI", ollama: "Ollama",
  claude_cli: "Claude", codex_cli: "Codex", copilot_cli: "Copilot", antigravity_cli: "Antigravity (agy)",
};
const STEPS = {
  install: "Falta instalarlo.", session: "Falta iniciar sesión.", key: "Falta configurar la clave.",
  enable: "Falta habilitarlo.",
};
const REASONS = {
  disabled: "No está habilitado.", notice_not_accepted: "Falta aceptar el aviso de agy.",
  quota_paused: "La cuota está en pausa.", quota_below_pause_floor: "Queda menos del 10 % de cuota.",
  quota_below_launch_floor: "Queda menos del 15 % de cuota.", usage_unavailable: "No se pudo comprobar la cuota.",
  session_unverifiable: "No se puede comprobar la sesión sin gastar cuota.",
  status_unknown: "No se pudo comprobar la conexión.", reachability_unknown: "No se pudo comprobar el acceso.",
  unreachable: "No responde.", tls_certificate: "No se pudo verificar el certificado TLS.",
  not_installed: "No está instalado.", session_missing: "Falta iniciar sesión.", key_missing: "Falta configurar la clave.",
};

export function quotaLines(quota) {
  return formatQuotaLines(quota);
}

function choice(row) {
  const id = row.id;
  const selectable = row.enabled === true && row.available === true;
  return {
    id, kind: MODEL_IDS.includes(id) ? "model" : "executor", label: LABELS[id],
    selectable, checkedAt: row.checked_at || null, quota: quotaLines(row.quota),
    reason: selectable ? null : STEPS[row.missing_step] || REASONS[row.reason] || "No está conectado.",
    agyNotice: id === "antigravity_cli"
      ? "Usa tu suscripción de agy. Revisa las condiciones de tu plan y mantén desactivados los créditos de IA. Respeta la cuota; no uses cuentas compartidas o de terceros. Cada lanzamiento pide aprobación."
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
    empty: connected.length ? null : "No hay modelos ni ejecutores conectados para elegir.",
  };
}
