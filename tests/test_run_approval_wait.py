"""Durable review deadlines; fake adapter, synthetic data and a controlled clock only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.bootstrap import build_system
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.errors import InvalidStateTransition
from multiagent.workflows import WorkflowCatalog


@pytest.fixture
def system(tmp_path, monkeypatch):
    result = build_system(Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                                   gemini_api_key="", openai_api_key="", openai_enabled=False), dry_run=True)
    result.engine.workflows = WorkflowCatalog(result.settings, workflows_dir=
        Path(__file__).parent / "fixtures" / "workflows" / "g2_intermediate")
    clock = [datetime(2032, 1, 1, tzinfo=timezone.utc)]
    monkeypatch.setattr(result.engine, "_now", lambda: clock[0].isoformat())
    result.test_clock = clock
    yield result
    result.worker.stop()
    result.adapters["ollama"].client.close()


def checkpoint(system):
    run = system.run_service.create_run(SocialPostRequest(project_name="Synthetic approval",
        objective="Test deadlines", topic="Synthetic artifact", use_brand_context=False, pipeline_mode="full"))
    return system.engine.process_run(run["id"])


def arm(system):
    run = checkpoint(system)
    return system.engine.request_approval(run["id"], deadline=system.test_clock[0] + timedelta(seconds=60))


def test_legacy_checkpoint_is_unchanged(system):
    run = checkpoint(system)
    assert run["waiting_reason"] == "review"
    assert run["attention"] is None


def test_configured_checkpoint_arms_deadline(system):
    system.engine.approval_timeout_seconds = 60
    run = checkpoint(system)
    assert run["waiting_reason"] == "approval_pending"
    assert run["attention"]["who"] == "user"
    assert run["attention"]["deadline"] == "2032-01-01T00:01:00+00:00"


def test_deadline_exact_boundary_and_one_event(system):
    run = arm(system)
    system.test_clock[0] += timedelta(seconds=59)
    assert system.engine.check_approval_deadlines() == 0
    system.test_clock[0] += timedelta(seconds=1)
    assert system.engine.check_approval_deadlines() == 1
    assert system.engine.check_approval_deadlines() == 0
    after = system.run_service.get(run["id"])
    assert after["status"] == "waiting_human"
    assert after["llm_calls"] == run["llm_calls"]
    assert after["attention"]["overdue"] is True
    assert all(a["approved"] == 0 for a in after["artifacts"])
    assert len([e for e in after["events"] if e["event_type"] == "run.approval_overdue"]) == 1


def test_concurrent_observers_and_restart(system):
    run = arm(system)
    system.test_clock[0] += timedelta(seconds=60)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: system.engine.check_approval_deadlines(), range(2)))
    assert sum(counts) == 1
    # A new store instance sees the durable wait and the same notice.
    other = type(system.store)(system.store.db)
    assert other.get_run(run["id"])["attention"]["overdue"] is True


@pytest.mark.parametrize("action", ["approve", "reject", "cancel", "changes"])
def test_human_resolution_clears_attention_and_stale_deadline(system, action):
    run = arm(system)
    if action == "changes":
        system.run_service.changes(run["id"], "Synthetic revision")
    else:
        getattr(system.run_service, action)(run["id"])
    system.test_clock[0] += timedelta(hours=1)
    assert system.engine.check_approval_deadlines() == 0
    assert system.run_service.get(run["id"])["attention"] is None


def test_retry_preserves_deadline_and_next_attempt_gets_new_wait(system):
    run = arm(system)
    first = run["attention"]
    assert system.engine.request_approval(run["id"], deadline=system.test_clock[0] +
        timedelta(hours=2))["attention"] == first
    system.run_service.changes(run["id"], "Synthetic revision")
    system.engine.approval_timeout_seconds = 120
    next_run = system.engine.process_run(run["id"])
    assert next_run["attention"]["wait_id"] != first["wait_id"]
    assert next_run["attention"]["attempt"] > first["attempt"]


def test_api_service_list_and_idle_worker_observe(system):
    run = arm(system)
    system.test_clock[0] += timedelta(seconds=60)
    assert system.worker.process_once() is False
    item = next(r for r in system.run_service.list() if r["id"] == run["id"])
    assert item["attention"]["overdue"] is True
    assert system.engine.process_run(run["id"])["status"] == "waiting_human"


@pytest.mark.parametrize("deadline", [datetime(2032, 1, 2), datetime(2031, 1, 1, tzinfo=timezone.utc)])
def test_invalid_deadline_is_rejected_without_mutation(system, deadline):
    run = checkpoint(system)
    with pytest.raises(ValueError):
        system.engine.request_approval(run["id"], deadline=deadline)
    assert system.store.get_run(run["id"])["waiting_reason"] == "review"


def test_cancellation_race_rejects_stale_wait(system):
    run = checkpoint(system)
    _, artifact = system.engine.review_point(run)
    system.run_service.cancel(run["id"])
    with pytest.raises(InvalidStateTransition):
        system.store.begin_approval_wait(run, artifact, since=system.engine._now(),
                                        deadline="2032-01-01T00:01:00+00:00")
    assert system.store.get_run(run["id"])["attention"] is None


def test_http_attention_and_explicit_human_approval(system):
    run = arm(system)
    system.test_clock[0] += timedelta(seconds=60)
    with TestClient(create_app(system), base_url="http://localhost") as client:
        response = client.get(f"/api/v1/runs/{run['id']}")
        assert response.status_code == 200
        assert response.json()["attention"]["overdue"] is True
        listing = client.get("/api/v1/runs").json()
        assert next(item for item in listing if item["id"] == run["id"])["attention"]["who"] == "user"
        response = client.post(f"/api/v1/runs/{run['id']}/approve")
        assert response.status_code == 200
        assert response.json()["attention"] is None


def test_begin_wait_rolls_back_if_event_write_fails(system):
    run = checkpoint(system)
    with system.store.db.tx() as conn:
        conn.execute("CREATE TRIGGER reject_pending BEFORE INSERT ON run_events "
                     "WHEN NEW.event_type='run.approval_pending' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="synthetic"):
        system.engine.request_approval(run["id"], deadline=system.test_clock[0] + timedelta(seconds=60))
    after = system.store.get_run(run["id"])
    assert after["waiting_reason"] == "review"
    assert after["attention"] is None


@pytest.mark.parametrize("action", ["approve", "changes"])
def test_human_decision_before_arming_never_fails_requeued_run(system, monkeypatch, action):
    system.engine.approval_timeout_seconds = 60
    original = system.store.begin_approval_wait

    def decide_first(run, artifact, **kwargs):
        if action == "changes":
            system.run_service.changes(run["id"], "Synthetic revision")
        else:
            system.run_service.approve(run["id"])
        return original(run, artifact, **kwargs)

    monkeypatch.setattr(system.store, "begin_approval_wait", decide_first)
    result = checkpoint(system)
    assert result["status"] == "queued"
    assert result["attention"] is None
    assert not any(event["event_type"] == "run_failed" for event in result["events"])


def test_reads_take_no_write_transaction_until_a_deadline_is_due(system, monkeypatch):
    run = arm(system)
    original = system.store.db.tx
    calls = []

    def transaction():
        calls.append(True)
        return original()

    monkeypatch.setattr(system.store.db, "tx", transaction)
    system.run_service.get(run["id"])
    system.run_service.list()
    assert calls == []
    system.test_clock[0] += timedelta(seconds=60)
    assert system.run_service.get(run["id"])["attention"]["overdue"] is True
    assert len(calls) == 1
    system.run_service.list()
    assert len(calls) == 1


def test_settings_default_off_and_bootstrap_wires_timeout(tmp_path):
    base = dict(_env_file=None, local_dir=tmp_path, worker_enabled=False,
                gemini_api_key="", openai_api_key="", openai_enabled=False)
    assert Settings(**base).approval_timeout_seconds is None
    result = build_system(Settings(**base, approval_timeout_seconds=60), dry_run=True)
    try:
        assert result.engine.approval_timeout_seconds == 60
    finally:
        result.worker.stop()
        result.adapters["ollama"].client.close()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_settings_reject_invalid_timeout(value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, approval_timeout_seconds=value)


def test_gui_review_actions_are_visible_for_approval_pending(system):
    from multiagent.gui.runs import run_view
    run = arm(system)
    from multiagent.services import ApplicationServices
    view = run_view(ApplicationServices(system), run)
    assert view["waiting"]["reason"] == "approval_pending"
    assert any(step["state"] == "waiting_review" for step in view["steps"])
    from multiagent.gui.token import ensure_token
    token = ensure_token(system.settings.gui_token_file)
    with TestClient(create_app(system), base_url="http://localhost") as client:
        response = client.post(f"/gui/api/runs/{run['id']}/approve", json={},
                               headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["waiting"] is None
