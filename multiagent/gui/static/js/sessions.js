// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// "Sesiones": the unified list of Claude, Codex, Copilot sessions and framework runs, plus one session's detail.
// The list stays mounted (hidden) while a detail is open, so filters, loaded pages and scroll survive the trip.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "./api.js";
import { navigate, routeHref } from "./route.js";
import { SessionDetail } from "./session-detail.js";
import {
  ROOTS_HELP, SOURCES, SURFACES, modelsText, relativeTime, sessionTitle, sourceLabel, statusInfo, surfaceLabel,
  tokensText, warningText,
} from "./session-text.js";
import { errorText, fmtDate } from "./text.js";

const PAGE_SIZE = 50;
const TEXT_APPLY_MS = 350;
const CLOCK_TICK_MS = 30000;
const MAX_AUTO_PAGES = 6; // pages fetched in a row while every row of them is a hidden subagent
const EMPTY_FILTERS = { source: "", surface: "", cwd: "", since: "", until: "", text: "" };

// "2026-10-05" (a local day from the date input) -> UTC instant at the start or end of that day.
function dayBound(value, end) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
  if (!match) return "";
  const date = end
    ? new Date(+match[1], +match[2] - 1, +match[3], 23, 59, 59, 999)
    : new Date(+match[1], +match[2] - 1, +match[3], 0, 0, 0, 0);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function listQuery(filters, after) {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (filters.source) query.set("source", filters.source);
  if (filters.surface) query.set("surface", filters.surface);
  if (filters.cwd.trim()) query.set("cwd", filters.cwd.trim());
  if (filters.text.trim()) query.set("text", filters.text.trim());
  const since = dayBound(filters.since, false);
  const until = dayBound(filters.until, true);
  if (since) query.set("since", since);
  if (until) query.set("until", until);
  if (after) query.set("after", after);
  return query.toString();
}

const hasFilters = (filters) => Object.values(filters).some((value) => value !== "");

function Filters({ draft, showSubagents, onChange, onSubmit, onClear, onToggleSubagents }) {
  const field = (key, immediate) => (event) => onChange(key, event.target.value, immediate);
  return html`<form class="filters" role="search" aria-label="Filtrar sesiones" onSubmit=${onSubmit}>
    <div class="filter-grid">
      <div><label for="sf-source">Origen</label>
        <select id="sf-source" value=${draft.source} onChange=${field("source", true)}>
          <option value="">Todos</option>
          ${SOURCES.map((s) => html`<option key=${s.id} value=${s.id}>${s.label}</option>`)}
        </select></div>
      <div><label for="sf-surface">Superficie</label>
        <select id="sf-surface" value=${draft.surface} onChange=${field("surface", true)}>
          <option value="">Todas</option>
          ${SURFACES.map((s) => html`<option key=${s.id} value=${s.id}>${s.label}</option>`)}
        </select></div>
      <div><label for="sf-text">Título contiene</label>
        <input id="sf-text" type="search" maxlength="200" value=${draft.text} onInput=${field("text", false)} /></div>
      <div><label for="sf-cwd">Carpeta (dentro de)</label>
        <input id="sf-cwd" type="text" maxlength="1000" value=${draft.cwd} onInput=${field("cwd", false)} /></div>
      <div><label for="sf-since">Actualizada desde</label>
        <input id="sf-since" type="date" value=${draft.since} onChange=${field("since", true)} /></div>
      <div><label for="sf-until">Actualizada hasta</label>
        <input id="sf-until" type="date" value=${draft.until} onChange=${field("until", true)} /></div>
    </div>
    <div class="actions">
      <label class="check-inline"><input type="checkbox" checked=${showSubagents}
        onChange=${(event) => onToggleSubagents(event.target.checked)} /> Incluir subagentes en la lista</label>
      <button type="submit" class="btn small">Buscar</button>
      <button type="button" class="btn secondary small" onClick=${onClear}>Limpiar filtros</button>
    </div>
  </form>`;
}

