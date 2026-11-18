// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Conversation model of the guided "Nueva sesión" chat (GUI2-T03). Plain data and pure functions: no DOM, no Preact and
// no imports, so a Node test can run it. new-session.js draws it; this file decides what happens.
//
// The flow is a small state machine (state.phase):
//   welcome   the greeting and the three starters
//   choose    workflow cards: the ones that match the text, or all of them
//   continue  the runs that can be picked up again
//   ask       one question at a time, one per workflow input, and the project name last
//   confirm   the summary card; only its «Lanzar» button creates a run
//   launched  the run was created
// Free text is read by a model on the server (GUI2-T17, POST /gui/api/new-session/interpret): new-session.js sends it and
// hands the answer to the reducer as `interpretation`. This file only validates what it is given: a proposal for a workflow
// that is in the catalog is shown as a card with its inputs prefilled; anything else (no model, an error, an invalid or
// empty answer) falls back to the deterministic matching of names and descriptions, and the chat says which one it used.
// Nothing here calls a model or the network, and nothing runs before «Lanzar».

export const COMMANDS = ["workflow", "continuar", "ayuda"];
export const MAX_SUGGESTIONS = 4;
export const PROJECT_KEY = "@project";          // not an identifier, so it never clashes with an input name
export const PROJECT_MIN = 2;
export const PROJECT_MAX = 120;
export const STARTERS = [
  { id: "workflow", label: "Trabajar en un workflow" },
  { id: "continuar", label: "Continuar una ejecución" },
  { id: "ayuda", label: "Ayuda" },
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
  return entry.bundle ? "Bundle «" + entry.bundle + "»" : "Incluido con el framework";
}

// The line a workflow card shows under its name.
export function cardDescription(entry) {
  if (entry.description) return entry.description;
  if (entry.unreadable) return "No se pudo leer este workflow; al elegirlo se verá el motivo.";
  return entry.described ? "Este workflow no trae descripción." : "Leyendo la descripción…";
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
  const parts = ["El workflow no describe esta entrada."];
  if (item.used_in && item.used_in.length) parts.push("Se usa en: " + item.used_in.join(", ") + ".");
  if (item.prompt_line) parts.push("En el prompt: «" + item.prompt_line + "».");
  return parts.join(" ");
}

const PROJECT_QUESTION = {
  key: PROJECT_KEY, source: "project", name: "project", label: "Proyecto", kind: "text", choices: [], required: true,
  description: "Nombre con el que se agrupa esta ejecución en la lista (mínimo 2 letras).",
  example: "Informe trimestral",
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
    if (value.length < PROJECT_MIN) return { error: "El nombre del proyecto necesita al menos " + PROJECT_MIN + " letras." };
    if (value.length > PROJECT_MAX) return { error: "El nombre del proyecto admite hasta " + PROJECT_MAX + " caracteres." };
    return { value };
  }
  if (!value) return question.required ? { error: "Esta entrada es obligatoria: escribe un valor." } : { value: "" };
  if (question.kind === "choice") {
    const hit = question.choices.find((choice) => normalize(choice) === normalize(value));
    return hit === undefined ? { error: "Elige una de las opciones: " + question.choices.join(", ") + "." } : { value: hit };
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
export const progress = (state) => ({ n: state.index + 1, total: state.questions.length });

function questionLine(state) {
  const question = state.questions[state.index];
  const { n, total } = progress(state);
  return "Pregunta " + n + " de " + total + ": " + question.label + (question.required ? "" : " (opcional)");
}

export const HELP_MESSAGE = "Puedo guiarte para lanzar un workflow o retomar una ejecución. Escribe en tus palabras lo que " +
  "necesitas, o usa los comandos: /workflow (muestra los workflows; con texto detrás, los que encajan), /continuar " +
  "(ejecuciones que puedes retomar) y /ayuda. Nada se ejecuta hasta que pulses «Lanzar».";

function helpMessage(state) {
  const question = currentQuestion(state);
  if (!question) return HELP_MESSAGE;
  return HELP_MESSAGE + "\nAhora estás en la " + questionLine(state).replace(/^P/, "p") + ". Responde en la caja de abajo" +
    (question.required ? "." : " o pulsa «Omitir».");
}

// Why the model was not used, for the line that tells the operator the words were compared instead.
const FALLBACK_REASONS = {
  disabled: "la interpretación con modelo está desactivada",
  no_workflows: "no hay workflows que ofrecerle al modelo",
  model_error: "el modelo no respondió",
  refused: "la política de salida del framework no dejó enviar el texto",
  invalid_output: "el modelo devolvió una respuesta que no encaja con los workflows",
  no_match: "el modelo no encontró un workflow que encaje",
};
export const fallbackReason = (reason) => FALLBACK_REASONS[reason] || "no se pudo usar el modelo";

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
    return { ...say(state, "assistant", "No hay workflows disponibles en este servidor. Puedes probar «Ver como formulario»."), phase: "choose", suggestions: [], source: "", proposal: null, showAll: true };
  }
  const proposed = usableProposal(interpretation, all);
  if (proposed) {
    const filled = Object.keys(proposed.inputs).length;
    const note = (proposed.explanation ? " " + proposed.explanation : "") +
      (filled ? " Rellené " + filled + (filled === 1 ? " entrada" : " entradas") + " con lo que escribiste; las revisarás antes de lanzar." : "");
    return { ...say(state, "assistant", "Interpretado con un modelo: propongo «" + proposed.entry.id + "»." + note + " Elígelo para continuar; nada se ejecuta hasta que pulses «Lanzar»."),
      phase: "choose", suggestions: [{ key: proposed.key, matched: [] }], source: SOURCE_MODEL, showAll: false,
      proposal: { key: proposed.key, inputs: proposed.inputs, project: proposed.project } };
  }
  const why = !interpretation ? "" : " (" + fallbackReason(interpretation.source === SOURCE_KEYWORDS ? interpretation.reason : "invalid_output") + ")";
  const found = matchWorkflows(text, all, { limit: MAX_SUGGESTIONS });
  const source = SOURCE_KEYWORDS;
  const how = interpretation ? "Comparé las palabras con los nombres y descripciones" + why + ". " : "";
  if (found.length === 0) {
    return { ...say(state, "assistant", how + "No encontré un workflow que encaje con eso. Estos son todos los disponibles: elige uno o prueba con otras palabras."),
      phase: "choose", suggestions: everyone(all), source: "", proposal: null, showAll: true };
  }
  return { ...say(state, "assistant", how + "Creo que " + (found.length === 1 ? "este workflow encaja" : "estos workflows encajan") +
    " con lo que escribiste. Elige uno o sigue describiendo lo que necesitas."),
  phase: "choose", suggestions: found.map((item) => ({ key: entryKey(item.entry), matched: item.matched })), source, proposal: null, showAll: false };
}

