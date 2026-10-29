// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The section «Continuidad» of the right panel (CNT-T06): the decision card of the task held on the session that is open, or a line
// saying that nothing waits. It reads GET /gui/api/continuity/session/<id>.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { DecisionCard } from "./card.js";
import { usePolled } from "./poll.js";

export function ContinuitySlot({ token, sessionId }) {
  const path = sessionId ? "/gui/api/continuity/session/" + encodeURIComponent(sessionId) : null;
  const { loading, data, error, reload } = usePolled(path, token);
  if (!sessionId) return html`<p class="muted small slot-empty">Abre una sesión para ver si su tarea espera por un límite de uso.</p>`;
  if (loading) return html`<p class="muted small" role="status">Buscando…</p>`;
  if (!data) return html`<p class="message error small" role="alert">No se pudo leer la continuidad de esta sesión${error ? ": " + error.message : "."}</p>`;
  if (!data.held || !data.card) return html`<p class="muted small slot-empty">Esta sesión no espera por ningún límite de uso.</p>`;
  return html`<div class="continuity-slot">
    ${error && html`<p class="message error small" role="alert">No se pudo actualizar: ${error.message}</p>`}
    <${DecisionCard} key=${data.card.task_id} token=${token} card=${data.card} showOpen=${false} onChanged=${reload} onStale=${reload} />
  </div>`;
}
