
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices
from multiagent.tasks.envelopes import canonical_json


def make_services(root: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings)


def snapshot(*, state: str = "open", source_state: str = "disponible") -> dict[str, Any]:
    group_id = "ORQ4-fixture"
    return {
        "groups": [{"id": group_id, "title": "Synthetic fixture", "authorized": True}],
        "tasks": [{
            "id": f"{group_id}-T01",
            "group_id": group_id,
            "title": "Synthetic task",
            "objective": "Exercise the snapshot API using fake data.",
            "source_id": "T01",
            "source_state": source_state,
            "state": state,
            "assignee": "agent:fixture" if state == "paused" else "",
            "preferred": "",
            "file_scope": ["src/fixture.py"] if state == "paused" else [],
            "lock_policy": {
                "shared_scope": [],
                "workspace_scope": ["src/fixture.py"] if state == "paused" else [],
            },
            "workspace_domain": "workspace:fixture" if state == "paused" else "",
            "context_refs": ["doc://fixtures/orq4/task.md"],
            "gate": [],
        }],
        "dependencies": [],
    }


def import_body(key: str = "orq4:fixture:one", *, data=None, actor: str = "agent:importer") -> dict[str, Any]:
    value = data or snapshot()
    return {
        "snapshot": value,
        "source_key": key,
        "source_ref": "doc://fixtures/orq4/source.json",
        "snapshot_sha256": hashlib.sha256(canonical_json(value)).hexdigest(),
        "actor": actor,
    }


class ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, *_):
        return None


def invoke_cli(monkeypatch, capsys, client: TestClient, args: list[str]) -> tuple[int, Any]:
    monkeypatch.setattr(cli, "api_client", lambda: ClientContext(client))
    code = 0
    try:
        cli.main(args)
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, json.loads(captured.out) if code == 0 else captured.err


