"""CAP-T04: the prompt is a stable prefix (skills, output schema), the variable part, then a short fixed tail."""
import json
from types import SimpleNamespace

from pydantic import BaseModel

from multiagent.adapters.base import cached_tokens
from multiagent.adapters.gemini import GeminiAdapter
from multiagent.adapters.openai_cloud import OpenAIAdapter
from multiagent.config import Settings
from multiagent.contracts import ContentOutput
from multiagent.catalog import ModelSpec
from multiagent.document_trust import ENVELOPE_CLOSE
from multiagent.prompts import PromptManager
from multiagent.workflow_engine import OUTPUT_CONTRACT_REMINDER, WorkflowEngine

from test_context_compaction import good_summary, prompts_of, run_all
from test_document_prompt_boundaries import WINDOW_WITH_BARRIER, guarded_runtime
from test_generic_runs import BUNDLE, request, runtime
from test_usage_services import services  # noqa: F401  (pytest fixture)

CONTENT_JSON = json.dumps({
    "title": "T", "hook": "A hook long enough for contract validation",
    "body": "A body long enough to validate the structured contract correctly.",
    "cta": "CTA", "hashtags": [], "source_urls_used": [],
})


class Out(BaseModel):
    value: str


def manager(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "prompts" / "a.v1.md").write_text("# Role\nTopic: {topic}\nFeedback: {revision_feedback}\n", encoding="utf-8")
    (tmp_path / "skills" / "seo.md").write_text("Use short sentences.", encoding="utf-8")
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return PromptManager(settings, prompts_dir=tmp_path / "prompts", skills_dir=tmp_path / "skills")


def build(pm, **values):
    schema = WorkflowEngine._output_schema_block(Out)
    prefix = pm.stable_prefix(pm.skill_text("seo"), schema)
    return prefix, pm.assemble(prefix, pm.render("a", 1, **values).text, suffix=OUTPUT_CONTRACT_REMINDER)


def test_prefix_identical_across_attempts_and_steps_and_the_fixed_tail_is_last(tmp_path):
    # "Another step" means the same skills AND the same output schema: the prefix is per (skills, contract).
    pm = manager(tmp_path)
    prefix1, first = build(pm, topic="cats", revision_feedback="")
    prefix2, retry = build(pm, topic="cats", revision_feedback="shorter please")   # a later attempt, with feedback
    prefix3, other = build(pm, topic="dogs", revision_feedback="")                 # another step, same agent
    assert prefix1 == prefix2 == prefix3
    for text in (first, retry, other):
        assert text.startswith(prefix1 + "\n\n")
        assert text.endswith(OUTPUT_CONTRACT_REMINDER)                              # trusted text comes last
    assert prefix1.startswith("## Active skill\nUse short sentences.\n\n## OUTPUT SCHEMA")
    assert "## REQUIRED OUTPUT CONTRACT" not in prefix1
    assert first[len(prefix1):].endswith("Feedback: \n\n\n" + OUTPUT_CONTRACT_REMINDER)   # variable part, then the tail
    assert "cats" not in prefix1 and "shorter please" not in prefix1
    assert "dogs" in other[len(prefix1):]


def test_context_sections_come_after_the_rendered_prompt_and_no_skill_still_works(tmp_path):
    pm = manager(tmp_path)
    prefix = pm.stable_prefix("", WorkflowEngine._output_schema_block(Out))
    assert prefix.startswith("## OUTPUT SCHEMA")
    text = pm.assemble(prefix, "BODY", ["SECTION"], "TAIL")
    assert text.endswith("BODY\n\nSECTION\n\nTAIL") and text.startswith(prefix)
    assert pm.assemble("", "BODY", []) == "BODY"


def _prefix_of(prompt):
    """The prompt up to the end of the OUTPUT SCHEMA block (the schema JSON has no blank line)."""
    start = prompt.index("## OUTPUT SCHEMA")
    return prompt[:prompt.index("\n\n", start)]


def _capture(system, monkeypatch):
    prompts = []
    adapter = system.engine.router.adapters["fake"]
    generate = adapter.generate_structured

    def capture(**kwargs):
        prompts.append(kwargs["prompt"])
        return generate(**kwargs)

    monkeypatch.setattr(adapter, "generate_structured", capture)
    return prompts


