# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI routes for runs (``/gui/api``): create, preview, inspect and decide.

Every route calls the same application services as ``/api/v1`` and adds only
what the screen needs: workflow inputs for the form, a text preview of the
prompt, a run view model (steps with skipped ones and their reason, clear
counters, history of human actions) and the human decisions. Nothing here runs
a step: previews only render templates.

Event payloads are cleaned before leaving the server: keys that hold local
file paths are dropped and long strings are cut.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import ExecutionMode, HumanStepSubmission
from ..run_request import RunRequest
from ..services.errors import HUMAN_STEP_UNAVAILABLE, INVALID_REQUEST, ServiceError, translate
from ..services.workflow_inputs import missing_required
from ..workflows import condition_is_true

EVENTS_SHOWN = 200
TEXT_LIMIT = 2000
TERMINAL = frozenset({"completed", "failed", "cancelled", "rejected"})
REVIEW_REASONS = frozenset({"review", "budget_exhausted", ""})
HUMAN_STEP_REASONS = frozenset({"execute_step", "research"})
LIST_FIELDS = ("id", "workflow_id", "project_name", "status", "bundle", "waiting_reason", "waiting_step",
               "llm_calls", "max_llm_calls", "created_at", "updated_at", "error")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreviewBody(_Body):
    workflow_id: str = Field(min_length=1, max_length=120)
    bundle: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    sensitive: bool = False


class CreateBody(PreviewBody):
    project_name: str = Field(min_length=2, max_length=120)
    idempotency_key: str = Field(default="", max_length=120)


class ChangesBody(_Body):
    feedback: str = Field(max_length=4000)


class RejectBody(_Body):
    feedback: str = Field(default="", max_length=4000)


class CancelBody(_Body):
    force: bool = False


class SubmitBody(_Body):
    raw_response: str = Field(min_length=1)
    provider: str = Field(default="human", max_length=120)
    model: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=4000)


# --- view model ----------------------------------------------------------------

def _clean(value: Any, depth: int = 0) -> Any:
    """Drop local-path keys and cut long text; payloads are shown, never trusted."""
    if isinstance(value, dict):
        return {key: _clean(item, depth + 1) for key, item in value.items()
                if not (isinstance(key, str) and (key == "path" or key.endswith("_path")))} if depth < 6 else "..."
    if isinstance(value, list):
        return [_clean(item, depth + 1) for item in value[:50]] if depth < 6 else "..."
    if isinstance(value, str) and len(value) > TEXT_LIMIT:
        return value[:TEXT_LIMIT] + "..."
    return value


