# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Task orchestration end to end and facade/HTTP/CLI parity (ORQ-T07), in Mock mode.

Scenario of the analysis: T1 -> (T2 || T3) -> T4. T2 and T3 wait for T1 and
can run in parallel; T4 waits for both.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError, TaskServices
from multiagent.tasks.envelopes import canonical_json


def make_services(root: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings)


def build_scenario(tasks) -> None:
    tasks.create_group("ORQ", "Orchestration", actor="user", authorized=True)
    tasks.create_task("ORQ", "T1", "Contract", actor="user", file_scope=["docs/contract.md"])
    tasks.create_task("ORQ", "T2", "Store", actor="user", depends_on=["T1"], file_scope=["multiagent/db"])
    tasks.create_task("ORQ", "T3", "Graph", actor="user", depends_on=["T1"], file_scope=["multiagent/tasks"])
    tasks.create_task("ORQ", "T4", "Services", actor="user", depends_on=["T2", "T3"])


def finish(tasks, task_id: str, agent: str) -> dict[str, Any]:
    for action in ("claim", "start", "submit"):
        tasks.transition(task_id, action, actor=agent)
    return tasks.transition(task_id, "accept", actor="user", cause="manual review")


def statuses(tasks) -> dict[str, str]:
    return {task["id"]: task["status"] for task in tasks.list_tasks(group_id="ORQ")}


# -------- end to end (facade) --------

def test_initial_states_follow_the_dependencies(tmp_path):
    tasks = make_services(tmp_path).tasks
    build_scenario(tasks)
    assert statuses(tasks) == {"T1": "ready", "T2": "blocked", "T3": "blocked", "T4": "blocked"}
    assert [t["id"] for t in tasks.ready_tasks()] == ["T1"]


def test_an_invalid_transition_is_rejected_and_changes_nothing(tmp_path):
    tasks = make_services(tmp_path).tasks
    build_scenario(tasks)
    seq = tasks.events()[-1]["seq"]
    with pytest.raises(ServiceError) as caught:
        tasks.transition("T2", "claim", actor="agent:a")
    assert caught.value.code == "invalid_state" and caught.value.details["status"] == "blocked"
    with pytest.raises(ServiceError):
        tasks.transition("T1", "accept", actor="user")  # not submitted yet
    assert tasks.events(after=seq) == []
    assert statuses(tasks)["T2"] == "blocked"


def test_accept_releases_the_parallel_branch_and_then_the_join(tmp_path):
    tasks = make_services(tmp_path).tasks
    build_scenario(tasks)
    tasks.transition("T1", "claim", actor="agent:a")
    tasks.transition("T1", "start", actor="agent:a")
    tasks.transition("T1", "submit", actor="agent:a")
    assert statuses(tasks)["T2"] == "blocked"  # submitted is not accepted
    tasks.transition("T1", "accept", actor="user")
    assert [t["id"] for t in tasks.ready_tasks()] == ["T2", "T3"]
    # T2 and T3 run in parallel with different agents: their files do not overlap.
    tasks.transition("T2", "claim", actor="agent:a")
    tasks.transition("T3", "claim", actor="agent:b")
    finish_after_claim = ("start", "submit")
    for task_id, agent in (("T2", "agent:a"), ("T3", "agent:b")):
        for action in finish_after_claim:
            tasks.transition(task_id, action, actor=agent)
    tasks.transition("T2", "accept", actor="user")
    assert statuses(tasks)["T4"] == "blocked"
    tasks.transition("T3", "accept", actor="user")
    assert statuses(tasks)["T4"] == "ready"
    released = [e["task_id"] for e in tasks.events() if e["type"] == "task.ready"]
    assert released == ["T2", "T3", "T4"]


