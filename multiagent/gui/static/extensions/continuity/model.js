// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import "./i18n.js";
import { currentLanguage, t } from "../../js/i18n.js";
// Pure helpers of the continuity screens (CNT-T06): the words of the decision card, the local time with its source, the body
// of a decision and the rows of the policy. No DOM and no Preact in here, so the tests run it under Node.
// The data is what GET /gui/api/continuity/{decisions,session,policy} answers (multiagent/gui/continuity_api.py). Every text
// that comes from the server (a task title, an executor name, a model) is shown as plain text and never as markup.

const SOURCE_KEYS = {
  app_epoch: "ext.continuity.source.app_epoch",
  app_text: "ext.continuity.source.app_text",
  rule: "ext.continuity.source.rule",
  configured: "ext.continuity.source.configured",
};
const WINDOW_KEYS = {
  five_hour: "ext.continuity.window.five_hour",
  weekly: "ext.continuity.window.weekly",
  model_weekly: "ext.continuity.window.model_weekly",
  monthly: "ext.continuity.window.monthly",
  spend: "ext.continuity.window.spend",
  unknown: "ext.continuity.window.unknown",
};
const PATCH_REASON_KEYS = {
  not_git: "ext.continuity.patch.not_git",
  empty: "ext.continuity.patch.empty",
  too_large: "ext.continuity.patch.too_large",
  git_error: "ext.continuity.patch.git_error",
};
const MODE_KEYS = {
  manual: ["ext.continuity.mode.manual", "ext.continuity.mode.manual.explanation"],
  semi_auto: ["ext.continuity.mode.semi_auto", "ext.continuity.mode.semi_auto.explanation"],
  auto: ["ext.continuity.mode.auto", "ext.continuity.mode.auto.explanation"],
};
const POLICY_KEYS = {
  no_ask: "ext.continuity.policy.no_ask",
  ask: "ext.continuity.policy.ask",
  never: "ext.continuity.policy.never",
};

const isDate = (date) => date instanceof Date && !Number.isNaN(date.getTime());
export function toDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const date = new Date(value);
  return isDate(date) ? date : null;
}

const hhmm = (date, locale) => new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
const dayText = (date, locale) => new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short" }).format(date).replace(",", "");
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

// "16:10" today, "mañana 03:40" the next day, "jue 5 nov 03:40" later; null when the value is not a time.
export function clockText(value, now = new Date(), locale = currentLanguage()) {
  const date = toDate(value);
  if (!date) return null;
  const time = hhmm(date, locale);
  if (sameDay(date, now)) return time;
  const tomorrow = new Date(now.getTime());
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (sameDay(date, tomorrow)) return t("ext.continuity.clock.tomorrow", { time });
  return dayText(date, locale) + " " + time;
}

// «a las 16:10», «mañana a las 03:40» or «el jue 5 nov a las 03:40»; null when the value is not a time.
export function whenText(value, now = new Date(), locale = currentLanguage()) {
  const date = toDate(value);
  if (!date) return null;
  const time = hhmm(date, locale);
  if (sameDay(date, now)) return t("ext.continuity.when.today", { time });
  const tomorrow = new Date(now.getTime());
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (sameDay(date, tomorrow)) return t("ext.continuity.when.tomorrow", { time });
  return t("ext.continuity.when.later", { day: dayText(date, locale), time });
}

// The time the card shows as the reset: what the app said, else the moment the wait ends (a short retry has no reset).
export const shownReset = (card) => (card && card.reset ? card.reset.at || card.reset.resume_after || null : null);
export const sourceText = (source) => SOURCE_KEYS[source] ? t(SOURCE_KEYS[source]) : t("ext.continuity.source.unknown");
export const isEstimate = (card) => Boolean(card && card.reset && ["configured", "rule"].includes(card.reset.source));

const who = (card) => (card.model ? card.app + " · " + card.model : card.app);

// «Claude · modelo alcanzó su límite de sesión (5 horas)», or the wording of the other classes.
export function limitText(card) {
  const name = who(card);
  if (card.reason === "credits_exhausted") return t("ext.continuity.limit.credits", { app: name });
  if (card.reason === "rate_limited") return t("ext.continuity.limit.rate", { app: name });
  if (card.reason === "limit_unknown") return t("ext.continuity.limit.unknown", { app: name });
  const window = WINDOW_KEYS[card.window] ? t(WINDOW_KEYS[card.window]) : t(WINDOW_KEYS.unknown);
  return t("ext.continuity.limit.window", { app: name, window });
}

