// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CNT-T16 consumes CNT-T15's independent wait metadata. Reading it never changes the task lifecycle or an approval gate.
import { useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";

export const WAITING_PATH = "/api/v1/tasks?waiting_for=all";
export const WAITING_POLL_MS = 20000;
const WHO = { user: "Tu decisión", agent: "Agente", task: "Tarea", quota: "Cuota", window: "Ventana de integración" };

export function waitingTasks(data) {
  if (!Array.isArray(data)) throw new Error("La lista de esperas no es válida.");
  return data.filter((task) => task && typeof task.id === "string" && task.waiting &&
    Object.hasOwn(WHO, task.waiting.who));
}

export function waitingForUser(tasks) { return tasks.filter((task) => task.waiting.who === "user"); }
export function waitBadge(wait) { return wait.who === "user" ? "Espera tu decisión" : "En espera · " + WHO[wait.who]; }
export function waitRecipient(wait) { return WHO[wait.who] + (wait.target ? ": " + wait.target : ""); }
export async function readWaiting(token, get = apiGet) { return waitingTasks(await get(WAITING_PATH, token)); }
export function waitDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
}

// One store owns the request and timer for both mounted surfaces. A folded panel is inactive;
// the view stays active while mounted. Failed refreshes keep the last successful list.
const stores = new Map();
function panelVisible() { return document.querySelector(".app")?.dataset.panel === "open"; }
function storeFor(token) {
  if (stores.has(token)) return stores.get(token);
  const store = { state: { loading: true, tasks: [], error: null }, listeners: new Set(), ticket: 0,
    timer: null, observer: null, loading: false };
  store.active = () => [...store.listeners].some((listener) => listener.scope === "view" || panelVisible());
  store.publish = () => { for (const listener of store.listeners) listener.update(store.state); };
  store.load = async () => {
    const mine = ++store.ticket;
    try {
      const tasks = await readWaiting(token);
      if (mine === store.ticket) { store.state = { loading: false, tasks, error: null }; store.publish(); }
    } catch (error) {
      if (mine === store.ticket) { store.state = { ...store.state, loading: false, error }; store.publish(); }
    }
  };
  store.start = () => {
    if (!store.active()) return;
    if (store.state.loading) store.load();
    if (!store.timer) store.timer = setInterval(() => {
      if (document.visibilityState !== "hidden" && store.active()) store.load();
    }, WAITING_POLL_MS);
  };
  store.observe = () => {
    const app = document.querySelector(".app");
    if (app && typeof MutationObserver !== "undefined") {
      store.observer = new MutationObserver(() => store.start());
      store.observer.observe(app, { attributes: true, attributeFilter: ["data-panel"] });
    }
  };
  stores.set(token, store);
  return store;
}

export function useTaskWaiting(token, scope = "view") {
  const [state, setState] = useState({ loading: true, tasks: [], error: null });
  const refresh = useRef(null);
  useEffect(() => {
    const store = storeFor(token);
    const listener = { scope, update: setState };
    store.listeners.add(listener);
    setState(store.state);
    refresh.current = store.load;
    if (!store.observer) store.observe();
    store.start();
    return () => {
      store.listeners.delete(listener);
      refresh.current = null;
      if (!store.listeners.size) {
        store.ticket++;
        clearInterval(store.timer);
        store.observer?.disconnect();
        stores.delete(token);
      }
    };
  }, [token, scope]);
  return { ...state, reload: () => refresh.current && refresh.current() };
}
