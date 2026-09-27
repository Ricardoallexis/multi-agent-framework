"""G3 runtime checks using Claude's technical bundle and the real validators."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from multiagent.adapters.fake import FakeAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts import ExecutionMode, HumanStepSubmission
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.errors import IdempotencyConflict
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.workflow_engine import WorkflowEngine
from multiagent.workflow_validation import WorkflowDefinitionError


BUNDLE = Path(__file__).parent / "fixtures/bundles/spec_review"


def runtime(tmp_path, bundle_root=BUNDLE):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(bundle_root, settings)
    engine = WorkflowEngine.from_bundle(
        bundle, store=store, settings=settings, artifacts=ArtifactWriter(settings),
        adapters={}, dry_run=True,
    )
    return SimpleNamespace(store=store, engine=engine, bundle=bundle,
                           service=RunService(settings, store, engine=engine))


def request(**overrides):
    return RunRequest(**{
        "project_name": "Synthetic software review", "workflow_id": "spec_review",
        "inputs": {"spec_text": "Users can reset a password by email; links expire after 30 minutes."},
        **overrides,
    })


def test_technical_bundle_runs_through_both_reviews_with_prior_outputs(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    prompts = []
    adapter = system.engine.router.adapters["fake"]
    generate = adapter.generate_structured

    def capture(**kwargs):
        prompts.append(kwargs["prompt"])
        return generate(**kwargs)

    monkeypatch.setattr(adapter, "generate_structured", capture)
    req = request()
    run = system.service.create_run(req)
    assert run["workflow_id"] == "spec_review"
    assert run["request"]["inputs"] == req.inputs
    assert run["pipeline_mode"] == "custom"
    review = system.engine.process_run(run["id"])
    assert (review["status"], review["waiting_step"]) == ("waiting_human", "assess_risks")
    assert [a["kind"] for a in review["artifacts"]] == ["RequirementsList", "RiskAssessment"]
    assert req.inputs["spec_text"] in prompts[0]
    assert system.service.approve(run["id"])["status"] == "queued"
    final_review = system.engine.process_run(run["id"])
    assert final_review["waiting_step"] == "draft_test_plan"
    completed = system.service.approve(run["id"])
    assert (completed["status"], completed["current_step"], completed["llm_calls"]) == ("completed", 3, 3)
    assert len(completed["artifacts"]) == 3
    with system.store.db.connect() as conn:
        saved = conn.execute("SELECT input_json FROM run_steps WHERE run_id=? AND step_id=?",
                             (run["id"], "draft_test_plan")).fetchone()
    context = json.loads(saved["input_json"])
    for step_id, contract in [("extract_requirements", "RequirementsList"), ("assess_risks", "RiskAssessment")]:
        value = context[step_id + "_output"]
        assert json.loads(value) == system.bundle.sample_outputs[contract]
        assert value in prompts[-1]
    assert "draft_test_plan_output" not in context
    assert context["previous_output"] == ""
    assert sum(e["event_type"] == "workflow_completed" for e in completed["events"]) == 1


def test_custom_contracts_are_used_for_human_submissions_too(tmp_path):
    system = runtime(tmp_path)
    run = system.service.create_run(request(execution_mode=ExecutionMode.HUMAN_GUIDED))
    for expected_step in ["extract_requirements", "assess_risks", "draft_test_plan"]:
        state = system.engine.process_run(run["id"])
        assert state["waiting_step"] == expected_step
        pending = system.store.pending_human_step(run["id"])
        state = system.service.human_submit(run["id"], HumanStepSubmission(
            raw_response=json.dumps(system.bundle.sample_outputs[pending["expected_contract"]]),
            provider="synthetic_human",
        ))
        if state["waiting_reason"] == "review":
            state = system.service.approve(run["id"])
    assert state["status"] == "completed"
    assert state["llm_calls"] == 0


def test_generic_revision_uses_previous_output_without_repeating_prefix(tmp_path):
    system = runtime(tmp_path)
    run = system.service.create_run(request())
    system.engine.process_run(run["id"])
    system.service.changes(run["id"], "Explain the highest risk")
    reviewed = system.engine.process_run(run["id"])
    assert reviewed["waiting_attempt"] == 2
    assert system.store.latest_step_attempt(run["id"], "extract_requirements") == 1
    with system.store.db.connect() as conn:
        saved = conn.execute("SELECT input_json FROM run_steps WHERE run_id=? AND step_id=? AND attempt=2",
                             (run["id"], "assess_risks")).fetchone()
    context = json.loads(saved["input_json"])
    assert context["revision_feedback"] == "Explain the highest risk"
    assert json.loads(context["previous_output"]) == system.bundle.sample_outputs["RiskAssessment"]


@pytest.mark.parametrize("field,value,code", [
    ("agent", "absent", "unknown_agent"), ("contract", "Absent", "unknown_contract"),
    ("prompt_id", "absent/prompt", "prompt_not_found"),
])
def test_bundle_reference_errors_prevent_run_creation(tmp_path, field, value, code):
    copied = tmp_path / "bundle"
    shutil.copytree(BUNDLE, copied)
    path = copied / "workflows/spec_review.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["steps"][0][field] = value
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    system = runtime(tmp_path, copied)
    with pytest.raises(WorkflowDefinitionError) as caught:
        system.service.create_run(request())
    assert code in {issue.code for issue in caught.value.issues}
    with system.store.db.connect() as conn:
        for table in ["runs", "projects", "run_steps", "artifacts"]:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_generic_idempotency_includes_workflow_and_inputs_and_survives_backfill(tmp_path):
    system = runtime(tmp_path)
    req = request(idempotency_key="synthetic-generic")
    run = system.service.create_run(req)
    assert system.service.create_run(req)["id"] == run["id"]
    for change in [{"workflow_id": "another_workflow"}, {"inputs": {"spec_text": "Different specification"}}]:
        with pytest.raises(IdempotencyConflict):
            system.service.create_run(request(idempotency_key=req.idempotency_key, **change))
    with system.store.db.connect() as conn:
        conn.execute("UPDATE runs SET request_fingerprint='' WHERE id=?", (run["id"],))
        conn.commit()
    system.store.backfill_orchestration_metadata()
    assert system.service.create_run(req)["id"] == run["id"]


@pytest.mark.parametrize("name", ["previous_output", "revision_feedback", "future_output", "nested.name"])
def test_inputs_cannot_shadow_runtime_context(name):
    with pytest.raises(ValidationError):
        request(inputs={name: "value"})


class ExampleOutput(BaseModel):
    value: int


def test_fake_samples_resolve_contract_aliases_and_validate_payloads():
    sample = {"Alias": {"value": 7}}
    adapter = FakeAdapter(sample_outputs=sample, output_schemas={"Alias": ExampleOutput})
    sample["Alias"]["value"] = 99
    result = adapter.generate_structured(spec=None, prompt="", output_schema=ExampleOutput)
    assert result.parsed.value == 7
    invalid = FakeAdapter(sample_outputs={"Alias": {"value": "invalid"}}, output_schemas={"Alias": ExampleOutput})
    with pytest.raises(ValidationError):
        invalid.generate_structured(spec=None, prompt="", output_schema=ExampleOutput)


def test_generic_runtime_has_no_technical_fixture_identifiers():
    root = Path(__file__).resolve().parents[1] / "multiagent"
    for relative in ["run_request.py", "run_service.py", "workflow_engine.py", "db/store.py", "adapters/fake.py"]:
        source = (root / relative).read_text(encoding="utf-8")
        for identifier in ["spec_review", "extract_requirements", "assess_risks", "draft_test_plan",
                           "RequirementsList", "RiskAssessment", "TestPlan"]:
            assert identifier not in source
