// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Vista de evaluación (EVA-T03), de solo lectura: los conjuntos con su última ejecución y su estado respecto de la línea
// base, y, al abrir uno ("#evaluation/<conjunto>"), sus ejecuciones guardadas y la comparación de la última con la línea
// base. No ejecuta conjuntos ni fija líneas base: eso se hace con la CLI o la API. Todo lo que llega es texto no confiable.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { routeHref } from "./route.js";
import { StatusChip } from "./ui/index.js";

export const SET_STATUS = {
  regression: { text: "Regresión", tone: "danger", explanation: "La última ejecución empeoró respecto de la línea base." },
  no_baseline: { text: "Sin línea base", tone: "neutral", explanation: "Ninguna ejecución se fijó como línea base." },
  up_to_date: { text: "Al día", tone: "success", explanation: "La última ejecución no empeoró respecto de la línea base." },
};
export const CASE_STATUS = {
  regressed: { text: "Regresión", tone: "danger" },
  improved: { text: "Mejora", tone: "success" },
  changed: { text: "Cambió", tone: "attention" },
  unchanged: { text: "Sin cambios", tone: "muted" },
};
const CASE_ORDER = ["regressed", "improved", "changed", "unchanged"];
export const FINGERPRINT_LABEL = {
  agent_or_prompt: "Agente o prompt", workflow: "Flujo de trabajo", models: "Modelos y proveedores", set_changed: "El conjunto",
};
const LIST_PATH = "/gui/api/evaluations";

export const scoreText = (score) => typeof score === "number" && Number.isFinite(score) ? Math.round(score * 100) + " %" : "Sin puntuación";
export const deltaText = (delta) => typeof delta !== "number" || !Number.isFinite(delta) ? "Sin dato"
  : (delta > 0 ? "+" : "") + (Math.round(delta * 1000) / 10) + " puntos";
export const modeText = (run) => run && run.live ? "En vivo" : "Mock";
const when = (value) => {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Fecha no disponible" : new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(date);
};

// Cases ordered by what matters first: regressions, improvements, changes, the rest.
export function orderCases(cases) {
  const rank = (item) => { const index = CASE_ORDER.indexOf(item.status); return index < 0 ? CASE_ORDER.length : index; };
  return (Array.isArray(cases) ? cases : []).map((item, index) => [item, index])
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map((pair) => pair[0]);
}

// The kinds of change of the fingerprint that are true, as labels.
export const fingerprintChanges = (fingerprint) =>
  Object.keys(FINGERPRINT_LABEL).filter((key) => fingerprint && fingerprint[key] === true).map((key) => FINGERPRINT_LABEL[key]);

function Failure({ error }) {
  return html`<p class="message error" role="alert">${error && error.message ? error.message : "No se pudo leer la evaluación."}</p>`;
}

export function SetList({ data }) {
  const sets = Array.isArray(data && data.sets) ? data.sets : [];
  if (!sets.length) return html`<p>Todavía no hay ejecuciones de evaluación guardadas.</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">Conjuntos de evaluación</caption>
    <thead><tr><th scope="col">Conjunto</th><th scope="col">Última puntuación</th><th scope="col">Tipo</th><th scope="col">Línea base</th><th scope="col">Estado</th></tr></thead>
    <tbody>${sets.map((entry) => html`<tr key=${entry.set_name}>
      <th scope="row"><a href=${routeHref("evaluation", entry.set_name)}>${entry.set_name}</a></th>
      <td>${scoreText(entry.latest_run && entry.latest_run.score)} <span class="muted small">${when(entry.latest_run && entry.latest_run.created_at)}</span></td>
      <td>${modeText(entry.latest_run)}</td>
      <td>${entry.baseline ? scoreText(entry.baseline.run && entry.baseline.run.score) : "Sin línea base"}</td>
      <td><${StatusChip} status=${entry.status} labels=${SET_STATUS} /></td>
    </tr>`)}</tbody>
  </table></div>`;
}

export function RunsTable({ runs, baselineId }) {
  if (!runs.length) return html`<p>Este conjunto no tiene ejecuciones guardadas.</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">Ejecuciones guardadas del conjunto</caption>
    <thead><tr><th scope="col">Fecha</th><th scope="col">Versión</th><th scope="col">Puntuación</th><th scope="col">Tipo</th><th scope="col">Quién</th><th scope="col">Línea base</th></tr></thead>
    <tbody>${runs.map((run) => html`<tr key=${run.id}>
      <th scope="row">${when(run.created_at)}</th>
      <td>${run.set_version || "Sin versión"}</td>
      <td>${scoreText(run.score)}</td>
      <td>${modeText(run)}</td>
      <td>${run.actor || "Sin dato"}</td>
      <td>${run.id === baselineId ? "Sí" : ""}</td>
    </tr>`)}</tbody>
  </table></div>`;
}

