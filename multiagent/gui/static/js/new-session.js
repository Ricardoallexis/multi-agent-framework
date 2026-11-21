// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// "Nueva sesión" (GUI2-T03): a guided chat in place of the plain form. A greeting with three starters, workflow cards for
// free text, one question at a time (the inputs the workflows declare), and a summary card whose «Lanzar» button is the
// only thing that creates a run (the same POST /gui/api/runs the form uses). «Ver como formulario» opens the existing
// form (create-run.js) with the answers already in place. What happens next is decided by new-session-model.js (pure);
// this file reads the workflows, draws the conversation and calls the API. Every message is rendered as text.
// Free text goes to POST /gui/api/new-session/interpret (a model on the server proposes a workflow and inputs, GUI2-T17);
// without a valid proposal the words are compared with names and descriptions, and the chat says which one it used.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "./api.js";
import { CreateRun, PreviewPanel } from "./create-run.js";
import { isRegisteredView } from "./extensions.js";
import { Help, helpFor } from "./help.js";
import { modeHelpEntry } from "./help-text.js";
import { ModelPicker } from "./model-picker.js";
import { buildModelPicker } from "./model-picker-model.js";
import {
  SOURCE_KEYWORDS, SOURCE_MODEL, STARTERS, SECRET_REVIEW_NOTICE, SECRET_REVIEW_REASON, answerRows, buildLimitNotice,
  cardDescription, connectionLabel, createConversation, createSecretReview, currentQuestion, executorLaunchBody, formSeed, inputsPath,
  interpretBody, isSecretReviewRequired, loadCatalog, originLabel, parseCommand, pickRuns, progress,
  quotaRemainingLines, reduce, runBody, secretResendRequest, selectOrchestratorSuggestion,
  selectSingleExecutorSuggestion, selectSingleModelSuggestion, singleSessionBody, singleSessionModels, suggestedEntries,
} from "./new-session-model.js";
import { navigate, routeHref, threadHref, useRoute } from "./route.js";
import { RunThread } from "./thread.js";
import { MODE_LABELS, errorText, exampleText, fmtDate, statusInfo } from "./text.js";

const MODES = Object.keys(MODE_LABELS);
const FREE_PLACEHOLDER = "Ej.: quiero resumir un documento";
const SINGLE_PREVIEW_PATH = "/gui/api/runs/preview";
const SINGLE_RUN_PATH = "/gui/api/runs";
const EXECUTOR_LAUNCH_PATH = "/gui/api/executors/launch";
const EXECUTOR_STATUS_PATH = "/gui/api/executors/launch/";

const newKey = () => (globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
// A touch screen does not get the soft keyboard pushed up just by opening the screen.
const finePointer = () => { try { return window.matchMedia("(pointer: fine)").matches; } catch (_) { return true; } };
const prefillOf = (state) => { const question = currentQuestion(state); return question ? state.answers[question.key] || "" : ""; };

// SEC-T04: keep held secret-review state in memory while the user visits #approvals/<id> and returns to #new.
let heldSecretState = null;
if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("multiagent:approval-decided", (event) => {
    const detail = event && event.detail ? event.detail : {};
    if (heldSecretState && heldSecretState.pendingSecret && heldSecretState.pendingSecret.approvalId === detail.id) {
      heldSecretState = { ...heldSecretState, decision: detail.decision || "" };
    }
  });
}

function Messages({ messages }) {
  return html`<div class="ns-thread" role="log" aria-label="Conversación">
    ${messages.map((message) => html`<div key=${message.id} class=${"ns-msg " + (message.role === "user" ? "ns-user" : "ns-bot")}>
      <span class="sr-only">${message.role === "user" ? "Tú: " : "Asistente: "}</span>${message.text}</div>`)}
  </div>`;
}

function Starters({ disabled, onPick }) {
  return html`<div class="ns-starters">
    <div class="ns-chips" role="group" aria-label="Opciones de inicio">
      ${STARTERS.map((starter) => html`<button key=${starter.id} type="button" class="ns-chip" disabled=${disabled}
        ...${helpFor("chat.starters")} onClick=${() => onPick(starter)}>${starter.label}</button>`)}
    </div>
    <${Help} topic="chat.starters" compact=${true} />
  </div>`;
}

function Cards({ items, source, disabled, canShowAll, onPick, onShowAll }) {
  return html`<div class="ns-suggest">
    <div class="ns-cards" role="group" aria-label="Workflows">
      ${items.map(({ entry, matched }) => html`<button key=${entry.bundle + "|" + entry.id} type="button" class="ns-card" disabled=${disabled}
        ...${helpFor("chat.suggestion")} onClick=${() => onPick(entry)}>
        <span class="ns-card-name">${entry.id}</span>
        <span class="ns-card-desc">${cardDescription(entry)}</span>
        <span class="ns-card-meta">Origen: ${originLabel(entry)}</span>
        ${source === SOURCE_MODEL && html`<span class="ns-card-meta">Propuesto por: un modelo</span>`}
        ${source === SOURCE_KEYWORDS && html`<span class="ns-card-meta">Propuesto por: comparación de palabras</span>`}
        ${matched.length > 0 && html`<span class="ns-card-meta">Coincide con: ${matched.join(", ")}</span>`}
      </button>`)}
    </div>
    <div class="ns-row">
      <${Help} topic="chat.suggestion" compact=${true} />
      ${canShowAll && html`<button type="button" class="btn secondary" disabled=${disabled} ...${helpFor("chat.all")} onClick=${onShowAll}>Ver todos los workflows</button>
        <${Help} topic="chat.all" compact=${true} />`}
    </div>
  </div>`;
}

function RunsList({ runs }) {
  if (runs === null) return html`<p class="muted" aria-busy="true">Cargando ejecuciones…</p>`;
  if (runs.error) return html`<p class="message error" role="alert">${errorText(runs.error)}</p>`;
  return html`<div class="ns-suggest">
    <p class="muted">${runs.runs.length === 0
      ? "Todavía no hay ejecuciones. Elige «Trabajar en un workflow» para crear la primera."
      : runs.resumable ? "Pulsa una para abrir su hilo y seguir donde quedó." : "No hay ejecuciones por retomar; estas son las más recientes."}</p>
    <ul class="ns-run-list">
      ${runs.runs.map((run) => {
        const info = statusInfo(run.status);
        return html`<li key=${run.id}><a class="ns-card ns-run" href=${threadHref(run.id)} ...${helpFor("chat.runs")}>
          <span class="ns-card-name">${run.workflow_id} <span class="muted">· ${run.project_name}</span></span>
          <span class=${"badge tone-" + info.tone}>${info.label}</span>
          <span class="ns-card-meta">${fmtDate(run.created_at)}${run.bundle ? " · " + run.bundle : ""}</span>
        </a></li>`;
      })}
    </ul>
    <${Help} topic="chat.runs" compact=${true} />
  </div>`;
}

function QuestionCard({ question, n, total, disabled, onChoose }) {
  return html`<section class="ns-question" aria-label="Pregunta actual">
    <p class="ns-q-count">Pregunta ${n} de ${total} · ${question.required ? "obligatoria" : "opcional"}</p>
    <p class="ns-q-label">${question.label}</p>
    <p id="ns-q-desc" class="ns-q-desc">${question.description}</p>
    ${question.example && html`<p id="ns-q-example" class="ns-q-example">Ejemplo: <span class="ns-example">${question.example}</span></p>`}
    ${question.kind === "choice" && html`<div class="ns-chips" role="group" aria-label="Opciones">
      ${question.choices.map((choice) => html`<button key=${choice} type="button" class="ns-chip" disabled=${disabled}
        ...${helpFor("chat.choice")} onClick=${() => onChoose(choice)}>${choice}</button>`)}
    </div>
    <${Help} topic="chat.choice" compact=${true} />`}
    <${Help} topic="chat.question" compact=${true} />
  </section>`;
}

