// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The selector reads only connection facts. Launching belongs to the session flow.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { helpFor } from "./help.js";
import { buildModelPicker } from "./model-picker-model.js";
import { errorText, fmtDate } from "./text.js";

export function ModelPicker({ connections, selectedId, onSelect, loading, error, onRetry, onConnect, help = {} }) {
  const picker = buildModelPicker(connections, selectedId);
  const selected = picker.models.find((item) => item.id === picker.selectedId);
  return html`<div class="ns-model-select">
    <label for="ns-model-select">Modelo</label>
    <select id="ns-model-select" value=${picker.selectedId || ""} ...${helpFor("chat.model_picker")}
      onChange=${(event) => event.target.value === "connect" ? onConnect() : onSelect(event.target.value || null)}>
      <option value="">${picker.models.length ? "Elegir modelo" : "Conecta un modelo"}</option>
      ${picker.models.map((item) => html`<option key=${item.id} value=${item.id}>${item.label}${item.verificationLabel ? " · " + item.verificationLabel : ""}</option>`)}
      <option value="connect">Conectar otro… (${picker.disconnected.length} sin conectar)</option>
    </select>
    ${help.picker}${help.connect}
    ${loading && html`<p role="status" class="muted small">Comprobando conexiones…</p>`}
    ${error && html`<div class="ns-row"><p role="alert" class="message error">${errorText(error)}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.model_retry")} onClick=${onRetry}>Reintentar</button></div>`}
    ${error && help.retry}
    ${!loading && !error && !picker.models.length && html`<p class="ns-picker-notice" role="status">Conecta un modelo para usarlo.</p>`}
    ${selected && html`<p class="muted small ns-model-facts">${selected.label} · ${selected.verificationLabel || "Conectado"} · Comprobado: ${selected.checkedAt ? fmtDate(selected.checkedAt) : "sin fecha"}
      ${selected.quota.map((line) => html`<span key=${line}> · ${line}</span>`)}
      ${selected.warning && html`<span class="ns-picker-notice"> · ${selected.warning}</span>`}
    </p>`}
    ${selected && help.choice}
    ${picker.disconnected.length > 0 && html`<details class="ns-picker-disconnected">
      <summary ...${helpFor("chat.model_disconnected")}>Sin conectar (${picker.disconnected.length})</summary>
      ${help.disconnected}
      ${picker.disconnected.map((item) => html`<p key=${item.id} class="muted small">${item.label} · ${item.reason}
        ${item.quota.map((line) => html`<span key=${line}> · ${line}</span>`)}
        ${item.warning && html`<span> · ${item.warning}</span>`}
        ${item.agyNotice && html`<span> · ${item.agyNotice}</span>`}
      </p>`)}
    </details>`}
  </div>`;
}
