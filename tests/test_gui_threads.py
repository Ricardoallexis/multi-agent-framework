# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The run thread is a read-only projection of persisted run events."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        max_llm_calls=4, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def get(env, run_id, **params):
    return env.client.get(f"/gui/api/threads/runs/{run_id}", params=params,
                          headers={"Authorization": f"Bearer {env.token}"})


def create(env):
    response = env.client.post("/gui/api/runs", headers={"Authorization": f"Bearer {env.token}"}, json={
        "workflow_id": "social_post", "project_name": "Thread test",
        "inputs": {"topic": "Cats", "platform": "Instagram", "audience": "Teams", "instructions": "Plain"},
        "execution_mode": "auto"})
    assert response.status_code == 202, response.text
    return response.json()["id"]


def entries(env, run_id):
    response = get(env, run_id)
    assert response.status_code == 200, response.text
    return response.json()["entries"]


def test_review_thread_and_incremental_cursor(env):
    run_id = create(env)
    initial = get(env, run_id).json()
    assert [entry["kind"] for entry in initial["entries"]] == ["launched"]
    launch = initial["entries"][0]
    assert (launch["workflow_id"], launch["project_name"], launch["execution_mode"]) == (
        "social_post", "Thread test", "auto")
    assert env.services.system.worker.process_once()
    reply = get(env, run_id, after=initial["cursor"]).json()
    assert reply["cursor"] > initial["cursor"]
    assert all(item["seq"] > initial["cursor"] for item in reply["entries"])
    assert [item["seq"] for item in reply["entries"]] == sorted(item["seq"] for item in reply["entries"])
    step = next(item for item in reply["entries"] if item["kind"] == "step")
    assert step["step_id"] == "create" and step["status"] == "completed"
    assert step["executor"]["kind"] == "model"
    waiting = next(item for item in reply["entries"] if item["kind"] == "waiting")
    assert waiting["reason"] == "review"
    assert waiting["decision"]["actions"] == ["approve", "changes", "reject"]
    assert waiting["decision"]["step_id"] == "create"
    assert get(env, run_id, after=reply["cursor"]).json()["entries"] == []
    env.services.runs.approve(run_id)
    final = get(env, run_id, after=reply["cursor"]).json()["entries"]
    assert len(final) == 1 and final[0]["kind"] == "final" and final[0]["status"] == "completed"
    assert final[0]["output"]


def test_tool_approval_id_and_read_only_action_list(env, monkeypatch):
    run_id = create(env)
    store = env.services.system.store
    store.update_run(run_id, status="waiting_human", waiting_reason="tool_approval",
                     waiting_step="create", waiting_attempt=1)
    store.db.log_event(run_id, "tool_approval_requested", {"step_id": "create", "attempt": 1})
    monkeypatch.setattr("multiagent.gui.runs.pending_tool_approval", lambda run: {
        "permission_request_id": "tpr_test"})
    waiting = entries(env, run_id)[-1]
    assert waiting["decision"] == {"type": "tool_approval", "step_id": "create", "attempt": 1,
                                   "actions": ["approve", "reject"], "permission_request_id": "tpr_test"}


@pytest.mark.parametrize("reason,actions", [
    ("budget_exhausted", ["approve", "reject"]),
    ("budget_cut_approval", ["approve", "reject"]),
    ("escalation_approval", ["approve", "reject"]),
    ("content_review", ["approve", "reject"]),
    ("egress_review", ["approve", "reject"]),
    ("execute_step", ["human-submit", "reject"]),
])
def test_other_wait_reasons(env, reason, actions):
    run_id = create(env)
    store = env.services.system.store
    store.update_run(run_id, status="waiting_human", waiting_reason=reason,
                     waiting_step="create", waiting_attempt=1)
    store.db.log_event(run_id, "call_paused", {"step_id": "create", "attempt": 1,
                                              "waiting_reason": reason})
    waiting = entries(env, run_id)[-1]
    assert waiting["reason"] == reason and waiting["decision"]["actions"] == actions


@pytest.mark.parametrize("status,event,error", [
    ("failed", "workflow_failed", "bad run"),
    ("cancelled", "workflow_cancelled", ""),
    ("rejected", "human_rejected", ""),
])
def test_terminal_messages(env, status, event, error):
    run_id = create(env)
    env.services.system.store.update_run(run_id, status=status, error=error)
    env.services.system.store.db.log_event(run_id, event, {"error": error})
    final = entries(env, run_id)[-1]
    assert final["kind"] == "final" and final["status"] == status
    assert final["error"] == error


def test_inbox_events_and_clean_text(env):
    run_id = create(env)
    store = env.services.system.store
    store.db.log_event(run_id, "user.message", {"message_id": "m1", "kind": "note",
                                                "text": "x" * 2100, "path": "C:/secret"})
    store.db.log_event(run_id, "user.message_consumed", {"message_id": "m1"})
    messages = entries(env, run_id)[1:]
    assert [entry["kind"] for entry in messages] == ["user_message", "message_consumed"]
    assert len(messages[0]["text"]) == 2003 and "C:/secret" not in json.dumps(messages)


def test_missing_run_and_missing_token(env):
    assert get(env, "missing").status_code == 404
    assert env.client.get("/gui/api/threads/runs/missing").status_code == 401
    run_id = create(env)
    assert get(env, run_id, after=-1).status_code == 422


def test_bundle_run_is_read_from_its_own_store(env):
    response = env.client.post("/gui/api/runs", headers={"Authorization": f"Bearer {env.token}"}, json={
        "workflow_id": "spec_review", "bundle": "team", "project_name": "Bundle thread",
        "inputs": {"spec_text": "Synthetic requirement"}})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    launch = entries(env, run_id)[0]
    assert launch["workflow_id"] == "spec_review" and launch["project_name"] == "Bundle thread"
