# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CAP-T21: OpenTelemetry spans and metrics of execution backends (off by default, optional extra)."""

from __future__ import annotations

import json
import sys
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cmp_harness import Provider
from multiagent.adapters.fake import FakeAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import BackendContext, REQUIRED_CAPABILITIES, build_backend, register_backend, unregister_backend
from multiagent.bootstrap import build_system
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.errors import ProviderUnavailable
from multiagent.observability import otel
from multiagent.observability.otel import RunTelemetry, build_telemetry
from multiagent.observability.usage import read_usage
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.workflow_engine import WorkflowEngine
from multiagent.workflows import WorkflowCatalog

trace = pytest.importorskip("opentelemetry.trace")


class FakeSpan(trace.Span):
    def __init__(self, name, parent, attributes, start_time):
        self.name, self.parent, self.attributes, self.start_time = name, parent, dict(attributes or {}), start_time
        self.status, self.ended, self.end_time = None, False, None

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def set_status(self, status):
        self.status = status

    def end(self, end_time=None):
        self.ended, self.end_time = True, end_time

    def get_span_context(self):
        return trace.INVALID_SPAN_CONTEXT

    def is_recording(self):
        return not self.ended

    def set_attributes(self, attributes):
        self.attributes.update(attributes)

    def add_event(self, *args, **kwargs):
        pass

    def update_name(self, name):
        self.name = name

    def record_exception(self, *args, **kwargs):
        pass


class FakeTracer:
    def __init__(self):
        self.spans: list[FakeSpan] = []

    def start_span(self, name, context=None, attributes=None, start_time=None, **_):
        parent = trace.get_current_span(context) if context is not None else None
        span = FakeSpan(name, parent if isinstance(parent, FakeSpan) else None, attributes, start_time)
        self.spans.append(span)
        return span

    def named(self, prefix):
        return [span for span in self.spans if span.name.startswith(prefix)]


class FakeHistogram:
    def __init__(self, name):
        self.name, self.points = name, []

    def record(self, value, attributes=None):
        self.points.append((value, dict(attributes or {})))


class FakeMeter:
    def __init__(self):
        self.histograms: dict[str, FakeHistogram] = {}

    def create_histogram(self, name, unit="", description=""):
        return self.histograms.setdefault(name, FakeHistogram(name))


def settings_for(tmp_path: Path, **extra) -> Settings:
    return Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                    runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                    worker_enabled=False, gemini_api_key="", **extra)


@pytest.fixture
def fakes(monkeypatch):
    tracer, meter = FakeTracer(), FakeMeter()
    monkeypatch.setattr("multiagent.backends.telemetry.build_telemetry",
                        lambda settings: build_telemetry(settings, tracer=tracer, meter=meter))
    return tracer, meter


def run_once(system):
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="otel", objective="Create an educational post", topic="KNX"))
    assert system.worker.process_once() is True
    return run["id"], system.run_service.get(run["id"])


def test_mock_run_produces_genai_spans_and_metrics(tmp_path, fakes):
    tracer, meter = fakes
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    run_id, state = run_once(system)

    assert state["status"] == RunStatus.WAITING_HUMAN.value
    (workflow,) = tracer.named("invoke_workflow")
    assert workflow.attributes["gen_ai.operation.name"] == "invoke_workflow"
    assert workflow.attributes["multiagent.run_id"] == run_id
    assert workflow.ended and workflow.attributes["multiagent.run.status"] == "waiting_human"

    (step,) = tracer.named("invoke_agent")
    assert step.parent is workflow and step.ended
    assert step.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert step.attributes["multiagent.step_id"] == step.attributes["gen_ai.agent.id"]
    assert step.attributes["multiagent.step.outcome"] == "completed"
    event_types = {event["id"]: event["event_type"] for event in system.store.get_run(run_id)["events"]}
    assert event_types[step.attributes["multiagent.start_event_id"]] == "step_started"
    assert event_types[step.attributes["multiagent.end_event_id"]] == "step_completed"

    (call,) = tracer.named("chat")
    record = read_usage(system.store, run_id)[0]
    assert call.parent is step and call.ended
    assert call.attributes["gen_ai.operation.name"] == "chat"
    assert call.attributes["gen_ai.provider.name"] == record["provider"] == "fake"
    assert call.attributes["gen_ai.request.model"] == record["model"]
    assert call.attributes["gen_ai.usage.input_tokens"] == record["tokens_in"]
    assert call.attributes["gen_ai.usage.output_tokens"] == record["tokens_out"]
    assert call.status is None
    assert "gen_ai.input.messages" not in call.attributes and "gen_ai.output.messages" not in call.attributes

    usage = meter.histograms["gen_ai.client.token.usage"].points
    assert {(point[1]["gen_ai.token.type"], point[0]) for point in usage} == {
        ("input", record["tokens_in"]), ("output", record["tokens_out"])}
    assert meter.histograms["gen_ai.client.operation.duration"].points[0][0] == record["duration_ms"] / 1000


