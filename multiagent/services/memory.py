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

Anyone with a valid actor may write; only a ``user`` actor may ask to delete (``invalid_state``,
``confirmation_required`` otherwise), so an agent cannot erase what the user decided.

A ``user:...`` label is only a claim: an executor runs with the same system account as the person and can type it.
So a ``user`` entry is born ``untrusted`` (never ``high``) and waits for a ``memory_trust`` approval; a deletion waits
for a ``memory_delete`` approval. Both are decided in the GUI with the one-use code of ``user_approvals``, and the effect
is applied by ``apply_approval`` from ``UserApprovalServices.decide``, the only place that learns of a person's click.
Nothing else turns an entry ``trusted`` or removes one on a ``user`` label. There is no editing: delete and write again. A project
holds at most ``MAX_ENTRIES`` entries. A project is named by its id or its name. No embeddings and no search: reading
is by project and, optionally, by kind.

MEM-T02: an untrusted entry that contradicts one the person confirmed (same project and kind, same normalized title,
a different body) is never quoted in a prompt (``context.project_memory.select_entries``). Writing one adds
``conflicts_with`` (the id of the confirmed entry) to the view, and keeping the new one takes its ``memory_trust`` approval
(for an ``agent:`` or ``executor:`` author too, opened only when it clashes). Approving it trusts the new entry and
opens a ``memory_delete`` for the old one, which waits for its own click: until then both are trusted and quoted.

