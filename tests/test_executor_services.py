# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ORQ-T51: the executor service (run, resume, open, status) with fake programs, and facade/HTTP/CLI parity.

No real Claude, Codex or Copilot is ever started. The real adapters (ORQ-T48 to T50) are configured with
``command=(sys.executable, fake_cli.py, <spec>.json)``: a script generated in ``tmp_path`` that records what it
received and prints what the test configured. An autouse guard fails the test if the bare name of a real CLI is
looked up on PATH. The gate (ORQ-2) and the lifecycle (ORQ-1) are the real services.
"""
from __future__ import annotations

import json
import ast
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors import process
from multiagent.executors.claude import ClaudeAdapter
from multiagent.executors.codex import CodexExecutorAdapter
from multiagent.executors.contract import ExecutorLaunchRequest
from multiagent.executors.copilot import CopilotAdapter, CopilotAdapterError
from multiagent.executors.defaults import default_adapters
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing.executor_capabilities import VerifiedOperatorContext
from multiagent.services import ApplicationServices, ExecutorServices, ServiceError
from multiagent.services import executors as executor_module
from multiagent.services.assignment import AssignmentServices
from multiagent.tasks.envelopes import TASK_RESULT_SCHEMA, build_task_envelope

THREAD = "0199a213-81c0-7800-8aa1-bbab2a035a53"
COPILOT_SID = "0cb916db-26aa-40f2-86b5-1ba81b225fd2"
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
CLI_EXECUTORS = ("claude_cli", "codex_cli", "copilot_cli")
RESULT = {"schema": TASK_RESULT_SCHEMA, "base_commit": "", "patch": None, "summary": "Did the work.",
          "decisions": ["kept it small"], "contract_changes": "", "tests_not_run": ["full suite"], "requests": []}

FAKE_CLI = r'''
import json, os, sys, time

here = os.path.dirname(os.path.abspath(__file__))
spec = json.load(open(sys.argv[1], encoding="utf-8"))
args = sys.argv[2:]
stdin = sys.stdin.buffer.read().decode("utf-8", "replace")
sid = ""
for index, arg in enumerate(args):
    if arg in ("--session-id", "--resume") and index + 1 < len(args):
        sid = args[index + 1]
    elif arg.startswith("--resume="):
        sid = arg.split("=", 1)[1]
with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as log:
    log.write(json.dumps({"spec": os.path.basename(sys.argv[1]), "argv": args, "stdin": stdin, "cwd": os.getcwd(),
                          "scrubbed": sorted(k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC")))}) + "\n")
live = spec.get("live_stdout", "")
if live:
    sys.stdout.write(live)
    sys.stdout.flush()
    time.sleep(spec.get("live_sleep", 0))
elif spec.get("sleep"):
    time.sleep(spec["sleep"])
out = spec.get("stdout", "")
out = "\n".join(out) + "\n" if isinstance(out, list) else out
sys.stdout.write(out.replace("__SID__", sid))
sys.stdout.flush()
sys.stderr.write(spec.get("stderr", ""))
sys.stderr.flush()
sys.exit(spec.get("exit", 0))
'''


# --------------------------------------------------------------------------- what the fake programs print

def claude_out(result_text=None, **extra) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "duration_ms": 5, "num_turns": 2,
                       "result": json.dumps(RESULT) if result_text is None else result_text, "session_id": "__SID__",
                       "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}, **extra})


def codex_out(result=RESULT, thread=THREAD) -> list[str]:
    return [json.dumps({"type": "thread.started", "thread_id": thread}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(result)}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 4}})]


def copilot_out(result=RESULT, session=COPILOT_SID) -> list[str]:
    events = [{"type": "session.start", "data": {"sessionId": session}},
              {"type": "assistant.message", "data": {"content": "Done.\n```json\n" + json.dumps(result) + "\n```"}},
              {"type": "result", "sessionId": session, "exitCode": 0, "usage": {"premiumRequests": 1}}]
    return [json.dumps(event) for event in events]


class Fake:
    """The generated program, its per-app scenarios and the calls it recorded."""

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.script = folder / "fake_cli.py"
        self.script.write_text(FAKE_CLI, encoding="utf-8")
        self.set("claude", stdout=claude_out())
        self.set("codex", stdout=codex_out())
        self.set("copilot", stdout=copilot_out())

    def spec(self, kind: str) -> Path:
        return self.folder / f"{kind}.json"

    def set(self, kind: str, **scenario) -> None:
        self.spec(kind).write_text(json.dumps(scenario), encoding="utf-8")

    def command(self, kind: str) -> tuple[str, ...]:
        return (sys.executable, str(self.script), str(self.spec(kind)))

    def calls(self, kind: str | None = None) -> list[dict]:
        path = self.folder / "calls.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [row for row in rows if kind is None or row["spec"] == f"{kind}.json"]


@pytest.fixture(autouse=True)
def never_a_real_cli(monkeypatch):
    real = process.find_executable

    def guard(name, **kwargs):
        if name in ("claude", "codex", "copilot"):
            raise AssertionError(f"the bare name {name!r} was looked up: a test would start the real program")
        return real(name, **kwargs)

    monkeypatch.setattr(process, "find_executable", guard)
    for name in ("CLAUDE_CODE_TEST", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "not-for-the-child")


# --------------------------------------------------------------------------- the world

def make_services(root: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings)


def routing_config(enabled=CLI_EXECUTORS, declarations=None) -> dict:
    executors = {name: {"surface": name, "adapter_id": name, "enabled": name in enabled,
                        **(declarations or {}).get(name, {})} for name in CLI_EXECUTORS}
    executors["claude_cli"].setdefault("mode", "automatic")
    executors["manual_app"] = {"surface": "claude_app", "enabled": True}      # enabled, but no adapter
    return {"version": 1, "executors": executors, "requirement_map": {}, "data_classifications": {},
            "profiles": {"auto": {"version": 1, "enabled": True, "executor_ids": ["claude_cli"],
                                  "permission_ceiling": []}},
            "operator_boundary": {"operator_id": "ops", "authorized_aliases": ["agent:verified"],
                                  "claim_actor_alias": "agent:verified", "operator_grants": [],
                                  "alias_grants": {"agent:verified": []}}}


def verified() -> VerifiedOperatorContext:
    return VerifiedOperatorContext("ops", "agent:verified", grants=frozenset(), provenance="test", verified=True)


def make_world(root: Path, bin_folder: Path, *, enabled=CLI_EXECUTORS, declarations=None) -> SimpleNamespace:
    fake = Fake(bin_folder)      # the folder may be shared by several worlds: they write the same default scenarios
    services = make_services(root / "local")
    config = routing_config(enabled, declarations)
    adapters = {
        "claude": ClaudeAdapter(command=fake.command("claude")),
        "codex": CodexExecutorAdapter(command=fake.command("codex")),
        "copilot": CopilotAdapter(fake.command("copilot"), session_guard=services.executors.session_guard),
    }
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, list(adapters.values())))
    services.executors.clock = lambda: NOW
    tasks = services.tasks
    tasks.create_group("ORQ", "Orchestration", actor="user", authorized=True)
    tasks.create_task("ORQ", "T1", "Read the module", actor="user", objective="Summarize the module.")
    tasks.create_task("ORQ", "T2", "Change the app", actor="user", file_scope=["src/app"])
    tasks.create_task("ORQ", "T3", "Third", actor="user")
    tasks.create_task("ORQ", "T4", "Fourth", actor="user", depends_on=["T1"])
    return SimpleNamespace(services=services, fake=fake, adapters=adapters, root=root)


@pytest.fixture
def world(tmp_path) -> SimpleNamespace:
    return make_world(tmp_path, tmp_path / "bin")


def event_types(services, task_id: str) -> list[str]:
    return [event["type"] for event in services.tasks.events(task_id=task_id)]


def attempt_events(services, task_id: str) -> list[dict]:
    return [e["payload"] for e in services.tasks.events(task_id=task_id) if e["type"] == "executor.attempt_finished"]


def refused(call, code="invalid_state", reason=None) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    assert caught.value.code == code, caught.value
    if reason is not None:
        assert caught.value.details.get("reason") == reason, caught.value.details
    return caught.value


# --------------------------------------------------------------------------- the success path

def test_a_run_submits_the_parsed_result_through_submit_result_and_the_gate_decides(world):
    services, fake = world.services, world.fake
    services.gates.set_task_gate("T1", ["review"], actor="user")

    out = services.executors.run_task("T1", "claude_cli")

    assert out["outcome"] == "submitted" and out["run_status"] == "completed"
    assert out["executor_id"] == "claude_cli" and out["actor"] == "executor:claude_cli" and out["attempt"] == 1
    assert out["permission"] == "read" and out["usage"]["output_tokens"] == 5 and UUID.fullmatch(out["session_id"])
    assert out["gate"]["checks"] == ["review"] and out["result"]["duplicate"] is False
    # The task is only submitted: nothing accepted it.
    task = services.tasks.get_task("T1")
    assert (task["state"], task["assignee"], task["attempt"]) == ("submitted", "executor:claude_cli", 1)
    stored = services.envelopes.get_result("T1")
    assert stored["agent"] == "executor:claude_cli" and stored["summary"] == "Did the work." and stored["attempt"] == 1
    assert event_types(services, "T1") == [
        "task.created", "gate.updated", "task.claimed", "task.started", "executor.routing_selected",
        "executor.session_recorded", "task.submitted", "result.submitted", "executor.attempt_finished"]
    session = next(e for e in services.tasks.events(task_id="T1") if e["type"] == "executor.session_recorded")
    assert session["ref"] == out["session_id"] and session["payload"]["executor_id"] == "claude_cli"
    assert session["payload"]["attempt"] == 1 and session["actor"] == "executor:claude_cli"
    finished = attempt_events(services, "T1")[0]
    assert finished["outcome"] == "submitted" and finished["result_ref"] == "result://T1/1" and finished["exit_code"] == 0

    # What the app received: the canonical task envelope on stdin, read-only flags, the framework's folder, a clean env.
    (call,) = fake.calls("claude")
    assert '"task_id":"T1"' in call["stdin"] and '"attempt":1' in call["stdin"] and '"status":"running"' in call["stdin"]
    assert '"assignee":"executor:claude_cli"' in call["stdin"]
    assert call["argv"][call["argv"].index("--permission-mode") + 1] == "plan"
    assert call["argv"][call["argv"].index("--session-id") + 1] == out["session_id"]
    assert Path(call["cwd"]).resolve() == (services.settings.executor_workdir_root / "task-T1").resolve()
    assert call["scrubbed"] == []
    # The command shown has no local path.
    assert out["command"]["argv"][0].lower().startswith("python") and "<path>" in out["command"]["argv"]
    assert not any(str(world.root) in part for part in out["command"]["argv"])

    # The gate decides: no review yet leaves it submitted, a review by someone else then passes it.
    assert services.gates.run_gate("T1", actor="user")["outcome"] == "pending"
    assert services.tasks.get_task("T1")["state"] == "submitted"
    services.gates.review("T1", actor="reviewer", approved=True)
    assert services.gates.run_gate("T1", actor="user")["task"]["state"] == "accepted"


def test_the_permission_follows_the_task_scope_and_the_app_gets_its_own_folder(world):
    out = world.services.executors.run_task("T2", "claude_cli", actor="agent:claude")
    assert out["permission"] == "write" and out["actor"] == "agent:claude"
    (call,) = world.fake.calls("claude")
    assert call["argv"][call["argv"].index("--permission-mode") + 1] == "acceptEdits"
    assert Path(call["cwd"]).name == "task-T2"
    assert world.services.tasks.get_task("T2")["assignee"] == "agent:claude"


def test_a_configured_folder_per_task_or_executor_wins_over_the_default(tmp_path):
    per_task, per_executor = tmp_path / "wt-task", tmp_path / "wt-exec"
    per_task.mkdir(), per_executor.mkdir()
    world = make_world(tmp_path, tmp_path / "bin", declarations={"claude_cli": {"workdir": str(per_executor)}})
    world.services.system.catalog.executor_routing["task_workdirs"] = {"T1": str(per_task)}
    world.services.executors.run_task("T1", "claude_cli")
    world.services.executors.run_task("T2", "claude_cli")
    cwds = [Path(call["cwd"]).resolve() for call in world.fake.calls("claude")]
    assert cwds == [per_task.resolve(), per_executor.resolve()]
    missing = tmp_path / "nowhere"
    world.services.system.catalog.executor_routing["task_workdirs"] = {"T3": str(missing)}
    refused(lambda: world.services.executors.run_task("T3", "claude_cli"), reason="workdir_invalid")
    assert world.services.tasks.get_task("T3")["state"] == "open"


# --------------------------------------------------------------------------- failures are recorded, not retried

def test_a_missing_envelope_fails_the_attempt_with_trimmed_evidence_and_does_not_retry(world):
    services = world.services
    noise = "I looked at the code and wrote no JSON. " * 400
    world.fake.set("claude", stdout=claude_out(noise))

    out = services.executors.run_task("T1", "claude_cli")

    assert out["outcome"] == "failed" and out["result"] is None
    assert 0 < len(out["evidence"]) <= 800 and "no result envelope" in out["evidence"]
    task = services.tasks.get_task("T1")
    assert task["state"] == "paused" and task["attempt"] == 1
    assert "result.submitted" not in event_types(services, "T1") and "task.submitted" not in event_types(services, "T1")
    (finished,) = attempt_events(services, "T1")
    assert finished["outcome"] == "failed" and finished["evidence"] == out["evidence"] and finished["result_ref"] is None
    assert len(world.fake.calls("claude")) == 1                       # no automatic retry loop
    assert out["executor_paused_until"] is None
    assert next(e for e in services.executors.status()["executors"] if e["executor_id"] == "claude_cli")["available"]
    # A failed attempt still recorded its session, so it can be resumed.
    assert services.executors.status("T1")["task"]["sessions"][0]["session_id"] == out["session_id"]


def test_a_result_envelope_that_submit_result_rejects_fails_the_attempt(world):
    services = world.services
    world.fake.set("claude", stdout=claude_out(json.dumps({**RESULT, "attempt": 99})))
    out = services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "failed" and "rejected" in out["evidence"] and "invalid_request" in out["evidence"]
    assert services.tasks.get_task("T1")["state"] == "paused"
    refused(lambda: services.envelopes.get_result("T1"), code="not_found")


def test_a_quota_stop_pauses_the_executor_until_the_configured_time(tmp_path):
    world = make_world(tmp_path, tmp_path / "bin", declarations={"claude_cli": {"quota_pause_minutes": 90}})
    services = world.services
    world.fake.set("claude", stdout="", stderr="Claude AI usage limit reached|1760000000", exit=1)

    out = services.executors.run_task("T1", "claude_cli")

    until = (NOW + timedelta(minutes=90)).isoformat()
    assert out["outcome"] == "quota_exhausted" and out["executor_paused_until"] == until
    assert services.tasks.get_task("T1")["state"] == "paused"
    pause = next(e for e in services.tasks.events() if e["type"] == "executor.paused")
    assert pause["ref"] == "claude_cli" and pause["payload"]["until"] == until and pause["task_id"] == "T1"
    status = services.executors.status()["executors"]
    assert next(e for e in status if e["executor_id"] == "claude_cli")["paused_until"] == until
    assert next(e for e in status if e["executor_id"] == "codex_cli")["paused_until"] is None

    error = refused(lambda: services.executors.run_task("T3", "claude_cli"), reason="executor_paused")
    assert error.details["until"] == until
    assert services.tasks.get_task("T3")["state"] == "open"                      # nothing was claimed
    assert services.executors.run_task("T3", "codex_cli")["outcome"] == "submitted"   # another executor is fine
    services.executors.clock = lambda: NOW + timedelta(minutes=91)
    world.fake.set("claude", stdout=claude_out())
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "submitted"     # paused task, expired pause
    assert len(world.fake.calls("claude")) == 2                                  # the stop did not retry by itself


def test_the_default_quota_pause_comes_from_the_settings(world):
    world.services.settings.executor_quota_pause_minutes = 45
    world.fake.set("claude", stderr="You've hit your usage limit", exit=1)
    out = world.services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "quota_exhausted"
    assert out["executor_paused_until"] == (NOW + timedelta(minutes=45)).isoformat()


def test_an_open_session_block_pauses_the_task_but_not_the_executor(world):
    services = world.services
    world.fake.set("codex", stdout=[json.dumps({"type": "thread.started", "thread_id": THREAD})],
                   stderr=f"Error: thread {THREAD} already has an active writer", exit=1)
    out = services.executors.run_task("T1", "codex_cli")
    assert out["outcome"] == "blocked_open_session" and out["executor_paused_until"] is None
    assert "active writer" in out["evidence"] and services.tasks.get_task("T1")["state"] == "paused"
    assert next(e for e in services.executors.status()["executors"] if e["executor_id"] == "codex_cli")["available"]


def test_a_timeout_stops_the_program_and_pauses_the_task(world):
    world.fake.set("claude", sleep=30, stdout=claude_out())
    out = world.services.executors.run_task("T1", "claude_cli", timeout_seconds=1)
    assert out["outcome"] == "timeout" and out["run_status"] == "timeout"
    assert world.services.tasks.get_task("T1")["state"] == "paused"
    assert any("timed out" in note for note in out["notes"])


def test_a_program_that_cannot_be_found_is_refused_before_the_task_changes(tmp_path):
    world = make_world(tmp_path, tmp_path / "bin")
    config = world.services.system.catalog.executor_routing
    adapter = ClaudeAdapter(command=("definitely-not-a-real-program-9f2",))
    world.services.assignment = AssignmentServices(world.services, registry=ExecutorRegistry(config, [adapter]))
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli"), reason="executor_unavailable")
    assert error.details["code"] == process.EXECUTABLE_NOT_FOUND and str(tmp_path) not in error.message
    assert world.services.tasks.get_task("T1")["state"] == "open"
    assert event_types(world.services, "T1") == ["task.created"]


def test_a_request_the_adapter_refuses_leaves_the_task_alone(world):
    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli", model="bad model; rm -rf /"),
                    code="invalid_request")
    assert error.details["field"] == "request"
    assert world.services.tasks.get_task("T1")["state"] == "open" and world.fake.calls() == []


def test_a_program_that_refuses_to_start_after_the_pre_flight_is_a_failed_attempt(world, monkeypatch):
    services = world.services

    def refuse(request):
        raise process.ProcessLaunchError(process.LAUNCH_FAILED, "the program could not be started: access denied")

    monkeypatch.setattr(world.adapters["claude"], "launch", refuse)
    out = services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "failed" and out["run_status"] == "launch_refused" and out["session_id"] is None
    assert "launch_failed" in out["evidence"] and services.tasks.get_task("T1")["state"] == "paused"
    assert attempt_events(services, "T1")[0]["run_status"] == "launch_refused"
    assert "executor.session_recorded" not in event_types(services, "T1")


def test_an_unexpected_error_does_not_leave_the_task_running(world, monkeypatch):
    def explode(request):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(world.adapters["claude"], "launch", explode)
    with pytest.raises(RuntimeError):
        world.services.executors.run_task("T1", "claude_cli")
    assert world.services.tasks.get_task("T1")["state"] == "paused"


def test_a_group_that_is_not_authorized_runs_nothing(world):
    services = world.services
    services.tasks.update_group("ORQ", actor="user", authorized=False)
    refused(lambda: services.executors.run_task("T1", "claude_cli"))
    assert services.tasks.get_task("T1")["state"] == "open" and world.fake.calls() == []


# --------------------------------------------------------------------------- who may run

def test_only_enabled_executors_with_an_adapter_run(world):
    services = world.services
    for task_id, executor_id, code, reason in (
            ("T1", "manual_app", "invalid_state", "executor_without_adapter"),
            ("T1", "no_such_executor", "not_found", None)):
        refused(lambda: services.executors.run_task(task_id, executor_id), code=code, reason=reason)
    refused(lambda: services.executors.run_task("T1"), code="invalid_request")
    refused(lambda: services.executors.run_task("T1", "bad id!"), code="invalid_request")
    refused(lambda: services.executors.run_task("T1", "claude_cli", timeout_seconds=0), code="invalid_request")
    refused(lambda: services.executors.run_task("NOPE", "claude_cli"), code="not_found")
    assert world.fake.calls() == [] and event_types(services, "T1") == ["task.created"]


def test_a_disabled_executor_is_refused_without_touching_the_task(tmp_path):
    world = make_world(tmp_path, tmp_path / "bin", enabled=("claude_cli",))
    error = refused(lambda: world.services.executors.run_task("T1", "codex_cli"), reason="executor_disabled")
    assert error.details["executor_id"] == "codex_cli"
    assert world.services.tasks.get_task("T1")["state"] == "open" and world.fake.calls() == []
    assert not next(e for e in world.services.executors.status()["executors"]
                    if e["executor_id"] == "codex_cli")["available"]


def test_only_runnable_tasks_run_and_another_assignee_is_refused(world):
    services = world.services
    refused(lambda: services.executors.run_task("T4", "claude_cli"), reason="task_not_runnable")     # blocked
    services.executors.run_task("T1", "claude_cli")
    refused(lambda: services.executors.run_task("T1", "claude_cli"), reason="task_not_runnable")     # submitted
    services.tasks.transition("T3", "claim", actor="agent:other")
    refused(lambda: services.executors.run_task("T3", "claude_cli"))                                 # not the assignee
    assert services.tasks.get_task("T3")["state"] == "claimed"
    assert len(world.fake.calls("claude")) == 1


def test_a_task_that_the_actor_already_claimed_starts_and_runs(world):
    services = world.services
    services.tasks.transition("T3", "claim", actor="agent:me")
    out = services.executors.run_task("T3", "codex_cli", actor="agent:me")
    assert out["outcome"] == "submitted" and out["session_id"] == THREAD
    assert services.envelopes.get_result("T3")["agent"] == "agent:me"


# --------------------------------------------------------------------------- the profile path and the ECO hook

def test_a_profile_lets_the_assignment_preview_choose_and_the_hook_supplies_model_and_effort(world):
    services = world.services
    refused(lambda: services.executors.run_task("T1", profile_id="auto"), reason="assignment_not_selected")  # unverified
    assert services.tasks.get_task("T1")["state"] == "open"
    calls = []

    original = services.executors._routing_selection

    def hook(decision, model, effort, **kwargs):
        calls.append(decision["selected_executor"])
        return original(decision, "claude-eco", effort, **kwargs)

    services.executors._routing_selection = hook
    out = services.executors.run_task("T1", profile_id="auto", operator_context=verified())
    assert out["executor_id"] == "claude_cli" and out["model"] == "claude-eco" and calls == ["claude_cli"]
    (call,) = world.fake.calls("claude")
    assert call["argv"][call["argv"].index("--model") + 1] == "claude-eco"
    other = refused(lambda: services.executors.run_task("T2", "codex_cli", profile_id="auto",
                                                        operator_context=verified()), reason="assignment_not_selected")
    assert other.details["status"] == "blocked"                      # the profile does not offer codex_cli


def test_without_a_decision_model_and_effort_stay_as_given(world):
    out = world.services.executors.run_task("T1", "claude_cli", "claude-opus-4-1", "high")
    assert (out["model"], out["effort"]) == ("claude-opus-4-1", "high")
    (call,) = world.fake.calls("claude")
    assert call["argv"][call["argv"].index("--model") + 1] == "claude-opus-4-1" and "--effort" not in call["argv"]
    assert any("effort ignored" in note for note in out["notes"])


def test_evidence_of_unsupported_models_and_efforts_refuses_them(tmp_path):
    evidence = {"status": "supported", "source": "test", "provenance": "operator_test", "scope": "codex_cli",
                "observed_at": "2026-10-11", "values": ["gpt-ok"]}
    world = make_world(tmp_path, tmp_path / "bin", declarations={"codex_cli": {"model_support": evidence}})
    error = refused(lambda: world.services.executors.run_task("T1", "codex_cli", "gpt-other"), reason="model_not_supported")
    assert error.details["supported"] == ["gpt-ok"]
    assert world.services.executors.run_task("T1", "codex_cli", "gpt-ok")["model"] == "gpt-ok"
    (call,) = world.fake.calls("codex")
    assert call["argv"][call["argv"].index("-m") + 1] == "gpt-ok"


# --------------------------------------------------------------------------- resume

def test_resume_continues_the_stored_session_of_a_paused_task(world):
    services = world.services
    world.fake.set("claude", stdout=claude_out("no envelope here"))
    first = services.executors.run_task("T1", "claude_cli", "claude-opus-4-1")
    assert first["outcome"] == "failed" and services.tasks.get_task("T1")["state"] == "paused"
    world.fake.set("claude", stdout=claude_out())

    again = services.executors.resume_task("T1")

    assert again["outcome"] == "submitted" and again["mode"] == "resume" and again["session_id"] == first["session_id"]
    assert again["attempt"] == 1 and again["model"] == "claude-opus-4-1"        # the recorded model continues
    second = world.fake.calls("claude")[1]
    assert second["argv"][second["argv"].index("--resume") + 1] == first["session_id"] and "--session-id" not in second["argv"]
    assert services.tasks.get_task("T1")["state"] == "submitted"
    assert event_types(services, "T1").count("executor.session_recorded") == 1
    assert "task.resumed" in event_types(services, "T1")


def test_resume_after_the_gate_asks_for_changes_starts_a_new_attempt_in_the_same_session(world):
    services = world.services
    services.gates.set_task_gate("T1", ["review"], actor="user")
    first = services.executors.run_task("T1", "claude_cli")
    services.gates.review("T1", actor="reviewer", approved=False, comment="not enough")
    assert services.gates.run_gate("T1", actor="user")["task"]["state"] == "needs_changes"

    again = services.executors.resume_task("T1")

    assert again["outcome"] == "submitted" and again["attempt"] == 2 and again["session_id"] == first["session_id"]
    assert [r["attempt"] for r in services.envelopes.list_results("T1")] == [2, 1]
    assert services.tasks.get_task("T1")["state"] == "submitted"


def test_only_sessions_the_framework_created_are_resumed(world):
    services = world.services
    services.tasks.transition("T3", "claim", actor="executor:claude_cli")
    services.tasks.transition("T3", "start", actor="executor:claude_cli")
    services.tasks.transition("T3", "pause", actor="executor:claude_cli")
    refused(lambda: services.executors.resume_task("T3"), reason="no_stored_session")        # nothing recorded
    refused(lambda: services.executors.open_target("T3"), reason="no_stored_session")
    stranger = "99999999-8888-4777-8666-555555555555"
    assert services.executors.session_guard(stranger) is False and services.executors.session_guard("nope") is False
    # The Copilot adapter asks the service's guard: a session the framework did not store is refused.
    request = ExecutorLaunchRequest("T1", build_task_envelope(task_id="T1", group_id="ORQ", revision=1, attempt=1,
                                                              assignee="a", status="running", objective="x"),
                                    world.root)
    with pytest.raises(CopilotAdapterError) as caught:
        world.adapters["copilot"].build_resume_command(request, stranger)
    assert caught.value.code == "session_not_created_by_framework"
    out = services.executors.run_task("T1", "copilot_cli")
    assert services.executors.session_guard(out["session_id"]) is True
    assert "--resume=" + out["session_id"] in world.adapters["copilot"].build_resume_command(request, out["session_id"])


def test_a_session_opened_in_an_app_is_not_resumed_for_twelve_hours(world):
    services = world.services
    world.fake.set("claude", stdout=claude_out("no envelope"))
    first = services.executors.run_task("T1", "claude_cli")
    sid = first["session_id"]
    assert services.executors.session_guard(sid) is True
    world.fake.set("claude", stdout=claude_out())

    opened = services.executors.record_app_open(sid, source="claude", actor="gui")

    blocked_until = (NOW + timedelta(hours=12)).isoformat()
    assert opened["resume_blocked_until"] == blocked_until and services.executors.session_guard(sid) is False
    error = refused(lambda: services.executors.resume_task("T1"), reason="session_open_in_app")
    assert error.details["blocked_until"] == blocked_until
    assert services.executors.status("T1")["task"]["resume_blocked_until"] == blocked_until
    assert services.tasks.get_task("T1")["state"] == "paused" and len(world.fake.calls("claude")) == 1
    event = next(e for e in services.tasks.events(task_id="T1") if e["type"] == "session.opened_in_app")
    assert event["ref"] == sid and event["actor"] == "gui" and event["payload"]["source"] == "claude"

    services.executors.clock = lambda: NOW + timedelta(hours=11, minutes=59)
    refused(lambda: services.executors.resume_task("T1"), reason="session_open_in_app")
    services.executors.clock = lambda: NOW + timedelta(hours=12, minutes=1)
    assert services.executors.session_guard(sid) is True
    assert services.executors.resume_task("T1")["outcome"] == "submitted"


def test_recording_an_open_validates_its_input_and_keeps_an_unknown_session_out_of_any_task(world):
    executors = world.services.executors
    refused(lambda: executors.record_app_open("not-a-uuid", source="claude"), code="invalid_request")
    refused(lambda: executors.record_app_open(THREAD, source="Bad Source"), code="invalid_request")
    executors.record_app_open(THREAD.upper(), source="codex")
    event = next(e for e in world.services.tasks.events() if e["type"] == "session.opened_in_app")
    assert event["ref"] == THREAD and event["task_id"] == "" and event["actor"] == "gui"


# --------------------------------------------------------------------------- open

@pytest.mark.parametrize("executor_id, kind, value, confirm", [
    ("codex_cli", "url", f"codex://threads/{THREAD}", False),
    ("copilot_cli", "url", f"ghapp://sessions/{COPILOT_SID}", True),
])
def test_open_target_is_the_adapters_target_for_the_recorded_session(world, executor_id, kind, value, confirm):
    services = world.services
    out = services.executors.run_task("T1", executor_id)
    adapter = world.adapters[executor_id.removesuffix("_cli")]
    expected = adapter.open_target(out["session_id"])
    assert out["open_target"] == {"kind": kind, "value": value, "needs_confirmation": confirm}

    opened = services.executors.open_target("T1")

    assert opened["target"] == {"kind": expected.kind, "value": expected.value,
                                "needs_confirmation": expected.needs_confirmation}
    assert opened["target"]["value"] == value and opened["session_id"] == out["session_id"]
    assert opened["resume_blocked_until"] == (NOW + timedelta(hours=12)).isoformat()
    assert services.executors.session_guard(out["session_id"]) is False     # handing out the target counts as an open


def test_open_target_of_claude_is_the_command_the_gui_launches(world):
    out = world.services.executors.run_task("T1", "claude_cli")
    opened = world.services.executors.open_target("T1", actor="agent:me")
    assert opened["target"] == {"kind": "command", "value": ["claude", "--desktop", "--resume", out["session_id"]],
                                "needs_confirmation": False}
    event = next(e for e in world.services.tasks.events(task_id="T1") if e["type"] == "session.opened_in_app")
    assert event["actor"] == "agent:me" and event["payload"]["source"] == "claude"


# --------------------------------------------------------------------------- the lifecycle stays the single function

def test_the_service_only_uses_the_lifecycle_and_submit_result(world):
    services = world.services
    actions, real = [], services.tasks.transition

    def spy(task_id, action, **kwargs):
        actions.append(action)
        return real(task_id, action, **kwargs)

    services.tasks.transition = spy
    services.gates.set_task_gate("T1", ["review"], actor="user")
    services.executors.run_task("T1", "claude_cli")                                         # claim, start, submit_result
    world.fake.set("claude", stdout=claude_out("no envelope"))
    services.executors.run_task("T3", "codex_cli")                                          # codex success path
    services.executors.run_task("T2", "claude_cli")                                         # claim, start, pause
    world.fake.set("claude", stdout=claude_out())
    services.executors.resume_task("T2")                                                    # resume
    assert set(actions) <= {"claim", "start", "pause", "resume"} and {"claim", "start", "pause", "resume"} <= set(actions)
    kinds = {e["type"] for e in services.tasks.events() if e["type"].startswith("task.")}
    assert not kinds & {"task.accepted", "task.failed", "task.cancelled", "task.reopened", "task.changes_requested"}
    assert services.tasks.get_task("T1")["state"] == "submitted"                            # the gate has not run

    source = Path(executor_module.__file__).read_text(encoding="utf-8")
    for forbidden in ("_apply(", "gate_pass", "gate_start", '"accept"', '"fail"', '"cancel"',
                      '"reopen"', '"request_changes"', "UPDATE tasks"):
        assert forbidden not in source, forbidden
    tree = ast.parse(source)
    direct_updates = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Attribute) and node.func.attr == "update_task"]
    # The watcher may atomically update execution metadata, never task lifecycle fields through the store.
    assert all(len(call.args) == 2 and {keyword.arg for keyword in call.keywords} == {"execution", "expected_revision"}
               for call in direct_updates)


# --------------------------------------------------------------------------- wiring

def test_the_bundled_catalog_wires_the_adapters_and_keeps_every_cli_executor_disabled(tmp_path):
    services = make_services(tmp_path / "local")
    registry = services.assignment.registry
    expected = {"codex_cli": CodexExecutorAdapter, "claude_cli": ClaudeAdapter, "copilot_cli": CopilotAdapter}
    assert [type(adapter) for adapter in default_adapters()] == [CodexExecutorAdapter, ClaudeAdapter, CopilotAdapter]
    for executor_id in CLI_EXECUTORS:
        descriptor = registry.get(executor_id)
        assert descriptor.enabled is False and descriptor.adapter_id == executor_id
        assert isinstance(registry.resolve(executor_id), expected[executor_id])
    status = services.executors.status()["executors"]
    assert [e["executor_id"] for e in status] == list(CLI_EXECUTORS)
    assert all(e["enabled"] is False and e["available"] is False for e in status)
    services.tasks.create_group("ORQ", "G", actor="user", authorized=True)
    services.tasks.create_task("ORQ", "T1", "Task", actor="user")
    refused(lambda: services.executors.run_task("T1", "codex_cli"), reason="executor_disabled")
    assert services.tasks.get_task("T1")["state"] == "open"
    assert isinstance(services.executors, ExecutorServices)


def test_the_copilot_adapter_of_the_default_wiring_uses_the_service_guard(tmp_path):
    services = make_services(tmp_path / "local")
    copilot = services.assignment.registry.resolve("copilot_cli")
    assert copilot._session_guard == services.executors.session_guard


# --------------------------------------------------------------------------- parity: facade, HTTP and CLI

class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc_info) -> bool:
        return False


SCRIPT: list[tuple[str, dict]] = [
    ("status", {}),
    ("run", {"task_id": "T1", "executor_id": "codex_cli"}),
    ("run", {"task_id": "T1", "executor_id": "codex_cli"}),                      # submitted: not runnable
    ("run", {"task_id": "T2", "executor_id": "claude_cli"}),                     # disabled in this world
    ("run", {"task_id": "NOPE", "executor_id": "codex_cli"}),
    ("run", {"task_id": "T3", "executor_id": "codex_cli", "model": "bad model"}),
    ("run", {"task_id": "T3", "executor_id": "copilot_cli", "actor": "agent:copilot"}),
    ("status", {"task_id": "T1"}),
    ("status", {"task_id": "NOPE"}),
    ("open", {"task_id": "T1"}),
    ("open", {"task_id": "T2"}),                                                 # nothing recorded
    ("resume", {"task_id": "T1"}),                                               # opened in the app a moment ago
    ("resume", {"task_id": "T2"}),
    ("status", {"task_id": "T1"}),
    ("resume-due", {}),                                                          # CNT-T04: nothing is held
    ("resume-due", {"task_id": "NOPE", "dry_run": True}),
    ("lift-pause", {"executor_id": "codex_cli"}),                                # not paused: nothing to lift
    ("lift-pause", {"executor_id": "NOPE"}),
]


def run_facade(services, op: str, args: dict):
    executors = services.executors
    args = dict(args)
    if op == "status":
        return executors.status(args.get("task_id"))
    if op == "run":
        return executors.run_task(args.pop("task_id"), args.pop("executor_id", None), args.pop("model", None),
                                  args.pop("effort", None), args.pop("actor", None))
    if op == "open":
        return executors.open_target(args["task_id"])
    if op == "resume-due":
        return executors.resume_due(task_id=args.get("task_id"), dry_run=args.get("dry_run", False))
    if op == "lift-pause":
        return executors.lift_pause(args["executor_id"])
    return executors.resume_task(args["task_id"])


def run_http(client: TestClient, op: str, args: dict) -> tuple[int, object]:
    if op == "status":
        response = client.get("/api/v1/executors/status", params={"task": args["task_id"]} if "task_id" in args else None)
    else:
        response = client.post(f"/api/v1/executors/{op}", json=args)
    return response.status_code, response.json()


def cli_args(op: str, args: dict) -> list[str]:
    if op == "status":
        return ["executors", "status"] + (["--task", args["task_id"]] if "task_id" in args else [])
    if op == "resume-due":
        return ["executors", op] + (["--task", args["task_id"]] if "task_id" in args else []) \
            + (["--dry-run"] if args.get("dry_run") else [])
    if op == "lift-pause":
        return ["executors", op, args["executor_id"]]
    argv = ["executors", op, args["task_id"]]
    for flag, key in (("--executor", "executor_id"), ("--model", "model"), ("--effort", "effort"), ("--actor", "actor")):
        if key in args:
            argv += [flag, args[key]]
    return argv


def normalize(value):
    """Drop what is time or random; everything else must be identical across interfaces."""
    if isinstance(value, dict):
        return {k: ("<time>" if k.endswith("_until") and v else normalize(v)) for k, v in value.items()
                if k not in ("created_at", "updated_at", "imported_at")}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return UUID.sub("<uuid>", value) if isinstance(value, str) else value


def test_facade_http_and_cli_give_the_same_results(monkeypatch, capsys, tmp_path):
    bin_folder = tmp_path / "bin"
    Fake(bin_folder)
    enabled = ("codex_cli", "copilot_cli")
    facade = make_world(tmp_path / "facade", bin_folder, enabled=enabled).services
    http = make_world(tmp_path / "http", bin_folder, enabled=enabled).services
    through_cli = make_world(tmp_path / "cli", bin_folder, enabled=enabled).services
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(cli_client))
    with TestClient(create_app(services=http), base_url="http://127.0.0.1") as http_client, \
            TestClient(create_app(services=through_cli), base_url="http://127.0.0.1") as cli_client:
        for step, (op, args) in enumerate(SCRIPT):
            try:
                expected = ("ok", normalize(run_facade(facade, op, args)))
            except ServiceError as error:
                expected = ("error", (error.status, error.code))
            status, body = run_http(http_client, op, args)
            if expected[0] == "ok":
                assert status == 200 and normalize(body) == expected[1], (step, op, body)
            else:
                assert (status, body["detail"]["code"]) == expected[1], (step, op, body)
            code = 0
            try:
                cli.main(cli_args(op, args))
            except SystemExit as exit_:
                code = exit_.code
            captured = capsys.readouterr()
            if expected[0] == "ok":
                assert code == 0 and normalize(json.loads(captured.out)) == expected[1], (step, op, captured.out)
            else:
                assert code == 1 and f"HTTP {expected[1][0]}: {expected[1][1]}:" in captured.err, (step, op, captured.err)
    ok = [step for step, (op, _) in enumerate(SCRIPT) if op == "run"]
    assert len(ok) >= 4
    # Three independent stacks recorded the same thing.
    assert [e["type"] for e in facade.tasks.events()] == [e["type"] for e in http.tasks.events()] \
        == [e["type"] for e in through_cli.tasks.events()]


def test_the_script_covers_every_public_operation():
    operations = {"status": "status", "run": "run_task", "open": "open_target", "resume": "resume_task",
                  "resume-due": "resume_due", "lift-pause": "lift_pause"}
    assert {op for op, _ in SCRIPT} == set(operations)
    public = {name for name, member in vars(ExecutorServices).items() if callable(member) and not name.startswith("_")}
    assert set(operations.values()) <= public
    # The checkpoint operations (CNT-T03) are for the other services (hold and hand-off), not for HTTP or the CLI.
    # The waits by condition (CNT-T12) are for the watcher process (CNT-T13), which calls the services itself.
    assert public - set(operations.values()) == {"build_adapters", "record_app_open", "session_guard",
                                                 "checkpoint_limit", "latest_checkpoint", "retire_session",
                                                 "hold_on_condition", "record_approval", "check_waits", "resume_met"}

def test_structured_claude_permission_denial_pauses_only_the_task(world):
    world.fake.set("claude", stdout=claude_out(permission_denials=[
        {"tool_name": "Bash", "tool_input": {"command": "private synthetic input"}},
    ]))

    out = world.services.executors.run_task("T1", "claude_cli")

    task = world.services.tasks.get_task("T1")
    assert out["outcome"] == "permission_blocked" and out["run_status"] == "failed"
    assert task["state"] == "paused"
    assert task["continuity"]["attention"]["kind"] == "permission_blocked"
    assert task["continuity"]["attention"]["deadline"] is None
    refused(lambda: world.services.envelopes.get_result("T1"), code="not_found")
    assert "permission_denied: Bash" in out["notes"]
    assert "private synthetic input" not in repr(out)
    assert next(row for row in world.services.executors.status()["executors"]
                if row["executor_id"] == "claude_cli")["available"] is True
    events = [event for event in world.services.tasks.events(task_id="T1") if event["type"] == "executor.attention"]
    assert len(events) == 1 and events[0]["payload"]["kind"] == "permission_blocked"
    assert events[0]["payload"]["cleared"] is False


def test_attention_updates_are_idempotent_and_clear_corrupt_legacy_value(world):
    services = world.services
    services.tasks.transition("T1", "claim", actor="executor:claude_cli")
    running = services.tasks.transition("T1", "start", actor="executor:claude_cli")
    attempt = running["attempt"]
    service = services.executors
    attention = {"kind": "stalled", "since": NOW.isoformat(), "last_output_at": NOW.isoformat(),
                 "source": "stdout", "deadline": None}
    assert service._write_attention("T1", attempt, "claude_cli", attention)
    assert not service._write_attention("T1", attempt, "claude_cli", attention)
    events = [event for event in services.tasks.events(task_id="T1") if event["type"] == "executor.attention"]
    assert len(events) == 1
    raw = service._store.get_task("T1")
    execution = dict(raw.get("execution") or {})
    continuity = dict(execution.get("continuity") or {})
    continuity["attention"] = "legacy-corrupt-value"
    execution["continuity"] = continuity
    with service._store.transaction() as conn:
        service._store.update_task(conn, "T1", expected_revision=raw["revision"], execution=execution)
    assert service._write_attention("T1", attempt, "claude_cli", None)
    assert services.tasks.get_task("T1").get("continuity", {}).get("attention") is None
    events = [event for event in services.tasks.events(task_id="T1") if event["type"] == "executor.attention"]
    assert len(events) == 2 and events[-1]["payload"]["cleared"] is True
def test_live_attention_clears_on_observed_activity_but_not_missing_claude_metadata(world):
    services = world.services
    services.tasks.transition("T1", "claim", actor="executor:claude_cli")
    running = services.tasks.transition("T1", "start", actor="executor:claude_cli")
    service = services.executors
    attempt = running["attempt"]
    attention = {"kind": "stalled", "since": NOW.isoformat(), "last_output_at": NOW.isoformat(),
                 "source": "stdout", "deadline": (NOW + timedelta(minutes=15)).isoformat()}
    service._write_attention("T1", attempt, "claude_cli", attention)
    activity = process.ProcessActivity(last_output_at=None, started_at=NOW.isoformat(), idle_seconds=1200)
    service._session_activity = lambda adapter, session_id: None
    service._observe_process(task_id="T1", attempt=attempt, executor_id="claude_cli",
                             adapter=SimpleNamespace(adapter_id="claude_cli"), session_id="synthetic",
                             process_activity=activity, stall_seconds=900, deadline=attention["deadline"])
    assert services.tasks.get_task("T1")["continuity"]["attention"] == attention

    service._observe_process(task_id="T1", attempt=attempt, executor_id="codex_cli",
                             adapter=SimpleNamespace(adapter_id="codex_cli"), session_id="synthetic",
                             process_activity=process.ProcessActivity(NOW.isoformat(), NOW.isoformat(), 0),
                             stall_seconds=900, deadline=attention["deadline"])
    task = services.tasks.get_task("T1")
    assert task["state"] == "running" and task.get("continuity", {}).get("attention") is None
    events = [event for event in services.tasks.events(task_id="T1") if event["type"] == "executor.attention"]
    assert len(events) == 2 and events[-1]["payload"]["cleared"] is True

    service._observe_process(task_id="T1", attempt=attempt, executor_id="codex_cli",
                             adapter=SimpleNamespace(adapter_id="codex_cli"), session_id="synthetic",
                             process_activity=process.ProcessActivity(None, (NOW - timedelta(minutes=20)).isoformat(), 1200),
                             stall_seconds=900, deadline=attention["deadline"])
    task = services.tasks.get_task("T1")
    assert task["state"] == "running"
    assert task["continuity"]["attention"]["last_output_at"] is None
    assert task["continuity"]["attention"]["source"] == "process_start"
    service._session_activity = lambda adapter, session_id: {
        "open_tests": True, "last_output_at": NOW.isoformat(), "source": "session_metadata"}
    service._observe_process(task_id="T1", attempt=attempt, executor_id="codex_cli",
                             adapter=SimpleNamespace(adapter_id="codex_cli"), session_id="synthetic",
                             process_activity=activity, stall_seconds=900, deadline=attention["deadline"])
    assert services.tasks.get_task("T1").get("continuity", {}).get("attention") is None
def test_codex_live_id_is_read_from_the_bounded_stdout_probe(world, monkeypatch):
    service = world.services.executors
    observed = []
    real_observe = service._observe_process

    def spy(**kwargs):
        observed.append(kwargs["session_id"])
        return real_observe(**kwargs)

    monkeypatch.setattr(service, "_observe_process", spy)
    world.fake.set("codex", stdout=codex_out(),
                   live_stdout=json.dumps({"type": "thread.started", "thread_id": THREAD}) + "\n",
                   live_sleep=1.3)

    out = service.run_task("T1", "codex_cli")

    assert out["outcome"] == "submitted", out
    assert THREAD in observed