// «Se restablece a las 16:10 (hora local · fuente: aviso de la app).» or «Restablecimiento no disponible…».
export function resetText(card, now = new Date()) {
  const reset = card.reset || {};
  const when = whenText(shownReset(card), now);
  if (reset.open_ended) {
    return reset.at && when
      ? t("ext.continuity.reset.open_ended_with_time", { when })
      : t("ext.continuity.reset.open_ended");
  }
  if (!when) return t("ext.continuity.reset.unavailable");
  if (card.reason === "rate_limited" && !reset.at) return t("ext.continuity.reset.retry", { when });
  return t("ext.continuity.reset.regular", {
    when,
    source: sourceText(reset.source),
    estimate: isEstimate(card) ? t("ext.continuity.reset.estimate") : "",
  });
}

// «Avance guardado: parche de 3 archivos.» / «Avance guardado sin parche (…).» / «Sin avance guardado.»
export function checkpointText(card) {
  const saved = card.checkpoint || {};
  if (saved.patch) {
    const files = saved.files_total;
    return t("ext.continuity.checkpoint.patch", {
      files: Number.isInteger(files) && files > 0
        ? files === 1 ? t("ext.continuity.checkpoint.one_file", { count: files }) : t("ext.continuity.checkpoint.many_files", { count: files })
        : "",
    });
  }
  if (saved.saved) return t("ext.continuity.checkpoint.no_patch", {
    reason: PATCH_REASON_KEYS[saved.reason] ? t(PATCH_REASON_KEYS[saved.reason]) : "",
  });
  return t("ext.continuity.checkpoint.none");
}

export function handoffText(card) {
  const handoffs = card.handoffs || { used: 0, max: 0 };
  return t("ext.continuity.handoffs", { used: handoffs.used, max: handoffs.max });
}

export const taskText = (card) => t("ext.continuity.task", {
  id: card.task_id,
  title: card.title && card.title !== card.task_id ? " · " + card.title : "",
});

// The label of «Esperar»: «Esperar hasta 16:10» (the time of the reset). null when there is no time to wait for.
export function waitLabel(card, now = new Date()) {
  const clock = card.can_wait ? clockText(shownReset(card), now) : null;
  return clock ? t("ext.continuity.wait_until", { time: clock }) : null;
}

// «Delegar a Codex · gpt-6.1 · T3» (the model and the level only when the server knows them).
export function candidateLabel(candidate) {
  return [t("ext.continuity.delegate_to", { app: candidate.app }), candidate.model, candidate.level].filter(Boolean).join(" · ");
}

export function candidateNote(candidate, now = new Date()) {
  const parts = [t("ext.continuity.candidate.sends", { sends: candidate.sends })];
  if (!candidate.model) parts.push(t("ext.continuity.candidate.model_on_handoff"));
  if (candidate.policy === "ask") parts.push(t("ext.continuity.candidate.ask"));
  if (!candidate.available) {
    const until = clockText(candidate.paused_until, now);
    parts.push(until ? t("ext.continuity.candidate.paused_until", { time: until }) : t("ext.continuity.candidate.unavailable"));
  }
  return parts.join(" ");
}

export const historyNote = () => t("ext.continuity.history_note");

// What the card says once a decision is recorded. null while it is pending.
export function decisionText(card, now = new Date()) {
  const decided = clockText(card.decided_at, now);
  if (card.decision === "wait") {
    const clock = clockText(shownReset(card), now);
    return t("ext.continuity.decision.wait", { time: clock ? t("ext.continuity.until_time", { time: clock }) : "" });
  }
  if (card.decision === "delegate") {
    const found = (card.candidates || []).find((item) => item.executor_id === card.candidate);
    const target = found ? found.app : card.candidate || t("ext.continuity.other_agent");
    return t("ext.continuity.decision.delegate", { target, time: decided ? " (" + decided + ")" : "" });
  }
  return null;
}

