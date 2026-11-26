// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Usage view: presents local measurements from the usage API as untrusted text.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPut } from "./api.js";
import { Help, helpFor } from "./help.js";
import { t, currentLanguage } from "./i18n.js";
import { registeredUsageSections } from "./extensions.js";
import { currentMode, onModeChange } from "./gui-mode.js";
import { navigate } from "./route.js";
import { BUDGET_MODES, budgetChanged, openTab, budgetPayload, budgetRows, budgetValid, paceText, parseAmount, sourceText, usagePace, usageTabFromHash } from "./usage-model.js";
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

// «Tu ritmo» (USO-T07): one sentence per app that has a plan, worked out from used_percent and elapsed_percent. No model.
export function PaceSentences({ plans }) {
  const items = usagePace(plans);
  return html`<section class="usage-pace" aria-label=${t("usage.pace.title")}>
    <h3>${t("usage.pace.title")}</h3>
    ${items.length ? html`<ul class="usage-pace-list">${items.map((item) => html`<li key=${item.app} class=${"usage-pace-item usage-pace-" + item.kind}>
      <strong>${APPS[item.app] || item.app || t("usage.app")}</strong> <span>${paceText(item)}</span></li>`)}</ul>`
      : html`<p class="muted">${t("usage.pace.no_data")}</p>`}
  </section>`;
}

// The budget of each API provider, changed here (USO-T07). An empty amount is «sin presupuesto», 0 is «no gastar». The form
// sends nothing while it matches what the server holds. Saving replaces the budgets that came from the environment.
export function BudgetSettings({ token }) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const [rows, setRows] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  async function load() {
    try {
      const data = await apiGet("/gui/api/settings/budget", token);
      setState({ loading: false, data, error: "" });
      setRows(budgetRows(data));
    } catch (failure) {
      setState((previous) => ({ ...previous, loading: false, error: failure instanceof ApiError ? failure.message : String(failure) }));
    }
  }
  useEffect(() => { load(); }, [token]);
  const original = state.data ? budgetRows(state.data) : [];
  const valid = budgetValid(rows);
  const changed = budgetChanged(rows, original);
  const edit = (provider, change) => { setSaved(false); setRows(rows.map((row) => row.provider === provider ? { ...row, ...change } : row)); };
  async function save(event) {
    event.preventDefault();
    if (!changed || !valid || saving) return;
    setSaving(true);
    try {
      const data = await apiPut("/gui/api/settings/budget", token, budgetPayload(rows));
      setState({ loading: false, data, error: "" });
      setRows(budgetRows(data));
      setSaved(true);
    } catch (failure) {
      setSaved(false);
      setState((previous) => ({ ...previous, error: failure instanceof ApiError ? failure.message : String(failure) }));
    }
    setSaving(false);
  }
  const data = state.data;
  const environmentBudgets = data && data.source === "environment" && Array.isArray(data.budgets) ? data.budgets : [];
  return html`<section class="usage-budget" aria-label=${t("usage.budget.title")}>
    <h3>${t("usage.budget.title")}</h3><${Help} topic="usage.costs" scope="budget" />
    ${state.error && html`<p class="message error" role="alert">${t("usage.budget.error", { message: state.error })}</p>`}
    ${state.loading ? html`<p role="status">${t("usage.budget.loading")}</p>` : data && html`<form class="usage-budget-form" onSubmit=${save}>
      <p class="muted small usage-budget-source">${t("usage.budget.source", { source: sourceText(data.source) })}</p>
      ${environmentBudgets.length > 0 && html`<div class="usage-budget-environment"><p class="muted small">${t("usage.budget.environment_note")}</p>
        <ul>${environmentBudgets.map((budget) => html`<li key=${budget.id}>${budget.id} · ${budget.scope} · ${money(budget.amount)} · ${t("usage.budget.mode." + budget.mode)}</li>`)}</ul></div>`}
      <div class="usage-budget-rows">${rows.map((row) => {
        const parsed = parseAmount(row.amount);
        const id = "usage-budget-" + row.provider;
        return html`<div class="usage-budget-row" key=${row.provider}>
          <label for=${id + "-amount"}>${row.provider} · ${t("usage.budget.amount")}</label>
          <input id=${id + "-amount"} type="text" inputmode="decimal" autocomplete="off" value=${row.amount} placeholder=${t("usage.budget.no_budget")}
            aria-invalid=${parsed.valid ? "false" : "true"} ...${helpFor("usage.costs", { scope: "budget" })} onInput=${(event) => edit(row.provider, { amount: event.currentTarget.value })} />
          <label for=${id + "-mode"}>${t("usage.budget.when_reached")}</label>
          <select id=${id + "-mode"} value=${row.mode} ...${helpFor("usage.costs", { scope: "budget" })} onChange=${(event) => edit(row.provider, { mode: event.currentTarget.value })}>
            ${BUDGET_MODES.map((mode) => html`<option key=${mode} value=${mode} selected=${row.mode === mode}>${t("usage.budget.mode." + mode)}</option>`)}
          </select>
          ${!parsed.valid && html`<p class="message error small" role="alert">${t("usage.budget.invalid_amount", { provider: row.provider })}</p>`}
        </div>`;
      })}</div>
      <p class="muted small">${t("usage.budget.amount_note")}</p>
      <p class="muted small">${t("usage.budget.mode_note")}</p>
      <div class="usage-budget-actions"><button type="submit" class="btn" disabled=${!changed || !valid || saving} ...${helpFor("usage.costs", { scope: "budget" })}>${saving ? t("usage.budget.saving") : t("usage.budget.save")}</button>
        ${!changed && valid && html`<span class="muted small">${t("usage.budget.no_changes")}</span>`}
        ${saved && html`<span class="muted small" role="status">${t("usage.budget.saved")}</span>`}</div>
    </form>`}
  </section>`;
}

