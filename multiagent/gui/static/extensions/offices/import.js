// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Import, export, reimport and remove actions for the Offices view (VOF-T02).
import { html, useCallback, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { currentMode, onModeChange } from "../../js/gui-mode.js";
import { Help, helpFor } from "../../js/help.js";
import {
  bytesToBase64,
  cardView,
  declarationName,
  errorText,
  exportFileName,
  exportWarningText,
  includeCodeVisible,
  TEAMS_NOTE,
  ZIP_UPLOAD_MAX_BYTES,
  zipBody,
} from "./import-model.js";

const BASE = "/gui/api/offices";
const stagePath = (stageId) => BASE + "/stage/" + encodeURIComponent(stageId);
const challengePath = (approvalId) => "/gui/api/approvals/" + encodeURIComponent(approvalId) + "/challenge";
const decisionPath = (approvalId) => "/gui/api/approvals/" + encodeURIComponent(approvalId) + "/decision";
const declarationList = (values, advanced) => values.map((value) => advanced ? value : declarationName(value)).join(", ");

function failureText(error) {
  const reason = error?.details?.reason || error?.details?.cause
    || (error?.status === 413 ? "zip_too_large" : error?.code);
  if (reason === "approval_session_unavailable" || reason === "missing_or_invalid_session") {
    return "Vuelve a abrir la GUI para permitir una consulta o instalar una oficina.";
  }
  if (reason === "user_click_required") return "Vuelve a abrir la GUI para decidir esta acción.";
  if (error?.message === "El servidor no entregó un código de un solo uso válido.") return error.message;
  return errorText(reason, error?.details);
}

async function decideApproval(token, approvalId, { decision = "approve", fingerprint = "", riskAck = false } = {}) {
  const challenge = await apiPost(challengePath(approvalId), token, {});
  if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
    throw new Error("El servidor no entregó un código de un solo uso válido.");
  }
  if (fingerprint && challenge.sha256 && challenge.sha256 !== fingerprint) {
    throw new ApiError({ code: "office_changed_since_card", details: { reason: "office_changed_since_card" } });
  }
  const body = { code: challenge.code, decision };
  if (riskAck === true) body.risk_ack = true;
  return apiPost(decisionPath(approvalId), token, body);
}

function bytesFromBase64(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

export async function exportOffice({ token, officeId, area = null, includeCode = false, post = apiPost }) {
  const path = BASE + "/" + encodeURIComponent(officeId) + "/export";
  const payload = await post(path, token, { area: area || null, include_code: includeCode === true });
  if (!payload || typeof payload.zip_base64 !== "string") {
    throw new Error("El servidor no entregó el archivo de la oficina.");
  }
  if (typeof document === "undefined" || !document.body || typeof URL.createObjectURL !== "function") {
    throw new Error("El navegador no puede descargar el archivo de la oficina.");
  }
  const blob = new Blob([bytesFromBase64(payload.zip_base64)], { type: "application/zip" });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = exportFileName(payload);
  link.hidden = true;
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }
  return payload;
}

