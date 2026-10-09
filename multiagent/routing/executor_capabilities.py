"""Versioned, provider-neutral executor capability declarations for assignment."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class SupportState(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


def _set(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


@dataclass(frozen=True)
class CapabilityEvidence:
    status: SupportState = SupportState.UNKNOWN
    source: str | None = None
    provenance: str = "unknown"
    scope: str | None = None
    observed_at: str | None = None
    values: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SupportState):
            object.__setattr__(self, "status", SupportState(self.status))
        object.__setattr__(self, "values", _set(self.values))

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "source": self.source,
                "provenance": self.provenance, "scope": self.scope,
                "observed_at": self.observed_at,
                "values": sorted(self.values) if self.values is not None else None}


@dataclass(frozen=True)
class VerifiedOperatorContext:
    """Per-call trusted caller context; global config policy cannot create one."""
    operator_id: str
    actor_alias: str
    grants: frozenset[str] | None = None
    provenance: str = "unknown"
    verified: bool = False
    trusted_data_class: str | None = None
    data_class_scope: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "grants", _set(self.grants))


@dataclass(frozen=True)
class ExecutorDescriptor:
    executor_id: str
    surface: str
    version: str = "1"
    adapter_id: str | None = None
    adapter_version: str | None = None
    mode: str = "manual"
    capabilities_available: frozenset[str] | None = None
    roles_available: frozenset[str] | None = None
    permissions_available: frozenset[str] | None = None
    permissions_required: frozenset[str] | None = None
    permissions_consumed: frozenset[str] | None = None
    permission_ceiling: frozenset[str] | None = None
    allowed_data_classes: frozenset[str] | None = None
    execution_modes: frozenset[str] | None = None
    model_support: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    effort_support: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    context_isolation: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    cancellation: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    provenance: str = "catalog_declared"

    def __post_init__(self) -> None:
        for name in ("capabilities_available", "roles_available", "permissions_available",
                     "permissions_required", "permissions_consumed", "permission_ceiling",
                     "allowed_data_classes", "execution_modes"):
            object.__setattr__(self, name, _set(getattr(self, name)))
        for name in ("model_support", "effort_support", "context_isolation", "cancellation"):
            value = getattr(self, name)
            if isinstance(value, Mapping):
                object.__setattr__(self, name, CapabilityEvidence(**value))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, frozenset):
                result[name] = sorted(value)
            elif isinstance(value, CapabilityEvidence):
                result[name] = value.to_dict()
            else:
                result[name] = value
        return result


def validate_executor_descriptor(value: ExecutorDescriptor | Mapping[str, Any]) -> ExecutorDescriptor:
    if isinstance(value, ExecutorDescriptor):
        descriptor = value
    elif isinstance(value, Mapping):
        descriptor = ExecutorDescriptor(**dict(value))
    else:
        raise TypeError("executor descriptor must be a mapping or ExecutorDescriptor")
    if not descriptor.executor_id or not descriptor.surface or not descriptor.version:
        raise ValueError("executor_id, surface, and version are required")
    if descriptor.mode not in {"automatic", "manual", "inherited", "unknown"}:
        raise ValueError("executor mode must be automatic, manual, inherited, or unknown")
    for name in ("model_support", "effort_support", "context_isolation"):
        evidence = getattr(descriptor, name)
        if evidence.status is not SupportState.SUPPORTED:
            continue
        if not evidence.values:
            expected = {"model_support": "model IDs", "effort_support": "effort values",
                        "context_isolation": "context-isolation values"}[name]
            raise ValueError(f"supported {name} requires explicit {expected}")
        if not evidence.source or evidence.provenance in {"", "unknown", None} or not evidence.scope:
            raise ValueError(f"supported {name} requires source, provenance, and scope")
    return descriptor


def validate_executor_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate executor_routing without resolving adapters or inferring support."""
    if not isinstance(config, Mapping) or config.get("version") != 1:
        raise ValueError("executor_routing must be a version 1 mapping")
    profiles = config.get("profiles", {})
    executors = config.get("executors", {})
    requirement_map = config.get("requirement_map", {})
    boundary = config.get("operator_boundary", {})
    data_classifications = config.get("data_classifications", {})
    for name, value in (("profiles", profiles), ("executors", executors),
                        ("requirement_map", requirement_map), ("operator_boundary", boundary),
                        ("data_classifications", data_classifications)):
        if not isinstance(value, Mapping):
            raise ValueError(f"executor_routing.{name} must be a mapping")
    for executor_id, entry in executors.items():
        if not isinstance(executor_id, str) or not executor_id or not isinstance(entry, Mapping):
            raise ValueError("executor declarations require nonempty IDs and mapping values")
        surface = entry.get("surface")
        if not isinstance(surface, str) or not surface.strip() or surface != surface.strip():
            raise ValueError(f"executor {executor_id} requires a nonempty surface ID")
    for scope, data_class in data_classifications.items():
        if (not isinstance(scope, str) or not any(scope.startswith(prefix) for prefix in ("task:", "group:", "envelope:"))
                or not isinstance(data_class, str) or not data_class.strip()):
            raise ValueError("data classifications require scoped keys and nonempty class labels")
        if scope.startswith("envelope:") and (len(scope) != 73 or any(ch not in "0123456789abcdef" for ch in scope[9:])):
            raise ValueError("envelope classification scope must contain a sha256 digest")
    for profile_id, profile in profiles.items():
        if not isinstance(profile_id, str) or not profile_id or not isinstance(profile, Mapping):
            raise ValueError("profile declarations require nonempty IDs and mapping values")
        ids = profile.get("executor_ids", [])
        if not isinstance(ids, (list, tuple)) or any(not isinstance(item, str) for item in ids):
            raise ValueError(f"profile {profile_id} executor_ids must be a list of IDs")
    return {"version": 1, "profiles": dict(profiles), "executors": dict(executors),
            "requirement_map": dict(requirement_map), "operator_boundary": dict(boundary),
            "data_classifications": dict(data_classifications)}
