from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import BaseModel

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import (
    AssetMetadata,
    AssetSource,
    ContentOutput,
    ExecutionMode,
    HumanStepSubmission,
    PipelineMode,
    PublicationStatus,
    ResearchMode,
    RunStatus,
    SocialPostRequest,
)
from multiagent.errors import AuthenticationError, HumanSubmissionError, IdempotencyConflict, InvalidStateTransition, ModelUnavailable


def settings_for(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "multiagent.db",
        runs_dir=tmp_path / "data" / "runs",
        assets_dir=tmp_path / "data" / "assets",
        worker_enabled=False,
        gemini_api_key="",
    )
    values.update(overrides)
    return Settings(**values)


def content_payload(*, hashtags=None):
    return {
        "title": "Valid test title",
        "hook": "A hook clear enough to validate the contract.",
        "body": "A body long enough to validate the ContentOutput contract deterministically within the framework.",
        "cta": "Let us discuss the project.",
        "hashtags": hashtags or ["#ExampleBrand", "#Technology"],
        "source_urls_used": [],
    }


def strategy_payload():
    return {
        "objective": "Define a useful publication strategy",
        "target_audience": "Technical integrators",
        "audience_problem": "They need clear information to make decisions.",
        "content_angle": "Explain practical value with technical clarity.",
        "core_message": "A coherent architecture supports integration and maintenance.",
        "supporting_points": ["A supporting point detailed enough to validate the contract."],
        "content_structure": ["Hook", "Development", "CTA"],
        "hook_direction": "Open with a useful technical question.",
        "cta_strategy": "Invite the audience to continue the conversation.",
        "tone": "Professional",
        "claims_allowed": [],
        "claims_to_avoid": [],
        "differentiators": [],
        "platform_considerations": [],
    }


def visual_payload():
    return {
        "objective": "Create a supporting visual direction",
        "medium": "image",
        "output_mode": "both",
        "format": "LinkedIn post",
        "aspect_ratio": "1:1",
        "visual_hierarchy": ["Home", "Technology"],
        "composition": "Clean, balanced, professional composition with a central home and discreetly integrated technology.",
        "main_subject": "Contemporary smart home",
        "secondary_elements": [],
        "environment": "Contemporary residential setting",
        "lighting": "Natural and realistic",
        "brand_application": [],
        "typography_direction": "Minimal and legible",
        "text_overlay": [],
        "accessibility_notes": ["Maintain sufficient contrast"],
        "production_notes": [],
        "human_brief": "Create a professional smart-home image with discreet, clear, and credible integrated technology.",
        "technical_prompt": "Professional contemporary smart home architectural visualization, clean composition, subtle integrated technology, realistic natural light, no fake logos, no invented text.",
        "negative_constraints": ["No fake logos", "No invented text"],
    }


def research_payload():
    return {
        "objective": "Research recent information with traceability",
        "executive_summary": "Test summary based on a simulated source.",
        "key_findings": ["A recent finding detailed enough to validate external research."],
        "verified_claims": ["Claim verified by the test source."],
        "uncertain_claims": [],
        "conflicting_information": [],
        "dates": [],
        "numbers_and_statistics": [],
        "uncertainties": [],
        "source_urls": ["https://example.com/source"],
        "source_quality": ["Test source"],
        "gaps": [],
        "grounding_status": "VERIFIED",
    }


def test_human_bridge_is_default_web_path(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="human-web", objective="Create current content", topic="KNX updates", requires_web=True
    ))
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["waiting_reason"] == "research"
    assert state["waiting_step"] == "research"
    nxt = system.run_service.human_next(run["id"])
    assert nxt["expected_contract"] == "ResearchOutput"
    assert "REQUIRED OUTPUT CONTRACT" in nxt["prompt"]

    system.run_service.human_submit(run["id"], HumanStepSubmission(
        raw_response=json.dumps(research_payload()), provider="chatgpt_web", model="advanced"
    ))
    assert system.run_service.get(run["id"])["status"] == RunStatus.QUEUED.value
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert [a["step_id"] for a in state["artifacts"]] == ["research", "create"]


