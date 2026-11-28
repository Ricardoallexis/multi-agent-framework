# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The dispatcher responds to local commits and still performs periodic maintenance."""
from __future__ import annotations

import threading
import time

from multiagent.worker import LocalWorker
from test_worker_concurrency import BlockingEngine, queued, stores


class Notices:
    def __init__(self):
        self.callbacks = []

    def subscribe(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def send(self):
        for callback in tuple(self.callbacks):
            callback()


class IdleStore:
    def __init__(self, db):
        self.db = db
        self.claims = 0
        self.maintenance = 0
        self.renewals = 0
        self.first = threading.Event()

    def recover_running_runs(self, *args, **kwargs):
        self.maintenance += 1
        return []

    def claim_queued_run(self, *args, **kwargs):
        self.claims += 1
        self.first.set()
        return None

    def renew_lease(self, *args, **kwargs):
        self.renewals += 1


class IdleEngine:
    def __init__(self):
        self.checks = 0

    def check_approval_deadlines(self):
        self.checks += 1


def test_commit_wakes_worker_with_long_poll(tmp_path):
    store, _ = stores(tmp_path)
    engine = BlockingEngine(store)
    worker = LocalWorker(store, engine, poll_seconds=30, lease_seconds=90)
    worker.start()
    try:
        # Let the initial empty claim finish before creating the run.
        time.sleep(0.1)
        run_id = queued(store)
        assert engine.started.wait(2)
        assert engine.calls == [run_id]
    finally:
        engine.release.set()
        worker.stop()


def test_burst_is_coalesced_and_does_not_repeat_maintenance():
    notices = Notices()
    store = IdleStore(notices)
    engine = IdleEngine()
    worker = LocalWorker(store, engine, poll_seconds=30, lease_seconds=90)
    worker.start()
    try:
        assert store.first.wait(1)
        for _ in range(100):
            notices.send()
        time.sleep(0.18)
        assert store.claims <= 5
        assert engine.checks == 1
        assert store.maintenance == 2  # start recovery and first periodic pass
    finally:
        started = time.monotonic()
        worker.stop()
        assert time.monotonic() - started < 1
    assert notices.callbacks == []


def test_polling_fallback_when_subscription_fails():
    class BrokenNotices:
        def subscribe(self, callback):
            raise RuntimeError("notifications unavailable")

    store = IdleStore(BrokenNotices())
    worker = LocalWorker(store, IdleEngine(), poll_seconds=0.03)
    worker.start()
    try:
        assert store.first.wait(1)
        first = store.claims
        time.sleep(0.1)
        assert store.claims > first
    finally:
        worker.stop()


def test_busy_worker_does_not_claim_or_renew_on_each_notice():
    class Active:
        def is_alive(self):
            return True

    notices = Notices()
    store = IdleStore(notices)
    worker = LocalWorker(store, IdleEngine(), poll_seconds=0.25, shutdown_wait_seconds=0)
    worker._active["running"] = Active()
    worker.start()
    try:
        time.sleep(0.05)
        assert store.renewals == 1
        for _ in range(100):
            notices.send()
        time.sleep(0.12)
        assert store.renewals == 1
        assert store.claims == 0
        time.sleep(0.18)
        assert store.renewals >= 2
    finally:
        worker.stop()
