// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// App: connects the stream and puts the view of the current route inside the shell (shell.js: top bar, left sidebar,
// centre, right panel). All data is rendered as text through htm/Preact (no raw HTML injection).
// Screens, right-panel sections and help texts that tasks add come from static/extensions/<name>/ (extensions.js, FLU-T04):
// loadExtensions() runs before the first render (for at most LOAD_BUDGET_MS), so a registered view is one more button in the
// sidebar and one more route here, and nothing in this file changes when an extension is added.
import { html, render, useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { loadToken, clearToken, getJSON } from "./auth.js";
import { startStream } from "./stream.js";
import { publishEvent } from "./bus.js";
import { navigate, useRoute, NEW_SECTION } from "./route.js";
import { NewSession } from "./new-session.js";
import { TeamThread } from "./team-thread.js";   // CHT-T05
import { PlanThread } from "./plan-thread.js";   // CHT-T06
import { RightPanel } from "./right-panel.js";
import { AgentTree } from "./agent-tree.js";
import { Runs } from "./runs.js";
import { Sessions } from "./sessions.js";
import { Shell } from "./shell.js";
import { Sidebar } from "./sidebar.js";
import { WorkMap } from "./work-map.js";
import { Usage } from "./usage.js";
import { Evaluation } from "./evaluation.js";
import { Teams } from "./teams.js";
import { Plans } from "./plans.js";
import { extensionFailures, loadExtensions, registeredViews, registryVersion, subscribeRegistry } from "./extensions.js";

const STATUS_TEXT = {
  connected: "Conectado",
  reconnecting: "Reconectando…",
  offline: "Sin conexión",
  unauthorized: "Token no válido",
};

// "Sesiones" is the main screen and the default one.
const SECTIONS = [
  { id: "sessions", label: "Sesiones" },
  { id: "runs", label: "Ejecuciones" },
  { id: "usage", label: "Uso" },
  { id: "evaluation", label: "Evaluación" },
  { id: "teams", label: "Equipos" },
  { id: "plans", label: "Planes" },
  { id: "map", label: "Mapa" },
];
// The built-in screens, then the ones extensions registered (by their `order`).
const allSections = () => [...SECTIONS, ...registeredViews().map((view) => ({ id: view.id, label: view.label }))];

// "#new" is not a section of its own: it shows the guided chat of a new session (new-session.js), which falls back to the
// create-run form on request.
const NEW_TITLE = "Nueva sesión";
// "#tree/<session id>" (GUI2-T08) is a second view of one session: it has no entry in the sidebar of its own.
const TREE_SECTION = "tree";
const TREE_TITLE = "Árbol de agentes";

function NoToken() {
  return html`<main class="notice">
    <h1>Falta el token de acceso</h1>
    <p>Abre esta interfaz con el enlace que imprime <code>python -m multiagent.gui</code>;
      incluye el token después de <code>#token=</code>.</p>
  </main>`;
}

// The two screens as buttons at the top of the sidebar (the sidebar itself lists the items of both).
function Views({ section }) {
  return html`<div class="views">
    ${allSections().map((s) => html`<button key=${s.id} type="button" data-nav data-help-exempt="navigation" aria-current=${s.id === section ? "page" : null}
      onClick=${() => navigate(s.id)}>${s.label}</button>`)}
  </div>`;
}

// Draws again when an extension registers or fails after the first render (one that was slow).
function useRegistry() {
  const [, setSeen] = useState(registryVersion);
  useEffect(() => {
    const sync = () => setSeen(registryVersion());
    sync();
    return subscribeRegistry(sync);
  }, []);
}

function App({ token }) {
  useRegistry();
  const route = useRoute();
  const failed = extensionFailures();
  const [status, setStatus] = useState("reconnecting");
  const [mock, setMock] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);

  useEffect(() => {
    getJSON("/gui/api/session", token).then((s) => setMock(Boolean(s.mock))).catch(() => {});
    return startStream({
      token,
      onStatus: (value) => { if (value === "unauthorized") clearToken(); setStatus(value); },
      onEvent: (event) => { setLastEvent(event.id); publishEvent(event); },
    });
  }, [token]);

  const section = route.section;
  const creating = section === NEW_SECTION;
  const tree = section === TREE_SECTION;
  const current = allSections().find((s) => s.id === section);
  const views = registeredViews();
  // The sidebar and the right panel treat the tree of a session as the session itself (it stays marked, its panel stays).
  const context = tree ? { section: "sessions", id: route.id } : route;
  const sidebar = html`<${Sidebar} token=${token} status=${status} route=${context} views=${html`<${Views} section=${context.section} />`} />`;
  const panel = html`<${RightPanel} token=${token} route=${context} />`;
  return html`<${Shell} status=${status} statusText=${STATUS_TEXT[status]} mock=${mock} sidebar=${sidebar} panel=${panel}>
    ${section === "usage" ? html`<h2 id="usage-title">Uso</h2>` : html`<h2>${creating ? NEW_TITLE : tree ? TREE_TITLE : current?.label}</h2>`}
    <${Sessions} token=${token} route=${route} active=${section === "sessions"} />
    ${tree && html`<${AgentTree} key=${route.id || ""} token=${token} sessionId=${route.id} />`}
    ${creating && route.kind === "team" && html`<${TeamThread} key=${route.id} token=${token} versionId=${route.id} />`}
    ${creating && route.kind === "plan" && html`<${PlanThread} key=${route.id} token=${token} proposalId=${route.id} />`}
    ${creating && route.kind !== "team" && route.kind !== "plan" && html`<${NewSession} token=${token} mock=${mock} />`}
    ${section === "runs" && html`<${Runs} token=${token} status=${status} mock=${mock} initialRunId=${route.id} />`}
    ${section === "usage" && html`<${Usage} token=${token} />`}
    ${section === "evaluation" && html`<${Evaluation} token=${token} route=${route} />`}
    ${section === "teams" && html`<${Teams} token=${token} route=${route} />`}
    ${section === "plans" && html`<${Plans} token=${token} route=${route} />`}
    ${section === "map" && html`<${WorkMap} token=${token} />`}
    ${views.map((view) => section === view.id && html`<${view.component} key=${view.id} token=${token} status=${status} mock=${mock} route=${route} />`)}
    ${failed.length > 0 && html`<p class="muted small" role="status">No se pudo cargar: ${failed.map((item) => item.id).join(", ")}.</p>`}
    ${lastEvent && html`<p class="muted small">Último evento recibido: ${lastEvent}</p>`}
  <//>`;
}

const token = loadToken();
const root = document.getElementById("app");
if (token) {
  // The extensions register their screens before the first render, but the page never waits for them beyond LOAD_BUDGET_MS:
  // if the list hangs or fails, or a module is slow or broken, the core screens draw anyway, the notice lists what is missing,
  // and an extension that finishes late appears when it does (useRegistry).
  const start = () => render(html`<${App} token=${token} />`, root);
  try {
    loadExtensions({ fetchManifest: ({ signal }) => getJSON("/gui/api/extensions", token, signal) }).then(start, start);
  } catch (_) {
    start();
  }
} else {
  render(html`<${NoToken} />`, root);
}
