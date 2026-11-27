// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Prepare the export with the assistant» (OFA-T05): a box where the person says what to do with an office. A model drafts a
// summary once; the card shows what will be included, what is left out and why, what could not be checked and the questions,
// with two buttons: «Download the package» (the only thing that produces the file) and «Change something» (drafts again with
// the correction; no limit). The fixed note about pattern checks is always visible. Nothing downloads by itself, there is no
// polling and every text from the server is drawn as a text node, never as markup. The rules are in export-assistant-model.js
// (pure, tested with Node); this file only holds the state and draws it.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost } from "../../js/api.js";
import { currentMode, onModeChange } from "../../js/gui-mode.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { exportOffice } from "./import.js";
import { exportDownloadedText, exportFileName, exportWarningText } from "./import-model.js";
import {
  TEXT_MAX, answerKind, canDraft, canRedraft, cardView, choiceBody, composeText, correctionTooLong, createHeldStore, draftBody,
  exportPath, failureView, fixedNote, includeCodeAllowed, pauseView,
} from "./export-assistant-model.js";

// SEC-T04 pattern (new-session.js): the request that waits for a secret decision survives a visit to #approvals, which unmounts
// this view. It lives in module memory only: a reload starts clean. A held row also means the assistant was open.
const held = createHeldStore();
export const assistantHeld = (officeId) => held.get(officeId) !== null;

const asApiError = (error) => (error instanceof ApiError ? error : new ApiError({ message: String(error && error.message || error) }));

function PathList({ title, paths, why = "" }) {
  return paths.length > 0 && html`<div class="offices-xa-block">
    <strong>${title}</strong>
    ${why && html`<p class="muted small">${why}</p>`}
    <ul class="offices-xa-list">${paths.map((path) => html`<li key=${path}><code>${path}</code></li>`)}</ul>
  </div>`;
}

