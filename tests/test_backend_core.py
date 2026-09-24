from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.adapters.fake import FakeAdapter
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import (
    AssetMetadata, AssetSource, AssetStatus, BrandField, BrandProfilePayload,
    ContentOutput, ResearchMode, RunStatus, SocialPostRequest, VisualHumanBriefOutput,
    VisualTechnicalPromptOutput,
)
from multiagent.errors import ContextBudgetError, ProviderUnavailable
from multiagent.schema_utils import flatten_json_schema
from multiagent.api import create_app


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "data" / "multiagent.db",
        runs_dir=tmp_path / "data" / "runs",
        assets_dir=tmp_path / "data" / "assets",
        worker_enabled=False,
        gemini_api_key="",
    )


def test_migrations_and_wal(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    assert system.db.schema_version() == 2
    with system.db.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_database_connect_closes_handle(tmp_path):
    """Regression test for WinError 32 while cleaning temporary databases on Windows."""
    import sqlite3

    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    with system.db.connect() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_versioned_prompt_exists(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    p = system.engine.prompts.load("creator/social_post", 1)
    assert p.version == 1
    assert "Creator" in p.text


def test_visual_technical_schema_does_not_expose_human_brief():
    props = VisualTechnicalPromptOutput.model_json_schema()["properties"]
    assert "technical_prompt" in props
    assert "human_brief" not in props


def test_visual_human_schema_does_not_expose_technical_prompt():
    props = VisualHumanBriefOutput.model_json_schema()["properties"]
    assert "human_brief" in props
    assert "technical_prompt" not in props


def test_flatten_schema_removes_defs():
    schema = BrandProfilePayload.model_json_schema()
    flat = flatten_json_schema(schema)
    raw = json.dumps(flat)
    assert "$defs" not in raw
    assert "#/$defs/" not in raw


def test_social_post_dry_run_reaches_checkpoint(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="p-demo", objective="Create an educational post", topic="KNX"))
    assert system.worker.process_once() is True
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert len(state["artifacts"]) == 1
    assert (s.data_dir / state["artifacts"][0]["path"]).exists()


def test_research_runs_only_when_web_is_required(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="p-web", objective="Create a post with current information", topic="Wi-Fi", requires_web=True, research_mode=ResearchMode.GEMINI_GROUNDED))
    system.worker.process_once(); state = system.run_service.get(run["id"])
    kinds = [a["kind"] for a in state["artifacts"]]
    assert kinds == ["ResearchOutput", "ContentOutput"]


def test_changes_repeat_only_create_step(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="p-changes", objective="Create an educational post", topic="KNX", requires_web=True, research_mode=ResearchMode.GEMINI_GROUNDED))
    system.worker.process_once()
    system.run_service.changes(run["id"], "Make it shorter")
    system.worker.process_once()
    with system.db.connect() as conn:
        research_attempts = conn.execute("SELECT COUNT(*) FROM run_steps WHERE run_id=? AND step_id='research'", (run["id"],)).fetchone()[0]
        create_attempts = conn.execute("SELECT COUNT(*) FROM run_steps WHERE run_id=? AND step_id='create'", (run["id"],)).fetchone()[0]
    assert research_attempts == 1
    assert create_attempts == 2


def test_max_revision_iterations(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="p-loop", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    for text in ["change 1", "change 2"]:
        system.run_service.changes(run["id"], text); system.worker.process_once()
    # A third revision request would trigger attempt=4 > max_iterations+1.
    system.run_service.changes(run["id"], "change 3"); system.worker.process_once()
    assert system.run_service.get(run["id"])["status"] == RunStatus.WAITING_HUMAN.value
    assert system.run_service.get(run["id"])["budget_exhausted"] == 1


def test_approve_marks_artifact(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="p-app", objective="Create an educational post", topic="KNX"))
    system.worker.process_once(); state = system.run_service.approve(run["id"])
    assert state["status"] == RunStatus.COMPLETED.value
    assert state["artifacts"][-1]["approved"] == 1


