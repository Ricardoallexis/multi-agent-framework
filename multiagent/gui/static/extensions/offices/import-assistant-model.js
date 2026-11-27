// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Explain this office to me» and «Try in Mock» (OFA-T06): the pure rules of the import conversation. No DOM, no Preact and
// no network, so Node tests run them. It builds the bodies of the routes under /gui/api/offices/stage/{stage_id} (checks,
// explain, rehearse), reads their answers into what the card shows and gives every text as plain text: the card draws text
// nodes, never markup, so nothing that comes from the package or from a model can become HTML. The explanation only describes:
// it never changes a finding, the risk box or the one approval. The pauses of the guards (a remote model, a secret, the budget,
// a limit) are read by the same rules as the export assistant (export-assistant-model.js).
import { t } from "../../js/i18n.js";
import { TEXT_MAX, pauseView as exportPauseView } from "./export-assistant-model.js";
import { errorText } from "./import-model.js";

export { TEXT_MAX };

export const stagePath = (stageId, step) => "/gui/api/offices/stage/" + encodeURIComponent(stageId) + "/" + step;

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);
const strings = (value) => list(value).filter((item) => typeof item === "string");
const object = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

// A path is shown only if it is a relative one inside the package.
const safePath = (path) => typeof path === "string" && path !== "" && !/^([a-zA-Z]:|[\\/])/.test(path)
  && !/(^|[\\/])\.\.([\\/]|$)/.test(path);

// --- the question ------------------------------------------------------------------------------------------------------

// The question is optional: without one the server explains the package. With one it must fit the limit.
export const canAsk = (value) => text(value).trim().length <= TEXT_MAX;

// The body of POST …/explain. `confirmCloud` and `approvalId` are what the person did for this very question.
export function explainBody(question, { confirmCloud = false, approvalId = "" } = {}) {
  const body = {};
  const clean = text(question).trim();
  if (clean) body.question = clean;
  if (confirmCloud === true) body.confirm_cloud = true;
  if (approvalId) body.approval_id = approvalId;
  return body;
}

// The body of POST …/rehearse: every flow of the package (the server tries up to eight).
export const rehearseBody = () => ({});

// --- the answers of /explain -----------------------------------------------------------------------------------------

// "explanation": an answer with a read of the package (a model's or the fixed one); otherwise its reason (a pause).
export function answerKind(answer) {
  if (!answer || typeof answer !== "object") return "model_error";
  if (answer.explanation && typeof answer.explanation === "object" && typeof answer.stage_id === "string") return "explanation";
  return text(answer.reason) || "model_error";
}

const KNOWN_PAUSES = Object.freeze([
  "cloud_confirmation_required", "secret_review_required", "budget_cut", "refused", "limit_reached", "cloud_not_allowed",
]);

// The sentence, the link and the data for an answer that has no explanation. A limit never switches the model by itself.
export function pauseView(answer) {
  const kind = answerKind(answer);
  if (kind === "explanation") return null;
  const view = exportPauseView({ ...object(answer), version: 0 });
  view.kind = kind;
  if (!KNOWN_PAUSES.includes(kind)) {
    view.text = t("ext.offices.ia.reason.default");
    return view;
  }
  if (view.cloud) {
    view.cloud.text = t("ext.offices.ia.cloud_text");
    view.text = view.cloud.text;
  }
  if (kind === "secret_review_required") view.text = t("ext.offices.ia.reason.secret_review_required");
  return view;
}

const WHY_KEYS = {
  office_not_normal: "ext.offices.ia.why.office_not_normal",
  cloud_not_allowed: "ext.offices.xa.why.cloud_not_allowed",
  model_unavailable: "ext.offices.xa.why.model_unavailable",
};

const targetText = (item) => {
  const input = text(item && item.from_input);
  if (input) return t("ext.offices.ia.target_input", { name: input });
  return text(item && item.path) || t("ext.offices.ia.target_file");
};

const PERMISSION_KEYS = {
  none: "ext.offices.ia.perm.none",
  each_call: "ext.offices.ia.perm.each_call",
};

const where = (row) => [text(row && row.area), text(row && row.workflow)].filter(Boolean).join(" / ");

const findingView = (item) => {
  const path = text(item && item.path);
  const line = Number.isInteger(item && item.line) && item.line > 0 ? item.line : 1;
  return { path, line, text: t("ext.offices.xa.finding_at", { path, line, type: text(item && item.type) }) };
};

const UNVERIFIED_FLOW_KEYS = {
  function_step: "ext.offices.ia.unverified.function_step",
  prompt_not_in_package: "ext.offices.ia.unverified.prompt_not_in_package",
  workflow_unreadable: "ext.offices.ia.unverified.workflow_unreadable",
};

