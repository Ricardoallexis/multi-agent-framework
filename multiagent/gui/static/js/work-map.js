// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Read-only project and session map (GUI2-T13/T14). It uses the protected same-origin map API and renders only
// allowlisted names/titles; in particular, child.label is private source text and is never read here.
import { html, useEffect, useMemo, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { navigate } from "./route.js";
import { LevelBadge } from "./ui/level-badge.js";
import { labelEntry, resolveLabels } from "./ui/labels.js";
import { Progress } from "./ui/progress.js";
import { StatusChip } from "./ui/status-chip.js";
import { followChildren } from "./subagents-panel.js";
import { errorText } from "./text.js";

const LIMIT = 300;
const REFRESH_MS = 10000;
const FINISHED = new Set(["done", "completed", "archived", "stopped", "failed", "cancelled", "integrated", "closed", "success"]);
const appClass = (source) => ({ claude: "wm-app-claude", codex: "wm-app-codex", copilot: "wm-app-copilot", framework: "wm-app-framework" }[String(source || "").toLowerCase()] || "wm-app-other");
const titleOf = (item, fallback) => item?.title || item?.name || fallback;
const displayLabel = (label, fallback) => label && typeof label === "object" ? (label.text || fallback) : fallback;
export const labelTone = (label) => ({ wait: "warning", hold: "muted" }[label?.tone] || label?.tone || "neutral");
const isFinished = (node) => FINISHED.has(String(node.status || "").toLowerCase()) || FINISHED.has(String(node.label?.key || "").toLowerCase());
export const isNodeCollapsed = (node, folded, overrides = new Map()) =>
  Boolean((overrides.has(node.id) ? overrides.get(node.id) : (folded && isFinished(node))) && node.children?.length);
const statusMap = (node) => node.label && node.status ? { [node.status]: { text: displayLabel(node.label, node.status), tone: labelTone(node.label) } } : null;
const statusLabels = (node) => node.kind === "stage" && node.status === "active"
  ? { active: { text: "Activa", tone: node.label?.tone ? labelTone(node.label) : "success" } } : statusMap(node);
const kindLabel = (kind) => ({
  project: "Proyecto", stage: "Etapa", task: "Tarea", session: "Sesión", child: "Hijo", group: "Grupo",
}[kind] || "Elemento");
const STATE_LABELS_ES = {
  active: "En curso", archived: "Archivado", blocked: "Con bloqueo", cancelled: "Cancelado", closed: "Cerrado",
  completed: "Completado", doing: "En curso", done: "Terminado", failed: "Fallido", idle: "Sin actividad",
  integrated: "Integrado", interrupted: "Interrumpido", not_run: "No ejecutado", paused: "En pausa",
  pending: "Pendiente", queued: "En cola", ready: "Listo", rejected: "Rechazado", review: "En revisión",
  running: "En ejecución", skipped: "Omitido", stopped: "Detenido", success: "Correcto", waiting_human: "Esperando a una persona",
  waiting_review: "Esperando revisión", will_skip: "Se omitirá",
};
const localizedState = (state) => STATE_LABELS_ES[String(state || "").toLowerCase()] || state;
const statusSet = (node) => {
  const labels = statusLabels(node) || (node.kind === "session" ? "session" : node.kind === "child" ? "subagent" : "run");
  const entry = labelEntry(resolveLabels(labels), node.status);
  return { [node.status]: { ...entry, text: entry.text === String(node.status) ? localizedState(node.status) : entry.text } };
};
export function nodeAccessibleLabel(node) {
  const state = node.status
    ? labelEntry(resolveLabels(statusSet(node)), node.status).text
    : "";
  const translated = state === String(node.status) ? localizedState(node.status) : state;
  return `${kindLabel(node.kind)}: ${node.title}${node.status ? `. Estado: ${translated}` : ""}`;
}
export function relationAccessibleLabel(type, id, state = "") {
  return `${type}: ${id}${state ? `, estado: ${localizedState(state)}` : ""}`;
}
export function restoreMapFocus(root, focused, doc = document) {
  if (!root || !focused || doc.activeElement !== doc.body || doc.hasFocus?.() === false) return false;
  const rows = [...root.querySelectorAll("[data-wm-node]")];
  const row = [focused.id, ...focused.ancestors]
    .map((id) => rows.find((candidate) => candidate.getAttribute("data-wm-node") === id))
    .find(Boolean);
  (row || root).focus();
  return true;
}
const tokenText = (tokens) => {
  if (!tokens || typeof tokens !== "object") return "Tokens: no disponibles";
  const input = Number.isFinite(tokens.input) ? tokens.input : Number.isFinite(tokens.input_tokens) ? tokens.input_tokens : null;
  const output = Number.isFinite(tokens.output) ? tokens.output : Number.isFinite(tokens.output_tokens) ? tokens.output_tokens : null;
  return input === null && output === null ? "Tokens: no disponibles" : `Tokens: ${input === null ? "—" : input} entrada · ${output === null ? "—" : output} salida`;
};
const snapshotKey = (value) => value && typeof value === "object"
  ? value.id ?? value.group_id ?? (value.task_id && value.session_id ? `${value.task_id}\0${value.session_id}` : null)
  : null;

export function reconcileMapSnapshot(previous, next) {
  if (Object.is(previous, next)) return previous;
  if (!previous || !next || typeof previous !== "object" || typeof next !== "object") return next;
  if (Array.isArray(previous) && Array.isArray(next)) {
    const keyed = new Map(previous.map((item, index) => [snapshotKey(item) ?? `index:${index}`, item]));
    let changed = previous.length !== next.length;
    const result = next.map((item, index) => {
      const key = snapshotKey(item) ?? `index:${index}`;
      const stable = reconcileMapSnapshot(keyed.get(key), item);
      if (stable !== previous[index]) changed = true;
      return stable;
    });
    return changed ? result : previous;
  }
  if (Array.isArray(previous) || Array.isArray(next)) return next;
  const oldKeys = Object.keys(previous);
  const nextKeys = Object.keys(next);
  if (oldKeys.length !== nextKeys.length || nextKeys.some((key) => !Object.prototype.hasOwnProperty.call(previous, key))) {
    return Object.fromEntries(nextKeys.map((key) => [key, reconcileMapSnapshot(previous[key], next[key])]));
  }
  let changed = false;
  const result = {};
  for (const key of nextKeys) {
    result[key] = reconcileMapSnapshot(previous[key], next[key]);
    if (result[key] !== previous[key]) changed = true;
  }
  return changed ? result : previous;
}

export function createMapRefreshCoordinator({
  fetchMap, onData, onError, onLoading, interval = REFRESH_MS,
  setIntervalFn = setInterval, clearIntervalFn = clearInterval,
}) {
  let active = true;
  let timer = null;
  let inFlight = false;
  let pending = false;
  let pendingLoading = false;
  let loading = false;
  let authBlocked = false;

  const setBusy = (value) => {
    if (loading === value) return;
    loading = value;
    onLoading(value);
  };
  const refresh = ({ showLoading = false, manual = false } = {}) => {
    if (!active) return;
    if (manual) authBlocked = false;
    if (authBlocked && !manual) return;
    if (showLoading) setBusy(true);
    if (inFlight) {
      pending = true;
      pendingLoading = pendingLoading || showLoading;
      return;
    }
    inFlight = true;
    Promise.resolve().then(fetchMap).then((value) => {
      if (active) onData(value);
    }).catch((failure) => {
      if (failure?.status === 401 || failure?.status === 403) authBlocked = true;
      if (active) onError(failure);
    }).finally(() => {
      inFlight = false;
      if (!active) return;
      if (pending && !authBlocked) {
        const nextLoading = pendingLoading;
        pending = false;
        pendingLoading = false;
        refresh({ showLoading: nextLoading });
        return;
      }
      pending = false;
      pendingLoading = false;
      setBusy(false);
    });
  };
  return {
    refresh,
    start() {
      timer = setIntervalFn(() => refresh(), interval);
      refresh({ showLoading: true });
    },
    stop() {
      active = false;
      pending = false;
      if (timer !== null) clearIntervalFn(timer);
      timer = null;
    },
  };
}

export function createRefreshQueue(refresh, delay = 60, { setTimeoutFn = setTimeout, clearTimeoutFn = clearTimeout } = {}) {
  let timer = null;
  return {
    schedule() {
      if (timer !== null) return;
      timer = setTimeoutFn(() => {
        timer = null;
        refresh();
      }, delay);
    },
    stop() {
      if (timer !== null) clearTimeoutFn(timer);
      timer = null;
    },
  };
}

export function followMapSessions({ token, sessionIds, onChange, follow = followChildren, versions = new Map() }) {
  const stops = [];
  const sessions = [...new Set(sessionIds)].slice(0, LIMIT);
  const current = new Set(sessions);
  for (const session of versions.keys()) if (!current.has(session)) versions.delete(session);
  let stopped = false;
  for (const session of sessions) {
    stops.push(follow({
      token,
      session,
      onState() {},
      onChange(event) {
        if (stopped || !event || typeof event.version !== "string" || versions.get(session) === event.version) return;
        versions.set(session, event.version);
        onChange(session, event);
      },
    }));
  }
  return () => {
    stopped = true;
    stops.forEach((stop) => stop());
  };
}

export function mapCountAnnouncement(visible, total, remaining) {
  return `${visible} elementos visibles; ${total} en total; ${remaining} ocultos por el límite.`;
}

export const progressText = (progress) => {
  if (!progress || !Number.isFinite(progress.done) || !Number.isFinite(progress.total)) return "Progreso no disponible";
  const cancelled = Number.isFinite(progress.cancelled) ? progress.cancelled : 0;
  const counted = Math.max(0, progress.total - cancelled);
  const done = Math.min(progress.done, counted);
  const cancelledText = cancelled ? ` · ${cancelled} canceladas` : "";
  return progress.percent === null || !Number.isFinite(progress.percent)
    ? `${done} de ${counted} tareas contadas${cancelledText} · porcentaje no disponible`
    : `${done} de ${counted} tareas contadas${cancelledText} · ${progress.percent}%`;
};
export function projectRows(data, onlyActive = false, query = "") {
  const projects = data?.projects || [];
  const sessions = data?.sessions || {};
  const rows = [];
  const links = new Map();
  for (const link of data?.links || []) {
    const rows = links.get(link.task_id) || [];
    rows.push(link.session_id);
    links.set(link.task_id, rows);
  }
  const active = (session) => ["active", "idle"].includes(session?.status);
  const seenRoots = new Set();
  const seenSessions = new Set();
  const matches = (text) => !query || String(text || "").toLocaleLowerCase("es").includes(query);

  const childNodes = (sid, depth, ancestry = new Set()) => {
    const session = sessions[sid];
    if (!session || (onlyActive && !active(session))) return [];
    const children = (session.children || []).filter((child) => !onlyActive || !["done", "stopped"].includes(child.status));
    return children.map((child) => {
      const linked = child.link_id && sessions[child.link_id]
        ? sessionNode(child.link_id, depth + 2, ancestry, "aviso") : null;
      return { kind: "child", id: `child:${encodeURIComponent(sid)}:${encodeURIComponent(child.id)}`, title: child.name || "Agente sin nombre", source: child.source,
        status: child.status, model: child.model, level: child.level, tokens: child.tokens, depth: depth + 1,
        linkedSessionId: child.link_id || null, children: linked ? [linked] : [] };
    });
  };
  const sessionNode = (sid, depth = 0, ancestry = new Set(), relation = "") => {
    if (ancestry.has(sid) || seenSessions.has(sid)) return null;
    const session = sessions[sid];
    if (!session || (onlyActive && !active(session))) return null;
    seenSessions.add(sid);
    const nextAncestry = new Set(ancestry); nextAncestry.add(sid);
    const childLinks = new Set((session.children || []).map((child) => child.link_id).filter(Boolean));
    const children = Object.entries(sessions).filter(([id, value]) => value.parent_id === sid && !childLinks.has(id)).map(([id]) => id).sort();
    return { kind: "session", id: "session:" + sid, title: session.name || "Sesión sin título", source: session.source, status: session.status,
      model: Array.isArray(session.model) ? session.model.filter(Boolean).join(", ") : session.model,
      tokens: session.tokens, depth, sessionId: sid, relation, children: [
        ...children.map((id) => sessionNode(id, depth + 1, nextAncestry)).filter(Boolean), ...childNodes(sid, depth, nextAncestry),
      ] };
  };
  const taskNode = (task, stageTitle, includeLinks = true) => {
    const ids = links.get(task.id) || [];
    const assignmentEdges = includeLinks ? ids.filter((id) => sessions[id] && (!onlyActive || active(sessions[id]))).map((id) => {
      seenRoots.add(id);
      return { sessionId: id, title: sessions[id].name || "Sesión sin título" };
    }) : [];
    const linked = assignmentEdges.map(({ sessionId }) => sessionNode(sessionId, 4, new Set(), "encargo")).filter(Boolean);
    const content = titleOf(task, task.id || "Tarea sin título");
    return { kind: "task", id: "task:" + task.id, title: content, status: task.label?.key || task.status || task.state,
      label: task.label, depth: 3, phase: task.phase, dueAt: task.due_at, assignee: task.assignee_info || task.assignee || task.preferred,
      dependsOn: task.depends_on || [], blockers: task.blockers || [], assignmentEdges, children: linked,
      searchable: `${content} ${stageTitle} ${task.id || ""}` };
  };

  for (const project of projects) {
    const stageRows = (project.stages || []).map((stage) => ({ kind: "stage", id: "stage:" + (stage.group_id || stage.code),
      title: titleOf(stage, stage.code || "Etapa"), status: stage.label?.key || stage.lifecycle_state || stage.status, label: stage.label,
      progress: stage.progress, depth: 1,
      children: (stage.tasks || []).map((task) => taskNode(task, titleOf(stage, ""))) }));
    const attention = project.attention || {};
    const attentionTasks = [...new Map([...(attention.reviews || []), ...(attention.mine || []), ...(attention.blocked || [])].map((task) => [task.id, task])).values()];
    const attentionRows = attentionTasks.map((task) => taskNode(task, "Atención", false));
    const verificationRows = (attention.verifications || []).map((stage) => ({ kind: "stage", id: "verify:" + stage.group_id,
      title: titleOf(stage, "Verificación"), status: stage.label?.key || stage.lifecycle_state, label: stage.label, depth: 2, children: [] }));
    const children = [...stageRows, ...((attentionRows.length || verificationRows.length) ? [{ kind: "group", id: "attention:" + project.id,
      title: "Atención", depth: 1, children: [...verificationRows, ...attentionRows] }] : [])];
    rows.push({ kind: "project", id: "project:" + project.id, title: titleOf(project, project.id || "Proyecto"),
      progress: project.progress, depth: 0, children, visibility: project.visibility });
  }
  const orphanRoots = Object.keys(sessions).filter((id) => !seenRoots.has(id) && !seenSessions.has(id)
    && (!sessions[id]?.parent_id || !sessions[sessions[id].parent_id])).sort();
  const unlinked = orphanRoots.map((id) => sessionNode(id)).filter(Boolean);
  for (const id of Object.keys(sessions).sort()) {
    if (!seenRoots.has(id) && !seenSessions.has(id)) {
      const node = sessionNode(id);
      if (node) unlinked.push(node); // orphaned cycles have no parentless root
    }
  }
  if (unlinked.length) rows.push({ kind: "group", id: "untasked", title: "Sesiones sin tarea", depth: 0, children: unlinked });

  const filterTree = (node) => {
    if (onlyActive && isFinished(node)) return null;
    const children = (node.children || []).map(filterTree).filter(Boolean);
    const self = matches(`${node.title} ${node.id} ${node.status || ""} ${displayLabel(node.label, "")} ${node.source || ""} ${node.model || ""}`);
    if (!query || self || children.length) return { ...node, children };
    return null;
  };
  const filtered = rows.map(filterTree).filter(Boolean);
  const budget = { remaining: LIMIT };
  const bounded = filtered.map((root) => boundTree(root, budget)).filter(Boolean);
  const total = countNodes(filtered);
  return { tree: bounded, count: Math.min(total, LIMIT), total, remaining: Math.max(0, total - LIMIT), capped: total > LIMIT };
}

function countNodes(nodes) { return nodes.reduce((n, node) => n + 1 + countNodes(node.children || []), 0); }
function boundTree(node, budget) {
  if (budget.remaining <= 0) return null;
  budget.remaining -= 1;
  const children = [];
  for (const child of node.children || []) {
    if (budget.remaining <= 0) break;
    const bounded = boundTree(child, budget);
    if (bounded) children.push(bounded);
  }
  return { ...node, children };
}

export function Node({ node, onlyActive, folded, foldedIds, onToggleFold }) {
  const collapsed = isNodeCollapsed(node, folded, foldedIds);
  const hasChildren = node.children?.length > 0;
  const isSession = node.kind === "session";
  const isChild = node.kind === "child";
  const isTask = node.kind === "task";
  const relation = (type, id, state = "") => html`<span key=${node.id + type + id} class="wm-relation" role="note"
    tabindex="0" aria-label=${relationAccessibleLabel(type, id, state)}>${type} · ${id}${state ? ` (${localizedState(state)})` : ""}</span>`;
  return html`<li class=${"wm-node wm-depth-" + Math.min(node.depth || 0, 6) + (isChild ? " " + appClass(node.source) : "")}>
    <div class="wm-row" role="group" tabindex="0" aria-label=${nodeAccessibleLabel(node)} data-wm-node=${node.id}>
      ${hasChildren ? html`<button type="button" class="wm-fold" aria-label=${(collapsed ? "Expandir " : "Contraer ") + node.title}
        aria-expanded=${collapsed ? "false" : "true"} ...${helpFor("work_map.fold")} onClick=${() => onToggleFold(node.id, collapsed)}> ${collapsed ? "+" : "−"}</button>` : html`<span class="wm-fold-space" aria-hidden="true"></span>`}
      <span class="wm-kind">${node.kind === "project" ? "Proyecto" : node.kind === "stage" ? "Etapa" : node.kind === "task" ? "Tarea" : isSession ? "Sesión" : isChild ? "Hijo" : "Grupo"}</span>
      <strong class="wm-title">${node.title}</strong>
      ${node.visibility === "private" && html`<span class="wm-private-badge" aria-label="Proyecto privado">Privado</span>`}
      ${node.source && html`<span class=${"wm-app-tag " + appClass(node.source)}>${node.source}</span>`}
      ${(isSession || isChild) && html`<span class="wm-model">${node.model || "Modelo no disponible"}</span>`}
      ${node.level && html`<${LevelBadge} level=${node.level} />`}
      ${node.status && html`<${StatusChip} status=${node.status} labels=${statusSet(node)} />`}
      ${node.progress && html`<${Progress} className="wm-progress" label=${node.kind === "project" ? "Progreso del proyecto" : "Progreso de la etapa"} value=${node.progress.percent} valueText=${progressText(node.progress)} tone="info" />`}
      ${isTask && node.phase && html`<span class="wm-detail">${node.phase}</span>`}
      ${isTask && node.dueAt && html`<span class="wm-detail">Fecha: ${node.dueAt}</span>`}
      ${isTask && node.dependsOn?.map((id) => relation("Depende de", id))}
      ${isTask && node.blockers?.map((blocker) => relation("Bloqueo", blocker.task_id, blocker.state || "estado no disponible"))}
      ${isTask && node.assignmentEdges?.map((edge) => html`<button key=${node.id + edge.sessionId} type="button" class="wm-relation-button" ...${helpFor("work_map.assignment_edge")} onClick=${() => navigate("sessions", edge.sessionId)}>Encargo → ${edge.title}</button>`)}
      ${isChild && node.linkedSessionId && html`<button type="button" class="wm-relation-button" ...${helpFor("work_map.notice_edge")} onClick=${() => navigate("sessions", node.linkedSessionId)}>Aviso → abrir sesión hija</button>`}
      ${isSession && html`<span class="wm-tokens">${tokenText(node.tokens)}</span>`}
      ${isChild && html`<span class="wm-tokens">${tokenText(node.tokens)}</span>`}
      ${isSession && html`<button type="button" class="btn secondary wm-open" ...${helpFor("work_map.open_session")} onClick=${() => navigate("sessions", node.sessionId)}>Abrir sesión</button>`}
    </div>
    ${node.visibility === "private" && html`<p class="wm-private" role="note">Proyecto privado. Revisa el destino antes de compartir datos de sus tareas con una aplicación.</p>`}
    ${!collapsed && hasChildren && html`<ul class="wm-children">${node.children.map((child) => html`<${Node} key=${child.id} node=${child} onlyActive=${onlyActive} folded=${folded} foldedIds=${foldedIds} onToggleFold=${onToggleFold} />`)}</ul>`}
  </li>`;
}

export function ListItem({ node }) {
  const relation = (id, type, state = "") => html`<span key=${node.id + type + id} class="wm-relation" role="note"
    tabindex="0" aria-label=${relationAccessibleLabel(type, id, state)}>${type} · ${id}${state ? ` (${localizedState(state)})` : ""}</span>`;
  return html`<div role="listitem" tabindex="0" aria-label=${nodeAccessibleLabel(node)} data-wm-node=${node.id}
    key=${node.id} class=${"wm-list-row wm-depth-" + Math.min(node.depth || 0, 6)}>
    <span class="wm-kind">${kindLabel(node.kind)}</span><strong>${node.title}</strong>
    ${node.visibility === "private" && html`<span class="wm-private-badge">Privado</span>`}
    ${node.source && html`<span class=${"wm-app-tag " + appClass(node.source)}>${node.source}</span>`}
    ${(node.kind === "session" || node.kind === "child") && html`<span class="wm-model">${node.model || "Modelo no disponible"}</span>`}
    ${node.level && html`<${LevelBadge} level=${node.level} />`}
    ${node.status && html`<${StatusChip} status=${node.status} labels=${statusSet(node)} />`}
    ${node.progress && html`<${Progress} className="wm-progress" label=${node.kind === "project" ? "Progreso del proyecto" : "Progreso de la etapa"} value=${node.progress.percent} valueText=${progressText(node.progress)} tone="info" />`}
    ${node.phase && html`<span class="wm-detail">${node.phase}</span>`}${node.dueAt && html`<span class="wm-detail">Fecha: ${node.dueAt}</span>`}
    ${node.dependsOn?.map((id) => relation(id, "Depende de"))}
    ${node.blockers?.map((blocker) => relation(blocker.task_id, "Bloqueo", blocker.state || "estado no disponible"))}
    ${node.kind === "task" && node.assignmentEdges?.map((edge) => html`<button key=${node.id + edge.sessionId} type="button" class="wm-relation-button" ...${helpFor("work_map.assignment_edge")} onClick=${() => navigate("sessions", edge.sessionId)}>Encargo → ${edge.title}</button>`)}
    ${node.kind === "child" && node.linkedSessionId && html`<button type="button" class="wm-relation-button" ...${helpFor("work_map.notice_edge")} onClick=${() => navigate("sessions", node.linkedSessionId)}>Aviso → abrir sesión hija</button>`}
    ${(node.kind === "session" || node.kind === "child") && html`<span class="wm-tokens">${tokenText(node.tokens)}</span>`}
    ${node.kind === "session" && html`<button type="button" class="btn secondary wm-open" ...${helpFor("work_map.open_session")} onClick=${() => navigate("sessions", node.sessionId)}>Abrir sesión</button>`}
    ${node.visibility === "private" && html`<span class="wm-private">Proyecto privado. Revisa el destino antes de compartir datos de sus tareas con una aplicación.</span>`}
  </div>`;
}

export function WorkMap({ token }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [onlyActive, setOnlyActive] = useState(true);
  const [folded, setFolded] = useState(true);
  const [query, setQuery] = useState("");
  const [layout, setLayout] = useState(() => window.matchMedia?.("(max-width: 640px)").matches ? "list" : "tree");
  const [foldedIds, setFoldedIds] = useState(() => new Map());
  const rootRef = useRef(null);
  const focusedNodeRef = useRef(null);
  const parentIdsRef = useRef(new Map());
  const refreshRef = useRef(null);
  const projectionRef = useRef(null);
  const childVersions = useRef(new Map());
  const dataToken = useRef(token);
  const currentData = dataToken.current === token ? data : null;
  const load = () => refreshRef.current?.({ showLoading: true, manual: true });
  useEffect(() => {
    dataToken.current = token;
    setData(null);
    setError(null);
    setLoading(true);
    projectionRef.current = null;
    childVersions.current.clear();
    focusedNodeRef.current = null;
    const coordinator = createMapRefreshCoordinator({
      fetchMap: () => apiGet("/gui/api/map?only=all", token),
      onData: (value) => {
        setData((previous) => previous?.version && previous.version === value.version ? previous : reconcileMapSnapshot(previous, value));
        setError(null);
      },
      onError: setError,
      onLoading: setLoading,
    });
    refreshRef.current = coordinator.refresh;
    coordinator.start();
    return () => {
      coordinator.stop();
      if (refreshRef.current === coordinator.refresh) refreshRef.current = null;
    };
  }, [token]);
  const projected = useMemo(() => {
    const fresh = projectRows(currentData, onlyActive, query.trim().toLocaleLowerCase("es"));
    const stable = reconcileMapSnapshot(projectionRef.current, fresh);
    projectionRef.current = stable;
    return stable;
  }, [currentData, onlyActive, query]);
  const sessionIds = useMemo(() => {
    const found = [];
    const visit = (nodes) => nodes.forEach((node) => {
      if (node.kind === "session") found.push(node.sessionId);
      visit(node.children || []);
    });
    visit(projected.tree);
    return [...new Set(found)].slice(0, LIMIT);
  }, [projected]);
  const sessionSignature = sessionIds.join("\0");
  useEffect(() => {
    const queue = createRefreshQueue(() => refreshRef.current?.());
    const stop = followMapSessions({
      token, sessionIds, versions: childVersions.current, onChange: () => queue.schedule(),
    });
    return () => {
      stop();
      queue.stop();
    };
  }, [token, sessionSignature]);
  const toggleFold = (id, collapsed) => setFoldedIds((old) => { const next = new Map(old); next.set(id, !collapsed); return next; });
  const listed = useMemo(() => {
    const applyFold = (node) => {
      if ((foldedIds.has(node.id) ? foldedIds.get(node.id) : (folded && isFinished(node)))) {
        return node.children?.length ? { ...node, children: [] } : node;
      }
      const children = (node.children || []).map(applyFold);
      return children.every((child, index) => child === node.children[index]) ? node : { ...node, children };
    };
    return projected.tree.map(applyFold);
  }, [projected.tree, foldedIds, folded]);
  const parentIds = useMemo(() => {
    const parents = new Map();
    const visit = (nodes, parent = null) => nodes.forEach((node) => {
      parents.set(node.id, parent);
      visit(node.children || [], node.id);
    });
    visit(projected.tree);
    return parents;
  }, [projected.tree]);
  parentIdsRef.current = parentIds;
  const rememberFocus = (event) => {
    const row = event.target.closest?.("[data-wm-node]");
    if (!row) { focusedNodeRef.current = null; return; }
    const ancestors = [];
    let parent = parentIdsRef.current.get(row.getAttribute("data-wm-node"));
    while (parent) {
      ancestors.push(parent);
      parent = parentIdsRef.current.get(parent);
    }
    focusedNodeRef.current = { id: row.getAttribute("data-wm-node"), ancestors };
  };
  useEffect(() => {
    const clearOnPointer = () => { focusedNodeRef.current = null; };
    document.addEventListener("pointerdown", clearOnPointer, true);
    return () => document.removeEventListener("pointerdown", clearOnPointer, true);
  }, []);
  useEffect(() => {
    restoreMapFocus(rootRef.current, focusedNodeRef.current);
  }, [listed, layout]);
  const leaveFocus = (event) => {
    if (event.relatedTarget && !rootRef.current?.contains(event.relatedTarget)) focusedNodeRef.current = null;
  };
  const flattened = useMemo(() => {
    const result = [];
    const flatten = (nodes) => nodes.forEach((node) => { result.push(node); flatten(node.children || []); });
    flatten(listed);
    return result;
  }, [listed]);
  const visibleCount = layout === "tree" ? countNodes(listed) : flattened.length;
  const countStatus = mapCountAnnouncement(visibleCount, projected.total, projected.remaining);
  return html`<section ref=${rootRef} class="work-map" tabindex="0" aria-labelledby="work-map-title" aria-busy=${loading ? "true" : "false"}
    onFocus=${rememberFocus} onBlur=${leaveFocus}>
    <header class="wm-head"><div><h3 id="work-map-title">Mapa de trabajo</h3><${Help} topic="work_map.refresh" compact=${true} /><${Help} topic="work_map.open_session" compact=${true} /><p class="muted">Proyectos, tareas y sesiones disponibles para esta interfaz.</p></div>
      <button type="button" class="btn secondary" ...${helpFor("work_map.refresh")} onClick=${load}>Actualizar</button></header>
    <div class="wm-toolbar" role="search" aria-label="Filtros del mapa">
      <label for="wm-query">Buscar en el mapa</label><${Help} topic="work_map.search" compact=${true} /><input id="wm-query" type="search" maxlength="120" ...${helpFor("work_map.search")} value=${query} onInput=${(event) => setQuery(event.currentTarget.value)} />
      <label class="wm-check"><input type="checkbox" checked=${onlyActive} ...${helpFor("work_map.active")} onChange=${(event) => setOnlyActive(event.currentTarget.checked)} /> Solo activos</label><${Help} topic="work_map.active" compact=${true} />
      <label class="wm-check"><input type="checkbox" checked=${folded} ...${helpFor("work_map.fold_finished")} onChange=${(event) => setFolded(event.currentTarget.checked)} /> Plegar terminados</label><${Help} topic="work_map.fold_finished" compact=${true} />
      <div class="wm-layout" role="group" aria-label="Presentación del mapa"><button type="button" aria-pressed=${layout === "tree"} ...${helpFor("work_map.tree")} onClick=${() => setLayout("tree")}>Árbol</button><${Help} topic="work_map.tree" compact=${true} /><button type="button" aria-pressed=${layout === "list"} ...${helpFor("work_map.list")} onClick=${() => setLayout("list")}>Lista</button><${Help} topic="work_map.list" compact=${true} /></div><${Help} topic="work_map.fold" compact=${true} /><${Help} topic="work_map.assignment_edge" compact=${true} /><${Help} topic="work_map.notice_edge" compact=${true} />
    </div>
    <p class="wm-count" role="status" aria-live="polite">${countStatus}</p>
    ${loading && !currentData && html`<p class="muted">Cargando el mapa…</p>`}
    ${error && html`<p class="message error" role="alert">No se pudo cargar el mapa: ${errorText(error)}</p><button type="button" class="btn secondary" ...${helpFor("work_map.refresh")} onClick=${load}>Reintentar</button>`}
    ${currentData?.warnings?.length > 0 && html`<p class="wm-private" role="note">Algunas fuentes no estuvieron disponibles. Los avisos se omitieron para no mostrar identificadores privados.</p>`}
    ${projected.capped && html`<p class="wm-private" role="note">Se muestran ${LIMIT} elementos; ${projected.remaining} más están ocultos. Ajusta la búsqueda para acotar el mapa.</p>`}
    <div class="wm-results" role="region" tabindex="0" aria-label=${layout === "tree" ? "Resultados del mapa, árbol" : "Resultados del mapa, lista"}>
      ${layout === "tree" ? html`<ul class="wm-tree" aria-label="Proyecto y sesiones, árbol">${projected.tree.map((node) => html`<${Node} key=${node.id} node=${node} onlyActive=${onlyActive} folded=${folded} foldedIds=${foldedIds} onToggleFold=${toggleFold} />`)}</ul>` : html`<div class="wm-list" role="list" aria-label="Elementos del mapa">${flattened.map((node) => html`<${ListItem} key=${node.id} node=${node} />`)}</div>`}
    </div>
    ${!loading && projected.count === 0 && html`<p class="muted">No hay elementos que coincidan con estos filtros.</p>`}
  </section>`;
}
