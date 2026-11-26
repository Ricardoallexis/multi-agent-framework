// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Entry module of the extension «concurrency» (PAR-T04): the card «Trabajo a la vez» (#concurrency), reached from «Apps y agentes».
// It reads GET /gui/api/settings/concurrency (the cap in force and why), GET /gui/api/settings (the levels already confirmed)
// and GET /gui/api/usage/costs (the budget line, read only). It writes only PUT /gui/api/settings/concurrency, and only
// when the person presses «Guardar»: never when it loads. Money and what happens at a limit are changed in «Uso» (#usage);
// this card shows and links them, it has no budget field. The cap itself is the server's calculation: the card never computes it.
import "./i18n.js";
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "../../js/api.js";
import { authHeaders } from "../../js/auth.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { currentMode } from "../../js/gui-mode.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { errorText } from "../../js/text.js";
import { HELP } from "./help.js";
import {
  APPS, DURATIONS, MARGINS, MODES, addModelLimit, budgetLine, buildPatch, confirmedLevels, draftFromConfig, effectNote,
  isDirty, isTemporary, limitNote, manualAboveRange, modeExpired, parseCap, setCap, warningNote, withLimit, withMode, withoutKey,
} from "./model.js";

registerHelp(HELP, "concurrency");

const CONCURRENCY_PATH = "/gui/api/settings/concurrency";
const KNOWN_WINDOWS = ["five_hour", "weekly", "model_weekly", "monthly"];

// js/api.js only has apiGet and apiPost and the route is PUT, so the one write of this card is made here, with the same
// token header and the same error shape.
async function putConcurrency(token, body) {
  const headers = { ...authHeaders(token), "Content-Type": "application/json" };
  let response;
  try {
    response = await fetch(CONCURRENCY_PATH, { method: "PUT", headers, cache: "no-store", body: JSON.stringify(body) });
  } catch (_) {
    throw new ApiError({ code: "network_error", message: t("shell.local_server_unreachable") });
  }
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* empty or non-JSON body */ }
  if (!response.ok) {
    const detail = payload && typeof payload.detail === "object" && payload.detail ? payload.detail : {};
    throw new ApiError({
      status: response.status,
      code: detail.code || "http_" + response.status,
      message: detail.message || (typeof payload?.detail === "string" ? payload.detail : "HTTP " + response.status),
      details: detail.details || {},
    });
  }
  return payload;
}

const appName = (id) => (APPS.includes(id) ? t("ext.concurrency.app." + id) : String(id || ""));
const windowName = (id) => (KNOWN_WINDOWS.includes(id) ? t("ext.concurrency.window." + id) : String(id || ""));

// A note from model.js is {key, params}; the names of apps and windows are translated here.
function noteText(note) {
  if (!note) return "";
  const params = { ...note.params };
  if ("app" in params) params.app = appName(params.app);
  if ("window" in params) params.window = windowName(params.window);
  return t(note.key, params);
}

function ModeFields({ config, draft, setDraft, effective }) {
  const warning = warningNote(draft, effective);
  return html`<div class="concurrency-fields">
    <label class="concurrency-field">
      <span>${t("ext.concurrency.mode.label")}</span>
      <select class="concurrency-input" value=${draft.mode} ...${helpFor("concurrency.mode")}
        onChange=${(event) => setDraft(withMode(config, draft, event.currentTarget.value))}>
        ${MODES.map((mode) => html`<option key=${mode} value=${mode}>${t("ext.concurrency.mode." + mode)}</option>`)}
      </select>
    </label>
    <${Help} topic="concurrency.mode" compact=${true} />
    <label class="concurrency-field">
      <span>${t("ext.concurrency.margin.label")}</span>
      <select class="concurrency-input" value=${String(draft.margin)} ...${helpFor("concurrency.margin")}
        onChange=${(event) => setDraft({ ...draft, margin: Number(event.currentTarget.value) })}>
        ${MARGINS.map((margin) => html`<option key=${margin} value=${String(margin)}>${t("ext.concurrency.margin.option", { margin })}</option>`)}
      </select>
    </label>
    <${Help} topic="concurrency.margin" compact=${true} />
    ${isTemporary(draft.mode) && html`<label class="concurrency-field">
      <span>${t("ext.concurrency.duration.label")}</span>
      <select class="concurrency-input" value=${draft.duration} ...${helpFor("concurrency.duration")}
        onChange=${(event) => setDraft({ ...draft, duration: event.currentTarget.value })}>
        ${DURATIONS.map((item) => html`<option key=${item} value=${item}>${t("ext.concurrency.duration." + item)}</option>`)}
      </select>
    </label>
    <${Help} topic="concurrency.duration" compact=${true} />`}
    ${warning && html`<p class="concurrency-warning" role="status">${noteText(warning)}</p>`}
  </div>`;
}

