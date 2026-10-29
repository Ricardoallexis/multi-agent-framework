// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// One alert of the document barrier (BDI-T05) as plain text: the source, the policy that was applied, the signals and the
// fragments that were put in quarantine, with the state of the review. Pure (no hooks), so a test can call it in Node.
// Everything that comes from the server is untrusted text (an excerpt is a piece of a hostile document): it only goes in
// as a text child of htm/Preact, never as HTML, and the original of a fragment is never fetched or shown.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "../../js/help.js";
import { routeHref } from "../../js/route.js";
import { fmtDate } from "../../js/text.js";

const KINDS = {
  document: "Documento", attachment: "Adjunto", channel_entry: "Entrada del canal", worker_report: "Informe de un trabajador",
  transcript: "Transcripción", tool_output: "Salida de una herramienta", web_page: "Página web", model_output: "Salida de un modelo",
  context_summary: "Resumen de contexto", executor_result: "Resultado de un ejecutor", unknown: "Origen desconocido",
};
const POLICIES = { quarantine_warn: "Cuarentena con aviso", pause: "Pausa para revisión" };
const ACTIONS = { quarantine: "Cuarentena con aviso", pause: "Pausa para revisión" };
const SEVERITIES = { low: "baja", medium: "media", high: "alta" };
const SIGNALS = {
  ignore_rules: "ignorar las reglas", false_authorization: "falsa autorización", command_request: "petición de un comando",
  false_urgency: "falsa urgencia", role_marker: "marca de rol", fake_delimiter: "delimitador falso",
  encoded_command: "comando codificado", encoded_instruction: "instrucción codificada", encoded_payload: "contenido codificado",
  too_large_to_scan: "demasiado grande para revisarlo", invisible_characters: "caracteres invisibles",
  hidden_content: "contenido oculto", hidden_instruction: "instrucción oculta",
};
// state -> label, tone (a class that app.css variables colour; the label is the cue, colour only helps) and what it means
const STATES = {
  quarantined: { label: "En cuarentena", tone: "warn", note: "El fragmento se cambió por una marca y el trabajo siguió." },
  open: { label: "Espera una decisión", tone: "bad",
    note: "Una persona debe aprobar o rechazar desde la ejecución. Esta pantalla no decide." },
  approved: { label: "Aprobada", tone: "ok", note: "Una persona dejó seguir el paso; el fragmento sigue en cuarentena." },
  rejected: { label: "Rechazada", tone: "bad", note: "Una persona rechazó la ejecución." },
};
const UNKNOWN_STATE = { label: "Sin estado", tone: "warn", note: "" };

export const kindText = (kind) => KINDS[kind] || String(kind || "") || KINDS.unknown;
export const policyText = (alert) => POLICIES[alert.policy] || alert.policy || ACTIONS[alert.action] || alert.action || "Sin dato";
export const severityText = (value) => SEVERITIES[value] || String(value || "") || "sin dato";
export const signalText = (code) => SIGNALS[code] || String(code || "");
export const stateInfo = (state) => STATES[state] || UNKNOWN_STATE;
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
    <blockquote class="document-alerts-excerpt">${fragment.excerpt || "(sin extracto)"}</blockquote>
    <figcaption class="document-alerts-meta">${fragment.length} caracteres${codes.length > 0 ? " · " + codes.join(", ") : ""}
      ${fragment.ref ? " · original guardado aparte (" + shortRef(fragment.ref) + ")" : ""}</figcaption>
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
    <p class="document-alerts-where">Paso <strong>${alert.step_id || "—"}</strong>${alert.attempt ? ", intento " + alert.attempt : ""}${alert.part ? ", parte " + alert.part : ""}</p>
    <dl class="document-alerts-facts">
      <${Fact} label="Fuente">
        <span class="document-alerts-line"><strong>${name}</strong> · ${kindText(source.kind)}${source.trust === "untrusted" ? " (no confiable)" : ""}</span>
        ${source.ref && source.ref !== name ? html`<span class="document-alerts-line document-alerts-meta">${source.ref}</span>` : null}
        ${origins.length > 0 ? html`<span class="document-alerts-line document-alerts-meta">Viene de: ${origins.join(", ")}</span>` : null}
      <//>
      <${Fact} label="Política aplicada">${policyText(alert)}${POLICIES[alert.policy] ? html` <span class="document-alerts-meta">(${alert.policy})</span>` : null}<//>
      <${Fact} label="Gravedad">${severityText(alert.max_severity)}${codes.length > 0 ? " · " + codes.join(", ") : ""}<//>
    </dl>
    ${fragments.length > 0 ? html`<div class="document-alerts-fragments">
      <p class="document-alerts-label">${fragments.length === 1 ? "Fragmento en cuarentena" : "Fragmentos en cuarentena · " + fragments.length}</p>
      ${fragments.map((fragment, index) => html`<${Fragment} key=${fragment.sha256 || index} fragment=${fragment} />`)}
    </div>` : null}
    ${state.note ? html`<p class="document-alerts-note">${state.note}</p>` : null}
    ${alert.decision ? html`<p class="document-alerts-meta">Decisión: ${alert.decision === "approved" ? "aprobada" : "rechazada"}${alert.decided_by ? " · declarado por " + alert.decided_by : ""}${alert.channel ? " (" + alert.channel + ")" : ""}</p>` : null}
    ${showRun && alert.run_id ? html`<p class="document-alerts-run">
      <a class="document-alerts-run-link" href=${routeHref("runs", alert.run_id)} ...${helpFor("document_alerts.open_run", { scope })}>Abrir la ejecución</a>
      <${Help} topic="document_alerts.open_run" compact=${true} scope=${scope} />
    </p>` : null}
  </li>`;
}
