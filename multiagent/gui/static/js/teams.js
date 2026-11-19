// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Vista de equipos (EQP-T05): los equipos con su versión aprobada y la propuesta pendiente y, al abrir uno ("#teams/<bundle>"),
// su organigrama como lista anidada, el texto Mermaid copiable, sus versiones con su diff y la decisión de aprobar o
// rechazar la propuesta pendiente. La decisión la toma una persona: el servidor fija el actor y el canal. Todo lo que llega
// (nombres, propósitos, notas) es texto no confiable y se muestra solo como texto.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { Confirm } from "./human-actions.js";
import { routeHref } from "./route.js";
import { StatusChip } from "./ui/index.js";
import { TeamCardBody, TeamDiffBody } from "./team-card.js";   // CHT-T05: the same card as the chat

const BASE = "/gui/api/teams";
const enc = encodeURIComponent;

export const POSITION_STATUS = {
  occupied: { text: "Ocupado", tone: "success", explanation: "El puesto tiene a alguien asignado." },
  vacant: { text: "Vacante", tone: "attention", explanation: "El puesto no tiene a nadie asignado." },
  pending: { text: "Pendiente de aprobar", tone: "neutral", explanation: "El puesto o su ocupante cambia en la propuesta pendiente." },
};
export const VERSION_STATUS = {
  draft: { text: "Borrador", tone: "muted" },
  proposed: { text: "Propuesta", tone: "attention" },
  approved: { text: "Aprobada", tone: "success" },
  rejected: { text: "Rechazada", tone: "danger" },
};
export const LINK_TEXT = { reviews: "revisa a", delegates_to: "delega en", reports_to: "reporta a" };
export const SECTION_TEXT = { roles: "Puestos", members: "Miembros", assignments: "Asignaciones", relationships: "Relaciones" };
const BUCKET_TEXT = { added: "Añadidos", removed: "Quitados", changed: "Cambiados" };
// Where the chart comes from (services.teams.org_chart ``source``), in words.
export const SOURCE_TEXT = { approved: "la versión aprobada", proposed: "la última propuesta", draft: "el último borrador",
  "team.yaml": "el team.yaml del bundle, sin versiones guardadas", version: "la versión elegida" };

const when = (value) => {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Fecha no disponible" : new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(date);
};
export const versionText = (version) => version ? "v" + version.version + " · " + (VERSION_STATUS[version.status] || { text: version.status }).text : "Ninguna";

// The members of a position as text ("Ana (human)"); an empty list reads as nobody.
export const membersText = (members) => Array.isArray(members) && members.length
  ? members.map((member) => (member.name || member.id) + (member.kind ? " (" + member.kind + ")" : "")).join(", ") : "Nadie";

// The org chart as a forest of { position, children }: the roots first, each with the positions that report to it.
// A position is drawn once even if the data were inconsistent (a cycle never hangs the view).
export function buildTree(chart) {
  const positions = Array.isArray(chart && chart.positions) ? chart.positions : [];
  const byId = new Map(positions.map((position) => [position.role_id, position]));
  const seen = new Set();
  const node = (id) => {
    seen.add(id);
    const position = byId.get(id);
    const children = (position.reports || []).filter((child) => byId.has(child) && !seen.has(child)).map(node);
    return { position, children };
  };
  const roots = (Array.isArray(chart && chart.roots) ? chart.roots : []).filter((id) => byId.has(id) && !seen.has(id));
  const tree = [];
  for (const id of roots) if (!seen.has(id)) tree.push(node(id));
  for (const position of positions) if (!seen.has(position.role_id)) tree.push(node(position.role_id));
  return tree;
}

// One line for an item of a diff section.
export function describeItem(section, item) {
  if (!item) return "";
  if (section === "assignments") return item.member + " en " + item.role;
  if (section === "relationships") return item.from + " " + (LINK_TEXT[item.type] || item.type) + " " + item.to;
  return String(item.id);
}
export const fieldChanges = (fields) => Object.keys(fields || {}).map((name) => name + ": " + JSON.stringify(fields[name].before) + " → " + JSON.stringify(fields[name].after));

// The error of the service as it came: its message and its code.
export const failureText = (error) => (error && error.message ? error.message : "Error desconocido") + (error && error.code ? " (" + error.code + ")" : "");

function Failure({ error }) {
  return html`<p class="message error" role="alert">${error ? failureText(error) : "No se pudo leer el equipo."}</p>`;
}

