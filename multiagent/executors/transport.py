# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Transport interface and a result-preserving wrapper for headless CLI adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Any, Literal, Mapping, Protocol

from . import process
from .contract import ExecutorApplicationAdapter, ExecutorLaunchRequest, ExecutorRunResult, RunStatus


class TransportKind(str, Enum):
    CLI = "cli"
    ACP = "acp"


@dataclass(frozen=True)
class TransportCapabilities:
    multi_turn: bool
    cancel: bool
    live_events: bool
    permission_requests: bool


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["message", "thought", "tool", "diff", "plan", "usage", "end_turn", "error"]
    tool_status: Literal["pending", "in_progress", "completed", "failed"] | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionAsk:
    tool: str
    target: str | None
    options: tuple[str, ...]


class EventSink(Protocol):
    def on_event(self, event: AgentEvent) -> None: ...

    def on_permission(self, ask: PermissionAsk) -> str: ...


class AgentSession(Protocol):
    session_id: str | None

    def prompt(self, text: str) -> AgentEvent: ...

    def cancel(self) -> bool: ...

    def close(self) -> ExecutorRunResult: ...


class AgentTransport(Protocol):
    kind: TransportKind

    def capabilities(self) -> TransportCapabilities: ...

    def open_session(self, request: ExecutorLaunchRequest, sink: EventSink) -> AgentSession: ...


_RESUMABLE = frozenset({"claude_cli", "codex_cli", "copilot_cli"})
_USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
                         "cache_read_input_tokens", "cache_creation_input_tokens", "premium_requests"})


class CliTransport:
    """Run a validated task envelope with the existing adapter, without replacing its result."""

    kind = TransportKind.CLI

    def __init__(self, adapter: ExecutorApplicationAdapter):
        self.adapter = adapter

    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(multi_turn=self.adapter.adapter_id in _RESUMABLE,
                                     cancel=True, live_events=False, permission_requests=False)

    def open_session(self, request: ExecutorLaunchRequest, sink: EventSink) -> AgentSession:
        return _CliSession(self.adapter, request, sink)


class _CliSession:
    def __init__(self, adapter: ExecutorApplicationAdapter, request: ExecutorLaunchRequest,
                 sink: EventSink):
        self._adapter = adapter
        self._request = request
        self._sink = sink
        self._result: ExecutorRunResult | None = None
        self._closed = False
        self._active = False
        self._handle: process.ProcessHandle | None = None
        self._lock = Lock()
        self.session_id: str | None = None

    def prompt(self, text: str) -> AgentEvent:
        if not isinstance(text, str) or text != self._adapter.render_prompt(self._request):
            raise ValueError("CLI prompt must be rendered from the validated task envelope")
        with self._lock:
            if self._closed or self._active:
                raise RuntimeError("CLI session is closed or already running")
            if self._result is not None and self.session_id is None:
                raise RuntimeError("CLI session cannot resume without a framework-created session id")
            self._active = True
        try:
            with process.capturing(self._capture):
                if self._result is None:
                    result = self._adapter.launch(self._request)
                else:
                    result = self._adapter.resume(self._request, self.session_id)
            with self._lock:
                self._result = result
                if self.session_id is None:
                    self.session_id = result.session_id
        finally:
            with self._lock:
                self._active = False
                self._handle = None
        if result.usage is not None:
            counters = {key: value for key, value in result.usage.items()
                        if key in _USAGE_KEYS and isinstance(value, (int, float))
                        and not isinstance(value, bool) and value >= 0}
            if counters:
                self._sink.on_event(AgentEvent("usage", data=counters))
        event = AgentEvent("end_turn" if result.status is RunStatus.COMPLETED else "error",
                           data={"status": result.status.value})
        self._sink.on_event(event)
        return event

    def _capture(self, handle: process.ProcessHandle) -> None:
        with self._lock:
            self._handle = handle

    def cancel(self) -> bool:
        with self._lock:
            handle = self._handle if self._active and not self._closed else None
        return handle.cancel() if handle is not None else False

    def close(self) -> ExecutorRunResult:
        with self._lock:
            if self._active:
                raise RuntimeError("CLI session is still running")
            self._closed = True
        if self._result is None:
            raise RuntimeError("CLI session has no result")
        return self._result
