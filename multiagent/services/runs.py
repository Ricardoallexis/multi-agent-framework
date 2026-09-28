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
from ..run_service import RunService
from ..worker import LocalWorker
from ..workflow_engine import WorkflowEngine
from .errors import HUMAN_STEP_UNAVAILABLE, INVALID_REQUEST, NOT_FOUND, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices


@dataclass
class _Runtime:
    bundle: str | None
    store: Store
    service: RunService
    worker: LocalWorker | None = None


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
            engine = WorkflowEngine.from_bundle(
                bundle, store=store, artifacts=ArtifactWriter(settings), settings=settings,
                adapters=system.adapters, dry_run=system.engine.router.dry_run,
            )
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
        Errors: ``not_found``, ``invalid_definition``, ``idempotency_conflict``.
        """
        runtime = self._system_runtime if bundle is None else self._bundle_runtime(bundle)
        return self._tag(runtime, runtime.service.create_run(request))

    @service_errors
    def get(self, run_id: str) -> dict[str, Any]:
        """Run with its events and artifacts."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.get(run_id))

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
    def approve(self, run_id: str) -> dict[str, Any]:
        """Approve the artifact under review and continue or complete the run."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.approve(run_id))

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
    def reject(self, run_id: str, feedback: str = "") -> dict[str, Any]:
        """Reject the run while it waits for a human."""
        runtime = self._find(run_id)
        return self._tag(runtime, runtime.service.reject(run_id, feedback))

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
        """Token usage of the run: every record plus totals per run, agent and step.

        Tokens a provider did not report stay ``None`` and are counted in
        ``records_without_usage`` instead of adding 0. Costs are estimates from
        the model catalog, never billed amounts. Error: ``not_found``.
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
