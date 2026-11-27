// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Prepare the export with the assistant» (OFA-T05): the pure rules of the conversation that ends in an office package. No DOM,
// no Preact and no network, so Node tests run them. It builds the bodies of the three routes under /gui/api/offices/{id}/export
// (draft, preview, download), reads their answers into what the card shows, and gives every text as plain text (the card draws
// text nodes, never markup). The model only advises: nothing is downloaded unless the person presses «Download the package».
// Every answer is read by its `reason` first: the pauses of the guards (a remote model, a secret, the budget, a limit) carry
// only a reason and a few fields, never a draft.
import { limitReachedText } from "../../js/new-session-model.js";
import { t } from "../../js/i18n.js";
import { errorText } from "./import-model.js";

export const TEXT_MAX = 2000;
export const APPS_HREF = "#apps";
export const APPROVALS_HREF = "#approvals";
// The wording the model reads; it is not shown to the person, so it stays in one language.
const CORRECTION_LABEL = "Correction:";

export const exportPath = (officeId, step) => "/gui/api/offices/" + encodeURIComponent(officeId) + "/export/" + step;

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);
const strings = (value) => list(value).filter((item) => typeof item === "string");

// «Include code» belongs to the advanced mode only (the server refuses it in simple mode too).
export const includeCodeAllowed = (mode) => mode === "advanced";

// The sentence the model gets after «Change something»: the first request and the correction, in order, in one text.
export function composeText(request, correction = "") {
  const base = text(request).trim();
  const fix = text(correction).trim();
  return fix ? base + "\n\n" + CORRECTION_LABEL + " " + fix : base;
}

export const canDraft = (value) => {
  const clean = text(value).trim();
  return clean.length > 0 && clean.length <= TEXT_MAX;
};

// In-memory store of a request that waits for the person's decision on a secret (SEC-T04 pattern of new-session.js): the
// person leaves for #approvals, the view is unmounted, and coming back must find the request and its approval_id again.
// One row per office; nothing is written to the browser, so a reload starts clean.
export function createHeldStore() {
  const rows = new Map();
  return {
    get: (officeId) => rows.get(officeId) || null,
    set(officeId, state) {
      rows.set(officeId, { request: text(state && state.request), approvalId: text(state && state.approvalId),
        confirmedModel: text(state && state.confirmedModel) });
    },
    clear: (officeId) => { rows.delete(officeId); },
  };
}

// A correction that cannot be sent only because request and correction together pass the limit.
export const correctionTooLong = (request, correction) => text(correction).trim().length > 0 && !canRedraft(request, correction);

export const canRedraft = (request, correction) => text(correction).trim().length > 0 && canDraft(composeText(request, correction));

// The body of POST …/export/draft. `confirmCloud` and `approvalId` are what the person did for this very text.
export function draftBody(value, { includeCode = false, confirmCloud = false, approvalId = "" } = {}) {
  const body = { text: text(value).trim() };
  if (includeCode === true) body.include_code = true;
  if (confirmCloud === true) body.confirm_cloud = true;
  if (approvalId) body.approval_id = approvalId;
  return body;
}

// The body of POST …/export/preview and …/download: the version the draft saw and what the person leaves out. The model's
// «suggested_omit» is the starting point; the server rejects any path that is not in the portable list.
export function choiceBody(answer, { includeCode = false, acceptCodeRisk = false } = {}) {
  const body = {
    version: Number.isInteger(answer && answer.version) ? answer.version : 0,
    include_code: includeCode === true,
    omit: strings(answer && answer.suggested_omit),
  };
  if (includeCode === true && acceptCodeRisk === true) body.accept_code_risk = true;
  return body;
}

export const PAUSE_REASONS = Object.freeze([
  "cloud_confirmation_required", "secret_review_required", "budget_cut", "refused", "limit_reached", "cloud_not_allowed",
]);

// What kind of answer the server gave: "card" (a draft with a version), or its reason. A card may carry a reason too
// (office_not_normal, cloud_not_allowed or model_unavailable: the summary was prepared without a model); it is still a card.
export function answerKind(answer) {
  if (!answer || typeof answer !== "object") return "model_error";
  if (Number.isInteger(answer.version) && answer.version > 0 && typeof answer.summary === "string") return "card";
  return text(answer.reason) || "model_error";
}

const REASON_KEYS = {
  cloud_not_allowed: "ext.offices.xa.why.cloud_not_allowed",
  secret_review_required: "ext.offices.xa.reason.secret_review_required",
  budget_cut: "ext.offices.xa.reason.budget_cut",
  refused: "ext.offices.xa.reason.refused",
};

