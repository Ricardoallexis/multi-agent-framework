// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Alerts and quarantine of documents in GUI2 (BDI-T05). One folder, no shared file edited (see js/extensions.js):
//   the view  #document_alerts           the latest alerts of the system, and #document_alerts/<run id> for one run;
//   the slot  "document_alerts"           a section of the right panel with the alerts of the run that is open.
// Both read GET /gui/api/document-alerts (document_alerts_api.py), which only reports what the engine recorded. Nothing here
// approves, rejects or releases anything: a review is decided in the run itself, by a person.
import "./i18n.js";
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "../../js/api.js";
import { ContentReport, useContentReport } from "../../js/context-report.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
import { POLL_MS, panelVisible } from "../../js/panel-data.js";
import { subjectOf } from "../../js/panel-model.js";
import { Empty, Failure, Loading } from "../../js/panel-parts.js";
import { registerSlot } from "../../js/panel-slots.js";
import { AlertItem, pendingCount } from "./alert-item.js";
import { HELP } from "./help.js";

registerHelp(HELP, "document_alerts");

const alertsPath = (runId) => "/gui/api/document-alerts" + (runId ? "?run_id=" + encodeURIComponent(runId) : "");
const LOADING = { status: "loading", alerts: [], truncated: false, error: null };
const asApiError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));

// The alerts of a run (or of the system when runId is null): read when it opens, every POLL_MS while it can be seen, and on
// demand. A failed read keeps what is already on screen (marked as old) and says why; it never turns into «sin alertas».
function useAlerts(token, runId) {
  const [state, setState] = useState(LOADING);
  const reload = useRef(null);
  useEffect(() => {
    let live = true;
    setState(LOADING);        // another run or token: the alerts of the previous one never stay on screen
    const load = () => apiGet(alertsPath(runId), token).then(
      (data) => live && setState({ status: "ready", alerts: Array.isArray(data && data.alerts) ? data.alerts : [],
                                   truncated: Boolean(data && data.truncated), error: null }),
      (failure) => live && setState((previous) => ({ ...previous, status: "error", error: asApiError(failure) })));
    reload.current = load;
    load();
    const timer = setInterval(() => { if (panelVisible()) load(); }, POLL_MS);
    return () => { live = false; clearInterval(timer); reload.current = null; };
  }, [token, runId]);
  return { ...state, reload: () => reload.current && reload.current() };
}

function summaryText(alerts) {
  if (alerts.length === 0) return t("ext.document_alerts.summary.none");
  const waiting = pendingCount(alerts);
  const total = t(alerts.length === 1 ? "ext.document_alerts.summary.one" : "ext.document_alerts.summary.many", { count: alerts.length });
  return waiting > 0 ? total + " · " + t(waiting === 1
    ? "ext.document_alerts.summary.waiting_one" : "ext.document_alerts.summary.waiting_many", { count: waiting }) : total;
}

// The summary, the refresh button and the list; `scope` keeps the ids of the help apart (the card and the view can be on screen
// together). A read that failed before anything was read says so and shows no list; one that failed later keeps the old list.
function Alerts({ data, scope, showRun, labelledBy }) {
  const stale = data.alerts.length > 0;
  const summary = data.status === "loading" ? t("ext.document_alerts.loading")
    : data.status === "error" && !stale ? t("ext.document_alerts.error.read") : summaryText(data.alerts);
  return html`<div class="document-alerts-card" aria-busy=${data.status === "loading" ? "true" : "false"}>
    <div class="document-alerts-bar">
      <p class="document-alerts-summary" id=${"document-alerts-summary-" + scope} role="status" aria-live="polite">${summary}</p>
      <span class="document-alerts-refresh">
        <button type="button" class="btn secondary small" ...${helpFor("document_alerts.refresh", { scope })} onClick=${data.reload}>${t("ext.document_alerts.refresh")}</button>
        <${Help} topic="document_alerts.refresh" compact=${true} scope=${scope} />
      </span>
    </div>
    ${data.status === "error" ? html`<${Failure} error=${data.error} scope=${"document-alerts-" + scope} what=${t("ext.document_alerts.what")}
      stale=${stale} onRetry=${data.reload} />` : null}
    ${data.alerts.length > 0 ? html`<ul class="document-alerts-list" aria-labelledby=${labelledBy || "document-alerts-summary-" + scope}>
      ${data.alerts.map((alert) => html`<${AlertItem} key=${alert.id} alert=${alert} showRun=${showRun} />`)}
    </ul>` : null}
    ${data.truncated ? html`<${Empty}>${t("ext.document_alerts.truncated")}<//>` : null}
  </div>`;
}

function RunAlerts({ token, runId, onContent }) {
  const data = useAlerts(token, runId);
  useContentReport(onContent, data.alerts.length > 0, pendingCount(data.alerts) > 0);   // GUI3-T04: alerts that wait for a decision are urgent
  return html`<${Alerts} data=${data} scope="card" showRun=${false} />`;
}

// A section of the right panel (props: token, route, sessionId, onContent): the alerts of the run that is open in the centre.
// With no run open the card has nothing to show (GUI3-T04): ContentReport says so and draws nothing.
export function DocumentAlertsCard({ token, route, sessionId, onContent }) {
  const subject = subjectOf(route, sessionId);
  const runId = subject && subject.runId;
  return runId
    ? html`<${RunAlerts} key=${runId} token=${token} runId=${runId} onContent=${onContent} />`
    : html`<${ContentReport} onContent=${onContent} has=${false} /><${Empty}>${t("ext.document_alerts.no_run")}<//>`;
}

// The screen of the sidebar (props: token, status, mock, route = { section, id }): #document_alerts or #document_alerts/<run id>.
export function DocumentAlertsView({ token, route }) {
  const runId = (route && route.id) || null;
  const data = useAlerts(token, runId);
  return html`<section class="document-alerts-view" aria-label=${t("ext.document_alerts.title")}>
    <p class="muted small document-alerts-scope">${runId
      ? t("ext.document_alerts.scope.run", { run: runId })
      : t("ext.document_alerts.scope.system")}</p>
    <${Alerts} data=${data} scope="view" showRun=${!runId} />
  </section>`;
}

registerView({ id: "document_alerts", owner: "document_alerts", group: "pending", icon: "file", label: t("ext.document_alerts.title"), topic: "document_alerts.view", component: DocumentAlertsView, order: 50, mode: "simple" });
registerSlot("document_alerts", DocumentAlertsCard, { title: t("ext.document_alerts.title"), topic: "document_alerts.card", owner: "document_alerts", order: 50 });
