# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Envelope services: task envelopes, task results, artifact references and event cursors.

Contract fixed by ORQ-T22; implemented by ORQ-T25 on the envelope store
(``multiagent.db.envelope_store``, ORQ-T23), the pure envelope validation
(``multiagent.tasks.envelopes``, ORQ-T24) and the single task transition
function of ``services.tasks`` (ORQ-T25). Available as
``ApplicationServices.envelopes``.

A **task envelope** (``multiagent.task_envelope/1``) is what an agent needs to
start a task and nothing more: objective, effective status, dependencies *by
reference*, file scope, effective gate, base commit and the event sequence to
read from. It is computed, never stored, and carries only data and references:
no commands, credentials or absolute paths of the user.

A **task result** (``multiagent.task_result/1``) is what an agent delivers: a
bounded summary, decisions, contract changes, tests not run, requests and the
patch as a blob by sha256. Dependents receive the result, not the conversation.
Submitting it also performs the ``submit`` transition in one transaction and is
idempotent for the same task, attempt and patch with the same content.

References are text: ``result://<task_id>/<attempt>``, ``blob:sha256:<hex>``,
``doc://<relative path>[#anchor][@commit]``, ``report://<id>`` and
``rules@sha256:<hex>``. The core resolves only ``result:`` and ``blob:``; the
others are validated and returned as text for the agent to read with its own
tools. A blob is content addressed, stored once, and read by ranges so that
what is not requested never enters the agent's context.

Each consumer keeps a cursor over the team event log. Reading events does not
move the cursor; ``ack_events`` does (at-least-once delivery, recoverable after
an interruption).

Every method returns JSON-ready data and raises only ``ServiceError``:
``not_found`` (unknown task, result, blob or reference), ``invalid_request``
(malformed data or reference, a value over its limit, an unresolvable scheme)
or ``invalid_state`` (the task status does not allow the operation, the actor
is not the assignee, or a result with the same key already has other content).
"""
from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from ..db.envelope_store import HARD_MAX_BLOB_BYTES, EnvelopeStore
from ..tasks import graph
from ..tasks import release as release_policy
from ..tasks.envelopes import (
    MAX_CONSTRAINT_CHARS,
    MAX_CONSTRAINTS,
    MAX_CONTEXT_REFS,
    MAX_REFERENCE_CHARS,
    MAX_RESULT_BYTES,
    REFERENCE_SCHEMES,
    RESOLVABLE_SCHEMES,
    TASK_ENVELOPE_SCHEMA,
    TASK_RESULT_SCHEMA,
    EnvelopeError,
    blob_ref,
    build_task_envelope,
    canonical_json,
    task_envelope_sha256,
    parse_reference,
    patch_sha256,
    result_ref,
    unchanged_envelope,
    validate_reference,
    validate_task_result,
)
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors
from .tasks import _TASK_ID, TERMINAL_STATES, _text

if TYPE_CHECKING:
    from . import ApplicationServices

__all__ = [
    "DEFAULT_BLOB_MAX_BYTES",
    "DEFAULT_READ_BYTES",
    "EVENT_TYPES",
    "EnvelopeServices",
    "MAX_READ_BYTES",
    "MAX_REFERENCE_CHARS",
    "MAX_RESULT_BYTES",
    "REFERENCE_SCHEMES",
    "RESOLVABLE_SCHEMES",
    "TASK_ENVELOPE_SCHEMA",
    "TASK_RESULT_SCHEMA",
]

DEFAULT_BLOB_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_READ_BYTES = 64 * 1024
MAX_READ_BYTES = 1024 * 1024
MAX_CONSUMER_CHARS = 100
MAX_EVENT_TYPE_CHARS = 64
MAX_READ_EVENTS = 1000
EVENT_TYPES = ("result.submitted", "task.context_updated")

_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
_GROUP_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
_TEXT_MEDIA_TYPES = ("application/json",)
# The context cannot change while the gate validates the delivery, nor once the task is closed.
_FROZEN_STATES = (*TERMINAL_STATES, "validating")
_EVENT_BATCH = 500


def _invalid(field: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field, **details})


def _conflict(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details=details)


def _missing(kind: str, identifier: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"{kind} not found: {identifier}", status=404, details={kind.lower(): identifier})


def _from_envelope_error(error: EnvelopeError) -> ServiceError:
    return _invalid(error.field, str(error))


def _integer(field: str, value: Any, *, minimum: int, maximum: int | None = None) -> int:
    if (isinstance(value, bool) or not isinstance(value, int) or value < minimum
            or (maximum is not None and value > maximum)):
        bounds = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        raise _invalid(field, f"{field} must be an integer {bounds}", value=repr(value))
    return value


def _consumer(value: Any) -> str:
    return _text("consumer", value, maximum=MAX_CONSUMER_CHARS)


def _scope(value: Any) -> str:
    """``""`` (the whole team), ``group:<id>`` or ``task:<id>``."""
    if value == "":
        return ""
    if isinstance(value, str):
        kind, _, identifier = value.partition(":")
        if (kind == "group" and _GROUP_ID.fullmatch(identifier)) or (kind == "task" and _TASK_ID.fullmatch(identifier)):
            return value
    raise _invalid("scope", f"scope must be '', group:<id> or task:<id>: {value!r}")


def _is_text(media_type: str) -> bool:
    return media_type.startswith("text/") or media_type in _TEXT_MEDIA_TYPES


def _utf8_text(data: bytes, *, at_end: bool) -> str | None:
    """``data`` as UTF-8, or ``None`` when it is not UTF-8.

    Inside the blob (``at_end`` false) a character cut by the end of the range
    is left out; at the end of the blob an incomplete character is not UTF-8.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if at_end or exc.reason != "unexpected end of data" or len(data) - exc.start > 3:
            return None
        try:
            return data[:exc.start].decode("utf-8")
        except UnicodeDecodeError:
            return None


class EnvelopeServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: EnvelopeStore | None = None
        # Size limit of one blob (contract §4); configurable up to the store's hard limit.
        self.blob_max_bytes = DEFAULT_BLOB_MAX_BYTES

    @property
    def store(self) -> EnvelopeStore:
        # Created on first use, like the task store.
        if self._store is None:
            self._store = EnvelopeStore(self._services.system.db)
        return self._store

    @property
    def _tasks(self):
        return self._services.tasks

    # -------- helpers --------
    def _task_or_missing(self, task_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
        task = self._tasks.store.get_task(task_id, conn=conn) if isinstance(task_id, str) else None
        if task is None:
            raise _missing("Task", str(task_id))
        return task

    @staticmethod
    def _result_view(row: Mapping[str, Any]) -> dict[str, Any]:
        return {**row["result"], "ref": result_ref(row["task_id"], row["attempt"]),
                "result_sha256": row["result_sha256"], "created_at": row["created_at"]}

    def _cursor_view(self, consumer: str, scope: str, conn: sqlite3.Connection) -> dict[str, Any]:
        return {"consumer": consumer, "scope": scope, "seq": self.store.get_cursor(consumer, scope, conn=conn),
                "head": self.store.event_head(conn=conn)}

    # -------- task context and envelope --------
    @service_errors
    def set_task_context(self, task_id: str, *, actor: str, base_commit: str | None = None,
                         context_refs: Iterable[str] | None = None,
                         constraints: Iterable[str] | None = None) -> dict[str, Any]:
        """Set the base commit, context references and constraints of a task; event ``task.context_updated``.

        Only the given fields change; ``revision`` goes up. Context references
        follow the reference grammar; constraints are plain data, never executed.
        Returns the task with its ``base_commit``, ``context_refs`` and ``constraints``.
        Errors: ``not_found``, ``invalid_request`` (format or limits, or no field
        given), ``invalid_state`` (the task is terminal or ``validating``).
        """
        actor = _text("actor", actor, maximum=100)
        fields: list[str] = []
        if base_commit is not None:
            if base_commit != "" and not (isinstance(base_commit, str) and _COMMIT.fullmatch(base_commit)):
                raise _invalid("base_commit", "base_commit must be empty or 7 to 40 lowercase hex characters")
            fields.append("base_commit")
        refs: list[str] | None = None
        if context_refs is not None:
            if isinstance(context_refs, (str, bytes)):
                raise _invalid("context_refs", "context_refs must be a list")
            refs = []
            try:
                for raw in context_refs:
                    ref = validate_reference(raw, field="context_refs")
                    if ref not in refs:
                        refs.append(ref)
            except EnvelopeError as error:
                raise _from_envelope_error(error) from None
            if len(refs) > MAX_CONTEXT_REFS:
                raise _invalid("context_refs", f"context_refs has more than {MAX_CONTEXT_REFS} references")
            fields.append("context_refs")
        texts: list[str] | None = None
        if constraints is not None:
            if isinstance(constraints, (str, bytes)):
                raise _invalid("constraints", "constraints must be a list")
            texts = [_text("constraints", raw, maximum=MAX_CONSTRAINT_CHARS) for raw in constraints]
            if len(texts) > MAX_CONSTRAINTS:
                raise _invalid("constraints", f"constraints has more than {MAX_CONSTRAINTS} items")
            fields.append("constraints")
        if not fields:
            raise _invalid("context", "Give at least one of base_commit, context_refs or constraints")
        with self._tasks.store.transaction() as conn:
            task = self._task_or_missing(task_id, conn)
            if task["state"] in _FROZEN_STATES:
                raise _conflict(f"Cannot change the context of a task that is {task['state']}", task_id=task_id,
                                status=task["state"])
            self.store.set_task_context(conn, task_id, base_commit=base_commit, context_refs=refs,
                                        constraints=texts)
            self._tasks._bump(conn, task)
            self._tasks.store.append_event(conn, "task.context_updated", actor=actor, group_id=task["group_id"],
                                           task_id=task_id, payload={"fields": fields})
            return {**self._tasks._task_view(task_id, conn), **(self.store.task_context(task_id, conn=conn) or {})}

    @service_errors
    def get_envelope(self, task_id: str, *, known_hash: str | None = None) -> dict[str, Any]:
        """The task envelope, or ``{"unchanged": true, "hash": ..., "revision": ...}`` when ``known_hash`` matches.

        The hash is the sha256 of the canonical JSON of the envelope without
        ``events_after`` (sorted keys, compact separators, UTF-8), so an event
        of another task does not invalidate it. Dependencies appear with their
        status and, when they have a result, the ``result_ref`` of its latest
        attempt; ``artifact_refs`` lists the patch blobs of those results. A
        task without an objective uses its title. ``events_after`` is the head
        of the team event log when the envelope is computed; read the events
        after it with a cursor (``read_events``), not through the hash.
        Errors: ``not_found``, ``invalid_request`` (``known_hash``).
        """
        if known_hash is not None and not (isinstance(known_hash, str) and _SHA256.fullmatch(known_hash)):
            raise _invalid("known_hash", "known_hash must be 64 lowercase hex characters")
        if not isinstance(task_id, str):
            raise _invalid("task_id", "task_id must be text")
        with self._tasks.store.db.connect() as conn:
            # The head first: events_after is never ahead of the data, so a reader that
            # starts there sees every later change (at worst one it already reflects).
            head = self.store.event_head(conn=conn)
            tasks, deps = self._tasks._snapshot(conn)
            if task_id not in tasks:
                raise _missing("Task", str(task_id))
            if self._tasks.store.legacy_release_dependencies(task_id=task_id, conn=conn):
                raise _conflict("This task has legacy release dependencies; request context v2",
                                task_id=task_id, required_capability="release_dependencies/v1")
            task = tasks[task_id]
            states = self._tasks._states(tasks)
            depends_on: list[dict[str, Any]] = []
            artifacts: list[str] = []
            for dep in deps:
                if dep["task_id"] != task_id:
                    continue
                other = dep["depends_on_id"]
                item = {"task_id": other, "requires": dep["requires"],
                        "status": graph.effective_status(other, states, deps)}
                latest = self.store.latest_result(other, conn=conn)
                if latest is not None:
                    item["result_ref"] = result_ref(other, latest["attempt"])
                    if latest["patch_sha256"]:
                        artifacts.append(blob_ref(latest["patch_sha256"]))
                depends_on.append(item)
            context = self.store.task_context(task_id, conn=conn) or {}
            gate = task["gate"] or self._tasks._group_or_missing(task["group_id"], conn).get("default_gate", [])
        try:
            envelope = build_task_envelope(
                task_id=task_id, group_id=task["group_id"], revision=task["revision"], attempt=task["attempt"],
                assignee=task["assignee"] or None, status=graph.effective_status(task_id, states, deps),
                objective=task["objective"] or task["title"], depends_on=depends_on, artifact_refs=artifacts,
                context_refs=context.get("context_refs", []), file_scope=task["file_scope"],
                constraints=context.get("constraints", []), gate=gate,
                base_commit=context.get("base_commit", ""), events_after=head)
        except EnvelopeError as error:
            raise _from_envelope_error(error) from None
        if known_hash is not None and known_hash == task_envelope_sha256(envelope):
            return unchanged_envelope(envelope)
        return envelope

    @service_errors
    def get_context_v2(self, task_id: str, *, known_fingerprint: str | None = None,
                       after: int = 0) -> dict[str, Any]:
        """Return the unchanged v1 task envelope plus the complete release context.

        ``after`` is a consistency cursor only: it must not be ahead of the event
        head observed at the start of this read. This method neither reads nor
        acknowledges events. The fingerprint covers the envelope without
        ``events_after`` and the full release context, including provenance.
        """
        if not isinstance(task_id, str):
            raise _invalid("task_id", "task_id must be text")
        after = _integer("after", after, minimum=0)
        if known_fingerprint is not None and not (isinstance(known_fingerprint, str)
                                                   and _SHA256.fullmatch(known_fingerprint)):
            raise _invalid("known_fingerprint", "known_fingerprint must be 64 lowercase hex characters")
        tasks_service = self._tasks
        with tasks_service.store.db.connect() as conn:
            # Pin a WAL read snapshot before sampling the cursor and all context rows.
            conn.execute("BEGIN")
            head = self.store.event_head(conn=conn)
            if after > head:
                raise _invalid("after", "after cursor is ahead of the current event head", head=head)
            tasks, native = tasks_service._snapshot(conn)
            if task_id not in tasks:
                raise _missing("Task", task_id)
            task = tasks[task_id]
            legacy_edges = tasks_service.store.legacy_release_dependencies(conn=conn)
            group_task_ids = {item_id for item_id, item in tasks.items() if item["group_id"] == task["group_id"]}
            group_native = [edge for edge in native if edge["task_id"] in group_task_ids]
            group_legacy = [edge for edge in legacy_edges if edge["task_id"] in group_task_ids]
            states = tasks_service._states(tasks)
            group = tasks_service._group_or_missing(task["group_id"], conn)
            release_data = tasks_service._release_context(conn)
            release_statuses = tasks_service._status_map(tasks, native, release_data)
            context = self.store.task_context(task_id, conn=conn) or {}
            native_items: list[dict[str, Any]] = []
            own_native_items: list[dict[str, Any]] = []
            artifacts: list[str] = []
            for edge in group_native:
                prerequisite = edge["depends_on_id"]
                dependent_id = edge["task_id"]
                is_satisfied = graph.is_satisfied(edge["requires"], states.get(prerequisite))
                item = {"kind": "native", "task_id": prerequisite, "requires": edge["requires"],
                        "dependent_task_id": dependent_id,
                        "source_ref": "", "prerequisite": {"state": states.get(prerequisite),
                        "status": release_statuses.get(prerequisite)},
                        "satisfied": is_satisfied,
                        "reason": "native_requirement_satisfied" if is_satisfied else "native_requirement_unmet",
                        "evidence": []}
                latest = self.store.latest_result(prerequisite, conn=conn)
                if latest is not None:
                    item["result_ref"] = result_ref(prerequisite, latest["attempt"])
                    if latest["patch_sha256"]:
                        item["evidence"].append(blob_ref(latest["patch_sha256"]))
                        if dependent_id == task_id:
                            artifacts.append(blob_ref(latest["patch_sha256"]))
                native_items.append(item)
                if dependent_id == task_id:
                    own_native_items.append(item)
            legacy_items: list[dict[str, Any]] = []
            release_facts: dict[str, Any] = {}
            for edge in group_legacy:
                prerequisite = edge["depends_on_id"]
                dependent_id = edge["task_id"]
                facts = release_data["facts"].get(prerequisite, {})
                dependent_group = release_data["groups"].get(tasks[dependent_id]["group_id"], {})
                threshold = dependent_group.get("legacy_release_mode")
                satisfied, reason, evidence = release_policy.evaluate(threshold, facts)
                legacy_items.append({"kind": "legacy_release", "task_id": prerequisite,
                                     "dependent_task_id": dependent_id,
                                     "threshold": threshold, "source_ref": edge["source_ref"],
                                     "prerequisite": {"state": states.get(prerequisite),
                                     "status": release_statuses.get(prerequisite)},
                                     "satisfied": satisfied, "reason": reason, "evidence": evidence,
                                     "facts": tasks_service._release_facts_view(prerequisite, conn)})
                release_facts[prerequisite] = tasks_service._release_facts_view(prerequisite, conn)
                provenance = tasks_service.store.snapshot_provenance("task", prerequisite, conn=conn)
                if provenance:
                    release_facts[prerequisite]["provenance"] = provenance
            dependencies = native_items + legacy_items
            blockers = [edge for edge in dependencies
                        if edge.get("dependent_task_id") == task_id and not edge["satisfied"]]
            readiness = task["state"]
            if readiness == "open":
                readiness = "blocked" if blockers else "ready"
            group_provenance = tasks_service.store.snapshot_provenance("group", task["group_id"], conn=conn)
            task_provenance = tasks_service.store.snapshot_provenance("task", task_id, conn=conn)
            gate = task["gate"] or group.get("default_gate", [])
            envelope_data = {
                "task_id": task_id, "group_id": task["group_id"], "revision": task["revision"],
                "attempt": task["attempt"], "assignee": task["assignee"] or None,
                "status": graph.effective_status(task_id, states, native),
                "objective": task["objective"] or task["title"],
                "depends_on": [{"task_id": edge["depends_on_id"], "requires": edge["requires"],
                                "status": graph.effective_status(edge["depends_on_id"], states, native),
                                **({"result_ref": result_ref(edge["depends_on_id"], latest["attempt"])}
                                   if (latest := self.store.latest_result(edge["depends_on_id"], conn=conn)) else {})}
                               for edge in native if edge["task_id"] == task_id],
                "artifact_refs": artifacts,
                "context_refs": context.get("context_refs", []), "file_scope": task["file_scope"],
                "constraints": context.get("constraints", []), "gate": gate,
                "base_commit": context.get("base_commit", ""), "events_after": head,
            }
        try:
            envelope = build_task_envelope(**envelope_data)
        except EnvelopeError as error:
            raise _from_envelope_error(error) from None
        release_context = {
            "task_id": task_id, "group_id": task["group_id"],
            "legacy_release_mode": group.get("legacy_release_mode"),
            "group_policy": {"authorized": group["authorized"],
                             "lifecycle_state": group.get("lifecycle_state", "open"),
                             "revision": group.get("revision", 1),
                             "default_requires": group["default_requires"],
                             "default_gate": group.get("default_gate", []),
                             "legacy_release_mode": group.get("legacy_release_mode")},
            "readiness": readiness, "blockers": blockers, "dependencies": dependencies,
            "facts": release_facts,
            "provenance": {"task": task_provenance, "group": group_provenance},
        }
        fingerprint_data = {"task_envelope": {key: value for key, value in envelope.items()
                                               if key != "events_after"},
                            "release_context": release_context}
        fingerprint = hashlib.sha256(canonical_json(fingerprint_data)).hexdigest()
        if known_fingerprint == fingerprint:
            return {"unchanged": True, "fingerprint": fingerprint}
        return {"capabilities": ["release_dependencies/v1"], "task_envelope": envelope,
                "release_context": release_context, "fingerprint": fingerprint}

    # -------- blobs and artifacts --------
    @service_errors
    def put_blob(self, content: bytes, *, actor: str, media_type: str = "application/octet-stream",
                 expected_sha256: str | None = None) -> dict[str, Any]:
        """Store content by its sha256; returns ``{ref, sha256, size, media_type, created}``.

        The same content is stored once (``created`` is false the second time,
        and the first ``media_type`` stays). Errors: ``invalid_request`` (not
        bytes, too large, bad media type, or ``expected_sha256`` does not match).
        """
        actor = _text("actor", actor, maximum=100)
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise _invalid("content", "content must be bytes")
        content = bytes(content)
        limit = min(self.blob_max_bytes, HARD_MAX_BLOB_BYTES)
        if len(content) > limit:
            raise _invalid("content", f"The blob is {len(content)} bytes; the limit is {limit}", limit=limit)
        if not (isinstance(media_type, str) and len(media_type) <= 100 and _MEDIA_TYPE.fullmatch(media_type)):
            raise _invalid("media_type", f"media_type must be type/subtype in lowercase: {media_type!r}")
        sha256 = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None:
            expected = expected_sha256.removeprefix("sha256:") if isinstance(expected_sha256, str) else None
            if expected != sha256:
                raise _invalid("expected_sha256", "The content does not match expected_sha256", sha256=sha256)
        with self._tasks.store.transaction() as conn:
            row, created = self.store.put_blob(conn, content, actor=actor, media_type=media_type)
        return {"ref": blob_ref(sha256), "sha256": sha256, "size": row["size"], "media_type": row["media_type"],
                "created": created}

    @service_errors
    def get_artifact(self, ref: str, *, offset: int = 0, length: int = DEFAULT_READ_BYTES) -> dict[str, Any]:
        """Read a range of a blob: ``{ref, sha256, size, media_type, offset, length, encoding, content, eof}``.

        Text media types (``text/*``, ``application/json``) come back as UTF-8
        and the rest as base64. A text range that would cut a character ends
        before it (``length`` is what was returned); text that is not UTF-8,
        or a range that starts inside a character, comes back as base64. ``length`` is at most 1 MiB. A ``result:``
        reference is read with ``get_result``.
        Errors: ``not_found`` (no such blob), ``invalid_request`` (a scheme the
        core does not resolve, or a bad range).
        """
        try:
            parsed = parse_reference(ref, field="ref")
        except EnvelopeError as error:
            raise _from_envelope_error(error) from None
        if parsed.scheme == "result":
            raise _invalid("ref", "A result reference is read with get_result", ref=parsed.text)
        if not parsed.resolvable:
            raise _invalid("ref", f"The core does not resolve {parsed.scheme} references: read it with your own tools",
                           ref=parsed.text)
        offset = _integer("offset", offset, minimum=0)
        length = _integer("length", length, minimum=1, maximum=MAX_READ_BYTES)
        sha256 = parsed.sha256 or ""
        row = self.store.get_blob(sha256)
        if row is None:
            raise _missing("Blob", parsed.text)
        if offset > row["size"]:
            raise _invalid("offset", f"offset is past the end of the blob ({row['size']} bytes)", size=row["size"])
        try:
            data = self.store.read_blob(sha256, offset=offset, length=length)
        except FileNotFoundError:
            raise _missing("Blob", parsed.text) from None
        text = (_utf8_text(data, at_end=offset + len(data) >= row["size"])
                if _is_text(row["media_type"]) else None)
        if text is not None:
            data = text.encode("utf-8")
            encoding, content = "utf-8", text
        else:
            encoding, content = "base64", base64.b64encode(data).decode("ascii")
        return {"ref": parsed.text, "sha256": sha256, "size": row["size"], "media_type": row["media_type"],
                "offset": offset, "length": len(data), "encoding": encoding, "content": content,
                "eof": offset + len(data) >= row["size"]}

    # -------- results --------
    @service_errors
    def submit_result(self, task_id: str, result: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        """Store the result of the current attempt and submit the task; returns ``{result, duplicate, task}``.

        The task must be ``running`` and ``actor`` its assignee, as for
        ``submit``. Result and transition commit together; events
        ``task.submitted`` and ``result.submitted`` (and ``task.accepted`` for an activity, which needs no
        review). The same task, attempt and
        patch with the same content returns the stored result with
        ``duplicate`` true and writes nothing.
        Errors: ``not_found``, ``invalid_request`` (format, limits, a patch
        blob that does not exist), ``invalid_state`` (status, not the assignee,
        or other content for the same key).
        """
        actor = _text("actor", actor, maximum=100)
        with self._tasks.store.transaction() as conn:
            task = self._task_or_missing(task_id, conn)
            attempt = task["attempt"]
            if attempt < 1:
                raise _conflict(f"Cannot submit a result for {task_id}: it was never started", task_id=task_id,
                                status=task["state"])
            try:
                validated = validate_task_result(result, task_id=task_id, attempt=attempt, agent=actor)
            except EnvelopeError as error:
                raise _from_envelope_error(error) from None
            encoded = canonical_json(validated).decode("utf-8")
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            patch = patch_sha256(validated)
            ref = result_ref(task_id, attempt)
            stored = self.store.find_result(task_id, attempt, patch, conn=conn)
            if stored is not None:
                if stored["result_sha256"] != digest:
                    raise _conflict(f"Attempt {attempt} of {task_id} already has a result with other content",
                                    task_id=task_id, attempt=attempt, ref=ref)
                return {"result": self._result_view(stored), "duplicate": True,
                        "task": self._tasks._task_view(task_id, conn)}
            blob = self.store.get_blob(patch, conn=conn) if patch else None
            if patch and blob is None:
                raise _invalid("patch.blob", f"Unknown patch blob sha256:{patch}; store it with put_blob first")
            # The single transition function checks the status and the assignee.
            view = self._tasks._apply(conn, task_id, "submit", actor=actor, auto_accept=False)
            try:
                row = self.store.insert_result(conn, task_id=task_id, attempt=attempt, agent=actor,
                                               result_json=encoded, result_sha256=digest, patch_sha256=patch)
            except sqlite3.IntegrityError:
                raise _conflict(f"Attempt {attempt} of {task_id} already has a result", task_id=task_id,
                                attempt=attempt, ref=ref) from None
            payload: dict[str, Any] = {"attempt": attempt, "result_ref": ref,
                                       "files_count": len(validated["patch"]["files"]) if patch else 0}
            if blob is not None:
                payload.update(patch_blob=blob_ref(patch), patch_size=blob["size"])
            self._tasks.store.append_event(conn, "result.submitted", actor=actor, group_id=task["group_id"],
                                           task_id=task_id, ref=ref, payload=payload)
            if task.get("kind") == "activity":
                # An activity needs no review: with its result stored, submitting it closes it (task.accepted).
                view = self._tasks._accept_activity(conn, task_id, actor)
            return {"result": self._result_view(row), "duplicate": False, "task": view}

    @service_errors
    def get_result(self, ref_or_task_id: str, *, attempt: int | None = None) -> dict[str, Any]:
        """A task result by ``result://<task_id>/<attempt>`` or by task id (latest attempt unless ``attempt``).

        Errors: ``not_found``, ``invalid_request`` (a reference that is not a
        result, or a reference together with ``attempt``).
        """
        if isinstance(ref_or_task_id, str) and ":" in ref_or_task_id:
            try:
                parsed = parse_reference(ref_or_task_id, field="ref")
            except EnvelopeError as error:
                raise _from_envelope_error(error) from None
            if parsed.scheme != "result":
                raise _invalid("ref", f"Not a result reference: {ref_or_task_id!r}")
            if attempt is not None:
                raise _invalid("attempt", "The attempt goes in the reference, not beside it")
            task_id, attempt = parsed.task_id or "", parsed.attempt
        else:
            task_id = ref_or_task_id
            if attempt is not None:
                attempt = _integer("attempt", attempt, minimum=1)
        with self._tasks.store.db.connect() as conn:
            self._task_or_missing(task_id, conn)
            rows = self.store.results(task_id, attempt=attempt, conn=conn)
        if not rows:
            raise _missing("Result", result_ref(task_id, attempt) if attempt else task_id)
        return self._result_view(rows[0])

    @service_errors
    def list_results(self, task_id: str) -> list[dict[str, Any]]:
        """Summaries and references of every attempt's result, most recent first. Errors: ``not_found``."""
        with self._tasks.store.db.connect() as conn:
            self._task_or_missing(task_id, conn)
            rows = self.store.results(task_id, conn=conn)
        summaries = []
        for row in rows:
            result = row["result"]
            summaries.append({
                "ref": result_ref(row["task_id"], row["attempt"]), "task_id": row["task_id"],
                "attempt": row["attempt"], "agent": row["agent"], "summary": result["summary"],
                "patch_blob": blob_ref(row["patch_sha256"]) if row["patch_sha256"] else None,
                "files_count": len(result["patch"]["files"]) if result.get("patch") else 0,
                "result_sha256": row["result_sha256"], "created_at": row["created_at"],
            })
        return summaries

    # -------- cursors --------
    @service_errors
    def get_cursor(self, consumer: str, *, scope: str = "") -> dict[str, Any]:
        """``{consumer, scope, seq, head}``; a consumer without a cursor is at 0.

        ``scope`` is ``""`` (the whole team), ``group:<id>`` or ``task:<id>``.
        Errors: ``invalid_request``.
        """
        consumer, scope = _consumer(consumer), _scope(scope)
        with self._tasks.store.db.connect() as conn:
            return self._cursor_view(consumer, scope, conn)

    @service_errors
    def read_events(self, consumer: str, *, scope: str = "", types: Iterable[str] | None = None,
                    assigned_to_me: bool = False, limit: int = 100) -> dict[str, Any]:
        """Events after the consumer's cursor within ``scope``: ``{events, cursor, head, has_more}``.

        Does not move the cursor: acknowledge with ``ack_events``, usually with
        the ``seq`` of the last event handled. ``types`` keeps only those event
        types and ``assigned_to_me`` only the events of tasks currently assigned
        to ``consumer``; ``limit`` (1..1000) counts the events returned.
        ``has_more`` is true when more events of the scope follow the last one
        returned. Errors: ``invalid_request``.
        """
        consumer, scope = _consumer(consumer), _scope(scope)
        limit = _integer("limit", limit, minimum=1, maximum=MAX_READ_EVENTS)
        wanted: set[str] | None = None
        if types is not None:
            if isinstance(types, (str, bytes)) or not isinstance(types, Iterable):
                raise _invalid("types", "types must be a list")
            wanted = {_text("types", item, maximum=MAX_EVENT_TYPE_CHARS) for item in types}
        if not isinstance(assigned_to_me, bool):
            raise _invalid("assigned_to_me", "assigned_to_me must be true or false")
        kind, _, identifier = scope.partition(":")
        where = {"group_id": identifier if kind == "group" else None, "task_id": identifier if kind == "task" else None}
        tasks = self._tasks.store
        with tasks.db.connect() as conn:
            cursor = self.store.get_cursor(consumer, scope, conn=conn)
            head = self.store.event_head(conn=conn)
            mine = {task["id"] for task in tasks.list_tasks(assignee=consumer, conn=conn)} if assigned_to_me else set()
            events: list[dict[str, Any]] = []
            after, has_more = cursor, False
            # Filters apply after reading, so the log is read in batches until ``limit`` events match.
            while True:
                batch = tasks.events(after=after, limit=_EVENT_BATCH, conn=conn, **where)
                for event in batch:
                    if len(events) == limit:
                        has_more = True
                        break
                    after = event["seq"]
                    if (wanted is None or event["type"] in wanted) and (not assigned_to_me or event["task_id"] in mine):
                        events.append(event)
                if has_more or len(batch) < _EVENT_BATCH:
                    break
            if not has_more and len(events) == limit:
                has_more = bool(tasks.events(after=after, limit=1, conn=conn, **where))
        return {"events": events, "cursor": cursor, "head": head, "has_more": has_more}

    @service_errors
    def ack_events(self, consumer: str, seq: int, *, scope: str = "") -> dict[str, Any]:
        """Move the cursor to ``seq`` (never backwards); returns the same shape as ``get_cursor``.

        Errors: ``invalid_request`` (``seq`` is not an integer >= 0 or is past the head of the log).
        """
        consumer, scope = _consumer(consumer), _scope(scope)
        seq = _integer("seq", seq, minimum=0)
        with self._tasks.store.transaction() as conn:
            head = self.store.event_head(conn=conn)
            if seq > head:
                raise _invalid("seq", f"seq {seq} is past the head of the event log ({head})", head=head)
            self.store.set_cursor(conn, consumer, scope, seq)
            return self._cursor_view(consumer, scope, conn)
