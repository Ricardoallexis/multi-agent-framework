// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CHT-T06: pure text of the plan thread (no DOM, no network). The server sends the proposal with its tasks, their inputs,
// the `revision` a decision must send, and, when a bundle used by the plan has roles without an approved team, the reason
// why approving is blocked. Everything here returns plain text or plain data; the view draws it as text.
import { t } from "./i18n.js";

export const TASK_STATE_TEXT = {
  get blocked() { return t("plans.taskState.blocked"); },
  get ready() { return t("plans.taskState.ready"); },
  get claimed() { return t("plans.taskState.claimed"); },
  get running() { return t("plans.taskState.running"); },
  get paused() { return t("plans.taskState.paused"); },
  get validating() { return t("plans.taskState.validating"); },
  get needs_changes() { return t("plans.taskState.needsChanges"); },
  get submitted() { return t("plans.taskState.submitted"); },
  get accepted() { return t("plans.taskState.accepted"); },
  get failed() { return t("plans.taskState.failed"); },
  get cancelled() { return t("plans.taskState.cancelled"); },
};
export const RUN_STATUS_TEXT = {
  get queued() { return t("plans.runStatus.queued"); },
  get running() { return t("plans.runStatus.running"); },
  get waiting_human() { return t("plans.runStatus.waitingHuman"); },
  get completed() { return t("plans.runStatus.completed"); },
  get failed() { return t("plans.runStatus.failed"); },
  get cancelled() { return t("plans.runStatus.cancelled"); },
  get rejected() { return t("plans.runStatus.rejected"); },
};

// The inputs of one task as a list of [name, value] with only text values; an empty or missing object gives [].
export const inputPairs = (inputs) => inputs && typeof inputs === "object" && !Array.isArray(inputs)
  ? Object.entries(inputs).map(([name, value]) => [name, value === null || value === undefined ? "" : String(value)]) : [];

// What each task of a proposal shows: its inputs as a readable object (or null when it has none).
export const inputsValue = (inputs) => inputPairs(inputs).length ? Object.fromEntries(inputPairs(inputs)) : null;

export const PLAN_STATUS_TEXT = {
  get proposed() { return t("plans.status.proposed"); },
  get approved() { return t("plans.status.approved"); },
  get rejected() { return t("plans.status.rejected"); },
};
export const planStatusText = (status) => Object.prototype.hasOwnProperty.call(PLAN_STATUS_TEXT, status)
  ? PLAN_STATUS_TEXT[status] : (status ? String(status) : "");

export const canDecide = (entry) => Boolean(entry && entry.decision && (entry.decision.actions || []).length);
export const decisionActions = (entry) => canDecide(entry) ? entry.decision.actions : [];

// The revision the page shows is the one the decision sends as expected_revision; never a later one.
export const decisionRevision = (entry) => entry && entry.decision && Number.isInteger(entry.decision.revision)
  ? entry.decision.revision : (entry && entry.proposal && Number.isInteger(entry.proposal.revision) ? entry.proposal.revision : null);

export const rolesText = (roles) => Array.isArray(roles) && roles.length ? roles.join(", ") : t("plans.teamMissing.noRoles");

// «Falta el equipo aprobado»: the message and where it leads (a proposed version of the team, or Equipos).
export function missingTeam(item) {
  const bundle = item && item.bundle ? String(item.bundle) : "";
  const versionId = item && item.team_version_id ? String(item.team_version_id) : "";
  const text = bundle
    ? t("plans.teamMissing.withBundle", { bundle, roles: rolesText(item && item.roles) })
    : t("plans.teamMissing.withoutBundle", { roles: rolesText(item && item.roles) });
  if (versionId) return { text, href: "#new/team/" + encodeURIComponent(versionId), label: t("plans.teamMissing.openProposedTeam"), kind: "version" };
  return { text, href: "#teams" + (bundle ? "/" + encodeURIComponent(bundle) : ""), label: t("plans.teamMissing.openTeams"), kind: "teams" };
}

// Why «Aprobar» is disabled, or "" when it is not.
export function approveBlock(entry) {
  const blocked = entry && entry.decision && entry.decision.approve_blocked;
  if (!blocked) return "";
  const bundles = (blocked.blockers || []).map((item) => item.bundle
    ? t("plans.team.bundle", { bundle: item.bundle }) : t("plans.teamMissing.noBundle"));
  return t("plans.approveBlock.message", {
    bundles: bundles.join(", ") || t("plans.approveBlock.someBundle"),
  });
}

// One message per task of an approved plan: its state, its run and where the run opens.
export function taskRunLine(entry) {
  const taskState = entry && entry.state;
  const state = Object.prototype.hasOwnProperty.call(TASK_STATE_TEXT, taskState)
    ? TASK_STATE_TEXT[taskState] : (taskState ? String(taskState) : t("plans.taskRun.noState"));
  const runId = entry && entry.run_id ? String(entry.run_id) : "";
  const runStatus = entry && entry.run_status;
  const status = runStatus
    ? (Object.prototype.hasOwnProperty.call(RUN_STATUS_TEXT, runStatus) ? RUN_STATUS_TEXT[runStatus] : String(runStatus))
    : "";
  const title = (entry && (entry.title || entry.task_id)) || "";
  return {
    text: t("plans.taskRun.line", {
      taskId: entry && entry.task_id ? entry.task_id : "",
      titleSuffix: title && title !== entry.task_id ? " · " + title : "",
      state,
    }),
    runText: runId ? t("plans.taskRun.run", { runId, statusSuffix: status ? " (" + status + ")" : "" })
      : t("plans.taskRun.noRun"),
    href: runId ? "#new/run/" + encodeURIComponent(runId) : "",
  };
}

// The text of a failed decision: a stale revision says so and asks to read again; anything else shows the service text.
export function decisionFailure(error) {
  const status = error && error.status;
  const code = error && error.code;
  const details = (error && error.details) || {};
  if (status === 409 && details.reason === "stale_revision") {
    return {
      text: t("plans.decisionFailure.staleRevision", {
        revision: details.revision ?? t("plans.decisionFailure.newRevision"),
      }),
      reload: true,
    };
  }
  if (status === 409 && (details.reason === "team_not_approved" || code === "team_not_approved")) {
    const blocker = Array.isArray(details.blockers)
      ? details.blockers.find((item) => item && typeof item.bundle === "string" && item.bundle.trim())
      : null;
    const bundle = typeof details.bundle === "string" && details.bundle.trim()
      ? details.bundle : (blocker ? blocker.bundle : "");
    return {
      text: bundle
        ? t("plans.decisionFailure.teamNotApproved", { bundle })
        : t("plans.decisionFailure.teamNotApprovedUnknown"),
      reload: true,
    };
  }
  if (status === 409) return {
    text: (error.message || t("plans.decisionFailure.conflict")) + (code ? " (" + code + ")" : "") +
      t("plans.decisionFailure.reloaded"),
    reload: true,
  };
  return {
    text: (error && error.message ? error.message : t("plans.error.unknown")) + (code ? " (" + code + ")" : ""),
    reload: status === 404,
  };
}
