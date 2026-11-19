// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Vista de planes propuestos (PLN-T04): lista las propuestas guardadas con su estado y revisión y, al abrir una
// ("#plans/<id>"), muestra sus tareas, dependencias, preferencia e historial, y permite editar tareas, reasignar una
// tarea, aprobar o rechazar cada una tras confirmar y enviando la revisión visible. Ante un conflicto (409), avisa y
// recarga la propuesta. Todo lo que llega del servidor es texto no confiable y se pinta solo como texto (htm/Preact).
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { Confirm } from "./human-actions.js";
import { routeHref } from "./route.js";
import { StatusChip } from "./ui/index.js";

const BASE = "/gui/api/plan-proposals";
const enc = encodeURIComponent;

export const PLAN_STATUS = {
  proposed: { text: "Propuesta", tone: "attention", explanation: "Espera revisión y decisión de una persona." },
  approved: { text: "Aprobada", tone: "success", explanation: "Aprobada; sus tareas quedaron creadas." },
  rejected: { text: "Rechazada", tone: "danger", explanation: "Rechazada; no admite más cambios." },
};

export const HISTORY_KIND = {
  save: "Guardada",
  edit: "Editada",
  reassign: "Reasignada",
  approve: "Aprobada",
  reject: "Rechazada",
};

// What «Avanzar plan» answers, as one sentence: the tasks that moved, or that nothing was left to advance.
export function advanceSummary(result) {
  const moved = result && Array.isArray(result.advanced) ? result.advanced : [];
  if (!moved.length) return "No había nada que avanzar.";
  return "Plan avanzado: " + moved.map((item) => item.task_id).join(", ") + ".";
}

const when = (value) => {
  if (!value) return "Sin fecha";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "Fecha no disponible"
    : new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(date);
};

// The error of the service as it came: its message and its code.
export const failureText = (error) =>
  (error && error.message ? error.message : "Error desconocido") + (error && error.code ? " (" + error.code + ")" : "");

export const formatDependencies = (deps) =>
  Array.isArray(deps) && deps.length ? deps.join(", ") : "Ninguna";

