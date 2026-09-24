from __future__ import annotations

import os
import threading
import uuid


class LocalWorker:
    """Single local worker with startup recovery for orphaned RUNNING jobs."""

    def __init__(self, store, engine, poll_seconds: float = 0.75):
        self.store = store
        self.engine = engine
        self.poll_seconds = poll_seconds
        self.worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_running_runs(self.worker_id)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="multiagent-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _process(self, run: dict) -> None:
        self.store.mark_worker_start(run["id"], self.worker_id)
        self.engine.process_run(run["id"])

    def _loop(self) -> None:
        while not self._stop.is_set():
            run = self.store.next_queued_run()
            if run:
                self._process(run)
            else:
                self._stop.wait(self.poll_seconds)

    def process_once(self) -> bool:
        run = self.store.next_queued_run()
        if not run:
            return False
        self._process(run)
        return True
