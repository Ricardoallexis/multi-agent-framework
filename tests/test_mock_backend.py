# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic mock execution backend (BKD-T03, roadmap S4.A3)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
import yaml
from pydantic import Field, model_validator

from multiagent.adapters.fake import FakeAdapter
from multiagent.adapters.gemini import GeminiAdapter
from multiagent.adapters.ollama import OllamaAdapter
from multiagent.adapters.openai_cloud import OpenAIAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import (
    BackendContext,
    ExecutionBackend,
    available_backends,
    backend_problems,
    build_backend,
)
from multiagent.bundles import load_bundle
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.contracts import OUTPUT_SCHEMAS, ExecutionMode, HumanStepSubmission, SocialPostRequest, StrictModel
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.errors import HumanSubmissionError
from multiagent.model_router import ModelRouter
from multiagent.observability.usage import read_usage, summarize_usage
from multiagent.prompts import PromptManager
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.worker import LocalWorker
from multiagent.workflows import WorkflowCatalog


BUNDLE = Path(__file__).parent / "fixtures/bundles/spec_review"


def _make_runtime(
    tmp_path: Path,
    *,
    bundle_root: Path = BUNDLE,
    sample_outputs: dict | None = None,
    output_schemas: dict | None = None,
    max_llm_calls: int = 4,
    max_run_seconds: int = 300,
):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        max_llm_calls=max_llm_calls,
        max_run_seconds=max_run_seconds,
    )
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(bundle_root, settings)
    artifacts = ArtifactWriter(settings)
    context = BackendContext(
        store=store,
        settings=settings,
        workflows=bundle.workflows,
        prompts=bundle.prompts,
        catalog=bundle.catalog,
        artifacts=artifacts,
        output_schemas=bundle.output_schemas if output_schemas is None else output_schemas,
        sample_outputs=bundle.sample_outputs if sample_outputs is None else sample_outputs,
    )
    backend = build_backend("mock", context)
    service = RunService(settings, store, backend)
    worker = LocalWorker(store, backend)
    return SimpleNamespace(
        settings=settings,
        store=store,
        bundle=bundle,
        artifacts=artifacts,
        backend=backend,
        service=service,
        worker=worker,
    )


def _two_step_bundle(tmp_path: Path, *, checkpoint_last: bool = False) -> Path:
    copied = tmp_path / "bundle_two_step"
    shutil.copytree(BUNDLE, copied)
    wf_path = copied / "workflows/spec_review.yaml"
    raw = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    raw["steps"] = raw["steps"][:2]
    raw["steps"][0]["checkpoint_after"] = False
    raw["steps"][1]["checkpoint_after"] = checkpoint_last
    wf_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return copied


def _request(**overrides) -> RunRequest:
    return RunRequest(**{
        "project_name": "Mock backend test project",
        "workflow_id": "spec_review",
        "inputs": {"spec_text": "Users can reset a password by email; links expire after 30 minutes."},
        **overrides,
    })


def test_mock_backend_meets_contract(tmp_path):
    rt = _make_runtime(tmp_path)
    assert "mock" in available_backends()
    assert backend_problems(rt.backend, name="mock") == []
    assert isinstance(rt.backend, ExecutionBackend)
    assert rt.backend.name == "mock"
    assert rt.backend.capabilities == frozenset({
        "usage", "budgets", "cancellation", "human_steps", "review", "recovery",
    })
    assert rt.backend.check_approval_deadlines() == 0


