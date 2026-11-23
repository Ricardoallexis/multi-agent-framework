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
import { useRoute, NEW_SECTION } from "./route.js";
import { Help, helpFor } from "./help.js";
import { NewSession } from "./new-session.js";
import { TeamThread } from "./team-thread.js";   // CHT-T05
import { PlanThread } from "./plan-thread.js";   // CHT-T06
import { RightPanel } from "./right-panel.js";
import { subjectOf } from "./panel-model.js";
import { AgentTree } from "./agent-tree.js";
import { Runs } from "./runs.js";
import { Sessions } from "./sessions.js";
import { Shell } from "./shell.js";
import { Sidebar } from "./sidebar.js";
import { PendingScreen } from "./pending.js";
import { WorkMap } from "./work-map.js";
import { Usage } from "./usage.js";
import { Customize } from "./customize.js";   // GUI3-T07
import { Evaluation } from "./evaluation.js";
import { Teams } from "./teams.js";
import { Plans } from "./plans.js";
import { Packages } from "./packages.js";   // PAQ-T02
import { Start } from "./start.js";   // INI-T03
import { setInstanceLabel } from "./instance.js";   // PRV-T01
import { ADVANCED_ONLY_TEXT, currentMode, onModeChange, saveMode, setCurrentMode, visibleIn } from "./gui-mode.js";   // SIM-T02
import { extensionFailures, loadExtensions, registeredViews, registryVersion, subscribeRegistry } from "./extensions.js";

const STATUS_TEXT = {
  connected: "Conectado",
  reconnecting: "Reconectando…",
  offline: "Sin conexión",
  unauthorized: "Token no válido",
};

// "Sesiones" is the main screen and the default one.
const SECTIONS = [
  { id: "sessions", label: "Sesiones", icon: "inbox", mode: "simple" },
  { id: "runs", label: "Ejecuciones", icon: "run", mode: "advanced" },
  { id: "usage", label: "Uso", icon: "chart", mode: "simple" },
  { id: "evaluation", label: "Evaluación", icon: "check", mode: "advanced" },
  { id: "teams", label: "Equipos", icon: "person", mode: "advanced" },
  { id: "plans", label: "Planes", icon: "file", mode: "advanced" },
  { id: "map", label: "Mapa", icon: "map", mode: "advanced" },
  { id: "customize", label: "Personalizar", icon: "settings", mode: "simple" },   // GUI3-T07: reached from «Más», the gear of the sidebar and Ctrl+K
  { id: "packages", label: "Paquetes", icon: "file", mode: "simple" },   // PAQ-T02: reached from «Más»
  { id: "start", label: "Inicio", icon: "check", mode: "simple" },   // INI-T03: in the sidebar while a step is missing, in «Más» after
];
// The built-in screens, then the ones extensions registered (by their `order`).
const allSections = () => [...SECTIONS, ...registeredViews().map((view) => ({ id: view.id, label: view.label, group: view.group, icon: view.icon, mode: view.mode }))];

// "#new" is not a section of its own: it shows the guided chat of a new session (new-session.js), which falls back to the
// create-run form on request.
const NEW_TITLE = "Nueva sesión";
// "#tree/<session id>" (GUI2-T08) is a second view of one session: it has no entry in the sidebar of its own.
const TREE_SECTION = "tree";
const TREE_TITLE = "Árbol de agentes";

// SIM-T02: in simple mode a route of an advanced view shows one line instead of the view (the address does not change).
function AdvancedOnly({ token }) {
  const [failed, setFailed] = useState(false);
  const enable = () => { setFailed(false); saveMode(token, "advanced").catch(() => setFailed(true)); };
  return html`<div class="advanced-only">
    <p class="advanced-only-text">${ADVANCED_ONLY_TEXT}</p>
    <button type="button" class="btn" ...${helpFor("shell.advanced_only")} onClick=${enable}>Activar modo avanzado</button>
    <${Help} topic="shell.advanced_only" compact=${true} />
    ${failed && html`<p class="message error small" role="alert">No se pudo cambiar el modo. Inténtalo de nuevo.</p>`}
  </div>`;
}

