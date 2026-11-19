// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure layout decisions for "Ejecuciones" (RWD-T02): master-detail panes by viewport width and waiting-action placement.
// No DOM, no network, no storage so Node tests can verify every combination directly.

export const TABLET_MAX_WIDTH = 899.98;
export const PHONE_MAX_WIDTH = 599.98;
export const NARROW_QUERY = "(max-width: 899.98px)";
export const PHONE_QUERY = "(max-width: 599.98px)";

const WAITING_LABELS = {
  review: "Esperando tu revisión",
  approval_pending: "Esperando tu aprobación",
  budget_exhausted: "Presupuesto agotado",
  tool_approval: "Esperando aprobación de herramienta",
  execute_step: "Paso manual pendiente",
  research: "Investigación externa pendiente",
};

export function waitingReasonLabel(waiting) {
  if (!waiting || typeof waiting !== "object") return "";
  const reason = typeof waiting.reason === "string" ? waiting.reason : "";
  const base = WAITING_LABELS[reason] || "Esperando acción humana";
  const stepId = typeof waiting.step_id === "string" ? waiting.step_id.trim() : "";
  return stepId ? base + " · paso «" + stepId + "»" : base;
}

export function computeRunsLayout({
  width = null,
  narrow = false,
  phone = false,
  hasSelection = false,
  creating = false,
  waiting = false,
  terminal = false,
} = {}) {
  const hasWidth = typeof width === "number" && Number.isFinite(width);
  const isNarrow = hasWidth ? width <= TABLET_MAX_WIDTH : Boolean(narrow);
  const isPhone = hasWidth ? width <= PHONE_MAX_WIDTH : Boolean(phone);
  const activeDetail = Boolean(hasSelection) || Boolean(creating);
  const isWaiting = Boolean(waiting) && !Boolean(terminal);

  return {
    narrow: isNarrow,
    phone: isPhone,
    showList: !isNarrow || !activeDetail,
    showDetail: !isNarrow || activeDetail,
    showBack: isNarrow && activeDetail,
    actionFirst: isWaiting,
    actionBar: isPhone && isWaiting,
    actionPosition: isWaiting ? (isPhone ? "bottom-bar" : "top") : "bottom",
  };
}
