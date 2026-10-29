// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The decision card of a task that waits for a usage limit (CNT-T06): who reached which limit, the local time of the reset with its
// source, the saved work, the hand-offs used, and the choices «Esperar hasta HH:MM», «Delegar a <agente>» and «Decidir después».
// Nothing is saved without a confirmation that says what will happen; «Decidir después» saves nothing.
//   CardView      pure drawing of a card from props (no hooks, so the Node tests call it): card, ui = { confirming, later }, busy, message
//   DecisionCard  the card with its state: opens the confirmation, sends the decision and tells the page when the card changed
// All text is shown as plain text; the card never draws what the server sends as markup.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import {
  HISTORY_NOTE, confirmText, decisionBody, decisionErrorText, decisionPath, describeCard, isStale, newDecisionId,
} from "./model.js";

const idPart = (value) => String(value).replace(/[^A-Za-z0-9_-]+/g, "-");

// The confirmation box: it says what is going to be saved. The focus goes to «Volver», so Enter never confirms by accident,
// and Escape goes back.
export function ConfirmBox({ text, busy, onConfirm, onBack }) {
  const back = useRef(null);
  useEffect(() => { if (back.current) back.current.focus(); }, []);
  const onKeyDown = (event) => { if (event.key === "Escape") { event.preventDefault(); onBack(); } };
  return html`<div class="confirm continuity-confirm" role="group" aria-label=${text.title} onKeyDown=${onKeyDown}>
    <p><strong>${text.title}</strong></p>
    ${text.lines.map((line, index) => html`<p key=${index} class="muted small">${line}</p>`)}
    <div class="actions">
      <button type="button" class="btn continuity-btn" data-help-exempt="confirm" disabled=${busy} onClick=${onConfirm}>${text.yes}</button>
      <button type="button" class="btn secondary continuity-btn" data-help-exempt="confirm" ref=${back} disabled=${busy}
        onClick=${onBack}>Volver</button>
    </div>
  </div>`;
}

