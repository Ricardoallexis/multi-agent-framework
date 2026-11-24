# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""One-use human approvals persisted as team events, without the content being sent."""
from __future__ import annotations

import re
import secrets
from typing import Any

from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError

_HASH = re.compile(r"[0-9a-f]{64}\Z")
KINDS = frozenset({"secret_send", "osv_query", "bundle_load", "executor_activate", "memory_trust", "memory_delete",
                   "office_privacy_change"})
# EJE-T01: decided only by the fixed note of «Activar», never listed in «Aprobaciones» or «Pendientes» (Decision 20).
# SEN-T01: lowering the privacy level of an office area is decided only in «Privacidad» (Decision 14), also hidden.
HIDDEN_KINDS = frozenset({"executor_activate", "office_privacy_change"})
# MEM-T01: a person confirms the trust and the deletion of a memory entry; both show in «Pendientes»
MEMORY_KINDS = frozenset({"memory_trust", "memory_delete"})
EVENTS = frozenset({"user_approval.requested", "user_approval.decided", "user_approval.consumed"})
ACTOR = "user:gui"


def _approval_error(reason: str, approval_id: str = "") -> ServiceError:
    return ServiceError(INVALID_STATE, "Approval is no longer valid", status=409,
                        details={"reason": reason, "approval_id": approval_id})


def _new_id() -> str:
    """A fresh id that starts with a letter or digit, so a task condition accepts it as its ``approval`` token (SEC-T03)."""
    while True:
        token = secrets.token_urlsafe(24)
        if token[0].isalnum():
            return token


