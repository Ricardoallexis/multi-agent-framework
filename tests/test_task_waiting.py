# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T15: independent task waits, atomic decisions and API; synthetic data only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_claude_home=tmp_path / "claude", gui_codex_home=tmp_path / "codex",
                        gui_copilot_home=tmp_path / "copilot")
    service = ApplicationServices.create(settings, dry_run=True)
    service.tasks.wait_clock = lambda: NOW
    service.tasks.create_group("G", "Synthetic", actor="user", authorized=True)
    for task_id in ("A", "B"):
        service.tasks.create_task("G", task_id, task_id, actor="user")
    service.tasks.transition("A", "claim", actor="agent:a")
    service.tasks.transition("A", "start", actor="agent:a")
    yield service
    service.runs.close()


def put(tasks, **changes):
    data = {"who": "user", "question": "Synthetic question?", "actor": "agent:a",
            "expected_revision": tasks.get_task("A")["revision"]}
    data.update(changes)
    return tasks.set_wait("A", **data)


def end_args(task):
    return {"wait_id": task["waiting"]["id"], "expected_revision": task["revision"], "actor": "user"}


def rejected(call, status):
    with pytest.raises(ServiceError) as error:
        call()
    assert error.value.status == status


def test_wait_preserves_lifecycle_and_execution_and_exposes_gui_fields(services):
    tasks = services.tasks
    before = tasks.get_task("A")
    with tasks.store.db.connect() as conn:
        execution = tasks.store.get_task("A", conn=conn)["execution"]
    after = put(tasks, reference="synthetic:decision")
    for key in ("state", "status", "assignee", "attempt", "label"):
        assert after[key] == before[key]
    assert after["revision"] == before["revision"] + 1
    wait = after["waiting"]
    assert wait["label"] == "waiting" and wait["kind"] == "approval_pending"
    assert wait["since"] == NOW.isoformat() and wait["reference"] == "synthetic:decision"
    with tasks.store.db.connect() as conn:
        assert tasks.store.get_task("A", conn=conn)["execution"] == execution
    assert tasks.events(task_id="A")[-1]["type"] == "task.wait_set"


def test_user_decision_clears_only_metadata_not_gate_or_task_state(services):
    tasks = services.tasks
    waiting = put(tasks)
    after = tasks.resolve_wait("A", **end_args(waiting), decision="Proceed with the synthetic option")
    assert after["waiting"] is None and after["state"] == "running" and after["attempt"] == waiting["attempt"]
    assert tasks.events(task_id="A")[-1]["type"] == "task.wait_resolved"
    assert not any(e["type"] == "task.accepted" for e in tasks.events(task_id="A"))


@pytest.mark.parametrize("actor", ["agent:a", "system:watcher", "executor:fake", "system"])
def test_automation_never_records_user_approval(services, actor):
    waiting = put(services.tasks)
    args = {**end_args(waiting), "actor": actor}
    rejected(lambda: services.tasks.resolve_wait("A", **args, decision="approved"), 409)
    assert services.tasks.get_task("A")["waiting"] == waiting["waiting"]


def test_creator_may_withdraw_question_without_answering_it(services):
    tasks = services.tasks
    waiting = put(tasks)
    after = tasks.clear_wait("A", **{**end_args(waiting), "actor": "agent:a"})
    assert after["waiting"] is None and after["state"] == "running"
    event = tasks.events(task_id="A")[-1]
    assert event["type"] == "task.wait_cleared" and event["payload"]["outcome"] == "withdrawn"


def test_replacement_rejects_late_reply_and_stale_revision(services):
    tasks = services.tasks
    first = put(tasks)
    second = put(tasks, question="Replacement question?")
    assert second["waiting"]["id"] != first["waiting"]["id"]
    rejected(lambda: tasks.resolve_wait("A", **end_args(first), decision="late"), 409)
    rejected(lambda: tasks.resolve_wait("A", **{**end_args(second), "wait_id": first["waiting"]["id"]}, decision="late"), 409)
    assert tasks.get_task("A")["waiting"] == second["waiting"]


def test_agent_wait_accepts_only_requested_agent_or_explicit_human(services):
    tasks = services.tasks
    waiting = put(tasks, who="agent", target="agent:b")
    rejected(lambda: tasks.resolve_wait("A", **{**end_args(waiting), "actor": "agent:c"}, decision="answer"), 409)
    assert tasks.resolve_wait("A", **{**end_args(waiting), "actor": "agent:b"}, decision="answer")["waiting"] is None


def test_task_wait_cannot_resolve_before_target_is_accepted(services):
    tasks = services.tasks
    waiting = put(tasks, who="task", target="B")
    rejected(lambda: tasks.resolve_wait("A", **end_args(waiting), decision="done"), 409)
    tasks.transition("B", "claim", actor="agent:b")
    tasks.transition("B", "start", actor="agent:b")
    tasks.transition("B", "submit", actor="agent:b")
    tasks.transition("B", "accept", actor="user")
    assert tasks.resolve_wait("A", **end_args(tasks.get_task("A")), decision="done")["waiting"] is None


def test_quota_has_aware_reset_and_does_not_resolve_early(services):
    tasks = services.tasks
    waiting = put(tasks, who="quota", reset_at=(NOW + timedelta(hours=1)).isoformat())
    rejected(lambda: tasks.resolve_wait("A", **end_args(waiting), decision="reset"), 409)
    tasks.wait_clock = lambda: NOW + timedelta(hours=1)
    assert tasks.resolve_wait("A", **end_args(waiting), decision="reset")["waiting"] is None