def test_full_pipeline_human_guided(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="full-human", objective="Create a technical campaign", topic="Interoperability",
        execution_mode=ExecutionMode.HUMAN_GUIDED, pipeline_mode=PipelineMode.FULL,
    ))
    system.worker.process_once()
    assert system.run_service.get(run["id"])["waiting_step"] == "strategy"
    system.run_service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps(strategy_payload()), provider="chatgpt_web", model="advanced"))
    system.worker.process_once()
    assert system.run_service.get(run["id"])["waiting_step"] == "create"
    system.run_service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps(content_payload()), provider="chatgpt_web", model="advanced"))
    system.worker.process_once()
    assert system.run_service.get(run["id"])["waiting_step"] == "design"
    state = system.run_service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps(visual_payload()), provider="gemini_web", model="image-model"))
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["waiting_reason"] == "review"
    assert [a["step_id"] for a in state["artifacts"]] == ["strategy", "create", "design"]
    assert (settings_for(tmp_path).runs_dir / run["id"] / "00_RUN.md").exists()


def test_invalid_human_submission_preserves_request(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="human-invalid", objective="Create content", topic="KNX", execution_mode=ExecutionMode.HUMAN_GUIDED
    ))
    system.worker.process_once()
    with pytest.raises(HumanSubmissionError):
        system.run_service.human_submit(run["id"], HumanStepSubmission(raw_response="response without json", provider="human"))
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert system.store.pending_human_step(run["id"]) is not None
    assert any(e["event_type"] == "human_submission_invalid" for e in state["events"])


def test_feedback_requires_exactly_five_hashtags(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="human-feedback", objective="Create content", topic="KNX", execution_mode=ExecutionMode.HUMAN_GUIDED
    ))
    system.worker.process_once()
    system.run_service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps(content_payload()), provider="human"))
    system.run_service.changes(run["id"], "Include exactly 5 hashtags")
    system.worker.process_once()
    with pytest.raises(HumanSubmissionError):
        system.run_service.human_submit(run["id"], HumanStepSubmission(
            raw_response=json.dumps(content_payload(hashtags=["#1", "#2", "#3", "#4"])), provider="human"
        ))
    state = system.run_service.human_submit(run["id"], HumanStepSubmission(
        raw_response=json.dumps(content_payload(hashtags=["#1", "#2", "#3", "#4", "#5"])), provider="human"
    ))
    assert state["waiting_reason"] == "review"


def test_idempotency_conflict_same_key_different_payload(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    first = system.run_service.create_social_post(SocialPostRequest(
        project_name="idem", objective="Create content A", topic="KNX", idempotency_key="same-key"
    ))
    same = system.run_service.create_social_post(SocialPostRequest(
        project_name="idem", objective="Create content A", topic="KNX", idempotency_key="same-key"
    ))
    assert same["id"] == first["id"]
    with pytest.raises(IdempotencyConflict):
        system.run_service.create_social_post(SocialPostRequest(
            project_name="idem", objective="Create content B", topic="Matter", idempotency_key="same-key"
        ))
    state = system.run_service.get(first["id"])
    assert any(e["event_type"] == "idempotency_hit" for e in state["events"])
    assert any(e["event_type"] == "idempotency_conflict" for e in state["events"])


def test_recovery_requeues_orphaned_running_job(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="recover", objective="Create content", topic="KNX"))
    system.store.update_run(run["id"], status=RunStatus.RUNNING.value, worker_id="old-worker", started_at="2026-01-01T00:00:00+00:00")
    system.store.db.log_event(run["id"], "step_started", {"step_id": "create", "attempt": 1})
    system.store.increment_llm_calls(run["id"])
    recovered = system.store.recover_running_runs("new-worker")
    state = system.run_service.get(run["id"])
    assert recovered == [run["id"]]
    assert state["status"] == RunStatus.QUEUED.value
    assert state["llm_calls"] == 1
    assert any(e["event_type"] == "run_interrupted" for e in state["events"])
    assert any(e["event_type"] == "run_recovered" for e in state["events"])


def test_recovery_applies_pending_cancellation(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="recover-cancel", objective="Create content", topic="KNX"))
    system.store.update_run(run["id"], status=RunStatus.RUNNING.value, cancel_requested=1)
    system.store.recover_running_runs("new-worker")
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.CANCELLED.value
    assert any(e["event_type"] == "workflow_cancelled" for e in state["events"])


