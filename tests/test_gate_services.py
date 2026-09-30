# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Gate services (ORQ-T13): gates, reviews and project commands over the task transition function.

Project commands run the current Python interpreter with harmless scripts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError

PY = json.dumps(sys.executable)
# Exits 1 for the task id "BAD", 0 otherwise, and prints the task id.
TEST_SCRIPT = json.dumps("import sys; print('checked', sys.argv[1]); sys.exit(1 if sys.argv[1] == 'BAD' else 0)")


def make_services(local: Path, commands: str | None = None) -> ApplicationServices:
    local.mkdir(parents=True, exist_ok=True)
    if commands is not None:
        (local / "project_commands.toml").write_text(commands, encoding="utf-8")
    settings = Settings(_env_file=None, local_dir=local, worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True)


DEFAULT_COMMANDS = f"""
[commands.test]
argv = [{PY}, "-c", {TEST_SCRIPT}, "{{task_id}}"]
timeout = 60
"""


@pytest.fixture
def services(tmp_path: Path) -> ApplicationServices:
    services = make_services(tmp_path / "local", DEFAULT_COMMANDS)
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    return services


def deliver(services, task_id: str, agent: str = "agent:a") -> None:
    for action in ("claim", "start", "submit"):
        services.tasks.transition(task_id, action, actor=agent)


def error(call) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value


def test_declaring_gates_normalizes_aliases_and_rejects_unknown_checks(services):
    tasks, gates = services.tasks, services.gates
    tasks.create_task("G", "A", "A", actor="user")
    assert gates.get_gate("A") == {"task_id": "A", "checks": [], "source": "none", "last_run": []}
    assert gates.set_group_gate("G", ["tests"], actor="user")["default_gate"] == ["command:test"]
    assert gates.get_gate("A")["source"] == "group"
    own = gates.set_task_gate("A", ["review", "commit"], actor="user")
    assert (own["checks"], own["source"]) == (["review", "command:commit"], "task")
    assert tasks.get_task("A")["revision"] == 2
    for bad in (["deploy"], ["command:Bad"], ["tests", "command:test"], "tests", ["review"] * 11):
        assert error(lambda: gates.set_task_gate("A", bad, actor="user")).code == "invalid_request"
    assert error(lambda: gates.set_group_gate("NOPE", [], actor="user")).code == "not_found"
    assert error(lambda: gates.get_gate("NOPE")).code == "not_found"
    assert [e["type"] for e in tasks.events(task_id="A")][-1] == "gate.updated"


def test_a_passing_gate_accepts_and_releases_dependents(services):
    tasks, gates = services.tasks, services.gates
    gates.set_group_gate("G", ["tests"], actor="user")
    tasks.create_task("G", "A", "A", actor="user")
    tasks.create_task("G", "B", "B", actor="user", depends_on=["A"])
    deliver(services, "A")
    manual = error(lambda: tasks.transition("A", "accept", actor="user"))
    assert manual.code == "invalid_state" and manual.details["gate"] == ["command:test"]
    result = gates.run_gate("A", actor="user", cause="round 1")
    assert (result["outcome"], result["run"], result["task"]["status"]) == ("passed", 1, "accepted")
    stored = result["results"][0]
    assert (stored["check_name"], stored["status"], stored["command"], stored["exit_code"]) == ("command:test", "passed", "test", 0)
    assert "checked A" in stored["output_excerpt"]
    assert tasks.get_task("B")["status"] == "ready"
    types = [e["type"] for e in tasks.events(task_id="A")]
    assert types[-3:] == ["gate.started", "gate.check_passed", "gate.passed"]
    assert [e["task_id"] for e in tasks.events() if e["type"] == "task.ready"] == ["B"]


def test_a_failing_gate_asks_for_changes_and_stops_the_branch(services):
    tasks, gates = services.tasks, services.gates
    gates.set_group_gate("G", ["tests", "review"], actor="user")
    tasks.create_task("G", "BAD", "Broken", actor="user")
    tasks.create_task("G", "NEXT", "Next", actor="user", depends_on=["BAD"])
    deliver(services, "BAD")
    result = gates.run_gate("BAD", actor="user")
    assert (result["outcome"], result["task"]["status"]) == ("failed", "needs_changes")
    assert [r["check_name"] for r in result["results"]] == ["command:test"]  # review never ran
    assert result["results"][0]["exit_code"] == 1
    assert tasks.get_task("NEXT")["status"] == "blocked"
    # A second attempt numbers its gate runs from 1 again.
    tasks.transition("BAD", "start", actor="agent:a")
    tasks.transition("BAD", "submit", actor="agent:a")
    assert gates.run_gate("BAD", actor="user")["run"] == 1
    assert [(r["attempt"], r["run"]) for r in gates.results("BAD")] == [(1, 1), (2, 1)]
    assert [r["attempt"] for r in gates.results("BAD", attempt=2)] == [2]