@pytest.mark.parametrize("changes", [
    {"who": "unknown"}, {"who": "agent"}, {"who": "window"}, {"who": "task", "target": "A"},
    {"who": "quota"}, {"who": "quota", "reset_at": "2026-01-02T00:00:00"},
    {"who": "quota", "reset_at": "2025-01-01T00:00:00Z"}, {"reset_at": "2026-01-02T00:00:00Z"},
    {"question": ""}, {"question": "x" * 501}, {"question": "😀" * 400}, {"expected_revision": True},
])
def test_invalid_wait_is_rejected_without_partial_writes(services, changes):
    tasks = services.tasks
    revision = tasks.get_task("A")["revision"]
    rejected(lambda: put(tasks, **changes), 422)
    task = tasks.get_task("A")
    assert task["revision"] == revision and task["waiting"] is None


def test_unrelated_actor_and_missing_target_are_rejected(services):
    rejected(lambda: put(services.tasks, actor="agent:other"), 409)
    rejected(lambda: put(services.tasks, who="task", target="MISSING"), 404)


def test_state_and_wait_event_roll_back_together(services, monkeypatch):
    tasks = services.tasks
    before = tasks.get_task("A")
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic event failure")
    monkeypatch.setattr(tasks.store, "append_event", fail)
    with pytest.raises(RuntimeError):
        put(tasks)
    assert tasks.get_task("A") == before


def test_terminal_transition_clears_wait_but_reopen_does_not_restore_it(services):
    tasks = services.tasks
    put(tasks)
    closed = tasks.transition("A", "cancel", actor="agent:a")
    assert closed["waiting"] is None
    rejected(lambda: put(tasks), 409)
    reopened = tasks.transition("A", "reopen", actor="user", expected_revision=closed["revision"], cause="synthetic follow-up")
    assert reopened["waiting"] is None


def test_human_changes_request_clears_wait_in_same_transaction(services):
    tasks = services.tasks
    tasks.transition("A", "submit", actor="agent:a")
    put(tasks)
    changed = tasks.transition("A", "request_changes", actor="user")
    assert changed["state"] == "needs_changes" and changed["waiting"] is None


def test_competing_writes_use_revision_cas(services):
    tasks = services.tasks
    revision = tasks.get_task("A")["revision"]
    def write(question):
        try:
            return put(tasks, question=question, expected_revision=revision)["waiting"]["question"]
        except ServiceError as error:
            assert error.status == 409
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(write, ["one?", "two?"]))
    assert outcomes.count("conflict") == 1
    assert len([e for e in tasks.events(task_id="A") if e["type"] == "task.wait_set"]) == 1


def test_api_waiting_panel_data_decision_and_validation(services):
    tasks = services.tasks
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        request = {"who": "user", "question": "<synthetic> choose?", "actor": "agent:a",
                   "expected_revision": tasks.get_task("A")["revision"], "reference": "synthetic:ref"}
        response = client.put("/api/v1/tasks/A/waiting", json=request)
        assert response.status_code == 200, response.text
        task = response.json()
        panel = client.get("/api/v1/tasks", params={"waiting_for": "user"}).json()
        assert [t["id"] for t in panel] == ["A"] and panel[0]["waiting"] == task["waiting"]
        assert client.get("/api/v1/tasks/A").json()["state"] == "running"
        assert client.get("/api/v1/tasks", params={"waiting_for": "unknown"}).status_code == 422
        assert client.put("/api/v1/tasks/A/waiting", json={**request, "expected_revision": True}).status_code == 422
        body = {**end_args(task), "decision": "Use the synthetic choice"}
        answer = client.post("/api/v1/tasks/A/waiting/decision", json=body)
        assert answer.status_code == 200, answer.text
        assert answer.json()["waiting"] is None and answer.json()["state"] == "running"
        assert client.get("/api/v1/tasks", params={"waiting_for": "all"}).json() == []


def test_api_clear_is_withdrawal_and_stale_reply_cannot_clear_new_wait(services):
    tasks = services.tasks
    first = put(tasks)
    second = put(tasks, question="New question?")
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        assert client.post("/api/v1/tasks/A/waiting/decision", json={**end_args(first), "decision": "late"}).status_code == 409
        reply = client.post("/api/v1/tasks/A/waiting/clear", json={**end_args(second), "actor": "agent:a"})
        assert reply.status_code == 200 and reply.json()["waiting"] is None


def test_migration_requires_an_object_and_existing_rows_default_empty(services):
    tasks = services.tasks
    assert tasks.get_task("B")["waiting"] is None
    with pytest.raises(sqlite3.IntegrityError), tasks.store.transaction() as conn:
        conn.execute("UPDATE tasks SET waiting_json = '[]' WHERE id = 'A'")
    assert tasks.get_task("A")["waiting"] is None


def test_migration_adds_waiting_without_changing_existing_task(tmp_path):
    source = Path(__file__).resolve().parents[1] / "migrations"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for file in source.glob("*.sql"):
        if int(file.name.split("_", 1)[0]) < 12:
            shutil.copyfile(file, migrations / file.name)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", migrations_dir=migrations,
                        worker_enabled=False, mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False)
    service = ApplicationServices.create(settings, dry_run=True)
    try:
        service.tasks.create_group("G", "Synthetic legacy", actor="user", authorized=True)
        before = service.tasks.create_task("G", "A", "Preserved synthetic task", actor="user")
        shutil.copyfile(source / "012_task_waiting.sql", migrations / "012_task_waiting.sql")
        service.system.db.migrate()
        after = service.tasks.get_task("A")
        assert after == before and after["waiting"] is None
        assert service.tasks.set_wait("A", who="user", question="New synthetic question?", actor="user",
                                      expected_revision=after["revision"])["waiting"]["who"] == "user"
    finally:
        service.runs.close()