function RootsHelp({ hint }) {
  return html`<section class="panel roots-help" aria-labelledby="roots-title">
    <h4 id="roots-title">${ROOTS_HELP.title}</h4>
    <p>${ROOTS_HELP.intro}</p>
    <ol>${ROOTS_HELP.steps.map((step) => html`<li key=${step}>${step}</li>`)}</ol>
    <p>Ejemplo: <code>${ROOTS_HELP.example}</code></p>
    ${hint && html`<p class="muted small">Aviso del servidor: <code>${hint}</code></p>`}
  </section>`;
}

function Row({ session, now }) {
  const info = statusInfo(session.status);
  return html`<li>
    <a class="session-row" href=${routeHref("sessions", session.id)} data-sid=${session.id}>
      <span class="row-top">
        <span class=${"badge src src-" + session.source}>${sourceLabel(session.source)}</span>
        <span class="badge">${surfaceLabel(session.surface)}</span>
        ${session.parent_id && html`<span class="badge">Subagente</span>`}
        <span class="session-title">${sessionTitle(session)}</span>
        <span class=${"badge tone-" + info.tone}>${info.label}</span>
      </span>
      <span class="row-line muted small">Carpeta: ${session.cwd || (session.source === "framework" ? "no aplica" : "no consta")}</span>
      <span class="row-line small">Actualizada ${relativeTime(session.updated_at, now)} · ${fmtDate(session.updated_at)}</span>
      <span class="row-line small">Modelos: ${modelsText(session.models)} · Tokens: ${tokensText(session.tokens, { brief: true })} · Subagentes: ${session.child_count}</span>
    </a>
  </li>`;
}

