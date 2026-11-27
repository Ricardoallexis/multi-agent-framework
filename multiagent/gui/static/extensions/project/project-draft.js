// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Nuevo proyecto» (PRY-T06): an empty box where the person says what they want to achieve. A model drafts the project once and
// the card shows its stages and tasks, what could not be verified and two buttons: «Crear proyecto» (the only thing that
// creates anything, with the huella of the preview shown) and «Cambiar algo» (drafts again with the correction; no limit).
// Nothing here offers a ready-made project. Every text from the model is drawn as a text node, never as markup. The rules are
// in project-draft-model.js (pure, tested with Node); this file only holds the state and draws it.
// PRY-T12: with the `stage` prop ({projectId, revision}) the same box adds one stage to the open project: «Add this stage» is the
// only button that writes, and the project id goes only in the path of the /stages routes.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import {
  CORRECTION_MAX, CREATE_PATH, DRAFT_PATH, STAGE, TEXT_MAX,
  answerKind, canDraft, canRedraft, cardView, cloudNotice, createBody, createFailure, draftBody, isConflict, reasonLink, reasonText,
  say, stagePath,
} from "./project-draft-model.js";

const asApiError = (error) => (error instanceof ApiError ? error : new ApiError({ message: String(error && error.message || error) }));

export function TaskList({ tasks }) {
  return html`<ul class="project-new-tasks">${tasks.map((task, index) => html`<li key=${index}>
      <strong>${task.title}</strong>
      ${task.objective && html`<span class="project-meta"> ${task.objective}</span>`}
    </li>`)}</ul>`;
}

export function StageList({ stages }) {
  return html`<ol class="project-new-stages">${stages.map((stage) => html`<li key=${stage.number} class="project-new-stage">
    <h5><span class="project-new-number">${t("ext.project.draft.stage_number", { number: stage.number })}</span> ${stage.title}</h5>
    <${TaskList} tasks=${stage.tasks} />
  </li>`)}</ol>`;
}

