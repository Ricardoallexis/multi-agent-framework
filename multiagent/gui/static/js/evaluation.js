// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Read-only evaluation view: saved sets and runs, with a comparison against the baseline. Incoming text is untrusted.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { t, currentLanguage } from "./i18n.js";
import { routeHref } from "./route.js";
import { StatusChip } from "./ui/index.js";

export const SET_STATUS = {
  regression: { get text() { return t("evaluation.regression"); }, tone: "danger", get explanation() { return t("evaluation.regression_explain"); } },
  no_baseline: { get text() { return t("evaluation.no_baseline"); }, tone: "neutral", get explanation() { return t("evaluation.no_baseline_explain"); } },
  up_to_date: { get text() { return t("evaluation.up_to_date"); }, tone: "success", get explanation() { return t("evaluation.up_to_date_explain"); } },
};
export const CASE_STATUS = {
  regressed: { get text() { return t("evaluation.regression"); }, tone: "danger" },
  improved: { get text() { return t("evaluation.improved"); }, tone: "success" },
  changed: { get text() { return t("evaluation.changed"); }, tone: "attention" },
  unchanged: { get text() { return t("evaluation.unchanged"); }, tone: "muted" },
};
const CASE_ORDER = ["regressed", "improved", "changed", "unchanged"];
export const FINGERPRINT_LABEL = {
  agent_or_prompt: "evaluation.agent_prompt", workflow: "evaluation.workflow", models: "evaluation.models", set_changed: "evaluation.set",
};
const LIST_PATH = "/gui/api/evaluations";

export const scoreText = (score) => typeof score === "number" && Number.isFinite(score) ? Math.round(score * 100) + " %" : t("evaluation.no_score");
export const deltaText = (delta) => typeof delta !== "number" || !Number.isFinite(delta) ? t("evaluation.no_value")
  : (delta > 0 ? "+" : "") + (Math.round(delta * 1000) / 10) + t("evaluation.points");
export const modeText = (run) => run && run.live ? t("evaluation.live") : "Mock";
const when = (value) => {
  if (!value) return t("evaluation.no_date");
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? t("evaluation.date_unavailable") : new Intl.DateTimeFormat(currentLanguage() === "es" ? "es-MX" : "en", { dateStyle: "medium", timeStyle: "short" }).format(date);
};

// Cases ordered by what matters first: regressions, improvements, changes, the rest.
export function orderCases(cases) {
  const rank = (item) => { const index = CASE_ORDER.indexOf(item.status); return index < 0 ? CASE_ORDER.length : index; };
  return (Array.isArray(cases) ? cases : []).map((item, index) => [item, index])
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map((pair) => pair[0]);
}

// The kinds of change of the fingerprint that are true, as labels.
export const fingerprintChanges = (fingerprint) =>
  Object.keys(FINGERPRINT_LABEL).filter((key) => fingerprint && fingerprint[key] === true).map((key) => t(FINGERPRINT_LABEL[key]));

function Failure({ error }) {
  return html`<p class="message error" role="alert">${error && error.message ? error.message : t("evaluation.read_error")}</p>`;
}

export function SetList({ data }) {
  const sets = Array.isArray(data && data.sets) ? data.sets : [];
  if (!sets.length) return html`<p>${t("evaluation.no_runs")}</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">${t("evaluation.sets")}</caption>
    <thead><tr><th scope="col">${t("evaluation.set_heading")}</th><th scope="col">${t("evaluation.latest_score")}</th><th scope="col">${t("evaluation.type")}</th><th scope="col">${t("evaluation.baseline")}</th><th scope="col">${t("evaluation.status")}</th></tr></thead>
    <tbody>${sets.map((entry) => html`<tr key=${entry.set_name}>
      <th scope="row"><a href=${routeHref("evaluation", entry.set_name)}>${entry.set_name}</a></th>
      <td>${scoreText(entry.latest_run && entry.latest_run.score)} <span class="muted small">${when(entry.latest_run && entry.latest_run.created_at)}</span></td>
      <td>${modeText(entry.latest_run)}</td>
      <td>${entry.baseline ? scoreText(entry.baseline.run && entry.baseline.run.score) : t("evaluation.no_baseline")}</td>
      <td><${StatusChip} status=${entry.status} labels=${SET_STATUS} /></td>
    </tr>`)}</tbody>
  </table></div>`;
}

