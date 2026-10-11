"""ECO-T04: step kind and per-step reasoning effort.

Covers workflow loading (optional fields, invalid values), the new ``RoutingRequest`` fields and
how the engine fills them, the per-adapter effort translation with fake or mocked clients (no
network), the ``effort_applied`` record on model attempts, and backward compatibility.
"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from pydantic import BaseModel

from multiagent.adapters.base import EffortTranslation, LLMAdapter, LLMResponse
from multiagent.adapters.fake import FakeAdapter
from multiagent.adapters.gemini import GEMINI_THINKING_LEVEL, GeminiAdapter
from multiagent.adapters.ollama import OllamaAdapter
from multiagent.adapters.openai_cloud import OPENAI_REASONING_EFFORT, OpenAIAdapter
from multiagent.bootstrap import build_system
from multiagent.catalog import Catalog, ModelSpec
from multiagent.config import Settings
from multiagent.contracts import (
    OUTPUT_SCHEMAS, ContentOutput, ModelTier, RunStatus, SocialPostRequest, TaskRequirements,
)
from multiagent.errors import ProviderUnavailable
from multiagent.model_router import ModelBinding, ModelRouter
from multiagent.routing_policy import (
    DEFAULT_STEP_KIND, EFFORT_LEVELS, STEP_KINDS, RoutingDecision, RoutingRequest,
)
from multiagent.workflow_validation import WorkflowDefinitionError, validate_workflow_definition
from multiagent.workflows import WorkflowCatalog, WorkflowDefinition, WorkflowStep

STEP = {
    "id": "create",
    "agent": "creator",
    "prompt_id": "creator/social_post",
    "prompt_version": 2,
    "skills": ["social_media"],
    "contract": "ContentOutput",
    "preferred_model": "local_default",
    "fallback_model": "cloud_fast",
    "requires_web": False,
    "when": None,
    "checkpoint_after": True,
}
SAMPLE = {"id": "sample", "max_iterations": 2, "steps": [STEP]}
LEGACY_FIELDS = tuple(STEP)


# ---------- helpers ----------

@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="", openai_api_key="")


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    path = tmp_path / "workflows"
    path.mkdir()
    return path


def sample_with(**step_changes) -> dict:
    raw = copy.deepcopy(SAMPLE)
    raw["steps"][0].update(step_changes)
    return raw


def load_text(settings, workflows_dir, text: str) -> WorkflowDefinition:
    (workflows_dir / "sample.yaml").write_text(text, encoding="utf-8")
    return WorkflowCatalog(settings, workflows_dir=workflows_dir).load("sample")


def load_raw(settings, workflows_dir, raw) -> WorkflowDefinition:
    return load_text(settings, workflows_dir, yaml.safe_dump(raw, sort_keys=False))


def load_error(settings, workflows_dir, raw) -> WorkflowDefinitionError:
    with pytest.raises(WorkflowDefinitionError) as info:
        load_raw(settings, workflows_dir, raw)
    return info.value


def issue_codes(error: WorkflowDefinitionError) -> set[tuple[str, str]]:
    return {(issue.code, issue.location) for issue in error.issues}


def app_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
        worker_enabled=False, gemini_api_key="",
    )


class Payload(BaseModel):
    value: str


def spec_for(provider: str, **overrides) -> ModelSpec:
    values = dict(id=f"{provider}_model", provider=provider, model=f"{provider}-test", tier="local",
                  structured_output=True, web=False)
    values.update(overrides)
    return ModelSpec(**values)


CONTENT_JSON = json.dumps({
    "title": "T", "hook": "A hook long enough for contract validation",
    "body": "A body long enough to validate the structured contract correctly.",
    "cta": "CTA", "hashtags": [], "source_urls_used": [],
})


# ---------- workflow loading ----------

def test_steps_without_the_new_fields_load_with_defaults(settings, workflows_dir):
    definition = load_raw(settings, workflows_dir, SAMPLE)
    step = definition.steps[0]
    assert (step.kind, step.effort) == (DEFAULT_STEP_KIND, None) == ("work", None)
    assert step.checkpoint_after is True and step.id == "create"


@pytest.mark.parametrize("name", ["social_post", "social_post_full"])
def test_bundled_workflows_are_unchanged(settings, name):
    definition = WorkflowCatalog(settings).load(name)
    validate_workflow_definition(definition, catalog=Catalog(settings), settings=settings, output_schemas=OUTPUT_SCHEMAS)
    assert {(step.kind, step.effort) for step in definition.steps} == {("work", None)}


@pytest.mark.parametrize("kind", STEP_KINDS)
@pytest.mark.parametrize("effort", EFFORT_LEVELS)
def test_every_kind_and_effort_loads(settings, workflows_dir, kind, effort):
    definition = load_raw(settings, workflows_dir, sample_with(kind=kind, effort=effort))
    assert (definition.steps[0].kind, definition.steps[0].effort) == (kind, effort)
    validate_workflow_definition(definition, catalog=Catalog(settings), settings=settings, output_schemas=OUTPUT_SCHEMAS)


def test_each_field_can_be_set_alone(settings, workflows_dir):
    only_kind = load_raw(settings, workflows_dir, sample_with(kind="review")).steps[0]
    assert (only_kind.kind, only_kind.effort) == ("review", None)
    only_effort = load_raw(settings, workflows_dir, sample_with(effort="low")).steps[0]
    assert (only_effort.kind, only_effort.effort) == ("work", "low")


def test_yaml_effort_none_is_the_level_not_null(settings, workflows_dir):
    base = yaml.safe_dump(SAMPLE, sort_keys=False)    # the step is last and checkpoint_after is its last key
    assert load_text(settings, workflows_dir, base + "  effort: none\n").steps[0].effort == "none"
    assert load_text(settings, workflows_dir, base + "  effort: null\n").steps[0].effort is None
    assert load_text(settings, workflows_dir, base + "  effort:\n").steps[0].effort is None


@pytest.mark.parametrize("field,value,code", [
    ("kind", "design", "invalid_value"),
    ("kind", "Work", "invalid_value"),
    ("kind", "", "invalid_value"),
    ("kind", 3, "invalid_type"),
    ("kind", None, "invalid_type"),
    ("kind", ["work"], "invalid_type"),
    ("effort", "extreme", "invalid_value"),
    ("effort", "HIGH", "invalid_value"),
    ("effort", "", "invalid_value"),
    ("effort", 2, "invalid_type"),
    ("effort", False, "invalid_type"),     # YAML turns `off` / `no` into false
    ("effort", ["low"], "invalid_type"),
])
def test_invalid_values_are_rejected_with_a_clear_error(settings, workflows_dir, field, value, code):
    error = load_error(settings, workflows_dir, sample_with(**{field: value}))
    assert issue_codes(error) == {(code, f"steps[0].{field}")}
    message = str(error)
    allowed = STEP_KINDS if field == "kind" else EFFORT_LEVELS
    assert all(name in message for name in allowed)
    assert error.issues[0].step_id == "create"


def test_both_invalid_fields_are_reported_together(settings, workflows_dir):
    error = load_error(settings, workflows_dir, sample_with(kind="x", effort="y"))
    assert issue_codes(error) == {("invalid_value", "steps[0].kind"), ("invalid_value", "steps[0].effort")}


def test_misspelled_new_field_gets_a_suggestion(settings, workflows_dir):
    error = load_error(settings, workflows_dir, sample_with(efort="low"))
    assert issue_codes(error) == {("unknown_field", "steps[0].efort")}
    assert "did you mean 'effort'" in str(error)


def test_old_required_fields_are_still_required_but_new_ones_are_not(settings, workflows_dir):
    raw = sample_with()
    del raw["steps"][0]["checkpoint_after"]
    error = load_error(settings, workflows_dir, raw)
    assert issue_codes(error) == {("missing_field", "steps[0].checkpoint_after")}


def test_python_built_step_gets_the_same_checks(settings):
    step = WorkflowStep(**STEP)
    assert (step.kind, step.effort) == ("work", None)
    bad = WorkflowStep(**STEP, kind="nope", effort="loud")
    definition = WorkflowDefinition(id="sample", max_iterations=2, steps=[bad])
    with pytest.raises(WorkflowDefinitionError) as info:
        validate_workflow_definition(definition, catalog=Catalog(settings), settings=settings, output_schemas=OUTPUT_SCHEMAS)
    assert issue_codes(info.value) == {("invalid_value", "steps[0].kind"), ("invalid_value", "steps[0].effort")}


def test_definition_services_expose_kind_and_effort(tmp_path):
    from multiagent.services import ApplicationServices

    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    bundle = tmp_path / "spec_review"
    shutil.copytree(Path(__file__).parent / "fixtures" / "bundles" / "spec_review", bundle)
    path = bundle / "workflows" / "spec_review.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["steps"][1]["kind"] = "review"
    document["steps"][1]["effort"] = "medium"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    services = ApplicationServices.create(settings, bundles={"spec_review": bundle}, dry_run=True)

    steps = services.definitions.get_workflow("spec_review", bundle="spec_review")["steps"]
    assert [(s["kind"], s["effort"]) for s in steps] == [("work", None), ("review", "medium"), ("work", None)]
    builtin = services.definitions.get_workflow("social_post")["steps"]
    assert {(s["kind"], s["effort"]) for s in builtin} == {("work", None)}


# ---------- RoutingRequest ----------

def test_routing_request_defaults_keep_current_behavior():
    request = RoutingRequest()
    assert (request.step_kind, request.effort, request.attempt) == ("work", None, 1)
    data = request.to_dict()
    assert (data["step_kind"], data["effort"], data["attempt"]) == ("work", None, 1)


def test_routing_request_carries_the_new_fields():
    request = RoutingRequest(step_kind="planning", effort="high", attempt=3)
    assert (request.step_kind, request.effort, request.attempt) == ("planning", "high", 3)
    assert request.to_dict()["effort"] == "high"


@pytest.mark.parametrize("values", [
    {"step_kind": "design"}, {"effort": "huge"}, {"attempt": 0}, {"attempt": "1"}, {"attempt": True},
])
def test_routing_request_rejects_unknown_values(values):
    with pytest.raises(ValueError):
        RoutingRequest(**values)


def _use_workflow(system, tmp_path: Path, **step_changes) -> None:
    """Point the engine at a copy of social_post whose ``create`` step has the given fields."""
    directory = tmp_path / "custom_workflows"
    directory.mkdir()
    document = yaml.safe_load((Path(system.engine.workflows.workflows_dir) / "social_post.yaml").read_text(encoding="utf-8"))
    document["steps"][1].update(step_changes)
    (directory / "social_post.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    system.engine.workflows.workflows_dir = directory


def _profile_system(tmp_path: Path, monkeypatch, **step_changes):
    system = build_system(app_settings(tmp_path), dry_run=True)
    _use_workflow(system, tmp_path, **step_changes)
    system.catalog.routing_assignments["social_post"] = {"create": "test-profile"}
    system.catalog.routing_profiles["test-profile"] = {
        "enabled": True, "candidate_model_ids": ["local_default"], "max_candidates": 1,
        "max_provider_invocations": 5, "max_retries_per_candidate": 0,
    }
    seen: list[RoutingRequest] = []

    def select(request, evidence):
        seen.append(request)
        return RoutingDecision(status="selected", requested_model="local_default", selected_model="local_default",
                               profile_id="test-profile")

    monkeypatch.setattr(system.engine.router, "select", select)
    return system, seen


def _run(system, **overrides):
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="eco-t04", objective="Create an educational post", topic="KNX", **overrides))
    system.worker.process_once()
    return run


def test_engine_passes_kind_effort_and_attempt_to_the_router(tmp_path, monkeypatch):
    system, seen = _profile_system(tmp_path, monkeypatch, kind="review", effort="high")
    run = _run(system)
    assert [(r.step_id, r.step_kind, r.effort, r.attempt) for r in seen] == [("create", "review", "high", 1)]

    system.run_service.changes(run["id"], "Make it shorter")
    system.worker.process_once()
    assert [(r.step_kind, r.effort, r.attempt) for r in seen] == [("review", "high", 1), ("review", "high", 2)]


def test_engine_defaults_reach_the_router_for_plain_steps(tmp_path, monkeypatch):
    system, seen = _profile_system(tmp_path, monkeypatch)
    _run(system)
    assert [(r.step_kind, r.effort, r.attempt) for r in seen] == [("work", None, 1)]


def test_engine_records_kind_and_effort_and_the_applied_effort(tmp_path):
    system = build_system(app_settings(tmp_path), dry_run=True)
    _use_workflow(system, tmp_path, kind="planning", effort="low")
    run = _run(system)
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    started = next(e for e in state["events"] if e["event_type"] == "step_started")["payload"]
    assert (started["step_kind"], started["effort"]) == ("planning", "low")
    attempt = [e for e in state["events"] if e["event_type"] == "model_attempt"][-1]["payload"]
    assert attempt["effort_requested"] == "low"
    assert attempt["effort_applied"] is True
    assert attempt["effective_config_effort"] == {"effort": "low"}   # the fake provider's simulated setting
    assert attempt["effort_note"] is None
    assert attempt["metadata"]["effort_applied"] is True


def test_engine_without_effort_changes_nothing_in_the_records(tmp_path):
    system = build_system(app_settings(tmp_path), dry_run=True)
    run = _run(system)
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    attempts = [e["payload"] for e in state["events"] if e["event_type"] == "model_attempt"]
    assert attempts
    for payload in attempts:
        assert not [key for key in payload if key.startswith("effort") or key == "effective_config_effort"]
        assert not [key for key in payload["metadata"] if key.startswith("effort")]
    started = next(e for e in state["events"] if e["event_type"] == "step_started")["payload"]
    assert (started["step_kind"], started["effort"]) == ("work", None)


# ---------- adapter translation ----------

def ollama_adapter(tmp_path, seen: dict) -> OllamaAdapter:
    def handler(request: httpx.Request):
        if request.url.path == "/api/chat":
            seen.update(json.loads(request.content.decode()))
            return httpx.Response(200, json={
                "model": "qwen3:8b", "message": {"role": "assistant", "content": CONTENT_JSON},
                "prompt_eval_count": 5, "eval_count": 7,
            })
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    return OllamaAdapter(app_settings(tmp_path), client=client)


@pytest.mark.parametrize("thinking", [None, False, True])
@pytest.mark.parametrize("effort,think", [("none", False), ("low", True), ("medium", True), ("high", True), ("max", True)])
def test_ollama_effort_sets_thinking_whatever_the_catalog_says(tmp_path, effort, think, thinking):
    seen: dict = {}
    adapter = ollama_adapter(tmp_path, seen)
    response = adapter.generate_structured(spec=spec_for("ollama", model="qwen3:8b", thinking=thinking),
                                           prompt="x", output_schema=ContentOutput, effort=effort)
    assert seen["think"] is think
    assert response.metadata["thinking"] is think
    assert response.metadata["effort_requested"] == effort
    assert response.metadata["effort_applied"] is True
    assert response.metadata["effort_setting"] == {"think": think}


@pytest.mark.parametrize("thinking,expected", [(None, False), (False, False), (True, True)])
def test_ollama_without_effort_keeps_the_catalog_behavior(tmp_path, thinking, expected):
    seen: dict = {}
    response = ollama_adapter(tmp_path, seen).generate_structured(
        spec=spec_for("ollama", model="qwen3:8b", thinking=thinking), prompt="x", output_schema=ContentOutput)
    assert seen["think"] is expected
    assert response.metadata["thinking"] is expected
    assert not [key for key in response.metadata if key.startswith("effort")]


class GeminiInteractions:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=CONTENT_JSON, steps=[], usage=SimpleNamespace(input_tokens=3, output_tokens=4),
                               model="gemini-test")


def gemini_adapter(tmp_path) -> tuple[GeminiAdapter, GeminiInteractions]:
    interactions = GeminiInteractions()
    return GeminiAdapter(app_settings(tmp_path), client=SimpleNamespace(interactions=interactions)), interactions


@pytest.mark.parametrize("effort,level", [
    ("none", "minimal"), ("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "high"),
])
def test_gemini_effort_becomes_the_thinking_level(tmp_path, effort, level):
    adapter, interactions = gemini_adapter(tmp_path)
    response = adapter.generate_structured(spec=spec_for("gemini", thinking="minimal"), prompt="x",
                                           output_schema=ContentOutput, effort=effort)
    assert interactions.calls[0]["generation_config"] == {"thinking_level": level}
    assert response.metadata["effort_applied"] is True
    assert response.metadata["effort_setting"] == {"thinking_level": level}
    assert GEMINI_THINKING_LEVEL[effort] == level


def test_gemini_effort_is_combined_with_grounding(tmp_path):
    adapter, interactions = gemini_adapter(tmp_path)
    adapter.generate_structured(spec=spec_for("gemini", web=True), prompt="x", output_schema=ContentOutput,
                                requires_web=True, effort="medium")
    call = interactions.calls[0]
    assert call["tools"] == [{"type": "google_search"}]
    assert call["generation_config"] == {"thinking_level": "medium"}


def test_gemini_without_effort_sends_no_generation_config(tmp_path):
    adapter, interactions = gemini_adapter(tmp_path)
    response = adapter.generate_structured(spec=spec_for("gemini", thinking="low"), prompt="x", output_schema=ContentOutput)
    assert "generation_config" not in interactions.calls[0]
    assert response.metadata == {"grounded": False, "thinking": "low"}


class OpenAIResponses:
    def __init__(self):
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        parsed = ContentOutput.model_validate_json(CONTENT_JSON)
        message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", parsed=parsed)])
        return SimpleNamespace(output=[message], output_text=CONTENT_JSON, model="gpt-test",
                               usage=SimpleNamespace(input_tokens=3, output_tokens=4))


def openai_adapter(tmp_path) -> tuple[OpenAIAdapter, OpenAIResponses]:
    responses = OpenAIResponses()
    settings = app_settings(tmp_path)
    settings.openai_enabled = True
    return OpenAIAdapter(settings, client=SimpleNamespace(responses=responses)), responses


@pytest.mark.parametrize("effort,level", [("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "high")])
def test_openai_effort_becomes_reasoning_effort(tmp_path, effort, level):
    adapter, responses = openai_adapter(tmp_path)
    response = adapter.generate_structured(spec=spec_for("openai", tier="cloud"), prompt="x",
                                           output_schema=ContentOutput, effort=effort)
    assert responses.calls[0]["reasoning"] == {"effort": level}
    assert response.metadata["effort_applied"] is True
    assert response.metadata["effort_setting"] == {"reasoning_effort": level}
    assert OPENAI_REASONING_EFFORT[effort] == level


def test_openai_none_has_no_portable_level_and_is_ignored_without_failing(tmp_path):
    adapter, responses = openai_adapter(tmp_path)
    response = adapter.generate_structured(spec=spec_for("openai", tier="cloud"), prompt="x",
                                           output_schema=ContentOutput, effort="none")
    assert "reasoning" not in responses.calls[0]
    assert response.metadata["effort_requested"] == "none"
    assert response.metadata["effort_applied"] is False
    assert response.metadata["effort_setting"] is None
    assert response.metadata["effort_note"] == "no_portable_openai_level_for_none"


def test_openai_without_effort_is_unchanged(tmp_path):
    adapter, responses = openai_adapter(tmp_path)
    response = adapter.generate_structured(spec=spec_for("openai", tier="cloud"), prompt="x", output_schema=ContentOutput)
    assert set(responses.calls[0]) == {"model", "input", "text_format"}
    assert response.metadata == {}


def test_fake_adapter_simulates_support_or_lack_of_it():
    spec = spec_for("fake")
    supported = FakeAdapter().generate_structured(spec=spec, prompt="x", output_schema=ContentOutput, effort="high")
    assert supported.metadata["effort_applied"] is True and supported.metadata["effort_setting"] == {"effort": "high"}
    unsupported = FakeAdapter(supports_effort=False).generate_structured(
        spec=spec, prompt="x", output_schema=ContentOutput, effort="high")
    assert unsupported.metadata["effort_applied"] is False
    assert unsupported.metadata["effort_note"] == "provider_does_not_support_effort"
    assert FakeAdapter().generate_structured(spec=spec, prompt="x", output_schema=ContentOutput).metadata == {}


def test_adapter_that_does_not_override_translation_ignores_every_effort():
    class Plain(LLMAdapter):
        provider = "plain"

        def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
            raise NotImplementedError

        def health(self):
            return {}

    spec = spec_for("plain")
    assert Plain().translate_effort(spec, None) == EffortTranslation(None)
    ignored = Plain().translate_effort(spec, "high")
    assert (ignored.requested, ignored.applied, ignored.setting) == ("high", False, None)


@pytest.mark.parametrize("factory", [
    lambda tmp: ollama_adapter(tmp, {}),
    lambda tmp: gemini_adapter(tmp)[0],
    lambda tmp: openai_adapter(tmp)[0],
    lambda tmp: FakeAdapter(),
])
def test_unknown_effort_is_ignored_by_every_adapter(tmp_path, factory):
    translation = factory(tmp_path).translate_effort(spec_for("x"), "ludicrous")
    assert (translation.requested, translation.applied, translation.note) == ("ludicrous", False, "unknown_effort")


# ---------- effort recorded on model attempts ----------

class Models:
    """Catalog stand-in with one local and one cloud model."""

    routing_profiles: dict = {}

    def __init__(self):
        self.models = {
            "primary": ModelSpec("primary", "one", "model-one", "local", True, False),
            "fallback": ModelSpec("fallback", "two", "model-two", "cloud", True, True),
            "no_web": ModelSpec("no_web", "one", "model-one", "local", True, False),
        }

    def model(self, model_id):
        return self.models[model_id]


class Recording(LLMAdapter):
    """Accepts ``effort`` and reports what it did, like the real adapters."""

    def __init__(self, provider, *, supports=True, error=None):
        self.provider, self.supports, self.error, self.calls = provider, supports, error, []

    def translate_effort(self, spec, effort):
        if effort is None:
            return EffortTranslation(None)
        if not self.supports:
            return EffortTranslation(effort, False, None, "provider_does_not_support_effort")
        return EffortTranslation(effort, True, {"level": effort.upper()})

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.calls.append(effort)
        if self.error is not None:
            raise self.error
        parsed = output_schema(value="ok")
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model,
                           metadata=self.translate_effort(spec, effort).metadata())

    def health(self):
        return {"provider": self.provider, "available": True}


class Legacy(LLMAdapter):
    """Written before ``effort`` existed: no such parameter, no translation."""

    def __init__(self, provider):
        self.provider, self.calls = provider, 0

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        self.calls += 1
        parsed = output_schema(value="ok")
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model)

    def health(self):
        return {"provider": self.provider, "available": True}


def requirements(requires_web=False):
    return TaskRequirements(requires_web=requires_web, output_schema="Payload", tier=ModelTier.LOCAL)


def execute(router, binding, *, effort=None, requires_web=False):
    events: list[dict] = []
    result = router.execute(binding=binding, requirements=requirements(requires_web), prompt="x", output_schema=Payload,
                            on_attempt=events.append, effort=effort)
    return result, events


def test_attempt_records_the_requested_and_applied_effort():
    adapter = Recording("one")
    (_, attempts), events = execute(ModelRouter(Models(), {"one": adapter}), ModelBinding("primary"), effort="medium")
    assert adapter.calls == ["medium"]
    for payload in (events[0], attempts[0]):
        assert payload["effort_requested"] == "medium"
        assert payload["effort_applied"] is True
        assert payload["effective_config_effort"] == {"level": "MEDIUM"}
        assert payload["effort_note"] is None
        assert payload["effective_config_model"] == "model-one"


def test_unsupported_effort_is_recorded_as_not_applied_and_does_not_fail():
    adapter = Recording("one", supports=False)
    (response, _), events = execute(ModelRouter(Models(), {"one": adapter}), ModelBinding("primary"), effort="high")
    assert response.parsed.value == "ok"
    assert events[0]["effort_requested"] == "high"
    assert events[0]["effort_applied"] is False
    assert events[0]["effective_config_effort"] is None
    assert events[0]["effort_note"] == "provider_does_not_support_effort"


def test_adapter_without_the_effort_parameter_is_never_called_with_it():
    adapter = Legacy("one")
    (response, _), events = execute(ModelRouter(Models(), {"one": adapter}), ModelBinding("primary"), effort="low")
    assert response.parsed.value == "ok" and adapter.calls == 1
    assert events[0]["effort_requested"] == "low"
    assert events[0]["effort_applied"] is False
    assert events[0]["effort_note"] == "adapter_does_not_accept_effort"


def test_no_effort_leaves_attempts_and_adapter_calls_as_before():
    adapter = Legacy("one")
    (_, attempts), events = execute(ModelRouter(Models(), {"one": adapter}), ModelBinding("primary"))
    assert adapter.calls == 1
    for payload in (events[0], attempts[0]):
        assert not [key for key in payload if key.startswith("effort") or key == "effective_config_effort"]
    recording = Recording("one")
    execute(ModelRouter(Models(), {"one": recording}), ModelBinding("primary"))
    assert recording.calls == [None]


def test_effort_follows_the_step_to_the_fallback_model_with_its_own_translation():
    primary = Recording("one", error=ProviderUnavailable("down"))
    fallback = Recording("two", supports=False)
    _, events = execute(ModelRouter(Models(), {"one": primary, "two": fallback}), ModelBinding("primary", "fallback"),
                        effort="low")
    first, second = events
    assert first["status"] == "failed" and first["provider_invoked"] is True
    assert first["effort_applied"] is True        # the failed request did carry the setting
    assert second["status"] == "success" and second["effort_applied"] is False
    assert second["effort_note"] == "provider_does_not_support_effort"


def test_preflight_rejection_never_counts_as_applied():
    primary = Recording("one")
    fallback = Recording("two")
    _, events = execute(ModelRouter(Models(), {"one": primary, "two": fallback}), ModelBinding("no_web", "fallback"),
                        effort="low", requires_web=True)
    rejected = events[0]
    assert rejected["stage"] == "preflight" and rejected["provider_invoked"] is False
    assert rejected["effort_requested"] == "low" and rejected["effort_applied"] is False
    assert rejected["effort_note"] == "provider_not_invoked"
    assert primary.calls == []


def test_dry_run_passes_the_effort_to_the_fake_adapter():
    router = ModelRouter(Models(), {"fake": FakeAdapter(sample_outputs={"ContentOutput": json.loads(CONTENT_JSON)})},
                         dry_run=True)
    events: list[dict] = []
    router.execute(binding=ModelBinding("primary"), requirements=requirements(), prompt="x", output_schema=ContentOutput,
                   on_attempt=events.append, effort="max")
    assert events[0]["effort_requested"] == "max" and events[0]["effort_applied"] is True
    assert events[0]["effective_config_effort"] == {"effort": "max"}