// One existing model cap. The box keeps what is typed, so emptying it to type another number does not make the row vanish:
// the draft takes only a whole number from 1 to 256 and the key goes away only through «Quitar».
function CapRow({ model, value, onValid, onRemove }) {
  const [text, setText] = useState(String(value));
  useEffect(() => { if (parseCap(text) !== value) setText(String(value)); }, [value]);
  return html`<li class="concurrency-row">
    <label class="concurrency-field">
      <span>${t("ext.concurrency.models.cap_label", { name: model })}</span>
      <input type="number" class="concurrency-input" min="1" max="256" inputMode="numeric" value=${text}
        ...${helpFor("concurrency.per_model")}
        onInput=${(event) => { setText(event.currentTarget.value); onValid(event.currentTarget.value); }} />
    </label>
    <button type="button" class="btn secondary" aria-label=${t("ext.concurrency.models.remove_label", { name: model })}
      ...${helpFor("concurrency.per_model")} onClick=${onRemove}>${t("ext.concurrency.models.remove")}</button>
  </li>`;
}

function AdvancedFields({ draft, setDraft, levels }) {
  const [modelId, setModelId] = useState("");
  const [modelCap, setModelCap] = useState("");
  const [addInvalid, setAddInvalid] = useState(false);
  const models = Object.keys(draft.perModel).sort();
  const capValid = parseCap(modelCap) !== null;
  const canAdd = modelId.trim() !== "" && capValid;
  return html`<div class="concurrency-advanced">
    <label class="concurrency-check">
      <input type="checkbox" checked=${draft.allowT4} ...${helpFor("concurrency.top_tier")}
        onChange=${(event) => setDraft({ ...draft, allowT4: event.currentTarget.checked })} />
      <span>${t("ext.concurrency.allow_t4")}</span>
    </label>
    <${Help} topic="concurrency.top_tier" compact=${true} />
    <p class="muted concurrency-note">${t("ext.concurrency.allow_t4.note")}</p>

    <h3 id="concurrency-apps-title" class="concurrency-subtitle">${t("ext.concurrency.apps.title")}</h3>
    <${Help} topic="concurrency.per_app" compact=${true} />
    <p class="muted concurrency-note">${t("ext.concurrency.apps.hint")}</p>
    <ul class="concurrency-list">
      ${APPS.map((app) => html`<li key=${app} class="concurrency-row">
        <label class="concurrency-field">
          <span>${t("ext.concurrency.cap.label", { name: appName(app) })}</span>
          <input type="number" class="concurrency-input" min="1" max="256" inputMode="numeric" value=${draft.perApp[app] === undefined ? "" : String(draft.perApp[app])}
            ...${helpFor("concurrency.per_app")}
            onInput=${(event) => setDraft({ ...draft, perApp: withLimit(draft.perApp, app, event.currentTarget.value) })} />
        </label>
      </li>`)}
    </ul>

    <h3 id="concurrency-models-title" class="concurrency-subtitle">${t("ext.concurrency.models.title")}</h3>
    <${Help} topic="concurrency.per_model" compact=${true} />
    ${models.length === 0 && html`<p class="muted concurrency-note">${t("ext.concurrency.models.empty")}</p>`}
    <ul class="concurrency-list">
      ${models.map((model) => html`<${CapRow} key=${model} model=${model} value=${draft.perModel[model]}
        onValid=${(text) => setDraft({ ...draft, perModel: setCap(draft.perModel, model, text) })}
        onRemove=${() => setDraft({ ...draft, perModel: withoutKey(draft.perModel, model) })} />`)}
    </ul>
    <div class="concurrency-row">
      <label class="concurrency-field">
        <span>${t("ext.concurrency.models.id_label")}</span>
        <input type="text" class="concurrency-input" maxLength="200" value=${modelId} ...${helpFor("concurrency.per_model")}
          onInput=${(event) => setModelId(event.currentTarget.value)} />
      </label>
      <label class="concurrency-field">
        <span>${t("ext.concurrency.models.add_cap_label")}</span>
        <input type="number" class="concurrency-input" min="1" max="256" inputMode="numeric" value=${modelCap} ...${helpFor("concurrency.per_model")}
          onInput=${(event) => { setModelCap(event.currentTarget.value); setAddInvalid(false); }} />
      </label>
      <button type="button" class="btn secondary" disabled=${!canAdd} ...${helpFor("concurrency.per_model")}
        onClick=${() => {
          if (!canAdd) { setAddInvalid(true); return; }
          setDraft({ ...draft, perModel: addModelLimit(draft.perModel, modelId, modelCap) });
          setModelId("");
          setModelCap("");
          setAddInvalid(false);
        }}>${t("ext.concurrency.models.add")}</button>
    </div>
    ${(addInvalid || (modelCap.trim() !== "" && !capValid)) && html`<p class="message error concurrency-note" role="alert">${t("ext.concurrency.models.add_invalid")}</p>`}

    <h3 id="concurrency-levels-title" class="concurrency-subtitle">${t("ext.concurrency.levels.title")}</h3>
    <${Help} topic="concurrency.levels" compact=${true} />
    ${levels.length === 0
      ? html`<p class="muted concurrency-note">${t("ext.concurrency.levels.empty")}</p>`
      : html`<ul class="concurrency-list">${levels.map((row) => html`<li key=${row.model} class="concurrency-level">${t("ext.concurrency.levels.row", { model: row.model, level: row.level })}</li>`)}</ul>`}
    <p class="muted concurrency-note">${t("ext.concurrency.levels.note")}</p>
  </div>`;
}

