// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Right panel (GUI2-T02): context of the session open in the centre, in four sections. The shell (shell.js) puts it in
// <aside id="right-panel"> and owns the collapse button; this file draws the sections and their help. Each section
// holds a slot component from panel-slots.js, which shows an empty state until the task that owns it fills it
// («Subagentes» already holds the subagents panel of GUI2-T04; its "?" uses that task's topic "subagents.panel").
import { html } from "../vendor/htm-preact-standalone.module.js";
import { Help } from "./help.js";
import { ChangesSlot, SourcesSlot, SubagentsSlot, TokensSlot } from "./panel-slots.js";

export function RightPanel({ token, route }) {
  const sessionId = route.section === "sessions" ? route.id : null;
  const slot = { token, route, sessionId };
  return html`<div class="rp">
    <h2 class="sr-only">Contexto de la sesión</h2>
    <section class="rp-section" aria-labelledby="panel-changes-title">
      <div class="rp-head"><h3 id="panel-changes-title">Cambios</h3><${Help} topic="panel.changes" compact=${true} /></div>
      <${ChangesSlot} ...${slot} />
    </section>
    <section class="rp-section" aria-labelledby="panel-subagents-title">
      <div class="rp-head"><h3 id="panel-subagents-title">Subagentes</h3><${Help} topic="subagents.panel" compact=${true} /></div>
      <${SubagentsSlot} ...${slot} />
    </section>
    <section class="rp-section" aria-labelledby="panel-tokens-title">
      <div class="rp-head"><h3 id="panel-tokens-title">Tokens</h3><${Help} topic="panel.tokens" compact=${true} /></div>
      <${TokensSlot} ...${slot} />
    </section>
    <section class="rp-section" aria-labelledby="panel-sources-title">
      <div class="rp-head"><h3 id="panel-sources-title">Fuentes</h3><${Help} topic="panel.sources" compact=${true} /></div>
      <${SourcesSlot} ...${slot} />
    </section>
  </div>`;
}