def test_a_failure_stops_its_branch(tmp_path):
    tasks = make_services(tmp_path).tasks
    build_scenario(tasks)
    finish(tasks, "T1", "agent:a")
    finish(tasks, "T3", "agent:b")
    tasks.transition("T2", "claim", actor="agent:a")
    tasks.transition("T2", "start", actor="agent:a")
    failed = tasks.transition("T2", "fail", actor="agent:a", cause="tests fail")
    assert failed["status"] == "failed"
    t4 = tasks.get_task("T4")
    assert t4["status"] == "blocked"
    assert t4["blockers"] == [{"task_id": "T2", "state": "failed", "requires": "accepted"}]
    assert [t["id"] for t in tasks.ready_tasks()] == []
    with pytest.raises(ServiceError):
        tasks.transition("T2", "start", actor="agent:a")  # failed is terminal


def test_events_after_have_no_gaps(tmp_path):
    tasks = make_services(tmp_path).tasks
    build_scenario(tasks)
    finish(tasks, "T1", "agent:a")
    events = tasks.events()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    cursor = events[4]["seq"]
    assert [e["seq"] for e in tasks.events(after=cursor)] == [e["seq"] for e in events[5:]]
    tasks.transition("T2", "claim", actor="agent:a")
    newer = tasks.events(after=events[-1]["seq"])
    assert [(e["seq"], e["type"]) for e in newer] == [(events[-1]["seq"] + 1, "task.claimed")]


def test_state_and_events_survive_reopening_the_database(tmp_path):
    first = make_services(tmp_path).tasks
    build_scenario(first)
    finish(first, "T1", "agent:a")
    first.transition("T2", "claim", actor="agent:a")
    before = (statuses(first), first.events())
    second = make_services(tmp_path).tasks
    assert (statuses(second), second.events()) == before
    second.transition("T2", "start", actor="agent:a")
    assert second.get_task("T2")["attempt"] == 1


# -------- parity: facade, HTTP and CLI run the same script --------

class _ClientContext:
    """Keep the shared TestClient open across the CLI's per-request context."""

    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc_info) -> bool:
        return False


# (operation, arguments); errors are part of the script.
def snapshot_operation_args() -> dict[str, Any]:
    snapshot = {
        "groups": [{"id": "SNAP-G", "title": "Snapshot parity", "authorized": True}],
        "tasks": [{
            "id": "SNAP-T01", "group_id": "SNAP-G", "title": "Synthetic snapshot task",
            "objective": "Exercise snapshot operations across public interfaces.",
            "source_id": "T01", "source_state": "disponible", "state": "open",
            "context_refs": ["doc://fixtures/snapshot-parity.md"], "gate": [],
        }],
        "dependencies": [],
    }
    return {
        "snapshot": snapshot,
        "source_key": "parity:task-snapshot:one",
        "source_ref": "doc://fixtures/snapshot-parity.json",
        "snapshot_sha256": hashlib.sha256(canonical_json(snapshot)).hexdigest(),
        "actor": "agent:parity",
    }