export function NewProject({ token, onClose, onCreated, onStale, stage }) {
  const mode = stage ? STAGE : "";
  const [request, setRequest] = useState("");
  const [correction, setCorrection] = useState("");
  const [changing, setChanging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState(null);           // the last answer with a draft; the card shows it
  const [notice, setNotice] = useState(null);           // an answer without a draft (a reason) or null
  const [pending, setPending] = useState(null);         // a remote model waits for the person's «yes»: {answer, options}
  const [error, setError] = useState("");
  const [added, setAdded] = useState(null);             // stage mode: the title of the stage that was just added
  const approval = useRef("");
  const confirmedModel = useRef("");      // the remote model the person said yes to in this box; forgotten when the box closes
  const alive = useRef(true);
  const heading = useRef(null);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => { if ((answer || added) && heading.current) heading.current.focus(); }, [answer, added]);

  async function ask(options) {
    if (busy) return;
    setBusy(true); setError(""); setNotice(null); setPending(null);
    try {
      const model = confirmedModel.current;
      const draftPath = stage ? stagePath(stage.projectId, "draft") : DRAFT_PATH;
      const reply = await apiPost(draftPath, token, draftBody(request, {
        ...options, approvalId: approval.current, ...(model ? { confirmCloud: true, cloudModel: model } : {}),
        ...(stage ? { expectedRevision: stage.revision } : {}),
      }));
      if (!alive.current) return;
      const kind = answerKind(reply, mode);
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
    const body = createBody(answer, mode);
    if (!body || busy) return;
    setBusy(true); setError("");
    try {
      const createPath = stage ? stagePath(stage.projectId, "create") : CREATE_PATH;
      const made = await apiPost(createPath, token, body);
      if (!alive.current) return;
      if (stage) { setAdded(answer.draft.title); setAnswer(null); setBusy(false); }
      if (onCreated) onCreated(made.id);
    } catch (failure) {
      const problem = asApiError(failure);
      if (!alive.current) return;
      setError(createFailure(problem, mode)); setBusy(false);
      if (stage && isConflict(problem)) { setAnswer(null); if (onStale) onStale(); }     // nothing was added: draft it again
    }
  }

  const view = answer ? cardView(answer, mode) : null;
  const cloud = pending ? cloudNotice(pending.answer, mode) : null;
  const link = notice ? reasonLink(notice, mode) : null;
  return html`<section class="project-new" aria-label=${say(mode, "title")}>
    <div class="project-new-head">
      <h3>${say(mode, "title")}</h3>
      ${stage ? html`<button type="button" class="btn secondary" onClick=${() => onClose && onClose()} ...${helpFor("project.stage_back")}>${say(mode, "back")}</button>
      <${Help} topic="project.stage_back" compact=${true} />` : html`<button type="button" class="btn secondary" onClick=${() => onClose && onClose()} ...${helpFor("project.draft_back")}>${say(mode, "back")}</button>
      <${Help} topic="project.draft_back" compact=${true} />`}
    </div>
    ${added !== null && html`<section class="project-new-card" role="status" aria-label=${t("ext.project.stage.added_title")}>
      <h4 tabIndex="-1" ref=${heading}>${t("ext.project.stage.added_title")}</h4>
      <p>${t("ext.project.stage.added", { title: added })}</p>
    </section>`}
    ${added === null && html`<p class="project-meta">${say(mode, "intro")}</p>`}
    ${added === null && html`<form class="project-new-form" onSubmit=${(event) => { event.preventDefault(); if (!busy && canDraft(request)) { setAnswer(null); ask({}); } }}>
      <label for="project-new-text">${say(mode, "text_label")}</label>
      <textarea id="project-new-text" maxLength=${TEXT_MAX} value=${request} disabled=${busy} ...${helpFor("project.draft_text")}
        onInput=${(event) => setRequest(event.target.value)}></textarea>
      <${Help} topic="project.draft_text" />
      <p class="project-meta">${say(mode, "model_note")}</p>
      <div class="project-new-actions">
        ${stage ? html`<button type="submit" class="btn" disabled=${busy || !canDraft(request)} ...${helpFor("project.stage_send")}>
          ${busy && !answer ? t("ext.project.draft.drafting") : say(mode, "send")}</button>
        <${Help} topic="project.stage_send" compact=${true} />` : html`<button type="submit" class="btn" disabled=${busy || !canDraft(request)} ...${helpFor("project.draft_send")}>
          ${busy && !answer ? t("ext.project.draft.drafting") : say(mode, "send")}</button>
        <${Help} topic="project.draft_send" compact=${true} />`}
      </div>
    </form>`}

    ${cloud && html`<div class="project-new-notice" role="group" aria-label=${t("ext.project.draft.cloud_title")}>
      <p>${cloud.text}</p>
      ${cloud.contextText && html`<p>${cloud.contextText}</p>`}
      ${cloud.privateText && html`<p>${cloud.privateText}</p>`}
      ${cloud.localText && html`<p>${cloud.localText} <a href="#apps">${t("ext.project.draft.apps_link")}</a></p>`}
      <div class="project-new-actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("project.draft_cloud")}
          onClick=${() => { confirmedModel.current = cloud.model; ask(pending.options); }}>${cloud.sendLabel}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("project.draft_cloud")}
          onClick=${() => setPending(null)}>${t("ext.project.draft.cancel")}</button>
        <${Help} topic="project.draft_cloud" compact=${true} />
      </div>
    </div>`}

    ${notice && html`<p class="project-new-notice" role="status">${reasonText(notice, mode)}${link ? html` <a href=${link.href}>${link.label}</a>` : null}</p>`}
    ${error && html`<p class="project-error" role="alert">${error}</p>`}

    ${view && html`<section class="project-new-card" role="group" aria-label=${say(mode, "card_title")}>
      <h4 tabIndex="-1" ref=${heading}>${view.title}</h4>
      <p class="project-meta">${view.countsText} · ${view.modelText}</p>
      ${stage ? html`<${TaskList} tasks=${view.tasks} />` : html`<${StageList} stages=${view.stages} />`}
      ${view.problems.length > 0 && html`<div class="project-new-problems" role="alert">
        <strong>${t("ext.project.draft.problems")}</strong>
        <ul>${view.problems.map((problem, index) => html`<li key=${index}>${problem}</li>`)}</ul>
      </div>`}
      <p class="project-meta">${stage ? view.unauthorizedText : t("ext.project.draft.private_note")}</p>
      ${view.noExecutor && html`<p class="project-meta">${say(mode, "no_executor")} <a href="#apps">${t("ext.project.draft.apps_link")}</a></p>`}
      <p class="project-meta">${say(mode, "not_created")}</p>
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
        ${stage ? html`<button type="button" class="btn" disabled=${busy || !view.canCreate} ...${helpFor("project.stage_create")}
          onClick=${create}>${say(mode, "create")}</button>` : html`<button type="button" class="btn" disabled=${busy || !view.canCreate} ...${helpFor("project.draft_create")}
          onClick=${create}>${say(mode, "create")}</button>`}
        <button type="button" class="btn secondary" disabled=${busy || changing} ...${helpFor("project.draft_change")}
          onClick=${() => setChanging(true)}>${t("ext.project.draft.change")}</button>
        ${stage ? html`<${Help} topic="project.stage_create" compact=${true} />` : html`<${Help} topic="project.draft_create" compact=${true} />`}<${Help} topic="project.draft_change" compact=${true} />
      </div>
    </section>`}
  </section>`;
}
