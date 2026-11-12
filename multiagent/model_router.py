from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import inspect
import logging
import math
import time
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from .adapters.base import EffortTranslation, LLMAdapter, LLMResponse, ignored_effort
from .call_guards import CallContext
from .catalog import (
    MODEL_LEVELS, Catalog, CatalogError, ModelSpec, current_price, validate_routing_profile,
)
from .contracts import TaskRequirements
from .routing_policy import RoutingCandidate, RoutingDecision, RoutingEvidence, RoutingRequest
from .schema_utils import estimate_tokens_conservative
from .errors import (
    AuthenticationError,
    ContextBudgetError,
    ModelUnavailable,
    ProviderRequestError,
    ProviderUnavailable,
    QuotaExceeded,
    RateLimitExceeded,
    StructuredOutputError,
)


@dataclass(frozen=True)
class ModelBinding:
    preferred_model: str
    fallback_model: str | None = None
    max_retries: int = 0
    fallback_max_retries: int = 0


ResponseValidator = Callable[[BaseModel, LLMResponse], None]
AttemptCallback = Callable[[dict[str, Any]], None]
_logger = logging.getLogger(__name__)
_PRICE_EVIDENCE_FIELDS = ("amount", "denominator", "unit", "currency", "source", "valid_from", "valid_until", "provenance")
# Reasons a level-bounded profile can reject a candidate for; the top-level blocked reason lists those seen.
_LEVEL_REASON_CODES = frozenset({
    "model_unclassified", "level_below_minimum", "level_above_maximum", "price_evidence_missing_or_expired",
})
_ROUTABLE_ERRORS = (
    ProviderUnavailable,
    ProviderRequestError,
    AuthenticationError,
    ModelUnavailable,
    QuotaExceeded,
    RateLimitExceeded,
    ContextBudgetError,
    StructuredOutputError,
)


# Why a step failed on the quality of its output; escalation (ECO-T06) records it as the reason.
STRUCTURED_OUTPUT_FAILED = "structured_output_failed"
SEMANTIC_VALIDATION_FAILED = "semantic_validation_failed"


def _semantic_failure(exc: Exception) -> StructuredOutputError:
    """The error for an output that parsed but failed the step's own validation (same type as before)."""
    error = StructuredOutputError(f"Semantically invalid output: {exc}")
    error.failure_kind = SEMANTIC_VALIDATION_FAILED
    return error


def failure_kind(error: BaseException) -> str:
    """``semantic_validation_failed`` or ``structured_output_failed`` for a ``StructuredOutputError``."""
    return getattr(error, "failure_kind", STRUCTURED_OUTPUT_FAILED)


