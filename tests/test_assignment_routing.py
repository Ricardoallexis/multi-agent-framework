from multiagent.routing import (
    AssignmentRequest,
    CapabilityEvidence,
    ExecutorDescriptor,
    SupportState,
    select_assignment,
    validate_executor_config,
    validate_executor_descriptor,
)
from multiagent.executors import ExecutorRegistry


def _descriptor(**overrides):
    fields = {
        "executor_id": "executor_a",
        "surface": "codex_app_subagents",
        "mode": "automatic",
        "capabilities_available": {"text"},
        "roles_available": {"worker"},
        "permissions_available": {"provider:cloud"},
        "permissions_required": {"provider:cloud"},
        "permissions_consumed": {"provider:cloud"},
        "permission_ceiling": {"provider:cloud"},
        "allowed_data_classes": {"non_sensitive"},
        "execution_modes": {"local"},
        "model_support": CapabilityEvidence(
            SupportState.SUPPORTED, source="adapter", provenance="adapter_declared",
            scope="executor_a", observed_at="2026-10-09T00:00:00+00:00",
            values={"model-a"},
        ),
        "effort_support": CapabilityEvidence(
            SupportState.SUPPORTED, source="adapter", provenance="adapter_declared",
            scope="executor_a", observed_at="2026-10-09T00:00:00+00:00",
            values={"low", "high"},
        ),
        "context_isolation": CapabilityEvidence(
            SupportState.SUPPORTED, source="adapter", provenance="adapter_declared",
            scope="executor_a", values={"fresh"},
        ),
    }
    fields.update(overrides)
    return ExecutorDescriptor(**fields)


def test_unknown_or_catalog_only_descriptors_never_select_automatically():
    descriptor = _descriptor(mode="manual")
    request = AssignmentRequest(task_id="T-1", profile_id="p")
    result = select_assignment(
        request, profile={"enabled": True, "version": 1, "executor_ids": ["executor_a"]},
        descriptors={"executor_a": descriptor}, routing_decision=None, grants={},
    )
    assert result.status == "manual"
    assert "executor_not_automatic" in result.reason_codes
    assert result.effective_executor is None
    assert result.observed_executor is None


def test_selector_requires_permission_grant_and_profile_ceiling():
    request = AssignmentRequest(task_id="T-1", profile_id="p", required_permissions={"provider:cloud"}, data_class="non_sensitive")
    profile = {"enabled": True, "version": 2, "executor_ids": ["executor_a"],
               "permission_ceiling": ["provider:cloud"], "allowed_data_classes": ["non_sensitive"], "execution_modes": ["local"]}
    denied = select_assignment(request, profile=profile, descriptors={"executor_a": _descriptor()},
                               routing_decision=None, grants={"effective_permissions": [],
                                                             "permission_ceiling": ["provider:cloud"]})
    assert denied.status == "manual"
    assert "permissions_not_granted_and_allowed" in denied.reason_codes
    allowed = select_assignment(request, profile=profile, descriptors={"executor_a": _descriptor()},
                                routing_decision=None, grants={"effective_permissions": ["provider:cloud"],
                                                              "permission_ceiling": ["provider:cloud"]})
    assert allowed.status == "selected"
    assert allowed.selected_executor == "executor_a"
    assert allowed.effective_executor is None


def test_required_model_effort_context_and_private_data_fail_closed_when_unknown():
    request = AssignmentRequest(task_id="T-1", profile_id="p", requested_model="model-a",
                                requested_effort="low", required_context_isolation="fresh",
                                data_class="sensitive")
    profile = {"enabled": True, "executor_ids": ["executor_a"],
               "allowed_data_classes": ["sensitive"], "permission_ceiling": []}
    result = select_assignment(request, profile=profile, descriptors={"executor_a": _descriptor(allowed_data_classes=None)},
                               routing_decision=None, grants={"effective_permissions": [],
                                                             "permission_ceiling": []})
    assert result.status == "manual"
    assert "data_class_not_allowed" not in result.reason_codes
    assert "privacy_class_unknown" in result.reason_codes


