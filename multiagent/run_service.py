from __future__ import annotations

from typing import Any

from .config import Settings
from .contracts import HumanStepSubmission, RunStatus, SocialPostRequest, WaitingReason
from .db.store import Store
from .errors import BudgetExceeded, HumanSubmissionError, InvalidStateTransition


class RunService:
    def __init__(self, settings: Settings, store: Store, engine=None):
        self.settings = settings
        self.store = store
        self.engine = engine

    def bind_engine(self, engine) -> None:
        self.engine = engine

    def create_social_post(self, request: SocialPostRequest) -> dict[str, Any]:
        return self.store.create_run(request, self.settings.max_llm_calls, self.settings.max_run_seconds)

    def get(self, run_id: str) -> dict[str, Any]:
        return self.store.get_run(run_id)

    def list(self, *, limit: int = 20, status: str = "", project: str = "") -> list[dict[str, Any]]:
        return self.store.list_runs(limit=limit, status=status, project=project)

    def approve(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.REVIEW.value, WaitingReason.BUDGET_EXHAUSTED.value, ""
        }:
            raise InvalidStateTransition("A run can only be approved while waiting for human review")
        self.store.approve_latest_artifact(run_id)
        self.store.update_run(
            run_id,
            status=RunStatus.COMPLETED.value,
            revision_feedback="",
            waiting_reason="",
            waiting_step="",
            waiting_attempt=None,
        )
        self.store.db.log_event(run_id, "human_approved", {})
        self.store.db.log_event(run_id, "workflow_completed", {})
        return self.store.get_run(run_id)

    def changes(self, run_id: str, feedback: str, *, regenerate: bool = False) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.REVIEW.value, WaitingReason.BUDGET_EXHAUSTED.value, ""
        }:
            raise InvalidStateTransition("Revisions can only be requested in WAITING_HUMAN/review")
        if run.get("budget_exhausted"):
            raise BudgetExceeded("The budget is exhausted; only the existing artifact can be approved or rejected")
        if not feedback.strip() and not regenerate:
            raise ValueError("Feedback is required for revise")
        current_step = max(0, int(run["current_step"]))
        self.store.update_run(
            run_id,
            status=RunStatus.QUEUED.value,
            revision_feedback="" if regenerate else feedback.strip(),
            current_step=current_step,
            waiting_reason="",
            waiting_step="",
            waiting_attempt=None,
        )
        mode = "regenerate" if regenerate else "revise"
        self.store.db.log_event(run_id, "human_revision", {"mode": mode, "feedback": feedback})
        return self.store.get_run(run_id)

    def reject(self, run_id: str, feedback: str = "") -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value:
            raise InvalidStateTransition("A run can only be rejected in WAITING_HUMAN")
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
