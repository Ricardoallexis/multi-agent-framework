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
import { VERSION_STATUS, versionStatusText } from "./ui/labels.js";
import { TeamCardBody, TeamDiffBody } from "./team-card.js";   // CHT-T05: the same card as the chat
import { currentLanguage, t } from "./i18n.js";

const BASE = "/gui/api/teams";
const enc = encodeURIComponent;
export { VERSION_STATUS };

export const POSITION_STATUS = {
  occupied: { get text() { return t("teams.position.occupied"); }, tone: "success", get explanation() { return t("teams.position.explanation.occupied"); } },
  vacant: { get text() { return t("teams.position.vacant"); }, tone: "attention", get explanation() { return t("teams.position.explanation.vacant"); } },
  pending: { get text() { return t("teams.position.pending"); }, tone: "neutral", get explanation() { return t("teams.position.explanation.pending"); } },
};
export const LINK_TEXT = {
  get reviews() { return t("teams.link.reviews"); },
  get delegates_to() { return t("teams.link.delegates_to"); },
  get reports_to() { return t("teams.link.reports_to"); },
};
export const SECTION_TEXT = {
  get roles() { return t("teams.section.roles"); },
  get members() { return t("teams.section.members"); },
  get assignments() { return t("teams.section.assignments"); },
  get relationships() { return t("teams.section.relationships"); },
};
const BUCKET_TEXT = {
  get added() { return t("teams.bucket.added"); },
  get removed() { return t("teams.bucket.removed"); },
  get changed() { return t("teams.bucket.changed"); },
};
// Where the chart comes from (services.teams.org_chart ``source``), in words.
export const SOURCE_TEXT = {
  get approved() { return t("teams.source.approved"); },
  get proposed() { return t("teams.source.proposed"); },
  get draft() { return t("teams.source.draft"); },
  get ["team.yaml"]() { return t("teams.source.team_yaml"); },
  get version() { return t("teams.source.version"); },
};

const when = (value) => {
  if (!value) return t("teams.date.none");
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? t("teams.date.unavailable")
    : new Intl.DateTimeFormat(currentLanguage() === "es" ? "es-MX" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(date);
};
export const versionText = (version) => version ? t("teams.version.prefix") + version.version + " · " + versionStatusText(version.status) : t("teams.version.none");

// The members of a position as text ("Ana (human)"); an empty list reads as nobody.
export const membersText = (members) => Array.isArray(members) && members.length
  ? members.map((member) => (member.name || member.id) + (member.kind ? " (" + member.kind + ")" : "")).join(", ") : t("teams.member.none");

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
  if (section === "assignments") return item.member + " " + t("teams.diff.in") + " " + item.role;
  if (section === "relationships") return item.from + " " + (LINK_TEXT[item.type] || item.type) + " " + item.to;
  return String(item.id);
}
export const fieldChanges = (fields) => Object.keys(fields || {}).map((name) => name + ": " + JSON.stringify(fields[name].before) + " → " + JSON.stringify(fields[name].after));

// The error of the service as it came: its message and its code.
export const failureText = (error) => (error && error.message ? error.message : t("teams.error.unknown")) + (error && error.code ? " (" + error.code + ")" : "");

function Failure({ error }) {
  return html`<p class="message error" role="alert">${error ? failureText(error) : t("teams.error.read_team")}</p>`;
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
  if (!teams.length) return html`<p>${t("teams.list.empty")}</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">${t("teams.list.caption")}</caption>
    <thead><tr><th scope="col">${t("teams.list.team")}</th><th scope="col">${t("teams.list.approved_version")}</th><th scope="col">${t("teams.list.pending_proposal")}</th></tr></thead>
    <tbody>${teams.map((entry) => html`<tr key=${entry.bundle}>
      <th scope="row"><a href=${routeHref("teams", entry.bundle)}>${entry.bundle}</a></th>
      <td>${entry.approved ? t("teams.version.prefix") + entry.approved.version + " · " + when(entry.approved.decided_at) : t("teams.version.none")}</td>
      <td>${entry.pending ? html`<${StatusChip} status="proposed" labels=${VERSION_STATUS} /> ${t("teams.version.prefix") + entry.pending.version + " · " + when(entry.pending.proposed_at)}` : t("teams.version.none")}</td>
    </tr>`)}</tbody>
  </table></div>`;
}

