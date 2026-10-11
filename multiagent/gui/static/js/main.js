// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// App shell. All data is rendered as text through htm/Preact (no raw HTML injection).
import { html, render, useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { loadToken, clearToken, getJSON } from "./auth.js";
import { startStream } from "./stream.js";

const STATUS_TEXT = {
  connected: "Conectado",
  reconnecting: "Reconectando…",
  offline: "Sin conexión",
  unauthorized: "Token no válido",
};

const SECTIONS = [
  { id: "sessions", label: "Sesiones", text: "Aquí se verán las sesiones de Claude, Codex y Copilot." },
  { id: "runs", label: "Ejecuciones", text: "Aquí se verán las ejecuciones del framework y sus eventos." },
];

function NoToken() {
  return html`<main class="notice">
    <h1>Falta el token de acceso</h1>
    <p>Abre esta interfaz con el enlace que imprime <code>python -m multiagent.gui</code>;
      incluye el token después de <code>#token=</code>.</p>
  </main>`;
}

function App({ token }) {
  const [section, setSection] = useState("sessions");
  const [status, setStatus] = useState("reconnecting");
  const [mock, setMock] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);

  useEffect(() => {
    getJSON("/gui/api/session", token).then((s) => setMock(Boolean(s.mock))).catch(() => {});
    return startStream({
      token,
      onStatus: (value) => { if (value === "unauthorized") clearToken(); setStatus(value); },
      onEvent: (event) => setLastEvent(event.id),
    });
  }, [token]);

  const current = SECTIONS.find((s) => s.id === section);
  return html`<div class="shell">
    <header class="topbar">
      <h1>Multi-Agent Framework</h1>
      ${mock && html`<span class="badge mock" title="Resultados sintéticos, sin llamadas a modelos">MOCK</span>`}
      <span class=${"badge " + status} role="status" aria-live="polite">${STATUS_TEXT[status]}</span>
    </header>
    <nav class="nav" aria-label="Secciones">
      ${SECTIONS.map((s) => html`<button key=${s.id} aria-current=${s.id === section ? "page" : null}
        onClick=${() => setSection(s.id)}>${s.label}</button>`)}
    </nav>
    <main class="content">
      <h2>${current.label}</h2>
      <p class="muted">${current.text} (pendiente de implementar)</p>
      ${lastEvent && html`<p class="muted">Último evento recibido: ${lastEvent}</p>`}
    </main>
  </div>`;
}

const token = loadToken();
render(token ? html`<${App} token=${token} />` : html`<${NoToken} />`, document.getElementById("app"));
