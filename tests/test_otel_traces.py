# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CAP-T21: OpenTelemetry spans and metrics of the native engine (off by default, optional extra)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from multiagent.adapters.fake import FakeAdapter
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.errors import ProviderUnavailable
from multiagent.observability import otel
from multiagent.observability.otel import RunTelemetry, build_telemetry
from multiagent.observability.usage import read_usage

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
    monkeypatch.setattr("multiagent.workflow_engine.build_telemetry",
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


def test_prompts_and_responses_only_with_the_content_setting(tmp_path, fakes):
    tracer, _ = fakes
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True, telemetry_otel_capture_content=True),
                          dry_run=True)
    run_once(system)
    (call,) = tracer.named("chat")
    prompt = json.loads(call.attributes["gen_ai.input.messages"])
    assert prompt[0]["role"] == "user" and prompt[0]["parts"][0]["content"]
    assert json.loads(call.attributes["gen_ai.output.messages"])[0]["role"] == "assistant"


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
    telemetry.on_event("r1", "step_started", {"step_id": "s1", "attempt": 1}, 1)
    base = {"request_id": "q1", "tool_id": "mock.echo", "tool_version": 1, "step_id": "s1", "attempt": 1}
    telemetry.on_event("r1", "tool_call_requested", base, 2)
    telemetry.on_event("r1", "tool_call_completed", {**base, "duration_ms": 5}, 3)
    telemetry.on_event("r1", "tool_call_requested", {**base, "request_id": "q2"}, 4)
    telemetry.on_event("r1", "tool_call_failed", {**base, "request_id": "q2", "code": "timeout"}, 5)
    telemetry.on_event("r1", "tool_call_requested", {**base, "request_id": "q3"}, 6)
    telemetry.on_event("r1", "tool_call_denied", {**base, "request_id": "q4", "code": "unknown_tool"}, 7)
    telemetry.end_run("r1", "failed")

    first, second, dangling, denied = tracer.named("execute_tool")
    assert first.parent is tracer.named("invoke_agent")[0]
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
    telemetry.on_event("r1", "step_started", {"step_id": "s1", "attempt": 1}, 1)
    telemetry.on_event("r1", "tool_call_requested",
                       {"request_id": "q1", "tool_id": "mock.write", "step_id": "s1", "attempt": 1}, 2)
    telemetry.end_run("r1", "waiting_human")
    (tool,) = tracer.named("execute_tool")
    assert tool.ended and tool.status is None
    assert tool.attributes["multiagent.tool.outcome"] == "waiting_human"
    assert "error.type" not in tool.attributes


def test_a_failing_tracer_never_touches_the_run(tmp_path, monkeypatch):
    class Broken(FakeTracer):
        def start_span(self, *args, **kwargs):
            raise RuntimeError("exporter down")

    monkeypatch.setattr("multiagent.workflow_engine.build_telemetry",
                        lambda settings: build_telemetry(settings, tracer=Broken(), meter=FakeMeter()))
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    run_id, state = run_once(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value
    assert len(read_usage(system.store, run_id)) == 1


def test_off_by_default_builds_nothing(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("OpenTelemetry must not be touched when the setting is off")

    monkeypatch.setattr(otel, "_sdk_providers", forbidden)
    assert build_telemetry(settings_for(tmp_path)) is None
    system = build_system(settings_for(tmp_path), dry_run=True)
    assert system.engine.telemetry is None
    assert system.store.db.event_listeners == [] and system.engine.usage_recorder.observers == []
    run_id, state = run_once(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value and len(read_usage(system.store, run_id)) == 1


def test_missing_package_with_the_setting_on_changes_nothing(tmp_path, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    with caplog.at_level("WARNING"):
        assert build_telemetry(settings_for(tmp_path, telemetry_otel_enabled=True)) is None
    assert "opentelemetry-api is not installed" in caplog.text
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    assert system.engine.telemetry is None
    assert run_once(system)[1]["status"] == RunStatus.WAITING_HUMAN.value


def test_run_with_the_setting_off_imports_no_opentelemetry(tmp_path):
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from multiagent.bootstrap import build_system\n"
        "from multiagent.config import Settings\n"
        "from multiagent.contracts import SocialPostRequest\n"
        f"root = Path({str(tmp_path / 'sub')!r})\n"
        "s = Settings(data_dir=root / 'd', db_path=root / 'd' / 'm.db', runs_dir=root / 'd' / 'r',\n"
        "             assets_dir=root / 'd' / 'a', worker_enabled=False, gemini_api_key='')\n"
        "system = build_system(s, dry_run=True)\n"
        "system.run_service.create_social_post(SocialPostRequest(project_name='xx', objective='Create a post', topic='KNX'))\n"
        "system.worker.process_once()\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'opentelemetry'))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120,
                          cwd=str(Path(__file__).resolve().parents[1]),
                          env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("[]"), done.stdout


def test_sdk_in_memory_exporter_receives_the_spans(tmp_path, monkeypatch):
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr("multiagent.workflow_engine.build_telemetry",
                        lambda settings: build_telemetry(settings, tracer=provider.get_tracer("t"), meter=None))
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    run_once(system)
    spans = {span.name.split(" ")[0]: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"invoke_workflow", "invoke_agent", "chat"}
    assert spans["chat"].parent.span_id == spans["invoke_agent"].context.span_id
    assert spans["chat"].attributes["gen_ai.usage.output_tokens"] > 0
