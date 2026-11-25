// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Personalizar» (GUI3-T07, route #customize): what the person chooses to see. Three blocks: which sections stay in the
// sidebar, when each card of «Contexto» is drawn, and whether the «?» help always shows (the same state as F1). Every choice
// is kept through shell-state.js; when the browser does not remember, the screen says so and the choices last while the page
// stays open. The blocks change what is shown, never what can be done: nothing is removed from the application.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { ACCENTS, LANGUAGE_CHOICES, cardRows, pinnableSections, togglePinned, withCardMode } from "./customize-model.js";
import { CARD_MODES } from "./context-model.js";
import { Help, helpFor } from "./help.js";
import { registeredSlots } from "./extensions.js";
import { saveMode } from "./gui-mode.js";   // SIM-T02
import { onChoiceChange, readAccent, readCardModes, readHelpMode, readPinnedSections, resetChoices, storageRemembers, writeAccent, writeCardModes, writeHelpMode, writePinnedSections } from "./shell-state.js";
import { languageChoice, setLanguage, t } from "./i18n.js";

export function Customize({ token, sections, mode = "advanced" }) {
  const [remembers] = useState(storageRemembers);
  const [pinned, setPinned] = useState(readPinnedSections);
  const [modes, setModes] = useState(readCardModes);
  const [helpOn, setHelpOn] = useState(readHelpMode);
  const [accent, setAccent] = useState(readAccent);
  const [language, setSelectedLanguage] = useState(languageChoice);
  const [modeError, setModeError] = useState(false);   // SIM-T02: the server did not take the new mode
  useEffect(() => onChoiceChange((change) => {   // the change carries its value, so it shows even when nothing is stored
    if (change.pinned) setPinned(change.pinned);
    if (change.modes) setModes(change.modes);
    if ("help" in change) setHelpOn(change.help);
    if (change.accent) setAccent(change.accent);
  }), []);
  const rows = pinnableSections(sections, pinned, mode);
  const toggleMode = (event) => {   // the server keeps the mode (POST /gui/api/settings/mode); the page redraws without reloading
    setModeError(false);
    saveMode(token, event.target.checked ? "advanced" : "simple").catch(() => setModeError(true));
  };
  const cards = cardRows(registeredSlots(), modes);
  return html`<div class="customize">
    ${!remembers && html`<p class="message small" role="status">${t("customize.storage_warning")}</p>`}
    <section class="customize-block" aria-label=${t("customize.mode")}>
      <h3 class="customize-title">${t("customize.mode")}</h3>
      <label class="check-inline">
        <input type="checkbox" checked=${mode === "advanced"} ...${helpFor("customize.advanced_mode")} onChange=${toggleMode} />
        ${t("customize.advanced")}</label>
      <${Help} topic="customize.advanced_mode" compact=${true} />
      ${modeError && html`<p class="message error small" role="alert">${t("customize.mode_error")}</p>`}
    </section>
    <section class="customize-block" aria-label=${t("customize.sidebar")}>
      <h3 class="customize-title">${t("customize.sidebar")}</h3>
      <p class="muted small">${t("customize.sidebar_info")}</p>
      <ul class="customize-list">
        ${rows.map((row) => html`<li key=${row.id} class="customize-row">
          <span class="customize-name">${row.label}</span>
          <button type="button" class="btn secondary" aria-pressed=${row.pinned ? "true" : "false"} aria-label=${t("customize.pin_label", { name: row.label })}
            ...${helpFor("customize.pin", { scope: row.id })} onClick=${() => writePinnedSections(togglePinned(pinned, row.id))}>${t("customize.pin")}</button>
          <${Help} topic="customize.pin" scope=${row.id} compact=${true} />
        </li>`)}
      </ul>
    </section>
    <section class="customize-block" aria-label=${t("customize.context")}>
      <h3 class="customize-title">${t("customize.context")}</h3>
      <p class="muted small">${t("customize.context_info")}</p>
      ${cards.map((card) => html`<fieldset key=${card.name} class="customize-card">
        <legend>${t("customize.card_" + card.name) === "customize.card_" + card.name ? card.title : t("customize.card_" + card.name)}</legend>
        <${Help} topic="customize.card" scope=${card.name} compact=${true} />
        <div class="customize-modes">
          ${CARD_MODES.map((mode) => html`<label key=${mode} class="check-inline">
            <input type="radio" name=${"card-" + card.name} value=${mode} checked=${card.mode === mode}
              ...${helpFor("customize.card", { scope: card.name })} onChange=${() => writeCardModes(withCardMode(modes, card.name, mode))} />
            ${t("customize." + mode)}</label>`)}
        </div>
      </fieldset>`)}
    </section>
    <section class="customize-block" aria-label=${t("customize.colors")}>
      <h3 class="customize-title">${t("customize.colors")}</h3>
      <p class="muted small">${t("customize.colors_info")}</p>
      <fieldset class="customize-card">
        <legend>${t("customize.palette")}</legend>
        <${Help} topic="customize.accent" compact=${true} />
        <div class="customize-modes">
          ${ACCENTS.map((option) => html`<label key=${option.id} class="check-inline accent-option">
            <input type="radio" name="accent" value=${option.id} checked=${accent === option.id}
              ...${helpFor("customize.accent")} onChange=${() => writeAccent(option.id)} />
            <span class="accent-swatch" data-accent=${option.id} aria-hidden="true"></span>
            ${t("customize.accent_" + option.id)}</label>`)}
        </div>
      </fieldset>
    </section>
    <section class="customize-block" aria-label=${t("customize.language")}>
      <h3 class="customize-title">${t("customize.language")}</h3>
      <fieldset class="customize-card">
        <legend>${t("customize.language")}</legend>
        <${Help} topic="customize.language" compact=${true} />
        <div class="customize-modes">
          ${LANGUAGE_CHOICES.map((id) => html`<label key=${id} class="check-inline">
            <input type="radio" name="language" value=${id} checked=${language === id}
              ...${helpFor("customize.language")} onChange=${() => { setLanguage(id); setSelectedLanguage(id); }} />
            ${t("customize.language_" + id)}</label>`)}
        </div>
      </fieldset>
    </section>
    <section class="customize-block" aria-label=${t("customize.help")}>
      <h3 class="customize-title">${t("customize.help")}</h3>
      <label class="check-inline">
        <input type="checkbox" checked=${helpOn} ...${helpFor("customize.help")} onChange=${(event) => writeHelpMode(event.target.checked)} />
        ${t("customize.show_help")}</label>
      <${Help} topic="customize.help" compact=${true} />
    </section>
    <div class="customize-actions">
      <button type="button" class="btn secondary" ...${helpFor("customize.reset")} onClick=${resetChoices}>${t("customize.reset")}</button>
      <${Help} topic="customize.reset" compact=${true} />
    </div>
  </div>`;
}
