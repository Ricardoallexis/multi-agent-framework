# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Synthetic stdio ACP peer; no installed adapter or network is used."""
from __future__ import annotations

import json
import os
import sys
from threading import Event, Thread
import time
from pathlib import Path

import pytest

from multiagent.executors import acp, process, transport as transport_module
from multiagent.executors.contract import ExecutorLaunchRequest, FailureKind, Permission, RunStatus


FAKE = r'''
import json, os, sys, time
mode = os.environ["FAKE_MODE"]
log = os.environ["FAKE_LOG"]
prompts = 0
def send(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)
def receive():
    value = json.loads(sys.stdin.readline())
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")
    return value
while True:
    try:
        req = receive()
    except (EOFError, ValueError):
        break
    method = req.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":req["id"],"result":{
            "protocolVersion":2 if mode == "version" else 1,"agentCapabilities":{}}})
    elif method == "session/new":
        send({"jsonrpc":"2.0","id":req["id"],"result":{"sessionId":"fake-session"}})
    elif method == "session/prompt":
        prompts += 1
        if mode == "exit":
            sys.exit(2)
        if mode == "wait":
            receive()
            time.sleep(5)
            continue
        if mode == "cancel_grace":
            if prompts == 1:
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"usage_update",
                    "used":5,"size":20}}})
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"agent_message_chunk",
                    "content":{"type":"text","text":"{\"old\":true}"}}}})
                receive()
                send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"cancelled"}})
            else:
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"agent_message_chunk",
                    "content":{"type":"text","text":"{\"new\":true}"}}}})
                send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"end_turn"}})
            continue
        if mode == "multi_turn":
            if prompts == 1:
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"tool_call",
                    "toolCallId":"reused-id","kind":"read",
                    "locations":[{"path":os.path.join(os.getcwd(),"first.txt")}]}}})
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"usage_update",
                    "used":3,"size":10}}})
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"agent_message_chunk",
                    "content":{"type":"text","text":"{\"turn\":1}"}}}})
            elif prompts == 2:
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"agent_message_chunk",
                    "content":{"type":"text","text":"{\"turn\":2}"}}}})
            send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"end_turn"}})
            continue
        if mode == "pending_permission":
            if prompts > 1:
                send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"end_turn"}})
                continue
            send({"jsonrpc":"2.0","id":77,"method":"session/request_permission","params":{
                "sessionId":"fake-session","toolCall":{"toolCallId":"tool-2","kind":"edit"},
                "options":[{"optionId":"a","name":"Allow","kind":"allow_once"}]}})
            receive()
            receive()
            send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"cancelled"}})
            continue
        if mode == "timeout":
            time.sleep(5)
            continue
        if mode == "bad_json":
            print("not JSON", flush=True)
            continue
        if mode == "bad_id":
            send({"jsonrpc":"2.0","id":999,"result":{"stopReason":"end_turn"}})
            continue
        if mode == "error":
            send({"jsonrpc":"2.0","id":req["id"],"error":{"code":-32000,"message":"fake"}})
            continue
        if mode == "many":
            for n in range(4200):
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":"agent_message_chunk",
                    "content":{"type":"text","text":str(n)}}}})
            send({"jsonrpc":"2.0","id":req["id"],"result":{"stopReason":"end_turn"}})
            continue
        if mode == "usage":
            send({"jsonrpc":"2.0","method":"session/update","params":{
                "sessionId":"fake-session","update":{"sessionUpdate":"usage_update",
                "used":12,"size":100,"cost":{"amount":0.02,"currency":"USD"}}}})
            send({"jsonrpc":"2.0","method":"session/update","params":{
                "sessionId":"fake-session","update":{"sessionUpdate":"diff","content":{"text":"ignored"}}}})
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":"fake-session","update":{"sessionUpdate":"agent_message_chunk",
            "content":{"type":"text","text":"done"}}}})
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":"fake-session","update":{"sessionUpdate":"tool_call_update",
            "toolCallId":"tool-1","status":"completed"}}})
        if mode in ("permission", "permission_locations", "string_id", "fs"):
            server_id = "permission-77" if mode == "string_id" else 77
            if mode in ("permission", "permission_locations", "string_id"):
                if os.environ.get("FAKE_TOOL_UPDATE") == "1":
                    send({"jsonrpc":"2.0","method":"session/update","params":{
                        "sessionId":"fake-session","update":{"sessionUpdate":"tool_call",
                        "toolCallId":"tool-2","title":"synthetic"}}})
                send({"jsonrpc":"2.0","method":"session/update","params":{
                    "sessionId":"fake-session","update":{"sessionUpdate":
                    "tool_call_update" if os.environ.get("FAKE_TOOL_UPDATE") == "1" else "tool_call",
                    "toolCallId":"tool-2",
                    **({} if os.environ.get("FAKE_NO_KIND") == "1" else
                       {"kind":os.environ.get("FAKE_TOOL_KIND","edit")}),
                    "locations":json.loads(os.environ.get("FAKE_LOCATIONS","null"))}}})
                if os.environ.get("FAKE_NULL_UPDATE") == "1":
                    send({"jsonrpc":"2.0","method":"session/update","params":{
                        "sessionId":"fake-session","update":{"sessionUpdate":"tool_call_update",
                        "toolCallId":"tool-2","kind":None,"locations":None}}})
                send({"jsonrpc":"2.0","id":server_id,"method":"session/request_permission","params":{
                    "sessionId":"fake-session","toolCall":{"toolCallId":"tool-2",
                    **({"kind":None,"locations":None} if os.environ.get("FAKE_PERMISSION_NULL") == "1" else {})},
                    "options":([
                    {"optionId":"a","name":"Allow once","kind":"allow_once"},
                    {"optionId":"b","name":"Always allow","kind":"allow_always"},
                    {"optionId":"r","name":"Reject","kind":"reject_once"}]
                    if os.environ.get("FAKE_ONLY_ALWAYS") != "1" else
                    [{"optionId":"b","name":"Always allow","kind":"allow_always"}])}})
            else:
                send({"jsonrpc":"2.0","id":server_id,"method":os.environ["FAKE_FS_METHOD"],
                      "params":{"sessionId":os.environ.get("FAKE_SESSION_ID","fake-session"),
                          "path":os.environ["FAKE_PATH"],
                          "content":os.environ.get("FAKE_CONTENT","changed"),
                          **json.loads(os.environ.get("FAKE_FS_PARAMS","{}"))}})
            receive()
        send({"jsonrpc":"2.0","id":req["id"],"result":{} if mode == "no_stop" else
              {"stopReason":"end_turn"}})
'''


