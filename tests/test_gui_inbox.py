# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVA-T05: the GUI inbox and background runs with synthetic work only."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.availability import AvailabilityPolicy


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
JS = STATIC_DIR / "js"


@pytest.fixture
def env(tmp_path):
    bundle = tmp_path / "general"
    shutil.copytree(ROOT / "examples" / "bundles" / "general", bundle)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": bundle}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield client, services, {"Authorization": f"Bearer {token}"}
    services.runs.close()


def create_run(client, headers):
    response = client.post("/gui/api/runs", headers=headers, json={
        "workflow_id": "social_post", "project_name": "Synthetic background",
        "inputs": {"topic": "Synthetic", "platform": "Test", "audience": "Test", "instructions": "No calls"},
        "execution_mode": "auto",
    })
    assert response.status_code == 202, response.text
    return response.json()["id"]


def test_run_messages_require_token_and_policy_and_never_decide(env):
    client, services, headers = env
    run_id = create_run(client, headers)
    path = f"/gui/api/runs/{run_id}/messages"
    assert client.get(path).status_code == 401
    assert client.post(path, json={"message_id": "m1", "kind": "cancel"}).status_code == 401
    assert client.get("/gui/api/inbox/policy", headers=headers).json()["enabled"] is False
    denied = client.post(path, headers=headers, json={"message_id": "m1", "kind": "cancel"})
    assert denied.status_code == 409 and denied.json()["detail"]["details"]["reason"] == "availability_disabled"
    assert client.get(path, headers=headers).json() == []

    services.inbox.availability = AvailabilityPolicy(enabled=True)
    assert client.get("/gui/api/inbox/policy", headers=headers).json()["enabled"] is True
    posted = client.post(path, headers=headers, json={"message_id": "m1", "kind": "cancel", "text": ""})
    assert posted.status_code == 200 and posted.json()["actor"] == "user:operator"
    assert client.post(path, headers=headers, json={"message_id": "m1", "kind": "cancel"}).json()["created"] is False
    assert [item["kind"] for item in client.get(path, headers=headers).json()] == ["cancel"]
    assert client.get(path, headers=headers, params={"after": 1}).json() == []
    assert services.runs.get(run_id)["status"] != "cancelled"
    assert not any(event["event_type"] in {"human_approved", "review_approved"}
                   for event in services.runs.get(run_id)["events"])


def test_task_messages_use_the_same_service_with_fixed_actor(env):
    client, services, headers = env
    services.tasks.create_group("inbox-group", "Synthetic group", actor="user:operator")
    services.tasks.create_task("inbox-group", "T1", "Synthetic task", actor="user:operator")
    services.inbox.availability = AvailabilityPolicy(enabled=True)
    path = "/gui/api/tasks/T1/messages"
    answer = client.post(path, headers=headers, json={"message_id": "n1", "kind": "note", "text": "Remember this"})
    assert answer.status_code == 200 and answer.json()["actor"] == "user:operator"
    assert client.get(path, headers=headers).json()[0]["text"] == "Remember this"
    assert client.post(path, headers=headers, json={"message_id": "a1", "kind": "approve"}).status_code == 422
    assert client.post(path, headers=headers, json={"message_id": "n2", "kind": "note",
                                                 "actor": "agent:fake", "text": "x"}).status_code == 422


def test_background_section_reads_only_active_runs_and_preserves_pending_contract(env):
    client, services, headers = env
    run_id = create_run(client, headers)
    services.system.store.update_run(run_id, status="running")
    background = client.get("/gui/api/pending/background", headers=headers)
    assert background.status_code == 200
    item = next(item for item in background.json()["items"] if item["id"] == "run:" + run_id)
    assert set(item) == {"id", "type", "title", "reason", "since", "href"}
    assert item["href"] == "#new/run/" + run_id
    assert set(client.get("/gui/api/pending", headers=headers).json()) == {"items", "count"}
    services.system.store.update_run(run_id, status="completed")
    assert all(item["id"] != "run:" + run_id
               for item in client.get("/gui/api/pending/background", headers=headers).json()["items"])


@pytest.mark.skipif(NODE is None, reason="Node is required")
def test_pure_inbox_rules_and_view_wiring():
    script = f"""
      import * as m from {json.dumps((JS / 'inbox-model.js').as_uri())};
      console.log(JSON.stringify({{
        kinds: m.MESSAGE_KINDS, confirmations: m.NEEDS_CONFIRMATION,
        disabled: m.messageError('note', 'x', false, 'policy off'),
        empty: m.messageError('adjust', ' ', true),
        note: m.messageError('note', 'hello', true),
        path: m.messagePath('runs', 'a/b'),
      }}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "kinds": ["cancel", "interrupt", "adjust", "note"],
        "confirmations": ["cancel", "interrupt", "adjust"],
        "disabled": "policy off", "empty": "Escribe un mensaje.", "note": "",
        "path": "/gui/api/runs/a%2Fb/messages",
    }
    thread = ui_text((JS / "thread.js"))
    pending = ui_text((JS / "pending.js"))
    assert "<${RunInbox}" in thread and 'messagePath("runs", runId)' in thread
    assert 'messagePath("runs", cancelId)' in pending and "pending.background" in pending
    assert "approve" not in ui_text((JS / "inbox-model.js"))
