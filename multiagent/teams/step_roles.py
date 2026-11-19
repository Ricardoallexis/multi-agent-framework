# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The role of a workflow step when a run executes it (S6.C1 / ROL-T01).

A model step may name a ``role``. The role comes from the team version bound to the run: the
``team_version_bound`` event that ``services.runs.create`` stores in the same transaction as the run, so no
worker takes the run before it. The event carries the approved team itself, so a backend reads the role from
the run's own store and never from the system database where team versions live.

For a step with a role:

* the member is the role's assigned ``agent`` member whose ``agent`` is the step's agent; a step whose
  ``agent`` is null gets the role's only agent member. A role never changes the agent a step names;
* the prompt gets one section with the role's purpose and rules (``StepRole.section``), and only the steps
  of that role get it; a step that declares no skills uses the role's skills;
* ``step_started`` records ``role``, ``member``, ``team_id``, ``team_version_id`` and ``team_version``
  (``StepRole.fields``).

A step without a role runs as before, with or without a bound team.

Problems are ``WorkflowDefinitionError`` issues, found before any model call:

=========================  =====================================================
``role_without_team``      The step names a role, but no approved team version is bound to the run.
``team_binding_invalid``   The bound version carries no readable team, or the team does not match its sha256.
``unknown_role``           The bound team version has no role with that id.
``role_member_not_found``  No agent member of the role runs the step's agent (or the role has none).
``role_member_ambiguous``  More than one agent member of the role could run the step.
``unknown_agent``          The member's agent is not in the catalog (step without an agent).
``inactive_agent``         The member's agent is marked ``active: false`` in the catalog.
``skill_not_found``        A role skill the step uses has no ``.md`` file in the skills directory.
=========================  =====================================================
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..external_output import canonical_json, sha256
from ..workflow_validation import WorkflowDefinitionError, WorkflowIssue, _resource_file_exists
from ..workflows import WorkflowDefinition
from .schema import Member, MemberKind, Role, Team

if TYPE_CHECKING:
    from ..catalog import Catalog

TEAM_VERSION_BOUND = "team_version_bound"
# What a step with a role records in ``step_started``, its attempts and the run report; a step without a role, none.
ROLE_FIELDS = ("role", "member", "team_id", "team_version_id", "team_version")


@dataclass(frozen=True)
class BoundTeam:
    """The team version a run is bound to, as its ``team_version_bound`` event records it."""

    version_id: str
    version: int | None
    team_id: str
    # ``None`` when the event carries no team, or one that does not match the recorded sha256.
    team: Team | None


@dataclass(frozen=True)
class StepRole:
    """The role and member that run one step, from the bound team version."""

    role: Role
    member: Member
    team: BoundTeam

    @property
    def agent(self) -> str:
        return str(self.member.agent)

    def fields(self) -> dict[str, Any]:
        """What the step's events, its attempts and the run report record about the role (``ROLE_FIELDS``)."""
        return dict(zip(ROLE_FIELDS, (self.role.id, self.member.id, self.team.team_id, self.team.version_id,
                                      self.team.version)))

    def section(self) -> str:
        """The prompt section of the role: its purpose and its rules."""
        lines = [f"## Role: {self.role.id}", f"Purpose: {self.role.purpose}"]
        if self.role.rules:
            lines.append("Rules:")
            lines.extend(f"- {rule}" for rule in self.role.rules)
        return "\n".join(lines)


def bound_team_from_payload(payload: Mapping[str, Any]) -> BoundTeam:
    """The bound team of one ``team_version_bound`` payload."""
    raw = payload.get("team")
    team = None
    if isinstance(raw, dict) and sha256(canonical_json(raw)) == payload.get("sha256"):
        try:
            team = Team.model_validate(raw)
        except ValueError:          # pydantic's ValidationError is a ValueError
            team = None
    version = payload.get("version")
    return BoundTeam(
        version_id=str(payload.get("version_id") or payload.get("id") or ""),
        version=version if isinstance(version, int) and not isinstance(version, bool) else None,
        team_id=str(payload.get("team_id") or ""),
        team=team,
    )


def bound_team(events: Iterable[Mapping[str, Any]] | None) -> BoundTeam | None:
    """The team version bound to a run (its first ``team_version_bound`` event), or ``None``."""
    for event in events or ():
        if isinstance(event, Mapping) and event.get("event_type") == TEAM_VERSION_BOUND:
            payload = event.get("payload")
            return bound_team_from_payload(payload if isinstance(payload, Mapping) else {})
    return None


