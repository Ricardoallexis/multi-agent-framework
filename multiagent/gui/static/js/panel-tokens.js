// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Tokens» of the right panel (GUI2-T05): orchestrator against workers, the estimated cost and the price evidence
// behind it. A session of an app: the session is the orchestrator and its subagents and threads the workers; the apps
// do not report prices, so its cost reads «desconocido». A framework run: the workflow engine uses no model and every
// step is a worker; cost and evidence come from the run telemetry. A number nobody reported is never shown as 0.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { Help } from "./help.js";
import { usePanelData } from "./panel-data.js";
import { tokensModel } from "./panel-model.js";
import { Empty, Failure, Loading } from "./panel-parts.js";
import { UNKNOWN, costNote, costText, evidenceText, priceText, tokensLine, withoutData } from "./panel-text.js";

const SCOPE = "tokens";

function workersDetail(workers, kind) {
  if (!workers.readable) return "No se pudo leer la lista de trabajadores.";
  if (kind === "run") {
    const parts = [workers.count === 1 ? "1 llamada" : workers.count + " llamadas"];
    if (workers.failed > 0) parts.push(workers.failed + (workers.failed === 1 ? " fallida" : " fallidas"));
    return parts.join(" · ");
  }
  if (workers.count === 0) return "Esta sesión no lanzó subagentes.";
  return workers.count === 1 ? "1 subagente o hilo" : workers.count + " subagentes o hilos";
}

function Part({ name, detail, value, note }) {
  return html`<li class="ctx-row">
    <strong class="ctx-name">${name}</strong>
    ${detail && html`<span class="ctx-meta muted small">${detail}</span>`}
    <span class="ctx-value">${value}</span>
    ${note && html`<span class="ctx-meta muted small">${note}</span>`}
  </li>`;
}

function ModelRow({ entry, run }) {
  const unit = run ? ["llamada", "llamadas"] : ["subagente o hilo", "subagentes o hilos"];
  const detail = [entry.level && "nivel " + entry.level, entry.count + " " + unit[entry.count === 1 ? 0 : 1]].filter(Boolean).join(" · ");
  return html`<li class="ctx-row">
    <strong class="ctx-name">${entry.model}</strong><span class="ctx-meta muted small">${detail}</span>
    <span class="ctx-value">${tokensLine(entry.tokens)}</span>
    ${entry.unreported > 0 && html`<span class="ctx-meta muted small">${withoutData(entry.unreported)}</span>`}
  </li>`;
}

function Evidence({ item }) {
  const same = evidenceText(item.input) === evidenceText(item.output);
  return html`<li class="ctx-row">
    <strong class="ctx-name">${item.model}</strong>
    <span class="ctx-meta small">Entrada: ${priceText(item.input, item.currency)}</span>
    <span class="ctx-meta small">Salida: ${priceText(item.output, item.currency)}</span>
    <span class="ctx-meta muted small">${same ? "Evidencia: " + evidenceText(item.input)
      : "Evidencia de entrada: " + evidenceText(item.input) + ". Evidencia de salida: " + evidenceText(item.output)}</span>
  </li>`;
}

export function TokensPanel({ token, subject }) {
  const data = usePanelData(token, subject);
  if (data.status === "error") {
    return html`<${Failure} error=${data.error} scope=${SCOPE} what="los tokens" onRetry=${() => data.reload && data.reload()} />`;
  }
  if (data.status !== "ready") return html`<${Loading}>Cargando los tokens…<//>`;
  if (subject.kind === "run" && !data.usage) {
    return data.usageError
      ? html`<${Failure} error=${data.usageError} scope=${SCOPE} what="el uso de tokens de la ejecución" onRetry=${() => data.reload && data.reload()} />`
      : html`<${Empty}>Esta ejecución todavía no tiene datos de uso.<//>`;
  }
  const model = tokensModel(subject, data);
  const { orchestrator, workers, cost } = model;
  const run = model.kind === "run";
  const totalText = tokensLine(model.total) + (model.total && model.incomplete ? " (incompleto)" : "");
  return html`<div class="ctx">
    <p class="ctx-summary" role="status" aria-live="polite">Total: ${totalText}</p>
    <ul class="ctx-list" aria-label="Tokens del orquestador y de los trabajadores">
      <${Part} name=${orchestrator.label}
        detail=${orchestrator.noModel ? "Solo coordina los pasos; no llama a ningún modelo." : orchestrator.models.join(", ") || "modelo sin indicar"}
        value=${orchestrator.noModel ? "sin tokens propios" : tokensLine(orchestrator.tokens, { cache: true })} />
      <${Part} name=${workers.label} detail=${workersDetail(workers, model.kind)}
        value=${workers.tokens ? tokensLine(workers.tokens, { cache: true }) : workers.readable && workers.count === 0 ? "ninguno" : UNKNOWN}
        note=${withoutData(workers.unreported)} />
    </ul>
    <p class="ctx-cost"><span class="ctx-label">Costo estimado</span> <strong class="ctx-value">${costText(cost)}</strong></p>
    <p class="muted small ctx-note">${costNote(cost)}</p>
    ${model.byModel.length > 0 && html`<div class="ctx-details-row">
      <details class="ctx-details"><summary>${run ? "Por modelo" : "Trabajadores por modelo"} · ${model.byModel.length}</summary>
        <ul class="ctx-list" aria-label=${run ? "Tokens por modelo" : "Tokens de los trabajadores por modelo"}>
          ${model.byModel.map((entry) => html`<${ModelRow} key=${entry.model + (entry.level || "")} entry=${entry} run=${run} />`)}
        </ul>
      </details>
      <${Help} topic="panel.by_model" scope=${SCOPE} compact=${true} />
    </div>`}
    ${cost.evidence.length > 0 && html`<div class="ctx-details-row">
      <details class="ctx-details"><summary>Evidencia de precio · ${cost.evidence.length}</summary>
        <ul class="ctx-list" aria-label="Precio usado por modelo">
          ${cost.evidence.map((item) => html`<${Evidence} key=${item.model} item=${item} />`)}
        </ul>
      </details>
      <${Help} topic="panel.cost_evidence" scope=${SCOPE} compact=${true} />
    </div>`}
    ${data.error && html`<${Failure} stale=${true} error=${data.error} scope=${SCOPE} what="los tokens" onRetry=${() => data.reload && data.reload()} />`}
  </div>`;
}