export function CardView({ card, ui = {}, now = new Date(), busy = false, message = null, cardRef = null, showOpen = true, onChoose = () => {},
  onConfirm = () => {}, onBack = () => {}, onLater = () => {}, onReopen = () => {} }) {
  const view = describeCard(card, now);
  const scope = idPart(card.task_id);
  const titleId = "continuity-card-" + scope + "-title";
  const confirming = ui.confirming || null;
  const pressedWait = card.decision === "wait" ? "true" : "false";
  const hasChoices = Boolean(view.wait) || view.candidates.length > 0;
  return html`<article class="continuity-card" role="group" aria-labelledby=${titleId} tabindex="-1" ref=${cardRef}
    data-decision=${card.decision} data-reason=${card.reason || ""}>
    <h4 id=${titleId} class="continuity-card-title">${view.title}</h4>
    <p class="continuity-task muted small">${view.task}</p>
    <ul class="continuity-facts">
      <li class="continuity-reset"><span class="continuity-clock" role="img" aria-label="Reloj: hora local del restablecimiento">◷</span>
        <span>${view.reset}</span></li>
      <li>${view.checkpoint}</li>
      <li>${view.handoffs}${view.exhausted ? ". Ya no quedan traspasos para esta tarea." : ""}</li>
    </ul>
    <p class="continuity-note muted small">${HISTORY_NOTE}</p>
    ${view.decision && html`<p class="continuity-decision" role="status">${view.decision}</p>`}
    ${view.blocked && html`<p class="continuity-note continuity-blocked" role="status">${view.blocked}</p>`}
    ${confirming
      ? html`<${ConfirmBox} key=${confirming.decisionId} text=${confirmText(card, confirming, now)} busy=${busy} onConfirm=${onConfirm} onBack=${onBack} />`
      : ui.later
        ? html`<div class="continuity-actions" role="group" aria-label="Decisión pendiente">
            <p class="continuity-note muted small">Sin decidir: la tarea sigue en espera y la decisión queda pendiente.</p>
            <span class="continuity-option"><button type="button" class="btn secondary continuity-btn" ...${helpFor("continuity.later", { scope })}
              onClick=${onReopen}>Decidir ahora</button><${Help} topic="continuity.later" scope=${scope} compact=${true} /></span>
          </div>`
        : html`<div class="continuity-actions" role="group" aria-label="Qué quieres hacer con esta tarea">
            ${view.wait && html`<span class="continuity-option"><button type="button" class="btn continuity-btn" aria-pressed=${pressedWait}
              disabled=${busy} ...${helpFor("continuity.wait", { scope })} onClick=${() => onChoose({ kind: "wait" })}>${view.wait}</button>
              <${Help} topic="continuity.wait" scope=${scope} compact=${true} /></span>`}
            ${view.candidates.map((candidate) => html`<span class="continuity-option" key=${candidate.id}>
              <button type="button" class="btn secondary continuity-btn" aria-pressed=${card.decision === "delegate" && card.candidate === candidate.id ? "true" : "false"}
                disabled=${busy || !candidate.available} ...${helpFor("continuity.delegate", { scope: scope + "-" + idPart(candidate.id) })}
                onClick=${() => onChoose({ kind: "delegate", executor_id: candidate.id })}>${candidate.label}</button>
              <${Help} topic="continuity.delegate" scope=${scope + "-" + idPart(candidate.id)} compact=${true} />
              <span class="continuity-option-note muted small">${candidate.note}</span></span>`)}
            ${!hasChoices && html`<p class="continuity-note muted small">${view.exhausted
              ? "No hay otra opción: ya no quedan traspasos."
              : "No hay otra opción: reanuda la tarea cuando la aplicación vuelva a estar disponible."}</p>`}
            <span class="continuity-option"><button type="button" class="btn secondary continuity-btn" ...${helpFor("continuity.later", { scope })}
              disabled=${busy} onClick=${onLater}>Decidir después</button>
              <${Help} topic="continuity.later" scope=${scope} compact=${true} /></span>
          </div>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
    ${showOpen && card.session_ref && html`<p class="continuity-open small"><a class="continuity-link" href=${"#sessions/" + encodeURIComponent(card.session_ref)}>Abrir la sesión</a></p>`}
  </article>`;
}

// One card with its own state. `showOpen` false hides «Abrir la sesión» (the right panel already shows that session). `onChanged(card)` receives the card the server answered after a decision; `onStale()` asks the page
// to read the list again after a conflict (the task changed, or it is no longer held).
export function DecisionCard({ token, card, showOpen = true, onChanged = () => {}, onStale = () => {} }) {
  const [ui, setUi] = useState({ confirming: null, later: false });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  // When the confirmation closes (back, saved or failed) the focus returns to the card, so the keyboard does not start over.
  const cardRef = useRef(null);
  const opened = useRef(false);
  useEffect(() => {
    if (ui.confirming) { opened.current = true; return; }
    if (opened.current && cardRef.current) cardRef.current.focus({ preventScroll: true });
    opened.current = false;
  }, [Boolean(ui.confirming)]);

  // A confirmation belongs to the card the person saw: when the task changes under it, it closes.
  useEffect(() => { setUi((previous) => (previous.confirming ? { ...previous, confirming: null } : previous)); }, [card.revision]);

  const onChoose = (choice) => {
    setMessage(null);
    setUi({ confirming: { ...choice, decisionId: newDecisionId() }, later: false });
  };
  const onBack = () => setUi((previous) => ({ ...previous, confirming: null }));

  async function onConfirm() {
    const choice = ui.confirming;
    if (!choice || busy) return;
    setBusy(true);
    try {
      const answer = await apiPost(decisionPath(card.task_id), token, decisionBody(card, choice.kind, choice.executor_id, choice.decisionId));
      setUi({ confirming: null, later: false });
      setMessage({ kind: "ok", text: choice.kind === "wait" ? "Decisión guardada: la tarea espera." : "Decisión guardada: pediste delegar la tarea." });
      onChanged(answer.card || null);
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setUi((previous) => ({ ...previous, confirming: null }));
      setMessage({ kind: "error", text: decisionErrorText(failure) });
      if (isStale(failure)) onStale();
    } finally {
      setBusy(false);
    }
  }

  return html`<${CardView} card=${card} ui=${ui} now=${new Date()} busy=${busy} message=${message} cardRef=${cardRef} showOpen=${showOpen} onChoose=${onChoose} onConfirm=${onConfirm}
    onBack=${onBack} onLater=${() => { setMessage(null); setUi({ confirming: null, later: true }); }}
    onReopen=${() => setUi({ confirming: null, later: false })} />`;
}
