# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Envelope services: task envelopes, task results, artifact references and event cursors.

Contract fixed by ORQ-T22; implemented by ORQ-T25 on the envelope store
(``multiagent.db.envelope_store``, ORQ-T23), the pure envelope validation
(``multiagent.tasks.envelopes``, ORQ-T24) and the single task transition
function of ``services.tasks``. Until ORQ-T25 every method raises
``NotImplementedError("ORQ-T25")`` and the service is not part of
``ApplicationServices``.

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

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import ApplicationServices

TASK_ENVELOPE_SCHEMA = "multiagent.task_envelope/1"
TASK_RESULT_SCHEMA = "multiagent.task_result/1"
REFERENCE_SCHEMES = ("result", "blob", "doc", "report", "rules")
RESOLVABLE_SCHEMES = ("result", "blob")
MAX_REFERENCE_CHARS = 200
MAX_CONTEXT_REFS = 20
MAX_CONSTRAINTS = 20
MAX_CONSTRAINT_CHARS = 200
MAX_SUMMARY_CHARS = 1500
MAX_DECISIONS = 20
MAX_DECISION_CHARS = 300
MAX_CONTRACT_CHANGES_CHARS = 1500
MAX_TESTS_NOT_RUN = 20
MAX_TEST_NOT_RUN_CHARS = 200
MAX_REQUESTS = 10
MAX_PATCH_FILES = 200
MAX_RESULT_BYTES = 16 * 1024
DEFAULT_BLOB_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_READ_BYTES = 64 * 1024
MAX_READ_BYTES = 1024 * 1024
EVENT_TYPES = ("result.submitted", "task.context_updated")


class EnvelopeServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    # -------- task context and envelope --------
    def set_task_context(self, task_id: str, *, actor: str, base_commit: str | None = None,
                         context_refs: Iterable[str] | None = None,
                         constraints: Iterable[str] | None = None) -> dict[str, Any]:
        """Set the base commit, context references and constraints of a task; event ``task.context_updated``.

        Only the given fields change; ``revision`` goes up. Context references
        follow the reference grammar; constraints are plain data, never executed.
        Errors: ``not_found``, ``invalid_request`` (format or limits),
        ``invalid_state`` (the task is terminal or ``validating``).
        """
        raise NotImplementedError("ORQ-T25")

    def get_envelope(self, task_id: str, *, known_hash: str | None = None) -> dict[str, Any]:
        """The task envelope, or ``{"unchanged": true, "hash": ..., "revision": ...}`` when ``known_hash`` matches.

        The hash is the sha256 of the canonical JSON of the envelope (sorted
        keys, compact separators, UTF-8). Dependencies appear with their
        status and, when they have a result, the ``result_ref`` of its latest
        attempt; ``artifact_refs`` lists the patch blobs of those results.
        Errors: ``not_found``.
        """
        raise NotImplementedError("ORQ-T25")

    # -------- blobs and artifacts --------
    def put_blob(self, content: bytes, *, actor: str, media_type: str = "application/octet-stream",
                 expected_sha256: str | None = None) -> dict[str, Any]:
        """Store content by its sha256; returns ``{ref, sha256, size, media_type, created}``.

        The same content is stored once (``created`` is false the second time).
        Errors: ``invalid_request`` (too large, bad media type, or
        ``expected_sha256`` does not match).
        """
        raise NotImplementedError("ORQ-T25")

    def get_artifact(self, ref: str, *, offset: int = 0, length: int = DEFAULT_READ_BYTES) -> dict[str, Any]:
        """Read a range of a blob: ``{ref, sha256, size, media_type, offset, length, encoding, content, eof}``.

        Text media types come back as UTF-8, the rest as base64. ``length`` is at most 1 MiB.
        Errors: ``not_found`` (no such blob), ``invalid_request`` (a scheme the
        core does not resolve, or a bad range).
        """
        raise NotImplementedError("ORQ-T25")

    # -------- results --------
    def submit_result(self, task_id: str, result: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        """Store the result of the current attempt and submit the task; returns ``{result, duplicate, task}``.

        The task must be ``running`` and ``actor`` its assignee, as for
        ``submit``. Result and transition commit together; events
        ``task.submitted`` and ``result.submitted``. The same task, attempt and
        patch with the same content returns the stored result with
        ``duplicate`` true and writes nothing.
        Errors: ``not_found``, ``invalid_request`` (format, limits, a patch
        blob that does not exist), ``invalid_state`` (status, not the assignee,
        or other content for the same key).
        """
        raise NotImplementedError("ORQ-T25")

    def get_result(self, ref_or_task_id: str, *, attempt: int | None = None) -> dict[str, Any]:
        """A task result by ``result://<task_id>/<attempt>`` or by task id (latest attempt unless ``attempt``).

        Errors: ``not_found``, ``invalid_request`` (a reference that is not a result).
        """
        raise NotImplementedError("ORQ-T25")

    def list_results(self, task_id: str) -> list[dict[str, Any]]:
        """Summaries and references of every attempt's result, most recent first. Errors: ``not_found``."""
        raise NotImplementedError("ORQ-T25")

    # -------- cursors --------
    def get_cursor(self, consumer: str, *, scope: str = "") -> dict[str, Any]:
        """``{consumer, scope, seq, head}``; a consumer without a cursor is at 0.

        ``scope`` is ``""`` (the whole team), ``group:<id>`` or ``task:<id>``.
        Errors: ``invalid_request``.
        """
        raise NotImplementedError("ORQ-T25")

    def read_events(self, consumer: str, *, scope: str = "", types: Iterable[str] | None = None,
                    assigned_to_me: bool = False, limit: int = 100) -> dict[str, Any]:
        """Events after the consumer's cursor within ``scope``: ``{events, cursor, head, has_more}``.

        Does not move the cursor. ``assigned_to_me`` keeps only events of tasks
        currently assigned to ``consumer``. Errors: ``invalid_request``.
        """
        raise NotImplementedError("ORQ-T25")

    def ack_events(self, consumer: str, seq: int, *, scope: str = "") -> dict[str, Any]:
        """Move the cursor to ``seq`` (never backwards); returns the same shape as ``get_cursor``.

        Errors: ``invalid_request`` (``seq`` is not an integer >= 0 or is past the head of the log).
        """
        raise NotImplementedError("ORQ-T25")