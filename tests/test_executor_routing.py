# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ECO-T08: the model and effort an executor application is asked to use come from the routing profile.

A priced fake catalog and fake executors: nothing is launched (``launch`` is replaced by a recorder that keeps the
``ExecutorLaunchRequest`` it was given), so no real Claude, Codex or Copilot ever starts. The adapters' own request
checks (``build_command``) are the real ones. Covers: model and effort from the economic profile reach the launch
request, an effort or model the executor does not declare is dropped with a reason (Claude's effort is unknown, so it
is never sent), Copilot never receives a Claude model, the caller's values always win, the assignment preview shows the
same selection for each executor, and the evidence of the choice is recorded on the attempt.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.executors import process
from multiagent.executors.claude import ClaudeAdapter
from multiagent.executors.contract import CommandRecord, ExecutorLaunchRequest, ExecutorRunResult, RunStatus
from multiagent.executors.copilot import CopilotAdapter
from multiagent.executors.registry import ExecutorRegistry
from multiagent.executors.selection import (
    ADAPTER_REFUSES, NOT_DECLARED, SOURCE_PROFILE, SOURCE_SESSION, SOURCE_USER, model_exclusions, select_for_executor,
)
from multiagent.routing.executor_capabilities import (
    CapabilityEvidence, ExecutorDescriptor, SupportState, VerifiedOperatorContext, descriptor_from_declaration,
)
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.assignment import AssignmentServices
from multiagent.tasks.envelopes import TASK_RESULT_SCHEMA

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
RESULT = {"schema": TASK_RESULT_SCHEMA, "base_commit": "", "patch": None, "summary": "Did the work.",
          "decisions": [], "contract_changes": "", "tests_not_run": [], "requests": []}
RATE_SOURCE = "unit-test rate card"


@pytest.fixture(autouse=True)
def never_a_real_cli(monkeypatch):
    real = process.find_executable

    def guard(name, **kwargs):
        if name in ("claude", "codex", "copilot"):
            raise AssertionError(f"the bare name {name!r} was looked up: a test would start the real program")
        return real(name, **kwargs)

    monkeypatch.setattr(process, "find_executable", guard)


# --------------------------------------------------------------------------- a priced fake catalog

def _side(amount):
    return {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD", "source": RATE_SOURCE,
            "provenance": "catalog_declared", "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"}


def _spec(model_id, name, level, amount):
    """A local, classified, priced model: input at ``amount`` and output at twice that (cheaper is cheaper on both)."""
    return ModelSpec(
        model_id, "ollama", name, "local", True, False, capabilities=frozenset({"text_generation"}),
        roles=frozenset({"assistant"}), permissions=frozenset(), permissions_required=frozenset(),
        allowed_data_classes=None, pricing={"input": _side(amount), "output": _side(amount * 2)}, level=level,
        classification="classified")


MODELS = {spec.id: spec for spec in (
    _spec("other", "other-cheap", "T1", 0.05),            # the cheapest of all, but no executor below declares it
    _spec("fake_small", "fake-small", "T1", 0.1),
    _spec("fake_mid", "fake-mid", "T2", 0.3),
    _spec("fake_big", "fake-big", "T3", 0.9),
    _spec("claude_cat", "claude-sonnet-5.5", "T1", 0.02),   # a Claude model: the cheapest of the work models
    _spec("claude_haiku", "claude-haiku-5", "T1", 0.06),
    _spec("luna", "gpt-6-luna", "T2", 0.25),
)}