_SNAPSHOT_ARGS = snapshot_operation_args()
SCRIPT: list[tuple[str, dict[str, Any]]] = [
    ("create_group", {"group_id": "ORQ", "title": "Orchestration", "actor": "user", "authorized": True}),
    ("create_group", {"group_id": "ORQ", "title": "Again", "actor": "user"}),
    ("create_task", {"group_id": "ORQ", "task_id": "T1", "title": "Contract", "actor": "user",
                     "file_scope": ["docs/contract.md"]}),
    ("create_task", {"group_id": "ORQ", "task_id": "T2", "title": "Store", "actor": "user", "depends_on": ["T1"]}),
    ("create_task", {"group_id": "ORQ", "task_id": "T3", "title": "Graph", "actor": "user",
                     "depends_on": ["T1"], "file_scope": ["docs"]}),
    ("create_task", {"group_id": "ORQ", "task_id": "T4", "title": "Services", "actor": "user",
                     "depends_on": ["T2", "T3"]}),
    ("create_task", {"group_id": "ORQ", "task_id": "T5", "title": "Orphan", "actor": "user", "depends_on": ["GHOST"]}),
    ("list_tasks", {}),
    ("ready_tasks", {}),
    ("transition", {"task_id": "T2", "action": "claim", "actor": "agent:a"}),
    ("transition", {"task_id": "T1", "action": "claim", "actor": "agent:a"}),
    ("transition", {"task_id": "T1", "action": "start", "actor": "agent:b"}),
    ("transition", {"task_id": "T1", "action": "start", "actor": "agent:a"}),
    ("transition", {"task_id": "T1", "action": "submit", "actor": "agent:a", "expected_revision": 1}),
    ("transition", {"task_id": "T1", "action": "submit", "actor": "agent:a"}),
    ("transition", {"task_id": "T1", "action": "accept", "actor": "user", "cause": "reviewed"}),
    ("transition", {"task_id": "T3", "action": "claim", "actor": "agent:b"}),
    ("add_dependency", {"task_id": "T1", "depends_on_id": "T4", "actor": "user"}),
    ("add_dependency", {"task_id": "T4", "depends_on_id": "T1", "actor": "user", "requires": "submitted"}),
    ("remove_dependency", {"task_id": "T4", "depends_on_id": "T1", "actor": "user"}),
    ("transition", {"task_id": "T2", "action": "cancel", "actor": "user"}),
    ("get_task", {"task_id": "T4"}),
    ("update_group", {"group_id": "ORQ", "actor": "user", "authorized": False}),
    ("get_group", {"group_id": "ORQ"}),
    ("list_groups", {}),
    ("events", {"after": 3}),
    ("events", {"task_id": "T1"}),
    ("plan_snapshot", _SNAPSHOT_ARGS),
    ("import_snapshot", _SNAPSHOT_ARGS),
    ("get_snapshot", {"source_key": _SNAPSHOT_ARGS["source_key"]}),
    ("rollback_snapshot", {"source_key": _SNAPSHOT_ARGS["source_key"], "actor": "agent:parity"}),
    ("set_release_mode", {"group_id": "ORQ", "mode": "probada", "actor": "user"}),
    ("add_release_dependency", {"task_id": "T4", "depends_on_id": "T1", "actor": "user",
                                 "source_ref": "doc://fixtures/release-edge.md"}),
    ("get_release_facts", {"task_id": "T1"}),
    ("record_delivery_fact", {"task_id": "T1", "delivery_ref": "report://delivery-t1",
                              "delivery_sha256": "a" * 64, "has_patch": True,
                              "patchset_sha256": "b" * 64, "actor": "agent:parity"}),
    ("record_test_verdict", {"task_id": "T1", "test_verdict": "passed",
                              "test_patchset_sha256": "b" * 64,
                              "test_ref": "report://tests-t1", "actor": "agent:parity"}),
    ("record_integration_fact", {"task_id": "T1", "integration_commit": "c" * 40,
                                  "integration_ref": "doc://fixtures/integration-t1.md",
                                  "actor": "agent:parity"}),
    ("get_release_facts", {"task_id": "T1"}),
    ("reset_release_facts", {"task_id": "T1", "actor": "agent:parity", "reason": "parity reset"}),
    ("remove_release_dependency", {"task_id": "T4", "depends_on_id": "T1", "actor": "user"}),
    ("create_task", {"group_id": "ORQ", "task_id": "T6", "title": "Atomic mixed dependencies",
                     "actor": "agent:parity", "depends_on": ["T1"],
                     "release_dependencies": [{"depends_on_id": "T3",
                                                "source_ref": "doc://fixtures/release-t6.md"}]}),
    ("update_scope", {"task_id": "T6", "file_scope": ["src/x"], "actor": "agent:parity", "expected_revision": 99}),
    ("update_scope", {"task_id": "T6", "file_scope": ["src/x"], "actor": "agent:parity", "expected_revision": 1}),
    ("set_group_state", {"group_id": "ORQ", "state": "closed", "actor": "user", "cause": "stale", "expected_revision": 99}),
    ("set_group_state", {"group_id": "ORQ", "state": "closed", "actor": "user", "cause": "parity close",
                         "expected_revision": 3}),
]


