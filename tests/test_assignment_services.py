from __future__ import annotations

from pathlib import Path

from multiagent.config import Settings
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing.executor_capabilities import ExecutorDescriptor, VerifiedOperatorContext
from multiagent.services import ApplicationServices
from multiagent.services.assignment import AssignmentServices


class AutomaticAdapter:
    adapter_id = "synthetic"
    version = "1"

    def describe(self, config):
        return ExecutorDescriptor(
            executor_id="synthetic_executor", surface="codex_app_subagents", version="1",
            adapter_id=self.adapter_id, adapter_version=self.version, mode="automatic",
            capabilities_available=frozenset(), roles_available=frozenset(),
            permissions_available=frozenset(), permissions_required=frozenset(),
            permissions_consumed=frozenset(), permission_ceiling=frozenset(),
            allowed_data_classes=frozenset({"public"}), execution_modes=frozenset({"AUTO"}),
        )


def setup_services(tmp_path: Path, *, requirements=()):
    settings = Settings(
        _env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
        gemini_api_key="", openai_api_key="", openai_enabled=False,
    )
    services = ApplicationServices.create(settings)
    config = {
        "version": 1,
        "profiles": {"auto": {"version": 1, "enabled": True,
                               "executor_ids": ["synthetic_executor"],
                               "permission_ceiling": [], "allowed_data_classes": ["public"]}},
        "executors": {"synthetic_executor": {"surface": "codex_app_subagents",
                                               "adapter_id": "synthetic"}},
        "requirement_map": {label: {"capabilities": [], "permissions": []} for label in requirements},
        "data_classifications": {"group:ORQ": "public"},
        "operator_boundary": {
            "operator_id": "ops", "authorized_aliases": ["agent:verified"],
            "claim_actor_alias": "agent:verified", "operator_grants": [],
            "alias_grants": {"agent:verified": []},
        },
    }
    registry = ExecutorRegistry(config, [AutomaticAdapter()])
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.system.catalog.executor_routing_hash = __import__("hashlib").sha256(
        __import__("json").dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                   default=str).encode("utf-8")).hexdigest()
    services.assignment = AssignmentServices(services, registry=registry)
    group = services.tasks.create_group("ORQ", "Authorized", actor="test", authorized=True)
    task = services.tasks.create_task("ORQ", "ORQ-A", "Assignment target", actor="test",
                                      requirements=requirements, file_scope=["src"])
    return services, task


def verified_context(data_class="public", scope="group:ORQ"):
    return VerifiedOperatorContext("ops", "agent:verified", grants=frozenset(),
                                   provenance="synthetic_test_provider", verified=True,
                                   trusted_data_class=data_class, data_class_scope=scope)


def test_preview_is_read_only_and_accept_requires_request_scoped_verified_context(tmp_path):
    services, task = setup_services(tmp_path)
    before = services.tasks.get_task(task["id"])
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    after = services.tasks.get_task(task["id"])

    assert preview["decision"]["status"] == "selected"
    assert preview["decision_id"]
    assert before == after
    anonymous = services.assignment.accept(task["id"], preview["preview_token"], True)
    assert anonymous["decision"]["status"] == "manual"
    assert "operator_context_unverified" in anonymous["decision"]["reason_codes"]
    assert services.tasks.get_task(task["id"])["state"] == "open"


def test_accept_claims_only_after_revalidation_and_claim_uses_context_alias(tmp_path):
    services, task = setup_services(tmp_path)
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    accepted = services.assignment.accept(task["id"], preview["preview_token"], True, verified_context())

    assert accepted["decision"]["status"] == "selected"
    assert accepted["task"]["state"] == "claimed"
    assert accepted["task"]["assignee"] == "agent:verified"
    events = services.tasks.events(task_id=task["id"])
    assert [event["type"] for event in events] == ["task.created", "task.claimed"]
    assert events[-1]["actor"] == "agent:verified"


def test_stale_revision_and_cancelled_task_reject_the_preview(tmp_path):
    services, task = setup_services(tmp_path)
    stale = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    services.envelopes.set_task_context(task["id"], actor="test", constraints=["changed"])
    try:
        services.assignment.accept(task["id"], stale["preview_token"], True, verified_context())
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_state"
        assert error.details.get("reason") == "assignment_preview_stale"
    else:
        raise AssertionError("changed task revision must stale the preview")

    cancelled, other = setup_services(tmp_path / "cancelled")
    preview = cancelled.assignment.preview(other["id"], {"profile_id": "auto"}, verified_context())
    cancelled.tasks.transition(other["id"], "cancel", actor="test")
    try:
        cancelled.assignment.accept(other["id"], preview["preview_token"], True, verified_context())
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_state"
    else:
        raise AssertionError("cancelled tasks must not be claimed from an old preview")


