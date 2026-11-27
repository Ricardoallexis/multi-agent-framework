// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Explain this office to me» and «Try in Mock» (OFA-T06): beside the review card of a staged package, a conversation that says
// what the office does (what each area does, what each tool reads, writes and asks, which inputs nobody asks for, what could
// not be verified) and a button that tries the office in a disposable copy. The model only explains: nothing here approves,
// trusts code, changes a finding or the risk box, and the install keeps its one approval per version. There is no polling:
// the explanation and the trial answer in the request that asks for them, and one trial runs at a time. Every text from the
// package or from a model is drawn as a text node, never as markup. The rules are in import-assistant-model.js (pure, tested
// with Node); this file only holds the state and draws it.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { currentMode, onModeChange } from "../../js/gui-mode.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { createHeldStore } from "./export-assistant-model.js";
import {
  TEXT_MAX, answerKind, canAsk, explainBody, failureView, pauseView, rehearsalGate, rehearsalView, rehearseBody, reportView, stagePath,
} from "./import-assistant-model.js";

// SEC-T04 pattern (export-assistant.js): the question that waits for a secret decision survives a visit to #approvals, which
// unmounts the card. It lives in module memory only, by staged package: a reload starts clean. `openStages` remembers which
// assistants were open so the card can reopen them.
const held = createHeldStore();
const openStages = new Set();
export function forgetAssistant(stageId) { held.clear(stageId); openStages.delete(stageId); }

const asApiError = (error) => (error instanceof ApiError ? error : new ApiError({ message: String(error && error.message || error) }));

function Lines({ title, why = "", rows }) {
  return rows.length > 0 && html`<div class="offices-ia-block">
    <strong>${title}</strong>
    ${why && html`<p class="muted small">${why}</p>`}
    <ul class="offices-ia-list">${rows.map((row, index) => html`<li key=${index}>${row}</li>`)}</ul>
  </div>`;
}

function RehearsalResult({ view }) {
  return html`<div class="offices-ia-result" role="group" aria-label=${t("ext.offices.ia.result_title")}>
    <p><strong>${t("ext.offices.ia.result_title")}</strong></p>
    <p class=${view.failed ? "message error small" : "small"}>${view.stateText}</p>
    <p class="muted small">${view.doneText}${view.deletedText ? " " + view.deletedText : ""}</p>
    <ul class="offices-ia-list">${view.flows.map((flow) => html`<li key=${flow.key}>
      <strong>${flow.title}</strong>
      ${flow.reasonText && html`<p class="small">${flow.reasonText}</p>`}
      ${flow.steps.length > 0 && html`<ul class="offices-ia-list">${flow.steps.map((step, index) => html`<li key=${index}>${step}</li>`)}</ul>`}
      ${flow.syntheticText && html`<p class="muted small">${flow.syntheticText}</p>`}
      ${flow.missingText && html`<p class="small">${flow.missingText}</p>`}
    </li>`)}</ul>
    ${view.omittedText && html`<p class="muted small">${view.omittedText}</p>`}
    <p><strong>${t("ext.offices.ia.files_title")}</strong></p>
    ${view.files.length > 0
      ? html`<ul class="offices-ia-list">${view.files.map((file) => html`<li key=${file.path}><code>${file.text}</code></li>`)}</ul>`
      : html`<p class="muted small">${t("ext.offices.ia.files_none")}</p>`}
  </div>`;
}

