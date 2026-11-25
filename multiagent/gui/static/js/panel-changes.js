// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Cambios» of the right panel (GUI2-T05): the files the open session changed (tool calls that write, with the +/- lines
// when the source counts them) or the files the results of the open run list. Paths only: no message text is shown.
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { usePanelData } from "./panel-data.js";
import { t } from "./i18n.js";
import { SHOWN, changesModel } from "./panel-model.js";
import { Delta, Empty, Failure, Loading, MoreToggle } from "./panel-parts.js";
import { appearsText, cappedText, changesSummary, editsText, loadingText, relativePath, splitPath } from "./panel-text.js";

const SCOPE = "changes";

function FileRow({ file, folder, run }) {
  const { dir, name } = splitPath(relativePath(file.path, folder));
  const meta = run ? appearsText(file.edits) : editsText(file.edits) + (file.tools.length ? " · " + file.tools.join(", ") : "");
  return html`<li class="ctx-row">
    <span class="ctx-path" title=${file.path}><span class="ctx-dir">${dir}</span><strong class="ctx-name">${name}</strong></span>
    <span class="ctx-meta muted small">${meta}</span>
    <${Delta} file=${file} />
  </li>`;
}

function emptyText(subject, model) {
  if (subject.kind === "session") return t("panel.session_no_changes");
  return model.artifacts > 0
    ? t("panel.run_no_files")
    : t("panel.run_no_results");
}

export function ChangesPanel({ token, subject }) {
  const data = usePanelData(token, subject);
  const [expanded, setExpanded] = useState(false);
  if (data.status === "error") {
    return html`<${Failure} error=${data.error} scope=${SCOPE} what=${t("panel.changes")} onRetry=${() => data.reload && data.reload()} />`;
  }
  if (data.status !== "ready") return html`<${Loading}>${t("panel.loading_changes")}<//>`;
  const model = changesModel(subject, data);
  const files = expanded ? model.files : model.files.slice(0, SHOWN);
  const run = subject.kind === "run";
  const folder = run || !data.summary ? null : data.summary.cwd;
  return html`<div class="ctx" aria-busy=${data.loading ? "true" : "false"}>
    <p class="ctx-summary" role="status" aria-live="polite">${model.files.length ? changesSummary(model.totals)
      : data.loading ? t("panel.searching_changes") : emptyText(subject, model)}</p>
    ${files.length > 0 && html`<ul class="ctx-list" aria-label=${t("panel.changed_files")}>
      ${files.map((file) => html`<${FileRow} key=${file.path} file=${file} folder=${folder} run=${run} />`)}
    </ul>`}
    <${MoreToggle} scope=${SCOPE} total=${model.files.length} shown=${SHOWN} expanded=${expanded} onToggle=${() => setExpanded(!expanded)} />
    ${model.more && html`<${Empty}>${t("panel.more_changes")}<//>`}
    ${data.loading && html`<${Loading}>${loadingText(data.loaded)}<//>`}
    ${data.capped && html`<${Empty}>${cappedText(data.loaded)}<//>`}
    ${data.error && html`<${Failure} stale=${true} error=${data.error} scope=${SCOPE} what=${t("panel.changes")} onRetry=${() => data.reload && data.reload()} />`}
  </div>`;
}
