// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Subagents panel (GUI2-T04): the subagents, spawned threads and workers of one session, in two tabs
// ("Activos" / "Terminados"). Everything it shows is plain text (no HTML); an item opens that child's
// conversation through the existing hash route (#sessions/<id>). It refreshes from the lightweight
// "children" events of /gui/api/stream?session=<id>&only=children and, if that stream is down, every 10 s.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "./api.js";
import { authHeaders } from "./auth.js";
import { Help, helpFor } from "./help.js";
import { routeHref } from "./route.js";
import { parseBlock } from "./stream.js";
import { errorText } from "./text.js";
import { sourceLabel } from "./session-text.js";
import {
  GROUPS, NO_CONVERSATION, PAGE_SIZE, activityText, childTitle, childrenWarning, countsText, detailText, groupOf,
  initials, statusInfo,
} from "./subagents-text.js";

const TICK_MS = 10000;          // fallback poll while the stream is down; also keeps "hace N min" honest
const CONNECTED_EVERY = 3;      // while the stream is up, refresh anyway every 3rd tick (tokens and times)
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 30000;

const childrenPath = (sessionId) => "/gui/api/sessions/" + encodeURIComponent(sessionId) + "/children";
const asApiError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));

// Follows "something changed" for one session. onChange(data) gets { version, counts }; onState gets
// connected | reconnecting | failed (the server refuses the session for good). Returns a stop function.
export function followChildren({ token, session, onChange, onState }) {
  let stopped = false;
  let controller = null;
  let delay = RECONNECT_MIN_MS;
  (async () => {
    while (!stopped) {
      controller = new AbortController();
      try {
        const response = await fetch("/gui/api/stream?session=" + encodeURIComponent(session) + "&only=children", {
          headers: { ...authHeaders(token), Accept: "text/event-stream" },
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status >= 400 && response.status < 500 && response.status !== 408 && response.status !== 429) {
          onState("failed");
          return;
        }
        if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
        onState("connected");
        delay = RECONNECT_MIN_MS;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          let end;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const event = parseBlock(buffer.slice(0, end));
            buffer = buffer.slice(end + 2);
            if (!event) continue;
            if (event.type === "error") { onState("failed"); return; }
            if (event.type !== "children") continue;
            try { onChange(JSON.parse(event.data)); } catch (_) { /* a bad event must not drop the stream */ }
          }
        }
      } catch (_) {
        if (stopped) return;
      }
      if (stopped) return;
      onState("reconnecting");
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, RECONNECT_MAX_MS);
    }
  })();
  return () => { stopped = true; if (controller) controller.abort(); };
}

// Where an item goes: its own conversation, or (framework steps) the run that holds it.
function childHref(child, sessionId) {
  if (child.link_id) return routeHref("sessions", child.link_id);
  if (child.source === "framework" && sessionId.startsWith("framework:")) return routeHref("runs", sessionId.slice("framework:".length));
  return null;
}

function Item({ child, sessionId, currentId, now }) {
  const info = statusInfo(child.status, child.note);
  const href = childHref(child, sessionId);
  const current = Boolean(child.link_id) && child.link_id === currentId;
  const body = html`
    <span class=${"sub-avatar src-" + child.source} aria-hidden="true" title=${sourceLabel(child.source)}>${initials(child)}</span>
    <span class="sub-text">
      <span class="sub-title">${childTitle(child)}</span>
      <span class="sub-detail muted small">${detailText(child)}</span>
      <span class="sub-state small">
        <span class=${"sub-dot tone-" + info.tone} aria-hidden="true"></span>
        <span class=${"sub-status tone-" + info.tone} title=${info.help}>${info.label}</span>
      </span>
      <span class="sub-activity muted small">${activityText(child, now)}</span>
      ${!child.link_id && !href && html`<span class="muted small">${NO_CONVERSATION}</span>`}
    </span>`;
  return html`<li>${href
    ? html`<a class="sub-item" href=${href} aria-current=${current ? "page" : null}
        data-source=${child.source}>${body}</a>`
    : html`<div class="sub-item static" data-source=${child.source}>${body}</div>`}</li>`;
}

