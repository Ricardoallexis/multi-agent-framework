// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Import, export, reimport and remove actions for the Offices view (VOF-T02).
import { html, useCallback, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { currentMode, onModeChange } from "../../js/gui-mode.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import {
  bytesToBase64,
  cardView,
  declarationName,
  declaredFunctionsText,
  errorText,
  exportFileName,
  exportWarningText,
  includeCodeVisible,
  importTitle,
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
    return t("ext.offices.error.reopen_for_approval");
  }
  if (reason === "user_click_required") return t("ext.offices.error.reopen_for_decision");
  if (error?.message === t("ext.offices.error.invalid_approval_code")) return error.message;
  return errorText(reason, error?.details);
}

async function decideApproval(token, approvalId, { decision = "approve", fingerprint = "", riskAck = false } = {}) {
  const challenge = await apiPost(challengePath(approvalId), token, {});
  if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
    throw new Error(t("ext.offices.error.invalid_approval_code"));
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
    throw new Error(t("ext.offices.error.missing_export_file"));
  }
  if (typeof document === "undefined" || !document.body || typeof URL.createObjectURL !== "function") {
    throw new Error(t("ext.offices.error.cannot_download"));
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

export function ImportOffice({
  token, into = null, reimport = null, onDone, initialCard = null, title = null, reviewChanges = false,
}) {
  const [mode, setMode] = useState(currentMode);
  const [upload, setUpload] = useState(null);
  const [folder, setFolder] = useState("");
  const [card, setCard] = useState(initialCard);
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
      if (reason === "office_stage_expired" || reason === "office_changed_since_card"
          || reason === "office_source_changed" || reason === "office_source_unavailable") setCard(null);
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
      setError(t("ext.offices.error.read_file"));
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
      if (failure?.details?.reason === "office_source_changed"
          || failure?.details?.reason === "office_source_unavailable") setCard(null);
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
        setMessage(t("ext.offices.scan.denied"));
      } else {
        setMessage(t("ext.offices.scan.allowed"));
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
  return html`<section class="offices-importer" aria-label=${t("ext.offices.import.title")}>
    <div class="offices-import-head">
      <p class="offices-import-title"><strong>${importTitle({ title, reimport, into })}</strong></p>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.import_office", { scope: helpScope })} onClick=${cancel}>${t("ext.offices.cancel")}</button>
      <${Help} topic="offices.import_office" compact=${true} scope=${helpScope} />
    </div>
    ${!card && html`<div class="offices-import-form">
      <label class="offices-import-field">${t("ext.offices.import.zip_file")}
        <input type="file" accept=".zip,application/zip" disabled=${busy}
          ...${helpFor("offices.import_office", { scope: helpScope })} onChange=${chooseZip} />
      </label>
      ${upload && html`<p class="offices-import-file">${upload.filename}</p>
        <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.import_office", { scope: helpScope })} onClick=${prepareZip}>
          ${busy ? t("ext.offices.import.preparing") : t("ext.offices.import.review")}
        </button>`}
      ${showIncludeCode && html`<label class="offices-import-field">${t("ext.offices.import.folder_path")}
        <input type="text" value=${folder} placeholder=${t("ext.offices.import.folder_placeholder")} disabled=${busy}
          ...${helpFor("offices.import_office", { scope: helpScope + "-folder" })} onInput=${(event) => setFolder(event.target.value)} />
      </label>
      <p class="muted small">${t("ext.offices.import.folder_note")}</p>
      <button type="button" class="btn secondary" disabled=${busy || !folder.trim()}
        ...${helpFor("offices.import_office", { scope: helpScope + "-folder" })} onClick=${prepareFolder}>${t("ext.offices.import.review_folder")}</button>
      <${Help} topic="offices.import_office" compact=${true} scope=${helpScope + "-folder"} />`}
    </div>`}
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${message && html`<p class="message small" role="status">${message}</p>`}
    ${view && html`<article class="offices-import-card" aria-label=${view.title}>
      <p class="offices-import-title"><strong>${view.title}</strong></p>
      <p class="offices-import-notice">${view.notice}</p>
      ${reviewChanges && html`<p><strong>${t("ext.offices.import.review_changes")}</strong></p>`}
      <p class="offices-import-meta">${view.level}</p>
      <p class="offices-import-teams">${t("ext.offices.teams_note")}</p>
      <${Help} topic="offices.card" compact=${true} scope=${helpScope} />
      ${view.fileChanges.length > 0 && html`<section class="offices-import-files" aria-label=${t("ext.offices.import.file_changes")}>
        <p><strong>${t("ext.offices.import.file_changes")}</strong></p>
        <ul>${view.fileChanges.map((file, index) => html`<li key=${file.area + "|" + file.path + "|" + index}>
          ${file.area && html`<strong>${file.area}: </strong>`}${file.path} — ${file.change}
        </li>`)}</ul>
      </section>`}
      <ul class="offices-import-areas">
        ${view.areas.map((area) => html`<li key=${area.id} class="offices-import-area">
          <div class="offices-import-row"><strong>${area.name}</strong>${area.change && html`<span>${area.change}</span>`}</div>
          <p>${t("ext.offices.import.members")}: ${area.members.length
            ? area.members.map((member) => [member.name, member.role].filter(Boolean).join(" · ")).join(", ")
            : t("ext.offices.import.no_members")}</p>
          <p>${area.requirement}</p>
          <p>${t("ext.offices.level.label")}: ${area.level}</p>
          <p>${t("ext.offices.import.workflows")}: ${declarationList(area.declares.workflows, advanced) || t("ext.offices.import.none_declared")}</p>
          <p>${t("ext.offices.import.contracts")}: ${declarationList(area.declares.contracts, advanced) || t("ext.offices.import.none_declared")}</p>
          <p>${t("ext.offices.import.team_roles")}: ${declarationList(area.declares.roles, advanced) || t("ext.offices.import.none_declared")}</p>
          ${area.workflowDeclarations.map((workflow) => html`<div key=${workflow.id}>
            ${workflow.roles.length
              ? html`<p>${t("ext.offices.import.workflow_requires_roles", {
                workflow: declarationList([workflow.id], advanced), roles: declarationList(workflow.roles, advanced),
              })}</p>`
              : html`<p>${t("ext.offices.import.workflow_no_roles", { workflow: declarationList([workflow.id], advanced) })}</p>`}
            ${workflow.fileTools.length > 0 && html`<p>${view.fileToolsConfigured
              ? t("ext.offices.import.workflow_uses_file_tools")
              : t("ext.offices.import.workflow_needs_file_tools")}</p>
              ${advanced && html`<p>${t("ext.offices.import.required_tools")}: ${workflow.fileTools.join(", ")}</p>`}`}
          </div>`)}
        </li>`)}
      </ul>
      <div class="offices-import-code">
        <p>${view.code.present ? t("ext.offices.import.code_present") : t("ext.offices.import.code_absent")}</p>
        ${advanced && view.code.files.length > 0 && html`<p>${t("ext.offices.import.code_files")}: ${view.code.files.join(", ")}</p>`}
        ${view.code.functions.length > 0 && html`<p>${declaredFunctionsText(declarationList(view.code.functions, advanced), advanced)}</p>`}
        ${view.code.functionsUnverified && html`<p>${t("ext.offices.import.functions_unverified")}</p>`}
        ${advanced && view.code.pythonContracts.length > 0 && html`<p>${t("ext.offices.import.python_contracts_unverified")}: ${view.code.pythonContracts.join(", ")}</p>`}
        ${view.code.pythonContractsUnverified && html`<p>${t("ext.offices.import.contracts_not_run")}</p>`}
        ${view.code.scanUnavailable && html`<p role="status">${t("ext.offices.import.scanner_unavailable")}</p>`}
        ${view.code.verdict && html`<p>${t("ext.offices.import.scan_result")}: ${view.code.verdict}</p>`}
        ${view.code.areas.map((item) => html`<p key=${item.id}>${item.name}: ${item.verdict}</p>`)}
      </div>
      ${view.needsScanDecision && html`<div class="offices-import-actions" role="group" aria-label=${t("ext.offices.import.allow_scan")}>
        <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.install", { scope: helpScope + "-scan" })}
          onClick=${() => scanDecision("approve")}>${t("ext.offices.import.allow_scan")}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.install", { scope: helpScope + "-scan" })}
          onClick=${() => scanDecision("reject")}>${t("ext.offices.privacy.deny")}</button>
        <${Help} topic="offices.install" compact=${true} scope=${helpScope + "-scan"} />
      </div>`}
      ${view.riskAckRequired && html`<label class="check-inline offices-risk-ack">
        <input type="checkbox" checked=${riskAck} disabled=${busy} ...${helpFor("offices.install", { scope: helpScope + "-risk" })}
          onChange=${(event) => setRiskAck(event.target.checked)} />
        ${t("ext.offices.import.risk_ack")}
      </label>
      <${Help} topic="offices.install" compact=${true} scope=${helpScope + "-risk"} />`}
      <div class="offices-import-actions">
        <button type="button" class="btn" disabled=${busy || scanRunning || view.needsScanDecision || (view.riskAckRequired && !riskAck)}
          ...${helpFor("offices.install", { scope: helpScope + "-install" })}
          onClick=${install}>${busy ? t("ext.offices.import.installing") : reimport
            ? t("ext.offices.update.button") : t("ext.offices.install.button")}</button>
        ${scanRunning && html`<button type="button" class="btn secondary" disabled=${busy}
          ...${helpFor("offices.card", { scope: helpScope + "-refresh" })} onClick=${() => refreshCard(card.stage_id)}>${t("ext.offices.import.refresh_card")}</button>
          <${Help} topic="offices.card" compact=${true} scope=${helpScope + "-refresh"} />`}
        <${Help} topic="offices.install" compact=${true} scope=${helpScope + "-install"} />
      </div>
      ${Array.isArray(card.warnings) && card.warnings.length > 0 && html`<ul class="offices-import-warnings">
        ${card.warnings.map((warning, index) => html`<li key=${index}>${exportWarningText(warning)}</li>`)}
      </ul>`}
    </article>`}
  </section>`;
}

export function OfficeSourceUpdate({ token, officeId, onDone }) {
  const [card, setCard] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const path = BASE + "/" + encodeURIComponent(officeId);

  async function prepareUpdate() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setCard(await apiPost(path + "/stage-source-update", token, {}));
    } catch (failure) {
      setError(failureText(failure));
    } finally {
      setBusy(false);
    }
  }

  async function finishUpdate() {
    setCard(null);
    try {
      onDone(await apiGet(path, token));
    } catch (failure) {
      setError(failureText(failure));
    }
  }

  return html`<div class="offices-source-update">
    ${!card && html`<button type="button" class="btn secondary small" disabled=${busy}
      ...${helpFor("offices.import_office", { scope: "source-update-" + officeId })}
      onClick=${prepareUpdate}>${t("ext.offices.update.button")}</button>
      <${Help} topic="offices.import_office" compact=${true} scope=${"source-update-" + officeId} />`}
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${card && html`<${ImportOffice} token=${token} reimport=${officeId} initialCard=${card}
      title=${t("ext.offices.update.button")} reviewChanges=${true} onDone=${finishUpdate} />`}
  </div>`;
}
