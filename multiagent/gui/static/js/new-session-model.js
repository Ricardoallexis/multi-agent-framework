// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Conversation model of the guided "Nueva sesión" chat (GUI2-T03). Plain data and pure functions: no DOM, no Preact and
// only the pure quota-text helper, so a Node test can run it. new-session.js draws it; this file decides what happens.
//
// The flow is a small state machine (state.phase):
//   welcome   the greeting and the three starters
//   choose    workflow cards: the ones that match the text, or all of them
//   continue  the runs that can be picked up again
//   ask       one question at a time, one per workflow input, and the project name last
//   confirm   the summary card; only its «Lanzar» button creates a run
//   launched  the run was created; the run thread is shown in this same column
// Free text is read by a model on the server (GUI2-T17, POST /gui/api/new-session/interpret): new-session.js sends it and
// hands the answer to the reducer as `interpretation`. This file only validates what it is given: a proposal for a workflow
// that is in the catalog is shown as a card with its inputs prefilled; anything else (no model, an error, an invalid or
// empty answer) falls back to the deterministic matching of names and descriptions, and the chat says which one it used.
// Nothing here calls a model or the network, and nothing runs before «Lanzar».
import { formatQuotaLines } from "./quota-text.js";
import { currentLanguage, t } from "./i18n.js";

export const COMMANDS = ["workflow", "continuar", "ayuda"];
export const MAX_SUGGESTIONS = 4;
export const PROJECT_KEY = "@project";          // not an identifier, so it never clashes with an input name
export const PROJECT_MIN = 2;
export const PROJECT_MAX = 120;

// SES-T03: a single session can offer only a connected provider's model that the
// ask_once step admits in the general bundle. The server remains the final validator.
export function singleSessionModels(connections, routing) {
  const rows = Array.isArray(connections?.connections) ? connections.connections : [];
  const allowed = new Set(routing?.steps?.find((step) => step.step_id === "answer")?.model_options || []);
  const models = Array.isArray(routing?.catalog_models) ? routing.catalog_models : [];
  return rows.filter((row) => ["ollama", "gemini", "openai"].includes(row.id)).map((row) => {
    const matches = models.filter((model) => model.provider === row.id && allowed.has(model.id));
    const eligible = row.enabled === true && row.available === true;
    let reason = null;
    if (!eligible) {
      if (row.limit || row.reason === "limit_reached" || row.reason === "quota_paused") {
        reason = limitReachedText(row.limit || row.resets_at || row.paused_until);
      } else if (row.reason === "key_invalid") {
        reason = t("new_session.key_invalid");
      } else if (row.reason === "no_access") {
        reason = t("new_session.no_access");
      } else if (row.reason === "key_missing") {
        reason = t("new_session.key_missing");
      } else if (row.reason === "disabled") {
        reason = t("new_session.disabled");
      } else if (row.reason === "unreachable") {
        reason = t("new_session.unreachable");
      } else if (row.reason === "tls_certificate") {
        reason = t("new_session.tls_certificate");
      } else {
        reason = t("new_session.connection_unavailable");
      }
    } else if (!matches.length) {
      reason = t("new_session.model_not_in_catalog");
    }
    const status = row.id === "gemini" && eligible
      ? (row.verification === "verified" ? t("new_session.connected") : t("new_session.not_checked"))
      : null;
    const limit = !eligible && (row.limit || row.reason === "limit_reached" || row.reason === "quota_paused")
      ? buildLimitNotice(
        row.limit || { code: "limit_reached", resets_at: row.resets_at || row.paused_until || null, target: row.id, suggestions: [] },
        connections,
      )
      : null;
    return {
      provider: row.id,
      models: eligible ? matches : [],
      reason,
      status,
      quotaNote: row.id === "gemini" ? t("new_session.gemini_quota_unavailable") : null,
      limit,
    };
  });
}

export function singleSessionBody(task, model, confirmCloud = false, project = "") {
  return {
    workflow_id: "ask_once", bundle: null, project_name: String(project).trim() || "Sesión única",
    inputs: { task: String(task).trim() }, execution_mode: model === "cloud_fast" ? "cloud" : "local", sensitive: false,
    ...(confirmCloud ? { confirm_cloud: true } : {}),
  };
}

// SES-T04: explicit approval data for a single-session executor launch.
export function quotaRemainingLines(quota) {
  return formatQuotaLines(quota, { remaining: true });
}

export function executorLaunchBody(task, executorId, model, effort, acceptQuotaWarning = false) {
  return {
    task: String(task).trim(), executor_id: executorId, model: model || null, effort: effort || null, confirmed: true,
    ...(acceptQuotaWarning ? { accept_quota_warning: true } : {}),
  };
}

// CUO-T06: pure limit-notice, agy floor warning and suggestion selection helpers (no DOM, no network).
const CONNECTION_LABELS = {
  gemini: "Gemini",
  openai: "OpenAI",
  ollama: "Ollama",
  claude_cli: "Claude",
  codex_cli: "Codex",
  copilot_cli: "Copilot",
  antigravity_cli: "Antigravity (agy)",
};
const MODEL_PROVIDER_IDS = new Set(["gemini", "openai", "ollama"]);
const EXECUTOR_PROVIDER_IDS = new Set(["claude_cli", "codex_cli", "copilot_cli", "antigravity_cli"]);

