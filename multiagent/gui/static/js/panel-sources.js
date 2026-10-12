// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Fuentes» of the right panel (GUI2-T05): the references the open session or run used, as a short list. A session of an
// app: its working folder, the files and folders its tool calls consulted, the web pages it fetched (without query or
// fragment), searches and attachments. A run: its workflow, the prompts and contracts of its steps and the names of its
// inputs. Paths and names only; no message text and no input value is shown.
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { usePanelData } from "./panel-data.js";
import { SHOWN, sourcesCount, sourcesModel } from "./panel-model.js";
import { Empty, Failure, Loading, MoreToggle } from "./panel-parts.js";
import { attachmentsText, cappedText, loadingText, promptText, readsText, searchesText, splitPath } from "./panel-text.js";

const SCOPE = "sources";

function Path({ path, title }) {
  const { dir, name } = splitPath(path);
  return html`<span class="ctx-path" title=${title || path}><span class="ctx-dir">${dir}</span><strong class="ctx-name">${name}</strong></span>`;
}

function SessionSources({ model, expanded, onToggle }) {
  const files = expanded ? model.files : model.files.slice(0, SHOWN);
  const urls = expanded ? model.urls : model.urls.slice(0, SHOWN);
  const extras = [model.searches > 0 && searchesText(model.searches), model.attachments > 0 && attachmentsText(model.attachments)].filter(Boolean);
  return html`<div class="ctx-block">
    ${model.folder && html`<p class="ctx-line"><span class="ctx-label">Carpeta de trabajo</span> <span class="ctx-path" title=${model.folder}>${model.folder}</span></p>`}
    ${files.length > 0 && html`<p class="ctx-label" id="src-files-label">Archivos y carpetas consultados · ${model.files.length}</p>
      <ul class="ctx-list" aria-labelledby="src-files-label">
        ${files.map((file) => html`<li key=${file.path} class="ctx-row"><${Path} path=${file.shown} title=${file.path} />
          <span class="ctx-meta muted small">${readsText(file.count)}</span></li>`)}
      </ul>`}
    ${urls.length > 0 && html`<p class="ctx-label" id="src-urls-label">Páginas web · ${model.urls.length}</p>
      <ul class="ctx-list" aria-labelledby="src-urls-label">
        ${urls.map((entry) => html`<li key=${entry.url} class="ctx-row"><span class="ctx-path" title=${entry.url}>${entry.url}</span>
          <span class="ctx-meta muted small">${readsText(entry.count)}</span></li>`)}
      </ul>`}
    <${MoreToggle} scope=${SCOPE} total=${Math.max(model.files.length, model.urls.length)} shown=${SHOWN} expanded=${expanded} onToggle=${onToggle} />
    ${extras.length > 0 && html`<p class="muted small ctx-note">${extras.join(" · ")}</p>`}
    ${model.more && html`<${Empty}>Hay más rutas de las que se listan: se muestran las más recientes.<//>`}
  </div>`;
}

function RunSources({ model }) {
  return html`<div class="ctx-block">
    ${model.workflow && html`<p class="ctx-line"><span class="ctx-label">Workflow</span> <span class="ctx-path">${model.workflow}${model.bundle ? " (paquete " + model.bundle + ")" : ""}</span></p>`}
    ${model.prompts.length > 0 && html`<p class="ctx-label" id="src-prompts-label">Prompts · ${model.prompts.length}</p>
      <ul class="ctx-list" aria-labelledby="src-prompts-label">
        ${model.prompts.map((prompt) => html`<li key=${promptText(prompt)} class="ctx-row"><span class="ctx-path">${promptText(prompt)}</span></li>`)}
      </ul>`}
    ${model.contracts.length > 0 && html`<p class="ctx-label" id="src-contracts-label">Contratos de los pasos · ${model.contracts.length}</p>
      <ul class="ctx-list" aria-labelledby="src-contracts-label">
        ${model.contracts.map((contract) => html`<li key=${contract} class="ctx-row"><span class="ctx-path">${contract}</span></li>`)}
      </ul>`}
    ${model.inputs.length > 0 && html`<p class="ctx-line"><span class="ctx-label">Entradas (solo los nombres)</span> <span class="ctx-path">${model.inputs.join(", ")}</span></p>`}
    ${model.partial && html`<${Empty}>Solo se revisaron los últimos eventos de la ejecución; puede haber más prompts.<//>`}
  </div>`;
}

export function SourcesPanel({ token, subject }) {
  const data = usePanelData(token, subject);
  const [expanded, setExpanded] = useState(false);
  if (data.status === "error") {
    return html`<${Failure} error=${data.error} scope=${SCOPE} what="las fuentes" onRetry=${() => data.reload && data.reload()} />`;
  }
  if (data.status !== "ready") return html`<${Loading}>Cargando las fuentes…<//>`;
  const model = sourcesModel(subject, data);
  const count = sourcesCount(model);
  const empty = subject.kind === "run" ? "Esta ejecución todavía no usó ninguna referencia." : "Esta sesión no ha consultado archivos ni páginas web por ahora.";
  return html`<div class="ctx" aria-busy=${data.loading ? "true" : "false"}>
    <p class="ctx-summary" role="status" aria-live="polite">${count > 0 ? count + (count === 1 ? " fuente" : " fuentes")
      : data.loading ? "Buscando fuentes…" : empty}</p>
    ${count > 0 && (model.kind === "run"
      ? html`<${RunSources} model=${model} />`
      : html`<${SessionSources} model=${model} expanded=${expanded} onToggle=${() => setExpanded(!expanded)} />`)}
    ${data.loading && html`<${Loading}>${loadingText(data.loaded)}<//>`}
    ${data.capped && html`<${Empty}>${cappedText(data.loaded)}<//>`}
    ${data.error && html`<${Failure} stale=${true} error=${data.error} scope=${SCOPE} what="las fuentes" onRetry=${() => data.reload && data.reload()} />`}
  </div>`;
}