function listAll(state, catalog) {
  const all = sortCatalog(catalog || []);
  const message = all.length === 0
    ? "No hay workflows disponibles en este servidor. Puedes probar «Ver como formulario»."
    : "Estos son los workflows disponibles. Elige uno o descríbeme lo que necesitas.";
  return { ...say(state, "assistant", message), phase: "choose", source: "", suggestions: everyone(all), showAll: true };
}

function onCommand(state, action) {
  const echoed = say(state, "user", action.echo || "/" + action.name + (action.rest ? " " + action.rest : ""));
  if (action.name === "ayuda") return say(echoed, "assistant", helpMessage(state));
  const fresh = clearWorkflow(echoed);
  if (action.name === "continuar") return { ...say(fresh, "assistant", "Estas son las ejecuciones que puedes retomar."), phase: "continue", suggestions: [], source: "" };
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
  return say({ ...next, phase: "confirm" }, "assistant", "Revisa el resumen y lánzalo cuando quieras. Nada se ejecuta hasta que pulses «Lanzar».");
}

function onAnswer(state, text) {
  const question = currentQuestion(state);
  if (!question) return state;
  const checked = validateAnswer(question, text);
  if (checked.error) return { ...state, error: checked.error };
  return advance(state, checked.value, checked.value || "(sin respuesta)");
}

function onSkip(state) {
  const question = currentQuestion(state);
  if (!question || question.required) return state;
  return advance(state, "", "(omitida)");
}

function onBack(state) {
  if (state.phase !== "ask" || state.index === 0) return state;
  const back = { ...state, index: state.index - 1, pendingPrefill: [], error: "" };
  return say(back, "assistant", "Volvemos a la " + questionLine(back).replace(/^P/, "p") + ". Tu respuesta anterior está en la caja.");
}

function onEdit(state) {
  if (state.phase !== "confirm") return state;
  const back = { ...state, phase: "ask", index: 0, pendingPrefill: [], error: "" };
  return say(say(back, "assistant", "Repasemos tus respuestas. Cada una aparece en la caja: pulsa Intro para conservarla o escribe otra."), "assistant", questionLine(back));
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
  const next = say(say(state, "user", "Elijo «" + workflow.id + "»"), "assistant",
    prefilled ? "Perfecto. Usé la propuesta del modelo para " + prefilled + (prefilled === 1 ? " respuesta" : " respuestas") + "; compruébalas." : "Perfecto. Te pregunto lo necesario, una cosa a la vez.");
  const first = questions.findIndex((question) => question.required && !answers[question.key]);
  const started = {
    ...next, phase: first === -1 && prefilled ? "confirm" : "ask", index: first === -1 ? 0 : first, error: "", answers,
    pendingPrefill: Object.keys(answers),
    workflow: { id: workflow.id, bundle: workflow.bundle || null, description: spec.description || workflow.description || "" },
    questions, inputNames: (spec.inputs || []).map((item) => item.name),
  };
  if (started.phase === "confirm") return say(started, "assistant", "Revisa el resumen y lánzalo cuando quieras. Nada se ejecuta hasta que pulses «Lanzar».");
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
        return say(say(state, "user", text), "assistant",
          "Para seguir usa los botones del resumen: «Lanzar», «Editar» o «Ver como formulario». Escribe /workflow para empezar con otro workflow.");
      }
      return suggest(clearWorkflow(say(state, "user", text)), text, action.catalog, action.interpretation);
    }
    case "command": return onCommand(state, action);
    case "pick": return onPick(state, action);
    case "show_all": {
      const shown = say(state, "assistant", "Estos son todos los workflows disponibles.");
      return { ...shown, phase: "choose", source: "", proposal: null, suggestions: everyone(action.catalog), showAll: true };
    }
    case "skip": return onSkip(state);
    case "back": return onBack(state);
    case "edit": return onEdit(state);
    case "launched": return say({ ...state, phase: "launched" }, "assistant", "La ejecución está creada. Te llevo a su detalle.");
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
export async function loadCatalog({ get, onUpdate = () => {}, concurrency = 4 }) {
  const bundles = await get("/gui/api/bundles");
  const origins = [null].concat((bundles || []).map((item) => item.name));
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
