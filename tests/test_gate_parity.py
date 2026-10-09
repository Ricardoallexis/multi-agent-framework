# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Task gates end to end and facade/HTTP/CLI parity (ORQ-T15), in Mock mode.

Project commands run the current Python interpreter. The ``test`` command fails
for the task ``BAD`` and for the first attempt of the task ``T2``; it passes
otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, GateServices, ServiceError

PY = json.dumps(sys.executable)
SCRIPT = json.dumps(
    "import sys; task, attempt = sys.argv[1], sys.argv[2]; print('checking', task, attempt); "
    "sys.exit(1 if task == 'BAD' or (task == 'T2' and attempt == '1') else 0)")
COMMANDS = f"""
[commands.test]
argv = [{PY}, "-c", {SCRIPT}, "{{task_id}}", "{{attempt}}"]
timeout = 60
"""


def make_services(local: Path) -> ApplicationServices:
    local.mkdir(parents=True, exist_ok=True)
    (local / "project_commands.toml").write_text(COMMANDS, encoding="utf-8")
    settings = Settings(_env_file=None, local_dir=local, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings)


class _WithoutTimeout:
    """The shared TestClient as the CLI sees it, minus the ``timeout`` argument.

    ``gate run`` passes ``timeout=None`` to wait for long project commands; the TestClient has
    no timeout to apply and warns about any that it is given.
    """

    def __init__(self, client: TestClient):
        self._client = client

    def request(self, method: str, url: str, **kwargs):
        kwargs.pop("timeout", None)
        return self._client.request(method, url, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._client, name)


class _ClientContext:
    """Keep the shared TestClient open across the CLI's per-request context."""

    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> _WithoutTimeout:
        return _WithoutTimeout(self.client)

    def __exit__(self, *exc_info) -> bool:
        return False


def deliver(tasks, task_id: str, agent: str = "agent:a") -> None:
    for action in ("claim", "start", "submit"):
        tasks.transition(task_id, action, actor=agent)


# -------- end to end over HTTP --------

def test_group_gate_drives_the_branch_over_http(tmp_path):
    services = make_services(tmp_path / "local")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as http:
        def post(path, body):
            response = http.post(path, json=body)
            return response.status_code, response.json()

        assert post("/api/v1/task-groups", {"id": "ORQ", "title": "Gated", "actor": "user", "authorized": True})[0] == 201
        assert http.put("/api/v1/task-groups/ORQ/gate", json={"checks": ["tests"], "actor": "user"}).status_code == 200
        post("/api/v1/tasks", {"group_id": "ORQ", "id": "T1", "title": "One", "actor": "user"})
        post("/api/v1/tasks", {"group_id": "ORQ", "id": "T2", "title": "Two", "actor": "user", "depends_on": ["T1"]})
        post("/api/v1/tasks", {"group_id": "ORQ", "id": "T3", "title": "Three", "actor": "user", "depends_on": ["T2"]})

        def deliver_http(task_id):
            for action in ("claim", "start", "submit"):
                status, _ = post(f"/api/v1/tasks/{task_id}/transitions", {"action": action, "actor": "agent:a"})
                assert status == 200

        deliver_http("T1")
        # With a gate, accepting by hand is refused (criterion J).
        status, body = post("/api/v1/tasks/T1/transitions", {"action": "accept", "actor": "user"})
        assert (status, body["detail"]["code"]) == (409, "invalid_state")
        status, body = post("/api/v1/tasks/T1/gate/run", {"actor": "user"})
        assert (status, body["outcome"], body["task"]["status"]) == (200, "passed", "accepted")
        assert http.get("/api/v1/tasks/T2").json()["status"] == "ready"

        # T2 fails its first attempt: the branch stops (criterion L).
        deliver_http("T2")
        status, body = post("/api/v1/tasks/T2/gate/run", {"actor": "user"})
        assert (body["outcome"], body["task"]["status"], body["results"][0]["exit_code"]) == ("failed", "needs_changes", 1)
        assert http.get("/api/v1/tasks/T3").json()["status"] == "blocked"

        # The second attempt passes and releases T3; results are kept per attempt (criterion O).
        post("/api/v1/tasks/T2/transitions", {"action": "start", "actor": "agent:a"})
        post("/api/v1/tasks/T2/transitions", {"action": "submit", "actor": "agent:a"})
        assert post("/api/v1/tasks/T2/gate/run", {"actor": "user"})[1]["outcome"] == "passed"
        assert http.get("/api/v1/tasks/T3").json()["status"] == "ready"
        results = http.get("/api/v1/tasks/T2/gate/results").json()
        assert [(r["attempt"], r["run"], r["status"]) for r in results] == [(1, 1, "failed"), (2, 1, "passed")]
        assert "checking T2 2" in results[1]["output_excerpt"]

        events = [e["type"] for e in http.get("/api/v1/team-events", params={"task": "T2"}).json()]
        assert events.count("gate.started") == 2 and events[-1] == "gate.passed"


