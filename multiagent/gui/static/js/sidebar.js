// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Left sidebar (GUI2-T02): "Nueva sesión", search, and sessions and framework runs grouped by project. The
// «Actividades» filter instead shows open project activities and links to their boards. The shell (shell.js) puts it
// inside <nav id="sidebar">. Names, titles and folders are untrusted text: they are only ever rendered as text nodes.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { debounce, subscribe } from "./bus.js";
import { Help, helpFor } from "./help.js";
import { navigate, routeHref, NEW_SECTION } from "./route.js";
import { relativeTime } from "./session-text.js";
import { activityItems, appLabel, buildGroups, countsOf, isCurrent, navigationSections, runItems, sessionItems } from "./sidebar-model.js";
import { readMoreNoticeSeen, readPinnedSections, writeMoreNoticeSeen } from "./shell-state.js";
import { PendingLink } from "./pending.js";
import { Icon } from "./ui/icon.js";
import { StatusChip } from "./ui/status-chip.js";

const SESSION_LIMIT = 100;
const RUN_LIMIT = 50;
const EVENT_DEBOUNCE_MS = 1500; // runs change with the event stream
const REFRESH_MS = 60000;       // sessions of the apps are files: they are looked at again once a minute

// The dot is a StatusChip (ui/status-chip.js): a mark whose shape differs per state, with the words for screen readers.
function Item({ item, current, now }) {
  const meta = item.who
    ? appLabel(item.app) + " · " + item.who + " · " + relativeTime(item.updatedAt, now)
    : appLabel(item.app) + " · " + relativeTime(item.updatedAt, now);
  return html`<li>
    <a class="side-item" href=${routeHref(item.route[0], item.route[1])} aria-current=${current ? "page" : null}
      title=${item.folder || item.title}>
      ${item.dotLabels
        ? html`<${StatusChip} status=${item.dot} labels=${item.dotLabels} variant="dot" />`
        : html`<${StatusChip} status=${item.dot} labels="dot" variant="dot" detail=${item.detail} />`}
      <span class="side-text">
        <span class="side-title">${item.title}</span>
        <span class="side-meta">${meta}</span>
      </span>
    </a>
  </li>`;
}

function Group({ group, route, now }) {
  const counts = countsOf(group);
  return html`<details class="group" open>
    <summary class="group-head" title=${group.folders.join("\n") || group.name}>
      <span class="group-name">${group.name}</span>
      <span class="group-counts">${counts.map((entry) => html`<span key=${entry.app} class=${"count src-" + entry.app}>
        <span aria-hidden="true">${entry.label} ${entry.count}</span><span class="sr-only">${entry.count} de ${entry.label}</span></span>`)}</span>
    </summary>
    <ul class="group-items">
      ${group.items.map((item) => html`<${Item} key=${item.key} item=${item} current=${isCurrent(item, route)} now=${now} />`)}
    </ul>
  </details>`;
}

