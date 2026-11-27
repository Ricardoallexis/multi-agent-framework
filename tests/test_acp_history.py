# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Saved ACP turns use only local synthetic data and never open an adapter."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from multiagent.services.acp_history import AcpHistory, MAX_FILES, TEXT_LIMIT
from multiagent.services.runs import _AcpSink
from multiagent.executors.acp import AcpConsent
from multiagent.executors.transport import AgentEvent


def event(kind, text=""):
    return {"kind": kind, "tool_status": None, "data": {"text": text}}


def test_completed_turn_replays_prompt_and_joined_redacted_message(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "a" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [event("prompt", "question"),
                                     event("message", "password="), event("message", "sample-secret"),
                                     event("end_turn")])
    page = history.page(identifier)
    assert page["saved"] is True and page["active"] is False
    assert [item["kind"] for item in page["events"]] == ["prompt", "message", "end_turn"]
    assert page["events"][1]["data"]["text"].startswith("[REDACTED:")
    assert "sample-secret" not in (history.folder / f"{identifier}.jsonl").read_text(encoding="utf-8")
    assert page["redacted"] is True and page["incomplete"] is False
    assert history.page(identifier, 1)["events"][0]["cursor"] == 2


def test_acp_content_chunks_are_screened_together(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "9" * 32
    history.create(identifier, "sample-area", "office/area")
    live = [
        {"kind": "thought", "tool_status": None, "data": {"content": {"type": "text", "text": "password="}}},
        {"kind": "thought", "tool_status": None, "data": {"content": {"type": "text", "text": "sample-secret"}}},
        event("end_turn")]
    history.append_turn(identifier, live)
    assert history.page(identifier)["events"][0]["data"]["text"].startswith("[REDACTED:")
    assert live[0]["data"]["content"]["text"] == "password="


def test_list_content_chunks_are_screened_together(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "1" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [
        {"kind": "message", "data": {"content": [{"type": "text", "text": "password="}]}},
        {"kind": "message", "data": {"content": [{"type": "text", "text": "sample-secret"}]}},
        event("end_turn")])
    assert history.page(identifier)["events"][0]["data"]["text"].startswith("[REDACTED:")
    assert "sample-secret" not in (history.folder / f"{identifier}.jsonl").read_text(encoding="utf-8")


def test_mixed_text_and_content_chunks_are_screened_together(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "2" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [
        event("thought", "password="),
        {"kind": "thought", "data": {"content": {"type": "text", "text": "sample-secret"}}},
        event("end_turn")])
    assert history.page(identifier)["events"][0]["data"]["text"].startswith("[REDACTED:")


def test_prompt_secret_is_redacted_on_disk(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "3" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [event("prompt", "password=sample-secret"), event("end_turn")])
    disk = (history.folder / f"{identifier}.jsonl").read_text(encoding="utf-8")
    assert "sample-secret" not in disk
    assert history.page(identifier)["events"][0]["redacted"] is True


def test_tool_status_is_restricted_to_visible_states(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "4" * 32
    history.create(identifier, "sample-area", "office/area")
    statuses = ["pending", "in_progress", "completed", "failed", None, "unexpected"]
    history.append_turn(identifier, [
        {"kind": "tool", "tool_status": status, "data": {"title": "tool"}} for status in statuses])
    assert [item["tool_status"] for item in history.page(identifier)["events"]] == statuses[:-1] + [None]


def test_hidden_protocol_fields_are_not_saved(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "7" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [
        {"kind": "tool", "tool_status": "completed", "data": {
            "title": "Read", "content": {"type": "text", "text": "visible", "env": "hidden-value"},
            "credentials": "hidden-value", "locations": [{"path": "file.txt", "token": "hidden-value"}]}},
        event("end_turn")])
    assert "hidden-value" not in (history.folder / f"{identifier}.jsonl").read_text(encoding="utf-8")


def test_text_is_clipped_and_invalid_id_never_reaches_disk(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "b" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [event("prompt", "x" * (TEXT_LIMIT + 50)), event("end_turn")])
    assert history.page(identifier)["events"][0]["data"]["text"] == "x" * TEXT_LIMIT + "…"
    before = set(history.folder.iterdir())
    assert history.page("../bad") is None
    assert history.delete("../bad") is False
    assert set(history.folder.iterdir()) == before


def test_incomplete_tail_and_zero_file_do_not_break_list(tmp_path):
    history = AcpHistory(tmp_path)
    identifier = "c" * 32
    history.create(identifier, "sample-area", "office/area")
    history.append_turn(identifier, [event("prompt", "hello"), event("end_turn")])
    with (history.folder / f"{identifier}.jsonl").open("ab") as output:
        output.write(b'{"cursor":3')
    assert len(history.page(identifier)["events"]) == 2
    assert history.page(identifier)["incomplete"] is True
    (history.folder / ("d" * 32 + ".jsonl")).write_bytes(b"\x00" * 64)
    assert {row["id"]: row["unreadable"] for row in history.list()} == {
        identifier: True, "d" * 32: True}
    assert history.page("d" * 32) is None


def test_retention_count_age_size_and_deletion(tmp_path, monkeypatch):
    history = AcpHistory(tmp_path)
    old = "e" * 32
    history.create(old, "sample-area", "office/area")
    past = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
    os.utime(history.folder / f"{old}.jsonl", (past, past))
    for number in range(MAX_FILES):
        history.create(f"{number:032x}", "sample-area", "office/area")
    assert history.page(old) is None
    assert len(history.list()) == MAX_FILES
    newest = "f" * 32
    history.create(newest, "sample-area", "office/area")
    assert len(history.list()) == MAX_FILES
    history.purge()
    assert len(history.list()) == MAX_FILES
    monkeypatch.setattr("multiagent.services.acp_history.MAX_BYTES",
                        (history.folder / f"{newest}.jsonl").stat().st_size + 5)
    with pytest.raises(OverflowError):
        history.append_turn(newest, [event("prompt", "hello")])
    monkeypatch.setattr("multiagent.services.acp_history.MAX_BYTES", 2 * 1024 * 1024)
    assert history.delete(newest) is True
    assert history.page(newest) is None
    assert history.delete_all() == MAX_FILES - 1
    assert history.list() == []


def test_oversized_file_is_purged(tmp_path, monkeypatch):
    history = AcpHistory(tmp_path)
    identifier = "8" * 32
    history.create(identifier, "sample-area", "office/area")
    monkeypatch.setattr("multiagent.services.acp_history.MAX_BYTES", 100)
    history.purge()
    assert history.page(identifier) is None


def test_write_failure_reports_warning_without_interrupting_turn(tmp_path):
    class BrokenHistory:
        def append_turn(self, identifier, events):
            raise OSError("synthetic disk failure")

    sink = _AcpSink(AcpConsent(scope_id="office/area", allow_write=False, allow_always=False),
                    tmp_path, history=BrokenHistory(), identifier="a" * 32)
    sink.on_event(AgentEvent("prompt", data={"text": "hello"}))
    sink.on_event(AgentEvent("end_turn", data={"stop_reason": "end_turn"}))
    assert sink.history_warning == "history_not_saved"
    assert [item["kind"] for item in sink.page(0)] == ["prompt", "end_turn", "error"]
