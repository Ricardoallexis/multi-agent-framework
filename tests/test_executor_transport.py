# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Headless transport preserves adapter results and never advertises live output."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

import pytest

from multiagent.executors.contract import (CommandRecord, ExecutorLaunchRequest, ExecutorRunResult,
                                           FailureKind, LimitInfo, RunStatus)
from multiagent.executors.transport import CliTransport, TransportKind
from multiagent.executors import transport


IDS = ("claude_cli", "codex_cli", "copilot_cli", "antigravity_cli")


class Sink:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)

    def on_permission(self, ask):
        raise AssertionError("CLI must not request interactive permission")


class Adapter:
    version = "1"

    def __init__(self, adapter_id, first, second=None):
        self.adapter_id = adapter_id
        self.first = first
        self.second = second
        self.calls = []

    def render_prompt(self, request):
        return f"task:{request.task_id}"

    def launch(self, request):
        self.calls.append(("launch", request))
        return self.first

    def resume(self, request, session_id):
        self.calls.append(("resume", request, session_id))
        if self.adapter_id == "antigravity_cli":
            raise RuntimeError("resume_not_supported")
        return self.second


def _result(status=RunStatus.COMPLETED, session_id="framework-session"):
    return ExecutorRunResult(
        status=status, session_id=session_id, result_envelope={"summary": "synthetic"},
        usage={"input_tokens": 7, "secret": "do not emit"},
        command=CommandRecord(("fake", "--safe"), "work", "start", "end", 0),
        stdout_tail="private stdout", stderr_tail="private stderr", notes=("unchanged",),
        limit=LimitInfo(FailureKind.PLAN_LIMIT) if status is RunStatus.QUOTA_EXHAUSTED else None,
        failure_kind=FailureKind.PLAN_LIMIT if status is RunStatus.QUOTA_EXHAUSTED else None,
    )


def _request(tmp_path):
    return ExecutorLaunchRequest("T1", {"task_id": "T1"}, Path(tmp_path))


@pytest.mark.parametrize("adapter_id", IDS)
def test_first_prompt_is_exact_launch_result_and_only_bounded_post_run_events(tmp_path, adapter_id):
    direct = _result()
    adapter = Adapter(adapter_id, direct)
    sink = Sink()
    transport = CliTransport(adapter)
    assert transport.kind is TransportKind.CLI
    assert transport.capabilities().permission_requests is False
    assert transport.capabilities().live_events is False
    assert transport.capabilities().cancel is True
    assert transport.capabilities().multi_turn is (adapter_id != "antigravity_cli")
    request = _request(tmp_path)
    session = transport.open_session(request, sink)
    assert session.cancel() is False
    with pytest.raises(ValueError, match="validated task envelope"):
        session.prompt("arbitrary chat text")
    assert adapter.calls == [] and sink.events == []
    event = session.prompt(adapter.render_prompt(request))
    assert adapter.calls == [("launch", request)]
    assert session.session_id == direct.session_id
    assert [item.kind for item in sink.events] == ["usage", "end_turn"]
    assert sink.events[0].data == {"input_tokens": 7}
    assert event is sink.events[-1]
    assert session.close() is direct
    with pytest.raises(RuntimeError, match="closed"):
        session.prompt(adapter.render_prompt(request))


@pytest.mark.parametrize("adapter_id", IDS)
def test_resume_uses_only_id_from_first_launch_and_keeps_last_result(tmp_path, adapter_id):
    first, second = _result(), _result(RunStatus.QUOTA_EXHAUSTED)
    adapter = Adapter(adapter_id, first, second)
    request = _request(tmp_path)
    session = CliTransport(adapter).open_session(request, Sink())
    session.prompt(adapter.render_prompt(request))
    if adapter_id == "antigravity_cli":
        with pytest.raises(RuntimeError, match="resume_not_supported"):
            session.prompt(adapter.render_prompt(request))
        assert session.close() is first
    else:
        session.prompt(adapter.render_prompt(request))
        assert adapter.calls[-1] == ("resume", request, first.session_id)
        assert session.close() is second


def test_no_session_id_means_no_resume_and_close_returns_first_result(tmp_path):
    first = _result(session_id=None)
    adapter = Adapter("codex_cli", first)
    request = _request(tmp_path)
    session = CliTransport(adapter).open_session(request, Sink())
    session.prompt(adapter.render_prompt(request))
    with pytest.raises(RuntimeError, match="framework-created session id"):
        session.prompt(adapter.render_prompt(request))
    assert len(adapter.calls) == 1
    assert session.close() is first


def test_close_before_attempt_reports_missing_result(tmp_path):
    session = CliTransport(Adapter("codex_cli", _result())).open_session(_request(tmp_path), Sink())
    with pytest.raises(RuntimeError, match="no result"):
        session.close()


def test_cancel_uses_captured_process_handle(tmp_path, monkeypatch):
    started, cancelled = Event(), Event()
    callback = None

    @contextmanager
    def capture(value):
        nonlocal callback
        callback = value
        yield

    class Handle:
        def cancel(self):
            cancelled.set()
            return True

    class RunningAdapter(Adapter):
        def launch(self, request):
            callback(Handle())
            started.set()
            assert cancelled.wait(2)
            return _result(RunStatus.CANCELLED)

    monkeypatch.setattr(transport.process, "capturing", capture)
    adapter = RunningAdapter("codex_cli", None)
    request = _request(tmp_path)
    session = CliTransport(adapter).open_session(request, Sink())
    thread = Thread(target=session.prompt, args=(adapter.render_prompt(request),))
    thread.start()
    assert started.wait(2)
    assert session.cancel() is True
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert session.close().status is RunStatus.CANCELLED
