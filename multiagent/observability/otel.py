# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OpenTelemetry traces and metrics of execution backends (CAP-T21, roadmap S10.B1, partial).

Off by default (``TELEMETRY_OTEL_ENABLED``). Nothing here imports ``opentelemetry`` at module level: with the setting
off, or without the optional ``otel`` extra, a backend builds no telemetry object and no OpenTelemetry module is
imported. Spans follow the domain events and usage records that already exist, they do not replace them:

* ``invoke_workflow`` span for each ``process_run`` pass of a run (a run that pauses and resumes has one per pass);
* ``invoke_agent`` span for each workflow step attempt, opened by its first tool event or ``step_started``, closed by ``step_completed``;
* ``chat`` span for each provider call, built from its ``TokenUsageRecord`` (the same numbers the local store keeps);
* ``execute_tool`` span for each ``tool_call_*`` sequence of ``ToolInvoker``.

Attributes use the GenAI semantic conventions (``gen_ai.*``); ``multiagent.*`` attributes carry the ids of the domain
events (``run_id``, ``step_id``, ``attempt``, the ``run_events`` id that opened and closed the span). Prompts and
responses are never attached unless ``TELEMETRY_OTEL_CAPTURE_CONTENT`` is on. Two histograms record token usage
(``gen_ai.client.token.usage``) and call duration (``gen_ai.client.operation.duration``).

The exporter is the OTLP one, configured by the standard ``OTEL_*`` variables; a provider already configured by the host
application is used as it is. Everything is best effort: a failure here is logged and never touches a run.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Mapping

_logger = logging.getLogger(__name__)

INSTRUMENTATION_NAME = "multiagent"
DEFAULT_SERVICE_NAME = "multi-agent-framework"
OP_WORKFLOW = "invoke_workflow"
OP_AGENT = "invoke_agent"
OP_CHAT = "chat"
OP_TOOL = "execute_tool"
_TOOL_START = "tool_call_requested"
_TOOL_END = {"tool_call_completed": None, "tool_call_failed": "failed", "tool_call_denied": "denied"}
_MAX_CONTENT_CHARS = 32_000
_provider_lock = threading.Lock()
_private_providers: tuple[Any, Any] = (None, None)