def test_review_check_needs_another_actor_and_the_last_decision_wins(services):
    tasks, gates = services.tasks, services.gates
    tasks.create_task("G", "A", "A", actor="user")
    gates.set_task_gate("A", ["review"], actor="user")
    assert error(lambda: gates.review("A", actor="agent:r", approved=True)).code == "invalid_state"  # not submitted
    deliver(services, "A")
    pending = gates.run_gate("A", actor="user")
    assert (pending["outcome"], pending["task"]["status"], pending["results"][0]["status"]) == ("pending", "submitted", "pending")
    assert error(lambda: gates.review("A", actor="agent:a", approved=True)).code == "invalid_state"  # the assignee
    gates.review("A", actor="agent:r", approved=False, comment="missing docs")
    assert gates.run_gate("A", actor="user")["outcome"] == "failed"
    tasks.transition("A", "start", actor="agent:a")
    tasks.transition("A", "submit", actor="agent:a")
    gates.review("A", actor="agent:r", approved=False)
    gates.review("A", actor="agent:r", approved=True)
    assert gates.run_gate("A", actor="user")["outcome"] == "passed"


def test_configuration_errors_return_the_task_to_submitted(tmp_path):
    services = make_services(tmp_path / "local", "[commands.test]\nargv = ['x', '{secret}']")
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    services.tasks.create_task("G", "A", "A", actor="user")
    services.gates.set_task_gate("A", ["tests"], actor="user")
    deliver(services, "A")
    result = services.gates.run_gate("A", actor="user")
    assert (result["outcome"], result["task"]["status"]) == ("error", "submitted")
    assert "Invalid project commands file" in result["results"][0]["output_excerpt"]
    assert error(lambda: services.gates.project_commands()).code == "invalid_request"


def test_an_unconfigured_command_is_an_error_and_a_timeout_a_failure(tmp_path):
    services = make_services(tmp_path / "local", f"""
[commands.slow]
argv = [{PY}, "-c", "import time; time.sleep(30)"]
timeout = 1
""")
    tasks, gates = services.tasks, services.gates
    tasks.create_group("G", "Group", actor="user", authorized=True)
    for task_id, check in (("MISSING", "command:lint"), ("SLOW", "command:slow")):
        tasks.create_task("G", task_id, task_id, actor="user")
        gates.set_task_gate(task_id, [check], actor="user")
        deliver(services, task_id)
    assert gates.run_gate("MISSING", actor="user")["task"]["status"] == "submitted"
    slow = gates.run_gate("SLOW", actor="user")
    assert (slow["outcome"], slow["task"]["status"]) == ("failed", "needs_changes")
    assert gates.project_commands() == [{"name": "slow", "timeout": 1, "cwd": str((tmp_path / "local").parent.resolve())}]


def test_run_gate_needs_a_submitted_task_with_a_gate(services):
    tasks, gates = services.tasks, services.gates
    tasks.create_task("G", "A", "A", actor="user")
    deliver(services, "A")
    no_gate = error(lambda: gates.run_gate("A", actor="user"))
    assert no_gate.code == "invalid_state"
    assert tasks.transition("A", "accept", actor="user")["status"] == "accepted"  # no gate: manual accept still works
    tasks.create_task("G", "B", "B", actor="user")
    gates.set_task_gate("B", ["tests"], actor="user")
    assert error(lambda: gates.run_gate("B", actor="user")).details["state"] == "open"
    assert error(lambda: gates.run_gate("NOPE", actor="user")).code == "not_found"


def test_gate_actions_are_not_public_and_abort_returns_a_stuck_task(services):
    tasks, gates, store = services.tasks, services.gates, services.tasks.store
    tasks.create_task("G", "A", "A", actor="user")
    gates.set_task_gate("A", ["tests"], actor="user")
    deliver(services, "A")
    for action in ("gate_start", "gate_pass", "gate_fail", "gate_return"):
        assert error(lambda: tasks.transition("A", action, actor="user")).code == "invalid_request"
    # Simulate a crash in the middle of a gate run.
    with store.transaction() as conn:
        tasks._apply(conn, "A", "gate_start", actor="user", gate=True, event="gate.started")
    assert tasks.get_task("A")["status"] == "validating"
    for action in ("cancel", "fail", "unclaim"):
        assert error(lambda: tasks.transition("A", action, actor="user")).code == "invalid_state"
    assert error(lambda: gates.abort_gate("A", actor="user", cause="")).code == "invalid_request"
    back = gates.abort_gate("A", actor="user", cause="runner crashed")
    assert back["status"] == "submitted"
    assert error(lambda: gates.abort_gate("A", actor="user", cause="again")).code == "invalid_state"


def test_validating_still_satisfies_submitted_dependencies(services):
    tasks, gates = services.tasks, services.gates
    tasks.create_task("G", "A", "A", actor="user")
    tasks.create_task("G", "B", "B", actor="user", depends_on=[{"task_id": "A", "requires": "submitted"}])
    gates.set_task_gate("A", ["tests"], actor="user")
    deliver(services, "A")
    assert tasks.get_task("B")["status"] == "ready"
    gates.run_gate("A", actor="user")
    assert [e["task_id"] for e in tasks.events() if e["type"] == "task.ready"] == ["B"]  # released once


def test_reviews_are_rejected_for_bad_input(services):
    tasks, gates = services.tasks, services.gates
    tasks.create_task("G", "A", "A", actor="user")
    deliver(services, "A")
    assert error(lambda: gates.review("A", actor="agent:r", approved="yes")).code == "invalid_request"
    assert error(lambda: gates.review("NOPE", actor="agent:r", approved=True)).code == "not_found"
    assert error(lambda: gates.results("A", attempt=-1)).code == "invalid_request"
