// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The shell inbox polls the local, token-protected pending endpoint.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { loadToken } from "./auth.js";
import { Help, helpFor } from "./help.js";
import { readNotificationsEnabled, writeNotificationsEnabled } from "./shell-state.js";
import { countPending, newlyPending, pendingIds, sortPending } from "./pending-model.js";

const PENDING_PATH = "/gui/api/pending";
const VISIBLE_POLL_MS = 5000;
const HIDDEN_POLL_MS = 30000;
let memoryNotifications = false;
const TYPE_LABELS = {
  run: "Ejecución",
  team: "Equipo",
  plan: "Plan",
  continuity: "Continuidad",
  task: "Tarea",
};

function notificationPermission() {
  try { return "Notification" in window ? window.Notification.permission : "unsupported"; } catch (_) { return "unsupported"; }
}

function notificationsEnabled() {
  const saved = readNotificationsEnabled();
  return saved === null ? memoryNotifications : saved;
}

function storeNotificationsEnabled(value) {
  memoryNotifications = value;
  writeNotificationsEnabled(value);
}

function notifyNewItems(items) {
  if (notificationPermission() !== "granted" || !notificationsEnabled()) return;
  for (const item of items) {
    try { new window.Notification("Pendiente nuevo", { body: item.title }); } catch (_) { /* notifications can be blocked */ }
  }
}

function itemHref(href) {
  if (typeof href !== "string") return "#";
  if (/^#(?:new\/(?:run|team|plan)|teams|plans)\/[^#]*$/.test(href)) return href;
  return ["#continuity", "#task_waiting", "#map"].includes(href) ? href : "#";
}

function formatSince(value) {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
}

function usePendingInbox() {
  const [data, setData] = useState({ items: [], loading: true, error: null });
  const seen = useRef(null);
  const inFlight = useRef(false);
  const reload = useRef(null);

  useEffect(() => {
    let stopped = false;
    let timer = null;
    const token = loadToken();
    const refresh = async () => {
      if (stopped || inFlight.current) return;
      inFlight.current = true;
      try {
        const answer = await apiGet(PENDING_PATH, token);
        const items = sortPending(Array.isArray(answer?.items) ? answer.items : []);
        if (seen.current !== null) {
          notifyNewItems(newlyPending(items, seen.current, notificationsEnabled(), notificationPermission()));
        }
        seen.current = pendingIds(items);
        if (!stopped) setData({ items, loading: false, error: null });
      } catch (error) {
        if (!stopped) setData((current) => ({ ...current, loading: false, error }));
      } finally {
        inFlight.current = false;
      }
    };
    const schedule = () => {
      if (timer !== null) clearInterval(timer);
      timer = setInterval(refresh, document.visibilityState === "hidden" ? HIDDEN_POLL_MS : VISIBLE_POLL_MS);
    };
    const onVisibilityChange = () => {
      schedule();
      if (document.visibilityState !== "hidden") refresh();
    };
    reload.current = refresh;
    refresh();
    schedule();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      reload.current = null;
      if (timer !== null) clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  return { ...data, count: countPending(data.items), reload: () => reload.current && reload.current() };
}

export function PendingInbox() {
  const { items, count, loading, error, reload } = usePendingInbox();
  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState(() => notificationPermission() === "granted" && notificationsEnabled());
  const [permission, setPermission] = useState(notificationPermission);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    document.title = count ? `(${count}) Multiagent` : "Multi-Agent Framework";
  }, [count]);

  const onNotificationsClick = async () => {
    if (!("Notification" in window)) {
      setNotice("Este navegador no permite avisos.");
      return;
    }
    let nextPermission = window.Notification.permission;
    if (nextPermission === "default") {
      try { nextPermission = await window.Notification.requestPermission(); } catch (_) { nextPermission = "denied"; }
    }
    setPermission(nextPermission);
    if (nextPermission === "granted") {
      const next = !enabled;
      storeNotificationsEnabled(next);
      setEnabled(next);
      setNotice(next ? "Avisos activados en este navegador." : "Avisos desactivados.");
    } else {
      storeNotificationsEnabled(false);
      setEnabled(false);
      setNotice("No se activaron los avisos; puedes cambiar el permiso en el navegador.");
    }
  };

  return html`<div class="pending-tool">
    <button type="button" class="tool-btn pending-button" aria-expanded=${open ? "true" : "false"} aria-controls="pending-list"
      ...${helpFor("pending.toggle")} onClick=${() => setOpen(!open)}>
      Pendientes <span class="pending-count" aria-label=${count + (count === 1 ? " pendiente" : " pendientes")}>${count}</span>
    </button>
    <${Help} topic="pending.toggle" compact=${true} />
    <section id="pending-list" class="pending-popover" role="region" aria-labelledby="pending-title" hidden=${!open}>
      <header class="pending-header">
        <h2 id="pending-title">Pendientes</h2>
        <div class="pending-actions">
          <span class="pending-control">
            <button type="button" class="btn secondary small" ...${helpFor("pending.refresh")} onClick=${reload}>Actualizar</button>
            <${Help} topic="pending.refresh" compact=${true} />
          </span>
          <span class="pending-control">
            <button type="button" class="btn secondary small" aria-pressed=${enabled ? "true" : "false"}
              disabled=${permission === "unsupported" || permission === "denied"}
              ...${helpFor("pending.notifications")} onClick=${onNotificationsClick}>
              ${enabled ? "Desactivar avisos" : "Activar avisos"}
            </button>
            <${Help} topic="pending.notifications" compact=${true} />
          </span>
          <span class="pending-control">
            <button type="button" class="btn secondary small" ...${helpFor("pending.close")} onClick=${() => setOpen(false)}>Cerrar</button>
            <${Help} topic="pending.close" compact=${true} />
          </span>
        </div>
      </header>
      <p class="pending-status" role="status" aria-live="polite">
        ${loading ? "Leyendo pendientes…" : count === 1 ? "1 pendiente." : count + " pendientes."}
      </p>
      ${notice && html`<p class="pending-notice" role="status">${notice}</p>`}
      ${error && html`<p class="pending-error" role="alert">No se pudieron actualizar los pendientes: ${error.message}</p>`}
      ${items.length ? html`<ul class="pending-items">
        ${items.map((item) => html`<li class="pending-item" key=${item.id}>
          <div class="pending-item-main">
            <span class="pending-type">${TYPE_LABELS[item.type] || "Pendiente"}</span>
            <a href=${itemHref(item.href)} ...${helpFor("pending.open", { scope: item.id })} onClick=${() => setOpen(false)}>
              ${item.title}
            </a>
            ${item.reason && html`<span class="pending-reason">${item.reason}</span>`}
            <time class="pending-since" dateTime=${item.since || null}>${formatSince(item.since)}</time>
          </div>
          <${Help} topic="pending.open" compact=${true} scope=${item.id} />
        </li>`)}
      </ul>` : !loading && !error && html`<p class="pending-empty">No hay pendientes.</p>`}
    </section>
  </div>`;
}
