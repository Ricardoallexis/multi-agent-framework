"""G2 lifecycle tests; require Claude's resume helper and G2 fixture patch."""
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from multiagent.adapters.fake import FakeAdapter
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import (
    ExecutionMode, HumanStepSubmission, OUTPUT_SCHEMAS, PipelineMode, SocialPostRequest,
)
from multiagent.errors import BudgetExceeded, HumanSubmissionError, InvalidStateTransition
from multiagent.workflow_validation import WorkflowDefinitionError
from multiagent.workflows import WorkflowCatalog


FIXTURES = Path(__file__).parent / "fixtures" / "workflows"


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


def use_workflow(system, name):
    system.engine.workflows = WorkflowCatalog(system.settings, workflows_dir=FIXTURES / name)


def start(system, scenario="g2_intermediate", **overrides):
    use_workflow(system, scenario)
    request = SocialPostRequest(**{
        "project_name": "Synthetic checkpoint lifecycle", "objective": "Explain durable execution",
        "topic": "Checkpoint continuation", "use_brand_context": False,
        "pipeline_mode": PipelineMode.FULL, **overrides,
    })
    run = system.run_service.create_social_post(request)
    system.engine.process_run(run["id"])
    return system.store.get_run(run["id"])


def events(run, kind):
    return [event["payload"] for event in run["events"] if event["event_type"] == kind]


def submit(system, run):
    pending = system.store.pending_human_step(run["id"])
    # FakeAdapter constructs synthetic contract data; no external provider is used.
    response = FakeAdapter().generate_structured(
        spec=None, prompt="", output_schema=OUTPUT_SCHEMAS[pending["expected_contract"]],
        requires_web=pending["step_id"] == "research",
    )
    return system.run_service.human_submit(run["id"], HumanStepSubmission(
        raw_response=response.raw_text, provider="synthetic_human",
    ))


def test_intermediate_approval_advances_without_inference_and_preserves_prefix(system, monkeypatch):
    run = start(system)
    assert (run["waiting_step"], run["waiting_attempt"], run["current_step"]) == ("strategy", 1, 1)
    first_id = run["artifacts"][0]["id"]
    with monkeypatch.context() as patch:
        execute = Mock(side_effect=AssertionError("Approval must not invoke a provider"))
        patch.setattr(system.engine.router, "execute", execute)
        approved = system.run_service.approve(run["id"])
        execute.assert_not_called()
    assert approved["status"] == "queued"
    assert approved["current_step"] == 2
    assert approved["waiting_step"] == approved["waiting_reason"] == ""
    assert approved["waiting_attempt"] is None
    assert not events(approved, "workflow_completed")
    assert approved["llm_calls"] == run["llm_calls"]
    with pytest.raises(InvalidStateTransition):
        system.run_service.approve(run["id"])
    final_review = system.engine.process_run(run["id"])
    assert final_review["waiting_step"] == "design"
    assert [(a["step_id"], a["attempt"]) for a in final_review["artifacts"]] == [
        ("strategy", 1), ("create", 1), ("design", 1),
    ]
    assert final_review["artifacts"][0]["id"] == first_id
    assert final_review["artifacts"][0]["approved"] == 1
    completed = system.run_service.approve(run["id"])
    assert (completed["status"], completed["current_step"]) == ("completed", 4)
    assert len(events(completed, "workflow_completed")) == 1
    with pytest.raises(InvalidStateTransition):
        system.run_service.approve(run["id"])
    assert len(events(system.store.get_run(run["id"]), "workflow_completed")) == 1


def test_approval_targets_wait_identity_even_with_a_later_unrelated_artifact(system):
    run = start(system)
    unrelated = system.store.add_artifact(
        run_id=run["id"], step_id="unrelated", attempt=1, kind="ContentOutput",
        path="synthetic.json", data={},
    )
    approved = system.run_service.approve(run["id"])
    assert {a["id"]: a["approved"] for a in approved["artifacts"]} == {
        run["artifacts"][0]["id"]: 1, unrelated: 0,
    }
    assert events(approved, "human_approved")[0]["attempt"] == 1


@pytest.mark.parametrize("regenerate", [False, True])
def test_revision_retries_only_the_current_step_and_approves_the_new_attempt(system, regenerate):
    run = start(system, "g2_every_step")
    system.run_service.approve(run["id"])
    second = system.engine.process_run(run["id"])
    assert second["waiting_step"] == "create"
    queued = system.run_service.changes(run["id"], "Clarify the explanation", regenerate=regenerate)
    assert queued["current_step"] == 2
    assert queued["revision_feedback"] == ("" if regenerate else "Clarify the explanation")
    revised = system.engine.process_run(run["id"])
    assert (revised["waiting_step"], revised["waiting_attempt"]) == ("create", 2)
    assert system.store.latest_step_attempt(run["id"], "strategy") == 1
    approved = system.run_service.approve(run["id"])
    assert [(a["attempt"], a["approved"]) for a in approved["artifacts"] if a["step_id"] == "create"] == [(1, 0), (2, 1)]
    assert approved["revision_feedback"] == ""
    final = system.engine.process_run(run["id"])
    assert final["waiting_step"] == "design"


