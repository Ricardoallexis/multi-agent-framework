# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The person's inbox (AVA-T03, contract ``AVA-T01-contrato.md`` section 7).

A person can write to a run or to a task while it works: ``cancel`` (stop it), ``interrupt`` (stop and wait for me),
``adjust`` (stop and wait, I want to change something) or ``note`` (for the record, go on). The messages go to the event
log that already exists, with no new table: a ``user.message`` event per message and a ``user.message_consumed`` event
when the work read it (``run_events`` for a run, ``team_events`` for a task). Available as ``ApplicationServices.inbox``.
Off unless the availability policy is on (``availability_enabled``): then posting is refused and nothing here changes.

The log
-------
- *Append only.* A message is never changed or deleted; reading it is a second event that points at it.
- ``seq`` numbers the messages of one run or task 1, 2, 3 without gaps, assigned inside the transaction that writes the
  message. It is the cursor: the hand-off package keeps ``inbox_cursor`` (``cursor``), and the new session reads from there
  (``messages(after=cursor)``).
- ``message_id`` makes posting idempotent: the same id with the same kind and text answers the first message
  (``created`` false) and writes nothing; the same id with other content is ``message_id_conflict``. Reading dedupes by
  ``message_id`` too, so a duplicate that got into the log some other way is processed once.
- The sender is a person who can be named: an actor of 1 to 100 letters, digits, ``_ . : -`` that does not start (in any
  case) with ``system:``, ``executor:`` or ``agent:`` and is not the assignee of the task it writes to (an agent does not
  cancel or interrupt its own work). A message is the person's own instruction and nothing else: it is never evidence of an
  approval, and no code here calls an approval path.

Who reads it (never an API thread)
----------------------------------
- *Runs.* ``UserInboxGuard`` (order 50 of the call guards) reads the inbox in the worker thread, before each model call of a
  step: the boundary of a step. ``cancel`` becomes ``Stop`` (the engine ends the run as cancelled); ``interrupt`` and
  ``adjust`` become ``Pause(user_message)`` (the run waits in ``waiting_human`` and the person settles it with the approval
  path that exists: ``approve`` goes on, ``reject`` rejects, ``cancel`` always works); ``note`` is recorded and the run
  goes on. Messages are read in the order of the policy (``cancel`` first), one verdict per boundary, and the marker is
  written before the verdict, so a message is read once. The routes that post only append.
- *Tasks.* A background attempt (AVA-T02) calls ``on_attempt_tick`` from its process watch. ``cancel`` cancels the task
  and kills the process tree; ``interrupt`` and ``adjust`` kill the tree and, when the attempt has recorded its end, save a
  checkpoint (CNT-T03) and hold the task on an ``approval`` condition (``user-message:<message_id>``) that only the sender
  of the message can record (``approver``; ``record_approval`` also refuses ``system:`` and ``executor:``). If a usage limit
  already holds the task, the approval is added to that hold (the reset time and the approval must both hold); ``note`` is
  recorded. A synchronous attempt has no
  watch that reads the inbox: its messages wait for the next background attempt.
- *Hand-off* (AVA-T04). ``cursor`` is the ``inbox_cursor`` of the package, ``messages(after=cursor)`` is what the new session
  reads, and ``require_no_cancel`` is the re-read before ``claim``: a cancel that nobody consumed aborts the hand-off.
  A session that was retired never acts: ``resume_task`` refuses it (``session_retired``) and ``start``, ``pause``,
  ``resume`` and ``submit`` are the assignee's alone (``tasks.ASSIGNEE_ONLY``).

