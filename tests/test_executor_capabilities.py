"""ORQ-T46: extended executor descriptor (contract ORQ-6 section 2) and the CLI executor declarations."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing import (
    AssignmentRequest, CapabilityEvidence, ExecutorDescriptor, SupportState, select_assignment,
    validate_executor_config, validate_executor_descriptor,
)
from multiagent.routing.executor_capabilities import (
    BASE_EVIDENCE_FIELDS, EXECUTOR_EVIDENCE_FIELDS, descriptor_from_declaration,
)
from multiagent.services.assignment import AssignmentServices

from test_assignment_services import AutomaticAdapter, setup_services, verified_context

CLI_EXECUTORS = ("claude_cli", "codex_cli", "copilot_cli")


def _evidence(status="supported", scope="executor_x", values=("v",), **overrides):
    fields = {"status": status, "source": "P-384 #385/#387/#389/#390", "provenance": "operator_test",
              "scope": scope, "observed_at": "2026-10-10", "values": set(values) if values else None}
    fields.update(overrides)
    return CapabilityEvidence(**fields)


def _bundled(tmp_path: Path) -> Catalog:
    return Catalog(Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                            gemini_api_key="", openai_api_key="", openai_enabled=False))


def _declared(tmp_path: Path) -> dict:
    return _bundled(tmp_path).executor_routing["executors"]


# -- backward compatibility ---------------------------------------------------------------------------------

def test_old_descriptor_stays_valid_with_unknown_new_capabilities():
    descriptor = validate_executor_descriptor(ExecutorDescriptor(
        executor_id="old", surface="codex_app_subagents", mode="automatic",
        model_support=_evidence(values=("m1",)), effort_support=_evidence(values=("low",)),
        context_isolation=_evidence(values=("fresh",))))
    assert descriptor.enabled is True
    for name in EXECUTOR_EVIDENCE_FIELDS:
        evidence = getattr(descriptor, name)
        assert evidence.status is SupportState.UNKNOWN and evidence.values is None and evidence.source is None
    assert set(BASE_EVIDENCE_FIELDS) <= set(descriptor.to_dict())
    assert set(EXECUTOR_EVIDENCE_FIELDS) <= set(descriptor.to_dict())


def test_positional_construction_and_mapping_input_work_as_before():
    # provenance remains the field right after cancellation: the new fields were appended.
    positional = ExecutorDescriptor("e", "s", "2", None, None, "manual")
    assert (positional.executor_id, positional.surface, positional.version, positional.mode) == ("e", "s", "2", "manual")
    from_mapping = validate_executor_descriptor({
        "executor_id": "mapped", "surface": "claude_cli", "mode": "manual",
        "model_support": {"status": "unknown"},
        "resume": {"status": "supported", "source": "P-384 #389", "provenance": "operator_test",
                   "scope": "mapped", "observed_at": "2026-10-10", "values": ["--resume <id>"]},
    })
    assert from_mapping.resume.status is SupportState.SUPPORTED
    assert from_mapping.resume.values == frozenset({"--resume <id>"})
    assert from_mapping.noninteractive_launch.status is SupportState.UNKNOWN


def test_new_fields_round_trip_through_json():
    descriptor = validate_executor_descriptor(ExecutorDescriptor(
        executor_id="rt", surface="codex_cli", mode="manual", enabled=False,
        noninteractive_launch=_evidence(values=("codex exec --json",)),
        structured_output=_evidence(values=("--json",)),
        session_store=_evidence(values=("~/.codex/sessions/**/rollout-*.jsonl",)),
        resume=_evidence(values=("codex exec resume <id>",)),
        blocks_when_open=_evidence(values=("active writer",)),
        open_in_app=_evidence(values=("target:codex://threads/<id>", "live:continue_in_app")),
        quota_signal=_evidence(status="unknown", source=None, provenance="unknown", scope=None,
                               observed_at=None, values=None),
        terms=_evidence(status="unsupported", values=None)))
    restored = ExecutorDescriptor(**json.loads(json.dumps(descriptor.to_dict())))
    assert restored == descriptor
    assert restored.enabled is False
    assert restored.open_in_app.values == frozenset({"target:codex://threads/<id>", "live:continue_in_app"})
    assert restored.terms.status is SupportState.UNSUPPORTED


# -- validation ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", EXECUTOR_EVIDENCE_FIELDS)
def test_claims_need_source_provenance_scope_and_date(name):
    for missing in ("source", "scope", "observed_at"):
        evidence = _evidence(status="unsupported", values=None, **{missing: None})
        with pytest.raises(ValueError, match="requires source, provenance, scope, and observed_at"):
            validate_executor_descriptor(ExecutorDescriptor(executor_id="x", surface="s", **{name: evidence}))
    with pytest.raises(ValueError, match="requires source"):
        validate_executor_descriptor(ExecutorDescriptor(
            executor_id="x", surface="s", **{name: _evidence(status="unsupported", values=None, provenance="unknown")}))


@pytest.mark.parametrize("name", [n for n in EXECUTOR_EVIDENCE_FIELDS if n != "blocks_when_open"])
def test_supported_capability_needs_explicit_values(name):
    with pytest.raises(ValueError, match=f"supported {name} requires explicit values"):
        validate_executor_descriptor(ExecutorDescriptor(
            executor_id="x", surface="s", **{name: _evidence(values=None)}))


def test_blocks_when_open_may_be_supported_without_values_and_unknown_needs_nothing():
    validate_executor_descriptor(ExecutorDescriptor(
        executor_id="x", surface="s", blocks_when_open=_evidence(values=None)))
    validate_executor_descriptor(ExecutorDescriptor(executor_id="x", surface="s", quota_signal=CapabilityEvidence()))


def test_enabled_must_be_a_boolean():
    with pytest.raises(ValueError, match="enabled"):
        validate_executor_descriptor(ExecutorDescriptor(executor_id="x", surface="s", enabled="false"))


def test_config_validation_rejects_malformed_declarations():
    def config(**entry):
        return {"version": 1, "executors": {"x": {"surface": "x", **entry}}}

    validate_executor_config(config(enabled=False, resume={"status": "unknown"}))
    with pytest.raises(ValueError, match="enabled"):
        validate_executor_config(config(enabled="no"))
    with pytest.raises(ValueError, match="mapping"):
        validate_executor_config(config(resume="--resume"))
    # An unquoted YAML date would be a datetime.date: not JSON-safe for the config hash.
    import datetime
    with pytest.raises(ValueError, match="quoted string"):
        validate_executor_config(config(resume={"status": "unsupported", "source": "s", "provenance": "operator_test",
                                                "scope": "x", "observed_at": datetime.date(2026, 10, 10)}))
    with pytest.raises(ValueError, match="requires explicit values"):
        validate_executor_config(config(resume={"status": "supported", "source": "s", "provenance": "operator_test",
                                                "scope": "x", "observed_at": "2026-10-10"}))
    with pytest.raises(ValueError):
        validate_executor_config(config(resume={"status": "maybe"}))
    with pytest.raises(ValueError, match="invalid"):
        validate_executor_config(config(resume={"status": "unknown", "bogus": 1}))


# -- catalog declarations -----------------------------------------------------------------------------------

def test_bundled_catalog_declares_the_cli_executors_disabled(tmp_path):
    catalog = _bundled(tmp_path)
    config = validate_executor_config(catalog.executor_routing)
    registry = ExecutorRegistry(config)
    for executor_id in CLI_EXECUTORS:
        declaration = config["executors"][executor_id]
        assert declaration["enabled"] is False
        assert declaration["surface"] == executor_id and declaration["adapter_id"] is None
        descriptor = registry.get(executor_id)
        assert descriptor.enabled is False and descriptor.mode == "manual" and descriptor.adapter_id is None
        assert descriptor.provenance == "catalog_declared"
        for name in (*BASE_EVIDENCE_FIELDS, *EXECUTOR_EVIDENCE_FIELDS):
            evidence = getattr(descriptor, name)
            if evidence.status is SupportState.UNKNOWN:
                continue
            assert evidence.source.startswith("P-384 #")
            assert evidence.provenance == "operator_test" and evidence.observed_at in {"2026-10-10", "2026-10-11"}
            assert evidence.scope == executor_id
        for name in ("noninteractive_launch", "structured_output", "session_store", "resume", "open_in_app"):
            assert getattr(descriptor, name).status is SupportState.SUPPORTED
            assert getattr(descriptor, name).values
        # Not probed in P-384: stay unknown.
        assert descriptor.quota_signal.status is SupportState.UNKNOWN
        assert descriptor.terms.status is SupportState.UNKNOWN
    # The existing manual declarations are untouched and still load.
    for executor_id in ("codex_app_subagents", "claude_code", "claude_app", "chatgpt_app"):
        assert registry.get(executor_id).enabled is True and registry.get(executor_id).mode == "manual"


def test_cli_evidence_matches_the_contract_table(tmp_path):
    registry = ExecutorRegistry(validate_executor_config(_bundled(tmp_path).executor_routing))
    claude, codex, copilot = (registry.get(name) for name in ("claude_cli", "codex_cli", "copilot_cli"))
    assert "claude -p --output-format json" in claude.noninteractive_launch.values
    assert "codex exec --json" in codex.noninteractive_launch.values
    assert "copilot -p --output-format=json --no-ask-user" in copilot.noninteractive_launch.values
    assert "--resume <id>" in claude.resume.values
    assert "codex exec resume <id>" in codex.resume.values
    assert "--resume=<id>" in copilot.resume.values
    assert "~/.claude/projects/**/<id>.jsonl" in claude.session_store.values
    assert "~/.codex/sessions/**/rollout-*.jsonl" in codex.session_store.values
    assert "~/.copilot/session-state/<id>/" in copilot.session_store.values
    # Only Codex blocks writes while the session is open in the app.
    assert claude.blocks_when_open.status is SupportState.UNSUPPORTED
    assert codex.blocks_when_open.status is SupportState.SUPPORTED
    assert copilot.blocks_when_open.status is SupportState.UNSUPPORTED
    assert "target:claude --desktop --resume <id>" in claude.open_in_app.values
    assert "target:codex://threads/<id>" in codex.open_in_app.values
    assert "target:ghapp://sessions/<id>" in copilot.open_in_app.values
    assert "confirmation:required" in copilot.open_in_app.values
    # Effort: Claude was not tested; Codex and Copilot were.
    assert claude.effort_support.status is SupportState.UNKNOWN
    assert codex.effort_support.status is SupportState.SUPPORTED and copilot.effort_support.status is SupportState.SUPPORTED


def test_declaration_without_adapter_never_becomes_automatic_even_if_enabled():
    declaration = {"surface": "claude_cli", "enabled": True,
                   "noninteractive_launch": {"status": "supported", "source": "P-384 #385", "provenance": "operator_test",
                                             "scope": "claude_cli", "observed_at": "2026-10-10", "values": ["claude -p"]}}
    descriptor = descriptor_from_declaration("claude_cli", declaration)
    assert descriptor.mode == "manual" and descriptor.enabled is True
    result = select_assignment(
        AssignmentRequest(task_id="T-1", profile_id="p"),
        profile={"enabled": True, "version": 1, "executor_ids": ["claude_cli"]},
        descriptors={"claude_cli": descriptor}, routing_decision=None, grants={})
    assert result.status == "manual" and result.selected_executor is None
    assert "executor_not_automatic" in result.reason_codes


# -- enabled flag in registry and selector ------------------------------------------------------------------

def test_disabled_declaration_overrides_an_adapter_and_selector_skips_it():
    adapter = AutomaticAdapter()
    base = {"surface": "codex_app_subagents", "adapter_id": "synthetic"}
    profile = {"enabled": True, "version": 1, "executor_ids": ["synthetic_executor"], "permission_ceiling": [],
               "allowed_data_classes": ["public"], "execution_modes": ["AUTO"]}

    def decide(declaration):
        registry = ExecutorRegistry({"version": 1, "executors": {"synthetic_executor": declaration}}, [adapter])
        descriptor = registry.get("synthetic_executor")
        decision = select_assignment(
            AssignmentRequest(task_id="T-1", profile_id="p", data_class="public"), profile=profile,
            descriptors=registry.snapshot(), routing_decision=None, grants={})
        return descriptor, decision

    descriptor, decision = decide(base)  # no ``enabled``: unchanged behavior
    assert descriptor.enabled is True and decision.selected_executor == "synthetic_executor"
    descriptor, decision = decide({**base, "enabled": True})
    assert descriptor.enabled is True and decision.selected_executor == "synthetic_executor"
    for flag in (False, "yes", 0):  # anything but a literal true disables
        descriptor, decision = decide({**base, "enabled": flag})
        assert descriptor.enabled is False
        assert decision.selected_executor is None and decision.status == "manual"
        assert "executor_disabled" in decision.reason_codes


def test_adapter_descriptor_that_is_disabled_stays_disabled():
    class DisabledAdapter(AutomaticAdapter):
        def describe(self, config):
            return ExecutorDescriptor(**{**super().describe(config).to_dict(), "enabled": False})

    registry = ExecutorRegistry({"version": 1, "executors": {"synthetic_executor": {
        "surface": "codex_app_subagents", "adapter_id": "synthetic"}}}, [DisabledAdapter()])
    assert registry.get("synthetic_executor").enabled is False


# -- assignment preview keeps working -----------------------------------------------------------------------

def test_assignment_preview_never_selects_the_disabled_cli_executors(tmp_path):
    services, task = setup_services(tmp_path)
    config = services.system.catalog.executor_routing
    config["executors"].update(_declared(tmp_path / "bundled"))
    config["profiles"]["cli_only"] = {"version": 1, "enabled": True, "executor_ids": list(CLI_EXECUTORS),
                                      "permission_ceiling": [], "allowed_data_classes": ["public"]}
    config["profiles"]["mixed"] = {"version": 1, "enabled": True,
                                   "executor_ids": [*CLI_EXECUTORS, "synthetic_executor"],
                                   "permission_ceiling": [], "allowed_data_classes": ["public"]}
    validate_executor_config(config)
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, [AutomaticAdapter()]))

    only = services.assignment.preview(task["id"], {"profile_id": "cli_only"}, verified_context())["decision"]
    assert only["status"] == "manual" and only["selected_executor"] is None and only["planned_executor"] is None
    assert only["reason_codes"] == ["executor_disabled"]

    requested = services.assignment.preview(
        task["id"], {"profile_id": "cli_only", "requested_executor": "codex_cli"}, verified_context())["decision"]
    assert requested["selected_executor"] is None and requested["status"] == "blocked"
    assert "executor_disabled" in requested["reason_codes"]
    assert "requested_executor_not_selected" in requested["reason_codes"]

    # Requested model/effort cannot make a disabled executor eligible either.
    model = services.assignment.preview(
        task["id"], {"profile_id": "cli_only", "requested_executor": "codex_cli", "requested_model": "gpt-6.1-sol",
                     "requested_effort": "ultra"}, verified_context())["decision"]
    assert model["selected_executor"] is None

    mixed = services.assignment.preview(task["id"], {"profile_id": "mixed"}, verified_context())
    assert mixed["decision"]["status"] == "selected"
    assert mixed["decision"]["selected_executor"] == "synthetic_executor"
    for executor_id in CLI_EXECUTORS:  # evidence is part of the audited snapshot, only through the preview token
        assert executor_id in mixed["decision"]["descriptor_hashes"]
    accepted = services.assignment.accept(task["id"], mixed["preview_token"], True, verified_context())
    assert accepted["decision"]["selected_executor"] == "synthetic_executor"
    assert accepted["task"]["state"] == "claimed"
