# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T03: the plan of an approved proposal advances by itself when one of its runs finishes.

``run_finished`` is called by the worker (system and bundle workers) once a run is released, and by ``RunServices``
after ``approve``, ``reject`` and ``cancel``, which end a run outside the worker. It advances the plan once, with the
actor ``system:worker`` and the channel ``worker``, and leaves a team event: ``plan.advanced`` or ``plan.advance_failed``.
Runs that were recovered at start-up do not come here; for them "Advance the plan" stays.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from .errors import ServiceError
from .plan_proposals import SYSTEM_ACTOR

if TYPE_CHECKING:
    from . import ApplicationServices

logger = logging.getLogger(__name__)

ACTOR = SYSTEM_ACTOR
CHANNEL = "worker"
ADVANCED_EVENT = "plan.advanced"
FAILED_EVENT = "plan.advance_failed"
FINISHED = {"completed", "failed", "cancelled", "rejected"}
_MAX_LISTED = 20
_MAX_PAYLOAD_BYTES = 1900       # the team event payload limit is 2048


def notify_run_finished(services: Any, run_id: str) -> None:
    """Tell the plan that a run ended. Never raises: a failing hook must not change the answer or the run."""
    try:
        autoadvance = getattr(services, "plan_autoadvance", None)
        if autoadvance is not None:
            autoadvance.run_finished(run_id)
    except Exception:   # noqa: BLE001 - see the docstring
        logger.exception("The plan hook failed for run %s", run_id)


def notify_task_accepted(services: Any, task_id: str) -> None:
    """Tell the plan that a task was accepted (``accept`` or a passed gate). Never raises; call it after the commit."""
    try:
        autoadvance = getattr(services, "plan_autoadvance", None)
        if autoadvance is not None:
            autoadvance.task_finished(task_id)
    except Exception:   # noqa: BLE001 - see notify_run_finished
        logger.exception("The plan hook failed for task %s", task_id)


def _fits(payload: dict[str, Any]) -> bool:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= _MAX_PAYLOAD_BYTES


class PlanAutoAdvance:
    def __init__(self, services: "ApplicationServices"):
        self._services = services
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._local = threading.local()
        worker = getattr(getattr(services, "system", None), "worker", None)
        if worker is not None:
            worker.on_finished = self.notify

    def notify(self, run_id: str) -> None:
        notify_run_finished(self._services, run_id)

    def _lock(self, group_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(group_id, threading.Lock())

    def run_finished(self, run_id: str) -> dict[str, Any] | None:
        """Advance the plan of the run's task once. ``None`` when the run is not of an approved plan or not over."""
        run = self._services.runs.get(run_id)
        task_id = (run.get("request") or {}).get("task_id")
        if run.get("status") not in FINISHED or not task_id:
            return None
        return self.task_finished(task_id, run_id=run_id)

    def task_finished(self, task_id: str, run_id: str = "") -> dict[str, Any] | None:
        """Advance the plan of an approved proposal of the task's group once (a run ended, or the task was accepted).

        Not re-entrant: the transitions the advance itself makes (a ``submit`` also accepts an activity) never come back.
        """
        if getattr(self._local, "inside", False):
            return None
        services = self._services
        group_id = services.tasks.get_task(task_id)["group_id"]
        approved = services.plan_proposals.list(group_id=group_id, status="approved")
        if not approved:
            return None
        proposal_id = approved[-1]["id"]
        with self._lock(group_id):
            error: Exception | None = None
            self._local.inside = True
            try:
                for _attempt in range(2):
                    try:
                        result = services.plan_proposals.advance(proposal_id, actor=ACTOR, channel=CHANNEL)
                    except ServiceError as exc:
                        error = exc
                        if exc.status == 409:
                            continue
                        break
                    except Exception as exc:   # noqa: BLE001 - recorded below
                        error = exc
                        break
                    if not result.get("nothing_to_advance"):
                        self._record(ADVANCED_EVENT, group_id, task_id, run_id, proposal_id,
                                     advanced=result.get("advanced") or [])
                    return result
            finally:
                self._local.inside = False
            self._record(FAILED_EVENT, group_id, task_id, run_id, proposal_id, reason=self._reason(error))
            return None

    @staticmethod
    def _reason(error: Exception | None) -> str:
        if isinstance(error, ServiceError):
            return f"{error.code}: {error.message}"[:300]
        return type(error).__name__

    def _record(self, event: str, group_id: str, task_id: str, run_id: str, proposal_id: str, *,
                advanced: list[dict[str, Any]] | None = None, reason: str = "") -> None:
        payload: dict[str, Any] = {"proposal_id": proposal_id}
        if run_id:
            payload["run_id"] = run_id
        if event == ADVANCED_EVENT:
            listed = [dict(item) for item in (advanced or [])[:_MAX_LISTED]]
            payload["advanced"] = listed
            while listed and not _fits(payload):
                listed.pop()
        else:
            payload["reason"] = reason
        store = self._services.tasks.store
        try:
            with store.transaction() as conn:
                store.append_event(conn, event, actor=ACTOR, group_id=group_id, task_id=task_id, payload=payload)
        except Exception:   # noqa: BLE001 - the event is a record, never a reason to fail
            logger.exception("Could not record %s for run %s", event, run_id)
