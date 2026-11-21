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
  GROUPS, NO_CONVERSATION, PAGE_SIZE, activityText, childLabels, childTitle, childrenWarning, countsText, detailText,
  groupOf, initials,
} from "./subagents-text.js";
import { LevelBadge } from "./ui/level-badge.js";
import { StatusChip } from "./ui/status-chip.js";
import { Tabs } from "./ui/tabs.js";

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
  const href = childHref(child, sessionId);
  const current = Boolean(child.link_id) && child.link_id === currentId;
  const body = html`
    <span class=${"sub-avatar src-" + child.source} aria-hidden="true" title=${sourceLabel(child.source)}>${initials(child)}</span>
    <span class="sub-text">
      <span class="sub-title">${childTitle(child)}</span>
      <span class="sub-detail muted small">${detailText(child, false)}<${LevelBadge} level=${child.level} /></span>
      <span class="sub-state small"><${StatusChip} status=${child.status} labels=${childLabels(child)} variant="inline" /></span>
      <span class="sub-activity muted small">${activityText(child, now)}</span>
      ${!child.link_id && !href && html`<span class="muted small">${NO_CONVERSATION}</span>`}
    </span>`;
  return html`<li>${href
    ? html`<a class="sub-item" href=${href} aria-current=${current ? "page" : null}
        data-source=${child.source}>${body}</a>`
    : html`<div class="sub-item static" data-source=${child.source}>${body}</div>`}</li>`;
}

export function SubagentsPanel({ token, sessionId, currentId = null, onContent }) {
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

  // GUI3-T04: the «Contexto» panel draws this card by whether the session has subagents.
  useEffect(() => { if (typeof onContent === "function") onContent({ has: data.loaded && data.children.length > 0 }); },
    [onContent, data.loaded, data.children.length]);
  const groups = { active: [], done: [] };
  for (const child of data.children) groups[groupOf(child.status)].push(child);
  const counts = data.loaded ? { active: groups.active.length, done: groups.done.length } : data.counts;
  const shown = groups[tab];
  const visible = expanded[tab] ? shown : shown.slice(0, PAGE_SIZE);
  const hidden = shown.length - visible.length;
  const group = GROUPS.find((entry) => entry.id === tab);
  const noChildren = data.children.length === 0;

  const viewingChild = currentId && currentId !== sessionId;
  // The title "Subagentes" and its "?" (topic "subagents.panel") belong to the section of the right panel that holds this panel.
  return html`<div class="sub-panel">
    ${viewingChild && html`<div class="header-tools back-bar">
      <a class="btn secondary small" href=${routeHref("sessions", sessionId)} ...${helpFor("subagents.back")}>← Volver a la sesión principal</a>
      <${Help} topic="subagents.back" compact=${true} />
    </div>`}
    <div class="sub-head">
      <p class="muted small sub-counts" role="status" aria-live="polite">${data.loaded ? countsText(counts) : "Cargando subagentes…"}${
        stream === "failed" ? ". Sin actualización en vivo; se revisa cada 10 segundos." : ""}</p>
      <div class="header-tools">
        <button type="button" class="btn secondary small" ...${helpFor("subagents.refresh")} onClick=${refresh}>Actualizar</button>
        <${Help} topic="subagents.refresh" compact=${true} />
      </div>
    </div>
    <${Tabs} id="sub" label="Estado de los subagentes" selected=${tab} onSelect=${setTab} ...${helpFor("subagents.tabs")}
      tabs=${GROUPS.map((entry) => ({ id: entry.id, label: entry.label, count: counts[entry.id] }))}
      after=${html`<${Help} topic="subagents.tabs" compact=${true} />`}
      panelProps=${{ "aria-busy": data.loaded ? "false" : "true" }}>
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
    <//>
    ${data.warnings.length > 0 && html`<p class="muted small">${data.warnings.map(childrenWarning).join(" ")}</p>`}
  </div>`;
}
