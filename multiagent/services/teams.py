# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Team version proposals, structural diffs and human approvals (S6.A3 / EQP-T03)."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..db.database import utcnow
from ..db.team_store import TeamStore
from ..external_output import canonical_json, sha256
from .errors import service_errors
from .memory import MAX_BODY_CHARS, _CONTROL
from .projects import actor_kind
from .tasks import _conflict, _invalid, _missing, _text

if TYPE_CHECKING:
    from . import ApplicationServices

_ID = re.compile(r"^tmv-[0-9a-f]{12}$")
STATUSES = ("draft", "proposed", "approved", "rejected")
_TEAM_HEADER_FIELDS = ("id", "description", "schema_version")


def _actor(value: Any, *, human: bool = False) -> str:
    actor = _text("actor", value, maximum=100)
    kind = actor_kind(actor)
    if kind is None:
        raise _invalid("actor", "actor must be user:[name], person:<name>, agent:<app>[/<model>] or executor:<id>")
    if human and kind != "user":
        raise _invalid("actor", "Only a user: actor can approve or reject a team",
                       actor=actor, reason="confirmation_required")
    return actor


def _channel(value: Any) -> str:
    channel = _text("channel", value, maximum=40)
    if "\n" in channel or _CONTROL.search(channel):
        raise _invalid("channel", "channel is one line and may not hold control characters")
    return channel


def _note(value: Any) -> str:
    if value is None:
        return ""
    note = _text("note", value, maximum=MAX_BODY_CHARS, required=False)
    if _CONTROL.search(note):
        raise _invalid("note", "note may not hold control characters")
    return note


def _diff_by_id(
    base_items: list[dict[str, Any]],
    target_items: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    base_map = {item["id"]: item for item in base_items}
    target_map = {item["id"]: item for item in target_items}
    added = [target_map[key] for key in sorted(target_map.keys() - base_map.keys())]
    removed = [base_map[key] for key in sorted(base_map.keys() - target_map.keys())]
    changed: list[dict[str, Any]] = []
    for key in sorted(base_map.keys() & target_map.keys()):
        before = base_map[key]
        after = target_map[key]
        fields: dict[str, dict[str, Any]] = {}
        for field in sorted((before.keys() | after.keys()) - {"id"}):
            if before.get(field) != after.get(field):
                fields[field] = {"before": before.get(field), "after": after.get(field)}
        if fields:
            changed.append({"id": key, "fields": fields})
    return {"added": added, "removed": removed, "changed": changed}


def _diff_by_key(
    base_items: list[dict[str, Any]],
    target_items: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], tuple[str, ...]],
) -> dict[str, list[dict[str, Any]]]:
    base_map = {key_fn(item): item for item in base_items}
    target_map = {key_fn(item): item for item in target_items}
    return {
        "added": [target_map[key] for key in sorted(target_map.keys() - base_map.keys())],
        "removed": [base_map[key] for key in sorted(base_map.keys() - target_map.keys())],
        "changed": [],
    }


