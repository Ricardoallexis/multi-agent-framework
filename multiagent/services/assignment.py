# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Preview and accept task assignments without launching an executor."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..catalog import confirmed_level
from ..executors.registry import ExecutorRegistry
from ..executors.selection import mode_level_notice, model_exclusions, select_for_executor
from ..routing.assignment import AssignmentDecision, AssignmentRequest, select_assignment
from ..routing.executor_capabilities import VerifiedOperatorContext, validate_executor_config
from ..routing_policy import DEFAULT_STEP_KIND, STEP_KINDS, RoutingRequest
from ..tasks.envelopes import classification_envelope_sha256, envelope_sha256
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors

__all__ = ["AssignmentServices"]

_REQUEST_FIELDS = {
    "profile_id", "requested_executor", "requested_model", "requested_effort",
    "required_capabilities", "required_role", "required_roles", "required_permissions", "permission_scope",
    "data_class", "required_context_isolation", "additional_requirements", "constraints", "step_kind",
}
_TOKEN_VERSION = 1


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _error(code: str, message: str, *, status: int, **details: Any) -> ServiceError:
    return ServiceError(code, message, status=status, details=details)


def _set(value: Any) -> frozenset[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(str(item) for item in value)
    raise _error(INVALID_REQUEST, "Permission and capability scopes must be lists or null", status=422)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(v) for v in sorted(value, key=str)]
    if isinstance(value, (tuple, list)):
        return [_jsonable(v) for v in value]
    return value


