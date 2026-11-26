// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The pieces of the project view (PRY-T03): the summary, the stages with their phases and the detail of one task. They draw
// what index.js loaded and call back for everything that changes something; the rules for what to show are in model.js.
// Every value is drawn as text (Preact escapes it): results written by agents are untrusted.
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { Help, helpFor } from "../../js/help.js";
import { currentMode, onModeChange } from "../../js/gui-mode.js";
import { t } from "../../js/i18n.js";
import { SegmentBar } from "../../js/segment-bar.js";
import { readHiddenStages, writeHiddenStages } from "../../js/shell-state.js";
import { Progress } from "../../js/ui/progress.js";
import { StatusChip } from "../../js/ui/status-chip.js";
import {
  attentionRows, canReview, chipOf, dayText, deliverables, detailOf, hasFinished, hiddenIn, hideFinished, hideStage, labelSummary, list, listOwners,
  listRows, matchesFilter, matchesList, overviewFigures, phaseGroups, planText, progressOf, projectSegments, requiresText, SEGMENT_LIMIT, shortId, stageRow,
  taskIndex, taskName, visibleStages, whoText,
} from "./model.js";

// One row of a list of tasks: opens the detail of the task. It is data, not an action, so it has no help of its own.
function TaskRow({ task, project, current, onOpen, verb = "", children }) {
  return html`<li>
    <button type="button" class="project-task-row" data-help-exempt="data-row" aria-current=${current ? "true" : "false"}
      onClick=${() => onOpen(task.id)}>
      <span class="project-row-head">
        <span class="project-title">${verb ? html`<span class="project-verb">${verb}</span> ` : null}${task.title}</span>
        <${StatusChip} ...${chipOf(task.label)} />
      </span>
      <span class="project-meta">${shortId(task.id, project.id)} · ${whoText(task)} · ${dayText(task.due_at)}</span>
      ${children}
    </button>
  </li>`;
}

function BlockerLines({ task, project }) {
  const index = taskIndex(project);
  const blockers = list(task.blockers);
  if (!blockers.length) return null;
  return html`<span class="project-blockers">${blockers.map((blocker) => {
    const found = index.get(blocker.task_id);
    const state = found ? found.task.label.text : blocker.state || t("ext.project.status.no_state");
    return html`<span key=${blocker.task_id}>${t("ext.project.blocker.line", {
      task: taskName(blocker.task_id, project), state, requires: requiresText(blocker.requires),
    })}</span>`;
  })}</span>`;
}

// The stages the person hid in this browser (AVZ-T03). The Overview and the Stages tab are never open together, so each reads the
// choice when it opens and both write it through shell-state.js; a project that changes starts from its own choice.
function useHiddenStages(project) {
  const [state, setState] = useState(() => ({ id: project.id, hidden: readHiddenStages(project.id) }));
  const stored = state.id === project.id ? state.hidden : readHiddenStages(project.id);
  const save = (next) => {
    writeHiddenStages(project.id, next);
    setState({ id: project.id, hidden: next });
  };
  return [hiddenIn(project, stored), save];
}

// The simple or advanced mode of the GUI; the technical columns of the table of stages are for advanced only.
function useMode() {
  const [mode, setMode] = useState(currentMode());
  useEffect(() => onModeChange(setMode), []);
  return mode;
}

// «Etapas ocultas (3) · Mostrar» and «Ocultar las terminadas», above the stages of both tabs.
function HiddenBar({ project, hidden, onChange }) {
  return html`<div class="project-hidden-bar">
    ${hidden.length ? html`<span class="project-hidden-count" role="status">${t("ext.project.stages.hidden_count", { count: hidden.length })}</span>
      <button type="button" class="btn secondary small" onClick=${() => onChange([])} ...${helpFor("project.stages_show")}>${t("ext.project.stages.show")}</button>
      <${Help} topic="project.stages_show" compact=${true} />` : null}
    <button type="button" class="btn secondary small" disabled=${!hasFinished(project, hidden)}
      onClick=${() => onChange(hideFinished(project, hidden))} ...${helpFor("project.stages_hide_done")}>${t("ext.project.stages.hide_done")}</button>
    <${Help} topic="project.stages_hide_done" compact=${true} />
  </div>`;
}

