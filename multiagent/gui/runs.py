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
from ..run_request import RunRequest, RoutingPreference
from ..routing_preferences import preference_request, validate_run_preferences
from ..routing_policy import EFFORT_LEVELS, RoutingRequest
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
    routing_preferences: dict[str, RoutingPreference] = Field(default_factory=dict)


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


def _routing_preview(services, workflow_id: str, bundle: str | None, inputs: dict[str, str],
                     execution_mode: ExecutionMode, sensitive: bool, preferences=None) -> dict[str, list[dict[str, Any]]]:
    """Read the configured model route for confirmation; no profile setting is changed or executed."""
    preferences = preferences or {}
    definition = services.definitions.get_workflow(workflow_id, bundle=bundle)
    engine = services.system.engine if bundle is None else None
    if preferences:
        if engine is None:
            raise ServiceError(INVALID_REQUEST, "Bundle preferences are unavailable in read-only preview", status=422)
        draft = RunRequest(workflow_id=workflow_id, project_name="Preview", inputs=inputs,
                           execution_mode=execution_mode, sensitive=sensitive, routing_preferences=preferences)
        try:
            validate_run_preferences(engine, draft, engine.preflight(workflow_id))
        except ValueError as exc:
            raise ServiceError(INVALID_REQUEST, str(exc), status=422) from exc
    if bundle is None:
        catalog, router = services.system.catalog, services.system.engine.router
    else:
        # Bundle routing is declared by the bundle. Use its catalog for profile details; model selection stays
        # unavailable here because preview never builds or mutates a bundle runtime.
        catalog, router = services.definitions._bundle(bundle).catalog, None
    steps = []
    for step in definition.get("steps", []):
        try:
            enabled = condition_is_true(step.get("when"), inputs)
        except (TypeError, ValueError):
            enabled = True
        if not enabled:
            steps.append({"step_id": step["id"], "step_kind": step.get("kind") or "work", "skipped": True,
                          "profile_id": None, "mode": None, "level": None, "model": None, "effort": None,
                          "status": "will_skip", "model_options": [], "effort_options": [], "budget_scope": None})
            continue
        preference = preferences.get(step["id"])
        preference = preference.model_dump(exclude_unset=True) if hasattr(preference, "model_dump") else (preference or {})
        if preference:
            actual_step = next(item for item in engine.preflight(workflow_id).steps if item.id == step["id"])
            selected_request, selected_profile = preference_request(engine, workflow_id, actual_step, inputs,
                                                                  execution_mode, sensitive, preference)
            profile_id, profile = selected_request.profile_id, selected_profile
        else:
            profile_id, profile = catalog.routing_profile_for(workflow_id, step["id"])
        profile = profile if isinstance(profile, dict) else None
        web_setting = step.get("requires_web")
        requires_web = (str(inputs.get("requires_web", "")).strip().casefold() in {"true", "1", "yes", "sí", "si"}
                        if web_setting == "from_request" else web_setting is True)
        if sensitive or execution_mode is ExecutionMode.LOCAL:
            preferred = "local_reasoning" if step["id"] == "strategy" else "local_default"
        elif execution_mode is ExecutionMode.CLOUD:
            preferred = "gemini_grounded" if requires_web else ("cloud_advanced" if step["id"] in {"strategy", "design"} else "cloud_fast")
        else:
            preferred = step.get("preferred_model") or None
        mode = profile.get("mode", "supervised") if profile else None
        model, effort, level, status, reasons = preferred, step.get("effort"), None, "catalog_default", []
        model_options = [model] if model in getattr(catalog, "models", {}) else []
        budget_scope = None
        if execution_mode is ExecutionMode.HUMAN_GUIDED:
            model, effort, status = None, None, "human_guided"
        elif router is not None and profile_id and profile is not None:
            request = RoutingRequest(
                profile_id=profile_id, workflow_id=workflow_id, step_id=step["id"],
                requested_model=profile.get("requested_model") or preferred,
                required_capabilities=frozenset({"structured_output"} | ({"web"} if requires_web else set())),
                required_role=step.get("agent"),
                required_permissions=frozenset(profile.get("required_permissions") or ()),
                data_class="sensitive" if sensitive else "non_sensitive",
                execution_mode=execution_mode.value, requires_web=requires_web, output_schema=step.get("contract"),
                step_kind=step.get("kind") or "work", effort=step.get("effort"),
            )
            try:
                if preference:
                    request = selected_request
                decision = router.select(request, **({"profile_override": profile} if preference else {})).to_dict()
                model = decision.get("selected_model")
                effort = decision.get("effort") or step.get("effort")
                level = decision.get("selected_level")
                status = decision.get("status", "unknown")
                reasons = decision.get("reason_codes", [])
                model_options = list(dict.fromkeys(
                    candidate["model_id"] for candidate in decision.get("candidates", [])
                    if candidate.get("eligible") is True and candidate.get("model_id")
                ))
                budget_scope = decision.get("budget_scope")
            except (TypeError, ValueError):
                model, status = None, "selection_unavailable"
                model_options = []
        elif profile_id and bundle is not None:
            model, status = None, "bundle_selection_unavailable"
            model_options = []
        steps.append({"step_id": step["id"], "step_kind": step.get("kind") or "work", "skipped": False,
                      "profile_id": profile_id, "mode": mode, "level": level, "model": model,
                      "effort": effort, "status": status, "reason_codes": reasons,
                      "model_options": model_options, "effort_options": list(EFFORT_LEVELS),
                      "budget_scope": budget_scope,
                      "profile_options": [key for key, item in catalog.routing_profiles.items() if isinstance(item, dict) and item.get("enabled") is True],
                      "mode_options": ["supervised", "automatic", "single"],
                      "level_options": list(dict.fromkeys(catalog.model(key).level for key in model_options if catalog.model(key).level)),
                      "preferences": preference})
    return {"steps": steps}


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
        """What the create-run form needs: the workflow description, its steps, and its inputs.

        Declared inputs carry label, description, example, type and choices; placeholders nobody
        declared are derived and carry the prompt line where they appear (item ``source``)."""
        return services.definitions.workflow_inputs(workflow_id, bundle=bundle)

    @router.post("/runs/preview")
    def preview(body: PreviewBody):
        """Render the prompts for these inputs as text. Nothing is executed or stored."""
        result = services.definitions.preview_prompts(
            body.workflow_id, body.inputs, bundle=body.bundle,
            execution_mode=body.execution_mode.value, sensitive=body.sensitive)
        result["routing"] = _routing_preview(services, body.workflow_id, body.bundle, body.inputs,
                                             body.execution_mode, body.sensitive, body.routing_preferences)
        return result

    @router.get("/composer/executors")
    def composer_executors():
        """Enabled executor choices and only the model/effort values supported by evidence."""
        executors = getattr(services, "executors", None)
        return executors._composer_options() if executors is not None else {"executors": []}


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
                execution_mode=body.execution_mode, idempotency_key=body.idempotency_key, sensitive=body.sensitive,
                routing_preferences=body.routing_preferences)
        except ValidationError as exc:
            raise translate(exc) from exc
        return run_view(services, services.runs.create(request, bundle=body.bundle))

    @router.get("/runs")
    def list_runs(limit: int = Query(50, ge=1, le=200), status: str = ""):
        return [{key: run.get(key) for key in LIST_FIELDS} for run in services.runs.list(limit=limit, status=status)]

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        return run_view(services, services.runs.get(run_id))

    @router.get("/runs/{run_id}/usage")
    def get_run_usage(run_id: str):
        """Provider usage of the run for the Tokens panel (GUI2-T05): the same read-only answer as
        ``/api/v1/runs/{id}/usage`` (records, totals per run/agent/step/model, estimated cost with its price
        evidence). Unreported tokens and unknown costs are ``null``, never 0. ``not_found`` for an unknown run."""
        return services.runs.usage(run_id)

    @router.get("/runs/{run_id}/human-next")
    def human_next(run_id: str):
        """The pending human-guided step with the exact prompt to copy."""
        run = services.runs.get(run_id)
        if not _awaits_human_step(run, run.get("human_step_request")):
            raise ServiceError(HUMAN_STEP_UNAVAILABLE, "The run is not waiting for a human-guided step", status=409)
        return services.runs.human_next(run_id)

    @router.post("/runs/{run_id}/approve")
    def approve(run_id: str):
        services.runs.approve(run_id, channel="gui")
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
        services.runs.reject(run_id, body.feedback, channel="gui")
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
