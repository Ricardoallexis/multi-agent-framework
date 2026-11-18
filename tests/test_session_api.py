# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T04 HTTP routes (/gui/api/sessions and the session SSE follow) over synthetic stores."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import events
from multiagent.gui.token import ensure_token
from session_fixtures import (
    CLAUDE_ID,
    CLAUDE_OTHER,
    CODEX_ID,
    COPILOT_ID,
    add_framework_run,
    append,
    claude_user,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)

SID = f"claude:{CLAUDE_ID}"
ROUTES = ("/gui/api/sessions", f"/gui/api/sessions/{SID}", f"/gui/api/sessions/{SID}/items",
          f"/gui/api/stream?session={SID}")


def build(tmp_path, **settings):
    stores = make_stores(tmp_path)
    services = make_services(stores, **settings)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    token = ensure_token(services.settings.gui_token_file)
    return stores, services, token


@pytest.fixture
def world(tmp_path):
    stores, services, token = build(tmp_path)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, stores=stores, services=services, token=token,
                              auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def sse(text: str) -> list[SimpleNamespace]:
    out = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if not line.startswith(":") and ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if "event" in fields:
            out.append(SimpleNamespace(id=fields.get("id"), event=fields["event"], data=json.loads(fields["data"])))
    return out


# -------- token --------
@pytest.mark.parametrize("path", ROUTES)
def test_every_session_route_needs_the_token(world, path):
    assert world.client.get(path).status_code == 401
    assert world.client.get(path, headers={"Authorization": "Bearer wrong-token-value"}).status_code == 401
    assert world.client.get(path, headers={"Authorization": f"Basic {world.token}"}).status_code == 401
    assert world.client.get(path, headers=world.auth).status_code == 200


def test_unauthorized_answers_leak_nothing_and_keep_the_gui_headers(world):
    response = world.client.get("/gui/api/sessions")
    assert response.json()["detail"]["code"] == "unauthorized" and CLAUDE_ID not in response.text
    assert "default-src 'none'" in response.headers["content-security-policy"]
    ok = world.client.get("/gui/api/sessions", headers=world.auth)
    assert ok.headers["content-security-policy"] and ok.headers["x-content-type-options"] == "nosniff"


def test_sessions_are_not_part_of_the_public_api(world):
    for path in ("/api/v1/sessions", f"/api/v1/sessions/{SID}"):
        assert world.client.get(path, headers=world.auth).status_code == 404