@pytest.mark.parametrize("requires_web", [False, True])
def test_conditional_checkpoint_is_skipped_or_reviews_the_human_research(system, requires_web):
    run = start(system, "g2_conditional_checkpoint", requires_web=requires_web)
    if requires_web:
        assert run["waiting_reason"] == "research"
        reviewed = submit(system, run)
        assert (reviewed["waiting_reason"], reviewed["current_step"]) == ("review", 0)
        system.run_service.approve(run["id"])
        run = system.engine.process_run(run["id"])
    assert run["waiting_step"] == "design"
    research = [a for a in run["artifacts"] if a["step_id"] == "research"]
    assert len(research) == int(requires_web)
    if research:
        assert research[0]["approved"] == 1


def test_omitted_tail_completes_without_repeating_the_accepted_step(system):
    run = start(system, "g2_skipped_tail")
    queued = system.run_service.approve(run["id"])
    assert queued["status"] == "queued"
    completed = system.engine.process_run(run["id"])
    assert (completed["status"], completed["current_step"]) == ("completed", 4)
    assert completed["llm_calls"] == run["llm_calls"]
    assert len(completed["artifacts"]) == 1
    assert len(events(completed, "workflow_completed")) == 1


def test_human_submission_with_and_without_checkpoint(system):
    run = start(system, execution_mode=ExecutionMode.HUMAN_GUIDED)
    reviewed = submit(system, run)
    assert (reviewed["waiting_reason"], reviewed["current_step"]) == ("review", 1)
    system.run_service.approve(run["id"])
    create = system.engine.process_run(run["id"])
    assert create["waiting_step"] == "create"
    queued = submit(system, create)
    assert (queued["status"], queued["current_step"]) == ("queued", 3)
    assert queued["llm_calls"] == 0
    assert system.store.pending_human_step(run["id"]) is None


@pytest.mark.parametrize("variant", ["step_renamed", "step_removed", "step_moved", "contract_changed"])
@pytest.mark.parametrize("action", ["approve", "changes", "human_submit"])
def test_incompatible_definition_preserves_the_entire_wait(system, monkeypatch, variant, action):
    overrides = {"execution_mode": ExecutionMode.HUMAN_GUIDED} if action == "human_submit" else {}
    run = start(system, **overrides)
    use_workflow(system, "g2_changed/" + variant)
    write_raw = Mock(side_effect=AssertionError("Resume validation must precede any raw write"))
    monkeypatch.setattr(system.engine.artifacts, "write_human_raw", write_raw)
    error_type = HumanSubmissionError if action == "human_submit" else InvalidStateTransition
    with pytest.raises(error_type) as caught:
        if action == "human_submit":
            submit(system, run)
        elif action == "changes":
            system.run_service.changes(run["id"], "Revise")
        else:
            system.run_service.approve(run["id"])
    assert isinstance(caught.value.__cause__, WorkflowDefinitionError)
    code = "resume_contract_mismatch" if variant == "contract_changed" else "resume_step_mismatch"
    assert code in {issue.code for issue in caught.value.__cause__.issues}
    assert system.store.get_run(run["id"]) == run
    write_raw.assert_not_called()


@pytest.mark.parametrize("variant", ["checkpoint_removed", "later_step_changed"])
def test_compatible_definition_keeps_the_persisted_review(system, variant):
    run = start(system)
    use_workflow(system, "g2_changed/" + variant)
    queued = system.run_service.approve(run["id"])
    assert (queued["status"], queued["current_step"]) == ("queued", 2)
    assert system.engine.process_run(run["id"])["waiting_step"] == "design"


@pytest.mark.parametrize("fields", [
    {"current_step": -1}, {"current_step": 99}, {"waiting_attempt": 0},
    {"waiting_attempt": 9}, {"waiting_attempt": None}, {"waiting_step": "missing"},
    {"cancel_requested": 1},
])
def test_corrupt_or_cancelled_wait_cannot_be_approved(system, fields):
    run = start(system)
    system.store.update_run(run["id"], **fields)
    before = system.store.get_run(run["id"])
    with pytest.raises(InvalidStateTransition):
        system.run_service.approve(run["id"])
    assert system.store.get_run(run["id"]) == before


@pytest.mark.parametrize("action", ["approve", "human_submit"])
def test_changed_prompt_is_rejected_before_persisting(system, tmp_path, action):
    run = start(system, **({"execution_mode": ExecutionMode.HUMAN_GUIDED} if action == "human_submit" else {}))
    raw = yaml.safe_load((FIXTURES / "g2_intermediate/social_post_full.yaml").read_text(encoding="utf-8"))
    raw["steps"][1].update(prompt_id="creator/social_post", prompt_version=2)
    folder = tmp_path / "changed"
    folder.mkdir()
    (folder / "social_post_full.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    system.engine.workflows = WorkflowCatalog(system.settings, workflows_dir=folder)
    with pytest.raises(HumanSubmissionError if action == "human_submit" else InvalidStateTransition):
        submit(system, run) if action == "human_submit" else system.run_service.approve(run["id"])
    assert system.store.get_run(run["id"]) == run


@pytest.mark.parametrize("event_type,terminal", [("human_approved", False), ("workflow_completed", True)])
def test_approval_transaction_rolls_back_artifact_cursor_and_events(system, event_type, terminal):
    run = start(system)
    if terminal:
        system.run_service.approve(run["id"])
        system.engine.process_run(run["id"])
        run = system.store.get_run(run["id"])
    with system.db.connect() as conn:
        conn.execute(f"""CREATE TRIGGER reject_review_event BEFORE INSERT ON run_events
                        WHEN NEW.event_type='{event_type}'
                        BEGIN SELECT RAISE(ABORT, 'synthetic event failure'); END""")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="synthetic event failure"):
        system.run_service.approve(run["id"])
    assert system.store.get_run(run["id"]) == run