Events: ``user.message`` ``{message_id, seq, kind, priority, text, actor}``, ``user.message_consumed`` ``{message_id, seq,
kind, action, ...}`` (``action`` is what the reader set out to do: ``noted``, ``cancelled``, ``paused`` or ``interrupting``;
it is written *before* the action is taken, so a reader that lost the race for a message does nothing) and, for a task,
``user.message_applied`` ``{message_id, action, ...}`` with what happened (``cancelled`` or ``refused`` for a cancel, with
``killed`` or ``error``; ``held`` or ``not_held`` for an interrupt, with ``checkpoint`` or ``error``; ``failed``). Payloads stay
under the 2048 bytes of a team event; a text is at most ``MAX_TEXT_BYTES`` bytes.

Errors (``ServiceError``): ``not_found`` (run, task), ``invalid_request`` (ids, kind, text, actor, paging) and
``invalid_state`` with ``details.reason``: ``availability_disabled``, ``availability_policy_invalid``, ``run_closed``,
``task_closed``, ``sender_not_allowed``, ``message_id_conflict``, ``inbox_full`` and ``inbox_cancelled``.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..call_guards import ORDER_INBOX, PROCEED, STOP_USER_CANCEL, CallContext, Pause, Stop, Verdict
from ..contracts import RunStatus, WaitingReason
from ..db.database import utcnow
from .availability import (
    DISABLED, USER_MESSAGE_EVENT, AvailabilityPolicy, AvailabilityPolicyError, MessageKind, UserMessage, order_messages,
    policy_from_settings,
)
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors
from . import hold as _hold
from .tasks import TERMINAL_STATES, _text

if TYPE_CHECKING:
    from . import ApplicationServices

__all__ = [
    "APPLIED_EVENT", "CONSUMED_EVENT", "EVENT_TYPES", "INBOX_ACTOR", "MAX_PENDING", "MAX_TEXT_BYTES", "SCOPES",
    "InboxServices", "UserInboxGuard", "collect_messages", "pending_messages",
]

_logger = logging.getLogger(__name__)

CONSUMED_EVENT = "user.message_consumed"
APPLIED_EVENT = "user.message_applied"
EVENT_TYPES = (USER_MESSAGE_EVENT, CONSUMED_EVENT, APPLIED_EVENT)
SCOPE_RUN, SCOPE_TASK = "run", "task"
SCOPES = (SCOPE_RUN, SCOPE_TASK)
INBOX_ACTOR = "system:inbox"              # who writes the markers and the hold of a task: never the person, never an approver
NOT_PEOPLE = ("system:", "executor:", "agent:")     # programs and agents are not the person the inbox listens to (any case)
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")   # a sender that can be named as the approver of its interrupt
DEFAULT_DEADLINE_MINUTES = 12 * 60        # how long an interrupted task waits for the person (the default of hold_on_condition)
_INTENT = {MessageKind.NOTE.value: "noted", MessageKind.CANCEL.value: "cancelled",
           MessageKind.INTERRUPT.value: "interrupting", MessageKind.ADJUST.value: "interrupting"}
MAX_TEXT_BYTES = 1200
MAX_PAYLOAD_BYTES = 1900                  # a team event is limited to 2048 bytes
MAX_PENDING = 100                         # messages nobody has read yet in one run or task
MAX_LIMIT = 500
APPROVAL_PREFIX = "user-message:"         # the approval id an interrupt waits for
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_KINDS = tuple(kind.value for kind in MessageKind)
_CLOSED_RUN = (RunStatus.COMPLETED.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value, RunStatus.REJECTED.value)


def _invalid(field_name: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field_name, **details})


def _conflict(reason: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})


# ---------------------------------------------------------------------------------------------------------
# The pure part: from the events of a log to the messages and what is still unread
# ---------------------------------------------------------------------------------------------------------

def _message_view(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """A ``user.message`` event as a message; ``None`` when it does not have the shape of one (it is skipped)."""
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        return None
    message_id, kind, seq, text = (payload.get("message_id"), payload.get("kind"), payload.get("seq"), payload.get("text"))
    if (not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id) or kind not in _KINDS
            or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1 or not isinstance(text, str)):
        return None
    priority = payload.get("priority")
    return {"message_id": message_id, "seq": seq, "kind": kind,
            "priority": priority if isinstance(priority, int) and not isinstance(priority, bool) else None,
            "text": text, "actor": str(record.get("actor") or payload.get("actor") or ""), "at": str(record.get("at") or ""),
            "state": "pending", "consumed": None}


