# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure ``office.yaml`` (``maf.office/1``) schema and filesystem safety checks.

Reads and validates an office directory without touching the installed
registry, application services, the GUI, or the network.
"""
from __future__ import annotations

import ntpath
import re
from collections.abc import Hashable, Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
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
from ..contracts import StrictModel
from ..tools.files import _is_link_or_junction

OFFICE_SCHEMA = "maf.office/1"
OFFICE_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,39}$"
SENSITIVITIES = ("normal", "sensitive")
RELATION_KINDS = ("hands_off", "reviews", "consults")
RELATION_LABELS = {
    "hands_off": "entrega",
    "reviews": "revisa",
    "consults": "consulta",
}

_OFFICE_ID = re.compile(OFFICE_ID_PATTERN + r"\Z")
_SHA256 = re.compile(r"^[0-9a-f]{64}\Z")
_DOCUMENT = "<document>"
_ROOT = "<root>"


def _sorted_values(values: Any) -> list[str]:
    return sorted(str(item) for item in values)


_SortedSet = PlainSerializer(_sorted_values, return_type=list[str], when_used="json")


class _OfficeModel(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )


def _issue(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


class OfficeDefinitionError(ValueError):
    """Raised when an ``office.yaml`` definition fails structural or safety validation."""

    def __init__(self, issues: Sequence[Mapping[str, str]], *, source: Path | None = None) -> None:
        self.issues: list[dict[str, str]] = [
            {
                "code": str(item["code"]),
                "message": str(item["message"]),
                "location": str(item["location"]),
            }
            for item in issues
        ]
        self.source = source
        where = f" ({source})" if source else ""
        count = len(self.issues)
        summary = f"Invalid office definition{where}: {count} problem{'' if count == 1 else 's'}"
        details = [f"  - [{item['code']}] {item['location']}: {item['message']}" for item in self.issues]
        super().__init__("\n".join([summary, *details]))


def area_bundle_name(office_id: str, area_id: str) -> str:
    """Return the unambiguous bundle name ``<office_id>__<area_id>`` for an office area."""
    if not isinstance(office_id, str) or _OFFICE_ID.fullmatch(office_id) is None:
        raise ValueError(f"Invalid office id {office_id!r}")
    if not isinstance(area_id, str) or _OFFICE_ID.fullmatch(area_id) is None:
        raise ValueError(f"Invalid area id {area_id!r}")
    return f"{office_id}__{area_id}"


def _require_non_blank(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PydanticCustomError("office_invalid", f"{label} must be a non-blank string")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise PydanticCustomError("office_invalid", f"{label} must be a boolean")
    return value


def _require_office_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _OFFICE_ID.fullmatch(value) is None:
        raise PydanticCustomError(
            "office_invalid",
            f"{label} {value!r} must match {OFFICE_ID_PATTERN}",
        )
    return value


def _validate_relative_path(value: Any) -> str:
    if not isinstance(value, str):
        raise PydanticCustomError("office_invalid", "Area path must be a string")
    if not value.strip():
        raise PydanticCustomError("office_invalid", "Area path must not be blank")
    if "\x00" in value:
        raise PydanticCustomError("office_path_unsafe", "Area path must not contain null bytes")
    if "\\" in value:
        raise PydanticCustomError("office_path_unsafe", "Backslashes are not allowed in area path")
    if value.startswith("/"):
        raise PydanticCustomError("office_path_unsafe", "Absolute paths are not allowed in area path")
    drive, _ = ntpath.splitdrive(value)
    if drive or PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute():
        raise PydanticCustomError("office_path_unsafe", "Absolute and drive paths are not allowed in area path")
    parts = value.split("/")
    if ".." in parts:
        raise PydanticCustomError("office_path_unsafe", "Parent directory references ('..') are not allowed")
    clean = tuple(part for part in parts if part not in ("", "."))
    if not clean:
        raise PydanticCustomError("office_path_unsafe", "Area path must point to a subfolder inside the office")
    return "/".join(clean)


class OfficeArea(_OfficeModel):
    """One area (team bundle) declared by an office."""

    id: str
    name: str
    path: str
    team_sha256: str | None = None
    sensitivity: Literal["normal", "sensitive"] | None = None
    local_only: bool = False
    requires: Annotated[frozenset[str], _SortedSet] = frozenset()

    @field_validator("id", mode="before")
    @classmethod
    def _valid_id(cls, value: Any) -> str:
        return _require_office_id(value, "Area id")

    @field_validator("name", mode="before")
    @classmethod
    def _valid_name(cls, value: Any) -> str:
        return _require_non_blank(value, "Area name")

    @field_validator("path", mode="before")
    @classmethod
    def _valid_path(cls, value: Any) -> str:
        return _validate_relative_path(value)

    @field_validator("team_sha256", mode="before")
    @classmethod
    def _valid_team_sha256(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise PydanticCustomError("office_invalid", "team_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("sensitivity", mode="before")
    @classmethod
    def _valid_sensitivity(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value not in SENSITIVITIES:
            expected = ", ".join(SENSITIVITIES)
            raise PydanticCustomError("office_invalid", f"Invalid sensitivity {value!r}; expected one of: {expected}")
        return value

    @field_validator("local_only", mode="before")
    @classmethod
    def _valid_local_only(cls, value: Any) -> bool:
        return _require_bool(value, "Area local_only")

    @field_validator("requires", mode="before")
    @classmethod
    def _valid_requires(cls, value: Any) -> frozenset[str]:
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            raise PydanticCustomError("invalid_capability_id", "Area requires must be a list of capability ids")
        try:
            return validate_capability_ids(list(value))
        except CapabilityIdError as exc:
            raise PydanticCustomError(
                "invalid_capability_id",
                f"Invalid capability id {exc.value!r}",
            ) from exc


class PlanItem(_OfficeModel):
    """One task template in ``orchestrator.default_plan``."""

    id: str | None = None
    area: str
    workflow: str
    after: list[str] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _valid_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _require_non_blank(value, "Plan item id")

    @field_validator("area", mode="before")
    @classmethod
    def _valid_area(cls, value: Any) -> str:
        return _require_non_blank(value, "Plan item area")

    @field_validator("workflow", mode="before")
    @classmethod
    def _valid_workflow(cls, value: Any) -> str:
        return _require_non_blank(value, "Plan item workflow")

    @field_validator("after", mode="before")
    @classmethod
    def _valid_after(cls, value: Any) -> list[str]:
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            raise PydanticCustomError("office_invalid", "Plan item 'after' must be a list of plan item ids")
        items = list(value)
        for item in items:
            _require_non_blank(item, "Plan item 'after' entry")
        return items

    @model_validator(mode="after")
    def _default_id_from_area(self) -> PlanItem:
        if self.id is None:
            object.__setattr__(self, "id", self.area)
        return self


class Orchestrator(_OfficeModel):
    """Orchestrator configuration for an office."""

    enabled: bool = True
    name: str | None = None
    default_plan: list[PlanItem] = Field(default_factory=list)

    @field_validator("enabled", mode="before")
    @classmethod
    def _valid_enabled(cls, value: Any) -> bool:
        return _require_bool(value, "Orchestrator enabled")

    @field_validator("name", mode="before")
    @classmethod
    def _valid_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _require_non_blank(value, "Orchestrator name")


class Relation(_OfficeModel):
    """Directed relationship between two areas in an office."""

    from_: str = Field(alias="from")
    to: str
    kind: Literal["hands_off", "reviews", "consults"]

    @field_validator("from_", mode="before")
    @classmethod
    def _valid_from(cls, value: Any) -> str:
        return _require_non_blank(value, "Relation 'from'")

    @field_validator("to", mode="before")
    @classmethod
    def _valid_to(cls, value: Any) -> str:
        return _require_non_blank(value, "Relation 'to'")

    @field_validator("kind", mode="before")
    @classmethod
    def _valid_kind(cls, value: Any) -> str:
        if not isinstance(value, str) or value not in RELATION_KINDS:
            expected = ", ".join(RELATION_KINDS)
            raise PydanticCustomError("office_invalid", f"Invalid relation kind {value!r}; expected one of: {expected}")
        return value


class OfficeDefinition(_OfficeModel):
    """Validated ``office.yaml`` (``maf.office/1``) document."""

    schema_: Literal["maf.office/1"] = Field(default=OFFICE_SCHEMA, alias="schema")
    id: str
    name: str
    description: str | None = None
    local_only: bool = False
    orchestrator: Orchestrator = Field(default_factory=lambda: Orchestrator(enabled=False))
    areas: list[OfficeArea] = Field(min_length=1)
    relations: list[Relation] = Field(default_factory=list)
    warnings: list[dict[str, str]] = Field(default_factory=list)

    @field_validator("schema_", mode="before")
    @classmethod
    def _valid_schema(cls, value: Any) -> str:
        if value != OFFICE_SCHEMA:
            raise PydanticCustomError("office_invalid", f"Unsupported schema {value!r}; expected {OFFICE_SCHEMA!r}")
        return value

    @field_validator("id", mode="before")
    @classmethod
    def _valid_id(cls, value: Any) -> str:
        return _require_office_id(value, "Office id")

    @field_validator("name", mode="before")
    @classmethod
    def _valid_name(cls, value: Any) -> str:
        return _require_non_blank(value, "Office name")

    @field_validator("description", mode="before")
    @classmethod
    def _valid_description(cls, value: Any) -> str | None:
        if value is not None and not isinstance(value, str):
            raise PydanticCustomError("office_invalid", "Office description must be a string")
        return value

    @field_validator("local_only", mode="before")
    @classmethod
    def _valid_local_only(cls, value: Any) -> bool:
        return _require_bool(value, "Office local_only")

    @model_validator(mode="after")
    def _validate_semantics(self, info: ValidationInfo) -> OfficeDefinition:
        if info.context and info.context.get("skip_office_semantics"):
            return self
        issues, warnings = _collect_semantic_issues(self)
        if issues:
            raise PydanticCustomError(
                issues[0]["code"],
                "; ".join(f"[{item['code']}] {item['location']}: {item['message']}" for item in issues),
            )
        object.__setattr__(self, "warnings", warnings)
        return self


def load_office(root: Path | str) -> OfficeDefinition:
    """Read and validate ``root / 'office.yaml'`` without side effects or network access."""
    root_path = Path(root)
    if _is_link_or_junction(root_path):
        raise OfficeDefinitionError(
            [_issue("office_path_unsafe", _ROOT, "Office root directory must not be a symbolic link or junction")],
            source=root_path,
        )
    if not root_path.exists() or not root_path.is_dir():
        raise OfficeDefinitionError(
            [_issue("office_invalid", _ROOT, f"Office root {str(root_path)!r} is not an existing directory")],
            source=root_path,
        )

    manifest_path = root_path / "office.yaml"
    if _is_link_or_junction(manifest_path):
        raise OfficeDefinitionError(
            [_issue("office_path_unsafe", "office.yaml", "office.yaml must not be a symbolic link or junction")],
            source=manifest_path,
        )
    if not manifest_path.exists() or not manifest_path.is_file():
        raise OfficeDefinitionError(
            [_issue("office_invalid", "office.yaml", "Missing office.yaml in office directory")],
            source=manifest_path,
        )

    raw = _parse_raw_yaml(manifest_path)
    if not isinstance(raw, dict):
        raise OfficeDefinitionError(
            [_issue("office_invalid", _ROOT, f"Expected a YAML mapping; got {type(raw).__name__}")],
            source=manifest_path,
        )

    pre_issues: list[dict[str, str]] = []
    if "schema" not in raw:
        pre_issues.append(_issue("office_invalid", "schema", f"Missing required field 'schema' ({OFFICE_SCHEMA})"))
    if "warnings" in raw:
        pre_issues.append(_issue("office_invalid", "warnings", "Unknown field 'warnings'"))

    try:
        office = OfficeDefinition.model_validate(raw, context={"skip_office_semantics": True})
    except ValidationError as exc:
        issues = [*pre_issues, *_translate_validation_error(raw, exc)]
        raise OfficeDefinitionError(issues, source=manifest_path) from exc

    if pre_issues:
        raise OfficeDefinitionError(pre_issues, source=manifest_path)

    issues, warnings = _collect_semantic_issues(office)
    issues.extend(_collect_filesystem_issues(root_path, office.areas))
    if issues:
        raise OfficeDefinitionError(issues, source=manifest_path)

    object.__setattr__(office, "warnings", warnings)
    return office


def _collect_semantic_issues(office: OfficeDefinition) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    seen_areas: dict[str, int] = {}
    for index, area in enumerate(office.areas):
        if area.id in seen_areas:
            issues.append(_issue(
                "office_duplicate_id",
                f"areas[{index}].id",
                f"Area id {area.id!r} is already defined at areas[{seen_areas[area.id]}]",
            ))
        else:
            seen_areas[area.id] = index

    seen_plan_ids: dict[str, int] = {}
    plan_items = office.orchestrator.default_plan
    for index, item in enumerate(plan_items):
        item_id = item.id or item.area
        if item_id in seen_plan_ids:
            issues.append(_issue(
                "office_duplicate_id",
                f"orchestrator.default_plan[{index}].id",
                f"Plan item id {item_id!r} is already used at orchestrator.default_plan[{seen_plan_ids[item_id]}]",
            ))
        else:
            seen_plan_ids[item_id] = index

        if item.area not in seen_areas:
            issues.append(_issue(
                "office_invalid",
                f"orchestrator.default_plan[{index}].area",
                f"Plan item {item_id!r} references unknown area {item.area!r}",
            ))

    for index, item in enumerate(plan_items):
        item_id = item.id or item.area
        for dep_index, dep in enumerate(item.after):
            if dep == item_id:
                issues.append(_issue(
                    "office_invalid",
                    f"orchestrator.default_plan[{index}].after[{dep_index}]",
                    f"Plan item {item_id!r} cannot depend on itself",
                ))
            elif dep not in seen_plan_ids:
                issues.append(_issue(
                    "office_invalid",
                    f"orchestrator.default_plan[{index}].after[{dep_index}]",
                    f"Plan item {item_id!r} depends on unknown plan item {dep!r}",
                ))

    issues.extend(_plan_cycle_issues(plan_items, seen_plan_ids))

    for index, rel in enumerate(office.relations):
        if rel.from_ not in seen_areas:
            warnings.append(_issue(
                "office_relation_unknown_area",
                f"relations[{index}].from",
                f"Relation references unknown area {rel.from_!r}",
            ))
        if rel.to not in seen_areas:
            warnings.append(_issue(
                "office_relation_unknown_area",
                f"relations[{index}].to",
                f"Relation references unknown area {rel.to!r}",
            ))

    return issues, warnings


def _plan_cycle_issues(plan_items: Sequence[PlanItem], seen_plan_ids: Mapping[str, int]) -> list[dict[str, str]]:
    edges: dict[str, list[tuple[str, int]]] = {}
    for index, item in enumerate(plan_items):
        item_id = item.id or item.area
        for dep in item.after:
            if dep != item_id and dep in seen_plan_ids:
                edges.setdefault(item_id, []).append((dep, index))

    issues: list[dict[str, str]] = []
    unvisited, visiting, done = 0, 1, 2
    state: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        state[node] = visiting
        stack.append(node)
        for target, item_index in edges.get(node, ()):
            target_state = state.get(target, unvisited)
            if target_state == visiting:
                start = stack.index(target)
                cycle = " -> ".join([*stack[start:], target])
                issues.append(_issue(
                    "office_invalid",
                    f"orchestrator.default_plan[{item_index}].after",
                    f"Cycle in default_plan 'after' dependencies: {cycle}",
                ))
            elif target_state == unvisited:
                dfs(target)
        stack.pop()
        state[node] = done

    for item_id in seen_plan_ids:
        if state.get(item_id, unvisited) == unvisited and item_id in edges:
            dfs(item_id)
    return issues


def _collect_filesystem_issues(root: Path, areas: Sequence[OfficeArea]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    root_resolved = root.resolve()

    for index, area in enumerate(areas):
        location = f"areas[{index}].path"
        current = root
        unsafe = False
        for part in area.path.split("/"):
            current = current / part
            if _is_link_or_junction(current):
                issues.append(_issue(
                    "office_path_unsafe",
                    location,
                    f"Area path {area.path!r} uses a symbolic link or junction",
                ))
                unsafe = True
                break
        if unsafe:
            continue

        try:
            resolved = current.resolve()
        except OSError as exc:
            issues.append(_issue(
                "office_path_unsafe",
                location,
                f"Area path {area.path!r} could not be resolved safely: {exc}",
            ))
            continue

        if not resolved.is_relative_to(root_resolved):
            issues.append(_issue(
                "office_path_unsafe",
                location,
                f"Area path {area.path!r} escapes the office directory",
            ))
            continue

        if not resolved.exists() or not resolved.is_dir():
            issues.append(_issue(
                "office_invalid",
                location,
                f"Area folder {area.path!r} does not exist or is not a directory",
            ))

    return issues


def _translate_validation_error(raw: Mapping[str, Any], exc: ValidationError) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        loc = tuple(err["loc"])
        err_type = err["type"]
        msg = err["msg"]
        if err_type == "invalid_capability_id":
            issues.extend(_expand_capability_issues(raw, loc, msg))
            continue
        code = "office_path_unsafe" if err_type == "office_path_unsafe" else "office_invalid"
        issues.append(_issue(code, _format_loc(loc), msg))
    return issues


def _expand_capability_issues(raw: Mapping[str, Any], loc: tuple[int | str, ...], fallback_msg: str) -> list[dict[str, str]]:
    if len(loc) == 3 and loc[0] == "areas" and isinstance(loc[1], int) and loc[2] == "requires":
        areas = raw.get("areas")
        if isinstance(areas, list) and 0 <= loc[1] < len(areas) and isinstance(areas[loc[1]], Mapping):
            raw_requires = areas[loc[1]].get("requires")
            if isinstance(raw_requires, list):
                found: list[dict[str, str]] = []
                for idx, cap in enumerate(raw_requires):
                    try:
                        validate_capability_id(cap)
                    except CapabilityIdError as cap_exc:
                        found.append(_issue(
                            "office_invalid",
                            f"areas[{loc[1]}].requires[{idx}]",
                            str(cap_exc),
                        ))
                if found:
                    return found
    return [_issue("office_invalid", _format_loc(loc), fallback_msg)]


def _format_loc(loc: tuple[int | str, ...]) -> str:
    if not loc:
        return _ROOT
    alias_map = {"schema_": "schema", "from_": "from"}
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            name = alias_map.get(str(item), str(item))
            parts.append(name)
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


def _parse_raw_yaml(path: Path) -> Any:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise OfficeDefinitionError(
            [_issue("office_invalid", _DOCUMENT, f"Could not read UTF-8 office.yaml: {exc}")],
            source=path,
        ) from exc

    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise OfficeDefinitionError(
            [_issue("office_invalid", _DOCUMENT, f"Invalid YAML in office.yaml: {exc}")],
            source=path,
        ) from exc
