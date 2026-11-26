// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// App: connects the stream and puts the view of the current route inside the shell (shell.js: top bar, left sidebar,
// centre, right panel). All data is rendered as text through htm/Preact (no raw HTML injection).
// Screens, right-panel sections and help texts that tasks add come from static/extensions/<name>/ (extensions.js, FLU-T04):
// loadExtensions() runs before the first render (for at most LOAD_BUDGET_MS), so a registered view is one more button in the
// sidebar and one more route here, and nothing in this file changes when an extension is added.
import { html, render, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { loadToken, clearToken, getJSON } from "./auth.js";
import { startStream } from "./stream.js";
import { publishEvent } from "./bus.js";
import { initialSection, navigate, useRoute, NEW_SECTION } from "./route.js";
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
import { Start, useStartStatus } from "./start.js";   // INI-T03
import { inStartSidebar } from "./start-model.js";   // INI-T03
import { setInstanceLabel } from "./instance.js";   // PRV-T01
import { ADVANCED_ONLY_TEXT, currentMode, onModeChange, saveMode, setCurrentMode, visibleIn } from "./gui-mode.js";   // SIM-T02
import { extensionFailures, loadExtensions, registeredViews, registryVersion, subscribeRegistry } from "./extensions.js";
import { onLanguageChange, t } from "./i18n.js";

const STATUS_TEXT = {
  connected: t("main.connected"),
  reconnecting: t("main.reconnecting"),
  offline: t("main.offline"),
  unauthorized: t("main.unauthorized"),
};

// "Sesiones" is the main screen and the default one.
const SECTIONS = [
  { id: "sessions", label: t("main.sessions"), icon: "inbox", mode: "simple" },
  { id: "runs", label: t("main.runs"), icon: "run", mode: "advanced" },
  { id: "usage", label: t("main.usage"), icon: "chart", mode: "simple" },
  { id: "evaluation", label: t("main.evaluation"), icon: "check", mode: "advanced" },
  { id: "teams", label: t("main.teams"), icon: "person", mode: "advanced" },
  { id: "plans", label: t("main.plans"), icon: "file", mode: "advanced" },
  { id: "map", label: t("main.map"), icon: "map", mode: "advanced" },
  { id: "customize", label: t("main.customize"), icon: "settings", mode: "simple" },   // GUI3-T07: reached from «Más», the gear of the sidebar and Ctrl+K
  { id: "packages", label: t("main.packages"), icon: "file", mode: "simple" },   // PAQ-T02: reached from «Más»
  { id: "start", label: t("main.start"), icon: "check", mode: "simple" },   // INI-T03: in the sidebar while a step is missing, in «Más» after
];
// The built-in screens, then the ones extensions registered (by their `order`).
const allSections = (mode = currentMode()) => [...SECTIONS.map((section) => ({
  ...section,
  label: section.id === "sessions" && mode === "simple" ? t([section.id, "all_conversations"].join(".")) : t("main." + section.id),
})), ...registeredViews().map((view) => ({ id: view.id, label: view.label, group: view.group, icon: view.icon, mode: view.mode }))];

// "#new" is not a section of its own: it shows the guided chat of a new session (new-session.js), which falls back to the
// create-run form on request.
// "#tree/<session id>" (GUI2-T08) is a second view of one session: it has no entry in the sidebar of its own.
const TREE_SECTION = "tree";

// SIM-T02: in simple mode a route of an advanced view shows one line instead of the view (the address does not change).
function AdvancedOnly({ token }) {
  const [failed, setFailed] = useState(false);
  const enable = () => { setFailed(false); saveMode(token, "advanced").catch(() => setFailed(true)); };
  return html`<div class="advanced-only">
    <p class="advanced-only-text">${ADVANCED_ONLY_TEXT}</p>
    <button type="button" class="btn" ...${helpFor("shell.advanced_only")} onClick=${enable}>${t("main.advanced_only")}</button>
    <${Help} topic="shell.advanced_only" compact=${true} />
    ${failed && html`<p class="message error small" role="alert">${t("main.mode_error")}</p>`}
  </div>`;
}

function NoToken() {
  return html`<main class="notice">
    <h1>${t("main.missing_token")}</h1>
    <p>${t("main.open_link")} <code>python -m multiagent.gui</code>${t("main.token_fragment")} <code>#token=</code>.</p>
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
  const [, setLanguageVersion] = useState(0);
  useEffect(() => onLanguageChange(() => {
    for (const key of Object.keys(STATUS_TEXT)) STATUS_TEXT[key] = t("main." + key);
    setLanguageVersion((version) => version + 1);
  }), []);
  const route = useRoute();
  const start = useStartStatus(token);
  const initialAddressDone = useRef(Boolean(location.hash));
  const failed = extensionFailures();
  const [status, setStatus] = useState("reconnecting");
  const [mock, setMock] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);
  const [contextDot, setContextDot] = useState(false);   // GUI3-T04: a card of «Contexto» has content
  const [mode, setMode] = useState(currentMode);          // SIM-T02: the server's mode, shared by the whole page
  const [modeKnown, setModeKnown] = useState(false);      // the line of an advanced route waits for the server's answer

  useEffect(() => onModeChange(setMode), []);
  useEffect(() => {
    if (initialAddressDone.current || !modeKnown) return;
    if (location.hash) { initialAddressDone.current = true; return; }
    if (mode === "simple" && !start.status && !start.failed) return;
    navigate(initialSection(mode, inStartSidebar(start.status)));
    initialAddressDone.current = true;
  }, [mode, modeKnown, start.status, start.failed]);
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
  const sections = allSections(mode);
  const current = sections.find((s) => s.id === section);
  const views = registeredViews();
  const pendingViews = views.filter((view) => view.group === "pending");
  const inPending = section === "pending" || pendingViews.some((view) => view.id === section);   // GUI3-T05: one screen, a tab per view
  const entry = !creating && !tree && !inPending ? views.find((view) => view.id === section) : null;
  const blocked = modeKnown && !creating && !inPending && !visibleIn(tree ? { mode: "advanced" } : current, mode);   // SIM-T02
  // The sidebar and the right panel treat the tree of a session as the session itself (it stays marked, its panel stays).
  const context = tree ? { section: "sessions", id: route.id } : route;
  const sidebar = html`<${Sidebar} token=${token} status=${status} route=${context} sections=${sections} mode=${mode} />`;
  const panel = html`<${RightPanel} token=${token} route=${context} onDot=${setContextDot} />`;
  const hasSubject = Boolean(subjectOf(context, context.section === "sessions" ? context.id : null));   // a session or a run is open
  return html`<${Shell} status=${status} statusText=${STATUS_TEXT[status]} mock=${mock} sidebar=${sidebar} panel=${panel}
    hasSubject=${hasSubject} contextDot=${contextDot}>
    ${section === "usage" ? html`<h2 id="usage-title">${t("main.usage")}</h2>` : html`<h2>${creating ? t("main.new_session") : tree ? t("main.agent_tree") : inPending ? t("main.pending") : current?.label}</h2>`}
    ${blocked && html`<${AdvancedOnly} token=${token} />`}
    ${!blocked && entry?.topic && html`<${Help} key=${entry.id} topic=${entry.topic} />`}
    <${Sessions} token=${token} route=${route} active=${section === "sessions" && !blocked} mode=${mode} />
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
    ${section === "customize" && html`<${Customize} token=${token} sections=${sections} mode=${mode} />`}
    ${!blocked && section === "packages" && html`<${Packages} token=${token} />`}
    ${!blocked && section === "start" && html`<${Start} token=${token} />`}
    ${inPending && html`<${PendingScreen} views=${pendingViews} route=${route} token=${token} status=${status} mock=${mock} />`}
    ${views.map((view) => !blocked && !inPending && section === view.id && html`<${view.component} key=${view.id} token=${token} status=${status} mock=${mock} route=${route} />`)}
    ${failed.length > 0 && html`<p class="muted small" role="status">${t("main.load_failed")} ${failed.map((item) => item.id).join(", ")}.</p>`}
    ${lastEvent && html`<p class="muted small">${t("main.latest_event")} ${lastEvent}</p>`}
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
