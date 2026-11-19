# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Proposals and decisions with human transitions and effective validity."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..db.database import utcnow
from ..db.decision_store import DecisionStore
from .errors import service_errors
from .memory import MAX_BODY_CHARS, MAX_TITLE_CHARS, _CONTROL
from .projects import actor_kind
from .tasks import _conflict, _invalid, _missing, _text

if TYPE_CHECKING:
    from . import ApplicationServices

_ID = re.compile(r"^dec-[0-9a-f]{12}$")
_LINKS = ("run_id", "step_id", "task_id", "team_id", "project")


def _date(field: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _invalid(field, f"{field} must be a UTC date and time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _invalid(field, f"{field} must be a UTC date and time") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise _invalid(field, f"{field} must be a UTC date and time")
    return parsed.astimezone(timezone.utc).isoformat()


def _actor(value: Any, *, human: bool = False) -> str:
    actor = _text("actor", value, maximum=100)
    kind = actor_kind(actor)
    if kind is None:
        raise _invalid("actor", "actor must be user:[name], person:<name>, agent:<app>[/<model>] or executor:<id>")
    if human and kind != "user":
        raise _conflict("Only a user can decide", actor=actor, reason="confirmation_required")
    return actor


def _content(title: Any, body: Any) -> tuple[str, str]:
    title = _text("title", title, maximum=MAX_TITLE_CHARS)
    body = _text("body", body, maximum=MAX_BODY_CHARS)
    if "\n" in title or _CONTROL.search(title):
        raise _invalid("title", "title is one line and may not hold control characters")
    if _CONTROL.search(body):
        raise _invalid("body", "body may not hold control characters")
    return title, body


def _view(row: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    effective = "expired" if (row["status"] == "accepted" and row["valid_until"] is not None
                              and row["valid_until"] <= now) else row["status"]
    active = (effective == "accepted" and row["superseded_by"] is None
              and (row["valid_from"] is None or row["valid_from"] <= now))
    return {**row, "status": effective, "active": active}


class DecisionServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: DecisionStore | None = None

    @property
    def store(self) -> DecisionStore:
        if self._store is None:
            self._store = DecisionStore(self._services.system.db)
        return self._store

    @staticmethod
    def _id(value: Any) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise _invalid("decision_id", f"Invalid decision id: {value!r}")
        return value

    @service_errors
    def propose(self, *, title: str, body: str, actor: str, valid_until: str | None = None,
                run_id: str | None = None, step_id: str | None = None, attempt: int | None = None,
                task_id: str | None = None, team_id: str | None = None,
                project: str | None = None) -> dict[str, Any]:
        title, body = _content(title, body)
        actor = _actor(actor)
        until = _date("valid_until", valid_until)
        links = {name: _text(name, value, maximum=200) if value is not None else None
                 for name, value in locals().items() if name in _LINKS}
        if attempt is not None and (type(attempt) is not int or attempt < 1):
            raise _invalid("attempt", "attempt must be a positive integer")
        with self.store.db.tx() as conn:
            row = self.store.insert(conn, id=f"dec-{uuid.uuid4().hex[:12]}", title=title, body=body,
                                    proposed_by=actor, status="proposed", valid_until=until,
                                    attempt=attempt, **links)
        return _view(row)

    @service_errors
    def record_review(self, *, title: str, body: str, proposed_by: str, decided_by: str,
                      accepted: bool, outcome: str, run_id: str | None = None, step_id: str | None = None,
                      attempt: int | None = None, project: str | None = None, task_id: str | None = None,
                      team_id: str | None = None) -> dict[str, Any]:
        """Atomically record a completed run or team review after the decision has been resolved."""
        title, body = _content(title, body)
        proposed_by = _actor(proposed_by)
        decided_by = _actor(decided_by, human=True)
        outcome = _text("outcome", outcome, maximum=MAX_BODY_CHARS)
        links = {name: _text(name, value, maximum=200) if value is not None else None
                 for name, value in (("run_id", run_id), ("step_id", step_id),
                                     ("project", project), ("task_id", task_id),
                                     ("team_id", team_id))}
        if attempt is not None and (type(attempt) is not int or attempt < 1):
            raise _invalid("attempt", "attempt must be a positive integer")
        now = utcnow()
        with self.store.db.tx() as conn:
            row = self.store.insert(conn, id=f"dec-{uuid.uuid4().hex[:12]}", title=title, body=body,
                                    proposed_by=proposed_by, decided_by=decided_by,
                                    status="accepted" if accepted else "rejected", outcome=outcome,
                                    valid_from=now if accepted else None, decided_at=now,
                                    attempt=attempt, **links)
        return _view(row)

    @service_errors
    def get(self, decision_id: str) -> dict[str, Any]:
        row = self.store.get(self._id(decision_id))
        if row is None:
            raise _missing("Decision", decision_id)
        return _view(row)

    @service_errors
    def list(self, *, active: bool = False, project: str | None = None,
             run_id: str | None = None) -> dict[str, Any]:
        if type(active) is not bool:
            raise _invalid("active", "active must be a boolean")
        for name, value in (("project", project), ("run_id", run_id)):
            if value is not None:
                _text(name, value, maximum=200)
        entries = [_view(row) for row in self.store.list(project=project, run_id=run_id)]
        if active:
            entries = [entry for entry in entries if entry["active"]]
        return {"decisions": entries, "count": len(entries)}

    def _transition(self, decision_id: str, *, actor: str, expected: str, target: str,
                    outcome: str | None = None, valid_from: str | None = None,
                    valid_until: str | None = None) -> dict[str, Any]:
        decision_id = self._id(decision_id)
        actor = _actor(actor, human=True)
        outcome = _text("outcome", outcome, maximum=MAX_BODY_CHARS, required=False) if outcome is not None else None
        start = _date("valid_from", valid_from)
        until = _date("valid_until", valid_until)
        with self.store.db.tx() as conn:
            row = self.store.get(decision_id, conn=conn)
            if row is None:
                raise _missing("Decision", decision_id)
            if row["status"] != expected or _view(row)["status"] != expected:
                raise _conflict(f"Cannot {target} a {row['status']} decision", reason="invalid_transition",
                                status=_view(row)["status"])
            if target == "accepted":
                start = start or utcnow()
                until = until if until is not None else row["valid_until"]
            fields = {"status": target, "decided_by": actor, "decided_at": utcnow(), "outcome": outcome}
            if target == "accepted":
                fields.update(valid_from=start, valid_until=until)
            row = self.store.update(conn, decision_id, **fields)
        return _view(row)

    @service_errors
    def accept(self, decision_id: str, *, actor: str, outcome: str | None = None,
               valid_from: str | None = None, valid_until: str | None = None) -> dict[str, Any]:
        return self._transition(decision_id, actor=actor, expected="proposed", target="accepted",
                                outcome=outcome, valid_from=valid_from, valid_until=valid_until)

    @service_errors
    def reject(self, decision_id: str, *, actor: str, outcome: str | None = None) -> dict[str, Any]:
        return self._transition(decision_id, actor=actor, expected="proposed", target="rejected", outcome=outcome)

    @service_errors
    def supersede(self, decision_id: str, *, actor: str, replacement_id: str | None = None,
                  title: str | None = None, body: str | None = None,
                  outcome: str | None = None, valid_until: str | None = None) -> dict[str, Any]:
        decision_id = self._id(decision_id)
        actor = _actor(actor, human=True)
        if replacement_id is not None:
            replacement_id = self._id(replacement_id)
            if title is not None or body is not None:
                raise _invalid("replacement_id", "Use a replacement_id or a new title and body")
        else:
            title, body = _content(title, body)
        until = _date("valid_until", valid_until)
        outcome = _text("outcome", outcome, maximum=MAX_BODY_CHARS, required=False) if outcome is not None else None
        with self.store.db.tx() as conn:
            old = self.store.get(decision_id, conn=conn)
            if old is None:
                raise _missing("Decision", decision_id)
            if old["status"] != "accepted" or _view(old)["status"] != "accepted":
                raise _conflict("Only an accepted decision can be superseded", reason="invalid_transition",
                                status=_view(old)["status"])
            if replacement_id is None:
                replacement_id = f"dec-{uuid.uuid4().hex[:12]}"
                replacement = self.store.insert(conn, id=replacement_id, title=title, body=body,
                                                proposed_by=actor, status="proposed", valid_until=until,
                                                **{name: old[name] for name in (*_LINKS, "attempt")})
            else:
                replacement = self.store.get(replacement_id, conn=conn)
                if replacement is None:
                    raise _missing("Decision", replacement_id)
                if replacement_id == decision_id or replacement["status"] != "proposed":
                    raise _conflict("Replacement must be a different proposed decision", reason="invalid_transition")
                until = until if until is not None else replacement["valid_until"]
            now = utcnow()
            replacement = self.store.update(conn, replacement_id, status="accepted", decided_by=actor,
                                            decided_at=now, valid_from=now, valid_until=until, outcome=outcome)
            self.store.update(conn, decision_id, status="superseded", superseded_by=replacement_id)
        return _view(replacement)