// «Try in Mock»: the fixed line, the state of the rehearsal that installing may need, the button and the last result.
function TryInMock({ token, stageId, checks, trial, forced, onTrial, disabled, scope }) {
  const [trying, setTrying] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const gate = rehearsalGate(checks, trial, { forced });
  const view = rehearsalView(trial);

  async function run() {
    if (trying || disabled) return;
    setTrying(true); setError("");
    try {
      const result = await apiPost(stagePath(stageId, "rehearse"), token, rehearseBody());
      if (alive.current) onTrial(result);
    } catch (failure) {
      if (!alive.current) return;
      const failed = failureView(asApiError(failure));
      if (failed.stale) onTrial(null);                // the package is gone: the result of an earlier trial no longer holds
      setError(failed.text);
    } finally {
      if (alive.current) setTrying(false);
    }
  }

  return html`<div class="offices-ia-trial">
    <p class=${"offices-ia-gate is-" + gate.kind} role=${gate.kind === "required" ? "alert" : "status"}>${gate.text}</p>
    <p class="muted small">${t("ext.offices.ia.rehearse_line")}</p>
    <div class="offices-ia-actions">
      <button type="button" class="btn secondary" disabled=${trying || disabled} ...${helpFor("offices.rehearse", { scope })}
        onClick=${run}>${trying ? t("ext.offices.ia.rehearse_running") : t("ext.offices.ia.rehearse")}</button>
      <${Help} topic="offices.rehearse" compact=${true} scope=${scope} />
    </div>
    ${trying && html`<p class="muted small" role="status">${t("ext.offices.ia.rehearse_running")}</p>`}
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${view && html`<${RehearsalResult} view=${view} />`}
  </div>`;
}

