# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure organization chart model and Mermaid renderer for team definitions (S6.B1, S6.B2)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .schema import RelationshipType, Team, TeamDefinitionError, TeamIssue

STATUS_OCCUPIED = "occupied"
STATUS_VACANT = "vacant"
STATUS_PENDING = "pending"

_HIERARCHY_TYPE = RelationshipType.REPORTS_TO.value
_LINK_TYPES = frozenset({RelationshipType.REVIEWS.value, RelationshipType.DELEGATES_TO.value})


@dataclass(frozen=True)
class _NormalizedTeam:
    team_id: str
    roles: dict[str, str]
    occupants: dict[str, list[dict[str, Any]]]
    reports_to: dict[str, str | None]
    links: list[dict[str, str]]


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _enum_str(value: Any) -> str:
    return str(getattr(value, "value", value))


def _normalize_member(member: Any) -> tuple[str, dict[str, Any]]:
    member_id = str(_field(member, "id"))
    kind = _enum_str(_field(member, "kind", default="unknown"))
    raw_name = _field(member, "name")
    raw_agent = _field(member, "agent")
    name = raw_name if raw_name is not None else raw_agent
    return member_id, {"id": member_id, "kind": kind, "name": name}


def _find_cycle_path(role_ids: Sequence[str], edges: Mapping[str, list[tuple[str, int]]]) -> list[tuple[list[str], int]]:
    unvisited, visiting, done = 0, 1, 2
    state: dict[str, int] = {}
    stack: list[str] = []
    cycles: list[tuple[list[str], int]] = []

    def dfs(node: str) -> None:
        state[node] = visiting
        stack.append(node)
        for target, rel_index in sorted(edges.get(node, ()), key=lambda item: (item[0], item[1])):
            target_state = state.get(target, unvisited)
            if target_state == visiting:
                start = stack.index(target)
                cycles.append(([*stack[start:], target], rel_index))
            elif target_state == unvisited:
                dfs(target)
        stack.pop()
        state[node] = done

    for role_id in role_ids:
        if state.get(role_id, unvisited) == unvisited and role_id in edges:
            dfs(role_id)
    return cycles


def _normalize_team(team: Team | Mapping[str, Any]) -> _NormalizedTeam:
    team_id = str(_field(team, "id", default="<team>"))
    raw_roles = _field(team, "roles", default=()) or ()
    raw_members = _field(team, "members", default=()) or ()
    raw_assignments = _field(team, "assignments", default=()) or ()
    raw_relationships = _field(team, "relationships", default=()) or ()

    roles: dict[str, str] = {}
    for role in raw_roles:
        role_id = str(_field(role, "id"))
        purpose = str(_field(role, "purpose", default=""))
        roles[role_id] = purpose

    members: dict[str, dict[str, Any]] = {}
    for member in raw_members:
        member_id, record = _normalize_member(member)
        members[member_id] = record

    occupants_by_role: dict[str, dict[str, dict[str, Any]]] = {role_id: {} for role_id in roles}
    for assignment in raw_assignments:
        role_id = str(_field(assignment, "role"))
        member_id = str(_field(assignment, "member"))
        if role_id not in roles:
            continue
        occupants_by_role[role_id][member_id] = members.get(
            member_id,
            {"id": member_id, "kind": "unknown", "name": None},
        )

    occupants = {
        role_id: [by_member[mid] for mid in sorted(by_member)]
        for role_id, by_member in occupants_by_role.items()
    }

    issues: list[TeamIssue] = []
    reports_edges: dict[str, list[tuple[str, int]]] = {}
    links_set: set[tuple[str, str, str]] = set()

    for index, rel in enumerate(raw_relationships):
        rel_type = _enum_str(_field(rel, "type"))
        from_role = str(_field(rel, "from", "from_"))
        to_role = str(_field(rel, "to"))

        if rel_type == _HIERARCHY_TYPE:
            if from_role not in roles:
                issues.append(TeamIssue(
                    "unknown_role",
                    f"relationships[{index}].from",
                    f"Relationship 'from' references unknown role {from_role!r}",
                ))
            if to_role not in roles:
                issues.append(TeamIssue(
                    "unknown_role",
                    f"relationships[{index}].to",
                    f"Relationship 'to' references unknown role {to_role!r}",
                ))
            if from_role in roles and to_role in roles:
                reports_edges.setdefault(from_role, []).append((to_role, index))
        elif rel_type in _LINK_TYPES and from_role in roles and to_role in roles:
            links_set.add((rel_type, from_role, to_role))

    sorted_role_ids = sorted(roles)
    for role_id in sorted_role_ids:
        targets = reports_edges.get(role_id, ())
        if len(targets) > 1:
            manager_names = ", ".join(target for target, _ in targets)
            issues.append(TeamIssue(
                "multiple_managers",
                f"relationships[{targets[1][1]}]",
                f"Role {role_id!r} reports to multiple managers: {manager_names}",
            ))

    for cycle_path, rel_index in _find_cycle_path(sorted_role_ids, reports_edges):
        issues.append(TeamIssue(
            "reports_to_cycle",
            f"relationships[{rel_index}]",
            f"Cycle in 'reports_to' relationships: {' -> '.join(cycle_path)}",
        ))

    if issues:
        raise TeamDefinitionError(team_id, issues)

    reports_to: dict[str, str | None] = {role_id: None for role_id in sorted_role_ids}
    for role_id, targets in reports_edges.items():
        if targets:
            reports_to[role_id] = targets[0][0]

    links = [
        {"type": rel_type, "from": from_role, "to": to_role}
        for rel_type, from_role, to_role in sorted(links_set)
    ]
    return _NormalizedTeam(
        team_id=team_id,
        roles=roles,
        occupants=occupants,
        reports_to=reports_to,
        links=links,
    )