class AssignmentServices:
    """Recompute assignment decisions from trusted task, catalog and registry snapshots."""

    def __init__(self, services: Any, *, registry: ExecutorRegistry | None = None,
                 adapters: Callable[[], Iterable[Any]] | None = None):
        """``adapters`` builds the executor adapters registered when the registry is loaded from the catalog (ORQ-T51)."""
        self._services = services
        self._catalog = None
        self._registry = registry
        self._registry_loaded = registry is not None
        self._injected = registry is not None
        self._adapters = adapters
        self._config_valid = True

    def _ensure_runtime_config(self) -> None:
        """Load Catalog-backed assignment data on first use, not facade construction."""
        if self._catalog is None:
            self._catalog = self._services.system.catalog
        if self._registry_loaded:
            return
        raw_config = getattr(self._catalog, "executor_routing", {})
        activations = getattr(self._services, "executor_activations", None)
        if activations is not None:     # EJE-T01: the executors the person turned on (a local file; the catalog is not changed)
            raw_config = activations.apply_to_config(raw_config)
        try:
            validated = validate_executor_config(raw_config)
            self._registry = ExecutorRegistry(validated, list(self._adapters() or ()) if self._adapters else ())
        except (TypeError, ValueError):
            self._config_valid = False
            self._registry = ExecutorRegistry({"version": 1, "profiles": {}, "executors": {},
                                               "requirement_map": {}, "operator_boundary": {}})
        self._registry_loaded = True

    def reset_registry(self) -> None:
        """Load the registry again on the next use (EJE-T01), so a change in the activations takes effect at once.

        A registry given to the constructor (tests) is not lost: only a registry built from the catalog is dropped.
        """
        if not self._injected:
            self._registry_loaded = False
            self._config_valid = True

    @property
    def registry(self) -> ExecutorRegistry:
        """The executor registry (adapters and descriptors); loaded from the catalog on first use."""
        self._ensure_runtime_config()
        return self._registry

    @property
    def _config(self) -> dict[str, Any]:
        self._ensure_runtime_config()
        config = getattr(self._catalog, "executor_routing", {})
        if not self._config_valid or not getattr(self._catalog, "executor_routing_valid", True):
            return {}
        return dict(config) if isinstance(config, Mapping) else {}

    @staticmethod
    def _request_input(request: AssignmentRequest | Mapping[str, Any], task_id: str) -> dict[str, Any]:
        if isinstance(request, AssignmentRequest):
            values = request.to_dict()
            if request.task_id != task_id:
                raise _error(INVALID_REQUEST, "Request task_id does not match the route", status=422,
                             task_id=request.task_id)
            request = {key: values.get(key) for key in _REQUEST_FIELDS if key in values}
        if not isinstance(request, Mapping):
            raise _error(INVALID_REQUEST, "request must be an AssignmentRequest or object", status=422)
        unknown = set(request) - _REQUEST_FIELDS
        if unknown:
            raise _error(INVALID_REQUEST, "Request contains fields outside the assignment contract", status=422,
                         fields=sorted(unknown))
        result = dict(request)
        for field in ("required_capabilities", "required_roles", "required_permissions", "permission_scope"):
            if field in result:
                result[field] = _set(result[field])
        step_kind = result.get("step_kind")
        if step_kind is not None and step_kind not in STEP_KINDS:
            raise _error(INVALID_REQUEST, f"step_kind must be one of {', '.join(STEP_KINDS)}", status=422,
                         field="step_kind")
        result.setdefault("additional_requirements", ())
        for field in ("additional_requirements",):
            value = result[field]
            if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
                raise _error(INVALID_REQUEST, f"{field} must be a list", status=422)
            result[field] = tuple(dict.fromkeys(str(item) for item in value))
        constraints = result.get("constraints", {})
        if not isinstance(constraints, Mapping):
            raise _error(INVALID_REQUEST, "constraints must be an object", status=422)
        result["constraints"] = dict(constraints)
        return result

    def _operator_policy(self) -> dict[str, Any]:
        boundary = self._config.get("operator_boundary", {})
        return dict(boundary) if isinstance(boundary, Mapping) else {}

    def _verified_alias(self, context: VerifiedOperatorContext | None) -> str | None:
        if not isinstance(context, VerifiedOperatorContext) or context.verified is not True:
            return None
        policy = self._operator_policy()
        aliases = policy.get("authorized_aliases", ()) or ()
        claim_alias = policy.get("claim_actor_alias")
        if (context.operator_id != policy.get("operator_id") or context.actor_alias not in aliases
                or not claim_alias or context.actor_alias != claim_alias):
            return None
        return context.actor_alias

    def _trusted_data_class(self, task: Mapping[str, Any], envelope: Mapping[str, Any],
                            context: VerifiedOperatorContext | None) -> tuple[str | None, str | None, bool]:
        """Resolve class from trusted config or a scoped verified context; reject conflicts."""
        configured = self._config.get("data_classifications", {})
        if not isinstance(configured, Mapping):
            configured = {}
        envelope_hash = classification_envelope_sha256(envelope)     # without ``data_class``: it is what this resolves
        scopes = (f"task:{task.get('id')}", f"group:{task.get('group_id')}",
                  f"envelope:{envelope_hash}")
        applicable = {scope: configured[scope] for scope in scopes
                      if isinstance(configured.get(scope), str) and configured.get(scope)}
        configured_values = set(applicable.values())
        if len(configured_values) > 1:
            return None, None, True
        configured_class = next(iter(configured_values)) if configured_values else None
        configured_scope = next((scope for scope in scopes if scope in applicable), None)

        context_class = None
        context_scope = None
        if isinstance(context, VerifiedOperatorContext) and context.verified is True:
            candidate_scope = getattr(context, "data_class_scope", None)
            candidate_class = getattr(context, "trusted_data_class", None)
            if candidate_scope in scopes and isinstance(candidate_class, str) and candidate_class:
                context_scope, context_class = candidate_scope, candidate_class
        if configured_class is not None and context_class is not None and configured_class != context_class:
            return None, None, True
        if configured_class is not None:
            return str(configured_class), str(configured_scope), False
        if context_class is not None:
            return str(context_class), str(context_scope), False
        return None, None, False

    def _grants(self, context: VerifiedOperatorContext | None, profile: Mapping[str, Any] | None,
                request: Mapping[str, Any]) -> dict[str, Any]:
        policy = self._operator_policy()
        alias = self._verified_alias(context)
        operator_grants = _set(policy.get("operator_grants", policy.get("grants")))
        alias_map = policy.get("alias_grants", {}) or {}
        alias_grants = _set(alias_map.get(alias)) if isinstance(alias_map, Mapping) and alias in alias_map else None
        caller_grants = (context.grants if alias is not None and isinstance(context, VerifiedOperatorContext)
                         else None)
        known = operator_grants is not None and alias_grants is not None and caller_grants is not None
        ceilings = [v for v in (profile.get("permission_ceiling") if isinstance(profile, Mapping) else None,
                                  request.get("permission_scope")) if v is not None]
        ceiling = set(ceilings[0]) if ceilings else None
        for value in ceilings[1:]:
            ceiling &= set(value)
        effective = (operator_grants & alias_grants & caller_grants) if known else None
        if effective is not None and ceiling is not None:
            effective &= ceiling
        return {"effective_permissions": sorted(effective) if effective is not None else None,
                "permission_ceiling": sorted(ceiling) if ceiling is not None else None,
                "provenance": {"operator": "trusted_config", "alias": "verified_context" if self._verified_alias(context) else "unknown"}}

    def _routing_decision(self, request: AssignmentRequest, profile: Mapping[str, Any] | None,
                          grants: Mapping[str, Any], step_kind: str = DEFAULT_STEP_KIND,
                          excluded_models: Mapping[str, str] | None = None):
        model_profile_id = profile.get("model_routing_profile_id") if isinstance(profile, Mapping) else None
        if not model_profile_id:
            return None
        model_request = RoutingRequest(
            profile_id=str(model_profile_id), requested_model=request.requested_model,
            step_kind=step_kind, excluded_models=excluded_models or None,
            required_capabilities=request.required_capabilities, required_role=request.required_role,
            required_permissions=request.required_permissions,
            effective_permissions=_set(grants.get("effective_permissions")),
            authorized_data_classes=_set(profile.get("allowed_data_classes")),
            authorization_provenance="assignment_trusted_grants" if grants.get("effective_permissions") is not None else "unknown",
            data_class=request.data_class,
        )
        return self._services.system.engine.router.select(model_request, **self.mode_options())

    def mode_options(self) -> dict[str, Any]:
        """The arguments that project the saved Eco or Maximum mode on the router; empty when none applies (PAR-T03).

        Only a mode the person saved counts, and only Eco and Maximum change levels, so without one the router is
        called exactly as before. The router applies the levels; the person's own model is never replaced by them.
        """
        gui, concurrency = getattr(self._services, "gui_settings", None), getattr(self._services, "concurrency", None)
        if gui is None or concurrency is None or "concurrency" not in gui.read():
            return {}
        options = concurrency.routing_options()
        return options if options.get("concurrency_mode") in ("eco", "maximum") else {}

    def mode_notices(self, model: str | None, step_kind: str | None = None) -> tuple[str, ...]:
        """The warning when the model the person named is outside the level of the active mode; the model is kept."""
        options = self.mode_options() if model else {}
        if not options:
            return ()
        models = getattr(self._services.system.catalog, "models", {}) or {}
        spec = models.get(model) or next((item for item in models.values() if getattr(item, "model", None) == model), None)
        level = getattr(spec, "level", None)
        declared = (options.get("confirmed_levels") or {}).get(getattr(spec, "id", None))
        if declared is not None and confirmed_level(declared) is not None:      # a level the person confirmed counts, as in the router
            level = confirmed_level(declared)
        notice = mode_level_notice(options["concurrency_mode"], level,
                                   step_kind=step_kind or DEFAULT_STEP_KIND, allow_t4=bool(options.get("allow_t4")))
        return (notice,) if notice else ()

    @staticmethod
    def _profile_executor_ids(profile: Mapping[str, Any] | None, request: AssignmentRequest) -> list[str]:
        ids = profile.get("executor_ids") if isinstance(profile, Mapping) else None
        if not isinstance(ids, (list, tuple)):
            return []
        names = [str(item) for item in ids]
        return [request.requested_executor] if request.requested_executor in names else names

    def _routing_by_executor(self, request: AssignmentRequest, profile: Mapping[str, Any] | None,
                             grants: Mapping[str, Any], step_kind: str, global_route: Any,
                             descriptors: Mapping[str, Any]) -> dict[str, Any] | None:
        """The model routing decision for each executor of the profile, with the models it cannot receive left out.

        The router ranks and applies the profile as usual (level bounds, price evidence, privacy); the only change is
        that the candidates this executor cannot take are ineligible with a reason, so each executor is offered the
        best model it can receive (ECO-T08). None when the profile has no model routing profile.
        """
        if global_route is None:
            return None
        models = getattr(self._services.system.catalog, "models", {}) or {}
        routes: dict[str, Any] = {}
        for executor_id in self._profile_executor_ids(profile, request):
            descriptor = descriptors.get(executor_id)
            if descriptor is None:
                continue
            excluded = model_exclusions(models, descriptor, self._registry.resolve(executor_id))
            routes[executor_id] = (self._routing_decision(request, profile, grants, step_kind, excluded)
                                   if excluded else global_route)
        return routes

    def _executor_requests(self, request: AssignmentRequest, profile: Mapping[str, Any] | None,
                           descriptors: Mapping[str, Any], routes: Mapping[str, Any] | None,
                           selected: str | None, step_kind: str | None = None) -> dict[str, Any]:
        """Model and effort each executor of the profile would be asked to use, with where each value comes from."""
        requests: dict[str, Any] = {}
        notices = self.mode_notices(request.requested_model, step_kind)
        for executor_id in self._profile_executor_ids(profile, request):
            descriptor = descriptors.get(executor_id)
            if descriptor is None:
                continue
            selection = select_for_executor(
                executor_id, descriptor, (routes or {}).get(executor_id), requested_model=request.requested_model,
                requested_effort=request.requested_effort, adapter=self._registry.resolve(executor_id),
                user_model_notices=notices)
            entry = selection.to_dict()
            entry.pop("executor_id")
            requests[executor_id] = {**entry, "selected": executor_id == selected}
        return requests

    def _build(self, task_id: str, external: Mapping[str, Any],
               context: VerifiedOperatorContext | None) -> tuple[AssignmentDecision, dict[str, Any], dict[str, Any]]:
        tasks = self._services.tasks
        try:
            task = tasks.get_task(task_id)
            envelope = self._services.envelopes.get_envelope(task_id)
        except ServiceError as exc:
            if exc.code == NOT_FOUND:
                raise
            raise
        if envelope.get("unchanged"):
            raise _error(INVALID_STATE, "Could not obtain a complete task envelope", status=409)
        ready = tasks.ready_tasks(group_id=task["group_id"])
        if task.get("status") != "ready" or not any(item.get("id") == task_id for item in ready):
            # Preserve enough identity to return a stable manual/blocked result.
            request = AssignmentRequest(task_id=task_id, profile_id=external.get("profile_id"),
                                        attempt=task.get("attempt"), revision=task.get("revision"),
                                        envelope_hash=envelope_sha256(envelope), group_id=task.get("group_id"),
                                        task_status=task.get("status"), preferred=task.get("preferred"),
                                        file_scope=tuple(task.get("file_scope", ())),
                                        canonical_requirements=tuple(task.get("requirements", ())))
            decision = select_assignment(request, profile=None, descriptors={}, routing_decision=None, grants=None)
            decision = replace(decision, status="blocked", reason_codes=("task_not_ready_or_group_unauthorized",))
            return decision, task, {"envelope": envelope, "snapshot": {}, "request": request.to_dict()}

        config = self._config
        profiles = config.get("profiles", {}) if isinstance(config.get("profiles", {}), Mapping) else {}
        profile_id = external.get("profile_id")
        profile = profiles.get(profile_id) if profile_id is not None else None
        requirement_map = config.get("requirement_map", {})
        requirement_map = requirement_map if isinstance(requirement_map, Mapping) else {}
        canonical = tuple(task.get("requirements", ()))
        additional = tuple(external.get("additional_requirements", ()))
        required_caps = set(external.get("required_capabilities") or ())
        required_permissions = set(external.get("required_permissions") or ())
        required_roles = set(external.get("required_roles") or ())
        required_role = external.get("required_role")
        mapped_data_classes: set[str] = set()
        unknown_requirements = []
        for label in dict.fromkeys((*canonical, *additional)):
            mapped = requirement_map.get(label)
            if not isinstance(mapped, Mapping):
                unknown_requirements.append(label)
                continue
            required_caps.update(str(v) for v in mapped.get("capabilities", ()) or ())
            required_permissions.update(str(v) for v in mapped.get("permissions", ()) or ())
            required_roles.update(str(v) for v in mapped.get("roles", ()) or ())
            if mapped.get("data_class"):
                mapped_data_classes.add(str(mapped["data_class"]))
            required_data_classes = mapped.get("data_classes", ()) or ()
            if isinstance(required_data_classes, str):
                mapped_data_classes.add(required_data_classes)
            else:
                mapped_data_classes.update(str(v) for v in required_data_classes)
            if required_role is None and mapped.get("role"):
                required_role = str(mapped["role"])
        conflicting_data_classes = len(mapped_data_classes) > 1
        trusted_data_class, trusted_data_class_scope, privacy_class_conflict = self._trusted_data_class(
            task, envelope, context
        )
        request = AssignmentRequest(
            task_id=task_id, profile_id=profile_id, attempt=task.get("attempt"), revision=task.get("revision"),
            envelope_hash=envelope_sha256(envelope), group_id=task.get("group_id"),
            task_status=task.get("status"), preferred=task.get("preferred"),
            file_scope=tuple(task.get("file_scope", ())), canonical_requirements=canonical,
            additional_requirements=additional, required_capabilities=frozenset(required_caps),
            required_role=required_role, required_roles=frozenset(required_roles),
            required_permissions=frozenset(required_permissions),
            permission_scope=external.get("permission_scope"),
            data_class=trusted_data_class,
            requested_executor=external.get("requested_executor"), requested_model=external.get("requested_model"),
            requested_effort=external.get("requested_effort"),
            required_context_isolation=external.get("required_context_isolation"),
            constraints={"envelope_constraints": list(envelope.get("constraints", [])),
                         "request_constraints": dict(external.get("constraints", {}))},
            provenance={"canonical_requirements": "task", "additional_requirements": "request_restrictive",
                        "envelope": "orq3", "data_class_scope": trusted_data_class_scope,
                        "data_class_provenance": "trusted_scoped_classification"
                        if trusted_data_class is not None else "unknown"},
        )
        grants = self._grants(context, profile, external)
        descriptors = self._registry.snapshot()
        step_kind = external.get("step_kind") or DEFAULT_STEP_KIND
        routing = self._routing_decision(request, profile, grants, step_kind)
        routes = self._routing_by_executor(request, profile, grants, step_kind, routing, descriptors)
        decision = select_assignment(request, profile=profile if isinstance(profile, Mapping) else None,
                                     descriptors=descriptors, routing_decision=routing, grants=grants,
                                     routing_by_executor=routes)
        if not self._config_valid:
            decision = replace(decision, status="blocked",
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes, "executor_routing_config_invalid"))))
        if unknown_requirements:
            decision = replace(decision, status="blocked",
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes, "requirement_mapping_unknown"))),
                               evidence={**dict(decision.evidence), "unknown_requirements": unknown_requirements})
        if conflicting_data_classes:
            decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes,
                                                                 "requirement_data_class_conflict"))))
        if external.get("requested_executor") and decision.selected_executor != external.get("requested_executor"):
            decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes, "requested_executor_not_selected"))))
        if request.constraints.get("envelope_constraints"):
            # Canonical envelope constraints are authoritative, but no trusted exact
            # constraint mapping exists yet; fail closed rather than silently ignoring them.
            decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes,
                                                                 "canonical_constraints_unmapped"))))
        if external.get("constraints"):
            # Arbitrary body constraints have no typed narrowing semantics yet.
            # Keep canonical envelope constraints intact and fail closed on unknown additions.
            decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes,
                                                                 "request_constraints_unknown"))))
        if privacy_class_conflict:
            decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes,
                                                                 "privacy_class_conflict"))))
        if mapped_data_classes and (trusted_data_class is None or mapped_data_classes != {trusted_data_class}):
            decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes,
                                                                 "privacy_class_unknown" if trusted_data_class is None
                                                                 else "data_class_mismatch"))))
        if external.get("data_class") is not None:
            # The request can narrow by exact equality, but cannot classify the task.
            if privacy_class_conflict:
                reason = "privacy_class_conflict"
            elif trusted_data_class is None:
                reason = "privacy_class_unknown"
            elif external.get("data_class") != trusted_data_class:
                reason = "data_class_mismatch"
            else:
                reason = None
            if reason:
                decision = replace(decision, status="blocked", selected_executor=None, planned_executor=None,
                                   reason_codes=tuple(dict.fromkeys((*decision.reason_codes, reason))))
        executor_requests = self._executor_requests(request, profile if isinstance(profile, Mapping) else None,
                                                    descriptors, routes, decision.selected_executor, step_kind)
        if executor_requests:
            # What each executor would be asked to run with (ECO-T08): the same function the executor service uses.
            decision = replace(decision, evidence={**dict(decision.evidence), "step_kind": step_kind,
                                                   "executor_requests": executor_requests})
        snapshot = {
            "config_hash": _digest(config),
            "config_version": config.get("version"),
            "descriptors": {key: _jsonable(value) for key, value in sorted(descriptors.items())},
            "descriptor_hashes": dict(decision.descriptor_hashes),
            "adapter_versions": {key: getattr(value, "adapter_version", None)
                                 for key, value in sorted(descriptors.items())},
        }
        return decision, task, {"envelope": envelope, "snapshot": snapshot, "request": request.to_dict()}

    @staticmethod
    def _encode_token(payload: Mapping[str, Any]) -> str:
        digest = _digest(payload)
        wrapped = {"version": _TOKEN_VERSION, "payload": payload, "digest": digest}
        return base64.urlsafe_b64encode(_canonical(wrapped)).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_token(token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 100_000:
            raise _error(INVALID_REQUEST, "preview_token is invalid", status=422)
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            wrapped = json.loads(raw)
            payload = wrapped["payload"]
            if wrapped.get("version") != _TOKEN_VERSION or wrapped.get("digest") != _digest(payload):
                raise ValueError("digest mismatch")
            return dict(payload)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise _error(INVALID_REQUEST, "preview_token is invalid or corrupted", status=422) from None

    @service_errors
    def preview(self, task_id: str, request: AssignmentRequest | Mapping[str, Any],
                operator_context: VerifiedOperatorContext | None = None) -> dict[str, Any]:
        """Compute a side-effect-free assignment decision and canonical digest token."""
        external = self._request_input(request, task_id)
        decision, task, context = self._build(task_id, external, operator_context)
        alias = self._verified_alias(operator_context)
        if alias is None:
            decision = replace(decision, status="manual", selected_executor=None, planned_executor=None,
                               reason_codes=tuple(dict.fromkeys((*decision.reason_codes,
                                                                 "operator_context_unverified"))))
        payload = {"task_id": task_id, "attempt": task.get("attempt"), "revision": task.get("revision"),
                   "envelope_hash": envelope_sha256(context["envelope"]), "request": context["request"],
                   "input": _jsonable(external), "decision": decision.to_dict(), **context["snapshot"],
                   "operator_id": operator_context.operator_id if alias is not None else None,
                   "actor_alias": alias}
        return {"decision": decision.to_dict(), "decision_id": _digest(payload),
                "preview_token": self._encode_token(payload)}

    @staticmethod
    def _unverified_manual_decision(reason: str) -> dict[str, Any]:
        # A preview token is a public digest, not a signature. Never echo its
        # decision payload before recomputing it under trusted request context.
        return {"status": "manual", "reason_codes": [reason]}

    @service_errors
    def accept(self, task_id: str, preview_token: str, acknowledge: bool,
               operator_context: VerifiedOperatorContext | None = None) -> dict[str, Any]:
        """Recompute a preview and CAS claim only for a verified, policy-bound operator."""
        payload = self._decode_token(preview_token)
        if payload.get("task_id") != task_id:
            raise _error(INVALID_STATE, "Preview token belongs to another task", status=409, task_id=task_id)
        if acknowledge is not True:
            return {"decision": self._unverified_manual_decision("acknowledgement_required"),
                    "decision_id": _digest(payload), "task": None}
        alias = self._verified_alias(operator_context)
        if alias is None:
            return {"decision": self._unverified_manual_decision("operator_context_unverified"),
                    "decision_id": _digest(payload), "task": None}
        original_input = payload.get("input")
        if not isinstance(original_input, Mapping):
            raise _error(INVALID_REQUEST, "Preview token request is invalid", status=422)
        # The token carries the original request separately from canonical task facts.
        external = {key: original_input.get(key) for key in _REQUEST_FIELDS if key in original_input}
        decision, task, context = self._build(task_id, external, operator_context)
        refreshed = {"task_id": task_id, "attempt": task.get("attempt"), "revision": task.get("revision"),
                     "envelope_hash": envelope_sha256(context["envelope"]), "request": context["request"],
                     "decision": decision.to_dict(), **context["snapshot"],
                     "operator_id": self._operator_policy().get("operator_id"),
                     "actor_alias": self._operator_policy().get("claim_actor_alias")}
        # A preview created without request-scoped identity cannot authorize a claim.
        old_decision = dict(payload.get("decision", {}))
        new_decision = dict(refreshed["decision"])
        # Request-scoped grants are known only at accept time. Compare the complete
        # plan and eligibility decision while allowing those grant evidence fields
        # to become more precise for a verified caller.
        for value in (old_decision, new_decision):
            value.pop("granted_permissions", None)
            evidence = dict(value.get("evidence", {}))
            evidence.pop("grants", None)
            value["evidence"] = evidence
        if (payload.get("attempt") != refreshed["attempt"] or payload.get("revision") != refreshed["revision"]
                or payload.get("envelope_hash") != refreshed["envelope_hash"]
                or payload.get("request") != refreshed["request"]
                or payload.get("config_hash") != refreshed["config_hash"]
                or payload.get("config_version") != refreshed["config_version"]
                or payload.get("descriptors") != refreshed["descriptors"]
                or payload.get("adapter_versions") != refreshed["adapter_versions"]
                or payload.get("descriptor_hashes") != refreshed["descriptor_hashes"]
                or payload.get("operator_id") != refreshed["operator_id"]
                or payload.get("actor_alias") != refreshed["actor_alias"]
                or old_decision != new_decision):
            raise _error(INVALID_STATE, "Preview is stale; create a new preview", status=409,
                         reason="assignment_preview_stale")
        if alias is None:
            unverified = decision.to_dict()
            unverified.update(status="manual", selected_executor=None, planned_executor=None,
                              reason_codes=list(dict.fromkeys([*unverified.get("reason_codes", []),
                                                               "operator_context_unverified"])))
            return {"decision": unverified, "decision_id": _digest(payload), "task": None}
        if decision.status != "selected" or not decision.selected_executor:
            return {"decision": decision.to_dict(), "decision_id": _digest(payload), "task": None}
        claim_alias = self._operator_policy().get("claim_actor_alias", alias)
        if alias != claim_alias:
            return {"decision": {**decision.to_dict(), "status": "manual",
                                  "reason_codes": ["operator_context_unverified"]}, "task": None}
        # TaskServices ignores same-actor overlaps, so assignment checks them before claiming.
        scope = task.get("file_scope", [])
        from .tasks import _overlaps
        for other in self._services.tasks.list_tasks():
            if other.get("id") == task_id or other.get("assignee") != alias:
                continue
            if other.get("state") not in {"claimed", "running", "paused", "validating", "needs_changes"}:
                continue
            overlaps = [path for path in other.get("file_scope", [])
                        if any(_overlaps(path, mine) for mine in scope)]
            if overlaps:
                raise _error(INVALID_STATE, "Files overlap another task assigned to this alias", status=409,
                             task_id=other["id"], conflicts=overlaps)
        claimed = self._services.tasks.transition(
            task_id, "claim", actor=alias, cause=f"assignment:{_digest(payload)}",
            expected_revision=task["revision"],
        )
        return {"decision": decision.to_dict(), "decision_id": _digest(payload), "task": claimed}
