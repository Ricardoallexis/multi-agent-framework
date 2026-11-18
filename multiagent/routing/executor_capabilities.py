"""Versioned, provider-neutral executor capability declarations for assignment."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from ..capabilities import invalid_capability_ids

_logger = logging.getLogger(__name__)
_warned_capability_ids: set[str] = set()
_warning_lock = threading.Lock()


def clear_executor_capability_warning_cache() -> None:
    """Clear the process-wide warning registry (primarily for isolated tests)."""
    with _warning_lock:
        _warned_capability_ids.clear()


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


# Evidence kept since T15 (model/effort per call, context isolation, cancellation).
BASE_EVIDENCE_FIELDS = ("model_support", "effort_support", "context_isolation", "cancellation")
# ORQ-T46 (contract ORQ-6 section 2): what an executor application (a CLI-driven agent app) declares.
# SUPPORTED means the property holds (for ``blocks_when_open``: the app blocks writes while a session is
# open); UNSUPPORTED means it was observed not to hold; UNKNOWN (default) means no evidence either way.
EXECUTOR_EVIDENCE_FIELDS = ("noninteractive_launch", "structured_output", "session_store", "resume",
                            "blocks_when_open", "open_in_app", "quota_signal", "terms")
EVIDENCE_FIELDS = BASE_EVIDENCE_FIELDS + EXECUTOR_EVIDENCE_FIELDS
_SET_FIELDS = ("capabilities_available", "roles_available", "permissions_available",
               "permissions_required", "permissions_consumed", "permission_ceiling",
               "allowed_data_classes", "execution_modes")


@dataclass(frozen=True)
class CapabilityEvidence:
    status: SupportState = SupportState.UNKNOWN
    source: str | None = None
    provenance: str = "unknown"
    scope: str | None = None
    observed_at: str | None = None
    values: frozenset[str] | None = None
    # MOD-T04 (contract MOD section 2): model evidence names the provider model it describes and may carry a caveat.
    # Both are optional and left out of ``to_dict`` while ``None``, so an executor descriptor keeps its old shape.
    model: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SupportState):
            object.__setattr__(self, "status", SupportState(self.status))
        object.__setattr__(self, "values", _set(self.values))

    def to_dict(self) -> dict[str, Any]:
        result = {"status": self.status.value, "source": self.source,
                  "provenance": self.provenance, "scope": self.scope,
                  "observed_at": self.observed_at,
                  "values": sorted(self.values) if self.values is not None else None}
        if self.model is not None:
            result["model"] = self.model
        if self.note is not None:
            result["note"] = self.note
        return result


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
    # ORQ-T46: executor-application capabilities (launch, structured output, sessions, resume, blocking,
    # open in app, quota, terms). Every one is unknown until evidence is declared.
    noninteractive_launch: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    structured_output: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    session_store: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    resume: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    blocks_when_open: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    open_in_app: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    quota_signal: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    terms: CapabilityEvidence = field(default_factory=CapabilityEvidence)
    # An executor declared ``enabled: false`` is never selected, whatever its mode or evidence.
    enabled: bool = True

    def __post_init__(self) -> None:
        for name in _SET_FIELDS:
            object.__setattr__(self, name, _set(getattr(self, name)))
        for name in EVIDENCE_FIELDS:
            value = getattr(self, name)
            if value is None:
                object.__setattr__(self, name, CapabilityEvidence())
            elif isinstance(value, Mapping):
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
    if not isinstance(descriptor.enabled, bool):
        raise ValueError("executor enabled must be true or false")
    for name in EXECUTOR_EVIDENCE_FIELDS:
        evidence = getattr(descriptor, name)
        if not isinstance(evidence, CapabilityEvidence):
            raise ValueError(f"{name} must be capability evidence")
        if evidence.status is SupportState.UNKNOWN:
            continue
        # Both a positive and a negative claim need a verifiable origin and a date.
        if (not evidence.source or evidence.provenance in {"", "unknown", None}
                or not evidence.scope or not evidence.observed_at):
            raise ValueError(f"{name} evidence requires source, provenance, scope, and observed_at")
        if evidence.status is SupportState.SUPPORTED and name != "blocks_when_open" and not evidence.values:
            raise ValueError(f"supported {name} requires explicit values")
    return descriptor


def executor_capability_warnings(descriptor: ExecutorDescriptor) -> list[str]:
    """Declared ``capabilities_available`` ids outside the namespaced pattern (a warning, not an error)."""
    return invalid_capability_ids(descriptor.capabilities_available or ())


def descriptor_from_declaration(executor_id: str, declaration: Mapping[str, Any]) -> ExecutorDescriptor:
    """Manual descriptor from a catalog declaration (no adapter): never automatic, evidence as declared."""
    kwargs: dict[str, Any] = {name: declaration.get(name) for name in _SET_FIELDS}
    kwargs.update({name: declaration.get(name) for name in EVIDENCE_FIELDS if declaration.get(name) is not None})
    descriptor = validate_executor_descriptor(ExecutorDescriptor(
        executor_id=str(executor_id), surface=str(declaration.get("surface", "unknown")),
        version=str(declaration.get("version", "1")), mode="manual", provenance="catalog_declared",
        enabled=declaration.get("enabled", True) is True, **kwargs))
    # S5.C1: existing declarations may use ids outside the namespaced pattern; warn, never reject.
    for bad in executor_capability_warnings(descriptor):
        with _warning_lock:
            should_warn = bad not in _warned_capability_ids
            _warned_capability_ids.add(bad)
        if should_warn:
            _logger.warning("Executor %s declares capability %r outside the namespaced pattern (e.g. file.read)",
                            descriptor.executor_id, bad)
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
        if "enabled" in entry and not isinstance(entry["enabled"], bool):
            raise ValueError(f"executor {executor_id} enabled must be true or false")
        for name in EVIDENCE_FIELDS:
            evidence = entry.get(name)
            if evidence is None:
                continue
            if not isinstance(evidence, Mapping):
                raise ValueError(f"executor {executor_id} {name} must be a mapping")
            if evidence.get("observed_at") is not None and not isinstance(evidence["observed_at"], str):
                raise ValueError(f"executor {executor_id} {name} observed_at must be a quoted string")
        try:
            descriptor_from_declaration(executor_id, entry)
        except TypeError as exc:
            raise ValueError(f"executor {executor_id} declaration is invalid: {exc}") from exc
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
