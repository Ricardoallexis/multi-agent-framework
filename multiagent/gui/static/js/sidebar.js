// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Left sidebar (GUI2-T02): "Nueva sesión", a search box, and the sessions of the apps and the framework runs grouped by
// project, each with a status dot and a count per app. The shell (shell.js) puts it inside <nav id="sidebar">.
// It reads only what the GUI API already gives (sessions and runs); there is no task board here because no GUI
// endpoint serves it. Names, titles and folders are untrusted text: they are only ever rendered as text nodes.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { debounce, subscribe } from "./bus.js";
import { Help, helpFor } from "./help.js";
import { navigate, routeHref, NEW_SECTION } from "./route.js";
import { relativeTime } from "./session-text.js";
import { appLabel, buildGroups, countsOf, dotText, isCurrent, runItems, sessionItems } from "./sidebar-model.js";

const SESSION_LIMIT = 100;
const RUN_LIMIT = 50;
const EVENT_DEBOUNCE_MS = 1500; // runs change with the event stream
const REFRESH_MS = 60000;       // sessions of the apps are files: they are looked at again once a minute

function Dot({ dot, detail }) {
  const text = dotText(dot, detail);
  return html`<span class=${"dot dot-" + dot} aria-hidden="true" title=${text}></span>`;
}

function Item({ item, current, now }) {
  const text = dotText(item.dot, item.detail);
  return html`<li>
    <a class="side-item" href=${routeHref(item.route[0], item.route[1])} aria-current=${current ? "page" : null}
      title=${item.folder || item.title}>
      <${Dot} dot=${item.dot} detail=${item.detail} />
      <span class="side-text">
        <span class="side-title">${item.title}</span>
        <span class="side-meta">${appLabel(item.app)} · ${relativeTime(item.updatedAt, now)}</span>
      </span>
      <span class="sr-only">Estado: ${text}</span>
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

// views: the buttons that switch between the "Sesiones" and "Ejecuciones" screens (they live next to the section list in main.js).
export function Sidebar({ token, status, route, views }) {
  const [data, setData] = useState({ sessions: [], runs: [], hint: null, failed: [], loaded: false });
  const [query, setQuery] = useState("");
  const [now, setNow] = useState(Date.now());
  const ticket = useRef(0);

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

  const items = sessionItems(data.sessions).concat(runItems(data.runs));
  const searching = query.trim() !== "";
  const groups = buildGroups(items, query);
  const shown = groups.reduce((total, group) => total + group.items.length, 0);

  let summary = "";
  if (!data.loaded) summary = "Cargando la lista…";
  else if (items.length === 0) summary = "Todavía no hay sesiones ni ejecuciones.";
  else if (shown === 0) summary = "Nada coincide con «" + query.trim() + "».";
  else summary = shown + (shown === 1 ? " elemento" : " elementos") + " en " + groups.length + (groups.length === 1 ? " proyecto." : " proyectos.");

  return html`<div class="side">
    <div class="side-top">
      <div class="side-new">
        <button type="button" class="btn side-new-btn" data-nav aria-current=${route.section === NEW_SECTION ? "page" : null}
          ...${helpFor("shell.new_session")} onClick=${() => navigate(NEW_SECTION)}><span aria-hidden="true">+ </span>Nueva sesión</button>
        <${Help} topic="shell.new_session" compact=${true} />
      </div>
      ${views}
      <div class="side-search" role="search">
        <label for="side-search">Buscar</label>
        <div class="side-search-row">
          <input id="side-search" type="search" maxlength="100" autocomplete="off" placeholder="Sesión, proyecto, estado…"
            ...${helpFor("shell.search")} value=${query} onInput=${(event) => setQuery(event.target.value)} />
          <${Help} topic="shell.search" compact=${true} />
        </div>
      </div>
    </div>
    <p class="muted small side-summary" role="status" aria-live="polite" aria-busy=${data.loaded ? "false" : "true"}>${summary}</p>
    ${data.failed.length > 0 && html`<p class="message error small" role="alert">No se pudo leer la lista de ${data.failed.join(" ni de ")}; se reintentará sola.</p>`}
    ${data.loaded && data.hint && data.sessions.length === 0 && html`<p class="muted small">Las sesiones de Claude, Codex y Copilot necesitan carpetas
      permitidas (gui_session_roots). En «Sesiones» se explica cómo.</p>`}
    <div class="side-groups">
      ${groups.map((group) => html`<${Group} key=${group.key + (searching ? "|q" : "")} group=${group} route=${route} now=${now} />`)}
    </div>
  </div>`;
}
