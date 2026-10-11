// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The bodies of the four sections of the right panel (GUI2-T02). Each one is a small component that for now shows an
// empty state; the tasks that fill them replace the body of ONE component here and nothing else:
//   ChangesSlot    GUI2-T05  changes per file
//   SubagentsSlot  GUI2-T04  subagents of the open session (tabs Activos / Terminados)
//   TokensSlot     GUI2-T05  tokens of the orchestrator and the workers
//   SourcesSlot    GUI2-T05  sources of the session
// Props every slot receives: token (the local GUI token, for apiGet), sessionId (id of the session open in the centre,
// "claude:...", "codex:..." and so on, or null when the centre shows the list, a run or the create form) and route.
// right-panel.js draws the title and the "?" help of each section around its slot; a slot draws only its content.
// SubagentsSlot is filled (GUI2-T04): it holds the subagents panel through subagents-mount.js.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { SubagentsMount } from "./subagents-mount.js";

function Empty({ children }) {
  return html`<p class="muted small slot-empty">${children}</p>`;
}

export function ChangesSlot() {
  return html`<div class="slot" data-slot="changes"><${Empty}>Sin cambios que mostrar por ahora.<//></div>`;
}

// Mount point of the subagents panel (GUI2-T04): id="subagents-slot" and data-slot="subagents". The panel lives inside
// it; with no session open it says so.
export function SubagentsSlot({ token, sessionId }) {
  return html`<div id="subagents-slot" class="slot" data-slot="subagents"><${SubagentsMount} token=${token} openId=${sessionId} /></div>`;
}

export function TokensSlot() {
  return html`<div class="slot" data-slot="tokens"><${Empty}>Sin datos de tokens por ahora.<//></div>`;
}

export function SourcesSlot() {
  return html`<div class="slot" data-slot="sources"><${Empty}>Sin fuentes que mostrar por ahora.<//></div>`;
}
