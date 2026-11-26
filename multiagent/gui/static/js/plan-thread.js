// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CHT-T06: the chat view of a plan proposal ("#new/plan/<proposal id>"): the plan with the inputs of each task, a message when
// a bundle still lacks an approved team (it disables Approve and says why), the Approve and Reject stop, and, once approved,
// one message per task with its run. It reads when it opens, after a decision and when the person asks. Approving and
// rejecting use the plan-proposal routes with the revision the page shows as expected_revision.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "./api.js";
import { Help, helpFor } from "./help.js";
import { Confirm } from "./human-actions.js";
import { errorText } from "./text.js";
import { readable } from "./readable.js";
import { ReadableValue } from "./thread-cards.js";
import { approveBlock, decisionActions, decisionFailure, decisionRevision, inputsValue, missingTeam, planStatusText, taskRunLine } from "./plan-card-model.js";
import { SegmentBar } from "./segment-bar.js";   // AVZ-T04
import { planBar } from "./thread-model.js";
import { t } from "./i18n.js";

const PLAN_ROUTES = "/gui/api/plan-proposals/";

// The inputs of one task: long texts folded, an object as a readable summary (readable.js). Shared with «Planes».
export function TaskInputs({ inputs }) {
  const value = inputsValue(inputs);
  if (!value) return html`<p class="muted small">${t("plans.thread.noInputs")}</p>`;
  return html`<div class="plan-task-inputs"><${ReadableValue} node=${readable(value)} /></div>`;
}

function PlanBody({ entry }) {
  const proposal = entry.proposal || {};
  const tasks = entry.tasks || [];
  return html`<section class="plan-card" aria-label=${t("plans.thread.taskList")}>
    <p><strong>${proposal.goal || ""}</strong> · ${t("plans.thread.group")} ${proposal.group_id || ""} · ${t("plans.thread.revision")} ${proposal.revision} · ${planStatusText(proposal.status)}</p>
    ${proposal.rationale && html`<p class="muted">${proposal.rationale}</p>`}
    <h4>${t("plans.thread.tasksAndInputs")}</h4><${Help} topic="plan_card.inputs" compact=${true} />
    ${tasks.length ? html`<ol class="plan-task-list">${tasks.map((task) => html`<li key=${task.id}>
      <strong>${task.title}</strong> <span class="muted small">${task.id} · ${task.workflow || ""}${task.bundle ? " (" + task.bundle + ")" : ""}</span>
      ${task.depends_on && task.depends_on.length > 0 && html`<p class="muted small">${t("plans.thread.dependsOn")} ${task.depends_on.join(", ")}</p>`}
      ${task.objective && html`<p>${task.objective}</p>`}
      <${TaskInputs} inputs=${task.inputs} />
    </li>`)}</ol>` : html`<p class="muted">${t("plans.thread.proposalHasNoTasks")}</p>`}
  </section>`;
}

