"""Pure selector for previewing extensible executor assignments."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .executor_capabilities import (
    CapabilityEvidence, ExecutorDescriptor, SupportState, validate_executor_descriptor,
)


def _strings(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


@dataclass(frozen=True)
class AssignmentRequest:
    """Normalized immutable request; canonical task fields are service-populated."""
    task_id: str
    profile_id: str | None = None
    attempt: int | None = None
    revision: int | None = None
    envelope_hash: str | None = None
    group_id: str | None = None
    task_status: str | None = None
    preferred: str | None = None
    file_scope: tuple[str, ...] = ()
    canonical_requirements: tuple[str, ...] = ()
    additional_requirements: tuple[str, ...] = ()
    required_capabilities: frozenset[str] | None = frozenset()
    required_role: str | None = None
    required_roles: frozenset[str] | None = frozenset()
    required_permissions: frozenset[str] | None = frozenset()
    permission_scope: frozenset[str] | None = None
    data_class: str | None = None
    requested_executor: str | None = None
    requested_model: str | None = None
    requested_effort: str | None = None
    required_context_isolation: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1

    def __post_init__(self) -> None:
        for name in ("required_capabilities", "required_roles", "required_permissions", "permission_scope"):
            object.__setattr__(self, name, _strings(getattr(self, name)))
        for name in ("file_scope", "canonical_requirements", "additional_requirements"):
            object.__setattr__(self, name, tuple(str(item) for item in getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        result = {}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, frozenset):
                result[name] = sorted(value) if value is not None else None
            elif isinstance(value, tuple):
                result[name] = list(value)
            elif isinstance(value, Mapping):
                result[name] = dict(value)
            else:
                result[name] = value
        return result


@dataclass(frozen=True)
class AssignmentDecision:
    status: str
    reason_codes: tuple[str, ...]
    request: AssignmentRequest
    profile_id: str | None = None
    profile_version: str | int | None = None
    selected_executor: str | None = None
    planned_executor: str | None = None
    effective_executor: str | None = None
    observed_executor: str | None = None
    requested_model: str | None = None
    planned_model: str | None = None
    effective_model: str | None = None
    observed_model: str | None = None
    requested_effort: str | None = None
    planned_effort: str | None = None
    effective_effort: str | None = None
    observed_effort: str | None = None
    requested_context_isolation: str | None = None
    planned_context_isolation: str | None = None
    effective_context_isolation: str | None = None
    observed_context_isolation: str | None = None
    required_capabilities: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    available_capabilities: tuple[str, ...] | None = None
    required_permissions: tuple[str, ...] = ()
    consumed_permissions: tuple[str, ...] | None = None
    available_permissions: tuple[str, ...] | None = None
    granted_permissions: tuple[str, ...] | None = None
    permission_ceiling: tuple[str, ...] | None = None
    allowed_data_classes: tuple[str, ...] | None = None
    routing_decision: Any = None
    descriptor_hashes: Mapping[str, str] = field(default_factory=dict)
    config_hash: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if name == "request":
                result[name] = value.to_dict()
            elif name == "routing_decision" and hasattr(value, "to_dict"):
                result[name] = value.to_dict()
            elif isinstance(value, tuple):
                result[name] = list(value)
            elif isinstance(value, Mapping):
                result[name] = dict(value)
            else:
                result[name] = value
        return result


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             default=str).encode("utf-8")).hexdigest()


def _grant_values(grants: Any) -> tuple[frozenset[str] | None, frozenset[str] | None, Mapping[str, Any]]:
    if grants is None:
        return None, None, {}
    if isinstance(grants, Mapping):
        effective = _strings(grants.get("effective_permissions", grants.get("granted_permissions")))
        ceiling = _strings(grants.get("permission_ceiling"))
        return effective, ceiling, grants
    return _strings(grants), None, {}


def select_assignment(request: AssignmentRequest, *, profile: Mapping[str, Any] | None,
                      descriptors: Mapping[str, ExecutorDescriptor], routing_decision: Any,
                      grants: Any, routing_by_executor: Mapping[str, Any] | None = None) -> AssignmentDecision:
    """Select a descriptor only when every required constraint is known and satisfied.

    ``routing_by_executor`` (ECO-T08) maps an executor id to the model routing decision made for that executor alone
    (candidates it cannot receive left out); an executor without an entry is judged by ``routing_decision``. The
    decision returned carries the routing decision of the executor it selected.
    """
    profile_id = request.profile_id
    config_hash = _hash(profile or {})
    empty = dict(request=request, profile_id=profile_id,
                 requested_model=request.requested_model,
                 requested_effort=request.requested_effort,
                 requested_context_isolation=request.required_context_isolation,
                 routing_decision=routing_decision, config_hash=config_hash)
    if profile_id is None:
        return AssignmentDecision(status="manual", reason_codes=("no_profile_requested",), **empty)
    if not isinstance(profile, Mapping):
        return AssignmentDecision(status="blocked", reason_codes=("unknown_profile",), **empty)
    profile_version = profile.get("version")
    empty["profile_version"] = profile_version
    if profile.get("enabled") is not True:
        return AssignmentDecision(status="manual", reason_codes=("profile_disabled",), **empty)
    ids = profile.get("executor_ids")
    if not isinstance(ids, (list, tuple)) or not ids:
        return AssignmentDecision(status="manual", reason_codes=("profile_has_no_executors",), **empty)
    if request.requested_executor:
        if request.requested_executor not in ids:
            return AssignmentDecision(status="manual", reason_codes=("requested_executor_outside_profile",), **empty)
        ids = [request.requested_executor]
    required_caps = set(profile.get("required_capabilities", ()) or ())
    required_caps.update(request.required_capabilities or ())
    required_roles = set(profile.get("required_roles", ()) or ())
    required_roles.update(request.required_roles or ())
    required_role = profile.get("required_role") or request.required_role
    required_permissions = set(profile.get("required_permissions", ()) or ())
    required_permissions.update(request.required_permissions or ())
    effective_grants, caller_ceiling, grant_evidence = _grant_values(grants)
    profile_ceiling = _strings(profile.get("permission_ceiling"))
    if caller_ceiling is not None:
        profile_ceiling = caller_ceiling if profile_ceiling is None else profile_ceiling & caller_ceiling
    data_classes = _strings(profile.get("allowed_data_classes"))
    allowed_modes = _strings(profile.get("execution_modes"))
    candidates: list[ExecutorDescriptor] = []
    routes: dict[str, Any] = {}
    reasons: list[str] = []
    hashes: dict[str, str] = {}
    for executor_id in ids:
        raw = descriptors.get(str(executor_id))
        if raw is None:
            reasons.append("executor_descriptor_unknown")
            continue
        try:
            descriptor = validate_executor_descriptor(raw)
        except (TypeError, ValueError):
            reasons.append("executor_descriptor_invalid")
            continue
        hashes[descriptor.executor_id] = _hash(descriptor.to_dict())
        if descriptor.enabled is not True:
            reasons.append("executor_disabled")
            continue
        if descriptor.mode != "automatic":
            reasons.append("executor_not_automatic")
            continue
        if descriptor.capabilities_available is None and required_caps:
            reasons.append("capabilities_unknown")
            continue
        if not required_caps.issubset(descriptor.capabilities_available or frozenset()):
            reasons.append("capability_missing")
            continue
        if required_role:
            required_roles.add(required_role)
        if required_roles and (descriptor.roles_available is None or not required_roles.issubset(descriptor.roles_available)):
            reasons.append("role_unknown_or_missing")
            continue
        class_required = (request.data_class is not None or data_classes is not None
                         or descriptor.allowed_data_classes is not None
                         or "provider:cloud" in (descriptor.permissions_required or frozenset())
                         or "provider:cloud" in (descriptor.permissions_consumed or frozenset()))
        if class_required:
            if request.data_class is None:
                reasons.append("privacy_class_unknown")
                continue
            if data_classes is not None and request.data_class not in data_classes:
                reasons.append("data_class_not_allowed")
                continue
            if descriptor.allowed_data_classes is None:
                reasons.append("privacy_class_unknown")
                continue
            if request.data_class not in descriptor.allowed_data_classes:
                reasons.append("data_class_not_allowed")
                continue
        if allowed_modes is not None and not (descriptor.execution_modes or frozenset()).intersection(allowed_modes):
            reasons.append("execution_mode_unsupported")
            continue
        consumed = descriptor.permissions_consumed
        required = set(required_permissions)
        required.update(descriptor.permissions_required or ())
        if consumed is None:
            if descriptor.permissions_required or required_permissions:
                reasons.append("consumed_permissions_unknown")
                continue
            consumed_set: set[str] = set()
        else:
            consumed_set = set(consumed)
        required.update(consumed_set)
        ceiling = profile_ceiling
        if descriptor.permission_ceiling is not None:
            ceiling = descriptor.permission_ceiling if ceiling is None else ceiling & descriptor.permission_ceiling
        if required:
            if descriptor.permissions_available is None:
                reasons.append("executor_permission_support_unknown")
                continue
            if not required.issubset(descriptor.permissions_available):
                reasons.append("executor_permission_unavailable")
                continue
            if effective_grants is None or ceiling is None:
                reasons.append("permission_grants_or_ceiling_unknown")
                continue
            if not required.issubset(effective_grants) or not required.issubset(ceiling):
                reasons.append("permissions_not_granted_and_allowed")
                continue
        if request.permission_scope is not None and not required.issubset(request.permission_scope):
            reasons.append("permission_scope_restricts_executor")
            continue
        route = routing_decision
        if routing_by_executor is not None and descriptor.executor_id in routing_by_executor:
            route = routing_by_executor[descriptor.executor_id]
        routed_model = getattr(route, "selected_model", None) if route else None
        target_model = routed_model or request.requested_model
        if target_model:
            evidence = descriptor.model_support
            if evidence.status is not SupportState.SUPPORTED:
                reasons.append("model_selection_unsupported_or_unknown")
                continue
            # The executor may list the catalog id or the provider's model name (what the app is given).
            names = {target_model}
            if routed_model and getattr(route, "planned_model", None):
                names.add(str(route.planned_model))
            if evidence.values is None or not names & evidence.values:
                reasons.append("planned_model_not_supported" if routed_model else "requested_model_not_supported")
                continue
        if request.requested_effort:
            evidence = descriptor.effort_support
            if evidence.status is not SupportState.SUPPORTED:
                reasons.append("effort_selection_unsupported_or_unknown")
                continue
            if evidence.values is None or request.requested_effort not in evidence.values:
                reasons.append("requested_effort_not_supported")
                continue
        if request.required_context_isolation:
            evidence = descriptor.context_isolation
            if (evidence.status is not SupportState.SUPPORTED or evidence.values is None
                    or request.required_context_isolation not in evidence.values):
                reasons.append("context_isolation_unsupported_or_unknown")
                continue
        if route is not None and getattr(route, "status", None) in {"blocked", "manual"}:
            reasons.append("model_routing_not_selectable")
            continue
        candidates.append(descriptor)
        routes[descriptor.executor_id] = route
    common = dict(
        request=request, profile_id=profile_id, profile_version=profile_version,
        requested_model=request.requested_model,
        planned_model=(getattr(routing_decision, "selected_model", None) if routing_decision else request.requested_model),
        effective_model=None, observed_model=None, requested_effort=request.requested_effort,
        requested_context_isolation=request.required_context_isolation,
        required_capabilities=tuple(sorted(required_caps)),
        required_roles=tuple(sorted(required_roles)),
        required_permissions=tuple(sorted(required_permissions)),
        granted_permissions=tuple(sorted(effective_grants)) if effective_grants is not None else None,
        permission_ceiling=tuple(sorted(profile_ceiling)) if profile_ceiling is not None else None,
        allowed_data_classes=tuple(sorted(data_classes)) if data_classes is not None else None,
        routing_decision=routing_decision, descriptor_hashes=hashes, config_hash=config_hash,
        evidence={"grants": dict(grant_evidence), "selection": "configuration_and_snapshot"},
    )
    if not candidates:
        return AssignmentDecision(status="manual", reason_codes=tuple(dict.fromkeys(reasons or ["no_eligible_executor"])),
                                 available_capabilities=None, **common)
    chosen = candidates[0]
    chosen_required = set(required_permissions)
    chosen_required.update(chosen.permissions_required or ())
    chosen_required.update(chosen.permissions_consumed or ())
    common["required_permissions"] = tuple(sorted(chosen_required))
    if routing_by_executor is not None and chosen.executor_id in routing_by_executor:
        chosen_route = routes[chosen.executor_id]
        common["routing_decision"] = chosen_route
        common["planned_model"] = getattr(chosen_route, "selected_model", None) if chosen_route else request.requested_model
    if chosen.permission_ceiling is not None:
        effective_ceiling = chosen.permission_ceiling if profile_ceiling is None else chosen.permission_ceiling & profile_ceiling
        common["permission_ceiling"] = tuple(sorted(effective_ceiling))
    if chosen.allowed_data_classes is not None:
        effective_classes = (chosen.allowed_data_classes if data_classes is None
                             else chosen.allowed_data_classes & data_classes)
        common["allowed_data_classes"] = tuple(sorted(effective_classes))
    return AssignmentDecision(

        status="selected", reason_codes=(), selected_executor=chosen.executor_id,
        planned_executor=chosen.executor_id, effective_executor=None, observed_executor=None,
        planned_effort=request.requested_effort,
        planned_context_isolation=request.required_context_isolation,
        available_capabilities=tuple(sorted(chosen.capabilities_available)) if chosen.capabilities_available is not None else None,
        consumed_permissions=tuple(sorted(chosen.permissions_consumed)) if chosen.permissions_consumed is not None else None,
        available_permissions=tuple(sorted(chosen.permissions_available)) if chosen.permissions_available is not None else None,
        **common,
    )