def test_snapshot_what_if_and_invalid_hash_do_not_persist(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        body = import_body()
        plan = client.post("/api/v1/task-snapshots/import", json={**body, "what_if": True})
        assert plan.status_code == 200, plan.text
        assert plan.json()["status"] == "planned"
        assert plan.json()["source_key"] == body["source_key"]
        assert client.get("/api/v1/task-groups").json() == []
        assert client.get("/api/v1/task-snapshots/orq4%3Afixture%3Aone").status_code == 404

        missing_hash = dict(body)
        missing_hash.pop("snapshot_sha256")
        assert client.post("/api/v1/task-snapshots/import", json=missing_hash).status_code == 422

        bad_hash = {**body, "source_key": "orq4:fixture:bad-hash", "snapshot_sha256": "0" * 64}
        response = client.post("/api/v1/task-snapshots/import", json=bad_hash)
        assert response.status_code == 422
        assert client.get("/api/v1/task-groups").json() == []


def test_snapshot_accepts_native_dependency_thresholds(tmp_path):
    for requires in ("accepted", "submitted"):
        services = make_services(tmp_path / requires)
        data = snapshot()
        group_id = data["groups"][0]["id"]
        first = data["tasks"][0]
        dependent = {**first, "id": f"{group_id}-T02", "source_id": "T02", "title": "Synthetic dependent"}
        data["tasks"].append(dependent)
        data["dependencies"] = [{
            "task_id": dependent["id"],
            "depends_on_id": first["id"],
            "requires": requires,
        }]
        body = import_body(f"orq4:fixture:native:{requires}", data=data)
        with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
            response = client.post("/api/v1/task-snapshots/import", json=body)
            assert response.status_code == 200, response.text
            task = client.get(f"/api/v1/tasks/{dependent['id']}").json()
            edge = task["depends_on"][0]
            assert edge["task_id"] == first["id"]
            assert edge["requires"] == requires
            assert edge["satisfied"] is False


def test_snapshot_rejects_b_only_release_fields_until_capability_is_available(tmp_path):
    services = make_services(tmp_path)
    data = snapshot()
    data["groups"][0]["legacy_release_mode"] = "probada"
    body = import_body("orq4:fixture:b-only", data=data)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        response = client.post("/api/v1/task-snapshots/import", json=body)
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_state"
        assert detail["details"]["required_capability"] == "release_dependencies/v1"
        assert client.get("/api/v1/task-groups").json() == []


def test_cli_snapshot_import_show_and_rollback_are_http_backed(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "db")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        file_body = import_body("orq4:fixture:cli")
        snapshot_file = tmp_path / "snapshot.json"
        snapshot_file.write_text(json.dumps({
            key: file_body[key] for key in ("snapshot", "source_key", "source_ref", "snapshot_sha256")
        }), encoding="utf-8")

        code, imported = invoke_cli(monkeypatch, capsys, client, [
            "task", "snapshot", "import", str(snapshot_file), "--actor", "agent:cli", "--what-if"
        ])
        assert code == 0
        assert client.get("/api/v1/task-groups").json() == []
        assert imported

        code, imported = invoke_cli(monkeypatch, capsys, client, [
            "task", "snapshot", "import", str(snapshot_file), "--actor", "agent:cli"
        ])
        assert code == 0
        assert imported["source_key"] == "orq4:fixture:cli"

        code, repeated = invoke_cli(monkeypatch, capsys, client, [
            "task", "snapshot", "import", str(snapshot_file), "--actor", "agent:cli"
        ])
        assert code == 0
        assert repeated["source_key"] == "orq4:fixture:cli"
        assert len(client.get("/api/v1/task-groups").json()) == 1

        code, receipt = invoke_cli(monkeypatch, capsys, client, [
            "task", "snapshot", "show", "orq4:fixture:cli"
        ])
        assert code == 0
        assert receipt["source_key"] == "orq4:fixture:cli"

        code, rolled_back = invoke_cli(monkeypatch, capsys, client, [
            "task", "snapshot", "rollback", "orq4:fixture:cli", "--actor", "agent:cli"
        ])
        assert code == 0
        assert rolled_back
        assert client.get("/api/v1/task-groups").json() == []
        retained_receipt = client.get("/api/v1/task-snapshots/orq4%3Afixture%3Acli")
        assert retained_receipt.status_code == 200
        assert retained_receipt.json()["source_key"] == "orq4:fixture:cli"


def test_cli_resume_sends_actor_domain_for_pending_workspace_lock(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path)
    data = snapshot(state="paused", source_state="pendiente")
    body = import_body("orq4:fixture:paused", data=data, actor="agent:importer")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        imported = client.post("/api/v1/task-snapshots/import", json=body)
        assert imported.status_code == 200, imported.text
        task_id = data["tasks"][0]["id"]
        code, result = invoke_cli(monkeypatch, capsys, client, [
            "task", "do", task_id, "resume", "--actor", "agent:fixture",
            "--actor-domain", "workspace:fixture",
        ])
        assert code == 0
        assert result["status"] == "running"
        assert result["attempt"] == 1


def test_snapshot_validation_rejects_bad_context_refs_and_group_gate_without_writes(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        invalid_cases = []
        bad_context = snapshot()
        bad_context["tasks"][0]["context_refs"] = ["http://invalid.example/evidence"]
        invalid_cases.append(("context", bad_context))
        bad_gate = snapshot()
        bad_gate["groups"][0]["default_gate"] = ["not-a-valid-check"]
        invalid_cases.append(("gate", bad_gate))

        for name, data in invalid_cases:
            for what_if in (True, False):
                body = import_body(f"orq4:fixture:invalid:{name}:{what_if}", data=data)
                response = client.post("/api/v1/task-snapshots/import", json={**body, "what_if": what_if})
                assert response.status_code == 422, response.text
                assert client.get("/api/v1/task-groups").json() == []
                assert client.get("/api/v1/tasks").json() == []
                assert client.get("/api/v1/team-events").json() == []


def test_snapshot_claimed_scope_conflict_blocks_plan_and_import_atomically(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        existing_group = client.post("/api/v1/task-groups", json={
            "id": "ORQ4-existing", "title": "Existing lock", "actor": "agent:setup", "authorized": True,
        })
        assert existing_group.status_code == 201, existing_group.text
        existing_task = client.post("/api/v1/tasks", json={
            "group_id": "ORQ4-existing", "id": "ORQ4-existing-T01", "title": "Existing claimed task",
            "actor": "agent:setup", "file_scope": ["src"],
        })
        assert existing_task.status_code == 201, existing_task.text
        claimed = client.post("/api/v1/tasks/ORQ4-existing-T01/transitions", json={
            "action": "claim", "actor": "agent:owner",
        })
        assert claimed.status_code == 200, claimed.text
        before_events = client.get("/api/v1/team-events").json()

        data = snapshot(state="claimed")
        task = data["tasks"][0]
        task.update({
            "assignee": "agent:other",
            "file_scope": ["src/a"],
            "lock_policy": {"shared_scope": ["src/a"], "workspace_scope": []},
        })
        body = import_body("orq4:fixture:conflicting-lock", data=data)
        for what_if in (True, False):
            response = client.post("/api/v1/task-snapshots/import", json={**body, "what_if": what_if})
            assert response.status_code == 409, response.text
            assert [group["id"] for group in client.get("/api/v1/task-groups").json()] == ["ORQ4-existing"]
            assert len(client.get("/api/v1/tasks").json()) == 1
            assert client.get("/api/v1/team-events").json() == before_events


def test_snapshot_rollback_preserves_unrelated_group_activity(tmp_path):
    services = make_services(tmp_path)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        body = import_body("orq4:fixture:rollback-with-other-activity")
        imported = client.post("/api/v1/task-snapshots/import", json=body)
        assert imported.status_code == 200, imported.text
        unrelated = client.post("/api/v1/task-groups", json={
            "id": "ORQ4-unrelated", "title": "Independent activity", "actor": "agent:other",
        })
        assert unrelated.status_code == 201, unrelated.text

        rollback = client.post(
            "/api/v1/task-snapshots/orq4%3Afixture%3Arollback-with-other-activity/rollback",
            json={"actor": "agent:rollback"},
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["state"] == "rolled_back"
        assert [group["id"] for group in client.get("/api/v1/task-groups").json()] == ["ORQ4-unrelated"]
        assert client.get("/api/v1/tasks").json() == []
        event_types = [event["type"] for event in client.get("/api/v1/team-events").json()]
        assert "snapshot.imported" in event_types
        assert "group.created" in event_types
        assert "snapshot.rolled_back" in event_types
