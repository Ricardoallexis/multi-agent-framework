"""Run lifecycle checks using the real workflow definition validator."""
import sqlite3
import threading
from unittest.mock import Mock

import pytest
import yaml

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import PipelineMode, SocialPostRequest
from multiagent.errors import IdempotencyConflict
from multiagent.run_service import RunService
from multiagent.workflow_validation import WorkflowDefinitionError
from multiagent.workflows import WorkflowCatalog


@pytest.fixture
def system(tmp_path):
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
        gemini_api_key="", openai_api_key="", openai_enabled=False,
    )
    result = build_system(settings, dry_run=True)
    yield result
    result.worker.stop()
    result.adapters["ollama"].client.close()


def request(**overrides):
    return SocialPostRequest(**{
        "project_name": "Synthetic preflight", "objective": "Explain lifecycle validation",
        "topic": "Workflow correctness", "use_brand_context": False,
        "pipeline_mode": PipelineMode.FULL, **overrides,
    })


def invalid_workflow(system, tmp_path, kind):
    raw = yaml.safe_load((system.settings.workflows_dir / "social_post_full.yaml").read_text(encoding="utf-8"))
    if kind == "unknown_agent":
        raw["steps"][-1]["agent"] = "missing_agent"
    elif kind == "unknown_contract":
        raw["steps"][-1]["contract"] = "MissingContract"
    elif kind == "prompt_not_found":
        raw["steps"][-1]["prompt_version"] = 999
    elif kind == "duplicate_step_id":
        raw["steps"][-1]["id"] = raw["steps"][1]["id"]
    elif kind == "unsupported_condition":
        raw["steps"][-1]["when"] = "sometimes"
    elif kind == "checkpoint_not_terminal":
        raw["steps"][1]["checkpoint_after"] = True
    definitions = tmp_path / "definitions"
    definitions.mkdir(exist_ok=True)
    if kind != "missing":
        text = "steps: [" if kind == "yaml_invalid" else yaml.safe_dump(raw)
        (definitions / "social_post_full.yaml").write_text(text, encoding="utf-8")
    system.engine.workflows = WorkflowCatalog(system.settings, workflows_dir=definitions)


INVALID_KINDS = ["missing", "yaml_invalid", "unknown_agent", "unknown_contract", "prompt_not_found",
                 "duplicate_step_id", "unsupported_condition", "checkpoint_not_terminal"]


