# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Durable worker claims, leases and in-process concurrency."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from multiagent.cli import build_parser
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.worker import LocalWorker


NOW = "2026-11-18T15:00:00+00:00"
LATER = "2026-11-18T15:01:00+00:00"


def stores(tmp_path):
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "runs.sqlite", worker_enabled=False)
    first = Store(Database(settings))
    first.db.migrate()
    return first, Store(Database(settings))


def queued(store, project="one"):
    return store.create_run(
        SocialPostRequest(project_name=project, objective="Create content", topic="worker test"),
        max_llm_calls=4, max_run_seconds=300,
    )["id"]


class BlockingEngine:
    def __init__(self, store, *, expected=1):
        self.store = store
        self.expected = expected
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = []

    def check_approval_deadlines(self):
        return 0

    def process_run(self, run_id):
        self.store.update_run(run_id, status=RunStatus.RUNNING.value)
        with self._lock:
            self.calls.append(run_id)
            if len(self.calls) >= self.expected:
                self.started.set()
        assert self.release.wait(10), "test did not release the engine"
        run = self.store.get_run(run_id)
        status = RunStatus.CANCELLED.value if run["cancel_requested"] else RunStatus.COMPLETED.value
        self.store.update_run(run_id, status=status)


def test_two_stores_claim_one_run_once(tmp_path):
    first, second = stores(tmp_path)
    run_id = queued(first)
    gate = threading.Barrier(2)

    def compete(store, owner):
        gate.wait()
        return store.claim_queued_run(owner, lease_seconds=30, now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(compete, first, "first")
        b = pool.submit(compete, second, "second")
        winners = [result for result in (a.result(), b.result()) if result is not None]
    assert len(winners) == 1
    assert winners[0]["id"] == run_id
    assert first.get_run(run_id)["worker_id"] in {"first", "second"}


def test_two_workers_execute_a_run_only_once(tmp_path):
    first, second = stores(tmp_path)
    run_id = queued(first)
    left, right = BlockingEngine(first), BlockingEngine(second)
    workers = [LocalWorker(first, left, 0.06), LocalWorker(second, right, 0.06)]
    for worker in workers:
        worker.start()
    try:
        assert left.started.wait(5) or right.started.wait(5)
        assert left.calls + right.calls == [run_id]
        left.release.set()
        right.release.set()
        for worker in workers:
            worker.stop()
        assert first.get_run(run_id)["status"] == "completed"
    finally:
        left.release.set()
        right.release.set()
        for worker in workers:
            worker.stop()


def test_expired_run_recovers_but_live_run_survives(tmp_path):
    first, second = stores(tmp_path)
    dead, live = queued(first), queued(first)
    first.update_run(dead, status="running", worker_id="dead", heartbeat_at=NOW)
    first.update_run(live, status="running", worker_id="live", heartbeat_at=LATER)
    recovered = second.recover_running_runs("new", lease_seconds=30, now=LATER)
    assert recovered == [dead]
    assert second.get_run(dead)["status"] == "queued"
    assert second.get_run(live)["status"] == "running"
    assert second.get_run(live)["worker_id"] == "live"
    assert [e["event_type"] for e in second.get_run(dead)["events"]].count("run_interrupted") == 1
    assert not any(e["event_type"] == "run_interrupted" for e in second.get_run(live)["events"])


def test_active_worker_renews_lease_during_blocked_execution(tmp_path):
    first, second = stores(tmp_path)
    run_id = queued(first)
    clock = [NOW]
    renewed = threading.Event()
    original_renew = first.renew_lease

    def observe_renew(*args, **kwargs):
        result = original_renew(*args, **kwargs)
        if kwargs.get("now") == LATER:
            renewed.set()
        return result

    first.renew_lease = observe_renew
    engine = BlockingEngine(first)
    worker = LocalWorker(first, engine, 0.06, lease_seconds=30, clock=lambda: clock[0])
    worker.start()
    try:
        assert engine.started.wait(5)
        clock[0] = LATER
        assert renewed.wait(5)
        assert second.recover_running_runs("other", lease_seconds=30, now=LATER) == []
        assert second.get_run(run_id)["worker_id"] == worker.worker_id
    finally:
        engine.release.set()
        worker.stop()


def test_global_and_project_limits_use_shared_claim_transaction(tmp_path):
    first, second = stores(tmp_path)
    a, b, c = queued(first, "one"), queued(first, "one"), queued(first, "two")
    assert first.claim_queued_run("first", lease_seconds=30, max_active_total=1, now=NOW)["id"] == a
    assert second.claim_queued_run("second", lease_seconds=30, max_active_total=1, now=NOW) is None
    assert second.claim_queued_run(
        "second", lease_seconds=30, max_active_per_project=1, now=NOW,
    )["id"] == c
    assert first.claim_queued_run(
        "first", lease_seconds=30, max_active_per_project=1, now=NOW,
    ) is None
    assert first.get_run(b)["worker_id"] in ("", None)


def test_three_runs_execute_together_and_shutdown_waits(tmp_path):
    first, _ = stores(tmp_path)
    ids = {queued(first) for _ in range(3)}
    engine = BlockingEngine(first, expected=3)
    worker = LocalWorker(first, engine, 0.06, concurrency=3, lease_seconds=30,
                         shutdown_wait_seconds=3)
    worker.start()
    try:
        assert engine.started.wait(5)
        assert set(engine.calls) == ids
        done = threading.Event()
        stopper = threading.Thread(target=lambda: (worker.stop(), done.set()))
        stopper.start()
        assert worker._stop.wait(1)
        assert not done.is_set()
        engine.release.set()
        assert done.wait(5)
        stopper.join()
        assert all(first.get_run(run_id)["status"] == "completed" for run_id in ids)
    finally:
        engine.release.set()
        worker.stop()


def test_shutdown_timeout_leaves_lease_for_later_recovery(tmp_path):
    first, second = stores(tmp_path)
    run_id = queued(first)
    engine = BlockingEngine(first)
    worker = LocalWorker(first, engine, 0.06, shutdown_wait_seconds=0, clock=lambda: NOW)
    worker.start()
    try:
        assert engine.started.wait(5)
        worker.stop()
        assert worker._thread is not None and not worker._thread.is_alive()
        assert second.recover_running_runs("replacement", lease_seconds=30, now=LATER) == [run_id]
    finally:
        engine.release.set()
        for thread in worker._active.values():
            thread.join(5)


def test_cancel_one_of_multiple_active_runs(tmp_path):
    first, _ = stores(tmp_path)
    one, two = queued(first), queued(first)
    engine = BlockingEngine(first, expected=2)
    worker = LocalWorker(first, engine, 0.06, concurrency=2)
    worker.start()
    try:
        assert engine.started.wait(5)
        first.update_run(one, cancel_requested=1)
        engine.release.set()
        worker.stop()
        assert first.get_run(one)["status"] == "cancelled"
        assert first.get_run(two)["status"] == "completed"
    finally:
        engine.release.set()
        worker.stop()


def test_startup_does_not_take_live_run(tmp_path):
    first, second = stores(tmp_path)
    run_id = queued(first)
    first.update_run(run_id, status="running", worker_id="live", heartbeat_at=NOW)
    engine = BlockingEngine(second)
    worker = LocalWorker(second, engine, 0.06, clock=lambda: NOW)
    worker.start()
    try:
        assert second.get_run(run_id)["worker_id"] == "live"
        assert second.get_run(run_id)["status"] == "running"
        assert not engine.calls
    finally:
        engine.release.set()
        worker.stop()


def test_worker_cli_exposes_concurrency():
    args = build_parser().parse_args(["worker", "--concurrency", "3"])
    assert args.concurrency == 3