export function Sidebar({ token, status, route, sections }) {
  const [data, setData] = useState({ sessions: [], runs: [], hint: null, failed: [], loaded: false });
  const [activityData, setActivityData] = useState({ projects: [], loaded: false, loading: false, failed: false });
  const [activitiesMode, setActivitiesMode] = useState(false);
  const [query, setQuery] = useState("");
  const [pinned] = useState(readPinnedSections);
  const [noticeSeen, setNoticeSeen] = useState(readMoreNoticeSeen);
  const [moreOpen, setMoreOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const ticket = useRef(0);
  const activityTicket = useRef(0);

  async function load() {
    const mine = ++ticket.current;
    const [sessions, runs] = await Promise.allSettled([
      apiGet("/gui/api/sessions?limit=" + SESSION_LIMIT, token),
      apiGet("/gui/api/runs?limit=" + RUN_LIMIT, token),
    ]);
    if (mine !== ticket.current) return;
    const ok = (result) => result.status === "fulfilled";
    setData((previous) => ({
      sessions: ok(sessions) ? sessions.value.sessions : previous.sessions,
      hint: ok(sessions) ? sessions.value.hint || null : previous.hint,
      runs: ok(runs) ? runs.value : previous.runs,
      failed: [ok(sessions) ? "" : "sesiones", ok(runs) ? "" : "ejecuciones"].filter(Boolean),
      loaded: true,
    }));
  }

  async function loadActivities() {
    const mine = ++activityTicket.current;
    setActivityData((previous) => ({ ...previous, loading: true, failed: false }));
    try {
      const answer = await apiGet("/gui/api/activities", token);
      if (!Array.isArray(answer?.projects)) throw new Error("Respuesta de actividades inválida.");
      if (mine === activityTicket.current) {
        setActivityData({ projects: answer.projects, loaded: true, loading: false, failed: false });
      }
    } catch (_) {
      if (mine === activityTicket.current) {
        setActivityData({ projects: [], loaded: true, loading: false, failed: true });
      }
    }
  }

  useEffect(() => { load(); }, [token]);
  useEffect(() => { if (status === "connected") load(); }, [status]);
  useEffect(() => {
    const refresh = debounce(load, EVENT_DEBOUNCE_MS);
    const off = subscribe(() => refresh());
    return () => { off(); refresh.cancel(); };
  }, [token]);
  useEffect(() => {
    const timer = setInterval(() => { setNow(Date.now()); load(); }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [token]);
  useEffect(() => {
    if (activitiesMode) loadActivities();
    else activityTicket.current += 1;
  }, [activitiesMode, token]);

  const items = activitiesMode
    ? activityItems(activityData.projects)
    : sessionItems(data.sessions).concat(runItems(data.runs));
  const searching = query.trim() !== "";
  const groups = buildGroups(items, query);
  const shown = groups.reduce((total, group) => total + group.items.length, 0);
  const navigation = navigationSections(sections.filter((s) => !s.group), sections.filter((s) => s.group), pinned);
  const dismissNotice = () => { setNoticeSeen(true); writeMoreNoticeSeen(); };
  useEffect(() => {
    if (route.section !== "sessions" && navigation.more.some((section) => section.id === route.section)) {
      setMoreOpen(true);
      if (!noticeSeen) dismissNotice();
    }
  }, [route.section]);
  const toggleMore = () => {
    setMoreOpen((open) => !open);
    if (!noticeSeen) dismissNotice();
  };

  let summary = "";
  if (activitiesMode && (!activityData.loaded || activityData.loading)) summary = "Cargando las actividades…";
  else if (activitiesMode && activityData.failed) summary = "No se pudieron cargar las actividades.";
  else if (activitiesMode && items.length === 0) summary = "Todavía no hay actividades abiertas.";
  else if (!activitiesMode && !data.loaded) summary = "Cargando la lista…";
  else if (!activitiesMode && items.length === 0) summary = "Todavía no hay sesiones ni ejecuciones.";
  else if (shown === 0) summary = "Nada coincide con «" + query.trim() + "».";
  else summary = shown + (shown === 1 ? " elemento" : " elementos") + " en " + groups.length + (groups.length === 1 ? " proyecto." : " proyectos.");

  return html`<div class="side">
    <div class="side-top">
      <div class="side-new">
        <button type="button" class="btn side-new-btn" data-nav aria-current=${route.section === NEW_SECTION ? "page" : null}
          ...${helpFor("shell.new_session")} onClick=${() => navigate(NEW_SECTION)}><span aria-hidden="true">+ </span>Nueva sesión</button>
        <${Help} topic="shell.new_session" compact=${true} />
      </div>
      <div class="side-search" role="search">
        <label for="side-search">Buscar</label>
        <div class="side-search-row">
          <input id="side-search" type="search" maxlength="100" autocomplete="off" placeholder="Sesión, proyecto, estado…"
            ...${helpFor("shell.search")} value=${query} onInput=${(event) => setQuery(event.target.value)} />
          <${Help} topic="shell.search" compact=${true} />
        </div>
        <button type="button" class="btn secondary" aria-pressed=${activitiesMode ? "true" : "false"}
          ...${helpFor("shell.activities")} onClick=${() => setActivitiesMode((enabled) => !enabled)}>Actividades</button>
        <${Help} topic="shell.activities" compact=${true} />
      </div>
      <div class="views" aria-label="Secciones">
        <div class="side-pending"><${PendingLink} current=${route.section === "pending" || navigation.pending.some((section) => section.id === route.section)} /></div>
        ${navigation.primary.map((s) => html`<button key=${s.id} type="button" data-nav data-help-exempt="navigation"
          aria-current=${s.id === route.section ? "page" : null} onClick=${() => navigate(s.id)}>
          <${Icon} name=${s.icon} />${s.label}</button>`)}
        <button type="button" class="side-more-button" aria-expanded=${moreOpen ? "true" : "false"} aria-controls="side-more"
          ...${helpFor("shell.more")} onClick=${toggleMore}><${Icon} name="dots" />Más</button>
        <${Help} topic="shell.more" compact=${true} />
        ${!noticeSeen && html`<div class="side-more-notice">
          <p>Las demás secciones están en «Más». Puedes fijarlas en la barra.</p>
          <button type="button" class="btn secondary" ...${helpFor("shell.more_notice")} onClick=${dismissNotice}>Entendido</button>
          <${Help} topic="shell.more_notice" compact=${true} />
        </div>`}
        <div id="side-more" class="side-more-list" hidden=${!moreOpen}>
          ${navigation.more.map((s) => html`<button key=${s.id} type="button" data-nav data-help-exempt="navigation"
            aria-current=${s.id === route.section ? "page" : null} onClick=${() => navigate(s.id)}>
            <${Icon} name=${s.icon} />${s.label}</button>`)}
        </div>
      </div>
    </div>
    <div class="side-sessions-head"><span>Sesiones</span><button type="button" data-nav data-help-exempt="navigation"
      aria-current=${route.section === "sessions" ? "page" : null} onClick=${() => navigate("sessions")}>Ver todas</button></div>
    <p class="muted small side-summary" aria-live="polite"
      aria-busy=${activitiesMode ? (activityData.loaded && !activityData.loading ? "false" : "true") : (data.loaded ? "false" : "true")}>${summary}</p>
    ${activitiesMode && activityData.failed && html`<p class="message error small" role="alert">No se pudieron leer las actividades. Vuelve a activar el filtro para reintentarlo.</p>`}
    ${!activitiesMode && data.failed.length > 0 && html`<p class="message error small" role="alert">No se pudo leer la lista de ${data.failed.join(" ni de ")}; se reintentará sola.</p>`}
    ${!activitiesMode && data.loaded && data.hint && data.sessions.length === 0 && html`<p class="muted small">Las sesiones de Claude, Codex y Copilot necesitan carpetas
      permitidas (gui_session_roots). En «Sesiones» se explica cómo.</p>`}
    <div class="side-groups">
      ${groups.map((group) => html`<${Group} key=${group.key + (searching ? "|q" : "")} group=${group} route=${route} now=${now} />`)}
    </div>
  </div>`;
}
