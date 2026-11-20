# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""One-use human approvals persisted as team events, without the content being sent."""
from __future__ import annotations

import re
import secrets
from typing import Any

from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError

_HASH = re.compile(r"[0-9a-f]{64}\Z")
KINDS = frozenset({"secret_send", "osv_query", "bundle_load"})
EVENTS = frozenset({"user_approval.requested", "user_approval.decided", "user_approval.consumed"})
ACTOR = "user:gui"


def _approval_error(reason: str, approval_id: str = "") -> ServiceError:
    return ServiceError(INVALID_STATE, "Approval is no longer valid", status=409,
                        details={"reason": reason, "approval_id": approval_id})


class UserApprovalServices:
    """The event log is authoritative; only a matching approved request can be consumed.

    ``destination`` and ``location`` are short, JSON-ready descriptions, never content.
    The caller supplies a masked sample and SHA-256 of the exact content. ``request``
    always creates a fresh id, even for an identical digest and destination.
    """

    def __init__(self, services: Any):
        self._store = services.tasks.store

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
        return result

    def request(self, *, kind: str, destination: str | dict[str, str], location: str | dict[str, str],
                secret_types: list[str] | tuple[str, ...] = (), masked_sample: str = "",
                sha256: str) -> dict[str, Any]:
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
        approval_id = secrets.token_urlsafe(24)
        payload = {"kind": kind, "destination": destination, "location": location,
                   "secret_types": list(secret_types), "masked_sample": masked_sample, "sha256": sha256}
        with self._store.transaction() as conn:
            self._store.append_event(conn, "user_approval.requested", actor="system:approval",
                                     ref=approval_id, payload=payload)
            return self._all(conn=conn)[approval_id]

    def status(self, approval_id: str) -> dict[str, Any]:
        item = self._all().get(approval_id)
        if item is None:
            raise ServiceError(NOT_FOUND, "Approval not found", status=404)
        return item

    def pending(self) -> list[dict[str, Any]]:
        return [item for item in self._all().values() if item["state"] == "pending"]

    def decide(self, approval_id: str, decision: str, *, actor: str = ACTOR) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ServiceError(INVALID_REQUEST, "Decision must be approve or reject", status=422)
        with self._store.transaction() as conn:
            item = self._all(conn=conn).get(approval_id)
            if item is None:
                raise ServiceError(NOT_FOUND, "Approval not found", status=404)
            if item["state"] != "pending":
                raise _approval_error("already_decided", approval_id)
            self._store.append_event(conn, "user_approval.decided", actor=actor, ref=approval_id,
                                     payload={"decision": "approved" if decision == "approve" else "rejected"})
            return self._all(conn=conn)[approval_id]

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
