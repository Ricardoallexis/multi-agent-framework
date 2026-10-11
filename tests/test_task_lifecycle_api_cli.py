from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices


class ClientContext:
    def __init__(self, client): self.client = client
    def __enter__(self): return self.client
    def __exit__(self, *args): return False


def make_services(root: Path):
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True)


def test_scope_and_group_lifecycle_http_and_cli(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "local")
    tasks = services.tasks
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user", file_scope=["src/a"])
    tasks.create_task("G", "U", "Open task", actor="user", file_scope=["src/u"])
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    tasks.record_delivery_fact("T", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        current = client.get("/api/v1/tasks/T").json()
        response = client.post("/api/v1/tasks/T/scope", json={
            "file_scope": ["src/b"], "actor": "user", "expected_revision": current["revision"]})
        assert response.status_code == 409  # submitted scope is immutable
        open_task = client.get("/api/v1/tasks/U").json()
        response = client.post("/api/v1/tasks/U/scope", json={
            "file_scope": ["src/v2"], "actor": "user", "expected_revision": open_task["revision"]})
        assert response.status_code == 200 and response.json()["file_scope"] == ["src/v2"]
        monkeypatch.setattr(cli, "api_client", lambda: ClientContext(client))
        cli.main(["task", "scope", "set", "U", "--actor", "user", "--revision",
                  str(response.json()["revision"]), "--file", "src/v3"])
        changed = json.loads(capsys.readouterr().out)
        assert changed["file_scope"] == ["src/v3"]
        tasks.transition("U", "claim", actor="agent:u")
        tasks.transition("U", "start", actor="agent:u")
        tasks.transition("U", "submit", actor="agent:u")
        tasks.record_delivery_fact("U", delivery_ref="report://delivery-u", delivery_sha256="b" * 64,
                                   has_patch=False, patchset_sha256="", actor="user")
        cli.main(["task", "group", "state", "set", "G", "closed", "--actor", "user",
                  "--cause", "done", "--revision", "1"])
        closed = json.loads(capsys.readouterr().out)
        assert closed["lifecycle_state"] == "closed" and closed["revision"] == 2
        cli.main(["task", "group", "state", "set", "G", "open", "--actor", "user",
                  "--cause", "follow-up", "--revision", "2"])
        opened = json.loads(capsys.readouterr().out)
        assert opened["lifecycle_state"] == "open" and opened["revision"] == 3
        closed = client.post("/api/v1/task-groups/G/state", json={
            "state": "closed", "actor": "user", "cause": "again", "expected_revision": 3})
        assert closed.status_code == 200 and closed.json()["revision"] == 4
        cli.main(["task", "create", "G", "T2", "--title", "Follow-up", "--actor", "user",
                  "--reopen-group", "--group-revision", "4", "--cause", "follow-up",
                  "--context-ref", "doc://docs/follow-up.md", "--context-ref", "report://orq4"])
        created = json.loads(capsys.readouterr().out)
        assert created["id"] == "T2" and tasks.get_group("G")["revision"] == 5
        context = client.get("/api/v2/tasks/T2/context")
        assert context.status_code == 200
        assert context.json()["task_envelope"]["context_refs"] == ["doc://docs/follow-up.md", "report://orq4"]
        response = client.post("/api/v1/tasks", json={
            "group_id": "G", "id": "T3", "title": "API task", "actor": "user",
            "context_refs": ["doc://docs/api-task.md"]})
        assert response.status_code == 201
        context = client.get("/api/v2/tasks/T3/context")
        assert context.json()["task_envelope"]["context_refs"] == ["doc://docs/api-task.md"]
