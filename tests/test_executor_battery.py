# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Cross-app executor contract checks using only disposable Python doubles.

The adapter modules contain the detailed argv and parser cases. This battery checks that their
different output formats obey the same service lifecycle and session rules without starting an
installed Claude, Codex, or Copilot CLI.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent.config import Settings
from multiagent.catalog import ModelSpec
from multiagent.executors import process
from multiagent.executors.claude import ClaudeAdapter
from multiagent.executors.codex import CodexExecutorAdapter
from multiagent.executors.contract import CommandRecord
from multiagent.executors.copilot import CopilotAdapter
from multiagent.executors.registry import ExecutorRegistry
from multiagent.services import ApplicationServices, AssignmentServices, ServiceError
from multiagent.routing.executor_capabilities import VerifiedOperatorContext
from multiagent.tasks.envelopes import TASK_RESULT_SCHEMA

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
TASK_RESULT = {
    "schema": TASK_RESULT_SCHEMA, "base_commit": "", "patch": None,
    "summary": "Completed by a test double.", "decisions": [], "contract_changes": "",
    "tests_not_run": [], "requests": [],
}
RATE_SOURCE = "synthetic executor battery"
SIDS = {
    "claude": "c2f6bd73-4396-49b4-a8e3-0912953aa0d1",
    "codex": "0199a213-81c0-7800-8aa1-bbab2a035a53",
    "copilot": "0cb916db-26aa-40f2-86b5-1ba81b225fd2",
}
CASES = (("claude_cli", "claude"), ("codex_cli", "codex"), ("copilot_cli", "copilot"))

# The child only prints the case selected by the test. It never discovers or forwards to a real CLI.
FAKE_PYTHON = r'''import json, pathlib, sys, time
kind, spec_path = sys.argv[1:3]
spec = json.loads(pathlib.Path(spec_path).read_text(encoding="utf-8"))
if spec.get("sleep"):
    time.sleep(spec["sleep"])
sys.stdout.write(spec.get("stdout", ""))
sys.stderr.write(spec.get("stderr", ""))
sys.exit(spec.get("exit", 0))
'''


def _stdout(kind: str, envelope: object, *, usage: bool) -> str:
    sid = SIDS[kind]
    result_text = json.dumps(envelope) if isinstance(envelope, dict) else str(envelope)
    if kind == "claude":
        row = {"type": "result", "subtype": "success", "is_error": False,
               "result": result_text, "session_id": sid}
        if usage:
            row["usage"] = {"input_tokens": 7, "output_tokens": 3}
        return json.dumps(row)
    if kind == "codex":
        rows = [{"type": "thread.started", "thread_id": sid},
                {"type": "item.completed", "item": {"type": "agent_message", "text": result_text}},
                {"type": "turn.completed", **({"usage": {"input_tokens": 7, "output_tokens": 3}} if usage else {})}]
        return "\n".join(json.dumps(row) for row in rows)
    rows = [{"type": "session.start", "data": {"sessionId": sid}},
            {"type": "assistant.message", "data": {"content": result_text}},
            {"type": "result", "sessionId": sid, "exitCode": 0}]
    if usage:
        rows[-1]["usage"] = {"premiumRequests": 1}
    return "\n".join(json.dumps(row) for row in rows)


class FakeCli:
    def __init__(self, folder: Path):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.script = self.folder / "fake_executor.py"
        self.script.write_text(FAKE_PYTHON, encoding="utf-8")

    def spec(self, kind: str, **values) -> Path:
        path = self.folder / f"{kind}.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    def command(self, kind: str) -> tuple[str, ...]:
        return (sys.executable, str(self.script), kind, str(self.folder / f"{kind}.json"))


def _support(scope: str, values: list[str]) -> dict:
    return {"status": "supported", "source": "test", "provenance": "synthetic", "scope": scope,
            "observed_at": "2026-10-11", "values": values}


def _model(kind: str) -> ModelSpec:
    def price(amount: int) -> dict:
        return {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
                "source": RATE_SOURCE, "provenance": "catalog_declared",
                "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"}

    return ModelSpec(f"{kind}_id", "ollama", f"{kind}-model", "local", True, False,
                     capabilities=frozenset({"structured_output"}), roles=frozenset({"assistant"}),
                     permissions=frozenset(), permissions_required=frozenset(), allowed_data_classes=None,
                     pricing={"input": price(1), "output": price(2)}, level="T1", classification="classified")


