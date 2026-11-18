# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Cross-reference team roles with bundle workflows (EQP-T02)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.contracts import ImpactClass
from ..tools.registry import ToolLookupError, ToolRegistry
from ..tools.selection import SelectionConfig, SelectionError, select_tool
from ..workflow_validation import _resource_file_exists
from ..workflows import WorkflowDefinition
from .schema import MemberKind, Team


@dataclass(frozen=True)
class TeamValidationIssue:
    code: str
    location: str
    message: str
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
            "severity": self.severity,
        }


def validate_team_workflows(
    team: Team,
    workflows: Sequence[WorkflowDefinition],
    *,
    output_schemas: Mapping[str, type],
    skills_dir: Path,
    tool_registry: ToolRegistry | None,
    tool_selection: Mapping[str, Any] | SelectionConfig | None = None,
) -> list[TeamValidationIssue]:
    """Return team contract, skill and least-privilege findings without executing tools."""
    issues: list[TeamValidationIssue] = []
    roles = {role.id: role for role in team.roles}

    for role_index, role in enumerate(team.roles):
        for contract_index, contract in enumerate(role.contracts):
            if contract not in output_schemas:
                issues.append(TeamValidationIssue(
                    "team_unknown_contract",
                    f"team.yaml:roles[{role_index}].contracts[{contract_index}]",
                    f"Role {role.id!r} references unknown output contract {contract!r}",
                    "error",
                ))
        for skill_index, skill in enumerate(role.skills):
            if not _resource_file_exists(skills_dir, f"{skill}.md"):
                issues.append(TeamValidationIssue(
                    "team_unknown_skill",
                    f"team.yaml:roles[{role_index}].skills[{skill_index}]",
                    f"Role {role.id!r} references unknown skill {skill!r}",
                    "error",
                ))

    member_roles: dict[str, set[str]] = {}
    for member in team.members:
        if member.kind == MemberKind.AGENT and member.agent is not None:
            member_roles[member.agent] = set()
    for assignment in team.assignments:
        member = next((item for item in team.members if item.id == assignment.member), None)
        if member is not None and member.kind == MemberKind.AGENT and member.agent is not None:
            member_roles.setdefault(member.agent, set()).add(assignment.role)

    used_by_role: dict[str, set[ImpactClass]] = {role.id: set() for role in team.roles}
    for workflow in workflows:
        for step_index, step in enumerate(workflow.steps):
            location = f"workflows/{workflow.id}:steps[{step_index}]"
            assigned_roles = member_roles.get(step.agent, set()) if step.agent is not None else set()
            if step.agent is not None and not assigned_roles:
                issues.append(TeamValidationIssue(
                    "team_agent_unassigned",
                    location,
                    f"Agent {step.agent!r} has no role in team {team.id!r}",
                    "warning",
                ))

            if not step.tools:
                continue
            resolved_tools = []
            unresolved = False
            for entry in step.tools:
                label = (
                    f"capability {entry.get('capability')!r}"
                    if "capability" in entry
                    else f"tool {entry.get('tool')!r}"
                )
                if tool_registry is None:
                    unresolved = True
                    issues.append(TeamValidationIssue(
                        "team_tool_unresolved",
                        location,
                        f"Could not resolve {label} without executing workflow {workflow.id!r}",
                        "warning",
                    ))
                    continue
                try:
                    registered = (
                        select_tool(tool_registry, entry["capability"], tool_selection)
                        if "capability" in entry
                        else tool_registry.lookup(entry["tool"], entry.get("version"))
                    )
                except (SelectionError, ToolLookupError):
                    unresolved = True
                    issues.append(TeamValidationIssue(
                        "team_tool_unresolved",
                        location,
                        f"Could not resolve {label} without executing workflow {workflow.id!r}",
                        "warning",
                    ))
                    continue
                resolved_tools.append(registered.spec)

            if unresolved:
                continue

            required_impacts = {
                impact
                for spec in resolved_tools
                for impact in spec.impacts
            }
            for role_id in assigned_roles:
                used_by_role[role_id].update(required_impacts)

            if not assigned_roles:
                continue
            allowed = {
                permission
                for role_id in assigned_roles
                for permission in roles[role_id].permissions
            }
            missing = required_impacts - allowed
            for impact in sorted(missing, key=lambda item: item.value):
                tool_ids = sorted(
                    spec.tool_id for spec in resolved_tools if impact in spec.impacts
                )
                role_names = ", ".join(sorted(assigned_roles))
                for tool_id in tool_ids:
                    issues.append(TeamValidationIssue(
                        "team_permission_missing",
                        location,
                        f"Role(s) {role_names} need permission {impact.value!r} for tool {tool_id!r}",
                        "error",
                    ))

    for role_index, role in enumerate(team.roles):
        for permission_index, permission in enumerate(sorted(role.permissions, key=lambda item: item.value)):
            if permission not in used_by_role[role.id]:
                issues.append(TeamValidationIssue(
                    "team_permission_unused",
                    f"team.yaml:roles[{role_index}].permissions[{permission_index}]",
                    f"Role {role.id!r} does not need permission {permission.value!r} for any assigned member step",
                    "warning",
                ))
    return issues


__all__ = ["TeamValidationIssue", "validate_team_workflows"]