def test_two_step_run_completes_with_required_events_and_zero_token_usage(tmp_path):
    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path))
    run = rt.service.create_run(_request())

    assert rt.worker.process_once() is True
    finished = rt.store.get_run(run["id"])
    assert finished["status"] == "completed"
    assert finished["current_step"] == 2
    assert finished["llm_calls"] == 2
    assert finished["successful_llm_calls"] == 2
    assert [a["kind"] for a in finished["artifacts"]] == ["RequirementsList", "RiskAssessment"]

    event_types = [e["event_type"] for e in finished["events"]]
    expected_sequence = [
        "run_started",
        "step_started",
        "step_completed",
        "step_started",
        "step_completed",
        "workflow_completed",
    ]
    positions = []
    cursor = 0
    for expected in expected_sequence:
        idx = event_types.index(expected, cursor)
        positions.append(idx)
        cursor = idx + 1
    assert positions == sorted(positions)

    usage_rows = read_usage(rt.store, run["id"])
    assert len(usage_rows) == 2
    assert [row["step_id"] for row in usage_rows] == ["extract_requirements", "assess_risks"]
    for row in usage_rows:
        assert row["provider"] == "mock"
        assert row["model"] == "mock"
        assert row["tokens_in"] == 0
        assert row["tokens_out"] == 0
        assert row["tokens_total"] == 0
        assert row["estimated_cost_usd"] == 0.0
        assert row["success"] is True

    summary = summarize_usage(usage_rows)["run"]
    assert summary["records"] == 2
    assert summary["tokens_total"] == 0
    assert summary["estimated_cost_usd"] == 0.0
    assert summary["cost_complete"] is True


def test_synthesizes_minimal_valid_outputs_when_sample_outputs_absent(tmp_path):
    from multiagent.backends.mock import _synthesize_model

    for name, schema in OUTPUT_SCHEMAS.items():
        schema.model_validate(_synthesize_model(schema))

    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path), sample_outputs={})
    run = rt.service.create_run(_request())

    assert rt.worker.process_once() is True
    finished = rt.store.get_run(run["id"])
    assert finished["status"] == "completed"
    assert len(finished["artifacts"]) == 2
    for artifact in finished["artifacts"]:
        schema = rt.bundle.output_schemas[artifact["kind"]]
        schema.model_validate(artifact["data"])


def test_unbuildable_schema_fails_run_with_clear_message_without_raising(tmp_path):
    class ImpossibleContract(StrictModel):
        score: int = Field(gt=10, lt=5)

    class ValidatorRejectContract(StrictModel):
        label: str

        @model_validator(mode="after")
        def reject(self):
            raise ValueError("domain invariant cannot be guessed")

    for bad_model in (ImpossibleContract, ValidatorRejectContract):
        sub_dir = tmp_path / bad_model.__name__
        bundle_dir = _two_step_bundle(sub_dir)
        rt = _make_runtime(
            sub_dir,
            bundle_root=bundle_dir,
            sample_outputs={},
            output_schemas={**load_bundle(bundle_dir, Settings(_env_file=None, local_dir=sub_dir / "l")).output_schemas,
                            "RequirementsList": bad_model},
        )
        run = rt.service.create_run(_request())
        result = rt.backend.process_run(run["id"])
        assert result["status"] == "failed"
        assert "RequirementsList" in result["error"]
        assert any(e["event_type"] == "workflow_failed" for e in result["events"])


def test_when_condition_skips_step_with_step_skipped_event(tmp_path):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    backend = build_backend(
        "mock",
        BackendContext(
            store=store,
            settings=settings,
            workflows=WorkflowCatalog(settings),
            prompts=PromptManager(settings),
            catalog=Catalog(settings),
            artifacts=ArtifactWriter(settings),
        ),
    )
    service = RunService(settings, store, backend)
    worker = LocalWorker(store, backend)

    run = service.create_run(SocialPostRequest(
        project_name="Social post mock",
        topic="Automated backups",
        platform="LinkedIn",
        audience="Engineering leads",
        instructions="Keep it concise",
        objective="Inform users",
        requires_web=False,
    ))
    assert worker.process_once() is True
    paused = store.get_run(run["id"])
    assert paused["status"] == "waiting_human"
    assert paused["waiting_reason"] == "review"
    assert paused["waiting_step"] == "create"
    assert any(
        e["event_type"] == "step_skipped" and e["payload"].get("step_id") == "research"
        for e in paused["events"]
    )
    completed = service.approve(run["id"])
    assert completed["status"] == "completed"