export function ConcurrencyView({ token }) {
  const [config, setConfig] = useState(null);
  const [draft, setDraft] = useState(null);
  const [levels, setLevels] = useState([]);
  const [costs, setCosts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saved, setSaved] = useState(false);
  const advanced = currentMode() === "advanced";

  // Reading never writes: the three answers only fill the card.
  async function load() {
    setLoading(true);
    setLoadError(null);
    setSaveError(null);
    setSaved(false);
    const [configRes, settingsRes, costsRes] = await Promise.allSettled([
      apiGet(CONCURRENCY_PATH, token),
      apiGet("/gui/api/settings", token),
      apiGet("/gui/api/usage/costs", token),
    ]);
    if (configRes.status === "fulfilled") {
      setConfig(configRes.value);
      setDraft(draftFromConfig(configRes.value));
    } else {
      setLoadError(configRes.reason);
    }
    setLevels(settingsRes.status === "fulfilled" ? confirmedLevels(settingsRes.value) : []);
    setCosts(costsRes.status === "fulfilled" ? costsRes.value : null);
    setLoading(false);
  }

  useEffect(() => { load(); }, [token]);

  // The only write: the fields the person changed, when the person presses «Guardar».
  async function save() {
    const patch = buildPatch(config, draft, { advanced });
    if (Object.keys(patch).length === 0) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const next = await putConcurrency(token, patch);
      setConfig(next);
      setDraft(draftFromConfig(next));
      setSaved(true);
    } catch (error) {
      setSaveError(error);
    } finally {
      setSaving(false);
    }
  }

  const dirty = config && draft ? isDirty(config, draft, { advanced }) : false;
  const note = draft ? effectNote(draft, config) : null;
  const now = limitNote(config);

  return html`<section class="concurrency-view" aria-label=${t("ext.concurrency.view")} aria-busy=${loading ? "true" : "false"}>
    <p class="muted concurrency-intro">${t("ext.concurrency.intro")}</p>
    <div class="concurrency-toolbar">
      <button type="button" class="btn secondary" disabled=${loading || saving} ...${helpFor("concurrency.refresh")} onClick=${load}>
        ${loading ? t("ext.concurrency.refreshing") : t("ext.concurrency.refresh")}
      </button>
      <${Help} topic="concurrency.refresh" compact=${true} />
    </div>
    ${loadError && html`<p class="message error" role="alert">${t("ext.concurrency.read_failed")}: ${errorText(loadError)}</p>`}
    ${loading && !config && !loadError && html`<p class="muted" role="status">${t("ext.concurrency.loading")}</p>`}
    ${config && draft && html`<div class="concurrency-card">
      <div class="concurrency-fields">
        <label class="concurrency-field">
          <span>${t("ext.concurrency.selection.label")}</span>
          <select class="concurrency-input" value=${draft.selection} ...${helpFor("concurrency.selection")}
            onChange=${(event) => setDraft({ ...draft, selection: event.currentTarget.value })}>
            <option value="auto">${t("ext.concurrency.selection.auto")}</option>
            <option value="manual">${t("ext.concurrency.selection.manual")}</option>
          </select>
        </label>
        <${Help} topic="concurrency.selection" compact=${true} />
        ${draft.selection === "manual" && html`<label class="concurrency-field">
          <span>${t("ext.concurrency.manual.label")}</span>
          <input type="number" class="concurrency-input" min="1" inputMode="numeric" value=${draft.manualText} ...${helpFor("concurrency.manual")}
            onInput=${(event) => setDraft({ ...draft, manualText: event.currentTarget.value })} />
        </label>
        <${Help} topic="concurrency.manual" compact=${true} />
        ${manualAboveRange(config) && html`<p class="muted concurrency-note" role="status">${t("ext.concurrency.manual.elsewhere", { value: config.manual })}</p>`}`}
      </div>
      ${draft.selection === "auto" && html`<${ModeFields} config=${config} draft=${draft} setDraft=${setDraft} effective=${config} />`}
      ${modeExpired(config) && html`<p class="muted concurrency-note" role="status">${t("ext.concurrency.expired")}</p>`}
      <p class="concurrency-effect" role="status">${noteText(note)}</p>
      ${now && html`<p class="concurrency-now"><strong>${t("ext.concurrency.now.title")}:</strong> ${noteText(now)}</p>`}
      ${advanced && html`<${AdvancedFields} draft=${draft} setDraft=${setDraft} levels=${levels} />`}
      <div class="concurrency-actions">
        <button type="button" class="btn" disabled=${!dirty || saving} ...${helpFor("concurrency.save")} onClick=${save}>
          ${saving ? t("ext.concurrency.saving") : t("ext.concurrency.save")}
        </button>
        <${Help} topic="concurrency.save" compact=${true} />
        ${dirty && !saving && html`<span class="muted concurrency-note" role="status">${t("ext.concurrency.unsaved")}</span>`}
        ${saved && !dirty && html`<span class="concurrency-ok" role="status">${t("ext.concurrency.saved")}</span>`}
      </div>
      ${saveError && html`<p class="message error" role="alert">${t("ext.concurrency.save_failed")}: ${errorText(saveError)}</p>`}
    </div>`}
    ${config && html`<div class="concurrency-limit">
      <p class="concurrency-rule"><strong>${t("ext.concurrency.limit.title")}:</strong> ${t("ext.concurrency.limit.rule")}</p>
      <div class="concurrency-links">
        <a class="btn secondary" href="#usage" ...${helpFor("concurrency.limit_link")}>${t("ext.concurrency.limit.link")}</a>
        <${Help} topic="concurrency.limit_link" compact=${true} />
      </div>
    </div>`}
    ${config && advanced && html`<div class="concurrency-budget">
      <p class="concurrency-budget-line"><strong>${t("ext.concurrency.budget.title")}:</strong> ${noteText(budgetLine(costs))}</p>
      <div class="concurrency-links">
        <a class="btn secondary" href="#usage" ...${helpFor("concurrency.budget_link")}>${t("ext.concurrency.budget.change")}</a>
        <${Help} topic="concurrency.budget_link" compact=${true} />
      </div>
    </div>`}
  </section>`;
}

registerView({ id: "concurrency", label: t("ext.concurrency.view"), topic: "concurrency.view", component: ConcurrencyView, order: 75, owner: "concurrency", group: "more", icon: "apps", mode: "simple" });