class UserApprovalServices:
    """The event log is authoritative; only a matching approved request can be consumed.

    ``destination`` and ``location`` are short, JSON-ready descriptions, never content.
    The caller supplies a masked sample and SHA-256 of the exact content. ``request``
    always creates a fresh id, even for an identical digest and destination.
    """

    def __init__(self, services: Any):
        # SEC-T02: read the stores when used, so a facade without a database (team schema, task services) still builds
        self._services = services

    @property
    def _store(self):
        return self._services.tasks.store

    @property
    def _run_store(self):
        return self._services.system.store

    def _sync_run_reviews(self) -> None:
        """Materialize durable run pauses as browser approvals when they are queried."""
        with self._run_store.db.connect() as conn:
            rows = conn.execute("SELECT id FROM runs WHERE status='waiting_human' "
                                "AND waiting_reason='secret_review'").fetchall()
        known = self._all()
        for row in rows:
            run = self._run_store.get_run(row["id"])
            paused = next((event["payload"] for event in reversed(run["events"])
                           if event["event_type"] == "call_paused"
                           and event["payload"].get("guard") == "secret"), None)
            request = paused.get("request", {}) if paused else {}
            approval_id = request.get("approval_id")
            if (not isinstance(approval_id, str) or approval_id in known or
                    paused.get("step_id") != run.get("waiting_step") or
                    paused.get("attempt") != run.get("waiting_attempt")):
                continue
            self.request(kind="secret_send", destination=request["destination"],
                         location={"source": "run", "run_id": run["id"]},
                         secret_types=request["secret_types"], masked_sample=request["masked_sample"],
                         sha256=request["sha256"], approval_id=approval_id)
            known[approval_id] = True

    def _events(self, *, conn=None):
        after = 0
        while True:
            batch = self._store.events(after=after, limit=500, conn=conn)
            for event in batch:
                if event["type"] in EVENTS:
                    yield event
            if len(batch) < 500:
                return
            after = batch[-1]["seq"]

    def _all(self, *, conn=None) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for event in self._events(conn=conn):
            approval_id = event["ref"]
            payload = event["payload"]
            if event["type"] == "user_approval.requested":
                result[approval_id] = {"id": approval_id, **payload,
                                       "state": "pending", "created_at": event["created_at"]}
            elif approval_id in result:
                result[approval_id]["state"] = (payload["decision"] if event["type"] == "user_approval.decided"
                                                 else "consumed")
                if event["type"] == "user_approval.decided":
                    result[approval_id]["risk_ack"] = payload.get("risk_ack", False)
        return result

    def request(self, *, kind: str, destination: str | dict[str, str], location: str | dict[str, str],
                secret_types: list[str] | tuple[str, ...] = (), masked_sample: str = "",
                sha256: str, approval_id: str | None = None) -> dict[str, Any]:
        if kind not in KINDS or not _HASH.fullmatch(sha256):
            raise ServiceError(INVALID_REQUEST, "Invalid approval kind or SHA-256", status=422)
        if not isinstance(destination, (str, dict)) or not isinstance(location, (str, dict)):
            raise ServiceError(INVALID_REQUEST, "Invalid approval destination or location", status=422)
        if len(str(destination)) > 300 or len(str(location)) > 300 or len(masked_sample) > 160:
            raise ServiceError(INVALID_REQUEST, "Approval description is too long", status=422)
        if masked_sample and not any(mark in masked_sample for mark in ("*", "…", "••")):
            raise ServiceError(INVALID_REQUEST, "The sample must be masked", status=422)
        if len(secret_types) > 16 or any(not isinstance(item, str) or len(item) > 60 for item in secret_types):
            raise ServiceError(INVALID_REQUEST, "Invalid secret types", status=422)
        approval_id = approval_id or _new_id()
        payload = {"kind": kind, "destination": destination, "location": location,
                   "secret_types": list(secret_types), "masked_sample": masked_sample, "sha256": sha256}
        with self._store.transaction() as conn:
            if approval_id in self._all(conn=conn):
                raise _approval_error("duplicate_id", approval_id)
            self._store.append_event(conn, "user_approval.requested", actor="system:approval",
                                     ref=approval_id, payload=payload)
            return self._all(conn=conn)[approval_id]

    def status(self, approval_id: str) -> dict[str, Any]:
        self._sync_run_reviews()
        item = self._all().get(approval_id)
        if item is None:
            raise ServiceError(NOT_FOUND, "Approval not found", status=404)
        return item

    def pending(self) -> list[dict[str, Any]]:
        self._sync_run_reviews()
        return [item for item in self._all().values()
                if item["state"] == "pending" and item["kind"] not in HIDDEN_KINDS]

    def pending_like(self, kind: str, *, sha256: str, destination: str | dict[str, str]) -> dict[str, Any] | None:
        """The newest pending approval of ``kind`` for this exact digest and destination, hidden kinds included (EJE-T01)."""
        found = None
        for item in self._all().values():
            if (item["state"] == "pending" and item["kind"] == kind and item["sha256"] == sha256
                    and item["destination"] == destination):
                found = item
        return found

    def decide(self, approval_id: str, decision: str, *, actor: str = ACTOR,
               risk_ack: bool = False) -> dict[str, Any]:
        self._sync_run_reviews()
        if decision not in {"approve", "reject"}:
            raise ServiceError(INVALID_REQUEST, "Decision must be approve or reject", status=422)
        with self._store.transaction() as conn:
            item = self._all(conn=conn).get(approval_id)
            if item is None:
                raise ServiceError(NOT_FOUND, "Approval not found", status=404)
            if item["state"] != "pending":
                raise _approval_error("already_decided", approval_id)
            if item["kind"] == "bundle_load" and decision == "approve" and risk_ack is not True:
                raise ServiceError("risk_ack_required", "Acknowledge the bundle load risk", status=422)
            payload = {"decision": "approved" if decision == "approve" else "rejected"}
            if item["kind"] == "bundle_load" and decision == "approve":
                payload["risk_ack"] = True
            self._store.append_event(conn, "user_approval.decided", actor=actor, ref=approval_id,
                                     payload=payload)
            decided = self._all(conn=conn)[approval_id]
        if decision == "approve" and decided.get("kind") in MEMORY_KINDS:
            self._services.memory.apply_approval(decided)       # MEM-T01: the person's click is what trusts or deletes
            return self._all()[approval_id]
        location = decided.get("location")
        if decided.get("kind") == "secret_send" and isinstance(location, dict) and location.get("source") == "run":
            run = self._run_store.get_run(location["run_id"])
            if decision == "approve" and not self.consume(
                    approval_id, sha256=decided["sha256"], destination=decided["destination"]):
                raise _approval_error("not_consumable", approval_id)
            self._run_store.finish_secret_review(run, approval_id=approval_id, sha256=decided["sha256"],
                                                 destination=decided["destination"],
                                                 approve=decision == "approve")
            return self._all()[approval_id]
        return decided

    # SEC-T03: task approvals. The click only approves; the next check of the task's hold consumes it and launches once.
    def peek(self, approval_id: str) -> dict[str, Any] | None:
        """One approval as the event log shows it, or ``None``; unlike ``status`` it never reads the run pauses."""
        return self._all().get(approval_id)

    def for_task(self, task_id: str, *, sha256: str, destination: str | dict[str, str]) -> dict[str, Any] | None:
        """The newest ``secret_send`` of this task for this exact digest and destination that is still pending or approved."""
        found = None
        for item in self._all().values():
            location = item.get("location")
            if (item.get("kind") == "secret_send" and isinstance(location, dict) and location.get("source") == "task"
                    and location.get("task_id") == task_id and item.get("sha256") == sha256
                    and item.get("destination") == destination and item["state"] in ("pending", "approved")):
                found = item
        return found

    def consume(self, approval_id: str, *, sha256: str, destination: str | dict[str, str]) -> bool:
        """Atomically spend approval for one exact content digest and destination."""
        with self._store.transaction() as conn:
            item = self._all(conn=conn).get(approval_id)
            if item is None or item["state"] != "approved":
                return False
            if not secrets.compare_digest(item["sha256"], sha256) or item["destination"] != destination:
                return False
            self._store.append_event(conn, "user_approval.consumed", actor="system:approval",
                                     ref=approval_id, payload={})
            return True
