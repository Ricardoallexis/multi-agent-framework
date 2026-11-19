// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { html, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { errorText } from "./text.js";
import { decisionActions, diffLines, cardRoles, modeText } from "./team-card-model.js";

// CHT-T05: the card of a team version. The same card is shown in the chat (#new/team/<id>) and in Equipos.
// Everything comes from the server as text; the decision goes through the teams routes, which decide who signs and where.
export function TeamCardBody({ card }) {
  const roles = cardRoles(card);
  return html`<section class="team-card" aria-label="Puestos del equipo">
    <h4>Modo del equipo</h4><${Help} topic="team_card.mode" compact=${true} />
    <p><strong>${modeText(card && card.mode)}</strong></p>
    <h4>Puestos, reglas, permisos y modelo</h4><${Help} topic="team_card.section" compact=${true} />
    ${card && card.model_error && html`<p class="notice" role="status">${card.model_error}</p>`}
    ${roles.length ? html`<ul class="team-card-roles">${roles.map((role) => html`<li key=${role.id}>
      <strong>${role.id}</strong>${role.purpose && html` <span class="muted">${role.purpose}</span>`}
      <dl class="thread-fields">${role.rows.map((row) => html`<dt key=${row.key + "-label"}>${row.label}</dt>
        <dd key=${row.key}>${row.text}</dd>`)}</dl>
      ${role.notes.map((note, index) => html`<p key=${index} class="message info" role="status">${note}</p>`)}
    </li>`)}</ul>` : html`<p class="muted">La versión no tiene puestos.</p>`}
  </section>`;
}

export function TeamDiffBody({ diff }) {
  const lines = diffLines(diff);
  return html`<section class="team-card-diff" aria-label="Cambios de la versión">
    <h4>Cambios</h4><${Help} topic="team_card.diff" compact=${true} />
    <p class="muted small">${lines.base}</p>
    ${lines.empty && html`<p>Sin cambios respecto de la versión de comparación.</p>`}
    ${lines.header.length > 0 && html`<p><strong>Datos del equipo:</strong> ${lines.header.join("; ")}.</p>`}
    ${lines.sections.map((part) => html`<div key=${part.section}><strong>${part.label}</strong><ul>
      ${part.buckets.map((bucket) => html`<li key=${bucket.bucket}>${bucket.label}: ${bucket.lines.join(", ")}.</li>`)}</ul></div>`)}
  </section>`;
}

export function TeamDecision({ entry, token, onDone }) {
  const actions = decisionActions(entry);
  const versionId = entry.version && entry.version.id;
  const [pending, setPending] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  if (!actions.length) return null;
  const send = async () => {
    if (busy) return;
    setBusy(true); setMessage(null);
    try {
      await apiPost("/gui/api/teams/versions/" + encodeURIComponent(versionId) + "/" + pending, token,
        note.trim() ? { note: note.trim() } : {});
      setPending(""); setNote("");
      setMessage({ kind: "ok", text: pending === "approve" ? "La propuesta quedó aprobada." : "La propuesta quedó rechazada." });
    } catch (failure) {
      setPending("");
      setMessage({ kind: "error", text: errorText(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) })) });
    } finally { setBusy(false); onDone(); }
  };
  return html`<div class="thread-decision">
    ${!pending && html`<div class="thread-actions">
      ${actions.includes("approve") && html`<button type="button" class="btn" disabled=${busy}
        ...${helpFor("team_card.approve")} onClick=${() => setPending("approve")}>Aprobar</button>`}
      ${actions.includes("reject") && html`<button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("team_card.reject")} onClick=${() => setPending("reject")}>Rechazar</button>`}
      <${Help} topic="team_card.approve" compact=${true} /><${Help} topic="team_card.reject" compact=${true} />
    </div>`}
    ${pending && html`<div class="thread-confirm" role="group" aria-label="Confirmar decisión">
      <p><strong>¿${pending === "approve" ? "Aprobar" : "Rechazar"} esta versión del equipo?</strong></p>
      <label>Nota (opcional)
        <textarea rows="3" maxlength="4000" value=${note} disabled=${busy} ...${helpFor("team_card.note")}
          onInput=${(event) => setNote(event.target.value)}></textarea></label>
      <${Help} topic="team_card.note" compact=${true} />
      <div class="thread-actions"><button type="button" class=${pending === "reject" ? "btn danger" : "btn"} disabled=${busy}
        ...${helpFor("team_card.confirm")} onClick=${send}>Sí, ${pending === "approve" ? "aprobar" : "rechazar"}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("team_card.confirm")}
          onClick=${() => setPending("")}>Volver</button><${Help} topic="team_card.confirm" compact=${true} /></div>
    </div>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </div>`;
}

export function TeamVersionCard({ entry, token, onDone }) {
  const version = entry.version || {};
  return html`<li class="thread-card thread-waiting">
    <div class="thread-card-head"><strong>Versión de equipo para decidir</strong>
      <a href=${"#teams/" + encodeURIComponent(version.bundle || "")} ...${helpFor("team_card.open")}>Ver en Equipos</a>
      <${Help} topic="team_card.open" compact=${true} /></div>
    <p>${entry.text || ""} · ${version.bundle || ""} · estado ${version.status || "desconocido"}</p>
    <${TeamCardBody} card=${entry.card} />
    <${TeamDiffBody} diff=${entry.diff} />
    <${TeamDecision} entry=${entry} token=${token} onDone=${onDone} />
  </li>`;
}