def compute_team_diff(
    base_team: dict[str, Any] | None,
    target_team: dict[str, Any],
    *,
    base_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute structural differences between ``base_team`` (or empty) and ``target_team``."""
    team_fields: dict[str, dict[str, Any]] = {}
    if base_team is not None:
        for field in _TEAM_HEADER_FIELDS:
            if base_team.get(field) != target_team.get(field):
                team_fields[field] = {"before": base_team.get(field), "after": target_team.get(field)}
    roles = _diff_by_id((base_team or {}).get("roles", []), target_team.get("roles", []))
    members = _diff_by_id((base_team or {}).get("members", []), target_team.get("members", []))
    assignments = _diff_by_key(
        (base_team or {}).get("assignments", []),
        target_team.get("assignments", []),
        lambda item: (item["role"], item["member"]),
    )
    relationships = _diff_by_key(
        (base_team or {}).get("relationships", []),
        target_team.get("relationships", []),
        lambda item: (item["type"], item["from"], item["to"]),
    )
    has_changes = bool(
        team_fields
        or any(section[bucket] for section in (roles, members, assignments, relationships)
               for bucket in ("added", "removed", "changed"))
    )
    return {
        "base_version_id": base_row["id"] if base_row else None,
        "base_version": base_row["version"] if base_row else None,
        "base_sha256": base_row["sha256"] if base_row else None,
        "has_changes": has_changes,
        "team_fields": team_fields,
        "roles": roles,
        "members": members,
        "assignments": assignments,
        "relationships": relationships,
    }


def _view(
    row: dict[str, Any],
    *,
    current_approved_id: str | None = None,
    diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = dict(row)
    data.pop("approved_seq", None)
    data["team"] = json.loads(data.pop("team_json"))
    data["active"] = data["status"] == "approved" and data["id"] == current_approved_id
    if diff is not None:
        data["diff"] = diff
    return data


class TeamServices:
    """Manage versioned team definitions and human approval gates."""

    def __init__(self, services: ApplicationServices):
        self._services = services
        self._store: TeamStore | None = None

    @property
    def store(self) -> TeamStore:
        if self._store is None:
            self._store = TeamStore(self._services.system.db)
        return self._store

    @staticmethod
    def _id(value: Any) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise _invalid("version_id", f"Invalid team version id: {value!r}")
        return value

    def _diff_against_row(self, base_row: dict[str, Any] | None, target_row: dict[str, Any]) -> dict[str, Any]:
        base_team = json.loads(base_row["team_json"]) if base_row is not None else None
        target_team = json.loads(target_row["team_json"])
        return compute_team_diff(base_team, target_team, base_row=base_row)

    def _default_base_row(
        self,
        conn: sqlite3.Connection,
        row: dict[str, Any],
        current_approved: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if row["status"] == "approved":
            return self.store.latest_approved(row["bundle"], before_seq=row["approved_seq"], conn=conn)
        return current_approved

    @service_errors
    def propose(
        self,
        bundle: str,
        *,
        actor: str,
        channel: str = "python",
        draft: bool = False,
    ) -> dict[str, Any]:
        """Validate ``bundle/team.yaml`` and persist a new ``proposed`` (or ``draft``) version."""
        actor = _actor(actor)
        channel = _channel(channel)
        if type(draft) is not bool:
            raise _invalid("draft", "draft must be a boolean")
        root = self._services.bundle_path(bundle)
        if not (root / "team.yaml").is_file():
            raise _missing("Team", bundle)

        validation = self._services.definitions.validate_bundle(bundle)
        loaded = self._services.definitions._bundle(bundle)
        assert loaded.team is not None
        team = loaded.team

        canonical_bytes = canonical_json(team.model_dump(mode="json"))
        team_json_str = canonical_bytes.decode("utf-8")
        digest = sha256(canonical_bytes)

        with self.store.db.tx() as conn:
            approved_row = self.store.latest_approved(bundle, conn=conn)
            if approved_row is not None and approved_row["sha256"] == digest:
                raise _invalid(
                    "team",
                    "Team definition has no changes compared to the approved version",
                    reason="no_changes",
                    bundle=bundle,
                    team_id=team.id,
                    approved_version_id=approved_row["id"],
                    sha256=digest,
                )
            version = self.store.next_version(conn, bundle, team.id)
            now = utcnow()
            fields: dict[str, Any] = {
                "id": f"tmv-{uuid.uuid4().hex[:12]}",
                "bundle": bundle,
                "team_id": team.id,
                "version": version,
                "team_json": team_json_str,
                "sha256": digest,
                "status": "draft" if draft else "proposed",
                "created_by": actor,
                "created_channel": channel,
                "created_at": now,
                "note": "",
            }
            if not draft:
                fields.update(proposed_by=actor, proposed_at=now, proposed_channel=channel)
            row = self.store.insert(conn, **fields)

        approved_id = approved_row["id"] if approved_row else None
        result = _view(row, current_approved_id=approved_id, diff=self._diff_against_row(approved_row, row))
        if validation.get("warnings"):
            result["warnings"] = validation["warnings"]
        return result

    @service_errors
    def submit(self, version_id: str, actor: str, *, channel: str | None = None) -> dict[str, Any]:
        """Transition a ``draft`` team version to ``proposed``."""
        version_id = self._id(version_id)
        actor = _actor(actor)
        resolved_channel = _channel(channel) if channel is not None else None
        with self.store.db.tx() as conn:
            row = self.store.get(version_id, conn=conn)
            if row is None:
                raise _missing("Team version", version_id)
            if row["status"] != "draft":
                raise _conflict(
                    f"Cannot submit a {row['status']} team version",
                    reason="invalid_transition",
                    status=row["status"],
                )
            approved_row = self.store.latest_approved(row["bundle"], conn=conn)
            updated = self.store.update(
                conn,
                version_id,
                status="proposed",
                proposed_by=actor,
                proposed_at=utcnow(),
                proposed_channel=resolved_channel or row["created_channel"],
            )
        approved_id = approved_row["id"] if approved_row else None
        return _view(updated, current_approved_id=approved_id, diff=self._diff_against_row(approved_row, updated))

    def _decide(
        self,
        version_id: str,
        *,
        actor: str,
        channel: str,
        note: str,
        target: str,
    ) -> dict[str, Any]:
        version_id = self._id(version_id)
        actor = _actor(actor, human=True)
        channel = _channel(channel)
        note = _note(note)
        action = "approve" if target == "approved" else "reject"
        with self.store.db.tx() as conn:
            row = self.store.get(version_id, conn=conn)
            if row is None:
                raise _missing("Team version", version_id)
            if row["status"] != "proposed":
                raise _conflict(
                    f"Cannot {action} a {row['status']} team version",
                    reason="invalid_transition",
                    status=row["status"],
                )
            prior_approved = self.store.latest_approved(row["bundle"], conn=conn)
            fields: dict[str, Any] = {
                "status": target,
                "decided_by": actor,
                "decided_at": utcnow(),
                "decided_channel": channel,
                "note": note,
            }
            if target == "approved":
                fields["approved_seq"] = self.store.next_approved_seq(conn, row["bundle"])
            updated = self.store.update(conn, version_id, **fields)
        current_id = updated["id"] if target == "approved" else (prior_approved["id"] if prior_approved else None)
        diff = self._diff_against_row(prior_approved, updated) if target == "approved" else None
        return _view(updated, current_approved_id=current_id, diff=diff)

    @service_errors
    def approve(
        self,
        version_id: str,
        *,
        actor: str,
        channel: str = "python",
        note: str = "",
    ) -> dict[str, Any]:
        """Approve a ``proposed`` team version (``user:`` actor only)."""
        return self._decide(version_id, actor=actor, channel=channel, note=note, target="approved")

    @service_errors
    def reject(
        self,
        version_id: str,
        *,
        actor: str,
        channel: str = "python",
        note: str = "",
    ) -> dict[str, Any]:
        """Reject a ``proposed`` team version (``user:`` actor only)."""
        return self._decide(version_id, actor=actor, channel=channel, note=note, target="rejected")

    @service_errors
    def get(self, version_id: str) -> dict[str, Any]:
        """Return one team version with its structural diff against the relevant approved version."""
        version_id = self._id(version_id)
        with self.store.db.connect() as conn:
            row = self.store.get(version_id, conn=conn)
            if row is None:
                raise _missing("Team version", version_id)
            current_approved = self.store.latest_approved(row["bundle"], conn=conn)
            base_row = self._default_base_row(conn, row, current_approved)
        current_id = current_approved["id"] if current_approved else None
        return _view(row, current_approved_id=current_id, diff=self._diff_against_row(base_row, row))

    @service_errors
    def list(self, bundle: str, *, status: str | None = None) -> dict[str, Any]:
        """List versions for a registered bundle, oldest first."""
        self._services.bundle_path(bundle)
        if status is not None and status not in STATUSES:
            raise _invalid("status", f"status must be one of {', '.join(STATUSES)}")
        with self.store.db.connect() as conn:
            rows = self.store.list(bundle, status=status, conn=conn)
            current_approved = self.store.latest_approved(bundle, conn=conn)
        current_id = current_approved["id"] if current_approved else None
        versions = [_view(row, current_approved_id=current_id) for row in rows]
        return {"bundle": bundle, "versions": versions, "count": len(versions)}

    @service_errors
    def approved(self, bundle: str) -> dict[str, Any] | None:
        """Return the currently active approved version for ``bundle``, or ``None``."""
        self._services.bundle_path(bundle)
        row = self.store.latest_approved(bundle)
        if row is None:
            return None
        return _view(row, current_approved_id=row["id"])

    @service_errors
    def diff(self, version_id: str, *, against: str | None = None) -> dict[str, Any]:
        """Compute structural diff for ``version_id`` against ``against`` or the latest approved version."""
        version_id = self._id(version_id)
        if against is not None:
            against = self._id(against)
        with self.store.db.connect() as conn:
            row = self.store.get(version_id, conn=conn)
            if row is None:
                raise _missing("Team version", version_id)
            if against is not None:
                base_row = self.store.get(against, conn=conn)
                if base_row is None:
                    raise _missing("Team version", against)
            else:
                current_approved = self.store.latest_approved(row["bundle"], conn=conn)
                base_row = self._default_base_row(conn, row, current_approved)
        return self._diff_against_row(base_row, row)
