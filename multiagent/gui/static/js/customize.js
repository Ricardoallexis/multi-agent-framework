// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Personalizar» (GUI3-T07, route #customize): what the person chooses to see. Three blocks: which sections stay in the
// sidebar, when each card of «Contexto» is drawn, and whether the «?» help always shows (the same state as F1). Every choice
// is kept through shell-state.js; when the browser does not remember, the screen says so and the choices last while the page
// stays open. The blocks change what is shown, never what can be done: nothing is removed from the application.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { ACCENTS, MODE_LABELS, cardRows, pinnableSections, togglePinned, withCardMode } from "./customize-model.js";
import { CARD_MODES } from "./context-model.js";
import { Help, helpFor } from "./help.js";
import { registeredSlots } from "./extensions.js";
import { onChoiceChange, readAccent, readCardModes, readHelpMode, readPinnedSections, resetChoices, storageRemembers, writeAccent, writeCardModes, writeHelpMode, writePinnedSections } from "./shell-state.js";

export function Customize({ sections }) {
  const [remembers] = useState(storageRemembers);
  const [pinned, setPinned] = useState(readPinnedSections);
  const [modes, setModes] = useState(readCardModes);
  const [helpOn, setHelpOn] = useState(readHelpMode);
  const [accent, setAccent] = useState(readAccent);
  useEffect(() => onChoiceChange((change) => {   // the change carries its value, so it shows even when nothing is stored
    if (change.pinned) setPinned(change.pinned);
    if (change.modes) setModes(change.modes);
    if ("help" in change) setHelpOn(change.help);
    if (change.accent) setAccent(change.accent);
  }), []);
  const rows = pinnableSections(sections, pinned);
  const cards = cardRows(registeredSlots(), modes);
  return html`<div class="customize">
    ${!remembers && html`<p class="message small" role="status">Este navegador no guarda las preferencias: se aplican mientras esta página siga abierta.</p>`}
    <section class="customize-block" aria-label="Barra lateral">
      <h3 class="customize-title">Barra lateral</h3>
      <p class="muted small">Las secciones fijadas salen en la barra, junto a las fijas. Las demás siguen en «Más» y en Ctrl K.</p>
      <ul class="customize-list">
        ${rows.map((row) => html`<li key=${row.id} class="customize-row">
          <span class="customize-name">${row.label}</span>
          <button type="button" class="btn secondary" aria-pressed=${row.pinned ? "true" : "false"} aria-label=${"Fijar " + row.label}
            ...${helpFor("customize.pin", { scope: row.id })} onClick=${() => writePinnedSections(togglePinned(pinned, row.id))}>Fijar</button>
          <${Help} topic="customize.pin" scope=${row.id} compact=${true} />
        </li>`)}
      </ul>
    </section>
    <section class="customize-block" aria-label="Contexto">
      <h3 class="customize-title">Contexto</h3>
      <p class="muted small">Cuándo se dibuja cada tarjeta del panel: «Automática» solo si tiene algo que mostrar, «Siempre» aunque esté vacía, «Nunca» oculta. Lo urgente se ve siempre.</p>
      ${cards.map((card) => html`<fieldset key=${card.name} class="customize-card">
        <legend>${card.title}</legend>
        <${Help} topic="customize.card" scope=${card.name} compact=${true} />
        <div class="customize-modes">
          ${CARD_MODES.map((mode) => html`<label key=${mode} class="check-inline">
            <input type="radio" name=${"card-" + card.name} value=${mode} checked=${card.mode === mode}
              ...${helpFor("customize.card", { scope: card.name })} onChange=${() => writeCardModes(withCardMode(modes, card.name, mode))} />
            ${MODE_LABELS[mode]}</label>`)}
        </div>
      </fieldset>`)}
    </section>
    <section class="customize-block" aria-label="Colores">
      <h3 class="customize-title">Colores</h3>
      <p class="muted small">El color de los botones principales, los enlaces y la selección. Los fondos, el texto y los avisos no cambian.</p>
      <fieldset class="customize-card">
        <legend>Paleta</legend>
        <${Help} topic="customize.accent" compact=${true} />
        <div class="customize-modes">
          ${ACCENTS.map((option) => html`<label key=${option.id} class="check-inline accent-option">
            <input type="radio" name="accent" value=${option.id} checked=${accent === option.id}
              ...${helpFor("customize.accent")} onChange=${() => writeAccent(option.id)} />
            <span class="accent-swatch" data-accent=${option.id} aria-hidden="true"></span>
            ${option.label}</label>`)}
        </div>
      </fieldset>
    </section>
    <section class="customize-block" aria-label="Ayuda">
      <h3 class="customize-title">Ayuda</h3>
      <label class="check-inline">
        <input type="checkbox" checked=${helpOn} ...${helpFor("customize.help")} onChange=${(event) => writeHelpMode(event.target.checked)} />
        Mostrar siempre los «?»</label>
      <${Help} topic="customize.help" compact=${true} />
    </section>
    <div class="customize-actions">
      <button type="button" class="btn secondary" ...${helpFor("customize.reset")} onClick=${resetChoices}>Restablecer</button>
      <${Help} topic="customize.reset" compact=${true} />
    </div>
  </div>`;
}