const LIMIT_TIME_FORMATS = Object.fromEntries(["en", "es"].map((language) => [language, new Intl.DateTimeFormat(language, {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
})]));

export function connectionLabel(id) {
  return CONNECTION_LABELS[id] || String(id || "");
}

export function limitTimeText(resetsAt) {
  if (typeof resetsAt !== "string" && !(resetsAt instanceof Date)) return "";
  if (typeof resetsAt === "string" && !resetsAt.trim()) return "";
  const date = resetsAt instanceof Date ? resetsAt : new Date(resetsAt);
  if (Number.isNaN(date.getTime())) return "";
  return LIMIT_TIME_FORMATS[currentLanguage()].format(date);
}

export function limitReachedText(limitOrResetsAt) {
  const raw = limitOrResetsAt && typeof limitOrResetsAt === "object" && !(limitOrResetsAt instanceof Date)
    ? limitOrResetsAt.resets_at
    : limitOrResetsAt;
  const hhmm = limitTimeText(raw);
  return hhmm ? t("new_session.limit_resets_at", { time: hhmm }) : t("new_session.limit_no_reset");
}

export function quotaWarningText(warning) {
  if (!warning || typeof warning.remaining_percent !== "number" || !Number.isFinite(warning.remaining_percent)) {
    return null;
  }
  const pct = String(Math.round(warning.remaining_percent * 10) / 10).replace(".", currentLanguage() === "es" ? "," : ".");
  return t("new_session.quota_warning", { percent: pct });
}

export function buildLimitNotice(limit, connectionsPayload = null) {
  if (!limit || typeof limit !== "object") return null;
  const rows = Array.isArray(connectionsPayload?.connections) ? connectionsPayload.connections : [];
  const byId = new Map(rows.map((row) => [row.id, row]));
  const rawSuggestions = Array.isArray(limit.suggestions) ? limit.suggestions : [];
  const suggestions = [];
  for (const id of rawSuggestions) {
    if (!id || suggestions.some((item) => item.id === id) || id === limit.target) continue;
    const row = byId.get(id);
    if (row && (row.available !== true || row.enabled !== true)) continue;
    suggestions.push({
      id,
      label: connectionLabel(id),
      kind: MODEL_PROVIDER_IDS.has(id) ? "model" : EXECUTOR_PROVIDER_IDS.has(id) ? "executor" : "other",
    });
    if (suggestions.length >= 3) break;
  }
  return {
    code: limit.code || "limit_reached",
    target: limit.target || "",
    resetsAt: limit.resets_at || null,
    text: limitReachedText(limit),
    suggestions,
  };
}

export function selectOrchestratorSuggestion(suggestion, draft) {
  return {
    selectedConnection: suggestion?.id || null,
    draft: String(draft === null || draft === undefined ? "" : draft),
    sent: false,
  };
}

export function selectSingleModelSuggestion(suggestion, choices, task) {
  const keptTask = String(task === null || task === undefined ? "" : task);
  if (!suggestion || !suggestion.id) return { target: "single", model: "", confirmCloud: false, task: keptTask, sent: false };
  if (suggestion.kind === "executor") {
    return { target: "single-executor", executorId: suggestion.id, task: keptTask, sent: false };
  }
  const match = (choices || []).find((choice) => choice.provider === suggestion.id && choice.models?.length);
  return {
    target: "single",
    model: match ? match.models[0].id : "",
    confirmCloud: false,
    task: keptTask,
    sent: false,
  };
}

export function selectSingleExecutorSuggestion(suggestion, task) {
  const keptTask = String(task === null || task === undefined ? "" : task);
  if (!suggestion || !suggestion.id) return { target: "single-executor", executorId: "", task: keptTask, sent: false };
  if (suggestion.kind === "model") {
    return { target: "single", provider: suggestion.id, task: keptTask, sent: false };
  }
  return { target: "single-executor", executorId: suggestion.id, task: keptTask, sent: false };
}

// SEC-T04: secret review holding and resending with approval_id for POST /gui/api/new-session/interpret.
export const SECRET_REVIEW_REASON = "secret_review_required";
export const secretReviewNotice = () => t("new_session.secret_review_notice");

export function isSecretReviewRequired(interpretation) {
  return Boolean(interpretation && interpretation.reason === SECRET_REVIEW_REASON);
}

export function interpretBody(text, { confirmCloud = false, approvalId = "" } = {}) {
  const body = { text: String(text || ""), confirm_cloud: Boolean(confirmCloud) };
  if (approvalId) body.approval_id = String(approvalId);
  return body;
}

export function officeOptions(payload) {
  const offices = Array.isArray(payload?.offices) ? payload.offices : [];
  return [{ value: "", label: t("new_session.no_office") }, ...offices
    .filter((office) => office && typeof office.id === "string" && office.id)
    .map((office) => ({ value: office.id, label: String(office.name || office.id) }))];
}

