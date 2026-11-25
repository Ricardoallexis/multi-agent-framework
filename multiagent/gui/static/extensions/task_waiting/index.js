// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Read-only wait badges and the decision inbox, through FLU-T04. No shared shell file or decision endpoint is changed.
import "./i18n.js";
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { useContentReport } from "../../js/context-report.js";
import { registerHelp, registerSlot, registerView } from "../../js/extensions.js";
import { Help, helpFor } from "../../js/help.js";
import { t } from "../../js/i18n.js";
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
      <dt>${t("ext.task_waiting.recipient")}</dt><dd>${waitRecipient(wait)}</dd>
      <dt>${t("ext.task_waiting.since")}</dt><dd><time dateTime=${wait.since}>${waitDate(wait.since)}</time></dd>
      ${wait.reset_at && html`<dt>${t("ext.task_waiting.reset")}</dt><dd><time dateTime=${wait.reset_at}>${waitDate(wait.reset_at)}</time></dd>`}
      ${wait.reference && html`<dt>${t("ext.task_waiting.reference")}</dt><dd>${wait.reference}</dd>`}
      <dt>${t("ext.task_waiting.task_state")}</dt><dd>${task.label?.text || task.state}</dd>
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
      <p role="status" aria-live="polite">${data.loading ? t("ext.task_waiting.loading") : data.error
        ? t("ext.task_waiting.error.refresh")
        : t(decisions.length === 1 ? "ext.task_waiting.count.one" : "ext.task_waiting.count.many", { count: decisions.length })}</p>
      <button type="button" class="btn secondary small" ...${helpFor("task_waiting.refresh", { scope })} onClick=${data.reload}>${t("ext.task_waiting.refresh")}</button>
      <${Help} topic="task_waiting.refresh" compact=${true} scope=${scope} />
    </div>
    ${data.error && html`<p class="task-waiting-error" role="alert">${data.tasks.length
      ? t("ext.task_waiting.error.stale") + " " : ""}${data.error.message}</p>`}
    ${!data.loading && (!data.error || data.tasks.length > 0) && html`
      <${WaitingList} tasks=${decisions} empty=${t("ext.task_waiting.empty.decisions")} />
      ${others && html`<h4 class="task-waiting-subtitle">${t("ext.task_waiting.others")}</h4><${Help} topic="task_waiting.others" />
        <${WaitingList} tasks=${data.tasks.filter((task) => task.waiting.who !== "user")} empty=${t("ext.task_waiting.empty.others")} />`}
      `}
  </div>`;
}

export function TaskWaitingView({ token }) {
  const data = useTaskWaiting(token, "view");
  return html`<section class="task-waiting-view" aria-label=${t("ext.task_waiting.title")}>
    <${WaitingContent} data=${data} scope="view" others=${true} />
  </section>`;
}

export function TaskWaitingPanel({ token, onContent }) {
  const data = useTaskWaiting(token, "panel");
  useContentReport(onContent, waitingForUser(data.tasks).length > 0);   // GUI3-T04
  return html`<${WaitingContent} data=${data} scope="panel" />`;
}

registerView({ id: "task_waiting", owner: "task_waiting", group: "pending", pendingType: "task", icon: "clock", label: t("ext.task_waiting.title"), topic: "task_waiting.view", component: TaskWaitingView, order: 55, mode: "simple" });
registerSlot("task_waiting", TaskWaitingPanel, { title: t("ext.task_waiting.title"), topic: "task_waiting.panel", owner: "task_waiting", order: 55,
  defaultMode: "never" });   // GUI3-T04: the same list is in «Pendientes»; it can be set to «Automática» or «Siempre» (GUI3-T07)
