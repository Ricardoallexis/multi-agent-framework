// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// One session: summary header, items paged with "cargar más" and live follow over the SSE stream. Its subagents are listed
// by the right panel of the shell (subagents-mount.js), not here.
// Session content is untrusted text: it is only ever rendered as text nodes (see session-items.js).
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "./api.js";
import { debounce } from "./bus.js";
import { Help, helpFor } from "./help.js";
import { OpenInApp } from "./open-in-app.js";
import { routeHref } from "./route.js";
import { Items } from "./session-items.js";
import {
  modelsText, relativeTime, sessionTitle, sourceLabel, statusInfo, surfaceLabel, tokensText, warningText,
} from "./session-text.js";
import { startStream } from "./stream.js";
import { errorText, fmtDate } from "./text.js";

const ITEMS_PAGE = 200;
const SUMMARY_REFRESH_MS = 2000;
const NEAR_BOTTOM_PX = 160;

const sessionPath = (id, tail = "") => "/gui/api/sessions/" + encodeURIComponent(id) + tail;
const asApiError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
const isCursorReset = (warnings) => (warnings || []).some((w) => String(w).startsWith("cursor_reset"));

// A framework run streams its raw events; the items endpoint turns each into a "system" item with this text.
function frameworkItem(event) {
  const payload = event && typeof event.payload === "object" && event.payload ? event.payload : {};
  const body = Object.keys(payload).length ? " " + JSON.stringify(payload) : "";
  const text = String(event.type || "evento") + body;
  return { cursor: String(event.id), timestamp: event.ts || null, kind: "system", model: payload.model || null,
    text: text.slice(0, 20000), truncated: text.length > 20000, tool: null, files: [], usage: null, child_id: null };
}

const FOLLOW_TEXT = {
  waiting: "Sin seguimiento en vivo: carga el resto de la sesión para seguirla.",
  paused: "En pausa",
  connecting: "Conectando…",
  live: "En vivo",
  reconnecting: "Reconectando…",
  offline: "Sin conexión; reintentando…",
  ended: "Seguimiento terminado",
  failed: "No se pudo seguir en vivo",
};

function Header({ token, summary, onRefresh }) {
  const info = statusInfo(summary.status);
  const isRun = summary.source === "framework";
  return html`<header class="session-header">
    <h3 id="session-title">${sessionTitle(summary)}</h3>
    <p class="badges">
      <span class=${"badge src src-" + summary.source}>${sourceLabel(summary.source)}</span>
      <span class="badge">${surfaceLabel(summary.surface)}</span>
      <span class=${"badge tone-" + info.tone} title=${info.help}>${info.label}</span>
      ${summary.parent_id && html`<span class="badge">Subagente</span>`}
    </p>
    ${summary.parent_id && html`<p>Es un subagente de <a href=${routeHref("sessions", summary.parent_id)}>su sesión principal</a>.</p>`}
    <dl class="facts">
      <dt>Carpeta</dt><dd>${summary.cwd || (isRun ? "No aplica a las ejecuciones del framework" : "No consta")}</dd>
      <dt>Creada</dt><dd>${fmtDate(summary.created_at)}</dd>
      <dt>Última actualización</dt><dd>${relativeTime(summary.updated_at)} (${fmtDate(summary.updated_at)})</dd>
      <dt>Modelos</dt><dd>${modelsText(summary.models)}</dd>
      <dt>Tokens</dt><dd>${tokensText(summary.tokens)}</dd>
      <dt>Subagentes</dt><dd>${summary.child_count}</dd>
      <dt>Identificador</dt><dd><code>${summary.id}</code></dd>
    </dl>
    <${Help} topic="session.facts" />
    <div class="header-actions">
      ${isRun && html`<a class="btn secondary" href=${routeHref("runs", summary.native_id)} ...${helpFor("session.run_link")}>Ver la ejecución en «Ejecuciones»</a>
        <${Help} topic="session.run_link" compact=${true} />`}
      <button type="button" class="btn secondary small" ...${helpFor("session.refresh")} onClick=${onRefresh}>Actualizar datos</button>
      <${Help} topic="session.refresh" compact=${true} />
    </div>
    <div id="open-in-app-slot" class="open-in-app-slot"><${OpenInApp} token=${token} summary=${summary} /></div>
  </header>`;
}

