// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Nuevo proyecto» (PRY-T06): an empty box where the person says what they want to achieve. A model drafts the project once and
// the card shows its stages and tasks, what could not be verified and two buttons: «Crear proyecto» (the only thing that
// creates anything, with the huella of the preview shown) and «Cambiar algo» (drafts again with the correction; no limit).
// Nothing here offers a ready-made project. Every text from the model is drawn as a text node, never as markup. The rules are
// in project-draft-model.js (pure, tested with Node); this file only holds the state and draws it.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import {
  CORRECTION_MAX, CREATE_PATH, DRAFT_PATH, TEXT_MAX,
  answerKind, canDraft, canRedraft, cardView, cloudNotice, createBody, createFailure, draftBody, reasonLink, reasonText,
} from "./project-draft-model.js";

const asApiError = (error) => (error instanceof ApiError ? error : new ApiError({ message: String(error && error.message || error) }));

export function StageList({ stages }) {
  return html`<ol class="project-new-stages">${stages.map((stage) => html`<li key=${stage.number} class="project-new-stage">
    <h5><span class="project-new-number">${t("ext.project.draft.stage_number", { number: stage.number })}</span> ${stage.title}</h5>
    <ul class="project-new-tasks">${stage.tasks.map((task, index) => html`<li key=${index}>
      <strong>${task.title}</strong>
      ${task.objective && html`<span class="project-meta"> ${task.objective}</span>`}
    </li>`)}</ul>
  </li>`)}</ol>`;
}

