from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import time
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from .adapters.base import LLMAdapter, LLMResponse
from .catalog import Catalog, ModelSpec
from .contracts import TaskRequirements
from .routing_policy import RoutingCandidate, RoutingDecision, RoutingEvidence, RoutingRequest
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
        if not isinstance(pricing, dict):
            return None
        components = []
        for component in ("input", "output"):
            price = pricing.get(component)
            if not isinstance(price, dict):
                return None
            amount = price.get("amount")
            denominator = price.get("denominator")
            unit = price.get("unit")
            currency = price.get("currency")
            source = price.get("source")
            valid_from = price.get("valid_from")
            valid_until = price.get("valid_until")
            if (not isinstance(amount, (int, float)) or isinstance(amount, bool)
                    or not math.isfinite(float(amount)) or amount < 0
                    or type(denominator) is not int or denominator <= 0
                    or not unit or not currency or not source or not valid_from or not valid_until
                    or price.get("provenance") in {None, "unknown"}):
                return None
            try:
                starts = datetime.fromisoformat(str(valid_from).replace("Z", "+00:00"))
                ends = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
                if starts.tzinfo is None or ends.tzinfo is None or starts > now or ends <= now:
                    return None
            except (TypeError, ValueError):
                return None
            components.append((float(amount), str(currency), int(denominator), str(unit)))
        if components[0][1:] != components[1][1:]:
            return None
        return components

    def select(self, request: RoutingRequest, evidence: RoutingEvidence | None = None) -> RoutingDecision:
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
        )
        if profile_id is None:
            return RoutingDecision(status="manual", reason_codes=("no_profile_requested",), **base)
        profile = getattr(self.catalog, "routing_profiles", {}).get(profile_id)
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

        model_ids = profile.get("candidate_model_ids")
        max_candidates = profile.get("max_candidates", 2)
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
            candidate = RoutingCandidate(
                model_id=spec.id, provider=spec.provider, planned_model=spec.model, tier=spec.tier,
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
            return RoutingDecision(status="blocked", reason_codes=("no_eligible_candidate",), candidates=tuple(candidates), **base)

        economic = bool(profile.get("economic_selection", False))
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
            elif len(prices) > 1:
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
        fallback = tuple(spec.id for spec in alternatives[:1])
        chosen = next(candidate for candidate in candidates if candidate.model_id == selected.id)
        reasons = ("lowest_estimated_cost_for_common_token_quantities_or_dominant_rates",) if economic else ("first_eligible_configured_candidate_after_planning_filters",)
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
                         duration_ms: int | None = None) -> dict[str, Any]:
        metadata = dict(response.metadata) if response is not None else dict(getattr(error, "metadata", {}) or {})
        observed_model = ModelRouter._observed_model(metadata)
        if observed_model is None and error is not None:
            error_metadata = dict(getattr(error, "metadata", {}) or {})
            observed_model = ModelRouter._observed_model(error_metadata)
        model_digest = response.model_digest if response is not None else getattr(error, "model_digest", None)
        tokens_in = response.tokens_in if response is not None else getattr(error, "tokens_in", None)
        tokens_out = response.tokens_out if response is not None else getattr(error, "tokens_out", None)
        num_ctx = metadata.get("num_ctx", getattr(error, "num_ctx", None))
        return {
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

    def execute(self, *, binding: ModelBinding, requirements: TaskRequirements, prompt: str,
                output_schema: type[BaseModel], before_call=None,
                validate_response: ResponseValidator | None = None, on_failed_attempt=None,
                on_attempt: AttemptCallback | None = None):
        if self.dry_run:
            adapter = self.adapters["fake"]
            fake_spec = ModelSpec(id="fixture", provider="fake", model="fixture", tier=requirements.tier.value,
                                  structured_output=True, web=True)
            started = time.perf_counter()
            response: LLMResponse | None = None
            provider_invoked = False
            try:
                if before_call:
                    before_call()
                provider_invoked = True
                response = adapter.generate_structured(spec=fake_spec, prompt=prompt, output_schema=output_schema,
                                                       requires_web=requirements.requires_web)
                if validate_response:
                    try:
                        validate_response(response.parsed, response)
                    except (ValueError, TypeError) as exc:
                        raise StructuredOutputError(f"Semantically invalid output: {exc}") from exc
                response.metadata["catalog_model_id"] = "fixture"
                event = self._attempt_payload(model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                                              stage="provider", provider_invoked=True, status="success",
                                              response=response, duration_ms=int((time.perf_counter() - started) * 1000))
                self._notify_attempt(on_attempt, event)
                return response, [{"model": "fixture", "provider": "fake", "ok": True, "retry": 0}]
            except _ROUTABLE_ERRORS as exc:
                failure = self._attempt_payload(model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                                                stage="provider", provider_invoked=True, status="failed",
                                                response=response, error=exc,
                                                duration_ms=(response.latency_ms if response is not None else int((time.perf_counter() - started) * 1000)))
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                raise
            except Exception as exc:
                failure = self._attempt_payload(
                    model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                    stage="provider" if provider_invoked else "preflight",
                    provider_invoked=provider_invoked, status="failed", response=response, error=exc,
                    duration_ms=(response.latency_ms if response is not None else int((time.perf_counter() - started) * 1000)),
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
                                                status="failed", error=exc)
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
                                                status="failed", error=last_exc)
                attempts.append(failure)
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                continue

            retries = binding.max_retries if candidate_index == 0 else binding.fallback_max_retries
            for retry in range(retries + 1):
                ordinal += 1
                response: LLMResponse | None = None
                started = time.perf_counter()
                if before_call:
                    try:
                        before_call()
                    except Exception as exc:
                        failure = self._attempt_payload(model_id=model_id, spec=spec, retry=retry,
                                                        ordinal=ordinal, stage="preflight", provider_invoked=False,
                                                        status="failed", error=exc)
                        attempts.append(failure)
                        self._notify_attempt(on_attempt, failure)
                        raise
                try:
                    response = adapter.generate_structured(spec=spec, prompt=prompt, output_schema=output_schema,
                                                           requires_web=requirements.requires_web)
                    if validate_response:
                        try:
                            validate_response(response.parsed, response)
                        except (ValueError, TypeError) as exc:
                            raise StructuredOutputError(f"Semantically invalid output: {exc}") from exc
                except _ROUTABLE_ERRORS as exc:
                    last_exc = exc
                    duration_ms = (response.latency_ms if response is not None and response.latency_ms > 0
                                   else int((time.perf_counter() - started) * 1000))
                    failure = self._attempt_payload(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                                   stage="provider", provider_invoked=True, status="failed",
                                                   response=response, error=exc, duration_ms=duration_ms)
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
                    )
                    attempts.append(failure)
                    self._notify_failed_attempt(on_failed_attempt, failure)
                    self._notify_attempt(on_attempt, failure)
                    raise
                else:
                    response.metadata["catalog_model_id"] = model_id
                    event = self._attempt_payload(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                                  stage="provider", provider_invoked=True, status="success",
                                                  response=response, duration_ms=int((time.perf_counter() - started) * 1000))
                    attempts.append(event)
                    self._notify_attempt(on_attempt, event)
                    return response, attempts
        assert last_exc is not None
        raise last_exc
