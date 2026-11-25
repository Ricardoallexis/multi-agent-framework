// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Usage view: presents local measurements from the usage API as untrusted text.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { t, currentLanguage } from "./i18n.js";
import { registeredUsageSections } from "./extensions.js";
import { Progress, Tabs, LevelBadge, StatusChip, levelInfo } from "./ui/index.js";

const APPS = { claude: "Claude", codex: "Codex", copilot: "Copilot" };
const SOURCE_STATUS = {
  ok: { get text() { return t("usage.available"); }, tone: "success" },
  partial: { get text() { return t("usage.partial"); }, tone: "warning" },
  not_available: { get text() { return t("usage.no_data"); }, tone: "neutral" },
  disabled: { get text() { return t("usage.disabled"); }, tone: "muted" },
};
const WINDOW_LABEL = { five_hour: "usage.five_hours", weekly: "usage.week", model_weekly: "usage.model_week", monthly: "usage.month", spend: "usage.spend" };
const STATE_LABEL = {
  current: "usage.current",
  window_reset: "usage.window_reset",
  limit_reached: "usage.limit_reached",
  unknown: "usage.no_value",
};
const locale = () => currentLanguage() === "es" ? "es-MX" : "en";
const number = (value) => new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(value);
const money = (value) => typeof value === "number" && Number.isFinite(value)
  ? new Intl.NumberFormat(locale(), { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(value)
  : t("usage.no_amount");
const timestamp = (value) => {
  if (!value) return t("usage.no_date");
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? t("usage.date_unavailable") : new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(date);
};
const pct = (value) => typeof value === "number" && Number.isFinite(value) ? number(value) + " %" : t("usage.unavailable");
const amount = (value) => typeof value === "number" && Number.isFinite(value) ? number(value) : t("usage.unavailable");

export function PlanCard({ app }) {
  const name = APPS[app.app] || app.app || t("usage.app");
  if (app.status === "disabled") return html`<article class="usage-card"><h4>${name}</h4><p>${t("usage.disabled_session")}</p></article>`;
  const windows = Array.isArray(app.windows) ? app.windows : [];
  const credits = app.credits;
  const claudeWithoutSnapshot = app.app === "claude" && !app.source;
  const status = SOURCE_STATUS[app.status] ? app.status : "not_available";
  return html`<article class="usage-card">
    <div class="usage-card-head"><h4>${name}</h4><${StatusChip} status=${status} labels=${SOURCE_STATUS} /></div>
    <p class="muted small">${app.plan ? t("usage.plan_prefix") + app.plan : t("usage.no_plan")} · ${claudeWithoutSnapshot ? t("usage.claude_no_measurement") : app.source?.kind ? t("usage.source_prefix") + app.source.kind : t("usage.no_source")}${app.source?.observed_at ? t("usage.observed") + timestamp(app.source.observed_at) : ""}</p>
    ${windows.length ? windows.map((window, index) => {
      const label = WINDOW_LABEL[window.window] ? t(WINDOW_LABEL[window.window]) : window.window || t("usage.window");
      const stateAllowsPercent = window.state === "current" || window.state === "limit_reached";
      const copilotWithoutCap = app.app === "copilot" && credits?.cap == null;
      const value = window.state === "window_reset" || !stateAllowsPercent || claudeWithoutSnapshot || copilotWithoutCap
        ? null : window.used_percent;
      const state = STATE_LABEL[window.state] ? t(STATE_LABEL[window.state]) : t("usage.unknown_state");
      const reset = window.state === "window_reset";
      const noCap = copilotWithoutCap;
      const description = reset ? state : noCap ? t("usage.no_cap") : claudeWithoutSnapshot && window.state === "current"
        ? t("usage.claude_no_pct")
        : state + (window.resets_at ? t("usage.reset_at") + timestamp(window.resets_at) : "");
      const elapsed = window.elapsed_percent;
      const pace = typeof value === "number" && typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed > 0
        ? t("usage.pace_estimate") + pct(value / elapsed * 100) + "." : t("usage.pace_unavailable");
      return html`<div key=${window.window + "-" + index} class="usage-window">
        ${typeof value === "number" ? html`<${Progress} label=${label} value=${value} tone=${window.state === "limit_reached" ? "danger" : "active"}
          valueText=${pct(value)} />` : html`<div class="usage-no-bar"><strong>${label}:</strong> ${t("usage.percent_unavailable")}</div>`}
        <p class="muted small">${description}</p>
        <p class="muted small">${pace}</p>
      </div>`;
    }) : html`<p>${t("usage.no_usage")}</p>`}
    ${app.app === "copilot" && credits && html`<p class="usage-credit"><strong>${t("usage.copilot_credits")}</strong>
      ${credits.used == null ? t("usage.credit_use_unavailable") : amount(credits.used) + t("usage.credits")}
      ${credits.cap == null ? t("usage.no_credit_cap") : credits.used == null ? t("usage.of") + amount(credits.cap) + t("usage.credits") : t("usage.of") + amount(credits.cap) + " (" + pct(credits.cap > 0 ? credits.used / credits.cap * 100 : null) + ")"}</p>`}
    ${Array.isArray(app.notes) && app.notes.length > 0 && html`<div class="usage-disclosure"><details>
      <summary ...${helpFor("usage.plan_notes", { scope: app.app || "app" })}>${t("usage.source_notes")}${app.notes.length})</summary>
      <ul>${app.notes.map((note, i) => html`<li key=${i}>${note}</li>`)}</ul>
    </details><${Help} topic="usage.plan_notes" scope=${app.app || "app"} /></div>`}
  </article>`;
}

function Plans({ data }) {
  const apps = Array.isArray(data?.apps) ? data.apps : [];
  return html`<section aria-labelledby="usage-plans-title">
    <h3 id="usage-plans-title">${t("usage.plan_heading")}</h3><${Help} topic="usage.plans" />
    <p class="muted small">${data?.generated_at ? t("usage.measured") + timestamp(data.generated_at) : t("usage.awaiting_measurement")}</p>
    <div class="usage-grid">${apps.length ? apps.map((app) => html`<${PlanCard} key=${app.app} app=${app} />`) : html`<p class="muted">${t("usage.plans_error")}</p>`}</div>
  </section>`;
}

export function TokenRows({ result, label }) {
  const rows = Array.isArray(result?.rows) ? result.rows : [];
  const totals = result?.totals || {};
  const apps = result?.apps || {};
  const totalTokens = amount(totals.total);
  return html`<section aria-labelledby=${"usage-token-" + label + "-title"}>
    <h3 id=${"usage-token-" + label + "-title"}>${label === "today" ? t("usage.today") : t("usage.this_week")}</h3>
    <p class="muted small">${result?.window ? timestamp(result.window.since) + " – " + timestamp(result.window.until) : t("usage.no_interval")}</p>
    <p><strong>${t("usage.total_tokens")}</strong> ${totalTokens === t("usage.unavailable") ? t("usage.not_available_lower") : totalTokens + " tokens"} · ${amount(totals.events)} ${t("usage.events")}</p>
    <div class="usage-table-wrap"><table class="usage-table">
      <caption>${t("usage.table_caption")}</caption>
      <thead><tr><th scope="col">${t("usage.app")}</th><th scope="col">${t("usage.model_level")}</th><th scope="col">${t("usage.type")}</th><th scope="col">${t("usage.tokens")}</th><th scope="col">${t("usage.events_heading")}</th></tr></thead>
      <tbody>${rows.length ? rows.map((row, index) => html`<tr key=${index}>
        <td>${APPS[row.app] || row.app || t("usage.no_app")}</td><td>${row.model || t("usage.unknown_model")} ${levelInfo(row.level) ? html`<${LevelBadge} level=${row.level} />` : html`<span class="muted small">${t("usage.unclassified_level")}</span>`}</td>
        <td>${row.role === "delegated" ? t("usage.delegated") : row.role === "primary" ? t("usage.primary") : row.role || t("usage.unclassified")}</td>
        <td>${amount(row.total)}</td><td>${amount(row.events)}</td>
      </tr>`) : html`<tr><td colspan="5">${t("usage.no_token_events")}</td></tr>`}</tbody>
    </table></div>
    <ul class="usage-source-list">${Object.entries(apps).map(([app, info]) => html`<li key=${app}><strong>${APPS[app] || app}:</strong> ${info.status === "ok" ? amount(info.events) + " " + t("usage.events") : info.status === "disabled" ? t("usage.source_disabled") : t("usage.source_unavailable")}${Array.isArray(info.warnings) && info.warnings.length ? " · " + info.warnings.join("; ") : ""}</li>`)}</ul>
  </section>`;
}

function Global({ today, week, period, setPeriod }) {
  const tabs = [{ id: "today", label: t("usage.today") }, { id: "week", label: t("usage.week") }];
  return html`<section aria-labelledby="usage-global-title">
    <h3 id="usage-global-title">${t("usage.global_heading")}</h3><${Help} topic="usage.global" />
    <${Tabs} id="usageperiod" label=${t("usage.period")} tabs=${tabs} selected=${period} onSelect=${setPeriod}
      ...${helpFor("usage.period")} after=${html`<${Help} topic="usage.period" compact=${true} />`}>
      <${TokenRows} result=${period === "today" ? today : week} label=${period} />
    <//>
  </section>`;
}

export function CostGroup({ item, title, scope = "total" }) {
  const known = item?.cost_known_records || 0;
  const unknown = item?.cost_unknown_records || 0;
  const evidence = Array.isArray(item?.evidence) ? item.evidence : [];
  const hasEstimate = typeof item?.estimated_cost_usd === "number" && Number.isFinite(item.estimated_cost_usd);
  return html`<article class="usage-card"><h4>${title}</h4>
    <p class="usage-cost">${hasEstimate ? html`${money(item.estimated_cost_usd)} <span class="muted">${t("usage.estimated")}</span>` : t("usage.no_amount")}</p>
    <p>${known} ${t("usage.priced_records")} · ${unknown} ${t("usage.unknown_records")}</p>
    ${unknown > 0 && html`<div class="usage-disclosure"><details>
      <summary ...${helpFor("usage.cost_reasons", { scope })}>${t("usage.missing_amount_reason")}</summary>
      <ul>${Object.entries(item.cost_unknown_reasons || {}).map(([reason, count]) => html`<li key=${reason}>${reason}: ${count}</li>`)}</ul>
    </details><${Help} topic="usage.cost_reasons" scope=${scope} /></div>`}
    ${evidence.length ? html`<div class="usage-disclosure"><details>
      <summary ...${helpFor("usage.cost_evidence", { scope })}>${t("usage.view_evidence")}${evidence.length})</summary>
      <ul>${evidence.map((entry, i) => html`<li key=${i}>${entry.model || t("usage.model")} · ${entry.currency || t("usage.unknown_currency")} · ${entry.source || t("usage.unknown_source")} · ${entry.provenance || t("usage.no_provenance")}</li>`)}</ul>
    </details><${Help} topic="usage.cost_evidence" scope=${scope} /></div>` : html`<p class="muted small">${t("usage.price_evidence")}</p>`}
  </article>`;
}