export function RunsTable({ runs, baselineId }) {
  if (!runs.length) return html`<p>${t("evaluation.set_no_runs")}</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">${t("evaluation.saved_runs")}</caption>
    <thead><tr><th scope="col">${t("evaluation.date")}</th><th scope="col">${t("evaluation.version")}</th><th scope="col">${t("evaluation.score")}</th><th scope="col">${t("evaluation.type")}</th><th scope="col">${t("evaluation.who")}</th><th scope="col">${t("evaluation.baseline")}</th></tr></thead>
    <tbody>${runs.map((run) => html`<tr key=${run.id}>
      <th scope="row">${when(run.created_at)}</th>
      <td>${run.set_version || t("evaluation.no_version")}</td>
      <td>${scoreText(run.score)}</td>
      <td>${modeText(run)}</td>
      <td>${run.actor || t("evaluation.no_value")}</td>
      <td>${run.id === baselineId ? t("evaluation.yes") : ""}</td>
    </tr>`)}</tbody>
  </table></div>`;
}

export function ComparisonView({ comparison }) {
  const summary = comparison.summary || {};
  const cases = orderCases(comparison.cases);
  const changes = fingerprintChanges(comparison.fingerprint);
  const only = (list) => Array.isArray(list) ? list : [];
  return html`<div class="usage-comparison">
    <p><${StatusChip} status=${comparison.regression ? "regression" : "up_to_date"} labels=${SET_STATUS} />
      ${" "}${t("evaluation.comparison_score", { base: scoreText(comparison.score && comparison.score.base),
        latest: scoreText(comparison.score && comparison.score.candidate),
        delta: deltaText(comparison.score && comparison.score.delta), threshold: scoreText(comparison.threshold) })}</p>
    <p class="muted small">${CASE_ORDER.map((name) => CASE_STATUS[name].text + ": " + (summary[name] || 0)).join(" · ")}</p>
    <h4>${t("evaluation.cases")}</h4><${Help} topic="evaluation.cases" />
    ${cases.length ? html`<div class="usage-table-wrap"><table class="usage-table">
      <caption class="sr-only">${t("evaluation.cases_baseline")}</caption>
      <thead><tr><th scope="col">${t("evaluation.case")}</th><th scope="col">${t("evaluation.result")}</th><th scope="col">${t("evaluation.baseline")}</th><th scope="col">${t("evaluation.latest")}</th><th scope="col">${t("evaluation.different_checks")}</th></tr></thead>
      <tbody>${cases.map((item) => html`<tr key=${item.id}>
        <th scope="row">${item.id}</th>
        <td><${StatusChip} status=${item.status} labels=${CASE_STATUS} /></td>
        <td>${scoreText(item.base_score)} · ${item.base_passed ? t("evaluation.pass") : t("evaluation.fail")}</td>
        <td>${scoreText(item.candidate_score)} · ${item.candidate_passed ? t("evaluation.pass") : t("evaluation.fail")}</td>
        <td>${Array.isArray(item.changed_checks) && item.changed_checks.length ? item.changed_checks.join(", ") : t("evaluation.none")}</td>
      </tr>`)}</tbody>
    </table></div>` : html`<p>${t("evaluation.no_common_cases")}</p>`}
    ${only(comparison.only_in_base).length > 0 && html`<p><strong>${t("evaluation.only_baseline")}</strong> ${only(comparison.only_in_base).join(", ")}.</p>`}
    ${only(comparison.only_in_candidate).length > 0 && html`<p><strong>${t("evaluation.only_latest")}</strong> ${only(comparison.only_in_candidate).join(", ")}.</p>`}
    <h4>${t("evaluation.fingerprint_heading")}</h4><${Help} topic="evaluation.fingerprint" />
    <p>${changes.length ? changes.join(", ") + "." : t("evaluation.no_fingerprint_changes")}</p>
  </div>`;
}