export function blockedText(card) {
  if (card.blocked === "failed_resumes") return t("ext.continuity.blocked.failed_resumes");
  if (card.blocked === "condition_expired") return t("ext.continuity.blocked.condition_expired");
  return null;
}

// Everything the card draws, as plain strings.
export function describeCard(card, now = new Date()) {
  const reset = shownReset(card);
  return {
    title: limitText(card),
    task: taskText(card),
    reset: resetText(card, now),
    resetIso: toDate(reset) ? toDate(reset).toISOString() : null,
    resetClock: clockText(reset, now),
    checkpoint: checkpointText(card),
    handoffs: handoffText(card),
    decision: decisionText(card, now),
    blocked: blockedText(card),
    wait: waitLabel(card, now),
    candidates: (card.candidates || []).map((candidate) => ({
      id: candidate.executor_id, label: candidateLabel(candidate), note: candidateNote(candidate, now), available: candidate.available,
    })),
    exhausted: Boolean(card.handoffs_exhausted),
  };
}

// «En espera · hasta 16:10» for the agent tree (the same words as js/agent-tree-model.js).
export function treeText(card, now = new Date()) {
  const clock = clockText(shownReset(card), now);
  return clock ? t("ext.continuity.tree.waiting_until", { time: clock }) : t("ext.continuity.tree.waiting_unknown");
}

export function newDecisionId() {
  const crypto = globalThis.crypto;
  if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "d-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
}

export const decisionPath = (taskId) => "/gui/api/tasks/" + encodeURIComponent(taskId) + "/continuity/decision";

export function decisionBody(card, decision, executorId, decisionId) {
  const body = { decision_id: decisionId, decision, expected_revision: card.revision };
  if (decision === "delegate") body.executor_id = executorId;
  return body;
}

// The text of the confirmation box of one choice: what is going to be saved, and what will not happen.
export function confirmText(card, choice, now = new Date()) {
  if (choice.kind === "wait") {
    const clock = clockText(shownReset(card), now);
    return {
      title: t("ext.continuity.confirm.wait_title", { time: clock || t("ext.continuity.time_indicated") }),
      lines: [t("ext.continuity.confirm.wait_line"), t("ext.continuity.confirm.no_send")],
      yes: t("ext.continuity.confirm.wait_yes"),
    };
  }
  const candidate = (card.candidates || []).find((item) => item.executor_id === choice.executor_id);
  const target = candidate ? [candidate.app, candidate.model, candidate.level].filter(Boolean).join(" · ") : t("ext.continuity.other_agent");
  return {
    title: t("ext.continuity.confirm.delegate_title", { target }),
    lines: [
      candidate ? t("ext.continuity.confirm.sends", { sends: candidate.sends }) : t("ext.continuity.confirm.envelope"),
      historyNote(),
      t("ext.continuity.confirm.delegate_line"),
    ],
    yes: t("ext.continuity.confirm.delegate_yes"),
  };
}

// --- the policy (section «Continuidad» of Uso) --------------------------------------------------------------------------

export function pausedText(candidate, now = new Date()) {
  if (candidate.enabled === false) return t("ext.continuity.state.disabled");
  if (candidate.paused_until) return t("ext.continuity.state.paused_until", {
    time: clockText(candidate.paused_until, now) || t("ext.continuity.unknown_time"),
  });
  if (candidate.paused_open_ended) return t("ext.continuity.state.paused_open_ended");
  return candidate.available === false ? t("ext.continuity.state.unavailable") : t("ext.continuity.state.available");
}

// USO-T08: the switch «continue by itself». It is the person's: the form sends `automatic` only when the switch changed.
export const automaticOn = (data) => Boolean(data) && data.automatic === true;
// The saved mode (manual or semi-automatic) behind the switch; with it on, `data.mode` says "auto" and the form must not take that.
export const savedMode = (data) => (data && ["manual", "semi_auto"].includes(data.saved_mode) ? data.saved_mode : data && data.mode);
export const automaticChanged = (wanted, data) => Boolean(wanted) !== automaticOn(data);
export const automaticPayload = (base, wanted, data) => (automaticChanged(wanted, data) ? { ...base, automatic: Boolean(wanted) } : base);
export function automaticStateText(data, now = new Date()) {
  if (!automaticOn(data)) return t("ext.continuity.settings.automatic_off");
  const when = clockText(data.automatic_since, now);
  return when ? t("ext.continuity.settings.automatic_on", { when }) : t("ext.continuity.settings.automatic_on_unknown");
}

