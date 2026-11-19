// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Cards of the run conversation. Values from the server are rendered as text by htm/Preact.
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "./help.js";
import { actionsFor, detailHref } from "./thread-model.js";

const LABELS = { launched: "Inicio", step: "Paso terminado", user_message: "Tu mensaje",
  message_consumed: "Mensaje leído", waiting: "Se necesita tu decisión", final: "Final" };
const KIND_LABELS = { fix: "Corregir", add: "Añadir", remove: "Quitar", rewrite: "Reescribir", other: "Otro" };
const REASONS = { review: "Revisión", tool_approval: "Aprobación de herramienta", execute_step: "Paso humano",
  research: "Investigación externa", escalation_approval: "Escalamiento", budget_exhausted: "Presupuesto agotado",
  content_review: "Revisión de contenido" };

function Decision({ entry, busy, onAction }) {
  const [pending, setPending] = useState("");
  const [feedback, setFeedback] = useState("");
  const [comments, setComments] = useState([]);
  const [error, setError] = useState("");
  const actions = actionsFor(entry, "waiting_human");
  if (!actions.length) return null;
  const tool = entry.decision.type === "tool_approval";
  const approveBody = tool ? { permission_request_id: entry.decision.permission_request_id } : {};
  const addComment = () => setComments([...comments, { target: "general", kind: "fix", text: "", quote: "" }]);
  const patchComment = (index, patch) => setComments(comments.map((item, i) => i === index ? { ...item, ...patch } : item));
  const send = () => {
    if (pending === "changes" && !feedback.trim() && !comments.length) {
      setError("Escribe una indicación o añade un comentario."); return;
    }
    if (pending === "changes" && comments.some((item) => !item.target.trim() || !item.text.trim())) {
      setError("Completa la parte y el texto de cada comentario."); return;
    }
    const body = pending === "approve" ? approveBody : pending === "reject" ? { feedback: feedback.trim() }
      : { feedback: feedback.trim(), comments: comments.map((item) => ({ ...item, target: item.target.trim(), text: item.text.trim(), quote: item.quote || null })) };
    onAction(pending, body);
  };
  return html`<div class="thread-decision">
    <p class="muted">${REASONS[entry.reason] || entry.reason || "Parada"} · paso ${entry.step_id || "sin indicar"}${entry.attempt ? " · intento " + entry.attempt : ""}</p>
    ${!pending && html`<div class="thread-actions">
      ${actions.includes("approve") && html`<button type="button" class="btn" disabled=${busy || (tool && !entry.decision.permission_request_id)}
        ...${helpFor("thread.approve")} onClick=${() => setPending("approve")}>Aprobar</button>`}
      ${actions.includes("changes") && html`<button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("thread.changes")} onClick=${() => setPending("changes")}>Pedir cambios</button>`}
      ${actions.includes("reject") && html`<button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("thread.reject")} onClick=${() => setPending("reject")}>Rechazar</button>`}
      <${Help} topic="thread.approve" compact=${true} /><${Help} topic="thread.changes" compact=${true} />
      <${Help} topic="thread.reject" compact=${true} />
    </div>`}
    ${pending && html`<div class="thread-confirm" role="group" aria-label="Confirmar decisión">
      <p><strong>¿${pending === "approve" ? "Aprobar" : pending === "changes" ? "Pedir cambios" : "Rechazar"} esta parada?</strong></p>
      ${(pending === "changes" || pending === "reject") && html`<label>Motivo ${pending === "reject" ? "(opcional)" : "o indicación"}
        <textarea rows="3" maxlength="4000" value=${feedback} disabled=${busy} ...${helpFor("thread.feedback")}
          onInput=${(event) => { setFeedback(event.target.value); setError(""); }}></textarea></label>
        <${Help} topic="thread.feedback" compact=${true} />`}
      ${pending === "changes" && html`<div class="thread-comments">
        ${comments.map((item, index) => html`<fieldset key=${index}><legend>Comentario ${index + 1}</legend>
          <label>Parte<input maxlength="200" value=${item.target} disabled=${busy} ...${helpFor("thread.comment")}
            onInput=${(event) => patchComment(index, { target: event.target.value })} /></label>
          <label>Tipo<select value=${item.kind} disabled=${busy} ...${helpFor("thread.comment")}
            onChange=${(event) => patchComment(index, { kind: event.target.value })}>
            ${Object.entries(KIND_LABELS).map(([kind, label]) => html`<option key=${kind} value=${kind}>${label}</option>`)}
          </select></label>
          <label>Texto<textarea rows="2" maxlength="2000" value=${item.text} disabled=${busy} ...${helpFor("thread.comment")}
            onInput=${(event) => patchComment(index, { text: event.target.value })}></textarea></label>
          <label>Cita de la salida (opcional)<textarea rows="2" maxlength="2000" value=${item.quote} disabled=${busy}
            ...${helpFor("thread.comment")} onInput=${(event) => patchComment(index, { quote: event.target.value })}></textarea></label>
          <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.comment")}
            onClick=${() => setComments(comments.filter((_, i) => i !== index))}>Quitar comentario</button>
        </fieldset>`)}
        <button type="button" class="btn secondary" disabled=${busy || comments.length >= 20} ...${helpFor("thread.comment")}
          onClick=${addComment}>Añadir comentario</button><${Help} topic="thread.comment" compact=${true} />
      </div>`}
      ${error && html`<p class="message error" role="alert">${error}</p>`}
      <div class="thread-actions"><button type="button" class=${pending === "reject" ? "btn danger" : "btn"} disabled=${busy}
        ...${helpFor("thread.confirm")} onClick=${send}>Sí, ${pending === "approve" ? "aprobar" : pending === "changes" ? "pedir cambios" : "rechazar"}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.confirm")}
          onClick=${() => { setPending(""); setError(""); }}>Volver</button><${Help} topic="thread.confirm" compact=${true} /></div>
    </div>`}
  </div>`;
}