def test_mock_backend_emits_one_run_step_and_chat_span(tmp_path, fakes):
    tracer, meter = fakes
    system = build_system(settings_for(tmp_path, execution_backend="mock", telemetry_otel_enabled=True), dry_run=True)
    assert system.backend.telemetry is not None
    assert system.engine.telemetry is None
    assert len(system.store.db.event_listeners) == 1
    assert system.engine.usage_recorder.observers == []
    assert len(system.backend.usage_recorder.observers) == 1

    run_id, state = run_once(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    (workflow,) = tracer.named("invoke_workflow")
    (step,) = tracer.named("invoke_agent")
    (call,) = tracer.named("chat")
    assert tracer.named("execute_tool") == []
    assert all(span.ended for span in tracer.spans)
    assert workflow.attributes["multiagent.run_id"] == run_id
    assert workflow.attributes["gen_ai.operation.name"] == "invoke_workflow"
    assert workflow.attributes["multiagent.run.status"] == "waiting_human"
    assert step.parent is workflow and call.parent is step
    assert step.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert step.attributes["multiagent.step_id"] == step.attributes["gen_ai.agent.id"]
    assert step.attributes["multiagent.step.outcome"] == "completed"
    event_types = {event["id"]: event["event_type"] for event in state["events"]}
    assert event_types[step.attributes["multiagent.start_event_id"]] == "step_started"
    assert event_types[step.attributes["multiagent.end_event_id"]] == "step_completed"
    (record,) = read_usage(system.store, run_id)
    assert call.attributes["gen_ai.operation.name"] == "chat"
    assert call.attributes["gen_ai.provider.name"] == record["provider"] == "mock"
    assert call.attributes["gen_ai.request.model"] == record["model"] == "mock"
    assert call.attributes["gen_ai.usage.input_tokens"] == record["tokens_in"] == 0
    assert call.attributes["gen_ai.usage.output_tokens"] == record["tokens_out"] == 0
    assert len(meter.histograms["gen_ai.client.token.usage"].points) == 2

    system.backend.process_run(run_id)
    assert len(tracer.named("invoke_workflow")) == 1


def test_bundle_engine_keeps_traces(tmp_path, fakes):
    tracer, _ = fakes
    settings = settings_for(tmp_path, telemetry_otel_enabled=True)
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(Path(__file__).parent / "fixtures/bundles/spec_review", settings)
    engine = WorkflowEngine.from_bundle(bundle, store=store, settings=settings,
                                        artifacts=ArtifactWriter(settings), adapters={}, dry_run=True)
    assert engine.telemetry is not None
    assert len(store.db.event_listeners) == 1
    assert len(engine.usage_recorder.observers) == 1
    service = RunService(settings, store, engine=engine)
    run = service.create_run(RunRequest(
        project_name="otel", workflow_id="spec_review",
        inputs={"spec_text": "Users can reset a password by email; links expire after 30 minutes."},
    ))
    state = engine.process_run(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    (workflow,) = tracer.named("invoke_workflow")
    steps = tracer.named("invoke_agent")
    calls = tracer.named("chat")
    assert len(steps) == len(calls) == 2
    assert all(step.parent is workflow for step in steps)
    assert all(call.parent in steps for call in calls)
    assert workflow.ended and all(span.ended for span in steps + calls)


def test_registered_backend_uses_context_store_for_traces(tmp_path, fakes):
    tracer, _ = fakes
    store = SimpleNamespace(
        db=SimpleNamespace(event_listeners=[]),
        get_run=lambda run_id: {"id": run_id, "workflow_id": "custom", "status": "queued"},
    )

    class CustomBackend:
        name = "custom"
        capabilities = REQUIRED_CAPABILITIES

        def __init__(self):
            self.usage_recorder = SimpleNamespace(observers=[])

        def preflight(self, workflow_id):
            return None

        def process_run(self, run_id):
            for listener in store.db.event_listeners:
                listener(run_id, "step_started", {"step_id": "s1", "attempt": 1}, 1)
                listener(run_id, "step_completed", {"step_id": "s1", "attempt": 1}, 2)
            return {"id": run_id, "status": "completed"}

        def submit_human_step(self, run_id, submission):
            return {}

        def review_point(self, run):
            return None, {}

        def check_approval_deadlines(self, *, run_id=None):
            return 0

    register_backend("custom", lambda context: CustomBackend())
    try:
        context = BackendContext(store=store, settings=settings_for(tmp_path, telemetry_otel_enabled=True),
                                 workflows=None, prompts=None, catalog=None, artifacts=None)
        backend = build_backend("custom", context)
        assert not hasattr(backend, "store")
        assert len(store.db.event_listeners) == len(backend.usage_recorder.observers) == 1
        assert backend.process_run("run-1")["status"] == "completed"
    finally:
        unregister_backend("custom")
    (workflow,) = tracer.named("invoke_workflow")
    (step,) = tracer.named("invoke_agent")
    assert step.parent is workflow and step.ended and workflow.ended


def test_prompts_and_responses_only_with_the_content_setting(tmp_path, fakes):
    tracer, _ = fakes
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True, telemetry_otel_capture_content=True),
                          dry_run=True)
    run_once(system)
    (call,) = tracer.named("chat")
    prompt = json.loads(call.attributes["gen_ai.input.messages"])
    assert prompt[0]["role"] == "user" and prompt[0]["parts"][0]["content"]
    assert json.loads(call.attributes["gen_ai.output.messages"])[0]["role"] == "assistant"


