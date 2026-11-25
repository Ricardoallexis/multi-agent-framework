// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure layout decisions for "Ejecuciones" (RWD-T02): master-detail panes by viewport width and waiting-action placement.
// No DOM, no network, no storage so Node tests can verify every combination directly.

export const TABLET_MAX_WIDTH = 899.98;
export const PHONE_MAX_WIDTH = 599.98;
export const NARROW_QUERY = "(max-width: 899.98px)";
export const PHONE_QUERY = "(max-width: 599.98px)";

const WAITING_LABELS = {
  review: { key: "runs.waiting.review_label", fallback: "Waiting for your review" },
  approval_pending: { key: "runs.waiting.approval_pending", fallback: "Waiting for your approval" },
  budget_exhausted: { key: "runs.waiting.budget_exhausted", fallback: "Budget exhausted" },
  tool_approval: { key: "runs.waiting.tool_approval", fallback: "Waiting for tool approval" },
  execute_step: { key: "runs.waiting.execute_step", fallback: "Manual step pending" },
  research: { key: "runs.waiting.research", fallback: "External research pending" },
};

export function waitingReasonLabel(waiting, translate = null) {
  if (!waiting || typeof waiting !== "object") return "";
  const reason = typeof waiting.reason === "string" ? waiting.reason : "";
  const entry = WAITING_LABELS[reason];
  const key = entry ? entry.key : "runs.waiting.human_action";
  const fallback = entry ? entry.fallback : "Waiting for a human action";
  const base = translate ? translate(key) : fallback;
  const stepId = typeof waiting.step_id === "string" ? waiting.step_id.trim() : "";
  return stepId ? (translate ? translate("runs.waiting.step_suffix", { label: base, step_id: stepId }) : base + " · step “" + stepId + "”") : base;
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

export const DEFAULT_RUN_TAB = "steps";

export const RUN_DETAIL_TABS = [
  { id: "steps", label: "runs.tab.steps" },
  { id: "results", label: "runs.tab.results" },
  { id: "details", label: "runs.tab.details" },
];

export const RUN_TAB_SECTIONS = {
  steps: ["steps", "counters"],
  results: ["results"],
  details: ["history", "inputs", "technical"],
};

export function resolveRunTab(tabId) {
  return RUN_DETAIL_TABS.some((tab) => tab.id === tabId) ? tabId : DEFAULT_RUN_TAB;
}
