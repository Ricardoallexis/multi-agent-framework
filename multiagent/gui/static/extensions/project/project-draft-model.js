// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Nuevo proyecto» (PRY-T06): the pure rules of the conversation that ends in a project. No DOM, no Preact and no network, so
// Node tests run them. It builds the bodies of the three routes (/gui/api/projects/draft, /plan and /create), reads their answers
// into what the card shows, and gives every text as plain text (the card draws text nodes, never markup). The model only drafts:
// nothing is created unless the person presses «Crear proyecto», with the huella (plan_sha256) of the preview they saw.
// PRY-T12 «Add a stage»: the same rules for one stage of the project that is open (mode "stage"). The project id travels only in
// the path of the three /stages routes; nothing the person writes or the model answers chooses it. Only «Add this stage» writes.
import { t } from "../../js/i18n.js";

export const DRAFT_PATH = "/gui/api/projects/draft";
export const CREATE_PATH = "/gui/api/projects/create";
export const APPS_HREF = "#apps";
export const APPROVALS_HREF = "#approvals";
export const TEXT_MAX = 2000;
export const CORRECTION_MAX = 1000;
export const STAGE = "stage";
const isStage = (mode) => mode === STAGE;
export const stagePath = (projectId, action) => "/gui/api/projects/" + encodeURIComponent(projectId) + "/stages/" + action;

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);