function TreeNode({ node }) {
  const { position } = node;
  const status = POSITION_STATUS[position.status] || { text: position.status };
  return html`<li>
    <strong>${position.role_id}</strong> <${StatusChip} status=${position.status} labels=${POSITION_STATUS} variant="inline" />
    ${position.pending_removal && html` <span class="badge">${t("teams.position.pending_removal")}</span>`}
    <div class="muted small">${position.purpose}</div>
    <div class="small">${t("teams.position.has")}: ${membersText(position.members)}. ${t("teams.position.status")}: ${status.text}.</div>
    ${node.children.length > 0 && html`<ul>${node.children.map((child) => html`<${TreeNode} key=${child.position.role_id} node=${child} />`)}</ul>`}
  </li>`;
}

export function OrgChartView({ chart }) {
  const tree = buildTree(chart);
  const links = Array.isArray(chart.links) ? chart.links.filter((link) => link.type !== "reports_to") : [];
  const [copied, setCopied] = useState(null);
  const copy = async () => {
    try { await navigator.clipboard.writeText(chart.mermaid); setCopied(t("teams.org.copy_success")); }
    catch (_) { setCopied(t("teams.org.copy_failure")); }
  };
  return html`<div>
    <p class="muted small">${t("teams.org.source")}: ${SOURCE_TEXT[chart.source] || chart.source}${chart.version ? ", " + t("teams.org.version") + " " + chart.version : ""}.
      ${chart.pending_version_id ? " " + t("teams.org.pending") : ""}</p>
    ${chart.forest && html`<p class="notice">${t("teams.org.forest")}</p>`}
    <h4>${t("teams.org.chart")}</h4><${Help} topic="teams.chart" />
    ${tree.length ? html`<ul class="team-tree" aria-label=${t("teams.org.roles_aria")}>${tree.map((node) => html`<${TreeNode} key=${node.position.role_id} node=${node} />`)}</ul>`
      : html`<p>${t("teams.org.empty")}</p>`}
    <h4>${t("teams.org.other_links")}</h4><${Help} topic="teams.links" />
    ${links.length ? html`<ul>${links.map((link, index) => html`<li key=${index}>${link.from} ${LINK_TEXT[link.type] || link.type} ${link.to}</li>`)}</ul>`
      : html`<p>${t("teams.org.no_links")}</p>`}
    <h4>${t("teams.org.mermaid")}</h4><${Help} topic="teams.mermaid" />
    <pre class="data" tabindex="0" aria-label=${t("teams.org.mermaid_aria")}><code>${chart.mermaid}</code></pre>
    <div class="actions"><button type="button" class="btn secondary small" ...${helpFor("teams.copy")} onClick=${copy}>${t("teams.org.copy")}</button>
      <${Help} topic="teams.copy" compact=${true} /></div>
    ${copied && html`<p class="muted small" role="status" aria-live="polite">${copied}</p>`}
  </div>`;
}