def run_facade(services: ApplicationServices, op: str, args: dict[str, Any]) -> Any:
    tasks = services.tasks
    args = dict(args)
    if op == "create_group":
        return tasks.create_group(args.pop("group_id"), args.pop("title"), **args)
    if op in ("get_group", "update_group"):
        return getattr(tasks, op)(args.pop("group_id"), **args)
    if op == "create_task":
        return tasks.create_task(args.pop("group_id"), args.pop("task_id"), args.pop("title"), **args)
    if op == "get_task":
        return tasks.get_task(args["task_id"])
    if op == "transition":
        return tasks.transition(args.pop("task_id"), args.pop("action"), **args)
    if op in ("add_dependency", "remove_dependency"):
        return getattr(tasks, op)(args.pop("task_id"), args.pop("depends_on_id"), **args)
    if op in ("plan_snapshot", "import_snapshot"):
        return getattr(tasks, op)(args.pop("snapshot"), **args)
    if op == "set_release_mode":
        return tasks.set_release_mode(args.pop("group_id"), args.pop("mode"), **args)
    if op in ("add_release_dependency", "remove_release_dependency"):
        return getattr(tasks, op)(args.pop("task_id"), args.pop("depends_on_id"), **args)
    if op in ("record_delivery_fact", "record_test_verdict", "record_integration_fact", "reset_release_facts"):
        return getattr(tasks, op)(args.pop("task_id"), **args)
    if op == "get_release_facts":
        return tasks.get_release_facts(args["task_id"])
    if op == "update_scope":
        return tasks.update_scope(args.pop("task_id"), **args)
    if op == "set_group_state":
        return tasks.set_group_state(args.pop("group_id"), args.pop("state"), **args)
    return getattr(tasks, op)(**args)


def run_http(client: TestClient, op: str, args: dict[str, Any]) -> tuple[int, Any]:
    a = dict(args)
    if op == "create_group":
        response = client.post("/api/v1/task-groups", json={"id": a.pop("group_id"), **a})
    elif op == "update_group":
        response = client.patch(f"/api/v1/task-groups/{a.pop('group_id')}", json=a)
    elif op == "get_group":
        response = client.get(f"/api/v1/task-groups/{a['group_id']}")
    elif op == "list_groups":
        response = client.get("/api/v1/task-groups")
    elif op == "create_task":
        response = client.post("/api/v1/tasks", json={"id": a.pop("task_id"), **a})
    elif op == "get_task":
        response = client.get(f"/api/v1/tasks/{a['task_id']}")
    elif op == "list_tasks":
        response = client.get("/api/v1/tasks")
    elif op == "ready_tasks":
        response = client.get("/api/v1/tasks/ready")
    elif op == "transition":
        response = client.post(f"/api/v1/tasks/{a.pop('task_id')}/transitions", json=a)
    elif op == "add_dependency":
        response = client.post(f"/api/v1/tasks/{a.pop('task_id')}/dependencies", json=a)
    elif op == "remove_dependency":
        response = client.delete(f"/api/v1/tasks/{a['task_id']}/dependencies/{a['depends_on_id']}",
                                 params={"actor": a["actor"]})
    elif op == "events":
        params = {"after": a.get("after", 0)}
        if "task_id" in a:
            params["task"] = a["task_id"]
        response = client.get("/api/v1/team-events", params=params)
    elif op in ("plan_snapshot", "import_snapshot"):
        body = {key: a[key] for key in ("snapshot", "source_key", "source_ref", "snapshot_sha256", "actor")}
        if op == "plan_snapshot":
            body["what_if"] = True
        response = client.post("/api/v1/task-snapshots/import", json=body)
    elif op == "get_snapshot":
        response = client.get(f"/api/v1/task-snapshots/{quote(a['source_key'], safe='')}")
    elif op == "rollback_snapshot":
        response = client.post(f"/api/v1/task-snapshots/{quote(a['source_key'], safe='')}/rollback",
                               json={"actor": a["actor"]})
    elif op == "set_release_mode":
        response = client.put(f"/api/v1/task-groups/{quote(a['group_id'], safe='')}/release-mode",
                              json={"mode": a["mode"], "actor": a["actor"]})
    elif op == "add_release_dependency":
        task_id = quote(a.pop("task_id"), safe="")
        response = client.post(f"/api/v1/tasks/{task_id}/release-dependencies", json=a)
    elif op == "remove_release_dependency":
        task_id, depends_on_id = quote(a["task_id"], safe=""), quote(a["depends_on_id"], safe="")
        response = client.delete(f"/api/v1/tasks/{task_id}/release-dependencies/{depends_on_id}",
                                 params={"actor": a["actor"]})
    elif op == "get_release_facts":
        response = client.get(f"/api/v1/tasks/{quote(a['task_id'], safe='')}/release-facts")
    elif op in ("record_delivery_fact", "record_test_verdict", "record_integration_fact", "reset_release_facts"):
        task_id = quote(a.pop("task_id"), safe="")
        route = {"record_delivery_fact": "delivery", "record_test_verdict": "test",
                 "record_integration_fact": "integration", "reset_release_facts": "reset"}[op]
        response = client.post(f"/api/v1/tasks/{task_id}/release-facts/{route}", json=a)
    elif op == "update_scope":
        response = client.post(f"/api/v1/tasks/{quote(a.pop('task_id'), safe='')}/scope", json=a)
    elif op == "set_group_state":
        response = client.post(f"/api/v1/task-groups/{quote(a.pop('group_id'), safe='')}/state", json=a)
    else:  # pragma: no cover - the script only uses the operations above
        raise AssertionError(op)
    return response.status_code, response.json()


