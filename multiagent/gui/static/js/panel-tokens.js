// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Tokens» of the right panel (GUI2-T05): orchestrator against workers, the estimated cost and the price evidence
// behind it. A session of an app: the session is the orchestrator and its subagents and threads the workers; the apps
// do not report prices, so its cost reads «desconocido». A framework run: the workflow engine uses no model and every
// step is a worker; cost and evidence come from the run telemetry. A number nobody reported is never shown as 0.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { Help } from "./help.js";
import { t } from "./i18n.js";
import { usePanelData } from "./panel-data.js";
import { tokensModel } from "./panel-model.js";
import { Empty, Failure, Loading } from "./panel-parts.js";
import { costNote, costText, evidenceText, priceText, tokensLine, withoutData } from "./panel-text.js";

const SCOPE = "tokens";

function workersDetail(workers, kind) {
  if (!workers.readable) return t("panel.workers_error");
  if (kind === "run") {
    const parts = [workers.count === 1 ? "1 " + t("panel.call_one") : workers.count + " " + t("panel.calls_many")];
    if (workers.failed > 0) parts.push(workers.failed + (workers.failed === 1 ? t("panel.failed_one") : t("panel.failed_many")));
    return parts.join(" · ");
  }
  if (workers.count === 0) return t("panel.no_workers");
  return workers.count === 1 ? t("panel.one_worker") : workers.count + t("panel.many_workers");
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
  const unit = run ? [t("panel.call_unit"), t("panel.calls_unit")] : [t("panel.worker_unit"), t("panel.workers_unit")];
  const detail = [entry.level && t("panel.level") + entry.level, entry.count + " " + unit[entry.count === 1 ? 0 : 1]].filter(Boolean).join(" · ");
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
    <span class="ctx-meta small">${t("panel.input_price")}${priceText(item.input, item.currency)}</span>
    <span class="ctx-meta small">${t("panel.output_price")}${priceText(item.output, item.currency)}</span>
    <span class="ctx-meta muted small">${same ? t("panel.evidence") + evidenceText(item.input)
      : t("panel.input_evidence") + evidenceText(item.input) + t("panel.output_evidence") + evidenceText(item.output)}</span>
  </li>`;
}

export function TokensPanel({ token, subject }) {
  const data = usePanelData(token, subject);
  if (data.status === "error") {
    return html`<${Failure} error=${data.error} scope=${SCOPE} what=${t("panel.tokens")} onRetry=${() => data.reload && data.reload()} />`;
  }
  if (data.status !== "ready") return html`<${Loading}>${t("panel.loading_tokens")}<//>`;
  if (subject.kind === "run" && !data.usage) {
    return data.usageError
      ? html`<${Failure} error=${data.usageError} scope=${SCOPE} what=${t("panel.run_token_usage")} onRetry=${() => data.reload && data.reload()} />`
      : html`<${Empty}>${t("panel.run_no_usage")}<//>`;
  }
  const model = tokensModel(subject, data);
  const { orchestrator, workers, cost } = model;
  const run = model.kind === "run";
  const totalText = tokensLine(model.total) + (model.total && model.incomplete ? t("panel.incomplete") : "");
  return html`<div class="ctx">
    <p class="ctx-summary" role="status" aria-live="polite">${t("panel.total_heading")}${totalText}</p>
    <ul class="ctx-list" aria-label=${t("panel.tokens_aria")}>
      <${Part} name=${orchestrator.label}
        detail=${orchestrator.noModel ? t("panel.no_model_call") : orchestrator.models.join(", ") || t("panel.no_model_reported")}
        value=${orchestrator.noModel ? t("panel.no_own_tokens") : tokensLine(orchestrator.tokens, { cache: true })} />
      <${Part} name=${workers.label} detail=${workersDetail(workers, model.kind)}
        value=${workers.tokens ? tokensLine(workers.tokens, { cache: true }) : workers.readable && workers.count === 0 ? t("panel.none") : t("panel.unknown")}
        note=${withoutData(workers.unreported)} />
    </ul>
    <p class="ctx-cost"><span class="ctx-label">${t("panel.estimated_cost")}</span> <strong class="ctx-value">${costText(cost)}</strong></p>
    <p class="muted small ctx-note">${costNote(cost)}</p>
    ${model.byModel.length > 0 && html`<div class="ctx-details-row">
      <details class="ctx-details"><summary>${run ? t("panel.by_model") : t("panel.workers_by_model")} · ${model.byModel.length}</summary>
        <ul class="ctx-list" aria-label=${run ? t("panel.tokens_by_model") : t("panel.worker_tokens_by_model")}>
          ${model.byModel.map((entry) => html`<${ModelRow} key=${entry.model + (entry.level || "")} entry=${entry} run=${run} />`)}
        </ul>
      </details>
      <${Help} topic="panel.by_model" scope=${SCOPE} compact=${true} />
    </div>`}
    ${cost.evidence.length > 0 && html`<div class="ctx-details-row">
      <details class="ctx-details"><summary>${t("panel.price_evidence")}${cost.evidence.length}</summary>
        <ul class="ctx-list" aria-label=${t("panel.price_by_model")}>
          ${cost.evidence.map((item) => html`<${Evidence} key=${item.model} item=${item} />`)}
        </ul>
      </details>
      <${Help} topic="panel.cost_evidence" scope=${SCOPE} compact=${true} />
    </div>`}
    ${data.error && html`<${Failure} stale=${true} error=${data.error} scope=${SCOPE} what=${t("panel.tokens")} onRetry=${() => data.reload && data.reload()} />`}
  </div>`;
}