def collect_messages(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The messages of an inbox in arrival order (``seq``), each with whether it was read.

    ``records`` are ``{type, payload, actor, at}`` for the ``user.message`` and ``user.message_consumed`` events, oldest
    first. A ``message_id`` that appears more than once is one message (the lowest ``seq``); the first marker that names a
    message is the one that counts. Events that do not have the shape of one are skipped.
    """
    messages: dict[str, dict[str, Any]] = {}
    consumed: dict[str, dict[str, Any]] = {}
    for record in records:
        kind = record.get("type")
        payload = record.get("payload")
        if not isinstance(payload, Mapping) or not isinstance(payload.get("message_id"), str):
            continue
        message_id = payload["message_id"]
        if kind == USER_MESSAGE_EVENT:
            view = _message_view(record)
            kept = messages.get(message_id)
            if view is not None and (kept is None or view["seq"] < kept["seq"]):
                messages[message_id] = view
        elif kind == CONSUMED_EVENT and message_id not in consumed:
            consumed[message_id] = {**{key: value for key, value in payload.items() if key not in ("message_id", "seq", "kind")},
                                    "at": str(record.get("at") or "")}
    result = sorted(messages.values(), key=lambda item: item["seq"])
    for view in result:
        marker = consumed.get(view["message_id"])
        if marker is not None:
            view["state"], view["consumed"] = "consumed", marker
    return result


def pending_messages(messages: Iterable[Mapping[str, Any]], policy: AvailabilityPolicy = DISABLED) -> list[dict[str, Any]]:
    """The unread messages in the order they are consumed: the policy's priority of the kind, then ``seq``."""
    unread = {item["message_id"]: item for item in messages if item.get("state") == "pending"}
    ordered = order_messages((UserMessage(item["message_id"], MessageKind(item["kind"]), item["seq"])
                              for item in unread.values()), policy)
    return [dict(unread[message.message_id]) for message in ordered]


def _run_records(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The inbox records among the events a run state carries (``event_type`` and ``payload``)."""
    return [{"type": event.get("event_type"), "payload": event.get("payload"),
             "actor": (event.get("payload") or {}).get("actor", "") if isinstance(event.get("payload"), Mapping) else "",
             "at": event.get("ts", "")}
            for event in events if event.get("event_type") in (USER_MESSAGE_EVENT, CONSUMED_EVENT)]


# ---------------------------------------------------------------------------------------------------------
# The guard of a run
# ---------------------------------------------------------------------------------------------------------

class UserInboxGuard:
    """Reads the person's inbox before a model call of a run step (order 50, the first guard of the chain).

    It runs where the chain runs: in the worker thread, at the boundary of a step. For the first unread message in the
    policy's order it writes the ``user.message_consumed`` marker and answers: ``note`` goes on to the next message,
    ``cancel`` is ``Stop`` (``STOP_USER_CANCEL``: the engine ends the run as cancelled), ``interrupt`` and ``adjust`` are
    ``Pause`` (``user_message``: the run waits for the person). It never approves anything and never changes the run.

    It is an observer: a bug in it never cuts a call (the verdict it returns is still honoured). Without ``emit`` it cannot
    write the marker, so it reads nothing.
    """

    name = "user_inbox"
    order = ORDER_INBOX
    observer = True
    skippable = False

    def __init__(self, policy: AvailabilityPolicy, *, emit: Any = None) -> None:
        self.policy = policy
        self._emit = emit

    def check(self, call: CallContext, state: Mapping[str, Any]) -> Verdict:
        if self._emit is None:
            return PROCEED
        unread = pending_messages(collect_messages(_run_records(state.get("events", ()))), self.policy)
        for message in unread:
            kind = MessageKind(message["kind"])
            if kind is MessageKind.NOTE:
                self._consume(message, "noted", call)
                continue
            if kind is MessageKind.CANCEL:
                self._consume(message, "cancelled", call)
                return Stop(STOP_USER_CANCEL, f"The person asked to cancel (message {message['message_id']})",
                            details={"message_id": message["message_id"], "seq": message["seq"]})
            self._consume(message, "paused", call)
            return Pause(WaitingReason.USER_MESSAGE,
                         request={"message_id": message["message_id"], "seq": message["seq"], "kind": message["kind"],
                                  "text": message["text"]},
                         message=f"The person sent {message['kind']} message {message['message_id']}: the run waits for them")
        return PROCEED

    def _consume(self, message: Mapping[str, Any], action: str, call: CallContext) -> None:
        self._emit(CONSUMED_EVENT, {"message_id": message["message_id"], "seq": message["seq"], "kind": message["kind"],
                                    "action": action, "step_id": call.step_id, "attempt": call.attempt})


# ---------------------------------------------------------------------------------------------------------
# The two logs
# ---------------------------------------------------------------------------------------------------------

class _RunLog:
    """The inbox of a run: ``run_events``. The sender goes in the payload (the table has no actor column)."""

    scope = SCOPE_RUN

    def __init__(self, store: Any, run_id: str):
        self.store, self.scope_id = store, run_id

    def ensure_open(self) -> None:
        with self.store.db.connect() as conn:
            row = conn.execute("SELECT status FROM runs WHERE id=?", (self.scope_id,)).fetchone()
        if row is None:
            raise ServiceError(NOT_FOUND, f"Run not found: {self.scope_id}", status=404)
        if row["status"] in _CLOSED_RUN:
            raise _conflict("run_closed", f"Run {self.scope_id} is {row['status']}: it does not read messages",
                            run_id=self.scope_id, status=row["status"])

    def exists(self) -> bool:
        with self.store.db.connect() as conn:
            return conn.execute("SELECT 1 FROM runs WHERE id=?", (self.scope_id,)).fetchone() is not None

    def owned_by(self, actor: str) -> bool:
        return False                                   # a run has no assignee

    def reader(self):
        return self.store.db.connect()

    def writer(self):
        return self.store.db.tx()

    def read(self, conn: Any) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT ts, event_type, payload_json FROM run_events WHERE run_id=? AND event_type IN (?,?) "
                            "ORDER BY id", (self.scope_id, USER_MESSAGE_EVENT, CONSUMED_EVENT))
        records = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            records.append({"type": row["event_type"], "payload": payload, "actor": payload.get("actor", ""), "at": row["ts"]})
        return records

    def append(self, conn: Any, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                     (self.scope_id, utcnow(), event_type, json.dumps({**payload, "actor": actor}, ensure_ascii=False)))


class _TaskLog:
    """The inbox of a task: ``team_events``, the events of the task."""

    scope = SCOPE_TASK

    def __init__(self, store: Any, task_id: str):
        self.store, self.scope_id = store, task_id

    def _task(self) -> dict[str, Any]:
        task = self.store.get_task(self.scope_id)
        if task is None:
            raise ServiceError(NOT_FOUND, f"Task not found: {self.scope_id}", status=404, details={"task": self.scope_id})
        return task

    def ensure_open(self) -> None:
        task = self._task()
        if task["state"] in TERMINAL_STATES:
            raise _conflict("task_closed", f"Task {self.scope_id} is {task['state']}: it does not read messages",
                            task_id=self.scope_id, status=task["state"])

    def exists(self) -> bool:
        return self.store.get_task(self.scope_id) is not None

    def owned_by(self, actor: str) -> bool:
        """True when ``actor`` is the assignee of the task (compared without regard to case)."""
        assignee = str((self.store.get_task(self.scope_id) or {}).get("assignee") or "")
        return bool(assignee) and assignee.casefold() == actor.casefold()

    def reader(self):
        return self.store.db.connect()

    def writer(self):
        return self.store.transaction()

    def read(self, conn: Any) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT created_at, type, actor, payload_json FROM team_events WHERE task_id=? AND type IN (?,?) "
                            "ORDER BY seq", (self.scope_id, USER_MESSAGE_EVENT, CONSUMED_EVENT))
        return [{"type": row["type"], "payload": json.loads(row["payload_json"]), "actor": row["actor"],
                 "at": row["created_at"]} for row in rows]

    def append(self, conn: Any, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        task = self.store.get_task(self.scope_id, conn=conn) or {}
        self.store.append_event(conn, event_type, actor=actor, group_id=str(task.get("group_id", "")),
                                task_id=self.scope_id, cause="user inbox", ref=str(payload.get("message_id", "")),
                                payload=payload)


def _encoded_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# ---------------------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------------------

class InboxServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        # A host or a test may set the policy here; otherwise the one of the executors, else the ``availability_*`` settings.
        self.availability: AvailabilityPolicy | None = None
        self._lock = threading.Lock()
        self._interrupts: dict[str, dict[str, Any]] = {}      # task id -> the interrupt waiting for its attempt to end

    # -------- wiring --------
    def policy(self) -> AvailabilityPolicy:
        """The availability policy in force (it may be off). ``invalid_state`` ``availability_policy_invalid`` when the
        configuration is not valid."""
        if self.availability is not None:
            return self.availability
        override = getattr(getattr(self._services, "executors", None), "availability", None)
        if override is not None:
            return override
        try:
            return policy_from_settings(self._services.settings)
        except AvailabilityPolicyError as exc:
            raise _conflict("availability_policy_invalid", f"The availability configuration is not valid: {exc}") from None

    def _policy_or_off(self) -> AvailabilityPolicy:
        try:
            return self.policy()
        except ServiceError:
            return DISABLED

    def _log(self, scope: str, scope_id: Any) -> _RunLog | _TaskLog:
        if scope not in SCOPES:
            raise _invalid("scope", f"scope must be one of {', '.join(SCOPES)}")
        if not isinstance(scope_id, str) or not scope_id.strip() or len(scope_id) > 200:
            raise _invalid("id", f"Invalid {scope} id: {scope_id!r}")
        if scope == SCOPE_RUN:
            log: _RunLog | _TaskLog = _RunLog(self._services.runs._find(scope_id).store, scope_id)
        else:
            log = _TaskLog(self._services.tasks.store, scope_id)
        if not log.exists():
            raise ServiceError(NOT_FOUND, f"{scope.capitalize()} not found: {scope_id}", status=404, details={scope: scope_id})
        return log

    # -------- writing --------
    @service_errors
    def post(self, scope: str, scope_id: str, *, message_id: str, kind: str, text: str = "", actor: str) -> dict[str, Any]:
        """Write a message of the person to a run or a task; idempotent by ``message_id``.

        Returns the message (``{message_id, seq, kind, priority, text, actor, at, state, consumed, created}``). The work reads
        it at its next step boundary, in the policy's order; this call never reads it, never changes the run or the task and
        never approves anything. Errors: see the module docstring.
        """
        policy = self.policy()
        if not policy.enabled:
            raise _conflict("availability_disabled", "The availability policy is off: the inbox is not in use")
        if not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id):
            raise _invalid("message_id", "message_id must be 1 to 64 letters, digits, '_', '.', ':' or '-', starting with a "
                                         "letter or digit")
        kind = kind.value if isinstance(kind, MessageKind) else kind
        if kind not in _KINDS:
            raise _invalid("kind", f"kind must be one of {', '.join(_KINDS)}")
        if not isinstance(text, str):
            raise _invalid("text", "text must be text")
        text = text.strip()
        if "\x00" in text:
            raise _invalid("text", "text must not contain NUL characters")
        if kind in (MessageKind.ADJUST.value, MessageKind.NOTE.value) and not text:
            raise _invalid("text", f"a {kind} message needs its text")
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise _invalid("text", f"text is longer than {MAX_TEXT_BYTES} bytes")
        actor = _text("actor", actor, maximum=100)
        if not _ACTOR.fullmatch(actor):
            raise _invalid("actor", "actor must be 1 to 100 letters, digits, '_', '.', ':' or '-', starting with a letter or digit")
        if actor.casefold().startswith(NOT_PEOPLE):
            raise _conflict("sender_not_allowed", "Only a person writes to the inbox, not a program or an agent")
        log = self._log(scope, scope_id)
        log.ensure_open()
        if log.owned_by(actor):
            raise _conflict("sender_not_allowed", "The assignee does not write to the inbox of its own task")
        payload = {"message_id": message_id, "kind": kind, "priority": policy.priority_rank(kind), "text": text}
        with log.writer() as conn:
            messages = collect_messages(log.read(conn))
            for earlier in messages:
                if earlier["message_id"] == message_id:
                    if earlier["kind"] != kind or earlier["text"] != text:
                        raise _conflict("message_id_conflict", f"message_id {message_id} was already used for another message",
                                        message_id=message_id)
                    return {**earlier, "created": False}
            if sum(1 for item in messages if item["state"] == "pending") >= MAX_PENDING:
                raise _conflict("inbox_full", f"{MAX_PENDING} messages are waiting to be read: wait for them", limit=MAX_PENDING)
            seq = max((item["seq"] for item in messages), default=0) + 1
            payload["seq"] = seq
            if _encoded_size({**payload, "actor": actor}) > MAX_PAYLOAD_BYTES:
                raise _invalid("text", "text is too long once encoded")
            log.append(conn, USER_MESSAGE_EVENT, actor, payload)
        return {"message_id": message_id, "seq": seq, "kind": kind, "priority": payload["priority"], "text": text,
                "actor": actor, "at": utcnow(), "state": "pending", "consumed": None, "created": True}

    # -------- reading --------
    @service_errors
    def messages(self, scope: str, scope_id: str, *, after: int = 0, limit: int = 100,
                 state: str | None = None) -> list[dict[str, Any]]:
        """The messages with ``seq`` greater than ``after``, oldest first, at most ``limit`` (1..500); ``state`` narrows to
        ``pending`` or ``consumed``. Errors: ``not_found``, ``invalid_request``."""
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise _invalid("after", "after must be an integer >= 0", after=repr(after))
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise _invalid("limit", f"limit must be an integer between 1 and {MAX_LIMIT}", limit=repr(limit))
        if state not in (None, "pending", "consumed"):
            raise _invalid("state", "state must be pending or consumed")
        log = self._log(scope, scope_id)
        with log.reader() as conn:
            messages = collect_messages(log.read(conn))
        return [item for item in messages if item["seq"] > after and (state is None or item["state"] == state)][:limit]

    @service_errors
    def pending(self, scope: str, scope_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        """The messages nobody has read yet with ``seq`` above ``after``, in the order they are consumed."""
        messages = self.messages(scope, scope_id, after=after, limit=MAX_LIMIT, state="pending")
        return pending_messages(messages, self._policy_or_off())

    @service_errors
    def cursor(self, scope: str, scope_id: str) -> int:
        """The ``seq`` of the last message of the inbox (0 when empty): the ``inbox_cursor`` a hand-off package keeps."""
        log = self._log(scope, scope_id)
        with log.reader() as conn:
            return max((item["seq"] for item in collect_messages(log.read(conn))), default=0)

    @service_errors
    def require_no_cancel(self, scope: str, scope_id: str) -> None:
        """Read the inbox again and refuse (``invalid_state``, ``inbox_cancelled``) when a ``cancel`` was never consumed.

        The hand-off calls this before ``claim``: a person who asked to cancel is not answered with a new session.
        """
        cancels = [item for item in self.pending(scope, scope_id) if item["kind"] == MessageKind.CANCEL.value]
        if cancels:
            raise _conflict("inbox_cancelled", f"The person asked to cancel (message {cancels[0]['message_id']}): "
                            "nothing new is started", message_id=cancels[0]["message_id"], seq=cancels[0]["seq"])

    # -------- the boundary of a task attempt (called by the executors, in the thread of the attempt) --------
    def on_attempt_tick(self, task_id: str, attempt: int, executor_id: str, session_id: str | None) -> None:
        """Read the inbox of a task from the process watch of its background attempt. Never raises: it is telemetry about a
        running program, and a message that cannot be applied is recorded as ``refused``, never retried in a loop."""
        try:
            policy = self._policy_or_off()
            if not policy.enabled:
                return
            store = self._services.tasks.store
            task = store.get_task(task_id)
            if task is None or task.get("state") != "running" or task.get("attempt") != attempt:
                return                                          # not the live attempt of the task: it is not its boundary
            log = _TaskLog(store, task_id)
            with log.reader() as conn:
                unread = pending_messages(collect_messages(log.read(conn)), policy)
            for message in unread:
                action = _INTENT[message["kind"]]
                if not self._mark_consumed(log, message, {"action": action}):
                    continue                                    # somebody else read it first: it does nothing
                if action == "noted":
                    continue
                self._apply_to_task(message, task_id, attempt, executor_id, session_id)
                break                                           # the attempt is ending: the rest waits for the next one
        except Exception:  # noqa: BLE001 - the program that is being watched must never feel this
            _logger.warning("the inbox of %s could not be read", task_id, exc_info=True)

    def _apply_to_task(self, message: Mapping[str, Any], task_id: str, attempt: int, executor_id: str,
                       session_id: str | None) -> None:
        """Act on a message this reader has just marked as read (``cancel``, ``interrupt`` or ``adjust``). What happened is
        recorded as ``user.message_applied``: a cancel here, an interrupt when its attempt has ended (``finish_interrupt``)."""
        message_id, executors = message["message_id"], self._services.executors
        try:
            if message["kind"] == MessageKind.CANCEL.value:
                try:
                    self._services.tasks.transition(task_id, "cancel", actor=message["actor"] or "user",
                                                    cause=f"user message {message_id}")
                except ServiceError as exc:
                    self._record_applied(task_id, {"message_id": message_id, "action": "refused", "error": exc.code})
                    return
                killed = executors.background.cancel(task_id)
                self._record_applied(task_id, {"message_id": message_id, "action": "cancelled", "killed": killed})
                return
            # interrupt and adjust: stop the program now; the checkpoint and the hold wait until the attempt recorded its end
            # (its session is only known then), see ``finish_interrupt``. The interrupt is registered first: the attempt may
            # end, and look for it, the moment the program is killed.
            with self._lock:
                self._interrupts[task_id] = {"message_id": message_id, "attempt": attempt, "executor_id": executor_id,
                                             "session_id": session_id or "", "actor": message["actor"]}
            executors.background.cancel(task_id)
        except Exception as exc:  # noqa: BLE001 - the message is marked as read: say that it could not be acted on
            _logger.warning("the message %s of %s could not be acted on", message_id, task_id, exc_info=True)
            with self._lock:
                self._interrupts.pop(task_id, None)
            self._record_applied(task_id, {"message_id": message_id, "action": "failed", "error": type(exc).__name__})

    def _record_applied(self, task_id: str, outcome: Mapping[str, Any]) -> None:
        """Write ``user.message_applied``: what became of a message once its reader acted on it. Never raises."""
        try:
            store = self._services.tasks.store
            with store.transaction() as conn:
                task = store.get_task(task_id, conn=conn) or {}
                store.append_event(conn, APPLIED_EVENT, actor=INBOX_ACTOR, group_id=str(task.get("group_id", "")),
                                   task_id=task_id, cause="user inbox", ref=str(outcome.get("message_id", "")),
                                   payload=dict(outcome))
        except Exception:  # noqa: BLE001
            _logger.warning("what became of a message of %s could not be recorded", task_id, exc_info=True)

    @staticmethod
    def _deadline_minutes(task: Mapping[str, Any], executors: Any) -> int:
        """The deadline of the approval: the default 12 hours, or 12 hours past the reset of a limit that already holds the
        task (a condition may not expire before the limit it is added to resets)."""
        minutes = DEFAULT_DEADLINE_MINUTES
        hold = (task.get("continuity") or {}).get("hold")
        reset = _hold.parse_utc(hold.get("resume_after")) if isinstance(hold, Mapping) and hold.get("resume_after") else None
        if reset is not None:
            minutes += max(0, int((reset - executors.clock()).total_seconds() // 60) + 1)
        return minutes

    def _mark_consumed(self, log: _TaskLog | _RunLog, message: Mapping[str, Any], outcome: Mapping[str, Any]) -> bool:
        """Write the marker unless another reader wrote it first (checked inside the writing transaction)."""
        with log.writer() as conn:
            current = {item["message_id"]: item for item in collect_messages(log.read(conn))}.get(message["message_id"])
            if current is None or current["state"] != "pending":
                return False
            log.append(conn, CONSUMED_EVENT, INBOX_ACTOR,
                       {"message_id": message["message_id"], "seq": message["seq"], "kind": message["kind"], **outcome})
        return True

    def finish_interrupt(self, task_id: str, attempt: int) -> dict[str, Any] | None:
        """After a background attempt recorded its end: if an interrupt stopped it, save the checkpoint and hold the task on
        an approval only a person can give. Never raises. Returns what it did, or ``None`` when no interrupt was pending."""
        with self._lock:
            pending = self._interrupts.pop(task_id, None)
        if pending is None or pending["attempt"] != attempt:
            return None
        message_id, outcome = pending["message_id"], {"message_id": pending["message_id"], "action": "not_held"}
        try:
            task = self._services.tasks.store.get_task(task_id)
            if task is None or task.get("state") not in ("running", "paused"):
                outcome["error"] = f"task_{task['state'] if task else 'missing'}"
            else:
                executors = self._services.executors
                try:
                    saved = executors.checkpoint_limit(task_id, None, executor_id=pending["executor_id"], attempt=attempt,
                                                       record_limit=False, actor=INBOX_ACTOR)
                    outcome["checkpoint"] = (saved.get("patch") or {}).get("ref") or saved.get("patch_reason") or "saved"
                except Exception as exc:  # noqa: BLE001 - no checkpoint is no reason to leave the task unheld
                    outcome["checkpoint"] = f"error: {getattr(exc, 'code', type(exc).__name__)}"
                params: dict[str, Any] = {"approval": APPROVAL_PREFIX + message_id}
                if pending.get("actor"):
                    params["approver"] = pending["actor"]       # only the person who wrote it decides
                # ``reason`` is left to the hold: a task that a usage limit already holds keeps that reason (the condition is added
                # to that hold, and the reset time and the approval must both hold); otherwise it is ``approval``.
                executors.hold_on_condition(task_id, {"type": "approval", "params": params}, actor=INBOX_ACTOR,
                                            executor_id=pending["executor_id"],
                                            deadline_minutes=self._deadline_minutes(self._services.tasks.get_task(task_id),
                                                                                    executors))
                outcome["action"] = "held"
        except ServiceError as exc:
            outcome["error"] = exc.code
        except Exception as exc:  # noqa: BLE001 - recorded, not raised
            _logger.warning("the interrupt of %s could not be applied", task_id, exc_info=True)
            outcome["error"] = type(exc).__name__
        self._record_applied(task_id, outcome)
        return outcome