def _position_status(members: Sequence[Mapping[str, Any]], *, force_pending: bool = False) -> str:
    if force_pending:
        return STATUS_PENDING
    return STATUS_OCCUPIED if members else STATUS_VACANT


def build_org_chart(
    team: Team | Mapping[str, Any],
    *,
    pending: Team | Mapping[str, Any] | bool | None = None,
) -> dict[str, Any]:
    """Build a validated, deterministic organization chart model from a team definition.

    ``pending`` may be:
    - ``None`` or ``False``: positions are ``occupied`` or ``vacant``.
    - ``True``: every position is marked ``pending``.
    - A ``Team`` or mapping representing a later proposed version layered over
      the approved ``team``: added roles and roles with changed occupants become
      ``pending``, while removed roles remain with ``pending_removal: True``.
    """
    base = _normalize_team(team)
    all_pending = pending is True
    overlay = _normalize_team(pending) if pending is not None and not isinstance(pending, bool) else None

    positions_by_id: dict[str, dict[str, Any]] = {}
    if overlay is None:
        for role_id in sorted(base.roles):
            members = [dict(member) for member in base.occupants[role_id]]
            positions_by_id[role_id] = {
                "role_id": role_id,
                "purpose": base.roles[role_id],
                "members": members,
                "reports_to": base.reports_to[role_id],
                "reports": [],
                "status": _position_status(members, force_pending=all_pending),
                "pending_removal": False,
            }
        links = [dict(link) for link in base.links]
    else:
        base_ids = set(base.roles)
        cand_ids = set(overlay.roles)
        removed_ids = base_ids - cand_ids
        added_ids = cand_ids - base_ids

        for role_id in sorted(base_ids | cand_ids):
            if role_id in removed_ids:
                members = [dict(member) for member in base.occupants[role_id]]
                positions_by_id[role_id] = {
                    "role_id": role_id,
                    "purpose": base.roles[role_id],
                    "members": members,
                    "reports_to": base.reports_to[role_id],
                    "reports": [],
                    "status": _position_status(members),
                    "pending_removal": True,
                }
            elif role_id in added_ids:
                members = [dict(member) for member in overlay.occupants[role_id]]
                positions_by_id[role_id] = {
                    "role_id": role_id,
                    "purpose": overlay.roles[role_id],
                    "members": members,
                    "reports_to": overlay.reports_to[role_id],
                    "reports": [],
                    "status": STATUS_PENDING,
                    "pending_removal": False,
                }
            else:
                base_members = base.occupants[role_id]
                cand_members = overlay.occupants[role_id]
                occupant_changed = base_members != cand_members
                members = [dict(member) for member in cand_members]
                positions_by_id[role_id] = {
                    "role_id": role_id,
                    "purpose": overlay.roles[role_id],
                    "members": members,
                    "reports_to": overlay.reports_to[role_id],
                    "reports": [],
                    "status": _position_status(members, force_pending=occupant_changed),
                    "pending_removal": False,
                }

        merged_links = {
            (link["type"], link["from"], link["to"])
            for link in overlay.links
        } | {
            (link["type"], link["from"], link["to"])
            for link in base.links
            if link["from"] in removed_ids or link["to"] in removed_ids
        }
        links = [
            {"type": rel_type, "from": from_role, "to": to_role}
            for rel_type, from_role, to_role in sorted(merged_links)
        ]

    for role_id in sorted(positions_by_id):
        manager_id = positions_by_id[role_id]["reports_to"]
        if manager_id is not None and manager_id in positions_by_id:
            positions_by_id[manager_id]["reports"].append(role_id)

    roots = [
        role_id
        for role_id in sorted(positions_by_id)
        if positions_by_id[role_id]["reports_to"] is None
    ]
    positions = [positions_by_id[role_id] for role_id in sorted(positions_by_id)]

    return {
        "team_id": overlay.team_id if overlay is not None else base.team_id,
        "root": roots[0] if len(roots) == 1 else None,
        "roots": roots,
        "forest": len(roots) > 1,
        "positions": positions,
        "links": links,
    }


