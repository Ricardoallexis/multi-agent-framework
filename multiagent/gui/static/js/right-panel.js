// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Context panel (GUI2-T02, GUI3-T04): context of the session or run open in the centre, in four sections. The shell (shell.js) puts it in
// <aside id="right-panel"> and owns the collapse button; this file draws the sections and their help. Each section
// holds a slot component from panel-slots.js, which shows an empty state until the task that owns it fills it
// (Subagents already holds the subagents panel of GUI2-T04; its "?" uses that task's topic "subagents.panel").
// Sections registered with registerSlot (panel-slots.js, FLU-T04) follow the four fixed ones.
// GUI3-T04: a card is drawn by its mode (Automatic, Always, Never, context-model.js) and by what it reported; a card
// that is not drawn stays in the DOM, hidden, so it keeps loading its data and the dot beside Context knows about it.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { anyContent, groupShown, ownContents, sameContent, shownCards } from "./context-model.js";
import { contextWhere } from "./context-where.js";
import { Help } from "./help.js";
import { usePanelData } from "./panel-data.js";
import { t } from "./i18n.js";
import { subjectOf } from "./panel-model.js";
import { ChangesSlot, SourcesSlot, SubagentsSlot, TokensSlot, registeredSlots } from "./panel-slots.js";
import { currentMode } from "./gui-mode.js";
import { onChoiceChange, readCardModes } from "./shell-state.js";

const OWN_CARDS = [{ name: "changes" }, { name: "subagents" }, { name: "tokens" }, { name: "sources" }];

// A registered slot: the same section frame as the fixed ones. `topic` (optional) is a key of the extension's help.
function ExtraSection({ entry, slot, shown, onContent }) {
  const titleId = "panel-" + entry.name + "-title";
  return html`<section class="rp-section" aria-labelledby=${titleId} hidden=${!shown}>
    <div class="rp-head"><h3 id=${titleId}>${entry.title}</h3>${entry.topic && html`<${Help} topic=${entry.topic} compact=${true} />`}</div>
    <div class="slot" data-slot=${entry.name}><${entry.component} ...${slot} onContent=${onContent} /></div>
  </section>`;
}

function ContextGroup({ title, shown, children }) {
  return html`<section class="rp-section rp-group" aria-label=${title} hidden=${!shown}>
    <div class="rp-head"><h3>${title}</h3></div>
    ${children}
  </section>`;
}

// onDot(true|false): whether a card that is drawn has content, for the dot beside «Contexto» (shell.js).
export function RightPanel({ token, route, onDot }) {
  const sessionId = route.section === "sessions" ? route.id : null;
  const slot = { token, route, sessionId };
  const subject = subjectOf(route, sessionId);
  const data = usePanelData(token, subject);
  const [reported, setReported] = useState({});
  const reporters = useRef({});
  const reporter = (name) => {
    if (!reporters.current[name]) {
      reporters.current[name] = (content) => setReported((previous) => (name in previous && sameContent(previous[name], content)
        ? previous : { ...previous, [name]: content }));
    }
    return reporters.current[name];
  };
  const extra = registeredSlots();
  const cards = OWN_CARDS.concat(extra.map((entry) => ({ name: entry.name, defaultMode: entry.defaultMode })));
  const contents = { ...reported, ...ownContents(subject, data), subagents: subject ? reported.subagents || { has: false } : { has: false } };
  const [modes, setModes] = useState(() => readCardModes());   // «Personalizar» changes them (GUI3-T07)
  useEffect(() => onChoiceChange((change) => { if (change.modes) setModes(change.modes); }), []);
  const shown = shownCards(cards, modes, contents);
  const where = contextWhere(subject, data);
  const dot = Boolean(subject) && anyContent(cards, modes, contents);
  useEffect(() => { if (typeof onDot === "function") onDot(dot); }, [dot]);
  return html`<div class="rp" data-mode=${currentMode()}>
    <h2 class="sr-only">${t("panel.context")}</h2>
    <${ContextGroup} title=${t("panel.where_heading")} shown=${where.length > 0}>
      <dl class="rp-where">
        ${where.map((row) => html`<div class="rp-where-row" key=${row.label}><dt>${row.label}</dt><dd>${row.value}</dd></div>`)}
      </dl>
    <//>
    <${ContextGroup} title=${t("panel.conversation_heading")} shown=${groupShown(shown, ["tokens"])}>
      <div class="rp-card" aria-labelledby="panel-tokens-title" hidden=${!shown.tokens}>
        <div class="rp-card-head"><h4 id="panel-tokens-title">${t("panel.tokens_heading")}</h4><${Help} topic="panel.tokens" compact=${true} /></div>
        <${TokensSlot} ...${slot} />
      </div>
    <//>
    <${ContextGroup} title=${t("panel.outputs_heading")} shown=${groupShown(shown, ["changes", "sources"])}>
      <div class="rp-card" aria-labelledby="panel-changes-title" hidden=${!shown.changes}>
        <div class="rp-card-head"><h4 id="panel-changes-title">${t("panel.changes_heading")}</h4><${Help} topic="panel.changes" compact=${true} /></div>
        <${ChangesSlot} ...${slot} />
      </div>
      <div class="rp-card" aria-labelledby="panel-sources-title" hidden=${!shown.sources}>
        <div class="rp-card-head"><h4 id="panel-sources-title">${t("panel.sources_heading")}</h4><${Help} topic="panel.sources" compact=${true} /></div>
        <${SourcesSlot} ...${slot} />
      </div>
    <//>
    <${ContextGroup} title=${t("panel.in_progress_heading")} shown=${groupShown(shown, ["subagents"])}>
      <div class="rp-card" aria-labelledby="panel-subagents-title" hidden=${!shown.subagents}>
        <div class="rp-card-head"><h4 id="panel-subagents-title">${t("panel.subagents_heading")}</h4><${Help} topic="subagents.panel" compact=${true} /></div>
        <${SubagentsSlot} ...${slot} onContent=${reporter("subagents")} />
      </div>
      <div class="rp-progress-slot" aria-hidden="true"></div>
    <//>
    ${extra.map((entry) => html`<${ExtraSection} key=${entry.name} entry=${entry} slot=${slot} shown=${shown[entry.name]} onContent=${reporter(entry.name)} />`)}
  </div>`;
}