export function NewProject({ token, onClose, onCreated }) {
  const [request, setRequest] = useState("");
  const [correction, setCorrection] = useState("");
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState(null);           // the last answer with a draft; the card shows it
  const [notice, setNotice] = useState(null);           // an answer without a draft (a reason) or null
  const [pending, setPending] = useState(null);         // a remote model waits for the person's «yes»: {answer, options}
  const [error, setError] = useState("");
  const approval = useRef("");
  const confirmedModel = useRef("");      // the remote model the person said yes to in this box; forgotten when the box closes
  const alive = useRef(true);
  const heading = useRef(null);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => { if (answer && heading.current) heading.current.focus(); }, [answer]);

  async function ask(options) {
    if (busy) return;
    setBusy(true); setError(""); setNotice(null); setPending(null);
    try {
      const model = confirmedModel.current;
      const reply = await apiPost(DRAFT_PATH, token, draftBody(request, {
        ...options, approvalId: approval.current, ...(model ? { confirmCloud: true, cloudModel: model } : {}),
      }));
      if (!alive.current) return;
      const kind = answerKind(reply);
      if (kind === "draft") { setAnswer(reply); setChanging(false); setCorrection(""); approval.current = ""; }
      else if (kind === "cloud_confirmation_required") { confirmedModel.current = ""; setPending({ answer: reply, options }); }
      else {
        if (kind === "secret_review_required" && reply.approval_id) approval.current = reply.approval_id;
        setNotice(reply);
      }
    } catch (failure) {
      if (alive.current) setError(asApiError(failure).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }

  async function create() {
    const body = createBody(answer);
    if (!body || busy) return;
    setBusy(true); setError("");
    try {
      const made = await apiPost(CREATE_PATH, token, body);
      if (alive.current && onCreated) onCreated(made.id);
    } catch (failure) {
      if (alive.current) { setError(createFailure(asApiError(failure))); setBusy(false); }
    }
  }

  const view = answer ? cardView(answer) : null;
  const cloud = pending ? cloudNotice(pending.answer) : null;
  const link = notice ? reasonLink(notice) : null;
  return html`<section class="project-new" aria-label=${t("ext.project.draft.title")}>
    <div class="project-new-head">
      <h3>${t("ext.project.draft.title")}</h3>
      <button type="button" class="btn secondary" onClick=${() => onClose && onClose()} ...${helpFor("project.draft_back")}>${t("ext.project.draft.back")}</button>
      <${Help} topic="project.draft_back" compact=${true} />
    </div>
    <p class="project-meta">${t("ext.project.draft.intro")}</p>
    <form class="project-new-form" onSubmit=${(event) => { event.preventDefault(); if (!busy && canDraft(request)) { setAnswer(null); ask({}); } }}>
      <label for="project-new-text">${t("ext.project.draft.text_label")}</label>
      <textarea id="project-new-text" maxLength=${TEXT_MAX} value=${request} disabled=${busy} ...${helpFor("project.draft_text")}
        onInput=${(event) => setRequest(event.target.value)}></textarea>
      <${Help} topic="project.draft_text" />
      <p class="project-meta">${t("ext.project.draft.model_note")}</p>
      <div class="project-new-actions">
        <button type="submit" class="btn" disabled=${busy || !canDraft(request)} ...${helpFor("project.draft_send")}>
          ${busy && !answer ? t("ext.project.draft.drafting") : t("ext.project.draft.send")}</button>
        <${Help} topic="project.draft_send" compact=${true} />
      </div>
    </form>

    ${cloud && html`<div class="project-new-notice" role="group" aria-label=${t("ext.project.draft.cloud_title")}>
      <p>${cloud.text}</p>
      <p>${cloud.privateText}</p>
      ${cloud.localText && html`<p>${cloud.localText} <a href="#apps">${t("ext.project.draft.apps_link")}</a></p>`}
      <div class="project-new-actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("project.draft_cloud")}
          onClick=${() => { confirmedModel.current = cloud.model; ask(pending.options); }}>${cloud.sendLabel}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("project.draft_cloud")}
          onClick=${() => setPending(null)}>${t("ext.project.draft.cancel")}</button>
        <${Help} topic="project.draft_cloud" compact=${true} />
      </div>
    </div>`}

    ${notice && html`<p class="project-new-notice" role="status">${reasonText(notice)}${link ? html` <a href=${link.href}>${link.label}</a>` : null}</p>`}
    ${error && html`<p class="project-error" role="alert">${error}</p>`}

    ${view && html`<section class="project-new-card" role="group" aria-label=${t("ext.project.draft.card_title")}>
      <h4 tabIndex="-1" ref=${heading}>${view.title}</h4>
      <p class="project-meta">${t("ext.project.draft.card_counts", view.counts)} · ${view.modelText}</p>
      <${StageList} stages=${view.stages} />
      ${view.problems.length > 0 && html`<div class="project-new-problems" role="alert">
        <strong>${t("ext.project.draft.problems")}</strong>
        <ul>${view.problems.map((problem, index) => html`<li key=${index}>${problem}</li>`)}</ul>
      </div>`}
      <p class="project-meta">${t("ext.project.draft.private_note")}</p>
      ${view.noExecutor && html`<p class="project-meta">${t("ext.project.draft.no_executor")} <a href="#apps">${t("ext.project.draft.apps_link")}</a></p>`}
      <p class="project-meta">${t("ext.project.draft.not_created")}</p>
      ${changing && html`<div class="project-new-change">
        <label for="project-new-correction">${t("ext.project.draft.change_label")}</label>
        <textarea id="project-new-correction" maxLength=${CORRECTION_MAX} value=${correction} disabled=${busy}
          ...${helpFor("project.draft_change")} onInput=${(event) => setCorrection(event.target.value)}></textarea>
        <div class="project-new-actions">
          <button type="button" class="btn" disabled=${busy || !canRedraft(correction)} ...${helpFor("project.draft_redraft")}
            onClick=${() => ask({ previous: answer.draft, correction })}>${busy ? t("ext.project.draft.drafting") : t("ext.project.draft.redraft")}</button>
          <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("project.draft_redraft")}
            onClick=${() => { setChanging(false); setCorrection(""); }}>${t("ext.project.draft.cancel")}</button>
          <${Help} topic="project.draft_redraft" compact=${true} />
        </div>
      </div>`}
      <div class="project-new-actions">
        <button type="button" class="btn" disabled=${busy || !view.canCreate} ...${helpFor("project.draft_create")}
          onClick=${create}>${t("ext.project.draft.create")}</button>
        <button type="button" class="btn secondary" disabled=${busy || changing} ...${helpFor("project.draft_change")}
          onClick=${() => setChanging(true)}>${t("ext.project.draft.change")}</button>
        <${Help} topic="project.draft_create" compact=${true} /><${Help} topic="project.draft_change" compact=${true} />
      </div>
    </section>`}
  </section>`;
}