def test_human_step_invalid_fails_and_valid_continues(tmp_path):
    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path, checkpoint_last=True))
    run = rt.service.create_run(_request(step_modes={"extract_requirements": ExecutionMode.HUMAN_GUIDED}))

    assert rt.worker.process_once() is True
    waiting = rt.store.get_run(run["id"])
    assert waiting["status"] == "waiting_human"
    assert waiting["waiting_reason"] == "execute_step"
    assert waiting["waiting_step"] == "extract_requirements"
    assert waiting["llm_calls"] == 0
    assert any(e["event_type"] == "human_step_requested" for e in waiting["events"])
    assert "Users can reset a password by email" in rt.service.human_next(run["id"])["prompt"]

    with pytest.raises(HumanSubmissionError):
        rt.service.human_submit(run["id"], HumanStepSubmission(raw_response="not json at all"))

    with pytest.raises(HumanSubmissionError):
        rt.service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps({"requirements": []})))

    after_invalid = rt.store.get_run(run["id"])
    assert after_invalid["status"] == "waiting_human"
    assert sum(e["event_type"] == "human_submission_invalid" for e in after_invalid["events"]) == 2

    valid_json = json.dumps(rt.bundle.sample_outputs["RequirementsList"])
    submitted = rt.service.human_submit(
        run["id"],
        HumanStepSubmission(raw_response=f"```json\n{valid_json}\n```", provider="external_human", model="notes"),
    )
    assert submitted["status"] == "queued"
    assert submitted["current_step"] == 1
    assert any(e["event_type"] == "human_step_submitted" for e in submitted["events"])

    assert rt.worker.process_once() is True
    at_review = rt.store.get_run(run["id"])
    assert at_review["status"] == "waiting_human"
    assert at_review["waiting_reason"] == "review"
    assert at_review["waiting_step"] == "assess_risks"
    assert at_review["llm_calls"] == 1

    done = rt.service.approve(run["id"])
    assert done["status"] == "completed"

    # A human_guided step with checkpoint_after=True pauses directly at review after submission.
    run_cp = rt.service.create_run(_request(execution_mode=ExecutionMode.HUMAN_GUIDED))
    assert rt.worker.process_once() is True
    rt.service.human_submit(run_cp["id"], HumanStepSubmission(raw_response=valid_json))
    assert rt.worker.process_once() is True
    risk_json = json.dumps(rt.bundle.sample_outputs["RiskAssessment"])
    at_cp = rt.service.human_submit(run_cp["id"], HumanStepSubmission(raw_response=risk_json))
    assert (at_cp["status"], at_cp["waiting_reason"], at_cp["waiting_step"]) == ("waiting_human", "review", "assess_risks")
    assert any(e["event_type"] == "checkpoint" and e["payload"]["step_id"] == "assess_risks" for e in at_cp["events"])
    assert rt.service.approve(run_cp["id"])["status"] == "completed"