def test_engine_prompt_has_prefix_first_and_tail_last(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    prompts = _capture(system, monkeypatch)
    req = request()
    run = system.service.create_run(req)
    system.engine.process_run(run["id"])
    system.service.approve(run["id"])
    system.engine.process_run(run["id"])
    assert len(prompts) >= 2
    spec_text = req.inputs["spec_text"]
    for prompt in prompts:
        assert prompt.startswith(("## Active skill", "## OUTPUT SCHEMA"))             # stable part first
        assert prompt.endswith(OUTPUT_CONTRACT_REMINDER)                              # fixed tail last
        assert prompt.count("## REQUIRED OUTPUT CONTRACT") == 1
    assert prompts[0].index("## OUTPUT SCHEMA") < prompts[0].index(spec_text) < prompts[0].index(
        "## REQUIRED OUTPUT CONTRACT")                                                # input between prefix and tail


def test_two_real_attempts_of_the_same_step_share_the_prefix_byte_for_byte(tmp_path, monkeypatch):
    # Holds for steps with the same skills and the same output contract (the same step does by definition).
    system = runtime(tmp_path)
    prompts = _capture(system, monkeypatch)
    run = system.service.create_run(request())
    system.engine.process_run(run["id"])
    system.service.changes(run["id"], "Explain the highest risk")
    system.engine.process_run(run["id"])
    retried = [p for p in prompts if "Explain the highest risk" in p]
    assert retried                                                                  # the second attempt, with feedback
    for later in retried:
        earlier = [p for p in prompts if "Explain the highest risk" not in p and _prefix_of(p) == _prefix_of(later)]
        assert earlier, "the retry has the very same prefix as its first attempt"
        assert later != earlier[0] and later.endswith(OUTPUT_CONTRACT_REMINDER)


def test_untrusted_data_is_always_followed_by_the_fixed_tail(tmp_path):
    system = guarded_runtime(tmp_path, window=WINDOW_WITH_BARRIER, summaries=[good_summary(), good_summary()])
    run_all(system)
    prompts = prompts_of(system)
    assert "kind=context_summary" in prompts[3]                                      # the step that gets the summary
    for prompt in prompts:
        assert prompt.endswith(OUTPUT_CONTRACT_REMINDER)
        if ENVELOPE_CLOSE in prompt:
            assert prompt.rindex(ENVELOPE_CLOSE) < prompt.index("## REQUIRED OUTPUT CONTRACT")


def test_no_run_variable_appears_inside_the_prefix(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    prompts = _capture(system, monkeypatch)
    req = request()
    run = system.service.create_run(req)
    system.engine.process_run(run["id"])
    system.service.approve(run["id"])
    system.engine.process_run(run["id"])
    variables = [run["id"], *[v for v in req.inputs.values() if isinstance(v, str) and v]]
    for prompt in prompts:
        prefix = _prefix_of(prompt)
        assert "REQUIRED OUTPUT CONTRACT" not in prefix
        for value in variables:
            assert value not in prefix


def test_cached_tokens_reach_telemetry_and_unknown_stays_null(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    adapter = system.engine.router.adapters["fake"]
    generate = adapter.generate_structured
    calls = []

    def with_cache(**kwargs):
        response = generate(**kwargs)
        calls.append(response)
        if len(calls) == 1:
            response.metadata["cache_read_tokens"] = 64                             # only the first call reports it
        return response

    monkeypatch.setattr(adapter, "generate_structured", with_cache)
    run = system.service.create_run(request())
    system.engine.process_run(run["id"])
    with system.store.db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT cache_read_tokens,cache_write_tokens FROM telemetry ORDER BY id")]
    assert len(rows) >= 2 and rows[0]["cache_read_tokens"] == 64
    assert all(r["cache_read_tokens"] is None for r in rows[1:])                    # unknown, never 0
    assert all(r["cache_write_tokens"] is None for r in rows)


def test_usage_tokens_show_cache_read_apart_and_out_of_the_total(services):  # noqa: F811
    store = services.system.store
    store.record_telemetry(run_id="r-cache", step_id="s", agent_id="a", attempt=1, provider="openai", model="m-cache", model_digest="", prompt_id="p", prompt_version=1, prompt_sha256="", error_type="",
                           tokens_in=100, tokens_out=10, cache_read_tokens=64, success=1)
    store.record_telemetry(run_id="r-cache", step_id="s2", agent_id="a", attempt=1, provider="openai",
                           model="m-nocache", model_digest="", prompt_id="p", prompt_version=1, prompt_sha256="", error_type="", tokens_in=100, tokens_out=10, success=1)
    with store.db.connect() as conn:
        conn.execute("UPDATE telemetry SET created_at='2026-10-10T12:00:00+00:00' WHERE run_id='r-cache'")
        conn.commit()
    result = services.usage.tokens(apps="framework")
    cached = next(r for r in result["rows"] if r["model"] == "m-cache")
    plain = next(r for r in result["rows"] if r["model"] == "m-nocache")
    assert (cached["input"], cached["cache_read"], cached["total"]) == (100, 64, 110)
    assert plain["cache_read"] is None and plain["total"] == 110


def spec(provider):
    return ModelSpec(id=f"{provider}_m", provider=provider, model=f"{provider}-test", tier="cloud",
                     structured_output=True, web=False)


def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "d", db_path=tmp_path / "d" / "m.db", runs_dir=tmp_path / "d" / "r",
                    assets_dir=tmp_path / "d" / "a", worker_enabled=False, gemini_api_key="", openai_enabled=True)


class _Responses:
    def __init__(self, usage):
        self.usage = usage

    def parse(self, **kwargs):
        parsed = ContentOutput.model_validate_json(CONTENT_JSON)
        message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", parsed=parsed)])
        return SimpleNamespace(output=[message], output_text=CONTENT_JSON, model="gpt-test", usage=self.usage)


class _Interactions:
    def __init__(self, usage):
        self.usage = usage

    def create(self, **kwargs):
        return SimpleNamespace(output_text=CONTENT_JSON, steps=[], usage=self.usage, model="gemini-test")


def test_openai_cached_tokens_are_recorded(tmp_path):
    usage = SimpleNamespace(input_tokens=100, output_tokens=5, input_tokens_details=SimpleNamespace(cached_tokens=64))
    adapter = OpenAIAdapter(settings(tmp_path), client=SimpleNamespace(responses=_Responses(usage)))
    response = adapter.generate_structured(spec=spec("openai"), prompt="x", output_schema=ContentOutput)
    assert response.metadata["cache_read_tokens"] == 64
    assert (response.tokens_in, response.tokens_out) == (100, 5)


def test_gemini_cached_tokens_are_recorded(tmp_path):
    usage = SimpleNamespace(input_tokens=50, output_tokens=2, cached_content_token_count=32)
    adapter = GeminiAdapter(settings(tmp_path), client=SimpleNamespace(interactions=_Interactions(usage)))
    response = adapter.generate_structured(spec=spec("gemini"), prompt="x", output_schema=ContentOutput)
    assert response.metadata["cache_read_tokens"] == 32
    assert response.metadata["grounded"] is False


def test_adapters_without_the_field_do_not_change(tmp_path):
    usage = SimpleNamespace(input_tokens=3, output_tokens=4)
    openai = OpenAIAdapter(settings(tmp_path), client=SimpleNamespace(responses=_Responses(usage)))
    gemini = GeminiAdapter(settings(tmp_path), client=SimpleNamespace(interactions=_Interactions(usage)))
    assert "cache_read_tokens" not in openai.generate_structured(
        spec=spec("openai"), prompt="x", output_schema=ContentOutput).metadata
    assert gemini.generate_structured(spec=spec("gemini"), prompt="x", output_schema=ContentOutput).metadata == {
        "grounded": False, "thinking": None}


def test_cached_tokens_helper_ignores_missing_and_invalid_values():
    assert cached_tokens(None, "a") is None
    assert cached_tokens({"a": {"b": 7}}, "a.b") == 7
    assert cached_tokens(SimpleNamespace(a=True), "a") is None
    assert cached_tokens(SimpleNamespace(a=-1), "a") is None
