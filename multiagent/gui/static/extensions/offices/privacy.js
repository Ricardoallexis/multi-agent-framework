// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { LEVEL_OPTIONS, NOTE_TITLE, isLowering, levelBody, privacyRows } from "./privacy-model.js";

export { askLevelOnce } from "../../js/office-level-question.js";

const BASE = "/gui/api/offices";
const officePath = (officeId) => BASE + "/" + encodeURIComponent(officeId);
const privacyPath = (officeId) => officePath(officeId) + "/privacy";
const approvalPath = (approvalId, action) => "/gui/api/approvals/" + encodeURIComponent(approvalId) + "/" + action;
const levelText = (level) => LEVEL_OPTIONS.find((option) => option.value === level)?.label || "Sin nivel";
const displayActor = (value, fallback = "Sin fijar") => String(value || "").startsWith("user:") ? "Tú" : value || fallback;
const displayDate = (value) => value ? new Date(value).toLocaleString() : "Sin fecha";

async function decideApproval(token, approvalId, sha256, decision) {
  const challenge = await apiPost(approvalPath(approvalId, "challenge"), token, {});
  if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
    throw new Error("El servidor no entregó un código de un solo uso válido.");
  }
  if (sha256 && challenge.sha256 && challenge.sha256 !== sha256) {
    throw new ApiError({ code: "office_changed_since_card", details: { reason: "office_changed_since_card" } });
  }
  return apiPost(approvalPath(approvalId, "decision"), token, { code: challenge.code, decision });
}

