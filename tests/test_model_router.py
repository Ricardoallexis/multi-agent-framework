from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel
import pytest

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.catalog import ModelSpec
from multiagent.contracts import ModelTier, TaskRequirements
from multiagent.errors import ProviderUnavailable, StructuredOutputError
from multiagent.model_router import ModelBinding, ModelRouter
from multiagent.routing_policy import RoutingEvidence, RoutingRequest, summarize_routing_evidence


class Payload(BaseModel):
    value: str


class StaticCatalog:
    def __init__(self):
        self.routing_profiles = {}
        self.models = {
            "primary": ModelSpec("primary", "one", "provider-one", "local", True, False),
            "fallback": ModelSpec("fallback", "two", "provider-two", "cloud", True, True),
            "no_web": ModelSpec("no_web", "one", "provider-one", "local", True, False),
        }

    def model(self, model_id):
        return self.models[model_id]


class SequenceAdapter(LLMAdapter):
    def __init__(self, provider, responses):
        self.provider = provider
        self.responses = list(responses)
        self.calls = 0

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def health(self):
        return {"provider": self.provider, "available": True}


def response(value, *, model, tokens_in=13, tokens_out=5, metadata=None):
    parsed = Payload(value=value)
    return LLMResponse(parsed, parsed.model_dump_json(), model.split("-")[0], model,
                       tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=12,
                       metadata=dict(metadata or {}))


def requirements(requires_web=False):
    return TaskRequirements(requires_web=requires_web, output_schema="Payload", tier=ModelTier.LOCAL)


def reject_marked(payload, _response):
    if payload.value == "reject":
        raise ValueError("semantic rejection")


def test_semantic_rejection_preserves_response_usage_and_fallback_event():
    primary = SequenceAdapter("one", [response("reject", model="provider-one-v2", metadata={"num_ctx": 4096, "vendor": "x"})])
    fallback = SequenceAdapter("two", [response("accepted", model="provider-two-v3")])
    events, failed = [], []
    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})

    result, attempts = router.execute(
        binding=ModelBinding("primary", "fallback"), requirements=requirements(), prompt="x",
        output_schema=Payload, validate_response=reject_marked,
        on_attempt=events.append, on_failed_attempt=failed.append,
    )

    assert result.parsed.value == "accepted"
    assert [event["call_ordinal"] for event in events] == [1, 2]
    assert [event["requested_model"] for event in events] == ["primary", "fallback"]
    rejected = events[0]
    assert rejected["status"] == "failed" and rejected["provider_invoked"] is True
    assert rejected["observed_model"] is None
    assert rejected["effective_model"] == "provider-one"
    assert rejected["tokens_in"] == 13 and rejected["tokens_out"] == 5
    assert rejected["num_ctx"] == 4096 and rejected["metadata"]["vendor"] == "x"
    assert rejected["error_type"] == "StructuredOutputError"
    assert len(failed) == 1 and failed[0]["tokens_in"] == 13
    assert len(attempts) == 2


def test_retry_and_final_failure_report_each_invoked_attempt():
    adapter = SequenceAdapter("one", [response("reject", model="provider-one-v2"), response("reject", model="provider-one-v2")])
    events, failed = [], []
    router = ModelRouter(StaticCatalog(), {"one": adapter})

    with pytest.raises(StructuredOutputError):
        router.execute(binding=ModelBinding("primary", max_retries=1), requirements=requirements(),
                       prompt="x", output_schema=Payload, validate_response=reject_marked,
                       on_attempt=events.append, on_failed_attempt=failed.append)

    assert [event["call_ordinal"] for event in events] == [1, 2]
    assert [event["retry"] for event in events] == [0, 1]
    assert all(event["provider_invoked"] for event in events)
    assert len(failed) == 2


def test_preflight_rejection_emits_event_without_usage_callback():
    primary = SequenceAdapter("one", [])
    fallback = SequenceAdapter("two", [response("accepted", model="provider-two-v3")])
    events, failed = [], []
    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})

    router.execute(binding=ModelBinding("no_web", "fallback"), requirements=requirements(requires_web=True),
                   prompt="x", output_schema=Payload, on_attempt=events.append,
                   on_failed_attempt=failed.append)

    assert events[0]["stage"] == "preflight"
    assert events[0]["provider_invoked"] is False
    assert events[0]["error_type"] == "ProviderUnavailable"
    assert primary.calls == 0 and fallback.calls == 1
    assert len(failed) == 1
    assert failed[0]["stage"] == "preflight" and failed[0]["provider_invoked"] is False