// The sentence, the link and the data for an answer that has no draft. A limit never switches the model by itself.
export function pauseView(answer) {
  const kind = answerKind(answer);
  if (kind === "card") return null;
  const view = { kind, text: "", link: null, approvalId: "", cloud: null };
  if (kind === "cloud_confirmation_required") {
    const model = text(answer.model);
    const receives = strings(answer.receives);
    view.cloud = {
      model,
      title: t("ext.offices.xa.cloud_title"),
      text: t("ext.offices.xa.cloud_text", { model }),
      costText: t("ext.offices.xa.cloud_cost"),
      filesText: t("ext.offices.xa.cloud_files", { count: receives.length }),
      receives,
      sendLabel: t("ext.offices.xa.cloud_send", { model }),
    };
    view.text = view.cloud.text;
  } else if (kind === "limit_reached") {
    view.text = limitReachedText(answer.limit) + ". " + t("ext.offices.xa.reason.limit_reached");
    view.link = { href: APPS_HREF, label: t("ext.offices.xa.apps_link") };
  } else if (REASON_KEYS[kind]) {
    view.text = t(REASON_KEYS[kind]);
    if (kind === "secret_review_required") {
      view.approvalId = text(answer.approval_id);
      view.link = { href: APPROVALS_HREF, label: t("ext.offices.xa.approvals_link") };
    }
  } else {
    view.text = t("ext.offices.xa.reason.default");
  }
  return view;
}

const FALLBACK_KEYS = {
  office_not_normal: "ext.offices.xa.why.office_not_normal",
  cloud_not_allowed: "ext.offices.xa.why.cloud_not_allowed",
  model_unavailable: "ext.offices.xa.why.model_unavailable",
};

const findingView = (item) => {
  const path = text(item && item.path);
  const line = Number.isInteger(item && item.line) && item.line > 0 ? item.line : 1;
  const type = text(item && item.type);
  return { path, line, type, text: t("ext.offices.xa.finding_at", { path, line, type }) };
};

// Where each finding is. A path is shown only if it is a relative one inside the package.
const safePath = (path) => typeof path === "string" && path !== "" && !/^([a-zA-Z]:|[\\/])/.test(path) && !/(^|[\\/])\.\.([\\/]|$)/.test(path);
const findingsOf = (value) => list(value).filter((item) => item && safePath(item.path)).map(findingView);

// What the card shows for a draft answer and, once the server checked the choice, its preview. The simple mode shows the
// data the person decides on; the advanced mode adds the paths never packaged, the unchecked paths and every finding.
export function cardView(answer, preview = null, { advanced = false } = {}) {
  if (answerKind(answer) !== "card") return null;
  const omitted = preview ? strings(preview.omitted) : strings(answer.suggested_omit);
  const included = preview ? strings(preview.included) : strings(answer.included).filter((path) => !omitted.includes(path));
  const findings = findingsOf(preview ? preview.findings : answer.findings);
  const unverified = preview ? strings(preview.unverified) : strings(answer.unverified);
  const blocked = preview ? preview.blocked === true : findings.length > 0 || unverified.length > 0;
  const model = answer.source === "model";
  return {
    version: answer.version,
    summary: text(answer.summary),
    sourceText: t(model ? "ext.offices.xa.source_model" : "ext.offices.xa.source_fixed"),
    whyText: FALLBACK_KEYS[answer.reason] ? t(FALLBACK_KEYS[answer.reason]) : "",
    included,
    omitted,
    excluded: advanced ? strings(answer.excluded) : [],
    unverified: advanced ? unverified : [],
    unverifiedCount: unverified.length,
    findings: advanced ? findings : [],
    findingsCount: findings.length,
    questions: strings(answer.questions),
    localModels: strings(answer.local_models),
    countsText: t("ext.offices.xa.counts", { included: included.length, left: omitted.length }),
    blocked,
    checked: preview !== null,
    canDownload: preview !== null && !blocked && included.length > 0,
    noticeText: t("ext.offices.xa.note"),
  };
}

// The fixed note, always visible in the assistant: the detectors look for known patterns and promise nothing more.
export const fixedNote = () => t("ext.offices.xa.note");

// The sentence for a failed call, by its reason first. `lines` says where each finding is (there is no file then), and
// `stale` tells the view to forget the draft: the office changed after it, so the request must be written again.
export function failureView(error) {
  const details = error && error.details && typeof error.details === "object" ? error.details : {};
  const reason = text(details.reason);
  const view = { text: "", lines: [], stale: false };
  if (reason === "office_export_stale") {
    view.text = t("ext.offices.xa.error.stale");
    view.stale = true;
  } else if (reason === "office_export_check_failed") {
    view.text = t("ext.offices.xa.error.check_failed");
    view.lines = findingsOf(details.found).map((item) => item.text)
      .concat(strings(details.unverified).filter(safePath).map((path) => t("ext.offices.xa.unchecked_at", { path })));
  } else if (reason === "office_export_invalid_proposal") {
    view.text = t("ext.offices.xa.error.invalid_proposal");
  } else if (reason === "office_export_code_risk_required") {
    view.text = t("ext.offices.xa.error.code_risk");
  } else if (reason === "export_path_not_portable") {
    view.text = t("ext.offices.xa.error.not_portable");
  } else if (reason === "advanced_mode_required") {
    view.text = errorText(reason, details);
  } else if (details.field === "text" || list(details.errors).some((item) => item && list(item.loc).includes("text"))) {
    view.text = t("ext.offices.xa.error.text");
  } else {
    view.text = text(error && error.message) || t("ext.offices.xa.reason.default");
  }
  return view;
}
