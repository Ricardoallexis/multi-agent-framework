// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// ARR3-T02: the plan draft card of «Nueva sesión». It shows what «Guardar el plan» will store (the tasks with their inputs as
// text and, if any, the team versions that will be proposed) and asks to confirm. Saving approves nothing: the server answers
// the href of the plan thread (#new/plan/<id>), where Aprobar and Rechazar are. «Cancelar» drops the draft without calling the
// server. Every text is shown as a text node, never as markup.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost } from "./api.js";
import { currentMode, onModeChange } from "./gui-mode.js";
import { Help, helpFor } from "./help.js";
import {
  FALLBACK_TEXT, PLAN_CANCEL_LABEL, PLAN_SAVED_NOTE, PLAN_SAVE_LABEL, PLAN_SAVE_PATH,
  planDraftRows, planSaveBody, planSaveFailure, teamRows,
} from "./plan-draft-model.js";

// No hooks: the body of the card for a mode.
export function PlanDraftBody({ draft, mode, catalog }) {
  const rows = planDraftRows(draft, mode, catalog);
  const teams = teamRows(draft);
  return html`<div class="plan-draft-body">
    ${draft.goal && html`<p class="plan-draft-goal"><strong>${draft.goal}</strong></p>`}
    ${FALLBACK_TEXT[draft.fallback_reason] && html`<p class="muted small plan-draft-fallback" role="status">
      ${FALLBACK_TEXT[draft.fallback_reason]} <${Help} topic="chat.office_plan" compact=${true} /></p>`}
    <ol class="plan-draft-tasks">${rows.map((row) => html`<li key=${row.id} class="plan-draft-task">
      <strong>${row.title}</strong>
      ${row.workflowText && html`<p class="muted small plan-draft-workflow">${row.workflowText}</p>`}
      ${row.dependsOnText && html`<p class="muted small">${row.dependsOnText}</p>`}
      ${row.inputs.length > 0 && html`<dl class="plan-draft-inputs">${row.inputs.map((item) => html`<div key=${item.label}>
        <dt>${item.label}</dt><dd>${item.value}</dd></div>`)}</dl>`}
    </li>`)}</ol>
    ${teams.length > 0 && html`<div class="plan-draft-teams">
      <h5>Equipos que se propondrán</h5>
      <ul>${teams.map((team) => html`<li key=${team.bundle}>${team.bundle}${team.rolesText ? ": " + team.rolesText : ""}</li>`)}</ul>
      <p class="muted small">Cada equipo queda propuesto, no aprobado: el plan no se puede aprobar hasta que apruebes el equipo.</p>
    </div>`}
  </div>`;
}

export function PlanDraftCard({ token, draft, catalog = [], onSaved, onCancel }) {
  const [mode, setMode] = useState(currentMode());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => onModeChange(setMode), []);
  async function save() {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const saved = await apiPost(PLAN_SAVE_PATH, token, planSaveBody(draft));
      if (onSaved) onSaved(saved.href);
    } catch (failure) {
      setError(planSaveFailure(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) })));
      setBusy(false);
    }
  }
  return html`<section class="ns-msg ns-bot plan-draft" role="group" aria-label="Borrador del plan">
    <h4>Borrador del plan</h4><${Help} topic="chat.plan_draft" compact=${true} />
    <${PlanDraftBody} draft=${draft} mode=${mode} catalog=${catalog} />
    <p class="muted small">${PLAN_SAVED_NOTE}</p>
    ${error && html`<p class="message error" role="alert">${error}</p>`}
    <div class="actions">
      <button type="button" class="btn" disabled=${busy} ...${helpFor("chat.plan_save")} onClick=${save}>${PLAN_SAVE_LABEL}</button>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("chat.plan_save")} onClick=${() => { if (onCancel) onCancel(); }}>${PLAN_CANCEL_LABEL}</button>
      <${Help} topic="chat.plan_save" compact=${true} />
    </div>
  </section>`;
}
