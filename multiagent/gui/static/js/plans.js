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
import { TaskInputs } from "./plan-thread.js";   // CHT-T06
import { inputsValue } from "./plan-card-model.js";
import { currentLanguage, t } from "./i18n.js";

const BASE = "/gui/api/plan-proposals";
const enc = encodeURIComponent;

export const PLAN_STATUS = {
  proposed: { get text() { return t("plans.status.proposed"); }, tone: "attention", get explanation() { return t("plans.status.proposedExplanation"); } },
  approved: { get text() { return t("plans.status.approved"); }, tone: "success", get explanation() { return t("plans.status.approvedExplanation"); } },
  rejected: { get text() { return t("plans.status.rejected"); }, tone: "danger", get explanation() { return t("plans.status.rejectedExplanation"); } },
};

export const HISTORY_KIND = {
  get save() { return t("plans.historyKind.save"); },
  get edit() { return t("plans.historyKind.edit"); },
  get reassign() { return t("plans.historyKind.reassign"); },
  get approve() { return t("plans.historyKind.approve"); },
  get reject() { return t("plans.historyKind.reject"); },
};

// What «Avanzar plan» answers, as one sentence: the tasks that moved, or that nothing was left to advance.
export function advanceSummary(result) {
  const moved = result && Array.isArray(result.advanced) ? result.advanced : [];
  if (!moved.length) return t("plans.advance.nothingToAdvance");
  return t("plans.advance.summary", { taskIds: moved.map((item) => item.task_id).join(", ") });
}

const when = (value) => {
  if (!value) return t("plans.date.none");
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? t("plans.date.unavailable")
    : new Intl.DateTimeFormat(currentLanguage() === "es" ? "es-MX" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(date);
};

// The error of the service as it came: its message and its code.
export const failureText = (error) =>
  (error && error.message ? error.message : t("plans.error.unknown")) + (error && error.code ? " (" + error.code + ")" : "");

export const formatDependencies = (deps) =>
  Array.isArray(deps) && deps.length ? deps.join(", ") : t("plans.dependencies.none");

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
  return html`<p class="message error" role="alert">${error ? failureText(error) : t("plans.error.readPlan")}</p>`;
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
  if (!proposals.length) return html`<p>${t("plans.list.empty")}</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">${t("plans.list.caption")}</caption>
    <thead><tr><th scope="col">${t("plans.list.proposal")}</th><th scope="col">${t("plans.list.group")}</th><th scope="col">${t("plans.list.status")}</th><th scope="col">${t("plans.list.revision")}</th><th scope="col">${t("plans.list.tasks")}</th><th scope="col">${t("plans.list.created")}</th></tr></thead>
    <tbody>${proposals.map((entry) => {
      const tasks = entry.proposal && Array.isArray(entry.proposal.tasks) ? entry.proposal.tasks : [];
      return html`<tr key=${entry.id}>
        <th scope="row"><a href=${routeHref("plans", entry.id)}>${entry.goal || entry.id}</a>
          <div class="muted small">${entry.id} · ${entry.planner}</div></th>
        <td>${entry.group_id}</td>
        <td><${StatusChip} status=${entry.status} labels=${PLAN_STATUS} /></td>
        <td>${t("plans.revision.value", { revision: entry.revision })}</td>
        <td>${tasks.length}</td>
        <td>${entry.created_by || t("plans.data.notProvided")} <span class="muted small">${when(entry.created_at)}</span></td>
      </tr>`;
    })}</tbody>
  </table></div>`;
}

export function TasksTable({ tasks }) {
  const list = Array.isArray(tasks) ? tasks : [];
  if (!list.length) return html`<p>${t("plans.tasks.empty")}</p>`;
  return html`<div class="usage-table-wrap"><table class="usage-table">
    <caption class="sr-only">${t("plans.tasks.caption")}</caption>
    <thead><tr><th scope="col">${t("plans.tasks.task")}</th><th scope="col">${t("plans.tasks.dependencies")}</th><th scope="col">${t("plans.tasks.workflow")}</th><th scope="col">${t("plans.tasks.preferred")}</th></tr></thead>
    <tbody>${list.map((task) => html`<tr key=${task.id}>
      <th scope="row">${task.title}
        <div class="muted small">${task.id}</div>
        ${task.objective && html`<div class="small">${task.objective}</div>`}
        ${inputsValue(task.inputs) && html`<${TaskInputs} inputs=${task.inputs} />`}</th>
      <td>${Array.isArray(task.depends_on) && task.depends_on.length
        ? html`<ul>${task.depends_on.map((dep) => html`<li key=${dep}>${dep}</li>`)}</ul>`
        : t("plans.dependencies.none")}</td>
      <td>${task.workflow}${task.bundle ? " (" + task.bundle + ")" : ""}</td>
      <td>${task.preferred || t("plans.tasks.noPreference")}</td>
    </tr>`)}</tbody>
  </table></div>`;
}

