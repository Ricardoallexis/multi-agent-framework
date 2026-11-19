# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Team definition schema, validation and file loading (S6.A1).

A team definition describes the roles in a team, the human or bundle-agent members,
assignments of members to roles, and typed relationships between roles.

Validation produces structured :class:`TeamIssue` records inside
:class:`TeamDefinitionError` with stable error ``code`` values and field paths:

=========================  =====================================================
``unsupported_format``     File extension is not ``.yaml``, ``.yml`` or ``.json``.
``yaml_invalid``           Document is not valid UTF-8 YAML or repeats a key.
``json_invalid``           Document is not valid UTF-8 JSON or repeats a key.
``root_not_mapping``       Document root is not a mapping.
``missing_field``          A required field is absent (including ``agent`` on an
                           ``agent`` member).
``unknown_field``          An unexpected field is present.
``invalid_schema_version`` ``schema_version`` is not integer ``1``.
``invalid_type``           A field has the wrong type.
``invalid_value``          A field value is invalid (blank text, unknown ``kind``
                           or relationship ``type``, ``agent`` set on ``person``).
``invalid_capability_id``  A role capability violates ``multiagent.capabilities``.
``invalid_permission``     A role permission is not an :class:`ImpactClass`.
``duplicate_role_id``      Two roles share an ``id``.
``duplicate_member_id``    Two members share an ``id``.
``unknown_role``           An assignment or relationship names an unknown role.
``unknown_member``         An assignment names an unknown member.
``unknown_agent``          An ``agent`` member names an agent absent from the bundle.
``self_relationship``      A relationship links a role to itself.
``reports_to_cycle``       ``reports_to`` relationships form a directed cycle.
=========================  =====================================================
"""
from __future__ import annotations

import json
from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from ..capabilities import CapabilityIdError, validate_capability_id, validate_capability_ids
from ..tools.contracts import ImpactClass

YAML_SUFFIXES = frozenset({".yaml", ".yml"})
JSON_SUFFIXES = frozenset({".json"})
_IMPACT_VALUES = frozenset(item.value for item in ImpactClass)

DOCUMENT = "<document>"
ROOT = "<root>"


def _sorted_values(values: Any) -> list[str]:
    return sorted(getattr(item, "value", item) for item in values)


_SortedSet = PlainSerializer(_sorted_values, return_type=list[str], when_used="json")


class _TeamModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


class MemberKind(str, Enum):
    AGENT = "agent"
    PERSON = "person"


class RelationshipType(str, Enum):
    REPORTS_TO = "reports_to"
    REVIEWS = "reviews"
    DELEGATES_TO = "delegates_to"


def _require_non_blank(value: str, label: str) -> str:
    if not value.strip():
        raise PydanticCustomError("invalid_value", f"{label} must not be blank")
    return value


def _require_non_blank_list(values: list[str], label: str) -> list[str]:
    for item in values:
        _require_non_blank(item, label)
    return values


class Role(_TeamModel):
    """One functional role within a team."""

    id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    rules: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    contracts: list[str] = Field(default_factory=list)
    capabilities: Annotated[frozenset[str], _SortedSet] = frozenset()
    permissions: Annotated[frozenset[ImpactClass], _SortedSet] = frozenset()

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        return _require_non_blank(value, "Role id")

    @field_validator("purpose")
    @classmethod
    def _valid_purpose(cls, value: str) -> str:
        return _require_non_blank(value, "Role purpose")

    @field_validator("rules")
    @classmethod
    def _valid_rules(cls, value: list[str]) -> list[str]:
        return _require_non_blank_list(value, "Role rule")

    @field_validator("skills")
    @classmethod
    def _valid_skills(cls, value: list[str]) -> list[str]:
        return _require_non_blank_list(value, "Role skill")

    @field_validator("contracts")
    @classmethod
    def _valid_contracts(cls, value: list[str]) -> list[str]:
        return _require_non_blank_list(value, "Role contract")

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: Any) -> frozenset[str]:
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            raise PydanticCustomError("invalid_capability_id", "Capabilities must be a list of capability ids")
        try:
            return validate_capability_ids(list(value))
        except CapabilityIdError as exc:
            raise PydanticCustomError(
                "invalid_capability_id",
                "Invalid capability id {bad!r}",
                {"bad": exc.value},
            ) from exc

    @field_validator("permissions", mode="before")
    @classmethod
    def _validate_permissions(cls, value: Any) -> frozenset[ImpactClass]:
        expected = ", ".join(sorted(_IMPACT_VALUES))
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            raise PydanticCustomError(
                "invalid_permission",
                f"Permissions must be a list of: {expected}",
            )
        parsed: list[ImpactClass] = []
        for item in value:
            if isinstance(item, ImpactClass):
                parsed.append(item)
            elif isinstance(item, str) and item in _IMPACT_VALUES:
                parsed.append(ImpactClass(item))
            else:
                raise PydanticCustomError(
                    "invalid_permission",
                    f"Invalid permission {item!r}; expected one of: {expected}",
                )
        return frozenset(parsed)


class Member(_TeamModel):
    """One member of a team: either a bundle catalog agent or a person."""

    id: str = Field(min_length=1)
    kind: MemberKind
    agent: str | None = None
    name: str | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        return _require_non_blank(value, "Member id")

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str | None) -> str | None:
        return _require_non_blank(value, "Member name") if value is not None else None

    @model_validator(mode="after")
    def _validate_agent_binding(self) -> Member:
        if self.kind == MemberKind.AGENT:
            if self.agent is None:
                raise PydanticCustomError("member_missing_agent", "Member of kind 'agent' requires 'agent'")
            if not self.agent.strip():
                raise PydanticCustomError("member_blank_agent", "Member 'agent' must not be blank")
        elif self.agent is not None:
            raise PydanticCustomError("member_forbidden_agent", "Member of kind 'person' must not set 'agent'")
        return self


class Assignment(_TeamModel):
    """Assigns a team member to a role."""

    role: str = Field(min_length=1)
    member: str = Field(min_length=1)

    @field_validator("role")
    @classmethod
    def _valid_role(cls, value: str) -> str:
        return _require_non_blank(value, "Assignment role")

    @field_validator("member")
    @classmethod
    def _valid_member(cls, value: str) -> str:
        return _require_non_blank(value, "Assignment member")


class Relationship(_TeamModel):
    """Directed relationship between two distinct roles."""

    type: RelationshipType
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)

    @field_validator("from_")
    @classmethod
    def _valid_from(cls, value: str) -> str:
        return _require_non_blank(value, "Relationship 'from'")

    @field_validator("to")
    @classmethod
    def _valid_to(cls, value: str) -> str:
        return _require_non_blank(value, "Relationship 'to'")

    @model_validator(mode="after")
    def _forbid_self_relationship(self) -> Relationship:
        if self.from_ == self.to:
            raise PydanticCustomError(
                "self_relationship",
                f"Role {self.from_!r} cannot have a {self.type.value!r} relationship with itself",
            )
        return self


@dataclass(frozen=True)
class TeamIssue:
    """One structured validation problem in a team definition."""

    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.location}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        """JSON-ready form matching ``WorkflowIssue`` shape."""
        return {"code": self.code, "location": self.location, "message": self.message}


class TeamDefinitionError(ValueError):
    """A team definition failed structural or semantic validation."""

    def __init__(self, team_id: str, issues: Iterable[TeamIssue], *, source: Path | None = None) -> None:
        self.team_id = team_id
        self.issues = tuple(issues)
        self.source = source
        where = f" ({source})" if source else ""
        super().__init__("\n".join([self._summary(where), *(f"  - {issue}" for issue in self.issues)]))

    def to_payload(self) -> dict[str, Any]:
        """Public, JSON-ready error payload without local file paths."""
        return {
            "error": self._summary(),
            "team_id": self.team_id,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def _summary(self, where: str = "") -> str:
        count = len(self.issues)
        return f"Invalid team definition '{self.team_id}'{where}: {count} problem{'' if count == 1 else 's'}"


class Team(_TeamModel):
    """Complete team definition."""

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1)
    description: str | None = None
    mode: Literal["supervised", "autonomous"] | None = Field(default=None, exclude_if=lambda value: value is None)
    roles: list[Role] = Field(default_factory=list)
    members: list[Member] = Field(default_factory=list)
    assignments: list[Assignment] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    @field_validator("mode", mode="before")
    @classmethod
    def _validate_mode(cls, value: Any) -> str:
        if value not in ("supervised", "autonomous"):
            raise PydanticCustomError("invalid_mode", "Unknown team mode; expected supervised or autonomous")
        return value

    @field_validator("schema_version", mode="before")
    @classmethod
    def _validate_schema_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise PydanticCustomError(
                "invalid_schema_version",
                f"Unsupported schema_version {value!r}; expected 1",
            )
        return value

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        return _require_non_blank(value, "Team id")

    @field_validator("description")
    @classmethod
    def _valid_description(cls, value: str | None) -> str | None:
        return _require_non_blank(value, "Team description") if value is not None else None

    @model_validator(mode="after")
    def _validate_semantics(self, info: ValidationInfo) -> Team:
        if info.context and info.context.get("skip_team_semantics"):
            return self
        issues = _collect_semantic_issues(self.roles, self.members, self.assignments, self.relationships)
        if issues:
            raise PydanticCustomError(issues[0].code, "; ".join(str(issue) for issue in issues))
        return self


def validate_team(
    team: Team,
    *,
    agents: Iterable[str] | Mapping[str, Any] | None = None,
    source: Path | None = None,
) -> Team:
    """Verify semantic rules on a ``Team`` (and bundle agent membership when ``agents`` is given)."""
    issues = _collect_semantic_issues(
        team.roles, team.members, team.assignments, team.relationships, agents=agents,
    )
    if issues:
        raise TeamDefinitionError(team.id, issues, source=source)
    return team


def parse_team_document(
    document: bytes | str,
    *,
    fmt: Literal["yaml", "json"] = "yaml",
    agents: Iterable[str] | Mapping[str, Any] | None = None,
    source: Path | None = None,
    team_label: str = "<team>",
) -> Team:
    """Parse a YAML or JSON team document and raise :class:`TeamDefinitionError` on any problem."""
    raw = _parse_raw_document(document, fmt=fmt, source=source, team_label=team_label)
    if not isinstance(raw, dict):
        raise TeamDefinitionError(
            team_label,
            [TeamIssue("root_not_mapping", ROOT, f"Expected a mapping; got {type(raw).__name__}")],
            source=source,
        )

    label = raw.get("id") if isinstance(raw.get("id"), str) and raw["id"].strip() else team_label
    try:
        team = Team.model_validate(raw, context={"skip_team_semantics": True})
    except ValidationError as exc:
        issues = _translate_validation_error(raw, exc)
        raise TeamDefinitionError(label, issues, source=source) from exc

    return validate_team(team, agents=agents, source=source)


def load_team(
    path: Path | str,
    *,
    agents: Iterable[str] | Mapping[str, Any] | None = None,
) -> Team:
    """Load and validate a team definition from a ``.yaml``, ``.yml`` or ``.json`` file."""
    file_path = Path(path)
    suffix = file_path.suffix.casefold()
    if suffix in YAML_SUFFIXES:
        fmt: Literal["yaml", "json"] = "yaml"
    elif suffix in JSON_SUFFIXES:
        fmt = "json"
    else:
        raise TeamDefinitionError(
            file_path.stem or "<team>",
            [TeamIssue(
                "unsupported_format",
                DOCUMENT,
                f"Unsupported team file extension {file_path.suffix!r}; expected .yaml, .yml or .json",
            )],
            source=file_path,
        )
    data = file_path.read_bytes()
    return parse_team_document(
        data,
        fmt=fmt,
        agents=agents,
        source=file_path,
        team_label=file_path.stem or "<team>",
    )


def _collect_semantic_issues(
    roles: Sequence[Role],
    members: Sequence[Member],
    assignments: Sequence[Assignment],
    relationships: Sequence[Relationship],
    *,
    agents: Iterable[str] | Mapping[str, Any] | None = None,
) -> list[TeamIssue]:
    issues: list[TeamIssue] = []

    seen_roles: dict[str, int] = {}
    for index, role in enumerate(roles):
        if role.id in seen_roles:
            issues.append(TeamIssue(
                "duplicate_role_id",
                f"roles[{index}].id",
                f"Role id {role.id!r} is already defined at roles[{seen_roles[role.id]}]",
            ))
        else:
            seen_roles[role.id] = index

    known_agents = set(agents) if agents is not None else None
    seen_members: dict[str, int] = {}
    for index, member in enumerate(members):
        if member.id in seen_members:
            issues.append(TeamIssue(
                "duplicate_member_id",
                f"members[{index}].id",
                f"Member id {member.id!r} is already defined at members[{seen_members[member.id]}]",
            ))
        else:
            seen_members[member.id] = index
        if known_agents is not None and member.kind == MemberKind.AGENT and member.agent not in known_agents:
            issues.append(TeamIssue(
                "unknown_agent",
                f"members[{index}].agent",
                f"Member {member.id!r} references unknown bundle agent {member.agent!r}",
            ))

    for index, assignment in enumerate(assignments):
        if assignment.role not in seen_roles:
            issues.append(TeamIssue(
                "unknown_role",
                f"assignments[{index}].role",
                f"Assignment references unknown role {assignment.role!r}",
            ))
        if assignment.member not in seen_members:
            issues.append(TeamIssue(
                "unknown_member",
                f"assignments[{index}].member",
                f"Assignment references unknown member {assignment.member!r}",
            ))

    reports_edges: dict[str, list[tuple[str, int]]] = {}
    for index, rel in enumerate(relationships):
        if rel.from_ not in seen_roles:
            issues.append(TeamIssue(
                "unknown_role",
                f"relationships[{index}].from",
                f"Relationship 'from' references unknown role {rel.from_!r}",
            ))
        if rel.to not in seen_roles:
            issues.append(TeamIssue(
                "unknown_role",
                f"relationships[{index}].to",
                f"Relationship 'to' references unknown role {rel.to!r}",
            ))
        if rel.from_ == rel.to:
            issues.append(TeamIssue(
                "self_relationship",
                f"relationships[{index}]",
                f"Role {rel.from_!r} cannot have a {rel.type.value!r} relationship with itself",
            ))
        elif rel.type == RelationshipType.REPORTS_TO and rel.from_ in seen_roles and rel.to in seen_roles:
            reports_edges.setdefault(rel.from_, []).append((rel.to, index))

    issues.extend(_reports_to_cycle_issues(seen_roles, reports_edges))
    return issues


def _reports_to_cycle_issues(
    seen_roles: Mapping[str, int],
    reports_edges: Mapping[str, list[tuple[str, int]]],
) -> list[TeamIssue]:
    issues: list[TeamIssue] = []
    unvisited, visiting, done = 0, 1, 2
    state: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        state[node] = visiting
        stack.append(node)
        for target, rel_index in reports_edges.get(node, ()):
            target_state = state.get(target, unvisited)
            if target_state == visiting:
                cycle_start = stack.index(target)
                cycle_path = [*stack[cycle_start:], target]
                issues.append(TeamIssue(
                    "reports_to_cycle",
                    f"relationships[{rel_index}]",
                    f"Cycle in 'reports_to' relationships: {' -> '.join(cycle_path)}",
                ))
            elif target_state == unvisited:
                dfs(target)
        stack.pop()
        state[node] = done

    for role_id in seen_roles:
        if state.get(role_id, unvisited) == unvisited and role_id in reports_edges:
            dfs(role_id)
    return issues


def _translate_validation_error(raw: Mapping[str, Any], exc: ValidationError) -> list[TeamIssue]:
    issues: list[TeamIssue] = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        loc = tuple(err["loc"])
        err_type = err["type"]
        msg = err["msg"]
        location = _format_loc(loc)

        if err_type == "invalid_capability_id":
            expanded = _expand_capability_issues(raw, loc, msg)
            issues.extend(expanded)
            continue
        if err_type == "invalid_permission":
            expanded = _expand_permission_issues(raw, loc, msg)
            issues.extend(expanded)
            continue
        if err_type == "member_missing_agent":
            issues.append(TeamIssue("missing_field", f"{location}.agent", msg))
            continue
        if err_type in ("member_blank_agent", "member_forbidden_agent"):
            issues.append(TeamIssue("invalid_value", f"{location}.agent", msg))
            continue

        issues.append(TeamIssue(_map_pydantic_code(err_type), location, msg))
    return issues


def _expand_capability_issues(raw: Mapping[str, Any], loc: tuple[int | str, ...], fallback_msg: str) -> list[TeamIssue]:
    if len(loc) == 3 and loc[0] == "roles" and isinstance(loc[1], int) and loc[2] == "capabilities":
        roles = raw.get("roles")
        if isinstance(roles, list) and 0 <= loc[1] < len(roles) and isinstance(roles[loc[1]], Mapping):
            raw_caps = roles[loc[1]].get("capabilities")
            if isinstance(raw_caps, list):
                found: list[TeamIssue] = []
                for idx, cap in enumerate(raw_caps):
                    try:
                        validate_capability_id(cap)
                    except CapabilityIdError as cap_exc:
                        found.append(TeamIssue(
                            "invalid_capability_id",
                            f"roles[{loc[1]}].capabilities[{idx}]",
                            str(cap_exc),
                        ))
                if found:
                    return found
    return [TeamIssue("invalid_capability_id", _format_loc(loc), fallback_msg)]


def _expand_permission_issues(raw: Mapping[str, Any], loc: tuple[int | str, ...], fallback_msg: str) -> list[TeamIssue]:
    if len(loc) == 3 and loc[0] == "roles" and isinstance(loc[1], int) and loc[2] == "permissions":
        roles = raw.get("roles")
        if isinstance(roles, list) and 0 <= loc[1] < len(roles) and isinstance(roles[loc[1]], Mapping):
            raw_perms = roles[loc[1]].get("permissions")
            if isinstance(raw_perms, list):
                expected = ", ".join(sorted(_IMPACT_VALUES))
                found: list[TeamIssue] = []
                for idx, perm in enumerate(raw_perms):
                    if not (isinstance(perm, ImpactClass) or (isinstance(perm, str) and perm in _IMPACT_VALUES)):
                        found.append(TeamIssue(
                            "invalid_permission",
                            f"roles[{loc[1]}].permissions[{idx}]",
                            f"Invalid permission {perm!r}; expected one of: {expected}",
                        ))
                if found:
                    return found
    return [TeamIssue("invalid_permission", _format_loc(loc), fallback_msg)]


def _map_pydantic_code(err_type: str) -> str:
    if err_type == "missing":
        return "missing_field"
    if err_type == "extra_forbidden":
        return "unknown_field"
    if err_type in ("invalid_schema_version", "invalid_mode", "self_relationship", "invalid_value"):
        return err_type
    if err_type in ("literal_error", "enum", "value_error") or err_type.startswith(("string_too_short", "too_short")):
        return "invalid_value"
    if "type" in err_type or err_type.startswith(("string_", "int_", "bool_", "list_", "dict_", "model_")):
        return "invalid_type"
    return "invalid_value"


def _format_loc(loc: tuple[int | str, ...]) -> str:
    if not loc:
        return ROOT
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    seen: set[Hashable] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if isinstance(key, Hashable):
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _unique_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _parse_raw_document(
    document: bytes | str,
    *,
    fmt: Literal["yaml", "json"],
    source: Path | None,
    team_label: str,
) -> Any:
    code = "yaml_invalid" if fmt == "yaml" else "json_invalid"
    try:
        text = document.decode("utf-8") if isinstance(document, bytes) else document
    except UnicodeDecodeError as exc:
        raise TeamDefinitionError(
            team_label,
            [TeamIssue(code, DOCUMENT, f"The file is not valid UTF-8: {exc.reason} at byte {exc.start}")],
            source=source,
        ) from exc

    if fmt == "yaml":
        try:
            return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
        except yaml.YAMLError as exc:
            raise TeamDefinitionError(
                team_label,
                [TeamIssue("yaml_invalid", DOCUMENT, f"Invalid YAML: {exc}")],
                source=source,
            ) from exc

    try:
        return json.loads(text, object_pairs_hook=_unique_json_pairs)
    except (json.JSONDecodeError, ValueError) as exc:
        raise TeamDefinitionError(
            team_label,
            [TeamIssue("json_invalid", DOCUMENT, f"Invalid JSON: {exc}")],
            source=source,
        ) from exc
