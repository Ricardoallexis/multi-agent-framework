// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The section «Continuidad» of Uso (CNT-T06): the mode, the wait threshold, the hand-off limit and the candidate executors, as the
// framework applies them today. It reads GET /gui/api/continuity/policy and changes nothing.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "../../js/help.js";
import { policyCandidates, policyRows } from "./model.js";
import { POLICY_POLL_MS, usePolled } from "./poll.js";

export function PolicyView({ policy, now = new Date() }) {
  return html`<div class="continuity-policy-body">
    <dl class="continuity-rows">
      ${policyRows(policy).map(([name, value]) => html`<div class="continuity-row" key=${name}><dt>${name}</dt><dd>${value}</dd></div>`)}
    </dl>
    <h4 class="continuity-subtitle">Aplicaciones candidatas</h4>
    ${policy.candidates && policy.candidates.length
      ? html`<ul class="continuity-candidates">${policyCandidates(policy, now).map((item) => html`<li key=${item.id}>
          <strong>${item.app}</strong><span class="continuity-chip">${item.state}</span><span class="continuity-chip">${item.policy}</span></li>`)}</ul>`
      : html`<p class="muted">No hay aplicaciones con ejecutor configurado.</p>`}
    <p class="muted small">${policy.source === "default" ? "Son los valores predeterminados del framework: todavía no se pueden cambiar desde aquí." : ""}</p>
  </div>`;
}

export function ContinuityPolicy({ token }) {
  const { loading, data, error, reload, refreshing } = usePolled("/gui/api/continuity/policy", token, POLICY_POLL_MS);
  return html`<section class="continuity-policy" aria-labelledby="continuity-usage-title">
    <h3 id="continuity-usage-title">Continuidad sin créditos</h3><${Help} topic="continuity.usage" />
    <span class="continuity-option"><button type="button" class="btn secondary continuity-btn" disabled=${refreshing}
      ...${helpFor("continuity.refresh")} onClick=${reload}>${refreshing ? "Actualizando…" : "Actualizar"}</button>
      <${Help} topic="continuity.refresh" compact=${true} /></span>
    ${error && html`<p class="message error" role="alert">No se pudo leer la política${data ? "; se muestra la última respuesta" : ""}: ${error.message}</p>`}
    ${loading ? html`<p role="status">Cargando…</p>` : data && html`<${PolicyView} policy=${data} />`}
  </section>`;
}