class ModelRouter:
    """Route a step through one explicitly bound primary model and at most one fallback."""

    def __init__(self, catalog: Catalog, adapters: dict[str, LLMAdapter], dry_run: bool = False):
        self.catalog = catalog
        self.adapters = adapters
        self.dry_run = dry_run

    @staticmethod
    def _as_set(value):
        if value is None:
            return None
        if isinstance(value, str):
            return {value}
        return set(value)

    @staticmethod
    def _current_price(pricing, *, now):
        # One shared check (catalog.current_price) so selection and the usage cost estimate agree.
        return current_price(pricing, now=now)

    @classmethod
    def _price_evidence(cls, spec: ModelSpec, *, now, request: RoutingRequest, ranking: str) -> dict[str, Any] | None:
        """The price a model was chosen on: declared rates with source and validity, and whether they were current.

        ``estimated_cost`` is set only when the request carried both token estimates and the price is
        current; an unknown cost stays ``None`` and is never read as 0. ``ranking`` says how the model
        was compared with the other eligible ones (``estimated_tokens``, ``dominant_rates``,
        ``single_candidate``) or ``not_ranked`` when no price comparison was made.
        """
        pricing = getattr(spec, "pricing", None)
        if not isinstance(pricing, dict):
            return None
        evidence: dict[str, Any] = {"model_id": spec.id, "level": spec.level, "ranking": ranking}
        for component in ("input", "output"):
            price = pricing.get(component)
            evidence[component] = ({field: price.get(field) for field in _PRICE_EVIDENCE_FIELDS}
                                   if isinstance(price, dict) else None)
        current = cls._current_price(pricing, now=now)
        evidence["current"] = current is not None
        cost = None
        if (current is not None and request.estimated_input_tokens is not None
                and request.estimated_output_tokens is not None):
            cost = (current[0][0] * request.estimated_input_tokens / current[0][2]
                    + current[1][0] * request.estimated_output_tokens / current[1][2])
        evidence["estimated_cost"] = cost
        evidence["estimated_cost_currency"] = current[0][1] if current is not None and cost is not None else None
        return evidence

    def select(self, request: RoutingRequest, evidence: RoutingEvidence | None = None, *,
               profile_override: dict[str, Any] | None = None) -> RoutingDecision:
        """Resolve an explicitly requested, enabled profile without side effects or provider calls."""
        evidence = evidence or RoutingEvidence()
        profile_id = request.profile_id
        base = dict(
            requested_model=request.requested_model, profile_id=profile_id,
            required_capabilities=tuple(sorted(request.required_capabilities or ())),
            required_role=request.required_role,
            required_permissions=tuple(sorted(request.required_permissions or ())),
            effective_permissions=tuple(sorted(request.effective_permissions)) if request.effective_permissions is not None else None,
            data_class=request.data_class,
            authorized_data_classes=tuple(sorted(request.authorized_data_classes)) if request.authorized_data_classes is not None else None,
            provenance={"request": request.authorization_provenance or "unknown", "evidence_scope": evidence.scope},
            manual_required=True,
            step_kind=request.step_kind,
            effort=request.effort,
            effort_source="step" if request.effort is not None else None,
            escalation=dict(request.escalation) if request.escalation is not None else None,
        )
        if profile_id is None:
            return RoutingDecision(status="manual", reason_codes=("no_profile_requested",), **base)
        profile = profile_override if profile_override is not None else getattr(self.catalog, "routing_profiles", {}).get(profile_id)
        if not isinstance(profile, dict):
            return RoutingDecision(status="blocked", reason_codes=("unknown_profile",), **base)
        base["profile_version"] = profile.get("version")
        strict_budgets = profile.get("strict_budgets", [])
        if not isinstance(strict_budgets, (list, tuple, set, frozenset)):
            return RoutingDecision(status="blocked", reason_codes=("strict_budget_declaration_invalid",), **base)
        strict_budgets = {str(value) for value in strict_budgets}
        for field_name, budget_name in (("max_cost", "cost"), ("max_input_tokens", "input_tokens"),
                                        ("max_output_tokens", "output_tokens"), ("max_total_tokens", "total_tokens")):
            if field_name in profile:
                strict_budgets.add(budget_name)
        unsupported_strict = strict_budgets - {"provider_invocations"}
        budget_scope = {
            "run_id": evidence.scope,
            "profile_id": profile_id,
            "hard_pre_dispatch": {
                "provider_invocations": "profile.max_provider_invocations",
                "global_provider_invocations": "run.max_llm_calls",
            },
            "planning_estimates": {
                "input_tokens": profile.get("planning_max_input_tokens"),
                "output_tokens": profile.get("planning_max_output_tokens"),
                "cost": profile.get("planning_max_cost"),
            },
            "strict_requested": sorted(strict_budgets),
            "strict_supported": ["provider_invocations"],
            "usage_after_dispatch_unknown_is_not_zero": True,
        }
        base["budget_scope"] = budget_scope
        if unsupported_strict:
            return RoutingDecision(status="blocked", reason_codes=("strict_budget_enforcement_unsupported",), **base)
        if profile.get("enabled") is not True:
            return RoutingDecision(status="blocked", reason_codes=("profile_disabled",), **base)
        mode = str(request.execution_mode).casefold()
        if mode in {"human_guided", "supervised"}:
            return RoutingDecision(status="manual", reason_codes=("human_guided_requested",), **base)

        def as_set(value):
            if value is None:
                return None
            return {value} if isinstance(value, str) else set(value)

        try:
            profile = validate_routing_profile(profile_id, profile)
        except CatalogError as exc:
            base["provenance"] = {**base["provenance"], "profile_error": str(exc)}
            return RoutingDecision(status="blocked", reason_codes=("economic_profile_invalid",), **base)
        profile_mode = profile.get("mode")  # None for a profile that declares no economic fields
        single = profile_mode == "single"
        levels = profile.get("levels") if not single else None  # level bounds do not apply to a single model
        step_kind = request.step_kind
        base["profile_mode"] = profile_mode
        profile_effort = profile.get("effort")
        if request.effort is None and isinstance(profile_effort, dict) and profile_effort.get(step_kind) is not None:
            # The profile only fills a gap: a step that asked for an effort keeps it.
            base["effort"], base["effort_source"] = profile_effort[step_kind], "profile"
        bounds = None
        if request.escalation is not None:
            # An escalation looks at exactly one model level and nothing else about the profile changes. The
            # selector refuses on its own what the engine should never ask for: a profile that does not allow
            # escalation (no ``escalation`` block, a limit of 0, ``single`` mode) or a level above its ceiling.
            declared = profile.get("escalation") if profile_mode in ("supervised", "automatic") else None
            target = request.escalation["to_level"]
            if not isinstance(declared, dict) or declared.get("max_escalations_per_step", 0) < 1:
                return RoutingDecision(status="blocked", reason_codes=("escalation_not_enabled_for_profile",), **base)
            base["escalation"] = {**base["escalation"], "ceiling": declared["ceiling"],
                                  "max_escalations_per_step": declared["max_escalations_per_step"]}
            if MODEL_LEVELS.index(target) > MODEL_LEVELS.index(declared["ceiling"]):
                return RoutingDecision(status="blocked", reason_codes=("escalation_above_ceiling",), **base)
            bounds = {"min": target, "max": target}
        elif levels is not None:
            bounds = levels.get(step_kind)
            if bounds is None:
                return RoutingDecision(status="blocked", reason_codes=("step_kind_levels_missing",), **base)

        max_candidates = profile.get("max_candidates", 2)
        if single:
            model_ids = [profile["model_id"]]
        elif levels is not None:
            # A level-bounded profile ranks the whole catalog unless it names an allowlist.
            model_ids = profile.get("candidate_model_ids") or list(getattr(self.catalog, "models", {}))
        else:
            model_ids = profile.get("candidate_model_ids")
            if (not isinstance(model_ids, list) or not model_ids or type(max_candidates) is not int
                    or not 1 <= max_candidates <= 2 or len(model_ids) > max_candidates
                    or len(set(map(str, model_ids))) != len(model_ids)):
                return RoutingDecision(status="blocked", reason_codes=("invalid_candidate_limit_or_allowlist",), **base)
        retries = profile.get("max_retries_per_candidate", 0)
        invocation_limit = profile.get("max_provider_invocations")
        if (type(retries) is not int or retries < 0
                or (invocation_limit is not None and (type(invocation_limit) is not int or invocation_limit < 1))):
            return RoutingDecision(status="blocked", reason_codes=("invalid_profile_invocation_budget",), **base)
        required_caps = as_set(profile.get("required_capabilities"))
        request_caps = as_set(request.required_capabilities)
        required_perms = as_set(profile.get("required_permissions", []))
        request_perms = as_set(request.required_permissions)
        profile_roles = as_set(profile.get("required_roles", []))
        if any(value is None for value in (required_caps, request_caps, required_perms, request_perms, profile_roles)):
            return RoutingDecision(status="blocked", reason_codes=("required_descriptor_unknown",), **base)
        required_caps |= request_caps
        required_perms |= request_perms
        required_role = request.required_role or profile.get("required_role")
        required_roles = profile_roles | ({required_role} if required_role else set())
        if request.data_class == "sensitive":
            required_perms.add("data:sensitive")
            classes = as_set(request.authorized_data_classes)
            if classes is None or "sensitive" not in classes:
                return RoutingDecision(status="blocked", reason_codes=("sensitive_class_not_authorized",), **base)
        effective_grants = as_set(request.effective_permissions)
        allowed_permissions = as_set(profile.get("allowed_permissions", profile.get("max_permissions")))
        profile_data_classes = as_set(profile.get("allowed_data_classes"))
        mode_allowlist = as_set(profile.get("execution_modes", ["auto", "local", "cloud"]))
        base.update({
            "required_capabilities": tuple(sorted(required_caps)),
            "required_role": required_role,
            "required_permissions": tuple(sorted(required_perms)),
            "allowed_permissions": tuple(sorted(allowed_permissions)) if allowed_permissions is not None else None,
        })
        if mode_allowlist is None or mode not in mode_allowlist:
            return RoutingDecision(status="blocked", reason_codes=("execution_mode_not_allowed",), **base)

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        candidates: list[RoutingCandidate] = []
        eligible_specs: list[ModelSpec] = []
        for raw_id in model_ids:
            model_id = str(raw_id)
            try:
                spec = self.catalog.model(model_id)
            except (KeyError, TypeError):
                candidates.append(RoutingCandidate(model_id, None, None, None, eligible=False,
                                                   reason_codes=("unknown_model",)))
                continue
            reasons: list[str] = []
            capabilities = as_set(getattr(spec, "capabilities", None)) or set()
            if spec.structured_output:
                capabilities.add("structured_output")
            if spec.web:
                capabilities.add("web")
            if not spec.structured_output or (request.requires_web and not spec.web):
                reasons.append("schema_or_web_incompatible")
            if required_caps - capabilities:
                reasons.append("required_capability_missing_or_unknown")
            roles = as_set(getattr(spec, "roles", None))
            if required_roles and (roles is None or required_roles - roles):
                reasons.append("required_role_missing_or_unknown")
            available_permissions = as_set(getattr(spec, "permissions", None))
            consumed_permissions = as_set(getattr(spec, "permissions_required", None))
            if consumed_permissions is None:
                reasons.append("candidate_required_permissions_unknown")
                consumed_permissions = set()
            needed_permissions = required_perms | consumed_permissions
            if str(spec.tier).casefold() == "cloud":
                needed_permissions.add("provider:cloud")
            if needed_permissions and (available_permissions is None or needed_permissions - available_permissions):
                reasons.append("candidate_permission_unavailable_or_unknown")
            if needed_permissions and effective_grants is None:
                reasons.append("effective_permission_grants_unknown")
            elif needed_permissions and needed_permissions - effective_grants:
                reasons.append("effective_permission_not_granted")
            if needed_permissions and allowed_permissions is None:
                reasons.append("profile_permission_ceiling_unknown")
            elif needed_permissions and needed_permissions - allowed_permissions:
                reasons.append("permission_exceeds_profile_ceiling")
            tier = str(spec.tier).casefold()
            if mode == "local" and tier != "local":
                reasons.append("local_mode_requires_local_candidate")
            if mode == "cloud" and tier != "cloud":
                reasons.append("cloud_mode_requires_cloud_candidate")
            candidate_classes = as_set(getattr(spec, "allowed_data_classes", None))
            if request.data_class is not None:
                if candidate_classes is None or request.data_class not in candidate_classes:
                    reasons.append("candidate_data_class_unknown_or_disallowed")
                if profile_data_classes is None or request.data_class not in profile_data_classes:
                    reasons.append("profile_data_class_unknown_or_disallowed")
            elif candidate_classes is not None or profile_data_classes is not None or tier == "cloud":
                reasons.append("request_data_class_unknown")
            if tier == "cloud":
                authorized = as_set(request.authorized_data_classes)
                if (request.data_class is None or authorized is None
                        or request.data_class not in authorized):
                    reasons.append("cloud_data_class_not_explicitly_authorized")
            if bool(profile.get("require_health", False)):
                raw_health = evidence.provider_health.get(spec.provider)
                health = raw_health.to_dict() if hasattr(raw_health, "to_dict") else raw_health
                if not isinstance(health, Mapping) or health.get("value", health.get("available")) is not True:
                    reasons.append("provider_health_unknown_or_unavailable")
                elif (health.get("provenance") != "adapter_health" or not health.get("source")
                      or health.get("scope") not in {spec.provider, spec.id}):
                    reasons.append("provider_health_provenance_unknown")
                else:
                    max_age = profile.get("health_max_age_seconds")
                    observed_at = health.get("observed_at")
                    try:
                        observed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
                        age = (now - observed).total_seconds()
                        if (type(max_age) is not int or max_age <= 0 or observed.tzinfo is None
                                or age < 0 or age > max_age):
                            raise ValueError("health snapshot outside configured freshness window")
                    except (TypeError, ValueError):
                        reasons.append("provider_health_timestamp_unknown_or_stale")
                    valid_until = health.get("valid_until")
                    if valid_until is not None:
                        try:
                            if datetime.fromisoformat(str(valid_until).replace("Z", "+00:00")) <= now:
                                reasons.append("provider_health_snapshot_expired")
                        except (TypeError, ValueError):
                            reasons.append("provider_health_timestamp_unknown_or_stale")
            level = getattr(spec, "level", None)
            classification = getattr(spec, "classification", "unclassified")
            if bounds is not None:
                if classification != "classified" or level not in MODEL_LEVELS:
                    reasons.append("model_unclassified")
                else:
                    position = MODEL_LEVELS.index(level)
                    if "min" in bounds and position < MODEL_LEVELS.index(bounds["min"]):
                        reasons.append("level_below_minimum")
                    if "max" in bounds and position > MODEL_LEVELS.index(bounds["max"]):
                        reasons.append("level_above_maximum")
                if self._current_price(getattr(spec, "pricing", None), now=now) is None:
                    reasons.append("price_evidence_missing_or_expired")
            if request.excluded_models and spec.id in request.excluded_models:
                reasons.append(request.excluded_models[spec.id])
            candidate = RoutingCandidate(
                model_id=spec.id, provider=spec.provider, planned_model=spec.model, tier=spec.tier,
                level=level, classification=classification,
                capabilities=frozenset(capabilities), roles=frozenset(roles) if roles is not None else None,
                permissions_available=frozenset(available_permissions) if available_permissions is not None else None,
                permissions_required=frozenset(consumed_permissions),
                allowed_data_classes=frozenset(candidate_classes) if candidate_classes is not None else None,
                pricing=getattr(spec, "pricing", None), eligible=not reasons, reason_codes=tuple(reasons),
                provenance={"catalog": "catalog_declared", "health_scope": evidence.scope},
            )
            candidates.append(candidate)
            if not reasons:
                eligible_specs.append(spec)
        if not eligible_specs:
            summary = sorted({code for candidate in candidates for code in candidate.reason_codes
                              if code in _LEVEL_REASON_CODES}) if bounds is not None else []
            return RoutingDecision(status="blocked", reason_codes=("no_eligible_candidate", *summary),
                                   candidates=tuple(candidates), **base)

        if single:
            economic = False  # one named model: neither cheaper nor dearer is looked for
        elif levels is not None:
            # Orchestration, planning and review always take the cheapest; work follows economic_selection.
            economic = step_kind != "work" or bool(profile.get("economic_selection", True))
        else:
            economic = bool(profile.get("economic_selection", False))
        ranking = "not_ranked"
        amounts: list[float] | None = None
        prices = []
        if economic:
            prices = [self._current_price(getattr(spec, "pricing", None), now=now) for spec in eligible_specs]
            if any(price is None for price in prices):
                return RoutingDecision(status="blocked", reason_codes=("comparable_price_unknown",), candidates=tuple(candidates), **base)
            if len({price[0][1:] for price in prices}) != 1:
                return RoutingDecision(status="blocked", reason_codes=("price_units_or_currency_incomparable",), candidates=tuple(candidates), **base)
            if request.estimated_input_tokens is not None and request.estimated_output_tokens is not None:
                amounts = [price[0][0] * request.estimated_input_tokens / price[0][2]
                           + price[1][0] * request.estimated_output_tokens / price[1][2] for price in prices]
                ranking = "estimated_tokens"
            elif len(prices) == 1:
                ranking = "single_candidate"
            elif len(prices) > 1:
                ranking = "dominant_rates"
                dominators = [i for i, price in enumerate(prices) if all(
                    price[0][0] <= other[0][0] and price[1][0] <= other[1][0] for other in prices
                )]
                if len(dominators) == 1:
                    winner = dominators[0]
                    eligible_specs = [eligible_specs[winner], *[s for i, s in enumerate(eligible_specs) if i != winner]]
                elif not dominators or not all(prices[i] == prices[dominators[0]] for i in dominators):
                    return RoutingDecision(status="blocked", reason_codes=("common_token_quantities_or_price_dominance_missing",), candidates=tuple(candidates), **base)
            if amounts is not None:
                order = sorted(range(len(eligible_specs)), key=lambda i: (amounts[i], eligible_specs[i].id))
                eligible_specs = [eligible_specs[i] for i in order]
        planning_max_cost = profile.get("planning_max_cost")
        if planning_max_cost is not None:
            if (not isinstance(planning_max_cost, dict)
                    or not isinstance(planning_max_cost.get("amount"), (int, float))
                    or isinstance(planning_max_cost.get("amount"), bool)
                    or not math.isfinite(float(planning_max_cost["amount"]))
                    or planning_max_cost["amount"] < 0
                    or request.estimated_input_tokens is None or request.estimated_output_tokens is None):
                return RoutingDecision(status="blocked", reason_codes=("cost_budget_unknown_or_incomparable",), candidates=tuple(candidates), **base)
            within_cost = []
            unknown_cost = False
            for spec in eligible_specs:
                price = self._current_price(getattr(spec, "pricing", None), now=now)
                if price is None or price[0][1] != planning_max_cost.get("currency"):
                    unknown_cost = True
                    continue
                projected = (price[0][0] * request.estimated_input_tokens / price[0][2]
                             + price[1][0] * request.estimated_output_tokens / price[1][2])
                if projected <= float(planning_max_cost["amount"]):
                    within_cost.append(spec)
            eligible_specs = within_cost
            if not eligible_specs:
                reason = "cost_budget_unknown_or_incomparable" if unknown_cost else "cost_budget_exceeded"
                return RoutingDecision(status="blocked", reason_codes=(reason,), candidates=tuple(candidates), **base)
        max_input = profile.get("planning_max_input_tokens")
        max_output = profile.get("planning_max_output_tokens")
        if max_input is not None and (request.estimated_input_tokens is None or request.estimated_input_tokens > int(max_input)):
            return RoutingDecision(status="blocked", reason_codes=("input_token_budget_unknown_or_exceeded",), candidates=tuple(candidates), **base)
        if max_output is not None and (request.estimated_output_tokens is None or request.estimated_output_tokens > int(max_output)):
            return RoutingDecision(status="blocked", reason_codes=("output_token_budget_unknown_or_exceeded",), candidates=tuple(candidates), **base)

        selected, *alternatives = eligible_specs
        escalation_declared = profile.get("escalation") if profile_mode in ("supervised", "automatic") else None
        if isinstance(escalation_declared, dict) and escalation_declared.get("max_escalations_per_step", 0) >= 1:
            # A profile that escalates keeps the fallback inside the selected model's own level (a provider
            # error is answered by a peer); going up a level is what escalation does, once and recorded.
            alternatives = [spec for spec in alternatives if spec.level == selected.level]
        fallback = tuple(spec.id for spec in alternatives[:1])
        chosen = next(candidate for candidate in candidates if candidate.model_id == selected.id)
        if single:
            reasons = ("single_model_profile",)
        elif economic:
            reasons = ("lowest_estimated_cost_for_common_token_quantities_or_dominant_rates",)
        else:
            reasons = ("first_eligible_configured_candidate_after_planning_filters",)
        if request.escalation is not None:
            reasons = ("escalated_to_next_level", *reasons)
        price_evidence = self._price_evidence(selected, now=now, request=request, ranking=ranking)
        limit_keys = ("max_candidates", "max_retries_per_candidate", "max_provider_invocations",
                      "planning_max_input_tokens", "planning_max_output_tokens", "planning_max_cost")
        limits = {key: profile.get(key) for key in limit_keys if key in profile}
        return RoutingDecision(
            status="selected", requested_model=request.requested_model, selected_model=selected.id,
            planned_model=selected.model, fallback_models=fallback, profile_id=profile_id,
            profile_version=profile.get("version"), required_capabilities=tuple(sorted(required_caps)),
            required_role=required_role, required_permissions=tuple(sorted(required_perms)),
            effective_permissions=tuple(sorted(effective_grants)) if effective_grants is not None else None,
            allowed_permissions=tuple(sorted(allowed_permissions)) if allowed_permissions is not None else None,
            data_class=request.data_class,
            authorized_data_classes=tuple(sorted(as_set(request.authorized_data_classes))) if request.authorized_data_classes is not None else None,
            applied_capabilities=tuple(sorted(required_caps & (chosen.capabilities or frozenset()))),
            applied_roles=tuple(sorted(required_roles & (chosen.roles or frozenset()))),
            applied_permissions=tuple(sorted(required_perms | (chosen.permissions_required or frozenset()))),
            reason_codes=reasons, candidates=tuple(candidates),
            provenance={"profile": "catalog_declared", "permissions": request.authorization_provenance or "unknown",
                        "evidence": evidence.provenance},
            budget_scope={**budget_scope, "limits": limits},
            manual_required=False,
            step_kind=step_kind, profile_mode=profile_mode, selected_level=selected.level,
            price_evidence=price_evidence, effort=base["effort"], effort_source=base["effort_source"],
            escalation=base["escalation"],
        )
    def _validate_compat(self, spec: ModelSpec, req: TaskRequirements) -> None:
        if req.requires_web and not spec.web:
            raise ProviderUnavailable(f"{spec.id} does not satisfy requires_web")
        if not spec.structured_output:
            raise ProviderUnavailable(f"{spec.id} does not support structured output")

    @staticmethod
    def _notify_observer(callback, payload: dict[str, Any], *, name: str) -> None:
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:
            _logger.warning("ModelRouter %s callback failed; continuing routing", name, exc_info=True)

    @classmethod
    def _notify_attempt(cls, callback: AttemptCallback | None, payload: dict[str, Any]) -> None:
        cls._notify_observer(callback, payload, name="on_attempt")

    @classmethod
    def _notify_failed_attempt(cls, callback, payload: dict[str, Any]) -> None:
        cls._notify_observer(callback, payload, name="on_failed_attempt")

    @staticmethod
    def _observed_model(metadata: dict[str, Any]) -> str | None:
        """Accept a model as observed only when metadata identifies provider provenance."""
        value = metadata.get("provider_observed_model") or metadata.get("observed_model")
        provenance = metadata.get("observed_model_provenance")
        if value and provenance == "provider_response":
            return str(value)
        return None

    @staticmethod
    def _attempt_payload(*, model_id: str, spec: ModelSpec, retry: int, ordinal: int,
                         stage: str, provider_invoked: bool, status: str,
                         response: LLMResponse | None = None, error: Exception | None = None,
                         duration_ms: int | None = None,
                         effort_translation: EffortTranslation | None = None) -> dict[str, Any]:
        metadata = dict(response.metadata) if response is not None else dict(getattr(error, "metadata", {}) or {})
        observed_model = ModelRouter._observed_model(metadata)
        if observed_model is None and error is not None:
            error_metadata = dict(getattr(error, "metadata", {}) or {})
            observed_model = ModelRouter._observed_model(error_metadata)
        model_digest = response.model_digest if response is not None else getattr(error, "model_digest", None)
        tokens_in = response.tokens_in if response is not None else getattr(error, "tokens_in", None)
        tokens_out = response.tokens_out if response is not None else getattr(error, "tokens_out", None)
        num_ctx = metadata.get("num_ctx", getattr(error, "num_ctx", None))
        payload = {
            "model": model_id,
            "provider": response.provider if response is not None else spec.provider,
            "ok": status == "success",
            "retry": retry,
            "error": str(error) if error is not None else None,
            "error_type": type(error).__name__ if error is not None else None,
            "model_digest": model_digest,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "num_ctx": num_ctx,
            "duration_ms": duration_ms if duration_ms is not None else (response.latency_ms if response is not None else None),
            "metadata": metadata,
            "call_ordinal": ordinal,
            "candidate": model_id,
            "attempted_model": model_id,
            "effective_config_model": spec.model if provider_invoked else None,
            "effective_config_provenance": "catalog_config" if provider_invoked else "unknown",
            "stage": stage,
            "provider_invoked": provider_invoked,
            "status": status,
            "requested_model": model_id,
            "effective_model": spec.model if provider_invoked else None,
            "observed_model": observed_model,
        }
        if effort_translation is not None and effort_translation.requested is not None:
            # Added only when a step asked for an effort, so attempts without one keep their shape.
            # ``effort_applied`` is True only when the provider call actually carried the setting;
            # ``effective_config_effort`` is that provider setting (None when ignored).
            applied = provider_invoked and effort_translation.applied
            if applied and metadata.get("effort_applied") is False:
                applied = False  # the adapter itself reported that it ignored the effort
            note = effort_translation.note
            if not provider_invoked:
                note = "provider_not_invoked"
            elif metadata.get("effort_applied") is False and metadata.get("effort_note"):
                note = str(metadata["effort_note"])
            payload.update({
                "effort_requested": effort_translation.requested,
                "effort_applied": applied,
                "effective_config_effort": dict(effort_translation.setting) if applied and effort_translation.setting else None,
                "effort_note": None if applied else note,
            })
        return payload

    @staticmethod
    def _not_sent(effort: str | None) -> EffortTranslation | None:
        """Translation recorded for an attempt that never reached a provider."""
        return None if effort is None else ignored_effort(effort, "provider_not_invoked")

    @staticmethod
    def _accepts_effort(adapter: LLMAdapter) -> bool:
        """True when ``generate_structured`` takes ``effort`` (directly or through ``**kwargs``)."""
        try:
            parameters = inspect.signature(adapter.generate_structured).parameters
        except (TypeError, ValueError):
            return False
        return "effort" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())

    @classmethod
    def _plan_effort(cls, adapter: LLMAdapter, spec: ModelSpec, effort: str | None) -> tuple[dict[str, Any], EffortTranslation | None]:
        """Extra call arguments and the translation to record for one adapter.

        Without an effort nothing is passed, so adapters written before ``effort`` existed keep
        working untouched. An adapter that cannot take it is skipped and the effort is recorded as
        not applied; an unsupported effort never fails the call.
        """
        if effort is None:
            return {}, None
        if not cls._accepts_effort(adapter):
            return {}, ignored_effort(effort, "adapter_does_not_accept_effort")
        translate = getattr(adapter, "translate_effort", None)
        translation = translate(spec, effort) if callable(translate) else ignored_effort(effort, "provider_does_not_support_effort")
        return {"effort": effort}, translation

    @staticmethod
    def _takes_call(before_call: Callable[..., None]) -> bool:
        """True when ``before_call`` accepts the :class:`CallContext` of the candidate (one positional argument).

        A callable written before the guard chain existed takes no arguments and is called as it always was.
        """
        try:
            parameters = inspect.signature(before_call).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.VAR_POSITIONAL) for p in parameters)

    @classmethod
    def _before_call_runner(cls, before_call: Callable[..., None] | None, prompt: str):
        """``run(model_id, spec, retry, ordinal)`` that calls ``before_call`` for one candidate, or ``None``.

        The check receives a :class:`CallContext` with what the router knows (candidate, retry, ordinal and
        the estimate of ``prompt``, the exact text about to be sent); the host completes the rest of it.
        """
        if not before_call:
            return None
        if not cls._takes_call(before_call):
            return lambda model_id, spec, retry, ordinal: (before_call(), prompt)[1]
        estimated = estimate_tokens_conservative(prompt)

        def run(model_id: str, spec: ModelSpec, retry: int, ordinal: int) -> str:
            outgoing = [prompt]
            before_call(CallContext(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                    estimated_input_tokens=estimated, outgoing_text=prompt,
                                    replace_outgoing=lambda value: outgoing.__setitem__(0, value)))
            return outgoing[0]

        return run

    def execute(self, *, binding: ModelBinding, requirements: TaskRequirements, prompt: str,
                output_schema: type[BaseModel], before_call=None,
                validate_response: ResponseValidator | None = None, on_failed_attempt=None,
                on_attempt: AttemptCallback | None = None, effort: str | None = None):
        check_before_call = self._before_call_runner(before_call, prompt)
        if self.dry_run:
            adapter = self.adapters["fake"]
            fake_spec = ModelSpec(id="fixture", provider="fake", model="fixture", tier=requirements.tier.value,
                                  structured_output=True, web=True)
            started = time.perf_counter()
            response: LLMResponse | None = None
            provider_invoked = False
            effort_kwargs, translation = self._plan_effort(adapter, fake_spec, effort)
            try:
                outgoing_prompt = prompt
                if check_before_call:
                    outgoing_prompt = check_before_call("fixture", fake_spec, 0, 1)
                provider_invoked = True
                response = adapter.generate_structured(spec=fake_spec, prompt=outgoing_prompt, output_schema=output_schema,
                                                       requires_web=requirements.requires_web, **effort_kwargs)
                if validate_response:
                    try:
                        validate_response(response.parsed, response)
                    except (ValueError, TypeError) as exc:
                        raise _semantic_failure(exc) from exc
                response.metadata["catalog_model_id"] = "fixture"
                event = self._attempt_payload(model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                                              stage="provider", provider_invoked=True, status="success",
                                              response=response, duration_ms=int((time.perf_counter() - started) * 1000),
                                              effort_translation=translation)
                self._notify_attempt(on_attempt, event)
                return response, [{"model": "fixture", "provider": "fake", "ok": True, "retry": 0}]
            except _ROUTABLE_ERRORS as exc:
                failure = self._attempt_payload(model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                                                stage="provider", provider_invoked=True, status="failed",
                                                response=response, error=exc,
                                                duration_ms=(response.latency_ms if response is not None else int((time.perf_counter() - started) * 1000)),
                                                effort_translation=translation)
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                raise
            except Exception as exc:
                failure = self._attempt_payload(
                    model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                    stage="provider" if provider_invoked else "preflight",
                    provider_invoked=provider_invoked, status="failed", response=response, error=exc,
                    duration_ms=(response.latency_ms if response is not None else int((time.perf_counter() - started) * 1000)),
                    effort_translation=translation,
                )
                if provider_invoked:
                    self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                raise

        candidates = [binding.preferred_model] + ([binding.fallback_model] if binding.fallback_model else [])
        attempts: list[dict] = []
        last_exc: Exception | None = None
        ordinal = 0
        for candidate_index, model_id in enumerate(candidates):
            spec = self.catalog.model(model_id)
            try:
                self._validate_compat(spec, requirements)
            except _ROUTABLE_ERRORS as exc:
                ordinal += 1
                last_exc = exc
                failure = self._attempt_payload(model_id=model_id, spec=spec, retry=0, ordinal=ordinal,
                                                stage="preflight", provider_invoked=False,
                                                status="failed", error=exc,
                                                effort_translation=self._not_sent(effort))
                attempts.append(failure)
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                continue
            adapter = self.adapters.get(spec.provider)
            if not adapter:
                ordinal += 1
                last_exc = ProviderUnavailable(f"No adapter is configured for {spec.provider}")
                failure = self._attempt_payload(model_id=model_id, spec=spec, retry=0, ordinal=ordinal,
                                                stage="preflight", provider_invoked=False,
                                                status="failed", error=last_exc,
                                                effort_translation=self._not_sent(effort))
                attempts.append(failure)
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                continue

            retries = binding.max_retries if candidate_index == 0 else binding.fallback_max_retries
            effort_kwargs, translation = self._plan_effort(adapter, spec, effort)
            for retry in range(retries + 1):
                ordinal += 1
                outgoing_prompt = prompt
                response: LLMResponse | None = None
                started = time.perf_counter()
                if check_before_call:
                    try:
                        outgoing_prompt = check_before_call(model_id, spec, retry, ordinal)
                    except Exception as exc:
                        failure = self._attempt_payload(model_id=model_id, spec=spec, retry=retry,
                                                        ordinal=ordinal, stage="preflight", provider_invoked=False,
                                                        status="failed", error=exc, effort_translation=translation)
                        attempts.append(failure)
                        self._notify_attempt(on_attempt, failure)
                        raise
                try:
                    response = adapter.generate_structured(spec=spec, prompt=outgoing_prompt, output_schema=output_schema,
                                                           requires_web=requirements.requires_web, **effort_kwargs)
                    if validate_response:
                        try:
                            validate_response(response.parsed, response)
                        except (ValueError, TypeError) as exc:
                            raise _semantic_failure(exc) from exc
                except _ROUTABLE_ERRORS as exc:
                    last_exc = exc
                    duration_ms = (response.latency_ms if response is not None and response.latency_ms > 0
                                   else int((time.perf_counter() - started) * 1000))
                    failure = self._attempt_payload(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                                   stage="provider", provider_invoked=True, status="failed",
                                                   response=response, error=exc, duration_ms=duration_ms,
                                                   effort_translation=translation)
                    attempts.append(failure)
                    self._notify_failed_attempt(on_failed_attempt, failure)
                    self._notify_attempt(on_attempt, failure)
                    if not isinstance(exc, StructuredOutputError):
                        break
                except Exception as exc:
                    failure = self._attempt_payload(
                        model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                        stage="provider", provider_invoked=True, status="failed", response=response,
                        error=exc, duration_ms=(response.latency_ms if response is not None and response.latency_ms > 0
                                                else int((time.perf_counter() - started) * 1000)),
                        effort_translation=translation,
                    )
                    attempts.append(failure)
                    self._notify_failed_attempt(on_failed_attempt, failure)
                    self._notify_attempt(on_attempt, failure)
                    raise
                else:
                    response.metadata["catalog_model_id"] = model_id
                    event = self._attempt_payload(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                                  stage="provider", provider_invoked=True, status="success",
                                                  response=response, duration_ms=int((time.perf_counter() - started) * 1000),
                                                  effort_translation=translation)
                    attempts.append(event)
                    self._notify_attempt(on_attempt, event)
                    return response, attempts
        assert last_exc is not None
        raise last_exc
