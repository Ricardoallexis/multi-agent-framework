// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The section «Continuidad» of Uso (CNT-T06): the mode, the wait threshold, the hand-off limit and the candidate executors, as the
// framework applies them today (GET /gui/api/continuity/policy), and, since USO-T07, the form that changes the mode, the wait
// threshold and the allowed destinations (GET and PUT /gui/api/settings/continuity). The three fixed rules are a note, not a control.
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPut } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import {
  CONTINUITY_APPS, CONTINUITY_HOURS, CONTINUITY_MODES, CONTINUITY_POLICIES, continuityChanged, continuityDraft, continuityPayload,
} from "../../js/usage-model.js";
import { automaticChanged, automaticOn, automaticPayload, automaticStateText, policyCandidates, policyRows, savedMode } from "./model.js";
import { POLICY_POLL_MS, usePolled } from "./poll.js";

export function PolicyView({ policy, now = new Date() }) {
  return html`<div class="continuity-policy-body">
    <dl class="continuity-rows">
      ${policyRows(policy).map(([name, value]) => html`<div class="continuity-row" key=${name}><dt>${name}</dt><dd>${value}</dd></div>`)}
    </dl>
    <h4 class="continuity-subtitle">${t("ext.continuity.policy.candidates_title")}</h4>
    ${policy.candidates && policy.candidates.length
      ? html`<ul class="continuity-candidates">${policyCandidates(policy, now).map((item) => html`<li key=${item.id}>
          <strong>${item.app}</strong><span class="continuity-chip">${item.state}</span><span class="continuity-chip">${item.policy}</span></li>`)}</ul>`
      : html`<p class="muted">${t("ext.continuity.policy.no_candidates")}</p>`}
    <p class="muted small">${policy.source === "default" ? t("ext.continuity.policy.defaults_note") : ""}</p>
  </div>`;
}

const APP_NAMES = { claude_cli: "Claude", codex_cli: "Codex", copilot_cli: "Copilot" };
const SOURCE_KEYS = { gui: "ext.continuity.settings.source.gui", environment: "ext.continuity.settings.source.environment", default: "ext.continuity.settings.source.default" };