export function ExportAssistant({ token, officeId, onClose }) {
  const [mode, setMode] = useState(currentMode);
  const kept = useRef(held.get(officeId)).current;      // what a visit to #approvals left, or null
  const [request, setRequest] = useState(kept ? kept.request : "");
  const [correction, setCorrection] = useState("");
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState(null);       // the last answer with a draft; the card shows it
  const [preview, setPreview] = useState(null);     // what the server says the chosen package holds, once it checked it
  const [drafted, setDrafted] = useState({ includeCode: false });
  const [notice, setNotice] = useState(() => (kept && kept.approvalId
    ? { ...pauseView({ reason: "secret_review_required", approval_id: kept.approvalId }), value: kept.request } : null));
  const [error, setError] = useState(null);         // {text, lines} of a failed call, or null
  const [done, setDone] = useState("");
  const [includeCode, setIncludeCode] = useState(false);
  const [codeRisk, setCodeRisk] = useState(false);
  const approval = useRef(kept ? kept.approvalId : "");
  const confirmedModel = useRef(kept ? kept.confirmedModel : "");                // the remote model the person said yes to in this box
  const baseText = useRef("");                      // the request, with the corrections so far
  const alive = useRef(true);
  const heading = useRef(null);
  const advanced = includeCodeAllowed(mode);
  useEffect(() => onModeChange(setMode), []);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => { if (answer && heading.current) heading.current.focus(); }, [answer]);

  const withCode = advanced && includeCode;

  async function post(step, body) {
    return apiPost(exportPath(officeId, step), token, body);
  }

  // A draft is asked once; a remote model asks first, and a pause shows its own notice. Nothing here downloads.
  async function ask(value, { confirmCloud = false } = {}) {
    if (busy) return;
    setBusy(true); setError(null); setNotice(null); setDone("");
    try {
      const options = { includeCode: withCode, approvalId: approval.current };
      let reply = await post("draft", draftBody(value, { ...options, confirmCloud }));
      if (!alive.current) return;
      // The person already said yes to this very model in this box: the same yes is sent, any other model asks again.
      if (answerKind(reply) === "cloud_confirmation_required" && reply.model && reply.model === confirmedModel.current) {
        reply = await post("draft", draftBody(value, { ...options, confirmCloud: true }));
        if (!alive.current) return;
      }
      const kind = answerKind(reply);
      if (kind === "card") {
        approval.current = "";
        held.clear(officeId);
        baseText.current = value;
        setAnswer(reply); setPreview(null); setChanging(false); setCorrection("");
        setDrafted({ includeCode: withCode });
        try {
          const checked = await post("preview", choiceBody(reply, { includeCode: withCode, acceptCodeRisk: codeRisk }));
          if (alive.current) setPreview(checked);
        } catch (failure) {
          if (!alive.current) return;
          const view = failureView(asApiError(failure));
          if (view.stale) setAnswer(null);
          setError(view);
        }
      } else {
        const view = pauseView(reply);
        if (kind === "cloud_confirmation_required") confirmedModel.current = "";
        if (kind === "secret_review_required" && view.approvalId) {
          approval.current = view.approvalId;
          held.set(officeId, { request: value, approvalId: view.approvalId, confirmedModel: confirmedModel.current });
        }
        setNotice({ ...view, value });
      }
    } catch (failure) {
      const failed = asApiError(failure);
      // The server no longer accepts that approval: forget it so the next attempt asks for a new one.
      if (approval.current && failed.status >= 400 && failed.status < 500) { approval.current = ""; held.clear(officeId); }
      if (!alive.current) return;
      const view = failureView(failed);
      if (view.stale) { setAnswer(null); setPreview(null); }
      setError(view);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  // The only place that asks for the file: it runs from the «Download the package» button and from nowhere else.
  async function download() {
    if (busy || !answer || !view || !view.canDownload) return;
    setBusy(true); setError(null); setDone("");
    try {
      const body = choiceBody(answer, { includeCode: drafted.includeCode, acceptCodeRisk: codeRisk });
      // exportOffice saves the bytes the server returns as a file; its own route is replaced by the download route.
      const payload = await exportOffice({ token, officeId, post: (_path, tokenValue) => apiPost(exportPath(officeId, "download"), tokenValue, body) });
      const warnings = Array.isArray(payload.warnings) ? payload.warnings.map(exportWarningText) : [];
      if (alive.current) setDone([exportDownloadedText(exportFileName(payload)), ...warnings, t("ext.offices.xa.review_before_sharing")].join(" "));
    } catch (failure) {
      if (!alive.current) return;
      const failed = failureView(asApiError(failure));
      if (failed.stale) { setAnswer(null); setPreview(null); }
      setError(failed);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  const view = answer ? cardView(answer, preview, { advanced }) : null;
  const codeStale = Boolean(view) && drafted.includeCode !== withCode;
  const codeNeedsRisk = withCode && !codeRisk;
  const canSend = !busy && canDraft(request);
  return html`<section class="offices-xa" aria-label=${t("ext.offices.xa.title")}>
    <div class="offices-xa-head">
      <h3 class="offices-xa-title">${t("ext.offices.xa.title")}</h3>
      <button type="button" class="btn secondary small" onClick=${() => { held.clear(officeId); if (onClose) onClose(); }} ...${helpFor("offices.export_assistant", { scope: officeId + "-panel" })}>
        ${t("ext.offices.xa.close")}</button>
      <${Help} topic="offices.export_assistant" compact=${true} scope=${officeId + "-panel"} />
    </div>
    <p class="muted small">${t("ext.offices.xa.intro")}</p>
    <p class="offices-xa-note" role="note">${fixedNote()}</p>
    <form class="offices-xa-form" onSubmit=${(event) => { event.preventDefault(); if (canSend) { setAnswer(null); setPreview(null); ask(request.trim()); } }}>
      <label for=${"offices-xa-text-" + officeId}>${t("ext.offices.xa.text_label")}</label>
      <textarea id=${"offices-xa-text-" + officeId} maxLength=${TEXT_MAX} value=${request} disabled=${busy}
        ...${helpFor("offices.export_assistant_text", { scope: officeId })}
        onInput=${(event) => setRequest(event.target.value)}></textarea>
      <${Help} topic="offices.export_assistant_text" compact=${true} scope=${officeId} />
      <p class="muted small">${t("ext.offices.xa.model_note")}</p>
      ${advanced && html`<label class="check-inline">
        <input type="checkbox" checked=${includeCode} disabled=${busy} ...${helpFor("offices.export_assistant_code", { scope: officeId })}
          onChange=${(event) => { setIncludeCode(event.target.checked); if (!event.target.checked) setCodeRisk(false); }} />
        ${t("ext.offices.include_code")}
      </label>`}
      ${withCode && html`<label class="check-inline">
        <input type="checkbox" checked=${codeRisk} disabled=${busy} ...${helpFor("offices.export_assistant_code", { scope: officeId })}
          onChange=${(event) => setCodeRisk(event.target.checked)} />
        ${t("ext.offices.xa.code_risk")}
      </label>`}
      ${advanced && html`<${Help} topic="offices.export_assistant_code" compact=${true} scope=${officeId} />`}
      <div class="offices-xa-actions">
        <button type="submit" class="btn" disabled=${!canSend} ...${helpFor("offices.export_assistant_text", { scope: officeId })}>
          ${busy && !answer ? t("ext.offices.xa.drafting") : t("ext.offices.xa.send")}</button>
      </div>
    </form>

    ${notice && notice.cloud && html`<div class="offices-xa-notice" role="group" aria-label=${notice.cloud.title}>
      <p><strong>${notice.cloud.title}</strong></p>
      <p>${notice.cloud.text}</p>
      <p>${notice.cloud.costText} ${notice.cloud.filesText}</p>
      ${advanced && notice.cloud.receives.length > 0 && html`<ul class="offices-xa-list">
        ${notice.cloud.receives.map((path) => html`<li key=${path}><code>${path}</code></li>`)}</ul>`}
      <div class="offices-xa-actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.export_assistant_cloud", { scope: officeId })}
          onClick=${() => { confirmedModel.current = notice.cloud.model; ask(notice.value, { confirmCloud: true }); }}>${notice.cloud.sendLabel}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.export_assistant_cloud", { scope: officeId })}
          onClick=${() => setNotice(null)}>${t("ext.offices.xa.cancel")}</button>
        <${Help} topic="offices.export_assistant_cloud" compact=${true} scope=${officeId} />
      </div>
    </div>`}
    ${notice && !notice.cloud && html`<p class="offices-xa-notice" role="status">${notice.text}${notice.link
      ? html` <a href=${notice.link.href}>${notice.link.label}</a>` : null}</p>`}

    ${error && html`<div class="message error small" role="alert">
      <p>${error.text}</p>
      ${error.lines.length > 0 && html`<ul class="offices-xa-list">${error.lines.map((line, index) => html`<li key=${index}>${line}</li>`)}</ul>`}
    </div>`}
    ${done && html`<p class="message small" role="status">${done}</p>`}

    ${view && html`<section class="offices-xa-card" role="group" aria-label=${t("ext.offices.xa.card_title")}>
      <h4 tabIndex="-1" ref=${heading}>${t("ext.offices.xa.card_title")}</h4>
      <p class="muted small">${view.sourceText}${view.whyText ? " · " + view.whyText : ""}</p>
      <p class="offices-xa-summary">${view.summary}</p>
      <p class="muted small">${view.countsText}</p>
      <${PathList} title=${t("ext.offices.xa.included")} paths=${view.included} />
      <${PathList} title=${t("ext.offices.xa.left_out")} paths=${view.omitted} why=${t("ext.offices.xa.left_out_why")} />
      <${PathList} title=${t("ext.offices.xa.never")} paths=${view.excluded} why=${t("ext.offices.xa.never_why")} />
      ${!advanced && html`<p class="muted small">${t("ext.offices.xa.never_why")}</p>`}
      <${PathList} title=${t("ext.offices.xa.unverified")} paths=${view.unverified} />
      ${!advanced && view.unverifiedCount > 0 && html`<p class="small">${t("ext.offices.xa.unverified_count", { count: view.unverifiedCount })}</p>`}
      ${advanced && view.findings.length > 0 && html`<div class="offices-xa-block">
        <strong>${t("ext.offices.xa.findings")}</strong>
        <ul class="offices-xa-list">${view.findings.map((item, index) => html`<li key=${index}>${item.text}</li>`)}</ul>
      </div>`}
      ${!advanced && view.findingsCount > 0 && html`<p class="small">${t("ext.offices.xa.findings_count", { count: view.findingsCount })}</p>`}
      ${view.localModels.length > 0 && html`<div class="offices-xa-block">
        <strong>${t("ext.offices.xa.local_models")}</strong>
        <ul class="offices-xa-list">${view.localModels.map((name) => html`<li key=${name}>${name}</li>`)}</ul>
      </div>`}
      ${view.questions.length > 0 && html`<div class="offices-xa-block">
        <strong>${t("ext.offices.xa.questions")}</strong>
        <ul class="offices-xa-list">${view.questions.map((question, index) => html`<li key=${index}>${question}</li>`)}</ul>
      </div>`}
      ${view.blocked && html`<p class="message error small" role="alert">${t("ext.offices.xa.blocked")}</p>`}
      ${codeStale && html`<p class="muted small">${t("ext.offices.xa.code_redraft")}</p>`}
      ${!codeStale && codeNeedsRisk && html`<p class="muted small">${t("ext.offices.xa.code_risk_needed")}</p>`}
      ${changing && html`<div class="offices-xa-change">
        <label for=${"offices-xa-fix-" + officeId}>${t("ext.offices.xa.change_label")}</label>
        <textarea id=${"offices-xa-fix-" + officeId} maxLength=${TEXT_MAX} value=${correction} disabled=${busy}
          ...${helpFor("offices.export_assistant_change", { scope: officeId })}
          onInput=${(event) => setCorrection(event.target.value)}></textarea>
        ${correctionTooLong(baseText.current, correction) && html`<p class="message error small" role="alert">${t("ext.offices.xa.too_long")}</p>`}
        <div class="offices-xa-actions">
          <button type="button" class="btn" disabled=${busy || !canRedraft(baseText.current, correction)}
            ...${helpFor("offices.export_assistant_change", { scope: officeId })}
            onClick=${() => ask(composeText(baseText.current, correction))}>${busy ? t("ext.offices.xa.drafting") : t("ext.offices.xa.redraft")}</button>
          <button type="button" class="btn secondary" disabled=${busy}
            ...${helpFor("offices.export_assistant_change", { scope: officeId })}
            onClick=${() => { setChanging(false); setCorrection(""); }}>${t("ext.offices.xa.cancel")}</button>
        </div>
      </div>`}
      <div class="offices-xa-actions">
        <button type="button" class="btn" disabled=${busy || !view.canDownload || codeStale || codeNeedsRisk}
          ...${helpFor("offices.export_assistant_download", { scope: officeId })} onClick=${download}>${t("ext.offices.xa.download")}</button>
        <button type="button" class="btn secondary" disabled=${busy || changing}
          ...${helpFor("offices.export_assistant_change", { scope: officeId })} onClick=${() => setChanging(true)}>${t("ext.offices.xa.change")}</button>
        <${Help} topic="offices.export_assistant_download" compact=${true} scope=${officeId} />
        <${Help} topic="offices.export_assistant_change" compact=${true} scope=${officeId} />
      </div>
    </section>`}
  </section>`;
}
