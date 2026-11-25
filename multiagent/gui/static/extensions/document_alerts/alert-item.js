// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// One alert of the document barrier (BDI-T05) as plain text: the source, the policy that was applied, the signals and the
// fragments that were put in quarantine, with the state of the review. Pure (no hooks), so a test can call it in Node.
// Everything that comes from the server is untrusted text (an excerpt is a piece of a hostile document): it only goes in
// as a text child of htm/Preact, never as HTML, and the original of a fragment is never fetched or shown.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { routeHref } from "../../js/route.js";
import { fmtDate } from "../../js/text.js";

const KINDS = {
  document: () => t("ext.document_alerts.kind.document"),
  attachment: () => t("ext.document_alerts.kind.attachment"),
  channel_entry: () => t("ext.document_alerts.kind.channel_entry"),
  worker_report: () => t("ext.document_alerts.kind.worker_report"),
  transcript: () => t("ext.document_alerts.kind.transcript"),
  tool_output: () => t("ext.document_alerts.kind.tool_output"),
  web_page: () => t("ext.document_alerts.kind.web_page"),
  model_output: () => t("ext.document_alerts.kind.model_output"),
  context_summary: () => t("ext.document_alerts.kind.context_summary"),
  executor_result: () => t("ext.document_alerts.kind.executor_result"),
  unknown: () => t("ext.document_alerts.kind.unknown"),
};
const POLICIES = {
  quarantine_warn: () => t("ext.document_alerts.policy.quarantine_warn"),
  pause: () => t("ext.document_alerts.policy.pause"),
};
const ACTIONS = {
  quarantine: () => t("ext.document_alerts.policy.quarantine"),
  pause: () => t("ext.document_alerts.policy.pause"),
};
const SEVERITIES = {
  low: () => t("ext.document_alerts.severity.low"),
  medium: () => t("ext.document_alerts.severity.medium"),
  high: () => t("ext.document_alerts.severity.high"),
};
const SIGNALS = {
  ignore_rules: () => t("ext.document_alerts.signal.ignore_rules"),
  false_authorization: () => t("ext.document_alerts.signal.false_authorization"),
  command_request: () => t("ext.document_alerts.signal.command_request"),
  false_urgency: () => t("ext.document_alerts.signal.false_urgency"),
  role_marker: () => t("ext.document_alerts.signal.role_marker"),
  fake_delimiter: () => t("ext.document_alerts.signal.fake_delimiter"),
  encoded_command: () => t("ext.document_alerts.signal.encoded_command"),
  encoded_instruction: () => t("ext.document_alerts.signal.encoded_instruction"),
  encoded_payload: () => t("ext.document_alerts.signal.encoded_payload"),
  too_large_to_scan: () => t("ext.document_alerts.signal.too_large_to_scan"),
  invisible_characters: () => t("ext.document_alerts.signal.invisible_characters"),
  hidden_content: () => t("ext.document_alerts.signal.hidden_content"),
  hidden_instruction: () => t("ext.document_alerts.signal.hidden_instruction"),
};
// state -> label, tone (a class that app.css variables colour; the label is the cue, colour only helps) and what it means
const STATES = {
  quarantined: { label: "ext.document_alerts.state.quarantined", tone: "warn", note: "ext.document_alerts.note.quarantined" },
  open: { label: "ext.document_alerts.state.open", tone: "bad", note: "ext.document_alerts.note.open" },
  approved: { label: "ext.document_alerts.state.approved", tone: "ok", note: "ext.document_alerts.note.approved" },
  rejected: { label: "ext.document_alerts.state.rejected", tone: "bad", note: "ext.document_alerts.note.rejected" },
};
const UNKNOWN_STATE = { label: "ext.document_alerts.state.unknown", tone: "warn", note: "" };

export const kindText = (kind) => KINDS[kind]?.() || String(kind || "") || KINDS.unknown();
export const policyText = (alert) => POLICIES[alert.policy]?.() || alert.policy || ACTIONS[alert.action]?.() || alert.action
  || t("ext.document_alerts.no_data");
export const severityText = (value) => SEVERITIES[value]?.() || String(value || "") || t("ext.document_alerts.severity.unknown");
export const signalText = (code) => SIGNALS[code]?.() || String(code || "");
export const stateInfo = (state) => {
  const found = STATES[state] || UNKNOWN_STATE;
  return { ...found, label: t(found.label), note: found.note ? t(found.note) : "" };
};
// «blob:sha256:0123456789ab…»: enough to tell fragments apart
export const shortRef = (ref) => {
  const text = String(ref || "");
  return text.length > 24 ? text.slice(0, 24) + "…" : text;
};
export const pendingCount = (alerts) => alerts.filter((alert) => alert.state === "open").length;