def test_redacted_egress_is_the_only_prompt_exported(tmp_path, fakes):
    tracer, _ = fakes
    provider = Provider("ollama")
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True,
                                       telemetry_otel_capture_content=True, egress_default="redact"),
                          adapter_overrides={"ollama": provider})
    system.catalog.models["local_default"] = replace(system.catalog.models["local_default"], tier="cloud")
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="otel", objective="Write a post", topic="ana@example.com"))
    assert system.worker.process_once()
    (call,) = tracer.named("chat")
    exported = call.attributes["gen_ai.input.messages"]
    assert "ana@example.com" not in provider.prompts[0]
    assert "ana@example.com" not in exported
    assert "[REDACTED:email:1]" in provider.prompts[0] and "[REDACTED:email:1]" in exported


class Unavailable(FakeAdapter):
    provider = "ollama"

    def generate_structured(self, **kwargs):
        raise ProviderUnavailable("offline")


class GeminiFake(FakeAdapter):
    provider = "gemini"


def test_failed_provider_attempt_is_an_error_span(tmp_path, fakes):
    tracer, meter = fakes
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True),
                          adapter_overrides={"ollama": Unavailable(), "gemini": GeminiFake()})
    run_once(system)
    failed, ok = tracer.named("chat")
    assert failed.attributes["error.type"] == "ProviderUnavailable" and failed.status is not None
    assert failed.status.status_code == trace.StatusCode.ERROR
    assert "gen_ai.usage.input_tokens" not in failed.attributes
    assert ok.status is None
    assert any(p[1].get("error.type") == "ProviderUnavailable"
               for p in meter.histograms["gen_ai.client.operation.duration"].points)