function HideStage({ project, hidden, stage, scope, onChange }) {
  return html`<span class="project-hide">
    <button type="button" class="btn secondary small" aria-label=${t("ext.project.stages.hide_stage", { code: stage.code, title: stage.title })}
      onClick=${() => onChange(hideStage(project, hidden, stage.group_id))} ...${helpFor("project.stage_hide", { scope })}>${t("ext.project.stages.hide")}</button>
    <${Help} topic="project.stage_hide" scope=${scope} compact=${true} />
  </span>`;
}

function Figure({ value, label, note = "" }) {
  return html`<li class="project-figure">
    <span class="project-figure-value">${value}</span>
    <span class="project-figure-label">${label}</span>
    ${note ? html`<span class="project-meta">${note}</span>` : null}
  </li>`;
}

const noData = () => t("ext.project.figures.no_data");

// The strip of figures of the summary: stages, tasks done, weighted progress, in review, blocked and what is left.
function Figures({ project }) {
  const figures = overviewFigures(project);
  return html`<ul class="project-figures" aria-label=${t("ext.project.figures.aria_label")}>
    <${Figure} value=${figures.stages} label=${t("ext.project.figures.stages")} />
    <${Figure} value=${t("ext.project.figures.of", { done: figures.done, total: figures.counted })} label=${t("ext.project.figures.tasks")} />
    <${Figure} value=${figures.percent === null ? noData() : t("ext.project.figures.percent", { percent: figures.percent })}
      label=${t("ext.project.figures.weighted")} />
    <${Figure} value=${figures.review} label=${t("ext.project.figures.review")} />
    <${Figure} value=${figures.blocked} label=${t("ext.project.figures.blocked")} />
    <${Figure} value=${figures.leftTasks === null ? noData() : figures.leftTasks} label=${t("ext.project.figures.left")}
      note=${figures.leftSteps === null || figures.leftTasks === 0 ? "" : t("ext.project.figures.left_steps", { count: figures.leftSteps })} />
  </ul>`;
}

// The table of stages: name, state, a small bar and «n of m». The technical columns (plan, last change, weighted progress) are for
// advanced mode. A hidden stage has no row here; it is still counted in every figure above.
function StageTable({ project, hidden, advanced, onChange }) {
  const stages = visibleStages(project, hidden);
  if (!list(project.stages).length) return html`<p class="project-empty">${t("ext.project.stages.none")}</p>`;
  if (!stages.length) return html`<p class="project-empty">${t("ext.project.stages.all_hidden")}</p>`;
  return html`<div class="usage-table-wrap"><table class="project-table">
    <caption class="sr-only">${t("ext.project.table.title")}</caption>
    <thead><tr>
      <th scope="col">${t("ext.project.table.stage")}</th>
      <th scope="col">${t("ext.project.table.state")}</th>
      <th scope="col">${t("ext.project.table.bar")}</th>
      <th scope="col">${t("ext.project.table.done")}</th>
      ${advanced ? html`<th scope="col">${t("ext.project.table.weighted")}</th><th scope="col">${t("ext.project.table.plan")}</th>
        <th scope="col">${t("ext.project.table.changed")}</th>` : null}
      <th scope="col"><span class="sr-only">${t("ext.project.table.actions")}</span></th>
    </tr></thead>
    <tbody>${stages.map((stage, index) => {
      const row = stageRow(stage);
      return html`<tr key=${stage.group_id}>
        <th scope="row" class="project-cell-name">${row.code} · ${row.title}</th>
        <td><${StatusChip} ...${chipOf(row.label)} /></td>
        <td class="project-cell-bar"><${SegmentBar} segments=${row.segments} summary=${row.summary} className="project-segbar project-segbar-small" /></td>
        <td class="project-cell-done">${t("ext.project.table.n_of_m", { done: row.done, total: row.counted })}</td>
        ${advanced ? html`<td>${row.percent === null ? noData() : t("ext.project.figures.percent", { percent: row.percent })}</td>
          <td>${row.planned}</td><td>${row.changedAt ? dayText(row.changedAt) : noData()}</td>` : null}
        <td class="project-cell-hide"><${HideStage} project=${project} hidden=${hidden} stage=${stage} scope=${"table" + index} onChange=${onChange} /></td>
      </tr>`;
    })}</tbody>
  </table></div>`;
}