def test_transaction_rejects_cancellation_after_reading_review(system):
    run = start(system)
    _, artifact = system.engine.review_point(run)
    cancelled = system.run_service.cancel(run["id"])
    with pytest.raises(InvalidStateTransition):
        system.store.finish_review(run, artifact, approve=True, current_step=2)
    assert system.store.get_run(run["id"]) == cancelled


def test_restart_preserves_review_and_queued_continuation(system):
    run = start(system)
    restarted = build_system(system.settings, dry_run=True)
    try:
        use_workflow(restarted, "g2_intermediate")
        assert restarted.store.get_run(run["id"])["waiting_step"] == "strategy"
        restarted.run_service.approve(run["id"])
        # Persisted cursor is used even by the original engine instance.
        assert system.engine.process_run(run["id"])["waiting_step"] == "design"
        assert system.store.latest_step_attempt(run["id"], "strategy") == 1
    finally:
        restarted.worker.stop()
        restarted.adapters["ollama"].client.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_budget_stop_approves_existing_result_without_resuming(system, legacy):
    run = start(system)
    system.run_service.approve(run["id"])
    # max_llm_calls is intentionally immutable through Store.update_run.
    with system.db.connect() as conn:
        conn.execute("UPDATE runs SET max_llm_calls=llm_calls WHERE id=?", (run["id"],))
        conn.commit()
    stopped = system.engine.process_run(run["id"])
    assert stopped["waiting_reason"] == "budget_exhausted"
    assert (stopped["waiting_step"], stopped["waiting_attempt"]) == ("strategy", 1)
    if legacy:
        system.store.update_run(run["id"], waiting_step="", waiting_attempt=None)
    with pytest.raises(BudgetExceeded):
        system.run_service.changes(run["id"], "Try again")
    completed = system.run_service.approve(run["id"])
    assert completed["status"] == "completed"
    assert completed["budget_exhausted"] == 1
    assert completed["llm_calls"] == run["llm_calls"]
    assert len(completed["artifacts"]) == 1


def test_legacy_terminal_review_requires_a_coherent_completed_artifact(system):
    run = start(system)
    system.run_service.approve(run["id"])
    system.engine.process_run(run["id"])
    system.store.update_run(run["id"], waiting_reason="", waiting_step="", waiting_attempt=None)
    completed = system.run_service.approve(run["id"])
    assert completed["status"] == "completed"
    assert completed["artifacts"][-1]["approved"] == 1


def test_missing_workflow_while_waiting_preserves_run(system, tmp_path):
    run = start(system)
    system.engine.workflows = WorkflowCatalog(system.settings, workflows_dir=tmp_path / "missing")
    with pytest.raises(InvalidStateTransition) as caught:
        system.run_service.approve(run["id"])
    assert isinstance(caught.value.__cause__, KeyError)
    assert system.store.get_run(run["id"]) == run


def test_newer_attempt_invalidates_an_old_review_identity(system):
    run = start(system)
    definition, artifact = system.engine.review_point(run)
    step = definition.steps[run["current_step"]]
    system.store.record_step(
        run_id=run["id"], step_id=step.id, attempt=2, status="completed",
        input_data={}, output_data={}, model_id="synthetic", prompt_id=step.prompt_id,
        prompt_version=step.prompt_version,
    )
    before = system.store.get_run(run["id"])
    with pytest.raises(InvalidStateTransition):
        system.run_service.approve(run["id"])
    # Also reject a stale caller that read before attempt 2 appeared.
    with pytest.raises(InvalidStateTransition):
        system.store.finish_review(run, artifact, approve=True, current_step=2)
    assert system.store.get_run(run["id"]) == before


@pytest.mark.parametrize("pipeline", [PipelineMode.QUICK, PipelineMode.FULL])
def test_bundled_terminal_workflows_still_complete(system, pipeline):
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="Synthetic regression", objective="Explain checkpoint behavior",
        topic="Durable workflows", pipeline_mode=pipeline, use_brand_context=False,
    ))
    reviewed = system.engine.process_run(run["id"])
    assert reviewed["waiting_reason"] == "review"
    approved = system.run_service.approve(run["id"])
    assert approved["status"] == "completed"
    assert approved["current_step"] == len(system.engine.preflight(run["workflow_id"]).steps)
    assert approved["artifacts"][-1]["approved"] == 1