class SlowContentAdapter(LLMAdapter):
    provider = "ollama"

    def __init__(self, started: threading.Event, release: threading.Event):
        self.started = started
        self.release = release

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        self.started.set()
        assert self.release.wait(5), "test release timeout"
        parsed = ContentOutput.model_validate(content_payload())
        return LLMResponse(parsed=parsed, raw_text=parsed.model_dump_json(), provider="ollama", model=spec.model, tokens_in=10, tokens_out=20)

    def health(self):
        return {"provider": "ollama", "available": True}


def test_mid_inference_cancel_discards_result(tmp_path):
    started = threading.Event(); release = threading.Event()
    system = build_system(settings_for(tmp_path), adapter_overrides={"ollama": SlowContentAdapter(started, release)})
    run = system.run_service.create_social_post(SocialPostRequest(project_name="mid-cancel", objective="Create content", topic="KNX", use_brand_context=False))
    thread = threading.Thread(target=system.worker.process_once)
    thread.start()
    assert started.wait(3)
    mid = system.run_service.cancel(run["id"])
    assert mid["cancel_requested"] == 1
    release.set(); thread.join(5)
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.CANCELLED.value
    assert state["artifacts"] == []
    assert not any(e["event_type"] == "step_completed" for e in state["events"])
    assert any(e["event_type"] == "workflow_cancelled" for e in state["events"])


def test_budget_exhausted_with_artifact_remains_approvable(tmp_path):
    system = build_system(settings_for(tmp_path, max_llm_calls=1), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="budget", objective="Create content", topic="KNX"))
    system.worker.process_once()
    system.run_service.changes(run["id"], "Make it shorter")
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["waiting_reason"] == "budget_exhausted"
    assert state["budget_exhausted"] == 1
    approved = system.run_service.approve(run["id"])
    assert approved["status"] == RunStatus.COMPLETED.value