export function officeRequestBody(text, office, confirmCloud = false) {
  return {
    text: String(text || ""),
    confirm_cloud: Boolean(confirmCloud),
    approval_id: null,
    office: String(office || ""),
  };
}

export function officeWorkflowGroups(area, workflows) {
  const definitions = Array.isArray(area) ? area : area && typeof area === "object" ? [area] : [];
  const areaIds = definitions.map((item) => String(item.id || "")).filter(Boolean);
  const selected = typeof area === "string" ? area : area && !Array.isArray(area) ? String(area.id || "") : "";
  const grouped = new Map();
  for (const workflow of Array.isArray(workflows) ? workflows : []) {
    const bundle = String(workflow?.bundle || "");
    const id = String(workflow?.id || "");
    const areaId = String(workflow?.area || bundle.split("__").pop() || "");
    if (!areaId || !id || (selected && areaId !== selected)) continue;
    if (!grouped.has(areaId)) grouped.set(areaId, []);
    grouped.get(areaId).push({
      id,
      label: String(workflow.label || workflow.title || id.replace(/[_-]+/g, " ")),
    });
  }
  const order = areaIds.length ? areaIds : [...grouped.keys()].sort();
  if (selected && !order.includes(selected)) order.unshift(selected);
  return order.filter((id) => grouped.has(id)).map((id) => ({
    area: id,
    items: grouped.get(id),
  }));
}

// The words of the «Oficinas» view (extensions/offices/model.js, LEVEL_TEXT); shared code cannot import a view.
export const OFFICE_LEVEL_TEXT = Object.freeze({
  get non_sensitive() { return t("new_session.privacy_normal"); },
  get sensitive() { return t("new_session.privacy_sensitive"); },
  get restricted() { return t("new_session.privacy_local_only"); },
  get none() { return t("new_session.privacy_no_level"); },
});

// The strictest level among the areas of the scene.
export function officeLevelText(scene) {
  const classes = Array.isArray(scene?.areas) ? scene.areas.map((area) => area?.data_class) : [];
  for (const level of ["restricted", "sensitive", "non_sensitive"]) {
    if (classes.includes(level)) return OFFICE_LEVEL_TEXT[level];
  }
  return OFFICE_LEVEL_TEXT.none;
}

export function officeAllowsCloud(scene) {
  const areas = Array.isArray(scene?.areas) ? scene.areas : [];
  return areas.length > 0 && areas.every((area) => area?.data_class === "non_sensitive");
}

export function officeAreaForBundle(bundle, offices) {
  const name = String(bundle || "");
  const matches = (Array.isArray(offices) ? offices : [])
    .map((office) => String(office?.id || ""))
    .filter(Boolean)
    .sort((left, right) => right.length - left.length);
  const officeId = matches.find((id) => name.startsWith(id + "__"));
  return officeId ? { officeId, areaId: name.slice(officeId.length + 2) } : null;
}

export function officeAreaPrivacy(privacy, areaId) {
  return (Array.isArray(privacy?.areas) ? privacy.areas : [])
    .find((area) => area?.id === areaId) || null;
}

export function officeAreaAllowsCloud(privacy, areaId) {
  const area = officeAreaPrivacy(privacy, areaId);
  return area?.data_class === "non_sensitive" && area.cloud_effective === true;
}

export function officeAreaRequiresLocal(privacy, areaId) {
  return !officeAreaAllowsCloud(privacy, areaId);
}

export function hasAvailableLocalModel(connections, routing) {
  const localConnection = (Array.isArray(connections?.connections) ? connections.connections : [])
    .some((connection) => connection?.id === "ollama" && connection.enabled === true && connection.available === true);
  if (!localConnection) return false;
  const localModels = new Set((Array.isArray(routing?.catalog_models) ? routing.catalog_models : [])
    .filter((model) => model?.provider === "ollama")
    .map((model) => model.id));
  const allowedModels = new Set((Array.isArray(routing?.steps) ? routing.steps : [])
    .flatMap((step) => Array.isArray(step?.model_options) ? step.model_options : []));
  return [...localModels].some((model) => allowedModels.has(model));
}

export function officeConnectionData(payload, scene) {
  if (!payload || typeof payload !== "object") return payload;
  const cloudAllowed = officeAllowsCloud(scene);
  const connections = Array.isArray(payload.connections) ? payload.connections : [];
  return {
    ...payload,
    connections: connections.filter((item) =>
      item?.id === "ollama" || (cloudAllowed && MODEL_PROVIDER_IDS.has(item?.id))),
  };
}

export function officeLimitNotice(notice, scene) {
  if (!notice || typeof notice !== "object") return notice;
  const cloudAllowed = officeAllowsCloud(scene);
  return {
    ...notice,
    suggestions: (Array.isArray(notice.suggestions) ? notice.suggestions : []).filter((item) =>
      item?.kind === "model" && (cloudAllowed || item.id === "ollama")),
  };
}

export function officeModelOptions(step, routing, cloudAllowed = true) {
  const options = Array.isArray(step?.model_options) ? step.model_options : [];
  if (cloudAllowed) return options;
  const local = new Set((Array.isArray(routing?.catalog_models) ? routing.catalog_models : [])
    .filter((model) => model?.provider === "ollama")
    .map((model) => model.id));
  return options.filter((model) => local.has(model));
}

