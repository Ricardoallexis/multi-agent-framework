# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Saved plan proposals, revisions and explicit human decisions."""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from ..db.database import utcnow
from ..db.plan_proposal_store import PlanProposalStore
from ..external_output import canonical_json, sha256
from ..planning import validate_proposal
from ..teams.step_roles import bind_step_roles
from ..workflow_validation import WorkflowDefinitionError
from .errors import service_errors
from .memory import MAX_BODY_CHARS, MAX_TITLE_CHARS
from .projects import actor_kind
from .tasks import _conflict, _invalid, _missing, _text
from .teams import _channel, _note

if TYPE_CHECKING:
    from . import ApplicationServices


def _actor(value: Any, *, human: bool = False) -> str:
    actor = _text("actor", value, maximum=100)
    if actor_kind(actor) is None:
        raise _invalid("actor", "actor must identify a user, person, agent or executor")
    if human and actor_kind(actor) != "user":
        raise _invalid("actor", "Only a user: actor can approve or reject a plan",
                       actor=actor, reason="confirmation_required")
    return actor


def _revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise _invalid("expected_revision", "expected_revision must be a positive integer")
    return value


def _encoded(proposal: dict[str, Any]) -> tuple[str, str]:
    raw = canonical_json(proposal)
    return raw.decode("utf-8"), sha256(raw)


def _view(row: dict[str, Any], history: list[dict[str, Any]],
          blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if key != "proposal_json"}
    result["proposal"] = json.loads(row["proposal_json"])
    result["history"] = [{**{key: value for key, value in entry.items() if key != "changes_json"},
                          "changes": json.loads(entry["changes_json"])} for entry in history]
    result["blockers"] = blockers if blockers is not None else []
    return result