export function HistoryList({ history }) {
  const entries = Array.isArray(history) ? history : [];
  if (!entries.length) return html`<p>${t("plans.history.empty")}</p>`;
  return html`<ul class="history">
    ${entries.map((entry, index) => {
      const touched = changedTaskIds(entry.changes);
      return html`<li key=${entry.id || index}>
        <span class="when">${when(entry.at)} · ${t("plans.revision.value", { revision: entry.revision })}</span>
        <span class="who">${Object.prototype.hasOwnProperty.call(HISTORY_KIND, entry.kind) ? HISTORY_KIND[entry.kind] : entry.kind} · ${entry.actor} (${entry.channel})</span>
        ${touched.length > 0 && html`<span class="what">${t("plans.history.tasksModified")}: ${touched.join(", ")}</span>`}
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
    setMessage({ kind: "info", text: t("plans.action.sending") });
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
    <h4>${t("plans.advance.title")}</h4><${Help} topic="plans.advance" />
    <div class="actions">
      <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.advance")}
        onClick=${() => setConfirm(true)}>${t("plans.advance.action")}</button>
    </div>
    ${confirm && html`<${Confirm} title=${t("plans.advance.confirmTitle")} confirmLabel=${t("plans.advance.confirm")}
      keepLabel=${t("plans.action.cancel")} danger=${false} busy=${busy} onKeep=${() => setConfirm(false)} onConfirm=${advance}>
      <p class="muted">${t("plans.advance.explanation")}</p>
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
    setMessage({ kind: "info", text: t("plans.action.sending") });
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
        setMessage({
          kind: "error",
          text: t("plans.action.conflictReloaded", { error: failureText(failure) }),
        });
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
    const status = Object.prototype.hasOwnProperty.call(PLAN_STATUS, proposal.status)
      ? PLAN_STATUS[proposal.status].text : (proposal.status ? String(proposal.status) : "");
    return html`<div class="panel">
      <p class="muted">${t("plans.action.alreadyDecided", {
        status,
      })}</p>
      ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
      ${proposal.status === "approved" && html`<${AdvanceAction} token=${token} proposal=${proposal} />`}
    </div>`;
  }

  const chosenTask = tasks.find((item) => item.id === taskId) || tasks[0];
  const confirmBox = () => {
    if (confirm === "edit") {
      return html`<${Confirm} title=${t("plans.confirm.editTitle")} confirmLabel=${t("plans.confirm.edit")}
        keepLabel=${t("plans.confirm.keepEditing")} danger=${false} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("edit", { tasks: buildEditedTasks(editDrafts), expected_revision: proposal.revision, note: note.trim() },
          t("plans.confirm.editSuccess"))}>
        <p class="muted">${t("plans.confirm.editExplanation", { revision: proposal.revision })}</p>
        <label for="plan-note">${t("plans.note.label")}</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    if (confirm === "reassign" && chosenTask) {
      return html`<${Confirm} title=${t("plans.confirm.reassignTitle", { taskId: chosenTask.id })} confirmLabel=${t("plans.confirm.reassign")}
        keepLabel=${t("plans.action.cancel")} danger=${false} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("reassign", buildReassignPayload(chosenTask, reassignDraft, proposal.revision, note),
          t("plans.confirm.reassignSuccess"))}>
        <p class="muted">${t("plans.confirm.reassignExplanation", { taskId: chosenTask.id, revision: proposal.revision })}</p>
        <label for="plan-note">${t("plans.note.label")}</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    if (confirm === "approve") {
      return html`<${Confirm} title=${t("plans.confirm.approveTitle")} confirmLabel=${t("plans.confirm.approve")}
        keepLabel=${t("plans.confirm.keepPending")} danger=${false} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("approve", { expected_revision: proposal.revision, note: note.trim() },
          t("plans.confirm.approveSuccess"))}>
        <p class="muted">${t("plans.confirm.approveExplanation", { revision: proposal.revision })}</p>
        <label for="plan-note">${t("plans.note.label")}</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    if (confirm === "reject") {
      return html`<${Confirm} title=${t("plans.confirm.rejectTitle")} confirmLabel=${t("plans.confirm.reject")}
        keepLabel=${t("plans.confirm.keepPending")} danger=${true} busy=${busy} onKeep=${() => setConfirm(null)}
        onConfirm=${() => submitAction("reject", { note: note.trim() }, t("plans.confirm.rejectSuccess"))}>
        <p class="muted">${t("plans.confirm.rejectExplanation")}</p>
        <label for="plan-note">${t("plans.note.label")}</label><${Help} topic="plans.note" />
        <textarea id="plan-note" rows="2" maxlength="4000" value=${note} ...${helpFor("plans.note")} onInput=${(e) => setNote(e.target.value)}></textarea>
      <//>`;
    }
    return null;
  };

  return html`<div class="stack">
    <div class="panel">
      <h4>${t("plans.edit.title")}</h4><${Help} topic="plans.edit" />
      <div class="actions">
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("plans.edit")}
          onClick=${() => { setEditDrafts(toEditDrafts(tasks)); setEditing((open) => !open); setConfirm(null); }}>
          ${editing ? t("plans.edit.close") : t("plans.edit.action")}
        </button>
      </div>
      ${editing && html`<div class="stack">
        ${editDrafts.map((task, index) => html`<fieldset class="inputs" key=${task.id}>
          <legend>${t("plans.edit.taskLegend", { taskId: task.id })}</legend>
          <label for=${"edit-title-" + task.id}>${t("plans.edit.titleLabel")}</label>
          <input id=${"edit-title-" + task.id} type="text" maxlength="200" value=${task.title} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "title", e.target.value)} />
          <label for=${"edit-obj-" + task.id}>${t("plans.edit.objectiveLabel")}</label>
          <textarea id=${"edit-obj-" + task.id} rows="2" maxlength="4000" value=${task.objective} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "objective", e.target.value)}></textarea>
          <label for=${"edit-deps-" + task.id}>${t("plans.edit.dependenciesLabel")}</label>
          <input id=${"edit-deps-" + task.id} type="text" value=${task.depends_on_text} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "depends_on_text", e.target.value)} />
          <div class="two">
            <div>
              <label for=${"edit-wf-" + task.id}>${t("plans.edit.workflowLabel")}</label>
              <input id=${"edit-wf-" + task.id} type="text" value=${task.workflow} ...${helpFor("plans.edit")}
                onInput=${(e) => updateDraft(index, "workflow", e.target.value)} />
            </div>
            <div>
              <label for=${"edit-bundle-" + task.id}>${t("plans.edit.bundleLabel")}</label>
              <input id=${"edit-bundle-" + task.id} type="text" value=${task.bundle} ...${helpFor("plans.edit")}
                onInput=${(e) => updateDraft(index, "bundle", e.target.value)} />
            </div>
          </div>
          <label for=${"edit-pref-" + task.id}>${t("plans.edit.preferredLabel")}</label>
          <input id=${"edit-pref-" + task.id} type="text" maxlength="100" value=${task.preferred} ...${helpFor("plans.edit")}
            onInput=${(e) => updateDraft(index, "preferred", e.target.value)} />
        </fieldset>`)}
        <div class="actions">
          <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.edit")}
            onClick=${() => setConfirm("edit")}>${t("plans.edit.save")}</button>
        </div>
        ${confirm === "edit" && confirmBox()}
      </div>`}
    </div>

    <div class="panel">
      <h4>${t("plans.reassign.title")}</h4><${Help} topic="plans.reassign" />
      <div class="stack">
        <label for="reassign-task">${t("plans.tasks.task")}</label>
        <select id="reassign-task" value=${taskId} ...${helpFor("plans.reassign")} onChange=${(e) => selectReassignTask(e.target.value)}>
          ${tasks.map((task) => html`<option key=${task.id} value=${task.id}>${task.id} — ${task.title}</option>`)}
        </select>
        <div class="two">
          <div>
            <label for="reassign-preferred">${t("plans.edit.preferredField")}</label>
            <input id="reassign-preferred" type="text" maxlength="100" value=${reassignDraft.preferred} ...${helpFor("plans.reassign")}
              onInput=${(e) => setReassignDraft((prev) => ({ ...prev, preferred: e.target.value }))} />
          </div>
          <div>
            <label for="reassign-workflow">${t("plans.edit.workflowLabel")}</label>
            <input id="reassign-workflow" type="text" maxlength="120" value=${reassignDraft.workflow} ...${helpFor("plans.reassign")}
              onInput=${(e) => setReassignDraft((prev) => ({ ...prev, workflow: e.target.value }))} />
          </div>
        </div>
        <label for="reassign-bundle">${t("plans.edit.bundleLabel")}</label>
        <input id="reassign-bundle" type="text" maxlength="120" value=${reassignDraft.bundle} ...${helpFor("plans.reassign")}
          onInput=${(e) => setReassignDraft((prev) => ({ ...prev, bundle: e.target.value }))} />
        <div class="actions">
          <button type="button" class="btn secondary" disabled=${busy || !chosenTask} ...${helpFor("plans.reassign")}
            onClick=${() => setConfirm("reassign")}>${t("plans.reassign.action")}</button>
        </div>
        ${confirm === "reassign" && confirmBox()}
      </div>
    </div>

    <div class="panel">
      <h4>${t("plans.decision.title")}</h4>
      <div class="actions">
        <span class="action-item">
          <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.approve")}
            onClick=${() => setConfirm("approve")}>${t("plans.decision.approve")}</button>
          <${Help} topic="plans.approve" />
        </span>
        <span class="action-item">
          <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("plans.reject")}
            onClick=${() => setConfirm("reject")}>${t("plans.decision.reject")}</button>
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
    <p><a href=${routeHref("plans")}>${t("plans.detail.allPlans")}</a></p>
    ${state.error && html`<${Failure} error=${state.error} />`}
    ${state.loading && !proposal ? html`<p role="status" aria-live="polite">${t("plans.detail.loading")}</p>` : proposal && html`<div class="stack">
      <div class="panel">
        <h4>${proposal.goal}</h4>
        <dl class="facts">
          <dt>${t("plans.detail.identifier")}</dt><dd>${proposal.id}</dd>
          <dt>${t("plans.list.group")}</dt><dd>${proposal.group_id}</dd>
          <dt>${t("plans.list.status")}</dt><dd><${StatusChip} status=${proposal.status} labels=${PLAN_STATUS} /></dd>
          <dt>${t("plans.list.revision")}</dt><dd>${t("plans.revision.value", { revision: proposal.revision })}</dd>
          <dt>${t("plans.detail.planner")}</dt><dd>${proposal.planner}</dd>
          <dt>${t("plans.detail.createdBy")}</dt><dd>${proposal.created_by} (${proposal.created_channel}) · ${when(proposal.created_at)}</dd>
          ${proposal.decided_by && html`<dt>${t("plans.detail.decision")}</dt><dd>${proposal.decided_by} (${proposal.decided_channel}) · ${when(proposal.decided_at)}</dd>`}
          ${proposal.proposal && proposal.proposal.rationale && html`<dt>${t("plans.detail.rationale")}</dt><dd>${proposal.proposal.rationale}</dd>`}
        </dl>
      </div>
      <h4>${t("plans.detail.tasksAndDependencies")}</h4><${Help} topic="plans.detail" />
      <${TasksTable} tasks=${tasks} />
      <${PlanActions} token=${token} proposal=${proposal}
        onUpdated=${(updated) => setState({ loading: false, data: updated, error: null })}
        onReload=${load} />
      <h4>${t("plans.detail.changeHistory")}</h4><${Help} topic="plans.history" />
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
    <h3 id="plans-title">${t("plans.title")}</h3><${Help} topic="plans.section" always=${true} />
    <div class="usage-title-row">
      <p class="muted">${t("plans.subtitle")}</p>
      <div class="usage-refresh-control" role="group" aria-label=${t("plans.refresh.groupLabel")}>
        <button type="button" class="btn" disabled=${busy} ...${helpFor("plans.refresh")} onClick=${refresh}>${busy ? t("plans.refresh.updating") : t("plans.refresh.action")}</button>
        <${Help} topic="plans.refresh" compact=${true} />
      </div>
    </div>
    ${proposalId ? html`<${PlanDetail} key=${proposalId} token=${token} proposalId=${proposalId} reloadRef=${reloadRef} />` : html`<div>
      <div class="filters">
        <label for="plans-status">${t("plans.list.status")}</label>
        <select id="plans-status" value=${statusFilter} ...${helpFor("plans.filter")} onChange=${(e) => setStatusFilter(e.target.value)}>
          <option value="">${t("plans.filter.all")}</option>
          <option value="proposed">${t("plans.status.proposed")}</option>
          <option value="approved">${t("plans.status.approved")}</option>
          <option value="rejected">${t("plans.status.rejected")}</option>
        </select>
        <${Help} topic="plans.filter" />
      </div>
      <h4>${t("plans.list.title")}</h4><${Help} topic="plans.list" />
      ${list.error && html`<${Failure} error=${list.error} />`}
      ${list.loading && !list.data ? html`<p role="status" aria-live="polite">${t("plans.list.loading")}</p>` : list.data && html`<${PlanList} data=${list.data} />`}
    </div>`}
  </div>`;
}
