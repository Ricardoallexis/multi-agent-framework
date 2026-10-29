# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T13: deterministic process watching one (task, hold, attempt).

The CLI runs this loop in its own OS process. State files are exclusive leases,
not commands: only ExecutorServices.resume_met can activate an executor. The host
must supply its integration guard; a broken guard fails closed. A lost lease is
never restarted automatically. Checks and retries use no model.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .waits import check_token

ACTOR = "system:watcher"
INTERVAL_SECONDS = 120
MAX_RESTARTS = 3
IO_ATTEMPTS = 4
TERMINAL = frozenset({"resumed", "expired", "attention", "lost", "stopped"})


def pid_alive(pid: int) -> bool:
    """Observe a PID without sending a signal on Windows; uncertain means alive."""
    if pid < 1:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # access denied is not proof of death
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _key(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def _retry_permission(operation: Callable[[], Any]) -> Any:
    """Windows readers can briefly deny replace/unlink; never retry indefinitely."""
    for attempt in range(IO_ATTEMPTS):
        try:
            return operation()
        except PermissionError:
            if attempt == IO_ATTEMPTS - 1:
                raise
            time.sleep(0.05 * 2 ** attempt)


def _unlink(path: Path) -> None:
    _retry_permission(lambda: path.unlink(missing_ok=True))


def _atomic_state(path: Path, state: dict[str, Any]) -> None:
    temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        _retry_permission(lambda: os.replace(temp, path))
    finally:
        _unlink(temp)


class WatcherProcess:
    """One lease and one finite state machine. Dependencies are injectable for tests.

    ``integration_active`` is required, and checked again inside the per-executor
    lease before every resumption. A host integrating concurrently must coordinate
    its marker with that same lease (or otherwise serialize its own effects).
    ``tick`` never sleeps. ``run`` is the OS process loop; Ctrl-C stops only the
    watcher, leaving the task's checkpoint and hold untouched.
    """

    def __init__(self, root: Path, executors: Any, *, integration_marker: Path,
                 integration_active: Callable[[], bool],
                 clock: Callable[[], datetime] | None = None,
                 alive: Callable[[int], bool] = pid_alive, pid: int | None = None):
        self.root = Path(root)
        self.executors = executors
        self.integration_active = integration_active
        # Shared exclusive marker: the integration controller must create this
        # same file with CreateNew/O_EXCL before beginning its own work.
        self.integration_marker = Path(integration_marker)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.alive = alive
        self.pid = os.getpid() if pid is None else pid
        self.path: Path | None = None
        self.state: dict[str, Any] = {}

    def claim(self, task_id: str, hold_id: str, attempt: int, *, max_hours: float = 12) -> dict[str, Any]:
        # Task ids use a broader vocabulary than condition tokens.
        import re
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", task_id):
            raise ValueError("invalid task_id")
        check_token(hold_id, "hold_id")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if isinstance(max_hours, bool) or not 0 < max_hours <= 216:
            raise ValueError("max_hours must be above zero and at most 216")
        if self.path is not None:
            raise RuntimeError("this watcher already owns a lease")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / ("watch-" + _key(task_id, hold_id) + ".json")
        now = self.clock().isoformat()
        state = {"schema": "multiagent.watcher/1", "actor": ACTOR, "id": "watch-" + _key(task_id, hold_id)[:24],
                 "task_id": task_id, "hold_id": hold_id, "attempt": attempt, "pid": self.pid,
                 "owner": uuid.uuid4().hex, "state": "waiting", "since": now, "checked_at": now,
                 "next_check_at": now, "interval_seconds": INTERVAL_SECONDS, "failed_restarts": 0}
        state["deadline_at"] = (self.clock() + timedelta(hours=max_hours)).isoformat()
        # x is the cross-process lock. Finished and lost files remain as tombstones:
        # restarting the same hold must not reset the retry budget.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(state, stream)
        self.path, self.state = path, state
        return dict(state)

    def _save(self) -> dict[str, Any]:
        assert self.path is not None
        current = json.loads(self.path.read_text(encoding="utf-8"))
        if current.get("owner") != self.state["owner"] or current.get("state") == "lost":
            raise RuntimeError("watcher lease was lost")
        # Keep a complete state visible to readers; the lease is the pathname.
        _atomic_state(self.path, self.state)
        return dict(self.state)

    def _finish(self, state: str, reason: str) -> dict[str, Any]:
        self.state.update(state=state, reason=reason, finished_at=self.clock().isoformat())
        return self._save()

    def sweep(self) -> list[str]:
        """Mark dead, stale processes lost. Never activate an agent or delete a lease."""
        lost = []
        dead_owners = set()
        for path in self.root.glob("watch-*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if item.get("schema") != "multiagent.watcher/1":
                    continue
                if item.get("state") in TERMINAL:
                    if not self.alive(int(item["pid"])):
                        dead_owners.add(item["owner"])
                    continue
                checked = datetime.fromisoformat(item["checked_at"])
                if (self.clock() - checked).total_seconds() <= 2 * INTERVAL_SECONDS or self.alive(int(item["pid"])):
                    continue
                # No owner exists to replace this file now. Re-read before writing.
                if json.loads(path.read_text(encoding="utf-8")) != item:
                    continue
                item.update(state="lost", reason="process_missing", finished_at=self.clock().isoformat())
                _atomic_state(path, item)
                dead_owners.add(item["owner"])
                lost.append(item["id"])
            except (OSError, ValueError, KeyError, TypeError):
                continue  # a corrupt/in-flight file is not evidence for a restart
        for path in self.root.glob("executor-*.lock"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if not self.alive(int(item["pid"])) and json.loads(path.read_text(encoding="utf-8")) == item:
                    _unlink(path)
            except (OSError, ValueError, KeyError, TypeError):
                continue
        try:
            gate = json.loads(self.integration_marker.read_text(encoding="utf-8"))
            if (gate.get("schema") == "multiagent.watcher_gate/1" and gate.get("owner") in dead_owners
                    and not self.alive(int(gate["pid"]))
                    and json.loads(self.integration_marker.read_text(encoding="utf-8")) == gate):
                _unlink(self.integration_marker)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # never remove an integration controller's own marker
        return lost

    def _integration_blocks(self) -> bool:
        try:
            return bool(self.integration_active())
        except Exception:
            return True

    def tick(self) -> dict[str, Any]:
        if self.path is None:
            raise RuntimeError("claim a watcher lease first")
        if self.state["state"] in TERMINAL:
            return dict(self.state)
        now = self.clock()
        self.state["checked_at"] = now.isoformat()
        self.state["next_check_at"] = (now + timedelta(seconds=INTERVAL_SECONDS)).isoformat()
        self._save()  # heartbeat even if the following condition reader fails
        if now >= datetime.fromisoformat(self.state["deadline_at"]):
            return self._finish("expired", "watcher_deadline")
        try:
            answer = self.executors.check_waits(task_id=self.state["task_id"], watcher_id=self.state["id"],
                                                actor=ACTOR, record=True)
            candidates = [w for w in answer["waits"] if w.get("task_id") == self.state["task_id"]]
            if not candidates:
                return self._finish("stopped", "task_not_held")
            wait = candidates[0]
            if wait.get("hold_id") != self.state["hold_id"] or wait.get("attempt") != self.state["attempt"]:
                return self._finish("stopped", "hold_or_attempt_changed")
            self.state["evaluation"] = wait.get("evaluation")
            if wait.get("state") == "expired":
                return self._finish("expired", "condition_expired")
            if wait.get("state") == "skipped":
                return self._finish("attention", str(wait.get("why", "resume_refused")))
            if not wait.get("due"):
                return self._save()
            retry_at = self.state.get("retry_at")
            if retry_at and now < datetime.fromisoformat(retry_at):
                return self._save()
            if self._integration_blocks():
                self.state["reason"] = "integration_active"
                return self._save()
            executor_id = str(wait.get("executor_id") or "")
            if not executor_id:
                return self._finish("attention", "executor_missing")
            lease = self.root / ("executor-" + _key(executor_id) + ".lock")
            try:
                handle = lease.open("x", encoding="utf-8")
            except FileExistsError:
                self.state["reason"] = "executor_busy"
                return self._save()
            try:
                with handle:
                    json.dump({"pid": self.pid, "owner": self.state["owner"]}, handle)
                if self._integration_blocks():
                    self.state["reason"] = "integration_active"
                    return self._save()
                try:
                    gate = self.integration_marker.open("x", encoding="utf-8")
                except FileExistsError:
                    self.state.update(state="waiting", reason="integration_active")
                    return self._save()
                except OSError:
                    return self._finish("attention", "integration_marker_unavailable")
                # resume_met rechecks condition, executor, session, hold and attempt
                # under the task service's own transition guards.
                try:
                    with gate:
                        json.dump({"schema": "multiagent.watcher_gate/1", "pid": self.pid, "owner": self.state["owner"]}, gate)
                    self.state["state"] = "resuming"
                    self._save()
                    result = self.executors.resume_met(task_id=self.state["task_id"], hold_id=self.state["hold_id"],
                                                      attempt=self.state["attempt"], limit=1, actor=ACTOR)
                except Exception as exc:
                    failures = self.state["failed_restarts"] + 1
                    self.state.update(failed_restarts=failures, error=type(exc).__name__, state="waiting")
                    if failures >= MAX_RESTARTS:
                        return self._finish("attention", "restart_limit")
                    self.state["retry_at"] = (now + timedelta(seconds=INTERVAL_SECONDS * 2 ** (failures - 1))).isoformat()
                    return self._save()
                finally:
                    # Remove only our own integration lease, never someone else's marker.
                    if json.loads(self.integration_marker.read_text(encoding="utf-8")).get("owner") == self.state["owner"]:
                        _unlink(self.integration_marker)
                if result.get("resumed"):
                    return self._finish("resumed", "condition_met")
                if result.get("expired"):
                    return self._finish("expired", "condition_expired")
                if result.get("skipped"):
                    return self._finish("attention", str(result["skipped"][0].get("why", "resume_refused")))
                self.state["state"] = "waiting"  # condition ceased to hold
                return self._save()
            finally:
                _unlink(lease)
        except Exception as exc:
            if self.state["state"] in TERMINAL:
                # A cleanup error after an effect must never turn success back
                # into a retryable wait.
                raise
            if self.state["state"] == "resuming":
                return self._finish("attention", "resume_outcome_unknown")
            # Reader/telemetry errors do not consume the resumption budget.
            self.state.update(state="waiting", error=type(exc).__name__)
            return self._save()

    def run(self, *, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
        try:
            while self.state.get("state") not in TERMINAL:
                self.sweep()
                self.tick()
                if self.state["state"] not in TERMINAL:
                    sleep(INTERVAL_SECONDS)
        except (KeyboardInterrupt, OSError) as exc:
            # Do not claim an operator stop proves that an in-flight effect did
            # not happen. Persistence itself can fail, so return an honest
            # in-memory terminal outcome and leave the disk lease for inspection.
            if self.state.get("state") not in TERMINAL:
                if self.state.get("state") == "resuming":
                    outcome, reason = "attention", "resume_outcome_unknown"
                elif isinstance(exc, KeyboardInterrupt):
                    outcome, reason = "stopped", "operator_stop"
                else:
                    outcome, reason = "attention", "state_io_error"
                self.state.update(state=outcome, reason=reason, finished_at=self.clock().isoformat())
            try:
                return self._save()
            except OSError:
                self.state["persistence_error"] = True
        return dict(self.state)