def test_observer_callback_errors_do_not_change_fallback_or_success(caplog):
    primary = SequenceAdapter("one", [ProviderUnavailable("offline")])
    fallback = SequenceAdapter("two", [response("accepted", model="provider-two-v3")])
    events, failures = [], []

    def broken_attempt(event):
        events.append(event)
        raise RuntimeError("event sink unavailable")

    def broken_failure(event):
        failures.append(event)
        raise RuntimeError("legacy observer unavailable")

    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})
    result, _ = router.execute(
        binding=ModelBinding("primary", "fallback"), requirements=requirements(), prompt="x",
        output_schema=Payload, on_attempt=broken_attempt, on_failed_attempt=broken_failure,
    )

    assert result.parsed.value == "accepted"
    assert primary.calls == fallback.calls == 1
    assert len(events) == 2 and len(failures) == 1
    assert "on_attempt callback failed" in caplog.text
    assert "on_failed_attempt callback failed" in caplog.text


def test_fallback_candidate_uses_its_own_bounded_retry_count():
    primary = SequenceAdapter("one", [ProviderUnavailable("offline")])
    fallback = SequenceAdapter("two", [StructuredOutputError("invalid"), response("accepted", model="provider-two")])
    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})

    result, attempts = router.execute(
        binding=ModelBinding("primary", "fallback", max_retries=0, fallback_max_retries=1),
        requirements=requirements(), prompt="x", output_schema=Payload,
    )

    assert result.parsed.value == "accepted"
    assert primary.calls == 1
    assert fallback.calls == 2
    assert [attempt["retry"] for attempt in attempts] == [0, 0, 1]
    assert [attempt["attempted_model"] for attempt in attempts] == ["primary", "fallback", "fallback"]


def test_success_observer_error_does_not_discard_provider_response():
    adapter = SequenceAdapter("one", [response("accepted", model="provider-one-v2")])
    router = ModelRouter(StaticCatalog(), {"one": adapter})

    def broken_attempt(_event):
        raise RuntimeError("sink down")

    result, attempts = router.execute(
        binding=ModelBinding("primary"), requirements=requirements(), prompt="x",
        output_schema=Payload, on_attempt=broken_attempt,
    )

    assert result.parsed.value == "accepted"
    assert attempts[0]["status"] == "success"


def priced(amount, *, currency="USD", denominator=1_000_000):
    return {
        "input": {"amount": amount, "currency": currency, "denominator": denominator, "unit": "tokens",
                   "source": "fixture-rate-card", "provenance": "catalog_declared",
                   "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"},
        "output": {"amount": amount * 2, "currency": currency, "denominator": denominator, "unit": "tokens",
                    "source": "fixture-rate-card", "provenance": "catalog_declared",
                    "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"},
    }


def profile_catalog(*, enabled=True, economic=True, require_health=False):
    catalog = StaticCatalog()
    catalog.models = {
        "primary": ModelSpec("primary", "one", "configured-one", "local", True, False,
                              capabilities=frozenset({"text_generation"}), roles=frozenset({"assistant"}),
                              permissions=frozenset(), permissions_required=frozenset(),
                              allowed_data_classes=frozenset({"non_sensitive"}), pricing=priced(1.0)),
        "fallback": ModelSpec("fallback", "two", "configured-two", "local", True, False,
                              capabilities=frozenset({"text_generation"}), roles=frozenset({"assistant"}),
                              permissions=frozenset(), permissions_required=frozenset(),
                              allowed_data_classes=frozenset({"non_sensitive"}), pricing=priced(0.25)),
    }
    catalog.routing_profiles = {
        "costed": {
            "version": 1, "enabled": enabled, "candidate_model_ids": ["primary", "fallback"],
            "max_candidates": 2, "required_capabilities": ["structured_output"], "required_roles": [],
            "required_permissions": [], "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"],
            "execution_modes": ["auto", "local", "cloud"], "economic_selection": economic,
            "require_health": require_health, "max_retries_per_candidate": 0,
            "max_provider_invocations": 3,
        }
    }
    return catalog