// What the conversation shows for the read of a package (the answer of /explain, or the plain report of /checks). The model's
// words (`explanation`) go next to the facts and never replace them. The simple mode shows what the person decides on; the
// advanced mode adds the tool identifiers, the code files and every finding.
export function reportView(report, { advanced = false } = {}) {
  if (!report || typeof report !== "object") return null;
  const explanation = object(report.explanation);
  const byArea = new Map(list(explanation.areas).map((item) => [text(item && item.area), text(item && item.text)]));
  const byTool = new Map(list(explanation.tools).map((item) => [text(item && item.tool), text(item && item.text)]));
  const model = report.source === "model";
  const areas = list(report.areas).map((area) => {
    const names = list(area.workflows).map((flow) => text(flow && flow.id)).filter(Boolean);
    return {
      id: text(area.id), name: text(area.name) || text(area.id),
      text: model ? byArea.get(text(area.id)) || "" : "",
      workflows: list(area.workflows).map((flow) => ({ id: text(flow && flow.id), description: text(flow && flow.description) })),
      workflowsText: names.length > 0 ? t("ext.offices.ia.workflows", { names: names.join(", ") }) : t("ext.offices.ia.no_workflows"),
    };
  });
  const tools = list(report.tools).map((row) => {
    const reads = list(row.reads).map(targetText);
    const writes = list(row.writes).map(targetText);
    return {
      key: [row.area, row.workflow, row.step, row.tool].map(text).join("|"),
      title: text(row.title) || text(row.tool),
      id: advanced ? text(row.tool) : "",
      whereText: t("ext.offices.ia.tool_where", { area: text(row.area), workflow: text(row.workflow), step: text(row.step) }),
      note: model ? byTool.get(text(row.tool)) || "" : "",
      readsText: reads.length > 0 ? t("ext.offices.ia.tool_reads", { list: reads.join(", ") }) : t("ext.offices.ia.tool_reads_none"),
      writesText: writes.length > 0 ? t("ext.offices.ia.tool_writes", { list: writes.join(", ") }) : t("ext.offices.ia.tool_writes_none"),
      permissionText: t(PERMISSION_KEYS[row.permission] || "ext.offices.ia.perm.unknown"),
      known: row.known !== false,
    };
  });
  const flowRows = (rows) => list(rows).filter((row) => row && strings(row.inputs).length > 0)
    .map((row) => ({ where: where(row), inputs: strings(row.inputs) }));
  const findings = list(report.findings).filter((item) => item && safePath(item.path)).map(findingView);
  const unverifiedFiles = strings(report.unverified).filter(safePath);
  const flowsUnverified = list(report.inputs).filter((row) => row && row.unverified === true)
    .map((row) => t(UNVERIFIED_FLOW_KEYS[row.reason] || "ext.offices.ia.unverified.default", { where: where(row) }));
  const codeFiles = strings(object(report.code).files);
  const needsRoot = list(report.needs_file_tools_root).map(where).filter(Boolean);
  return {
    stageId: text(report.stage_id),
    sourceText: model ? t("ext.offices.ia.source_model") : t("ext.offices.ia.fixed_note"),
    whyText: WHY_KEYS[report.reason] ? t(WHY_KEYS[report.reason]) : "",
    summary: text(explanation.summary),
    answer: text(explanation.answer),
    areas, tools,
    unasked: flowRows(report.inputs_unasked),
    missing: flowRows(report.inputs_missing),
    unverifiedFiles: advanced ? unverifiedFiles : [],
    unverifiedFilesText: !advanced && unverifiedFiles.length > 0 ? t("ext.offices.xa.unverified_count", { count: unverifiedFiles.length }) : "",
    unverifiedLines: flowsUnverified,
    codeNotChecked: codeFiles.length > 0,
    codeFiles: advanced ? codeFiles : [],
    findings: advanced ? findings : [],
    findingsCount: findings.length,
    needsRoot,
    advice: model ? t("ext.offices.ia.advice") : "",
    noteText: t("ext.offices.ia.note"),
  };
}

// --- the Mock rehearsal ----------------------------------------------------------------------------------------------

const STATUS_KEYS = {
  completed: "ext.offices.ia.status.completed",
  failed: "ext.offices.ia.status.failed",
  timed_out: "ext.offices.ia.status.timed_out",
  not_rehearsed: "ext.offices.ia.status.not_rehearsed",
};
const FLOW_REASON_KEYS = {
  untrusted_code: "ext.offices.ia.reason.untrusted_code",
  needs_a_person: "ext.offices.ia.reason.needs_a_person",
  write_outside_rehearsal_folder: "ext.offices.ia.reason.write_outside",
  workflow_unreadable: "ext.offices.ia.reason.workflow_unreadable",
  run_refused: "ext.offices.ia.reason.run_refused",
  time_limit: "ext.offices.ia.reason.time_limit",
  run_failed: "ext.offices.ia.reason.run_failed",
};
const STEP_KEYS = {
  ran: "ext.offices.ia.step.ran",
  skipped: "ext.offices.ia.step.skipped",
  not_run: "ext.offices.ia.step.not_run",
  not_rehearsed: "ext.offices.ia.step.not_rehearsed",
};