def test_host_rule_applies(world):
    r = world.client.get("/gui/api/sessions", headers={**world.auth, "Host": "evil.example"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_host"


# -------- list --------
def test_list_returns_the_unified_page(world):
    run_id = add_framework_run(world.services)
    r = world.client.get("/gui/api/sessions", headers=world.auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    page = r.json()
    assert {s["id"] for s in page["sessions"]} == {SID, f"codex:{CODEX_ID}", f"copilot:{COPILOT_ID}", f"framework:{run_id}"}
    assert page["next_cursor"] is None and page["hint"] is None and page["warnings"] == []
    assert page == world.services.sessions.list_sessions()


def test_list_filters_and_pagination(world):
    get = lambda **params: world.client.get("/gui/api/sessions", params=params, headers=world.auth).json()  # noqa: E731
    assert [s["id"] for s in get(source="codex")["sessions"]] == [f"codex:{CODEX_ID}"]
    assert [s["id"] for s in get(text="claude")["sessions"]] == [SID]
    assert [s["id"] for s in get(since="2026-10-03")["sessions"]] == [f"copilot:{COPILOT_ID}"]
    assert get(cwd=str(world.stores.other))["sessions"] == []
    first = get(limit=2)
    second = get(limit=2, after=first["next_cursor"])
    assert len(first["sessions"]) == 2 and first["next_cursor"] == "0:2" and len(second["sessions"]) == 1
    assert second["next_cursor"] is None


def test_empty_roots_return_an_empty_page_with_a_hint(tmp_path):
    stores, services, token = build(tmp_path, gui_session_roots=[])
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        page = client.get("/gui/api/sessions", headers={"Authorization": f"Bearer {token}"}).json()
    assert page["sessions"] == [] and "gui_session_roots" in page["hint"]
    assert any(w.startswith("no_allowed_roots") for w in page["warnings"])
    services.runs.close()


@pytest.mark.parametrize("query,code,status", [
    ("source=nope", "invalid_request", 422), ("surface=nope", "invalid_request", 422),
    ("since=notadate", "invalid_request", 422), ("limit=0", "invalid_request", 422),
    ("limit=501", "invalid_request", 422), ("limit=abc", "invalid_request", 422),
    ("after=abc", "invalid_cursor", 400), ("after=1:2", "invalid_cursor", 400),
])
def test_bad_list_queries(world, query, code, status):
    r = world.client.get(f"/gui/api/sessions?{query}", headers=world.auth)
    assert r.status_code == status and r.json()["detail"]["code"] == code
    assert set(r.json()["detail"]) == {"code", "message", "details"}


# -------- one session and its items --------
def test_show_and_items(world):
    summary = world.client.get(f"/gui/api/sessions/{SID}", headers=world.auth)
    assert summary.status_code == 200 and summary.json()["title"] == "Claude work"
    assert summary.json() == world.services.sessions.get_session(SID)
    encoded = world.client.get(f"/gui/api/sessions/claude%3A{CLAUDE_ID}", headers=world.auth)
    assert encoded.json() == summary.json()
    items = world.client.get(f"/gui/api/sessions/{SID}/items", headers=world.auth).json()
    assert [i["kind"] for i in items["items"]] == ["user_message", "reasoning_omitted", "assistant_message"]
    assert items["items"][0]["text"] == "hola <b>mundo</b> <script>x()</script>"  # JSON text, not markup
    assert "private reasoning" not in json.dumps(items) and items["done"] is True
    page = world.client.get(f"/gui/api/sessions/{SID}/items", params={"limit": 1}, headers=world.auth).json()
    assert len(page["items"]) == 1 and page["done"] is False
    rest = world.client.get(f"/gui/api/sessions/{SID}/items", params={"after": page["next_cursor"]}, headers=world.auth).json()
    assert [i["kind"] for i in page["items"] + rest["items"]] == [i["kind"] for i in items["items"]]


def test_framework_session_over_http(world):
    run_id = add_framework_run(world.services, model="m1", events=2)
    sid = f"framework:{run_id}"
    summary = world.client.get(f"/gui/api/sessions/{sid}", headers=world.auth).json()
    assert summary["source"] == "framework" and summary["tokens"]["input"] == 17
    items = world.client.get(f"/gui/api/sessions/{sid}/items", headers=world.auth).json()
    assert len(items["items"]) == 3 and items["items"][0]["text"].startswith("workflow_started")
    later = world.client.get(f"/gui/api/sessions/{sid}/items", params={"after": items["items"][0]["cursor"]}, headers=world.auth).json()
    assert [i["cursor"] for i in later["items"]] == [i["cursor"] for i in items["items"][1:]]


@pytest.mark.parametrize("session_id", ["foo", "claude:not-a-uuid", "claude:", f"claude:{CLAUDE_ID}:zz", "framework:a b",
                                        "claude:..%2F..%2Fetc"])
def test_invalid_session_ids_are_400(world, session_id):
    for path in (f"/gui/api/sessions/{session_id}", f"/gui/api/sessions/{session_id}/items"):
        r = world.client.get(path, headers=world.auth)
        assert r.status_code in (400, 404) and r.status_code != 200
        if "%2F" not in session_id:
            assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_session_id"


@pytest.mark.parametrize("cursor", ["abc", "1", "0:-1", "5:3", "0:1:2"])
def test_invalid_item_cursors_are_400(world, cursor):
    r = world.client.get(f"/gui/api/sessions/{SID}/items", params={"after": cursor}, headers=world.auth)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_cursor"


def test_unknown_and_outside_sessions_are_404(world):
    write_claude(world.stores, CLAUDE_OTHER, cwd=world.stores.other)
    for session_id in (f"claude:{CLAUDE_OTHER}", f"codex:{CLAUDE_ID}", "framework:" + "0" * 32):
        for path in (f"/gui/api/sessions/{session_id}", f"/gui/api/sessions/{session_id}/items"):
            r = world.client.get(path, headers=world.auth)
            assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    assert world.client.get(f"/gui/api/stream?session=claude:{CLAUDE_OTHER}", headers=world.auth).status_code == 404


def test_items_limit_is_bounded(world):
    for limit in (0, 501):
        r = world.client.get(f"/gui/api/sessions/{SID}/items", params={"limit": limit}, headers=world.auth)
        assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_request"
    assert world.client.get(f"/gui/api/sessions/{SID}/items", params={"limit": 500}, headers=world.auth).status_code == 200


# -------- live follow --------
def test_stream_without_session_still_validates_its_cursor(world):
    assert world.client.get("/gui/api/stream?after=-1", headers=world.auth).status_code == 422
    assert world.client.get("/gui/api/stream?after=0:5", headers=world.auth).status_code == 422
    assert world.client.get("/gui/api/stream?after=0", headers=world.auth).status_code == 200


def test_stream_validates_session_and_cursor_before_streaming(world):
    for query, status in (("session=foo", 400), (f"session={SID}&after=zzz", 400), (f"session={SID}&after=5", 400),
                          ("session=framework:abc&after=0:1", 404), ("session=claude:" + "9" * 8 + "-9999-4999-8999-999999999999", 404)):
        r = world.client.get(f"/gui/api/stream?{query}", headers=world.auth)
        assert r.status_code == status, query


def test_stream_replays_a_file_session_and_resumes_from_a_cursor(world):
    r = world.client.get(f"/gui/api/stream?session={SID}", headers=world.auth)
    assert r.headers["content-type"].startswith("text/event-stream")
    events_ = sse(r.text)
    assert [e.event for e in events_] == ["item"] * 3 and [e.data["kind"] for e in events_][1] == "reasoning_omitted"
    assert [e.id for e in events_] == [e.data["cursor"] for e in events_] and events_[0].id.startswith("0:")
    assert events_[0].data["text"] == "hola <b>mundo</b> <script>x()</script>"
    after_first = sse(world.client.get(f"/gui/api/stream?session={SID}&after={events_[0].id}", headers=world.auth).text)
    assert [e.id for e in after_first] == [e.id for e in events_[1:]]
    # the cursor is the end of a record, so the items of one record (reasoning + answer) share it
    assert events_[1].id == events_[2].id != events_[0].id
    by_header = sse(world.client.get(f"/gui/api/stream?session={SID}", headers={**world.auth, "Last-Event-ID": events_[0].id}).text)
    assert [e.id for e in by_header] == [e.id for e in events_[1:]]
    assert sse(world.client.get(f"/gui/api/stream?session={SID}&after={events_[2].id}", headers=world.auth).text) == []


def test_stream_sends_a_heartbeat_when_the_session_is_quiet(world):
    end = world.services.sessions.read_items(SID)["next_cursor"]
    r = world.client.get(f"/gui/api/stream?session={SID}&after={end}", headers=world.auth)
    assert ": connected" in r.text and ": heartbeat" in r.text and sse(r.text) == []


def test_stream_delivers_items_appended_while_connected(tmp_path):
    stores, services, token = build(tmp_path, gui_stream_max_seconds=1.5)
    path = stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"

    def grow():
        time.sleep(0.4)
        append(path, [claude_user("late one", "2026-10-01T10:01:00Z"), claude_user("late two <i>x</i>", "2026-10-01T10:01:01Z")])

    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        worker = threading.Thread(target=grow)
        worker.start()
        r = client.get(f"/gui/api/stream?session={SID}", headers={"Authorization": f"Bearer {token}"})
        worker.join()
    texts = [e.data["text"] for e in sse(r.text) if e.event == "item"]
    assert texts == ["hola <b>mundo</b> <script>x()</script>", None, "respuesta", "late one", "late two <i>x</i>"]
    services.runs.close()


def test_stream_of_a_framework_run_keeps_run_event_ids(world):
    first = add_framework_run(world.services, "first", events=3)
    second = add_framework_run(world.services, "second", events=2)
    r = world.client.get(f"/gui/api/stream?session=framework:{first}", headers=world.auth)
    mine = sse(r.text)
    assert [e.event for e in mine] == ["workflow_started"] + ["step_completed"] * 3
    assert {e.data["run_id"] for e in mine} == {first}
    ids_ = [int(e.id) for e in mine]
    assert ids_ == sorted(ids_) and [e.data["id"] for e in mine] == ids_
    # the same numbers the items endpoint uses as cursors, so a client can switch from items to follow
    items = world.client.get(f"/gui/api/sessions/framework:{first}/items", headers=world.auth).json()
    assert [int(i["cursor"]) for i in items["items"]] == ids_
    tail = sse(world.client.get(f"/gui/api/stream?session=framework:{first}&after={ids_[1]}", headers=world.auth).text)
    assert [int(e.id) for e in tail] == ids_[2:]
    header = sse(world.client.get(f"/gui/api/stream?session=framework:{second}", headers={**world.auth, "Last-Event-ID": str(ids_[-1])}).text)
    assert {e.data["run_id"] for e in header} == {second}
    everything = sse(world.client.get("/gui/api/stream", headers=world.auth).text)
    assert {e.data["run_id"] for e in everything} == {first, second}  # no session: the multiplexed stream, unchanged


# -------- the generator itself (deterministic, no timing races) --------
def collect(chunks: list[str]) -> list[SimpleNamespace]:
    return sse("".join(chunks))


def test_stream_session_follows_the_file_as_it_grows(world):
    path = world.stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"

    async def scenario():
        read = lambda cursor: world.services.sessions.poll_items(SID, cursor)  # noqa: E731
        gen = events.stream_session(read, after="0:0", heartbeat=0.05, poll=0.01, max_seconds=10)
        chunks, items = [], 0
        async for chunk in gen:
            chunks.append(chunk)
            if chunk.startswith("id: "):
                items += 1
                if items == 3:  # everything that existed was replayed: now the file grows
                    append(path, [claude_user("grown", "2026-10-01T10:02:00Z")])
                if items == 4:
                    break
        await gen.aclose()
        return chunks

    got = collect(asyncio.run(asyncio.wait_for(scenario(), 20)))
    assert [e.data["text"] for e in got][-1] == "grown" and len(got) == 4


def test_stream_session_reports_a_cursor_reset_once_and_restarts(world):
    async def scenario():
        read = lambda cursor: world.services.sessions.poll_items(SID, cursor)  # noqa: E731
        gen = events.stream_session(read, after="0:99999999", heartbeat=0.05, poll=0.01, max_seconds=0.15)
        return [chunk async for chunk in gen]

    got = collect(asyncio.run(scenario()))
    assert got[0].event == "notice" and "cursor_reset" in got[0].data["warnings"][0] and got[0].id is None
    assert [e.event for e in got[1:]] == ["item"] * 3
    assert sum(1 for e in got if e.event == "notice") == 1  # not repeated on every poll


def test_stream_session_ends_with_an_error_event_when_the_session_vanishes(world):
    path = world.stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"

    async def scenario():
        read = lambda cursor: world.services.sessions.poll_items(SID, cursor)  # noqa: E731
        gen = events.stream_session(read, after="0:0", heartbeat=0.05, poll=0.01, max_seconds=10)
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
            if chunk.startswith("id: ") and len([c for c in chunks if c.startswith("id: ")]) == 3:
                path.unlink()
        return chunks

    got = collect(asyncio.run(asyncio.wait_for(scenario(), 20)))
    assert got[-1].event == "error" and got[-1].data["code"] == "not_found"
    assert [e.event for e in got].count("item") == 3