def test_task_substitution_and_unknown_canonical_requirement_fail_closed(tmp_path):
    services, first = setup_services(tmp_path)
    second = services.tasks.create_task("ORQ", "ORQ-B", "Other target", actor="test")
    preview = services.assignment.preview(first["id"], {"profile_id": "auto"}, verified_context())
    try:
        services.assignment.accept(second["id"], preview["preview_token"], True, verified_context())
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_state"
    else:
        raise AssertionError("a token cannot be substituted onto another task")

    unknown, _ = setup_services(tmp_path / "unknown", requirements=["unmapped-label"])
    unknown.system.catalog.executor_routing["requirement_map"].pop("unmapped-label")
    blocked = unknown.assignment.preview("ORQ-A", {"profile_id": "auto"}, verified_context())
    assert blocked["decision"]["status"] == "blocked"
    assert "requirement_mapping_unknown" in blocked["decision"]["reason_codes"]


def test_request_cannot_relax_envelope_constraints_and_envelope_change_stales_token(tmp_path):
    services, task = setup_services(tmp_path)
    services.envelopes.set_task_context(task["id"], actor="test", constraints=["no network"])
    preview = services.assignment.preview(
        task["id"],
        {"profile_id": "auto", "constraints": {"envelope_constraints": []}},
        verified_context(),
    )
    constraints = preview["decision"]["request"]["constraints"]
    assert constraints["envelope_constraints"] == ["no network"]
    assert constraints["request_constraints"] == {"envelope_constraints": []}
    assert preview["decision"]["status"] == "blocked"
    assert "request_constraints_unknown" in preview["decision"]["reason_codes"]

    canonical_preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    services.envelopes.set_task_context(task["id"], actor="test", constraints=["network disabled"])
    try:
        services.assignment.accept(task["id"], canonical_preview["preview_token"], True, verified_context())
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_state"
        assert error.details.get("reason") == "assignment_preview_stale"
    else:
        raise AssertionError("a changed envelope must invalidate the canonical preview")


def test_canonical_unmapped_envelope_constraints_block_claim(tmp_path):
    services, task = setup_services(tmp_path)
    services.envelopes.set_task_context(task["id"], actor="test", constraints=["no network"])
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    assert preview["decision"]["status"] == "blocked"
    assert "canonical_constraints_unmapped" in preview["decision"]["reason_codes"]
    accepted = services.assignment.accept(task["id"], preview["preview_token"], True, verified_context())
    assert accepted["task"] is None
    assert services.tasks.get_task(task["id"])["state"] == "open"

def test_request_cannot_supply_actor_grants_or_a_precomputed_decision(tmp_path):
    services, task = setup_services(tmp_path)
    for forged in ({"profile_id": "auto", "actor": "agent:verified"},
                   {"profile_id": "auto", "grants": ["cloud"]},
                   {"profile_id": "auto", "decision": {"status": "selected"}}):
        try:
            services.assignment.preview(task["id"], forged)
        except Exception as error:
            assert getattr(error, "code", None) == "invalid_request"
        else:
            raise AssertionError(f"forged request fields must be rejected: {forged}")


def test_canonical_and_additional_requirements_are_mapped_as_exact_union(tmp_path):
    services, _ = setup_services(tmp_path, requirements=["canonical-build"])
    services.system.catalog.executor_routing["requirement_map"] = {
        "canonical-build": {"capabilities": ["compile"], "roles": ["builder"], "permissions": []},
        "review-change": {"capabilities": ["review"], "roles": [], "permissions": ["read"]},
    }
    decision = services.assignment.preview(
        "ORQ-A", {"profile_id": "auto", "additional_requirements": ["review-change"]}, verified_context()
    )["decision"]
    assert decision["request"]["canonical_requirements"] == ["canonical-build"]
    assert decision["request"]["additional_requirements"] == ["review-change"]
    assert decision["request"]["required_capabilities"] == ["compile", "review"]
    assert decision["request"]["required_roles"] == ["builder"]
    assert decision["request"]["required_permissions"] == ["read"]


def test_same_verified_alias_file_overlap_is_rejected_before_claim(tmp_path):
    services, first = setup_services(tmp_path)
    services.tasks.transition(first["id"], "claim", actor="agent:verified")
    second = services.tasks.create_task("ORQ", "ORQ-SECOND", "Second", actor="test", file_scope=["src"])
    preview = services.assignment.preview(second["id"], {"profile_id": "auto"}, verified_context())
    try:
        services.assignment.accept(second["id"], preview["preview_token"], True, verified_context())
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_state"
        assert error.details.get("task_id") == first["id"]
    else:
        raise AssertionError("same-alias file overlap must be rejected before TaskServices claim")
    assert services.tasks.get_task(second["id"])["state"] == "open"