export function Overview({ project, onOpen }) {
  const [hidden, setHidden] = useHiddenStages(project);
  const advanced = useMode() === "advanced";
  const bar = progressOf(project.progress);
  const pieces = projectSegments(project);
  const summary = labelSummary(project);
  const rows = attentionRows(project, hidden);
  const rowsOf = (kind) => rows.filter((row) => row.kind === kind);
  const reviews = rowsOf("review");
  const verifications = rowsOf("verify");
  const mine = rowsOf("mine");
  const blocked = rowsOf("blocked");
  const working = list(project.working_now);
  const stages = new Map(list(project.stages).map((stage) => [stage.group_id, stage]));
  const nothing = !rows.length;
  const hiddenNote = (row) => (row.hidden ? html`<span class="project-meta">${t("ext.project.attention.stage_hidden")}</span>` : null);
  return html`<div class="project-stages">
    <section class="project-section" aria-labelledby="project-progress-title">
      <h4 id="project-progress-title">${t("ext.project.progress.title")}</h4><${Help} topic="project.progress" />
      <${Figures} project=${project} />
      <${Progress} label=${t("ext.project.progress.aria_label")} value=${bar.value} max=${bar.max} valueText=${bar.valueText} />
      ${pieces.segments.length ? html`<${SegmentBar} segments=${pieces.segments} summary=${pieces.summary} className="project-segbar" />` : null}
      ${pieces.unit === "stages" ? html`<p class="project-meta">${t("ext.project.segments.by_stage_note", { limit: SEGMENT_LIMIT })}</p>` : null}
      <ul class="project-counts" aria-label=${t("ext.project.status_counts")}>
        ${summary.map(({ label, count }) => html`<li key=${label.key} class="project-count">
          <${StatusChip} ...${chipOf(label)} /><span class="project-count-number">${count}</span>
        </li>`)}
      </ul>
    </section>

    <section class="project-section" aria-labelledby="project-attention-title">
      <h4 id="project-attention-title">${t("ext.project.attention.title")}</h4><${Help} topic="project.attention" />
      ${nothing ? html`<p class="project-empty">${t("ext.project.attention.none")}</p>` : null}
      ${reviews.length ? html`<h5 class="project-group-title">${t("ext.project.attention.reviews", { count: reviews.length })}</h5>
        <ul class="project-items">${reviews.map((row) => html`<${TaskRow} key=${row.id} task=${row.item} project=${project} onOpen=${onOpen} verb=${row.verb}>
          ${hiddenNote(row)}
        <//>`)}</ul>` : null}
      ${verifications.length ? html`<h5 class="project-group-title">${t("ext.project.attention.verifications", { count: verifications.length })}</h5>
        <ul class="project-items">${verifications.map((row) => html`<li key=${row.id} class="project-line">
          <span class="project-row-head"><span class="project-title"><span class="project-verb">${row.verb}</span> ${row.item.code} · ${row.item.title}</span>
            <${StatusChip} ...${chipOf(row.item.label)} /></span>
          <span class="project-meta">${t("ext.project.attention.verify_note")}</span>
          ${hiddenNote(row)}
        </li>`)}</ul>` : null}
      ${mine.length ? html`<h5 class="project-group-title">${t("ext.project.attention.mine", { count: mine.length })}</h5>
        <ul class="project-items">${mine.map((row) => html`<${TaskRow} key=${row.id} task=${row.item} project=${project} onOpen=${onOpen} verb=${row.verb}>
          ${hiddenNote(row)}
        <//>`)}</ul>` : null}
      ${blocked.length ? html`<h5 class="project-group-title">${t("ext.project.attention.blocked", { count: blocked.length })}</h5>
        <ul class="project-items">${blocked.map((row) => html`<${TaskRow} key=${row.id} task=${row.item} project=${project} onOpen=${onOpen} verb=${row.verb}>
          <${BlockerLines} task=${row.item} project=${project} />
          ${hiddenNote(row)}
        <//>`)}</ul>` : null}
    </section>

    <section class="project-section" aria-labelledby="project-stage-table-title">
      <h4 id="project-stage-table-title">${t("ext.project.table.title")}</h4><${Help} topic="project.stage_table" />
      <${HiddenBar} project=${project} hidden=${hidden} onChange=${setHidden} />
      <${StageTable} project=${project} hidden=${hidden} advanced=${advanced} onChange=${setHidden} />
    </section>

    <section class="project-section" aria-labelledby="project-working-title">
      <h4 id="project-working-title">${t("ext.project.working.title")}</h4><${Help} topic="project.working_now" />
      ${working.length ? html`<ul class="project-items">${working.map((task) => task.kind === "activity"
        ? html`<li key=${task.id} class="project-line">
            <span class="project-row-head"><span class="project-title">${task.title}</span><${StatusChip} ...${chipOf(task.label)} /></span>
            <span class="project-meta">${t("ext.project.working.activity", { owner: whoText(task) })}</span>
          </li>`
        : html`<${TaskRow} key=${task.id} task=${task} project=${project} onOpen=${onOpen} />`)}</ul>`
        : html`<p class="project-empty">${t("ext.project.working.none")}</p>`}
    </section>

    <section class="project-section" aria-labelledby="project-milestones-title">
      <h4 id="project-milestones-title">${t("ext.project.milestones.title")}</h4><${Help} topic="project.milestones" />
      ${list(project.milestones).length ? html`<ul class="project-items">${list(project.milestones).map((milestone) => {
        const stage = stages.get(milestone.group_id);
        return html`<li key=${milestone.group_id} class="project-line">
          <span class="project-row-head"><span class="project-title">${milestone.code} · ${milestone.title}</span>
            ${stage ? html`<${StatusChip} ...${chipOf(stage.label)} />` : null}</span>
          <span class="project-meta">${t("ext.project.milestones.dates", {
            planned: dayText(milestone.planned, t("ext.project.date.none").toLowerCase()),
            actual: dayText(milestone.actual, t("ext.project.milestones.not_closed")),
          })}</span>
        </li>`;
      })}</ul>` : html`<p class="project-empty">${t("ext.project.stages.none")}</p>`}
    </section>
  </div>`;
}

