// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view-model of the Codex ACP card of «Apps y agentes» (ACP-T04): the five facts, the step that is missing, the install panel,
// the area consent and the start of a conversation. No DOM and no network. Everything from the server is text; the install
// command is shown to be copied and run by the person: nothing here (or anywhere in the product) runs it.
import "./i18n.js";
import { t } from "../../js/i18n.js";

export const ACP_ID = "codex_acp";
export const ACP_PROBE_PATH = `/gui/api/connections/${ACP_ID}/acp-probe`;
export const ACP_CONSENT_PATH = `/gui/api/connections/${ACP_ID}/consent`;
export const NEW_SESSION_HREF = "#new";   // «Elegir CLI» leads to «Nueva sesión»; nothing switches transport by itself

export const consentReadPath = (bundle) => `${ACP_CONSENT_PATH}?bundle=${encodeURIComponent(bundle)}`;

const yesNo = (value) => (value === true ? t("ext.apps.yes") : value === false ? t("ext.apps.no") : t("ext.apps.acp.no_data_yet"));

// The step that comes first: the connection row says it; a saved activation whose probe went stale says «probe» too.
export function acpStep(row, activation = null) {
  if (row && ["install", "probe", "enable"].includes(row.missing_step)) return row.missing_step;
  return activation && activation.probe_required === true ? "probe" : null;
}

// Each fact apart: installed, compatible, authenticated (null is «no data yet»), activated and the default transport (CLI).
// The version and the protocol show only when the server still holds them for the executable that is installed now.
export function acpFacts(row) {
  const facts = [
    { id: "installed", label: t("ext.apps.acp.field.installed"), value: yesNo(row.installed) },
    { id: "compatible", label: t("ext.apps.acp.field.compatible"), value: yesNo(row.compatible) },
    { id: "authenticated", label: t("ext.apps.acp.field.authenticated"), value: yesNo(row.authenticated) },
    { id: "activated", label: t("ext.apps.acp.field.activated"), value: yesNo(row.activated) },
    { id: "transport", label: t("ext.apps.acp.field.transport"), value: t("ext.apps.acp.transport_cli") },
  ];
  if (typeof row.version === "string" && row.version) facts.push({ id: "version", label: t("ext.apps.acp.field.version"), value: row.version });
  if (row.protocol_version !== null && row.protocol_version !== undefined && row.protocol_version !== "") {
    facts.push({ id: "protocol", label: t("ext.apps.acp.field.protocol"), value: String(row.protocol_version) });
  }
  return facts;
}

const text = (value) => (typeof value === "string" ? value.trim() : "");

// The install panel: the command (to copy), version, license and the source as an ordinary link. Null when the server sent none.
export function installPanel(install) {
  const source = install && typeof install === "object" ? install : {};
  const command = text(source.command);
  if (!command) return null;
  const link = text(source.source);
  return { command, version: text(source.version), license: text(source.license), source: link,
    sourceHref: /^https:\/\//i.test(link) ? link : "" };
}

export function acpView(row, activation = null) {
  const step = acpStep(row, activation);
  return {
    facts: acpFacts(row),
    step,
    install: installPanel(row.install),
    activated: row.activated === true,
    // a conversation needs the adapter compatible and activated by the person, and no pending probe
    canStart: row.activated === true && row.compatible === true && step !== "probe",
  };
}

// --- the area (office and area) the consent and the conversation belong to ---------------------------------------------
// ``offices`` are the answers of GET /gui/api/offices/<id> (each with ``areas`` that carry their package name, ``bundle``).
export function officeChoices(offices) {
  const groups = [];
  for (const office of Array.isArray(offices) ? offices : []) {
    if (!office || typeof office.id !== "string") continue;
    const areas = (Array.isArray(office.areas) ? office.areas : [])
      .filter((area) => area && typeof area.bundle === "string" && area.bundle)
      .map((area) => ({ bundle: area.bundle, name: text(area.name) || area.id || area.bundle }));
    if (areas.length) groups.push({ id: office.id, name: text(office.name) || office.id, areas });
  }
  return groups;
}

// --- the consent of an area ---------------------------------------------------------------------------------------------
export function consentView(payload) {
  const configured = Boolean(payload && payload.configured === true);
  return { configured, allowWrite: configured && payload.allow_write === true };
}
export const consentStateText = (view) => (!view.configured ? t("ext.apps.acp.consent.not_configured")
  : view.allowWrite ? t("ext.apps.acp.consent.write") : t("ext.apps.acp.consent.read"));
// The client never sends a scope: the server finds the office and the area from the package.
export const consentBody = (bundle, allowWrite) => ({ bundle, allow_write: allowWrite === true });
export const consentNote = () => t("ext.apps.acp.consent.note");

const details = (failure) => (failure && typeof failure.details === "object" && failure.details ? failure.details : {});

// A 404 (the package is gone) and a 409 ``acp_area_required`` are texts of the card, never a broken card.
export function consentFailureText(failure) {
  if (failure && failure.status === 404) return t("ext.apps.acp.consent.not_found");
  if (failure && failure.status === 409 && details(failure).reason === "acp_area_required") return t("ext.apps.acp.area_required");
  return (failure && failure.message) || t("ext.apps.acp.consent.failed");
}

// --- the start of a conversation ---------------------------------------------------------------------------------------
// Apps only navigates (the first message is written in the thread view); here lives the notice of an adapter that is not ready.
const UNAVAILABLE_REASONS = {
  acp_unavailable: "ext.apps.acp.reason.acp_unavailable",
  acp_activation_required: "ext.apps.acp.reason.acp_activation_required",
  acp_probe_required: "ext.apps.acp.reason.acp_probe_required",
  acp_area_required: "ext.apps.acp.area_required",
  acp_session_limit: "ext.apps.acp.reason.acp_session_limit",
  executor_paused_changed: "ext.apps.acp.reason.executor_paused_changed",
  executor_stopped_by_person: "ext.apps.acp.reason.executor_stopped_by_person",
};

// «ACP no disponible» with its reason and CLI as a visible option. Nothing starts CLI by itself.
export function startFailure(failure) {
  const reason = details(failure).reason;
  if (failure && failure.status === 409 && UNAVAILABLE_REASONS[reason]) {
    return { unavailable: true, reason, title: t("ext.apps.acp.unavailable"), text: t(UNAVAILABLE_REASONS[reason]) };
  }
  if (failure && failure.status === 404) return { unavailable: false, reason: "not_found", title: "", text: t("ext.apps.acp.consent.not_found") };
  return { unavailable: false, reason: "failed", title: "", text: (failure && failure.message) || t("ext.apps.acp.start.failed") };
}

// The adapter is activated but cannot take a conversation yet: the same notice a 409 would give, before anything is sent.
export const notReadyFailure = (probeNeeded) => startFailure({ status: 409, details: { reason: probeNeeded ? "acp_probe_required" : "acp_unavailable" } });
