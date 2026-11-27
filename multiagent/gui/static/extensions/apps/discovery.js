// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The card «Search for apps on this computer» of «Apps y agentes» (EXA-T05). Only the person's press writes the answer: «Search once»,
// «Not now» or «Withdraw consent». Opening the screen measures nothing. Every text is plain text drawn by the template.
import "./i18n.js";
import { html, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPut } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { errorText } from "../../js/text.js";
import { t } from "../../js/i18n.js";
import { DISCOVERY_PATH, answerBody, discoveryView } from "./discovery-model.js";

export function DiscoveryCard({ discovery, mode, token, onAnswered }) {
  const [working, setWorking] = useState(null);   // the answer being saved
  const [error, setError] = useState(null);

  async function send(answer) {
    if (working) return;
    setWorking(answer);
    setError(null);
    try {
      const saved = await apiPut(DISCOVERY_PATH, token, answerBody(answer));
      onAnswered(saved);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    } finally {
      setWorking(null);
    }
  }

  const view = discoveryView(discovery, mode);
  if (!view) return null;
  const byId = Object.fromEntries(view.buttons.map((button) => [button.id, button]));
  return html`<section class=${"apps-step apps-discovery apps-discovery-" + view.state} aria-label=${view.title}>
    <p class="apps-step-title">${view.title}<${Help} topic="apps.discovery" compact=${true} /></p>
    <p class="apps-step-text">${view.summary}</p>
    ${view.lines.map((line, index) => line.kind === "reads"
      ? html`<div key=${"r" + index} class="apps-discovery-reads">
          <p class="apps-step-text">${line.title}</p>
          <ul class="apps-discovery-commands">${line.commands.map((command) => html`<li key=${command}><code>${command}</code></li>`)}</ul>
        </div>`
      : html`<p key=${"l" + index} class="apps-step-text muted">${line.text}</p>`)}
    <p class="apps-notice">${view.note}</p>
    ${view.answered && html`<p class="apps-status-line muted">${view.answered}</p>`}
    <div class="apps-actions">
      ${byId.accept && html`<button type="button" class="btn" disabled=${Boolean(working)} ...${helpFor("apps.discovery_accept")}
        onClick=${() => send(byId.accept.answer)}>${working === "accepted" ? t("ext.apps.discovery.working") : byId.accept.label}</button>
        <${Help} topic="apps.discovery_accept" compact=${true} />`}
      ${byId.decline && html`<button type="button" class="btn secondary" disabled=${Boolean(working)} ...${helpFor("apps.discovery_decline")}
        onClick=${() => send(byId.decline.answer)}>${working === "declined" ? t("ext.apps.discovery.saving") : byId.decline.label}</button>
        <${Help} topic="apps.discovery_decline" compact=${true} />`}
      ${byId.withdraw && html`<button type="button" class="btn secondary" disabled=${Boolean(working)} ...${helpFor("apps.discovery_withdraw")}
        onClick=${() => send(byId.withdraw.answer)}>${working === "declined" ? t("ext.apps.discovery.saving") : byId.withdraw.label}</button>
        <${Help} topic="apps.discovery_withdraw" compact=${true} />`}
    </div>
    ${view.answer === "accepted" && view.presets.length > 0 && html`<div class="apps-discovery-presets">
      ${view.presetsTitle && html`<p class="apps-step-text">${view.presetsTitle}</p>`}
      <ul class="apps-discovery-commands">
        ${view.presets.map((preset) => html`<li key=${preset.app}>${preset.command ? html`${preset.name}: <code>${preset.command}</code>` : preset.text}${preset.source
          ? html`<span class="muted"> ${t("ext.apps.discovery.preset_source", { source: preset.source, date: preset.sourceDate || "" })}</span>` : ""}</li>`)}
      </ul>
      <p class="apps-status-line muted">${view.presetsNote}</p>
    </div>`}
    ${error && html`<p class="message error apps-status-line" role="alert">${errorText(error)}</p>`}
  </section>`;
}