function PhaseBlock({ stage, stageIndex, group, groupIndex, collapsed, onToggle, children }) {
  const open = !collapsed.has(stage.group_id + ":" + group.key);
  const bodyId = "project-phase-" + stageIndex + "-" + groupIndex;
  const scope = "stage" + stageIndex + "-phase" + groupIndex;
  return html`<div class="project-phase">
    <div class="project-phase-head">
      <button type="button" class="project-phase-toggle" aria-expanded=${open ? "true" : "false"} aria-controls=${bodyId}
        onClick=${() => onToggle(stage.group_id + ":" + group.key)} ...${helpFor("project.phase_toggle", { scope })}>
        <span aria-hidden="true">${open ? "▾" : "▸"}</span>
        <span>${t("ext.project.phase.label", { phase: group.name })}</span>
        <span class="project-meta">${t("ext.project.phase.count", { shown: group.tasks.length, total: group.all.length })}</span>
      </button>
      <${Help} topic="project.phase_toggle" scope=${scope} compact=${true} />
    </div>
    <div id=${bodyId} class="project-phase-body" hidden=${!open}>${children}</div>
  </div>`;
}

export function StagesPanel({ project, filter, labels, collapsed, selectedId, onFilter, onToggle, onOpen }) {
  const [hidden, setHidden] = useHiddenStages(project);
  const stages = visibleStages(project, hidden);
  const filtered = filter.text.trim() !== "" || filter.label !== "";
  return html`<div class="project-stages">
    <h4 id="project-stages-title">${t("ext.project.stages.title")}</h4><${Help} topic="project.stages_view" />
    <div class="project-filters">
      <div class="project-filter">
        <label for="project-filter-text">${t("ext.project.filter.search")}</label>
        <input id="project-filter-text" type="text" value=${filter.text} maxlength="100" autocomplete="off"
          onInput=${(event) => onFilter({ ...filter, text: event.target.value })} ...${helpFor("project.filter_text")} />
        <${Help} topic="project.filter_text" />
      </div>
      <div class="project-filter">
        <label for="project-filter-state">${t("ext.project.filter.state")}</label>
        <select id="project-filter-state" value=${filter.label} onChange=${(event) => onFilter({ ...filter, label: event.target.value })}
          ...${helpFor("project.filter_state")}>
          <option value="">${t("ext.project.filter.all")}</option>
          ${labels.map(({ label }) => html`<option key=${label.key} value=${label.key}>${label.text}</option>`)}
        </select>
        <${Help} topic="project.filter_state" />
      </div>
    </div>
    <${HiddenBar} project=${project} hidden=${hidden} onChange=${setHidden} />
    ${list(project.stages).length ? null : html`<p class="project-empty">${t("ext.project.stages.none")}</p>`}
    ${list(project.stages).length && !stages.length ? html`<p class="project-empty">${t("ext.project.stages.all_hidden")}</p>` : null}
    ${stages.map((stage, stageIndex) => {
      const visible = list(stage.tasks).filter((task) => matchesFilter(task, filter));
      const counted = (stage.progress?.total || 0) - (stage.progress?.cancelled || 0);
      return html`<section key=${stage.group_id} class="project-stage" aria-label=${t("ext.project.stage.aria_label", { code: stage.code, title: stage.title })}>
        <div class="project-row-head">
          <h4 class="project-stage-title" data-help-exempt="data-title">${stage.code} · ${stage.title}</h4>
          <${StatusChip} ...${chipOf(stage.label)} />
          <${HideStage} project=${project} hidden=${hidden} stage=${stage} scope=${"block" + stageIndex} onChange=${setHidden} />
        </div>
        <p class="project-meta">${t("ext.project.stage.progress", { done: stage.progress?.done || 0, total: counted })} · ${planText(stage)}
          ${stage.closed_at ? " · " + t("ext.project.stage.closed", { date: dayText(stage.closed_at) }) : ""}</p>
        ${filtered ? html`<p class="project-meta">${t("ext.project.stage.filtered", { shown: visible.length, total: list(stage.tasks).length })}</p>` : null}
        ${phaseGroups(stage, visible).map((group, groupIndex) => {
          const rows = group.tasks.length
            ? html`<ul class="project-items">${group.tasks.map((task) => html`<${TaskRow} key=${task.id} task=${task} project=${project}
                current=${selectedId === task.id} onOpen=${onOpen} />`)}</ul>`
            : html`<p class="project-empty">${filtered ? t("ext.project.filter.no_match") : t("ext.project.phase.empty")}</p>`;
          return group.name
            ? html`<${PhaseBlock} key=${group.key} stage=${stage} stageIndex=${stageIndex} group=${group} groupIndex=${groupIndex}
                collapsed=${collapsed} onToggle=${onToggle}>${rows}<//>`
            : html`<div key=${group.key} class="project-phase-body">${rows}</div>`;
        })}
      </section>`;
    })}
  </div>`;
}