export function officeModelValue(step, overrides, routing, cloudAllowed = true) {
  const override = overrides && overrides[step?.step_id];
  const selected = override && Object.prototype.hasOwnProperty.call(override, "model")
    ? override.model || ""
    : step?.model || "";
  return officeModelOptions(step, routing, cloudAllowed).includes(selected) ? selected : "";
}

export function officeExecutionModes(modes, cloudAllowed = true) {
  const choices = Array.isArray(modes) ? modes : [];
  return cloudAllowed ? choices : choices.filter((value) => value !== "cloud");
}

export const OFFICE_LEVEL_REQUIRED_TEXT = "new_session.office_level_required";

export function officeLevelRequiredText(failure) {
  return failure?.details?.reason === "office_level_required" ? t(OFFICE_LEVEL_REQUIRED_TEXT) : "";
}

export function createSecretReview({ text, requestText, confirmCloud = false, approvalId = "" }) {
  return {
    text: String(text || ""),
    requestText: String(requestText || ""),
    confirmCloud: Boolean(confirmCloud),
    approvalId: String(approvalId || ""),
  };
}

export function secretResendRequest(pending) {
  return {
    text: pending ? pending.requestText : "",
    confirmCloud: Boolean(pending && pending.confirmCloud),
    approvalId: pending ? String(pending.approvalId || "") : "",
  };
}

export const STARTERS = [
  { id: "workflow", get label() { return t("new_session.starter_workflow"); } },
  { id: "continuar", get label() { return t("new_session.starter_continue"); } },
  { id: "ayuda", get label() { return t("new_session.starter_help"); } },
];
export const SOURCE_MODEL = "model";
export const SOURCE_KEYWORDS = "keywords";
export const RESUMABLE = ["waiting_human", "interrupted", "paused", "running", "queued"];   // in the order they are listed

// --- text matching ----------------------------------------------------------------------------------------------

const NAME_WEIGHT = 3;
const TEXT_WEIGHT = 1.5;
const STOPWORDS = new Set(("de del la las el los un una unos unas y o u e en a al ante con para por que se lo le les me mi mis tu tus " +
  "su sus es son ser fue sobre como mas muy ya hay quiero quiere necesito necesitas quisiera hacer haz trabajar trabajo workflow " +
  "workflows flujo flujos the of and or to for in on with an is are my want need make do it this that").split(" "));