// The body of POST /projects/draft. A correction only goes with the previous draft; the confirmation of a remote model and the
// approval of a secret are what the person did in the chat for this very text.
// `cloudModel` is the model the person confirmed in this box: the server only honours the confirmation for that very model.
// `expectedRevision` is the revision of the open project the draft is checked against (stage mode only).
export function draftBody(request, { previous = null, correction = "", confirmCloud = false, cloudModel = "", approvalId = "", expectedRevision = 0 } = {}) {
  const body = { text: text(request).trim() };
  if (expectedRevision) body.expected_revision = expectedRevision;
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
// In stage mode the draft is one stage, {title, tasks}; otherwise a project, {title, stages}.
export function answerKind(answer, mode = "") {
  if (!answer || typeof answer !== "object") return "model_error";
  const parts = isStage(mode) ? list(answer.draft && answer.draft.tasks) : list(answer.draft && answer.draft.stages);
  if (!answer.reason && answer.draft && typeof answer.draft === "object" && parts.length > 0) return "draft";
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

// The three sentences that say «project» get their own words in stage mode; the rest is shared.
const STAGE_REASON_KEYS = {
  ...REASON_KEYS,
  disabled: "ext.project.stage.reason.disabled",
  invalid_output: "ext.project.stage.reason.invalid_output",
};

// The texts of the box that differ between a new project and a new stage of the open project.
const TEXTS = {
  project: {
    title: "ext.project.draft.title", back: "ext.project.draft.back", intro: "ext.project.draft.intro",
    text_label: "ext.project.draft.text_label", model_note: "ext.project.draft.model_note", send: "ext.project.draft.send",
    card_title: "ext.project.draft.card_title", not_created: "ext.project.draft.not_created", create: "ext.project.draft.create",
    no_executor: "ext.project.draft.no_executor",
  },
  stage: {
    title: "ext.project.stage.add", back: "ext.project.stage.back", intro: "ext.project.stage.intro",
    text_label: "ext.project.stage.text_label", model_note: "ext.project.stage.model_note", send: "ext.project.stage.send",
    card_title: "ext.project.stage.card_title", not_created: "ext.project.stage.not_created", create: "ext.project.stage.create",
    no_executor: "ext.project.stage.no_executor",
  },
};
export const say = (mode, name, data) => t(TEXTS[isStage(mode) ? "stage" : "project"][name], data);

// The sentence for an answer that has no draft; where to go next is a link the view adds (apps, or approvals for a secret).
export function reasonText(answer, mode = "") {
  const keys = isStage(mode) ? STAGE_REASON_KEYS : REASON_KEYS;
  return t(keys[answerKind(answer, mode)] || (isStage(mode) ? "ext.project.stage.reason.default" : "ext.project.draft.reason.default"));
}

export function reasonLink(answer, mode = "") {
  const kind = answerKind(answer, mode);
  if (kind === "secret_review_required") return { href: APPROVALS_HREF, label: t("ext.project.draft.approvals_link") };
  if (kind === "disabled" || kind === "limit_reached") return { href: APPS_HREF, label: t("ext.project.draft.apps_link") };
  return null;
}

// The notice before a remote model gets the text: which model, that the text leaves this computer and that the project is
// private. `local` lists the local models of the catalog, which the person can choose instead (nothing changes by itself).
// In stage mode it also says that the project title and the titles of its stages go with the request.
export function cloudNotice(answer, mode = "") {
  const model = text(answer && answer.model);
  const local = list(answer && answer.local_models).map(text).filter(Boolean);
  return {
    model,
    text: t("ext.project.draft.cloud_text", { model }),
    privateText: isStage(mode) ? "" : t("ext.project.draft.private_note"),
    contextText: isStage(mode) ? t("ext.project.stage.cloud_context") : "",
    localText: local.length ? t("ext.project.draft.cloud_local", { models: local.join(", ") }) : "",
    sendLabel: t("ext.project.draft.cloud_send", { model }),
  };
}

const problemText = (problem, mode = "") => {
  if (problem && problem.reason === "project_exists") return t("ext.project.draft.problem.project_exists");
  if (isStage(mode) && problem && problem.reason === "id_collision") return t("ext.project.stage.error.id_collision");
  if (isStage(mode) && problem && problem.reason === "invalid_state") return t("ext.project.stage.error.changed");
  return text(problem && problem.message) || t("ext.project.draft.problem.default");
};

// What the card shows for a draft answer. `canCreate` is true only when projects.plan verified the draft (it gave a huella)
// and there is nothing it could not verify. In stage mode the card is one stage (`tasks`) and carries the revision it was checked against.
export function cardView(answer, mode = "") {
  if (answerKind(answer, mode) !== "draft") return null;
  const single = isStage(mode);
  const check = answer.check && typeof answer.check === "object" ? answer.check : {};
  const plan = check.ok && check.plan && typeof check.plan === "object" ? check.plan : null;
  const problems = list(check.problems).map((problem) => problemText(problem, mode));
  const stages = single ? [] : list(answer.draft.stages).map((stage, index) => ({
    number: index + 1,
    title: text(stage.title),
    tasks: list(stage.tasks).map((task) => ({ title: text(task.title), objective: text(task.objective) })),
  }));
  const tasks = single ? list(answer.draft.tasks).map((task) => ({ title: text(task.title), objective: text(task.objective) })) : [];
  const planSha256 = plan && /^[0-9a-f]{64}$/.test(text(plan.plan_sha256)) ? plan.plan_sha256 : "";
  const revision = Number.isSafeInteger(answer.expected_revision) && answer.expected_revision >= 1 ? answer.expected_revision : 0;
  const remote = answer.tier === "cloud";
  const counts = { stages: single ? 1 : stages.length, tasks: single ? tasks.length : stages.reduce((sum, stage) => sum + stage.tasks.length, 0) };
  return {
    title: text(answer.draft.title),
    stages,
    tasks,
    counts,
    countsText: single ? t("ext.project.stage.card_counts", counts) : t("ext.project.draft.card_counts", counts),
    unauthorizedText: single ? t("ext.project.stage.unauthorized") : "",
    revision,
    problems,
    planSha256,
    canCreate: Boolean(planSha256) && problems.length === 0 && (!single || revision > 0),
    model: text(answer.model),
    modelText: t("ext.project.draft.card_model", {
      model: text(answer.model), where: t(remote ? "ext.project.draft.where_remote" : "ext.project.draft.where_local"),
    }),
    noExecutor: answer.executors_active === false,
  };
}

// The body of POST /projects/create: the draft and the huella of the preview the person confirmed; null if there is nothing
// the person could confirm (the button is off then, and this keeps it from sending).
// In stage mode the body is {stage, expected_revision, plan_sha256}: the revision the preview was checked against.
export function createBody(answer, mode = "") {
  const view = cardView(answer, mode);
  if (!view || !view.canCreate) return null;
  if (isStage(mode)) return { stage: answer.draft, expected_revision: view.revision, plan_sha256: view.planSha256 };
  return { draft: answer.draft, plan_sha256: view.planSha256 };
}

// A 409 on creating a stage (the plan changed, the revision is old, an id is taken) wrote nothing and asks to draft again.
export const isConflict = (error) => Boolean(error && error.status === 409);

// The sentence for a failed create: a changed draft or a taken name has its own words, the rest is the server's message.
export function createFailure(error, mode = "") {
  const reason = error && error.details && error.details.reason;
  if (isStage(mode)) {
    if (reason === "id_collision") return t("ext.project.stage.error.id_collision");
    if (isConflict(error)) return t("ext.project.stage.error.changed");
    return text(error && error.message) || t("ext.project.stage.error.create");
  }
  if (reason === "plan_changed") return t("ext.project.draft.error.plan_changed");
  if (reason === "project_exists") return t("ext.project.draft.problem.project_exists");
  return text(error && error.message) || t("ext.project.draft.error.create");
}