export function ThreadCard({ entry, status, runId, busy, onAction }) {
  const details = detailHref(runId);
  const output = entry.kind === "final" ? entry.error || (entry.output == null ? "" :
    typeof entry.output === "string" ? entry.output : JSON.stringify(entry.output, null, 2)) : "";
  return html`<li class=${"thread-card thread-" + entry.kind}>
    <div class="thread-card-head"><strong>${LABELS[entry.kind] || "Evento"}</strong>
      <a href=${details} ...${helpFor("thread.detail")}>Ver detalle</a><${Help} topic="thread.detail" compact=${true} /></div>
    <p>${entry.text || ""}</p>
    ${entry.kind === "launched" && html`<p class="muted">${entry.workflow_id} · ${entry.project_name || "sin proyecto"}
      ${entry.execution_mode ? " · " + entry.execution_mode : ""}${entry.operating_mode ? " · " + entry.operating_mode : ""}</p>`}
    ${entry.kind === "launched" && entry.task_id && html`<p>Tarea ${entry.task_id}
      ${entry.task_href && html` · <a href=${entry.task_href} ...${helpFor("thread.related")}>Ver plan</a>`}</p>`}
    ${entry.kind === "launched" && entry.team && html`<p>Equipo ${entry.team.team_id || entry.team.bundle || "vinculado"}
      ${entry.team_href && html` · <a href=${entry.team_href} ...${helpFor("thread.related")}>Ver equipo</a>`}</p>`}
    ${entry.kind === "launched" && (entry.task_href || entry.team_href) && html`<${Help} topic="thread.related" compact=${true} />`}
    ${entry.kind === "step" && html`<p class="muted">${entry.step_id}${entry.executor ? " · " + (entry.executor.model || entry.executor.function || entry.executor.provider || entry.executor.kind || "") : ""}</p>`}
    ${entry.kind === "waiting" && status === "waiting_human" && entry.decision && html`<${Decision} entry=${entry} busy=${busy} onAction=${onAction} />`}
    ${output && html`<pre class="thread-output">${output}</pre>`}
  </li>`;
}