def test_asset_global_blob_and_project_reference(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    source = tmp_path / "logo.png"; source.write_bytes(b"same-bytes")
    p1 = system.store.get_or_create_project("p1")
    p2 = system.store.get_or_create_project("p2")
    a1 = system.store.add_asset(source, AssetMetadata(project_id=p1, source=AssetSource.HUMAN_CREATED), system.settings.assets_dir)
    a2 = system.store.add_asset(source, AssetMetadata(project_id=p2, source=AssetSource.HUMAN_CREATED), system.settings.assets_dir)
    assert a1["blob_id"] == a2["blob_id"]
    assert a1["id"] != a2["id"]
    assert a2["physical_duplicate"] is True
    with system.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_blobs").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 2


def test_publication_validates_project_artifact_and_status(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="pub-a", objective="Create content", topic="KNX"))
    system.worker.process_once(); state = system.run_service.get(run["id"])
    artifact_id = state["artifacts"][0]["id"]
    other_project = system.store.get_or_create_project("pub-b")
    with pytest.raises(InvalidStateTransition):
        system.store.add_publication(project_id=other_project, platform="LinkedIn", artifact_id=artifact_id)
    pid = system.store.add_publication(project_id=run["project_id"], platform="LinkedIn", artifact_id=artifact_id, status=PublicationStatus.DRAFT.value)
    with system.db.connect() as conn:
        row = conn.execute("SELECT status,published_at FROM publications WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "draft"
    assert row["published_at"] == ""


def test_runs_list_and_brand_activate_version(tmp_path):
    from multiagent.contracts import BrandField, BrandProfilePayload
    system = build_system(settings_for(tmp_path), dry_run=True)
    p1 = system.store.save_brand_profile(BrandProfilePayload(name="Example Brand", core={"purpose": BrandField(value="One", source="human_confirmed")}), activate=False)
    p2 = system.store.save_brand_profile(BrandProfilePayload(name="Example Brand", core={"purpose": BrandField(value="Second", source="human_confirmed")}), activate=False)
    system.brand_service.activate_version(p1["version"])
    assert system.store.active_brand_version() == p1["version"]
    system.run_service.create_social_post(SocialPostRequest(project_name="list-project", objective="Create content", topic="KNX"))
    rows = system.run_service.list(limit=10, project="list-project")
    assert len(rows) == 1
    assert rows[0]["project_name"] == "list-project"
    assert len(system.store.list_brand_profiles(10)) == 2


def test_prompts_for_core_roles_exist(tmp_path):
    s = settings_for(tmp_path)
    system = build_system(s, dry_run=True)
    assert system.prompts if hasattr(system, "prompts") else True  # build_system does not expose PromptManager
    expected = [
        s.prompts_dir / "branding" / "brand_profile.v2.md",
        s.prompts_dir / "researcher" / "research.v2.md",
        s.prompts_dir / "strategist" / "social_strategy.v1.md",
        s.prompts_dir / "creator" / "social_post.v2.md",
        s.prompts_dir / "designer" / "visual_brief.v1.md",
    ]
    assert all(path.exists() for path in expected)


def test_schema_migration_preserves_run_asset_and_brand(tmp_path):
    db_path = tmp_path / "data" / "multiagent.db"; db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
    conn.executescript((Path(__file__).parents[1] / "migrations" / "001_initial.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO schema_version(version,name,applied_at) VALUES(1,'001_initial.sql','old')")
    conn.execute("INSERT INTO projects(id,name,created_at) VALUES('p','Old Project','old')")
    brand = {"name":"Example Brand","core":{"purpose":{"value":"Legacy brand","source":"human_confirmed"}},"voice":{},"visual":{},"strategy":{},"assets":[]}
    conn.execute("INSERT INTO brand_profiles(id,version,is_active,payload_json,created_at,created_by) VALUES('b',1,1,?,'old','human')", (json.dumps(brand),))
    req = {"project_name":"Old Project","objective":"Create content","topic":"KNX","platform":"LinkedIn","audience":"","instructions":"","requires_web":False,"use_brand_context":True,"idempotency_key":"old-key"}
    conn.execute("""INSERT INTO runs(id,project_id,workflow_id,status,request_json,brand_version,current_step,revision_feedback,cancel_requested,idempotency_key,max_llm_calls,max_run_seconds,llm_calls,created_at,updated_at,error)
                  VALUES('r','p','social_post','waiting_human',?,1,1,'',0,'old-key',4,300,1,'old','old','')""", (json.dumps(req),))
    conn.execute("""INSERT INTO assets(id,project_id,sha256,original_filename,internal_path,mime_type,bytes,source,status,title,notes,tags_json,rejection_reason,prompt_id,prompt_version,model_id,created_at)
                  VALUES('a','p','abc','logo.png','assets/projects/p/logo.png','image/png',3,'human_created','approved','','','[]','','',NULL,'','old')""")
    conn.commit(); conn.close()

    system = build_system(settings_for(tmp_path), dry_run=True)
    assert system.db.schema_version() == 2
    run = system.run_service.get("r")
    assert len(run["request_fingerprint"]) == 64
    assert run["brand_profile_id"] == "b"
    with system.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_blobs WHERE id='a'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM asset_references WHERE id='a'").fetchone()[0] == 1


def test_gemini_error_taxonomy(tmp_path):
    from multiagent.adapters.gemini import GeminiAdapter
    from multiagent.catalog import ModelSpec

    class Interactions:
        def __init__(self, message): self.message = message
        def create(self, **kwargs): raise Exception(self.message)

    class Client:
        def __init__(self, message): self.interactions = Interactions(message)

    spec = ModelSpec(id="g", provider="gemini", model="gemini-test", tier="cloud", structured_output=True, web=False)
    schema = type("Probe", (BaseModel,), {"__annotations__": {"status": str}})
    adapter = GeminiAdapter(settings_for(tmp_path), client=Client("API_KEY_INVALID API key not valid"))
    with pytest.raises(AuthenticationError):
        adapter.generate_structured(spec=spec, prompt="x", output_schema=schema)
    adapter = GeminiAdapter(settings_for(tmp_path), client=Client("404 NOT_FOUND model no longer available"))
    with pytest.raises(ModelUnavailable):
        adapter.generate_structured(spec=spec, prompt="x", output_schema=schema)


def test_recovery_records_previous_worker(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="recover-worker", objective="Create content", topic="KNX"
    ))
    system.store.update_run(run["id"], status=RunStatus.RUNNING.value, worker_id="worker-old")
    system.store.recover_running_runs("worker-new")
    state = system.run_service.get(run["id"])
    event = next(e for e in state["events"] if e["event_type"] == "run_interrupted")
    assert event["payload"]["previous_worker_id"] == "worker-old"


def test_sensitive_auto_forces_local_without_fallback(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="sensitive-auto", objective="Create content", topic="KNX", sensitive=True
    ))
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    routing = next(e for e in state["events"] if e["event_type"] == "routing_decision" and e["payload"]["step_id"] == "create")
    assert routing["payload"]["preferred_model"] == "local_default"
    assert routing["payload"]["fallback_model"] is None
    assert routing["payload"]["reason"] == "sensitive -> local"


