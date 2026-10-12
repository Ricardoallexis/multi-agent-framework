// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The bodies of the four sections of the right panel (GUI2-T02). Each section holds one slot component, and each task
// that owns a section replaces the body of ONE component here and nothing else:
//   ChangesSlot    GUI2-T05  changes per file                      (panel-changes.js)
//   SubagentsSlot  GUI2-T04  subagents of the open session (tabs Activos / Terminados)
//   TokensSlot     GUI2-T05  tokens of the orchestrator and the workers  (panel-tokens.js)
//   SourcesSlot    GUI2-T05  sources of the session or run          (panel-sources.js)
// Props every slot receives: token (the local GUI token, for apiGet), sessionId (id of the session open in the centre,
// "claude:...", "codex:..." and so on, or null when the centre shows the list, a run or the create form) and route.
// right-panel.js draws the title and the "?" help of each section around its slot; a slot draws only its content.
// SubagentsSlot is filled (GUI2-T04): it holds the subagents panel through subagents-mount.js.
// The three sections of GUI2-T05 are about the session OR the run open in the centre: subjectOf() (panel-model.js) turns
// the route and the session id into it, and a run (#runs/<id>) is read through the run routes. With nothing open each
// section says so.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { ChangesPanel } from "./panel-changes.js";
import { subjectOf } from "./panel-model.js";
import { SourcesPanel } from "./panel-sources.js";
import { TokensPanel } from "./panel-tokens.js";
import { SubagentsMount } from "./subagents-mount.js";

function Empty({ children }) {
  return html`<p class="muted small slot-empty">${children}</p>`;
}

// The panel of the open subject, one instance per session or run (the key), or the empty state when nothing is open.
function Subject({ Panel, token, route, sessionId, empty }) {
  const subject = subjectOf(route, sessionId);
  return subject
    ? html`<${Panel} key=${subject.key} token=${token} subject=${subject} />`
    : html`<${Empty}>${empty}<//>`;
}

export function ChangesSlot({ token, route, sessionId }) {
  return html`<div class="slot" data-slot="changes"><${Subject} Panel=${ChangesPanel} token=${token} route=${route} sessionId=${sessionId}
    empty="Sin sesión abierta: abre una sesión o una ejecución para ver sus cambios." /></div>`;
}

// Mount point of the subagents panel (GUI2-T04): id="subagents-slot" and data-slot="subagents". The panel lives inside
// it; with no session open it says so.
export function SubagentsSlot({ token, sessionId }) {
  return html`<div id="subagents-slot" class="slot" data-slot="subagents"><${SubagentsMount} token=${token} openId=${sessionId} /></div>`;
}

export function TokensSlot({ token, route, sessionId }) {
  return html`<div class="slot" data-slot="tokens"><${Subject} Panel=${TokensPanel} token=${token} route=${route} sessionId=${sessionId}
    empty="Sin sesión abierta: abre una sesión o una ejecución para ver sus tokens y su costo." /></div>`;
}

export function SourcesSlot({ token, route, sessionId }) {
  return html`<div class="slot" data-slot="sources"><${Subject} Panel=${SourcesPanel} token=${token} route=${route} sessionId=${sessionId}
    empty="Sin sesión abierta: abre una sesión o una ejecución para ver sus fuentes." /></div>`;
}