function useLoad(path, token, ready = true) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const ticket = useRef(0);
  const load = async () => {
    if (!ready) return;
    const mine = ++ticket.current;
    setState((previous) => ({ ...previous, loading: true }));
    try {
      const data = await apiGet(path, token);
      if (mine === ticket.current) setState({ loading: false, data, error: null });
    } catch (error) {
      if (mine === ticket.current) setState((previous) => ({ loading: false, data: previous.data, error }));
    }
  };
  useEffect(() => { load(); return () => { ticket.current += 1; }; }, [path, token, ready]);
  return [state, load];
}

function SetDetail({ token, setName, reload }) {
  const query = "set_name=" + encodeURIComponent(setName);
  const [runs, loadRuns] = useLoad(LIST_PATH + "/runs?" + query, token);
  const [comparison, loadComparison] = useLoad(LIST_PATH + "/comparison?" + query, token);
  useEffect(() => { reload.current = () => { loadRuns(); loadComparison(); }; return () => { reload.current = null; }; });
  const list = runs.data && Array.isArray(runs.data.runs) ? runs.data.runs : [];
  const noBaseline = comparison.error && comparison.error.status === 404;
  return html`<div>
    <p><a href=${routeHref("evaluation")}>${t("evaluation.all_sets")}</a></p>
    <h4>${t("evaluation.runs_of", { name: setName })}</h4><${Help} topic="evaluation.runs" />
    ${runs.error && html`<${Failure} error=${runs.error} />`}
    ${runs.loading && !runs.data ? html`<p role="status" aria-live="polite">${t("evaluation.loading_runs")}</p>`
      : runs.data && html`<${RunsTable} runs=${list} baselineId=${comparison.data && comparison.data.base_id} />`}
    <h4>${t("evaluation.compare_heading")}</h4><${Help} topic="evaluation.compare" />
    ${noBaseline ? html`<p>${t("evaluation.no_baseline_compare")} <code>multiagent eval baseline set</code>.</p>`
      : comparison.error ? html`<${Failure} error=${comparison.error} />`
      : comparison.loading && !comparison.data ? html`<p role="status" aria-live="polite">${t("evaluation.loading_comparison")}</p>`
      : comparison.data && html`<${ComparisonView} comparison=${comparison.data} />`}
  </div>`;
}

export function Evaluation({ token, route }) {
  const setName = route && route.id ? route.id : null;
  const [sets, loadSets] = useLoad(LIST_PATH, token, !setName);
  const reload = useRef(null);
  const refresh = () => { if (setName) { if (reload.current) reload.current(); } else loadSets(); };
  const busy = !setName && sets.loading;
  return html`<div class="usage-view">
    <h3 id="evaluation-title">${t("evaluation.sets")}</h3><${Help} topic="evaluation.section" always=${true} />
    <div class="usage-title-row"><p class="muted">${t("evaluation.intro")}</p>
      <div class="usage-refresh-control" role="group" aria-label=${t("evaluation.refresh_aria")}>
        <button type="button" class="btn" disabled=${busy} ...${helpFor("evaluation.refresh")} onClick=${refresh}>${busy ? t("evaluation.refreshing") : t("evaluation.refresh")}</button>
        <${Help} topic="evaluation.refresh" compact=${true} />
      </div>
    </div>
    ${setName ? html`<${SetDetail} key=${setName} token=${token} setName=${setName} reload=${reload} />` : html`<div>
      <h4>${t("evaluation.sets_short")}</h4><${Help} topic="evaluation.sets" />
      ${sets.error && html`<${Failure} error=${sets.error} />`}
      ${sets.loading && !sets.data ? html`<p role="status" aria-live="polite">${t("evaluation.loading_sets")}</p>` : sets.data && html`<${SetList} data=${sets.data} />`}
    </div>`}
  </div>`;
}
