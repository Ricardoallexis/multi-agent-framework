// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Nuevo proyecto» (PRY-T06): the pure rules of the conversation that ends in a project. No DOM, no Preact and no network, so
// Node tests run them. It builds the bodies of the three routes (/gui/api/projects/draft, /plan and /create), reads their answers
// into what the card shows, and gives every text as plain text (the card draws text nodes, never markup). The model only drafts:
// nothing is created unless the person presses «Crear proyecto», with the huella (plan_sha256) of the preview they saw.
import { t } from "../../js/i18n.js";

export const DRAFT_PATH = "/gui/api/projects/draft";
export const CREATE_PATH = "/gui/api/projects/create";
export const APPS_HREF = "#apps";
export const APPROVALS_HREF = "#approvals";
export const TEXT_MAX = 2000;
export const CORRECTION_MAX = 1000;

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);

// The body of POST /projects/draft. A correction only goes with the previous draft; the confirmation of a remote model and the
// approval of a secret are what the person did in the chat for this very text.
// `cloudModel` is the model the person confirmed in this box: the server only honours the confirmation for that very model.
export function draftBody(request, { previous = null, correction = "", confirmCloud = false, cloudModel = "", approvalId = "" } = {}) {
  const body = { text: text(request).trim() };
  const fix = text(correction).trim();
  if (previous && fix) { body.previous = previous; body.correction = fix; }
  if (confirmCloud) {
    body.confirm_cloud = true;
    if (cloudModel) body.cloud_model = cloudModel;
  }
  if (approvalId) body.approval_id = approvalId;
  return body;
}

export const canDraft = (request) => {
  const clean = text(request).trim();
  return clean.length > 0 && clean.length <= TEXT_MAX;
};

export const canRedraft = (correction) => {
  const clean = text(correction).trim();
  return clean.length > 0 && clean.length <= CORRECTION_MAX;
};

// What kind of answer the server gave: "draft" (the card), or its reason (cloud_confirmation_required, secret_review_required,
// disabled, budget_cut, refused, limit_reached, invalid_output, model_error). Anything else counts as model_error.
export function answerKind(answer) {
  if (!answer || typeof answer !== "object") return "model_error";
  if (!answer.reason && answer.draft && typeof answer.draft === "object" && list(answer.draft.stages).length > 0) return "draft";
  return text(answer.reason) || "model_error";
}

const REASON_KEYS = {
  disabled: "ext.project.draft.reason.disabled",
  secret_review_required: "ext.project.draft.reason.secret_review_required",
  budget_cut: "ext.project.draft.reason.budget_cut",
  refused: "ext.project.draft.reason.refused",
  limit_reached: "ext.project.draft.reason.limit_reached",
  invalid_output: "ext.project.draft.reason.invalid_output",
  model_error: "ext.project.draft.reason.model_error",
};

// The sentence for an answer that has no draft; where to go next is a link the view adds (apps, or approvals for a secret).
export function reasonText(answer) {
  return t(REASON_KEYS[answerKind(answer)] || "ext.project.draft.reason.default");
}

export function reasonLink(answer) {
  const kind = answerKind(answer);
  if (kind === "secret_review_required") return { href: APPROVALS_HREF, label: t("ext.project.draft.approvals_link") };
  if (kind === "disabled" || kind === "limit_reached") return { href: APPS_HREF, label: t("ext.project.draft.apps_link") };
  return null;
}

// The notice before a remote model gets the text: which model, that the text leaves this computer and that the project is
// private. `local` lists the local models of the catalog, which the person can choose instead (nothing changes by itself).
export function cloudNotice(answer) {
  const model = text(answer && answer.model);
  const local = list(answer && answer.local_models).map(text).filter(Boolean);
  return {
    model,
    text: t("ext.project.draft.cloud_text", { model }),
    privateText: t("ext.project.draft.private_note"),
    localText: local.length ? t("ext.project.draft.cloud_local", { models: local.join(", ") }) : "",
    sendLabel: t("ext.project.draft.cloud_send", { model }),
  };
}

const problemText = (problem) => {
  if (problem && problem.reason === "project_exists") return t("ext.project.draft.problem.project_exists");
  return text(problem && problem.message) || t("ext.project.draft.problem.default");
};

// What the card shows for a draft answer. `canCreate` is true only when projects.plan verified the draft (it gave a huella)
// and there is nothing it could not verify.
export function cardView(answer) {
  if (answerKind(answer) !== "draft") return null;
  const check = answer.check && typeof answer.check === "object" ? answer.check : {};
  const plan = check.ok && check.plan && typeof check.plan === "object" ? check.plan : null;
  const problems = list(check.problems).map(problemText);
  const stages = list(answer.draft.stages).map((stage, index) => ({
    number: index + 1,
    title: text(stage.title),
    tasks: list(stage.tasks).map((task) => ({ title: text(task.title), objective: text(task.objective) })),
  }));
  const planSha256 = plan && /^[0-9a-f]{64}$/.test(text(plan.plan_sha256)) ? plan.plan_sha256 : "";
  const remote = answer.tier === "cloud";
  return {
    title: text(answer.draft.title),
    stages,
    counts: { stages: stages.length, tasks: stages.reduce((sum, stage) => sum + stage.tasks.length, 0) },
    problems,
    planSha256,
    canCreate: Boolean(planSha256) && problems.length === 0,
    model: text(answer.model),
    modelText: t("ext.project.draft.card_model", {
      model: text(answer.model), where: t(remote ? "ext.project.draft.where_remote" : "ext.project.draft.where_local"),
    }),
    noExecutor: answer.executors_active === false,
  };
}

// The body of POST /projects/create: the draft and the huella of the preview the person confirmed; null if there is nothing
// the person could confirm (the button is off then, and this keeps it from sending).
export function createBody(answer) {
  const view = cardView(answer);
  if (!view || !view.canCreate) return null;
  return { draft: answer.draft, plan_sha256: view.planSha256 };
}

// The sentence for a failed create: a changed draft or a taken name has its own words, the rest is the server's message.
export function createFailure(error) {
  const reason = error && error.details && error.details.reason;
  if (reason === "plan_changed") return t("ext.project.draft.error.plan_changed");
  if (reason === "project_exists") return t("ext.project.draft.problem.project_exists");
  return text(error && error.message) || t("ext.project.draft.error.create");
}