function SessionList({ token }) {
  const [draft, setDraft] = useState(EMPTY_FILTERS);
  const [applied, setApplied] = useState(EMPTY_FILTERS);
  const [showSubagents, setShowSubagents] = useState(false);
  const [data, setData] = useState({ sessions: [], next: null, hint: null, warnings: [], loaded: false });
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);
  const [now, setNow] = useState(Date.now());
  const ticket = useRef(0);
  const subagentsRef = useRef(showSubagents);
  subagentsRef.current = showSubagents;
  const visible = (rows) => (subagentsRef.current ? rows : rows.filter((s) => !s.parent_id));

  // Fetches from `after`, continuing while a whole page was hidden subagents so the list never looks empty by accident.
  async function fetchRows(filters, after, mine) {
    let cursor = after;
    let rows = [];
    let hint = null;
    let warnings = [];
    let next = null;
    for (let pages = 0; pages < MAX_AUTO_PAGES; pages += 1) {
      const page = await apiGet("/gui/api/sessions?" + listQuery(filters, cursor), token);
      if (mine !== ticket.current) return null;
      hint = page.hint || hint;
      warnings = warnings.concat((page.warnings || []).filter((w) => !warnings.includes(w)));
      rows = rows.concat(page.sessions);
      next = page.next_cursor;
      if (!next || visible(rows).length > 0) break;
      cursor = next;
    }
    return { rows, hint, warnings, next };
  }

  async function loadFirst() {
    const mine = ++ticket.current;
    setLoading(true);
    setLoadingMore(false);
    try {
      const result = await fetchRows(applied, null, mine);
      if (!result) return;
      setData({ sessions: result.rows, next: result.next, hint: result.hint, warnings: result.warnings, loaded: true });
      setError(null);
    } catch (failure) {
      if (mine === ticket.current) setError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    } finally {
      if (mine === ticket.current) setLoading(false);
    }
  }

  async function loadMore() {
    if (loadingMore || loading || !data.next) return;
    const mine = ticket.current;
    setLoadingMore(true);
    try {
      const result = await fetchRows(applied, data.next, mine);
      if (!result) return;
      setData((previous) => {
        const seen = new Set(previous.sessions.map((s) => s.id));
        return {
          ...previous,
          sessions: previous.sessions.concat(result.rows.filter((s) => !seen.has(s.id))),
          next: result.next,
          warnings: previous.warnings.concat(result.warnings.filter((w) => !previous.warnings.includes(w))),
        };
      });
      setError(null);
    } catch (failure) {
      if (mine === ticket.current) setError(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
    } finally {
      if (mine === ticket.current) setLoadingMore(false);
    }
  }

  useEffect(() => { loadFirst(); }, [applied, reload, token]);

  // Text filters apply shortly after the last keystroke; selects and dates apply at once.
  useEffect(() => {
    if (draft === applied) return undefined;
    const timer = setTimeout(() => setApplied(draft), TEXT_APPLY_MS);
    return () => clearTimeout(timer);
  }, [draft]);

  // Keeps "hace N min" honest while the page stays open.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const change = (key, value, immediate) => {
    const next = { ...draft, [key]: value };
    setDraft(next);
    if (immediate) setApplied(next);
  };
  const clear = () => { setDraft(EMPTY_FILTERS); setApplied(EMPTY_FILTERS); };
  const submit = (event) => { event.preventDefault(); setApplied(draft); setReload((value) => value + 1); };

  const rows = visible(data.sessions);
  const noRoots = data.loaded && Boolean(data.hint) && data.sessions.length === 0;
  let summary = "";
  if (loading && !data.loaded) summary = "Cargando sesiones…";
  else if (loading) summary = "Actualizando la lista…";
  else if (data.loaded && !noRoots) summary = rows.length === 0 ? "Sin resultados." : "Mostrando " + rows.length + (rows.length === 1 ? " sesión" : " sesiones") + (data.next ? "; hay más." : ".");

  return html`<section class="session-list" aria-labelledby="sessions-title">
    <div class="row">
      <h3 id="sessions-title" tabindex="-1">Todas las sesiones</h3>
      <button type="button" class="btn secondary small" onClick=${() => setReload((value) => value + 1)} disabled=${loading}>Actualizar la lista</button>
    </div>
    <p class="muted">Sesiones de Claude, Codex y Copilot (solo lectura) y ejecuciones del framework, la más reciente primero.</p>
    <${Filters} draft=${draft} showSubagents=${showSubagents} onChange=${change} onSubmit=${submit} onClear=${clear}
      onToggleSubagents=${setShowSubagents} />
    <p class="muted" role="status" aria-live="polite" aria-busy=${loading ? "true" : "false"}>${summary}</p>
    ${error && html`<p class="message error" role="alert">${errorText(error)}
      <button type="button" class="btn secondary small" onClick=${() => setReload((value) => value + 1)}>Reintentar</button></p>`}
    ${noRoots && html`<${RootsHelp} hint=${data.hint} />`}
    ${data.loaded && !noRoots && rows.length === 0 && !loading && html`<p class="panel">${hasFilters(applied)
      ? "Ninguna sesión coincide con esos filtros. Prueba a quitar alguno."
      : "No se encontraron sesiones en las carpetas permitidas. Si esperabas ver alguna, revisa que sus carpetas estén dentro de gui_session_roots."}</p>`}
    ${data.warnings.length > 0 && !noRoots && html`<details class="panel">
      <summary>Avisos de las fuentes (${data.warnings.length})</summary>
      <ul class="warnings">${data.warnings.map((warning) => html`<li key=${warning} class="note small">${warningText(warning)}</li>`)}</ul>
    </details>`}
    ${rows.length > 0 && html`<ul class="session-rows" aria-label="Sesiones">
      ${rows.map((session) => html`<${Row} key=${session.id} session=${session} now=${now} />`)}
    </ul>`}
    ${data.next && html`<p><button type="button" class="btn secondary" disabled=${loadingMore || loading} onClick=${loadMore}>
      ${loadingMore ? "Cargando…" : "Cargar más"}</button></p>`}
  </section>`;
}

export function Sessions({ token, route, active }) {
  const selectedId = active ? route.id : null;
  const wrapper = useRef(null);
  const previous = useRef(null);

  // Going back to the list puts the focus on the row that was open (or on the list heading).
  useEffect(() => {
    if (active && !selectedId && previous.current && wrapper.current) {
      const rows = wrapper.current.querySelectorAll("a.session-row");
      const row = [...rows].find((link) => link.dataset.sid === previous.current);
      const target = row || wrapper.current.querySelector("#sessions-title");
      if (target) target.focus();
    }
    previous.current = selectedId;
  }, [selectedId, active]);

  return html`<div class="sessions" hidden=${!active} ref=${wrapper}>
    <div hidden=${Boolean(selectedId)}><${SessionList} token=${token} /></div>
    ${selectedId && html`<${SessionDetail} key=${selectedId} token=${token} sessionId=${selectedId}
      onBack=${() => navigate("sessions")} />`}
  </div>`;
}