function Conversation({ token, stageId, onClose, scope }) {
  const [mode, setMode] = useState(currentMode);
  const [checks, setChecks] = useState(null);      // the fixed read of the package (no model), asked once when this opens
  const [answer, setAnswer] = useState(null);      // the last answer with an explanation
  const kept = useRef(held.get(stageId)).current;   // what a visit to #approvals left, or null
  const [question, setQuestion] = useState(kept ? kept.request : "");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(() => (kept && kept.approvalId
    ? { ...pauseView({ reason: "secret_review_required", approval_id: kept.approvalId }), value: kept.request } : null));
  const [error, setError] = useState("");
  const approval = useRef(kept ? kept.approvalId : "");
  const confirmedModel = useRef(kept ? kept.confirmedModel : "");   // the remote model the person said yes to in this box
  const alive = useRef(true);
  const advanced = mode === "advanced";
  useEffect(() => onModeChange(setMode), []);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => {
    let current = true;
    apiGet(stagePath(stageId, "checks"), token)
      .then((report) => { if (current) setChecks(report); })
      .catch((failure) => {
        if (!current) return;
        const failed = failureView(asApiError(failure));
        if (failed.stale) { setAnswer(null); setChecks(null); }
        setError(failed.text);
      });
    return () => { current = false; };
  }, [stageId, token]);

  async function ask({ confirmCloud = false, value = question } = {}) {
    if (busy) return;
    setBusy(true); setError(""); setNotice(null);
    try {
      let reply = await apiPost(stagePath(stageId, "explain"), token, explainBody(value, { confirmCloud, approvalId: approval.current }));
      if (!alive.current) return;
      // The person already said yes to this very model in this box: the same yes is sent, any other model asks again.
      if (answerKind(reply) === "cloud_confirmation_required" && reply.model && reply.model === confirmedModel.current) {
        reply = await apiPost(stagePath(stageId, "explain"), token, explainBody(value, { confirmCloud: true, approvalId: approval.current }));
        if (!alive.current) return;
      }
      const kind = answerKind(reply);
      if (kind === "explanation") {
        approval.current = "";
        held.clear(stageId);
        setAnswer(reply);
      } else {
        const paused = pauseView(reply);
        if (kind === "cloud_confirmation_required") confirmedModel.current = "";
        if (kind === "secret_review_required" && paused.approvalId) {
          approval.current = paused.approvalId;
          held.set(stageId, { request: value, approvalId: paused.approvalId, confirmedModel: confirmedModel.current });
        }
        setNotice({ ...paused, value });
      }
    } catch (failure) {
      const failed = asApiError(failure);
      // The server no longer accepts that approval: forget it so the next question asks for a new one.
      if (approval.current && failed.status >= 400 && failed.status < 500) { approval.current = ""; held.clear(stageId); }
      if (!alive.current) return;
      const view = failureView(failed);
      if (view.stale) { setAnswer(null); setChecks(null); }
      setError(view.text);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  const view = reportView(answer || checks, { advanced });
  return html`<section class="offices-ia-panel" aria-label=${t("ext.offices.ia.title")}>
    <div class="offices-ia-head">
      <h4 class="offices-ia-title">${t("ext.offices.ia.title")}</h4>
      <button type="button" class="btn secondary small" onClick=${() => { held.clear(stageId); onClose(); }} ...${helpFor("offices.import_assistant", { scope: scope + "-panel" })}>
        ${t("ext.offices.ia.close")}</button>
      <${Help} topic="offices.import_assistant" compact=${true} scope=${scope + "-panel"} />
    </div>
    <p class="muted small">${t("ext.offices.ia.intro")}</p>
    <p class="offices-ia-note" role="note">${t("ext.offices.ia.note")}</p>
    <form class="offices-ia-form" onSubmit=${(event) => { event.preventDefault(); if (canAsk(question)) ask(); }}>
      <label for=${"offices-ia-question-" + scope}>${t("ext.offices.ia.question_label")}</label>
      <textarea id=${"offices-ia-question-" + scope} maxLength=${TEXT_MAX} value=${question} disabled=${busy}
        placeholder=${t("ext.offices.ia.question_label")} ...${helpFor("offices.import_assistant_question", { scope })}
        onInput=${(event) => setQuestion(event.target.value)}></textarea>
      <${Help} topic="offices.import_assistant_question" compact=${true} scope=${scope} />
      <p class="muted small">${t("ext.offices.ia.model_note")}</p>
      <div class="offices-ia-actions">
        <button type="submit" class="btn" disabled=${busy || !canAsk(question)} ...${helpFor("offices.import_assistant_question", { scope })}>
          ${busy ? t("ext.offices.ia.explaining") : t("ext.offices.ia.explain")}</button>
      </div>
    </form>

    ${notice && notice.cloud && html`<div class="offices-ia-notice" role="group" aria-label=${notice.cloud.title}>
      <p><strong>${notice.cloud.title}</strong></p>
      <p>${notice.cloud.text}</p>
      <p>${notice.cloud.costText} ${notice.cloud.filesText}</p>
      ${notice.cloud.receives.length > 0 && html`<ul class="offices-ia-list">
        ${notice.cloud.receives.map((path) => html`<li key=${path}><code>${path}</code></li>`)}</ul>`}
      <div class="offices-ia-actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.import_assistant_cloud", { scope })}
          onClick=${() => { confirmedModel.current = notice.cloud.model; ask({ confirmCloud: true, value: notice.value }); }}>${notice.cloud.sendLabel}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.import_assistant_cloud", { scope })}
          onClick=${() => setNotice(null)}>${t("ext.offices.xa.cancel")}</button>
        <${Help} topic="offices.import_assistant_cloud" compact=${true} scope=${scope} />
      </div>
    </div>`}
    ${notice && !notice.cloud && html`<p class="offices-ia-notice" role="status">${notice.text}${notice.link
      ? html` <a href=${notice.link.href}>${notice.link.label}</a>` : null}</p>`}
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${!view && !error && html`<p class="muted small" role="status">${t("ext.offices.ia.reading")}</p>`}

    ${view && html`<div class="offices-ia-report">
      <p class="muted small">${view.sourceText}${view.whyText ? " · " + view.whyText : ""}</p>
      ${view.summary && html`<p class="offices-ia-summary">${view.summary}</p>`}
      ${view.answer && html`<div class="offices-ia-block">
        <strong>${t("ext.offices.ia.answer_title")}</strong>
        <p>${view.answer}</p>
      </div>`}
      ${view.advice && html`<p class="muted small">${view.advice}</p>`}
      ${view.areas.length > 0 && html`<div class="offices-ia-block">
        <strong>${t("ext.offices.ia.areas")}</strong>
        <ul class="offices-ia-list">${view.areas.map((area) => html`<li key=${area.id}>
          <strong>${area.name}</strong>
          ${area.text && html`<p class="small">${area.text}</p>`}
          <p class="muted small">${area.workflowsText}</p>
          ${area.workflows.filter((flow) => flow.description).map((flow) => html`<p key=${flow.id} class="small">${flow.id}: ${flow.description}</p>`)}
        </li>`)}</ul>
      </div>`}
      ${view.tools.length > 0 && html`<div class="offices-ia-block">
        <strong>${t("ext.offices.ia.tools")}</strong>
        <ul class="offices-ia-list">${view.tools.map((tool) => html`<li key=${tool.key}>
          <strong>${tool.title}</strong>${tool.id && html` <code>${tool.id}</code>`}
          <p class="muted small">${tool.whereText}</p>
          ${tool.note && html`<p class="small">${tool.note}</p>`}
          <p class="small">${tool.readsText}</p>
          <p class="small">${tool.writesText}</p>
          <p class="small">${tool.permissionText}</p>
        </li>`)}</ul>
      </div>`}
      <${Lines} title=${t("ext.offices.ia.needs_root")} rows=${view.needsRoot} why=${t("ext.offices.ia.needs_root_why")} />
      <${Lines} title=${t("ext.offices.ia.unasked")} why=${t("ext.offices.ia.unasked_why")}
        rows=${view.unasked.map((row) => row.where + ": " + row.inputs.join(", "))} />
      <${Lines} title=${t("ext.offices.ia.missing")} why=${t("ext.offices.ia.missing_why")}
        rows=${view.missing.map((row) => row.where + ": " + row.inputs.join(", "))} />
      ${(view.unverifiedLines.length > 0 || view.unverifiedFiles.length > 0 || view.unverifiedFilesText || view.codeNotChecked) && html`<div class="offices-ia-block">
        <strong>${t("ext.offices.ia.unverified")}</strong>
        <ul class="offices-ia-list">
          ${view.unverifiedLines.map((line, index) => html`<li key=${"f" + index}>${line}</li>`)}
          ${view.unverifiedFiles.map((path) => html`<li key=${path}><code>${path}</code></li>`)}
          ${view.unverifiedFilesText && html`<li>${view.unverifiedFilesText}</li>`}
          ${view.codeNotChecked && html`<li>${t("ext.offices.ia.code_not_checked")}</li>`}
        </ul>
        ${view.codeFiles.length > 0 && html`<p class="small">${t("ext.offices.ia.code_files", { files: view.codeFiles.join(", ") })}</p>`}
      </div>`}
      ${advanced && view.findings.length > 0 && html`<${Lines} title=${t("ext.offices.ia.findings")} rows=${view.findings.map((item) => item.text)} />`}
      ${!advanced && view.findingsCount > 0 && html`<p class="small">${t("ext.offices.ia.findings_count", { count: view.findingsCount })}</p>`}
    </div>`}
  </section>`;
}