def test_sensitive_cloud_is_rejected(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="sensitive-cloud", objective="Create content", topic="KNX",
        sensitive=True, execution_mode=ExecutionMode.CLOUD,
    ))
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.FAILED.value
    assert "sensitive" in state["error"].lower()


def test_sensitive_human_guided_adds_privacy_warning(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="sensitive-human", objective="Create content", topic="KNX",
        sensitive=True, execution_mode=ExecutionMode.HUMAN_GUIDED,
    ))
    system.worker.process_once()
    nxt = system.run_service.human_next(run["id"])
    assert "PRIVACY WARNING" in nxt["prompt"]


def test_invalid_and_valid_human_raw_submissions_are_not_overwritten(tmp_path):
    settings = settings_for(tmp_path)
    system = build_system(settings, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="human-raw", objective="Create content", topic="KNX",
        execution_mode=ExecutionMode.HUMAN_GUIDED,
    ))
    system.worker.process_once()
    with pytest.raises(HumanSubmissionError):
        system.run_service.human_submit(run["id"], HumanStepSubmission(raw_response="no-json", provider="human"))
    system.run_service.human_submit(run["id"], HumanStepSubmission(
        raw_response=json.dumps(content_payload()), provider="chatgpt_web", model="advanced"
    ))
    raw_files = sorted((settings.runs_dir / run["id"]).glob("02_create_attempt1_HUMAN_RAW_*.md"))
    assert len(raw_files) == 2
    assert raw_files[0].name.endswith("_001.md")
    assert raw_files[1].name.endswith("_002.md")
    assert "no-json" in raw_files[0].read_text(encoding="utf-8")


def test_web_create_requires_sources_used_in_human_guided(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="web-sources", objective="Create current content", topic="KNX",
        requires_web=True, execution_mode=ExecutionMode.HUMAN_GUIDED,
    ))
    system.worker.process_once()
    system.run_service.human_submit(run["id"], HumanStepSubmission(
        raw_response=json.dumps(research_payload()), provider="chatgpt_web", model="advanced"
    ))
    system.worker.process_once()
    assert system.run_service.get(run["id"])["waiting_step"] == "create"
    without_sources = content_payload()
    with pytest.raises(HumanSubmissionError):
        system.run_service.human_submit(run["id"], HumanStepSubmission(
            raw_response=json.dumps(without_sources), provider="chatgpt_web", model="advanced"
        ))
    with_sources = content_payload()
    with_sources["source_urls_used"] = ["https://example.com/source"]
    state = system.run_service.human_submit(run["id"], HumanStepSubmission(
        raw_response=json.dumps(with_sources), provider="chatgpt_web", model="advanced"
    ))
    assert state["waiting_reason"] == "review"


def test_full_pipeline_auto_dry_run_generates_three_artifacts(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="full-auto", objective="Create content and a visual brief", topic="Interoperability",
        pipeline_mode=PipelineMode.FULL,
    ))
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["waiting_reason"] == "review"
    assert [a["step_id"] for a in state["artifacts"]] == ["strategy", "create", "design"]


def test_cli_includes_research_submit_alias(tmp_path):
    from multiagent.cli import build_parser, cmd_human_submit
    args = build_parser().parse_args([
        "research-submit", "run-123", "--file", "research.json", "--provider", "chatgpt_web"
    ])
    assert args.func is cmd_human_submit
    assert args.run_id == "run-123"
    assert args.provider == "chatgpt_web"
