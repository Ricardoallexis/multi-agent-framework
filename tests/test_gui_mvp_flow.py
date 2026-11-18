# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The GUI as the one local interface (MVP 1.0, capacity 4).

The server root opens ``/gui/``; the page loads without stray requests; and a workflow step that declares a write
tool pauses in ``tool_approval``, the run view shows the pending call without raw arguments, and «Aprobar esta
llamada» approves it by its id."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.workflows import WorkflowCatalog

INPUTS = {"topic": "Gatos que programan", "platform": "Instagram", "audience": "Equipos pequenos",
          "instructions": "Tono cercano"}
WRITE_TOOL = [{"tool": "mock.note_write", "arguments": {"target": "notes/post.txt", "text": "${topic}"}}]


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        max_llm_calls=6, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)
    folder = tmp_path / "workflows"
    folder.mkdir()
    for name in ("social_post.yaml", "social_post_full.yaml"):
        document = yaml.safe_load((Path(settings.workflows_dir) / name).read_text(encoding="utf-8"))
        for step in document["steps"]:
            if step["id"] == "create":
                step["tools"] = WRITE_TOOL
        (folder / name).write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    services.system.engine.workflows = WorkflowCatalog(settings, workflows_dir=folder)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, headers={"Authorization": f"Bearer {token}"})
    services.runs.close()


def view_of(env, run_id):
    response = env.client.get(f"/gui/api/runs/{run_id}", headers=env.headers)
    assert response.status_code == 200, response.text
    return response.json()


def approve(env, run_id, body):
    return env.client.post(f"/gui/api/runs/{run_id}/approve", json=body, headers=env.headers)


def test_the_gui_shows_the_pending_tool_call_and_approves_it_by_its_permission_request(env):
    created = env.client.post("/gui/api/runs", headers=env.headers, json={
        "workflow_id": "social_post", "project_name": "Prueba GUI", "inputs": dict(INPUTS), "execution_mode": "auto"})
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    for _ in range(4):                                     # research, its review, then the create step
        env.services.system.worker.process_once()
        view = view_of(env, run_id)
        if view["waiting"] and view["waiting"]["reason"] == "tool_approval":
            break
        assert view["waiting"] and view["waiting"]["reason"] == "review", view
        assert approve(env, run_id, {}).status_code == 200
    else:
        pytest.fail("the run never asked to approve the tool call")

    call = view["tool_approval"]
    assert call["tool_id"] == "mock.note_write" and call["step_id"] == "create" and call["permission_request_id"]
    assert "arguments" not in call and "Gatos que programan" not in str(call)   # never raw arguments
    assert approve(env, run_id, {}).status_code == 409                          # «Aprobar» of a review is not enough
    assert approve(env, run_id, {"permission_request_id": "pr_other"}).status_code == 409
    assert view_of(env, run_id)["waiting"]["reason"] == "tool_approval"           # nothing was approved

    approved = approve(env, run_id, {"permission_request_id": call["permission_request_id"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "queued" and approved.json()["tool_approval"] is None
    env.services.system.worker.process_once()
    view = view_of(env, run_id)
    assert view["waiting"]["reason"] == "review"
    events = [event["event_type"] for event in env.services.runs.get(run_id)["events"]]
    assert events.count("tool_call_completed") == 1 and "tool_approval_released" in events
    assert approve(env, run_id, {}).json()["status"] == "completed"


def test_the_server_root_opens_the_gui_and_the_page_declares_its_icon(env):
    root = env.client.get("/", follow_redirects=False)
    assert root.status_code == 307 and root.headers["location"] == "/gui/"
    page = env.client.get("/gui/")
    assert page.status_code == 200 and '<link rel="icon" href="data:,">' in page.text   # no /favicon.ico request
    assert "data:" in page.headers["content-security-policy"].split("img-src", 1)[1].split(";", 1)[0]


def test_the_run_screen_draws_the_tool_call_and_sends_its_permission_request_id():
    source = (STATIC_DIR / "js" / "human-actions.js").read_text(encoding="utf-8")
    assert 'waiting.reason === "tool_approval"' in source
    assert "permission_request_id: call.permission_request_id" in source
    assert "${call.summary" in source and "call.impacts" in source and "call.actions" in source
    assert not re.search(r"call\.arguments\b", source)                          # the view never holds raw arguments
    assert "no sabe mostrar" in source                                          # other unknown reasons keep the notice