// «Codex reached its weekly limit. Claude (Sonnet 5.5) continued without asking, by automatic continuity, at 12:40.» The notice of the
// last hand-off of a session (GET /gui/api/continuity/session/<id>, `last_handoff`); empty text when the answer has none.
export function handoffNoticeText(notice, now = new Date()) {
  if (!notice || !notice.limit || !notice.to) return "";
  const from = notice.limit;
  const limit = limitText({ app: from.app || from.executor_id || "", model: from.model, reason: from.reason, window: from.window });
  const name = notice.to.app || notice.to.executor_id || "";
  const to = notice.to.model ? name + " (" + notice.to.model + ")" : name;
  const when = whenText(notice.at, now) || t("ext.continuity.unknown_time");
  return limit + " " + t(notice.automatic ? "ext.continuity.handoff_notice.auto" : "ext.continuity.handoff_notice.person", { to, when });
}

export function policyRows(policy) {
  const modeKeys = MODE_KEYS[policy.mode];
  const mode = modeKeys
    ? { label: t(modeKeys[0]), explanation: t(modeKeys[1]) }
    : { label: String(policy.mode || t("ext.continuity.no_data")), explanation: "" };
  const hours = policy.wait_threshold_hours;
  return [
    [t("ext.continuity.policy.mode"), mode.label + (mode.explanation ? ". " + mode.explanation : "")],
    [t("ext.continuity.policy.threshold"), Number.isFinite(hours)
      ? t(hours === 1 ? "ext.continuity.policy.one_hour" : "ext.continuity.policy.many_hours", { hours })
      : t("ext.continuity.no_data")],
    [t("ext.continuity.policy.max_handoffs"), Number.isFinite(policy.max_handoffs)
      ? t("ext.continuity.policy.handoffs_per_task", { count: policy.max_handoffs })
      : t("ext.continuity.no_data")],
    [t("ext.continuity.policy.during_integration"), t("ext.continuity.policy.never_during_integration")],
    [t("ext.continuity.policy.auto_mode"), policy.automatic === true
      ? automaticStateText({ automatic: true, automatic_since: policy.automatic_since })
      : policy.auto_scope === "project" || policy.automatic === false
        ? t("ext.continuity.policy.auto_project_only")
        : t("ext.continuity.no_data")],
  ];
}

export function policyCandidates(policy, now = new Date()) {
  return (policy.candidates || []).map((candidate) => ({
    id: candidate.executor_id, app: candidate.app,
    policy: POLICY_KEYS[candidate.policy] ? t(POLICY_KEYS[candidate.policy]) : t("ext.continuity.no_data"),
    state: pausedText(candidate, now),
  }));
}

// What a failed decision says. The reasons are those of POST /gui/api/tasks/<id>/continuity/decision.
export const ERROR_TEXT = {
  stale_revision: "ext.continuity.error.stale_revision",
  not_held: "ext.continuity.error.not_held",
  no_reset_time: "ext.continuity.error.no_reset_time",
  candidate_not_available: "ext.continuity.error.candidate_not_available",
  handoffs_exhausted: "ext.continuity.error.handoffs_exhausted",
  blocked: "ext.continuity.error.blocked",
  condition_hold: "ext.continuity.error.condition_hold",
  decision_id_reused: "ext.continuity.error.decision_id_reused",
};

export function decisionErrorText(error) {
  if (!error) return t("ext.continuity.error.save_failed") + ".";
  if (error.code === "network_error") return t("ext.continuity.error.network");
  if (error.code === "unauthorized" || error.status === 401) return t("ext.continuity.error.unauthorized");
  const reason = error.details && error.details.reason;
  if (ERROR_TEXT[reason]) return t(ERROR_TEXT[reason]);
  if (error.status === 404) return t("ext.continuity.error.not_found");
  return t("ext.continuity.error.save_failed") + (error.message ? ": " + error.message : ".");
}

// Errors after which the card is out of date and the page reads the list again.
export const isStale = (error) => Boolean(error) && (error.status === 404 || error.status === 409);
