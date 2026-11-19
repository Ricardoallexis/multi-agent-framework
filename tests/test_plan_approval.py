# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Saved plan proposals wait for a human before ordinary plan execution."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.services import ApplicationServices, ServiceError

ROOT = Path(__file__).parents[1]


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        execution_backend="mock", mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False)
    instance = ApplicationServices.create(settings)
    yield instance
    instance.runs.close()


def proposal(services):
    return services.planning.propose("manual", {"group_id": "launch", "goal": "Launch product",
        "tasks": [{"id": "technical", "title": "Technical review", "workflow": "social_post",
                   "depends_on": [], "objective": "Check the design", "inputs": {"topic": "Launch"}},
                  {"id": "commercial", "title": "Commercial review", "workflow": "social_post",
                   "depends_on": ["technical"]}]})


def test_save_edit_reassign_approve_and_decision(services):
    saved = services.plan_proposals.save(proposal(services), actor="agent:planner", channel="python", note="draft")
    assert saved["revision"] == 1 and saved["status"] == "proposed"
    assert saved["history"][0]["kind"] == "save" and saved["history"][0]["at"]
    assert services.tasks.list_tasks(group_id="launch") == []
    assert services.runs.list() == []
    edited_tasks = [dict(task) for task in saved["proposal"]["tasks"]]
    edited_tasks[0]["title"] = "Technical approval"
    edited = services.plan_proposals.edit(saved["id"], edited_tasks, actor="user:ana", channel="api",
                                          note="clearer", expected_revision=1)
    assert edited["revision"] == 2
    assert edited["history"][-1]["changes"]["technical"]["before"]["title"] == "Technical review"
    assert edited["history"][-1]["actor"] == "user:ana" and edited["history"][-1]["channel"] == "api"
    reassigned = services.plan_proposals.reassign(saved["id"], "technical", actor="user:ana",
                                                  channel="cli", expected_revision=2, preferred="agent:codex")
    assert reassigned["revision"] == 3
    assert reassigned["history"][-1]["changes"]["technical"]["after"]["preferred"] == "agent:codex"
    assert reassigned["history"][-1]["at"]
    with pytest.raises(ServiceError) as stale:
        services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=2)
    assert stale.value.status == 409
    with pytest.raises(ServiceError) as nonhuman:
        services.plan_proposals.approve(saved["id"], actor="agent:planner", channel="python",
                                        expected_revision=3)
    assert nonhuman.value.status == 422
    approved = services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python",
                                                expected_revision=3)
    assert approved["status"] == "approved" and approved["decision_id"]
    assert services.decisions.get(approved["decision_id"])["status"] == "accepted"
    assert approved["plan_status"]["status"] == "running"
    assert services.tasks.get_task("technical")["preferred"] == "agent:codex"
    assert services.tasks.get_task("technical")["objective"] == "Check the design"
    assert services.tasks.get_task("commercial")["status"] == "blocked"
    assert any(task["run"] for task in approved["plan_status"]["tasks"])
    with pytest.raises(ServiceError):
        services.plan_proposals.edit(saved["id"], edited_tasks, actor="user:ana", channel="python",
                                     expected_revision=3)


def test_reject_and_existing_task_conflict(services):
    saved = services.plan_proposals.save(proposal(services), actor="user:ana")
    with pytest.raises(ServiceError):
        services.plan_proposals.reject(saved["id"], actor="agent:planner", channel="python", note="no")
    rejected = services.plan_proposals.reject(saved["id"], actor="user:ana", channel="python", note="later")
    assert rejected["status"] == "rejected"
    assert services.decisions.get(rejected["decision_id"])["status"] == "rejected"
    assert services.tasks.list_tasks(group_id="launch") == []
    with pytest.raises(ServiceError) as closed:
        services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
    assert closed.value.details["reason"] == "invalid_transition"
    saved = services.plan_proposals.save(proposal(services), actor="user:ana")
    services.tasks.create_group("launch", "Launch", actor="user:ana")
    services.tasks.create_task("launch", "technical", "Existing", actor="user:ana")
    with pytest.raises(ServiceError) as duplicate:
        services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
    assert duplicate.value.status == 409
    assert services.plan_proposals.get(saved["id"])["status"] == "proposed"
    assert len(services.tasks.list_tasks(group_id="launch")) == 1


def test_reassign_validates_workflow_bundle_and_preferred(services):
    saved = services.plan_proposals.save(proposal(services), actor="user:ana")
    with pytest.raises(ServiceError):
        services.plan_proposals.reassign(saved["id"], "technical", actor="user:ana", channel="python",
                                          expected_revision=1, preferred="x" * 101)
    with pytest.raises(ServiceError):
        services.plan_proposals.reassign(saved["id"], "technical", actor="user:ana", channel="python",
                                          expected_revision=1, bundle="missing")
    assert services.plan_proposals.get(saved["id"])["revision"] == 1
    changed = services.plan_proposals.reassign(saved["id"], "technical", actor="user:ana",
                                                channel="python", expected_revision=1,
                                                workflow="social_post_full")
    assert changed["proposal"]["tasks"][0]["workflow"] == "social_post_full"
    assert changed["proposal"]["plan"]["tasks"]["technical"]["workflow"] == "social_post_full"