Every method returns JSON-ready data and raises only ``ServiceError``: ``not_found`` (project or entry),
``invalid_request`` (malformed data) or ``invalid_state`` (a non-user deletion, a full project).
"""
from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from ..context.project_memory import CONFIDENCES, KINDS, contradicted_by, entry_sha256
from ..db.memory_store import MemoryStore
from ..document_trust import Provenance, SourceKind
from .errors import ServiceError, service_errors
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
TRUST_KIND = "memory_trust"
DELETE_KIND = "memory_delete"
PENDING_REASON = "user_confirmation_pending"
CONFLICT_REASON = "contradicts_confirmed_entry"
_LOCATION_TITLE = 80            # an approval description holds at most 300 characters
PAIR_TITLE_CHARS = 80           # what «Pendientes» shows of each entry of a clash: fixed sizes
PAIR_BODY_CHARS = 240
_SOURCE_KIND_BY_ACTOR = {"user": SourceKind.USER.value, "agent": SourceKind.MODEL_OUTPUT.value,
                         "executor": SourceKind.EXECUTOR_RESULT.value}


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "project_id": row["project_id"], "kind": row["kind"], "title": row["title"],
            "body": row["body"], "confidence": row["confidence"], "trust": row["trust"],
            "source": {"kind": row["source_kind"], "actor": row["source_actor"], "ref": row["source_ref"]},
            "sha256": row["sha256"], "created_at": row["created_at"]}


def _cut(row: dict[str, Any]) -> dict[str, str]:
    def cut(text: str, size: int) -> str:
        return text if len(text) <= size else text[:size - 1].rstrip() + "…"
    return {"id": row["id"], "title": cut(row["title"], PAIR_TITLE_CHARS), "body": cut(row["body"], PAIR_BODY_CHARS)}


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

    def entries_for_project_name(self, project_name: str) -> list[dict[str, Any]]:
        """Raw memory rows for a bundle run, matched by name in the system database."""
        with self.store.db.connect() as conn:
            project = conn.execute("SELECT id FROM projects WHERE name=?", (project_name,)).fetchone()
            if project is None:
                return []
            return self.store.list(project["id"], conn=conn)

    @service_errors
    def create(self, project: str, *, kind: str, title: str, body: str, actor: str, confidence: str | None = None,
               source_ref: str = "") -> dict[str, Any]:
        """Write an entry; returns it with its ``id``, ``trust`` and ``source``.

        ``actor`` is the author (``user:...``, ``agent:...``, ``executor:...`` or ``person:...``); ``source_ref``
        names the run, task or document it came from (no blanks, up to 200 characters). A ``user:`` author is
        ``untrusted`` until a person approves ``memory_trust`` in the GUI: the view then adds ``pending_approval``
        (the approval id) and ``details.reason = "user_confirmation_pending"``, and a ``high`` confidence waits too
        (it is stored as ``medium``). Errors: ``not_found`` (project), ``invalid_request``, ``invalid_state`` (the
        project holds ``MAX_ENTRIES`` entries).
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
        wants_trust = author == "user"                            # MEM-T01: the label is a claim; a person confirms it
        if wants_trust:
            trust = "untrusted"
        confidence = confidence or DEFAULT_CONFIDENCE
        requested = confidence
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
        view = _view(row)
        confirmed = self._contradicted(row)
        if confirmed:
            view = {**view, "conflicts_with": confirmed[-1]["id"]}
        if wants_trust or confirmed:
            location = {"source": "memory", "entry": row["id"], "title": title[:_LOCATION_TITLE]}
            if requested == "high" and wants_trust:
                location["confidence"] = "high"
            if confirmed:
                location["conflicts_with"] = confirmed[-1]["id"]
            destination = {"memory": row["id"], "action": "trust"}
            approval = None if wants_trust else self._services.user_approvals.pending_like(
                TRUST_KIND, sha256=row["sha256"], destination=destination)
            if approval is None:
                approval = self._services.user_approvals.request(
                    kind=TRUST_KIND, destination=destination, location=location, sha256=row["sha256"])
            reason = PENDING_REASON if wants_trust else CONFLICT_REASON
            view = {**view, "pending_approval": approval["id"], "details": {"reason": reason}}
        return view

    def _contradicted(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """The confirmed entries of the project that the untrusted ``row`` contradicts, oldest first."""
        return contradicted_by(row, self.store.list(row["project_id"]))

    def conflict_pair(self, entry_id: str, other_id: str) -> dict[str, Any] | None:
        """The two entries of a clash as «Pendientes» shows them: ``{new, existing}``, each ``{id, title, body}`` cut to
        a fixed size and read from the entries by their ids (nothing is copied into the approval). ``None`` when
        either is gone or ``other_id`` is not a confirmed entry that ``entry_id`` contradicts."""
        try:
            new, old = self.store.get(self._entry_id(entry_id)), self.store.get(self._entry_id(other_id))
        except ServiceError:
            return None
        if new is None or old is None or not contradicted_by(new, [old]):
            return None
        return {"new": _cut(new), "existing": _cut(old)}

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
        """Ask to delete an entry. Only a ``user`` actor may: otherwise ``invalid_state`` (``confirmation_required``).

        Nothing is deleted here: it opens (or reuses) a ``memory_delete`` approval and returns ``{deleted: False, id,
        pending_approval, details: {reason: "user_confirmation_pending"}}``. The entry goes when a person approves it
        in the GUI. Errors: ``not_found``, ``invalid_request``."""
        entry_id = self._entry_id(entry_id)
        actor = _text("actor", actor, maximum=100)
        if actor_kind(actor) != "user":
            raise _conflict("Only the user can delete a memory entry; confirm it as user:...", actor=actor,
                            reason="confirmation_required")
        row = self.store.get(entry_id)
        if row is None:
            raise _missing("Memory entry", entry_id)
        found = self._ask_delete(row)
        return {"deleted": False, "id": entry_id, "pending_approval": found["id"],
                "details": {"reason": PENDING_REASON}}

    def apply_approval(self, approval: dict[str, Any]) -> bool:
        """Carry out a memory approval a person just approved (called by ``UserApprovalServices.decide``).

        The approval is spent first and only for this entry, this action and this content digest; an entry that
        changed or went away is left alone. Returns whether it was applied.
        """
        destination = approval.get("destination")
        kind = approval.get("kind")
        if (kind not in (TRUST_KIND, DELETE_KIND) or not isinstance(destination, dict)
                or destination.get("action") != ("trust" if kind == TRUST_KIND else "delete")):
            return False
        entry_id = self._entry_id(destination.get("memory"))
        if not self._services.user_approvals.consume(approval["id"], sha256=approval["sha256"],
                                                     destination=destination):
            return False
        with self.store.db.tx() as conn:
            row = self.store.get(entry_id, conn=conn)
            if row is None or row["sha256"] != approval["sha256"]:
                return False
            if kind == DELETE_KIND:
                return self.store.delete(conn, entry_id)
            if row["trust"] == "trusted":
                return False
            location = approval.get("location")
            location = location if isinstance(location, dict) else {}
            old = self._contradicted(row)
            if actor_kind(row["source_actor"]) != "user" and not any(
                    item["id"] == location.get("conflicts_with") for item in old):
                return False                    # only a clash makes a non-user entry confirmable (MEM-T02)
            wanted = location.get("confidence")
            if not self.store.set_trust(conn, entry_id, "trusted", confidence=wanted if wanted in CONFIDENCES else None):
                return False
        for item in old:                        # the old one waits for its own click; nothing is deleted here
            self._ask_delete(item)
        return True

    def _ask_delete(self, row: dict[str, Any]) -> dict[str, Any]:
        """Open (or reuse) the ``memory_delete`` approval of an entry."""
        approvals = self._services.user_approvals
        destination = {"memory": row["id"], "action": "delete"}
        found = approvals.pending_like(DELETE_KIND, sha256=row["sha256"], destination=destination)
        if found is None:
            found = approvals.request(
                kind=DELETE_KIND, destination=destination, sha256=row["sha256"],
                location={"source": "memory", "entry": row["id"], "title": row["title"][:_LOCATION_TITLE]})
        return found

    @staticmethod
    def _entry_id(value: Any) -> str:
        if not isinstance(value, str) or not _ENTRY_ID.fullmatch(value):
            raise _invalid("entry_id", f"Invalid memory entry id: {value!r}")
        return value