def _escape_mermaid_label(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return escaped.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def _occupant_text(position: Mapping[str, Any]) -> str:
    members = position.get("members") or ()
    if not members:
        return "vacant"
    names = [str(member.get("name") or member.get("id")) for member in members]
    return ", ".join(names)


def render_mermaid(chart: Mapping[str, Any]) -> str:
    """Render a deterministic Mermaid ``flowchart TD`` diagram for an organization chart."""
    positions = sorted(chart.get("positions") or (), key=lambda pos: str(pos["role_id"]))
    node_ids = {str(pos["role_id"]): f"r{index}" for index, pos in enumerate(positions)}

    lines = ["flowchart TD"]
    for pos in positions:
        role_id = str(pos["role_id"])
        node_id = node_ids[role_id]
        occupant = _occupant_text(pos)
        label = f"{role_id}: {occupant}"
        if pos.get("pending_removal"):
            label = f"{label} (pending removal)"
        lines.append(f'    {node_id}["{_escape_mermaid_label(label)}"]')

    for pos in positions:
        parent_id = node_ids[str(pos["role_id"])]
        for child_role_id in sorted(pos.get("reports") or ()):
            child_id = node_ids.get(str(child_role_id))
            if child_id is not None:
                lines.append(f"    {parent_id} --> {child_id}")

    links = sorted(
        chart.get("links") or (),
        key=lambda link: (str(link["type"]), str(link["from"]), str(link["to"])),
    )
    for link in links:
        src = node_ids.get(str(link["from"]))
        dst = node_ids.get(str(link["to"]))
        if src is not None and dst is not None:
            rel_type = str(link["type"])
            lines.append(f"    {src} -.->|{rel_type}| {dst}")

    lines.extend([
        "    classDef occupied fill:#e8f5e9,stroke:#2e7d32,color:#1b5e20",
        "    classDef vacant fill:#fff3e0,stroke:#ef6c00,color:#e65100",
        "    classDef pending fill:#e3f2fd,stroke:#1565c0,color:#0d47a1",
    ])

    for pos in positions:
        node_id = node_ids[str(pos["role_id"])]
        status = str(pos.get("status") or STATUS_VACANT)
        if status not in (STATUS_OCCUPIED, STATUS_VACANT, STATUS_PENDING):
            status = STATUS_VACANT
        lines.append(f"    class {node_id} {status}")

    return "\n".join(lines)


__all__ = [
    "STATUS_OCCUPIED",
    "STATUS_PENDING",
    "STATUS_VACANT",
    "build_org_chart",
    "render_mermaid",
]