ECONOMIC = {
    "version": 1, "enabled": True, "mode": "supervised",
    "levels": {"orchestration": {"min": "T3"}, "planning": {"min": "T3"}, "review": {"min": "T3"},
               "work": {"max": "T2"}},
    "effort": {"work": "low", "review": "medium"}, "economic_selection": True,
    "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
    "allowed_permissions": [], "execution_modes": ["auto", "local", "cloud"], "require_health": False,
    "max_retries_per_candidate": 0,
}
LEGACY = {      # a routing profile with no economic fields: the first eligible listed candidate
    "version": 1, "enabled": True, "candidate_model_ids": ["other", "fake_small"], "max_candidates": 2,
    "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
    "allowed_permissions": [], "execution_modes": ["auto", "local", "cloud"], "require_health": False,
    "economic_selection": False, "max_retries_per_candidate": 0, "max_provider_invocations": 2,
}


def _evidence(*values):
    return {"status": "supported", "source": "unit test", "provenance": "operator_test", "scope": "test",
            "observed_at": "2026-10-11", "values": list(values)}


def _declaration(executor_id, *, models=None, efforts=None, automatic=False):
    declaration = {"surface": executor_id, "adapter_id": executor_id, "enabled": True}
    if models:
        declaration["model_support"] = _evidence(*models)
    if efforts:
        declaration["effort_support"] = _evidence(*efforts)
    if automatic:
        declaration["noninteractive_launch"] = _evidence("fake launch")
    return declaration


# --------------------------------------------------------------------------- fake executors

class Recorder:
    """Replaces ``launch``/``resume``: keeps the request it was given and answers with a valid result envelope."""

    outcome = RunStatus.COMPLETED

    def _result(self, request, session_id):
        self.requests.append(request)
        command = CommandRecord(argv=(sys.executable,), cwd=str(request.workdir), started_at=NOW.isoformat(),
                                exit_code=0 if self.outcome is RunStatus.COMPLETED else 1)
        envelope = RESULT if self.outcome is RunStatus.COMPLETED else None
        return ExecutorRunResult(status=self.outcome, session_id=session_id, result_envelope=envelope, usage=None,
                                 command=command)

    def launch(self, request):
        return self._result(request, str(uuid.uuid4()))

    def resume(self, request, session_id):
        return self._result(request, session_id)


class FakeAdapter(Recorder):
    """A minimal executor adapter: automatic, describes itself from the declaration, builds a harmless argv."""

    version = "1"

    def __init__(self, adapter_id):
        self.adapter_id = adapter_id
        self.requests: list[ExecutorLaunchRequest] = []

    def describe(self, config):
        declared = descriptor_from_declaration(self.adapter_id, {"surface": self.adapter_id, **dict(config)})
        data = {**declared.to_dict(), "mode": "automatic", "adapter_id": self.adapter_id,
                "provenance": "adapter_declaration"}
        return ExecutorDescriptor(**data)

    def build_command(self, request):
        return [sys.executable, "--version"]

    def build_resume_command(self, request, session_id):
        return [sys.executable, "--version"]

    def render_prompt(self, request):
        return ""

    def parse_output(self, stdout):
        return None, None, None

    def classify_failure(self, exit_code, stderr):
        return None

    def open_target(self, session_id):
        return None


class RecordingClaude(Recorder, ClaudeAdapter):
    def __init__(self):
        ClaudeAdapter.__init__(self, command=(sys.executable,))
        self.requests = []


class RecordingCopilot(Recorder, CopilotAdapter):
    """The real Copilot adapter (its model check is the real one) with its launch replaced; it lists what is declared."""

    def __init__(self):
        CopilotAdapter.__init__(self, (sys.executable,))
        self.requests = []

    def describe(self, config):
        # Unlike the shipped adapter this keeps a declared Claude id in model_support, so only ``refuses_model`` stands
        # between a profile and a Claude model for Copilot.
        declared = descriptor_from_declaration("copilot_cli", dict(config))
        return ExecutorDescriptor(**{**declared.to_dict(), "mode": "automatic", "adapter_id": self.adapter_id,
                                     "adapter_version": self.version, "provenance": "adapter_declaration"})


def verified():
    return VerifiedOperatorContext("ops", "agent:verified", grants=frozenset(), provenance="test", verified=True)