export const parseDependencies = (text) => {
  const seen = new Set();
  const out = [];
  for (const part of String(text || "").split(",")) {
    const id = part.trim();
    if (id && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
};

export const toEditDrafts = (tasks) =>
  (Array.isArray(tasks) ? tasks : []).map((task) => ({
    id: task.id,
    title: task.title || "",
    objective: task.objective || "",
    depends_on_text: Array.isArray(task.depends_on) ? task.depends_on.join(", ") : "",
    workflow: task.workflow || "",
    bundle: task.bundle || "",
    preferred: task.preferred || "",
    inputs: task.inputs && typeof task.inputs === "object" ? { ...task.inputs } : undefined,
  }));

export function buildEditedTasks(drafts) {
  return (Array.isArray(drafts) ? drafts : []).map((task) => {
    const out = {
      id: task.id,
      title: String(task.title || "").trim(),
      depends_on: parseDependencies(task.depends_on_text),
      workflow: String(task.workflow || "").trim(),
    };
    const objective = String(task.objective || "");
    if (objective.trim()) out.objective = objective;
    const bundle = String(task.bundle || "").trim();
    if (bundle) out.bundle = bundle;
    const preferred = String(task.preferred || "").trim();
    if (preferred) out.preferred = preferred;
    if (task.inputs && typeof task.inputs === "object") out.inputs = task.inputs;
    return out;
  });
}

export function buildReassignPayload(task, draft, expectedRevision, note) {
  const payload = {
    task_id: task.id,
    expected_revision: expectedRevision,
    note: String(note || "").trim(),
  };
  const nextPreferred = String(draft.preferred || "").trim();
  const prevPreferred = String(task.preferred || "");
  if (nextPreferred !== prevPreferred) payload.preferred = nextPreferred;
  const nextWorkflow = String(draft.workflow || "").trim();
  if (nextWorkflow) payload.workflow = nextWorkflow;
  const nextBundle = String(draft.bundle || "").trim();
  if (nextBundle) payload.bundle = nextBundle;
  return payload;
}

export const changedTaskIds = (changes) =>
  changes && typeof changes === "object" ? Object.keys(changes) : [];

function Failure({ error }) {
  return html`<p class="message error" role="alert">${error ? failureText(error) : "No se pudo leer el plan."}</p>`;
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
  return [state, load, setState];
}

export function PlanList({ data }) {
  const proposals = Array.isArray(data && data.proposals) ? data.proposals : [];
  if (!proposals.length) return html`<p>No hay propuestas de plan con este filtro.</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">Propuestas de plan</caption>
    <thead><tr><th scope="col">Propuesta</th><th scope="col">Grupo</th><th scope="col">Estado</th><th scope="col">Revisión</th><th scope="col">Tareas</th><th scope="col">Creada</th></tr></thead>
    <tbody>${proposals.map((entry) => {
      const tasks = entry.proposal && Array.isArray(entry.proposal.tasks) ? entry.proposal.tasks : [];
      return html`<tr key=${entry.id}>
        <th scope="row"><a href=${routeHref("plans", entry.id)}>${entry.goal || entry.id}</a>
          <div class="muted small">${entry.id} · ${entry.planner}</div></th>
        <td>${entry.group_id}</td>
        <td><${StatusChip} status=${entry.status} labels=${PLAN_STATUS} /></td>
        <td>${"r" + entry.revision}</td>
        <td>${tasks.length}</td>
        <td>${entry.created_by || "Sin dato"} <span class="muted small">${when(entry.created_at)}</span></td>
      </tr>`;
    })}</tbody>
  </table></div>`;
}

export function TasksTable({ tasks }) {
  const list = Array.isArray(tasks) ? tasks : [];
  if (!list.length) return html`<p>La propuesta no contiene tareas.</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">Tareas de la propuesta</caption>
    <thead><tr><th scope="col">Tarea</th><th scope="col">Dependencias</th><th scope="col">Workflow</th><th scope="col">Preferente</th></tr></thead>
    <tbody>${list.map((task) => html`<tr key=${task.id}>
      <th scope="row">${task.title}
        <div class="muted small">${task.id}</div>
        ${task.objective && html`<div class="small">${task.objective}</div>`}</th>
      <td>${Array.isArray(task.depends_on) && task.depends_on.length
        ? html`<ul>${task.depends_on.map((dep) => html`<li key=${dep}>${dep}</li>`)}</ul>`
        : "Ninguna"}</td>
      <td>${task.workflow}${task.bundle ? " (" + task.bundle + ")" : ""}</td>
      <td>${task.preferred || "Sin preferencia"}</td>
    </tr>`)}</tbody>
  </table></div>`;
}

export function HistoryList({ history }) {
  const entries = Array.isArray(history) ? history : [];
  if (!entries.length) return html`<p>Sin movimientos registrados.</p>`;
  return html`<ul class="history">
    ${entries.map((entry, index) => {
      const touched = changedTaskIds(entry.changes);
      return html`<li key=${entry.id || index}>
        <span class="when">${when(entry.at)} · r${entry.revision}</span>
        <span class="who">${HISTORY_KIND[entry.kind] || entry.kind} · ${entry.actor} (${entry.channel})</span>
        ${touched.length > 0 && html`<span class="what">Tareas modificadas: ${touched.join(", ")}</span>`}
        ${entry.note && html`<blockquote>${entry.note}</blockquote>`}
      </li>`;
    })}
  </ul>`;
}

function AdvanceAction({ token, proposal }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  useEffect(() => { setConfirm(false); setMessage(null); }, [proposal.id]);
  const advance = async () => {
    if (busy) return;
    setBusy(true);
    setMessage({ kind: "info", text: "Enviando…" });
    try {
      const result = await apiPost(BASE + "/" + enc(proposal.id) + "/advance", token, {});
      setMessage({ kind: "ok", text: advanceSummary(result) });
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setMessage({ kind: "error", text: failureText(failure) });
    } finally {
      setConfirm(false);
      setBusy(false);
    }
  };
  return html`<div class="panel">
    <h4>Avanzar el plan</h4><${Help} topic="plans.advance" />
    <div class="actions">
      <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.advance")}
        onClick=${() => setConfirm(true)}>Avanzar plan…</button>
    </div>
    ${confirm && html`<${Confirm} title="¿Avanzar el plan una vez?" confirmLabel="Sí, avanzar"
      keepLabel="Cancelar" danger=${false} busy=${busy} onKeep=${() => setConfirm(false)} onConfirm=${advance}>
      <p class="muted">Se pasan a revisión las tareas cuyo run terminó y arrancan las que ya pueden empezar, a nombre del operador.</p>
    <//>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </div>`;
}

function PlanActions({ token, proposal, onUpdated, onReload }) {
  const tasks = proposal.proposal && Array.isArray(proposal.proposal.tasks) ? proposal.proposal.tasks : [];
  const [editing, setEditing] = useState(false);
  const [editDrafts, setEditDrafts] = useState(() => toEditDrafts(tasks));
  const [taskId, setTaskId] = useState(() => (tasks[0] ? tasks[0].id : ""));
  const [reassignDraft, setReassignDraft] = useState(() => {
    const first = tasks[0] || {};
    return { preferred: first.preferred || "", workflow: first.workflow || "", bundle: first.bundle || "" };
  });
  const [confirm, setConfirm] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    setConfirm(null);
    setNote("");
    setEditing(false);
    setMessage(null);
  }, [proposal.id]);

  useEffect(() => {
    setEditDrafts(toEditDrafts(tasks));
    const current = tasks.find((item) => item.id === taskId) || tasks[0];
    if (current) {
      setTaskId(current.id);
      setReassignDraft({
        preferred: current.preferred || "",
        workflow: current.workflow || "",
        bundle: current.bundle || "",
      });
    }
  }, [proposal.id, proposal.revision, proposal.status]);

  const selectReassignTask = (nextId) => {
    setTaskId(nextId);
    const chosen = tasks.find((item) => item.id === nextId);
    if (chosen) {
      setReassignDraft({
        preferred: chosen.preferred || "",
        workflow: chosen.workflow || "",
        bundle: chosen.bundle || "",
      });
    }
  };

  const updateDraft = (index, field, value) => {
    setEditDrafts((previous) => previous.map((item, idx) => (idx === index ? { ...item, [field]: value } : item)));
  };

  const submitAction = async (action, payload, okText) => {
    if (busy) return;
    setBusy(true);
    setMessage({ kind: "info", text: "Enviando…" });
    try {
      const updated = await apiPost(BASE + "/" + enc(proposal.id) + "/" + action, token, payload);
      setConfirm(null);
      setNote("");
      setEditing(false);
      onUpdated(updated);
      setMessage({ kind: "ok", text: okText });
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError({ message: String(error) });
      setConfirm(null);
      if (failure.status === 409 || failure.code === "invalid_state") {
        setMessage({ kind: "error", text: "Conflicto: " + failureText(failure) + ". Se recargó la propuesta." });
        await onReload();
      } else {
        setMessage({ kind: "error", text: failureText(failure) });
        if (failure.status === 404) await onReload();
      }
    } finally {
      setBusy(false);
    }
  };

  if (proposal.status !== "proposed") {
    return html`<div class="panel">
      <p class="muted">Esta propuesta ya fue decidida (${(PLAN_STATUS[proposal.status] || { text: proposal.status }).text}) y no admite más cambios.</p>
      ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
      ${proposal.status === "approved" && html`<${AdvanceAction} token=${token} proposal=${proposal} />`}
    </div>`;
  }

  const chosenTask = tasks.find((item) => item.id === taskId) || tasks[0];
  const confirmBox = () => {
    if (confirm === "edit") {
      return html`<${Confirm} title="¿Guardar los cambios de las tareas?" confirmLabel="Sí, guardar cambios"
        keepLabel="Seguir editando" danger=${false} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("edit", { tasks: buildEditedTasks(editDrafts), expected_revision: proposal.revision, note: note.trim() },
          "Cambios guardados en una nueva revisión.")}>
        <p class="muted">Se validarán las tareas y dependencias sobre la revisión r${proposal.revision}.</p>
        <label for="plan-note">Nota (opcional)</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    if (confirm === "reassign" && chosenTask) {
      return html`<${Confirm} title=${"¿Reasignar la tarea «" + chosenTask.id + "»?"} confirmLabel="Sí, reasignar"
        keepLabel="Cancelar" danger=${false} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("reassign", buildReassignPayload(chosenTask, reassignDraft, proposal.revision, note),
          "Tarea reasignada en una nueva revisión.")}>
        <p class="muted">Se actualizará la asignación de ${chosenTask.id} sobre la revisión r${proposal.revision}.</p>
        <label for="plan-note">Nota (opcional)</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    if (confirm === "approve") {
      return html`<${Confirm} title="¿Aprobar esta propuesta de plan?" confirmLabel="Sí, aprobar"
        keepLabel="Mantenerla pendiente" danger=${false} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("approve", { expected_revision: proposal.revision, note: note.trim() },
          "La propuesta quedó aprobada y se crearon sus tareas.")}>
        <p class="muted">Se crearán las tareas de la revisión r${proposal.revision} a nombre del operador y arrancarán las que no tengan dependencias pendientes.</p>
        <label for="plan-note">Nota (opcional)</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    if (confirm === "reject") {
      return html`<${Confirm} title="¿Rechazar esta propuesta de plan?" confirmLabel="Sí, rechazar"
        keepLabel="Mantenerla pendiente" danger=${true} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("reject", { note: note.trim() }, "La propuesta quedó rechazada.")}>
        <p class="muted">Quedará cerrada como rechazada sin crear tareas. No se puede deshacer.</p>
        <label for="plan-note">Nota (opcional)</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    return null;
  };

  return html`<div class="stack">
    <div class="panel">
      <h4>Editar tareas</h4><${Help} topic="plans.edit" />
      <div class="actions">
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("plans.edit")}
          onClick=${() => { setEditDrafts(toEditDrafts(tasks)); setEditing((open) => !open); setConfirm(null); }}>
          ${editing ? "Cerrar editor" : "Editar tareas"}
        </button>
      </div>
      ${editing && html`<div class="stack">
        ${editDrafts.map((task, index) => html`<fieldset class="inputs" key=${task.id}>
          <legend>Tarea: ${task.id}</legend>
          <label for=${"edit-title-" + task.id}>Título</label>
          <input id=${"edit-title-" + task.id} type="text" maxlength="200" value=${task.title} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "title", e.target.value)} />
          <label for=${"edit-obj-" + task.id}>Objetivo</label>
          <textarea id=${"edit-obj-" + task.id} rows="2" maxlength="4000" value=${task.objective} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "objective", e.target.value)}></textarea>
          <label for=${"edit-deps-" + task.id}>Dependencias (identificadores separados por coma)</label>
          <input id=${"edit-deps-" + task.id} type="text" value=${task.depends_on_text} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "depends_on_text", e.target.value)} />
          <div class="two">
            <div>
              <label for=${"edit-wf-" + task.id}>Workflow</label>
              <input id=${"edit-wf-" + task.id} type="text" value=${task.workflow} ...${helpFor("plans.edit")}
                onInput=${(e) => updateDraft(index, "workflow", e.target.value)} />
            </div>
            <div>
              <label for=${"edit-bundle-" + task.id}>Bundle (opcional)</label>
              <input id=${"edit-bundle-" + task.id} type="text" value=${task.bundle} ...${helpFor("plans.edit")}
                onInput=${(e) => updateDraft(index, "bundle", e.target.value)} />
            </div>
          </div>
          <label for=${"edit-pref-" + task.id}>Preferente (opcional)</label>
          <input id=${"edit-pref-" + task.id} type="text" maxlength="100" value=${task.preferred} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "preferred", e.target.value)} />
        </fieldset>`)}
        <div class="actions">
          <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.edit")}
            onClick=${() => setConfirm("edit")}>Guardar edición…</button>
        </div>
        ${confirm === "edit" && confirmBox()}
      </div>`}
    </div>

    <div class="panel">
      <h4>Reasignar una tarea</h4><${Help} topic="plans.reassign" />
      <div class="stack">
        <label for="reassign-task">Tarea</label>
        <select id="reassign-task" value=${taskId} ...${helpFor("plans.reassign")} onChange=${(e) => selectReassignTask(e.target.value)}>
          ${tasks.map((task) => html`<option key=${task.id} value=${task.id}>${task.id} — ${task.title}</option>`)}
        </select>
        <div class="two">
          <div>
            <label for="reassign-preferred">Preferente</label>
            <input id="reassign-preferred" type="text" maxlength="100" value=${reassignDraft.preferred} ...${helpFor("plans.reassign")}
              onInput=${(e) => setReassignDraft((prev) => ({ ...prev, preferred: e.target.value }))} />
          </div>
          <div>
            <label for="reassign-workflow">Workflow</label>
            <input id="reassign-workflow" type="text" maxlength="120" value=${reassignDraft.workflow} ...${helpFor("plans.reassign")}
              onInput=${(e) => setReassignDraft((prev) => ({ ...prev, workflow: e.target.value }))} />
          </div>
        </div>
        <label for="reassign-bundle">Bundle (opcional)</label>
        <input id="reassign-bundle" type="text" maxlength="120" value=${reassignDraft.bundle} ...${helpFor("plans.reassign")}
          onInput=${(e) => setReassignDraft((prev) => ({ ...prev, bundle: e.target.value }))} />
        <div class="actions">
          <button type="button" class="btn secondary" disabled=${busy || !chosenTask} ...${helpFor("plans.reassign")}
            onClick=${() => setConfirm("reassign")}>Reasignar tarea…</button>
        </div>
        ${confirm === "reassign" && confirmBox()}
      </div>
    </div>

    <div class="panel">
      <h4>Decisión</h4>
      <div class="actions">
        <span class="action-item">
          <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.approve")}
            onClick=${() => setConfirm("approve")}>Aprobar plan…</button>
          <${Help} topic="plans.approve" />
        </span>
        <span class="action-item">
          <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("plans.reject")}
            onClick=${() => setConfirm("reject")}>Rechazar plan…</button>
          <${Help} topic="plans.reject" />
        </span>
      </div>
      ${(confirm === "approve" || confirm === "reject") && confirmBox()}
    </div>
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </div>`;
}

