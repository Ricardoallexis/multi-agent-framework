from __future__ import annotations

from typing import Any

from .config import Settings
from .contracts import HumanStepSubmission, RunStatus, SocialPostRequest, WaitingReason
from .db.store import Store
from .errors import BudgetExceeded, HumanSubmissionError, InvalidStateTransition
from .tools.approvals import ToolApprovalMismatch
from .run_request import RunRequest
from .routing_preferences import validate_run_preferences


class PersonRequired(InvalidStateTransition):
    """The decision needs an authenticated person and the caller is not one (``details.reason`` ``person_required``)."""


class RunService:
    def __init__(self, settings: Settings, store: Store, engine=None):
        self.settings = settings
        self.store = store
        self.engine = engine

    def bind_engine(self, engine) -> None:
        self.engine = engine

    def create_social_post(self, request: SocialPostRequest) -> dict[str, Any]:
        return self.create_run(request)

    def create_run(self, request: RunRequest | SocialPostRequest) -> dict[str, Any]:
        """Validate and persist a named workflow, preserving social request compatibility."""
        existing = self.store.find_idempotent_run(request)
        if existing is not None:
            return existing
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        # Loading/validation errors propagate with their original type and issues.
        # No project or run is persisted until the entire definition is valid.
        definition = self.engine.preflight(self.store.workflow_id_for(request))
        if getattr(request, "routing_preferences", None):
            validate_run_preferences(self.engine, request, definition)
        return self.store.create_run(request, self.settings.max_llm_calls, self.settings.max_run_seconds)

    def get(self, run_id: str) -> dict[str, Any]:
        if self.engine is not None:
            self.engine.check_approval_deadlines(run_id=run_id)
        return self.store.get_run(run_id)

    def list(self, *, limit: int = 20, status: str = "", project: str = "") -> list[dict[str, Any]]:
        if self.engine is not None:
            self.engine.check_approval_deadlines()
        return self.store.list_runs(limit=limit, status=status, project=project)

    @staticmethod
    def _awaits_escalation(run: dict[str, Any]) -> bool:
        """True while a supervised economic profile waits for a human to approve a step's escalation."""
        return (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.ESCALATION.value)

    @staticmethod
    def _awaits_content_review(run: dict[str, Any]) -> bool:
        """True while the content guard (BDI-T04) holds a step for a human to look at its untrusted content."""
        return (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.CONTENT_REVIEW.value)

    @staticmethod
    def _awaits_budget_cut(run: dict[str, Any]) -> bool:
        """True while the budget guard (USO-T04) holds a call that would pass a budget in cut mode, or has no known price."""
        return (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") in {WaitingReason.BUDGET_CUT.value, WaitingReason.EGRESS_REVIEW.value})

    @staticmethod
    def _awaits_user_message(run: dict[str, Any]) -> bool:
        """True while a message of the person (AVA-T03, ``interrupt`` or ``adjust``) holds the run at a step boundary."""
        return (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.USER_MESSAGE.value)

    @staticmethod
    def _awaits_tool_approval(run: dict[str, Any]) -> bool:
        return (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.TOOL_APPROVAL.value)

    def approve(self, run_id: str, *, by: str | None = None, channel: str | None = None,
                recorded_only: bool = False, permission_request_id: str | None = None,
                person: bool = False) -> dict[str, Any]:
        """``by`` and ``channel`` say who and through which door (``api`` or ``gui``) the caller declared: they are
        recorded when the approval settles a content review and are not checked (see ``document_review``).

        ``recorded_only`` is for a caller nobody authenticated (the local HTTP API, which has no token): it cannot approve
        a spend, so a run held by the budget guard answers ``PersonRequired`` and stays as it was (USO-T04). Every other
        wait behaves as before. The token route of the GUI and the CLI do not pass it.

        ``person`` says an authenticated person is behind this call. A tool approval (``tool_approval``) is approved only
        with ``person=True``; without it the answer is ``PersonRequired``, as with ``recorded_only`` (CAP-T15, B1). Only
        the token route of the GUI (which the command line goes through) passes it."""
        run = self.store.get_run(run_id)
        if permission_request_id is not None and not self._awaits_tool_approval(run):
            # An id names one tool request; it never approves a wait of another kind (a stale click, CAP-T14 M1).
            raise ToolApprovalMismatch("The run is not waiting on the tool request that id names",
                                       code="permission_request_mismatch")
        if (recorded_only and self._awaits_budget_cut(run)) or (
                self._awaits_tool_approval(run) and (recorded_only or not person)):
            raise PersonRequired("This held call requires a person: use the GUI (it carries the token) or the "
                                 "command line; this route is not authenticated. Rejecting works here.")
        if self._awaits_escalation(run):
            # Approving an escalation resumes the same step with the stronger model; no artifact is under review.
            self.store.finish_escalation(run, approve=True)
            return self.store.get_run(run_id)
        if self._awaits_tool_approval(run):
            # The person must name the request they saw (ToolApprovalMismatch otherwise). Wiring this id into the
            # API and the GUI is a future task: no real engine step calls tools yet (S5.D1).
            self.store.finish_tool_approval(run, approve=True, by=by, channel=channel,
                                            permission_request_id=permission_request_id)
            return self.store.get_run(run_id)
        if self._awaits_content_review(run):
            # Approving a content review lets the held step go on with the prompt as it was built (the quarantined
            # fragments stay replaced); no artifact is under review. The caller is not authenticated: whoever can
            # reach this method can settle the review (known limit, BDI-T07).
            self.store.finish_content_review(run, approve=True, by=by, channel=channel)
            return self.store.get_run(run_id)
        if self._awaits_budget_cut(run):
            # Approving a budget cut lets exactly the held call go ahead (the step resumes and the guard spends this approval on
            # it). An unauthenticated caller (``recorded_only``) never gets here; nothing but this path approves a cut.
            self.store.finish_budget_cut(run, approve=True, by=by, channel=channel)
            return self.store.get_run(run_id)
        if self._awaits_user_message(run):
            # The person's own approval lets the run go on after their message paused it. The inbox never calls this.
            self.store.finish_user_message(run, approve=True)
            return self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.REVIEW.value, WaitingReason.APPROVAL_PENDING.value, WaitingReason.BUDGET_EXHAUSTED.value, ""
        }:
            raise InvalidStateTransition("A run can only be approved while waiting for human review")
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        definition, artifact = self.engine.review_point(run)
        budget = bool(run["budget_exhausted"]) or run["waiting_reason"] == WaitingReason.BUDGET_EXHAUSTED.value
        next_step = run["current_step"] + 1
        completed = budget or next_step == len(definition.steps)
        self.store.finish_review(
            run, artifact, approve=True, completed=completed,
            current_step=len(definition.steps) if completed else next_step,
        )
        return self.store.get_run(run_id)

    def changes(self, run_id: str, feedback: str, *, regenerate: bool = False) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.REVIEW.value, WaitingReason.APPROVAL_PENDING.value, WaitingReason.BUDGET_EXHAUSTED.value, ""
        }:
            raise InvalidStateTransition("Revisions can only be requested in WAITING_HUMAN/review")
        if run.get("budget_exhausted") or run.get("waiting_reason") == WaitingReason.BUDGET_EXHAUSTED.value:
            raise BudgetExceeded("The budget is exhausted; only the existing artifact can be approved or rejected")
        if not feedback.strip() and not regenerate:
            raise ValueError("Feedback is required for revise")
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        _, artifact = self.engine.review_point(run)
        self.store.finish_review(
            run, artifact, approve=False, current_step=run["current_step"],
            feedback="" if regenerate else feedback.strip(), regenerate=regenerate,
        )
        return self.store.get_run(run_id)

    def reject(self, run_id: str, feedback: str = "", *, by: str | None = None,
               channel: str | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value:
            raise InvalidStateTransition("A run can only be rejected in WAITING_HUMAN")
        if self._awaits_escalation(run):
            # Rejecting an escalation fails the step (and the run) with the reason; nothing was produced to reject.
            self.store.finish_escalation(run, approve=False, feedback=feedback)
            return self.store.get_run(run_id)
        if self._awaits_tool_approval(run):
            self.store.finish_tool_approval(run, approve=False, feedback=feedback, by=by, channel=channel)
            return self.store.get_run(run_id)
        if self._awaits_budget_cut(run):
            # Rejecting a budget cut fails the run with the reason: the held call is never made.
            self.store.finish_budget_cut(run, approve=False, feedback=feedback, by=by, channel=channel)
            return self.store.get_run(run_id)
        if self._awaits_user_message(run):
            self.store.finish_user_message(run, approve=False, feedback=feedback)
            return self.store.get_run(run_id)
        if self._awaits_content_review(run):
            # Rejecting a content review rejects the run: the held step never reads the content.
            self.store.finish_content_review(run, approve=False, feedback=feedback, by=by, channel=channel)
            return self.store.get_run(run_id)
        self.store.update_run(
            run_id,
            status=RunStatus.REJECTED.value,
            revision_feedback=feedback,
            waiting_reason="",
            waiting_step="",
            waiting_attempt=None,
        )
        self.store.db.log_event(run_id, "human_rejected", {"feedback": feedback})
        return self.store.get_run(run_id)

    def cancel(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] in {RunStatus.COMPLETED.value, RunStatus.REJECTED.value, RunStatus.CANCELLED.value}:
            return run
        if force or run["status"] in {
            RunStatus.QUEUED.value, RunStatus.WAITING_HUMAN.value, RunStatus.PAUSED.value,
            RunStatus.INTERRUPTED.value,
        }:
            self.store.update_run(
                run_id,
                status=RunStatus.CANCELLED.value,
                cancel_requested=1,
                waiting_reason="",
                waiting_step="",
                waiting_attempt=None,
            )
            self.store.db.log_event(run_id, "workflow_cancelled", {"force": force})
        else:
            self.store.update_run(run_id, cancel_requested=1)
            self.store.db.log_event(run_id, "cancel_requested", {})
        return self.store.get_run(run_id)

    def human_next(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        pending = self.store.pending_human_step(run_id)
        if not pending:
            raise HumanSubmissionError("The run has no pending HumanStepRequest")
        return {
            "run_id": run_id,
            "status": run["status"],
            "waiting_reason": run.get("waiting_reason"),
            "step_id": pending["step_id"],
            "attempt": pending["attempt"],
            "expected_contract": pending["expected_contract"],
            "prompt_id": pending["prompt_id"],
            "prompt_version": pending["prompt_version"],
            "prompt_sha256": pending["prompt_sha256"],
            "prompt": pending["prompt_base"],
        }

    def human_submit(self, run_id: str, submission: HumanStepSubmission) -> dict[str, Any]:
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        return self.engine.submit_human_step(run_id, submission)
