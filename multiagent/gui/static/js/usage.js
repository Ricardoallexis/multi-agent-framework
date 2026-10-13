// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Vista de uso: presenta mediciones locales de la API de uso como texto no confiable.
import { html, useEffect, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { Progress, Tabs, LevelBadge, StatusChip, levelInfo } from "./ui/index.js";

const APPS = { claude: "Claude", codex: "Codex", copilot: "Copilot" };
const SOURCE_STATUS = {
  ok: { text: "Datos disponibles", tone: "success" },
  partial: { text: "Datos parciales", tone: "warning" },
  not_available: { text: "Sin datos", tone: "neutral" },
  disabled: { text: "Desactivada", tone: "muted" },
};
const WINDOW_LABEL = { five_hour: "Cinco horas", weekly: "Semana", model_weekly: "Semana por modelo", monthly: "Mes", spend: "Gasto" };
const STATE_LABEL = {
  current: "En curso",
  window_reset: "La ventana se reinició; el porcentaje actual no está disponible",
  limit_reached: "Se alcanzó el límite",
  unknown: "Sin dato",
};
const NUMBER = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 1 });
const money = (value) => typeof value === "number" && Number.isFinite(value)
  ? new Intl.NumberFormat("es-MX", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(value)
  : "Sin importe conocido";
const timestamp = (value) => {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Fecha no disponible" : new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(date);
};
const pct = (value) => typeof value === "number" && Number.isFinite(value) ? NUMBER.format(value) + " %" : "No disponible";
const amount = (value) => typeof value === "number" && Number.isFinite(value) ? NUMBER.format(value) : "No disponible";

export function PlanCard({ app }) {
  const name = APPS[app.app] || app.app || "Aplicación";
  if (app.status === "disabled") return html`<article class="usage-card"><h4>${name}</h4><p>Esta aplicación está desactivada en la vista de sesiones.</p></article>`;
  const windows = Array.isArray(app.windows) ? app.windows : [];
  const credits = app.credits;
  const claudeWithoutSnapshot = app.app === "claude" && !app.source;
  const status = SOURCE_STATUS[app.status] ? app.status : "not_available";
  return html`<article class="usage-card">
    <div class="usage-card-head"><h4>${name}</h4><${StatusChip} status=${status} labels=${SOURCE_STATUS} /></div>
    <p class="muted small">${app.plan ? "Plan: " + app.plan : "Plan sin identificar"} · ${claudeWithoutSnapshot ? "No hay una medición reciente de Claude." : app.source?.kind ? "Fuente: " + app.source.kind : "Fuente sin identificar"}${app.source?.observed_at ? " · observado " + timestamp(app.source.observed_at) : ""}</p>
    ${windows.length ? windows.map((window, index) => {
      const label = WINDOW_LABEL[window.window] || window.window || "Ventana";
      const stateAllowsPercent = window.state === "current" || window.state === "limit_reached";
      const copilotWithoutCap = app.app === "copilot" && credits?.cap == null;
      const value = window.state === "window_reset" || !stateAllowsPercent || claudeWithoutSnapshot || copilotWithoutCap
        ? null : window.used_percent;
      const state = STATE_LABEL[window.state] || "Estado desconocido";
      const reset = window.state === "window_reset";
      const noCap = copilotWithoutCap;
      const description = reset ? state : noCap ? "Porcentaje no disponible: no hay un tope configurado." : claudeWithoutSnapshot && window.state === "current"
        ? "No hay una medición reciente de Claude; el porcentaje actual no está disponible."
        : state + (window.resets_at ? " · reinicia " + timestamp(window.resets_at) : "");
      const elapsed = window.elapsed_percent;
      const pace = typeof value === "number" && typeof elapsed === "number" && Number.isFinite(elapsed) && elapsed > 0
        ? "Ritmo estimado al reinicio: " + pct(value / elapsed * 100) + "." : "Ritmo al reinicio: no disponible.";
      return html`<div key=${window.window + "-" + index} class="usage-window">
        ${typeof value === "number" ? html`<${Progress} label=${label} value=${value} tone=${window.state === "limit_reached" ? "danger" : "active"}
          valueText=${pct(value)} />` : html`<div class="usage-no-bar"><strong>${label}:</strong> porcentaje no disponible.</div>`}
        <p class="muted small">${description}</p>
        <p class="muted small">${pace}</p>
      </div>`;
    }) : html`<p>No hay datos de uso disponibles.</p>`}
    ${app.app === "copilot" && credits && html`<p class="usage-credit"><strong>Créditos de Copilot:</strong>
      ${credits.used == null ? "consumo no disponible" : amount(credits.used) + " créditos"}
      ${credits.cap == null ? " · sin tope configurado; no se calcula porcentaje" : credits.used == null ? " de " + amount(credits.cap) + " créditos" : " de " + amount(credits.cap) + " (" + pct(credits.cap > 0 ? credits.used / credits.cap * 100 : null) + ")"}</p>`}
    ${Array.isArray(app.notes) && app.notes.length > 0 && html`<div class="usage-disclosure"><details>
      <summary ...${helpFor("usage.plan_notes", { scope: app.app || "app" })}>Notas de la fuente (${app.notes.length})</summary>
      <ul>${app.notes.map((note, i) => html`<li key=${i}>${note}</li>`)}</ul>
    </details><${Help} topic="usage.plan_notes" scope=${app.app || "app"} /></div>`}
  </article>`;
}

function Plans({ data }) {
  const apps = Array.isArray(data?.apps) ? data.apps : [];
  return html`<section aria-labelledby="usage-plans-title">
    <h3 id="usage-plans-title">Planes por aplicación</h3><${Help} topic="usage.plans" />
    <p class="muted small">${data?.generated_at ? "Medido " + timestamp(data.generated_at) : "Esperando la primera medición."}</p>
    <div class="usage-grid">${apps.length ? apps.map((app) => html`<${PlanCard} key=${app.app} app=${app} />`) : html`<p class="muted">No se pudo leer el estado de los planes.</p>`}</div>
  </section>`;
}

export function TokenRows({ result, label }) {
  const rows = Array.isArray(result?.rows) ? result.rows : [];
  const totals = result?.totals || {};
  const apps = result?.apps || {};
  const totalTokens = amount(totals.total);
  return html`<section aria-labelledby=${"usage-token-" + label + "-title"}>
    <h3 id=${"usage-token-" + label + "-title"}>${label === "today" ? "Hoy" : "Esta semana"}</h3>
    <p class="muted small">${result?.window ? timestamp(result.window.since) + " – " + timestamp(result.window.until) : "Intervalo sin datos"}</p>
    <p><strong>Total de tokens:</strong> ${totalTokens === "No disponible" ? "no disponible" : totalTokens + " tokens"} · ${amount(totals.events)} eventos</p>
    <div class="usage-table-wrap"><table class="usage-table">
      <caption>Consumo por aplicación, modelo, nivel y tipo de agente</caption>
      <thead><tr><th scope="col">Aplicación</th><th scope="col">Modelo y nivel</th><th scope="col">Tipo</th><th scope="col">Tokens</th><th scope="col">Eventos</th></tr></thead>
      <tbody>${rows.length ? rows.map((row, index) => html`<tr key=${index}>
        <td>${APPS[row.app] || row.app || "Sin aplicación"}</td><td>${row.model || "Modelo desconocido"} ${levelInfo(row.level) ? html`<${LevelBadge} level=${row.level} />` : html`<span class="muted small">nivel sin clasificar</span>`}</td>
        <td>${row.role === "delegated" ? "Delegado" : row.role === "primary" ? "Principal" : row.role || "Sin clasificar"}</td>
        <td>${amount(row.total)}</td><td>${amount(row.events)}</td>
      </tr>`) : html`<tr><td colspan="5">No hay eventos con tokens en este intervalo.</td></tr>`}</tbody>
    </table></div>
    <ul class="usage-source-list">${Object.entries(apps).map(([app, info]) => html`<li key=${app}><strong>${APPS[app] || app}:</strong> ${info.status === "ok" ? amount(info.events) + " eventos" : info.status === "disabled" ? "desactivada" : "fuente no disponible"}${Array.isArray(info.warnings) && info.warnings.length ? " · " + info.warnings.join("; ") : ""}</li>`)}</ul>
  </section>`;
}

function Global({ today, week, period, setPeriod }) {
  const tabs = [{ id: "today", label: "Hoy" }, { id: "week", label: "Semana" }];
  return html`<section aria-labelledby="usage-global-title">
    <h3 id="usage-global-title">Consumo global</h3><${Help} topic="usage.global" />
    <${Tabs} id="usageperiod" label="Intervalo del consumo" tabs=${tabs} selected=${period} onSelect=${setPeriod}
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
    <p class="usage-cost">${hasEstimate ? html`${money(item.estimated_cost_usd)} <span class="muted">estimado</span>` : "Sin importe conocido"}</p>
    <p>${known} registros con precio · ${unknown} sin importe conocido</p>
    ${unknown > 0 && html`<div class="usage-disclosure"><details>
      <summary ...${helpFor("usage.cost_reasons", { scope })}>Por qué falta el importe</summary>
      <ul>${Object.entries(item.cost_unknown_reasons || {}).map(([reason, count]) => html`<li key=${reason}>${reason}: ${count}</li>`)}</ul>
    </details><${Help} topic="usage.cost_reasons" scope=${scope} /></div>`}
    ${evidence.length ? html`<div class="usage-disclosure"><details>
      <summary ...${helpFor("usage.cost_evidence", { scope })}>Ver evidencia de precios (${evidence.length})</summary>
      <ul>${evidence.map((entry, i) => html`<li key=${i}>${entry.model || "Modelo"} · ${entry.currency || "moneda desconocida"} · ${entry.source || "fuente desconocida"} · ${entry.provenance || "procedencia no indicada"}</li>`)}</ul>
    </details><${Help} topic="usage.cost_evidence" scope=${scope} /></div>` : html`<p class="muted small">No hay evidencia de precio para estos registros.</p>`}
  </article>`;
}

function Costs({ data }) {
  const totals = data?.totals || {};
  const byMonth = Array.isArray(data?.by_month) ? data.by_month : [];
  const byModel = Array.isArray(data?.by_model) ? data.by_model : [];
  return html`<section aria-labelledby="usage-costs-title">
    <h3 id="usage-costs-title">API y presupuesto</h3><${Help} topic="usage.costs" />
    <p class="muted small">${data?.window ? timestamp(data.window.since) + " – " + timestamp(data.window.until) : "Intervalo sin datos"} · Los costos son estimaciones.</p>
    <${CostGroup} item=${totals} title="Total del período" scope="total" />
    <h4>Por mes</h4><div class="usage-grid">${byMonth.length ? byMonth.map((item, index) => html`<${CostGroup} key=${item.month || index} item=${item} title=${item.month || "Mes"} scope=${"month-" + (item.month || index)} />`) : html`<p>No hay costos registrados en este período.</p>`}</div>
    <h4>Por modelo</h4><div class="usage-grid">${byModel.length ? byModel.map((item, index) => html`<${CostGroup} key=${item.model || index} item=${item} title=${item.model || "Modelo desconocido"} scope=${"model-" + (item.model || index)} />`) : html`<p>No hay llamadas con costo registradas.</p>`}</div>
    <p class="notice">El servicio de uso todavía no expone un presupuesto ni un modo de aviso o corte. Aquí se muestran costos estimados y registros desconocidos por separado.</p>
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
      errors: requests.map((result, index) => result.status === "rejected" ? ["planes", "tokens de hoy", "tokens de la semana", "costos"][index] : null).filter(Boolean),
    });
    setRefreshing(false);
  }
  useEffect(() => { refresh(); }, [token]);
  return html`<div class="usage-view">
    <h3 id="usage-title">Datos de uso</h3><${Help} topic="usage.section" />
    <div class="usage-title-row"><p class="muted">Uso local medido por aplicación y por el framework.</p>
      <div class="usage-refresh-control" role="group" aria-label="Actualizar datos de uso">
        <button type="button" class="btn" disabled=${refreshing} ...${helpFor("usage.refresh")} onClick=${refresh}>${refreshing ? "Actualizando…" : "Actualizar"}</button>
        <${Help} topic="usage.refresh" compact=${true} />
      </div>
    </div>
    ${data.errors.length > 0 && html`<p class="message error" role="alert">No se pudieron actualizar: ${data.errors.join(", ")}. Se conserva la última respuesta disponible.</p>`}
    ${data.loading && !data.plans && !data.today && !data.costs ? html`<p role="status" aria-live="polite">Cargando datos de uso…</p>`
      : html`<${Tabs} id="usageview" label="Secciones de uso" tabs=${[
        { id: "plans", label: "Planes" }, { id: "global", label: "Global" }, { id: "costs", label: "API y presupuesto" },
      ]} selected=${view} onSelect=${setView} ...${helpFor("usage.view")} after=${html`<${Help} topic="usage.view" compact=${true} />`}>
        ${view === "plans" ? html`<${Plans} data=${data.plans} />` : view === "global" ? html`<${Global} today=${data.today} week=${data.week} period=${period} setPeriod=${setPeriod} />` : html`<${Costs} data=${data.costs} />`}
      <//>`}
  </div>`;
}
