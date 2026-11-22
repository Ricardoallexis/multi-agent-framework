from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from .db.database import utcnow


logger = logging.getLogger(__name__)


class LocalWorker:
    """Local dispatcher with durable claims shared across worker processes."""

    def __init__(self, store, engine, poll_seconds: float = 0.75, *,
                 concurrency: int = 1, max_active_total: int | None = None,
                 max_active_per_project: int | None = None, lease_seconds: float = 30,
                 shutdown_wait_seconds: float = 30, clock=utcnow, on_finished=None):
        if concurrency < 1 or lease_seconds <= 0 or shutdown_wait_seconds < 0:
            raise ValueError("Invalid worker concurrency, lease or shutdown wait")
        self.store = store
        self.engine = engine
        self.poll_seconds = poll_seconds
        self.concurrency = concurrency
        self.max_active_total = max_active_total
        self.max_active_per_project = max_active_per_project
        self.lease_seconds = lease_seconds
        self.shutdown_wait_seconds = shutdown_wait_seconds
        self.clock = clock
        # ARR3-T03: called with the run id after the claim is released; a failure is logged and changes nothing.
        self.on_finished = on_finished
        self.worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: dict[str, threading.Thread] = {}
        self._deadline: float | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_running_runs(self.worker_id, lease_seconds=self.lease_seconds, now=self.clock())
        self._stop.clear()
        self._deadline = None
        self._thread = threading.Thread(target=self._loop, name="multiagent-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._deadline = time.monotonic() + self.shutdown_wait_seconds
        if self._thread:
            self._thread.join(timeout=self.shutdown_wait_seconds + min(self.poll_seconds, 1) + 1)

    def _process(self, run: dict) -> None:
        try:
            self.engine.process_run(run["id"])
        except Exception as exc:
            self.store.fail_active_run(run["id"], exc)
            logger.exception("Run %s raised an unhandled execution error", run["id"])
        finally:
            self.store.release_claim(run["id"], self.worker_id)
        hook = self.on_finished
        if hook is not None:
            try:
                hook(run["id"])
            except Exception:
                logger.exception("The finish hook failed for run %s", run["id"])

    def _loop(self) -> None:
        interval = min(self.poll_seconds, self.lease_seconds / 3)
        while True:
            # One clock reading per pass: recovery must judge leases against the time they were
            # renewed at, or a late reading would requeue this worker's own live runs.
            now = self.clock()
            for run_id, thread in tuple(self._active.items()):
                if not thread.is_alive():
                    del self._active[run_id]
                else:
                    self.store.renew_lease(run_id, self.worker_id, now=now)
            if self._stop.is_set():
                if not self._active or (self._deadline is not None and time.monotonic() >= self._deadline):
                    return
            else:
                try:
                    self.engine.check_approval_deadlines()
                except Exception:
                    logger.exception("Could not inspect approval deadlines")
                self.store.recover_running_runs(self.worker_id, lease_seconds=self.lease_seconds, now=now)
                while len(self._active) < self.concurrency and not self._stop.is_set():
                    run = self.store.claim_queued_run(
                        self.worker_id, lease_seconds=self.lease_seconds,
                        max_active_total=self.max_active_total,
                        max_active_per_project=self.max_active_per_project, now=now,
                    )
                    if not run:
                        break
                    thread = threading.Thread(target=self._process, args=(run,),
                                              name=f"multiagent-run-{run['id'][:8]}", daemon=True)
                    self._active[run["id"]] = thread
                    thread.start()
            if self._stop.is_set():
                remaining = max(0, (self._deadline or time.monotonic()) - time.monotonic())
                threading.Event().wait(min(interval, remaining))
            else:
                self._stop.wait(interval)

    def process_once(self) -> bool:
        self.engine.check_approval_deadlines()
        self.store.recover_running_runs(self.worker_id, lease_seconds=self.lease_seconds, now=self.clock())
        run = self.store.claim_queued_run(
            self.worker_id, lease_seconds=self.lease_seconds, max_active_total=self.max_active_total,
            max_active_per_project=self.max_active_per_project, now=self.clock(),
        )
        if not run:
            return False
        self._process(run)
        return True
