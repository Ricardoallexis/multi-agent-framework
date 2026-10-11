"""Public contracts and run-scoped evidence helpers for model routing.

This module contains no provider calls and performs no persistence. Callers own
health snapshots, permission grants, and event/usage snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from datetime import datetime, timezone
from typing import Any, Mapping


CONTRACT_VERSION = 1

# Generic step kinds and reasoning-effort levels shared by workflow definitions,
# routing requests and provider adapters. They carry no domain meaning.
STEP_KINDS = ("orchestration", "planning", "review", "work")
DEFAULT_STEP_KIND = "work"
EFFORT_LEVELS = ("none", "low", "medium", "high", "max")


def _strings(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(str(item) for item in value)


def _metric(values: list[Any]) -> dict[str, Any]:
    present = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    return {
        "total": sum(present) if present and len(present) == len(values) else None,
        "known_subtotal": sum(present) if present else None,
        "known_count": len(present),
        "unknown_count": len(values) - len(present),
    }


@dataclass(frozen=True)
class EvidenceValue:
    """A nullable observation with enough provenance to decide whether to trust it."""

    value: Any = None
    unit: str | None = None
    source: str | None = None
    provenance: str = "unknown"
    observed_at: str | None = None
    scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "provenance": self.provenance,
            "observed_at": self.observed_at,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class RoutingRequest:
    """Explicit routing constraints; grants are distinct from required permissions."""

    profile_id: str | None = None
    workflow_id: str | None = None
    step_id: str | None = None
    requested_model: str | None = None
    required_capabilities: frozenset[str] | None = frozenset()
    required_role: str | None = None
    required_permissions: frozenset[str] | None = frozenset()
    effective_permissions: frozenset[str] | None = None
    authorized_data_classes: frozenset[str] | None = None
    authorization_provenance: str | None = None
    data_class: str | None = None
    execution_mode: str = "AUTO"
    requires_web: bool = False
    output_schema: str | None = None
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    # Step kind (one of ``STEP_KINDS``), requested reasoning effort (one of
    # ``EFFORT_LEVELS`` or None for the provider default) and the 1-based attempt
    # of the step. The defaults describe a plain work step on its first attempt.
    step_kind: str = DEFAULT_STEP_KIND
    effort: str | None = None
    attempt: int = 1

    def __post_init__(self) -> None:
        for name in ("required_capabilities", "required_permissions", "effective_permissions", "authorized_data_classes"):
            object.__setattr__(self, name, _strings(getattr(self, name)))
        if self.step_kind not in STEP_KINDS:
            raise ValueError(f"step_kind must be one of {', '.join(STEP_KINDS)}; got {self.step_kind!r}")
        if self.effort is not None and self.effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)} or None; got {self.effort!r}")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError(f"attempt must be an integer of 1 or more; got {self.attempt!r}")

    def to_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for name in ("required_capabilities", "required_permissions", "effective_permissions", "authorized_data_classes"):
            value = result[name]
            result[name] = sorted(value) if value is not None else None
        return result


@dataclass(frozen=True)
class RoutingCandidate:
    """Candidate declaration. Available permissions do not constitute caller grants."""

    model_id: str
    provider: str | None
    planned_model: str | None
    tier: str | None
    capabilities: frozenset[str] | None = None
    roles: frozenset[str] | None = None
    permissions_available: frozenset[str] | None = None
    permissions_required: frozenset[str] | None = None
    allowed_data_classes: frozenset[str] | None = None
    pricing: Mapping[str, Any] | None = None
    eligible: bool = False
    reason_codes: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    # Catalog level (T1..T4) and classification (``classified`` / ``unclassified``) as declared;
    # a rejected candidate keeps them next to its ``reason_codes`` as evidence of why it was left out.
    level: str | None = None
    classification: str | None = None

    def __post_init__(self) -> None:
        for name in ("capabilities", "roles", "permissions_available", "permissions_required", "allowed_data_classes"):
            object.__setattr__(self, name, _strings(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for name in ("capabilities", "roles", "permissions_available", "permissions_required", "allowed_data_classes"):
            value = result[name]
            result[name] = sorted(value) if value is not None else None
        result["reason_codes"] = list(self.reason_codes)
        return result


@dataclass(frozen=True)
class RoutingEvidence:
    """Caller-supplied evidence snapshot. No implicit probes or cross-run reads."""

    scope: str | None = None
    provider_health: Mapping[str, EvidenceValue | Mapping[str, Any]] = field(default_factory=dict)
    stats_by_model: Mapping[str, Any] = field(default_factory=dict)
    provenance: str = "unknown"
    observed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        health = {
            key: value.to_dict() if isinstance(value, EvidenceValue) else dict(value)
            for key, value in self.provider_health.items()
        }
        return {
            "scope": self.scope,
            "provider_health": health,
            "stats_by_model": dict(self.stats_by_model),
            "provenance": self.provenance,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class RoutingDecision:
    """Versioned selector result consumed by workflow runtime and future T15 assignment."""

    status: str
    requested_model: str | None
    selected_model: str | None = None
    planned_model: str | None = None
    fallback_models: tuple[str, ...] = ()
    effective_config_model: str | None = None
    effective_model: str | None = None
    observed_model: str | None = None
    profile_id: str | None = None
    profile_version: str | int | None = None
    required_capabilities: tuple[str, ...] = ()
    required_role: str | None = None
    required_permissions: tuple[str, ...] = ()
    effective_permissions: tuple[str, ...] | None = None
    allowed_permissions: tuple[str, ...] | None = None
    data_class: str | None = None
    authorized_data_classes: tuple[str, ...] | None = None
    applied_capabilities: tuple[str, ...] | None = None
    applied_roles: tuple[str, ...] | None = None
    applied_permissions: tuple[str, ...] | None = None
    reason_codes: tuple[str, ...] = ()
    candidates: tuple[RoutingCandidate, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    budget_scope: Mapping[str, Any] = field(default_factory=dict)
    manual_required: bool = False
    version: int = CONTRACT_VERSION
    # Economic-profile evidence (ECO-T05). ``step_kind`` is the kind the decision was made for and
    # ``profile_mode`` the profile's ``mode`` (None for a profile that declares none). ``selected_level``
    # is the catalog level of the chosen model and ``price_evidence`` the price it was chosen on
    # (source, validity window, whether it was current, and the estimated cost when it could be computed).
    # ``effort`` is the effort the step should run with: the step's own, else the profile's per-kind
    # one, with ``effort_source`` saying which ("step" or "profile"). Candidates left out are the
    # ``candidates`` with ``eligible`` false and their ``reason_codes``.
    step_kind: str | None = None
    profile_mode: str | None = None
    selected_level: str | None = None
    price_evidence: Mapping[str, Any] | None = None
    effort: str | None = None
    effort_source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for name in (
            "required_capabilities", "required_permissions", "effective_permissions", "allowed_permissions",
            "authorized_data_classes", "applied_capabilities", "applied_roles", "applied_permissions",
            "reason_codes", "fallback_models",
        ):
            value = result[name]
            result[name] = list(value) if value is not None else None
        result["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        result["provenance"] = dict(self.provenance)
        result["budget_scope"] = dict(self.budget_scope)
        if self.price_evidence is not None:
            result["price_evidence"] = json.loads(json.dumps(self.price_evidence, default=str))
        return result


def _group_attempts(events: list[Mapping[str, Any]], field_name: str) -> tuple[dict[str, Any], int]:
    groups: dict[str, dict[str, Any]] = {}
    unknown = 0
    for event in events:
        payload = event.get("payload", event)
        if not isinstance(payload, Mapping) or event.get("event_type", "model_attempt") != "model_attempt":
            continue
        model_id = payload.get(field_name)
        invoked = payload.get("provider_invoked") is True
        if not model_id:
            if invoked:
                unknown += 1
            continue
        group = groups.setdefault(str(model_id), {
            "attempts": 0, "provider_invocations": 0, "successes": 0, "failures": 0,
            "preflight_rejections": 0, "duration_ms": None, "duration_ms_known_subtotal": None,
            "duration_observations": 0, "duration_unknown_count": 0,
            "provenance": "run_event_model_attempt",
        })
        group["attempts"] += 1
        if invoked:
            group["provider_invocations"] += 1
            group["successes"] += int(payload.get("status") == "success" or payload.get("ok") is True)
            group["failures"] += int(payload.get("status") == "failed" or payload.get("ok") is False)
            duration = payload.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                group["duration_ms_known_subtotal"] = (group["duration_ms_known_subtotal"] or 0) + duration
                group["duration_observations"] += 1
            else:
                group["duration_unknown_count"] += 1
        else:
            group["preflight_rejections"] += 1
    for group in groups.values():
        if group["duration_unknown_count"] == 0 and group["duration_observations"]:
            group["duration_ms"] = group["duration_ms_known_subtotal"]
    return groups, unknown


def summarize_routing_evidence(run_id: str, events: list[Mapping[str, Any]],
                               usage_records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize only the supplied run snapshot; events count calls, rows count reported usage."""
    attempts = [event for event in events if event.get("event_type") == "model_attempt"]
    by_requested, unknown_requested = _group_attempts(attempts, "requested_model")
    by_routing_requested, unknown_routing_requested = _group_attempts(attempts, "routing_requested_model")
    by_effective, unknown_effective = _group_attempts(attempts, "effective_model")
    by_observed, unknown_observed = _group_attempts(attempts, "observed_model")
    usage: dict[str, list[Mapping[str, Any]]] = {}
    for record in usage_records:
        name = record.get("model")
        if name:
            usage.setdefault(str(name), []).append(record)
    by_usage_model: dict[str, Any] = {}
    for name, records in usage.items():
        metrics = {
            key: _metric([row.get(key) for row in records])
            for key in ("tokens_in", "tokens_out", "duration_ms", "estimated_cost_usd")
        }
        by_usage_model[name] = {
            "records": len(records),
            "failed": sum(not bool(row.get("success")) for row in records),
            **metrics,
            "provenance": "usage_record.model",
        }
    return {
        "version": CONTRACT_VERSION,
        "scope": {"run_id": run_id, "source": "single_run_snapshot"},
        "by_requested_model": by_requested,
        "by_routing_requested_model": by_routing_requested,
        "by_effective_model": by_effective,
        "by_observed_model": by_observed,
        "by_usage_model": by_usage_model,
        "unknown_model_attempts": {
            "requested": unknown_requested,
            "routing_requested": unknown_routing_requested,
            "effective": unknown_effective,
            "observed": unknown_observed,
        },
        "provenance": {
            "attempt_counts": "run_events.model_attempt",
            "usage_totals": "telemetry_records",
            "cross_run_history": "unknown_not_loaded",
        },
    }