EXECUTORS = {
    "fake_cli": lambda: (FakeAdapter("fake_cli"), _declaration(
        "fake_cli", models=["fake-small", "fake-mid", "fake-big"], efforts=["low", "medium"])),
    "fake_strict": lambda: (FakeAdapter("fake_strict"), _declaration(
        "fake_strict", models=["fake-small", "fake-big"], efforts=["ultra"])),
    "fake_blind": lambda: (FakeAdapter("fake_blind"), _declaration("fake_blind", models=["fake-small"])),
    "fake_nomodel": lambda: (FakeAdapter("fake_nomodel"), _declaration("fake_nomodel", models=["not-in-catalog"])),
    "copilot_cli": lambda: (RecordingCopilot(), _declaration(
        "copilot_cli", models=["gpt-6-luna", "claude-sonnet-5.5"], efforts=["low"], automatic=True)),
    "claude_cli": lambda: (RecordingClaude(), {**_declaration("claude_cli", models=["claude-haiku-5"]),
                                              "mode": "automatic"}),
}


def make_world(tmp_path, executor_ids, *, routing_profile="economic", extra_profiles=None):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings)
    catalog = services.system.catalog
    catalog.models = dict(MODELS)
    catalog.routing_profiles = {"economic": dict(ECONOMIC), "legacy": dict(LEGACY)}
    adapters, declarations = {}, {}
    for executor_id in executor_ids:
        adapter, declaration = EXECUTORS[executor_id]()
        adapters[executor_id], declarations[executor_id] = adapter, declaration
    profile = {"version": 1, "enabled": True, "executor_ids": list(executor_ids), "permission_ceiling": [],
               "model_routing_profile_id": routing_profile}
    config = {"version": 1, "executors": declarations, "requirement_map": {}, "data_classifications": {},
              "profiles": {"auto": profile, **(extra_profiles or {})},
              "operator_boundary": {"operator_id": "ops", "authorized_aliases": ["agent:verified"],
                                    "claim_actor_alias": "agent:verified", "operator_grants": [],
                                    "alias_grants": {"agent:verified": []}}}
    catalog.executor_routing = config
    catalog.executor_routing_valid = True
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, list(adapters.values())))
    services.executors.clock = lambda: NOW
    services.tasks.create_group("ORQ", "Orchestration", actor="user", authorized=True)
    for task_id in ("T1", "T2", "T3", "T4"):
        services.tasks.create_task("ORQ", task_id, f"Task {task_id}", actor="user", objective="Do it.")
    return SimpleNamespace(services=services, adapters=adapters, config=config)


def events(world, task_id, event_type):
    return [e for e in world.services.tasks.events(task_id=task_id) if e["type"] == event_type]


def routing_event(world, task_id="T1"):
    (event,) = events(world, task_id, "executor.routing_selected")
    return event["payload"]


def run(world, task_id="T1", **kwargs):
    kwargs.setdefault("profile_id", "auto")
    kwargs.setdefault("operator_context", verified())
    return world.services.executors.run_task(task_id, **kwargs)


# --------------------------------------------------------------------------- the profile's choice reaches the launch