function PlanDetail({ token, proposalId, reloadRef }) {
  const [state, load, setState] = useLoad(BASE + "/" + enc(proposalId), token);
  useEffect(() => { reloadRef.current = load; return () => { reloadRef.current = null; }; });
  const proposal = state.data;
  const tasks = proposal && proposal.proposal && Array.isArray(proposal.proposal.tasks) ? proposal.proposal.tasks : [];
  return html`<div>
    <p><a href=${routeHref("plans")}>← Todos los planes</a></p>
    ${state.error && html`<${Failure} error=${state.error} />`}
    ${state.loading && !proposal ? html`<p role="status" aria-live="polite">Cargando la propuesta…</p>` : proposal && html`<div class="stack">
      <div class="panel">
        <h4>${proposal.goal}</h4>
        <dl class="facts">
          <dt>Identificador</dt><dd>${proposal.id}</dd>
          <dt>Grupo</dt><dd>${proposal.group_id}</dd>
          <dt>Estado</dt><dd><${StatusChip} status=${proposal.status} labels=${PLAN_STATUS} /></dd>
          <dt>Revisión</dt><dd>r${proposal.revision}</dd>
          <dt>Planificador</dt><dd>${proposal.planner}</dd>
          <dt>Creada por</dt><dd>${proposal.created_by} (${proposal.created_channel}) · ${when(proposal.created_at)}</dd>
          ${proposal.decided_by && html`<dt>Decisión</dt><dd>${proposal.decided_by} (${proposal.decided_channel}) · ${when(proposal.decided_at)}</dd>`}
          ${proposal.proposal && proposal.proposal.rationale && html`<dt>Justificación</dt><dd>${proposal.proposal.rationale}</dd>`}
        </dl>
      </div>
      <h4>Tareas y dependencias</h4><${Help} topic="plans.detail" />
      <${TasksTable} tasks=${tasks} />
      <${PlanActions} token=${token} proposal=${proposal}
        onUpdated=${(updated) => setState({ loading: false, data: updated, error: null })}
        onReload=${load} />
      <h4>Historial de cambios</h4><${Help} topic="plans.history" />
      <${HistoryList} history=${proposal.history} />
    </div>`}
  </div>`;
}