function Costs({ data }) {
  const totals = data?.totals || {};
  const byMonth = Array.isArray(data?.by_month) ? data.by_month : [];
  const byModel = Array.isArray(data?.by_model) ? data.by_model : [];
  return html`<section aria-labelledby="usage-costs-title">
    <h3 id="usage-costs-title">${t("usage.costs_heading")}</h3><${Help} topic="usage.costs" />
    <p class="muted small">${data?.window ? timestamp(data.window.since) + " – " + timestamp(data.window.until) : t("usage.no_interval")} · ${t("usage.costs_estimated")}</p>
    <${CostGroup} item=${totals} title=${t("usage.total_period")} scope="total" />
    <h4>${t("usage.by_month")}</h4><div class="usage-grid">${byMonth.length ? byMonth.map((item, index) => html`<${CostGroup} key=${item.month || index} item=${item} title=${item.month || t("usage.month")} scope=${"month-" + (item.month || index)} />`) : html`<p>${t("usage.no_period_costs")}</p>`}</div>
    <h4>${t("usage.by_model")}</h4><div class="usage-grid">${byModel.length ? byModel.map((item, index) => html`<${CostGroup} key=${item.model || index} item=${item} title=${item.model || t("usage.unknown_model")} scope=${"model-" + (item.model || index)} />`) : html`<p>${t("usage.no_cost_calls")}</p>`}</div>
    <p class="notice">${t("usage.budget_notice")}</p>
  </section>`;
}