export function DiffView({ diff }) {
  if (!diff) return null;
  if (!diff.has_changes) return html`<p>${t("teams.diff.no_changes")}</p>`;
  const fields = fieldChanges(diff.team_fields);
  return html`<div>
    <p class="muted small">${diff.base_version ? t("teams.diff.base_present", { version: diff.base_version }) : t("teams.diff.base_missing")}</p>
    ${fields.length > 0 && html`<p><strong>${t("teams.diff.team_details")}</strong> ${fields.join("; ")}.</p>`}
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
  if (!versions.length) return html`<p>${t("teams.versions.empty")}</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">${t("teams.versions.caption")}</caption>
    <thead><tr><th scope="col">${t("teams.versions.version")}</th><th scope="col">${t("teams.versions.status")}</th><th scope="col">${t("teams.versions.proposed_by")}</th><th scope="col">${t("teams.versions.decision")}</th><th scope="col">${t("teams.versions.changes")}</th></tr></thead>
    <tbody>${versions.map((version) => html`<tr key=${version.id}>
      <th scope="row">${t("teams.version.prefix")}${version.version}${version.active ? " (" + t("teams.versions.active") + ")" : ""}</th>
      <td><${StatusChip} status=${version.status} labels=${VERSION_STATUS} /></td>
      <td>${version.proposed_by || version.created_by || t("teams.versions.no_data")} <span class="muted small">${when(version.proposed_at || version.created_at)}</span></td>
      <td>${version.decided_by ? version.decided_by + " · " + when(version.decided_at) + (version.note ? " · " + version.note : "") : t("teams.versions.undecided")}</td>
      <td><button type="button" class="btn secondary small" aria-pressed=${openId === version.id ? "true" : "false"}
        ...${helpFor("teams.diff", { scope: version.id })} onClick=${() => onOpen(openId === version.id ? null : version.id)}>${openId === version.id ? t("teams.versions.hide_changes") : t("teams.versions.show_changes")}</button>
        <${Help} topic="teams.diff" scope=${version.id} compact=${true} /></td>
    </tr>`)}</tbody>
  </table></div>`;
}

function VersionDiff({ token, versionId }) {
  const [state] = useLoad(BASE + "/versions/" + enc(versionId), token);
  if (state.error) return html`<${Failure} error=${state.error} />`;
  if (!state.data) return html`<p role="status" aria-live="polite">${t("teams.loading.changes")}</p>`;
  // CHT-T05: the card (mode, rules, permissions, model per role) and the full diff, the same as in the chat.
  return html`<div class="usage-comparison"><h5>${t("teams.version.heading", { version: state.data.version })}</h5>
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
    setMessage({ kind: "info", text: t("teams.decision.sending") });
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
    keepLabel=${t("teams.decision.keep_pending")} danger=${danger} busy=${busy} onKeep=${() => setConfirm(null)} onConfirm=${() => decide(action, okText)}>
    <p class="muted">${text}</p>
    <label for="team-note">${t("teams.decision.note")}</label><${Help} topic="teams.note" />
    <textarea id="team-note" rows="2" maxlength="4000" value=${note} ...${helpFor("teams.note")} onInput=${(event) => setNote(event.target.value)}></textarea>
  <//>`;
  return html`<div>
    <p>${t("teams.decision.waiting", { version: pending.version, proposer: pending.proposed_by || t("teams.decision.someone") })}</p>
    ${confirm === "approve" ? box("approve", t("teams.decision.approve_title"), t("teams.decision.yes_approve"), t("teams.decision.approve_description"), t("teams.proposal.approved"), false)
      : confirm === "reject" ? box("reject", t("teams.decision.reject_title"), t("teams.decision.yes_reject"), t("teams.decision.reject_description"), t("teams.proposal.rejected"), true)
      : html`<div class="actions">
        <span class="action-item"><button type="button" class="btn" disabled=${busy} ...${helpFor("teams.approve")} onClick=${() => setConfirm("approve")}>${t("teams.decision.approve_button")}</button>
          <${Help} topic="teams.approve" /></span>
        <span class="action-item"><button type="button" class="btn secondary" disabled=${busy} ...${helpFor("teams.reject")} onClick=${() => setConfirm("reject")}>${t("teams.decision.reject_button")}</button>
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
    <p><a href=${routeHref("teams")}>${t("teams.detail.all_teams")}</a></p>
    <h4>${t("teams.detail.team")} ${bundle}</h4>
    <p>${t("teams.detail.approved_version")} ${entry ? (entry.approved ? versionText(entry.approved) : t("teams.version.none")) : "…"}.</p>
    <h4>${t("teams.detail.pending_proposal")}</h4><${Help} topic="teams.decision" />
    ${list.error && html`<${Failure} error=${list.error} />`}
    ${pending ? html`<${Decision} token=${token} pending=${pending} onDone=${refresh} />` : html`<p>${t("teams.detail.no_pending")}</p>`}
    ${chart.error && html`<${Failure} error=${chart.error} />`}
    ${chart.data ? html`<${OrgChartView} chart=${chart.data} />` : !chart.error && html`<p role="status" aria-live="polite">${t("teams.loading.org_chart")}</p>`}
    <h4>${t("teams.detail.versions")}</h4><${Help} topic="teams.versions" />
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
    <h3 id="teams-title">${t("teams.page.title")}</h3><${Help} topic="teams.section" always=${true} />
    <div class="usage-title-row"><p class="muted">${t("teams.page.description")}</p>
      <div class="usage-refresh-control" role="group" aria-label=${t("teams.page.refresh_aria")}>
        <button type="button" class="btn" disabled=${busy} ...${helpFor("teams.refresh")} onClick=${refresh}>${busy ? t("teams.page.refreshing") : t("teams.page.refresh")}</button>
        <${Help} topic="teams.refresh" compact=${true} />
      </div>
    </div>
    ${bundle ? html`<${TeamDetail} key=${bundle} token=${token} bundle=${bundle} reload=${reload} />` : html`<div>
      <h4>${t("teams.page.list_heading")}</h4><${Help} topic="teams.list" />
      ${teams.error && html`<${Failure} error=${teams.error} />`}
      ${teams.loading && !teams.data ? html`<p role="status" aria-live="polite">${t("teams.loading.teams")}</p>` : teams.data && html`<${TeamList} data=${teams.data} />`}
    </div>`}
  </div>`;
}