def test_model_and_effort_of_the_economic_profile_reach_the_launch_request(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    out = run(world)

    (request,) = world.adapters["fake_cli"].requests
    # "other" is cheaper but the executor does not declare it: the cheapest model it can receive is chosen.
    assert (request.model, request.effort) == ("fake-small", "low")
    assert (out["model"], out["effort"]) == ("fake-small", "low") and out["outcome"] == "submitted"
    assert out["selection"]["model_source"] == SOURCE_PROFILE and out["selection"]["effort_source"] == SOURCE_PROFILE
    assert out["selection"]["notes"] == []
    # What the executor could not take is kept as evidence, with the reason.
    skipped = {item["model"]: item["reason"] for item in out["selection"]["skipped"]}
    assert skipped["other"] == NOT_DECLARED


def test_the_evidence_of_the_choice_is_recorded_on_the_attempt(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    out = run(world)

    payload = routing_event(world)
    assert payload["executor_id"] == "fake_cli" and payload["attempt"] == 1 and payload["mode"] == "launch"
    assert (payload["model"], payload["model_source"]) == ("fake-small", "profile")
    assert (payload["effort"], payload["effort_source"]) == ("low", "profile")
    assert payload["profile_id"] == "economic" and payload["profile_mode"] == "supervised"
    assert payload["step_kind"] == "work" and payload["level"] == "T1" and payload["selected_model"] == "fake_small"
    assert payload["price_source"] == RATE_SOURCE and payload["price_current"] is True
    assert payload["routing_effort_source"] == "profile"
    assert {"model": "other", "reason": NOT_DECLARED} in payload["skipped"]
    # The event sits in the attempt: after the task started and before the session and the result.
    types = [e["type"] for e in world.services.tasks.events(task_id="T1")]
    assert types.index("task.started") < types.index("executor.routing_selected") < types.index("executor.session_recorded")
    (finished,) = events(world, "T1", "executor.attempt_finished")
    assert (finished["payload"]["model"], finished["payload"]["model_source"]) == ("fake-small", "profile")
    assert finished["payload"]["effort_source"] == "profile"
    (session,) = events(world, "T1", "executor.session_recorded")
    assert session["payload"]["model"] == "fake-small" and session["payload"]["model_source"] == "profile"
    # And the status of the task lists it.
    (listed,) = world.services.executors.status("T1")["task"]["routings"]
    assert listed["model"] == "fake-small" and listed["profile_id"] == "economic" and out["attempt"] == 1


def test_the_step_kind_picks_the_level_and_the_effort_of_the_step(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    out = run(world, step_kind="review")

    (request,) = world.adapters["fake_cli"].requests
    assert (request.model, request.effort) == ("fake-big", "medium")            # review: min T3, effort medium
    payload = routing_event(world)
    assert payload["step_kind"] == "review" and payload["level"] == "T3" and out["outcome"] == "submitted"
    with pytest.raises(ServiceError) as caught:
        world.services.executors.run_task("T2", profile_id="auto", operator_context=verified(), step_kind="sorcery")
    assert caught.value.code == "invalid_request"
    assert world.services.tasks.get_task("T2")["state"] == "open"


def test_a_routing_profile_without_economic_fields_also_supplies_the_model(tmp_path):
    world = make_world(tmp_path, ["fake_cli"], routing_profile="legacy")

    run(world)

    (request,) = world.adapters["fake_cli"].requests
    assert request.model == "fake-small" and request.effort is None             # "other" is first but not declared
    payload = routing_event(world)
    assert payload["profile_id"] == "legacy" and payload["profile_mode"] is None and payload["level"] == "T1"
    assert payload["model_source"] == "profile" and payload["effort_source"] is None


# --------------------------------------------------------------------------- what the executor does not declare is not sent

def test_an_effort_the_executor_does_not_list_is_dropped_with_the_reason(tmp_path):
    world = make_world(tmp_path, ["fake_strict"])

    out = run(world)

    (request,) = world.adapters["fake_strict"].requests
    assert request.model == "fake-small" and request.effort is None             # the profile asks "low"; it lists "ultra"
    assert out["effort"] is None and out["selection"]["effort_source"] is None
    assert out["selection"]["notes"] == ["effort_not_sent: low_not_in_executor_support"]
    payload = routing_event(world)
    assert payload["effort"] is None and payload["notes"] == ["effort_not_sent: low_not_in_executor_support"]
    assert payload["routing_effort_source"] == "profile"                         # the profile did ask for it


def test_an_executor_with_unknown_effort_support_never_receives_an_effort(tmp_path):
    world = make_world(tmp_path, ["fake_blind"])

    out = run(world)

    (request,) = world.adapters["fake_blind"].requests
    assert request.model == "fake-small" and request.effort is None
    assert out["selection"]["notes"] == ["effort_not_sent: executor_effort_support_unknown"]


def test_claude_effort_stays_unknown_and_is_never_sent(tmp_path):
    world = make_world(tmp_path, ["claude_cli"])
    descriptor = world.services.executors.registry.get("claude_cli")
    assert descriptor.effort_support.status is SupportState.UNKNOWN              # the adapter never declares it

    out = run(world)

    (request,) = world.adapters["claude_cli"].requests
    assert request.model == "claude-haiku-5" and request.effort is None
    assert out["selection"]["notes"] == ["effort_not_sent: executor_effort_support_unknown"]
    # An effort the caller names is still handed over (the Claude adapter then ignores it and says so).
    explicit = world.services.executors.run_task("T2", "claude_cli", effort="high")
    assert explicit["effort"] == "high" and explicit["selection"]["effort_source"] == SOURCE_USER


def test_an_executor_with_no_model_it_can_receive_is_not_selected(tmp_path):
    world = make_world(tmp_path, ["fake_nomodel"])

    preview = world.services.assignment.preview("T1", {"profile_id": "auto"}, verified())["decision"]
    assert preview["status"] == "manual" and "model_routing_not_selectable" in preview["reason_codes"]
    shown = preview["evidence"]["executor_requests"]["fake_nomodel"]
    assert shown["routing"]["status"] == "blocked" and shown["model"] is None and shown["selected"] is False
    assert shown["notes"] == ["model_not_sent: routing_decision_not_selected"]
    with pytest.raises(ServiceError) as caught:
        run(world)
    assert caught.value.details["reason"] == "assignment_not_selected"
    assert world.adapters["fake_nomodel"].requests == [] and world.services.tasks.get_task("T1")["state"] == "open"


# --------------------------------------------------------------------------- Copilot and Claude models

def test_copilot_never_receives_a_claude_model_from_a_profile(tmp_path):
    world = make_world(tmp_path, ["copilot_cli"])
    copilot = world.adapters["copilot_cli"]
    # The declaration lists a Claude id (the shipped adapter would strip it); the adapter refuses it all the same.
    assert "claude-sonnet-5.5" in world.services.executors.registry.get("copilot_cli").model_support.values
    assert copilot.refuses_model("claude-sonnet-5.5") == "claude_model_rejected"
    assert copilot.refuses_model("gpt-6-luna") is None

    out = run(world)

    (request,) = copilot.requests
    # claude_cat is the cheapest model the declaration allows (T1, 0.02), but it is skipped for Copilot.
    assert request.model == "gpt-6-luna" and out["outcome"] == "submitted"
    reason = f"{ADAPTER_REFUSES}:claude_model_rejected"
    assert {"model": "claude_cat", "reason": reason} in out["selection"]["skipped"]
    assert {"model": "claude_cat", "reason": reason} in routing_event(world)["skipped"]
    assert out["selection"]["routing"]["selected_model"] == "luna" and out["selection"]["routing"]["level"] == "T2"
    # A Claude model named by hand is still refused by the adapter before the task changes.
    with pytest.raises(ServiceError) as caught:
        world.services.executors.run_task("T2", "copilot_cli", "claude-sonnet-5.5")
    assert caught.value.code == "invalid_request" and "claude" in caught.value.message.lower()
    assert world.services.tasks.get_task("T2")["state"] == "open"


def test_the_adapter_hook_and_the_declaration_exclude_models_for_an_executor():
    copilot = CopilotAdapter((sys.executable,))
    listed = ExecutorDescriptor(
        executor_id="copilot_cli", surface="copilot_cli", mode="automatic",
        model_support=CapabilityEvidence(SupportState.SUPPORTED, "t", "operator_test", "copilot_cli", "2026-10-11",
                                         frozenset({"claude-sonnet-5.5", "gpt-6-luna"})))
    excluded = model_exclusions(MODELS, listed, copilot)
    assert excluded["claude_cat"] == f"{ADAPTER_REFUSES}:claude_model_rejected"
    assert excluded["other"] == NOT_DECLARED and excluded["fake_small"] == NOT_DECLARED
    assert "luna" not in excluded
    # An executor that declares nothing about models excludes nothing here (the assignment selector decides).
    silent = ExecutorDescriptor(executor_id="x", surface="x", mode="automatic")
    assert model_exclusions(MODELS, silent, None) == {}
    # With no declaration the adapter's refusal still applies.
    assert set(model_exclusions(MODELS, silent, copilot)) == {"claude_cat", "claude_haiku"}


# --------------------------------------------------------------------------- the caller's values win

def test_explicit_model_and_effort_win_over_the_profile(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    out = run(world, model="fake-mid", effort="medium")

    (request,) = world.adapters["fake_cli"].requests
    assert (request.model, request.effort) == ("fake-mid", "medium")            # the profile would say fake-small / low
    assert out["selection"]["model_source"] == SOURCE_USER and out["selection"]["effort_source"] == SOURCE_USER
    payload = routing_event(world)
    assert payload["model_source"] == "user" and payload["effort_source"] == "user"
    assert payload["profile_id"] == "economic"                                   # the routing evidence is still kept
    (finished,) = events(world, "T1", "executor.attempt_finished")
    assert finished["payload"]["model_source"] == "user"


def test_one_explicit_value_does_not_replace_the_other_from_the_profile(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    out = run(world, effort="medium")

    (request,) = world.adapters["fake_cli"].requests
    assert (request.model, request.effort) == ("fake-small", "medium")
    assert (out["selection"]["model_source"], out["selection"]["effort_source"]) == (SOURCE_PROFILE, SOURCE_USER)


def test_an_explicit_value_the_executor_does_not_declare_is_refused_not_replaced(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])
    with pytest.raises(ServiceError) as caught:
        run(world, model="not-declared")
    assert caught.value.details["reason"] == "model_not_supported"
    with pytest.raises(ServiceError) as caught:
        world.services.executors.run_task("T1", "fake_cli", effort="ultra")
    assert caught.value.details["reason"] == "effort_not_supported"
    assert world.adapters["fake_cli"].requests == [] and world.services.tasks.get_task("T1")["state"] == "open"


def test_without_a_profile_only_the_callers_values_apply(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    bare = world.services.executors.run_task("T1", "fake_cli")
    named = world.services.executors.run_task("T2", "fake_cli", "fake-mid", "low")

    first, second = world.adapters["fake_cli"].requests
    assert (first.model, first.effort) == (None, None) and (second.model, second.effort) == ("fake-mid", "low")
    assert bare["selection"]["model_source"] is None and bare["selection"]["routing"] == {}
    assert named["selection"]["model_source"] == SOURCE_USER and named["selection"]["effort_source"] == SOURCE_USER
    assert "profile_id" not in routing_event(world, "T1")


def test_a_resume_continues_the_recorded_model_and_says_so(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])
    adapter = world.adapters["fake_cli"]
    adapter.outcome = RunStatus.FAILED
    first = run(world)
    assert first["outcome"] == "failed" and world.services.tasks.get_task("T1")["state"] == "paused"
    adapter.outcome = RunStatus.COMPLETED

    again = world.services.executors.resume_task("T1")

    assert again["outcome"] == "submitted" and again["mode"] == "resume"
    assert (adapter.requests[-1].model, adapter.requests[-1].effort) == ("fake-small", "low")
    resumed = [e["payload"] for e in events(world, "T1", "executor.routing_selected")][-1]
    assert resumed["mode"] == "resume" and resumed["model_source"] == SOURCE_SESSION == "session"
    assert resumed["effort_source"] == SOURCE_SESSION
    named = world.services.executors._routing_selection(None, "fake-mid", None, executor_id="fake_cli",
                                                        descriptor=world.services.executors.registry.get("fake_cli"))
    assert (named.model, named.model_source, named.effort) == ("fake-mid", SOURCE_USER, None)


# --------------------------------------------------------------------------- the preview shows what will be requested

def test_the_preview_shows_the_model_and_effort_each_executor_would_be_asked(tmp_path):
    world = make_world(tmp_path, ["fake_cli", "copilot_cli", "fake_strict"])

    decision = world.services.assignment.preview("T1", {"profile_id": "auto"}, verified())["decision"]

    assert decision["status"] == "selected" and decision["selected_executor"] == "fake_cli"
    shown = decision["evidence"]["executor_requests"]
    assert set(shown) == {"fake_cli", "copilot_cli", "fake_strict"} and decision["evidence"]["step_kind"] == "work"
    assert (shown["fake_cli"]["model"], shown["fake_cli"]["effort"]) == ("fake-small", "low")
    assert shown["fake_cli"]["selected"] is True and shown["copilot_cli"]["selected"] is False
    assert (shown["copilot_cli"]["model"], shown["copilot_cli"]["effort"]) == ("gpt-6-luna", "low")
    assert (shown["fake_strict"]["model"], shown["fake_strict"]["effort"]) == ("fake-small", None)
    assert shown["fake_strict"]["notes"] == ["effort_not_sent: low_not_in_executor_support"]
    for entry in shown.values():
        assert entry["model_source"] == "profile" and entry["routing"]["profile_id"] == "economic"
        assert entry["routing"]["profile_mode"] == "supervised" and entry["routing"]["price_source"] == RATE_SOURCE
    assert shown["fake_cli"]["routing"]["level"] == "T1" and shown["copilot_cli"]["routing"]["level"] == "T2"
    # The decision itself carries the routing decision of the executor it selected.
    assert decision["routing_decision"]["selected_model"] == "fake_small" and decision["planned_model"] == "fake_small"
    # Nothing was launched or claimed by looking.
    assert world.services.tasks.get_task("T1")["state"] == "open"
    assert all(adapter.requests == [] for adapter in world.adapters.values())


def test_the_preview_matches_the_request_the_launch_carries(tmp_path):
    world = make_world(tmp_path, ["fake_cli", "copilot_cli", "fake_strict"])
    shown = world.services.assignment.preview("T1", {"profile_id": "auto"}, verified())["decision"]["evidence"][
        "executor_requests"]

    for task_id, executor_id in (("T1", "fake_cli"), ("T2", "copilot_cli"), ("T3", "fake_strict")):
        out = run(world, task_id, executor_id=executor_id)
        (request,) = world.adapters[executor_id].requests
        entry = shown[executor_id]
        assert (request.model, request.effort) == (entry["model"], entry["effort"]), executor_id
        assert out["selection"] == {"executor_id": executor_id, **{k: v for k, v in entry.items() if k != "selected"}}
        assert out["selection"]["routing"] == entry["routing"]
        payload = routing_event(world, task_id)
        assert payload["profile_id"] == entry["routing"]["profile_id"] and payload["level"] == entry["routing"]["level"]


def test_the_preview_marks_the_callers_values_as_user_and_respects_the_step_kind(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])

    decision = world.services.assignment.preview(
        "T1", {"profile_id": "auto", "requested_model": "fake-mid", "requested_effort": "medium",
               "step_kind": "review"}, verified())["decision"]

    entry = decision["evidence"]["executor_requests"]["fake_cli"]
    assert (entry["model"], entry["model_source"]) == ("fake-mid", "user")
    assert (entry["effort"], entry["effort_source"]) == ("medium", "user")
    assert entry["routing"]["step_kind"] == "review" and entry["routing"]["level"] == "T3"
    with pytest.raises(ServiceError) as caught:
        world.services.assignment.preview("T1", {"profile_id": "auto", "step_kind": "chores"}, verified())
    assert caught.value.code == "invalid_request"


def test_a_preview_that_shows_the_selection_can_still_be_accepted(tmp_path):
    world = make_world(tmp_path, ["fake_cli", "copilot_cli"])
    preview = world.services.assignment.preview("T1", {"profile_id": "auto", "step_kind": "work"}, verified())
    assert preview["decision"]["status"] == "selected"

    accepted = world.services.assignment.accept("T1", preview["preview_token"], True, verified())

    assert accepted["task"]["state"] == "claimed"
    assert accepted["decision"]["evidence"]["executor_requests"] == preview["decision"]["evidence"]["executor_requests"]


def test_the_selection_function_is_pure_and_reads_the_routing_decision_dict(tmp_path):
    world = make_world(tmp_path, ["fake_cli"])
    decision = world.services.assignment.preview("T1", {"profile_id": "auto"}, verified())["decision"]
    descriptor = world.services.executors.registry.get("fake_cli")

    selection = select_for_executor("fake_cli", descriptor, decision["routing_decision"])

    assert (selection.model, selection.effort) == ("fake-small", "low")
    assert selection.to_dict() == {"executor_id": "fake_cli", **{k: v for k, v in decision["evidence"][
        "executor_requests"]["fake_cli"].items() if k != "selected"}}
    # A decision that selected nothing sends nothing and says why.
    blocked = select_for_executor("fake_cli", descriptor, {"status": "blocked", "reason_codes": ["no_eligible_candidate"]})
    assert blocked.model is None and blocked.notes == ("model_not_sent: routing_decision_not_selected",)


def test_the_router_leaves_out_excluded_models_and_changes_nothing_without_them(tmp_path):
    from multiagent.routing_policy import RoutingEvidence, RoutingRequest

    world = make_world(tmp_path, ["fake_cli"])
    router = world.services.system.engine.router
    base = dict(profile_id="economic", step_kind="work", required_capabilities=frozenset({"structured_output"}),
                effective_permissions=frozenset(), authorization_provenance="unit-test", execution_mode="auto")

    plain = router.select(RoutingRequest(**base), RoutingEvidence(scope="t"))
    excluded = router.select(RoutingRequest(**base, excluded_models={"claude_cat": "executor_test", "other": "executor_test"}),
                             RoutingEvidence(scope="t"))

    assert plain.selected_model == "claude_cat"                      # the cheapest of the work models
    assert excluded.selected_model == "claude_haiku"
    left_out = {c.model_id: c.reason_codes for c in excluded.candidates if "executor_test" in c.reason_codes}
    assert set(left_out) == {"claude_cat", "other"}
    assert RoutingRequest().to_dict()["excluded_models"] is None


def test_http_preview_and_run_accept_the_step_kind(tmp_path):
    from fastapi.testclient import TestClient

    from multiagent.api import create_app

    world = make_world(tmp_path, ["fake_cli"])
    app = create_app(services=world.services, operator_context_provider=lambda _: verified())
    with TestClient(app, base_url="http://localhost") as client:
        preview = client.post("/api/v1/tasks/T1/assignment/preview", json={"profile_id": "auto", "step_kind": "review"})
        assert preview.status_code == 200, preview.text
        shown = preview.json()["decision"]["evidence"]["executor_requests"]["fake_cli"]
        assert (shown["model"], shown["effort"]) == ("fake-big", "medium")
        bad = client.post("/api/v1/tasks/T1/assignment/preview", json={"profile_id": "auto", "step_kind": "chores"})
        assert bad.status_code == 422
        ran = client.post("/api/v1/executors/run", json={"task_id": "T1", "profile_id": "auto", "step_kind": "review"})
        assert ran.status_code == 200, ran.text
        assert (ran.json()["model"], ran.json()["effort"]) == ("fake-big", "medium")