export function Usage({ token }) {
  const [data, setData] = useState({ loading: true, plans: null, today: null, week: null, costs: null, errors: [] });
  const [period, setPeriod] = useState("today");
  const [view, setView] = useState("plans");
  const [refreshing, setRefreshing] = useState(false);
  async function refresh() {
    setRefreshing(true);
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
    const until = now.toISOString();
    const weekStart = new Date(now.valueOf() - 7 * 24 * 60 * 60 * 1000).toISOString();
    const requests = await Promise.allSettled([
      apiGet("/gui/api/usage/plans", token),
      apiGet("/gui/api/usage/tokens?since=" + encodeURIComponent(start) + "&until=" + encodeURIComponent(until), token),
      apiGet("/gui/api/usage/tokens?since=" + encodeURIComponent(weekStart) + "&until=" + encodeURIComponent(until), token),
      apiGet("/gui/api/usage/costs", token),
    ]);
    const previous = data;
    const value = (index, key) => requests[index].status === "fulfilled" ? requests[index].value : previous[key];
    setData({
      loading: false, plans: value(0, "plans"), today: value(1, "today"), week: value(2, "week"), costs: value(3, "costs"),
      errors: requests.map((result, index) => result.status === "rejected" ? [t("usage.plans_error_name"), t("usage.today_tokens"), t("usage.week_tokens"), t("usage.costs_error_name")][index] : null).filter(Boolean),
    });
    setRefreshing(false);
  }
  useEffect(() => { refresh(); }, [token]);
  const extra = registeredUsageSections();   // tabs that extensions add (CNT-T06); none unless one registered
  const extraSection = extra.find((section) => section.id === view);
  return html`<div class="usage-view">
    <h3 id="usage-title">${t("usage.title")}</h3><${Help} topic="usage.section" always=${true} />
    <div class="usage-title-row"><p class="muted">${t("usage.intro")}</p>
      <div class="usage-refresh-control" role="group" aria-label=${t("usage.refresh_aria")}>
        <button type="button" class="btn" disabled=${refreshing} ...${helpFor("usage.refresh")} onClick=${refresh}>${refreshing ? t("usage.refreshing") : t("usage.refresh")}</button>
        <${Help} topic="usage.refresh" compact=${true} />
      </div>
    </div>
    ${data.errors.length > 0 && html`<p class="message error" role="alert">${t("usage.update_error", { sources: data.errors.join(", ") })}</p>`}
    ${data.loading && !data.plans && !data.today && !data.costs ? html`<p role="status" aria-live="polite">${t("usage.loading")}</p>`
      : html`<${Tabs} id="usageview" label=${t("usage.sections")} tabs=${[
        { id: "plans", label: t("usage.plans_tab") }, { id: "global", label: t("usage.global_tab") }, { id: "costs", label: t("usage.costs_heading") },
        ...extra.map((section) => ({ id: section.id, label: section.label })),
      ]} selected=${view} onSelect=${setView} ...${helpFor("usage.view")} after=${html`<${Help} topic="usage.view" compact=${true} />`}>
        ${view === "plans" ? html`<${Plans} data=${data.plans} />` : view === "global" ? html`<${Global} today=${data.today} week=${data.week} period=${period} setPeriod=${setPeriod} />` : extraSection ? html`<${extraSection.component} token=${token} />` : html`<${Costs} data=${data.costs} />`}
      <//>`}
  </div>`;
}