export function PrivacyPanel({ token, officeId, areas = [], onClose }) {
  const [payload, setPayload] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [pendingLower, setPendingLower] = useState(null);
  const [pendingCloud, setPendingCloud] = useState(null);

  useEffect(() => {
    let active = true;
    setPayload(null);
    setError("");
    apiGet(privacyPath(officeId), token).then(
      (value) => { if (active) setPayload(value); },
      (failure) => { if (active) setError(failure?.message || "No se pudo leer la privacidad de la oficina."); },
    );
    return () => { active = false; };
  }, [token, officeId]);

  const rows = privacyRows(payload, areas);

  async function setHigherLevel(body) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      setPayload(await apiPost(privacyPath(officeId) + "/raise", token, body));
      setMessage("La privacidad se actualizó.");
    } catch (failure) {
      setError(failure?.message || "No se pudo actualizar la privacidad.");
    } finally {
      setBusy(false);
    }
  }

  async function changeAreaLevel(row, chosen) {
    if (busy || row.level === chosen) return;
    const body = levelBody(row.id, chosen);
    if (isLowering(row.level, chosen)) {
      setBusy(true);
      setError("");
      setMessage("");
      try {
        const answer = await apiPost(privacyPath(officeId) + "/lower-request", token, body);
        setPendingLower({
          ...body, approvalId: answer.approval_id, sha256: answer.sha256,
          name: row.name, from: row.level, to: chosen,
        });
      } catch (failure) {
        setError(failure?.message || "No se pudo solicitar el cambio de privacidad.");
      } finally {
        setBusy(false);
      }
      return;
    }
    await setHigherLevel(body);
  }

  async function changeOfficeLocalOnly(value) {
    const chosen = value ? "local_only" : "normal";
    const current = payload?.local_only ? "local_only" : "normal";
    if (busy || current === chosen) return;
    const body = { area: null, sensitivity: null, local_only: value };
    if (isLowering(current, chosen)) {
      setBusy(true);
      setError("");
      setMessage("");
      try {
        const answer = await apiPost(privacyPath(officeId) + "/lower-request", token, body);
        setPendingLower({
          ...body, approvalId: answer.approval_id, sha256: answer.sha256,
          name: "Toda la oficina", from: current, to: chosen,
        });
      } catch (failure) {
        setError(failure?.message || "No se pudo solicitar el cambio de privacidad.");
      } finally {
        setBusy(false);
      }
      return;
    }
    await setHigherLevel(body);
  }

  async function confirmLower() {
    if (!pendingLower || busy) return;
    const pending = pendingLower;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await decideApproval(token, pending.approvalId, pending.sha256, "approve");
      setPendingLower(null);
      setPayload(await apiPost(privacyPath(officeId) + "/lower", token, {
        area: pending.area, approval_id: pending.approvalId,
      }));
      setMessage("La privacidad se actualizó.");
    } catch (failure) {
      setError(failure?.message || "No se pudo bajar el nivel de privacidad.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelLower() {
    if (!pendingLower || busy) return;
    setBusy(true);
    setError("");
    try {
      await decideApproval(token, pendingLower.approvalId, pendingLower.sha256, "reject");
      setPendingLower(null);
      setMessage("No se cambió la privacidad.");
    } catch (failure) {
      setError(failure?.message || "No se pudo cancelar el cambio de privacidad.");
    } finally {
      setBusy(false);
    }
  }

  async function requestCloud(area) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const answer = await apiPost(privacyPath(officeId) + "/cloud-request", token, { area, allowed: true });
      setPendingCloud({ area, approvalId: answer.approval_id, sha256: answer.sha256 });
    } catch (failure) {
      setError(failure?.message || "No se pudo solicitar el permiso de nube.");
    } finally {
      setBusy(false);
    }
  }

  async function grantCloud() {
    if (!pendingCloud || busy) return;
    const pending = pendingCloud;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await decideApproval(token, pending.approvalId, pending.sha256, "approve");
      setPendingCloud(null);
      setPayload(await apiPost(privacyPath(officeId) + "/cloud", token, {
        area: pending.area, approval_id: pending.approvalId,
      }));
      setMessage("El permiso de nube se actualizó.");
    } catch (failure) {
      setError(failure?.message || "No se pudo permitir el uso de modelos de la nube.");
    } finally {
      setBusy(false);
    }
  }

  async function cancelCloud() {
    if (!pendingCloud || busy) return;
    setBusy(true);
    setError("");
    try {
      await decideApproval(token, pendingCloud.approvalId, pendingCloud.sha256, "reject");
      setPendingCloud(null);
      setMessage("No se permitió el uso de modelos de la nube.");
    } catch (failure) {
      setError(failure?.message || "No se pudo cancelar el permiso de nube.");
    } finally {
      setBusy(false);
    }
  }

  async function revokeCloud(area) {
    await setHigherLevel({ area, sensitivity: null, local_only: null, cloud_allowed: false });
  }

  const pending = Boolean(pendingLower || pendingCloud);
  const scope = "privacy-" + officeId;
  return html`<section class="offices-privacy" aria-label=${"Privacidad de " + officeId}>
    <div class="offices-privacy-head">
      <p class="offices-privacy-title">Privacidad</p>
      <button type="button" class="btn secondary small" disabled=${busy || pending}
        ...${helpFor("offices.privacy", { scope })} onClick=${onClose}>Cerrar</button>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope} />
    </div>
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${message && html`<p class="message small" role="status">${message}</p>`}
    ${!payload && !error && html`<p class="muted small" role="status">Leyendo privacidad…</p>`}
    ${payload && html`<div class="offices-privacy-office">
      <label class="offices-privacy-toggle">
        <input type="checkbox" checked=${payload.local_only === true} disabled=${busy || pending}
          ...${helpFor("offices.privacy", { scope: scope + "-office-local" })}
          onChange=${(event) => changeOfficeLocalOnly(event.target.checked)} />
        Toda la oficina es Solo local
      </label>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-office-local"} />
      <p class="offices-meta">${displayActor(payload.cloud?.set_by,
        payload.cloud?.source === "default" ? "Sin fijar" : "La oficina")} · ${displayDate(payload.cloud?.set_at)}
        · ${payload.cloud?.source === "default" ? "por defecto" : "de la oficina"}</p>
      <p class="offices-meta">${payload.cloud?.allowed ? "Modelos de la nube permitidos en toda la oficina." : "Modelos de la nube no permitidos en toda la oficina."}</p>
      <button type="button" class="btn secondary small" disabled=${busy || pending}
        ...${helpFor("offices.privacy", { scope: scope + "-office-cloud" })}
        onClick=${() => payload.cloud?.allowed ? revokeCloud(null) : requestCloud(null)}>
        ${payload.cloud?.allowed ? "No permitir" : "Permitir modelos de la nube"}
      </button>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-office-cloud"} />
    </div>
    <div class="offices-privacy-areas">
      ${rows.map((row) => html`<article key=${row.id} class="offices-privacy-area">
        <p class="offices-privacy-area-title">${row.name}</p>
        <label class="offices-privacy-field">Nivel
          <select value=${row.level || ""} disabled=${busy || pending}
            ...${helpFor("offices.privacy", { scope: scope + "-" + row.id + "-level" })}
            onChange=${(event) => changeAreaLevel(row, event.target.value)}>
            <option value="" disabled>Sin nivel</option>
            ${LEVEL_OPTIONS.map((option) => html`<option value=${option.value}>${option.label}</option>`)}
          </select>
        </label>
        <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-" + row.id + "-level"} />
        <p class="offices-meta">${displayActor(row.setBy, row.sourceText === "sin fijar" ? "Sin fijar" : "La oficina")}
          · ${displayDate(row.setAt)} · ${row.sourceText}</p>
        <p class="offices-meta">${row.cloudEffective ? "Modelos de la nube permitidos " + row.cloudSourceText + "." : "Modelos de la nube no permitidos (" + row.cloudSourceText + ")."}</p>
        <p class="offices-meta">${displayActor(row.cloudSetBy, row.cloudSourceText === "por defecto" ? "Sin fijar" : "La oficina")}
          · ${displayDate(row.cloudSetAt)} · ${row.cloudSourceText}</p>
        <button type="button" class="btn secondary small" disabled=${busy || pending}
          ...${helpFor("offices.privacy", { scope: scope + "-" + row.id + "-cloud" })}
          onClick=${() => row.cloudEffective ? revokeCloud(row.id) : requestCloud(row.id)}>
          ${row.cloudEffective ? "No permitir" : "Permitir modelos de la nube"}
        </button>
        <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-" + row.id + "-cloud"} />
        ${row.dataClass === "sensitive" || row.dataClass === "restricted"
          ? html`<p class="offices-meta">Esta área nunca usa la nube, aunque se permita.</p>`
          : row.dataClass === null
            ? html`<p class="offices-meta">Esta área pregunta antes de usar la nube.</p>
              <${Help} topic="offices.level_question" compact=${true} scope=${scope + "-" + row.id + "-question"} />`
            : null}
      </article>`)}
    </div>
    ${pendingLower && html`<div class="offices-privacy-confirm" role="group" aria-label="Bajar el nivel">
      <p>Bajar el nivel de ${pendingLower.name} de ${levelText(pendingLower.from)} a ${levelText(pendingLower.to)}</p>
      <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.lower", { scope })}
        onClick=${confirmLower}>Bajar el nivel</button>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.lower", { scope })}
        onClick=${cancelLower}>Cancelar</button>
      <${Help} topic="offices.lower" compact=${true} scope=${scope} />
    </div>`}
    ${pendingCloud && html`<div class="offices-privacy-confirm" role="group" aria-label="Permitir modelos de la nube">
      <p>Permitir modelos de la nube${pendingCloud.area ? " para " + (rows.find((row) => row.id === pendingCloud.area)?.name || pendingCloud.area) : " en toda la oficina"}</p>
      <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.privacy", { scope: scope + "-cloud-confirm" })}
        onClick=${grantCloud}>Permitir modelos de la nube</button>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.privacy", { scope: scope + "-cloud-confirm" })}
        onClick=${cancelCloud}>Cancelar</button>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-cloud-confirm"} />
    </div>`}
    <div class="offices-privacy-note">
      <p class="offices-privacy-note-title">${NOTE_TITLE}</p>
      <${Help} topic="offices.privacy_note" compact=${true} scope=${scope + "-note"} />
      <ul>${(Array.isArray(payload?.note) ? payload.note : []).map((line, index) => html`<li key=${index}>${line}</li>`)}</ul>
    </div>`}
  </section>`;
}
