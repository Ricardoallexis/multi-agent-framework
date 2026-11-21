// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Read-only wait badges and the decision inbox, through FLU-T04. No shared shell file or decision endpoint is changed.
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { registerHelp, registerSlot, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { useTaskWaiting, waitBadge, waitRecipient, waitingForUser, waitDate } from "../../js/task-waiting.js";
import { HELP } from "./help.js";

registerHelp(HELP, "task_waiting");

export function WaitingTask({ task }) {
  const wait = task.waiting;
  return html`<li class="task-waiting-card">
    <p class="task-waiting-title"><strong>${task.id} · ${task.title || task.id}</strong>
      <span class="task-waiting-badge">${waitBadge(wait)}</span></p>
    <p class="task-waiting-question">${wait.question}</p>
    <dl class="task-waiting-details">
      <dt>Espera a</dt><dd>${waitRecipient(wait)}</dd>
      <dt>Desde</dt><dd><time dateTime=${wait.since}>${waitDate(wait.since)}</time></dd>
      ${wait.reset_at && html`<dt>Restablecimiento</dt><dd><time dateTime=${wait.reset_at}>${waitDate(wait.reset_at)}</time></dd>`}
      ${wait.reference && html`<dt>Referencia</dt><dd>${wait.reference}</dd>`}
      <dt>Estado de la tarea</dt><dd>${task.label?.text || task.state}</dd>
    </dl>
  </li>`;
}

function WaitingList({ tasks, empty }) {
  return tasks.length ? html`<ul class="task-waiting-list">${tasks.map((task) => html`<${WaitingTask} key=${task.id} task=${task} />`)}</ul>`
    : html`<p class="muted small">${empty}</p>`;
}

export function WaitingContent({ data, scope, others = false }) {
  const decisions = waitingForUser(data.tasks);
  return html`<div class="task-waiting-content" aria-busy=${data.loading ? "true" : "false"}>
    <div class="task-waiting-bar">
      <p role="status" aria-live="polite">${data.loading ? "Leyendo esperas…" : data.error ? "No se pudieron actualizar las esperas."
        : decisions.length + (decisions.length === 1 ? " tarea espera tu decisión." : " tareas esperan tu decisión.")}</p>
      <button type="button" class="btn secondary small" ...${helpFor("task_waiting.refresh", { scope })} onClick=${data.reload}>Actualizar</button>
      <${Help} topic="task_waiting.refresh" compact=${true} scope=${scope} />
    </div>
    ${data.error && html`<p class="task-waiting-error" role="alert">${data.tasks.length
      ? "Los datos visibles son anteriores al último intento. " : ""}${data.error.message}</p>`}
    ${!data.loading && (!data.error || data.tasks.length > 0) && html`
      <${WaitingList} tasks=${decisions} empty="Ninguna tarea espera tu decisión." />
      ${others && html`<h4 class="task-waiting-subtitle">Otras esperas</h4><${Help} topic="task_waiting.others" />
        <${WaitingList} tasks=${data.tasks.filter((task) => task.waiting.who !== "user")} empty="No hay otras esperas." />`}`}
  </div>`;
}

export function TaskWaitingView({ token }) {
  const data = useTaskWaiting(token, "view");
  return html`<section class="task-waiting-view" aria-label="Esperan tu decisión">
    <${WaitingContent} data=${data} scope="view" others=${true} />
  </section>`;
}

export function TaskWaitingPanel({ token }) {
  const data = useTaskWaiting(token, "panel");
  return html`<${WaitingContent} data=${data} scope="panel" />`;
}

registerView({ id: "task_waiting", owner: "task_waiting", group: "pending", icon: "clock", label: "Esperan tu decisión", topic: "task_waiting.view", component: TaskWaitingView, order: 55 });
registerSlot("task_waiting", TaskWaitingPanel, { title: "Esperan tu decisión", topic: "task_waiting.panel", owner: "task_waiting", order: 55 });
