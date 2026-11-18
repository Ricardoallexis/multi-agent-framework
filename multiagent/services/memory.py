# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project memory services (S12.A1, CAP-T20): facts, decisions and conventions that belong to one project.

An entry (``mem-<12 hex>``) has a ``kind`` (``fact``, ``decision`` or ``convention``), a ``title`` and a ``body``,
a ``confidence`` (``low``, ``medium`` or ``high``) and its provenance: who wrote it (``source.actor``), what kind of
source that is and the run or task it came from (``source.ref``, optional). The trust level is computed from the
source with ``document_trust.Provenance`` and is never taken from the request: only a ``user`` actor is ``trusted``;
an ``agent`` (``model_output``), an ``executor`` (``executor_result``) or a ``person`` label is ``untrusted``, and an
untrusted author cannot claim ``high`` confidence (it is lowered to ``medium``). Trust does not make an entry an
instruction: a step that opts in (``project_memory`` in the workflow, see ``context.project_memory``) receives every
entry quoted as data, with this provenance in the envelope header.

Anyone with a valid actor may write; only a ``user`` actor may delete (``invalid_state``, ``confirmation_required``
otherwise), so an agent cannot erase what the user decided. There is no editing: delete and write again. A project
holds at most ``MAX_ENTRIES`` entries. A project is named by its id or its name. No embeddings and no search: reading
is by project and, optionally, by kind.

Every method returns JSON-ready data and raises only ``ServiceError``: ``not_found`` (project or entry),
``invalid_request`` (malformed data) or ``invalid_state`` (a non-user deletion, a full project).
"""
from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from ..context.project_memory import CONFIDENCES, KINDS, entry_sha256
from ..db.memory_store import MemoryStore
from ..document_trust import Provenance, SourceKind
from .errors import service_errors
from .projects import actor_kind
from .tasks import _conflict, _invalid, _missing, _text

if TYPE_CHECKING:
    from . import ApplicationServices

MAX_ENTRIES = 200
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 2000
DEFAULT_CONFIDENCE = "medium"
_ENTRY_ID = re.compile(r"^mem-[0-9a-f]{12}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")      # tabs and newlines stay in a body
_SOURCE_KIND_BY_ACTOR = {"user": SourceKind.USER.value, "agent": SourceKind.MODEL_OUTPUT.value,
                         "executor": SourceKind.EXECUTOR_RESULT.value}


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "project_id": row["project_id"], "kind": row["kind"], "title": row["title"],
            "body": row["body"], "confidence": row["confidence"], "trust": row["trust"],
            "source": {"kind": row["source_kind"], "actor": row["source_actor"], "ref": row["source_ref"]},
            "sha256": row["sha256"], "created_at": row["created_at"]}


class MemoryServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: MemoryStore | None = None

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            self._store = MemoryStore(self._services.system.db)
        return self._store

    def _project(self, project: Any) -> str:
        name = _text("project", project, maximum=128)
        project_id = self.store.project_id(name)
        if project_id is None:
            raise _missing("Project", name)
        return project_id

    @service_errors
    def create(self, project: str, *, kind: str, title: str, body: str, actor: str, confidence: str | None = None,
               source_ref: str = "") -> dict[str, Any]:
        """Write an entry; returns it with its ``id``, ``trust`` and ``source``.

        ``actor`` is the author (``user:...``, ``agent:...``, ``executor:...`` or ``person:...``); ``source_ref``
        names the run, task or document it came from (no blanks, up to 200 characters). Errors: ``not_found``
        (project), ``invalid_request``, ``invalid_state`` (the project holds ``MAX_ENTRIES`` entries).
        """
        project_id = self._project(project)
        if kind not in KINDS:
            raise _invalid("kind", f"kind must be one of {', '.join(KINDS)}")
        title = _text("title", title, maximum=MAX_TITLE_CHARS)
        body = _text("body", body, maximum=MAX_BODY_CHARS)
        if "\n" in title or _CONTROL.search(title):
            raise _invalid("title", "title is one line and may not hold control characters")
        if _CONTROL.search(body):
            raise _invalid("body", "body may not hold control characters")
        if confidence is not None and confidence not in CONFIDENCES:
            raise _invalid("confidence", f"confidence must be one of {', '.join(CONFIDENCES)}")
        actor = _text("actor", actor, maximum=100)
        author = actor_kind(actor)
        if author is None:
            raise _invalid("actor", "actor must be user:[name], person:<name>, agent:<app>[/<model>] or executor:<id>")
        source_ref = _text("source_ref", source_ref, maximum=200, required=False)
        if any(char.isspace() for char in source_ref) or _CONTROL.search(source_ref):
            raise _invalid("source_ref", "source_ref has no blanks")
        source_kind = _SOURCE_KIND_BY_ACTOR.get(author, SourceKind.UNKNOWN.value)
        trust = Provenance(source_kind).trust.value               # computed, never requested
        confidence = confidence or DEFAULT_CONFIDENCE
        if trust != "trusted" and confidence == "high":
            confidence = "medium"
        with self.store.db.tx() as conn:
            if self.store.count(project_id, conn=conn) >= MAX_ENTRIES:
                raise _conflict(f"A project holds at most {MAX_ENTRIES} memory entries; delete some first",
                                project=project_id, reason="memory_full")
            row = self.store.insert(
                conn, entry_id=f"mem-{uuid.uuid4().hex[:12]}", project_id=project_id, kind=kind, title=title,
                body=body, confidence=confidence, source_kind=source_kind, source_actor=actor,
                source_ref=source_ref, trust=trust, sha256=entry_sha256(title, body))
        return _view(row)

    @service_errors
    def list(self, project: str, *, kind: str | None = None) -> dict[str, Any]:
        """The entries of a project, oldest first: ``{project_id, entries, count}``. Errors: ``not_found``."""
        project_id = self._project(project)
        if kind is not None and kind not in KINDS:
            raise _invalid("kind", f"kind must be one of {', '.join(KINDS)}")
        entries = [_view(row) for row in self.store.list(project_id, kind=kind)]
        return {"project_id": project_id, "entries": entries, "count": len(entries)}

    @service_errors
    def get(self, entry_id: str) -> dict[str, Any]:
        """One entry. Errors: ``not_found``, ``invalid_request`` (a malformed id)."""
        row = self.store.get(self._entry_id(entry_id))
        if row is None:
            raise _missing("Memory entry", entry_id)
        return _view(row)

    @service_errors
    def delete(self, entry_id: str, *, actor: str) -> dict[str, Any]:
        """Delete an entry (``{deleted, id}``). Only a ``user`` actor may: otherwise ``invalid_state``
        (``confirmation_required``). Errors: ``not_found``, ``invalid_request``."""
        entry_id = self._entry_id(entry_id)
        actor = _text("actor", actor, maximum=100)
        if actor_kind(actor) != "user":
            raise _conflict("Only the user can delete a memory entry; confirm it as user:...", actor=actor,
                            reason="confirmation_required")
        with self.store.db.tx() as conn:
            if not self.store.delete(conn, entry_id):
                raise _missing("Memory entry", entry_id)
        return {"deleted": True, "id": entry_id}

    @staticmethod
    def _entry_id(value: Any) -> str:
        if not isinstance(value, str) or not _ENTRY_ID.fullmatch(value):
            raise _invalid("entry_id", f"Invalid memory entry id: {value!r}")
        return value