function NoToken() {
  return html`<main class="notice">
    <h1>Falta el token de acceso</h1>
    <p>Abre esta interfaz con el enlace que imprime <code>python -m multiagent.gui</code>;
      incluye el token después de <code>#token=</code>.</p>
  </main>`;
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
  const [contextDot, setContextDot] = useState(false);   // GUI3-T04: a card of «Contexto» has content
  const [mode, setMode] = useState(currentMode);          // SIM-T02: the server's mode, shared by the whole page
  const [modeKnown, setModeKnown] = useState(false);      // the line of an advanced route waits for the server's answer

  useEffect(() => onModeChange(setMode), []);
  useEffect(() => {
    getJSON("/gui/api/session", token).then((s) => { setMock(Boolean(s.mock)); setCurrentMode(s.mode); setModeKnown(true); setInstanceLabel(s.instance_label); })
      .catch(() => setModeKnown(true));
    return startStream({
      token,
      onStatus: (value) => { if (value === "unauthorized") clearToken(); setStatus(value); },
      onEvent: (event) => { setLastEvent(event.id); publishEvent(event); },
    });
  }, [token]);

  const section = route.section;
  useEffect(() => { window.scrollTo(0, 0); }, [section]);   // a new screen starts at its top, not at the old one's scroll
  const creating = section === NEW_SECTION;
  const tree = section === TREE_SECTION;
  const current = allSections().find((s) => s.id === section);
  const views = registeredViews();
  const pendingViews = views.filter((view) => view.group === "pending");
  const inPending = section === "pending" || pendingViews.some((view) => view.id === section);   // GUI3-T05: one screen, a tab per view
  const entry = !creating && !tree && !inPending ? views.find((view) => view.id === section) : null;
  const blocked = modeKnown && !creating && !inPending && !visibleIn(tree ? { mode: "advanced" } : current, mode);   // SIM-T02
  // The sidebar and the right panel treat the tree of a session as the session itself (it stays marked, its panel stays).
  const context = tree ? { section: "sessions", id: route.id } : route;
  const sidebar = html`<${Sidebar} token=${token} status=${status} route=${context} sections=${allSections()} mode=${mode} />`;
  const panel = html`<${RightPanel} token=${token} route=${context} onDot=${setContextDot} />`;
  const hasSubject = Boolean(subjectOf(context, context.section === "sessions" ? context.id : null));   // a session or a run is open
  return html`<${Shell} status=${status} statusText=${STATUS_TEXT[status]} mock=${mock} sidebar=${sidebar} panel=${panel}
    hasSubject=${hasSubject} contextDot=${contextDot}>
    ${section === "usage" ? html`<h2 id="usage-title">Uso</h2>` : html`<h2>${creating ? NEW_TITLE : tree ? TREE_TITLE : inPending ? "Pendientes" : current?.label}</h2>`}
    ${blocked && html`<${AdvancedOnly} token=${token} />`}
    ${!blocked && entry?.topic && html`<${Help} key=${entry.id} topic=${entry.topic} />`}
    <${Sessions} token=${token} route=${route} active=${section === "sessions" && !blocked} />
    ${!blocked && tree && html`<${AgentTree} key=${route.id || ""} token=${token} sessionId=${route.id} />`}
    ${creating && route.kind === "team" && html`<${TeamThread} key=${route.id} token=${token} versionId=${route.id} />`}
    ${creating && route.kind === "plan" && html`<${PlanThread} key=${route.id} token=${token} proposalId=${route.id} />`}
    ${creating && route.kind !== "team" && route.kind !== "plan" && html`<${NewSession} token=${token} mock=${mock} />`}
    ${!blocked && section === "runs" && html`<${Runs} token=${token} status=${status} mock=${mock} initialRunId=${route.id} />`}
    ${!blocked && section === "usage" && html`<${Usage} token=${token} />`}
    ${!blocked && section === "evaluation" && html`<${Evaluation} token=${token} route=${route} />`}
    ${!blocked && section === "teams" && html`<${Teams} token=${token} route=${route} />`}
    ${!blocked && section === "plans" && html`<${Plans} token=${token} route=${route} />`}
    ${!blocked && section === "map" && html`<${WorkMap} token=${token} />`}
    ${section === "customize" && html`<${Customize} token=${token} sections=${allSections()} mode=${mode} />`}
    ${!blocked && section === "packages" && html`<${Packages} token=${token} />`}
    ${!blocked && section === "start" && html`<${Start} token=${token} />`}
    ${inPending && html`<${PendingScreen} views=${pendingViews} route=${route} token=${token} status=${status} mock=${mock} />`}
    ${views.map((view) => !blocked && !inPending && section === view.id && html`<${view.component} key=${view.id} token=${token} status=${status} mock=${mock} route=${route} />`)}
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