def test_checkpoint_approve_and_changes_produce_attempt_2_with_revision_feedback(tmp_path):
    rt = _make_runtime(tmp_path)
    run = rt.service.create_run(_request())

    assert rt.worker.process_once() is True
    review = rt.store.get_run(run["id"])
    assert (review["status"], review["waiting_reason"], review["waiting_step"], review["waiting_attempt"]) == (
        "waiting_human", "review", "assess_risks", 1,
    )
    assert any(e["event_type"] == "checkpoint" and e["payload"]["step_id"] == "assess_risks" for e in review["events"])

    revised = rt.service.changes(run["id"], "Explain the highest risk")
    assert revised["status"] == "queued"
    assert revised["current_step"] == 1
    assert revised["revision_feedback"] == "Explain the highest risk"

    assert rt.worker.process_once() is True
    review_2 = rt.store.get_run(run["id"])
    assert (review_2["status"], review_2["waiting_step"], review_2["waiting_attempt"]) == (
        "waiting_human", "assess_risks", 2,
    )
    assert rt.store.latest_step_attempt(run["id"], "extract_requirements") == 1
    assert rt.store.latest_step_attempt(run["id"], "assess_risks") == 2

    with rt.store.db.connect() as conn:
        row = conn.execute(
            "SELECT input_json FROM run_steps WHERE run_id=? AND step_id=? AND attempt=2",
            (run["id"], "assess_risks"),
        ).fetchone()
    assert row is not None
    input_data = json.loads(row["input_json"])
    assert input_data["revision_feedback"] == "Explain the highest risk"
    assert json.loads(input_data["previous_output"]) == rt.bundle.sample_outputs["RiskAssessment"]

    queued_next = rt.service.approve(run["id"])
    assert queued_next["status"] == "queued"
    assert queued_next["current_step"] == 2

    assert rt.worker.process_once() is True
    final_review = rt.store.get_run(run["id"])
    assert (final_review["status"], final_review["waiting_step"]) == ("waiting_human", "draft_test_plan")

    completed = rt.service.approve(run["id"])
    assert completed["status"] == "completed"
    assert completed["current_step"] == 3


def test_max_llm_calls_stops_with_budget_exhausted(tmp_path):
    rt = _make_runtime(tmp_path, max_llm_calls=1)
    run = rt.service.create_run(_request())

    assert rt.worker.process_once() is True
    stopped = rt.store.get_run(run["id"])
    assert stopped["status"] == "waiting_human"
    assert stopped["waiting_reason"] == "budget_exhausted"
    assert stopped["budget_exhausted"] == 1
    assert stopped["llm_calls"] == 1
    assert stopped["waiting_step"] == "extract_requirements"
    assert stopped["waiting_attempt"] == 1
    assert any(e["event_type"] == "budget_exhausted" for e in stopped["events"])

    completed = rt.service.approve(run["id"])
    assert completed["status"] == "completed"

    # Without any prior artifact, budget exhaustion fails the run.
    rt_zero = _make_runtime(tmp_path / "zero", max_llm_calls=1)
    zero_run = rt_zero.service.create_run(_request())
    rt_zero.store.update_run(zero_run["id"], llm_calls=1)
    assert rt_zero.worker.process_once() is True
    failed = rt_zero.store.get_run(zero_run["id"])
    assert failed["status"] == "failed"
    assert failed["budget_exhausted"] == 1
    assert any(e["event_type"] == "workflow_failed" for e in failed["events"])

    # Exceeding max_run_seconds stops with BudgetExceeded as well.
    rt_time = _make_runtime(tmp_path / "time", max_run_seconds=30)
    time_run = rt_time.service.create_run(_request())
    rt_time.store.update_run(time_run["id"], active_seconds=31.0)
    assert rt_time.worker.process_once() is True
    timed_out = rt_time.store.get_run(time_run["id"])
    assert timed_out["status"] == "failed"
    assert timed_out["budget_exhausted"] == 1
    assert "max_run_seconds" in timed_out["error"]


def test_cancel_while_queued_and_between_steps(tmp_path):
    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path))

    # Cancel while queued.
    queued_run = rt.service.create_run(_request())
    cancelled = rt.service.cancel(queued_run["id"])
    assert cancelled["status"] == "cancelled"
    assert any(e["event_type"] == "workflow_cancelled" for e in cancelled["events"])
    assert rt.worker.process_once() is False
    assert rt.backend.process_run(queued_run["id"])["status"] == "cancelled"

    # Cancel between steps (set cancel_requested after step 0 completes, before step 1 begins).
    running_run = rt.service.create_run(_request())

    def cancel_after_first_step(run_id: str, event_type: str, payload: dict, _event_id: int | None) -> None:
        if run_id == running_run["id"] and event_type == "step_completed" and payload.get("step_id") == "extract_requirements":
            rt.service.cancel(run_id)

    rt.store.db.event_listeners.append(cancel_after_first_step)
    assert rt.worker.process_once() is True
    mid_cancelled = rt.store.get_run(running_run["id"])
    assert mid_cancelled["status"] == "cancelled"
    assert mid_cancelled["llm_calls"] == 1
    assert len(mid_cancelled["artifacts"]) == 1
    assert any(e["event_type"] == "workflow_cancelled" for e in mid_cancelled["events"])