// The codes of the signals of an alert, once each and in order.
export function signalCodes(alert) {
  const codes = [];
  for (const signal of alert.signals || []) {
    if (signal && signal.code && !codes.includes(signal.code)) codes.push(signal.code);
  }
  return codes;
}

function Fact({ label, children }) {
  return html`<div class="document-alerts-fact"><dt class="document-alerts-label">${label}</dt><dd class="document-alerts-value">${children}</dd></div>`;
}

function Fragment({ fragment }) {
  const codes = (fragment.signals || []).map(signalText).filter(Boolean);
  return html`<figure class="document-alerts-fragment">
    <blockquote class="document-alerts-excerpt">${fragment.excerpt || t("ext.document_alerts.fragment.no_excerpt")}</blockquote>
    <figcaption class="document-alerts-meta">${t("ext.document_alerts.fragment.length", { count: fragment.length })}${codes.length > 0 ? " · " + codes.join(", ") : ""}
      ${fragment.ref ? " · " + t("ext.document_alerts.fragment.original_saved", { ref: shortRef(fragment.ref) }) : ""}</figcaption>
  </figure>`;
}

// showRun: the list of all runs names the run of each alert and links to it (the card of a run already is that run).
export function AlertItem({ alert, showRun = false }) {
  const state = stateInfo(alert.state);
  const source = alert.source || {};
  const name = source.label || source.ref || kindText(source.kind);
  const codes = signalCodes(alert).map(signalText).filter(Boolean);
  const fragments = alert.fragments || [];
  const origins = (alert.origins || []).map((origin) => origin.label || origin.ref || kindText(origin.kind));
  const scope = String(alert.id);
  return html`<li class="document-alerts-item" data-state=${alert.state}>
    <div class="document-alerts-item-head">
      <span class=${"document-alerts-badge document-alerts-tone-" + state.tone}>${state.label}</span>
      <span class="document-alerts-when">${fmtDate(alert.ts)}</span>
    </div>
    <p class="document-alerts-where">${t("ext.document_alerts.step_label")} <strong>${alert.step_id || "—"}</strong>${alert.attempt
      ? t("ext.document_alerts.attempt", { attempt: alert.attempt }) : ""}${alert.part
      ? t("ext.document_alerts.part", { part: alert.part }) : ""}</p>
    <dl class="document-alerts-facts">
      <${Fact} label=${t("ext.document_alerts.source")}>
        <span class="document-alerts-line"><strong>${name}</strong> · ${kindText(source.kind)}${source.trust === "untrusted" ? " (" + t("ext.document_alerts.untrusted") + ")" : ""}</span>
        ${source.ref && source.ref !== name ? html`<span class="document-alerts-line document-alerts-meta">${source.ref}</span>` : null}
        ${origins.length > 0 ? html`<span class="document-alerts-line document-alerts-meta">${t("ext.document_alerts.origins", { origins: origins.join(", ") })}</span>` : null}
      <//>
      <${Fact} label=${t("ext.document_alerts.policy_applied")}>${policyText(alert)}${POLICIES[alert.policy] ? html` <span class="document-alerts-meta">(${alert.policy})</span>` : null}<//>
      <${Fact} label=${t("ext.document_alerts.severity")}>${severityText(alert.max_severity)}${codes.length > 0 ? " · " + codes.join(", ") : ""}<//>
    </dl>
    ${fragments.length > 0 ? html`<div class="document-alerts-fragments">
      <p class="document-alerts-label">${fragments.length === 1 ? t("ext.document_alerts.fragment.one")
        : t("ext.document_alerts.fragment.many", { count: fragments.length })}</p>
      ${fragments.map((fragment, index) => html`<${Fragment} key=${fragment.sha256 || index} fragment=${fragment} />`)}
    </div>` : null}
    ${state.note ? html`<p class="document-alerts-note">${state.note}</p>` : null}
    ${alert.decision ? html`<p class="document-alerts-meta">${t("ext.document_alerts.decision", {
      decision: alert.decision === "approved" ? t("ext.document_alerts.decision.approved") : t("ext.document_alerts.decision.rejected"),
    })}${alert.decided_by ? " · " + t("ext.document_alerts.decided_by", { person: alert.decided_by }) : ""}${alert.channel ? " (" + alert.channel + ")" : ""}</p>` : null}
    ${showRun && alert.run_id ? html`<p class="document-alerts-run">
      <a class="document-alerts-run-link" href=${routeHref("runs", alert.run_id)} ...${helpFor("document_alerts.open_run", { scope })}>${t("ext.document_alerts.open_run")}</a>
      <${Help} topic="document_alerts.open_run" compact=${true} scope=${scope} />
    </p>` : null}
  </li>`;
}