export function SessionDetail({ token, sessionId, onBack }) {
  const isRun = sessionId.startsWith("framework:");
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [items, setItems] = useState([]);
  const [page, setPage] = useState({ loaded: false, done: false });
  const [loadingMore, setLoadingMore] = useState(false);
  const [itemsError, setItemsError] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [paused, setPaused] = useState(false);
  const [retry, setRetry] = useState(0);
  const [follow, setFollow] = useState({ state: "connecting", message: "" });
  const cursor = useRef(null);
  const alive = useRef(true);
  const busy = useRef(false);
  const stick = useRef(false);
  const article = useRef(null);

  const addWarnings = (list) => setWarnings((previous) => {
    const fresh = (list || []).filter((w) => !previous.includes(w));
    return fresh.length ? previous.concat(fresh) : previous;
  });

  async function refreshSummary() {
    try {
      const result = await apiGet(sessionPath(sessionId), token);
      if (alive.current) { setSummary(result); setError(null); }
    } catch (failure) {
      if (alive.current && !summary) setError(asApiError(failure));
    }
  }

  async function loadPage(after) {
    if (busy.current) return;
    busy.current = true;
    setLoadingMore(true);
    try {
      const query = "?limit=" + ITEMS_PAGE + (after ? "&after=" + encodeURIComponent(after) : "");
      const result = await apiGet(sessionPath(sessionId, "/items") + query, token);
      if (!alive.current) return;
      cursor.current = result.next_cursor;
      const restarted = Boolean(after) && isCursorReset(result.warnings);
      setItems((previous) => (after && !restarted ? previous.concat(result.items) : result.items));
      addWarnings(result.warnings);
      setPage({ loaded: true, done: Boolean(result.done) });
      setItemsError(null);
    } catch (failure) {
      if (alive.current) setItemsError(asApiError(failure));
    } finally {
      busy.current = false;
      if (alive.current) setLoadingMore(false);
    }
  }

  // Open: summary and first page. The component is keyed by session, so this runs once per session.
  useEffect(() => {
    alive.current = true;
    refreshSummary();
    loadPage(null);
    if (article.current) article.current.focus();
    return () => { alive.current = false; };
  }, [sessionId, token]);

  // Escape goes back to the list.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape" && !event.defaultPrevented && !event.isComposing) onBack();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onBack]);

  // Live follow starts once the page reached the end of the session; it resumes from the last cursor held.
  useEffect(() => {
    if (!page.done || paused) return undefined;
    setFollow({ state: "connecting", message: "" });
    const refresh = debounce(refreshSummary, SUMMARY_REFRESH_MS);
    const push = (list, restart) => {
      stick.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - NEAR_BOTTOM_PX;
      setItems((previous) => (restart ? list : previous.concat(list)));
      refresh();
    };
    const stop = startStream({
      token,
      session: sessionId,
      after: cursor.current || (isRun ? "0" : "0:0"),
      onStatus: (value, info) => {
        if (value === "connected") setFollow({ state: "live", message: "" });
        else if (value === "reconnecting" || value === "offline") setFollow({ state: value, message: "" });
        else if (value === "unauthorized") setFollow({ state: "failed", message: "El token de acceso ya no es válido." });
        else if (value === "failed") setFollow({ state: "failed", message: info && info.message ? info.message : "" });
      },
      onEvent: (event) => {
        let data = null;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        if (!data || typeof data !== "object") return;
        if (isRun) {
          if (data.id === undefined || data.id === null) return;
          const item = frameworkItem(data);
          cursor.current = item.cursor;
          push([item], false);
        } else if (event.type === "item") {
          if (data.cursor) cursor.current = data.cursor;
          push([data], false);
        } else if (event.type === "notice") {
          addWarnings(data.warnings);
          if (isCursorReset(data.warnings)) setItems([]); // the file was rewritten: the next items start over
        } else if (event.type === "error") {
          setFollow({ state: "ended", message: data.message || data.code || "" });
          stop();
        }
      },
    });
    return () => { stop(); refresh.cancel(); };
  }, [page.done, paused, retry, sessionId, token]);

  // Keep the newest item in view when the reader was already at the bottom.
  useEffect(() => {
    if (stick.current) { stick.current = false; window.scrollTo(0, document.documentElement.scrollHeight); }
  }, [items.length]);

  const allWarnings = [...new Set([...(summary ? summary.warnings || [] : []), ...warnings])];
  const followState = !page.done ? "waiting" : paused ? "paused" : follow.state;
  const retryable = followState === "failed" || followState === "ended";
  const followText = FOLLOW_TEXT[followState] + (follow.message && retryable ? ": " + follow.message : "");

  const back = html`<div class="header-tools back-bar"><button type="button" class="btn secondary small" ...${helpFor("session.back")} onClick=${onBack}>← Volver a la lista</button>
      <${Help} topic="session.back" compact=${true} />
      <span class="muted small"> Atajo: tecla Esc</span></div>`;
  if (error && !summary) {
    return html`<article class="session-detail" aria-labelledby="session-title" tabindex="-1" ref=${article}>
      ${back}
      <h3 id="session-title">No se pudo abrir la sesión</h3>
      <p class="message error" role="alert">${errorText(error)}</p>
    </article>`;
  }
  return html`<article class="session-detail" aria-labelledby="session-title" tabindex="-1" ref=${article}>
    ${back}
    ${!summary && html`<h3 id="session-title">Sesión</h3><p class="muted" aria-busy="true">Cargando la sesión…</p>`}
    ${summary && html`<${Header} token=${token} summary=${summary} onRefresh=${refreshSummary} />`}
    <section class="panel live-bar" aria-label="Seguimiento en vivo">
      <span class=${"badge live live-" + followState} role="status" aria-live="polite">${followText}</span>
      ${page.done && !retryable && html`<button type="button" class="btn secondary small" ...${helpFor("session.pause")} onClick=${() => setPaused(!paused)}>
        ${paused ? "Reanudar seguimiento" : "Pausar seguimiento"}</button>
        <${Help} topic="session.pause" compact=${true} />`}
      ${page.done && retryable && html`<button type="button" class="btn secondary small" ...${helpFor("session.retry")} onClick=${() => { setPaused(false); setRetry(retry + 1); }}>
        Reintentar</button>
        <${Help} topic="session.retry" compact=${true} />`}
      <${Help} topic="session.live" />
    </section>
    ${allWarnings.length > 0 && html`<details class="panel" open=${allWarnings.length <= 3}>
      <summary>Avisos de lectura (${allWarnings.length})</summary>
      <${Help} topic="session.warnings" />
      <ul class="warnings">${allWarnings.map((warning) => html`<li key=${warning} class="note small">${warningText(warning)}</li>`)}</ul>
    </details>`}
    <section class="panel" aria-labelledby="items-title">
      <h4 id="items-title">Contenido de la sesión</h4>
      <${Help} topic="session.items" />
      ${items.some((item) => item.kind === "reasoning_omitted") && html`<${Help} topic="session.reasoning" />`}
      ${itemsError && html`<div class="message error" role="alert">No se pudo leer el contenido: ${errorText(itemsError)}
        <button type="button" class="btn secondary small" ...${helpFor("session.items_retry")} onClick=${() => loadPage(cursor.current)}>Reintentar</button>
        <${Help} topic="session.items_retry" compact=${true} /></div>`}
      ${!page.loaded && !itemsError && html`<p class="muted" aria-busy="true">Cargando el contenido…</p>`}
      ${page.loaded && items.length === 0 && html`<p class="muted">Esta sesión no tiene mensajes que mostrar todavía.</p>`}
      ${items.length > 0 && html`<${Items} items=${items} />`}
      ${page.loaded && !page.done && html`<div><button type="button" class="btn secondary" disabled=${loadingMore} ...${helpFor("session.more")}
        onClick=${() => loadPage(cursor.current)}>${loadingMore ? "Cargando…" : "Cargar más"}</button>
        <span class="muted small"> Mostrando ${items.length} elementos; hay más.</span>
        <${Help} topic="session.more" /></div>`}
      ${page.done && items.length > 0 && html`<p class="muted small">${items.length} elementos${followState === "live" ? "; los nuevos aparecen al final." : "."}</p>`}
    </section>
  </article>`;
}