def test_recovered_run_continues_from_current_step(tmp_path):
    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path))
    run = rt.service.create_run(_request())

    # Simulate step 0 having completed before an unexpected worker crash during step 1.
    rt.store.increment_llm_calls(run["id"])
    rt.store.record_step(
        run_id=run["id"],
        step_id="extract_requirements",
        attempt=1,
        status="completed",
        input_data={"spec_text": "partial"},
        output_data=rt.bundle.sample_outputs["RequirementsList"],
        model_id="mock",
        prompt_id="spec/extract_requirements",
        prompt_version=1,
    )
    path = rt.artifacts.write(
        run_id=run["id"],
        step_index=0,
        step_id="extract_requirements",
        attempt=1,
        data=rt.bundle.sample_outputs["RequirementsList"],
    )
    rt.store.add_artifact(
        run_id=run["id"],
        step_id="extract_requirements",
        attempt=1,
        kind="RequirementsList",
        path=str(path.relative_to(rt.settings.data_dir)),
        data=rt.bundle.sample_outputs["RequirementsList"],
    )
    rt.store.update_run(run["id"], status="running", current_step=1, worker_id="dead-worker")

    recovered = rt.store.recover_running_runs(rt.worker.worker_id)
    assert recovered == [run["id"]]
    assert rt.store.get_run(run["id"])["status"] == "queued"

    assert rt.worker.process_once() is True
    finished = rt.store.get_run(run["id"])
    assert finished["status"] == "completed"
    assert finished["current_step"] == 2
    assert finished["llm_calls"] == 2
    assert rt.store.latest_step_attempt(run["id"], "extract_requirements") == 1
    assert rt.store.latest_step_attempt(run["id"], "assess_risks") == 1
    assert any(e["event_type"] == "run_recovered" for e in finished["events"])


def test_importing_mock_backend_does_not_load_workflow_engine():
    code = (
        "import sys, multiagent.backends.mock; "
        "print(','.join(m for m in sys.modules if m == 'multiagent.workflow_engine' or m.startswith('multiagent.workflow_engine.')))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""


def test_full_run_never_calls_any_adapter_or_router(tmp_path, monkeypatch):
    def fail_call(*args, **kwargs):
        raise AssertionError("MockBackend must never invoke adapters or the model router")

    for cls in (FakeAdapter, OllamaAdapter, GeminiAdapter, OpenAIAdapter):
        monkeypatch.setattr(cls, "generate_structured", fail_call)
    monkeypatch.setattr(ModelRouter, "select", fail_call)
    monkeypatch.setattr(ModelRouter, "execute", fail_call)

    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path))
    run = rt.service.create_run(_request())
    assert rt.worker.process_once() is True
    assert rt.store.get_run(run["id"])["status"] == "completed"


def test_process_run_non_queued_returns_unchanged_and_bad_workflow_fails_safely(tmp_path):
    rt = _make_runtime(tmp_path, bundle_root=_two_step_bundle(tmp_path))
    run = rt.service.create_run(_request())
    assert rt.worker.process_once() is True

    before_events = len(rt.store.get_run(run["id"])["events"])
    again = rt.backend.process_run(run["id"])
    assert again["status"] == "completed"
    assert len(again["events"]) == before_events

    bad_run = rt.service.create_run(_request())
    (rt.bundle.workflows.workflows_dir / "spec_review.yaml").unlink()
    failed = rt.backend.process_run(bad_run["id"])
    assert failed["status"] == "failed"
    assert any(e["event_type"] == "workflow_failed" for e in failed["events"])