export function ComparisonView({ comparison }) {
  const summary = comparison.summary || {};
  const cases = orderCases(comparison.cases);
  const changes = fingerprintChanges(comparison.fingerprint);
  const only = (list) => Array.isArray(list) ? list : [];
  return html`<div class="usage-comparison">
    <p><${StatusChip} status=${comparison.regression ? "regression" : "up_to_date"} labels=${SET_STATUS} />
      ${" "}Puntuación ${scoreText(comparison.score && comparison.score.base)} en la línea base, ${scoreText(comparison.score && comparison.score.candidate)} en la última
      (${deltaText(comparison.score && comparison.score.delta)}). Umbral de regresión: ${scoreText(comparison.threshold)}.</p>
    <p class="muted small">${CASE_ORDER.map((name) => CASE_STATUS[name].text + ": " + (summary[name] || 0)).join(" · ")}</p>
    <h4>Casos</h4><${Help} topic="evaluation.cases" />
    ${cases.length ? html`<div class="usage-table-wrap"><table class="usage-table">
      <caption class="sr-only">Casos respecto de la línea base</caption>
      <thead><tr><th scope="col">Caso</th><th scope="col">Resultado</th><th scope="col">Línea base</th><th scope="col">Última</th><th scope="col">Comprobaciones distintas</th></tr></thead>
      <tbody>${cases.map((item) => html`<tr key=${item.id}>
        <th scope="row">${item.id}</th>
        <td><${StatusChip} status=${item.status} labels=${CASE_STATUS} /></td>
        <td>${scoreText(item.base_score)} · ${item.base_passed ? "pasa" : "falla"}</td>
        <td>${scoreText(item.candidate_score)} · ${item.candidate_passed ? "pasa" : "falla"}</td>
        <td>${Array.isArray(item.changed_checks) && item.changed_checks.length ? item.changed_checks.join(", ") : "Ninguna"}</td>
      </tr>`)}</tbody>
    </table></div>` : html`<p>Las dos ejecuciones no comparten casos.</p>`}
    ${only(comparison.only_in_base).length > 0 && html`<p><strong>Solo en la línea base:</strong> ${only(comparison.only_in_base).join(", ")}.</p>`}
    ${only(comparison.only_in_candidate).length > 0 && html`<p><strong>Solo en la última ejecución:</strong> ${only(comparison.only_in_candidate).join(", ")}.</p>`}
    <h4>Qué cambió en la huella</h4><${Help} topic="evaluation.fingerprint" />
    <p>${changes.length ? changes.join(", ") + "." : "Nada: el agente o prompt, el flujo, los modelos y el conjunto son los mismos."}</p>
  </div>`;
}

function useLoad(path, token, ready = true) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const ticket = useRef(0);
  const load = async () => {
    if (!ready) return;
    const mine = ++ticket.current;
    setState((previous) => ({ ...previous, loading: true }));
    try {
      const data = await apiGet(path, token);
      if (mine === ticket.current) setState({ loading: false, data, error: null });
    } catch (error) {
      if (mine === ticket.current) setState((previous) => ({ loading: false, data: previous.data, error }));
    }
  };
  useEffect(() => { load(); return () => { ticket.current += 1; }; }, [path, token, ready]);
  return [state, load];
}

function SetDetail({ token, setName, reload }) {
  const query = "set_name=" + encodeURIComponent(setName);
  const [runs, loadRuns] = useLoad(LIST_PATH + "/runs?" + query, token);
  const [comparison, loadComparison] = useLoad(LIST_PATH + "/comparison?" + query, token);
  useEffect(() => { reload.current = () => { loadRuns(); loadComparison(); }; return () => { reload.current = null; }; });
  const list = runs.data && Array.isArray(runs.data.runs) ? runs.data.runs : [];
  const noBaseline = comparison.error && comparison.error.status === 404;
  return html`<div>
    <p><a href=${routeHref("evaluation")}>← Todos los conjuntos</a></p>
    <h4>Ejecuciones de ${setName}</h4><${Help} topic="evaluation.runs" />
    ${runs.error && html`<${Failure} error=${runs.error} />`}
    ${runs.loading && !runs.data ? html`<p role="status" aria-live="polite">Cargando las ejecuciones…</p>`
      : runs.data && html`<${RunsTable} runs=${list} baselineId=${comparison.data && comparison.data.base_id} />`}
    <h4>Última ejecución frente a la línea base</h4><${Help} topic="evaluation.compare" />
    ${noBaseline ? html`<p>Este conjunto no tiene línea base: no hay con qué comparar. Se fija con <code>multiagent eval baseline set</code>.</p>`
      : comparison.error ? html`<${Failure} error=${comparison.error} />`
      : comparison.loading && !comparison.data ? html`<p role="status" aria-live="polite">Cargando la comparación…</p>`
      : comparison.data && html`<${ComparisonView} comparison=${comparison.data} />`}
  </div>`;
}

export function Evaluation({ token, route }) {
  const setName = route && route.id ? route.id : null;
  const [sets, loadSets] = useLoad(LIST_PATH, token, !setName);
  const reload = useRef(null);
  const refresh = () => { if (setName) { if (reload.current) reload.current(); } else loadSets(); };
  const busy = !setName && sets.loading;
  return html`<div class="usage-view">
    <h3 id="evaluation-title">Conjuntos de evaluación</h3><${Help} topic="evaluation.section" />
    <div class="usage-title-row"><p class="muted">Conjuntos de evaluación y sus ejecuciones guardadas. Solo lectura.</p>
      <div class="usage-refresh-control" role="group" aria-label="Actualizar la evaluación">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("evaluation.refresh")} onClick=${refresh}>${busy ? "Actualizando…" : "Actualizar"}</button>
        <${Help} topic="evaluation.refresh" compact=${true} />
      </div>
    </div>
    ${setName ? html`<${SetDetail} key=${setName} token=${token} setName=${setName} reload=${reload} />` : html`<div>
      <h4>Conjuntos</h4><${Help} topic="evaluation.sets" />
      ${sets.error && html`<${Failure} error=${sets.error} />`}
      ${sets.loading && !sets.data ? html`<p role="status" aria-live="polite">Cargando los conjuntos…</p>` : sets.data && html`<${SetList} data=${sets.data} />`}
    </div>`}
  </div>`;
}