def test_idempotency_key_does_not_duplicate_run(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    req = SocialPostRequest(project_name="p-idem", objective="Create an educational post", topic="KNX", idempotency_key="abc")
    a = system.run_service.create_social_post(req); b = system.run_service.create_social_post(req)
    assert a["id"] == b["id"]


def test_brand_context_uses_only_confirmed_values(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    profile = BrandProfilePayload(
        name="Example Brand",
        core={"purpose": BrandField(value="Technology solutions", source="human_confirmed"), "claim": BrandField(value="We are the best", source="ai_proposed")},
        voice={"tone": BrandField(value="Professional", source="human_confirmed")},
    )
    system.store.save_brand_profile(profile, activate=True)
    ctx = system.store.brand_context(["core", "voice"])
    assert "Technology solutions" in ctx
    assert "We are the best" not in ctx


def test_asset_ingest_sha256_and_duplicate(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    project_id = system.store.get_or_create_project("assets")
    f = tmp_path / "IMG_1.png"; f.write_bytes(b"demo-image")
    meta = AssetMetadata(project_id=project_id, source=AssetSource.HUMAN_CREATED, status=AssetStatus.APPROVED)
    first = system.store.add_asset(f, meta, s.assets_dir); second = system.store.add_asset(f, meta, s.assets_dir)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(first["sha256"]) == 64


def test_api_create_get_approve(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    app = create_app(system)
    with TestClient(app) as client:
        r = client.post("/api/v1/runs", json={"project_name":"api-demo","objective":"Create an educational post","topic":"KNX"})
        assert r.status_code == 202
        run_id = r.json()["id"]
        system.worker.process_once()
        assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "waiting_human"
        assert client.post(f"/api/v1/runs/{run_id}/approve").json()["status"] == "completed"


def test_telemetry_records_prompt_version(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="tele", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    with system.db.connect() as conn:
        row = conn.execute("SELECT prompt_id,prompt_version,tokens_in,tokens_out FROM telemetry WHERE run_id=?", (run["id"],)).fetchone()
    assert row["prompt_id"] == "creator/social_post"
    assert row["prompt_version"] == 2
    assert row["tokens_in"] > 0


def test_cancel_queued(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="cancel", objective="Create an educational post", topic="KNX"))
    state = system.run_service.cancel(run["id"])
    assert state["status"] == RunStatus.CANCELLED.value

class AlwaysUnavailable(LLMAdapter):
    provider = "ollama"
    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        raise ProviderUnavailable("offline")
    def health(self):
        return {"provider":"ollama","available":False}


def test_local_to_gemini_fallback_counts_two_calls(tmp_path):
    s = settings_for(tmp_path)
    system = build_system(s, adapter_overrides={"ollama": AlwaysUnavailable(), "gemini": FakeAdapter()})
    run = system.run_service.create_social_post(SocialPostRequest(project_name="fallback", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert state["llm_calls"] == 2
    event = [e for e in state["events"] if e["event_type"] == "model_selected"][-1]
    payload = event["payload"]
    assert len(payload["attempts"]) == 2
    assert payload["attempts"][0]["ok"] is False
    assert payload["attempts"][1]["ok"] is True
    with system.db.connect() as conn:
        rows = conn.execute(
            "SELECT success,error_type FROM telemetry WHERE run_id=? AND step_id='create' ORDER BY id",
            (run["id"],),
        ).fetchall()
    assert [row["success"] for row in rows] == [0, 1]
    assert rows[0]["error_type"] == "ProviderUnavailable"


def test_create_does_not_require_web_even_when_research_does(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    definition = system.engine.workflows.load("social_post")
    assert definition.steps[0].requires_web is True
    assert definition.steps[1].requires_web is False


def test_reject_and_regenerate_are_distinct_actions(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(project_name="regen", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    regenerated = system.run_service.changes(run["id"], "", regenerate=True)
    assert regenerated["status"] == RunStatus.QUEUED.value
    system.worker.process_once()
    rejected = system.run_service.reject(run["id"], "Do not publish")
    assert rejected["status"] == RunStatus.REJECTED.value


def test_brand_generate_stays_inactive_and_filters_proposals(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    result = system.brand_service.generate_from_answers("Example Brand provides technology solutions for customers looking for automation. " * 3, model_id="gemini_fast")
    assert result["is_active"] is False
    system.brand_service.activate(result["id"])
    ctx = system.store.brand_context(["core", "voice"])
    assert "Turn technology" in ctx
    assert "Professional and approachable" not in ctx


def test_ollama_payload_structured_numctx_keepalive_and_tokens(tmp_path):
    from multiagent.adapters.ollama import OllamaAdapter
    from multiagent.catalog import ModelSpec

    captured = {}
    def handler(request: httpx.Request):
        if request.url.path == "/api/chat":
            captured.update(json.loads(request.content.decode()))
            return httpx.Response(200, json={
                "model":"qwen3:8b",
                "message":{"role":"assistant","content":json.dumps({
                    "title":"T","hook":"A hook long enough for contract validation",
                    "body":"A body long enough to validate the structured contract correctly.",
                    "cta":"CTA","hashtags":[],"source_urls_used":[]
                })},
                "prompt_eval_count":123,"eval_count":45,"load_duration":1,"prompt_eval_duration":2,"eval_duration":3
            })
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models":[{"name":"qwen3:8b","digest":"abc123"}]})
        return httpx.Response(404)

    s = settings_for(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    adapter = OllamaAdapter(s, client=client)
    spec = ModelSpec(id="local",provider="ollama",model="qwen3:8b",tier="local",structured_output=True,web=False,num_ctx=4096,keep_alive="15m")
    response = adapter.generate_structured(spec=spec,prompt="Write a short post",output_schema=ContentOutput)
    assert captured["stream"] is False
    assert captured["think"] is False
    assert captured["options"]["num_ctx"] == 4096
    assert captured["keep_alive"] == "15m"
    assert captured["format"]["type"] == "object"
    assert response.tokens_in == 123 and response.tokens_out == 45
    assert response.model_digest == "abc123"


def test_ollama_preflight_rejects_oversized_context(tmp_path):
    from multiagent.adapters.ollama import OllamaAdapter
    from multiagent.catalog import ModelSpec

    called = {"value":False}
    def handler(request: httpx.Request):
        called["value"] = True
        return httpx.Response(500)
    s = settings_for(tmp_path)
    s.ollama_num_ctx = 1024
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    adapter = OllamaAdapter(s, client=client)
    spec = ModelSpec(id="local",provider="ollama",model="x",tier="local",structured_output=True,web=False,num_ctx=1024,keep_alive="0")
    from multiagent.errors import ContextBudgetError
    with pytest.raises(ContextBudgetError):
        adapter.generate_structured(spec=spec,prompt="x"*10000,output_schema=ContentOutput)
    assert called["value"] is False



def test_ollama_schema_reduces_pydantic_constraints():
    from multiagent.schema_utils import ollama_structural_schema
    schema = ollama_structural_schema(ContentOutput.model_json_schema())
    hook = schema["properties"]["hook"]
    hashtags = schema["properties"]["hashtags"]
    assert hook == {"type": "string"}
    assert hashtags == {"items": {"type": "string"}, "type": "array"}
    assert "title" not in schema
    assert "default" not in schema["properties"]["title"]
    assert "minLength" not in hook and "maxLength" not in hook
    assert "maxItems" not in hashtags
    # The full contract remains in Pydantic and still retains validation constraints.
    original = ContentOutput.model_json_schema()
    assert original["properties"]["hook"]["minLength"] == 8


def test_ollama_http_400_is_request_error_and_exposes_detail(tmp_path):
    from multiagent.adapters.ollama import OllamaAdapter
    from multiagent.catalog import ModelSpec
    from multiagent.errors import ProviderRequestError

    def handler(request: httpx.Request):
        if request.url.path == "/api/chat":
            return httpx.Response(400, json={"error": "invalid schema keyword"})
        return httpx.Response(404)

    s = settings_for(tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    adapter = OllamaAdapter(s, client=client)
    spec = ModelSpec(id="local", provider="ollama", model="qwen3:8b", tier="local", structured_output=True, web=False, num_ctx=4096, keep_alive="15m")
    with pytest.raises(ProviderRequestError) as excinfo:
        adapter.generate_structured(spec=spec, prompt="Create a post", output_schema=ContentOutput)
    message = str(excinfo.value)
    assert "HTTP 400" in message
    assert "invalid schema keyword" in message

def test_asset_sidecar_and_publication_metrics(tmp_path):
    from multiagent.contracts import MetricSnapshotInput
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    project_id = system.store.get_or_create_project("content-data")
    f = tmp_path / "post.png"; f.write_bytes(b"image")
    meta = AssetMetadata(project_id=project_id, source=AssetSource.AI_ASSISTED, status=AssetStatus.PUBLISHED, prompt_id="designer/prompt", prompt_version=2, model_id="image-model")
    asset = system.store.add_asset(f, meta, s.assets_dir)
    assert (s.data_dir / asset["path"]).exists()
    assert (s.data_dir / asset["sidecar"]).exists()
    publication_id = system.store.add_publication(project_id=project_id, platform="Instagram", asset_id=asset["id"], objective="awareness")
    metric_id = system.store.add_metric_snapshot(MetricSnapshotInput(publication_id=publication_id, platform="Instagram", metrics={"reach":1200,"likes":80}))
    with system.db.connect() as conn:
        row = conn.execute("SELECT metrics_json FROM metric_snapshots WHERE id=?", (metric_id,)).fetchone()
    assert json.loads(row["metrics_json"])["reach"] == 1200


def test_run_pins_brand_version_at_creation(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    v1 = BrandProfilePayload(
        name="Example Brand",
        core={"purpose": BrandField(value="Confirmed identity V1", source="human_confirmed")},
    )
    first = system.store.save_brand_profile(v1, activate=True)
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="brand-pin", objective="Create an educational post", topic="KNX")
    )
    assert run["brand_version"] == first["version"]

    v2 = BrandProfilePayload(
        name="Example Brand",
        core={"purpose": BrandField(value="Confirmed identity V2", source="human_confirmed")},
    )
    system.store.save_brand_profile(v2, activate=True)
    system.worker.process_once()
    with system.db.connect() as conn:
        row = conn.execute(
            "SELECT input_json FROM run_steps WHERE run_id=? AND step_id='create' ORDER BY attempt DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
    context = json.loads(row["input_json"])
    assert "Confirmed identity V1" in context["brand_context"]
    assert "Confirmed identity V2" not in context["brand_context"]


def test_telemetry_stores_prompt_hash_and_brand_version(tmp_path):
    s = settings_for(tmp_path); system = build_system(s, dry_run=True)
    profile = BrandProfilePayload(
        name="Example Brand",
        core={"purpose": BrandField(value="Confirmed brand", source="human_confirmed")},
    )
    saved = system.store.save_brand_profile(profile, activate=True)
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="tele-hash", objective="Create an educational post", topic="KNX")
    )
    system.worker.process_once()
    with system.db.connect() as conn:
        row = conn.execute(
            "SELECT prompt_sha256,brand_version FROM telemetry WHERE run_id=? ORDER BY id DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
    assert len(row["prompt_sha256"]) == 64
    assert row["brand_version"] == saved["version"]


class CitedResearchAdapter(LLMAdapter):
    provider = "gemini"
    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        from multiagent.contracts import ResearchOutput
        parsed = ResearchOutput(
            objective="Research with a provider citation",
            key_findings=["A verifiable finding with enough detail for the test."],
            uncertainties=[],
            source_urls=[],
        )
        return LLMResponse(
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            provider="gemini",
            model=spec.model,
            sources=["https://example.org/cited"],
            tokens_in=20,
            tokens_out=20,
        )
    def health(self):
        return {"provider": "gemini", "available": True}


def test_grounding_citations_complete_research_output(tmp_path):
    s = settings_for(tmp_path)
    system = build_system(
        s,
        adapter_overrides={"gemini": CitedResearchAdapter(), "ollama": FakeAdapter()},
    )
    run = system.run_service.create_social_post(
        SocialPostRequest(
            project_name="citations",
            objective="Create a post with current information",
            topic="Wi-Fi",
            requires_web=True,
            research_mode=ResearchMode.GEMINI_GROUNDED,
        )
    )
    system.worker.process_once()
    research = system.store.latest_step_output(run["id"], "research")
    assert research["source_urls"] == ["https://example.org/cited"]


class ContextTooLargeAdapter(LLMAdapter):
    provider = "ollama"
    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        raise ContextBudgetError("local context exceeded")
    def health(self):
        return {"provider": "ollama", "available": True}


def test_local_context_budget_skips_to_cloud_fallback(tmp_path):
    s = settings_for(tmp_path)
    system = build_system(
        s,
        adapter_overrides={"ollama": ContextTooLargeAdapter(), "gemini": FakeAdapter()},
    )
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="ctx-fallback", objective="Create an educational post", topic="KNX")
    )
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    event = [e for e in state["events"] if e["event_type"] == "model_selected"][-1]
    assert event["payload"]["attempts"][0]["error_type"] == "ContextBudgetError"
    assert event["payload"]["attempts"][-1]["ok"] is True


def test_gemini_adapter_uses_structured_interactions_and_grounding(tmp_path):
    from types import SimpleNamespace
    from multiagent.adapters.gemini import GeminiAdapter
    from multiagent.catalog import ModelSpec
    from multiagent.contracts import ResearchOutput

    captured = {}

    class Interactions:
        def create(self, **kwargs):
            captured.update(kwargs)
            annotation = SimpleNamespace(type="url_citation", url="https://example.com/grounded")
            block = SimpleNamespace(annotations=[annotation])
            step = SimpleNamespace(type="model_output", content=[block])
            usage = SimpleNamespace(input_tokens=101, output_tokens=42)
            payload = ResearchOutput(
                objective="Test research",
                key_findings=["A finding long and verifiable enough for the test."],
                source_urls=[],
            )
            return SimpleNamespace(
                output_text=payload.model_dump_json(),
                steps=[step],
                usage=usage,
                model="gemini-test",
            )

    client = SimpleNamespace(interactions=Interactions())
    s = settings_for(tmp_path)
    adapter = GeminiAdapter(s, client=client)
    spec = ModelSpec(
        id="g",
        provider="gemini",
        model="gemini-test",
        tier="cloud",
        structured_output=True,
        web=True,
    )
    response = adapter.generate_structured(
        spec=spec,
        prompt="Research this",
        output_schema=ResearchOutput,
        requires_web=True,
    )
    assert isinstance(captured["response_format"], list)
    assert captured["response_format"][0]["mime_type"] == "application/json"
    assert captured["tools"] == [{"type": "google_search"}]
    assert response.sources == ["https://example.com/grounded"]
    assert response.tokens_in == 101 and response.tokens_out == 42


def test_openai_adapter_uses_responses_parse(tmp_path):
    from types import SimpleNamespace
    from multiagent.adapters.openai_cloud import OpenAIAdapter
    from multiagent.catalog import ModelSpec

    captured = {}
    parsed = ContentOutput(
        title="T",
        hook="A sufficiently long hook",
        body="A body long enough to validate the OpenAI structured output correctly.",
        cta="CTA",
    )

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            item = SimpleNamespace(type="output_text", parsed=parsed)
            message = SimpleNamespace(type="message", content=[item])
            usage = SimpleNamespace(input_tokens=50, output_tokens=25)
            return SimpleNamespace(output=[message], output_text=parsed.model_dump_json(), usage=usage, model="gpt-test")

    client = SimpleNamespace(responses=Responses())
    s = settings_for(tmp_path)
    s.openai_enabled = True
    adapter = OpenAIAdapter(s, client=client)
    spec = ModelSpec(
        id="o",
        provider="openai",
        model="gpt-test",
        tier="cloud",
        structured_output=True,
        web=False,
    )
    response = adapter.generate_structured(spec=spec, prompt="Create", output_schema=ContentOutput)
    assert captured["text_format"] is ContentOutput
    assert response.parsed.hook == parsed.hook
    assert response.tokens_in == 50 and response.tokens_out == 25