def test_planned_fields_are_not_effective_or_observed_and_snapshot_is_stable():
    request = AssignmentRequest(task_id="T-1", profile_id="p", requested_model="model-a",
                                requested_effort="low", required_context_isolation="fresh", data_class="non_sensitive")
    profile = {"enabled": True, "version": "v1", "executor_ids": ["executor_a"],
               "permission_ceiling": [], "allowed_data_classes": ["non_sensitive"], "execution_modes": ["local"]}
    descriptor = _descriptor(permissions_required=frozenset(), permissions_consumed=frozenset(),
                             permission_ceiling=frozenset())
    grants = {"effective_permissions": [], "permission_ceiling": []}
    first = select_assignment(request, profile=profile, descriptors={"executor_a": descriptor},
                              routing_decision=None, grants=grants)
    again = select_assignment(request, profile=profile, descriptors={"executor_a": descriptor},
                              routing_decision=None, grants=grants)
    assert first.status == "selected"
    assert first.planned_executor == "executor_a"
    assert first.effective_executor is None and first.observed_executor is None
    assert first.effective_model is None and first.observed_model is None
    assert first.to_dict() == again.to_dict()


def test_preset_registry_is_manual_and_config_is_valid():
    config = {
        "version": 1, "profiles": {},
        "executors": {
            "codex": {"surface": "codex_app_subagents", "adapter_id": None},
            "claude": {"surface": "claude_code", "adapter_id": None},
            "copilot": {"surface": "copilot_cli", "adapter_id": None},
        },
        "requirement_map": {}, "operator_boundary": {},
    }
    normalized = validate_executor_config(config)
    registry = ExecutorRegistry(normalized)
    assert set(registry.snapshot()) == {"codex", "claude", "copilot"}
    assert all(item.mode == "manual" for item in registry.snapshot().values())


def test_descriptor_rejects_supported_model_without_explicit_values():
    try:
        validate_executor_descriptor(_descriptor(model_support=CapabilityEvidence(SupportState.SUPPORTED)))
    except ValueError as exc:
        assert "explicit model IDs" in str(exc)
    else:
        raise AssertionError("supported model selection without allowlisted IDs was accepted")
def test_t14_planned_model_must_be_supported_by_executor():
    from types import SimpleNamespace

    request = AssignmentRequest(task_id="T-2", profile_id="p", requested_model="model-a", data_class="non_sensitive")
    profile = {"enabled": True, "executor_ids": ["executor_a"], "permission_ceiling": [], "allowed_data_classes": ["non_sensitive"]}
    route = SimpleNamespace(status="selected", selected_model="model-b")
    result = select_assignment(request, profile=profile, descriptors={"executor_a": _descriptor(
        permissions_required=frozenset(), permissions_consumed=frozenset(),
        permission_ceiling=frozenset(),
    )}, routing_decision=route,
        grants={"effective_permissions": [], "permission_ceiling": []})
    assert result.status == "manual"
    assert result.requested_model == "model-a"
    assert result.planned_model == "model-b"
    assert "planned_model_not_supported" in result.reason_codes
def test_configured_neutral_surface_is_extensible_but_empty_surface_is_rejected():
    config = {
        "version": 1, "profiles": {},
        "executors": {"local_sandbox": {"surface": "sandbox_v2", "adapter_id": None}},
        "requirement_map": {}, "operator_boundary": {},
    }
    normalized = validate_executor_config(config)
    descriptor = ExecutorRegistry(normalized).snapshot()["local_sandbox"]
    assert descriptor.surface == "sandbox_v2"
    assert descriptor.mode == "manual"

    config["executors"]["local_sandbox"]["surface"] = ""
    try:
        validate_executor_config(config)
    except ValueError as exc:
        assert "nonempty surface ID" in str(exc)
    else:
        raise AssertionError("empty surface ID was accepted")

def test_supported_capability_requires_scoped_provenance():
    bad = _descriptor(model_support=CapabilityEvidence(
        SupportState.SUPPORTED, values={"model-a"},
    ))
    try:
        validate_executor_descriptor(bad)
    except ValueError as exc:
        assert "source, provenance, and scope" in str(exc)
    else:
        raise AssertionError("unscoped capability evidence was accepted")
def test_permission_grant_does_not_imply_executor_can_supply_permission():
    request = AssignmentRequest(task_id="T-3", profile_id="p", required_permissions={"provider:cloud"},
                                data_class="non_sensitive")
    profile = {"enabled": True, "executor_ids": ["executor_a"],
               "permission_ceiling": ["provider:cloud"], "allowed_data_classes": ["non_sensitive"]}
    descriptor = _descriptor(permissions_available=frozenset(), allowed_data_classes=frozenset({"non_sensitive"}))
    result = select_assignment(
        request, profile=profile, descriptors={"executor_a": descriptor}, routing_decision=None,
        grants={"effective_permissions": ["provider:cloud"], "permission_ceiling": ["provider:cloud"]},
    )
    assert result.status == "manual"
    assert "executor_permission_unavailable" in result.reason_codes