def make_world(root: Path) -> SimpleNamespace:
    fake = FakeCli(root / "fake-bin")
    settings = Settings(_env_file=None, local_dir=root / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings)
    config = {"version": 1, "executors": {}, "requirement_map": {}, "data_classifications": {},
              "profiles": {}, "operator_boundary": {"operator_id": "ops", "authorized_aliases": [],
              "claim_actor_alias": "ops", "operator_grants": [], "alias_grants": {}}}
    for executor_id, kind in CASES:
        support_effort = _support(executor_id, ["high"]) if kind != "claude" else {
            "status": "unknown", "source": "test", "provenance": "synthetic", "scope": executor_id,
            "observed_at": "2026-10-11", "values": None,
        }
        config["executors"][executor_id] = {
            "surface": executor_id, "adapter_id": executor_id, "enabled": True,
            "model_support": _support(executor_id, [f"{kind}-model"]),
            "effort_support": support_effort,
        }
    adapters = {
        "claude_cli": ClaudeAdapter(command=fake.command("claude")),
        "codex_cli": CodexExecutorAdapter(command=fake.command("codex")),
        "copilot_cli": CopilotAdapter(fake.command("copilot"), session_guard=services.executors.session_guard),
    }
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, list(adapters.values())))
    services.executors.clock = lambda: NOW
    services.tasks.create_group("ORQ", "Orchestration", actor="user", authorized=True)
    services.tasks.create_task("ORQ", "T1", "Read module", actor="user", objective="Summarize module.")
    return SimpleNamespace(services=services, fake=fake, adapters=adapters)


@pytest.fixture(autouse=True)
def never_resolve_real_cli(monkeypatch):
    original = process.find_executable

    def guard(name, **kwargs):
        if name in {"claude", "codex", "copilot"}:
            raise AssertionError(f"test attempted to resolve real CLI {name!r}")
        return original(name, **kwargs)

    monkeypatch.setattr(process, "find_executable", guard)


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


@pytest.mark.parametrize("executor_id,kind", CASES)
def test_each_app_uses_the_requested_model_effort_and_unknown_usage_is_preserved(world, executor_id, kind):
    # The adapter's declared support is configuration inherited from the test catalog.
    world.fake.spec(kind, stdout=_stdout(kind, TASK_RESULT, usage=False))
    out = world.services.executors.run_task("T1", executor_id, model=f"{kind}-model", effort="high")
    assert out["outcome"] == "submitted" and out["usage"] is None
    assert out["model"] == f"{kind}-model"
    # Claude does not support effort; the service keeps its established drop-and-note behavior.
    if kind == "claude":
        assert out["effort"] == "high" and "--effort" not in out["command"]["argv"]
        assert any("effort" in note for note in out["notes"])
    else:
        assert out["effort"] == "high"
    event = next(e for e in world.services.tasks.events(task_id="T1")
                 if e["type"] == "executor.routing_selected")
    assert event["payload"]["model_source"] == "user"
    argv = out["command"]["argv"]
    model_flag = "-m" if kind == "codex" else "--model"
    assert argv[argv.index(model_flag) + 1] == f"{kind}-model"
    if kind == "codex":
        assert "-c" in argv and "model_reasoning_effort=high" in argv
    elif kind == "copilot":
        assert argv[argv.index("--effort") + 1] == "high"
    else:
        assert "--effort" not in argv


@pytest.mark.parametrize("executor_id,kind", CASES)
def test_assignment_profile_values_reach_each_adapter_without_caller_overrides(world, executor_id, kind):
    catalog = world.services.system.catalog
    catalog.models = {name: _model(name) for name in ("claude", "codex", "copilot")}
    catalog.routing_profiles = {"battery": {
        "version": 1, "enabled": True, "mode": "supervised", "economic_selection": True,
        "levels": {"work": {"max": "T1"}}, "effort": {"work": "high"},
        "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
        "allowed_permissions": [], "execution_modes": ["auto", "local", "cloud"], "require_health": False,
        "max_retries_per_candidate": 0,
    }}
    config = world.services.system.catalog.executor_routing
    for app_id, _app in CASES:
        config["executors"][app_id]["mode"] = "automatic"
        config["executors"][app_id]["noninteractive_launch"] = _support(app_id, ["test fake"])
    config["profiles"] = {"auto": {"version": 1, "enabled": True, "executor_ids": [executor_id],
                                    "permission_ceiling": [], "model_routing_profile_id": "battery"}}
    config["operator_boundary"] = {
        "operator_id": "ops", "authorized_aliases": ["agent:verified"],
        "claim_actor_alias": "agent:verified", "operator_grants": [], "alias_grants": {"agent:verified": []},
    }
    world.services.assignment = AssignmentServices(
        world.services, registry=ExecutorRegistry(config, list(world.adapters.values())))
    world.fake.spec(kind, stdout=_stdout(kind, TASK_RESULT, usage=True))

    out = world.services.executors.run_task("T1", profile_id="auto", operator_context=VerifiedOperatorContext(
        "ops", "agent:verified", grants=frozenset(), provenance="synthetic", verified=True))

    assert out["outcome"] == "submitted" and out["selection"]["model_source"] == "profile"
    assert out["selection"]["effort_source"] == (None if kind == "claude" else "profile")
    argv = out["command"]["argv"]
    model_flag = "-m" if kind == "codex" else "--model"
    assert argv[argv.index(model_flag) + 1] == f"{kind}-model"
    if kind == "claude":
        assert "--effort" not in argv
        assert "executor_effort_support_unknown" in " ".join(out["selection"]["notes"])
    elif kind == "codex":
        assert "-c" in argv and "model_reasoning_effort=high" in argv
    else:
        assert argv[argv.index("--effort") + 1] == "high"


