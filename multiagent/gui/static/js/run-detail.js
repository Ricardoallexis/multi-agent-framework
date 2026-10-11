// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// One run: state, steps (with skipped ones and why), counters, human history and results.
// It refreshes when the shared SSE stream carries an event of this run; polling is only the
// degraded mode (stream down, or a bundle run whose store the stream does not follow yet).
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, runPath } from "./api.js";
import { debounce, subscribe } from "./bus.js";
import { HumanActions } from "./human-actions.js";
import {
  actorText, cursorText, errorText, executorText, fmtDate, fmtJson, historyText, skipReason,
  statusInfo, stepStateLabel, waitingText, MODE_LABELS,
} from "./text.js";

const DEGRADED_POLL_MS = 10000;
const REFRESH_DEBOUNCE_MS = 250;

function Counters({ view }) {
  const counters = view.counters;
  return html`<section class="panel" aria-labelledby="counters-title">
    <h4 id="counters-title">Contadores</h4>
    <dl class="facts">
      <dt>Llamadas al modelo</dt>
      <dd>${counters.model_calls} de ${counters.max_model_calls} máximo
        <span class="help">Solo cuentan las llamadas que el framework hace a un modelo. Los pasos que hace una persona o un modelo externo (modo guiado) no cuentan.</span></dd>
      <dt>Pasos hechos por una persona</dt>
      <dd>${counters.human_steps}</dd>
      <dt>Pasos</dt>
      <dd>${counters.steps_done} hechos de ${counters.steps_total}${counters.steps_skipped ? " (" + counters.steps_skipped + (counters.steps_skipped === 1 ? " saltado)" : " saltados)") : ""}</dd>
      <dt>Tiempo activo</dt>
      <dd>${counters.active_seconds} s de ${counters.max_seconds} s máximo</dd>
    </dl>
  </section>`;
}

function Steps({ view }) {
  return html`<section class="panel" aria-labelledby="steps-title">
    <h4 id="steps-title">Pasos</h4>
    <p class="muted">${cursorText(view)}</p>
    <ol class="steps">
      ${view.steps.map((step) => html`<li key=${step.id} class=${"step " + step.state}>
        <span class="step-name">${step.id}</span>
        <span class=${"badge step-" + step.state}>${stepStateLabel(step.state)}</span>
        ${step.skip && html`<span class="step-note">${step.state === "will_skip" ? "Se saltará " : "Se saltó "}${skipReason(step.skip)}.</span>`}
        ${step.state === "done" && step.executor && html`<span class="step-note">${executorText(step.executor)}${step.attempts > 1 ? " · " + step.attempts + " intentos" : ""}.</span>`}
        ${step.state === "not_run" && html`<span class="step-note">La ejecución terminó antes de llegar a este paso.</span>`}
      </li>`)}
    </ol>
  </section>`;
}

function History({ view }) {
  return html`<section class="panel" aria-labelledby="history-title">
    <h4 id="history-title">Historial de acciones humanas</h4>
    ${view.history.length === 0
      ? html`<p class="muted">Todavía no hay acciones humanas en esta ejecución.</p>`
      : html`<ol class="history">
        ${view.history.map((entry) => html`<li key=${entry.seq}>
          <span class="when">${fmtDate(entry.ts)}</span>
          <span class="who">${actorText(entry)}</span>
          <span class="what">${historyText(entry)}</span>
          ${entry.feedback && html`<blockquote>${entry.feedback}</blockquote>`}
          ${entry.error && html`<blockquote class="error-text">${entry.error}</blockquote>`}
        </li>`)}
      </ol>`}
  </section>`;
}

function Results({ view }) {
  return html`<section class="panel" aria-labelledby="results-title">
    <h4 id="results-title">Resultados</h4>
    ${view.artifacts.length === 0
      ? html`<p class="muted">Todavía no hay resultados guardados.</p>`
      : view.artifacts.map((artifact, index) => html`<details key=${artifact.step_id + ":" + artifact.attempt}
          open=${view.waiting && view.waiting.step_id === artifact.step_id && index === view.artifacts.length - 1}>
        <summary>Paso «${artifact.step_id}» · intento ${artifact.attempt} · ${artifact.approved ? "aprobado" : "sin aprobar"} · ${artifact.kind}</summary>
        <pre class="data">${fmtJson(artifact.data)}</pre>
      </details>`)}
  </section>`;
}