def cli_args(op: str, args: dict[str, Any], snapshot_file: Path | None = None) -> list[str]:
    a = dict(args)
    if op == "create_group":
        argv = ["task-group", "create", a["group_id"], "--title", a["title"], "--actor", a["actor"]]
        return argv + (["--authorized"] if a.get("authorized") else [])
    if op == "update_group":
        return ["task-group", "update", a["group_id"], "--actor", a["actor"],
                "--authorized" if a["authorized"] else "--not-authorized"]
    if op == "get_group":
        return ["task-group", "show", a["group_id"]]
    if op == "list_groups":
        return ["task-groups"]
    if op == "create_task":
        argv = ["task", "create", a["group_id"], a["task_id"], "--title", a["title"], "--actor", a["actor"]]
        for dep in a.get("depends_on", []):
            argv += ["--depends-on", dep]
        for dep in a.get("release_dependencies", []):
            argv += ["--release-dep", f"{dep['depends_on_id']}={dep['source_ref']}"]
        for path in a.get("file_scope", []):
            argv += ["--file", path]
        return argv
    if op == "get_task":
        return ["task", "show", a["task_id"]]
    if op == "list_tasks":
        return ["tasks"]
    if op == "ready_tasks":
        return ["tasks", "--ready"]
    if op == "transition":
        argv = ["task", "do", a["task_id"], a["action"], "--actor", a["actor"]]
        if a.get("cause"):
            argv += ["--cause", a["cause"]]
        if "expected_revision" in a:
            argv += ["--revision", str(a["expected_revision"])]
        return argv
    if op in ("add_dependency", "remove_dependency"):
        argv = ["task", "dep", "add" if op == "add_dependency" else "remove", a["task_id"], a["depends_on_id"],
                "--actor", a["actor"]]
        return argv + (["--requires", a["requires"]] if a.get("requires") else [])
    if op == "events":
        argv = ["team-events", "--after", str(a.get("after", 0))]
        return argv + (["--task", a["task_id"]] if "task_id" in a else [])
    if op in ("plan_snapshot", "import_snapshot"):
        assert snapshot_file is not None
        argv = ["task", "snapshot", "import", str(snapshot_file), "--actor", a["actor"]]
        return argv + (["--what-if"] if op == "plan_snapshot" else [])
    if op == "get_snapshot":
        return ["task", "snapshot", "show", a["source_key"]]
    if op == "rollback_snapshot":
        return ["task", "snapshot", "rollback", a["source_key"], "--actor", a["actor"]]
    if op == "set_release_mode":
        return ["task", "release", "mode", a["group_id"], a["mode"], "--actor", a["actor"]]
    if op in ("add_release_dependency", "remove_release_dependency"):
        argv = ["task", "release", "dep", "add" if op == "add_release_dependency" else "remove",
                a["task_id"], a["depends_on_id"], "--actor", a["actor"]]
        return argv + (["--source-ref", a["source_ref"]] if op == "add_release_dependency" else [])
    if op == "get_release_facts":
        return ["task", "release", "facts", "show", a["task_id"]]
    if op == "record_delivery_fact":
        argv = ["task", "release", "facts", "delivery", a["task_id"], "--delivery-ref", a["delivery_ref"],
                "--delivery-sha256", a["delivery_sha256"], "--actor", a["actor"]]
        argv += ["--has-patch" if a["has_patch"] else "--no-patch"]
        return argv + (["--patchset-sha256", a["patchset_sha256"]] if a["patchset_sha256"] else [])
    if op == "record_test_verdict":
        return ["task", "release", "facts", "test", a["task_id"], "--verdict", a["test_verdict"],
                "--patchset-sha256", a["test_patchset_sha256"], "--test-ref", a["test_ref"],
                "--actor", a["actor"]]
    if op == "record_integration_fact":
        return ["task", "release", "facts", "integration", a["task_id"], "--commit", a["integration_commit"],
                "--integration-ref", a["integration_ref"], "--actor", a["actor"]]
    if op == "reset_release_facts":
        return ["task", "release", "facts", "reset", a["task_id"], "--actor", a["actor"],
                "--reason", a["reason"]]
    if op == "update_scope":
        argv = ["task", "scope", "set", a["task_id"], "--actor", a["actor"], "--revision", str(a["expected_revision"])]
        for path in a["file_scope"]:
            argv += ["--file", path]
        return argv
    if op == "set_group_state":
        return ["task", "group", "state", "set", a["group_id"], a["state"], "--actor", a["actor"],
                "--cause", a["cause"], "--revision", str(a["expected_revision"])]
    raise AssertionError(op)  # pragma: no cover