// The List view (PRY-T05): one flat list of all the tasks with filters by owner, state and due date.
export function ListPanel({ project, filter, labels, onFilter, onOpen, selectedId }) {
  const rows = listRows(project);
  const owners = listOwners(rows);
  const shown = rows.filter((row) => matchesList(row, filter));
  const set = (changes) => onFilter({ ...filter, ...changes });
  return html`<div class="project-stages">
    <h4 id="project-list-title">${t("ext.project.list.title")}</h4><${Help} topic="project.list_view" />
    <div class="project-filters">
      <div class="project-filter">
        <label for="project-list-owner">${t("ext.project.filter.owner")}</label>
        <select id="project-list-owner" value=${filter.owner} onChange=${(event) => set({ owner: event.target.value })} ...${helpFor("project.list_owner")}>
          <option value="">${t("ext.project.filter.all")}</option>
          ${owners.map((owner) => html`<option key=${owner.value} value=${owner.value}>${owner.text}</option>`)}
        </select>
        <${Help} topic="project.list_owner" />
      </div>
      <div class="project-filter">
        <label for="project-list-state">${t("ext.project.filter.state")}</label>
        <select id="project-list-state" value=${filter.label} onChange=${(event) => set({ label: event.target.value })} ...${helpFor("project.list_state")}>
          <option value="">${t("ext.project.filter.all")}</option>
          ${labels.map(({ label }) => html`<option key=${label.key} value=${label.key}>${label.text}</option>`)}
        </select>
        <${Help} topic="project.list_state" />
      </div>
      <div class="project-filter">
        <label for="project-list-since">${t("ext.project.filter.due_from")}</label>
        <input id="project-list-since" type="date" value=${filter.from} onInput=${(event) => set({ from: event.target.value })} ...${helpFor("project.list_dates")} />
        <label for="project-list-until">${t("ext.project.filter.due_until")}</label>
        <input id="project-list-until" type="date" value=${filter.to} onInput=${(event) => set({ to: event.target.value })} ...${helpFor("project.list_dates")} />
        <${Help} topic="project.list_dates" />
      </div>
    </div>
    <p class="project-meta" role="status">${t("ext.project.list.count", { shown: shown.length, total: rows.length })}</p>
    ${shown.length ? html`<ul class="project-items">${shown.map((task) => html`<${TaskRow} key=${task.id} task=${task} project=${project}
        current=${selectedId === task.id} onOpen=${onOpen}>
        <span class="project-meta">${t("ext.project.list.stage", { code: task.stage_code })} · ${planText(task)}</span>
      <//>`)}</ul>` : html`<p class="project-empty">${rows.length ? t("ext.project.list.no_match") : t("ext.project.tasks.none")}</p>`}
  </div>`;
}