def _history(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Human actions of the run, oldest first, built only from its events.

    ``actor`` is ``operator`` (a decision made in a client; the framework has no
    user identities), ``system`` or ``declared`` (provider and model the person
    typed when pasting a response: reported by the human, not verified).
    """
    history = []
    for event in events:
        kind, payload = event["event_type"], event["payload"]
        entry: dict[str, Any] = {"seq": event["seq"], "ts": event["ts"], "step_id": payload.get("step_id"),
                                 "attempt": payload.get("attempt"), "actor": "operator"}
        if kind == "human_step_requested":
            entry.update(kind="prompt_delivered", actor="system", contract=payload.get("expected_contract"))
        elif kind == "human_step_submitted":
            entry.update(kind="response_submitted", actor="declared", provider=payload.get("provider") or "",
                         model=payload.get("model") or "")
        elif kind == "human_submission_invalid":
            entry.update(kind="response_rejected", error=_clean(payload.get("error") or ""))
        elif kind == "human_approved":
            entry.update(kind="approved")
        elif kind == "human_revision":
            regenerated = payload.get("mode") == "regenerate"
            entry.update(kind="regenerated" if regenerated else "changes_requested",
                         feedback="" if regenerated else _clean(payload.get("feedback") or ""))
        elif kind == "human_rejected":
            entry.update(kind="rejected", feedback=_clean(payload.get("feedback") or ""))
        elif kind == "cancel_requested":
            entry.update(kind="cancel_requested")
        elif kind == "workflow_cancelled":
            entry.update(kind="cancelled", actor="operator" if "force" in payload else "system",
                         forced=bool(payload.get("force")))
        else:
            continue
        history.append(entry)
    return history


def _steps(run: dict[str, Any], definition: dict[str, Any] | None, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per workflow step with its state; skipped steps carry the condition."""
    skipped: dict[str, dict[str, Any]] = {}
    attempts: dict[str, int] = {}
    completed: dict[str, int] = {}
    executor: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in events:
        payload, step_id = event["payload"], event["payload"].get("step_id")
        if not step_id:
            continue
        kind = event["event_type"]
        if kind in {"step_started", "step_skipped", "human_step_requested"} and step_id not in order:
            order.append(step_id)
        if kind == "step_skipped":
            skipped[step_id] = {"condition": payload.get("condition")}
        elif kind == "step_started":
            attempts[step_id] = attempts.get(step_id, 0) + 1
        elif kind == "step_completed":
            completed[step_id] = completed.get(step_id, 0) + 1
            executor.setdefault(step_id, {})["completed"] = True
        elif kind == "model_selected":
            executor[step_id] = {"kind": "model", "provider": payload.get("provider"), "model": payload.get("model")}
        elif kind == "human_step_submitted":
            executor[step_id] = {"kind": "human", "provider": payload.get("provider") or "",
                                 "model": payload.get("model") or ""}
    status, waiting_step = run["status"], run.get("waiting_step") or ""
    reason, current = run.get("waiting_reason") or "", int(run.get("current_step") or 0)
    request = run.get("request") or {}
    if definition is not None:
        listing = [(index, step["id"], step.get("agent"), step.get("when"), step.get("contract"))
                   for index, step in enumerate(definition["steps"])]
    else:
        listing = [(index, step_id, None, None, None) for index, step_id in enumerate(order)]
    steps = []
    for index, step_id, agent, when, contract in listing:
        try:
            enabled = condition_is_true(when, request)
        except ValueError:
            enabled = True
        skip = skipped.get(step_id) or (None if enabled else {"condition": when})
        done = completed.get(step_id, 0) > 0
        if status == "waiting_human" and waiting_step == step_id:
            state = "waiting_review" if reason in REVIEW_REASONS else "waiting_human"
        elif step_id in skipped:
            state = "skipped"
        elif done or index < current:
            state = "done"
        elif skip is not None and status not in TERMINAL:
            state = "will_skip"
        elif status in {"running", "queued"} and index == current:
            state = "running" if status == "running" else "queued"
        elif status == "failed" and index == current:
            state = "failed"
        elif status in TERMINAL:
            state = "not_run"
        else:
            state = "pending"
        info = executor.get(step_id) or {}
        steps.append({
            "id": step_id, "index": index, "agent": agent, "contract": contract, "state": state,
            "skip": skip if state in {"skipped", "will_skip"} or (skip and state == "not_run") else None,
            "attempts": max(attempts.get(step_id, 0), completed.get(step_id, 0)),
            "executor": {key: info[key] for key in ("kind", "provider", "model") if key in info} or None,
        })
    return steps


def _inputs_of(request: dict[str, Any]) -> dict[str, str]:
    if isinstance(request.get("inputs"), dict):
        return {str(key): str(value) for key, value in request["inputs"].items()}
    skip = {"project_name", "idempotency_key", "execution_mode", "research_mode", "pipeline_mode"}
    return {key: value for key, value in request.items() if isinstance(value, str) and value and key not in skip}


def _awaits_human_step(run: dict[str, Any], pending: dict[str, Any] | None) -> bool:
    """A stored request stays 'pending' after a cancel; only a waiting run really awaits it."""
    return bool(pending) and run["status"] == "waiting_human" and (run.get("waiting_reason") or "") in HUMAN_STEP_REASONS


def run_view(services, run: dict[str, Any]) -> dict[str, Any]:
    """The run as the screen needs it (see module docstring)."""
    events = run["events"]
    request = run.get("request") or {}
    try:
        definition = services.definitions.read_workflow(run["workflow_id"], bundle=run.get("bundle"))
    except ServiceError:
        definition = None  # the file changed or vanished: show what the events say
    steps = _steps(run, definition, events)
    total = len(steps)
    cursor = int(run.get("current_step") or 0)
    pending = run.get("human_step_request")
    history = _history(events)
    return {
        "id": run["id"],
        "workflow_id": run["workflow_id"],
        "bundle": run.get("bundle"),
        "project_name": request.get("project_name") or "",
        "status": run["status"],
        "terminal": run["status"] in TERMINAL,
        "execution_mode": run.get("execution_mode") or "auto",
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "error": run.get("error") or "",
        "cancel_requested": bool(run.get("cancel_requested")),
        "waiting": ({"reason": run.get("waiting_reason") or "", "step_id": run.get("waiting_step") or "",
                     "attempt": run.get("waiting_attempt")} if run["status"] == "waiting_human" else None),
        "revision_feedback": run.get("revision_feedback") or "",
        "inputs": _inputs_of(request),
        "definition_available": definition is not None,
        "cursor": {"index": cursor, "total": total, "finished": total > 0 and cursor >= total},
        "steps": steps,
        "counters": {
            "model_calls": int(run.get("llm_calls") or 0),
            "max_model_calls": int(run.get("max_llm_calls") or 0),
            "human_steps": sum(1 for entry in history if entry["kind"] == "response_submitted"),
            "steps_total": total,
            "steps_done": sum(1 for step in steps if step["state"] == "done"),
            "steps_skipped": sum(1 for step in steps if step["state"] == "skipped"),
            "active_seconds": round(float(run.get("active_seconds") or 0.0), 1),
            "max_seconds": int(run.get("max_run_seconds") or 0),
            "budget_exhausted": bool(run.get("budget_exhausted")),
        },
        "pending_human": ({"step_id": pending["step_id"], "attempt": pending["attempt"],
                           "expected_contract": pending["expected_contract"]} if _awaits_human_step(run, pending) else None),
        "history": history,
        "artifacts": [{"step_id": a["step_id"], "attempt": a["attempt"], "kind": a["kind"],
                       "approved": bool(a["approved"]), "created_at": a["created_at"], "data": a["data"]}
                      for a in run.get("artifacts", [])],
        "events": [{"seq": e["seq"], "ts": e["ts"], "type": e["event_type"], "payload": _clean(e["payload"])}
                   for e in events[-EVENTS_SHOWN:]],
        "events_total": len(events),
    }


# --- routes --------------------------------------------------------------------

def _missing_error(missing: list[str]) -> ServiceError:
    return ServiceError(INVALID_REQUEST, "Required workflow inputs are empty", status=422,
                        details={"missing_inputs": missing})


def build_router(services) -> APIRouter:
    """Routes for the runs screen; ``services`` is read at call time."""
    router = APIRouter()

    @router.get("/bundles")
    def bundles():
        return services.definitions.list_bundles()

    @router.get("/workflows")
    def workflows(bundle: str | None = None):
        return services.definitions.list_workflows(bundle=bundle)

    @router.get("/workflows/{workflow_id}/inputs")
    def workflow_inputs(workflow_id: str, bundle: str | None = None):
        """Inputs the workflow expects (derived from its prompt placeholders) and its steps."""
        return services.definitions.workflow_inputs(workflow_id, bundle=bundle)

    @router.post("/runs/preview")
    def preview(body: PreviewBody):
        """Render the prompts for these inputs as text. Nothing is executed or stored."""
        return services.definitions.preview_prompts(
            body.workflow_id, body.inputs, bundle=body.bundle,
            execution_mode=body.execution_mode.value, sensitive=body.sensitive)

    @router.post("/runs", status_code=202)
    def create(body: CreateBody):
        """Create a run; empty required inputs are rejected here, whatever the client did."""
        spec = services.definitions.workflow_inputs(body.workflow_id, bundle=body.bundle)
        missing = missing_required(spec, body.inputs)
        if missing:
            raise _missing_error(missing)
        try:
            request = RunRequest(
                workflow_id=body.workflow_id, project_name=body.project_name.strip(), inputs=body.inputs,
                execution_mode=body.execution_mode, idempotency_key=body.idempotency_key, sensitive=body.sensitive)
        except ValidationError as exc:
            raise translate(exc) from exc
        return run_view(services, services.runs.create(request, bundle=body.bundle))

    @router.get("/runs")
    def list_runs(limit: int = Query(50, ge=1, le=200), status: str = ""):
        return [{key: run.get(key) for key in LIST_FIELDS} for run in services.runs.list(limit=limit, status=status)]

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        return run_view(services, services.runs.get(run_id))

    @router.get("/runs/{run_id}/human-next")
    def human_next(run_id: str):
        """The pending human-guided step with the exact prompt to copy."""
        run = services.runs.get(run_id)
        if not _awaits_human_step(run, run.get("human_step_request")):
            raise ServiceError(HUMAN_STEP_UNAVAILABLE, "The run is not waiting for a human-guided step", status=409)
        return services.runs.human_next(run_id)

    @router.post("/runs/{run_id}/approve")
    def approve(run_id: str):
        services.runs.approve(run_id)
        return run_view(services, services.runs.get(run_id))

    @router.post("/runs/{run_id}/changes")
    def changes(run_id: str, body: ChangesBody):
        if not body.feedback.strip():
            raise ServiceError(INVALID_REQUEST, "Feedback is required to request changes", status=422,
                               details={"field": "feedback"})
        services.runs.request_changes(run_id, body.feedback)
        return run_view(services, services.runs.get(run_id))

    @router.post("/runs/{run_id}/reject")
    def reject(run_id: str, body: RejectBody):
        services.runs.reject(run_id, body.feedback)
        return run_view(services, services.runs.get(run_id))

    @router.post("/runs/{run_id}/regenerate")
    def regenerate(run_id: str):
        services.runs.regenerate(run_id)
        return run_view(services, services.runs.get(run_id))

    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, body: CancelBody):
        services.runs.cancel(run_id, force=body.force)
        return run_view(services, services.runs.get(run_id))

    @router.post("/runs/{run_id}/human-submit")
    def human_submit(run_id: str, body: SubmitBody):
        services.runs.human_submit(run_id, HumanStepSubmission(**body.model_dump()))
        return run_view(services, services.runs.get(run_id))

    return router