function Costs({ data, token }) {
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
    <${BudgetSettings} token=${token} />
  </section>`;
}

// The tab comes from the address: #usage/plans, #usage/global, #usage/costs, #usage/continuity. #usage alone opens the first tab
// in the advanced mode and leaves every detail folded in the simple mode.
function useUsageTab() {
  const read = () => typeof location === "undefined" ? "" : usageTabFromHash(location.hash);
  const [tab, setTab] = useState(read);
  useEffect(() => {
    const update = () => setTab(read());
    window.addEventListener("hashchange", update);
    update();
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return tab;
}

export function Usage({ token }) {
  const [data, setData] = useState({ loading: true, plans: null, today: null, week: null, costs: null, errors: [] });
  const [period, setPeriod] = useState("today");
  const addressed = useUsageTab();
  const [mode, setMode] = useState(currentMode);
  useEffect(() => onModeChange(setMode), []);
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
  const tabs = [
    { id: "plans", label: t("usage.plans_tab") }, { id: "global", label: t("usage.global_tab") }, { id: "costs", label: t("usage.costs_heading") },
    ...extra.map((section) => ({ id: section.id, label: section.label })),
  ];
  const advanced = mode === "advanced";
  const view = openTab(mode, addressed, tabs.map((tab) => tab.id));
  const body = (id) => {
    const extraSection = extra.find((section) => section.id === id);
    return id === "plans" ? html`<${Plans} data=${data.plans} />` : id === "global" ? html`<${Global} today=${data.today} week=${data.week} period=${period} setPeriod=${setPeriod} />`
      : extraSection ? html`<${extraSection.component} token=${token} />` : html`<${Costs} data=${data.costs} token=${token} />`;
  };
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
      : html`<${PaceSentences} plans=${data.plans} />
      ${advanced ? html`<${Tabs} id="usageview" label=${t("usage.sections")} tabs=${tabs} selected=${view} onSelect=${(id) => navigate("usage", id)}
        ...${helpFor("usage.view")} after=${html`<${Help} topic="usage.view" compact=${true} />`}>
        ${body(view)}
      <//>` : html`<div class="usage-folds" role="group" aria-label=${t("usage.sections")}>
        ${tabs.map((tab) => {
          const open = view === tab.id;
          return html`<section class="usage-fold" key=${tab.id}>
            <h4 class="usage-fold-title"><button type="button" class="btn secondary usage-fold-toggle" aria-expanded=${open ? "true" : "false"}
              aria-controls=${"usage-fold-" + tab.id} ...${helpFor("usage.view")} onClick=${() => navigate("usage", open ? "" : tab.id)}>
              ${tab.label} · ${open ? t("usage.hide_detail") : t("usage.see_detail")}</button></h4>
            <div id=${"usage-fold-" + tab.id} class="usage-fold-body" hidden=${!open}>${open ? body(tab.id) : null}</div>
          </section>`;
        })}
        <${Help} topic="usage.view" compact=${true} />
      </div>`}`}
  </div>`;
}
