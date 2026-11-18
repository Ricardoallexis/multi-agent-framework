"""Isolated, durable runtime shared by backend conformance cases."""
from __future__ import annotations

from pathlib import Path
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from multiagent.adapters.fake import FakeAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import BackendContext, build_backend
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.model_router import ModelRouter
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.worker import LocalWorker


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "backend_conformance"


class StepGate:
    """Thread-safe barrier that pauses selected runs when logging a target step event."""

    def __init__(self, *, event_type: str = "step_started", step_id: str = "first", timeout: float = 10.0):
        self.event_type = event_type
        self.step_id = step_id
        self.timeout = timeout
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._hold_all = False
        self._held: dict[str, tuple[threading.Event, threading.Event]] = {}
        self._entered: list[str] = []
        self._events: list[tuple[str, str, dict[str, Any]]] = []

    def hold(self, *run_ids: str) -> None:
        with self._lock:
            for run_id in run_ids:
                self._held.setdefault(run_id, (threading.Event(), threading.Event()))

    def hold_all(self) -> None:
        with self._lock:
            self._hold_all = True

    def release(self, *run_ids: str) -> None:
        with self._lock:
            for run_id in run_ids:
                pair = self._held.get(run_id)
                if pair is not None:
                    pair[1].set()

    def release_all(self) -> None:
        with self._lock:
            self._hold_all = False
            for _, rel in self._held.values():
                rel.set()
            self._condition.notify_all()

    def entered_runs(self) -> list[str]:
        with self._lock:
            return list(self._entered)

    def recorded_events(self) -> list[tuple[str, str, dict[str, Any]]]:
        with self._lock:
            return list(self._events)

    def wait_entered(self, run_id: str, *, timeout: float | None = None) -> None:
        with self._lock:
            pair = self._held.setdefault(run_id, (threading.Event(), threading.Event()))
            entered = pair[0]
        limit = self.timeout if timeout is None else timeout
        assert entered.wait(limit), f"Run {run_id} did not reach {self.event_type}({self.step_id}) within {limit:.1f}s"

    def wait_count(self, count: int, *, timeout: float | None = None) -> list[str]:
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        with self._condition:
            while len(self._entered) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"Only {len(self._entered)} of {count} runs reached {self.event_type}({self.step_id}) "
                        f"within {limit:.1f}s"
                    )
                self._condition.wait(remaining)
            return list(self._entered)

    def maybe_wait(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._events.append((run_id, event_type, dict(payload)))
            if event_type != self.event_type or payload.get("step_id") != self.step_id:
                return
            self._entered.append(run_id)
            self._condition.notify_all()
            pair = self._held.get(run_id)
            if pair is None and self._hold_all:
                pair = (threading.Event(), threading.Event())
                self._held[run_id] = pair
            if pair is None:
                return
            entered, release = pair
            entered.set()
        if not release.wait(self.timeout):
            raise TimeoutError(f"Timed out waiting to release {run_id} at {event_type}({self.step_id})")


class BlockingDatabase(Database):
    """Database wrapper that notifies a ``StepGate`` after committing an event."""

    def __init__(self, settings: Settings, gate: StepGate | None = None):
        super().__init__(settings)
        self.gate = gate

    def log_event(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> int | None:
        event_id = super().log_event(run_id, event_type, payload)
        if self.gate is not None:
            self.gate.maybe_wait(run_id, event_type, payload or {})
        return event_id


class BlockingStore(Store):
    """Store subclass backed by ``BlockingDatabase`` so all backends share one wait point."""

    def __init__(self, settings: Settings, gate: StepGate | None = None):
        super().__init__(BlockingDatabase(settings, gate=gate))


def _build_runtime(name: str, settings: Settings, *, gate: StepGate, migrate: bool) -> SimpleNamespace:
    store = BlockingStore(settings, gate=gate)
    if migrate:
        store.db.migrate()
    bundle = load_bundle(FIXTURES, settings)
    adapter = FakeAdapter(output_schemas=bundle.output_schemas)
    router = ModelRouter(bundle.catalog, {"fake": adapter}, dry_run=True)
    sample = adapter.generate_structured(
        spec=None, prompt="", output_schema=bundle.output_schemas["ContentOutput"],
    ).parsed.model_dump(mode="json")
    context = BackendContext(
        store=store, settings=settings, workflows=bundle.workflows,
        prompts=bundle.prompts, catalog=bundle.catalog,
        artifacts=ArtifactWriter(settings), router=router,
        output_schemas=bundle.output_schemas,
        sample_outputs={"ContentOutput": sample},
    )
    backend = build_backend(name, context)
    return SimpleNamespace(
        name=name, settings=settings, store=store, backend=backend,
        service=RunService(settings, store, engine=backend),
        worker=LocalWorker(store, backend), adapter=adapter, gate=gate,
    )


@pytest.fixture
def backend_system(tmp_path, request):
    """Build the named backend with fresh storage and only a FakeAdapter."""
    name = request.node.callspec.params["system"]
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
        gemini_api_key="", openai_api_key="", openai_enabled=False,
    )
    settings.ensure_directories()
    return _build_runtime(name, settings, gate=StepGate(), migrate=True)


def open_peer(system: SimpleNamespace) -> SimpleNamespace:
    """Build a second Store + backend sharing the same SQLite database and StepGate."""
    return _build_runtime(system.name, system.settings, gate=system.gate, migrate=False)


def wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.02,
               message: str = "Timed out waiting for condition"):
    """Poll ``predicate`` until it returns a truthy value or ``timeout`` expires."""
    deadline = time.monotonic() + timeout
    pause = threading.Event()
    while True:
        value = predicate()
        if value:
            return value
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"{message} (after {timeout:.1f}s)")
        pause.wait(min(interval, remaining))


def stop_workers(system: SimpleNamespace, *workers: LocalWorker) -> None:
    """Release any blocked runs, stop workers and verify all threads exited."""
    if getattr(system, "gate", None) is not None:
        system.gate.release_all()
    for worker in workers:
        worker.stop()
    for worker in workers:
        if worker._thread is not None:
            worker._thread.join(timeout=5)
            assert not worker._thread.is_alive(), f"Worker thread {worker.worker_id} did not stop"
        for thread in list(worker._active.values()):
            thread.join(timeout=5)
            assert not thread.is_alive(), f"Run thread on {worker.worker_id} did not stop"


def create_run(system, workflow_id="two_steps", project_name="Conformance fixture", **overrides):
    return system.service.create_run(RunRequest(
        workflow_id=workflow_id, project_name=project_name,
        inputs={"topic": "durable execution"}, **overrides,
    ))


def advance(system, run_id):
    assert system.worker.process_once(), "the queued run was not picked up"
    return system.store.get_run(run_id)


def events(run, kind):
    return [event for event in run["events"] if event["event_type"] == kind]


def steps(system, run_id):
    with system.store.db.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM run_steps WHERE run_id=? ORDER BY rowid", (run_id,),
        )]


def usage(system, run_id):
    with system.store.db.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM telemetry WHERE run_id=? ORDER BY id", (run_id,),
        )]