def test_requests_cannot_bring_commands_and_unknown_names_do_not_run(tmp_path):
    services = make_services(tmp_path / "local")
    tasks, gates = services.tasks, services.gates
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "A", "A", actor="user")
    gates.set_task_gate("A", ["command:deploy"], actor="user")
    deliver(tasks, "A")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as http:
        # A request cannot carry a command (criterion N) ...
        assert http.post("/api/v1/tasks/A/gate/run", json={"actor": "u", "argv": ["python", "-c", "1"]}).status_code == 422
        assert http.put("/api/v1/tasks/A/gate", json={"checks": ["python -c 1"], "actor": "u"}).status_code == 422
        # ... and a check can only name a configured command.
        body = http.post("/api/v1/tasks/A/gate/run", json={"actor": "user"}).json()
        assert (body["outcome"], body["task"]["status"]) == ("error", "submitted")
        assert body["results"][0]["output_excerpt"] == "Project command 'deploy' is not configured"
        commands = http.get("/api/v1/project-commands").json()
        assert [c["name"] for c in commands] == ["test"] and "argv" not in commands[0]


def test_a_review_by_another_actor_is_required(tmp_path):
    services = make_services(tmp_path / "local")
    tasks, gates = services.tasks, services.gates
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "A", "A", actor="user")
    gates.set_task_gate("A", ["review", "tests"], actor="user")
    deliver(tasks, "A")
    assert gates.run_gate("A", actor="user")["outcome"] == "pending"
    with pytest.raises(ServiceError):
        gates.review("A", actor="agent:a", approved=True)  # the assignee (criterion Q)
    gates.review("A", actor="agent:reviewer", approved=True)
    result = gates.run_gate("A", actor="user")
    assert (result["outcome"], [r["check_name"] for r in result["results"]]) == ("passed", ["review", "command:test"])


# -------- parity --------

# (operation, arguments); errors are part of the script.
PARITY: list[tuple[str, dict[str, Any]]] = [
    ("set_group_gate", {"group_id": "G", "checks": ["tests"], "actor": "user"}),
    ("set_group_gate", {"group_id": "NOPE", "checks": [], "actor": "user"}),
    ("get_gate", {"task_id": "A"}),
    ("set_task_gate", {"task_id": "B", "checks": ["review", "tests"], "actor": "user"}),
    ("set_task_gate", {"task_id": "B", "checks": ["deploy"], "actor": "user"}),
    ("run_gate", {"task_id": "A", "actor": "user"}),
    ("run_gate", {"task_id": "A", "actor": "user"}),
    ("run_gate", {"task_id": "BAD", "actor": "user", "cause": "round 1"}),
    ("run_gate", {"task_id": "B", "actor": "user"}),
    ("review", {"task_id": "B", "actor": "agent:a", "approved": True}),
    ("review", {"task_id": "B", "actor": "agent:r", "approved": True, "comment": "fine"}),
    ("run_gate", {"task_id": "B", "actor": "user"}),
    ("run_gate", {"task_id": "MISSING", "actor": "user"}),
    ("review", {"task_id": "MISSING", "actor": "agent:r", "approved": False, "comment": "needs work"}),
    ("abort_gate", {"task_id": "A", "actor": "user", "cause": "not running"}),
    ("abort_gate", {"task_id": "STUCK", "actor": "user", "cause": "runner crashed"}),
    ("results", {"task_id": "BAD"}),
    ("results", {"task_id": "B", "attempt": 1}),
    ("get_gate", {"task_id": "B"}),
    ("project_commands", {}),
    ("run_gate", {"task_id": "NOPE", "actor": "user"}),
]


def setup_tasks(services: ApplicationServices) -> None:
    tasks = services.tasks
    tasks.create_group("G", "Group", actor="user", authorized=True)
    for task_id in ("A", "B", "BAD", "MISSING"):
        tasks.create_task("G", task_id, task_id, actor="user")
    services.gates.set_task_gate("MISSING", ["command:lint"], actor="user")
    for task_id in ("A", "B", "BAD", "MISSING"):
        deliver(tasks, task_id)
    # A gate run that was interrupted: the task stays validating until abort_gate returns it.
    tasks.create_task("G", "STUCK", "Stuck", actor="user")
    deliver(tasks, "STUCK")
    with tasks.store.transaction() as conn:
        tasks._apply(conn, "STUCK", "gate_start", actor="user", gate=True, event="gate.started")