def bind_step_roles(definition: WorkflowDefinition, team: BoundTeam | None, *, catalog: Catalog | None = None,
                    skills_dir: Path | None = None) -> tuple[WorkflowDefinition, dict[str, StepRole]]:
    """``definition`` with each role applied to its step, and the role of each step that names one, by step id.

    Applying a role fills a null ``agent`` with the member's agent and empty ``skills`` with the role's skills;
    steps without a role are kept as they are. ``catalog`` and ``skills_dir``, when given, check the agent and the
    skills a role brings. Raises ``WorkflowDefinitionError`` with every problem found (codes in the module
    docstring). A definition whose steps name no role comes back unchanged, whatever ``team`` is.
    """
    if not any(getattr(step, "role", None) is not None for step in definition.steps):
        return definition, {}
    issues: list[WorkflowIssue] = []
    roles: dict[str, StepRole] = {}
    steps = []
    for index, step in enumerate(definition.steps):
        if getattr(step, "role", None) is None:
            steps.append(step)
            continue
        location = f"steps[{index}].role"
        found = _step_role(step, team, location=location, issues=issues)
        if found is None:
            steps.append(step)
            continue
        agent = step.agent if step.agent is not None else found.agent
        skills = step.skills if step.skills else list(found.role.skills)
        if step.agent is None and catalog is not None:
            spec = catalog.agents.get(agent)
            if agent not in catalog.agents:
                issues.append(WorkflowIssue("unknown_agent", location,
                                            f"Member {found.member.id!r} of role {found.role.id!r} runs agent "
                                            f"{agent!r}, which is not in the catalog", step.id))
            elif isinstance(spec, dict) and spec.get("active") is False:
                issues.append(WorkflowIssue("inactive_agent", location,
                                            f"Agent {agent!r} of member {found.member.id!r} is marked inactive "
                                            "in the catalog", step.id))
        if not step.skills and skills_dir is not None:
            for skill in skills:
                if not _resource_file_exists(skills_dir, f"{skill}.md"):
                    issues.append(WorkflowIssue("skill_not_found", location,
                                                f"Skill {skill!r} of role {found.role.id!r} not found; expected "
                                                f"{skill}.md in the skills directory", step.id))
        roles[step.id] = found
        steps.append(replace(step, agent=agent, skills=skills))
    if issues:
        raise WorkflowDefinitionError(str(definition.id), issues)
    return replace(definition, steps=steps), roles


def _step_role(step, team: BoundTeam | None, *, location: str, issues: list[WorkflowIssue]) -> StepRole | None:
    def fail(code: str, message: str) -> None:
        issues.append(WorkflowIssue(code, location, message, step.id))

    if team is None:
        fail("role_without_team", f"The step has role {step.role!r}, but no approved team version is bound to "
                                  "this run; approve the bundle's team before starting the run")
        return None
    if team.team is None:
        fail("team_binding_invalid", f"The team version {team.version_id!r} bound to this run carries no readable "
                                     "team matching its sha256")
        return None
    role = next((item for item in team.team.roles if item.id == step.role), None)
    if role is None:
        known = ", ".join(item.id for item in team.team.roles) or "(none)"
        fail("unknown_role", f"Role {step.role!r} is not in team {team.team_id!r} version {team.version}; "
                             f"roles: {known}")
        return None
    assigned = {item.member for item in team.team.assignments if item.role == role.id}
    candidates = [member for member in team.team.members
                  if member.id in assigned and member.kind == MemberKind.AGENT
                  and (step.agent is None or member.agent == step.agent)]
    if len(candidates) == 1:
        return StepRole(role, candidates[0], team)
    names = ", ".join(repr(member.id) for member in candidates)
    if not candidates:
        fail("role_member_not_found",
             f"No agent member of role {role.id!r} runs agent {step.agent!r}" if step.agent is not None
             else f"Role {role.id!r} has no agent member to run this step")
    elif step.agent is not None:
        fail("role_member_ambiguous", f"Members {names} of role {role.id!r} all run agent {step.agent!r}")
    else:
        fail("role_member_ambiguous", f"Role {role.id!r} has {len(candidates)} agent members ({names}); "
                                      "name the step's agent to choose one")
    return None


__all__ = ["BoundTeam", "ROLE_FIELDS", "StepRole", "TEAM_VERSION_BOUND", "bind_step_roles", "bound_team",
           "bound_team_from_payload"]
