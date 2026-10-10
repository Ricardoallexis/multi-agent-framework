from __future__ import annotations

from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing.executor_capabilities import (
    CapabilityEvidence, ExecutorDescriptor, SupportState,
)
from multiagent.services.assignment import AssignmentServices
from test_routing_policy_conformance import _context, _services


def _priced(amount):
    return {
        "input": {"amount": amount, "currency": "USD", "denominator": 1_000_000, "unit": "tokens",
                  "source": "synthetic-test-rate", "provenance": "catalog_declared",
                  "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"},
        "output": {"amount": amount * 2, "currency": "USD", "denominator": 1_000_000, "unit": "tokens",
                   "source": "synthetic-test-rate", "provenance": "catalog_declared",
                   "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"},
    }

def test_assignment_decision_preserves_requested_planned_and_unknown_effective_models(tmp_path):
    services, task, registry = _services(tmp_path, profile={"model_routing_profile_id": "costed"})
    catalog = services.system.engine.router.catalog
    from multiagent.catalog import ModelSpec
    catalog.models = {
        "primary": ModelSpec("primary", "one", "configured-primary", "local", True, False,
                              capabilities=frozenset(), roles=frozenset(), permissions=frozenset(),
                              permissions_required=frozenset(), allowed_data_classes=frozenset({"public"}),
                              pricing=_priced(1.0)),
        "fallback": ModelSpec("fallback", "two", "configured-fallback", "local", True, False,
                               capabilities=frozenset(), roles=frozenset(), permissions=frozenset(),
                               permissions_required=frozenset(), allowed_data_classes=frozenset({"public"}),
                               pricing=_priced(0.25)),
    }
    catalog.routing_profiles = {"costed": {
        "version": 1, "enabled": True, "candidate_model_ids": ["primary", "fallback"],
        "max_candidates": 2, "required_capabilities": [], "required_roles": [],
        "required_permissions": [], "allowed_permissions": [], "allowed_data_classes": ["public"],
        "execution_modes": ["auto", "local", "cloud"], "economic_selection": True,
        "max_retries_per_candidate": 0, "max_provider_invocations": 2,
    }}
    services.system.engine.router.adapters = {}
    original = registry.get("review-executor")
    descriptor = ExecutorDescriptor(**{
        **original.to_dict(),
        "model_support": CapabilityEvidence(
            status=SupportState.SUPPORTED, source="synthetic-capability-fixture",
            provenance="catalog_declared", scope="review-executor", values=frozenset({"fallback"}),
        ),
    })

    class ModelAdapter:
        adapter_id = "review-adapter"
        version = "3"

        def describe(self, config):
            return descriptor

    config = services.system.catalog.executor_routing
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, [ModelAdapter()]))
    preview = services.assignment.preview(task["id"],
        {"profile_id": "review", "requested_model": "primary"}, _context())
    decision = preview["decision"]
    assert decision["status"] == "selected"
    assert decision["requested_model"] == "primary"
    assert decision["planned_model"] == "fallback"
    assert decision["effective_model"] is None and decision["observed_model"] is None
    routed = decision["routing_decision"]
    assert routed["requested_model"] == "primary"
    assert routed["selected_model"] == "fallback"
    assert routed["planned_model"] == "configured-fallback"
    assert routed["effective_model"] is None and routed["observed_model"] is None


def test_repeated_accept_cannot_create_second_claim(tmp_path):
    services, task, _ = _services(tmp_path)
    preview = services.assignment.preview(task["id"], {"profile_id": "review"}, _context())
    first = services.assignment.accept(task["id"], preview["preview_token"], True, _context())
    assert first["task"]["state"] == "claimed"
    try:
        services.assignment.accept(task["id"], preview["preview_token"], True, _context())
    except Exception as exc:
        assert getattr(exc, "code", None) == "invalid_state"
    events = services.tasks.events(task_id=task["id"])
    assert [event["type"] for event in events].count("task.claimed") == 1
    assert services.tasks.get_task(task["id"])["state"] == "claimed"


def test_explicit_unknown_t14_profile_blocks_instead_of_using_legacy_model(tmp_path):
    services, task, _ = _services(tmp_path, profile={"model_routing_profile_id": "missing-profile"})
    preview = services.assignment.preview(task["id"],
        {"profile_id": "review", "requested_model": "legacy-model"}, _context())
    decision = preview["decision"]
    assert decision["status"] != "selected"
    assert decision["routing_decision"]["status"] == "blocked"
    assert "unknown_profile" in decision["routing_decision"]["reason_codes"]
    assert decision["planned_model"] is None
    assert decision["effective_model"] is None and decision["observed_model"] is None