function Technical({ view }) {
  return html`<details class="panel technical">
    <summary>Eventos técnicos (${view.events_total}${view.events_total > view.events.length ? ", últimos " + view.events.length : ""})</summary>
    <ol class="events">
      ${view.events.map((event) => html`<li key=${event.seq}><code>#${event.seq}</code> ${fmtDate(event.ts)} · ${event.type}
        ${Object.keys(event.payload || {}).length > 0 && html`<pre class="data">${fmtJson(event.payload)}</pre>`}</li>`)}
    </ol>
  </details>`;
}

export function RunDetail({ token, runId, status, onChanged }) {
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const latest = useRef(0);
  const current = useRef(runId);
  current.current = runId;

  async function load() {
    const ticket = ++latest.current;
    const asked = runId;
    try {
      const result = await apiGet(runPath(asked), token);
      if (ticket !== latest.current || current.current !== asked) return; // a newer answer or another run
      setView(result);
      setError(null);
    } catch (failure) {
      if (ticket !== latest.current || current.current !== asked) return;
      setError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    }
  }

  function accept(updated) {
    latest.current += 1; // answers requested before this action are older than it
    setView(updated);
    setError(null);
    if (onChanged) onChanged();
  }

  useEffect(() => { setView(null); setError(null); load(); }, [runId, token]);

  useEffect(() => {
    const refresh = debounce(load, REFRESH_DEBOUNCE_MS);
    const off = subscribe((event) => { if (event.run_id === runId) refresh(); });
    return () => { off(); refresh.cancel(); };
  }, [runId, token]);

  useEffect(() => { if (status === "connected") load(); }, [status]);

  const live = status === "connected";
  const polling = view && !view.terminal && (!live || Boolean(view.bundle));
  useEffect(() => {
    if (!polling) return undefined;
    const timer = setInterval(load, DEGRADED_POLL_MS);
    return () => clearInterval(timer);
  }, [polling, runId, token]);

  if (!view) {
    return error
      ? html`<p class="message error" role="alert">${errorText(error)}</p>`
      : html`<p class="muted" aria-busy="true">Cargando la ejecución…</p>`;
  }
  const info = statusInfo(view.status);
  return html`<article class="run-detail" aria-labelledby="run-title">
    <header>
      <h3 id="run-title">${view.workflow_id} <span class="muted">· ${view.project_name || "sin proyecto"}</span></h3>
      <p>
        <span class=${"badge tone-" + info.tone}>${info.label}</span>
        ${!view.terminal && html`<span class=${"badge " + (live ? "connected" : "reconnecting")}>${live ? "En vivo" : "Sin conexión en vivo"}</span>`}
        <button type="button" class="btn secondary small" onClick=${load}>Actualizar</button>
      </p>
      <p class="muted">Ejecución <code>${view.id}</code>${" · creada " + fmtDate(view.created_at) + " · actualizada " + fmtDate(view.updated_at) +
        " · " + (view.bundle ? "bundle «" + view.bundle + "»" : "workflow incluido") +
        " · modo: " + (MODE_LABELS[view.execution_mode] || view.execution_mode)}</p>
    </header>
    ${polling && html`<p class="notice">${view.bundle && live
      ? "Las ejecuciones de un bundle todavía no llegan por el flujo en vivo; la vista se actualiza cada 10 segundos."
      : "Se perdió la conexión en vivo; la vista se actualiza cada 10 segundos hasta que vuelva."}</p>`}
    ${error && html`<p class="message error" role="alert">No se pudo actualizar: ${errorText(error)}</p>`}
    <p class="summary">${view.waiting ? waitingText(view.waiting) : info.help}${view.cancel_requested && !view.terminal ? " Se pidió cancelar." : ""}</p>
    ${view.error && html`<p class="message error" role="alert">Error de la ejecución: ${view.error}</p>`}
    <${HumanActions} token=${token} view=${view} onUpdate=${accept} onStale=${load} />
    <${Steps} view=${view} />
    <${Counters} view=${view} />
    <${History} view=${view} />
    <${Results} view=${view} />
    <section class="panel" aria-labelledby="inputs-title">
      <h4 id="inputs-title">Entradas de la ejecución</h4>
      ${Object.keys(view.inputs).length === 0
        ? html`<p class="muted">Esta ejecución no recibió entradas.</p>`
        : html`<dl class="facts">${Object.entries(view.inputs).map(([name, value]) => html`<${Input} key=${name} name=${name} value=${value} />`)}</dl>`}
    </section>
    <${Technical} view=${view} />
  </article>`;
}

// Two sibling roots: htm returns them as an array, which Preact renders as is.
function Input({ name, value }) {
  return html`<dt><code>${name}</code></dt><dd>${value || html`<span class="muted">(vacía)</span>`}</dd>`;
}