function Bullets({ items }) {
  return html`<ul class="project-bullets">${items.map((item, index) => html`<li key=${index}>${item}</li>`)}</ul>`;
}

function Deliverables({ result }) {
  const found = deliverables(result);
  return html`<h5 class="project-subtitle">${t("ext.project.deliverables.title")}</h5><${Help} topic="project.deliverables" />
    ${found ? html`<div class="project-deliverables">
      <p class="project-meta">${t("ext.project.deliverables.attempt", { attempt: found.attempt || "?" })}${found.agent ? " · " + found.agent : ""}</p>
      <p class="project-note">${found.summary || t("ext.project.deliverables.no_summary")}</p>
      ${found.files.length ? html`<div><strong>${t("ext.project.deliverables.files")}</strong><${Bullets} items=${found.files} /></div>` : null}
      ${found.decisions.length ? html`<div><strong>${t("ext.project.deliverables.decisions")}</strong><${Bullets} items=${found.decisions} /></div>` : null}
      ${found.testsNotRun.length ? html`<div><strong>${t("ext.project.deliverables.not_tested")}</strong><${Bullets} items=${found.testsNotRun} /></div>` : null}
      ${found.contractChanges ? html`<div><strong>${t("ext.project.deliverables.contract_changes")}</strong><p class="project-note">${found.contractChanges}</p></div>` : null}
      ${found.requests.length ? html`<div><strong>${t("ext.project.deliverables.requests")}</strong><${Bullets} items=${found.requests.map((r) => r.to + ": " + r.what)} /></div>` : null}
    </div>` : html`<p class="project-empty">${t("ext.project.deliverables.none")}</p>`}`;
}