class Sink:
    def __init__(self, consent=None, choice="allow_once"):
        self.acp_consent = consent
        self.choice = choice
        self.events = []
        self.asks = []

    def on_event(self, event):
        self.events.append(event)

    def on_permission(self, ask):
        self.asks.append(ask)
        return self.choice


@pytest.fixture()
def peer(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "fake_acp.py"
    script.write_text(FAKE, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "messages.jsonl"
    real_start = process.start

    def start_fake(_argv, **kwargs):
        return real_start([sys.executable, str(script)], **kwargs)

    monkeypatch.setattr(acp.process, "start", start_fake)
    def build(mode="ok", permission=Permission.READ, **extra):
        monkeypatch.setattr(acp, "_clean_env_extra", lambda _folder: {
            "FAKE_MODE": mode, "FAKE_LOG": str(log), **extra})
        return (acp.AcpTransport("fake-acp"), ExecutorLaunchRequest("T1", {}, work,
                permission=permission, timeout_seconds=1 if mode == "timeout" else 10),
                log, work)
    return build


def messages(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_negotiates_v1_and_prompt_updates_and_result(peer):
    transport, request, log, work = peer()
    sink = Sink()
    session = transport.open_session(request, sink)
    assert transport.capabilities().permission_requests
    assert session.session_id == "fake-session"
    event = session.prompt("synthetic request")
    assert event.kind == "end_turn"
    assert [x.kind for x in sink.events] == ["message", "tool", "end_turn"]
    assert sink.events[1].tool_status == "completed"
    result = session.close()
    assert result.status is RunStatus.COMPLETED
    assert result.command.finished_at is not None
    assert [x["method"] for x in messages(log)[:3]] == ["initialize", "session/new", "session/prompt"]
    capabilities = messages(log)[0]["params"]["clientCapabilities"]
    assert capabilities == {"fs": {"readTextFile": False, "writeTextFile": False}}
    assert "terminal" not in capabilities


@pytest.mark.parametrize("mode", ["version", "bad_json", "bad_id", "error", "no_stop", "exit", "timeout"])
def test_incompatible_or_broken_acp_never_invokes_cli(peer, mode, monkeypatch):
    monkeypatch.setattr(transport_module.CliTransport, "open_session",
                        lambda *_: pytest.fail("ACP must not open CLI"))
    transport, request, log, work = peer(mode)
    sink = Sink()
    if mode == "version":
        with pytest.raises(acp.AcpTransportError):
            transport.open_session(request, sink)
        return
    session = transport.open_session(request, sink)
    event = session.prompt("synthetic request")
    assert event.kind == "error"
    with pytest.raises(RuntimeError, match="ended"):
        session.prompt("after process failure")
    result = session.close()
    assert result.status is (RunStatus.TIMEOUT if mode == "timeout" else RunStatus.FAILED)
    assert result.failure_kind is FailureKind.TRANSPORT_UNAVAILABLE


def test_cancel_sends_notification_and_stops_process(peer):
    transport, request, log, work = peer("wait")
    session = transport.open_session(request, Sink())
    thread = Thread(target=session.prompt, args=("test",))
    thread.start()
    deadline = time.monotonic() + 2
    while len(messages(log)) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.cancel()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert session.close().status is RunStatus.CANCELLED
    assert any(x.get("method") == "session/cancel" and "id" not in x for x in messages(log))


def test_graceful_cancel_keeps_session_for_next_prompt(peer):
    transport, request, log, work = peer("cancel_grace")
    session = transport.open_session(request, Sink())
    first = []
    thread = Thread(target=lambda: first.append(session.prompt("first")))
    thread.start()
    deadline = time.monotonic() + 2
    while len(messages(log)) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.cancel()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert first[0].data["stop_reason"] == "cancelled"
    assert session._result.status is RunStatus.CANCELLED
    assert session._result.result_envelope == {"old": True}
    assert session._result.usage == {"used": 5, "size": 20}
    assert session.prompt("second").data["stop_reason"] == "end_turn"
    result = session.close()
    assert result.status is RunStatus.COMPLETED
    assert result.result_envelope == {"new": True}
    assert result.usage is None


def test_each_turn_has_its_own_envelope_and_usage(peer):
    transport, request, log, work = peer("multi_turn")
    session = transport.open_session(request, Sink())
    session.prompt("first")
    assert session._result.result_envelope == {"turn": 1}
    assert session._result.usage == {"used": 3, "size": 10}
    assert "reused-id" in session._tool_calls
    session.prompt("second")
    assert session._result.result_envelope == {"turn": 2}
    assert session._result.usage is None
    assert session._tool_calls == {}
    session.prompt("third")
    assert session.close().result_envelope is None


def test_pending_permission_is_cancelled_and_session_remains_usable(peer):
    transport, request, log, work = peer("pending_permission", permission=Permission.WRITE)
    entered, release = Event(), Event()

    class WaitingSink(Sink):
        def on_permission(self, ask):
            entered.set()
            assert release.wait(3)
            return super().on_permission(ask)

    session = transport.open_session(request, WaitingSink(acp.AcpConsent("office-1", allow_write=True)))
    sent = Event()
    original_send = session._send

    def send(payload):
        original_send(payload)
        if payload.get("method") == "session/cancel":
            sent.set()

    session._send = send
    prompt_thread = Thread(target=session.prompt, args=("first",))
    prompt_thread.start()
    assert entered.wait(3)
    cancellation = Thread(target=session.cancel)
    cancellation.start()
    assert sent.wait(3)
    release.set()
    cancellation.join(3)
    prompt_thread.join(3)
    assert not cancellation.is_alive() and not prompt_thread.is_alive()
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["outcome"] == {
        "outcome": "cancelled"}
    assert session._result.status is RunStatus.CANCELLED
    assert session.prompt("second").kind == "end_turn"
    assert session.close().status is RunStatus.COMPLETED


@pytest.mark.parametrize("window", ["starting", "finishing"])
def test_cancel_sees_only_active_turns_in_lock_windows(peer, window):
    transport, request, log, work = peer()
    session = transport.open_session(request, Sink())
    reached, release = Event(), Event()
    real_lock = session._turn_lock

    class WindowLock:
        def acquire(self, blocking=True):
            acquired = real_lock.acquire(blocking)
            if acquired and window == "starting":
                reached.set()
                assert release.wait(3)
            return acquired

        def release(self):
            if window == "finishing":
                reached.set()
                assert release.wait(3)
            real_lock.release()

        def locked(self):
            return real_lock.locked()

    session._turn_lock = WindowLock()
    if window == "finishing":
        session._call = lambda method, params: {"stopReason": "end_turn"}
    worker = Thread(target=session.prompt, args=("turn",))
    worker.start()
    assert reached.wait(3)
    before = len([x for x in messages(log) if x.get("method") == "session/cancel"])
    assert session.cancel() is False
    assert len([x for x in messages(log) if x.get("method") == "session/cancel"]) == before
    release.set()
    worker.join(3)
    assert not worker.is_alive()
    assert session.close().status is RunStatus.COMPLETED


def test_missing_adapter_is_structured_transport_error(peer, monkeypatch):
    transport, request, log, work = peer()
    def missing(*args, **kwargs):
        raise process.ProcessLaunchError(process.EXECUTABLE_NOT_FOUND, "synthetic missing adapter")
    monkeypatch.setattr(acp.process, "start", missing)
    with pytest.raises(acp.AcpTransportError) as caught:
        transport.open_session(request, Sink())
    assert caught.value.failure_kind is FailureKind.TRANSPORT_UNAVAILABLE
    assert not log.exists()


@pytest.mark.parametrize("consent,choice,expected", [
    (None, "allow_once", None),
    (acp.AcpConsent("office-1", allow_write=True), "allow_once", "a"),
    (acp.AcpConsent("office-1", allow_write=True), "allow_always", None),
    (acp.AcpConsent("office-1", allow_write=True, allow_always=True), "allow_always", "b"),
    (acp.AcpConsent("office-1", allow_write=True), "reject_once", "r"),
])
def test_permission_requires_verified_configured_scope_and_selected_option(peer, consent, choice, expected,
                                                                           monkeypatch):
    monkeypatch.setattr(transport_module.CliTransport, "open_session",
                        lambda *_: pytest.fail("denied ACP permission must not open CLI"))
    transport, request, log, work = peer("permission", permission=Permission.WRITE)
    sink = Sink(consent, choice)
    session = transport.open_session(request, sink)
    session.prompt("test")
    response = next(x for x in messages(log) if x.get("id") == 77)
    assert response["result"]["outcome"].get("optionId") == expected
    assert len(sink.asks) == (0 if consent is None else 1)
    session.close()


def test_read_permission_cannot_authorize_edit(peer):
    transport, request, log, work = peer("permission", permission=Permission.READ)
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    response = next(x for x in messages(log) if x.get("id") == 77)
    assert response["result"]["outcome"] == {"outcome": "cancelled"}
    assert not sink.asks
    session.close()


def test_server_string_request_id_is_correlated(peer):
    transport, request, log, work = peer("string_id", permission=Permission.WRITE)
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1", allow_write=True)))
    assert session.prompt("test").kind == "end_turn"
    response = next(x for x in messages(log) if x.get("id") == "permission-77")
    assert response["result"]["outcome"]["optionId"] == "a"
    session.close()


def test_permission_rejects_multiple_or_outside_locations(peer):
    transport, request, log, work = peer("permission_locations", permission=Permission.WRITE)
    outside = work.parent / "outside.txt"
    for locations in ([{"path": str(work / "inside.txt")}, {"path": str(outside)}],
                      [{"path": str(outside)}]):
        transport, request, log, work = peer("permission_locations", permission=Permission.WRITE,
                                           FAKE_LOCATIONS=json.dumps(locations))
        sink = Sink(acp.AcpConsent("office-1", allow_write=True))
        session = transport.open_session(request, sink)
        session.prompt("test")
        response = next(x for x in messages(log) if x.get("id") == 77)
        assert response["result"]["outcome"] == {"outcome": "cancelled"}
        assert not sink.asks
        session.close()
        log.unlink()


@pytest.mark.parametrize("null_update,null_request", [("1", "0"), ("0", "1"), ("1", "1")])
def test_null_tool_fields_preserve_known_outside_location(peer, null_update, null_request):
    transport, request, log, work = peer("permission", permission=Permission.WRITE)
    transport, request, log, work = peer(
        "permission", permission=Permission.WRITE, FAKE_TOOL_KIND="read",
        FAKE_LOCATIONS=json.dumps([{"path": str(work.parent / "outside.txt")}]),
        FAKE_NULL_UPDATE=null_update, FAKE_PERMISSION_NULL=null_request)
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["outcome"] == {
        "outcome": "cancelled"}
    assert not sink.asks
    session.close()


def test_null_kind_uses_cached_kind_and_empty_filtered_options_skip_sink(peer):
    transport, request, log, work = peer(
        "permission", FAKE_TOOL_KIND="edit", FAKE_NULL_UPDATE="1", FAKE_PERMISSION_NULL="1")
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["outcome"] == {
        "outcome": "cancelled"}
    assert not sink.asks
    session.close()

    log.unlink()
    transport, request, log, work = peer(
        "permission", permission=Permission.WRITE, FAKE_ONLY_ALWAYS="1")
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["outcome"] == {
        "outcome": "cancelled"}
    assert not sink.asks
    session.close()


@pytest.mark.parametrize("kind,allowed_without_write", [
    ("read", True), ("search", True), ("think", True), ("edit", False),
    ("delete", False), ("move", False), ("execute", False), ("fetch", False),
    ("switch_mode", False), ("other", False), ("unexpected", False),
])
def test_permission_kind_allowlist_with_cached_tool_call(peer, kind, allowed_without_write):
    transport, request, log, work = peer("permission", FAKE_TOOL_KIND=kind)
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    response = next(x for x in messages(log) if x.get("id") == 77)
    assert (response["result"]["outcome"].get("optionId") == "a") is allowed_without_write
    assert len(sink.asks) == int(allowed_without_write)
    session.close()


def test_permission_passes_all_in_scope_locations(peer):
    transport, request, log, work = peer("permission_locations", permission=Permission.WRITE)
    locations = [{"path": str(work / "from.txt")}, {"path": str(work / "to.txt")}]
    transport, request, log, work = peer("permission_locations", permission=Permission.WRITE,
                                       FAKE_TOOL_KIND="move", FAKE_LOCATIONS=json.dumps(locations))
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    assert sink.asks[0].targets == tuple(x["path"] for x in locations)
    assert sink.asks[0].target == locations[0]["path"]
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["outcome"]["optionId"] == "a"
    session.close()


def test_permission_uses_tool_call_update_and_missing_kind_defaults_to_other(peer):
    transport, request, log, work = peer("permission", permission=Permission.WRITE)
    locations = [{"path": str(work / "from.txt")}, {"path": str(work / "to.txt")}]
    transport, request, log, work = peer("permission", permission=Permission.WRITE,
                                        FAKE_TOOL_UPDATE="1", FAKE_NO_KIND="1",
                                        FAKE_LOCATIONS=json.dumps(locations))
    sink = Sink(acp.AcpConsent("office-1", allow_write=True))
    session = transport.open_session(request, sink)
    session.prompt("test")
    assert sink.asks[0].tool == "other"
    assert sink.asks[0].targets == tuple(x["path"] for x in locations)
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["outcome"]["optionId"] == "a"
    session.close()


def test_write_capability_is_announced_for_write_launch(peer):
    transport, request, log, work = peer(permission=Permission.WRITE)
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1", allow_write=True)))
    assert messages(log)[0]["params"]["clientCapabilities"]["fs"] == {
        "readTextFile": True, "writeTextFile": True}
    session.close()


def test_read_capability_requires_association_and_write_capability_requires_consent(peer):
    transport, request, log, work = peer(permission=Permission.WRITE)
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1")))
    assert messages(log)[0]["params"]["clientCapabilities"]["fs"] == {
        "readTextFile": True, "writeTextFile": False}
    session.close()


@pytest.mark.parametrize("method,permission,consent,path_kind,allowed", [
    ("fs/read_text_file", Permission.READ, None, "inside", False),
    ("fs/read_text_file", Permission.READ, acp.AcpConsent("office-1"), "inside", True),
    ("fs/read_text_file", Permission.READ, acp.AcpConsent("office-1"), "oversized", False),
    ("fs/read_text_file", Permission.READ, acp.AcpConsent("office-1"), "outside", False),
    ("fs/write_text_file", Permission.READ, acp.AcpConsent("office-1", allow_write=True), "inside", False),
    ("fs/write_text_file", Permission.WRITE, None, "inside", False),
    ("fs/write_text_file", Permission.WRITE, acp.AcpConsent("office-1", allow_write=True), "inside", True),
    ("fs/write_text_file", Permission.WRITE, acp.AcpConsent("office-1", allow_write=True), "relative", False),
])
def test_fs_is_confined_and_write_requires_executor_and_scope(peer, method, permission, consent,
                                                               path_kind, allowed):
    transport, request, log, work = peer("fs", permission=permission)
    inside = work / "note.txt"
    inside.write_text("original")
    large = work / "large.txt"
    large.write_text("x" * (acp.LINE_CAP // 2 + 1))
    path = {"inside": inside, "oversized": large, "outside": work.parent / "outside.txt",
            "relative": Path("note.txt")}[path_kind]
    if path_kind == "outside":
        path.write_text("outside")
    transport, request, log, work = peer("fs", permission=permission,
                                       FAKE_FS_METHOD=method, FAKE_PATH=str(path))
    session = transport.open_session(request, Sink(consent))
    session.prompt("test")
    response = next(x for x in messages(log) if x.get("id") == 77)
    assert ("result" in response) is allowed
    assert inside.read_text() == ("changed" if allowed and method == "fs/write_text_file" else "original")
    session.close()


def test_fs_rejects_symlink_escaping_workdir(peer):
    transport, request, log, work = peer("fs")
    outside = work.parent / "outside.txt"
    outside.write_text("private")
    link = work / "alias.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this host")
    transport, request, log, work = peer("fs", FAKE_FS_METHOD="fs/read_text_file",
                                       FAKE_PATH=str(link))
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1")))
    session.prompt("test")
    response = next(x for x in messages(log) if x.get("id") == 77)
    assert "error" in response
    session.close()


@pytest.mark.parametrize("parameters,expected", [
    ({"line": 2, "limit": 2}, "two\nthree\n"),
    ({"line": 3}, "three\n"),
    ({"limit": 1}, "one\n"),
])
def test_read_text_file_ranges(peer, parameters, expected):
    transport, request, log, work = peer("fs")
    path = work / "lines.txt"
    path.write_bytes(b"one\ntwo\nthree\n")
    transport, request, log, work = peer("fs", FAKE_FS_METHOD="fs/read_text_file",
                                       FAKE_PATH=str(path), FAKE_FS_PARAMS=json.dumps(parameters))
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1")))
    session.prompt("test")
    assert next(x for x in messages(log) if x.get("id") == 77)["result"]["content"] == expected
    session.close()


@pytest.mark.parametrize("parameters", [
    {"line": 0}, {"line": True}, {"line": 1.5}, {"line": "1"},
    {"limit": 0}, {"limit": False}, {"limit": -2}, {"limit": "2"},
])
def test_read_rejects_invalid_range(peer, parameters):
    transport, request, log, work = peer("fs")
    path = work / "lines.txt"
    path.write_text("text")
    transport, request, log, work = peer("fs", FAKE_FS_METHOD="fs/read_text_file",
                                       FAKE_PATH=str(path), FAKE_FS_PARAMS=json.dumps(parameters))
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1")))
    session.prompt("test")
    assert "error" in next(x for x in messages(log) if x.get("id") == 77)
    session.close()


@pytest.mark.parametrize("method", ["fs/read_text_file", "fs/write_text_file"])
def test_fs_requires_matching_session_id(peer, method):
    transport, request, log, work = peer("fs", permission=Permission.WRITE)
    path = work / "note.txt"
    path.write_text("original")
    transport, request, log, work = peer("fs", permission=Permission.WRITE, FAKE_FS_METHOD=method,
                                       FAKE_PATH=str(path), FAKE_SESSION_ID="other")
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1", allow_write=True)))
    session.prompt("test")
    assert "error" in next(x for x in messages(log) if x.get("id") == 77)
    assert path.read_text() == "original"
    session.close()


def test_write_creates_file_without_translating_newlines(peer):
    transport, request, log, work = peer("fs", permission=Permission.WRITE)
    path = work / "new.txt"
    transport, request, log, work = peer("fs", permission=Permission.WRITE,
                                       FAKE_FS_METHOD="fs/write_text_file", FAKE_PATH=str(path),
                                       FAKE_CONTENT="first\nsecond\n")
    session = transport.open_session(request, Sink(acp.AcpConsent("office-1", allow_write=True)))
    session.prompt("test")
    assert "result" in next(x for x in messages(log) if x.get("id") == 77)
    assert path.read_bytes() == b"first\nsecond\n"
    session.close()


def test_usage_update_and_unsupported_diff(peer):
    transport, request, log, work = peer("usage")
    sink = Sink()
    session = transport.open_session(request, sink)
    session.prompt("test")
    assert session.close().usage == {"used": 12, "size": 100,
                                     "cost": {"amount": 0.02, "currency": "USD"}}
    assert [x.kind for x in sink.events] == ["usage", "message", "tool", "end_turn"]


def test_many_updates_reach_slow_sink_in_order(peer):
    transport, request, log, work = peer("many")
    class SlowSink(Sink):
        def on_event(self, event):
            if event.kind == "message":
                time.sleep(0.0002)
            super().on_event(event)
    sink = SlowSink()
    session = transport.open_session(request, sink)
    assert session.prompt("test").kind == "end_turn"
    assert [e.data["content"]["text"] for e in sink.events if e.kind == "message"] == [str(n) for n in range(4200)]
    session.close()
