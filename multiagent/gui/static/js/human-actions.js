// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Human actions of one run: review decisions, cancellation, the copy-and-paste step and external output.
// The server decides what is possible; every action answers with the updated run.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost, runPath } from "./api.js";
import { Help, helpFor } from "./help.js";
import { waitingReasonLabel } from "./runs-layout.js";
import { errorText } from "./text.js";
import { ExternalOutput } from "./external-output.js";
import { ToolApprovalPreview } from "./thread-cards.js";

// Pasted answers survive a rejection or a re-render; memory only, keyed by run, step and attempt.
const drafts = new Map();

function Message({ message }) {
  if (!message) return null;
  const role = message.kind === "error" ? "alert" : "status";
  return html`<p class=${"message " + message.kind} role=${role}>${message.text}</p>`;
}

export function Confirm({ title, children, confirmLabel, keepLabel, danger, busy, onConfirm, onKeep }) {
  const confirmButton = useRef(null);
  useEffect(() => { if (confirmButton.current) confirmButton.current.focus(); }, []);
  return html`<div class="confirm" role="group" aria-label=${title}>
    <p><strong>${title}</strong></p>
    ${children}
    <div class="actions">
      <button type="button" ref=${confirmButton} data-help-exempt="confirm" class=${danger ? "btn danger" : "btn"} disabled=${busy}
        onClick=${onConfirm}>${confirmLabel}</button>
      <button type="button" data-help-exempt="confirm" class="btn secondary" disabled=${busy} onClick=${onKeep}>${keepLabel}</button>
    </div>
  </div>`;
}