// What the person can change: the mode, the wait threshold and which apps may take over. Nothing is sent while the form matches the server.
export function ContinuitySettings({ token, onSaved = () => {} }) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const [draft, setDraft] = useState(null);
  const [automatic, setAutomatic] = useState(false);     // USO-T08: the switch; off until the person turns it on
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  async function load() {
    try {
      const data = await apiGet("/gui/api/settings/continuity", token);
      setState({ loading: false, data, error: "" });
      setDraft(continuityDraft({ ...data, mode: savedMode(data) }));
      setAutomatic(automaticOn(data));
    } catch (failure) {
      setState((previous) => ({ ...previous, loading: false, error: failure instanceof ApiError ? failure.message : String(failure) }));
    }
  }
  useEffect(() => { load(); }, [token]);
  const original = state.data ? continuityDraft({ ...state.data, mode: savedMode(state.data) }) : null;
  const changed = Boolean(draft && original && (continuityChanged(draft, original) || automaticChanged(automatic, state.data)));
  const change = (part) => { setSaved(false); setDraft({ ...draft, ...part }); };
  async function save(event) {
    event.preventDefault();
    if (!changed || saving) return;
    setSaving(true);
    try {
      const data = await apiPut("/gui/api/settings/continuity", token, automaticPayload(continuityPayload(draft), automatic, state.data));
      setState({ loading: false, data, error: "" });
      setDraft(continuityDraft({ ...data, mode: savedMode(data) }));
      setAutomatic(automaticOn(data));
      setSaved(true);
      onSaved();
    } catch (failure) {
      setSaved(false);
      setState((previous) => ({ ...previous, error: failure instanceof ApiError ? failure.message : String(failure) }));
    }
    setSaving(false);
  }
  const data = state.data;
  return html`<div class="continuity-settings">
    <h4 class="continuity-subtitle">${t("ext.continuity.settings.title")}</h4>
    ${state.error && html`<p class="message error" role="alert">${t("ext.continuity.settings.error", { message: state.error })}</p>`}
    ${state.loading ? html`<p role="status">${t("ext.continuity.loading")}</p>` : data && draft && html`<form class="continuity-settings-form" onSubmit=${save}>
      <p class="muted small continuity-settings-source">${t("ext.continuity.settings.source", { source: t(SOURCE_KEYS[data.source] || SOURCE_KEYS.default) })}</p>
      <div class="continuity-settings-field">
        <label for="continuity-setting-mode">${t("ext.continuity.settings.mode_label")}</label>
        <select id="continuity-setting-mode" value=${draft.mode} ...${helpFor("continuity.usage")} onChange=${(event) => change({ mode: event.currentTarget.value })}>
          ${CONTINUITY_MODES.map((mode) => html`<option key=${mode} value=${mode} selected=${draft.mode === mode}>${t("ext.continuity.mode." + mode)}</option>`)}
        </select>
        <p class="muted small">${t("ext.continuity.mode." + draft.mode + ".explanation")}</p>
      </div>
      <div class="continuity-settings-field">
        <label for="continuity-setting-hours">${t("ext.continuity.settings.hours_label")}</label>
        <select id="continuity-setting-hours" value=${String(draft.wait_threshold_hours)} ...${helpFor("continuity.usage")}
          onChange=${(event) => change({ wait_threshold_hours: Number(event.currentTarget.value) })}>
          ${CONTINUITY_HOURS.map((hours) => html`<option key=${hours} value=${String(hours)} selected=${draft.wait_threshold_hours === hours}>${t(hours === 1 ? "ext.continuity.settings.hours_one" : "ext.continuity.settings.hours_many", { hours })}</option>`)}
        </select>
      </div>
      <fieldset class="continuity-settings-field">
        <legend>${t("ext.continuity.settings.destinations")}</legend>
        ${CONTINUITY_APPS.map((app) => html`<div class="continuity-settings-destination" key=${app}>
          <label for=${"continuity-setting-" + app}>${APP_NAMES[app]}</label>
          <select id=${"continuity-setting-" + app} value=${draft.candidates[app]} ...${helpFor("continuity.usage")}
            onChange=${(event) => change({ candidates: { ...draft.candidates, [app]: event.currentTarget.value } })}>
            ${CONTINUITY_POLICIES.map((policy) => html`<option key=${policy} value=${policy} selected=${draft.candidates[app] === policy}>${t("ext.continuity.policy." + policy)}</option>`)}
          </select></div>`)}
      </fieldset>
      <div class="continuity-settings-field continuity-automatic">
        <label for="continuity-setting-automatic" class="continuity-automatic-label">
          <input id="continuity-setting-automatic" type="checkbox" role="switch" checked=${automatic} ...${helpFor("continuity.usage")}
            onChange=${(event) => { setSaved(false); setAutomatic(event.currentTarget.checked); }} />
          <span>${t("ext.continuity.settings.automatic_label")}</span>
        </label>
        <p class="muted small continuity-automatic-state" role="status">${automaticStateText(data)}</p>
        <p class="muted small">${t("ext.continuity.settings.automatic_explanation")}</p>
        <p class="continuity-automatic-note"><strong>${t("ext.continuity.settings.automatic_not_title")}</strong> ${t("ext.continuity.settings.automatic_not_released")}</p>
      </div>
      <div class="continuity-settings-actions"><button type="submit" class="btn continuity-btn" disabled=${!changed || saving} ...${helpFor("continuity.usage")}>${saving ? t("ext.continuity.settings.saving") : t("ext.continuity.settings.save")}</button>
        ${!changed && html`<span class="muted small">${t("ext.continuity.settings.no_changes")}</span>`}
        ${saved && html`<span class="muted small" role="status">${t("ext.continuity.settings.saved")}</span>`}</div>
      <div class="continuity-fixed-rules confirm">
        <p><strong>${t("ext.continuity.settings.fixed_title")}</strong></p>
        <ul>
          <li>${t("ext.continuity.settings.fixed_handoffs", { count: Number.isFinite(data.max_handoffs) ? data.max_handoffs : 1 })}</li>
          <li>${t("ext.continuity.settings.fixed_running")}</li>
          <li>${t("ext.continuity.settings.fixed_paid")}</li>
        </ul>
        <p>${t("ext.continuity.settings.not_released")}</p>
      </div>
    </form>`}
  </div>`;
}

export function ContinuityPolicy({ token }) {
  const { loading, data, error, reload, refreshing } = usePolled("/gui/api/continuity/policy", token, POLICY_POLL_MS);
  return html`<section class="continuity-policy" aria-labelledby="continuity-usage-title">
    <h3 id="continuity-usage-title">${t("ext.continuity.policy.title")}</h3><${Help} topic="continuity.usage" />
    <span class="continuity-option"><button type="button" class="btn secondary continuity-btn" disabled=${refreshing}
      ...${helpFor("continuity.refresh")} onClick=${reload}>${refreshing ? t("ext.continuity.refreshing") : t("ext.continuity.refresh")}</button>
      <${Help} topic="continuity.refresh" compact=${true} /></span>
    ${error && html`<p class="message error" role="alert">${t("ext.continuity.policy.read_error", {
      latest: data ? t("ext.continuity.latest_response") : "", message: error.message,
    })}</p>`}
    ${loading ? html`<p role="status">${t("ext.continuity.loading")}</p>` : data && html`<${PolicyView} policy=${data} />`}
    <${ContinuitySettings} token=${token} onSaved=${reload} />
  </section>`;
}
