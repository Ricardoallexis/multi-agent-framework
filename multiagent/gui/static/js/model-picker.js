// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The selector reads only connection facts. Launching belongs to the session flow.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { helpFor } from "./help.js";
import { buildModelPicker } from "./model-picker-model.js";
import { errorText, fmtDate } from "./text.js";

function Choice({ item, selectedId, onSelect }) {
  return html`<label class=${"ns-picker-choice" + (item.selectable ? "" : " ns-picker-unavailable")}>
    <input type="radio" name="ns-model-choice" value=${item.id} checked=${selectedId === item.id}
      disabled=${!item.selectable} ...${helpFor("chat.model_choice")}
      onChange=${() => onSelect(item.id)} />
    <span class="ns-picker-facts"><strong>${item.label}${item.verificationLabel ? " · " + item.verificationLabel : ""}</strong>
      ${item.reason && html`<span class="ns-picker-reason">${item.reason}</span>`}
      <span class="muted small">Comprobado: ${item.checkedAt ? fmtDate(item.checkedAt) : "sin fecha"}</span>
      ${(item.kind === "executor" || item.id === "gemini") && item.quota.map((line) => html`<span class="muted small" key=${line}>${line}</span>`)}
      ${item.warning && html`<span class="ns-picker-notice">${item.warning}</span>`}
      ${item.agyNotice && html`<span class="ns-picker-notice">${item.agyNotice}</span>`}
    </span>
  </label>`;
}

export function ModelPicker({ connections, selectedId, onSelect, loading, error, onRetry, help }) {
  const picker = buildModelPicker(connections, selectedId);
  const group = (label, items) => html`<fieldset class="ns-picker-group">
    <legend>${label}</legend>
    ${items.length === 0 && html`<span class="muted small">Sin opciones conectadas.</span>`}
    ${items.map((item) => html`<${Choice} key=${item.id} item=${item} selectedId=${picker.selectedId} onSelect=${onSelect} />`)}
  </fieldset>`;
  return html`<section class="ns-picker" aria-label="Modelos y ejecutores conectados">
    <div class="ns-row"><strong>Modelos y ejecutores conectados</strong>${help.picker}</div>
    ${loading && html`<p role="status" class="muted small">Comprobando conexiones…</p>`}
    ${error && html`<div class="ns-row"><p role="alert" class="message error">${errorText(error)}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.model_retry")} onClick=${onRetry}>Reintentar</button>
      ${help.retry}
    </div>`}
    ${!loading && !error && html`
      ${picker.empty && html`<p class="muted small" role="status">${picker.empty}</p>`}
      ${group("Modelos", picker.models)}
      ${group("Ejecutores", picker.executors)}
      ${picker.disconnected.length > 0 && html`<details class="ns-picker-disconnected">
        <summary ...${helpFor("chat.model_disconnected")}>No conectados (${picker.disconnected.length})</summary>
        ${help.disconnected}
        <div class="ns-picker-choices">
          ${picker.disconnected.map((item) => html`<${Choice} key=${item.id} item=${item} selectedId=${picker.selectedId} onSelect=${onSelect} />`)}
        </div>
      </details>`}
      ${help.choice}
      ${help.connectionChecks && html`<p class="muted small ns-row">Estado de Gemini y Copilot ${help.connectionChecks}</p>`}
    `}
  </section>`;
}