class RunTelemetry:
    """Turns run events and usage records into spans and metrics on a tracer and a meter of the OpenTelemetry API."""

    def __init__(self, tracer: Any, meter: Any | None, *, capture_content: bool = False,
                 error_status: Any = None, set_span_in_context: Any = None) -> None:
        self._tracer = tracer
        self._capture_content = capture_content
        self._error_status = error_status
        self._set_span_in_context = set_span_in_context
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._content: dict[tuple[str, str, int], tuple[str, str]] = {}
        self._token_usage = self._duration = None
        if meter is not None:
            self._token_usage = meter.create_histogram(
                "gen_ai.client.token.usage", unit="{token}", description="Input and output tokens per provider call")
            self._duration = meter.create_histogram(
                "gen_ai.client.operation.duration", unit="s", description="Duration of a provider call")

    # ---------- helpers ----------
    def _start(self, name: str, parent: Any, attributes: Mapping[str, Any], *, start_time: int | None = None) -> Any:
        kwargs: dict[str, Any] = {"attributes": {k: v for k, v in attributes.items() if v is not None}}
        if parent is not None and self._set_span_in_context is not None:
            kwargs["context"] = self._set_span_in_context(parent)
        if start_time is not None:
            kwargs["start_time"] = start_time
        return self._tracer.start_span(name, **kwargs)

    def _end(self, span: Any, *, error: str | None = None, attributes: Mapping[str, Any] | None = None,
             end_time: int | None = None) -> None:
        try:
            for key, value in (attributes or {}).items():
                if value is not None:
                    span.set_attribute(key, value)
            if error is not None:
                span.set_attribute("error.type", error)
                if self._error_status is not None:
                    span.set_status(self._error_status(error))
            if end_time is not None:
                span.end(end_time)
            else:
                span.end()
        except Exception:
            _logger.warning("Could not close an OpenTelemetry span", exc_info=True)

    # ---------- the run ----------
    def begin_run(self, run_id: str, workflow_id: str) -> None:
        try:
            span = self._start(f"{OP_WORKFLOW} {workflow_id}", None, {
                "gen_ai.operation.name": OP_WORKFLOW, "gen_ai.workflow.name": workflow_id,
                "multiagent.run_id": run_id})
            with self._lock:
                self._runs[run_id] = {"span": span, "step": None, "tools": {}}
        except Exception:
            _logger.warning("Could not open an OpenTelemetry run span", exc_info=True)

    def end_run(self, run_id: str, status: str) -> None:
        with self._lock:
            state = self._runs.pop(run_id, None)
            for key in [key for key in self._content if key[0] == run_id]:
                del self._content[key]
        if state is None:
            return
        try:
            for span in list(state["tools"].values()):
                self._end(span, error=None if status == "waiting_human" else "run_ended",
                          attributes={"multiagent.tool.outcome": "waiting_human"} if status == "waiting_human" else None)
            if state["step"] is not None:
                self._end(state["step"]["span"], error="failed" if status == "failed" else None,
                          attributes={"multiagent.step.outcome": status})
            failed = status in ("failed", "cancelled")
            # The run's error message may contain provider output or other sensitive text.
            # Export only the stable outcome as an error type.
            self._end(state["span"], error=status if failed else None,
                      attributes={"multiagent.run.status": status})
        except Exception:
            _logger.warning("Could not close the OpenTelemetry run span", exc_info=True)

    # ---------- domain events ----------
    def on_event(self, run_id: str, event_type: str, payload: Mapping[str, Any], event_id: int | None) -> None:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return
            if event_type == "step_started":
                self._step_started(run_id, state, payload, event_id)
            elif event_type == "step_completed":
                self._step_closed(state, payload, event_id)
            elif event_type == _TOOL_START:
                self._tool_started(run_id, state, payload, event_id)
            elif event_type in _TOOL_END:
                self._tool_ended(run_id, state, event_type, payload, event_id)

    def _step_started(self, run_id: str, state: dict[str, Any], payload: Mapping[str, Any], event_id: int | None) -> None:
        step_id = str(payload.get("step_id", ""))
        attempt = payload.get("attempt")
        step = state["step"]
        if step is not None and step["step_id"] == step_id and step["attempt"] == attempt and not step["started"]:
            step["started"] = True
            for key, value in {
                "multiagent.execution_mode": payload.get("execution_mode"),
                "multiagent.prompt_id": payload.get("prompt_id"),
                "multiagent.start_event_id": event_id,
            }.items():
                if value is not None:
                    step["span"].set_attribute(key, value)
            return
        if step is not None:
            self._end(step["span"], attributes={"multiagent.step.outcome": "superseded"})
        self._open_step(run_id, state, step_id, attempt, event_id, payload, started=True)

    def _open_step(self, run_id: str, state: dict[str, Any], step_id: str, attempt: Any,
                   event_id: int | None, payload: Mapping[str, Any], *, started: bool) -> None:
        span = self._start(f"{OP_AGENT} {step_id}", state["span"], {
            "gen_ai.operation.name": OP_AGENT, "gen_ai.agent.id": step_id, "multiagent.run_id": run_id,
            "multiagent.step_id": step_id, "multiagent.attempt": attempt,
            "multiagent.execution_mode": payload.get("execution_mode"),
            "multiagent.prompt_id": payload.get("prompt_id"), "multiagent.start_event_id": event_id})
        state["step"] = {"span": span, "step_id": step_id, "attempt": attempt, "started": started}

    def _tool_parent(self, run_id: str, state: dict[str, Any], payload: Mapping[str, Any],
                     event_id: int | None) -> Any:
        step_id, attempt = payload.get("step_id"), payload.get("attempt")
        if step_id is not None and attempt is not None:
            step = state["step"]
            if step is None or step["step_id"] != str(step_id) or step["attempt"] != attempt:
                if step is not None:
                    self._end(step["span"], attributes={"multiagent.step.outcome": "superseded"})
                self._open_step(run_id, state, str(step_id), attempt, event_id, payload, started=False)
            return state["step"]["span"]
        return state["step"]["span"] if state["step"] is not None else state["span"]

    def _step_closed(self, state: dict[str, Any], payload: Mapping[str, Any], event_id: int | None) -> None:
        step = state["step"]
        if step is None or step["step_id"] != payload.get("step_id"):
            return
        state["step"] = None
        self._end(step["span"], attributes={"multiagent.step.outcome": "completed", "multiagent.end_event_id": event_id})

    def _tool_started(self, run_id: str, state: dict[str, Any], payload: Mapping[str, Any], event_id: int | None) -> None:
        request_id = str(payload.get("request_id", ""))
        if not request_id or request_id in state["tools"]:
            return
        tool_id = str(payload.get("tool_id", ""))
        parent = self._tool_parent(run_id, state, payload, event_id)
        state["tools"][request_id] = self._start(f"{OP_TOOL} {tool_id}", parent, {
            "gen_ai.operation.name": OP_TOOL, "gen_ai.tool.name": tool_id, "gen_ai.tool.type": "function",
            "gen_ai.tool.call.id": request_id, "multiagent.run_id": run_id,
            "multiagent.step_id": payload.get("step_id"), "multiagent.attempt": payload.get("attempt"),
            "multiagent.tool.version": payload.get("tool_version"), "multiagent.start_event_id": event_id})

    def _tool_ended(self, run_id: str, state: dict[str, Any], event_type: str,
                    payload: Mapping[str, Any], event_id: int | None) -> None:
        request_id = str(payload.get("request_id", ""))
        span = state["tools"].pop(request_id, None)
        if span is None:
            # The invoker may reject a call before emitting tool_call_requested.
            # Its terminal event is then the only domain event for the call.
            if not request_id:
                return
            tool_id = str(payload.get("tool_id", ""))
            parent = self._tool_parent(run_id, state, payload, event_id)
            span = self._start(f"{OP_TOOL} {tool_id}", parent, {
                "gen_ai.operation.name": OP_TOOL, "gen_ai.tool.name": tool_id, "gen_ai.tool.type": "function",
                "gen_ai.tool.call.id": request_id, "multiagent.run_id": run_id,
                "multiagent.step_id": payload.get("step_id"), "multiagent.attempt": payload.get("attempt"),
                "multiagent.tool.version": payload.get("tool_version"), "multiagent.start_event_id": event_id})
        error = _TOOL_END[event_type]
        if error is not None:
            error = str(payload.get("code") or error)
        self._end(span, error=error, attributes={
            "multiagent.tool.duration_ms": payload.get("duration_ms"), "multiagent.end_event_id": event_id})

    # ---------- provider calls ----------
    def hold_content(self, run_id: str, step_id: str, attempt: int, prompt: str, response: str) -> None:
        """Keep the prompt and the response of a call until its usage record arrives; a no-op unless content capture is on."""
        if self._capture_content:
            with self._lock:
                self._content[(run_id, step_id, attempt)] = (prompt, response)

    def on_usage(self, record: Any) -> None:
        """One ``chat`` span and the two histograms for a stored ``TokenUsageRecord``; human steps make no provider call."""
        if record.provider == "human":
            return
        try:
            self._on_usage(record)
        except Exception:
            _logger.warning("Could not export the OpenTelemetry span of a provider call", exc_info=True)

    def _on_usage(self, record: Any) -> None:
        with self._lock:
            state = self._runs.get(record.run_id)
            parent = None
            if state is not None:
                step = state["step"]
                parent = step["span"] if step is not None and step["step_id"] == record.step_id else state["span"]
            content = self._content.pop((record.run_id, record.step_id, record.attempt), None)
        end = time.time_ns()
        start = end - int(record.duration_ms or 0) * 1_000_000
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": OP_CHAT, "gen_ai.provider.name": record.provider,
            "gen_ai.request.model": record.model, "gen_ai.response.model": record.model,
            "gen_ai.agent.id": record.agent_id, "gen_ai.usage.input_tokens": record.tokens_in,
            "gen_ai.usage.output_tokens": record.tokens_out,
            "gen_ai.usage.cache_read.input_tokens": record.cache_read_tokens,
            "multiagent.run_id": record.run_id, "multiagent.step_id": record.step_id,
            "multiagent.attempt": record.attempt, "multiagent.prompt_id": record.prompt_id,
            "multiagent.prompt_version": record.prompt_version,
            "multiagent.estimated_cost_usd": record.estimated_cost_usd,
        }
        if content is not None:
            attributes["gen_ai.input.messages"] = json.dumps(
                [{"role": "user", "parts": [{"type": "text", "content": content[0][:_MAX_CONTENT_CHARS]}]}])
            attributes["gen_ai.output.messages"] = json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": content[1][:_MAX_CONTENT_CHARS]}]}])
        span = self._start(f"{OP_CHAT} {record.model}", parent, attributes, start_time=start)
        error = None if record.success else (record.error_type or "UnknownError")
        self._end(span, error=error, end_time=end)
        shared = {"gen_ai.operation.name": OP_CHAT, "gen_ai.provider.name": record.provider,
                  "gen_ai.request.model": record.model}
        if error is not None:
            shared["error.type"] = error
        if self._token_usage is not None:
            for kind, count in (("input", record.tokens_in), ("output", record.tokens_out)):
                if count is not None:
                    self._token_usage.record(count, {**shared, "gen_ai.token.type": kind})
        if self._duration is not None and record.duration_ms is not None:
            self._duration.record(record.duration_ms / 1000, shared)