function ConfirmCard({ conv, mock, state, busy, onLaunch, onEdit, onForm, boxRef }) {
  const { mode, setMode, sensitive, setSensitive, confirmReal, setConfirmReal, preview, previewError, previewing,
    routing, routingError, routingLoading, launchError, routingOverrides, setRoutingOverrides } = state;
  const blockers = [];
  if (!mock && !confirmReal) blockers.push("Confirma que entiendes que el servidor no está en modo Mock.");
  const routingSteps = routing && Array.isArray(routing.steps) ? routing.steps : [];
  const changedRoutes = Object.entries(routingOverrides).filter(([, override]) => Object.keys(override).length > 0);
  if (previewing) blockers.push("Espera a que se validen las preferencias.");
  if (previewError) blockers.push("Corrige las preferencias o las entradas indicadas por la vista previa.");
  const setRouteOverride = (step, field, value) => {
    setRoutingOverrides((current) => {
      const next = { ...current };
      const override = { ...(next[step.step_id] || {}) };
      if (!value) delete override[field];
      else override[field] = value;
      if (Object.keys(override).length) next[step.step_id] = override;
      else delete next[step.step_id];
      return next;
    });
  };
  return html`<section class="ns-confirm panel" aria-label="Resumen antes de lanzar" tabindex="-1" ref=${boxRef}>
    <p class="ns-confirm-title"><strong>Listo para lanzar: ${conv.workflow.id}</strong> <span class="muted">· ${originLabel(conv.workflow)}</span></p>
    <${Help} topic="chat.confirm" />
    <dl class="ns-answers">
      ${answerRows(conv).map((row) => html`<div key=${row.key} class="ns-answer"><dt>${row.label}</dt>
        <dd>${row.empty ? html`<span class="muted">(sin respuesta)</span>` : row.value}</dd></div>`)}
    </dl>
    <section class="ns-routing" aria-label="Enrutamiento del workflow">
      <div class="ns-row"><strong>Modelo y esfuerzo previstos</strong><${Help} topic="chat.routing" compact=${true} /></div>
      ${routingLoading && html`<p class="muted small" role="status">Leyendo el perfil y el modelo previstos…</p>`}
      ${routingError && html`<p class="message error" role="alert">${errorText(routingError)}</p>`}
      ${routing && routingSteps.length === 0 && html`<p class="muted small">El servidor no devolvió pasos de routing; se muestran los prompts disponibles.</p>`}
      ${routingSteps.map((step) => html`<div key=${step.step_id} class="ns-routing-step">
        <span class="ns-card-name">${step.step_id}</span>
        ${step.status === "function" ? html`<p class="hint">Función ${step.function}; se ejecuta sin modelo.</p>` : html`
        <div class="ns-chips" role="group" aria-label=${"Ajustes del paso " + step.step_id}>
          ${[["mode", "Modo", step.mode_options || []], ["profile_id", "Perfil", step.profile_options || []],
             ["level", "Nivel", step.level_options || []]].map(([field, label, options]) => html`
            <label class="ns-route-chip" key=${field}>${label}
              <select class="ns-chip" aria-label=${label + " del paso " + step.step_id}
                disabled=${busy || routingLoading || options.length === 0 || (field !== "profile_id" && !step.profile_id)} ...${helpFor("chat.routing")}
                value=${(routingOverrides[step.step_id] || {})[field] || ""}
                onChange=${(event) => setRouteOverride(step, field, event.target.value)}>
                <option value="">Según workflow${step[field] ? " (" + step[field] + ")" : ""}</option>
                ${options.map((value) => html`<option key=${value} value=${value}>${field === "mode"
                  ? ({ supervised: "Supervisado", automatic: "Automático", single: "Único" }[value] || value) : value}</option>`)}
              </select>
            </label>`)}
          <label class="ns-route-chip">Modelo
            <select class="ns-chip" aria-label=${"Modelo del paso " + step.step_id} disabled=${busy || routingLoading ||
              !step.profile_id || !Array.isArray(step.model_options) || step.model_options.length === 0}
              ...${helpFor("chat.routing")} value=${routingOverrides[step.step_id] &&
                Object.prototype.hasOwnProperty.call(routingOverrides[step.step_id], "model")
                ? routingOverrides[step.step_id].model || "" : step.model || ""}
              onChange=${(event) => setRouteOverride(step, "model", event.target.value)}>
              <option value="">Predeterminado${step.model ? " (" + step.model + ")" : ""}</option>
              ${(step.model_options || []).map((model) => html`<option key=${model} value=${model}>${model}</option>`)}
            </select>
          </label>
          <label class="ns-route-chip">Esfuerzo
            <select class="ns-chip" aria-label=${"Esfuerzo del paso " + step.step_id} disabled=${busy || routingLoading || !step.profile_id}
              ...${helpFor("chat.routing")} value=${routingOverrides[step.step_id] &&
                Object.prototype.hasOwnProperty.call(routingOverrides[step.step_id], "effort")
                ? routingOverrides[step.step_id].effort || "" : step.effort || ""}
              onChange=${(event) => setRouteOverride(step, "effort", event.target.value)}>
              <option value="">Predeterminado</option>
              ${(step.effort_options || []).map((effort) => html`<option key=${effort} value=${effort}>${effort}</option>`)}
            </select>
          </label>
        </div>
        `}
        ${step.status !== "function" && step.status !== "selected" && step.status !== "catalog_default" && html`<p class="hint">El router informó: ${(step.reason_codes || []).join(", ") || step.status}.</p>`}
      </div>`)}
    </section>
    <div class="two">
      <div><label for="ns-mode">Cómo se ejecuta</label>
        <${Help} topic="create.mode" entry=${modeHelpEntry(mode)} />
        <select id="ns-mode" ...${helpFor("create.mode")} value=${mode} disabled=${busy || changedRoutes.length > 0} onChange=${(event) => setMode(event.target.value)}>
          ${MODES.map((value) => html`<option key=${value} value=${value}>${MODE_LABELS[value]}</option>`)}
        </select></div>
      <div class="check"><label><input type="checkbox" checked=${sensitive} disabled=${busy || changedRoutes.length > 0} ...${helpFor("create.sensitive")}
        onChange=${(event) => setSensitive(event.target.checked)} /> Contenido sensible (no se enviará a la nube)</label>
        <${Help} topic="create.sensitive" /></div>
    </div>
    ${!mock && html`<div class="notice check"><label><input type="checkbox" checked=${confirmReal} disabled=${busy} ...${helpFor("create.confirm_real")}
      onChange=${(event) => setConfirmReal(event.target.checked)} /> Entiendo que el servidor no está en modo Mock y esta ejecución puede usar modelos reales.</label>
      <${Help} topic="create.confirm_real" /></div>`}
    <${PreviewPanel} preview=${preview} error=${previewError} loading=${previewing} />
    ${launchError && html`<p class="message error" role="alert">${errorText(launchError)}</p>`}
    <p id="ns-blockers" class="hint" role="status">${blockers.length ? "No se puede lanzar todavía. " + blockers.join(" ") : "Todo listo: nada se ejecuta hasta que pulses «Lanzar»."}</p>
    ${changedRoutes.length > 0 && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.routing")}
      onClick=${() => setRoutingOverrides({})}>Revertir ajustes de routing</button>`}
    <div class="actions">
      <button type="button" class="btn" disabled=${busy || blockers.length > 0} ...${helpFor("chat.launch", { also: "ns-blockers" })}
        onClick=${onLaunch}>${busy ? "Lanzando…" : "Lanzar"}</button>
      <${Help} topic="chat.launch" compact=${true} />
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.edit")} onClick=${onEdit}>Editar</button>
      <${Help} topic="chat.edit" compact=${true} />
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.form")} onClick=${onForm}>Ver como formulario</button>
      <${Help} topic="chat.form" compact=${true} />
    </div>
  </section>`;
}

// CUO-T06: limit notice with local reset time (or unknown) and up to 3 selection-only suggestion buttons.
function LimitBanner({ notice, connections, busy, onSelectSuggestion }) {
  const view = notice && notice.text ? notice : buildLimitNotice(notice, connections);
  if (!view) return null;
  return html`<div class="ns-msg ns-bot notice" role="status" aria-label="Límite alcanzado">
    <p>${view.text}</p>
    <${Help} topic="chat.limit_notice" compact=${true} />
    ${view.suggestions.length > 0 && html`<div class="ns-chips" role="group" aria-label="Alternativas disponibles">
      ${view.suggestions.map((item) => html`<button key=${item.id} type="button" class="ns-chip" disabled=${busy}
        ...${helpFor("chat.limit_suggestion")} onClick=${() => onSelectSuggestion(item)}>${item.label}</button>`)}
    </div>
    <${Help} topic="chat.limit_suggestion" compact=${true} />`}
  </div>`;
}

// SES-T03: one task, one catalog model, then the same run thread as every workflow.
function SingleSession({
  token, mock, connections, onBack, initialTask = "", initialProvider = "", onTaskChange = null, onSwitchExecutor = null,
}) {
  const [task, setTask] = useState(initialTask);
  const [model, setModel] = useState("");
  const [catalogPreview, setCatalogPreview] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmReal, setConfirmReal] = useState(false);
  const [confirmCloud, setConfirmCloud] = useState(false);
  const [ready, setReady] = useState(false);
  const attempt = useRef({ fingerprint: "", key: "" });
  useEffect(() => {
    let active = true;
    apiPost(SINGLE_PREVIEW_PATH, token, {
      workflow_id: "ask_once", bundle: "general", inputs: { task: "Vista previa" }, execution_mode: "auto",
    }).then((result) => { if (active) setCatalogPreview(result.routing); },
      (failure) => { if (active) setError(asError(failure)); });
    return () => { active = false; };
  }, [token]);
  const choices = singleSessionModels(connections, catalogPreview);
  useEffect(() => {
    if (!initialProvider || model) return;
    const hit = choices.find((choice) => choice.provider === initialProvider && choice.models.length > 0);
    if (hit) setModel(hit.models[0].id);
  }, [initialProvider, choices, model]);
  const cloudSelected = choices.some((choice) => choice.provider !== "ollama" &&
    choice.models.some((item) => item.id === model));
  const selected = choices.some((choice) => choice.models.some((item) => item.id === model));
  const activeLimit = error?.details?.limit || choices.find((choice) => choice.limit)?.limit || null;
  const updateTask = (value) => {
    setTask(value);
    if (onTaskChange) onTaskChange(value);
  };
  const selectSuggestion = (item) => {
    const next = selectSingleModelSuggestion(item, choices, task);
    if (next.target === "single-executor") {
      if (onSwitchExecutor) onSwitchExecutor(next.executorId, next.task);
      return;
    }
    if (next.model) {
      setModel(next.model);
      setConfirmCloud(false);
      edit();
    }
  };
  const check = async () => {
    if (!task.trim() || !selected || busy) return;
    setBusy(true); setError(null);
    try {
      const body = singleSessionBody(task, model);
      const { project_name, ...previewBody } = body;
      const result = await apiPost(SINGLE_PREVIEW_PATH, token, previewBody);
      setPreview(result); setReady(true);
    } catch (failure) { setError(asError(failure)); }
    finally { setBusy(false); }
  };
  const launch = async () => {
    if (!ready || !preview || !selected || busy || (!mock && !confirmReal) || (cloudSelected && !confirmCloud)) return;
    setBusy(true); setError(null);
    try {
      const body = singleSessionBody(task, model, cloudSelected && confirmCloud);
      const fingerprint = JSON.stringify(body);
      if (attempt.current.fingerprint !== fingerprint) attempt.current = { fingerprint, key: newKey() };
      const run = await apiPost(SINGLE_RUN_PATH, token, {
        ...body, idempotency_key: attempt.current.key,
      });
      attempt.current = { fingerprint: "", key: "" };
      location.hash = threadHref(run.id);
    } catch (failure) { setError(asError(failure)); setBusy(false); }
  };
  const edit = () => { setReady(false); setPreview(null); setError(null); };
  return html`<div class="ns">
    <div class="ns-intro"><h3 class="ns-title">Sesión única</h3>
      <p class="ns-lead">Describe una tarea sencilla y elige un modelo del paquete general.</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.session_mode")} onClick=${onBack}>Cambiar modo</button>
      <${Help} topic="chat.session_mode" compact=${true} /></div>
    <section class="panel" aria-label="Modelo de la sesión única">
      <strong>Modelo</strong><${Help} topic="chat.single_model" compact=${true} />
      ${!catalogPreview && !error && html`<p role="status">Leyendo modelos del paquete…</p>`}
      ${choices.map((choice) => html`<div key=${choice.provider} class="ns-row">
        ${choice.models.map((item) => html`<label key=${item.id} class="ns-picker-choice">
          <input type="radio" name="single-model" value=${item.id} checked=${model === item.id}
            ...${helpFor("chat.single_model")} onChange=${() => { setModel(item.id); setConfirmCloud(false); edit(); }} />
          <span>${connectionLabel(choice.provider)} · ${item.id}${choice.status ? " · " + choice.status : ""}
            ${choice.quotaNote && html`<span class="muted small"> · ${choice.quotaNote}</span>`}
          </span></label>`)}
        ${choice.reason && html`<p class="muted small">${connectionLabel(choice.provider)}: ${choice.reason}</p>`}
      </div>`)}
      ${catalogPreview && choices.length === 0 && html`<p role="status">No hay proveedores de modelos conectados.</p>`}
      <p class="muted small ns-row">Estado de Gemini y Copilot <${Help} topic="chat.connection_checks" compact=${true} /></p>
    </section>
    <${LimitBanner} notice=${activeLimit} connections=${connections} busy=${busy} onSelectSuggestion=${selectSuggestion} />
    <div class="ns-composer"><label for="single-task">Tarea</label><${Help} topic="chat.single_task" compact=${true} />
      <textarea id="single-task" rows="5" value=${task} disabled=${ready || busy}
        ...${helpFor("chat.single_task")} onInput=${(event) => { updateTask(event.target.value); edit(); }}></textarea></div>
    ${error && html`<p class="message error" role="alert">${errorText(error)}</p>`}
    ${ready && html`<section class="ns-confirm panel" aria-label="Resumen antes de lanzar">
      <p><strong>Modelo:</strong> ${model}</p><p><strong>Tarea:</strong> ${task}</p>
      <${PreviewPanel} preview=${preview} error=${null} loading=${false} />
      ${cloudSelected && html`<label class="check"><input type="checkbox" checked=${confirmCloud}
        ...${helpFor("chat.cloud_confirm")} onChange=${(event) => setConfirmCloud(event.target.checked)} />
        Confirmo enviar esta tarea al modelo en la nube.</label><${Help} topic="chat.cloud_confirm" compact=${true} />`}
      ${!mock && html`<label class="check"><input type="checkbox" checked=${confirmReal}
        ...${helpFor("create.confirm_real")} onChange=${(event) => setConfirmReal(event.target.checked)} />
        Entiendo que esta ejecución puede usar modelos reales.</label>`}
      <button type="button" class="btn" disabled=${busy || !selected || (!mock && !confirmReal) || (cloudSelected && !confirmCloud)}
        ...${helpFor("chat.launch")} onClick=${launch}>${busy ? "Lanzando…" : "Lanzar"}</button>
      <${Help} topic="chat.launch" compact=${true} />
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.edit")} onClick=${edit}>Editar</button>
      <${Help} topic="chat.edit" compact=${true} />
    </section>`}
    ${!ready && html`<button type="button" class="btn" disabled=${busy || !task.trim() || !selected}
      ...${helpFor("chat.single_task")} onClick=${check}>Revisar y lanzar</button>`}
  </div>`;
}

// SES-T04: an executor is chosen and reviewed separately from the model-only ask_once flow.
function SingleExecutorSession({
  token, connections, connectionLoading, connectionError, onRetry, onBack,
  initialTask = "", initialExecutorId = "", onTaskChange = null, onSwitchModel = null,
}) {
  const [task, setTask] = useState(initialTask);
  const [executorId, setExecutorId] = useState(initialExecutorId);
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [composer, setComposer] = useState({ options: [], loading: true, error: null });
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [launchError, setLaunchError] = useState(null);
  const [limitNotice, setLimitNotice] = useState(null);
  const [launched, setLaunched] = useState(null);
  const [tracking, setTracking] = useState({ data: null, error: null });

  useEffect(() => {
    let active = true;
    setComposer({ options: [], loading: true, error: null });
    apiGet("/gui/api/composer/executors", token).then(
      (data) => { if (active) setComposer({ options: data.executors || [], loading: false, error: null }); },
      (failure) => { if (active) setComposer({ options: [], loading: false, error: asError(failure) }); },
    );
    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    if (!executorId || model) return;
    const option = composer.options.find((item) => item.executor_id === executorId);
    if (option) {
      setModel(option.models?.[0] || "");
      setEffort(option.efforts?.[0] || "");
    }
  }, [executorId, composer.options, model]);

  const taskState = tracking.data?.task?.state;
  useEffect(() => {
    if (!launched || ["submitted", "accepted", "failed", "cancelled"].includes(taskState)) return undefined;
    let active = true;
    const refresh = () => apiGet(EXECUTOR_STATUS_PATH + encodeURIComponent(launched.task_id), token).then(
      (data) => { if (active) setTracking({ data, error: null }); },
      (failure) => { if (active) setTracking({ data: null, error: asError(failure) }); },
    );
    refresh();
    const timer = setInterval(refresh, 4000);   // as the run thread: the GUI never polls every 2 s or faster
    return () => { active = false; clearInterval(timer); };
  }, [launched, token, taskState]);

  const picker = buildModelPicker(connections);
  const choices = picker.executors.concat(picker.disconnected.filter((item) => item.kind === "executor"));
  const selected = choices.find((item) => item.id === executorId) || null;
  const selectedOption = composer.options.find((item) => item.executor_id === executorId) || null;
  const selectedConnection = (connections?.connections || []).find((item) => item.id === executorId) || null;
  const selectedAvailable = Boolean(selected && selected.selectable && selectedOption && selectedOption.available);
  const models = selectedOption?.models || [];
  const efforts = selectedOption?.efforts || [];
  const canReview = Boolean(task.trim() && selectedAvailable && models.includes(model) &&
    (!efforts.length || efforts.includes(effort)) && !busy);

  const updateTask = (value) => {
    setTask(value);
    if (onTaskChange) onTaskChange(value);
  };

  function chooseExecutor(id) {
    const option = composer.options.find((item) => item.executor_id === id);
    setExecutorId(id);
    setModel(option?.models?.[0] || "");
    setEffort(option?.efforts?.[0] || "");
    setReady(false);
    setLaunchError(null);
    setLimitNotice(null);
  }

  function selectSuggestion(item) {
    const next = selectSingleExecutorSuggestion(item, task);
    if (next.target === "single") {
      if (onSwitchModel) onSwitchModel(next.provider, next.task);
      return;
    }
    if (next.executorId) chooseExecutor(next.executorId);
  }

  function edit() {
    setReady(false);
    setLaunchError(null);
    setLimitNotice(null);
  }

  async function launch() {
    if (!ready || !canReview || busy) return;
    setBusy(true);
    setLaunchError(null);
    setLimitNotice(null);
    try {
      const acceptWarning = executorId === "antigravity_cli" && Boolean(selectedConnection?.warning);
      const result = await apiPost(
        EXECUTOR_LAUNCH_PATH,
        token,
        executorLaunchBody(task, executorId, model, effort, acceptWarning),
      );
      setTracking({ data: result.status, error: null });
      if (result.limit) setLimitNotice(buildLimitNotice(result.limit, connections));
      setLaunched({ task_id: result.task_id, launch: result.launch });
      setReady(false);
    } catch (failure) {
      const err = asError(failure);
      setLaunchError(err);
      if (err.details?.limit) setLimitNotice(buildLimitNotice(err.details.limit, connections));
    } finally { setBusy(false); }
  }

  const executorLabels = {
    claude_cli: "Claude", codex_cli: "Codex", copilot_cli: "Copilot", antigravity_cli: "Antigravity (agy)",
  };
  const sessions = tracking.data?.task?.sessions || [];
  const attempts = tracking.data?.task?.attempts || [];
  const latestAttempt = attempts[attempts.length - 1] || null;
  const sessionLinks = {
    claude_cli: "claude", codex_cli: "codex", copilot_cli: "copilot", antigravity_cli: "antigravity",
  };
  const activeLimit = limitNotice || (selectedConnection?.limit ? buildLimitNotice(selectedConnection.limit, connections) : null);

  return html`<div class="ns">
    <div class="ns-intro">
      <h3 class="ns-title">Sesión única con ejecutor</h3>
      <p class="ns-lead">Describe la tarea, elige un ejecutor y revisa cuota y ajustes antes de aprobar el lanzamiento.</p>
      ${!launched && html`<button type="button" class="btn secondary" ...${helpFor("chat.executor_launch")}
        onClick=${onBack}>Volver a Sesión única con modelo</button>
        <${Help} topic="chat.executor_launch" compact=${true} />`}
    </div>
    ${connectionError && html`<div class="ns-row"><p class="message error" role="alert">${errorText(connectionError)}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.executor_launch")} onClick=${onRetry}>Reintentar conexiones</button>
      <${Help} topic="chat.executor_launch" compact=${true} /></div>`}
    ${connectionLoading && html`<p class="muted small" role="status">Comprobando conexiones de ejecutores…</p>`}
    ${composer.loading && html`<p class="muted small" role="status">Leyendo los modelos y esfuerzos admitidos…</p>`}
    ${composer.error && html`<p class="message error" role="alert">${errorText(composer.error)}</p>`}
    ${!launched && html`
      <section class="ns-executor-picker panel" aria-label="Ejecutores conectados">
        <strong>Ejecutor</strong><${Help} topic="chat.executor_launch" compact=${true} />
        ${choices.length === 0 && !connectionLoading && html`<p class="muted small" role="status">No hay ejecutores conectados.</p>`}
        ${choices.map((item) => {
          const option = composer.options.find((candidate) => candidate.executor_id === item.id);
          const selectable = item.selectable && option?.available === true;
          const reason = !item.selectable ? item.reason : !option
            ? "No hay opciones de modelo admitidas para este ejecutor."
            : !option.available ? "No hay opciones de modelo y esfuerzo disponibles." : null;
          const connection = (connections?.connections || []).find((candidate) => candidate.id === item.id);
          return html`<label key=${item.id} class=${"ns-executor-choice" + (selectable ? "" : " ns-picker-unavailable")}>
            <input type="radio" name="single-executor" value=${item.id} checked=${executorId === item.id}
              disabled=${busy || !selectable} ...${helpFor("chat.executor_launch")} onChange=${() => chooseExecutor(item.id)} />
            <span class="ns-executor-facts"><strong>${item.label}</strong>
              ${reason && html`<span class="ns-picker-reason">${reason}</span>`}
              ${quotaRemainingLines(connection?.quota).map((line) => html`<span class="muted small" key=${line}>${line}</span>`)}
              ${item.warning && html`<span class="ns-picker-notice">${item.warning}</span>`}
              ${item.agyNotice && html`<span class="ns-picker-notice">${item.agyNotice}</span>`}
            </span>
          </label>`;
        })}
      </section>
      <div class="ns-executor-fields">
        <label>Modelo
          <select value=${model} disabled=${busy || !selectedAvailable || models.length === 0}
            ...${helpFor("chat.executor_launch")}
            onChange=${(event) => { setModel(event.target.value); edit(); }}>
            <option value="">Elige un modelo</option>
            ${models.map((value) => html`<option key=${value} value=${value}>${value}</option>`)}
          </select>
          <${Help} topic="chat.executor_launch" compact=${true} />
        </label>
        ${efforts.length > 0 && html`<label>Esfuerzo
          <select value=${effort} disabled=${busy || !selectedAvailable}
            ...${helpFor("chat.executor_launch")}
            onChange=${(event) => { setEffort(event.target.value); edit(); }}>
            <option value="">Elige un esfuerzo</option>
            ${efforts.map((value) => html`<option key=${value} value=${value}>${value}</option>`)}
          </select>
          <${Help} topic="chat.executor_launch" compact=${true} />
        </label>`}
      </div>
      <div class="ns-composer"><label for="single-executor-task">Tarea</label>
        <${Help} topic="chat.executor_launch" compact=${true} />
        <textarea id="single-executor-task" rows="5" maxlength="4000" value=${task} disabled=${ready || busy}
          ...${helpFor("chat.executor_launch")} onInput=${(event) => { updateTask(event.target.value); edit(); }}></textarea>
      </div>
      ${launchError && html`<p class="message error" role="alert">${errorText(launchError)}</p>`}
      <${LimitBanner} notice=${activeLimit} connections=${connections} busy=${busy} onSelectSuggestion=${selectSuggestion} />
      ${ready && html`<section class="ns-confirm panel" aria-label="Confirmación de lanzamiento">
        <h4>Confirma este lanzamiento</h4>
        <dl class="ns-answers">
          <div class="ns-answer"><dt>Ejecutor</dt><dd>${selected?.label || executorLabels[executorId] || executorId}</dd></div>
          <div class="ns-answer"><dt>Modelo</dt><dd>${model}</dd></div>
          <div class="ns-answer"><dt>Esfuerzo</dt><dd>${effort || "Predeterminado"}</dd></div>
          <div class="ns-answer"><dt>Cuota restante</dt><dd>${quotaRemainingLines(selectedConnection?.quota).join(" · ")}</dd></div>
          <div class="ns-answer"><dt>Tarea</dt><dd>${task}</dd></div>
        </dl>
        ${selected?.warning && html`<p class="notice ns-picker-notice" role="status">${selected.warning}</p>`}
        ${selected?.agyNotice && html`<p class="notice ns-picker-notice">${selected.agyNotice}</p>`}
        <p class="hint" role="status">Nada se lanza hasta que pulses «Aprobar y lanzar».</p>
        <button type="button" class="btn" disabled=${busy || !canReview}
          ...${helpFor("chat.executor_launch")} onClick=${launch}>${busy ? "Lanzando…" : "Aprobar y lanzar"}</button>
        <${Help} topic="chat.executor_launch" compact=${true} />
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.executor_launch")} onClick=${edit}>Editar</button>
        <${Help} topic="chat.executor_launch" compact=${true} />
      </section>`}
      ${!ready && html`<button type="button" class="btn" disabled=${!canReview}
        ...${helpFor("chat.executor_launch")} onClick=${() => setReady(true)}>Revisar lanzamiento</button>
        <${Help} topic="chat.executor_launch" compact=${true} />`}
    `}
    ${launched && html`<section class="ns-executor-tracking panel" aria-label="Seguimiento del ejecutor">
      <h4>Seguimiento de la tarea</h4>
      ${tracking.error && html`<p class="message error" role="alert">${errorText(tracking.error)}</p>`}
      ${activeLimit && html`<${LimitBanner} notice=${activeLimit} connections=${connections} busy=${busy} onSelectSuggestion=${selectSuggestion} />`}
      ${tracking.data?.task && html`<p role="status">Estado: ${tracking.data.task.state} · ${tracking.data.task.status}</p>`}
      ${(latestAttempt?.outcome || launched.launch?.outcome) && html`<p>Último intento: ${latestAttempt?.outcome || launched.launch?.outcome}</p>`}
      ${(latestAttempt?.evidence || launched.launch?.evidence) && html`<p class="message error" role="alert">
        ${latestAttempt?.evidence || launched.launch.evidence}</p>`}
      ${(latestAttempt?.notes || launched.launch?.notes || []).map((note) => html`<p class="muted small" key=${note}>${note}</p>`)}
      ${sessions.length === 0 && html`<p class="muted small">Esperando que el ejecutor registre su sesión…</p>`}
      ${sessions.map((session) => {
        const source = sessionLinks[session.executor_id];
        return source && html`<p key=${session.session_id}><a class="btn secondary"
          href=${routeHref("sessions", source + ":" + session.session_id)}
          ...${helpFor("chat.executor_launch")}>Abrir sesión en Sesiones</a>
          <${Help} topic="chat.executor_launch" compact=${true} /></p>`;
      })}
    </section>`}
  </div>`;
}

export function NewSession({ token, mock }) {
  const route = useRoute();
  const runId = route.section === "new" ? route.id : null;
  const restored = !runId ? heldSecretState : null;
  const store = useRef(restored ? restored.conv : createConversation());
  const [conv, setConv] = useState(store.current);
  const [view, setView] = useState("chat");                        // "chat" or "form" (the existing create-run form)
  const [formInitial, setFormInitial] = useState(null);
  const [catalog, setCatalog] = useState({ entries: [], loading: true, error: null });
  const [connections, setConnections] = useState({ data: null, loading: true, error: null });
  const [selectedConnection, setSelectedConnection] = useState(null);
  const [sessionKind, setSessionKind] = useState(restored ? restored.sessionKind : null); // SES-T03: ask the mode before either flow.
  const [draft, setDraft] = useState(restored ? restored.draft : "");
  const [busy, setBusy] = useState(false);
  const [pendingCloud, setPendingCloud] = useState(null);
  const [pendingSecret, setPendingSecret] = useState(restored ? restored.pendingSecret : null);
  const [limitNotice, setLimitNotice] = useState(null);
  const [singleTask, setSingleTask] = useState("");
  const [singleExecutorId, setSingleExecutorId] = useState("");
  const [singleProvider, setSingleProvider] = useState("");
  const [runs, setRuns] = useState(null);
  const [mode, setMode] = useState("auto");
  const [sensitive, setSensitive] = useState(false);
  const [confirmReal, setConfirmReal] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [launchError, setLaunchError] = useState(null);
  const [routingOverrides, setRoutingOverrides] = useState({});
  const alive = useRef(true);
  const catalogRun = useRef(null);
  const ticket = useRef(0);
  const attempt = useRef({ fingerprint: "", key: "" });
  const composer = useRef(null);
  const endMark = useRef(null);
  const confirmBox = useRef(null);
  const firstFocus = useRef(true);

  // Every change of the conversation goes through the pure reducer; this returns the new state at once. The writing box is
  // refilled in the same render (the answer already given after «Editar» or «Pregunta anterior», empty otherwise), so the
  // next key press never reads the text of the previous question; keepDraft leaves what the user typed.
  function dispatch(action, { keepDraft = false } = {}) {
    store.current = reduce(store.current, action);
    setConv(store.current);
    if (!keepDraft) setDraft(prefillOf(store.current));
    return store.current;
  }

  // The workflows and their descriptions, read once when the screen opens (and again with «Reintentar»).
  function startCatalog() {
    setCatalog({ entries: [], loading: true, error: null });
    catalogRun.current = loadCatalog({
      get: (path) => apiGet(path, token),
      onUpdate: (entries) => { if (alive.current) setCatalog({ entries, loading: true, error: null }); },
    }).then(
      (entries) => { if (alive.current) setCatalog({ entries, loading: false, error: null }); return { entries, error: null }; },
      (failure) => { const error = asError(failure); if (alive.current) setCatalog({ entries: [], loading: false, error }); return { entries: [], error }; },
    );
  }

  useEffect(() => {
    alive.current = true;
    startCatalog();
    return () => { alive.current = false; };
  }, [token]);

  // SES-T02: connection checks only read status; choosing a row never launches it.
  function refreshConnections() {
    setConnections((current) => ({ data: current.data, loading: true, error: null }));
    apiGet("/gui/api/connections", token).then(
      (data) => { if (alive.current) setConnections({ data, loading: false, error: null }); },
      (failure) => { if (alive.current) setConnections({ data: null, loading: false, error: asError(failure) }); },
    );
  }

  useEffect(() => {
    if (!runId) refreshConnections();
  }, [token, runId]);

  const question = currentQuestion(conv);

  // Focus: the composer after every step; the summary card (never its «Lanzar» button) when it appears.
  useEffect(() => {
    if (view !== "chat") return;
    if (conv.phase === "confirm") { if (confirmBox.current) confirmBox.current.focus(); return; }
    if (firstFocus.current) { firstFocus.current = false; if (!finePointer()) return; }
    if (composer.current && !composer.current.disabled) composer.current.focus({ preventScroll: true });
  }, [view, conv.phase, conv.index, conv.messages.length]);

  useEffect(() => {
    if (view === "chat" && conv.messages.length > 0 && endMark.current && endMark.current.scrollIntoView) endMark.current.scrollIntoView({ block: "nearest" });
  }, [view, conv.messages.length, conv.phase]);

  // «Continuar una ejecución»: the runs that can be picked up.
  useEffect(() => {
    if (conv.phase !== "continue") return undefined;
    let current = true;
    setRuns(null);
    apiGet("/gui/api/runs?limit=50", token)
      .then((list) => { if (current) setRuns(pickRuns(list)); })
      .catch((failure) => { if (current) setRuns({ error: asError(failure), runs: [] }); });
    return () => { current = false; };
  }, [conv.phase, token]);

  // The summary: the prompt of each step with these answers (nothing is executed or stored).
  useEffect(() => {
    if (conv.phase !== "confirm") return undefined;
    const mine = ++ticket.current;
    const body = runBody(conv, { mode, sensitive });
    setPreview(null); setPreviewError(null); setPreviewing(true);
    apiPost("/gui/api/runs/preview", token, {
      workflow_id: body.workflow_id, bundle: body.bundle, inputs: body.inputs, execution_mode: mode, sensitive, routing_preferences: routingOverrides,
    }).then(
      (result) => { if (mine === ticket.current) { setPreview(result); setPreviewing(false); } },
      (failure) => { if (mine === ticket.current) { setPreviewError(asError(failure)); setPreviewing(false); } },
    );
    return () => { ticket.current += 1; };
  }, [conv.phase, conv.workflow, conv.answers, mode, sensitive, routingOverrides, token]);

  const settledCatalog = async () => (catalogRun.current ? catalogRun.current : { entries: catalog.entries, error: catalog.error });
  const askModel = (text, confirm_cloud = false, approval_id = "") =>
    apiPost("/gui/api/new-session/interpret", token, interpretBody(text, { confirmCloud: confirm_cloud, approvalId: approval_id }));

  function holdSecret(held, keptDraft) {
    heldSecretState = { sessionKind: "orchestrator", conv: store.current, draft: keptDraft, pendingSecret: held, decision: "" };
    setPendingSecret(held);
  }

  function clearSecret() {
    heldSecretState = null;
    setPendingSecret(null);
  }

  function secretInterpretationFromError(failure) {
    if (failure && failure.details && failure.details.reason === SECRET_REVIEW_REASON) {
      return {
        source: SOURCE_KEYWORDS,
        reason: SECRET_REVIEW_REASON,
        approval_id: failure.details.approval_id || "",
        proposal: null,
      };
    }
    return { source: SOURCE_KEYWORDS, reason: "model_error", proposal: null };
  }

  function finishInterpretation(text, entries, interpretation, keptDraft) {
    if (interpretation && (interpretation.reason === "limit_reached" || interpretation.limit)) {
      const notice = buildLimitNotice(
        interpretation.limit || { code: "limit_reached", resets_at: null, target: "", suggestions: [] },
        connections.data,
      );
      setLimitNotice(notice);
      dispatch({ type: "send", text, catalog: entries, interpretation }, { keepDraft: true });
      setDraft(keptDraft);
      return;
    }
    setLimitNotice(null);
    const before = store.current.messages.length;
    const next = dispatch({ type: "send", text, catalog: entries, interpretation }, { keepDraft: true });
    if (next.messages.length !== before) setDraft(prefillOf(next));      // accepted; an answer that is not accepted stays in the box
  }

  // What the user sent: a command, an answer or free text. The text stays in the box when the answer is not accepted.
  async function submit(text) {
    const trimmed = String(text).trim();
    const asking = store.current.phase === "ask";
    if (busy || pendingCloud || pendingSecret || (!asking && !trimmed)) return;
    const command = parseCommand(trimmed);
    let entries = catalog.entries;
    if (!asking && (!command || command.name === "workflow")) {
      setBusy(true);
      try {
        const loaded = await settledCatalog();
        entries = loaded.entries;
        if (entries.length === 0 && loaded.error) {
          dispatch({ type: "notice", text: "No se pudo leer la lista de workflows: " + errorText(loaded.error) }, { keepDraft: true });
          return;
        }
      } finally { setBusy(false); }
    }
    let interpretation = null;
    if (!asking && (!command || (command.name === "workflow" && command.rest)) &&
        !["confirm", "launched"].includes(store.current.phase) && entries.length > 0) {
      setBusy(true);
      try {
        interpretation = await askModel(command ? command.rest : trimmed);
      } catch (failure) {
        interpretation = secretInterpretationFromError(failure);     // the words are compared instead on model error
      } finally { setBusy(false); }
      if (interpretation && interpretation.reason === "cloud_confirmation_required") {
        setPendingCloud({ text: trimmed, requestText: command ? command.rest : trimmed, entries, interpretation });
        return;
      }
      if (isSecretReviewRequired(interpretation)) {
        holdSecret(createSecretReview({
          text: trimmed,
          requestText: command ? command.rest : trimmed,
          confirmCloud: false,
          approvalId: interpretation.approval_id || "",
        }), String(text));
        return;
      }
    }
    if (!alive.current) return;
    finishInterpretation(trimmed, entries, interpretation, String(text));
  }

  async function answerCloud(send) {
    if (!pendingCloud || busy) return;
    const { text, requestText, entries, interpretation: declined } = pendingCloud;
    setPendingCloud(null);
    let interpretation = declined;
    if (send) {
      setBusy(true);
      try {
        interpretation = await askModel(requestText, true);
      } catch (failure) {
        interpretation = secretInterpretationFromError(failure);
      } finally { setBusy(false); }
      if (!alive.current) return;
      if (isSecretReviewRequired(interpretation)) {
        holdSecret(createSecretReview({
          text,
          requestText,
          confirmCloud: true,
          approvalId: interpretation.approval_id || "",
        }), draft);
        return;
      }
    }
    if (!alive.current) return;
    finishInterpretation(text, entries, interpretation, draft);
  }

  async function resendSecret() {
    if (!pendingSecret || busy) return;
    const current = pendingSecret;
    setBusy(true);
    let entries = catalog.entries;
    let interpretation = null;
    try {
      const loaded = await settledCatalog();
      entries = loaded.entries;
      if (entries.length === 0 && loaded.error) {
        dispatch({ type: "notice", text: "No se pudo leer la lista de workflows: " + errorText(loaded.error) }, { keepDraft: true });
        return;
      }
      const req = secretResendRequest(current);
      interpretation = await askModel(req.text, req.confirmCloud, req.approvalId);
    } catch (failure) {
      interpretation = secretInterpretationFromError(failure);
    } finally {
      setBusy(false);
    }
    if (!alive.current) return;
    if (interpretation && interpretation.reason === "cloud_confirmation_required") {
      clearSecret();
      setPendingCloud({ text: current.text, requestText: current.requestText, entries, interpretation });
      return;
    }
    if (isSecretReviewRequired(interpretation)) {
      holdSecret(createSecretReview({
        text: current.text,
        requestText: current.requestText,
        confirmCloud: current.confirmCloud,
        approvalId: interpretation.approval_id || current.approvalId || "",
      }), draft);
      return;
    }
    clearSecret();
    finishInterpretation(current.text, entries, interpretation, draft);
  }

  function cancelSecret() {
    clearSecret();
  }

  useEffect(() => {
    if (!pendingSecret || !heldSecretState || !heldSecretState.decision) return;
    const decision = heldSecretState.decision;
    heldSecretState = { ...heldSecretState, decision: "" };
    if (decision === "approved") {
      resendSecret();
    } else if (decision === "rejected") {
      clearSecret();
      dispatch({ type: "notice", text: "La aprobación fue rechazada; puedes editar el texto antes de enviarlo." }, { keepDraft: true });
    }
  }, [pendingSecret]);

  async function chooseStarter(starter) {
    if (busy) return;
    let entries = catalog.entries;
    if (starter.id === "workflow") {
      setBusy(true);
      try {
        const loaded = await settledCatalog();
        entries = loaded.entries;
        if (entries.length === 0 && loaded.error) {
          dispatch({ type: "notice", text: "No se pudo leer la lista de workflows: " + errorText(loaded.error) }, { keepDraft: true });
          return;
        }
      } finally { setBusy(false); }
    }
    dispatch({ type: "command", name: starter.id, rest: "", echo: starter.label, catalog: entries }, { keepDraft: true });
  }

  async function pick(entry) {
    if (busy) return;
    setBusy(true);
    setRoutingOverrides({});
    try {
      const spec = await apiGet(inputsPath(entry), token);
      dispatch({ type: "pick", workflow: entry, spec });
    } catch (failure) {
      dispatch({ type: "notice", text: "No se pudieron leer las entradas de «" + entry.id + "»: " + errorText(asError(failure)) }, { keepDraft: true });
    } finally { setBusy(false); }
  }

  async function showAll() {
    const loaded = await settledCatalog();
    dispatch({ type: "show_all", catalog: loaded.entries });
  }

  async function launch() {
    const state = store.current;
    if (busy || state.phase !== "confirm" || (!mock && !confirmReal)) return;
    if (previewing || previewError || !preview) return;
    const body = { ...runBody(state, { mode, sensitive }), routing_preferences: routingOverrides };
    const fingerprint = JSON.stringify(body);
    if (attempt.current.fingerprint !== fingerprint) attempt.current = { fingerprint, key: newKey() };
    setBusy(true);
    setLaunchError(null);
    try {
      const run = await apiPost("/gui/api/runs", token, { ...body, idempotency_key: attempt.current.key });
      attempt.current = { fingerprint: "", key: "" };
      dispatch({ type: "launched", runId: run.id });
      location.hash = threadHref(run.id);
    } catch (failure) {
      setLaunchError(asError(failure));
    } finally { setBusy(false); }
  }

  function openForm() {
    if (Object.values(routingOverrides).some((override) => Object.keys(override).length > 0)) {
      setLaunchError(new ApiError({ message: "No se puede abrir el formulario porque no admite estos ajustes de modelo/esfuerzo. Revierte los cambios primero." }));
      return;
    }
    setFormInitial(formSeed(store.current, { mode, sensitive }));
    setView("form");
  }

  function restart() {
    store.current = createConversation();
    setPendingCloud(null);
    setLimitNotice(null);
    clearSecret();
    setRoutingOverrides({});
    setConv(store.current);
    setLaunchError(null);
    setDraft("");
  }

  function pickOrchestratorSuggestion(item) {
    const next = selectOrchestratorSuggestion(item, draft);
    setSelectedConnection(next.selectedConnection);
    setDraft(next.draft);
  }

  // Intro sends, Mayús+Intro makes a new line. A key held down (repeat) is not a second answer.
  const keyDown = (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    if (!event.repeat) submit(event.currentTarget.value);
  };
  const typing = (event) => {
    setDraft(event.target.value);
    if (store.current.error) dispatch({ type: "typing" }, { keepDraft: true });      // the message was about the old text
  };

  if (runId) return html`<${RunThread} key=${runId} token=${token} runId=${runId} />`;

  // SES-T03: the orchestrator flow below keeps its existing state and ordering.
  if (sessionKind === null) return html`<div class="ns ns-intro"><h3 class="ns-title">¿Cómo quieres empezar?</h3>
    <p class="ns-lead">Elige el tipo de sesión antes de describir tu tarea.</p>
    <div class="actions"><button type="button" class="btn" ...${helpFor("chat.session_mode")}
      onClick=${() => setSessionKind("orchestrator")}>Orquestador</button>
      <button type="button" class="btn secondary" ...${helpFor("chat.session_mode")}
        onClick=${() => setSessionKind("single")}>Sesión única</button></div>
    <${Help} topic="chat.session_mode" compact=${true} /></div>`;
  if (sessionKind === "single") return html`<div class="ns">
    <div class="ns-row"><button type="button" class="btn secondary" ...${helpFor("chat.executor_launch")}
      onClick=${() => setSessionKind("single-executor")}>Usar un ejecutor</button>
      <${Help} topic="chat.executor_launch" compact=${true} /></div>
    <${SingleSession} token=${token} mock=${mock} connections=${connections.data}
      initialTask=${singleTask} initialProvider=${singleProvider} onTaskChange=${setSingleTask}
      onSwitchExecutor=${(executorId, keptTask) => {
        setSingleTask(keptTask);
        setSingleExecutorId(executorId);
        setSessionKind("single-executor");
      }}
      onBack=${() => setSessionKind(null)} />
  </div>`;
  if (sessionKind === "single-executor") return html`<${SingleExecutorSession} token=${token}
    connections=${connections.data} connectionLoading=${connections.loading} connectionError=${connections.error}
    initialTask=${singleTask} initialExecutorId=${singleExecutorId} onTaskChange=${setSingleTask}
    onSwitchModel=${(provider, keptTask) => {
      setSingleTask(keptTask);
      setSingleProvider(provider);
      setSessionKind("single");
    }}
    onRetry=${refreshConnections} onBack=${() => setSessionKind("single")} />`;

  if (view === "form") {
    return html`<${CreateRun} token=${token} mock=${mock} initial=${formInitial}
      onClose=${() => setView("chat")} onCreated=${(run) => navigate("runs", run.id)} />`;
  }

  const { n, total } = progress(conv);
  const suggestions = suggestedEntries(conv, catalog.entries);
  const describedBy = question ? "ns-q-desc" + (question.example ? " ns-q-example" : "") : "";
  const canSkip = question !== null && !question.required;
  const canBack = question !== null && conv.index > 0;
  const rows = question ? (question.kind === "long_text" ? 5 : 2) : 2;
  const placeholder = question ? exampleText({ example: question.example, type: question.kind }) || "" : FREE_PLACEHOLDER;
  const label = question ? "Tu respuesta: " + question.label : "Escribe qué quieres hacer";
  const status = catalog.error ? "" : catalog.loading ? "Leyendo los workflows disponibles…"
    : catalog.entries.length + (catalog.entries.length === 1 ? " workflow disponible." : " workflows disponibles.");
  const pickerHelp = {
    picker: html`<${Help} topic="chat.model_picker" compact=${true} />`,
    choice: html`<${Help} topic="chat.model_choice" compact=${true} />`,
    disconnected: html`<${Help} topic="chat.model_disconnected" compact=${true} />`,
    retry: html`<${Help} topic="chat.model_retry" compact=${true} />`,
    connectionChecks: html`<${Help} topic="chat.connection_checks" compact=${true} />`,
  };

  return html`<div class="ns">
    <button type="button" class="btn secondary" ...${helpFor("chat.session_mode")}
      onClick=${() => setSessionKind(null)}>Cambiar modo</button>
    <${Help} topic="chat.session_mode" compact=${true} />
    <div class="ns-intro">
      <h3 class="ns-title">¿Qué quieres hacer?</h3>
      <p class="ns-lead">Escribe en tus palabras lo que necesitas, o elige una opción. Te guío paso a paso y nada se ejecuta sin que lo confirmes.</p>
      <${Help} topic="chat.welcome" />
    </div>
    <${ModelPicker} connections=${connections.data} selectedId=${selectedConnection} onSelect=${setSelectedConnection}
      loading=${connections.loading} error=${connections.error} onRetry=${refreshConnections} help=${pickerHelp} />
    ${isRegisteredView("apps") && html`<div class="ns-row">
      <a class="btn secondary small" href=${routeHref("apps")} ...${helpFor("chat.connect_other")}>Conectar otro</a>
      <${Help} topic="chat.connect_other" compact=${true} />
    </div>`}
    ${conv.phase === "welcome" && html`<${Starters} disabled=${busy} onPick=${chooseStarter} />`}
    ${(conv.phase === "welcome" || conv.phase === "choose") && html`<p class="muted small" role="status" aria-live="polite">${status}</p>`}
    ${catalog.error && html`<div class="ns-row"><p class="message error" role="alert">No se pudo leer la lista de workflows: ${errorText(catalog.error)}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.retry")} onClick=${() => { startCatalog(); if (composer.current) composer.current.focus(); }}>Reintentar</button>
      <${Help} topic="chat.retry" compact=${true} /></div>`}
    <${Messages} messages=${conv.messages} />
    <${LimitBanner} notice=${limitNotice} connections=${connections.data} busy=${busy} onSelectSuggestion=${pickOrchestratorSuggestion} />
    ${pendingCloud && html`<div class="ns-msg ns-bot" role="group" aria-label="Confirmación de envío a la nube">
      <p>¿Enviar este texto a ${pendingCloud.interpretation.model} (nube)?</p>
      <div class="actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("chat.cloud_confirm")}
          onClick=${() => answerCloud(true)}>Enviar</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.cloud_confirm")}
          onClick=${() => answerCloud(false)}>No enviar</button>
        <${Help} topic="chat.cloud_confirm" compact=${true} />
      </div>
    </div>`}
    ${pendingSecret && html`<div class="ns-msg ns-bot notice" role="alert" aria-label="Revisión de secreto requerida">
      <p>${SECRET_REVIEW_NOTICE}</p>
      <p><a href=${routeHref("approvals", pendingSecret.approvalId || undefined)}>Ir a «Aprobaciones»</a></p>
      <div class="actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("chat.send", { scope: "secret-resend" })}
          onClick=${resendSecret}>Reenviar</button>
        <${Help} topic="chat.send" compact=${true} scope="secret-resend" />
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.edit", { scope: "secret-edit" })}
          onClick=${cancelSecret}>Editar mensaje</button>
        <${Help} topic="chat.edit" compact=${true} scope="secret-edit" />
      </div>
    </div>`}
    ${conv.phase === "choose" && suggestions.length > 0 && html`<${Cards} items=${suggestions} source=${conv.source} disabled=${busy}
      canShowAll=${!conv.showAll && catalog.entries.length > suggestions.length} onPick=${pick} onShowAll=${showAll} />`}
    ${conv.phase === "continue" && html`<${RunsList} runs=${runs} />`}
    ${question && html`<${QuestionCard} question=${question} n=${n} total=${total} disabled=${busy} onChoose=${(choice) => submit(choice)} />`}
    ${conv.phase === "confirm" && html`<${ConfirmCard} conv=${conv} mock=${mock} busy=${busy} boxRef=${confirmBox}
      state=${{ mode, setMode, sensitive, setSensitive, confirmReal, setConfirmReal, preview, previewError, previewing,
        routing: preview && preview.routing, routingError: previewError, routingLoading: previewing, launchError,
        routingOverrides, setRoutingOverrides }}
      onLaunch=${launch} onEdit=${() => dispatch({ type: "edit" })} onForm=${openForm} />`}
    <div class="ns-end" ref=${endMark}></div>
    <form class="ns-composer" onSubmit=${(event) => { event.preventDefault(); submit(draft); }} aria-busy=${busy ? "true" : "false"}>
      <div class="ns-composer-head"><label for="ns-composer-input">${label}</label><${Help} topic="chat.composer" compact=${true} /></div>
      <textarea id="ns-composer-input" ref=${composer} rows=${rows} value=${draft} placeholder=${placeholder}
        disabled=${busy || pendingCloud || pendingSecret || conv.phase === "launched"} aria-invalid=${conv.error ? "true" : null}
        ...${helpFor("chat.composer", { also: [describedBy, conv.error ? "ns-composer-error" : ""].filter(Boolean).join(" ") })}
        onInput=${typing} onKeyDown=${keyDown}></textarea>
      ${conv.error && html`<p id="ns-composer-error" class="field-error" role="alert">${conv.error}</p>`}
      <div class="ns-composer-row">
        ${canBack && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.back")}
          onClick=${() => dispatch({ type: "back" })}>Pregunta anterior</button>
          <${Help} topic="chat.back" compact=${true} />`}
        ${canSkip && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.skip")}
          onClick=${() => dispatch({ type: "skip" })}>Omitir</button>
          <${Help} topic="chat.skip" compact=${true} />`}
        <button type="submit" class="btn ns-send" disabled=${busy || pendingSecret || conv.phase === "launched"} ...${helpFor("chat.send")}>Enviar</button>
        <${Help} topic="chat.send" compact=${true} />
      </div>
    </form>
    <div class="ns-tools">
      ${conv.phase !== "welcome" && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.restart")}
        onClick=${restart}>Empezar de nuevo</button>
        <${Help} topic="chat.restart" compact=${true} />`}
      ${conv.phase !== "confirm" && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.form")}
        onClick=${openForm}>Ver como formulario</button>
        <${Help} topic="chat.form" compact=${true} />`}
    </div>
  </div>`;
}