function useLoad(path, token) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  const ticket = useRef(0);
  const load = async () => {
    const mine = ++ticket.current;
    setState((previous) => ({ ...previous, loading: true }));
    try {
      const data = await apiGet(path, token);
      if (mine === ticket.current) setState({ loading: false, data, error: null });
    } catch (error) {
      if (mine === ticket.current) setState((previous) => ({ loading: false, data: previous.data, error }));
    }
  };
  useEffect(() => { load(); return () => { ticket.current += 1; }; }, [path, token]);
  return [state, load];
}

export function TeamList({ data }) {
  const teams = Array.isArray(data && data.teams) ? data.teams : [];
  if (!teams.length) return html`<p>Ningún bundle registrado tiene un equipo definido.</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">Equipos</caption>
    <thead><tr><th scope="col">Equipo</th><th scope="col">Versión aprobada</th><th scope="col">Propuesta pendiente</th></tr></thead>
    <tbody>${teams.map((entry) => html`<tr key=${entry.bundle}>
      <th scope="row"><a href=${routeHref("teams", entry.bundle)}>${entry.bundle}</a></th>
      <td>${entry.approved ? "v" + entry.approved.version + " · " + when(entry.approved.decided_at) : "Ninguna"}</td>
      <td>${entry.pending ? html`<${StatusChip} status="proposed" labels=${VERSION_STATUS} /> ${"v" + entry.pending.version + " · " + when(entry.pending.proposed_at)}` : "Ninguna"}</td>
    </tr>`)}</tbody>
  </table></div>`;
}

function TreeNode({ node }) {
  const { position } = node;
  const status = POSITION_STATUS[position.status] || { text: position.status };
  return html`<li>
    <strong>${position.role_id}</strong> <${StatusChip} status=${position.status} labels=${POSITION_STATUS} variant="inline" />
    ${position.pending_removal && html` <span class="badge">Se quitará con la propuesta</span>`}
    <div class="muted small">${position.purpose}</div>
    <div class="small">Ocupa: ${membersText(position.members)}. Estado: ${status.text}.</div>
    ${node.children.length > 0 && html`<ul>${node.children.map((child) => html`<${TreeNode} key=${child.position.role_id} node=${child} />`)}</ul>`}
  </li>`;
}

export function OrgChartView({ chart }) {
  const tree = buildTree(chart);
  const links = Array.isArray(chart.links) ? chart.links.filter((link) => link.type !== "reports_to") : [];
  const [copied, setCopied] = useState(null);
  const copy = async () => {
    try { await navigator.clipboard.writeText(chart.mermaid); setCopied("Mermaid copiado."); }
    catch (_) { setCopied("No se pudo copiar: selecciona el texto y cópialo a mano."); }
  };
  return html`<div>
    <p class="muted small">Origen: ${SOURCE_TEXT[chart.source] || chart.source}${chart.version ? ", versión " + chart.version : ""}.
      ${chart.pending_version_id ? " Hay una propuesta pendiente: sus cambios se marcan como pendientes." : ""}</p>
    ${chart.forest && html`<p class="notice">El equipo tiene varias raíces: es un bosque, no un solo árbol.</p>`}
    <h4>Organigrama</h4><${Help} topic="teams.chart" />
    ${tree.length ? html`<ul class="team-tree" aria-label="Organigrama de puestos">${tree.map((node) => html`<${TreeNode} key=${node.position.role_id} node=${node} />`)}</ul>`
      : html`<p>El equipo no tiene puestos.</p>`}
    <h4>Relaciones aparte de la jerarquía</h4><${Help} topic="teams.links" />
    ${links.length ? html`<ul>${links.map((link, index) => html`<li key=${index}>${link.from} ${LINK_TEXT[link.type] || link.type} ${link.to}</li>`)}</ul>`
      : html`<p>No hay revisiones ni delegaciones declaradas.</p>`}
    <h4>Texto Mermaid</h4><${Help} topic="teams.mermaid" />
    <pre class="data" tabindex="0" aria-label="Texto Mermaid del organigrama"><code>${chart.mermaid}</code></pre>
    <div class="actions"><button type="button" class="btn secondary small" ...${helpFor("teams.copy")} onClick=${copy}>Copiar</button>
      <${Help} topic="teams.copy" compact=${true} /></div>
    ${copied && html`<p class="muted small" role="status" aria-live="polite">${copied}</p>`}
  </div>`;
}

