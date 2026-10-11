# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Executor services (ORQ-6, ORQ-T51): run an ORQ task in an executor application and bring its result back.

An executor application is an agent app driven through its official CLI (Codex, Claude, Copilot; adapters ORQ-T48 to
T50, process executor ORQ-T47, contract ``ORQ-6-contrato.md`` sections 5 to 9). Available as
``ApplicationServices.executors``.

``run_task`` takes a task that is ready (or claimed, paused or in ``needs_changes`` by the same actor), picks the
executor (the one asked for, or the one an assignment preview selects), builds the launch request (the task envelope
of ORQ-3, a work folder the framework owns, the permission the task scope allows) and launches it through the
adapter. The session id and the attempt are recorded as team events. The result envelope the app printed goes in
through ``envelopes.submit_result``: the task becomes ``submitted`` and the gate (ORQ-2) decides, never this service.
This module changes a task only through ``tasks.transition`` (``claim``, ``start``, ``resume``, ``pause``) and
``submit_result``; it never accepts, fails or reopens a task and writes no task row itself.

Failures are recorded, not retried. An attempt without a valid result envelope, a timeout, a cancellation or a block
by an open session leaves the task ``paused`` with trimmed evidence in the ``executor.attempt_finished`` event; a quota
or limit error also pauses the executor until a configured time (``executor.paused``). Nothing loops: the next run is
asked for by a person or by the orchestrator.

``resume_task`` continues the session the framework created for the task (the latest ``executor.session_recorded``
event), never one it did not create, and refuses a session that was opened in an app in the last 12 hours
(``session.opened_in_app``, written by ``record_app_open`` when the GUI launches Claude or ``open_target`` hands out a
target). Limitation (ORQ-6 section 6): a link the browser follows (``codex://``, ``ghapp://``) or an app the user opens
alone leaves no record, so the 12 hour rule cannot cover them; Codex blocks writes on a session open in its app by
itself. ``session_guard`` is the same check as a function, passed to the adapters that need one (Copilot).

``open_target`` returns the adapter's open target for the recorded session (the one the GUI uses) and records the open.

Model and effort (ECO-T08) come from the caller or, when a profile is given, from the model routing decision of the
assignment profile's routing profile (economic or not) for the task's ``step_kind``, through ``_routing_selection``.
The caller's values always win (source ``user``). A value from the profile is sent only when the executor declares it
takes it: a model that is not in its ``model_support`` values, or an effort its ``effort_support`` does not list (Claude's
effort is unknown, so it is never sent), is dropped and the reason goes in ``notes``. The router is asked per executor
with the models it cannot receive excluded (Copilot refuses every Claude model), so each executor gets the best model it
can take. The assignment preview shows the same selection for each executor (``decision.evidence.executor_requests``).

Events (team event log, no migration): ``executor.session_recorded`` (``ref`` is the session id),
``executor.routing_selected`` (``ref`` is the executor id: the model and effort of the attempt, where each came from,
the routing profile, mode, step kind, level, price source, effort source and the candidates skipped for this executor),
``executor.attempt_finished`` (outcome, exit code, usage, trimmed evidence), ``executor.paused`` (``ref`` is the
executor id) and ``session.opened_in_app`` (``ref`` is the session id).

Errors (``ServiceError``): ``not_found`` (task, executor), ``invalid_request`` (malformed ids or limits, a request the
adapter refuses) and ``invalid_state`` (``details.reason`` says why: ``executor_disabled``, ``executor_without_adapter``,
``executor_paused``, ``executor_unavailable``, ``task_not_runnable``, ``no_stored_session``, ``session_open_in_app``,
``assignment_not_selected``, ``model_not_supported``, ``effort_not_supported``, ``workdir_invalid``). ``model_not_supported``
and ``effort_not_supported`` refuse a value the caller named; one that came from the profile is dropped instead.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import MultiAgentError
from ..executors import process
from ..executors.contract import ExecutorApplicationAdapter, ExecutorLaunchRequest, OpenTarget, Permission
from ..executors.selection import ExecutorSelection, select_for_executor, select_for_resume
from ..routing.executor_capabilities import CapabilityEvidence, SupportState
from ..routing_policy import STEP_KINDS
from ..sessions.models import is_uuid
from ..tasks.envelopes import EnvelopeError
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors
from .tasks import _TASK_ID, _text

if TYPE_CHECKING:
    from . import ApplicationServices

__all__ = ["ExecutorServices", "EVENT_TYPES", "APP_OPEN_GUARD"]

_logger = logging.getLogger(__name__)

SESSION_EVENT = "executor.session_recorded"
ATTEMPT_EVENT = "executor.attempt_finished"
ROUTING_EVENT = "executor.routing_selected"
PAUSE_EVENT = "executor.paused"
OPEN_EVENT = "session.opened_in_app"
EVENT_TYPES = (SESSION_EVENT, ROUTING_EVENT, ATTEMPT_EVENT, PAUSE_EVENT, OPEN_EVENT)

APP_OPEN_GUARD = timedelta(hours=12)      # a session opened in an app is not resumed for this long (ORQ-6 section 6)
MAX_TIMEOUT_SECONDS = 86400
MAX_EVIDENCE_CHARS = 800                  # trimmed output kept as evidence of a failed attempt
MAX_NOTE_CHARS = 160
MAX_NOTES = 6
EVENT_PAYLOAD_BUDGET = 1900               # the team event payload limit is 2048 bytes
_PROBE_SESSION = "00000000-0000-4000-8000-000000000000"   # only to build an argv for the pre-flight check of Claude
_EXECUTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SOURCE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_FAILURE_OUTCOMES = ("timeout", "cancelled", "quota_exhausted", "blocked_open_session")


def _invalid(field_name: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field_name, **details})