@pytest.mark.parametrize("executor_id,kind", CASES)
def test_malformed_result_is_recorded_and_never_retried(world, executor_id, kind):
    world.fake.spec(kind, stdout=_stdout(kind, "not a result envelope", usage=True))
    result = world.services.executors.run_task("T1", executor_id, model=f"{kind}-model")
    assert result["outcome"] == "failed" and result["result"] is None
    assert world.services.tasks.get_task("T1")["state"] == "paused"
    attempts = [e for e in world.services.tasks.events(task_id="T1") if e["type"] == "executor.attempt_finished"]
    assert len(attempts) == 1 and attempts[0]["payload"]["outcome"] == "failed"


@pytest.mark.parametrize("executor_id,kind", CASES)
def test_framework_created_session_opened_recently_cannot_resume(world, executor_id, kind):
    world.fake.spec(kind, stdout=_stdout(kind, "invalid first answer", usage=False))
    first = world.services.executors.run_task("T1", executor_id, model=f"{kind}-model")
    assert first["session_id"] == SIDS[kind]
    # Session provenance comes from the service event written by this launch.
    assert world.services.executors.session_guard(first["session_id"])
    world.services.executors.record_app_open(first["session_id"], source=kind, actor="gui")
    with pytest.raises(ServiceError) as caught:
        world.services.executors.resume_task("T1")
    assert caught.value.details["reason"] == "session_open_in_app"
    assert world.services.tasks.get_task("T1")["state"] == "paused"


@pytest.mark.parametrize("executor_id,kind", CASES)
@pytest.mark.parametrize("flag,expected", [("timed_out", "timeout"), ("cancelled", "cancelled")])
def test_process_timeout_and_cancellation_are_not_submitted(world, monkeypatch, executor_id, kind, flag, expected):
    # Control only the framework's process boundary; no child is launched for these outcomes.
    from multiagent.executors.process import ProcessOutcome

    def outcome(argv, *, cwd, **kwargs):
        record = CommandRecord(tuple(argv), str(cwd), NOW.isoformat(), NOW.isoformat(), 1)
        return ProcessOutcome(exit_code=1, stdout_tail="", stderr_tail="", stdout_truncated=False,
                              stderr_truncated=False, timed_out=flag == "timed_out",
                              cancelled=flag == "cancelled", command=record)

    monkeypatch.setattr(process, "run", outcome)
    result = world.services.executors.run_task("T1", executor_id, model=f"{kind}-model", timeout_seconds=1)
    assert result["outcome"] == expected and world.services.tasks.get_task("T1")["state"] == "paused"
    assert "result.submitted" not in [e["type"] for e in world.services.tasks.events(task_id="T1")]


@pytest.mark.parametrize("executor_id,kind", CASES)
def test_quota_stops_the_app_and_requires_a_person_to_rerun(world, monkeypatch, executor_id, kind):
    from multiagent.executors.process import ProcessOutcome

    def quota(argv, *, cwd, **kwargs):
        record = CommandRecord(tuple(argv), str(cwd), NOW.isoformat(), NOW.isoformat(), 1)
        return ProcessOutcome(exit_code=1, stdout_tail="", stderr_tail="rate limit exceeded", stdout_truncated=False,
                              stderr_truncated=False, timed_out=False, cancelled=False, command=record)

    monkeypatch.setattr(process, "run", quota)
    result = world.services.executors.run_task("T1", executor_id, model=f"{kind}-model")
    assert result["outcome"] == "quota_exhausted" and result["executor_paused_until"]
    assert world.services.tasks.get_task("T1")["state"] == "paused"
    with pytest.raises(ServiceError) as caught:
        world.services.executors.run_task("T1", executor_id)
    assert caught.value.details["reason"] == "executor_paused"