def profile_request(**overrides):
    values = {
        "profile_id": "costed", "workflow_id": "sample", "step_id": "create",
        "requested_model": "primary", "required_capabilities": frozenset({"structured_output"}),
        "required_permissions": frozenset(), "effective_permissions": frozenset(),
        "authorized_data_classes": frozenset({"non_sensitive"}),
        "authorization_provenance": "caller_request", "data_class": "non_sensitive",
        "execution_mode": "auto", "output_schema": "Payload",
        "estimated_input_tokens": 1000, "estimated_output_tokens": 200,
    }
    values.update(overrides)
    return RoutingRequest(**values)


def test_profile_selector_chooses_cheapest_comparable_candidate_without_claiming_dispatch():
    router = ModelRouter(profile_catalog(), {"one": SequenceAdapter("one", []), "two": SequenceAdapter("two", [])})
    decision = router.select(profile_request(), RoutingEvidence(scope="run-1", provenance="run_snapshot"))

    assert decision.status == "selected"
    assert decision.requested_model == "primary"
    assert decision.selected_model == "fallback"
    assert decision.planned_model == "configured-two"
    assert decision.effective_model is None and decision.effective_config_model is None
    assert decision.observed_model is None
    assert decision.reason_codes == ("lowest_estimated_cost_for_common_token_quantities_or_dominant_rates",)


def test_explicit_disabled_unknown_or_economically_unknown_profile_never_uses_legacy():
    catalog = profile_catalog(enabled=False)
    router = ModelRouter(catalog, {"one": SequenceAdapter("one", []), "two": SequenceAdapter("two", [])})
    disabled = router.select(profile_request())
    unknown = router.select(profile_request(profile_id="missing"))

    catalog.routing_profiles["costed"]["enabled"] = True
    catalog.models["primary"] = ModelSpec("primary", "one", "configured-one", "local", True, False,
                                           capabilities=frozenset({"text_generation"}), roles=frozenset({"assistant"}),
                                           permissions=frozenset(), permissions_required=frozenset(),
                                           allowed_data_classes=frozenset({"non_sensitive"}), pricing=None)
    missing_cost = router.select(profile_request())

    assert disabled.status == "blocked" and disabled.reason_codes == ("profile_disabled",)
    assert unknown.status == "blocked" and unknown.reason_codes == ("unknown_profile",)
    assert missing_cost.status == "blocked" and "comparable_price_unknown" in missing_cost.reason_codes


def test_cloud_and_sensitive_require_explicit_effective_grants_and_health_is_not_probed():
    catalog = profile_catalog(economic=False, require_health=True)
    catalog.models["primary"] = ModelSpec("primary", "one", "configured-one", "cloud", True, False,
                                           capabilities=frozenset({"text_generation"}), roles=frozenset({"assistant"}),
                                           permissions=frozenset({"provider:cloud", "data:sensitive"}),
                                           permissions_required=frozenset({"provider:cloud"}),
                                           allowed_data_classes=frozenset({"non_sensitive", "sensitive"}))
    adapter = SequenceAdapter("one", [])
    router = ModelRouter(catalog, {"one": adapter, "two": SequenceAdapter("two", [])})
    denied_cloud = router.select(profile_request(execution_mode="cloud"))
    denied_sensitive = router.select(profile_request(
        execution_mode="local", data_class="sensitive", authorized_data_classes=frozenset(),
        effective_permissions=frozenset(),
    ))
    unknown_health = router.select(profile_request(execution_mode="cloud", effective_permissions=frozenset({"provider:cloud"})))

    assert denied_cloud.status == "blocked"
    assert denied_sensitive.status == "blocked" and "sensitive_class_not_authorized" in denied_sensitive.reason_codes
    assert unknown_health.status == "blocked"
    assert adapter.calls == 0