export function HumanActions({ token, view, onUpdate, onStale, actionBar = false }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [feedback, setFeedback] = useState("");
  const [comments, setComments] = useState([]);
  const [feedbackInvalid, setFeedbackInvalid] = useState(false);
  const [reason, setReason] = useState("");
  const cardRef = useRef(null);

  function focusActions() {
    if (!cardRef.current) return;
    let reduced = false;
    try { reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (_) { reduced = false; }
    cardRef.current.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
    const target = cardRef.current.querySelector("button, textarea, input, select");
    if (target) target.focus({ preventScroll: true });
  }

  // A new run (or a new state of it) starts without the previous message or open confirmation.
  const stateKey = [view.id, view.status, view.waiting && view.waiting.reason, view.waiting && view.waiting.step_id,
    view.waiting && view.waiting.attempt, view.cancel_requested].join("|");
  useEffect(() => { setConfirm(null); setFeedbackInvalid(false); }, [stateKey]);
  useEffect(() => { setMessage(null); setFeedback(""); setComments([]); setReason(""); }, [view.id]);

  function updateComment(index, patch) {
    setComments(comments.map((item, i) => i === index ? { ...item, ...patch } : item));
  }

  async function act(path, body, okText) {
    if (busy) return false;
    setBusy(true);
    setMessage({ kind: "info", text: "Enviando…" });
    try {
      const updated = await apiPost(path, token, body);
      setConfirm(null);
      onUpdate(updated);
      setMessage({ kind: "ok", text: okText(updated) });
      return true;
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setMessage({ kind: "error", text: errorText(failure) });
      if (failure.status === 409 || failure.status === 404) onStale();
      return false;
    } finally {
      setBusy(false);
    }
  }

  const id = view.id;
  const waiting = view.waiting;
  const cancelBox = (running) => html`<${Confirm} title="¿Cancelar la ejecución?" confirmLabel="Sí, cancelar"
    keepLabel="Mantenerla" danger=${true} busy=${busy} onKeep=${() => setConfirm(null)}
    onConfirm=${() => act(runPath(id, "cancel"), {}, (v) => v.status === "cancelled"
      ? "La ejecución quedó cancelada." : "Se pidió cancelar; se detendrá cuando termine el paso en curso.")}>
    <p class="muted">${running
      ? "Está corriendo: se pedirá detenerla y quedará cancelada cuando el worker lo note."
      : "Dejará de avanzar y no se puede deshacer."}</p>
  <//>`;
  const cancelControl = !view.terminal && !view.cancel_requested
    ? (confirm === "cancel"
      ? cancelBox(view.status === "running")
      : html`<div class="action-item"><button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.cancel")}
          onClick=${() => setConfirm("cancel")}>Cancelar ejecución…</button>
        <${Help} topic="human.cancel" /></div>`)
    : null;
  // Rejecting a tool call fails the run without running the tool; rejecting a review ends it as rejected.
  const rejectControl = (toolCall) => (confirm === "reject"
    ? html`<${Confirm} title="¿Rechazar la ejecución?" confirmLabel="Sí, rechazar" keepLabel="Mantenerla" danger=${true}
        busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => act(runPath(id, "reject"), { feedback: reason.trim() }, () => toolCall
          ? "La llamada quedó rechazada y la ejecución terminó con error." : "La ejecución quedó rechazada.")}>
        <p class="muted">${toolCall
          ? "La herramienta no se ejecutará y la ejecución terminará con error; no se puede deshacer."
          : "La ejecución terminará como rechazada; no se puede deshacer."}</p>
        <label for="reject-reason">Motivo (opcional)</label>
        <${Help} topic="human.reject_reason" />
        <textarea id="reject-reason" rows="2" maxlength="4000" value=${reason} ...${helpFor("human.reject_reason")}
          onInput=${(event) => setReason(event.target.value)}></textarea>
      <//>`
    : html`<div class="action-item"><button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.reject")}
        onClick=${() => setConfirm("reject")}>Rechazar ejecución…</button>
      <${Help} topic="human.reject" /></div>`);

  let body;
  if (view.terminal) {
    body = html`<p class="muted">Esta ejecución terminó; no queda ninguna acción por hacer.</p>`;
  } else if (waiting && (waiting.reason === "review" || waiting.reason === "approval_pending" || waiting.reason === "budget_exhausted" || waiting.reason === "")) {
    const exhausted = waiting.reason === "budget_exhausted" || view.counters.budget_exhausted;
    body = html`<div>
      ${exhausted && html`<p class="notice">El presupuesto está agotado: puedes aprobar el resultado que ya existe, rechazarlo o cancelar. Pedir cambios o regenerar ya no es posible.</p>`}
      <p class="muted">Revisa el resultado del paso en «Resultados» y decide.</p>
      <div class="action-item">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("human.approve")} onClick=${() => act(runPath(id, "approve"), {},
          (v) => v.status === "completed" ? "Aprobado. La ejecución terminó." : "Aprobado. La ejecución continúa con el siguiente paso.")}>Aprobar</button>
        <${Help} topic="human.approve" />
      </div>
      ${!exhausted && html`<div class="action-item">
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.regenerate")}
          onClick=${() => act(runPath(id, "regenerate"), {}, () => "Se pidió repetir el paso sin comentarios.")}>Regenerar sin comentarios</button>
        <${Help} topic="human.regenerate" />
      </div>`}
      ${!exhausted && html`<form class="stack" noValidate onSubmit=${(event) => {
        event.preventDefault();
        if (!feedback.trim() && !comments.length) { setFeedbackInvalid(true); setMessage({ kind: "error", text: "Escribe una indicación o añade un comentario antes de enviar." }); return; }
        if (comments.some((comment) => !comment.text.trim() || !comment.target.trim())) { setMessage({ kind: "error", text: "Completa la parte y el texto de cada comentario." }); return; }
        setFeedbackInvalid(false);
        act(runPath(id, "changes"), { feedback: feedback.trim(), comments: comments.map(({ target, kind, text, quote }) => ({ target: target.trim(), kind, text: text.trim(), quote: quote || null })) },
          () => "Se pidieron cambios; el paso se repetirá con tus comentarios.")
          .then((ok) => { if (ok) { setFeedback(""); setComments([]); } });
      }}>
        <label for="changes-feedback">Pedir cambios <span class="muted">(texto libre o comentarios)</span></label>
        <${Help} topic="human.changes" />
        <textarea id="changes-feedback" rows="3" maxlength="4000" value=${feedback} aria-invalid=${feedbackInvalid ? "true" : null}
          disabled=${busy} ...${helpFor("human.changes")} onInput=${(event) => setFeedback(event.target.value)}></textarea>
        ${comments.map((comment, index) => {
          const output = view.artifacts && view.artifacts.length ? view.artifacts[view.artifacts.length - 1].data : null;
          const keys = output && typeof output === "object" && !Array.isArray(output) ? Object.keys(output) : [];
          const choices = ["general", ...keys];
          return html`<fieldset key=${index} class="stack"><legend>Comentario ${index + 1}</legend>
            <label>Parte</label><${Help} topic="human.comment_target" />
            <select aria-label=${"Parte del comentario " + (index + 1)} value=${choices.includes(comment.target) ? comment.target : "_manual"} disabled=${busy} ...${helpFor("human.comment_target")}
              onChange=${(event) => updateComment(index, { target: event.target.value === "_manual" ? "" : event.target.value })}>
              <option value="general">General</option>${keys.map((key) => html`<option value=${key}>${key}</option>`)}<option value="_manual">Escribir ruta…</option>
            </select>
            ${!choices.includes(comment.target) && html`<input aria-label="Ruta de la parte" maxlength="200" value=${comment.target} disabled=${busy}
              ...${helpFor("human.comment_target")} onInput=${(event) => updateComment(index, { target: event.target.value })} />`}
            <label>Tipo</label><${Help} topic="human.comment_kind" />
            <select aria-label=${"Tipo del comentario " + (index + 1)} value=${comment.kind} disabled=${busy} ...${helpFor("human.comment_kind")}
              onChange=${(event) => updateComment(index, { kind: event.target.value })}>
              <option value="fix">Corregir</option><option value="add">Añadir</option><option value="remove">Quitar</option>
              <option value="rewrite">Reescribir</option><option value="other">Otro</option>
            </select>
            <label>Texto</label><${Help} topic="human.comment_text" />
            <textarea aria-label=${"Texto del comentario " + (index + 1)} rows="2" maxlength="2000" value=${comment.text} disabled=${busy} ...${helpFor("human.comment_text")}
              onInput=${(event) => updateComment(index, { text: event.target.value })}></textarea>
            <label>Cita de la salida (opcional)</label><${Help} topic="human.comment_quote" />
            <textarea aria-label=${"Cita del comentario " + (index + 1)} rows="2" maxlength="2000" value=${comment.quote} disabled=${busy} ...${helpFor("human.comment_quote")}
              onInput=${(event) => updateComment(index, { quote: event.target.value })}></textarea>
            <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("human.comment_remove")}
              onClick=${() => setComments(comments.filter((_, i) => i !== index))}>Quitar comentario</button><${Help} topic="human.comment_remove" />
          </fieldset>`;
        })}
        <div class="action-item"><button type="button" class="btn secondary" disabled=${busy || comments.length >= 20}
          ...${helpFor("human.comment_add")} onClick=${() => setComments([...comments, { target: "general", kind: "fix", text: "", quote: "" }])}>Añadir comentario</button>
          <${Help} topic="human.comment_add" /></div>
        <div class="actions"><button type="submit" class="btn secondary" disabled=${busy} ...${helpFor("human.changes")}>Pedir cambios</button></div>
      </form>`}
      <div class="action-list">
        ${rejectControl(false)}
        ${cancelControl}
      </div>
    </div>`;
  } else if (waiting && waiting.reason === "tool_approval") {
    const call = view.tool_approval;
    body = call
      ? html`<div>
          <p class="muted">El paso «${call.step_id}» quiere usar la herramienta «${call.tool_id}». Revisa qué hará y decide.</p>
          ${call.tool_id === "file.write" && html`<${ToolApprovalPreview} runId=${id} token=${token}
            permissionRequestId=${call.permission_request_id} />`}
          <dl class="tool-approval">
            <dt>Qué hará</dt><dd>${call.summary || "(sin resumen)"}</dd>
            <dt>Efectos</dt><dd>${call.impacts.length ? call.impacts.join(", ") : "ninguno declarado"}</dd>
            <dt>Acciones</dt><dd>${call.actions.length
              ? html`<ul>${call.actions.map((item, index) => html`<li key=${index}>${item.kind}${item.target ? ": " + item.target : ""}</li>`)}</ul>`
              : "ninguna declarada"}${call.actions_omitted && html` <span class="muted">${call.actions_omitted}</span>`}</dd>
          </dl>
          <div class="action-item">
            <button type="button" class="btn" disabled=${busy} ...${helpFor("human.tool_approve")}
              onClick=${() => act(runPath(id, "approve"), { permission_request_id: call.permission_request_id },
                (v) => v.status === "completed" ? "Llamada aprobada. La ejecución terminó." : "Llamada aprobada. La ejecución continúa.")}>Aprobar esta llamada</button>
            <${Help} topic="human.tool_approve" />
          </div>
          <div class="action-list">
            ${rejectControl(true)}
            ${cancelControl}
          </div>
        </div>`
      : html`<div><p class="muted">La ejecución espera la aprobación de una herramienta, pero no se encontró la petición. Recarga la vista o cancela.</p>
          <div class="action-list">${cancelControl}</div></div>`;
  } else if (waiting && view.pending_human) {
    body = html`<div>
      <${HumanStep} token=${token} view=${view} busy=${busy} act=${act} setMessage=${setMessage} onStale=${onStale} />
      <div class="action-list">${cancelControl}</div>
    </div>`;
  } else if (waiting) {
    body = html`<div><p class="muted">Esta ejecución espera una acción humana que esta pantalla no sabe mostrar (${waiting.reason}).</p>
      <div class="action-list">${cancelControl}</div></div>`;
  } else {
    body = html`<div>
      <p class="muted">${view.cancel_requested
        ? "Se pidió cancelar; se detendrá cuando termine el paso en curso."
        : "No hay ninguna acción humana pendiente. Esta vista se actualiza sola cuando la ejecución cambia."}</p>
      <div class="action-list">${cancelControl}</div>
    </div>`;
  }

  const isWaiting = Boolean(waiting && !view.terminal);
  const waitLabel = isWaiting ? waitingReasonLabel(waiting) : "";
  const isReviewLike = isWaiting && (waiting.reason === "review" || waiting.reason === "approval_pending" || waiting.reason === "budget_exhausted" || waiting.reason === "");
  const isToolApproval = isWaiting && waiting.reason === "tool_approval" && Boolean(view.tool_approval);

  return html`<section ref=${cardRef} class=${"panel" + (isWaiting ? " human-actions-waiting" : "")} aria-labelledby="human-actions-title">
    <h4 id="human-actions-title">Acciones humanas</h4>
    <${Help} topic="human.section" />
    ${isWaiting && html`<p class="human-waiting-reason"><strong>Motivo de la espera:</strong> ${waitLabel}</p>`}
    ${body}
    ${waiting && ["review", "execute_step", "research"].includes(waiting.reason) && !view.cancel_requested && html`<${ExternalOutput}
      key=${view.id} token=${token} view=${view} onUpdate=${onUpdate} onStale=${onStale} />`}
    <${Message} message=${message} />
    ${isWaiting && actionBar && html`<div class="human-action-bar" role="region" aria-label="Acción pendiente">
      <p class="human-bar-reason"><strong>${waitLabel}</strong></p>
      <div class="human-bar-actions">
        ${isReviewLike && html`<button type="button" class="btn" disabled=${busy} ...${helpFor("human.approve")}
          onClick=${() => act(runPath(id, "approve"), {},
            (v) => v.status === "completed" ? "Aprobado. La ejecución terminó." : "Aprobado. La ejecución continúa con el siguiente paso.")}>Aprobar</button>`}
        ${isToolApproval && html`<button type="button" class="btn" disabled=${busy} ...${helpFor("human.tool_approve")}
          onClick=${() => act(runPath(id, "approve"), { permission_request_id: view.tool_approval.permission_request_id },
            (v) => v.status === "completed" ? "Llamada aprobada. La ejecución terminó." : "Llamada aprobada. La ejecución continúa.")}>Aprobar llamada</button>`}
        <button type="button" class="btn secondary" ...${helpFor("human.bar_focus")} onClick=${focusActions}>
          ${isReviewLike || isToolApproval ? "Más acciones" : "Ir a la acción"}
        </button>
        <${Help} topic="human.bar_focus" compact=${true} />
      </div>
    </div>`}
  </section>`;
}

function HumanStep({ token, view, busy, act, setMessage, onStale }) {
  const pendingKey = view.pending_human.step_id + "|" + view.pending_human.attempt;
  const draftKey = view.id + "|" + pendingKey;
  const [pending, setPending] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [draft, setDraft] = useState(() => drafts.get(draftKey) || { response: "", provider: "human", model: "", prompt_used: "", notes: "" });
  const [invalid, setInvalid] = useState(false);
  const [check, setCheck] = useState(null);
  const promptBox = useRef(null);

  useEffect(() => {
    let current = true;
    setPending(null);
    setLoadError(null);
    setDraft(drafts.get(draftKey) || { response: "", provider: "human", model: "", prompt_used: "", notes: "" });
    setCheck(null);
    apiGet(runPath(view.id, "human-next"), token).then((result) => {
      if (current) setPending(result);
    }).catch((error) => {
      if (!current) return;
      setLoadError(error);
      if (error.status === 409) onStale();
    });
    return () => { current = false; };
  }, [draftKey, token]);

  function update(patch) {
    const next = { ...draft, ...patch };
    drafts.set(draftKey, next);
    setDraft(next);
    if ("response" in patch) setCheck(null);
  }

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(pending.prompt);
      setMessage({ kind: "ok", text: "Prompt copiado al portapapeles." });
    } catch (_) {
      if (promptBox.current) { promptBox.current.focus(); promptBox.current.select(); }
      setMessage({ kind: "info", text: "El navegador bloqueó copiar; el prompt quedó seleccionado, pulsa Ctrl+C." });
    }
  }

  if (loadError) {
    return html`<p class="message error" role="alert">No se pudo cargar el paso pendiente: ${loadError.message}</p>`;
  }
  if (!pending) return html`<p class="muted" aria-busy="true">Cargando el paso pendiente…</p>`;

  const submit = async (event) => {
    event.preventDefault();
    if (!draft.response.trim()) {
      setInvalid(true);
      setMessage({ kind: "error", text: "Pega la respuesta completa antes de enviarla." });
      return;
    }
    setInvalid(false);
    const ok = await act(runPath(view.id, "human-submit"), {
      raw_response: draft.response, provider: draft.provider.trim() || "human", model: draft.model.trim(),
      prompt_used: draft.prompt_used, notes: draft.notes.trim(),
    }, (v) => v.status === "waiting_human" ? "Respuesta aceptada. Ahora toca revisar el resultado." : "Respuesta aceptada. La ejecución continúa.");
    if (ok) drafts.delete(draftKey);
  };

  const validate = async () => {
    if (!draft.response.trim()) {
      setInvalid(true);
      setMessage({ kind: "error", text: "Pega la respuesta completa antes de validarla." });
      return;
    }
    setInvalid(false);
    try {
      const result = await apiPost(runPath(view.id, "human-check"), token, { raw_response: draft.response });
      setCheck(result);
    } catch (error) {
      setMessage({ kind: "error", text: errorText(error) });
      if (error.status === 409) onStale();
    }
  };

  return html`<div class="stack">
    <p>El paso «${pending.step_id}» (intento ${pending.attempt}) hay que hacerlo fuera del framework. La respuesta debe cumplir
      el contrato <code>${pending.expected_contract}</code>.</p>
    <label for="human-prompt">Prompt a copiar <span class="muted">(${pending.prompt_id} v${pending.prompt_version})</span></label>
    <${Help} topic="human.prompt" />
    <textarea id="human-prompt" ref=${promptBox} rows="10" readOnly value=${pending.prompt} ...${helpFor("human.prompt")}></textarea>
    <div class="actions"><button type="button" class="btn secondary" ...${helpFor("human.copy_prompt")} onClick=${copyPrompt}>Copiar prompt</button>
      <${Help} topic="human.copy_prompt" compact=${true} /></div>
    <form class="stack" noValidate onSubmit=${submit}>
      <label for="human-response">Respuesta pegada <span class="req">(obligatoria)</span></label>
      <${Help} topic="human.response" />
      <textarea id="human-response" rows="10" value=${draft.response} disabled=${busy}
        aria-invalid=${invalid ? "true" : null} ...${helpFor("human.response")}
        onInput=${(event) => update({ response: event.target.value })}></textarea>
      <div class="two">
        <div><label for="human-provider">Proveedor</label>
          <${Help} topic="human.provider" />
          <input id="human-provider" maxlength="120" autocomplete="off" value=${draft.provider} disabled=${busy}
            ...${helpFor("human.provider")} onInput=${(event) => update({ provider: event.target.value })} /></div>
        <div><label for="human-model">Modelo <span class="muted">(opcional)</span></label>
          <${Help} topic="human.model" />
          <input id="human-model" maxlength="200" autocomplete="off" value=${draft.model} disabled=${busy}
            ...${helpFor("human.model")} onInput=${(event) => update({ model: event.target.value })} /></div>
      </div>
      <label for="human-prompt-used">Prompt usado <span class="muted">(opcional)</span></label>
      <${Help} topic="human.prompt_used" />
      <textarea id="human-prompt-used" rows="3" maxlength="50000" value=${draft.prompt_used} disabled=${busy}
        ...${helpFor("human.prompt_used")} onInput=${(event) => update({ prompt_used: event.target.value })}></textarea>
      <label for="human-notes">Notas <span class="muted">(opcional)</span></label>
      <${Help} topic="human.notes" />
      <textarea id="human-notes" rows="3" maxlength="4000" value=${draft.notes} disabled=${busy}
        ...${helpFor("human.notes")} onInput=${(event) => update({ notes: event.target.value })}></textarea>
      <div class="action-item"><button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("human.check")} onClick=${validate}>Validar sin enviar</button>
        <${Help} topic="human.check" /></div>
      ${check && html`<div role="status" aria-live="polite">
        ${check.ok ? "La respuesta cumple el contrato." : html`<ul>${check.problems.map((problem) => html`<li><code>${problem.path}</code>: ${problem.message}</li>`)}</ul>`}
      </div>`}
      <div class="action-item"><button type="submit" class="btn" disabled=${busy} ...${helpFor("human.submit")}>Enviar respuesta</button>
        <${Help} topic="human.submit" /></div>
    </form>
  </div>`;
}
