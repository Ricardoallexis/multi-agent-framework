from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .call_guards import cut_by_run_limits
from .config import Settings
from .contracts import HumanStepSubmission, RunStatus, SocialPostRequest, WaitingReason
from .db.store import Store
from .db.database import utcnow
from .errors import BudgetExceeded, HumanSubmissionError, InvalidStateTransition
from .functions import announce_run_events, insert_run_events
from .tools.approvals import ToolApprovalMismatch
from .run_request import OPERATING_MODE_SET, RunRequest, validate_function_step_options
from .routing_preferences import validate_run_preferences
from .teams.step_roles import TEAM_VERSION_BOUND, bind_step_roles, bound_team_from_payload
from .workflow_validation import WorkflowDefinitionError
from .workflows import condition_is_true


class PersonRequired(InvalidStateTransition):
    """The decision needs an authenticated person and the caller is not one (``details.reason`` ``person_required``)."""


class UserClickRequired(InvalidStateTransition):
    """A secret review can only be released by the GUI approval decision."""


class RunService:
    def __init__(self, settings: Settings, store: Store, engine=None):
        self.settings = settings
        self.store = store
        self.engine = engine

    def bind_engine(self, engine) -> None:
        self.engine = engine

    def create_social_post(self, request: SocialPostRequest) -> dict[str, Any]:
        return self.create_run(request)

    def create_run(self, request: RunRequest | SocialPostRequest, *,
                   team_binding: dict[str, Any] | None = None) -> dict[str, Any]:
        """Validate and persist a named workflow, preserving social request compatibility.

        ``team_binding`` is the ``team_version_bound`` payload of a bundle's approved team (``services.runs``): the
        roles the steps name are checked against it, and it is stored with the run in the same transaction."""
        existing = self.store.find_idempotent_run(request)
        if existing is not None:
            return existing
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        # Loading/validation errors propagate with their original type and issues.
        # No project or run is persisted until the entire definition is valid.
        definition = self.engine.preflight(self.store.workflow_id_for(request))
        if isinstance(request, RunRequest):
            validate_function_step_options(request, definition)
        # Preferences are checked with the agent each role gives its step, as the run will execute it.
        definition, _ = bind_step_roles(
            definition, bound_team_from_payload(team_binding) if team_binding is not None else None,
            catalog=getattr(self.engine, "catalog", None),
            skills_dir=getattr(getattr(self.engine, "prompts", None), "skills_dir", None))
        if getattr(request, "routing_preferences", None):
            validate_run_preferences(self.engine, request, definition)
        events = ((TEAM_VERSION_BOUND, team_binding),) if team_binding is not None else ()
        run_mode = getattr(request, "operating_mode", None)
        team_mode = (team_binding or {}).get("team", {}).get("mode")
        if run_mode is not None or team_mode is not None:
            events += ((OPERATING_MODE_SET, {"mode": run_mode or team_mode,
                                             "source": "run" if run_mode is not None else "team"}),)
        return self.store.create_run(request, self.settings.max_llm_calls, self.settings.max_run_seconds,
                                     events=events)

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

    def _finish_function_wait(self, run: dict[str, Any], *, current_step: int,
                              feedback: str = "", events: tuple[tuple[str, dict[str, Any]], ...],
                              request_json: str | None = None) -> None:
        """Settle one function wait atomically; a repeated decision cannot queue it twice. The event listeners hear
        of the decision once it is committed (``functions.announce_run_events``)."""
        with self.store.db.tx() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run["id"],)).fetchone()
            fields = ("status", "current_step", "waiting_reason", "waiting_step", "waiting_attempt", "cancel_requested")
            if (row is None or row["status"] != RunStatus.WAITING_HUMAN.value
                    or row["cancel_requested"] or any(row[key] != run[key] for key in fields)):
                raise InvalidStateTransition("The function wait changed; read the run again")
            conn.execute(
                """UPDATE runs SET status=?,current_step=?,revision_feedback=?,request_json=COALESCE(?,request_json),error='',
                   waiting_reason='',waiting_step='',waiting_attempt=NULL,updated_at=? WHERE id=?""",
                (RunStatus.QUEUED.value, current_step, feedback, request_json, utcnow(), run["id"]),
            )
            inserted = insert_run_events(conn, run["id"], events)
        announce_run_events(self.store, inserted)

    def correct_function_inputs(self, run_id: str, inputs: dict[str, str], *,
                                by: str | None = None, channel: str | None = None) -> dict[str, Any]:
        """Replace only declared inputs after a function failure without a prior model output."""
        from .services.workflow_inputs import derive_inputs, missing_required

        run = self.store.get_run(run_id)
        if (run["status"] != RunStatus.WAITING_HUMAN.value
                or run.get("waiting_reason") != WaitingReason.FUNCTION_FAILURE.value):
            raise InvalidStateTransition("The run is not waiting for a function input correction")
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        try:
            definition = self.engine.preflight(run["workflow_id"])
        except (KeyError, WorkflowDefinitionError) as exc:
            raise InvalidStateTransition("The failed function no longer matches the workflow") from exc
        index = run["current_step"]
        if not 0 <= index < len(definition.steps):
            raise InvalidStateTransition("The failed function no longer matches the workflow")
        step = definition.steps[index]
        if (step.id != run["waiting_step"] or step.function is None
                or not condition_is_true(step.when, run["request"])):
            raise InvalidStateTransition("The failed function no longer matches the workflow")
        if (index > 0 and definition.steps[index - 1].function is None
                and self.store.latest_step_output(run_id, definition.steps[index - 1].id) is not None):
            raise InvalidStateTransition("Correct the previous model output for this function failure")
        allowed = {item.name for item in definition.inputs}
        if not inputs or not set(inputs) <= allowed:
            raise ValueError("Supply only declared workflow inputs read by the function")
        request = run["request"]
        merged = {**(request.get("inputs") or {}), **inputs}
        try:
            RunRequest.model_validate({"workflow_id": run["workflow_id"],
                                       "project_name": "Input correction", "inputs": merged})
        except ValidationError as exc:
            raise ValueError("Invalid workflow inputs") from exc
        spec = derive_inputs(definition, self.engine.prompts)
        missing = missing_required({**spec, "inputs": [item for item in spec["inputs"] if item["name"] in allowed]}, merged)
        if missing:
            raise ValueError("Required workflow inputs are empty: " + ", ".join(missing))
        for item in definition.inputs:
            value = merged.get(item.name, "")
            if item.type == "choice" and value and value not in item.choices:
                raise ValueError(f"Workflow input {item.name!r} must be one of its declared choices")
        retry = {"step_id": run["waiting_step"], "attempt": run["waiting_attempt"] + 1}
        correction = {"step_id": run["waiting_step"], "attempt": run["waiting_attempt"],
                      "input_names": sorted(inputs), "by": by, "channel": channel}
        self._finish_function_wait(
            run, current_step=index,
            events=(("function_inputs_corrected", correction), ("function_retry_requested", retry)),
            request_json=json.dumps({**request, "inputs": merged}, ensure_ascii=False),
        )
        return self.store.get_run(run_id)

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
        the token route of the GUI (which the command line goes through) passes it. The review before a function step
        (``function_review``) follows the same rule: without ``person=True`` the function is never released."""
        run = self.store.get_run(run_id)
        if (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.FUNCTION_REVIEW.value):
            if recorded_only or not person:
                raise PersonRequired("A person must approve the inputs before this function step runs: use the GUI "
                                     "(it carries the token) or the command line. Rejecting works here.")
            payload = {"step_id": run["waiting_step"], "attempt": run["waiting_attempt"],
                       "by": by, "channel": channel}
            self._finish_function_wait(run, current_step=run["current_step"],
                                       events=(("function_review_approved", payload),))
            return self.store.get_run(run_id)
        if (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.SECRET_REVIEW.value):
            raise UserClickRequired("Aprueba en la GUI → Aprobaciones")
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
            current_step=len(definition.steps) if completed else next_step, by=by, channel=channel,
        )
        return self.store.get_run(run_id)

    def changes(self, run_id: str, feedback: str, *, comments=(), regenerate: bool = False,
                by: str | None = None, channel: str | None = None) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if (run["status"] == RunStatus.WAITING_HUMAN.value
                and run.get("waiting_reason") == WaitingReason.FUNCTION_FAILURE.value):
            if not feedback.strip() or comments or regenerate:
                raise ValueError("Explicit feedback for the previous model step is required")
            if self.engine is None:
                raise RuntimeError("RunService has no WorkflowEngine attached")
            definition = self.engine.preflight(run["workflow_id"])
            index = run["current_step"]
            if (index < 1 or definition.steps[index].id != run["waiting_step"]
                    or definition.steps[index - 1].function is not None):
                raise InvalidStateTransition("The failed function has no previous model step to correct")
            previous = definition.steps[index - 1]
            artifact = self.store.review_artifact(run_id, step_id=previous.id)
            validator = getattr(self.engine, "validate_waiting_step", None) or self.engine._validate_waiting_step
            validator(run, artifact, definition=definition, current_step=index - 1)
            from .revision_comments import render_revision_feedback
            rendered = render_revision_feedback(feedback.strip(), (), step_id=previous.id,
                                                attempt=artifact["attempt"])
            revision = {"step_id": previous.id, "attempt": artifact["attempt"],
                        "artifact_id": artifact["id"], "mode": "revise", "feedback": feedback.strip(),
                        "by": by, "channel": channel}
            retry = {"step_id": run["waiting_step"], "attempt": run["waiting_attempt"] + 1}
            self._finish_function_wait(run, current_step=index - 1, feedback=rendered,
                                       events=(("human_revision", revision), ("function_retry_requested", retry)))
            return self.store.get_run(run_id)
        if run["status"] != RunStatus.WAITING_HUMAN.value or run.get("waiting_reason") not in {
            WaitingReason.REVIEW.value, WaitingReason.APPROVAL_PENDING.value, WaitingReason.BUDGET_EXHAUSTED.value, ""
        }:
            raise InvalidStateTransition("Revisions can only be requested in WAITING_HUMAN/review")
        exhausted = bool(run.get("budget_exhausted")) or run.get("waiting_reason") == WaitingReason.BUDGET_EXHAUSTED.value
        if exhausted and not cut_by_run_limits(run):
            raise BudgetExceeded("The budget is exhausted; only the existing artifact can be approved or rejected")
        if not feedback.strip() and not comments and not regenerate:
            raise ValueError("Feedback is required for revise")
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        _, artifact = self.engine.review_point(run)
        from .revision_comments import render_revision_feedback
        feedback = "" if regenerate else feedback.strip()
        rendered = render_revision_feedback(feedback, comments, step_id=artifact["step_id"],
                                            attempt=artifact["attempt"])
        current_step = run["current_step"]
        if exhausted:
            # The cut left the cursor on the step that could not run: the correction redoes the step under review.
            definition, _ = self.engine.review_point(run)
            current_step = [step.id for step in definition.steps].index(artifact["step_id"])
        self.store.finish_review(
            run, artifact, approve=False, current_step=current_step,
            feedback=feedback, revision_feedback=rendered,
            comments=[comment.model_dump() for comment in comments], by=by, channel=channel,
            regenerate=regenerate,
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
            "expected_schema": self.engine.output_schemas[pending["expected_contract"]].model_json_schema(),
            "request_path": next((event["payload"].get("prompt_path") for event in reversed(run["events"])
                                  if event["event_type"] == "human_step_requested"
                                  and event["payload"].get("step_id") == pending["step_id"]
                                  and event["payload"].get("attempt") == pending["attempt"]), None),
            "prompt_id": pending["prompt_id"],
            "prompt_version": pending["prompt_version"],
            "prompt_sha256": pending["prompt_sha256"],
            "prompt": pending["prompt_base"],
        }

    def human_submit(self, run_id: str, submission: HumanStepSubmission) -> dict[str, Any]:
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        return self.engine.submit_human_step(run_id, submission)

    def human_check(self, run_id: str, raw_response: str) -> dict[str, Any]:
        if self.engine is None:
            raise RuntimeError("RunService has no WorkflowEngine attached")
        return self.engine.check_human_step(run_id, raw_response)