def build_telemetry(settings: Any, *, tracer: Any = None, meter: Any = None) -> RunTelemetry | None:
    """A backend's telemetry, or ``None`` when the setting is off or the OpenTelemetry API is not installed.

    ``tracer`` and ``meter`` are the seam for a host (and the tests): they replace the providers built here.
    """
    if not getattr(settings, "telemetry_otel_enabled", False):
        return None
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.trace import Status, StatusCode
    except ImportError:
        _logger.warning("TELEMETRY_OTEL_ENABLED is on but opentelemetry-api is not installed "
                        "(pip install 'multi-agent-framework[otel]'); no traces are exported")
        return None
    try:
        if tracer is None:
            provider, meter_provider = _sdk_providers()
            tracer = (provider or trace.get_tracer_provider()).get_tracer(INSTRUMENTATION_NAME)
            meter = (meter_provider or metrics.get_meter_provider()).get_meter(INSTRUMENTATION_NAME)
        return RunTelemetry(
            tracer, meter, capture_content=bool(getattr(settings, "telemetry_otel_capture_content", False)),
            error_status=lambda description: Status(StatusCode.ERROR, description),
            set_span_in_context=trace.set_span_in_context)
    except Exception:
        _logger.warning("OpenTelemetry could not be set up; no traces are exported", exc_info=True)
        return None


def _sdk_providers() -> tuple[Any, Any]:
    """Use host SDK providers, or share private OTLP providers across engine instances."""
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return None, None
    global _private_providers
    with _provider_lock:
        host_trace = trace.get_tracer_provider()
        host_meter = metrics.get_meter_provider()
        provider = host_trace if isinstance(host_trace, TracerProvider) else _private_providers[0]
        meter_provider = host_meter if isinstance(host_meter, MeterProvider) else _private_providers[1]
        if provider is None or meter_provider is None:
            try:
                from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
                from opentelemetry.sdk.resources import Resource
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
            except ImportError:
                return provider, meter_provider
            resource = Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE_NAME})
            private_trace, private_meter = _private_providers
            if provider is None:
                private_trace = TracerProvider(resource=resource)
                private_trace.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                provider = private_trace
            if meter_provider is None:
                private_meter = MeterProvider(resource=resource,
                                              metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
                meter_provider = private_meter
            _private_providers = private_trace, private_meter
        return provider, meter_provider
