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
import { currentMode } from "./gui-mode.js";   // SIM-T03
import { modeHelpEntry } from "./help-text.js";
import { ModelPicker } from "./model-picker.js";
import { buildModelPicker } from "./model-picker-model.js";
import { askLevelOnce, LEVEL_OPTIONS, QUESTION_LINES } from "./office-level-question.js";
import {
  PROJECT_KEY, SOURCE_KEYWORDS, SOURCE_MODEL, STARTERS, SECRET_REVIEW_REASON, answerRows, buildLimitNotice,
  cardDescription, connectionLabel, createConversation, createSecretReview, currentQuestion, executorLaunchBody, formSeed, inputsPath,
  cardOrigin, originLabel, interpretBody, isSecretReviewRequired, loadCatalog, officeConnectionData,
  officeExecutionModes, officeLevelRequiredText, officeLimitNotice, officeModelOptions, officeModelValue, officeOptions,
  officeRequestBody,
  officeAreaAllowsCloud, officeAreaForBundle, officeAreaPrivacy, officeAreaRequiresLocal, officeLevelText,
  officeWorkflowGroups, hasAvailableLocalModel, parseCommand, plusMenuEntries, pickRuns, progress,
  quotaRemainingLines, reduce, runBody, secretResendRequest, selectOrchestratorSuggestion,
  selectSingleExecutorSuggestion, selectSingleModelSuggestion, singleSessionBody, singleSessionModels, suggestedEntries,
} from "./new-session-model.js";
import { PlanDraftCard } from "./plan-draft.js";   // ARR3-T02
import { isPlanSuggested } from "./plan-draft-model.js";
import { navigate, officeHref, routeHref, threadHref, useRoute } from "./route.js";
import { readNewSessionMode, writeNewSessionMode } from "./shell-state.js";
import { RunThread } from "./thread.js";
import { MODE_LABELS, errorText, exampleText, fmtDate, statusInfo } from "./text.js";
import { t } from "./i18n.js";

const MODES = Object.keys(MODE_LABELS);
const SINGLE_PREVIEW_PATH = "/gui/api/runs/preview";
const SINGLE_RUN_PATH = "/gui/api/runs";
const EXECUTOR_LAUNCH_PATH = "/gui/api/executors/launch";
const EXECUTOR_STATUS_PATH = "/gui/api/executors/launch/";
const OFFICE_LIST_PATH = "/gui/api/offices";
const newKey = () => (globalThis.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random());
const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
// A touch screen does not get the soft keyboard pushed up just by opening the screen.
const finePointer = () => { try { return window.matchMedia("(pointer: fine)").matches; } catch (_) { return true; } };
const prefillOf = (state) => { const question = currentQuestion(state); return question ? state.answers[question.key] || "" : ""; };
const displayKnownStepId = (id) => id === "answer" ? t("new_session.answer_step") : String(id);
const displayKnownModel = (id) => id === "local_default" ? t("new_session.local_default_model") : String(id);
const EXECUTOR_FAILURE_KEYS = {
  quota: "new_session.executor_failure_quota",
  blocked_open_session: "new_session.executor_failure_blocked_open_session",
  authentication: "new_session.executor_failure_authentication",
  permission_denied: "new_session.executor_failure_permission_denied",
  other: "new_session.executor_failure_other",
};
const displayExecutorNote = (note) => {
  const raw = String(note);
  const failure = /^failure:\s*([a-z_]+)(.*)$/.exec(raw);
  const key = failure && EXECUTOR_FAILURE_KEYS[failure[1]];
  return key ? t(key) + failure[2] : raw;
};
const displayExecutorEvidence = (evidence) => {
  const raw = String(evidence);
  const stderrPrefix = "stderr: ";
  return raw.startsWith(stderrPrefix)
    ? t("new_session.executor_stderr", { stderr: "" }) + raw.slice(stderrPrefix.length)
    : raw;
};

function OfficeWorkflowPicker({ groups, areas, disabled, onPick }) {
  const names = new Map((areas || []).map((area) => [area.id, area.name || area.id]));
  return html`<section class="ns-office-workflows" aria-label=${t("new_session.office_workflows_aria")}>
    ${groups.map((group) => html`<div key=${group.area} class="ns-office-area">
      <h4>${names.get(group.area) || group.area}</h4>
      <div class="ns-office-workflow-list">
        ${group.items.map((item) => html`<button key=${group.area + "|" + item.id} type="button" class="ns-card"
          disabled=${disabled} ...${helpFor("chat.office", { scope: "office-workflows" })}
          onClick=${() => onPick(group.area, item.id)}>${item.label}</button>`)}
      </div>
    </div>`)}
    <${Help} topic="chat.office" compact=${true} scope="office-workflows" />
  </section>`;
}

function OfficeSelector({ offices, officeId, onSelect }) {
  if (!Array.isArray(offices) || offices.length === 0) return null;
  const choices = officeOptions({ offices });
  return html`<div class="ns-office-select">
    <label for="ns-office-select">${t("new_session.office")}</label>
    <select id="ns-office-select" value=${officeId} ...${helpFor("chat.office", { scope: "office-select" })}
      onChange=${(event) => onSelect(event.target.value)}>
      ${choices.map((item) => html`<option key=${item.value || "none"} value=${item.value}>${item.label}</option>`)}
    </select>
    <${Help} topic="chat.office" compact=${true} scope="office-select" />
  </div>`;
}

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
  return html`<div class="ns-thread" role="log" aria-label=${t("new_session.conversation")}>
    ${messages.map((message) => html`<div key=${message.id} class=${"ns-msg " + (message.role === "user" ? "ns-user" : "ns-bot")}>
      <span class="sr-only">${message.role === "user" ? t("new_session.you_prefix") : t("new_session.assistant_prefix")}</span>${message.text}</div>`)}
  </div>`;
}

function Starters({ disabled, onPick }) {
  return html`<div class="ns-starters">
    <div class="ns-chips" role="group" aria-label=${t("new_session.starter_options")}>
      ${STARTERS.map((starter) => html`<button key=${starter.id} type="button" class="ns-chip" disabled=${disabled}
        ...${helpFor("chat.starters")} onClick=${() => onPick(starter)}>${starter.label}</button>`)}
    </div>
    <${Help} topic="chat.starters" compact=${true} />
  </div>`;
}

function Cards({ items, source, disabled, canShowAll, onPick, onShowAll }) {
  return html`<div class="ns-suggest">
    <div class="ns-cards" role="group" aria-label=${t("new_session.workflows")}>
      ${items.map(({ entry, matched }) => html`<button key=${entry.bundle + "|" + entry.id} type="button" class="ns-card" disabled=${disabled}
        ...${helpFor("chat.suggestion")} onClick=${() => onPick(entry)}>
        <span class="ns-card-name">${entry.id}</span>
        <span class="ns-card-desc">${cardDescription(entry)}</span>
        ${cardOrigin(entry, currentMode()) && html`<span class="ns-card-meta">${t("new_session.origin", { origin: cardOrigin(entry, currentMode()) })}</span>`}
        ${source === SOURCE_MODEL && html`<span class="ns-card-meta">${t("new_session.proposed_by_model")}</span>`}
        ${source === SOURCE_KEYWORDS && html`<span class="ns-card-meta">${t("new_session.proposed_by_matching")}</span>`}
        ${matched.length > 0 && html`<span class="ns-card-meta">${t("new_session.matches_terms", { terms: matched.join(", ") })}</span>`}
      </button>`)}
    </div>
    <div class="ns-row">
      <${Help} topic="chat.suggestion" compact=${true} />
      ${canShowAll && html`<button type="button" class="btn secondary" disabled=${disabled} ...${helpFor("chat.all")} onClick=${onShowAll}>${t("new_session.show_all_workflows")}</button>
        <${Help} topic="chat.all" compact=${true} />`}
    </div>
  </div>`;
}

