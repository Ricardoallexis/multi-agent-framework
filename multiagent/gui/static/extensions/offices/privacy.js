// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { LEVEL_OPTIONS, isLowering, levelBody, privacyRows } from "./privacy-model.js";

export { askLevelOnce } from "../../js/office-level-question.js";

const BASE = "/gui/api/offices";
const officePath = (officeId) => BASE + "/" + encodeURIComponent(officeId);
const privacyPath = (officeId) => officePath(officeId) + "/privacy";
const approvalPath = (approvalId, action) => "/gui/api/approvals/" + encodeURIComponent(approvalId) + "/" + action;
const levelText = (level) => LEVEL_OPTIONS.find((option) => option.value === level)?.label || t("ext.offices.level.none");
const displayActor = (value, fallbackKey = "ext.offices.privacy.unset") => String(value || "").startsWith("user:")
  ? t("ext.offices.privacy.you") : value || t(fallbackKey);
const displayDate = (value) => value ? new Date(value).toLocaleString() : t("ext.offices.privacy.no_date");

async function decideApproval(token, approvalId, sha256, decision) {
  const challenge = await apiPost(approvalPath(approvalId, "challenge"), token, {});
  if (!challenge || typeof challenge.code !== "string" || !challenge.code) {
    throw new Error(t("ext.offices.error.invalid_approval_code"));
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
      (failure) => { if (active) setError(failure?.message || t("ext.offices.error.read_privacy")); },
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
      setMessage(t("ext.offices.privacy.updated"));
    } catch (failure) {
      setError(failure?.message || t("ext.offices.error.update_privacy"));
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
        setError(failure?.message || t("ext.offices.error.request_privacy_change"));
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
          name: t("ext.offices.privacy.entire_office"), from: current, to: chosen,
        });
      } catch (failure) {
        setError(failure?.message || t("ext.offices.error.request_privacy_change"));
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
      setMessage(t("ext.offices.privacy.updated"));
    } catch (failure) {
      setError(failure?.message || t("ext.offices.error.lower_privacy"));
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
      setMessage(t("ext.offices.privacy.not_changed"));
    } catch (failure) {
      setError(failure?.message || t("ext.offices.error.cancel_privacy_change"));
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
      setError(failure?.message || t("ext.offices.error.request_cloud"));
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
      setMessage(t("ext.offices.privacy.cloud_updated"));
    } catch (failure) {
      setError(failure?.message || t("ext.offices.error.allow_cloud"));
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
      setMessage(t("ext.offices.privacy.cloud_not_allowed"));
    } catch (failure) {
      setError(failure?.message || t("ext.offices.error.cancel_cloud"));
    } finally {
      setBusy(false);
    }
  }

  async function revokeCloud(area) {
    await setHigherLevel({ area, sensitivity: null, local_only: null, cloud_allowed: false });
  }

  const pending = Boolean(pendingLower || pendingCloud);
  const scope = "privacy-" + officeId;
  return html`<section class="offices-privacy" aria-label=${t("ext.offices.privacy.aria_label", { office: officeId })}>
    <div class="offices-privacy-head">
      <p class="offices-privacy-title">${t("ext.offices.privacy.title")}</p>
      <button type="button" class="btn secondary small" disabled=${busy || pending}
        ...${helpFor("offices.privacy", { scope })} onClick=${onClose}>${t("ext.offices.privacy.close")}</button>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope} />
    </div>
    ${error && html`<p class="message error small" role="alert">${error}</p>`}
    ${message && html`<p class="message small" role="status">${message}</p>`}
    ${!payload && !error && html`<p class="muted small" role="status">${t("ext.offices.privacy.loading")}</p>`}
    ${payload && html`<div class="offices-privacy-office">
      <label class="offices-privacy-toggle">
        <input type="checkbox" checked=${payload.local_only === true} disabled=${busy || pending}
          ...${helpFor("offices.privacy", { scope: scope + "-office-local" })}
          onChange=${(event) => changeOfficeLocalOnly(event.target.checked)} />
        ${t("ext.offices.privacy.office_local_only")}
      </label>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-office-local"} />
      <p class="offices-meta">${displayActor(payload.cloud?.set_by,
        payload.cloud?.source === "default" ? "ext.offices.privacy.unset" : "ext.offices.privacy.office")} · ${displayDate(payload.cloud?.set_at)}
        · ${payload.cloud?.source === "default" ? t("office_level.source.default") : t("office_level.source.office")}</p>
      <p class="offices-meta">${payload.cloud?.allowed ? t("ext.offices.privacy.cloud_allowed_office") : t("ext.offices.privacy.cloud_denied_office")}</p>
      <button type="button" class="btn secondary small" disabled=${busy || pending}
        ...${helpFor("offices.privacy", { scope: scope + "-office-cloud" })}
        onClick=${() => payload.cloud?.allowed ? revokeCloud(null) : requestCloud(null)}>
        ${payload.cloud?.allowed ? t("ext.offices.privacy.deny") : t("ext.offices.privacy.allow_cloud")}
      </button>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-office-cloud"} />
    </div>
    <div class="offices-privacy-areas">
      ${rows.map((row) => html`<article key=${row.id} class="offices-privacy-area">
        <p class="offices-privacy-area-title">${row.name}</p>
        <label class="offices-privacy-field">${t("ext.offices.level.label")}
          <select value=${row.level || ""} disabled=${busy || pending}
            ...${helpFor("offices.privacy", { scope: scope + "-" + row.id + "-level" })}
            onChange=${(event) => changeAreaLevel(row, event.target.value)}>
            <option value="" disabled>${t("ext.offices.level.none")}</option>
            ${LEVEL_OPTIONS.map((option) => html`<option value=${option.value}>${option.label}</option>`)}
          </select>
        </label>
        <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-" + row.id + "-level"} />
        <p class="offices-meta">${displayActor(row.setBy, row.sourceText === t("office_level.source.unset")
          ? "ext.offices.privacy.unset" : "ext.offices.privacy.office")}
          · ${displayDate(row.setAt)} · ${row.sourceText}</p>
        <p class="offices-meta">${row.cloudEffective
          ? t("ext.offices.privacy.cloud_allowed_from", { source: row.cloudSourceText })
          : t("ext.offices.privacy.cloud_denied_from", { source: row.cloudSourceText })}</p>
        <p class="offices-meta">${displayActor(row.cloudSetBy, row.cloudSourceText === t("office_level.source.default")
          ? "ext.offices.privacy.unset" : "ext.offices.privacy.office")}
          · ${displayDate(row.cloudSetAt)} · ${row.cloudSourceText}</p>
        <button type="button" class="btn secondary small" disabled=${busy || pending}
          ...${helpFor("offices.privacy", { scope: scope + "-" + row.id + "-cloud" })}
          onClick=${() => row.cloudEffective ? revokeCloud(row.id) : requestCloud(row.id)}>
          ${row.cloudEffective ? t("ext.offices.privacy.deny") : t("ext.offices.privacy.allow_cloud")}
        </button>
        <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-" + row.id + "-cloud"} />
        ${row.dataClass === "sensitive" || row.dataClass === "restricted"
          ? html`<p class="offices-meta">${t("ext.offices.privacy.area_never_cloud")}</p>`
          : row.dataClass === null
            ? html`<p class="offices-meta">${t("ext.offices.privacy.area_asks_cloud")}</p>
              <${Help} topic="offices.level_question" compact=${true} scope=${scope + "-" + row.id + "-question"} />`
            : null}
      </article>`)}
    </div>
    ${pendingLower && html`<div class="offices-privacy-confirm" role="group" aria-label=${t("ext.offices.privacy.lower_level")}>
      <p>${t("ext.offices.privacy.lower_prompt", { name: pendingLower.name,
        from: levelText(pendingLower.from), to: levelText(pendingLower.to) })}</p>
      <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.lower", { scope })}
        onClick=${confirmLower}>${t("ext.offices.privacy.lower_level")}</button>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.lower", { scope })}
        onClick=${cancelLower}>${t("ext.offices.cancel")}</button>
      <${Help} topic="offices.lower" compact=${true} scope=${scope} />
    </div>`}
    ${pendingCloud && html`<div class="offices-privacy-confirm" role="group" aria-label=${t("ext.offices.privacy.allow_cloud")}>
      <p>${pendingCloud.area
        ? t("ext.offices.privacy.allow_cloud_area", { area: rows.find((row) => row.id === pendingCloud.area)?.name || pendingCloud.area })
        : t("ext.offices.privacy.allow_cloud_office")}</p>
      <button type="button" class="btn" disabled=${busy} ...${helpFor("offices.privacy", { scope: scope + "-cloud-confirm" })}
        onClick=${grantCloud}>${t("ext.offices.privacy.allow_cloud")}</button>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.privacy", { scope: scope + "-cloud-confirm" })}
        onClick=${cancelCloud}>${t("ext.offices.cancel")}</button>
      <${Help} topic="offices.privacy" compact=${true} scope=${scope + "-cloud-confirm"} />
    </div>`}
    <div class="offices-privacy-note">
      <p class="offices-privacy-note-title">${t("office_level.note_title")}</p>
      <${Help} topic="offices.privacy_note" compact=${true} scope=${scope + "-note"} />
      <ul>${(Array.isArray(payload?.note) ? payload.note : []).map((line, index) => html`<li key=${index}>${line}</li>`)}</ul>
    </div>`}
  </section>`;
}
