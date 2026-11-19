# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""C10: a plan that uses workflow roles waits for an approved bundle team."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"


class ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *_args) -> bool:
        return False


@pytest.fixture
def env(tmp_path):
    bundle_dir = tmp_path / "general"
    shutil.copytree(GENERAL_BUNDLE, bundle_dir)
    workflow_path = bundle_dir / "workflows" / "summarize_document.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow["steps"][0]["role"] = "analyst"
    workflow["steps"][1]["role"] = "writer"
    workflow_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
        execution_backend="mock", mock_mode=True, gemini_api_key="",
        openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, bundles={"general": bundle_dir})
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def create_proposal(env, *, workflow="summarize_document"):
    inputs = {"document_text": "A synthetic document."}
    proposal = env.services.planning.propose("manual", {
        "group_id": "launch",
        "goal": "Launch product",
        "tasks": [{
            "id": "review",
            "title": "Review the document",
            "workflow": workflow,
            "bundle": "general",
            "depends_on": [],
            "inputs": inputs,
        }],
    })
    return env.services.plan_proposals.save(proposal, actor="agent:planner", channel="python")


def gui_call(env, method, path, **kwargs):
    return env.client.request(
        method, "/gui/api/plan-proposals" + path,
        headers={"Authorization": f"Bearer {env.token}"}, **kwargs,
    )


def test_missing_team_blocks_before_creating_group_or_tasks(env):
    saved = create_proposal(env)
    expected = [{"bundle": "general", "roles": ["analyst", "writer"]}]
    assert saved["blockers"] == expected

    with pytest.raises(ServiceError) as caught:
        env.services.plan_proposals.approve(
            saved["id"], actor="user:ana", channel="python", expected_revision=1,
        )
    assert caught.value.status == 409
    assert caught.value.details == {"reason": "team_not_approved", "blockers": expected}
    assert env.services.plan_proposals.get(saved["id"])["status"] == "proposed"
    assert env.services.plan_proposals.list(status="proposed")[0]["blockers"] == expected
    assert env.services.tasks.list_tasks(group_id="launch") == []
    with env.services.system.db.connect() as conn:
        assert conn.execute("SELECT id FROM task_groups WHERE id='launch'").fetchone() is None


def test_role_plan_is_approved_after_its_team_is_approved(env):
    team = env.services.teams.propose("general", actor="user:ana", channel="python")
    env.services.teams.approve(team["id"], actor="user:ana", channel="python")
    saved = create_proposal(env)
    assert saved["blockers"] == []

    approved = env.services.plan_proposals.approve(
        saved["id"], actor="user:ana", channel="python", expected_revision=1,
    )
    assert approved["status"] == "approved"
    assert approved["blockers"] == []
    assert approved["plan_status"]["status"] == "running"


def test_package_workflow_without_roles_needs_no_team(env):
    saved = create_proposal(env, workflow="review_checklist")
    assert saved["blockers"] == []

    approved = env.services.plan_proposals.approve(
        saved["id"], actor="user:ana", channel="python", expected_revision=1,
    )
    assert approved["status"] == "approved"


def test_views_survive_a_workflow_that_can_no_longer_be_read(env, tmp_path):
    saved = create_proposal(env)
    (tmp_path / "general" / "workflows" / "summarize_document.yaml").unlink()

    assert env.services.plan_proposals.get(saved["id"])["blockers"] == []
    assert [row["id"] for row in env.services.plan_proposals.list(status="proposed")] == [saved["id"]]
    with pytest.raises(ServiceError):
        env.services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
    assert env.services.plan_proposals.get(saved["id"])["status"] == "proposed"


def test_api_cli_and_gui_return_the_same_team_conflict(env, monkeypatch, capsys):
    saved = create_proposal(env)
    blockers = [{"bundle": "general", "roles": ["analyst", "writer"]}]
    expected = {"reason": "team_not_approved", "blockers": blockers}

    assert env.client.get(f"/api/v1/plan-proposals/{saved['id']}").json()["blockers"] == blockers
    assert gui_call(env, "GET", f"/{saved['id']}").json()["blockers"] == blockers

    api = env.client.post(f"/api/v1/plan-proposals/{saved['id']}/approve", json={
        "actor": "user:ana", "expected_revision": 1,
    })
    gui = gui_call(env, "POST", f"/{saved['id']}/approve", json={"expected_revision": 1})
    assert api.status_code == gui.status_code == 409
    assert api.json()["detail"] == gui.json()["detail"]
    assert api.json()["detail"]["code"] == "invalid_state"
    assert api.json()["detail"]["details"] == expected

    monkeypatch.setattr(cli, "api_client", lambda: ClientContext(env.client))
    with pytest.raises(SystemExit) as exit_code:
        cli.main(["plan", "approve", saved["id"], "--revision", "1", "--actor", "user:ana"])
    assert exit_code.value.code == 1
    cli_error = capsys.readouterr().err
    assert "HTTP 409: invalid_state:" in cli_error
    assert '"reason": "team_not_approved"' in cli_error
    assert '"bundle": "general"' in cli_error
    assert '"roles": [' in cli_error and '"analyst"' in cli_error and '"writer"' in cli_error