function PlanDecision({ entry, token, onDone }) {
  const actions = decisionActions(entry);
  const proposalId = entry.proposal && entry.proposal.id;
  const revision = decisionRevision(entry);
  const blocked = approveBlock(entry);
  const [pending, setPending] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  if (!actions.length) return null;
  const send = async () => {
    if (busy) return;
    setBusy(true); setMessage(null);
    const verb = pending;
    try {
      const body = verb === "approve" ? { expected_revision: revision, note: note.trim() } : { note: note.trim() };
      await apiPost(PLAN_ROUTES + encodeURIComponent(proposalId) + "/" + verb, token, body);
      setPending(""); setNote("");
      setMessage({ kind: "ok", text: verb === "approve" ? t("plans.thread.planApproved") : t("plans.thread.planRejected") });
    } catch (failure) {
      setPending("");
      const shown = decisionFailure(failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
      setMessage({ kind: "error", text: shown.text });
    } finally { setBusy(false); onDone(); }
  };
  return html`<div class="thread-decision">
    ${!pending && html`<div class="thread-actions">
      ${actions.includes("approve") && html`<button type="button" class="btn" disabled=${busy || Boolean(blocked)}
        ...${helpFor("plan_card.approve")} onClick=${() => setPending("approve")}>${t("plans.thread.approve")}</button>`}
      ${actions.includes("reject") && html`<button type="button" class="btn secondary" disabled=${busy}
        ...${helpFor("plan_card.reject")} onClick=${() => setPending("reject")}>${t("plans.thread.reject")}</button>`}
      <${Help} topic="plan_card.approve" compact=${true} /><${Help} topic="plan_card.reject" compact=${true} />
    </div>`}
    ${!pending && blocked && html`<p class="message info" role="status">${blocked}</p>`}
    ${pending && html`<div class="thread-confirm" role="group" aria-label=${t("plans.thread.confirmDecision")}>
      <p><strong>${pending === "approve"
        ? t("plans.thread.confirmApprovePrompt", { revision })
        : t("plans.thread.confirmRejectPrompt", { revision })}</strong></p>
      <label>${t("plans.thread.optionalNote")}
        <textarea rows="3" maxlength="4000" value=${note} disabled=${busy} ...${helpFor("plan_card.note")}
          onInput=${(event) => setNote(event.target.value)}></textarea></label>
      <${Help} topic="plan_card.note" compact=${true} />
      <div class="thread-actions"><button type="button" class=${pending === "reject" ? "btn danger" : "btn"} disabled=${busy}
        ...${helpFor("plan_card.confirm")} onClick=${send}>${pending === "approve"
          ? t("plans.thread.confirmApprove") : t("plans.thread.confirmReject")}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("plan_card.confirm")}
          onClick=${() => setPending("")}>${t("plans.thread.back")}</button><${Help} topic="plan_card.confirm" compact=${true} /></div>
    </div>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </div>`;
}

function PlanCard({ entry, token, onDone }) {
  const proposal = entry.proposal || {};
  const open = decisionActions(entry).length > 0;
  return html`<li class=${"thread-card" + (open ? " thread-waiting" : "")}>
    <div class="thread-card-head"><strong>${open ? t("plans.thread.planToDecide") : t("plans.thread.plan")}</strong>
      <a href=${"#plans/" + encodeURIComponent(proposal.id || "")} ...${helpFor("plan_card.open")}>${t("plans.thread.viewInPlans")}</a>
      <${Help} topic="plan_card.open" compact=${true} /></div>
    <${PlanBody} entry=${entry} />
    <${PlanDecision} entry=${entry} token=${token} onDone=${onDone} />
  </li>`;
}

// ARR3-T03: what the plan did by itself. After a failure it offers the manual advance, the route «Planes» uses.
function PlanEventCard({ entry }) {
  const failed = entry.event === "plan.advance_failed";
  return html`<li class=${"thread-card" + (failed ? " thread-waiting" : "")}>
    <p role=${failed ? "alert" : "status"}>${entry.text}</p>
    <${Help} topic="plan.auto_advance" compact=${true} />
  </li>`;
}

function PlanAdvance({ token, proposalId, onDone }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const send = async () => {
    if (busy) return;
    setBusy(true); setMessage(null);
    try {
      const result = await apiPost(PLAN_ROUTES + encodeURIComponent(proposalId) + "/advance", token, {});
      setMessage({ kind: "ok", text: result && result.nothing_to_advance
        ? t("plans.thread.nothingToAdvance") : t("plans.thread.planAdvanced") });
    } catch (failure) {
      setMessage({ kind: "error", text: errorText(failure) });
    } finally { setConfirm(false); setBusy(false); onDone(); }
  };
  return html`<li class="thread-card thread-waiting"><div class="thread-actions">
    <button type="button" class="btn" disabled=${busy} ...${helpFor("plan.auto_advance")} onClick=${() => setConfirm(true)}>${t("plans.thread.advancePlan")}</button>
    <${Help} topic="plan.auto_advance" compact=${true} /></div>
    ${confirm && html`<${Confirm} title=${t("plans.thread.advanceConfirmTitle")} confirmLabel=${t("plans.thread.advanceConfirm")}
      keepLabel=${t("plans.thread.cancel")} danger=${false} busy=${busy} onKeep=${() => setConfirm(false)} onConfirm=${send}>
      <p class="muted">${t("plans.thread.advanceExplanation")}</p>
    <//>`}
    ${message && html`<p class=${"message " + message.kind} role=${message.kind === "error" ? "alert" : "status"}>${message.text}</p>`}
  </li>`;
}

function MissingTeamCard({ entry }) {
  const shown = missingTeam(entry);
  return html`<li class="thread-card thread-waiting">
    <div class="thread-card-head"><strong>${t("plans.thread.teamMissing")}</strong></div>
    <p role="status">${shown.text}</p>
    <p><a href=${shown.href} ...${helpFor("plan_card.team")}>${shown.label}</a><${Help} topic="plan_card.team" compact=${true} /></p>
  </li>`;
}

function TaskRunCard({ entry }) {
  const line = taskRunLine(entry);
  return html`<li class="thread-card">
    <p>${line.text}</p>
    <p>${line.href ? html`<a href=${line.href} ...${helpFor("plan_card.run")}>${line.runText}</a><${Help} topic="plan_card.run" compact=${true} />`
      : html`<span class="muted">${line.runText}</span>`}</p>
  </li>`;
}

export function PlanThread({ token, proposalId }) {
  const [state, setState] = useState({ loaded: false, entries: [], error: null, canAdvance: false });
  const ticket = useRef(0);
  const load = async () => {
    const mine = ++ticket.current;
    try {
      const page = await apiGet("/gui/api/threads/plans/" + encodeURIComponent(proposalId), token);
      if (mine === ticket.current) setState({ loaded: true, entries: page.entries || [], error: null, canAdvance: Boolean(page.can_advance) });
    } catch (error) {
      if (mine === ticket.current) setState((previous) => ({ ...previous, loaded: true, error }));
    }
  };
  useEffect(() => {
    setState({ loaded: false, entries: [], error: null, canAdvance: false });
    load();
    // ARR3-T03: the plan moves by itself when a run ends, so the thread reads again every 4 s while it is open.
    const timer = setInterval(load, 4000);
    return () => { ticket.current += 1; clearInterval(timer); };
  }, [proposalId, token]);
  const bar = planBar(state.entries);   // AVZ-T04: from the thread the 4 s read already brings
  return html`<section class="ns thread-view" aria-label=${t("plans.thread.threadLabel")}>
    <div class="thread-head"><h3>${t("plans.thread.proposal")}</h3><span class="muted">${proposalId}</span>
      <button type="button" class="btn secondary" ...${helpFor("plan_card.refresh")} onClick=${load}>${t("plans.thread.refresh")}</button>
      <${Help} topic="plan_card.refresh" compact=${true} /></div>
    ${bar && html`<div class="thread-progress">
      <${SegmentBar} segments=${bar.segments} summary=${bar.summary} className="thread-segbar" />
      <p class="thread-progress-line muted small">${bar.line}</p></div>`}
    ${state.error && html`<p class="message error" role="alert">${t("plans.thread.readProposalError")}: ${errorText(state.error)}</p>`}
    ${!state.loaded && html`<p class="muted" aria-busy="true">${t("plans.thread.loadingProposal")}</p>`}
    <ol class="thread-list" aria-label=${t("plans.thread.proposal")}>
      ${state.entries.map((entry) => entry.kind === "plan" ? html`<${PlanCard} key=${entry.seq} entry=${entry} token=${token} onDone=${load} />`
        : entry.kind === "team_missing" ? html`<${MissingTeamCard} key=${entry.seq} entry=${entry} />`
        : entry.kind === "task_run" ? html`<${TaskRunCard} key=${entry.seq} entry=${entry} />`
        : entry.kind === "plan_event" ? html`<${PlanEventCard} key=${entry.seq} entry=${entry} />` : null)}
      ${state.canAdvance && html`<${PlanAdvance} token=${token} proposalId=${proposalId} onDone=${load} />`}
    </ol>
  </section>`;
}