def test_routing_stats_group_call_events_separately_from_usage_rows():
    events = [
        {"event_type": "model_attempt", "payload": {"requested_model": "primary", "effective_model": "configured-one",
         "routing_requested_model": "original-pref", "observed_model": "served-one", "provider_invoked": True,
         "status": "failed", "duration_ms": 8}},
        {"event_type": "model_attempt", "payload": {"requested_model": "primary", "effective_model": None,
         "observed_model": None, "provider_invoked": False, "status": "failed", "duration_ms": None}},
    ]
    summary = summarize_routing_evidence("run-1", events, [
        {"model": "served-one", "tokens_in": 11, "tokens_out": None, "duration_ms": 8,
         "estimated_cost_usd": None, "success": False},
    ])

    assert summary["scope"]["run_id"] == "run-1"
    assert summary["by_requested_model"]["primary"]["provider_invocations"] == 1
    assert summary["by_routing_requested_model"]["original-pref"]["provider_invocations"] == 1
    assert summary["by_requested_model"]["primary"]["preflight_rejections"] == 1
    assert summary["by_observed_model"]["served-one"]["failures"] == 1
    assert summary["by_usage_model"]["served-one"]["tokens_in"]["total"] == 11
    assert summary["by_usage_model"]["served-one"]["tokens_out"]["total"] is None
    assert summary["by_usage_model"]["served-one"]["estimated_cost_usd"]["total"] is None
    partial = summarize_routing_evidence("run-2", [], [
        {"model": "m", "tokens_in": 11, "success": False},
        {"model": "m", "tokens_in": None, "success": False},
    ])
    metric = partial["by_usage_model"]["m"]["tokens_in"]
    assert metric == {"total": None, "known_subtotal": 11, "known_count": 1, "unknown_count": 1}


def test_strict_budgets_fail_closed_without_adapter_enforcement_and_planning_is_labeled():
    catalog = profile_catalog()
    catalog.routing_profiles["costed"]["max_cost"] = {"amount": 1, "currency": "USD"}
    router = ModelRouter(catalog, {"one": SequenceAdapter("one", []), "two": SequenceAdapter("two", [])})

    strict = router.select(profile_request())
    assert strict.status == "blocked"
    assert strict.reason_codes == ("strict_budget_enforcement_unsupported",)

    del catalog.routing_profiles["costed"]["max_cost"]
    catalog.routing_profiles["costed"]["planning_max_input_tokens"] = 500
    planned = router.select(profile_request())
    assert planned.status == "blocked"
    assert planned.reason_codes == ("input_token_budget_unknown_or_exceeded",)
    assert planned.budget_scope["planning_estimates"]["input_tokens"] == 500


def test_unknown_data_class_and_non_observational_health_cannot_qualify_candidate():
    catalog = profile_catalog(economic=False, require_health=True)
    catalog.routing_profiles["costed"]["health_max_age_seconds"] = 60
    router = ModelRouter(catalog, {"one": SequenceAdapter("one", []), "two": SequenceAdapter("two", [])})
    missing_class = router.select(profile_request(data_class=None))
    declared_health = router.select(
        profile_request(),
        RoutingEvidence(scope="run-1", provider_health={
            "one": {"value": True, "source": "yaml", "provenance": "catalog_declared",
                    "observed_at": "2026-10-09T12:00:00Z", "scope": "one"},
        }),
    )
    verified_health = router.select(
        profile_request(),
        RoutingEvidence(scope="run-1", provider_health={
            "one": {"value": True, "source": "adapter.health", "provenance": "adapter_health",
                    "observed_at": datetime.now(timezone.utc).isoformat(), "scope": "one"},
            "two": {"value": True, "source": "adapter.health", "provenance": "adapter_health",
                    "observed_at": datetime.now(timezone.utc).isoformat(), "scope": "two"},
        }),
    )
    assert missing_class.status == "blocked"
    assert declared_health.status == "blocked"
    assert verified_health.status == "selected"


def test_planning_cost_filter_applies_without_economic_ranking():
    catalog = profile_catalog(economic=False)
    catalog.routing_profiles["costed"]["planning_max_cost"] = {"amount": 0.001, "currency": "USD"}
    router = ModelRouter(catalog, {"one": SequenceAdapter("one", []), "two": SequenceAdapter("two", [])})

    decision = router.select(profile_request())

    assert decision.status == "selected"
    assert decision.selected_model == "fallback"
    assert decision.reason_codes == ("first_eligible_configured_candidate_after_planning_filters",)
    assert decision.budget_scope["planning_estimates"]["cost"] == {"amount": 0.001, "currency": "USD"}