def _conflict(reason: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})


def _missing(kind: str, identifier: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"{kind} not found: {identifier}", status=404, details={kind.lower(): identifier})


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _collapse(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _tail(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else "..." + flat[-(limit - 3):]


def _fit(payload: dict[str, Any]) -> dict[str, Any]:
    """Shrink an event payload to the budget: the evidence first, then the notes and the usage."""
    payload = dict(payload)

    def size() -> int:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    while size() > EVENT_PAYLOAD_BUDGET and len(str(payload.get("evidence", ""))) > 40:
        payload["evidence"] = str(payload["evidence"])[: len(str(payload["evidence"])) // 2]
    for key in ("skipped", "notes", "usage"):
        if size() > EVENT_PAYLOAD_BUDGET:
            payload.pop(key, None)
    return payload


def _command_view(record: Any) -> dict[str, Any]:
    """The command that ran without local paths: the program's name and its arguments, folders as ``<path>``."""
    argv = list(getattr(record, "argv", ()) or ())
    shown = [os.path.basename(argv[0])] if argv else []
    shown += ["<path>" if os.path.isabs(str(item)) else str(item) for item in argv[1:]]
    return {"argv": shown, "exit_code": getattr(record, "exit_code", None)}


def _target_view(target: OpenTarget) -> dict[str, Any]:
    value = list(target.value) if not isinstance(target.value, str) else target.value
    return {"kind": target.kind, "value": value, "needs_confirmation": bool(target.needs_confirmation)}


def _check_support(evidence: CapabilityEvidence, value: str | None, what: str, executor_id: str) -> None:
    """Refuse a model or effort the executor's evidence says it does not take; unknown evidence passes through."""
    if value is None:
        return
    if evidence.status is SupportState.UNSUPPORTED:
        raise _conflict(f"{what}_not_supported", f"{executor_id} does not take a {what} per call", executor_id=executor_id)
    if evidence.status is SupportState.SUPPORTED and evidence.values is not None and value not in evidence.values:
        raise _conflict(f"{what}_not_supported", f"{executor_id} has no evidence for {what} {value!r}",
                        executor_id=executor_id, supported=sorted(evidence.values))


@dataclass
class _Plan:
    executor_id: str
    descriptor: Any
    adapter: Any
    model: str | None
    effort: str | None
    declaration: Mapping[str, Any]
    decision: Mapping[str, Any] | None = None
    selection: ExecutorSelection | None = None


@dataclass
class _Attempt:
    """What one launch or resume produced, in the shape the service records."""
    status: str                                   # a RunStatus value, or "launch_refused"
    session_id: str | None = None
    envelope: Mapping[str, Any] | None = None
    usage: Mapping[str, Any] | None = None
    command: dict[str, Any] | None = None
    exit_code: int | None = None
    notes: list[str] = field(default_factory=list)
    evidence: str = ""


class ExecutorServices:
    def __init__(self, services: ApplicationServices, *, registry: Any = None,
                 clock: Callable[[], datetime] | None = None):
        self._services = services
        self._registry = registry
        # A test sets ``clock`` to move time; every "until" and the 12 hour rule read it.
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))

    # -------- wiring --------
    @property
    def _tasks(self):
        return self._services.tasks

    @property
    def _store(self):
        return self._services.tasks.store

    @property
    def registry(self):
        """The executor registry of the assignment service (the same descriptors the selector sees)."""
        return self._registry if self._registry is not None else self._services.assignment.registry

    def build_adapters(self) -> list:
        """The adapters that ship with the framework, with this service's session guard (see ``default_adapters``)."""
        from ..executors.defaults import default_adapters

        return default_adapters(session_guard=self.session_guard)

    # -------- events --------
    def _append(self, event_type: str, *, actor: str, task: Mapping[str, Any] | None = None, ref: str = "",
                cause: str = "", payload: dict[str, Any]) -> None:
        with self._store.transaction() as conn:
            self._store.append_event(conn, event_type, actor=actor, group_id=str(task["group_id"]) if task else "",
                                     task_id=str(task["id"]) if task else "", cause=cause, ref=ref,
                                     payload=_fit(payload))

    @staticmethod
    def _latest(conn: Any, event_type: str, *, task_id: str | None = None, ref: str | None = None) -> dict[str, Any] | None:
        sql, params = "SELECT * FROM team_events WHERE type=?", [event_type]
        if task_id is not None:
            sql, params = sql + " AND task_id=?", params + [task_id]
        if ref is not None:
            sql, params = sql + " AND ref=?", params + [ref]
        row = conn.execute(sql + " ORDER BY seq DESC LIMIT 1", params).fetchone()
        return ExecutorServices._row(row) if row is not None else None

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def _stored_session(self, task_id: str) -> dict[str, Any] | None:
        """The latest session the framework recorded for the task: ``{session_id, executor_id, model, effort, ...}``."""
        with self._store.db.connect() as conn:
            event = self._latest(conn, SESSION_EVENT, task_id=task_id)
        if event is None:
            return None
        return {**event["payload"], "session_id": event["ref"], "seq": event["seq"], "created_at": event["created_at"]}

    def _opened_until(self, conn: Any, session_id: str) -> datetime | None:
        """When the 12 hour rule for an open session ends, or None when it is not (or no longer) in force."""
        event = self._latest(conn, OPEN_EVENT, ref=session_id)
        opened = _parse_time(event["payload"].get("opened_at") or event["created_at"]) if event else None
        if opened is None:
            return None
        until = opened + APP_OPEN_GUARD
        return until if self.clock() < until else None

    def _paused_until(self, conn: Any, executor_id: str) -> datetime | None:
        event = self._latest(conn, PAUSE_EVENT, ref=executor_id)
        until = _parse_time(event["payload"].get("until")) if event else None
        return until if until is not None and self.clock() < until else None

    def session_guard(self, session_id: str) -> bool:
        """True when the framework created ``session_id`` (it is recorded) and no app opened it in the last 12 hours.

        This is the check handed to the adapters that need one (``CopilotAdapter(session_guard=...)``) and the one
        ``resume_task`` applies before it continues a session (ORQ-6 section 6).
        """
        if not is_uuid(session_id):
            return False
        sid = session_id.lower()
        with self._store.db.connect() as conn:
            if self._latest(conn, SESSION_EVENT, ref=sid) is None:
                return False
            return self._opened_until(conn, sid) is None

    # -------- input checks --------
    @staticmethod
    def _task_id(value: Any) -> str:
        if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
            raise _invalid("task_id", f"Invalid task_id: {value!r}")
        return value

    @staticmethod
    def _optional_token(field_name: str, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise _invalid(field_name, f"{field_name} must be a short non-empty text")
        return value.strip()

    @staticmethod
    def _timeout(value: Any, default: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TIMEOUT_SECONDS:
            raise _invalid("timeout_seconds", f"timeout_seconds must be an integer from 1 to {MAX_TIMEOUT_SECONDS}")
        return value

    # -------- planning --------
    def _executor(self, executor_id: str) -> tuple[Any, Any, Mapping[str, Any]]:
        """``(descriptor, adapter, declaration)`` of an executor that may run now, or an error saying why not."""
        registry = self.registry
        descriptor = registry.get(executor_id)
        if descriptor is None:
            raise _missing("Executor", executor_id)
        if descriptor.enabled is not True:
            raise _conflict("executor_disabled", f"{executor_id} is disabled; the operator enables it in the catalog",
                            executor_id=executor_id)
        adapter = registry.resolve(executor_id)
        if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
            raise _conflict("executor_without_adapter", f"{executor_id} has no executor adapter registered",
                            executor_id=executor_id)
        with self._store.db.connect() as conn:
            until = self._paused_until(conn, executor_id)
        if until is not None:
            raise _conflict("executor_paused", f"{executor_id} is paused until {_iso(until)}",
                            executor_id=executor_id, until=_iso(until))
        declaration = (registry.config.get("executors", {}) or {}).get(executor_id, {})
        return descriptor, adapter, declaration if isinstance(declaration, Mapping) else {}

    def _routing_selection(self, decision: Mapping[str, Any] | None, model: str | None, effort: str | None, *,
                           executor_id: str, descriptor: Any, adapter: Any = None) -> ExecutorSelection:
        """Model and effort for the launch (ECO-T08): the caller's, else the routing decision's, within the executor's evidence.

        ``decision`` is the assignment decision (None when no profile was given: then only the caller's values apply).
        Its ``routing_decision`` is the model routing decision made for the selected executor, so the economic profile's
        choice (level, price evidence, mode) and the effort the profile sets for the step kind come with their
        evidence. ``model`` and ``effort`` are the caller's: they win and are returned as given. Anything taken from
        the profile that the executor does not declare it takes is dropped and said in the selection's ``notes``;
        it is the same function the assignment preview uses (``decision.evidence.executor_requests``).
        """
        route = decision.get("routing_decision") if decision else None
        return select_for_executor(executor_id, descriptor, route, requested_model=model, requested_effort=effort,
                                   adapter=adapter)

    def _plan(self, task_id: str, executor_id: str | None, model: str | None, effort: str | None,
              profile_id: str | None, operator_context: Any, step_kind: str | None = None) -> _Plan:
        decision: Mapping[str, Any] | None = None
        if profile_id is not None:
            request = {key: value for key, value in (("profile_id", profile_id), ("requested_executor", executor_id),
                                                     ("requested_model", model), ("requested_effort", effort),
                                                     ("step_kind", step_kind))
                       if value is not None}
            decision = self._services.assignment.preview(task_id, request, operator_context)["decision"]
            if decision.get("status") != "selected" or not decision.get("selected_executor"):
                raise _conflict("assignment_not_selected", "The assignment preview did not select an executor",
                                reason_codes=list(decision.get("reason_codes", [])), status=decision.get("status"))
            if executor_id is not None and decision["selected_executor"] != executor_id:
                raise _conflict("assignment_not_selected", "The assignment preview selected another executor",
                                selected=decision["selected_executor"])
            executor_id = decision["selected_executor"]
        if executor_id is None:
            raise _invalid("executor_id", "Give executor_id, or profile_id to let the assignment preview choose")
        descriptor, adapter, declaration = self._executor(executor_id)
        # What the caller named is checked against the executor's evidence and refused if it does not take it;
        # what the profile chose was already filtered by that evidence and is never an error.
        _check_support(descriptor.model_support, model, "model", executor_id)
        _check_support(descriptor.effort_support, effort, "effort", executor_id)
        selection = self._routing_selection(decision, model, effort, executor_id=executor_id, descriptor=descriptor,
                                            adapter=adapter)
        return _Plan(executor_id, descriptor, adapter, selection.model, selection.effort, declaration, decision,
                     selection)

    def _workdir(self, task_id: str, plan: _Plan) -> Path:
        """The folder the app works in: configured for the task or the executor, else one the framework creates."""
        catalog = getattr(self._services.system, "catalog", None)
        routing = getattr(catalog, "executor_routing", {}) or {}
        per_task = routing.get("task_workdirs", {}) if isinstance(routing, Mapping) else {}
        configured = per_task.get(task_id) if isinstance(per_task, Mapping) else None
        configured = configured or plan.declaration.get("workdir")
        if configured:
            try:
                return process.validate_cwd(str(configured))
            except process.ProcessLaunchError:
                raise _conflict("workdir_invalid", "The configured work folder is not an existing absolute folder",
                                executor_id=plan.executor_id) from None
        folder = self._services.settings.executor_workdir_root / f"task-{task_id}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _limit(self, plan: _Plan, name: str, default: int) -> int:
        value = plan.declaration.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    # -------- running --------
    @service_errors
    def run_task(self, task_id: str, executor_id: str | None = None, model: str | None = None,
                 effort: str | None = None, actor: str | None = None, *, profile_id: str | None = None,
                 timeout_seconds: int | None = None, operator_context: Any = None,
                 step_kind: str | None = None) -> dict[str, Any]:
        """Launch the task in an executor application in a new session; returns the outcome of the attempt.

        ``executor_id`` names the executor; without it ``profile_id`` makes the assignment preview choose (it needs a
        verified ``operator_context``). Only an enabled executor with an adapter runs, and not one paused by a quota
        stop. ``actor`` is the identity that claims, starts and submits (default ``executor:<executor_id>``); it is
        also the ``agent`` of the result. The task must be ``ready``, or claimed, paused or in ``needs_changes`` by
        that actor. ``model`` and ``effort`` are the caller's and always win; without them the routing decision of the
        assignment profile for ``step_kind`` (default ``work``) supplies them, within what the executor declares it
        takes (see ``_routing_selection``).

        Returns ``{outcome, run_status, task_id, executor_id, attempt, actor, session_id, open_target, model, effort,
        selection, permission, usage, command, evidence, notes, executor_paused_until, result, gate, task}``.
        ``selection`` says where ``model`` and ``effort`` came from (``user``, ``profile`` or none), what was not sent
        and why, and the routing evidence (profile, mode, step kind, level, price source, effort source). ``outcome`` is
        ``submitted`` (the result is stored and the gate decides) or what stopped the attempt: ``failed``,
        ``timeout``, ``cancelled``, ``quota_exhausted`` or ``blocked_open_session``; those leave the task ``paused``.
        """
        task_id = self._task_id(task_id)
        if executor_id is not None and (not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id)):
            raise _invalid("executor_id", f"Invalid executor_id: {executor_id!r}")
        model, effort = self._optional_token("model", model), self._optional_token("effort", effort)
        profile_id = self._optional_token("profile_id", profile_id)
        if step_kind is not None and step_kind not in STEP_KINDS:
            raise _invalid("step_kind", f"step_kind must be one of {', '.join(STEP_KINDS)}")
        task = self._tasks.get_task(task_id)
        plan = self._plan(task_id, executor_id, model, effort, profile_id, operator_context, step_kind)
        actor = _text("actor", actor if actor is not None else f"executor:{plan.executor_id}", maximum=100)
        timeout = self._timeout(timeout_seconds, self._limit(plan, "timeout_seconds",
                                                             self._services.settings.executor_timeout_seconds))
        return self._execute(task, plan, actor, timeout, resume_session=None)

    @service_errors
    def resume_task(self, task_id: str, model: str | None = None, effort: str | None = None,
                    actor: str | None = None, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        """Continue the session the framework created for the task, with the same executor.

        Only a session recorded by this service is continued, never one the GUI or ``open_target`` opened in an app
        in the last 12 hours. The task must be paused, claimed or in ``needs_changes`` (``actor`` defaults to its
        assignee). ``model`` and ``effort`` default to those of the recorded session. Returns the same shape as
        ``run_task``. Errors: ``no_stored_session``, ``session_open_in_app`` and the ones of ``run_task``.
        """
        task_id = self._task_id(task_id)
        model, effort = self._optional_token("model", model), self._optional_token("effort", effort)
        task = self._tasks.get_task(task_id)
        session = self._stored_session(task_id)
        if session is None:
            raise _conflict("no_stored_session", "No session of this task was created by the framework; "
                            "only those are resumed", task_id=task_id)
        session_id = str(session["session_id"])
        with self._store.db.connect() as conn:
            blocked = self._opened_until(conn, session_id)
        if blocked is not None:
            raise _conflict("session_open_in_app", "The session was opened in an app in the last 12 hours",
                            task_id=task_id, session_id=session_id, blocked_until=_iso(blocked))
        executor_id = str(session.get("executor_id", ""))
        descriptor, adapter, declaration = self._executor(executor_id)
        selection = select_for_resume(executor_id, model=model, effort=effort, recorded_model=session.get("model"),
                                      recorded_effort=session.get("effort"))
        _check_support(descriptor.model_support, selection.model, "model", executor_id)
        _check_support(descriptor.effort_support, selection.effort, "effort", executor_id)
        plan = _Plan(executor_id, descriptor, adapter, selection.model, selection.effort, declaration,
                     selection=selection)
        actor = _text("actor", actor or task.get("assignee") or f"executor:{executor_id}", maximum=100)
        timeout = self._timeout(timeout_seconds, self._limit(plan, "timeout_seconds",
                                                             self._services.settings.executor_timeout_seconds))
        return self._execute(task, plan, actor, timeout, resume_session=session_id)

    def _execute(self, task: dict[str, Any], plan: _Plan, actor: str, timeout: int, *,
                 resume_session: str | None) -> dict[str, Any]:
        task_id = task["id"]
        mode = "resume" if resume_session else "launch"
        permission = Permission.WRITE if task["file_scope"] else Permission.READ
        workdir = self._workdir(task_id, plan)
        request = ExecutorLaunchRequest(
            task_id=task_id, task_envelope=self._services.envelopes.get_envelope(task_id), workdir=workdir,
            model=plan.model, effort=plan.effort, permission=permission, timeout_seconds=timeout)
        self._preflight(plan, request, resume_session)
        cause = f"executor {mode} in {plan.executor_id}"
        view = self._enter_running(task, actor, cause)
        if plan.selection is not None:
            # What this attempt was asked to run with and why, kept even if the launch then fails.
            self._append(ROUTING_EVENT, actor=actor, task=view, ref=plan.executor_id, cause=cause,
                         payload=plan.selection.event_payload(attempt=view["attempt"], mode=mode))
        try:
            # Built again: the attempt number, the status and the assignee changed with the transitions above.
            request = replace(request, task_envelope=self._services.envelopes.get_envelope(task_id))
            _logger.info("executor %s %s of %s (attempt %s)", plan.executor_id, mode, task_id, view["attempt"])
            attempt = self._call_adapter(plan.adapter, request, resume_session)
        except BaseException:
            # Nothing ran or the run was interrupted: do not leave a task "running" with no program behind it.
            self._pause_task(task_id, actor, f"executor {mode} interrupted by an error")
            raise
        if attempt.session_id and attempt.session_id != resume_session:
            self._append(SESSION_EVENT, actor=actor, task=view, ref=attempt.session_id, cause=cause, payload={
                "executor_id": plan.executor_id, "adapter_id": plan.adapter.adapter_id, "attempt": view["attempt"],
                "mode": mode, "model": plan.model, "effort": plan.effort, "permission": permission.value,
                "model_source": plan.selection.model_source if plan.selection else None,
                "effort_source": plan.selection.effort_source if plan.selection else None})
        submission, problem = None, None
        if attempt.status == "completed" and attempt.envelope is not None:
            try:
                submission = self._services.envelopes.submit_result(task_id, attempt.envelope, actor=actor)
            except ServiceError as exc:
                problem = f"the result envelope was rejected ({exc.code}): {exc.message}"
        elif attempt.status == "completed":
            problem = "the run completed without a task result envelope"
        outcome = "submitted" if submission else (attempt.status if attempt.status in _FAILURE_OUTCOMES else "failed")
        evidence = ""
        if submission is None:
            evidence = _collapse(" | ".join(part for part in (problem, attempt.evidence) if part), MAX_EVIDENCE_CHARS)
        self._append(ATTEMPT_EVENT, actor=actor, task=view, cause=cause, ref=attempt.session_id or "", payload={
            "executor_id": plan.executor_id, "adapter_id": plan.adapter.adapter_id, "attempt": view["attempt"],
            "mode": mode, "run_status": attempt.status, "outcome": outcome, "session_id": attempt.session_id,
            "exit_code": attempt.exit_code, "model": plan.model, "effort": plan.effort,
            "model_source": plan.selection.model_source if plan.selection else None,
            "effort_source": plan.selection.effort_source if plan.selection else None,
            "permission": permission.value, "usage": dict(attempt.usage) if attempt.usage else None,
            "notes": attempt.notes, "evidence": evidence,
            "result_ref": submission["result"]["ref"] if submission else None})
        paused_until: str | None = None
        task_note: str | None = None
        if submission is None:
            if outcome == "quota_exhausted":
                paused_until = self._pause_executor(plan, view, actor)
            task_note = self._pause_task(task_id, actor, f"attempt {view['attempt']} {outcome}")
        notes = list(attempt.notes) + ([task_note] if task_note else [])
        target = plan.adapter.open_target(attempt.session_id) if attempt.session_id else None
        return {
            "outcome": outcome, "run_status": attempt.status, "task_id": task_id, "executor_id": plan.executor_id,
            "attempt": view["attempt"], "actor": actor, "mode": mode, "session_id": attempt.session_id,
            "open_target": _target_view(target) if target else None, "model": plan.model, "effort": plan.effort,
            "selection": plan.selection.to_dict() if plan.selection else None, "permission": permission.value, "usage": dict(attempt.usage) if attempt.usage else None,
            "command": attempt.command, "evidence": evidence or None, "notes": notes,
            "executor_paused_until": paused_until, "result": submission,
            "gate": self._services.gates.get_gate(task_id), "task": self._tasks.get_task(task_id)}

    def _preflight(self, plan: _Plan, request: ExecutorLaunchRequest, resume_session: str | None) -> None:
        """Build the command and find the program before the task changes: a refusal here leaves the task as it was."""
        try:
            if resume_session:
                argv = plan.adapter.build_resume_command(request, resume_session)
            else:
                argv = plan.adapter.build_command(replace(request, session_id=_PROBE_SESSION))
            process.check_launch(argv, cwd=request.workdir)
        except process.ProcessLaunchError as exc:
            if exc.code == process.INVALID_ARGV:
                raise _invalid("request", f"The executor refused the request: {_collapse(str(exc), 200)}",
                               executor_id=plan.executor_id, code=exc.code) from None
            raise _conflict("executor_unavailable", f"The program of {plan.executor_id} cannot be started ({exc.code})",
                            executor_id=plan.executor_id, code=exc.code) from None
        except (MultiAgentError, EnvelopeError, ValueError) as exc:
            raise _invalid("request", f"The executor refused the request: {_collapse(str(exc), 200)}",
                           executor_id=plan.executor_id, code=str(getattr(exc, "code", None) or getattr(exc, "field", "") or "")
                           ) from None

    def _enter_running(self, task: dict[str, Any], actor: str, cause: str) -> dict[str, Any]:
        """Take the task to ``running`` with the lifecycle's own transitions (the single function of ORQ-1)."""
        status = task["status"]
        steps = {"ready": ("claim", "start"), "claimed": ("start",), "needs_changes": ("start",),
                 "paused": ("resume",)}.get(status)
        if steps is None:
            raise _conflict("task_not_runnable", f"Task {task['id']} is {status}; a run needs a ready, claimed, paused "
                            "or needs_changes task", task_id=task["id"], status=status)
        view = task
        for action in steps:
            view = self._tasks.transition(task["id"], action, actor=actor, cause=cause,
                                          expected_revision=view["revision"])
        return view

    def _call_adapter(self, adapter: Any, request: ExecutorLaunchRequest, resume_session: str | None) -> _Attempt:
        try:
            result = adapter.resume(request, resume_session) if resume_session else adapter.launch(request)
        except (MultiAgentError, EnvelopeError, ValueError, OSError) as exc:
            code = str(getattr(exc, "code", None) or getattr(exc, "field", None) or type(exc).__name__)
            return _Attempt(status="launch_refused", notes=[f"launch_refused: {code}"],
                            evidence=f"The program was not started ({code}): {_collapse(str(exc), 240)}")
        notes = [_collapse(note, MAX_NOTE_CHARS) for note in result.notes][:MAX_NOTES]
        evidence = ""
        if result.status.value != "completed" or result.result_envelope is None:
            parts = [f"stderr: {_tail(result.stderr_tail, 400)}" if result.stderr_tail.strip() else "",
                     f"stdout: {_tail(result.stdout_tail, 400)}" if result.stdout_tail.strip() else "",
                     f"notes: {'; '.join(notes)}" if notes else ""]
            evidence = " | ".join(part for part in parts if part)
        return _Attempt(status=result.status.value, session_id=result.session_id, envelope=result.result_envelope,
                        usage=result.usage, command=_command_view(result.command), exit_code=result.command.exit_code,
                        notes=notes, evidence=evidence)

    def _pause_executor(self, plan: _Plan, view: Mapping[str, Any], actor: str) -> str:
        minutes = self._limit(plan, "quota_pause_minutes", self._services.settings.executor_quota_pause_minutes)
        until = _iso(self.clock() + timedelta(minutes=minutes))
        self._append(PAUSE_EVENT, actor=actor, task=view, ref=plan.executor_id, cause="quota or limit reached",
                     payload={"executor_id": plan.executor_id, "until": until, "minutes": minutes,
                              "reason": "quota_exhausted"})
        return until

    def _pause_task(self, task_id: str, actor: str, cause: str) -> str | None:
        """Pause the task after a failed attempt; a task that moved on meanwhile is left alone and said so."""
        try:
            self._tasks.transition(task_id, "pause", actor=actor, cause=cause)
        except ServiceError as exc:
            return f"task_not_paused: {exc.code}"
        return None

    # -------- opening and status --------
    @service_errors
    def open_target(self, task_id: str, actor: str | None = None) -> dict[str, Any]:
        """The adapter's open target for the task's recorded session, and the record of that open.

        The target is the one the GUI uses (``claude --desktop --resume <id>``, ``codex://threads/<id>``,
        ``ghapp://sessions/<id>``). Handing it out is treated as an open: ``session.opened_in_app`` is recorded and
        ``resume_task`` refuses the session for 12 hours (``resume_blocked_until``). Errors: ``no_stored_session``.
        """
        task_id = self._task_id(task_id)
        actor = _text("actor", actor if actor is not None else "user", maximum=100)
        self._tasks.get_task(task_id)
        session = self._stored_session(task_id)
        if session is None:
            raise _conflict("no_stored_session", "No session of this task was recorded by the framework", task_id=task_id)
        executor_id = str(session.get("executor_id", ""))
        adapter = self.registry.resolve(executor_id)
        if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
            raise _conflict("executor_without_adapter", f"{executor_id} has no executor adapter registered",
                            executor_id=executor_id)
        target = adapter.open_target(str(session["session_id"]))
        if target is None:
            raise _conflict("invalid_open_target", "The recorded session has no valid open target",
                            task_id=task_id, session_id=session["session_id"])
        source = str(session.get("adapter_id") or executor_id).removesuffix("_cli")
        opened = self.record_app_open(str(session["session_id"]), source=source, actor=actor)
        return {"task_id": task_id, "executor_id": executor_id, "session_id": session["session_id"],
                "target": _target_view(target), "resume_blocked_until": opened["resume_blocked_until"]}

    @service_errors
    def record_app_open(self, session_id: str, *, source: str, actor: str = "gui") -> dict[str, Any]:
        """Record that a session was opened in an app: ``resume_task`` refuses it for the next 12 hours.

        Called by the GUI when it launches Claude (GUI-T06) and by ``open_target``. The record goes in the team event
        log (``session.opened_in_app``) with the task of the session when the framework created it.
        """
        if not is_uuid(session_id):
            raise _invalid("session_id", "session_id must be a UUID")
        if not isinstance(source, str) or not _SOURCE.fullmatch(source):
            raise _invalid("source", "source must be a short lowercase name such as claude, codex or copilot")
        actor = _text("actor", actor, maximum=100)
        sid, now = session_id.lower(), self.clock()
        with self._store.transaction() as conn:
            stored = self._latest(conn, SESSION_EVENT, ref=sid)
            self._store.append_event(conn, OPEN_EVENT, actor=actor, group_id=stored["group_id"] if stored else "",
                                     task_id=stored["task_id"] if stored else "", ref=sid, cause="opened in the app",
                                     payload={"source": source, "opened_at": _iso(now)})
        return {"session_id": sid, "source": source, "resume_blocked_until": _iso(now + APP_OPEN_GUARD)}

    @service_errors
    def status(self, task_id: str | None = None) -> dict[str, Any]:
        """The executors that have an adapter (enabled, paused until) and, with ``task_id``, that task's sessions and attempts."""
        registry = self.registry
        executors: list[dict[str, Any]] = []
        with self._store.db.connect() as conn:
            for executor_id, descriptor in sorted(registry.snapshot().items()):
                adapter = registry.resolve(executor_id)
                if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
                    continue
                until = self._paused_until(conn, executor_id)
                executors.append({"executor_id": executor_id, "surface": descriptor.surface,
                                  "adapter_id": adapter.adapter_id, "mode": descriptor.mode,
                                  "enabled": descriptor.enabled, "paused_until": _iso(until) if until else None,
                                  "available": descriptor.enabled is True and until is None})
        result: dict[str, Any] = {"executors": executors}
        if task_id is not None:
            task_id = self._task_id(task_id)
            task = self._tasks.get_task(task_id)
            sessions: list[dict[str, Any]] = []
            attempts: list[dict[str, Any]] = []
            routings: list[dict[str, Any]] = []
            with self._store.db.connect() as conn:
                for row in conn.execute("SELECT * FROM team_events WHERE task_id=? AND type IN (?,?,?) ORDER BY seq",
                                        (task_id, SESSION_EVENT, ATTEMPT_EVENT, ROUTING_EVENT)):
                    event = self._row(row)
                    if event["type"] == SESSION_EVENT:
                        sessions.append({"session_id": event["ref"], "created_at": event["created_at"], **event["payload"]})
                    elif event["type"] == ROUTING_EVENT:
                        routings.append({"seq": event["seq"], "created_at": event["created_at"], **event["payload"]})
                    else:
                        attempts.append({"seq": event["seq"], "created_at": event["created_at"], **event["payload"]})
                blocked = None
                if sessions:
                    until = self._opened_until(conn, sessions[-1]["session_id"])
                    blocked = _iso(until) if until else None
            result["task"] = {"task_id": task_id, "state": task["state"], "status": task["status"],
                              "attempt": task["attempt"], "assignee": task["assignee"], "sessions": sessions,
                              "attempts": attempts, "routings": routings, "resume_blocked_until": blocked}
        return result