export function Plans({ token, route }) {
  const proposalId = route && route.id ? route.id : null;
  const [statusFilter, setStatusFilter] = useState("");
  const listPath = statusFilter ? BASE + "?status=" + enc(statusFilter) : BASE;
  const [list, loadList] = useLoad(listPath, token);
  const reloadRef = useRef(null);
  const refresh = () => {
    if (proposalId) {
      if (reloadRef.current) reloadRef.current();
    } else {
      loadList();
    }
  };
  const busy = !proposalId && list.loading;
  return html`<div class="usage-view">
    <h3 id="plans-title">Planes propuestos</h3><${Help} topic="plans.section" />
    <div class="usage-title-row">
      <p class="muted">Propuestas de plan guardadas antes de crear tareas. Una persona revisa, ajusta y decide.</p>
      <div class="usage-refresh-control" role="group" aria-label="Actualizar los planes">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.refresh")} onClick=${refresh}>${busy ? "Actualizando…" : "Actualizar"}</button>
        <${Help} topic="plans.refresh" compact=${true} />
      </div>
    </div>
    ${proposalId ? html`<${PlanDetail} key=${proposalId} token=${token} proposalId=${proposalId} reloadRef=${reloadRef} />` : html`<div>
      <div class="filters">
        <label for="plans-status">Estado</label>
        <select id="plans-status" value=${statusFilter} ...${helpFor("plans.filter")} onChange=${(e) => setStatusFilter(e.target.value)}>
          <option value="">Todos</option>
          <option value="proposed">Propuesta</option>
          <option value="approved">Aprobada</option>
          <option value="rejected">Rechazada</option>
        </select>
        <${Help} topic="plans.filter" />
      </div>
      <h4>Lista de propuestas</h4><${Help} topic="plans.list" />
      ${list.error && html`<${Failure} error=${list.error} />`}
      ${list.loading && !list.data ? html`<p role="status" aria-live="polite">Cargando los planes…</p>` : list.data && html`<${PlanList} data=${list.data} />`}
    </div>`}
  </div>`;
}