export function DiffView({ diff }) {
  if (!diff) return null;
  if (!diff.has_changes) return html`<p>Sin cambios respecto de la versión de comparación.</p>`;
  const fields = fieldChanges(diff.team_fields);
  return html`<div>
    <p class="muted small">${diff.base_version ? "Frente a la versión " + diff.base_version + " aprobada." : "No hay versión aprobada con la que comparar."}</p>
    ${fields.length > 0 && html`<p><strong>Datos del equipo:</strong> ${fields.join("; ")}.</p>`}
    ${Object.keys(SECTION_TEXT).map((section) => {
      const parts = diff[section] || {};
      const buckets = Object.keys(BUCKET_TEXT).filter((bucket) => (parts[bucket] || []).length);
      if (!buckets.length) return null;
      return html`<div key=${section}><strong>${SECTION_TEXT[section]}</strong><ul>${buckets.map((bucket) => html`<li key=${bucket}>${BUCKET_TEXT[bucket]}:
        ${parts[bucket].map((item) => bucket === "changed" ? item.id + " (" + fieldChanges(item.fields).join("; ") + ")" : describeItem(section, item)).join(", ")}.</li>`)}</ul></div>`;
    })}
  </div>`;
}

function VersionsTable({ versions, openId, onOpen }) {
  if (!versions.length) return html`<p>Este equipo todavía no tiene versiones guardadas.</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">Versiones del equipo</caption>
    <thead><tr><th scope="col">Versión</th><th scope="col">Estado</th><th scope="col">Propuesta por</th><th scope="col">Decisión</th><th scope="col">Cambios</th></tr></thead>
    <tbody>${versions.map((version) => html`<tr key=${version.id}>
      <th scope="row">v${version.version}${version.active ? " (activa)" : ""}</th>
      <td><${StatusChip} status=${version.status} labels=${VERSION_STATUS} /></td>
      <td>${version.proposed_by || version.created_by || "Sin dato"} <span class="muted small">${when(version.proposed_at || version.created_at)}</span></td>
      <td>${version.decided_by ? version.decided_by + " · " + when(version.decided_at) + (version.note ? " · " + version.note : "") : "Sin decidir"}</td>
      <td><button type="button" class="btn secondary small" aria-pressed=${openId === version.id ? "true" : "false"}
        ...${helpFor("teams.diff", { scope: version.id })} onClick=${() => onOpen(openId === version.id ? null : version.id)}>${openId === version.id ? "Ocultar cambios" : "Ver cambios"}</button>
        <${Help} topic="teams.diff" scope=${version.id} compact=${true} /></td>
    </tr>`)}</tbody>
  </table></div>`;
}

function VersionDiff({ token, versionId }) {
  const [state] = useLoad(BASE + "/versions/" + enc(versionId), token);
  if (state.error) return html`<${Failure} error=${state.error} />`;
  if (!state.data) return html`<p role="status" aria-live="polite">Cargando los cambios…</p>`;
  // CHT-T05: the card (mode, rules, permissions, model per role) and the full diff, the same as in the chat.
  return html`<div class="usage-comparison"><h5>Versión ${state.data.version}</h5>
    ${state.data.card && html`<${TeamCardBody} card=${state.data.card} />`}<${TeamDiffBody} diff=${state.data.diff} /></div>`;
}

