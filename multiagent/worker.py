from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .db.database import utcnow


logger = logging.getLogger(__name__)


class SharedSlots:
    """A count of runs in progress that several workers of one process share (PAR-T03).

    Each worker caps its own threads; with a concurrency mode saved the cap of the mode is also the total of every worker
    in the process (the system one and the packages), so two workers cannot each fill the whole cap.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def acquire(self, limit: int) -> bool:
        with self._lock:
            if self._count >= limit:
                return False
            self._count += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._count = max(self._count - 1, 0)


class LocalWorker:
    """Local dispatcher with durable claims shared across worker processes."""

    def __init__(self, store, engine, poll_seconds: float = 0.75, *,
                 concurrency: int = 1, max_active_total: int | None = None,
                 max_active_per_project: int | None = None, lease_seconds: float = 30,
                 shutdown_wait_seconds: float = 30, clock=utcnow, on_finished=None,
                 limit_source: Callable[[], Mapping[str, Any] | None] | None = None,
                 on_batch_complete: Callable[[], Any] | None = None, shared_slots: SharedSlots | None = None):
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
        # PAR-T03: ``limit_source`` returns the cap of the active concurrency mode (``limit``, ``per_project``, ``reason``,
        # ``code``) or None while no mode is saved, and then every number below is the static one. ``limit`` 0 means
        # "wait for a turn": nothing new is claimed, nothing running is touched and no run fails or changes app.
        self.limit_source = limit_source
        # Called once when the batch ends: nothing is queued or running any more (a temporary mode lasting "tasks" ends).
        # It may answer False ("not yet": other queues of the process still have work); the batch is then not consumed.
        self.on_batch_complete = on_batch_complete
        # With a mode saved, the cap of the mode is also the total of the workers that share these slots.
        self.shared_slots = shared_slots
        self._finished_since_idle = False
        self._queue_state: dict[str, Any] = {"policy": False, "limit": None, "reason": None, "code": None,
                                             "active": 0, "waiting": False}
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

    def _take_shared(self, policy: Mapping[str, Any] | None) -> bool | None:
        """True when a shared slot was taken, None when none is needed, False when the process total is full."""
        if policy is None or self.shared_slots is None:
            return None
        return True if self.shared_slots.acquire(int(policy["limit"])) else False

    def _process(self, run: dict, held: bool | None = None) -> None:
        try:
            self.engine.process_run(run["id"])
        except Exception as exc:
            self.store.fail_active_run(run["id"], exc)
            logger.exception("Run %s raised an unhandled execution error", run["id"])
        finally:
            self.store.release_claim(run["id"], self.worker_id)
            if held:
                self.shared_slots.release()
            self._finished_since_idle = True
        hook = self.on_finished
        if hook is not None:
            try:
                hook(run["id"])
            except Exception:
                logger.exception("The finish hook failed for run %s", run["id"])

    def queue_state(self) -> dict[str, Any]:
        """What the queue does now and why, for a person to read: the cap in force, its reason and whether work waits."""
        return dict(self._queue_state)

    def _policy(self) -> Mapping[str, Any] | None:
        source = self.limit_source
        if source is None:
            return None
        try:
            value = source()
        except Exception:
            logger.exception("Could not read the concurrency policy; the static limits apply")
            return None
        limit = value.get("limit") if isinstance(value, Mapping) else None
        if type(limit) is not int or limit < 0:
            return None
        return value

    def _caps(self, policy: Mapping[str, Any] | None) -> tuple[int, int | None, int | None]:
        """Slots of this process, the total across processes and the per-project cap; the smallest cap always wins."""
        if policy is None:
            return self.concurrency, self.max_active_total, self.max_active_per_project
        limit = int(policy["limit"])
        total = limit if self.max_active_total is None else min(limit, self.max_active_total)
        projects = [value for value in (policy.get("per_project"), self.max_active_per_project)
                    if type(value) is int and value >= 1]
        return limit, total, (min(projects) if projects else None)

    def _note_queue(self, policy: Mapping[str, Any] | None, slots: int) -> None:
        """Record why work waits (never a failure); log only when the reason changes."""
        if policy is None:
            self._queue_state = {"policy": False, "limit": None, "reason": None, "code": None,
                                 "active": len(self._active), "waiting": False}
            return
        waiting = False
        try:
            waiting = bool(self.store.run_counts()["queued"])
        except Exception:
            logger.exception("Could not count the queued runs")
        reason, code = policy.get("reason"), policy.get("code")
        state = {"policy": True, "limit": slots, "reason": reason,
                 "code": code, "active": len(self._active), "waiting": waiting}
        if waiting and (code, slots) != (self._queue_state.get("code"), self._queue_state.get("limit")):
            logger.info("Queued runs wait for a turn: %s (limit %s)", reason, slots)
        self._queue_state = state

    def _batch_done(self) -> None:
        """Once the last run of a batch has ended, let a temporary mode that lasts "tasks" end. Never raises."""
        hook = self.on_batch_complete
        if hook is None or not self._finished_since_idle or self._active:
            return
        try:
            if self.store.run_counts()["open"]:
                return
            if hook() is not False:
                self._finished_since_idle = False
        except Exception:
            logger.exception("The end-of-batch hook failed")

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
                policy = self._policy()
                slots, total, per_project = self._caps(policy)
                self._batch_done()
                while len(self._active) < slots and not self._stop.is_set():
                    held = self._take_shared(policy)
                    if held is False:
                        break
                    try:
                        run = self.store.claim_queued_run(
                            self.worker_id, lease_seconds=self.lease_seconds,
                            max_active_total=total,
                            max_active_per_project=per_project, now=now,
                        )
                    except BaseException:
                        if held:
                            self.shared_slots.release()     # a claim that raises never keeps the shared place
                        raise
                    if not run:
                        if held:
                            self.shared_slots.release()
                        break
                    thread = threading.Thread(target=self._process, args=(run, held),
                                              name=f"multiagent-run-{run['id'][:8]}", daemon=True)
                    self._active[run["id"]] = thread
                    thread.start()
                self._note_queue(policy, slots)
            if self._stop.is_set():
                remaining = max(0, (self._deadline or time.monotonic()) - time.monotonic())
                threading.Event().wait(min(interval, remaining))
            else:
                self._stop.wait(interval)

    def process_once(self) -> bool:
        self.engine.check_approval_deadlines()
        self.store.recover_running_runs(self.worker_id, lease_seconds=self.lease_seconds, now=self.clock())
        policy = self._policy()
        slots, total, per_project = self._caps(policy)
        run = None
        held = self._take_shared(policy) if slots > 0 else False
        if held is not False:
            try:
                run = self.store.claim_queued_run(
                    self.worker_id, lease_seconds=self.lease_seconds, max_active_total=total,
                    max_active_per_project=per_project, now=self.clock(),
                )
            except BaseException:
                if held:
                    self.shared_slots.release()
                raise
            if not run and held:
                self.shared_slots.release()
        if not run:
            self._note_queue(policy, slots)
            self._batch_done()
            return False
        self._process(run, held)
        self._batch_done()
        return True


def concurrency_limit_source(services: Any, *, ttl_seconds: float = 5.0,
                             monotonic: Callable[[], float] = time.monotonic) -> Callable[[], dict[str, Any] | None]:
    """The ``limit_source`` of a worker: the cap of the concurrency mode the person saved, from ``services``.

    Returns None (the static limits apply, as before) until a mode is saved in the GUI settings. Then it returns what
    ``ConcurrencyServices.effective`` says (``limit``, ``reason``, ``code``) plus ``per_project``: one per project unless
    ``WORKER_MAX_ACTIVE_PER_PROJECT`` says otherwise. The reading is kept ``ttl_seconds`` so a poll does not read the
    usage files every pass. The services are looked up on each call: they may be built after the worker.
    """
    cache: dict[str, Any] = {"at": None, "value": None}

    def source() -> dict[str, Any] | None:
        now = monotonic()
        if cache["at"] is not None and now - cache["at"] < ttl_seconds:
            return cache["value"]
        value: dict[str, Any] | None = None
        gui, concurrency = getattr(services, "gui_settings", None), getattr(services, "concurrency", None)
        if gui is not None and concurrency is not None and "concurrency" in gui.read():
            effective = concurrency.effective()
            per_project = getattr(getattr(services, "settings", None), "worker_max_active_per_project", None)
            value = {"limit": int(effective["limit"]), "per_project": per_project or 1,
                     "reason": effective.get("reason"), "code": effective.get("code"),
                     "mode": effective.get("mode")}
        cache["at"], cache["value"] = now, value
        return value

    return source