// Lower case without accents: «Reunión» and «reunion» are the same word.
export function normalize(text) {
  return String(text === null || text === undefined ? "" : text).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

function wordsOf(text) {
  const found = [];
  for (const word of normalize(text).split(/[^a-z0-9]+/)) if (word.length >= 2 && !found.includes(word)) found.push(word);
  return found;
}

// The words of what the user wrote that can say something: no filler words, no repeats.
export function keywords(text) {
  return wordsOf(text).filter((word) => !STOPWORDS.has(word));
}

// 1 for the same word; 0.6 for words that share a stem (resumir/resumen, documento/documentos); 0.5 when the query is
// the start of a longer word (a name typed halfway); 0 otherwise.
function wordScore(query, word) {
  if (query === word) return 1;
  const shorter = Math.min(query.length, word.length);
  let common = 0;
  while (common < shorter && query[common] === word[common]) common += 1;
  if (common >= 5 || (common >= 4 && common === shorter)) return 0.6;
  if (query.length >= 3 && word.startsWith(query)) return 0.5;
  return 0;
}

const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
export const entryKey = (entry) => (entry.bundle || "") + "|" + entry.id;
const byOrigin = (a, b) => compare(a.bundle || "", b.bundle || "") || compare(a.id, b.id);

export function originLabel(entry) {
  return entry.bundle ? t("new_session.bundle_origin", { name: entry.bundle }) : t("new_session.framework_origin");
}

// The line a workflow card shows under its name.
export function cardDescription(entry) {
  if (entry.description) return entry.description;
  if (entry.unreadable) return t("new_session.workflow_unreadable");
  return entry.described ? t("new_session.workflow_no_description") : t("new_session.reading_description");
}

// Workflows that match the text, best first: [{ entry, score, matched }]. The name weighs double the description.
// Same input, same order: ties break by origin and id.
export function matchWorkflows(text, catalog, { limit = Infinity } = {}) {
  const query = keywords(text);
  if (!query.length) return [];
  const found = [];
  for (const entry of catalog || []) {
    const nameWords = wordsOf(String(entry.id).replace(/[_-]+/g, " "));
    const textWords = wordsOf(entry.description);
    let score = 0;
    const matched = [];
    for (const word of query) {
      let best = 0;
      for (const candidate of nameWords) best = Math.max(best, NAME_WEIGHT * wordScore(word, candidate));
      for (const candidate of textWords) best = Math.max(best, TEXT_WEIGHT * wordScore(word, candidate));
      if (best > 0) { score += best; matched.push(word); }
    }
    if (matched.length === 0) continue;
    if (matched.length === query.length) score += 1;       // everything the user said is covered
    found.push({ entry, score, matched });
  }
  found.sort((a, b) => b.score - a.score || byOrigin(a.entry, b.entry));
  return found.slice(0, limit);
}

export const sortCatalog = (catalog) => [...catalog].sort(byOrigin);
const everyone = (catalog) => sortCatalog(catalog || []).map((entry) => ({ key: entryKey(entry), matched: [] }));

// --- commands ---------------------------------------------------------------------------------------------------

// "/workflow", "/continuar" and "/ayuda" (and "/workflows"), with optional text after them. Anything else that starts
// with a slash is not a command: it is an answer or free text (a path, for instance).
export function parseCommand(text) {
  const match = /^\/(workflows?|continuar|ayuda)(?:\s+([\s\S]*))?$/i.exec(String(text || "").trim());
  if (!match) return null;
  const name = match[1].toLowerCase();
  return { name: name === "workflows" ? "workflow" : name, rest: (match[2] || "").trim() };
}

// --- questions --------------------------------------------------------------------------------------------------

function derivedDescription(item) {
  const parts = [t("new_session.derived_input")];
  if (item.used_in && item.used_in.length) parts.push(t("new_session.input_used_in", { locations: item.used_in.join(", ") }));
  if (item.prompt_line) parts.push(t("new_session.input_prompt_line", { line: item.prompt_line }));
  return parts.join(" ");
}

const PROJECT_QUESTION = {
  key: PROJECT_KEY, source: "project", name: "project", get label() { return t("new_session.project"); }, kind: "text", choices: [], required: true,
  get description() { return t("new_session.project_description"); },
  get example() { return t("new_session.project_example"); },
};

// One question per input of the workflow, in the order the server sends them, and the project name last. The optional
// extra-context inputs («..._context») are not asked: they stay in the form.
export function buildQuestions(spec) {
  const questions = [];
  for (const item of (spec && spec.inputs) || []) {
    if (item.context && !item.required) continue;
    const choices = item.type === "choice" ? [...(item.choices || [])] : [];
    questions.push({
      key: item.name, source: "input", name: item.name, label: item.label || item.name,
      kind: choices.length ? "choice" : item.type === "long_text" ? "long_text" : "text", choices,
      required: Boolean(item.required),
      description: item.description || derivedDescription(item),
      example: item.type === "choice" ? "" : item.example || "",
    });
  }
  questions.push({ ...PROJECT_QUESTION });
  return questions;
}

// { value } when the answer is acceptable (trimmed, and the exact spelling of a choice), { error } when it is not.
export function validateAnswer(question, raw) {
  const value = String(raw === null || raw === undefined ? "" : raw).trim();
  if (question.source === "project") {
    if (value.length < PROJECT_MIN) return { error: t("new_session.project_too_short", { count: PROJECT_MIN }) };
    if (value.length > PROJECT_MAX) return { error: t("new_session.project_too_long", { count: PROJECT_MAX }) };
    return { value };
  }
  if (!value) return question.required ? { error: t("new_session.required_answer") } : { value: "" };
  if (question.kind === "choice") {
    const hit = question.choices.find((choice) => normalize(choice) === normalize(value));
    return hit === undefined ? { error: t("new_session.choose_from_options", { choices: question.choices.join(", ") }) } : { value: hit };
  }
  return { value };
}

// --- the conversation -------------------------------------------------------------------------------------------

export function createConversation() {
  return {
    phase: "welcome", messages: [], nextId: 1,
    suggestions: [], source: "", showAll: false,          // choose: [{ key, matched }], who proposed them ("model", "keywords", ""), full list or not
    proposal: null,                                       // the model's proposal that is on screen: { key, inputs, project }
    workflow: null, questions: [], inputNames: [], answers: {}, pendingPrefill: [], index: 0, error: "",
  };
}

function say(state, role, text) {
  return { ...state, messages: [...state.messages, { id: state.nextId, role, text }], nextId: state.nextId + 1 };
}

const clearWorkflow = (state) => ({ ...state, proposal: null, workflow: null, questions: [], inputNames: [], answers: {}, pendingPrefill: [], index: 0, error: "" });

export const currentQuestion = (state) => (state.phase === "ask" ? state.questions[state.index] || null : null);
export const progress = (state) => {
  const asked = state.questions.filter((question) => !state.pendingPrefill.includes(question.key));
  const current = asked.findIndex((question) => question.key === currentQuestion(state)?.key);
  return { n: current + 1, total: asked.length };
};

function questionLine(state) {
  const question = state.questions[state.index];
  const { n, total } = progress(state);
  return t("new_session.question_line", {
    number: n, total, label: question.label, optional: question.required ? "" : " " + t("new_session.optional_parenthetical"),
  });
}

export const helpMessageText = () => t("new_session.help_message");

// Lower-cases the first letter so the numbered question line reads inside a sentence in either language.
function lowerFirst(text) { return text.replace(/^./, (c) => c.toLowerCase()); }

function helpMessage(state) {
  const question = currentQuestion(state);
  if (!question) return t("new_session.help_message");
  return t("new_session.help_message_with_question", {
    help: t("new_session.help_message"), question: lowerFirst(questionLine(state)),
    optional_action: question.required ? "." : t("new_session.optional_skip_suffix"),
  });
}

// Why the model was not used, for the line that tells the operator the words were compared instead.
const FALLBACK_REASONS = {
  disabled: "new_session.fallback_disabled",
  no_workflows: "new_session.fallback_no_workflows",
  model_error: "new_session.fallback_model_error",
  refused: "new_session.fallback_refused",
  cloud_confirmation_required: "new_session.fallback_cloud_confirmation",
  limit_reached: "new_session.fallback_limit",
  budget_cut: "new_session.fallback_budget",
  invalid_output: "new_session.fallback_invalid_output",
  no_match: "new_session.fallback_no_match",
};
export const fallbackReason = (reason) => t(FALLBACK_REASONS[reason] || "new_session.fallback_unavailable");

// The proposal of the server when it is usable here: its workflow is in the catalog. Inputs are kept as text; the ones the
// workflow does not ask are ignored when the workflow is picked.
function usableProposal(interpretation, catalog) {
  if (!interpretation || interpretation.source !== SOURCE_MODEL || !interpretation.proposal) return null;
  const proposal = interpretation.proposal;
  const entry = (catalog || []).find((item) => item.id === proposal.workflow_id && (item.bundle || null) === (proposal.bundle || null));
  if (!entry) return null;
  const inputs = {};
  for (const [name, value] of Object.entries(proposal.inputs || {})) if (typeof value === "string" && value.trim()) inputs[name] = value.trim();
  return { entry, key: entryKey(entry), inputs, project: String(proposal.project_name || "").trim(), explanation: String(proposal.explanation || "").trim() };
}

// Proposes workflows for a text: the model's choice when there is a valid one, otherwise by comparing the text with their
// names and descriptions.
function suggest(state, text, catalog, interpretation) {
  const all = sortCatalog(catalog || []);
  if (all.length === 0) {
    return { ...say(state, "assistant", t("new_session.no_workflows_form")), phase: "choose", suggestions: [], source: "", proposal: null, showAll: true };
  }
  const proposed = usableProposal(interpretation, all);
  if (proposed) {
    const filled = Object.keys(proposed.inputs).length;
    const note = (proposed.explanation ? " " + proposed.explanation : "") +
      (filled ? " " + t(filled === 1 ? "new_session.prefilled_one" : "new_session.prefilled_many", { count: filled }) : "");
    return { ...say(state, "assistant", t("new_session.model_proposal", { id: proposed.entry.id, note })),
      phase: "choose", suggestions: [{ key: proposed.key, matched: [] }], source: SOURCE_MODEL, showAll: false,
      proposal: { key: proposed.key, inputs: proposed.inputs, project: proposed.project } };
  }
  const why = !interpretation ? "" : " (" + fallbackReason(interpretation.source === SOURCE_KEYWORDS ? interpretation.reason : "invalid_output") + ")";
  const found = matchWorkflows(text, all, { limit: MAX_SUGGESTIONS });
  const source = SOURCE_KEYWORDS;
  const how = interpretation ? t("new_session.compared_words", { reason: why }) + " " : "";
  if (found.length === 0) {
    return { ...say(state, "assistant", how + t("new_session.no_matching_workflow")),
      phase: "choose", suggestions: everyone(all), source: "", proposal: null, showAll: true };
  }
  return { ...say(state, "assistant", how + t(found.length === 1 ? "new_session.match_one" : "new_session.match_many")),
  phase: "choose", suggestions: found.map((item) => ({ key: entryKey(item.entry), matched: item.matched })), source, proposal: null, showAll: false };
}

function listAll(state, catalog) {
  const all = sortCatalog(catalog || []);
  const message = all.length === 0
    ? t("new_session.no_workflows_form")
    : t("new_session.all_workflows");
  return { ...say(state, "assistant", message), phase: "choose", source: "", suggestions: everyone(all), showAll: true };
}

function onCommand(state, action) {
  const echoed = say(state, "user", action.echo || "/" + action.name + (action.rest ? " " + action.rest : ""));
  if (action.name === "ayuda") return say(echoed, "assistant", helpMessage(state));
  const fresh = clearWorkflow(echoed);
  if (action.name === "continuar") return { ...say(fresh, "assistant", t("new_session.runs_to_continue")), phase: "continue", suggestions: [], source: "" };
  return action.rest ? suggest(fresh, action.rest, action.catalog, action.interpretation) : listAll(fresh, action.catalog);
}

function advance(state, value, echo) {
  const question = state.questions[state.index];
  let next = say({ ...state, answers: { ...state.answers, [question.key]: value },
    pendingPrefill: state.pendingPrefill.filter((key) => key !== question.key), error: "" }, "user", echo);
  let following = state.index + 1;
  while (following < state.questions.length && next.pendingPrefill.includes(state.questions[following].key)) following += 1;
  if (following < state.questions.length) {
    next = { ...next, index: following };
    return say(next, "assistant", questionLine(next));
  }
  return say({ ...next, phase: "confirm" }, "assistant", t("launch.ready", { workflow: state.workflow.id }));
}

function onAnswer(state, text) {
  const question = currentQuestion(state);
  if (!question) return state;
  const checked = validateAnswer(question, text);
  if (checked.error) return { ...state, error: checked.error };
  return advance(state, checked.value, checked.value || t("new_session.no_answer"));
}

function onSkip(state) {
  const question = currentQuestion(state);
  if (!question || question.required) return state;
  return advance(state, "", t("new_session.skipped"));
}

function onBack(state) {
  if (state.phase !== "ask" || state.index === 0) return state;
  const back = { ...state, index: state.index - 1, pendingPrefill: [], error: "" };
  return say(back, "assistant", t("new_session.previous_question_message", { question: lowerFirst(questionLine(back)) }));
}

function onEdit(state) {
  if (state.phase !== "confirm") return state;
  const back = { ...state, phase: "ask", index: 0, pendingPrefill: [], error: "" };
  return say(say(back, "assistant", t("new_session.edit_answers")), "assistant", questionLine(back));
}

// The answers a model proposal gives for this workflow: only inputs it asks, each one checked like an answer typed by hand.
function prefilledAnswers(state, workflow, questions) {
  if (!state.proposal || state.proposal.key !== entryKey(workflow)) return {};
  const answers = {};
  for (const question of questions) {
    const raw = question.source === "project" ? state.proposal.project : state.proposal.inputs[question.name];
    if (!raw) continue;
    const checked = validateAnswer(question, raw);
    if (!checked.error && checked.value) answers[question.key] = checked.value;
  }
  return answers;
}

function onPick(state, action) {
  const spec = action.spec || { inputs: [] };
  const workflow = action.workflow;
  const questions = buildQuestions(spec);
  const answers = prefilledAnswers(state, workflow, questions);
  const prefilled = Object.keys(answers).length;
  // GUI3-T06: the project chosen in the box fills its answer, and its question stays in the flow to confirm it. A project
  // that only the model proposed is checked in the summary, as before.
  const fromBox = String(action.project || "").trim();
  if (fromBox) answers[PROJECT_KEY] = fromBox;
  const confirmFromBox = (key) => Boolean(fromBox) && key === PROJECT_KEY;
  const next = say(say(state, "user", t("new_session.choose_workflow_echo", { id: workflow.id })), "assistant",
    prefilled ? t(prefilled === 1 ? "new_session.prefill_confirm_one" : "new_session.prefill_confirm_many", { count: prefilled })
      : t("new_session.ask_one_at_a_time"));
  const first = questions.findIndex((question) => question.required && (confirmFromBox(question.key) || !answers[question.key]));
  const started = {
    ...next, phase: first === -1 && prefilled ? "confirm" : "ask", index: first === -1 ? 0 : first, error: "", answers,
    pendingPrefill: Object.keys(answers).filter((key) => !confirmFromBox(key)),
    workflow: { id: workflow.id, bundle: workflow.bundle || null, description: spec.description || workflow.description || "" },
    questions, inputNames: (spec.inputs || []).map((item) => item.name),
  };
  if (started.phase === "confirm") return say(started, "assistant", t("launch.ready", { workflow: workflow.id }));
  return say(started, "assistant", questionLine(started));
}

// Every change of the conversation goes through here. Actions:
//   reset                               back to the greeting
//   send   { text, catalog, interpretation? } what the user typed: a command, an answer or free text, depending on the phase;
//                                       interpretation is the answer of the server's model (see above)
//   command{ name, rest, echo, catalog }a starter chip (echo is the chip label)
//   pick   { workflow, spec }           a workflow card was chosen and its inputs were read
//   show_all { catalog }                the full list of workflows
//   skip | back | edit                  buttons of the question and of the summary
//   launched { runId }                  the run was created
//   notice { text }                     a message from the assistant (an error, for instance)
//   typing                              the user changed the text: the message about the previous one goes away
export function reduce(state, action) {
  switch (action.type) {
    case "reset": return createConversation();
    case "send": {
      const text = String(action.text === null || action.text === undefined ? "" : action.text).trim();
      const command = parseCommand(text);
      if (command) return onCommand(state, { ...action, ...command, echo: text });
      if (state.phase === "ask") return onAnswer(state, text);
      if (!text) return state;
      if (state.phase === "confirm" || state.phase === "launched") {
        return say(say(state, "user", text), "assistant", t("new_session.use_summary_buttons"));
      }
      return suggest(clearWorkflow(say(state, "user", text)), text, action.catalog, action.interpretation);
    }
    case "command": return onCommand(state, action);
    case "pick": return onPick(state, action);
    case "project": {
      const project = String(action.project || "").trim();
      const next = { ...state, answers: { ...state.answers, [PROJECT_KEY]: project } };
      if (state.phase === "confirm" && !project) {
        return { ...next, phase: "ask", index: state.questions.findIndex((question) => question.key === PROJECT_KEY), error: "" };
      }
      return next;
    }
    case "show_all": {
      const shown = say(state, "assistant", t("new_session.all_workflows_shown"));
      return { ...shown, phase: "choose", source: "", proposal: null, suggestions: everyone(action.catalog), showAll: true };
    }
    case "skip": return onSkip(state);
    case "back": return onBack(state);
    case "edit": return onEdit(state);
    case "launched": return say({ ...state, phase: "launched", runId: action.runId }, "assistant", t("new_session.run_created"));
    case "notice": return say(state, "assistant", String(action.text || ""));
    case "typing": return state.error ? { ...state, error: "" } : state;
    default: return state;
  }
}

// --- what the screen shows and sends ----------------------------------------------------------------------------

// The cards of the choose phase: the entries of the catalog the suggestions point at, with the words that matched.
export function suggestedEntries(state, catalog) {
  const known = new Map((catalog || []).map((entry) => [entryKey(entry), entry]));
  return state.suggestions.filter((item) => known.has(item.key)).map((item) => ({ entry: known.get(item.key), matched: item.matched }));
}

// Rows of the summary: every question with its answer; an empty optional one says so.
export function answerRows(state) {
  return state.questions.map((question) => {
    const value = state.answers[question.key] || "";
    return { key: question.key, label: question.label, value, empty: value === "" };
  });
}

// The body of POST /gui/api/runs (without the idempotency key): the same one the form sends. Every input of the workflow
// goes, the ones that were not asked (extra context) and the skipped ones as empty text.
export function runBody(state, { mode = "auto", sensitive = false } = {}) {
  const inputs = {};
  for (const name of state.inputNames) inputs[name] = state.answers[name] || "";
  return {
    workflow_id: state.workflow.id, bundle: state.workflow.bundle || null,
    project_name: (state.answers[PROJECT_KEY] || "").trim(), inputs, execution_mode: mode, sensitive: Boolean(sensitive),
  };
}

// What «Ver como formulario» hands to the existing form so it opens with the same workflow and answers (null: nothing yet).
export function formSeed(state, { mode = "auto", sensitive = false } = {}) {
  if (!state.workflow) return null;
  const values = {};
  for (const name of state.inputNames) values[name] = state.answers[name] || "";
  return {
    bundle: state.workflow.bundle || "", workflowId: state.workflow.id, project: state.answers[PROJECT_KEY] || "",
    values, mode, sensitive: Boolean(sensitive),
  };
}

// Runs to offer in «Continuar una ejecución»: the ones that can be picked up (a person is awaited, interrupted, paused,
// running or queued), the ones that wait for the person first; with none of those, the most recent ones.
export function pickRuns(runs) {
  const list = Array.isArray(runs) ? runs : [];
  const open = list.filter((run) => RESUMABLE.includes(run.status));
  open.sort((a, b) => RESUMABLE.indexOf(a.status) - RESUMABLE.indexOf(b.status));
  return open.length ? { resumable: true, runs: open.slice(0, 8) } : { resumable: false, runs: list.slice(0, 5) };
}

// --- reading the workflows --------------------------------------------------------------------------------------

export const workflowsPath = (bundle) => "/gui/api/workflows" + (bundle ? "?bundle=" + encodeURIComponent(bundle) : "");
export const inputsPath = (entry) =>
  "/gui/api/workflows/" + encodeURIComponent(entry.id) + "/inputs" + (entry.bundle ? "?bundle=" + encodeURIComponent(entry.bundle) : "");

// The catalog the chat matches against: every workflow of every origin with its description. The list endpoint gives
// only the ids, so the description comes from the inputs endpoint of each one (a few at a time). `get(path)` is the GET
// call (injected, so a test needs no server); `onUpdate(entries)` is called with the names first and again as the
// descriptions arrive. A workflow whose description cannot be read stays in the catalog and still matches by name.
export async function loadCatalog({ get, onUpdate = () => {}, concurrency = 4, bundles = null }) {
  const origins = Array.isArray(bundles)
    ? bundles.filter((bundle) => typeof bundle === "string" && bundle)
    : [null].concat((await get("/gui/api/bundles") || []).map((item) => item.name));
  if (!origins.length) {
    onUpdate([]);
    return [];
  }
  const results = await Promise.all(origins.map((origin) => get(workflowsPath(origin)).then((list) => ({ list }), (error) => ({ error }))));
  if (results.every((result) => result.error)) throw results[0].error;
  let entries = [];
  results.forEach((result, index) => {
    for (const item of result.list || []) entries.push({ id: item.id, bundle: item.bundle || origins[index] || null, description: "", described: false, unreadable: false });
  });
  entries = sortCatalog(entries);
  onUpdate(entries.slice());
  let next = 0;
  async function worker() {
    while (next < entries.length) {
      const at = next;
      next += 1;
      let description = "";
      let unreadable = false;
      try {
        const spec = await get(inputsPath(entries[at]));
        description = String((spec && spec.description) || "").trim();
      } catch (_) { unreadable = true; }       // the workflow stays in the catalog and matches by name
      entries[at] = { ...entries[at], description, described: true, unreadable };
      onUpdate(entries.slice());
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, entries.length) }, worker));
  return entries;
}

// SIM-T03: the «＋» menu and the origin line, by mode. Simple has no «Ver como formulario» (it shows workflow ids) and no origin.
export const plusMenuEntries = (mode) => (mode === "simple" ? ["executor"] : ["executor", "form"]);
export const cardOrigin = (entry, mode) => (mode === "simple" ? "" : originLabel(entry));
