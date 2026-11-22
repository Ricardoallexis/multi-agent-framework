// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The shell inbox polls the local, token-protected pending endpoint.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, apiPost } from "./api.js";
import { loadToken } from "./auth.js";
import { Help, helpFor } from "./help.js";
import { readNotificationsEnabled, writeNotificationsEnabled } from "./shell-state.js";
import { ALL_TAB, PENDING_SECTION, countPending, createPendingStore, newlyPending, pendingTabs, routeTab, tabRoute } from "./pending-model.js";
import { messageId, messagePath } from "./inbox-model.js";
import { navigate } from "./route.js";
import { Icon } from "./ui/icon.js";
import { instanceLabel, onInstanceLabelChange, pageTitle } from "./instance.js";   // PRV-T01
import { Tabs } from "./ui/tabs.js";

const PENDING_PATH = "/gui/api/pending";
let memoryNotifications = false;
const TYPE_LABELS = {
  approval: "Aprobación",
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
  if (/^#(?:new\/(?:run|team|plan)|teams|plans|approvals)\/[^#]*$/.test(href)) return href;
  return ["#continuity", "#task_waiting", "#map"].includes(href) ? href : "#";
}

function formatSince(value) {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
}

// One poll for the page, shared by the sidebar row and the «Todo» tab (createPendingStore): 5 s visible, 30 s hidden.
const store = createPendingStore({
  load: async () => {
    const token = loadToken();
    const answer = await apiGet(PENDING_PATH, token);
    const [background, policy] = await Promise.all([
      apiGet("/gui/api/pending/background", token), apiGet("/gui/api/inbox/policy", token),
    ]);
    return { answer, background, policy };
  },
  setTimer: (run, ms) => setInterval(run, ms),
  clearTimer: (timer) => clearInterval(timer),
  isHidden: () => document.visibilityState === "hidden",
  watchVisibility: (onChange) => {
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  },
  onNew: (items, seen) => notifyNewItems(newlyPending(items, seen, notificationsEnabled(), notificationPermission())),
});

export function usePendingInbox() {
  const [data, setData] = useState(store.get);
  useEffect(() => {
    const off = store.subscribe(setData);
    setData(store.get());
    return off;
  }, []);
  return { ...data, count: countPending(data.items), reload: () => store.refresh() };
}

// The fixed «Pendientes» row of the sidebar: a link to #pending with the count. It also keeps the number in the page title.
export function PendingLink({ current = false }) {
  const { count } = usePendingInbox();
  const [label, setLabel] = useState(instanceLabel);
  useEffect(() => onInstanceLabelChange(setLabel), []);
  useEffect(() => {
    document.title = pageTitle(count);
  }, [count, label]);
  return html`<a class="side-link" href=${"#" + PENDING_SECTION} aria-current=${current ? "page" : null}>
    <${Icon} name="inbox" />Pendientes
    <span class="pending-count" aria-label=${count + (count === 1 ? " pendiente" : " pendientes")}>${count}</span>
  </a>
  <${Help} topic="pending.toggle" compact=${true} />`;
}

// «Pendientes»: «Todo» and one tab for each view registered with group "pending" (approvals, decisions, document alerts, tasks
// that wait). The address says which one is open: #pending, or the view's own hash (#approvals, #approvals/<id>, ...).
export function PendingScreen({ views, route, token, status, mock }) {
  const { items, loading } = usePendingInbox();
  const tabs = pendingTabs(views, items, !loading);
  const selected = routeTab(route, tabs) || ALL_TAB;
  const entry = selected === ALL_TAB ? null : views.find((candidate) => candidate.id === selected);
  return html`<${Tabs} id="pending" label="Pendientes" className="pending-tabs" selected=${selected} tabs=${tabs}
    onSelect=${(tabId) => navigate(...tabRoute(tabId))} ...${helpFor("pending.tabs")}
    after=${html`<${Help} topic="pending.tabs" compact=${true} />`}>
    ${!entry ? html`<${PendingAll} />` : html`
      ${entry.topic && html`<${Help} key=${entry.id} topic=${entry.topic} always=${true} />`}
      <${entry.component} key=${entry.id} token=${token} status=${status} mock=${mock} route=${route} />`}
  <//>`;
}

// The «Todo» tab: everything that waits for a person, what runs in the background, and the browser notifications.
function PendingAll() {
  const { items, background, policy, count, loading, error, reload } = usePendingInbox();
  const [enabled, setEnabled] = useState(() => notificationPermission() === "granted" && notificationsEnabled());
  const [permission, setPermission] = useState(notificationPermission);
  const [notice, setNotice] = useState("");
  // AVA-T05: a background cancellation is an inbox message, never a direct decision.
  const [cancelId, setCancelId] = useState(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState("");
  const cancelMessageId = useRef(null);
  const cancelBackground = async () => {
    if (!cancelId || !policy.enabled || cancelBusy) return;
    setCancelBusy(true); setCancelError("");
    if (!cancelMessageId.current) cancelMessageId.current = messageId();
    try {
      await apiPost(messagePath("runs", cancelId), loadToken(),
        { message_id: cancelMessageId.current, kind: "cancel", text: "" });
      setNotice("Cancelación guardada. Se aplicará en la siguiente frontera de trabajo.");
      setCancelId(null); cancelMessageId.current = null; reload();
    } catch (failure) {
      setCancelError(failure?.details?.reason === "availability_disabled"
        ? "La política de disponibilidad está apagada en este servidor." : failure.message);
    } finally { setCancelBusy(false); }
  };

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

  return html`<section class="pending-all" aria-label="Todo">
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
      </div>
      <p class="pending-status" role="status" aria-live="polite">
        ${loading ? "Leyendo pendientes…" : count === 1 ? "1 pendiente." : count + " pendientes."}
      </p>
      ${notice && html`<p class="pending-notice" role="status">${notice}</p>`}
      ${error && html`<p class="pending-error" role="alert">No se pudieron actualizar los pendientes: ${error.message}</p>`}
      ${items.length ? html`<ul class="pending-items">
        ${items.map((item) => html`<li class="pending-item" key=${item.id}>
          <div class="pending-item-main">
            <span class="pending-type">${TYPE_LABELS[item.type] || "Pendiente"}</span>
            <a href=${itemHref(item.href)} ...${helpFor("pending.open", { scope: item.id })}>
              ${item.title}
            </a>
            ${item.reason && html`<span class="pending-reason">${item.reason}</span>`}
            <time class="pending-since" dateTime=${item.since || null}>${formatSince(item.since)}</time>
          </div>
          <${Help} topic="pending.open" compact=${true} scope=${item.id} />
        </li>`)}
      </ul>` : !loading && !error && html`<p class="pending-empty">No hay pendientes.</p>`}
      <section class="pending-background" aria-label="En fondo">
        <h3>En fondo</h3>
        <${Help} topic="pending.background" compact=${true} />
        <p class="muted">${policy.enabled ? "Puedes pedir cancelar una ejecución en curso." : policy.reason}</p>
        ${cancelError && html`<p role="alert">${cancelError}</p>`}
        ${background.length ? html`<ul class="pending-items">
          ${background.map((item) => html`<li class="pending-item pending-background-item" key=${item.id}>
            <div class="pending-item-main"><span class="pending-type">${item.reason}</span>
              <a href=${itemHref(item.href)} ...${helpFor("pending.open", { scope: item.id })}>${item.title}</a>
              <time class="pending-since" dateTime=${item.since || null}>${formatSince(item.since)}</time>
            </div>
            <button type="button" class="btn secondary small" disabled=${!policy.enabled || cancelBusy}
              ...${helpFor("pending.cancel", { scope: item.id })}
              onClick=${() => { setCancelId(item.id.slice(4)); setCancelError(""); cancelMessageId.current = null; }}>Cancelar</button>
            <${Help} topic="pending.cancel" compact=${true} scope=${item.id} />
          </li>`)}
        </ul>` : !loading && !error && html`<p class="pending-empty">No hay ejecuciones en fondo.</p>`}
        ${cancelId && html`<div class="pending-background-confirm" role="group" aria-label="Confirmar cancelación">
          <p>¿Pedir la cancelación de esta ejecución?</p>
          <button type="button" class="btn danger" disabled=${cancelBusy} ...${helpFor("pending.cancel_confirm")}
            onClick=${cancelBackground}>Sí, cancelar</button>
          <button type="button" class="btn secondary" disabled=${cancelBusy} ...${helpFor("pending.cancel_back")}
            onClick=${() => { setCancelId(null); cancelMessageId.current = null; }}>Volver</button>
          <${Help} topic="pending.cancel_confirm" compact=${true} />
          <${Help} topic="pending.cancel_back" compact=${true} />
        </div>`}
      </section>
    </section>`;
}