export function SubagentsPanel({ token, sessionId, currentId = null }) {
  const [data, setData] = useState({ children: [], counts: { active: 0, done: 0, total: 0 }, warnings: [], loaded: false });
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("active");
  const [expanded, setExpanded] = useState({ active: false, done: false });
  const [stream, setStream] = useState("reconnecting");
  const [now, setNow] = useState(Date.now());
  const alive = useRef(true);
  const version = useRef(null);
  const ticket = useRef(0);
  const connected = useRef(false);
  const tabs = useRef({});

  async function refresh() {
    const mine = ++ticket.current;
    try {
      const result = await apiGet(childrenPath(sessionId), token);
      if (!alive.current || mine !== ticket.current) return;
      version.current = result.version;
      setData({ children: result.children, counts: result.counts, warnings: result.warnings || [], loaded: true });
      setError(null);
    } catch (failure) {
      if (alive.current && mine === ticket.current) setError(asApiError(failure));
    }
  }

  // One panel per session: reset, load, then follow the stream.
  useEffect(() => {
    alive.current = true;
    version.current = null;
    connected.current = false;
    setData({ children: [], counts: { active: 0, done: 0, total: 0 }, warnings: [], loaded: false });
    setError(null);
    setExpanded({ active: false, done: false });
    refresh();
    const stop = followChildren({
      token,
      session: sessionId,
      onChange: (event) => { if (event && event.version !== version.current) refresh(); },
      onState: (value) => {
        connected.current = value === "connected";
        if (alive.current) setStream(value);
      },
    });
    let ticks = 0;
    const timer = setInterval(() => {
      ticks += 1;
      setNow(Date.now());
      if (!connected.current || ticks % CONNECTED_EVERY === 0) refresh();
    }, TICK_MS);
    return () => { alive.current = false; stop(); clearInterval(timer); };
  }, [sessionId, token]);

  const groups = { active: [], done: [] };
  for (const child of data.children) groups[groupOf(child.status)].push(child);
  const counts = data.loaded ? { active: groups.active.length, done: groups.done.length } : data.counts;
  const shown = groups[tab];
  const visible = expanded[tab] ? shown : shown.slice(0, PAGE_SIZE);
  const hidden = shown.length - visible.length;
  const group = GROUPS.find((entry) => entry.id === tab);
  const noChildren = data.children.length === 0;

  const selectTab = (id, focus) => {
    setTab(id);
    if (focus && tabs.current[id]) tabs.current[id].focus();
  };
  const onTabKey = (event) => {
    const order = GROUPS.map((entry) => entry.id);
    const at = order.indexOf(tab);
    let next = -1;
    if (event.key === "ArrowRight") next = (at + 1) % order.length;
    else if (event.key === "ArrowLeft") next = (at - 1 + order.length) % order.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = order.length - 1;
    if (next < 0) return;
    event.preventDefault();
    selectTab(order[next], true);
  };

  const viewingChild = currentId && currentId !== sessionId;
  return html`<section class="sub-panel" aria-labelledby="sub-title">
    <div class="sub-head">
      <h3 id="sub-title">Subagentes</h3>
      <div class="header-tools">
        <button type="button" class="btn secondary small" ...${helpFor("subagents.refresh")} onClick=${refresh}>Actualizar</button>
        <${Help} topic="subagents.refresh" compact=${true} />
      </div>
    </div>
    <${Help} topic="subagents.panel" />
    ${viewingChild && html`<div class="header-tools back-bar">
      <a class="btn secondary small" href=${routeHref("sessions", sessionId)} ...${helpFor("subagents.back")}>← Volver a la sesión principal</a>
      <${Help} topic="subagents.back" compact=${true} />
    </div>`}
    <p class="muted small" role="status" aria-live="polite">${data.loaded ? countsText(counts) : "Cargando subagentes…"}${
      stream === "failed" ? ". Sin actualización en vivo; se revisa cada 10 segundos." : ""}</p>
    <div class="sub-tabs-row">
      <div class="sub-tabs" role="tablist" aria-label="Estado de los subagentes" onKeyDown=${onTabKey}>
        ${GROUPS.map((entry) => html`<button key=${entry.id} type="button" role="tab" id=${"sub-tab-" + entry.id}
          class="sub-tab" ...${helpFor("subagents.tabs")} aria-selected=${tab === entry.id ? "true" : "false"} aria-controls="sub-list"
          tabindex=${tab === entry.id ? 0 : -1} ref=${(node) => { tabs.current[entry.id] = node; }}
          onClick=${() => selectTab(entry.id, false)}>${entry.label} · ${counts[entry.id]}</button>`)}
      </div>
      <${Help} topic="subagents.tabs" compact=${true} />
    </div>
    <div id="sub-list" role="tabpanel" aria-labelledby=${"sub-tab-" + tab} aria-busy=${data.loaded ? "false" : "true"}>
      ${error && html`<div class="message error" role="alert">No se pudieron listar los subagentes: ${errorText(error)}
        <button type="button" class="btn secondary small" ...${helpFor("subagents.retry")} onClick=${refresh}>Reintentar</button>
        <${Help} topic="subagents.retry" compact=${true} /></div>`}
      ${data.loaded && shown.length === 0 && !error && html`<p class="muted">${noChildren
        ? "Esta sesión no ha lanzado subagentes." : group.empty}</p>`}
      ${visible.length > 0 && html`<${Help} topic="subagents.item" />
        <ul class="sub-list" aria-label=${group.label}>
          ${visible.map((child) => html`<${Item} key=${child.id} child=${child} sessionId=${sessionId} currentId=${currentId} now=${now} />`)}
        </ul>`}
      ${shown.length > PAGE_SIZE && html`<div class="header-tools sub-more-row">
        <button type="button" class="sub-more" aria-expanded=${expanded[tab] ? "true" : "false"} ...${helpFor("subagents.more")}
          onClick=${() => setExpanded({ ...expanded, [tab]: !expanded[tab] })}>${expanded[tab] ? "Ver menos" : "Ver " + hidden + " más"}</button>
        <${Help} topic="subagents.more" compact=${true} />
      </div>`}
    </div>
    ${data.warnings.length > 0 && html`<p class="muted small">${data.warnings.map(childrenWarning).join(" ")}</p>`}
  </section>`;
}