function ReviewForm({ review, onReview, onFeedback }) {
  const { open, text, busy } = review;
  return html`<div class="project-review">
      <button type="button" class="btn" disabled=${busy} onClick=${() => onReview("accept")} ...${helpFor("project.approve")}>${t("ext.project.approve")}</button>
      <${Help} topic="project.approve" compact=${true} />
      <button type="button" class="btn secondary" disabled=${busy} aria-expanded=${open ? "true" : "false"}
        onClick=${() => onFeedback({ open: !open })} ...${helpFor("project.request_changes")}>${t("ext.project.request_changes")}</button>
      <${Help} topic="project.request_changes" compact=${true} />
    </div>
    ${open ? html`<div class="project-feedback">
      <label for="project-feedback-text">${t("ext.project.feedback.label")}</label>
      <textarea id="project-feedback-text" value=${text} maxlength="500" rows="4" onInput=${(event) => onFeedback({ text: event.target.value })}
        ...${helpFor("project.feedback_input")}></textarea>
      <${Help} topic="project.feedback_input" />
      <div class="project-review">
        <button type="button" class="btn" disabled=${busy || !text.trim()} onClick=${() => onReview("request_changes")}
          ...${helpFor("project.send_feedback")}>${t("ext.project.feedback.send")}</button>
        <${Help} topic="project.send_feedback" compact=${true} />
        <button type="button" class="btn secondary" disabled=${busy} onClick=${() => onFeedback({ open: false, text: "" })}
          ...${helpFor("project.cancel_feedback")}>${t("ext.project.feedback.cancel")}</button>
        <${Help} topic="project.cancel_feedback" compact=${true} />
      </div>
    </div>` : null}`;
}

export function TaskDetail({ project, task: fetched, loading, error, notice, review, headingRef, onReview, onFeedback }) {
  const task = detailOf(project, fetched);       // the task's own answer over the project's line of it (phase, date, who)
  const index = taskIndex(project);
  const found = task ? index.get(task.id) : null;
  const depends = list(task?.depends_on);
  const dependents = list(task?.dependents);
  return html`<section class="project-detail" aria-labelledby="project-detail-title" aria-busy=${loading ? "true" : "false"}>
    <h4 id="project-detail-title" tabindex="-1" ref=${headingRef}>${t("ext.project.task_detail.title")}</h4><${Help} topic="project.task_detail" />
    ${error ? html`<p class="project-error" role="alert">${error}</p>` : null}
    <p class="project-status" role="status">${notice}</p>
    ${loading && !task ? html`<p class="project-empty">${t("ext.project.task_detail.loading")}</p>` : null}
    ${task ? html`
      <div class="project-row-head">
        <h5 class="project-detail-name">${task.title}</h5>
        <${StatusChip} ...${chipOf(task.label)} />
      </div>
      <dl class="project-facts">
        <dt>${t("ext.project.task_detail.id")}</dt><dd>${shortId(task.id, project.id)}</dd>
        ${found ? html`<dt>${t("ext.project.task_detail.stage")}</dt><dd>${found.stage.code} · ${found.stage.title}</dd>` : null}
        <dt>${t("ext.project.task_detail.phase")}</dt><dd>${task.phase || t("ext.project.phase.none")}</dd>
        <dt>${t("ext.project.filter.owner")}</dt><dd>${whoText(task)}</dd>
        <dt>${t("ext.project.task_detail.due_date")}</dt><dd>${dayText(task.due_at)}</dd>
        <dt>${t("ext.project.deliverables.attempt_label")}</dt><dd>${task.attempt}</dd>
      </dl>
      ${task.objective ? html`<p class="project-note">${task.objective}</p>` : null}

      <h5 class="project-subtitle">${t("ext.project.dependencies.title")}</h5>
      ${depends.length ? html`<ul class="project-items">${depends.map((dep) => {
        const other = index.get(dep.task_id);
        return html`<li key=${dep.task_id} class="project-line">
          <span class="project-row-head"><span class="project-title">${taskName(dep.task_id, project)}</span>
            ${other ? html`<${StatusChip} ...${chipOf(other.task.label)} />` : null}</span>
          <span class="project-meta">${t("ext.project.dependencies.state", {
            requires: requiresText(dep.requires), state: dep.satisfied ? t("ext.project.dependencies.met") : t("ext.project.dependencies.not_met"),
          })}</span>
        </li>`;
      })}</ul>` : html`<p class="project-empty">${t("ext.project.dependencies.none")}</p>`}
      ${dependents.length ? html`<h5 class="project-subtitle">${t("ext.project.dependencies.unblocks")}</h5>
        <ul class="project-bullets">${dependents.map((id) => html`<li key=${id}>${taskName(id, project)}</li>`)}</ul>` : null}

      <${Deliverables} result=${task.result} />

      ${canReview(task) ? html`<${ReviewForm} review=${review} onReview=${onReview} onFeedback=${onFeedback} />` : null}
    ` : null}
  </section>`;
}
