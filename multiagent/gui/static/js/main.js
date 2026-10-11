// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// App: connects the stream and puts the view of the current route inside the shell (shell.js: top bar, left sidebar,
// centre, right panel). All data is rendered as text through htm/Preact (no raw HTML injection).
import { html, render, useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { loadToken, clearToken, getJSON } from "./auth.js";
import { startStream } from "./stream.js";
import { publishEvent } from "./bus.js";
import { navigate, useRoute, NEW_SECTION } from "./route.js";
import { RightPanel } from "./right-panel.js";
import { Runs } from "./runs.js";
import { Sessions } from "./sessions.js";
import { Shell } from "./shell.js";
import { Sidebar } from "./sidebar.js";

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
];

// "#new" is not a section of its own: for now it shows the create-run form (inside "Ejecuciones").
const NEW_TITLE = "Nueva sesión";

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
    ${SECTIONS.map((s) => html`<button key=${s.id} type="button" data-nav data-help-exempt="navigation" aria-current=${s.id === section ? "page" : null}
      onClick=${() => navigate(s.id)}>${s.label}</button>`)}
  </div>`;
}

function App({ token }) {
  const route = useRoute();
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
  const current = SECTIONS.find((s) => s.id === section);
  const sidebar = html`<${Sidebar} token=${token} status=${status} route=${route} views=${html`<${Views} section=${section} />`} />`;
  const panel = html`<${RightPanel} token=${token} route=${route} />`;
  return html`<${Shell} status=${status} statusText=${STATUS_TEXT[status]} mock=${mock} sidebar=${sidebar} panel=${panel}>
    <h2>${creating ? NEW_TITLE : current.label}</h2>
    <${Sessions} token=${token} route=${route} active=${section === "sessions"} />
    ${(section === "runs" || creating) && html`<${Runs} token=${token} status=${status} mock=${mock} initialRunId=${route.id} startCreating=${creating} />`}
    ${lastEvent && html`<p class="muted small">Último evento recibido: ${lastEvent}</p>`}
  <//>`;
}

const token = loadToken();
render(token ? html`<${App} token=${token} />` : html`<${NoToken} />`, document.getElementById("app"));