@pytest.mark.parametrize("kind", INVALID_KINDS)
def test_invalid_definition_prevents_run_creation(system, tmp_path, monkeypatch, kind):
    invalid_workflow(system, tmp_path, kind)
    execute = Mock(side_effect=AssertionError("Provider execution must not start"))
    monkeypatch.setattr(system.engine.router, "execute", execute)
    with pytest.raises((KeyError, WorkflowDefinitionError)) as caught:
        system.run_service.create_social_post(request())
    if kind != "missing":
        assert kind in {issue.code for issue in caught.value.issues}
    with system.db.connect() as conn:
        for table in ("runs", "projects", "run_events", "run_steps", "artifacts", "human_step_requests"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    execute.assert_not_called()


@pytest.mark.parametrize("kind", INVALID_KINDS)
def test_queued_run_revalidates_before_any_step(system, tmp_path, monkeypatch, kind):
    run = system.run_service.create_social_post(request())
    invalid_workflow(system, tmp_path, kind)
    execute = Mock(side_effect=AssertionError("Provider execution must not start"))
    monkeypatch.setattr(system.engine.router, "execute", execute)
    assert system.worker.process_once()
    state = system.store.get_run(run["id"])
    assert state["status"] == "failed"
    assert state["llm_calls"] == 0
    assert state["artifacts"] == []
    assert state["human_step_request"] is None
    assert state["waiting_reason"] == state["waiting_step"] == ""
    assert state["waiting_attempt"] is None
    assert not any(e["event_type"] in {"run_started", "step_started"} for e in state["events"])
    failures = [e["payload"] for e in state["events"] if e["event_type"] == "workflow_failed"]
    assert len(failures) == 1
    if kind != "missing":
        assert kind in {issue["code"] for issue in failures[0]["issues"]}
        assert all(set(issue) == {"code", "location", "step_id", "message"} for issue in failures[0]["issues"])
    execute.assert_not_called()


@pytest.mark.parametrize("pipeline,count", [(PipelineMode.QUICK, 1), (PipelineMode.FULL, 3)])
def test_existing_workflows_still_reach_review(system, pipeline, count):
    run = system.run_service.create_social_post(request(pipeline_mode=pipeline))
    state = system.engine.process_run(run["id"])
    assert state["status"] == "waiting_human"
    assert state["waiting_reason"] == "review"
    assert len(state["artifacts"]) == count
    assert {e["payload"]["provider"] for e in state["events"] if e["event_type"] == "model_selected"} == {"fake"}


def test_idempotent_retry_does_not_revalidate_existing_run(system, tmp_path):
    req = request(idempotency_key="synthetic-retry")
    run = system.run_service.create_social_post(req)
    invalid_workflow(system, tmp_path, "unknown_agent")
    assert system.run_service.create_social_post(req)["id"] == run["id"]
    with pytest.raises(IdempotencyConflict):
        system.run_service.create_social_post(request(idempotency_key="synthetic-retry", topic="Other topic"))
    assert len(system.store.list_runs()) == 1


def test_new_run_requires_bound_engine(system):
    with pytest.raises(RuntimeError, match="no WorkflowEngine"):
        RunService(system.settings, system.store).create_social_post(request())
    assert system.store.list_runs() == []


@pytest.mark.parametrize("cursor", [-1, 99])
def test_invalid_cursor_fails_without_execution(system, monkeypatch, cursor):
    run = system.run_service.create_social_post(request())
    system.store.update_run(run["id"], current_step=cursor)
    execute = Mock()
    monkeypatch.setattr(system.engine.router, "execute", execute)
    assert system.engine.process_run(run["id"])["status"] == "failed"
    execute.assert_not_called()


def test_worker_continues_after_unhandled_error(system, monkeypatch):
    first = system.run_service.create_social_post(request())
    second = system.run_service.create_social_post(request(topic="Next run"))
    original = system.engine.process_run
    finished = threading.Event()

    def process(run_id):
        if run_id == first["id"]:
            system.store.update_run(run_id, status="running")
            raise RuntimeError("Synthetic engine failure")
        state = original(run_id)
        finished.set()
        return state

    monkeypatch.setattr(system.engine, "process_run", process)
    system.worker.start()
    try:
        assert finished.wait(timeout=10)
        assert system.store.get_run(first["id"])["status"] == "failed"
        assert system.store.get_run(second["id"])["status"] == "waiting_human"
        assert system.worker._thread.is_alive()
    finally:
        system.worker.stop()


@pytest.mark.parametrize("status", ["completed", "waiting_human", "cancelled", "rejected", "failed"])
def test_failure_fallback_preserves_settled_states(system, status):
    run = system.run_service.create_social_post(request())
    system.store.update_run(run["id"], status=status)
    assert not system.store.fail_active_run(run["id"], RuntimeError("Late error"))
    assert system.store.get_run(run["id"])["status"] == status


def test_failure_state_and_event_share_transaction(system):
    run = system.run_service.create_social_post(request())
    with system.db.connect() as conn:
        conn.execute("""CREATE TRIGGER reject_failure BEFORE INSERT ON run_events
                      WHEN NEW.event_type='workflow_failed'
                      BEGIN SELECT RAISE(ABORT, 'synthetic event failure'); END""")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="synthetic event failure"):
        system.store.fail_active_run(run["id"], RuntimeError("Synthetic error"))
    assert system.store.get_run(run["id"])["status"] == "queued"


def test_pending_cancellation_precedes_definition_validation(system, tmp_path):
    run = system.run_service.create_social_post(request())
    invalid_workflow(system, tmp_path, "unknown_agent")
    system.store.update_run(run["id"], cancel_requested=1)
    assert system.engine.process_run(run["id"])["status"] == "cancelled"