export function ImportOffice({ token, into = null, reimport = null, onDone }) {
  const [mode, setMode] = useState(currentMode);
  const [upload, setUpload] = useState(null);
  const [folder, setFolder] = useState("");
  const [card, setCard] = useState(null);
  const [busy, setBusy] = useState(false);
  const [riskAck, setRiskAck] = useState(false);
  const [message, setMessage] = useState(null);
  const [error, setError] = useState("");
  const helpScope = reimport ? "reimport-" + reimport : into ? "area-" + into : "new-office";

  useEffect(() => onModeChange(setMode), []);

  const refreshCard = useCallback(async (stageId) => {
    try {
      const updated = await apiGet(stagePath(stageId), token);
      setCard(updated);
      setError("");
      return updated;
    } catch (failure) {
      const reason = failure?.details?.reason;
      setError(failureText(failure));
      if (reason === "office_stage_expired" || reason === "office_changed_since_card") setCard(null);
      return null;
    }
  }, [token]);

  const chooseZip = async (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    setError("");
    setMessage(null);
    setUpload(null);
    if (!file) return;
    if (file.size > ZIP_UPLOAD_MAX_BYTES) {
      setError(errorText("zip_too_large"));
      return;
    }
    try {
      setUpload({ filename: file.name, zipBase64: bytesToBase64(new Uint8Array(await file.arrayBuffer())) });
    } catch (_) {
      setError("No se pudo leer el archivo. Vuelve a elegirlo.");
    }
  };

  const prepareZip = async () => {
    if (!upload || busy) return;
    setBusy(true);
    setError("");
    setMessage(null);
    try {
      const body = zipBody(upload.filename, upload.zipBase64, { into, reimport });
      setCard(await apiPost(BASE + "/stage-zip", token, body));
      setRiskAck(false);
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy(false);
    }
  };

  const prepareFolder = async () => {
    if (!folder.trim() || busy) return;
    setBusy(true);
    setError("");
    setMessage(null);
    try {
      const staged = await apiPost(BASE + "/stage-folder", token, {
        folder: folder.trim(), into: into || null, reimport: reimport || null,
      });
      setCard(staged);
      setRiskAck(false);
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy(false);
    }
  };

  const scanDecision = async (decision) => {
    if (!card?.scan?.approval_id || busy) return;
    setBusy(true);
    setError("");
    try {
      await decideApproval(token, card.scan.approval_id, { decision });
      if (decision === "reject") {
        await apiPost(stagePath(card.stage_id) + "/discard", token, {});
        setCard(null);
        setUpload(null);
        setMessage("No se permitió la consulta. La ficha se canceló.");
      } else {
        setMessage("Consulta permitida. Se está revisando la oficina.");
        await refreshCard(card.stage_id);
      }
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy(false);
    }
  };

  const install = async () => {
    if (!card || busy || (card.risk_ack_required && !riskAck)) return;
    setBusy(true);
    setError("");
    setMessage(null);
    try {
      await decideApproval(token, card.approval_id, {
        fingerprint: card.fingerprint,
        riskAck: card.risk_ack_required === true && riskAck === true,
      });
      await apiPost(stagePath(card.stage_id) + "/install", token, { approval_id: card.approval_id });
      onDone();
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (card?.stage_id) await apiPost(stagePath(card.stage_id) + "/discard", token, {});
      onDone();
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy(false);
    }
  };

  const view = card ? cardView(card) : null;
  const scanRunning = card?.scan?.state === "running";
  const advanced = mode === "advanced";
  const showIncludeCode = includeCodeVisible(mode);
  return html`<section class="offices-importer" aria-label="Importar una oficina">
    <div class="offices-import-head">
      <p class="offices-import-title"><strong>${reimport ? "Reimportar oficina" : into ? "Añadir un área" : "Importar una oficina"}</strong></p>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.import_office", { scope: helpScope })} onClick=${cancel}>Cancelar</button>
      <${Help} topic="offices.import_office" compact=${true} scope=${helpScope} />
    </div>
    ${!card && html`<div class="offices-import-form">
      <label class="offices-import-field">Archivo .zip
        <input type="file" accept=".zip,application/zip" disabled=${busy}
          ...${helpFor("offices.import_office", { scope: helpScope })} onChange=${chooseZip} />
      </label>
      ${upload && html`<p class="offices-import-file">${upload.filename}</p>
        <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.import_office", { scope: helpScope })} onClick=${prepareZip}>
          ${busy ? "Preparando ficha…" : "Revisar ficha"}
        </button>`}
      ${showIncludeCode && html`<label class="offices-import-field">Carpeta de la oficina
        <input type="text" value=${folder} placeholder="Carpeta de la oficina" disabled=${busy}
          ...${helpFor("offices.import_office", { scope: helpScope + "-folder" })} onInput=${(event) => setFolder(event.target.value)} />
      </label>
      <button type="button" class="btn secondary" disabled=${busy || !folder.trim()}
        ...${helpFor("offices.import_office", { scope: helpScope + "-folder" })} onClick=${prepareFolder}>Revisar carpeta</button>
      <${Help} topic="offices.import_office" compact=${true} scope=${helpScope + "-folder"} />`}
    </div>`}
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${message && html`<p class="message small" role="status">${message}</p>`}
    ${view && html`<article class="offices-import-card" aria-label=${view.title}>
      <p class="offices-import-title"><strong>${view.title}</strong></p>
      <p class="offices-import-notice">${view.notice}</p>
      <p class="offices-import-meta">${view.level}</p>
      <p class="offices-import-teams">${TEAMS_NOTE}</p>
      <${Help} topic="offices.card" compact=${true} scope=${helpScope} />
      <ul class="offices-import-areas">
        ${view.areas.map((area) => html`<li key=${area.id} class="offices-import-area">
          <div class="offices-import-row"><strong>${area.name}</strong>${area.change && html`<span>${area.change}</span>`}</div>
          <p>Miembros: ${area.members.length
            ? area.members.map((member) => [member.name, member.role].filter(Boolean).join(" · ")).join(", ")
            : "No se declararon miembros"}</p>
          <p>${area.requirement}</p>
          <p>Nivel: ${area.level}</p>
          <p>Lo que sabe hacer: ${declarationList(area.declares.workflows, advanced) || "No declarado"}</p>
          <p>Contratos: ${declarationList(area.declares.contracts, advanced) || "No declarados"}</p>
          <p>Roles: ${declarationList(area.declares.roles, advanced) || "No declarados"}</p>
        </li>`)}
      </ul>
      <div class="offices-import-code">
        <p>${view.code.present ? "La oficina trae código propio." : "La oficina trae solo instrucciones y datos."}</p>
        ${advanced && view.code.files.length > 0 && html`<p>Archivos de código: ${view.code.files.join(", ")}</p>`}
        ${advanced && view.code.functions.length > 0 && html`<p>Funciones declaradas: ${view.code.functions.join(", ")}</p>`}
        ${view.code.verdict && html`<p>Resultado de la revisión: ${view.code.verdict}</p>`}
        ${view.code.areas.map((item) => html`<p key=${item.id}>${item.name}: ${item.verdict}</p>`)}
      </div>
      ${view.needsScanDecision && html`<div class="offices-import-actions" role="group" aria-label="Permitir la consulta">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.install", { scope: helpScope + "-scan" })}
          onClick=${() => scanDecision("approve")}>Permitir la consulta</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.install", { scope: helpScope + "-scan" })}
          onClick=${() => scanDecision("reject")}>No permitir</button>
        <${Help} topic="offices.install" compact=${true} scope=${helpScope + "-scan"} />
      </div>`}
      ${view.riskAckRequired && html`<label class="check-inline offices-risk-ack">
        <input type="checkbox" checked=${riskAck} disabled=${busy} ...${helpFor("offices.install", { scope: helpScope + "-risk" })}
          onChange=${(event) => setRiskAck(event.target.checked)} />
        Asumo los riesgos
      </label>
      <${Help} topic="offices.install" compact=${true} scope=${helpScope + "-risk"} />`}
      <div class="offices-import-actions">
        <button type="button" class="btn" disabled=${busy || scanRunning || view.needsScanDecision || (view.riskAckRequired && !riskAck)}
          ...${helpFor("offices.install", { scope: helpScope + "-install" })} onClick=${install}>${busy ? "Instalando…" : "Instalar oficina"}</button>
        ${scanRunning && html`<button type="button" class="btn secondary" disabled=${busy}
          ...${helpFor("offices.card", { scope: helpScope + "-refresh" })} onClick=${() => refreshCard(card.stage_id)}>Actualizar ficha</button>
          <${Help} topic="offices.card" compact=${true} scope=${helpScope + "-refresh"} />`}
        <${Help} topic="offices.install" compact=${true} scope=${helpScope + "-install"} />
      </div>
      ${Array.isArray(card.warnings) && card.warnings.length > 0 && html`<ul class="offices-import-warnings">
        ${card.warnings.map((warning, index) => html`<li key=${index}>${exportWarningText(warning)}</li>`)}
      </ul>`}
    </article>`}
  </section>`;
}