def run_cli(monkeypatch, capsys, client: TestClient, op: str, args: dict[str, Any],
            tmp_path: Path) -> tuple[int, Any]:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    snapshot_file = None
    if op in ("plan_snapshot", "import_snapshot"):
        snapshot_file = tmp_path / f"{op}.json"
        snapshot_file.write_text(json.dumps({key: args[key] for key in (
            "snapshot", "source_key", "source_ref", "snapshot_sha256",
        )}), encoding="utf-8")
    code = 0
    try:
        cli.main(cli_args(op, args, snapshot_file))
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else captured.err)


def normalize(value: Any) -> Any:
    """Drop timestamps: everything else must be identical across interfaces."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()
                if k not in ("created_at", "updated_at", "imported_at", "rolled_back_at")}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def outcome_facade(services: ApplicationServices, op: str, args: dict[str, Any]) -> tuple[str, Any]:
    try:
        return "ok", normalize(run_facade(services, op, args))
    except ServiceError as error:
        return "error", (error.status, error.code)


def test_facade_http_and_cli_give_the_same_results(monkeypatch, capsys, tmp_path):
    facade = make_services(tmp_path / "facade")
    http = make_services(tmp_path / "http")
    through_cli = make_services(tmp_path / "cli")
    with TestClient(create_app(services=http), base_url="http://127.0.0.1") as http_client, \
            TestClient(create_app(services=through_cli), base_url="http://127.0.0.1") as cli_client:
        for step, (op, args) in enumerate(SCRIPT):
            expected = outcome_facade(facade, op, args)
            status, body = run_http(http_client, op, args)
            if expected[0] == "ok":
                assert status in (200, 201), (step, op, body)
                assert normalize(body) == expected[1], (step, op)
            else:
                assert (status, body["detail"]["code"]) == expected[1], (step, op, body)
            code, output = run_cli(monkeypatch, capsys, cli_client, op, args, tmp_path)
            if expected[0] == "ok":
                assert code == 0 and normalize(output) == expected[1], (step, op, output)
            else:
                assert code == 1 and f"HTTP {expected[1][0]}: {expected[1][1]}:" in output, (step, op, output)


def test_the_parity_script_covers_every_public_operation():
    operations = {op for op, _ in SCRIPT}
    public = {name for name, member in vars(TaskServices).items() if callable(member) and not name.startswith("_")}
    assert operations == public