function RunsList({ runs }) {
  if (runs === null) return html`<p class="muted" aria-busy="true">${t("new_session.loading_runs")}</p>`;
  if (runs.error) return html`<p class="message error" role="alert">${errorText(runs.error)}</p>`;
  return html`<div class="ns-suggest">
    <p class="muted">${runs.runs.length === 0
      ? t("new_session.no_runs_to_continue")
      : runs.resumable ? t("new_session.open_run_to_continue") : t("new_session.no_resumable_runs")}</p>
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
  return html`<section class="ns-question" aria-label=${t("new_session.current_question")}>
    <p class="ns-q-count">${t("new_session.question_progress", { number: n, total, required: t(question.required ? "new_session.required" : "new_session.optional") })}</p>
    <p class="ns-q-label">${question.label}</p>
    <p id="ns-q-desc" class="ns-q-desc">${question.description}</p>
    ${question.example && html`<p id="ns-q-example" class="ns-q-example">${t("new_session.example")}: <span class="ns-example">${question.example}</span></p>`}
    ${question.kind === "choice" && html`<div class="ns-chips" role="group" aria-label=${t("new_session.options")}>
      ${question.choices.map((choice) => html`<button key=${choice} type="button" class="ns-chip" disabled=${disabled}
        ...${helpFor("chat.choice")} onClick=${() => onChoose(choice)}>${choice}</button>`)}
    </div>
    <${Help} topic="chat.choice" compact=${true} />`}
    <${Help} topic="chat.question" compact=${true} />
  </section>`;
}

function ConfirmCard({ conv, mock, state, busy, onLaunch, onEdit, onForm, boxRef }) {
  const { mode, setMode, sensitive, setSensitive, confirmReal, setConfirmReal, preview, previewError, previewing,
    routing, routingError, routingLoading, launchError, routingOverrides, setRoutingOverrides,
    fileToolsNotice, fileToolsMissing, privacyReason, localModelAvailable, localModelKnown, sensitiveLocked } = state;
  const cloudAllowed = state.cloudAllowed !== false;
  const blockers = [];
  if (!mock && !confirmReal) blockers.push(t("create_run.blocker_confirm_real"));
  if (fileToolsMissing) blockers.push(t("new_session.blocker_file_tools"));
  const routingSteps = routing && Array.isArray(routing.steps) ? routing.steps : [];
  const changedRoutes = Object.entries(routingOverrides).filter(([, override]) => Object.keys(override).length > 0);
  if (previewing) blockers.push(t("new_session.blocker_wait_preferences"));
  if (previewError) blockers.push(t("new_session.blocker_fix_preview"));
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
  return html`<section class="ns-confirm panel" aria-label=${t("new_session.summary_before_launch")} tabindex="-1" ref=${boxRef}>
    <p class="ns-confirm-title"><strong>${t("new_session.ready_to_launch", { workflow: conv.workflow.id })}</strong> ${cardOrigin(conv.workflow, currentMode()) && html`<span class="muted">· ${cardOrigin(conv.workflow, currentMode())}</span>`}</p>
    <${Help} topic="chat.confirm" />
    <dl class="ns-answers">
      <div class="ns-answer"><dt>${t("create_run.workflow_source")}</dt>
        <dd>${originLabel(conv.workflow)}</dd></div>
      ${answerRows(conv).filter((row) => !row.empty).map((row) => html`<div key=${row.key} class="ns-answer"><dt>${row.label}</dt>
        <dd>${row.empty ? html`<span class="muted">${t("new_session.no_answer")}</span>` : row.value}</dd></div>`)}
    </dl>
    <section class="ns-routing" aria-label=${t("new_session.workflow_routing")}>
      <div class="ns-row"><strong>${t("new_session.expected_model_effort")}</strong><${Help} topic="chat.routing" compact=${true} /></div>
      ${routingLoading && html`<p class="muted small" role="status">${t("new_session.loading_expected_profile")}</p>`}
      ${routingError && html`<p class="message error" role="alert">${errorText(routingError)}</p>`}
      ${routing && routingSteps.length === 0 && html`<p class="muted small">${t("new_session.no_routing_steps")}</p>`}
      ${routingSteps.map((step) => html`<div key=${step.step_id} class="ns-routing-step">
        <span class="ns-card-name">${displayKnownStepId(step.step_id)}</span>
        ${step.status === "function" ? html`<p class="hint">${t("new_session.function_step", { name: step.function })}</p>` : html`
        <div class="ns-chips" role="group" aria-label=${t("new_session.step_settings", { step: displayKnownStepId(step.step_id) })}>
          ${[["mode", "new_session.mode", step.mode_options || []], ["profile_id", "new_session.profile", step.profile_options || []],
             ["level", "new_session.level", step.level_options || []]].map(([field, labelKey, options]) => {
            const label = t(labelKey);
            return html`
            <label class="ns-route-chip" key=${field}>${label}
              <select class="ns-chip" aria-label=${t("new_session.label_for_step", { label, step: displayKnownStepId(step.step_id) })}
                disabled=${busy || routingLoading || options.length === 0 || (field !== "profile_id" && !step.profile_id)} ...${helpFor("chat.routing")}
                value=${(routingOverrides[step.step_id] || {})[field] || ""}
                onChange=${(event) => setRouteOverride(step, field, event.target.value)}>
                <option value="">${t("new_session.workflow_default")}${step[field] ? " (" + step[field] + ")" : ""}</option>
                ${options.map((value) => html`<option key=${value} value=${value}>${field === "mode"
                  ? ({ supervised: t("new_session.mode_supervised"), automatic: t("new_session.mode_automatic"), single: t("new_session.mode_single") }[value] || value) : value}</option>`)}
              </select>
            </label>`;
          })}
          <label class="ns-route-chip">${t("new_session.model")}
            <select class="ns-chip" aria-label=${t("new_session.step_model", { step: displayKnownStepId(step.step_id) })} disabled=${busy || routingLoading ||
              !step.profile_id || officeModelOptions(step, routing, cloudAllowed).length === 0}
              ...${helpFor("chat.routing")} value=${officeModelValue(step, routingOverrides, routing, cloudAllowed)}
              onChange=${(event) => setRouteOverride(step, "model", event.target.value)}>
              <option value="">${t("new_session.default")}${officeModelOptions(step, routing, cloudAllowed).includes(step.model)
                ? " (" + displayKnownModel(step.model) + ")" : ""}</option>
              ${officeModelOptions(step, routing, cloudAllowed).map((model) => html`<option key=${model} value=${model}>${displayKnownModel(model)}</option>`)}
            </select>
          </label>
          <label class="ns-route-chip">${t("new_session.effort")}
            <select class="ns-chip" aria-label=${t("new_session.step_effort", { step: displayKnownStepId(step.step_id) })} disabled=${busy || routingLoading || !step.profile_id}
              ...${helpFor("chat.routing")} value=${routingOverrides[step.step_id] &&
                Object.prototype.hasOwnProperty.call(routingOverrides[step.step_id], "effort")
                ? routingOverrides[step.step_id].effort || "" : step.effort || ""}
              onChange=${(event) => setRouteOverride(step, "effort", event.target.value)}>
              <option value="">${t("new_session.default")}</option>
              ${(step.effort_options || []).map((effort) => html`<option key=${effort} value=${effort}>${effort}</option>`)}
            </select>
          </label>
        </div>
        `}
        ${step.status !== "function" && step.status !== "selected" && step.status !== "catalog_default" && html`<p class="hint">${t("new_session.router_reported", { reasons: (step.reason_codes || []).join(", ") || step.status })}</p>`}
      </div>`)}
    </section>
    <div class="two">
      <div><label for="ns-mode">${t("create_run.execution_mode")}</label>
        <${Help} topic="create.mode" entry=${modeHelpEntry(mode)} />
        <select id="ns-mode" ...${helpFor("create.mode")} value=${mode} disabled=${busy || changedRoutes.length > 0} onChange=${(event) => setMode(event.target.value)}>
          ${officeExecutionModes(MODES, cloudAllowed).map((value) => html`<option key=${value} value=${value}>${t("create_run.execution_mode_" + value)}</option>`)}
        </select></div>
      <div class="check"><label><input type="checkbox" checked=${sensitive} disabled=${busy || changedRoutes.length > 0 || sensitiveLocked} ...${helpFor("create.sensitive")}
        onChange=${(event) => setSensitive(event.target.checked)} /> ${t("privacy.local_only")}</label>
        <${Help} topic="create.sensitive" />
        ${privacyReason && html`<p class="hint" role="status">${t(privacyReason)}</p>`}
        ${sensitiveLocked && localModelKnown && !localModelAvailable && html`<p class="hint" role="status">${t("privacy.no_local_model")}</p>`}</div>
    </div>
    ${!mock && html`<div class="notice check"><label><input type="checkbox" checked=${confirmReal} disabled=${busy} ...${helpFor("create.confirm_real")}
      onChange=${(event) => setConfirmReal(event.target.checked)} /> ${t("create_run.confirm_real_text")}</label>
      <${Help} topic="create.confirm_real" /></div>`}
    <${PreviewPanel} preview=${preview} error=${previewError} loading=${previewing} />
    ${launchError && html`<p class="message error" role="alert">${errorText(launchError)}</p>`}
    ${fileToolsNotice && fileToolsMissing && html`<p class="message error small" role="alert">${fileToolsNotice}</p>`}
    ${fileToolsNotice && !fileToolsMissing && html`<p class="hint" role="status">${fileToolsNotice}</p>`}
    ${blockers.length > 0 && html`<p id="ns-blockers" class="hint" role="status">${t("new_session.blockers_summary", { blockers: blockers.join(" ") })}</p>`}
    <p class="hint" role="status">${t("new_session.launch_ready")}</p>
    ${changedRoutes.length > 0 && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.routing")}
      onClick=${() => setRoutingOverrides({})}>${t("new_session.reset_routing")}</button>`}
    <div class="actions">
      <button type="button" class="btn" disabled=${busy || blockers.length > 0} ...${helpFor("chat.launch", { also: "ns-blockers" })}
        onClick=${onLaunch}>${busy ? t("new_session.launching") : t("new_session.launch")}</button>
      <${Help} topic="chat.launch" compact=${true} />
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.edit")} onClick=${onEdit}>${t("new_session.edit")}</button>
      <${Help} topic="chat.edit" compact=${true} />
    </div>
  </section>`;
}

// CUO-T06: limit notice with local reset time (or unknown) and up to 3 selection-only suggestion buttons.
function LimitBanner({ notice, connections, busy, onSelectSuggestion }) {
  const view = notice && notice.text ? notice : buildLimitNotice(notice, connections);
  if (!view) return null;
  return html`<div class="ns-msg ns-bot notice" role="status" aria-label=${t("new_session.limit_reached")}>
    <p>${view.text}</p>
    <${Help} topic="chat.limit_notice" compact=${true} />
    ${view.suggestions.length > 0 && html`<div class="ns-chips" role="group" aria-label=${t("new_session.available_alternatives")}>
      ${view.suggestions.map((item) => html`<button key=${item.id} type="button" class="ns-chip" disabled=${busy}
        ...${helpFor("chat.limit_suggestion")} onClick=${() => onSelectSuggestion(item)}>${item.label}</button>`)}
    </div>
    <${Help} topic="chat.limit_suggestion" compact=${true} />`}
  </div>`;
}

// GUI3-T06: the same task box is used by all three flows; its controls only choose or navigate.
function SessionBox({ task, onTask, onSubmit, disabled, label, placeholder, rows = 2, mode, onMode,
  modelControl, project, projects, onProject, onExecutor, onForm, error, describedBy = "", onBack, onSkip, inputRef,
  executorActive = false, maxLength, allowExecutor = true, allowForm = true, showProject = true }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef(null);
  return html`<form class="ns-composer" onSubmit=${(event) => { event.preventDefault(); onSubmit(task); }} aria-busy=${disabled ? "true" : "false"}>
    <div class="ns-composer-head"><label for="ns-composer-input">${label}</label><${Help} topic="chat.composer" compact=${true} /></div>
    <textarea id="ns-composer-input" ref=${inputRef} rows=${rows} maxlength=${maxLength} value=${task} placeholder=${placeholder} disabled=${disabled}
      aria-invalid=${error ? "true" : null} ...${helpFor("chat.composer", { also: [describedBy, error ? "ns-composer-error" : ""].filter(Boolean).join(" ") })}
      onInput=${(event) => onTask(event.target.value)} onKeyDown=${(event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
          event.preventDefault(); if (!event.repeat) onSubmit(event.currentTarget.value);
        }
      }}></textarea>
    ${error && html`<p id="ns-composer-error" class="field-error" role="alert">${error}</p>`}
    <div class="ns-box-controls">
      <div class="ns-plus-wrap" hidden=${!allowExecutor && !allowForm} onKeyDown=${(event) => {
        // Esc closes the menu from «＋» as well as from inside it, and the focus goes back to «＋». An open help text
        // inside the wrap closes first (it prevents the default).
        if (event.key === "Escape" && menuOpen && !event.defaultPrevented) { event.preventDefault(); setMenuOpen(false); menuButton.current?.focus(); }
      }}><button type="button" ref=${menuButton} class="ns-plus" aria-label=${t("new_session.more_options")} aria-expanded=${menuOpen ? "true" : "false"}
        ...${helpFor("chat.box_menu")} onClick=${() => setMenuOpen(!menuOpen)}>＋</button>
        <${Help} topic="chat.box_menu" compact=${true} />
        ${menuOpen && html`<div class="ns-plus-menu" role="group" aria-label=${t("new_session.starter_options")}>
          ${allowExecutor && html`<button type="button" disabled=${executorActive} ...${helpFor("chat.executor_launch")}
            onClick=${() => { setMenuOpen(false); onExecutor(); }}>${t("new_session.use_executor")}</button>
          <${Help} topic="chat.executor_launch" compact=${true} />`}
          ${allowForm && plusMenuEntries(currentMode()).includes("form") && html`<button type="button" ...${helpFor("chat.form")} onClick=${() => { setMenuOpen(false); onForm(); }}>${t("new_session.view_as_form")}</button>
            <${Help} topic="chat.form" compact=${true} />`}
        </div>`}
      </div>
      <div class="ns-box-select"><label for="ns-mode-select">${t("new_session.mode")}</label><select id="ns-mode-select" aria-label=${t("new_session.session_mode")} value=${mode} ...${helpFor("chat.session_mode")}
        onChange=${(event) => onMode(event.target.value)}><option value="orchestrator">${t("new_session.orchestrator")}</option>
        <option value="single">${t("new_session.single_session")}</option></select><${Help} topic="chat.session_mode" compact=${true} /></div>
      ${modelControl}
      ${showProject && html`<div class="ns-box-select"><label for="ns-project-select">${t("new_session.project")}</label><select id="ns-project-select" aria-label=${t("new_session.project")} value=${executorActive ? "" : project} disabled=${executorActive}
        ...${helpFor("chat.project_select")} onChange=${(event) => onProject(event.target.value)}>
        <option value="">${executorActive ? t("new_session.not_for_executor") : t("new_session.choose_on_confirm")}</option>
        ${projects.map((name) => html`<option key=${name} value=${name}>${name}</option>`)}</select>
        <${Help} topic="chat.project_select" compact=${true} /></div>`}
      <button type="submit" class="btn ns-send" disabled=${disabled} ...${helpFor("chat.send")}>${t("new_session.send")}</button>
      <${Help} topic="chat.send" compact=${true} />
    </div>
    ${(onBack || onSkip) && html`<div class="ns-composer-row">
      ${onBack && html`<button type="button" class="btn secondary" disabled=${disabled} ...${helpFor("chat.back")} onClick=${onBack}>${t("new_session.previous_question")}</button><${Help} topic="chat.back" compact=${true} />`}
      ${onSkip && html`<button type="button" class="btn secondary" disabled=${disabled} ...${helpFor("chat.skip")} onClick=${onSkip}>${t("new_session.skip")}</button><${Help} topic="chat.skip" compact=${true} />`}
    </div>`}
  </form>`;
}

// SES-T03: one task, one catalog model, then the same run thread as every workflow.
function SingleSession({
  token, mock, connections, task, onTaskChange, project, projects, onProject, onMode, onForm,
  initialProvider = "", onSwitchExecutor = null,
}) {
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
    onTaskChange(value);
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
      const body = singleSessionBody(task, model, false, project);
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
      const body = singleSessionBody(task, model, cloudSelected && confirmCloud, project);
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
  const disconnectedCount = buildModelPicker(connections).disconnected.length;
  const modelControl = html`<div class="ns-box-select"><label for="ns-single-model">${t("new_session.model")}</label><select id="ns-single-model" aria-label=${t("new_session.single_session_model")} value=${model}
    ...${helpFor("chat.single_model")} onChange=${(event) => {
      if (event.target.value === "connect") { location.hash = routeHref("apps"); return; }
      setModel(event.target.value); setConfirmCloud(false); edit();
    }}><option value="">${choices.some((choice) => choice.models.length) ? t("model_picker.choose_model") : t("model_picker.connect_model")}</option>
    ${choices.map((choice) => choice.models.map((item) => html`<option key=${item.id} value=${item.id}>${connectionLabel(choice.provider)} · ${displayKnownModel(item.id)}</option>`))}
    <option value="connect">${t("model_picker.connect_another", { count: disconnectedCount })}</option></select>
    <${Help} topic="chat.single_model" compact=${true} /></div>`;
  return html`<div class="ns">
    <div class="ns-intro"><h3 class="ns-title">${t("new_session.single_session")}</h3>
      <p class="ns-lead">${t("new_session.single_session_lead")}</p>
    </div>
    <${SessionBox} task=${task} onTask=${(value) => { updateTask(value); edit(); }} onSubmit=${check}
      disabled=${ready || busy} label=${t("new_session.task")} placeholder=${t("new_session.task_placeholder")} rows=${5} mode="single" onMode=${onMode}
      modelControl=${modelControl} project=${project} projects=${projects} onProject=${onProject}
      onExecutor=${() => onSwitchExecutor("", task)} onForm=${onForm} />
    <${LimitBanner} notice=${activeLimit} connections=${connections} busy=${busy} onSelectSuggestion=${selectSuggestion} />
    ${mock && html`<p class="muted small ns-mock-note">${t("new_session.mock_note")}</p>`}
    <section class="ns-single-model-details" aria-label=${t("new_session.model_status")}>
      ${!catalogPreview && !error && html`<p role="status">${t("new_session.loading_catalog_models")}</p>`}
      ${choices.map((choice) => html`<p key=${choice.provider} class="muted small">${connectionLabel(choice.provider)}${choice.status ? " · " + choice.status : ""}
        ${choice.quotaNote ? " · " + choice.quotaNote : ""}${choice.reason ? " · " + choice.reason : ""}</p>`)}
      ${catalogPreview && choices.length === 0 && html`<p role="status">${t("new_session.no_connected_providers")}</p>`}
      <p class="muted small ns-row">${t("new_session.provider_status")} <${Help} topic="chat.connection_checks" compact=${true} /></p>
    </section>
    ${error && html`<p class="message error" role="alert">${errorText(error)}</p>`}
    ${ready && html`<section class="ns-confirm panel" aria-label=${t("new_session.summary_before_launch")}>
      <p><strong>${t("new_session.model")}:</strong> ${displayKnownModel(model)}</p><p><strong>${t("new_session.project")}:</strong> ${project || t("new_session.single_session")}</p>
      <p><strong>${t("new_session.task")}:</strong> ${task}</p>
      <${PreviewPanel} preview=${preview} error=${null} loading=${false} />
      ${cloudSelected && html`<label class="check"><input type="checkbox" checked=${confirmCloud}
        ...${helpFor("chat.cloud_confirm")} onChange=${(event) => setConfirmCloud(event.target.checked)} />
        ${t("new_session.confirm_cloud_task")}</label><${Help} topic="chat.cloud_confirm" compact=${true} />`}
      ${!mock && html`<label class="check"><input type="checkbox" checked=${confirmReal}
        ...${helpFor("create.confirm_real")} onChange=${(event) => setConfirmReal(event.target.checked)} />
        ${t("new_session.confirm_real_single")}</label>`}
      <button type="button" class="btn" disabled=${busy || !selected || (!mock && !confirmReal) || (cloudSelected && !confirmCloud)}
        ...${helpFor("chat.launch")} onClick=${launch}>${busy ? t("new_session.launching") : t("new_session.launch")}</button>
      <${Help} topic="chat.launch" compact=${true} />
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.edit")} onClick=${edit}>${t("new_session.edit")}</button>
      <${Help} topic="chat.edit" compact=${true} />
    </section>`}
    ${!ready && html`<button type="button" class="btn" disabled=${busy || !task.trim() || !selected}
      ...${helpFor("chat.single_task")} onClick=${check}>${t("new_session.review_and_launch")}</button><${Help} topic="chat.single_task" compact=${true} />`}
  </div>`;
}

// SES-T04: an executor is chosen and reviewed separately from the model-only ask_once flow.
function SingleExecutorSession({
  token, mock, connections, connectionLoading, connectionError, onRetry, onBack,
  task, onTaskChange, project, projects, onProject, onMode, onForm,
  initialExecutorId = "", onSwitchModel = null,
}) {
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
    onTaskChange(value);
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
  const failureKind = latestAttempt ? latestAttempt.failure_kind : launched?.launch?.failure_kind;
  const attemptOutcome = latestAttempt?.outcome || launched?.launch?.outcome;
  const failedAttempt = tracking.data?.task?.state === "failed" || Boolean(failureKind)
    || ["failed", "quota_exhausted", "permission_blocked", "blocked_open_session", "timeout", "cancelled",
      "secret_review_required"].includes(attemptOutcome);
  const failureText = failedAttempt ? errorText({ code: "executor_failure", details: { failure_kind: failureKind || "other" } }) : "";
  const sessionLinks = {
    claude_cli: "claude", codex_cli: "codex", copilot_cli: "copilot", antigravity_cli: "antigravity",
  };
  const activeLimit = limitNotice || (selectedConnection?.limit ? buildLimitNotice(selectedConnection.limit, connections) : null);
  const modelControl = html`<div class="ns-box-select"><label for="ns-executor-model">${t("new_session.model")}</label><select id="ns-executor-model" aria-label=${t("new_session.executor_model")} value=${model}
    disabled=${!selectedAvailable || busy} ...${helpFor("chat.executor_launch")}
    onChange=${(event) => { setModel(event.target.value); edit(); }}><option value="">${t("new_session.choose_model")}</option>
    ${models.map((value) => html`<option key=${value} value=${value}>${value}</option>`)}</select></div>`;

  return html`<div class="ns">
    <div class="ns-intro">
      <h3 class="ns-title">${t("new_session.single_executor_session")}</h3>
      <p class="ns-lead">${t("new_session.single_executor_lead")}</p>
      ${!launched && html`<button type="button" class="btn secondary" ...${helpFor("chat.executor_launch")}
        onClick=${onBack}>${t("new_session.back_to_model_session")}</button><${Help} topic="chat.executor_launch" compact=${true} />`}
    </div>
    ${!launched && !ready && html`<${SessionBox} task=${task} onTask=${(value) => { updateTask(value); edit(); }}
      onSubmit=${() => { if (canReview) setReady(true); }} disabled=${ready || busy}
      label=${t("new_session.task")} placeholder=${t("new_session.task_placeholder")} rows=${5} maxLength=${4000} executorActive=${true}
      mode="single" onMode=${onMode}
      modelControl=${modelControl} project=${project} projects=${projects} onProject=${onProject}
      onExecutor=${() => {}} onForm=${onForm} />`}
    <${LimitBanner} notice=${activeLimit} connections=${connections} busy=${busy} onSelectSuggestion=${selectSuggestion} />
    ${mock && html`<p class="muted small ns-mock-note">${t("new_session.mock_note")}</p>`}
    ${connectionError && html`<div class="ns-row"><p class="message error" role="alert">${errorText(connectionError)}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.executor_launch")} onClick=${onRetry}>${t("new_session.retry_connections")}</button>
      <${Help} topic="chat.executor_launch" compact=${true} /></div>`}
    ${connectionLoading && html`<p class="muted small" role="status">${t("new_session.checking_executor_connections")}</p>`}
    ${composer.loading && html`<p class="muted small" role="status">${t("new_session.loading_models_efforts")}</p>`}
    ${composer.error && html`<p class="message error" role="alert">${errorText(composer.error)}</p>`}
    ${!launched && html`
      <section class="ns-executor-picker panel" aria-label=${t("new_session.connected_executors")}>
        <strong>${t("new_session.executor")}</strong><${Help} topic="chat.executor_launch" compact=${true} />
        ${choices.length === 0 && !connectionLoading && html`<p class="muted small" role="status">${t("new_session.no_connected_executors")}</p>`}
        ${choices.map((item) => {
          const option = composer.options.find((candidate) => candidate.executor_id === item.id);
          const selectable = item.selectable && option?.available === true;
          const reason = !item.selectable ? item.reason : !option
            ? t("new_session.no_supported_models")
            : !option.available ? t("new_session.no_model_effort_options") : null;
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
        ${efforts.length > 0 && html`<label>${t("new_session.effort")}
          <select value=${effort} disabled=${busy || !selectedAvailable}
            ...${helpFor("chat.executor_launch")}
            onChange=${(event) => { setEffort(event.target.value); edit(); }}>
            <option value="">${t("new_session.choose_effort")}</option>
            ${efforts.map((value) => html`<option key=${value} value=${value}>${value}</option>`)}
          </select>
          <${Help} topic="chat.executor_launch" compact=${true} />
        </label>`}
      </div>
      ${launchError && html`<p class="message error" role="alert">${errorText(launchError)}</p>`}
      ${ready && html`<section class="ns-confirm panel" aria-label=${t("new_session.launch_confirmation")}>
        <h4>${t("new_session.confirm_this_launch")}</h4>
        <dl class="ns-answers">
          <div class="ns-answer"><dt>${t("new_session.executor")}</dt><dd>${selected?.label || executorLabels[executorId] || executorId}</dd></div>
          <div class="ns-answer"><dt>${t("new_session.model")}</dt><dd>${displayKnownModel(model)}</dd></div>
          <div class="ns-answer"><dt>${t("new_session.effort")}</dt><dd>${effort || t("new_session.default")}</dd></div>
          <div class="ns-answer"><dt>${t("new_session.remaining_quota")}</dt><dd>${quotaRemainingLines(selectedConnection?.quota).join(" · ")}</dd></div>
          <div class="ns-answer"><dt>${t("new_session.task")}</dt><dd>${task}</dd></div>
        </dl>
        ${selected?.warning && html`<p class="notice ns-picker-notice" role="status">${selected.warning}</p>`}
        ${selected?.agyNotice && html`<p class="notice ns-picker-notice">${selected.agyNotice}</p>`}
        <p class="hint" role="status">${t("new_session.nothing_until_approve_launch")}</p>
        <button type="button" class="btn" disabled=${busy || !canReview}
          ...${helpFor("chat.executor_launch")} onClick=${launch}>${busy ? t("new_session.launching") : t("new_session.approve_and_launch")}</button>
        <${Help} topic="chat.executor_launch" compact=${true} />
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.executor_launch")} onClick=${edit}>${t("new_session.edit")}</button>
        <${Help} topic="chat.executor_launch" compact=${true} />
      </section>`}
    `}
    ${launched && html`<section class="ns-executor-tracking panel" aria-label=${t("new_session.executor_tracking")}>
      <h4>${t("new_session.task_tracking")}</h4>
      ${tracking.error && html`<p class="message error" role="alert">${errorText(tracking.error)}</p>`}
      ${tracking.data?.task && html`<p role="status">${t("new_session.status")}: ${tracking.data.task.state} · ${tracking.data.task.status}</p>`}
      ${tracking.data?.latest_result && html`<div class="ns-executor-response">
        <strong>${t("new_session.executor_response")}</strong>
        <p>${tracking.data.latest_result.summary}</p>
      </div>`}
      ${(latestAttempt?.outcome || launched.launch?.outcome) && html`<p>${t("new_session.last_attempt")}: ${latestAttempt?.outcome || launched.launch?.outcome}</p>`}
      ${failureText && html`<p class="message error" role="alert">${failureText}</p>`}
      ${(latestAttempt?.evidence || launched.launch?.evidence || (latestAttempt?.notes || launched.launch?.notes || []).length > 0) && html`<details>
        <summary>${t("new_session.technical_details")}</summary>
        ${(latestAttempt?.evidence || launched.launch?.evidence) && html`<p class="muted small">${displayExecutorEvidence(latestAttempt?.evidence || launched.launch.evidence)}</p>`}
        ${(latestAttempt?.notes || launched.launch?.notes || []).map((note) => html`<p class="muted small" key=${note}>${displayExecutorNote(note)}</p>`)}
      </details>`}
      ${sessions.length === 0 && html`<p class="muted small">${t("new_session.waiting_for_session")}</p>`}
      ${sessions.map((session) => {
        const source = sessionLinks[session.executor_id];
        return source && session.readable && html`<p key=${session.session_id}><a class="btn secondary"
          href=${routeHref("sessions", source + ":" + session.session_id)}
          ...${helpFor("chat.executor_launch")}>${t("new_session.open_session_in_sessions")}</a>
          <${Help} topic="chat.executor_launch" compact=${true} /></p>`;
      })}
    </section>`}
  </div>`;
}

export function NewSession({ token, mock }) {
  const route = useRoute();
  const officeId = route.kind === "office" ? route.office : "";
  const officeArea = route.kind === "office" ? route.area : null;
  const runId = route.section === "new" ? route.id : null;
  const restored = !runId ? heldSecretState : null;
  const store = useRef(restored ? restored.conv : createConversation());
  const [conv, setConv] = useState(store.current);
  const [view, setView] = useState("chat");                        // "chat" or "form" (the existing create-run form)
  const [formInitial, setFormInitial] = useState(null);
  const [catalog, setCatalog] = useState({ entries: [], loading: true, error: null });
  const [officeCatalog, setOfficeCatalog] = useState({ entries: [], loading: false, error: null });
  const [officeCatalogVersion, setOfficeCatalogVersion] = useState(0);
  const [officeList, setOfficeList] = useState({ offices: [], loading: true, error: null });
  const [officeDetail, setOfficeDetail] = useState(null);
  const [OfficeSourceUpdateView, setOfficeSourceUpdateView] = useState(null);
  const [sourceUpdateLoadFailed, setSourceUpdateLoadFailed] = useState(false);
  const [officeScene, setOfficeScene] = useState(null);
  const [officePrivacy, setOfficePrivacy] = useState({ officeId: "", data: null, loading: false, error: null });
  const [officeLoading, setOfficeLoading] = useState(false);
  const [officeError, setOfficeError] = useState(null);
  const [unknownOfficeNotice, setUnknownOfficeNotice] = useState(false);
  const [connections, setConnections] = useState({ data: null, loading: true, error: null });
  const [selectedConnection, setSelectedConnection] = useState(null);
  const [sessionKind, setSessionKind] = useState(restored ? restored.sessionKind
    : officeId ? (officeArea ? "single" : "orchestrator") : readNewSessionMode());
  const [draft, setDraft] = useState(restored ? restored.draft : "");
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState([]);
  const [busy, setBusy] = useState(false);
  const [pendingCloud, setPendingCloud] = useState(null);
  const [officeLevelQuestion, setOfficeLevelQuestion] = useState(null);
  const [officeLevelNotice, setOfficeLevelNotice] = useState(null);
  const [pendingSecret, setPendingSecret] = useState(restored ? restored.pendingSecret : null);
  const [limitNotice, setLimitNotice] = useState(null);
  const [planDraft, setPlanDraft] = useState(null);                  // ARR3-T02: the draft of a plan_suggested answer
  const [singleExecutorId, setSingleExecutorId] = useState("");
  const [singleProvider, setSingleProvider] = useState("");
  const [runs, setRuns] = useState(null);
  const [mode, setMode] = useState("auto");
  const [sensitive, setSensitiveState] = useState(false);
  const sensitiveTouched = useRef(false);
  const setSensitive = (value) => { sensitiveTouched.current = true; setSensitiveState(value); };
  const [confirmReal, setConfirmReal] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewWorkflowKey, setPreviewWorkflowKey] = useState("");
  const [previewError, setPreviewError] = useState(null);
  const [previewing, setPreviewing] = useState(false);
  const [launchError, setLaunchError] = useState(null);
  const [routingOverrides, setRoutingOverrides] = useState({});
  const alive = useRef(true);
  const catalogRun = useRef(null);
  const officeCatalogRun = useRef(null);
  const officeRouteKey = useRef([route.kind, officeId, officeArea].join("|"));
  const routeKey = [route.kind, officeId, officeArea].join("|");
  const routeChanged = officeRouteKey.current !== routeKey;
  const selectedWorkflow = conv.workflow;
  const officesForBundle = officeId ? [...officeList.offices, { id: officeId }] : officeList.offices;
  const selectedBundleArea = officeAreaForBundle(selectedWorkflow?.bundle, officesForBundle);
  const unresolvedOfficeBundle = Boolean(selectedWorkflow?.bundle?.includes("__")
    && (officeList.error || (officeList.loading && !selectedBundleArea)));
  const effectiveOfficeId = selectedBundleArea?.officeId || officeId;
  // A reply that comes back after the route changed (another office, another area or plain #new) belongs to the old
  // screen, and its handler still holds the old office: nothing it brings back is shown or confirmed here.
  const replyIsStale = (startKey) => !alive.current || officeRouteKey.current !== startKey;
  const ticket = useRef(0);
  const attempt = useRef({ fingerprint: "", key: "" });
  const composer = useRef(null);
  const endMark = useRef(null);
  const confirmBox = useRef(null);
  const firstFocus = useRef(true);

  function chooseMode(value) {
    const next = value === "single" ? "single" : "orchestrator";
    writeNewSessionMode(next);
    if (officeId && officeArea && next === "orchestrator") {
      setSessionKind(next);
      location.hash = officeHref(officeId);
      return;
    }
    setSessionKind(next);
  }

  function chooseProject(value) {
    setProject(value);
    if (store.current.workflow) {
      dispatch({ type: "project", project: value });
      if (currentQuestion(store.current)?.key === PROJECT_KEY) setDraft(value);
    }
  }

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

  function retryCatalog() {
    if (officeId) setOfficeCatalogVersion((value) => value + 1);
    else startCatalog();
  }

  useEffect(() => {
    let active = true;
    if (runId) {
      setOfficeList({ offices: [], loading: false, error: null });
      return () => { active = false; };
    }
    setOfficeList({ offices: [], loading: true, error: null });
    apiGet(OFFICE_LIST_PATH, token).then(
      (payload) => {
        if (active) setOfficeList({
          offices: Array.isArray(payload?.offices) ? payload.offices : [],
          loading: false,
          error: null,
        });
      },
      (failure) => {
        if (active) setOfficeList({ offices: [], loading: false, error: asError(failure) });
      },
    );
    return () => { active = false; };
  }, [runId, token]);

  useEffect(() => {
    alive.current = true;
    if (!officeId) startCatalog();
    else setCatalog({ entries: [], loading: false, error: null });
    return () => { alive.current = false; };
  }, [token, officeId]);

  useEffect(() => {
    if (!officeId) {
      setOfficeDetail(null);
      setOfficeScene(null);
      setOfficeLoading(false);
      setOfficeError(null);
      setOfficeCatalog({ entries: [], loading: false, error: null });
      officeCatalogRun.current = null;
      return undefined;
    }
    if (officeList.loading) return undefined;
    if (officeList.error) {
      setOfficeError(officeList.error);
      setOfficeLoading(false);
      return undefined;
    }
    if (!officeList.offices.some((office) => office.id === officeId)) {
      setUnknownOfficeNotice(true);
      navigate("new");
      return undefined;
    }
    let active = true;
    setOfficeLoading(true);
    setOfficeError(null);
    setOfficeDetail(null);
    setOfficeScene(null);
    setOfficeCatalog({ entries: [], loading: false, error: null });
    officeCatalogRun.current = null;
    const path = OFFICE_LIST_PATH + "/" + encodeURIComponent(officeId);
    Promise.all([apiGet(path, token), apiGet(path + "/scene", token)]).then(
      ([detail, scene]) => {
        if (!active) return;
        setOfficeDetail(detail);
        setOfficeScene(scene);
        setOfficeLoading(false);
      },
      (failure) => {
        if (!active) return;
        if (failure?.status === 404 || failure?.details?.reason === "office_not_installed") {
          setUnknownOfficeNotice(true);
          navigate("new");
          return;
        }
        setOfficeError(asError(failure));
        setOfficeLoading(false);
      },
    );
    return () => { active = false; };
  }, [officeId, officeList, token]);

  useEffect(() => {
    if (!officeId || !officeDetail || officeDetail.id !== officeId) return undefined;
    let active = true;
    const bundles = (Array.isArray(officeDetail.areas) ? officeDetail.areas : [])
      .map((area) => officeId + "__" + area.id);
    setOfficeCatalog({ entries: [], loading: true, error: null });
    officeCatalogRun.current = loadCatalog({
      get: (path) => apiGet(path, token),
      bundles,
      onUpdate: (entries) => {
        if (active) setOfficeCatalog({ entries, loading: true, error: null });
      },
    }).then(
      (entries) => {
        if (active) setOfficeCatalog({ entries, loading: false, error: null });
        return { entries, error: null };
      },
      (failure) => {
        const error = asError(failure);
        if (active) setOfficeCatalog({ entries: [], loading: false, error });
        return { entries: [], error };
      },
    );
    return () => { active = false; };
  }, [officeId, officeDetail, officeCatalogVersion, token]);

  useEffect(() => {
    if (!effectiveOfficeId) {
      setOfficePrivacy({ officeId: "", data: null, loading: false, error: null });
      return undefined;
    }
    let active = true;
    setOfficePrivacy({ officeId: effectiveOfficeId, data: null, loading: true, error: null });
    apiGet(OFFICE_LIST_PATH + "/" + encodeURIComponent(effectiveOfficeId) + "/privacy", token).then(
      (data) => { if (active) setOfficePrivacy({ officeId: effectiveOfficeId, data, loading: false, error: null }); },
      (failure) => {
        if (active) setOfficePrivacy({ officeId: effectiveOfficeId, data: null, loading: false, error: asError(failure) });
      },
    );
    return () => { active = false; };
  }, [effectiveOfficeId, token]);

  useEffect(() => {
    const nextKey = [route.kind, officeId, officeArea].join("|");
    if (officeRouteKey.current === nextKey) return;
    officeRouteKey.current = nextKey;
    setBusy(false);
    sensitiveTouched.current = false;
    setSensitiveState(false);
    setOfficeLevelQuestion(null);
    setOfficeLevelNotice(null);
    store.current = createConversation();
    setConv(store.current);
    setDraft("");
    setPlanDraft(null);
    setPendingCloud(null);
    setLimitNotice(null);
    setSelectedConnection(null);
    setRoutingOverrides({});
    setLaunchError(null);
    setView("chat");
    setFormInitial(null);
    setSessionKind(route.kind === "office" ? (officeArea ? "single" : "orchestrator") : readNewSessionMode());
    if (route.kind === "office") setUnknownOfficeNotice(false);
  }, [route.kind, officeId, officeArea]);

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

  useEffect(() => {
    if (runId) return;
    let active = true;
    apiGet("/gui/api/runs?limit=50", token).then((runs) => {
      if (active) setProjects([...new Set((Array.isArray(runs) ? runs : []).map((run) => run.project_name).filter(Boolean))]);
    }, () => {});
    return () => { active = false; };
  }, [token, runId]);

  const question = currentQuestion(conv);
  const currentOfficePrivacyState = officePrivacy.officeId === effectiveOfficeId ? officePrivacy : null;
  const privacyAreaId = selectedWorkflow
    ? (selectedBundleArea?.officeId === effectiveOfficeId ? selectedBundleArea.areaId : null)
    : officeArea;
  const currentOfficePrivacy = currentOfficePrivacyState?.data || null;
  const currentAreaPrivacy = officeAreaPrivacy(currentOfficePrivacy, privacyAreaId);
  const privacyRequiresLocal = unresolvedOfficeBundle
    || (Boolean(effectiveOfficeId) && officeAreaRequiresLocal(currentOfficePrivacy, privacyAreaId));
  const privacyReason = !privacyAreaId ? ""
    : currentAreaPrivacy?.data_class === "sensitive"
      ? "privacy.sensitive_reason"
      : currentAreaPrivacy?.data_class === "restricted"
        ? "privacy.local_only_reason"
        : currentAreaPrivacy && !officeAreaAllowsCloud(currentOfficePrivacy, privacyAreaId)
          ? "privacy.office_reason" : "";
  const cloudPermission = !unresolvedOfficeBundle
    && (!effectiveOfficeId || officeAreaAllowsCloud(currentOfficePrivacy, privacyAreaId));
  const sensitiveSelection = sensitive || privacyRequiresLocal;
  const cloudAllowed = cloudPermission && !sensitiveSelection;
  const currentWorkflowKey = selectedWorkflow ? [selectedWorkflow.bundle || "", selectedWorkflow.id].join("|") : "";
  const localModelKnown = Boolean(connections.data && !connections.loading && !connections.error
    && preview?.routing && previewWorkflowKey === currentWorkflowKey && !previewing && !previewError);
  const localModelAvailable = localModelKnown && hasAvailableLocalModel(connections.data, preview.routing);

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
    const body = runBody(conv, { mode, sensitive: sensitiveSelection });
    setPreview(null); setPreviewWorkflowKey(""); setPreviewError(null); setPreviewing(true);
    apiPost("/gui/api/runs/preview", token, {
      workflow_id: body.workflow_id, bundle: body.bundle, inputs: body.inputs, execution_mode: mode,
      sensitive: sensitiveSelection, routing_preferences: routingOverrides,
    }).then(
      (result) => {
        if (mine === ticket.current) {
          setPreview(result);
          setPreviewWorkflowKey([body.bundle || "", body.workflow_id].join("|"));
          setPreviewing(false);
        }
      },
      (failure) => { if (mine === ticket.current) { setPreviewError(asError(failure)); setPreviewing(false); } },
    );
    return () => { ticket.current += 1; };
  }, [conv.phase, conv.workflow, conv.answers, mode, sensitiveSelection, routingOverrides, token]);

  const currentOfficeDetail = officeDetail?.id === officeId ? officeDetail : null;
  const sourceUpdateAvailable = Boolean(currentOfficeDetail?.source_update?.tracked
    && currentOfficeDetail.source_update.available && currentOfficeDetail.source_update.changed);
  useEffect(() => {
    let active = true;
    if (!sourceUpdateAvailable) {
      setOfficeSourceUpdateView(null);
      setSourceUpdateLoadFailed(false);
      return () => { active = false; };
    }
    setOfficeSourceUpdateView(null);
    setSourceUpdateLoadFailed(false);
    import("../extensions/offices/import.js").then(({ OfficeSourceUpdate }) => {
      if (active) setOfficeSourceUpdateView(() => OfficeSourceUpdate);
    }).catch(() => {
      if (active) setSourceUpdateLoadFailed(true);
    });
    return () => { active = false; };
  }, [officeId, sourceUpdateAvailable]);
  const currentOfficeScene = officeScene?.office?.id === officeId ? officeScene : null;
  const activeCatalog = officeId
    ? (currentOfficeDetail ? officeCatalog : { entries: [], loading: officeLoading, error: null })
    : catalog;
  const officeSingle = Boolean(officeId && (officeArea || (!routeChanged && sessionKind === "single")));
  useEffect(() => {
    if (!selectedWorkflow) return;
    if (privacyRequiresLocal) setSensitiveState(true);
    else if (localModelKnown && !sensitiveTouched.current) setSensitiveState(localModelAvailable);
  }, [selectedWorkflow?.bundle, selectedWorkflow?.id, privacyRequiresLocal, localModelKnown, localModelAvailable]);
  useEffect(() => {
    if (mode === "cloud" && !cloudAllowed) setMode("auto");
  }, [mode, cloudAllowed]);
  const officeGroups = officeWorkflowGroups(
    officeArea || (currentOfficeDetail?.areas || []),
    activeCatalog.entries,
  );
  const selectedWorkflowArea = currentOfficeDetail?.areas?.find((area) => area.bundle === selectedWorkflow?.bundle);
  const selectedWorkflowDeclaration = selectedWorkflowArea?.workflow_declarations?.find(
    (item) => item.id === selectedWorkflow?.id,
  );
  const usesFileTools = Array.isArray(selectedWorkflowDeclaration?.file_tools)
    && selectedWorkflowDeclaration.file_tools.length > 0;
  const fileToolsMissing = usesFileTools && currentOfficeDetail?.file_tools_configured !== true;
  const fileToolsNotice = usesFileTools
    ? fileToolsMissing
      ? t("new_session.file_tools_missing")
      : t("new_session.file_tools_used")
    : "";
  const settledCatalog = async () => {
    const pending = officeId ? officeCatalogRun.current : catalogRun.current;
    return pending || { entries: activeCatalog.entries, error: activeCatalog.error };
  };
  const requestBody = (text, confirmCloud = false, approvalId = "") => {
    if (!officeId) return interpretBody(text, { confirmCloud, approvalId });
    const body = officeRequestBody(text, officeId, confirmCloud);
    if (approvalId) body.approval_id = approvalId;
    return body;
  };
  const askModel = (text, confirm_cloud = false, approval_id = "") =>
    apiPost("/gui/api/new-session/interpret", token, requestBody(text, confirm_cloud, approval_id));
  async function interpretText(text, confirmCloud = false, approvalId = "") {
    const interpretation = await askModel(text, confirmCloud, approvalId);
    if (officeId && mock && interpretation?.reason === "disabled") {
      const plan = await apiPost("/gui/api/new-session/plan", token, requestBody(text, confirmCloud, approvalId));
      if (plan && plan.proposal) return { ...interpretation, reason: "plan_suggested", plan_draft: plan };
    }
    return interpretation;
  }

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

  function showOfficeLevelRequired(failure, { startKey, retry, onResult } = {}) {
    const text = officeLevelRequiredText(failure);
    if (!text) return false;
    const area = failure?.details?.area;
    if (currentMode() === "simple" && officeId && typeof area === "string" && retry && onResult) {
      const areaName = currentOfficeDetail?.areas?.find((item) => item.id === area)?.name || area;
      setOfficeLevelNotice(null);
      setOfficeLevelQuestion({ area, areaName, officeId, routeKey: startKey, retry, onResult });
      return true;
    }
    dispatch({ type: "notice", text }, { keepDraft: true });
    setOfficeLevelQuestion(null);
    setOfficeLevelNotice({ text: "", officeId });
    return true;
  }

  async function chooseOfficeLevel(chosen) {
    if (!officeLevelQuestion || busy) return;
    const pending = officeLevelQuestion;
    const startKey = officeRouteKey.current;
    const stale = () => replyIsStale(startKey) || startKey !== pending.routeKey || routeKey !== startKey;
    if (stale()) {
      setOfficeLevelQuestion(null);
      return;
    }
    setOfficeLevelQuestion(null);
    setOfficeLevelNotice(null);
    setBusy(true);
    try {
      const result = await askLevelOnce({
        token,
        officeId: pending.officeId,
        area: pending.area,
        post: apiPost,
        isStale: stale,
        retry: (question) => {
          if (question) return Promise.resolve(chosen);
          if (stale()) return Promise.resolve(null);
          return pending.retry();
        },
      });
      if (stale()) return;
      if (result !== null) pending.onResult(result);
    } catch (failure) {
      if (stale()) return;
      setOfficeLevelNotice({
        text: officeLevelRequiredText(failure) || errorText(asError(failure)),
        officeId: pending.officeId,
      });
    } finally {
      if (!stale()) setBusy(false);
    }
  }

  function cancelOfficeLevelQuestion() {
    setOfficeLevelQuestion(null);
  }

  function queuePendingCloud(question, startKey) {
    const pending = { ...question, routeKey: startKey };
    setPendingCloud(pending);
  }

  function finishInterpretation(text, entries, interpretation, keptDraft) {
    if (isPlanSuggested(interpretation)) {       // ARR3-T02: the draft card asks to confirm before anything is saved
      setLimitNotice(null);
      setPlanDraft(interpretation.plan_draft);
      dispatch({ type: "notice", text: t("new_session.plan_notice") }, { keepDraft: true });
      setDraft(keptDraft);
      return;
    }
    if (interpretation && (interpretation.reason === "limit_reached" || interpretation.limit)) {
      const notice = buildLimitNotice(
        interpretation.limit || { code: "limit_reached", resets_at: null, target: "", suggestions: [] },
        officeId ? officeConnectionData(connections.data, currentOfficeScene) : connections.data,
      );
      setLimitNotice(officeId ? officeLimitNotice(notice, currentOfficeScene) : notice);
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
    if (busy || pendingCloud || pendingSecret || officeLevelQuestion || (!asking && !trimmed)) return;
    const startKey = officeRouteKey.current;
    const command = parseCommand(trimmed);
    let entries = activeCatalog.entries;
    if (!asking && (!command || command.name === "workflow")) {
      setBusy(true);
      try {
        const loaded = await settledCatalog();
        if (replyIsStale(startKey)) return;
        entries = loaded.entries;
        if (entries.length === 0 && loaded.error) {
          const label = officeId ? "new_session.office_options_failed" : "new_session.workflow_list_failed";
          dispatch({ type: "notice", text: t(label, { error: errorText(loaded.error) }) }, { keepDraft: true });
          return;
        }
      } finally { setBusy(false); }
    }
    let interpretation = null;
    if (!officeSingle && !asking && (!command || (command.name === "workflow" && command.rest)) &&
        !["confirm", "launched"].includes(store.current.phase) && (entries.length > 0 || officeId)) {
      setBusy(true);
      try {
        interpretation = await interpretText(command ? command.rest : trimmed);
      } catch (failure) {
        if (replyIsStale(startKey)) return;
        const requestText = command ? command.rest : trimmed;
        if (showOfficeLevelRequired(failure, {
          startKey,
          retry: () => interpretText(requestText),
          onResult: (answer) => {
            if (answer?.reason === "cloud_confirmation_required") {
              queuePendingCloud({ text: trimmed, requestText, entries, interpretation: answer }, startKey);
            } else if (isSecretReviewRequired(answer)) {
              holdSecret(createSecretReview({
                text: trimmed, requestText, confirmCloud: false, approvalId: answer.approval_id || "",
              }), String(text));
            } else {
              finishInterpretation(trimmed, entries, answer, String(text));
            }
          },
        })) return;
        interpretation = secretInterpretationFromError(failure);     // the words are compared instead on model error
      } finally { setBusy(false); }
      if (replyIsStale(startKey)) return;
      if (interpretation && interpretation.reason === "cloud_confirmation_required") {
        setPendingCloud({ text: trimmed, requestText: command ? command.rest : trimmed, entries, interpretation,
                          routeKey: startKey });
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
    if (replyIsStale(startKey)) return;
    finishInterpretation(trimmed, entries, interpretation, String(text));
  }

  async function answerCloud(send) {
    if (!pendingCloud || busy) return;
    const { text, requestText, entries, interpretation: declined, routeKey: askedFor } = pendingCloud;
    setPendingCloud(null);
    const startKey = officeRouteKey.current;
    if (askedFor !== startKey) return;      // asked for another office, area or #new: never confirmed here
    let interpretation = declined;
    if (send) {
      setBusy(true);
      try {
        interpretation = await interpretText(requestText, true);
      } catch (failure) {
        if (replyIsStale(startKey)) return;
        if (showOfficeLevelRequired(failure, {
          startKey,
          retry: () => interpretText(requestText, true),
          onResult: (answer) => {
            if (answer?.reason === "cloud_confirmation_required") {
              queuePendingCloud({ text, requestText, entries, interpretation: answer }, startKey);
            } else if (isSecretReviewRequired(answer)) {
              holdSecret(createSecretReview({
                text, requestText, confirmCloud: true, approvalId: answer.approval_id || "",
              }), draft);
            } else {
              finishInterpretation(text, entries, answer, draft);
            }
          },
        })) return;
        interpretation = secretInterpretationFromError(failure);
      } finally { setBusy(false); }
      if (replyIsStale(startKey)) return;
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
    if (replyIsStale(startKey)) return;
    finishInterpretation(text, entries, interpretation, draft);
  }

  async function resendSecret() {
    if (!pendingSecret || busy) return;
    const current = pendingSecret;
    const startKey = officeRouteKey.current;
    setBusy(true);
    let entries = activeCatalog.entries;
    const req = secretResendRequest(current);
    let interpretation = null;
    try {
      const loaded = await settledCatalog();
      entries = loaded.entries;
      if (entries.length === 0 && loaded.error) {
        dispatch({ type: "notice", text: t("new_session.workflow_list_failed", { error: errorText(loaded.error) }) }, { keepDraft: true });
        return;
      }
      if (replyIsStale(startKey)) return;
      interpretation = await interpretText(req.text, req.confirmCloud, req.approvalId);
    } catch (failure) {
      if (replyIsStale(startKey)) return;
      if (showOfficeLevelRequired(failure, {
        startKey,
        retry: () => interpretText(req.text, req.confirmCloud, req.approvalId),
        onResult: (answer) => {
          if (answer?.reason === "cloud_confirmation_required") {
            clearSecret();
            queuePendingCloud({ text: current.text, requestText: current.requestText, entries,
                                interpretation: answer }, startKey);
          } else if (isSecretReviewRequired(answer)) {
            holdSecret(createSecretReview({
              text: current.text, requestText: current.requestText, confirmCloud: current.confirmCloud,
              approvalId: answer.approval_id || current.approvalId || "",
            }), draft);
          } else {
            clearSecret();
            finishInterpretation(current.text, entries, answer, draft);
          }
        },
      })) {
        clearSecret();
        return;
      }
      interpretation = secretInterpretationFromError(failure);
    } finally {
      setBusy(false);
    }
    if (replyIsStale(startKey)) return;
    if (interpretation && interpretation.reason === "cloud_confirmation_required") {
      clearSecret();
      setPendingCloud({ text: current.text, requestText: current.requestText, entries, interpretation,
                        routeKey: startKey });
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
      dispatch({ type: "notice", text: t("new_session.approval_rejected") }, { keepDraft: true });
    }
  }, [pendingSecret]);

  async function chooseStarter(starter) {
    if (busy) return;
    let entries = activeCatalog.entries;
    if (starter.id === "workflow") {
      setBusy(true);
      try {
        const loaded = await settledCatalog();
        entries = loaded.entries;
        if (entries.length === 0 && loaded.error) {
          dispatch({ type: "notice", text: t("new_session.workflow_list_failed", { error: errorText(loaded.error) }) }, { keepDraft: true });
          return;
        }
      } finally { setBusy(false); }
    }
    dispatch({ type: "command", name: starter.id, rest: "", echo: starter.label, catalog: entries }, { keepDraft: true });
  }

  async function pick(entry) {
    if (busy) return;
    setBusy(true);
    sensitiveTouched.current = false;
    setSensitiveState(false);
    setRoutingOverrides({});
    try {
      const spec = await apiGet(inputsPath(entry), token);
      dispatch({ type: "pick", workflow: entry, spec, project });
    } catch (failure) {
      dispatch({ type: "notice", text: t("new_session.workflow_inputs_failed", { id: entry.id, error: errorText(asError(failure)) }) }, { keepDraft: true });
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
    const body = { ...runBody(state, { mode, sensitive: sensitiveSelection }), routing_preferences: routingOverrides };
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
    if (sessionKind === "orchestrator" && Object.values(routingOverrides).some((override) => Object.keys(override).length > 0)) {
      setLaunchError(new ApiError({ message: t("new_session.form_routing_unsupported") }));
      return;
    }
    const seed = sessionKind === "orchestrator" ? formSeed(store.current, { mode, sensitive: sensitiveSelection }) : {
      bundle: "general", workflowId: "ask_once", project: project || "Sesión única",
      values: { task: draft }, mode: "auto", sensitive: false,
    };
    const formContext = {
      connections: connections.data,
      ...(!officeList.loading && !officeList.error ? { offices: officeList.offices } : {}),
      officeId, privacy: currentOfficePrivacy, sensitiveTouched: sensitiveTouched.current,
    };
    setFormInitial(seed ? { ...seed, ...formContext, project: project || seed.project } : { ...formContext, project });
    setView("form");
  }

  function restart() {
    store.current = createConversation();
    setPendingCloud(null);
    setLimitNotice(null);
    setPlanDraft(null);
    clearSecret();
    setRoutingOverrides({});
    setConv(store.current);
    setLaunchError(null);
    setDraft("");
  }

  function pickOrchestratorSuggestion(item) {
    if (officeId && item?.kind === "executor") return;
    const next = selectOrchestratorSuggestion(item, draft);
    if (item?.kind === "executor") {
      setSingleExecutorId(item.id);
      chooseMode("single");
      setSessionKind("single-executor");
      return;
    }
    setSelectedConnection(next.selectedConnection);
    setDraft(next.draft);
  }

  const typing = (value) => {
    setDraft(value);
    if (store.current.error) dispatch({ type: "typing" }, { keepDraft: true });      // the message was about the old text
  };

  function selectOffice(value) {
    setUnknownOfficeNotice(false);
    location.hash = value ? officeHref(value) : "#new";
  }

  function pickOfficeWorkflow(area, workflowId) {
    const entry = officeCatalog.entries.find((item) =>
      item.id === workflowId && item.bundle === officeId + "__" + area);
    if (entry) pick(entry);
  }

  if (runId) return html`<${RunThread} key=${runId} token=${token} runId=${runId} />`;

  if (view === "form") {
    return html`<${CreateRun} token=${token} mock=${mock} initial=${formInitial}
      onClose=${() => setView("chat")} onCreated=${(run) => navigate("runs", run.id)} />`;
  }
  if (sessionKind === "single" && !officeId) return html`<div class="ns">
    <${OfficeSelector} offices=${officeList.offices} officeId=${officeId} onSelect=${selectOffice} />
    ${officeList.error && html`<p class="message error small" role="alert">${t("new_session.office_list_failed", { error: errorText(officeList.error) })}</p>`}
    ${officePrivacy.error && html`<p class="message error small" role="alert">${errorText(officePrivacy.error)}</p>`}
    <${SingleSession} token=${token} mock=${mock} connections=${connections.data}
    task=${draft} onTaskChange=${setDraft} project=${project} projects=${projects} onProject=${chooseProject}
    onMode=${chooseMode} onForm=${openForm} initialProvider=${singleProvider}
    onSwitchExecutor=${(executorId, keptTask) => {
      setDraft(keptTask); setSingleExecutorId(executorId); setSessionKind("single-executor");
    }} />
  </div>`;
  if (sessionKind === "single-executor" && !officeId) return html`<div class="ns">
    <${OfficeSelector} offices=${officeList.offices} officeId=${officeId} onSelect=${selectOffice} />
    ${officeList.error && html`<p class="message error small" role="alert">${t("new_session.office_list_failed", { error: errorText(officeList.error) })}</p>`}
    <${SingleExecutorSession} token=${token} mock=${mock}
    connections=${connections.data} connectionLoading=${connections.loading} connectionError=${connections.error}
    task=${draft} onTaskChange=${setDraft} project=${project} projects=${projects} onProject=${chooseProject}
    onMode=${chooseMode} onForm=${openForm} initialExecutorId=${singleExecutorId}
    onSwitchModel=${(provider, keptTask) => {
      setDraft(keptTask); setSingleProvider(provider); setSessionKind("single");
    }} onRetry=${refreshConnections} onBack=${() => setSessionKind("single")} />
  </div>`;

  const { n, total } = progress(conv);
  const suggestions = suggestedEntries(conv, activeCatalog.entries);
  const describedBy = question ? "ns-q-desc" + (question.example ? " ns-q-example" : "") : "";
  const canSkip = question !== null && !question.required;
  const canBack = question !== null && conv.index > 0;
  const rows = question ? (question.kind === "long_text" ? 5 : 2) : 2;
  const placeholder = question ? exampleText({ example: question.example, type: question.kind }) || "" : t("new_session.free_placeholder");
  const label = question ? t("new_session.your_answer", { label: question.label }) : t("new_session.what_to_do");
  const officeOptionCount = officeGroups.reduce((count, group) => count + group.items.length, 0);
  const status = activeCatalog.error ? "" : officeId && (officeList.loading || officeLoading || activeCatalog.loading) ? t("new_session.loading_office_options")
    : officeId ? t(officeOptionCount === 1 ? "new_session.office_option_one" : "new_session.office_option_many", { count: officeOptionCount })
      : activeCatalog.loading ? t("new_session.loading_workflows")
        : t(activeCatalog.entries.length === 1 ? "new_session.workflow_count_one" : "new_session.workflow_count_many", { count: activeCatalog.entries.length });
  const pickerHelp = {
    picker: html`<${Help} topic="chat.model_picker" compact=${true} />`,
    choice: html`<${Help} topic="chat.model_choice" compact=${true} />`,
    disconnected: html`<${Help} topic="chat.model_disconnected" compact=${true} />`,
    retry: html`<${Help} topic="chat.model_retry" compact=${true} />`,
    connect: html`<${Help} topic="chat.connect_other" compact=${true} />`,
  };
  const connectionData = officeId && !cloudPermission
    ? officeConnectionData(connections.data, { areas: [{ data_class: "restricted" }] })
    : connections.data;
  const activeLimitNotice = officeId ? officeLimitNotice(limitNotice, currentOfficeScene) : limitNotice;
  const modelControl = officeSingle ? null : html`<${ModelPicker} connections=${connectionData} selectedId=${selectedConnection}
    onSelect=${setSelectedConnection} loading=${connections.loading} error=${connections.error}
    onRetry=${refreshConnections} onConnect=${() => { location.hash = routeHref("apps"); }} help=${pickerHelp} />`;
  const noConnectedModels = !officeId && !connections.loading && buildModelPicker(connectionData).models.length === 0;
  return html`<div class="ns">
    <${OfficeSelector} offices=${officeList.offices} officeId=${officeId} onSelect=${selectOffice} />
    ${officeId && currentOfficeScene && html`<p class="muted small" role="status">${t("new_session.highest_privacy", { level: officeLevelText(currentOfficeScene) })}</p>`}
    ${currentOfficePrivacyState?.error && html`<p class="message error small" role="alert">${errorText(currentOfficePrivacyState.error)}</p>`}
    ${effectiveOfficeId && currentOfficePrivacyState?.loading && html`<p class="muted small" role="status">${t("new_session.loading_office_options")}</p>`}
    ${effectiveOfficeId && privacyReason && html`<p class="hint" role="status">${t(privacyReason)}</p>`}
    ${effectiveOfficeId && privacyRequiresLocal && localModelKnown && !localModelAvailable
      && html`<p class="hint" role="status">${t("privacy.no_local_model")}</p>`}
    ${officeList.error && html`<p class="message error small" role="alert">${t("new_session.office_list_failed", { error: errorText(officeList.error) })}</p>`}
    ${unknownOfficeNotice && html`<p class="message error small" role="alert">${t("new_session.office_unavailable")}</p>`}
    ${officeError && html`<p class="message error small" role="alert">${t("new_session.office_failed", { error: errorText(officeError) })}</p>`}
    <div class="ns-intro">
      <h3 class="ns-title">${t("new_session.welcome_title")}</h3>
      <p class="ns-lead">${officeSingle
        ? t("new_session.office_welcome")
        : t("new_session.chat_welcome")}</p>
      <${Help} topic="chat.welcome" />
    </div>
    ${conv.phase !== "confirm" && html`<${SessionBox} task=${draft} onTask=${typing} onSubmit=${submit}
      disabled=${busy || pendingCloud || pendingSecret || officeLevelQuestion || conv.phase === "launched"} label=${label}
      placeholder=${placeholder} rows=${rows} mode=${officeSingle ? "single" : "orchestrator"} onMode=${chooseMode}
      modelControl=${modelControl} project=${project} projects=${projects} onProject=${chooseProject}
      onExecutor=${() => { writeNewSessionMode("single"); setSingleExecutorId(""); setSessionKind("single-executor"); }} onForm=${openForm}
      allowExecutor=${!officeId} allowForm=${!officeId}
      showProject=${question?.source !== "project"}
      error=${conv.error} describedBy=${describedBy} inputRef=${composer}
      onBack=${canBack ? () => dispatch({ type: "back" }) : null}
      onSkip=${canSkip ? () => dispatch({ type: "skip" }) : null} />`}
    ${noConnectedModels && html`${isRegisteredView("apps") && html`<a class="btn secondary small" href=${routeHref("apps")}
      ...${helpFor("chat.connect_other")}>${t("model_picker.connect_another_short")}</a>`}`}
    <${LimitBanner} notice=${activeLimitNotice} connections=${connectionData} busy=${busy} onSelectSuggestion=${pickOrchestratorSuggestion} />
    ${mock && html`<p class="muted small ns-mock-note">${t("new_session.mock_note")}</p>`}
    ${conv.phase === "welcome" && !officeSingle && html`<${Starters} disabled=${busy} onPick=${chooseStarter} />`}
    ${(conv.phase === "welcome" || conv.phase === "choose") && html`<p class="muted small" role="status" aria-live="polite">${status}</p>`}
    ${officeId && currentOfficeDetail && conv.phase === "welcome" && !activeCatalog.loading && !activeCatalog.error && html`<${OfficeWorkflowPicker}
      groups=${officeGroups} areas=${currentOfficeDetail?.areas || []} disabled=${busy || officeLoading}
      onPick=${pickOfficeWorkflow} />`}
    ${activeCatalog.error && html`<div class="ns-row"><p class="message error" role="alert">
      ${officeId
        ? t("new_session.office_options_failed", { error: errorText(activeCatalog.error) })
        : t("new_session.workflow_list_failed", { error: errorText(activeCatalog.error) })}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.retry")} onClick=${() => { retryCatalog(); if (composer.current) composer.current.focus(); }}>${t("new_session.retry")}</button>
      <${Help} topic="chat.retry" compact=${true} /></div>`}
    <${Messages} messages=${conv.messages} />
    ${planDraft && html`<${PlanDraftCard} token=${token} draft=${planDraft} catalog=${activeCatalog.entries}
      onSaved=${(href) => { setPlanDraft(null); location.hash = href; }} onCancel=${() => setPlanDraft(null)} />`}
    ${officeLevelQuestion && html`<div class="ns-msg ns-bot notice" role="group" aria-label=${t("new_session.privacy_question")}>
      <p>${t("new_session.privacy_question_text", { area: officeLevelQuestion.areaName })}</p>
      <div class="actions">
        ${LEVEL_OPTIONS.map((option) => html`<button key=${option.value} type="button" class="btn secondary"
          disabled=${busy} ...${helpFor("chat.office", { scope: "office-level-question" })}
          onClick=${() => chooseOfficeLevel(option.value)}>${QUESTION_LINES[option.value]}</button>`)}
        <button type="button" class="btn secondary" disabled=${busy}
          ...${helpFor("chat.office", { scope: "office-level-question" })} onClick=${cancelOfficeLevelQuestion}>${t("new_session.cancel")}</button>
        <${Help} topic="chat.office" compact=${true} scope="office-level-question" />
      </div>
    </div>`}
    ${officeLevelNotice && html`<div class="ns-msg ns-bot notice" role="alert">
      ${officeLevelNotice.text && html`<p>${officeLevelNotice.text}</p>`}
      ${officeLevelNotice.officeId && html`<a class="btn secondary small" href=${routeHref("offices", officeLevelNotice.officeId)}
        ...${helpFor("chat.office", { scope: "office-privacy-link" })}>${t("new_session.privacy")}</a>
        <${Help} topic="chat.office" compact=${true} scope="office-privacy-link" />`}
    </div>`}
    ${sourceUpdateAvailable && html`<div class="ns-msg ns-bot notice" role="status">
        <p>${t("new_session.office_update_available")}</p>
        ${OfficeSourceUpdateView && html`<${OfficeSourceUpdateView} key=${officeId} token=${token} officeId=${officeId}
          onDone=${(detail) => { if (detail?.id === officeId) setOfficeDetail(detail); }} />`}
        ${sourceUpdateLoadFailed && html`<p><a href=${routeHref("offices")} ...${helpFor("chat.office", { scope: "office-source-update-review" })}>
          ${t("new_session.open_offices_to_review_update")}</a></p>`}
        ${sourceUpdateLoadFailed && html`<${Help} topic="chat.office" compact=${true} scope="office-source-update-review" />`}
      </div>`}
    ${currentOfficeDetail?.source_update?.tracked && !currentOfficeDetail.source_update.available && html`
      <div class="ns-msg ns-bot notice" role="status">
        <p>${t("new_session.source_folder_unavailable")}</p>
      </div>`}
    ${pendingCloud && html`<div class="ns-msg ns-bot" role="group" aria-label=${t("new_session.cloud_send_confirmation")}>
      <p>${t("new_session.send_text_to_cloud_model", { model: pendingCloud.interpretation.model })}</p>
      <div class="actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("chat.cloud_confirm")}
          onClick=${() => answerCloud(true)}>${t("new_session.send")}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.cloud_confirm")}
          onClick=${() => answerCloud(false)}>${t("new_session.do_not_send")}</button>
        <${Help} topic="chat.cloud_confirm" compact=${true} />
      </div>
    </div>`}
    ${pendingSecret && html`<div class="ns-msg ns-bot notice" role="alert" aria-label=${t("new_session.secret_review_required")}>
      <p>${t("new_session.secret_review_notice")}</p>
      <p><a href=${routeHref("approvals", pendingSecret.approvalId || undefined)}>${t("new_session.go_to_approvals")}</a></p>
      <div class="actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("chat.send", { scope: "secret-resend" })}
          onClick=${resendSecret}>${t("new_session.resend")}</button>
        <${Help} topic="chat.send" compact=${true} scope="secret-resend" />
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.edit", { scope: "secret-edit" })}
          onClick=${cancelSecret}>${t("new_session.edit_message")}</button>
        <${Help} topic="chat.edit" compact=${true} scope="secret-edit" />
      </div>
    </div>`}
    ${conv.phase === "choose" && suggestions.length > 0 && html`<${Cards} items=${suggestions} source=${conv.source} disabled=${busy}
      canShowAll=${!conv.showAll && activeCatalog.entries.length > suggestions.length} onPick=${pick} onShowAll=${showAll} />`}
    ${conv.phase === "continue" && html`<${RunsList} runs=${runs} />`}
    ${question && html`<${QuestionCard} question=${question} n=${n} total=${total} disabled=${busy} onChoose=${(choice) => submit(choice)} />`}
    ${conv.phase === "confirm" && html`<${ConfirmCard} conv=${conv} mock=${mock} busy=${busy} boxRef=${confirmBox}
      state=${{ mode, setMode, sensitive: sensitiveSelection, setSensitive, confirmReal, setConfirmReal, preview, previewError, previewing,
        routing: preview && preview.routing, routingError: previewError, routingLoading: previewing, launchError,
        routingOverrides, setRoutingOverrides, cloudAllowed, fileToolsNotice, fileToolsMissing,
        privacyReason, localModelAvailable, localModelKnown, sensitiveLocked: privacyRequiresLocal }}
      onLaunch=${launch} onEdit=${() => dispatch({ type: "edit" })} onForm=${openForm} />`}
    <div class="ns-end" ref=${endMark}></div>
    <div class="ns-tools">
      ${conv.phase !== "welcome" && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.restart")}
        onClick=${restart}>${t("new_session.start_over")}</button>
        <${Help} topic="chat.restart" compact=${true} />`}
    </div>
  </div>`;
}