def test_unverified_accept_never_echoes_forged_token_decision(tmp_path):
    services, task = setup_services(tmp_path)
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    payload = services.assignment._decode_token(preview["preview_token"])
    payload["decision"].update({"status": "selected", "selected_executor": "forged",
                                "effective_executor": "forged", "observed_model": "forged-model"})
    forged_token = services.assignment._encode_token(payload)

    for acknowledge, context in ((False, verified_context()), (True, None)):
        result = services.assignment.accept(task["id"], forged_token, acknowledge, context)
        assert result["decision"]["status"] == "manual"
        assert result["decision"].get("selected_executor") is None
        assert result["decision"].get("effective_executor") is None
        assert result["decision"].get("observed_model") is None
        assert result["task"] is None
        assert services.tasks.get_task(task["id"])["state"] == "open"

def test_body_class_cannot_classify_unknown_task(tmp_path):
    services, task = setup_services(tmp_path)
    services.system.catalog.executor_routing["data_classifications"] = {}
    preview = services.assignment.preview(
        task["id"], {"profile_id": "auto", "data_class": "non_sensitive"},
        verified_context(None, None),
    )
    assert preview["decision"]["status"] == "blocked"
    assert "privacy_class_unknown" in preview["decision"]["reason_codes"]
    accepted = services.assignment.accept(task["id"], preview["preview_token"], True,
                                          verified_context(None, None))
    assert accepted["task"] is None
    assert services.tasks.get_task(task["id"])["state"] == "open"


def test_body_public_class_cannot_lower_trusted_sensitive_class(tmp_path):
    services, task = setup_services(tmp_path)
    services.system.catalog.executor_routing["data_classifications"]["group:ORQ"] = "sensitive"
    context = verified_context("sensitive", "group:ORQ")
    preview = services.assignment.preview(
        task["id"], {"profile_id": "auto", "data_class": "public"}, context
    )
    assert preview["decision"]["status"] == "blocked"
    assert "data_class_mismatch" in preview["decision"]["reason_codes"]
    result = services.assignment.accept(task["id"], preview["preview_token"], True, context)
    assert result["task"] is None
    assert services.tasks.get_task(task["id"])["state"] == "open"


def test_matching_trusted_scope_and_class_allows_assignment(tmp_path):
    services, task = setup_services(tmp_path)
    services.system.catalog.executor_routing["data_classifications"] = {}
    preview = services.assignment.preview(
        task["id"], {"profile_id": "auto", "data_class": "public"},
        verified_context("public", "group:ORQ"),
    )
    assert preview["decision"]["status"] == "selected"
    accepted = services.assignment.accept(task["id"], preview["preview_token"], True,
                                          verified_context("public", "group:ORQ"))
    assert accepted["task"]["state"] == "claimed"


def test_verified_context_conflicting_with_config_classification_blocks(tmp_path):
    services, task = setup_services(tmp_path)
    context = verified_context("sensitive", f"task:{task['id']}")
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, context)
    assert preview["decision"]["status"] == "blocked"
    assert "privacy_class_conflict" in preview["decision"]["reason_codes"]


def test_config_scoped_classification_suffices_with_verified_identity_context(tmp_path):
    services, task = setup_services(tmp_path)
    context = verified_context(None, None)
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, context)
    assert preview["decision"]["status"] == "selected"
    result = services.assignment.accept(task["id"], preview["preview_token"], True, context)
    assert result["task"]["state"] == "claimed"


def test_conflicting_task_and_group_classifications_block(tmp_path):
    services, task = setup_services(tmp_path)
    services.system.catalog.executor_routing["data_classifications"][f"task:{task['id']}"] = "sensitive"
    context = verified_context("sensitive", f"task:{task['id']}")
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, context)
    assert preview["decision"]["status"] == "blocked"
    assert "privacy_class_conflict" in preview["decision"]["reason_codes"]


def test_trusted_config_change_stales_preview(tmp_path):
    services, task = setup_services(tmp_path)
    preview = services.assignment.preview(task["id"], {"profile_id": "auto"}, verified_context())
    services.system.catalog.executor_routing["profiles"]["auto"]["permission_ceiling"] = ["changed"]
    try:
        services.assignment.accept(task["id"], preview["preview_token"], True, verified_context())
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_state"
        assert error.details.get("reason") == "assignment_preview_stale"
    else:
        raise AssertionError("a changed trusted config must invalidate the preview")