// Approve or reject the pending proposal, each behind a confirmation with an optional note. The service answers; its
// error is shown as it came (for example, a proposal that someone already decided), and the page reads again.
function Decision({ token, pending, onDone }) {
  const [confirm, setConfirm] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  useEffect(() => { setConfirm(null); setNote(""); }, [pending.id]);
  const decide = async (action, okText) => {
    if (busy) return;
    setBusy(true);
    setMessage({ kind: "info", text: "Enviando…" });
    try {
      await apiPost(BASE + "/versions/" + enc(pending.id) + "/" + action, token, note.trim() ? { note: note.trim() } : {});
      setConfirm(null);
      setMessage({ kind: "ok", text: okText });
    } catch (error) {
      setConfirm(null);
      setMessage({ kind: "error", text: failureText(error instanceof ApiError ? error : new ApiError({ message: String(error) })) });
    } finally {
      setBusy(false);
      onDone();
    }
  };
  const box = (action, title, confirmLabel, text, okText, danger) => html`<${Confirm} title=${title} confirmLabel=${confirmLabel}
    keepLabel="Mantenerla pendiente" danger=${danger} busy=${busy} onKeep=${() => setConfirm(null)} onConfirm=${() => decide(action, okText)}>
    <p class="muted">${text}</p>
    <label for="team-note">Nota (opcional)</label><${Help} topic="teams.note" />
    <textarea id="team-note" rows="2" maxlength="4000" value=${note} ...${helpFor("teams.note")} onInput=${(event) => setNote(event.target.value)}></textarea>
  <//>`;
  return html`<div>
    <p>La versión ${pending.version} la propuso ${pending.proposed_by || "alguien"} y espera una decisión.</p>
    ${confirm === "approve" ? box("approve", "¿Aprobar la propuesta?", "Sí, aprobar", "Pasará a ser la versión activa del equipo. La decisión queda registrada a nombre del operador.", "La propuesta quedó aprobada.", false)
      : confirm === "reject" ? box("reject", "¿Rechazar la propuesta?", "Sí, rechazar", "Quedará rechazada y la versión aprobada actual no cambia. No se puede deshacer.", "La propuesta quedó rechazada.", true)
      : html`<div class="actions">
        <span class="action-item"><button type="button" class="btn" disabled=${busy} ...${helpFor("teams.approve")} onClick=${() => setConfirm("approve")}>Aprobar propuesta…</button>
          <${Help} topic="teams.approve" /></span>
        <span class="action-item"><button type="button" class="btn secondary" disabled=${busy} ...${helpFor("teams.reject")} onClick=${() => setConfirm("reject")}>Rechazar propuesta…</button>
          <${Help} topic="teams.reject" /></span>
      </div>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </div>`;
}

function TeamDetail({ token, bundle, reload }) {
  const path = BASE + "/" + enc(bundle);
  const [list, loadList] = useLoad(BASE, token);
  const [chart, loadChart] = useLoad(path + "/org-chart", token);
  const [versions, loadVersions] = useLoad(path + "/versions", token);
  const [openId, setOpenId] = useState(null);
  const refresh = () => { loadList(); loadChart(); loadVersions(); };
  useEffect(() => { reload.current = refresh; return () => { reload.current = null; }; });
  const entry = list.data && Array.isArray(list.data.teams) ? list.data.teams.find((item) => item.bundle === bundle) : null;
  const pending = entry ? entry.pending : null;
  return html`<div>
    <p><a href=${routeHref("teams")}>← Todos los equipos</a></p>
    <h4>Equipo ${bundle}</h4>
    <p>Versión aprobada: ${entry ? (entry.approved ? versionText(entry.approved) : "Ninguna") : "…"}.</p>
    <h4>Propuesta pendiente</h4><${Help} topic="teams.decision" />
    ${list.error && html`<${Failure} error=${list.error} />`}
    ${pending ? html`<${Decision} token=${token} pending=${pending} onDone=${refresh} />` : html`<p>No hay ninguna propuesta pendiente.</p>`}
    ${chart.error && html`<${Failure} error=${chart.error} />`}
    ${chart.data ? html`<${OrgChartView} chart=${chart.data} />` : !chart.error && html`<p role="status" aria-live="polite">Cargando el organigrama…</p>`}
    <h4>Versiones</h4><${Help} topic="teams.versions" />
    ${versions.error && html`<${Failure} error=${versions.error} />`}
    ${versions.data && html`<${VersionsTable} versions=${versions.data.versions || []} openId=${openId} onOpen=${setOpenId} />`}
    ${openId && html`<${VersionDiff} key=${openId} token=${token} versionId=${openId} />`}
  </div>`;
}

export function Teams({ token, route }) {
  const bundle = route && route.id ? route.id : null;
  const [teams, loadTeams] = useLoad(BASE, token);
  const reload = useRef(null);
  const refresh = () => { if (bundle) { if (reload.current) reload.current(); } else loadTeams(); };
  const busy = !bundle && teams.loading;
  return html`<div class="usage-view">
    <h3 id="teams-title">Equipos y organigramas</h3><${Help} topic="teams.section" />
    <div class="usage-title-row"><p class="muted">Equipos de los bundles, su organigrama y sus versiones. Una persona decide si aprueba o rechaza una propuesta.</p>
      <div class="usage-refresh-control" role="group" aria-label="Actualizar los equipos">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("teams.refresh")} onClick=${refresh}>${busy ? "Actualizando…" : "Actualizar"}</button>
        <${Help} topic="teams.refresh" compact=${true} />
      </div>
    </div>
    ${bundle ? html`<${TeamDetail} key=${bundle} token=${token} bundle=${bundle} reload=${reload} />` : html`<div>
      <h4>Lista de equipos</h4><${Help} topic="teams.list" />
      ${teams.error && html`<${Failure} error=${teams.error} />`}
      ${teams.loading && !teams.data ? html`<p role="status" aria-live="polite">Cargando los equipos…</p>` : teams.data && html`<${TeamList} data=${teams.data} />`}
    </div>`}
  </div>`;
}