def test_tool_events_become_execute_tool_spans():
    tracer = FakeTracer()
    telemetry = RunTelemetry(tracer, None, set_span_in_context=trace.set_span_in_context,
                           error_status=lambda text: trace.Status(trace.StatusCode.ERROR, text))
    telemetry.begin_run("r1", "wf")
    base = {"request_id": "q1", "tool_id": "mock.echo", "tool_version": 1, "step_id": "s1", "attempt": 1}
    telemetry.on_event("r1", "tool_call_requested", base, 2)
    telemetry.on_event("r1", "tool_call_completed", {**base, "duration_ms": 5}, 3)
    telemetry.on_event("r1", "tool_call_requested", {**base, "request_id": "q2"}, 4)
    telemetry.on_event("r1", "tool_call_failed", {**base, "request_id": "q2", "code": "timeout"}, 5)
    telemetry.on_event("r1", "tool_call_requested", {**base, "request_id": "q3"}, 6)
    telemetry.on_event("r1", "tool_call_denied", {**base, "request_id": "q4", "code": "unknown_tool"}, 7)
    telemetry.on_event("r1", "step_started", {"step_id": "s1", "attempt": 1}, 8)
    telemetry.on_event("r1", "step_completed", {"step_id": "s1", "attempt": 1}, 9)
    telemetry.end_run("r1", "failed")

    first, second, dangling, denied = tracer.named("execute_tool")
    assert first.parent is tracer.named("invoke_agent")[0]
    assert len(tracer.named("invoke_agent")) == 1
    assert first.parent.attributes["multiagent.start_event_id"] == 8
    assert first.attributes["gen_ai.operation.name"] == "execute_tool"
    assert first.attributes["gen_ai.tool.name"] == "mock.echo"
    assert (first.attributes["multiagent.start_event_id"], first.attributes["multiagent.end_event_id"]) == (2, 3)
    assert first.status is None and first.ended
    assert second.attributes["error.type"] == "timeout" and second.status is not None
    assert denied.attributes["error.type"] == "unknown_tool" and denied.status is not None
    assert (denied.attributes["multiagent.start_event_id"], denied.attributes["multiagent.end_event_id"]) == (7, 7)
    assert dangling.ended and dangling.attributes["error.type"] == "run_ended"
    assert all(span.ended for span in tracer.spans)
    assert tracer.named("invoke_workflow")[0].attributes["error.type"] == "failed"


def test_tool_waiting_for_approval_is_not_an_error_span():
    tracer = FakeTracer()
    telemetry = RunTelemetry(tracer, None, set_span_in_context=trace.set_span_in_context,
                             error_status=lambda text: trace.Status(trace.StatusCode.ERROR, text))
    telemetry.begin_run("r1", "wf")
    telemetry.on_event("r1", "tool_call_requested",
                       {"request_id": "q1", "tool_id": "mock.write", "step_id": "s1", "attempt": 1}, 1)
    telemetry.end_run("r1", "waiting_human")
    (tool,) = tracer.named("execute_tool")
    assert tool.ended and tool.status is None
    assert tool.attributes["multiagent.tool.outcome"] == "waiting_human"
    assert "error.type" not in tool.attributes
    (step,) = tracer.named("invoke_agent")
    assert tool.parent is step and step.ended and step.attributes["multiagent.step.outcome"] == "waiting_human"