def run_facade(services: ApplicationServices, op: str, args: dict[str, Any]) -> Any:
    gates = services.gates
    a = dict(args)
    if op == "set_group_gate":
        return gates.set_group_gate(a.pop("group_id"), a.pop("checks"), **a)
    if op == "set_task_gate":
        return gates.set_task_gate(a.pop("task_id"), a.pop("checks"), **a)
    if op == "project_commands":
        return gates.project_commands()
    return getattr(gates, op)(a.pop("task_id"), **a)


def run_http(client: TestClient, op: str, args: dict[str, Any]) -> tuple[int, Any]:
    a = dict(args)
    if op == "set_group_gate":
        response = client.put(f"/api/v1/task-groups/{a.pop('group_id')}/gate", json=a)
    elif op == "set_task_gate":
        response = client.put(f"/api/v1/tasks/{a.pop('task_id')}/gate", json=a)
    elif op == "get_gate":
        response = client.get(f"/api/v1/tasks/{a['task_id']}/gate")
    elif op == "run_gate":
        response = client.post(f"/api/v1/tasks/{a.pop('task_id')}/gate/run", json=a)
    elif op == "abort_gate":
        response = client.post(f"/api/v1/tasks/{a.pop('task_id')}/gate/abort", json=a)
    elif op == "review":
        response = client.post(f"/api/v1/tasks/{a.pop('task_id')}/reviews", json=a)
    elif op == "results":
        response = client.get(f"/api/v1/tasks/{a.pop('task_id')}/gate/results", params=a)
    elif op == "project_commands":
        response = client.get("/api/v1/project-commands")
    else:  # pragma: no cover - the script only uses the operations above
        raise AssertionError(op)
    return response.status_code, response.json()


def cli_args(op: str, args: dict[str, Any]) -> list[str]:
    a = dict(args)
    checks = [item for check in a.get("checks", []) for item in ("--check", check)]
    if op == "set_group_gate":
        return ["task-group", "gate", a["group_id"], *checks, "--actor", a["actor"]]
    if op == "set_task_gate":
        return ["task", "gate", "set", a["task_id"], *checks, "--actor", a["actor"]]
    if op == "get_gate":
        return ["task", "gate", "show", a["task_id"]]
    if op in ("run_gate", "abort_gate"):
        argv = ["task", "gate", op.split("_")[0], a["task_id"], "--actor", a["actor"]]
        return argv + (["--cause", a["cause"]] if a.get("cause") else [])
    if op == "review":
        argv = ["task", "review", a["task_id"], "--approve" if a["approved"] else "--reject", "--actor", a["actor"]]
        return argv + (["--comment", a["comment"]] if a.get("comment") else [])
    if op == "results":
        return ["task", "gate", "results", a["task_id"]] + (["--attempt", str(a["attempt"])] if "attempt" in a else [])
    if op == "project_commands":
        return ["project-commands"]
    raise AssertionError(op)  # pragma: no cover


def run_cli(monkeypatch, capsys, client: TestClient, op: str, args: dict[str, Any]) -> tuple[int, Any]:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    code = 0
    try:
        cli.main(cli_args(op, args))
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else captured.err)


# Timestamps, durations and local folders differ between runs and interfaces.
_VOLATILE = {"created_at", "updated_at", "started_at", "ended_at", "duration_ms", "cwd"}


def normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k not in _VOLATILE}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def outcome_facade(services: ApplicationServices, op: str, args: dict[str, Any]) -> tuple[str, Any]:
    try:
        return "ok", normalize(run_facade(services, op, args))
    except ServiceError as error:
        return "error", (error.status, error.code)


def test_facade_http_and_cli_give_the_same_gate_results(monkeypatch, capsys, tmp_path):
    facade = make_services(tmp_path / "facade")
    http = make_services(tmp_path / "http")
    through_cli = make_services(tmp_path / "cli")
    for services in (facade, http, through_cli):
        setup_tasks(services)
    with TestClient(create_app(services=http), base_url="http://127.0.0.1") as http_client, \
            TestClient(create_app(services=through_cli), base_url="http://127.0.0.1") as cli_client:
        for step, (op, args) in enumerate(PARITY):
            expected = outcome_facade(facade, op, args)
            status, body = run_http(http_client, op, args)
            if expected[0] == "ok":
                assert status == 200 and normalize(body) == expected[1], (step, op, body)
            else:
                assert (status, body["detail"]["code"]) == expected[1], (step, op, body)
            code, output = run_cli(monkeypatch, capsys, cli_client, op, args)
            if expected[0] == "ok":
                assert code == 0 and normalize(output) == expected[1], (step, op, output)
            else:
                assert code == 1 and f"HTTP {expected[1][0]}: {expected[1][1]}:" in output, (step, op, output)


def test_the_gate_parity_script_covers_every_public_operation():
    operations = {op for op, _ in PARITY}
    public = {name for name, member in vars(GateServices).items()
              if callable(member) and not name.startswith("_") and not isinstance(member, property)}
    assert operations == public