class PlanProposalServices:
    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: PlanProposalStore | None = None

    @property
    def store(self) -> PlanProposalStore:
        """Opened on first use, so building the services facade never touches the database."""
        if self._store is None:
            self._store = PlanProposalStore(self._services.system.db)
        return self._store

    def _row(self, proposal_id: str, conn: Any) -> dict[str, Any]:
        row = self.store.get(_text("id", proposal_id, maximum=100), conn=conn)
        if row is None:
            raise _missing("Plan proposal", proposal_id)
        return row

    def _blockers(self, proposal: dict[str, Any]) -> list[dict[str, Any]]:
        roles_by_bundle: dict[str | None, set[str]] = {}
        for task in proposal["tasks"]:
            bundle = task.get("bundle")
            definition, _ = self._services.definitions._read_parts(task["workflow"], bundle)
            try:
                bind_step_roles(definition, None)
            except WorkflowDefinitionError as exc:
                role_issues = [issue for issue in exc.issues if issue.code == "role_without_team"]
                if len(role_issues) != len(exc.issues):
                    raise
                step_ids = {issue.step_id for issue in role_issues}
                roles = {step.role for step in definition.steps
                         if step.id in step_ids and step.role is not None}
                if roles:
                    roles_by_bundle.setdefault(bundle, set()).update(roles)

        blockers = []
        for bundle, roles in sorted(roles_by_bundle.items(), key=lambda item: item[0] or ""):
            if bundle is not None and self._services.teams.approved(bundle) is not None:
                continue
            blockers.append({"bundle": bundle, "roles": sorted(roles)})
        return blockers

    def _view_blockers(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """The warning a view shows; a workflow that can no longer be read never breaks a listing (approve still refuses)."""
        if row["status"] != "proposed":
            return []
        try:
            return self._blockers(json.loads(row["proposal_json"]))
        except Exception:  # noqa: BLE001 - approve re-reads the workflows and reports the real error
            return []

    @staticmethod
    def _open(row: dict[str, Any], expected_revision: int | None = None) -> None:
        if expected_revision is not None and row["revision"] != expected_revision:
            raise _conflict("Stale plan proposal revision", reason="stale_revision",
                            expected_revision=expected_revision, revision=row["revision"])
        if row["status"] != "proposed":
            raise _conflict(f"Cannot change a {row['status']} plan proposal",
                            reason="invalid_transition", status=row["status"])

    @service_errors
    def save(self, proposal: Any, *, actor: str, channel: str = "python", note: str = "") -> dict[str, Any]:
        actor, channel, note = _actor(actor), _channel(channel), _note(note)
        normalized = validate_proposal(self._services, proposal)
        document, digest = _encoded(normalized)
        proposal_id = f"plp-{uuid.uuid4().hex[:12]}"
        with self.store.db.tx() as conn:
            self.store.insert(conn, id=proposal_id, group_id=normalized["group_id"],
                              planner=normalized["planner"], goal=normalized["goal"],
                              proposal_json=document, sha256=digest, revision=1, status="proposed",
                              created_by=actor, created_channel=channel)
            self.store.append(conn, proposal_id, kind="save", actor=actor, channel=channel,
                              note=note, revision=1)
        return self.get(proposal_id)

    @service_errors
    def get(self, proposal_id: str) -> dict[str, Any]:
        with self.store.db.connect() as conn:
            row = self._row(proposal_id, conn)
        return _view(row, self.store.history(proposal_id), self._view_blockers(row))

    @service_errors
    def list(self, *, group_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        if group_id is not None:
            _text("group_id", group_id, maximum=64)
        if status is not None and status not in {"proposed", "approved", "rejected"}:
            raise _invalid("status", "status must be proposed, approved or rejected")
        return [
            _view(row, self.store.history(row["id"]), self._view_blockers(row))
            for row in self.store.list(group_id=group_id, status=status)
        ]

    def _replace(self, proposal_id: str, tasks: Any, *, actor: str, channel: str,
                 note: str, expected_revision: int, kind: str) -> dict[str, Any]:
        actor, channel, note = _actor(actor), _channel(channel), _note(note)
        expected_revision = _revision(expected_revision)
        with self.store.db.tx() as conn:
            row = self._row(proposal_id, conn)
            self._open(row, expected_revision)
            original = json.loads(row["proposal_json"])
            normalized = validate_proposal(self._services, {**original, "tasks": tasks})
            document, digest = _encoded(normalized)
            revision = row["revision"] + 1
            self.store.update(conn, proposal_id, proposal_json=document, sha256=digest, revision=revision)
            before = {task["id"]: task for task in original["tasks"]}
            after = {task["id"]: task for task in normalized["tasks"]}
            changes = {task_id: {"before": before.get(task_id), "after": after.get(task_id)}
                       for task_id in sorted(before.keys() | after.keys()) if before.get(task_id) != after.get(task_id)}
            self.store.append(conn, proposal_id, kind=kind, actor=actor, channel=channel,
                              note=note, revision=revision, changes=changes)
        return self.get(proposal_id)

    @service_errors
    def edit(self, proposal_id: str, tasks: Any, *, actor: str, channel: str,
             note: str = "", expected_revision: int) -> dict[str, Any]:
        return self._replace(proposal_id, tasks, actor=actor, channel=channel, note=note,
                             expected_revision=expected_revision, kind="edit")

    @service_errors
    def reassign(self, proposal_id: str, task_id: str, *, actor: str, channel: str,
                 note: str = "", expected_revision: int, preferred: str | None = None,
                 workflow: str | None = None, bundle: str | None = None) -> dict[str, Any]:
        if preferred is None and workflow is None and bundle is None:
            raise _invalid("reassign", "At least one assignment field is required")
        with self.store.db.connect() as conn:
            row = self._row(proposal_id, conn)
            self._open(row, _revision(expected_revision))
            tasks = json.loads(row["proposal_json"])["tasks"]
        tasks = [dict(task) for task in tasks]
        target = next((task for task in tasks if task["id"] == task_id), None)
        if target is None:
            raise _missing("Plan task", task_id)
        for key, value in (("preferred", preferred), ("workflow", workflow), ("bundle", bundle)):
            if value is not None:
                target[key] = value
        return self._replace(proposal_id, tasks, actor=actor, channel=channel, note=note,
                             expected_revision=expected_revision, kind="reassign")

    def _decision(self, row: dict[str, Any], *, actor: str, note: str, approved: bool) -> dict[str, Any]:
        result = self.get(row["id"])
        try:
            decision = self._services.decisions.record_review(
                title=f"Plan {row['group_id']} revision {row['revision']}"[:MAX_TITLE_CHARS],
                body=f"proposal: {row['id']}\nsha256: {row['sha256']}\ngoal: {row['goal']}"[:MAX_BODY_CHARS],
                proposed_by=row["created_by"], decided_by=actor, accepted=approved,
                outcome=(f"{'approved' if approved else 'rejected'}: {note}" if note else
                         ("approved" if approved else "rejected"))[:MAX_BODY_CHARS],
                project=row["group_id"])
        except Exception as exc:
            result["decision_id"] = None
            result["decision_record_error"] = type(exc).__name__
        else:
            result["decision_id"] = decision["id"]
        return result

    @service_errors
    def approve(self, proposal_id: str, *, actor: str, channel: str, note: str = "",
                expected_revision: int) -> dict[str, Any]:
        actor, channel, note = _actor(actor, human=True), _channel(channel), _note(note)
        expected_revision = _revision(expected_revision)
        with self.store.db.connect() as conn:
            row = self._row(proposal_id, conn)
            self._open(row, expected_revision)
            proposal = json.loads(row["proposal_json"])
            group = conn.execute("SELECT id FROM task_groups WHERE id=?", (row["group_id"],)).fetchone()
            ids = [task["id"] for task in proposal["tasks"]]
            placeholders = ",".join("?" for _ in ids)
            existing = conn.execute(f"SELECT id FROM tasks WHERE id IN ({placeholders})", ids).fetchone()
            if existing:
                raise _conflict("A proposed task already exists", task_id=existing["id"],
                                reason="task_exists")
        blockers = self._blockers(proposal)
        if blockers:
            raise _conflict("A plan package needs an approved team before approval",
                            reason="team_not_approved", blockers=blockers)
        if group is None:
            self._services.tasks.create_group(row["group_id"], row["goal"][:200], actor=actor,
                                              authorized=True)
        pending = {task["id"]: task for task in proposal["tasks"]}
        while pending:
            ready = [task for task in pending.values() if not (set(task["depends_on"]) & pending.keys())]
            for task in ready:
                self._services.tasks.create_task(row["group_id"], task["id"], task["title"],
                                                 actor=actor, objective=task.get("objective", ""),
                                                 depends_on=task["depends_on"],
                                                 preferred=task.get("preferred", ""))
                del pending[task["id"]]
        with self.store.db.tx() as conn:
            row = self._row(proposal_id, conn)
            self._open(row, expected_revision)
            self.store.update(conn, proposal_id, status="approved", decided_by=actor,
                              decided_at=utcnow(), decided_channel=channel)
            self.store.append(conn, proposal_id, kind="approve", actor=actor, channel=channel,
                              note=note, revision=row["revision"])
        result = self._decision(row, actor=actor, note=note, approved=True)
        result["plan_status"] = self._services.plans.advance(proposal["plan"], actor=actor)
        return result

    @service_errors
    def reject(self, proposal_id: str, *, actor: str, channel: str, note: str) -> dict[str, Any]:
        actor, channel, note = _actor(actor, human=True), _channel(channel), _note(note)
        with self.store.db.tx() as conn:
            row = self._row(proposal_id, conn)
            self._open(row)
            self.store.update(conn, proposal_id, status="rejected", decided_by=actor,
                              decided_at=utcnow(), decided_channel=channel)
            self.store.append(conn, proposal_id, kind="reject", actor=actor, channel=channel,
                              note=note, revision=row["revision"])
        return self._decision(row, actor=actor, note=note, approved=False)