// What the card shows for the answer of /rehearse: which steps ran, which were not tried and why, which inputs were missing and
// what files it would have written. The server's `notice` is English prose; the fixed sentences here are the ones shown.
export function rehearsalView(result) {
  if (!result || typeof result !== "object" || !["finished", "failed"].includes(result.state)) return null;
  const files = (rows) => list(rows).filter((file) => file && safePath(file.path)).map((file) => ({
    path: text(file.path), text: t("ext.offices.ia.file_line", { path: text(file.path), size: Number.isInteger(file.size) ? file.size : 0 }),
  }));
  const flows = list(result.flows).map((flow) => {
    const reason = text(flow && flow.reason);
    const inputs = object(flow && flow.inputs);
    return {
      key: where(flow) || text(flow && flow.workflow),
      title: t("ext.offices.ia.flow_line", { where: where(flow), status: t(STATUS_KEYS[flow && flow.status] || "ext.offices.ia.status.failed") }),
      reasonText: reason ? t(FLOW_REASON_KEYS[reason] || "ext.offices.ia.reason.run_failed") : "",
      steps: list(flow && flow.steps).map((step) => t("ext.offices.ia.step_line", {
        id: text(step && step.id), state: t(STEP_KEYS[step && step.state] || "ext.offices.ia.step.not_run") })),
      syntheticText: strings(inputs.synthetic).length > 0
        ? t("ext.offices.ia.inputs_synthetic", { names: strings(inputs.synthetic).join(", ") }) : "",
      missingText: strings(inputs.missing).length > 0
        ? t("ext.offices.ia.inputs_missing", { names: strings(inputs.missing).join(", ") }) : "",
      files: files(flow && flow.files),
    };
  });
  return {
    failed: result.state === "failed",
    stateText: result.state === "failed" ? t("ext.offices.ia.trial_failed") : t("ext.offices.ia.trial_finished"),
    flows,
    files: files(result.files),
    omitted: Number.isInteger(result.flows_omitted) ? result.flows_omitted : 0,
    omittedText: Number.isInteger(result.flows_omitted) && result.flows_omitted > 0
      ? t("ext.offices.ia.flows_omitted", { count: result.flows_omitted }) : "",
    doneText: t("ext.offices.ia.done"),
    deletedText: result.folder_deleted === true ? t("ext.offices.ia.folder_deleted") : "",
  };
}

// --- the rehearsal that installing may need ----------------------------------------------------------------------------

// The state of the rehearsal for the card. `checks` is the card's `import_checks`; `trial` the answer of this session's own
// trial, if any; `forced` is true after the server answered 409 rehearsal_required (the card's record is then not enough).
// A trial that ended (even a failed one) counts as done; a failed one never blocks, the person decides.
export function rehearsalGate(checks, trial = null, { forced = false } = {}) {
  const info = object(checks);
  const required = info.rehearsal_required === true;
  const ended = (state) => state === "finished" || state === "failed";
  const own = trial && ended(trial.state) ? trial.state : "";
  const recorded = !forced && info.rehearsal && ended(info.rehearsal.state) ? info.rehearsal.state : "";
  const state = own || recorded;
  const done = state !== "";
  let kind = "recommended";
  if (done) kind = state === "failed" ? "failed" : "done";
  else if (required) kind = "required";
  return { required, done, state, kind, blocksInstall: required && !done,
    text: t("ext.offices.ia.gate." + kind) };
}

// The 409 of install: the sentence for it, or "" if the failure is anything else.
export function rehearsalRequiredText(error) {
  const details = object(error && error.details);
  const isIt = text(details.reason) === "office_rehearsal_required" || text(error && error.code) === "rehearsal_required";
  return isIt ? t("ext.offices.ia.error.rehearsal_required") : "";
}

// --- failures --------------------------------------------------------------------------------------------------------

const REHEARSE_REASONS = {
  office_rehearsal_busy: "ext.offices.ia.error.busy",
  office_rehearsal_folder_unsafe: "ext.offices.ia.error.folder_unsafe",
  office_rehearsal_no_workflows: "ext.offices.ia.error.no_workflows",
  office_rehearsal_unknown_flow: "ext.offices.ia.error.unknown_flow",
};

// The sentence for a failed call, by its reason first. `stale` tells the view to forget what it showed: the package is gone.
export function failureView(error) {
  const details = object(error && error.details);
  const reason = text(details.reason);
  const view = { text: "", stale: false };
  if (reason === "office_stage_expired" || reason === "office_changed_since_card" || reason === "office_source_changed") {
    view.text = t("ext.offices.ia.error.expired");
    view.stale = true;
  } else if (REHEARSE_REASONS[reason]) {
    view.text = t(REHEARSE_REASONS[reason]);
  } else if (reason === "office_import_invalid_explanation") {
    view.text = t("ext.offices.xa.error.invalid_proposal");
  } else if (reason === "advanced_mode_required") {
    view.text = errorText(reason, details);
  } else if (details.field === "question" || list(details.errors).some((item) => item && list(item.loc).includes("question"))) {
    view.text = t("ext.offices.ia.error.question");
  } else {
    view.text = text(error && error.message) || t("ext.offices.ia.error.default");
  }
  return view;
}
