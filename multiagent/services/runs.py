# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run services: create, inspect and steer runs.

Contract fixed by G4a-T01; implemented by G4a-T03 by delegating to
``RunService`` without duplicating its rules. Every method returns JSON-ready
data (the run dict of ``RunService`` plus ``"bundle"``, unless stated) and
raises only ``ServiceError`` for known failures: ``not_found`` for an unknown
run, ``invalid_state``/``invalid_operation``/``budget_exceeded`` when the
run's state does not allow the operation.

Runs of built-in workflows live in the system's store. A bundle engine only
knows its own workflows (``WorkflowEngine.from_bundle``), so each registered
bundle gets its own runtime, created on first use: a store under
``<data_dir>/bundles/<name>/``, an engine, a ``RunService`` and a worker that
starts when ``settings.worker_enabled`` is true. Run ids are unique, so the
per-id methods find the run in whichever store holds it.
"""
from __future__ import annotations

import threading
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..artifacts import ArtifactWriter
from ..bundles import load_bundle
from ..contracts import HumanStepSubmission, SocialPostRequest
from ..db.database import Database
from ..db.store import Store
from ..errors import HumanSubmissionError
from ..observability.usage import read_usage, summarize_usage
from ..run_request import RunRequest
from ..run_service import PersonRequired, RunService
from ..tools.approvals import ToolApprovalMismatch
from ..tools.contracts import PermissionRequest
from ..worker import LocalWorker
from ..workflow_engine import WorkflowEngine
from .errors import HUMAN_STEP_UNAVAILABLE, INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices


@dataclass
class _Runtime:
    bundle: str | None
    store: Store
    service: RunService
    worker: LocalWorker | None = None


_SHOWN_ACTIONS = 20


def _visible(text: Any) -> str:
    """Text for a person who decides on it: C0/C1 controls, bidi marks and other format, separator and private-use
    characters (categories Cc, Cf, Zl, Zp, Co) are removed, so a destination cannot disguise itself (CAP-T14 B2)."""
    return "".join(ch for ch in str(text) if unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp", "Co"))


def pending_tool_approval(run: dict[str, Any]) -> dict[str, Any] | None:
    """The permission request a run waits on (``waiting_reason`` ``tool_approval``), from its persisted events.

    Same reading as ``Store.finish_tool_approval``: the last ``tool_approval_requested`` of the waiting step and attempt
    that no later approval or rejection settled. Only summary, actions, destinations and ids come out: never raw
    arguments (the request holds none) nor model text."""
    if run.get("status") != "waiting_human" or run.get("waiting_reason") != "tool_approval":
        return None
    pending = None
    for event in run.get("events") or []:
        kind = event.get("event_type")
        if kind == "tool_approval_requested":
            try:
                candidate = PermissionRequest.model_validate(event.get("payload"))
            except Exception:
                pending = None
                continue
            pending = candidate if (candidate.context.run_id == run["id"] and candidate.context.step_id == run.get("waiting_step")
                                    and candidate.context.attempt == run.get("waiting_attempt")) else None
        elif kind in ("tool_approval_approved", "tool_approval_rejected"):
            pending = None
    if pending is None:
        return None
    omitted = max(0, len(pending.actions) - _SHOWN_ACTIONS)
    return {"permission_request_id": pending.permission_request_id, "tool_id": pending.tool_id,
            "tool_version": pending.tool_version, "step_id": pending.context.step_id,
            "attempt": pending.context.attempt, "summary": _visible(pending.summary)[:300],
            "impacts": sorted(impact.value for impact in pending.impacts),
            "actions": [{"kind": ref.kind.value, "target": _visible(ref.target) if ref.target is not None else None}
                        for ref in pending.actions[:_SHOWN_ACTIONS]],
            "actions_total": len(pending.actions),
            "actions_omitted": f"+{omitted} más (total {len(pending.actions)})" if omitted else "",
            "arguments_sha256": pending.arguments_sha256}


class RunServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._lock = threading.Lock()
        self._bundle_runtimes: dict[str, _Runtime] = {}

    @property
    def _system_runtime(self) -> _Runtime:
        system = self._services.system
        return _Runtime(None, system.store, system.run_service)

    # --- runtimes -----------------------------------------------------------

    def _bundle_settings(self, name: str):
        data_dir = self._services.settings.data_dir / "bundles" / name
        return self._services.settings.model_copy(update={
            "data_dir": data_dir,
            "db_path": data_dir / "multiagent.db",
            "runs_dir": data_dir / "runs",
            "assets_dir": data_dir / "assets",
        })

    def _bundle_runtime(self, name: str, *, create: bool = True) -> _Runtime | None:
        """Runtime of a registered bundle; ``None`` if ``create`` is false and it has no store yet."""
        path = self._services.bundle_path(name)  # not_found for unknown bundles
        with self._lock:
            runtime = self._bundle_runtimes.get(name)
            if runtime is not None:
                return runtime
            settings = self._bundle_settings(name)
            if not create and not settings.db_path.exists():
                return None
            system = self._services.system
            bundle = load_bundle(path, settings)
            settings.ensure_directories()
            database = Database(settings)
            database.migrate()
            store = Store(database)
            # The document policy is the system engine's (BDI-T04): one source of truth for the screening of the
            # context, the content guard of these runs and the gates of effects (``ExecutorServices`` reads the same
            # value). It is read when the runtime is built, so it is the policy in force from then on.
            engine = WorkflowEngine.from_bundle(
                bundle, store=store, artifacts=ArtifactWriter(settings), settings=settings,
                adapters=system.adapters, dry_run=system.engine.router.dry_run,
                document_policy=system.engine.document_policy,
            )
            inbox = getattr(self._services, "inbox", None)
            if inbox is not None:
                engine.availability_source = inbox.policy          # one place decides whether the inbox is in use (AVA-T03)
            usage = getattr(self._services, "usage", None)
            if usage is not None:
                usage.attach(engine)                               # the same budget guard and alerts as the system engine (USO-T04)
            runtime = _Runtime(name, store, RunService(settings, store, engine=engine),
                               LocalWorker(store, engine, settings.worker_poll_seconds))
            if settings.worker_enabled:
                runtime.worker.start()
            self._bundle_runtimes[name] = runtime
            return runtime

    def _runtimes(self):
        """System runtime, then every bundle that has runs (opening each store on demand)."""
        yield self._system_runtime
        for name in self._services.bundle_names():
            runtime = self._bundle_runtime(name, create=False)
            if runtime is not None:
                yield runtime

    def _find(self, run_id: str) -> _Runtime:
        for runtime in self._runtimes():
            try:
                runtime.store.get_run(run_id)
            except KeyError:
                continue
            return runtime
        raise ServiceError(NOT_FOUND, f"Run not found: {run_id}", status=404)

    @staticmethod
    def _tag(runtime: _Runtime, run: dict[str, Any]) -> dict[str, Any]:
        return {**run, "bundle": runtime.bundle}

    def close(self) -> None:
        """Stop the workers of bundle runtimes (the system worker belongs to its owner)."""
        with self._lock:
            for runtime in self._bundle_runtimes.values():
                if runtime.worker is not None:
                    runtime.worker.stop()

    # --- operations ---------------------------------------------------------

    @service_errors
    def create(self, request: RunRequest | SocialPostRequest, *, bundle: str | None = None) -> dict[str, Any]:
        """Validate the workflow and queue a run; returns the new (or idempotent) run.

        ``bundle`` selects a registered bundle for ``request.workflow_id``.
        A generic request may carry ``task_id``: the task must exist in the system database (a bundle's runs live in
        another one) and the link never changes; usage and telemetry of the run are then counted for that task.
        Errors: ``not_found`` (workflow, bundle or task), ``invalid_definition``, ``idempotency_conflict``.
        """
        task_id = getattr(request, "task_id", None)
        if task_id is not None and self._services.tasks.store.get_task(task_id) is None:
            raise ServiceError(NOT_FOUND, f"Task not found: {task_id}", status=404, details={"task": task_id})
        runtime = self._system_runtime if bundle is None else self._bundle_runtime(bundle)
        try:
            return self._tag(runtime, runtime.service.create_run(request))
        except ValueError as exc:
            if getattr(request, "routing_preferences", None):
                raise ServiceError(INVALID_REQUEST, str(exc), status=422) from exc
            raise

    @service_errors
    def get(self, run_id: str) -> dict[str, Any]:
        """Run with its events and artifacts.

        A run waiting for a tool approval also carries ``tool_approval``: the pending permission request as the persisted
        event states it, with bounded fields only (never the call's arguments, only their hash). Absent otherwise."""
        runtime = self._find(run_id)
        run = self._tag(runtime, runtime.service.get(run_id))
        pending = pending_tool_approval(run)
        if pending is not None:
            run["tool_approval"] = pending
        return run

    @service_errors
    def list(self, *, limit: int = 20, status: str = "", project: str = "") -> list[dict[str, Any]]:
        """Most recent runs of every store, optionally filtered by status and project."""
        runs = [
            self._tag(runtime, run)
            for runtime in self._runtimes()
            for run in runtime.service.list(limit=limit, status=status, project=project)
        ]
        runs.sort(key=lambda run: run.get("created_at") or "", reverse=True)
        return runs[:max(1, min(int(limit), 500))]

    @service_errors
    def approve(self, run_id: str, *, by: str | None = None, channel: str | None = None,
                recorded_only: bool = False, permission_request_id: str | None = None,
                person: bool = False) -> dict[str, Any]:
        """Approve the artifact under review and continue or complete the run.

        A run paused on a supervised escalation (``waiting_reason`` ``escalation_approval``) has no artifact
        under review: approving it queues the run so the step resumes with the escalated model. A run held by the
        content guard (``waiting_reason`` ``content_review``, policy ``pause``) has none either: approving it settles
        the open reviews (``document_review_resolved``) and queues the run, which goes on with the quarantined
        prompt it had. A run held by the budget guard (``waiting_reason`` ``budget_cut_approval``, USO-T04) has no artifact
        either: approving it records ``budget_cut_approved`` and queues the run, and the resumed step makes that one call
        (the approval is for one call and is spent by it). ``by`` and ``channel`` (``api`` by default, ``gui`` from the GUI
        router) are recorded with that resolution as the caller declared them. Known limit (BDI-T07): this method does not
        authenticate its caller, so any local process that reaches the run API can settle a content review.

        ``recorded_only`` is for a caller nobody authenticated (``/api/v1``, which has no token): it cannot approve a
        budget cut, because that spends money. The error is ``invalid_state`` with ``details.reason`` ``person_required``
        and the run stays waiting; the token route of the GUI and the command line approve it. Rejecting needs no such
        flag: a refusal spends nothing.

        A run waiting for a tool approval (``tool_approval``, CAP-T14) is approved only by naming the
        ``permission_request_id`` the person was shown (``get`` returns it as ``tool_approval``); a missing, old or
        other id is ``invalid_state`` with ``code`` ``permission_request_mismatch`` and nothing is approved. A caller
        nobody authenticated (``recorded_only``), or one that does not pass ``person=True``, never approves it
        (``PersonRequired``, CAP-T15 B1); only the token route of the GUI passes it.
        """
        runtime = self._find(run_id)
        try:
            return self._tag(runtime, runtime.service.approve(run_id, by=by, channel=channel, recorded_only=recorded_only,
                                                              permission_request_id=permission_request_id,
                                                              person=person))
        except ToolApprovalMismatch as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409,
                               details={"reason": exc.code, "run_id": run_id}) from exc
        except PersonRequired as exc:
            raise ServiceError(INVALID_STATE, str(exc), status=409,
                               details={"reason": "person_required", "run_id": run_id}) from exc

    @service_errors
    def request_changes(self, run_id: str, feedback: str) -> dict[str, Any]:
        """Send the reviewed step back with feedback."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.changes(run_id, feedback))

    @service_errors
    def regenerate(self, run_id: str) -> dict[str, Any]:
        """Repeat the reviewed step without feedback."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.changes(run_id, "", regenerate=True))

    @service_errors
    def reject(self, run_id: str, feedback: str = "", *, by: str | None = None,
               channel: str | None = None) -> dict[str, Any]:
        """Reject the run while it waits for a human.

        Rejecting a paused escalation fails the step and the run (status ``failed``) with the reason. Rejecting a
        run held for a content review rejects it (status ``rejected``) and records the resolution (``by``, ``channel``
        as in ``approve``). Rejecting a run held by the budget guard fails it (status ``failed``): the call is never made.
        """
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.reject(run_id, feedback, by=by, channel=channel))

    @service_errors
    def cancel(self, run_id: str, *, force: bool = False) -> dict[str, Any]:
        """Cancel the run (or request cancellation if it is running and not forced)."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.cancel(run_id, force=force))

    @service_errors
    def human_next(self, run_id: str) -> dict[str, Any]:
        """Pending human-guided step (prompt and expected contract).

        Error ``human_step_unavailable`` when the run has none.
        """
        runtime = self._find(run_id)
        try:
            pending = runtime.service.human_next(run_id)
        except HumanSubmissionError as exc:
            raise ServiceError(HUMAN_STEP_UNAVAILABLE, str(exc), status=409) from exc
        return {**pending, "bundle": runtime.bundle}

    @service_errors
    def human_submit(self, run_id: str, submission: HumanStepSubmission) -> dict[str, Any]:
        """Submit the response of a human or external model for the pending step.

        Error ``human_submission_invalid`` when the response is rejected.
        """
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.human_submit(run_id, submission))

    @service_errors
    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Artifacts of the run, oldest first."""
        return self.get(run_id)["artifacts"]

    @service_errors
    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        """Events of the run whose ``seq`` is greater than ``after``, oldest first.

        ``seq`` numbers a run's events 1, 2, 3... without gaps, so a client that
        sees a gap, a repeat, or a lower number than it already has knows its
        copy is stale and reloads the run.
        Errors: ``not_found``, ``invalid_request`` (``after`` is not an integer >= 0).
        """
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ServiceError(INVALID_REQUEST, "after must be an integer >= 0", status=422,
                               details={"after": repr(after)})
        return [event for event in self.get(run_id)["events"] if event["seq"] > after]

    @service_errors
    def usage(self, run_id: str) -> dict[str, Any]:
        """Token usage of the run: every record plus totals per run, agent, step and model.

        Tokens a provider did not report stay ``None`` and are counted in
        ``records_without_usage`` instead of adding 0. Costs are estimates
        (``cost_is_estimate``), never billed amounts: tokens x the catalog price
        evidence in force at the call, or ``None`` when either is missing. Totals
        keep unknown costs apart (``estimated_cost_usd`` is the known subtotal,
        ``cost_unknown_records`` how many calls are missing from it,
        ``cost_complete`` whether it is the whole cost). Error: ``not_found``.
        """
        runtime = self._find(run_id)
        records = read_usage(runtime.store, run_id)
        return {
            "run_id": run_id,
            "bundle": runtime.bundle,
            "cost_is_estimate": True,
            "records": records,
            "totals": summarize_usage(records),
        }

    @service_errors
    def report(self, run_id: str, *, format: str = "json", include_raw: bool = False) -> dict[str, Any] | str:
        """Export a run as JSON-ready data or Markdown. Raw payloads require explicit opt-in."""
        from .reports import build_run_report, render_run_report_markdown

        if format not in ("json", "markdown"):
            raise ServiceError(INVALID_REQUEST, "format must be json or markdown", status=422)
        report = build_run_report(self.get(run_id), self.usage(run_id), include_raw=include_raw)
        return report if format == "json" else render_run_report_markdown(report)