def test_real_workflow_tool_span_has_one_step_parent(tmp_path, fakes):
    tracer, _ = fakes
    settings = settings_for(tmp_path, telemetry_otel_enabled=True)
    system = build_system(settings, dry_run=True)
    folder = tmp_path / "workflows"
    folder.mkdir()
    document = yaml.safe_load((Path(settings.workflows_dir) / "social_post_full.yaml").read_text(encoding="utf-8"))
    next(step for step in document["steps"] if step["id"] == "strategy")["tools"] = [
        {"tool": "mock.echo", "arguments": {"text": "${topic}"}}]
    (folder / "social_post_full.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    system.engine.workflows = WorkflowCatalog(settings, workflows_dir=folder)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="otel", objective="Create a post", topic="KNX", pipeline_mode="full"))
    assert system.engine.process_run(run["id"])
    events = system.store.get_run(run["id"])["events"]
    tool_event = next(e for e in events if e["event_type"] == "tool_call_requested")
    step_event = next(e for e in events if e["event_type"] == "step_started" and e["payload"]["step_id"] == "strategy")
    assert tool_event["id"] < step_event["id"]
    (tool,) = tracer.named("execute_tool")
    strategy = [span for span in tracer.named("invoke_agent") if span.attributes["multiagent.step_id"] == "strategy"]
    assert len(strategy) == 1 and tool.parent is strategy[0]
    assert strategy[0].attributes["multiagent.start_event_id"] == step_event["id"]


def test_sdk_providers_reuse_host_or_one_private_pair(tmp_path, monkeypatch):
    from opentelemetry import metrics

    created = {"trace": 0, "meter": 0}

    class SDKTraceProvider:
        def __init__(self, **kwargs):
            created["trace"] += 1
            self.tracer = FakeTracer()

        def add_span_processor(self, processor):
            pass

        def get_tracer(self, name):
            return self.tracer

    class SDKMeterProvider:
        def __init__(self, **kwargs):
            created["meter"] += 1
            self.meter = FakeMeter()

        def get_meter(self, name):
            return self.meter

    def module(name, **members):
        result = types.ModuleType(name)
        result.__dict__.update(members)
        result.__path__ = []
        monkeypatch.setitem(sys.modules, name, result)

    module("opentelemetry.sdk")
    module("opentelemetry.sdk.trace", TracerProvider=SDKTraceProvider)
    module("opentelemetry.sdk.metrics", MeterProvider=SDKMeterProvider)
    module("opentelemetry.sdk.resources", Resource=types.SimpleNamespace(create=lambda attrs: attrs))
    module("opentelemetry.sdk.trace.export", BatchSpanProcessor=lambda exporter: exporter)
    module("opentelemetry.sdk.metrics.export", PeriodicExportingMetricReader=lambda exporter: exporter)
    module("opentelemetry.exporter")
    module("opentelemetry.exporter.otlp")
    module("opentelemetry.exporter.otlp.proto")
    module("opentelemetry.exporter.otlp.proto.http")
    module("opentelemetry.exporter.otlp.proto.http.trace_exporter", OTLPSpanExporter=lambda: object())
    module("opentelemetry.exporter.otlp.proto.http.metric_exporter", OTLPMetricExporter=lambda: object())
    monkeypatch.setattr(otel, "_private_providers", (None, None))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: object())
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: object())
    settings = settings_for(tmp_path, telemetry_otel_enabled=True)
    assert build_telemetry(settings) is not None
    assert build_telemetry(settings) is not None
    assert created == {"trace": 1, "meter": 1}
    private = otel._private_providers
    assert otel._sdk_providers() == private

    host = SDKTraceProvider(), SDKMeterProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: host[0])
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: host[1])
    assert otel._sdk_providers() == host
    telemetry = build_telemetry(settings)
    assert telemetry is not None and telemetry._tracer is host[0].tracer
    assert "gen_ai.client.token.usage" in host[1].meter.histograms
    assert created == {"trace": 2, "meter": 2}


def test_a_failing_tracer_never_touches_the_run(tmp_path, monkeypatch):
    class Broken(FakeTracer):
        def start_span(self, *args, **kwargs):
            raise RuntimeError("exporter down")

    monkeypatch.setattr("multiagent.backends.telemetry.build_telemetry",
                        lambda settings: build_telemetry(settings, tracer=Broken(), meter=FakeMeter()))
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    run_id, state = run_once(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert len(read_usage(system.store, run_id)) == 1


def test_sdk_in_memory_exporter_receives_the_spans(tmp_path, monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("multiagent.backends.telemetry.build_telemetry",
                        lambda settings: build_telemetry(settings, tracer=provider.get_tracer("t"), meter=None))
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    run_once(system)
    spans = {span.name.split(" ")[0]: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"invoke_workflow", "invoke_agent", "chat"}
    assert spans["chat"].parent.span_id == spans["invoke_agent"].context.span_id
    assert spans["chat"].attributes["gen_ai.usage.output_tokens"] > 0