// What import.js draws beside the review card. `checks` is the card's `import_checks`; `trial` the last answer of «Try in Mock»
// (kept by the card, since the install needs to know it); `forced` is set after the install answered 409 rehearsal_required.
export function ImportAssistant({ token, stageId, checks, trial, forced = false, onTrial, disabled = false, scope = "new-office" }) {
  const [open, setOpenState] = useState(() => openStages.has(stageId));
  const setOpen = (change) => setOpenState((value) => {
    const next = typeof change === "function" ? change(value) : change;
    if (next) openStages.add(stageId); else openStages.delete(stageId);
    return next;
  });
  const here = scope + "-assistant";
  return html`<section class="offices-ia" aria-label=${t("ext.offices.ia.section")}>
    <div class="offices-ia-actions">
      <button type="button" class="btn secondary" disabled=${disabled} aria-expanded=${open ? "true" : "false"}
        ...${helpFor("offices.import_assistant", { scope: here })} onClick=${() => setOpen((value) => !value)}>${t("ext.offices.ia.entry")}</button>
      <${Help} topic="offices.import_assistant" compact=${true} scope=${here} />
    </div>
    ${open && html`<${Conversation} token=${token} stageId=${stageId} scope=${here} onClose=${() => setOpen(false)} />`}
    <${TryInMock} token=${token} stageId=${stageId} checks=${checks} trial=${trial} forced=${forced} onTrial=${onTrial}
      disabled=${disabled} scope=${scope + "-trial"} />
  </section>`;
}