def test_advance_error_keeps_approval(services, monkeypatch):
    saved = services.plan_proposals.save(proposal(services), actor="user:ana")
    def broken(*_args, **_kwargs):
        raise RuntimeError("run unavailable")
    monkeypatch.setattr(services.plans, "advance", broken)
    with pytest.raises(RuntimeError, match="run unavailable"):
        services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
    assert services.plan_proposals.get(saved["id"])["status"] == "approved"
    assert len(services.tasks.list_tasks(group_id="launch")) == 2


def test_decision_register_failure_is_reported_after_approval(services, monkeypatch):
    saved = services.plan_proposals.save(proposal(services), actor="user:ana")
    def broken(**_kwargs):
        raise RuntimeError("register unavailable")
    monkeypatch.setattr(services.decisions, "record_review", broken)
    result = services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python",
                                              expected_revision=1)
    assert result["status"] == "approved"
    assert result["decision_id"] is None and result["decision_record_error"] == "RuntimeError"
    assert result["plan_status"]["status"] == "running"


def test_migration_018_new_and_after_017(tmp_path):
    source = ROOT / "migrations"
    for through in (18, 17):
        directory = tmp_path / f"migrations-{through}"
        directory.mkdir()
        for path in source.glob("*.sql"):
            if int(path.stem.split("_", 1)[0]) <= through:
                shutil.copyfile(path, directory / path.name)
        db = Database(SimpleNamespace(db_path=tmp_path / f"data-{through}" / "multiagent.db",
                                      migrations_dir=directory))
        db.migrate()
        if through == 17:
            shutil.copyfile(source / "018_plans.sql", directory / "018_plans.sql")
            assert db.migrate() == ["018_plans.sql"]
        assert db.schema_version() == 18
        with db.connect() as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='plan_proposals'").fetchone()
            assert conn.execute("SELECT name FROM sqlite_master WHERE name='plan_proposal_history'").fetchone()


def test_api_cli_python_parity(services, tmp_path, monkeypatch, capsys):
    document = proposal(services)
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        class ClientContext:
            def __enter__(self):
                return client
            def __exit__(self, *_args):
                return False
        monkeypatch.setattr(cli, "api_client", ClientContext)
        cli.main(["plan", "save", str(path), "--actor", "user:ana"])
        saved = json.loads(capsys.readouterr().out)
        assert client.get(f"/api/v1/plan-proposals/{saved['id']}").json() == saved
        assert services.plan_proposals.get(saved["id"]) == saved
        cli.main(["plan", "proposals", "--group", "launch"])
        assert json.loads(capsys.readouterr().out) == client.get(
            "/api/v1/plan-proposals", params={"group_id": "launch"}).json()
        cli.main(["plan", "show", saved["id"]])
        assert json.loads(capsys.readouterr().out) == saved
        cli.main(["plan", "reassign", saved["id"], "technical", "--revision", "1",
                  "--preferred", "agent:codex", "--actor", "user:ana"])
        changed = json.loads(capsys.readouterr().out)
        assert changed["revision"] == 2 and changed["history"][-1]["channel"] == "cli"
        cli.main(["plan", "approve", saved["id"], "--revision", "2", "--actor", "user:ana"])
        approved = json.loads(capsys.readouterr().out)
        assert approved["status"] == "approved" and approved["plan_status"]["status"] == "running"


def test_api_and_cli_edit_reject_paths(services, tmp_path, monkeypatch, capsys):
    document = proposal(services)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        class ClientContext:
            def __enter__(self):
                return client
            def __exit__(self, *_args):
                return False
        monkeypatch.setattr(cli, "api_client", ClientContext)
        response = client.post("/api/v1/plan-proposals", json={"proposal": document,
                               "actor": "user:ana", "channel": "api"})
        assert response.status_code == 201
        saved = response.json()
        tasks = [dict(task) for task in saved["proposal"]["tasks"]]
        tasks[0]["title"] = "Technical decision"
        path = tmp_path / "tasks.json"
        path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
        cli.main(["plan", "edit", saved["id"], str(path), "--revision", "1", "--actor", "user:ana"])
        edited = json.loads(capsys.readouterr().out)
        assert edited["history"][-1]["kind"] == "edit" and edited["history"][-1]["channel"] == "cli"
        stale = client.post(f"/api/v1/plan-proposals/{saved['id']}/reassign", json={
            "task_id": "technical", "preferred": "agent:codex", "actor": "user:ana",
            "expected_revision": 1})
        assert stale.status_code == 409
        cli.main(["plan", "reject", saved["id"], "--note", "revise later", "--actor", "user:ana"])
        rejected = json.loads(capsys.readouterr().out)
        assert rejected["status"] == "rejected" and rejected["history"][-1]["channel"] == "cli"
        assert services.plan_proposals.list(status="rejected")[0]["id"] == saved["id"]
        assert client.post(f"/api/v1/plan-proposals/{saved['id']}/edit", json={
            "tasks": tasks, "actor": "user:ana", "expected_revision": 2}).status_code == 409
