# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T04 session service over synthetic stores in tmp_path (never the user's real folders)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError
from session_fixtures import (
    CLAUDE_ID,
    CLAUDE_OTHER,
    CODEX_ID,
    COPILOT_ID,
    add_framework_run,
    make_services,
    make_stores,
    settings_for,
    write_claude,
    write_codex,
    write_copilot,
)


@pytest.fixture
def stores(tmp_path):
    return make_stores(tmp_path)


@pytest.fixture
def services(stores):
    services = make_services(stores)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    yield services
    services.runs.close()


def error_of(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def ids(page: dict) -> list[str]:
    return [s["id"] for s in page["sessions"]]


# -------- the unified list --------
def test_lists_every_source_newest_first_with_framework_runs(services):
    run_id = add_framework_run(services, model="m1")
    page = services.sessions.list_sessions()
    assert set(ids(page)) == {f"claude:{CLAUDE_ID}", f"codex:{CODEX_ID}", f"copilot:{COPILOT_ID}", f"framework:{run_id}"}
    assert ids(page)[0] == f"framework:{run_id}"  # created just now
    assert page["next_cursor"] is None and page["hint"] is None
    updated = [s["updated_at"] for s in page["sessions"]]
    assert updated == sorted(updated, reverse=True)
    json.dumps(page)  # JSON-ready


def test_framework_run_maps_to_a_summary(services):
    run_id = add_framework_run(services, model="m1")
    plain = add_framework_run(services, "plain")
    summary = services.sessions.get_session(f"framework:{run_id}")
    assert summary["source"] == "framework" and summary["surface"] == "framework" and summary["native_id"] == run_id
    assert summary["title"] == "social_post" and summary["cwd"] is None and summary["parent_id"] is None
    assert summary["models"] == ["m1"] and summary["reader"] == "framework-runs/1"
    assert summary["tokens"] == {"input": 17, "output": 4, "cache_read": None, "cache_write": None}  # one record had no output
    assert summary["status"] == "active" and summary["created_at"].endswith("Z")
    assert summary["open_in_app"] == {"kind": "url", "value": f"/gui/#run={run_id}", "needs_confirmation": False}
    unknown = services.sessions.get_session(f"framework:{plain}")
    assert unknown["tokens"] is None and unknown["models"] == []  # never a made-up zero
    listed = {s["id"]: s for s in services.sessions.list_sessions()["sessions"]}
    assert listed[f"framework:{run_id}"] == summary and listed[f"framework:{plain}"] == unknown


def test_framework_events_become_items_with_run_event_cursors(services):
    run_id = add_framework_run(services, events=3)
    sid = f"framework:{run_id}"
    page = services.sessions.read_items(sid)
    assert [i["kind"] for i in page["items"]] == ["system"] * 4  # workflow_started + 3
    assert page["items"][0]["text"].startswith("workflow_started ") and "social_post" in page["items"][0]["text"]
    assert page["items"][1]["text"].startswith("step_completed ") and '"step_id": "s0"' in page["items"][1]["text"]
    cursors = [int(i["cursor"]) for i in page["items"]]
    assert cursors == sorted(cursors) and page["next_cursor"] == str(cursors[-1]) and page["done"] is True
    first = services.sessions.read_items(sid, limit=2)
    assert len(first["items"]) == 2 and first["done"] is False
    rest = services.sessions.read_items(sid, after=first["next_cursor"], limit=2)
    assert [i["cursor"] for i in first["items"] + rest["items"]] == [i["cursor"] for i in page["items"]]
    assert rest["done"] is True
    assert services.sessions.read_items(sid, after=page["next_cursor"])["items"] == []
    assert services.sessions.read_items(sid, after="0")["items"] == page["items"]
    other = add_framework_run(services, "other", events=0)  # another run's events never leak in
    assert [i["text"].split(" ")[0] for i in services.sessions.read_items(f"framework:{other}")["items"]] == ["workflow_started"]


# -------- allowed folders and sources --------
def test_empty_roots_lists_nothing_and_hints(stores):
    services = make_services(stores, gui_session_roots=[])
    write_claude(stores)
    add_framework_run(services)
    page = services.sessions.list_sessions()
    assert page["sessions"] == [] and page["next_cursor"] is None
    assert "gui_session_roots" in page["hint"] and page["hint"].startswith("configure_roots")
    assert any(w.startswith("no_allowed_roots") for w in page["warnings"])
    assert error_of(services.sessions.get_session, f"claude:{CLAUDE_ID}").status == 404
    services.runs.close()


def test_sessions_outside_the_allowed_folders_are_hidden(services, stores):
    write_claude(stores, CLAUDE_OTHER, cwd=stores.other)
    assert f"claude:{CLAUDE_OTHER}" not in ids(services.sessions.list_sessions())
    for call in (services.sessions.get_session, services.sessions.read_items):
        err = error_of(call, f"claude:{CLAUDE_OTHER}")
        assert (err.code, err.status) == ("not_found", 404)
    # a sibling whose name only starts like the root is not inside it
    sibling = stores.tmp / "work-evil"
    sibling.mkdir()
    write_claude(stores, "11111111-1111-4111-8111-cccccccccccc", cwd=sibling)
    assert len(services.sessions.list_sessions(source="claude")["sessions"]) == 1


def test_changing_the_roots_changes_what_is_shown(services, stores):
    assert len(services.sessions.list_sessions(source="claude")["sessions"]) == 1
    services.settings.gui_session_roots = [str(stores.other)]
    assert services.sessions.list_sessions(source="claude")["sessions"] == []
    services.settings.gui_session_roots = [str(stores.roots)]
    assert len(services.sessions.list_sessions(source="claude")["sessions"]) == 1


def test_sources_can_be_switched_off(services):
    run_id = add_framework_run(services)
    services.settings.gui_session_sources = ["codex"]
    assert ids(services.sessions.list_sessions()) == [f"codex:{CODEX_ID}"]
    assert error_of(services.sessions.get_session, f"claude:{CLAUDE_ID}").code == "not_found"
    assert error_of(services.sessions.get_session, f"framework:{run_id}").code == "not_found"
    assert error_of(services.sessions.read_items, f"framework:{run_id}").code == "not_found"
    services.settings.gui_session_sources = ["framework"]
    assert ids(services.sessions.list_sessions()) == [f"framework:{run_id}"]


def test_store_overrides_are_used_and_nothing_else_is_scanned(services, stores):
    config = services.sessions._get_registry().config
    assert (config.claude_home, config.codex_home, config.copilot_home, config.claude_desktop_dir) == (
        stores.claude, stores.codex, stores.copilot, stores.desktop)
    assert config.allowed_roots == (str(stores.roots),)
    services.settings.executor_workdir_root.mkdir(parents=True)
    assert services.sessions._get_registry().config.allowed_roots == (
        str(stores.roots), str(services.settings.executor_workdir_root))


# -------- filters --------
def test_filters(services, stores):
    run_id = add_framework_run(services)
    sessions = services.sessions
    assert ids(sessions.list_sessions(source="codex")) == [f"codex:{CODEX_ID}"]
    assert ids(sessions.list_sessions(source="framework")) == [f"framework:{run_id}"]
    assert ids(sessions.list_sessions(surface="framework")) == [f"framework:{run_id}"]
    surfaces = {s["id"]: s["surface"] for s in sessions.list_sessions()["sessions"]}
    assert surfaces[f"claude:{CLAUDE_ID}"] == "cli" and surfaces[f"framework:{run_id}"] == "framework"
    for surface in ("cli", "desktop", "unknown"):
        assert set(ids(sessions.list_sessions(surface=surface))) == {i for i, s in surfaces.items() if s == surface}
    assert ids(sessions.list_sessions(text="CLAUDE work")) == [f"claude:{CLAUDE_ID}"]
    assert ids(sessions.list_sessions(text="social")) == [f"framework:{run_id}"]  # the title of a run is its workflow
    # a folder filter keeps sessions under it; runs have no folder, so they drop out
    assert set(ids(sessions.list_sessions(cwd=str(stores.work)))) == {
        f"claude:{CLAUDE_ID}", f"codex:{CODEX_ID}", f"copilot:{COPILOT_ID}"}
    assert sessions.list_sessions(cwd=str(stores.other))["sessions"] == []
    # date range on the last update
    assert set(ids(sessions.list_sessions(until="2026-10-02T23:59:59Z"))) == {f"claude:{CLAUDE_ID}", f"codex:{CODEX_ID}"}
    assert f"claude:{CLAUDE_ID}" not in ids(sessions.list_sessions(since="2026-10-02"))
    assert ids(sessions.list_sessions(since="2026-10-01T10:00:00Z", until="2026-10-01T11:00:00Z")) == [f"claude:{CLAUDE_ID}"]
    assert sessions.list_sessions(since="2999-01-01")["sessions"] == []


def test_text_search_matches_title_folder_source_and_framework_source_not_messages(services, stores):
    run_id = add_framework_run(services)
    sessions = services.sessions

    assert ids(sessions.list_sessions(text="CLAUDE work")) == [f"claude:{CLAUDE_ID}"]
    assert set(ids(sessions.list_sessions(text=str(stores.work)))) == {
        f"claude:{CLAUDE_ID}", f"codex:{CODEX_ID}", f"copilot:{COPILOT_ID}"}
    assert ids(sessions.list_sessions(text="FRAMEWORK")) == [f"framework:{run_id}"]
    assert sessions.list_sessions(text="private reasoning")["sessions"] == []


def test_text_search_matches_office_metadata_on_framework_runs(services, monkeypatch):
    run_id = add_framework_run(services, name="office-search")
    monkeypatch.setattr(services.runs, "_runtimes", lambda: [
        SimpleNamespace(bundle="office-bundle", store=services.system),
    ])
    monkeypatch.setattr(services.offices, "office_of", lambda _bundle: ("office-id", "area-id"))
    monkeypatch.setattr(services.offices, "get", lambda _office_id: {"name": "Northern office"})

    assert ids(services.sessions.list_sessions(text="northern office")) == [f"framework:{run_id}"]
    assert ids(services.sessions.list_sessions(text="office-id")) == [f"framework:{run_id}"]
    summary = services.sessions.list_sessions(text="northern office")["sessions"][0]
    assert not {"office_id", "office_name"} & summary.keys()

    def missing_office(_office_id):
        raise ServiceError("office_not_installed", "Office was removed", status=404)

    monkeypatch.setattr(services.offices, "get", missing_office)
    assert ids(services.sessions.list_sessions(text="office-id")) == [f"framework:{run_id}"]


def test_text_search_matches_project_metadata_on_runs_and_linked_sessions(services):
    run_id = add_framework_run(services, name="run-project-search")
    projects = services.projects.store
    with projects.db.tx() as conn:
        projects.insert_project(
            conn, "linked-project-search", "Linked project title", visibility="private", created_by="test")
    services.tasks.create_group("linked-project-group", "Linked project group", actor="test")
    with projects.db.tx() as conn:
        assert projects.attach_group(conn, "linked-project-group", "linked-project-search", 1)
    services.tasks.create_task("linked-project-group", "linked-task-search", "Linked task", actor="test")
    with projects.db.tx() as conn:
        projects.add_session_link(conn, "linked-task-search", f"claude:{CLAUDE_ID}", linked_by="test")

    assert ids(services.sessions.list_sessions(text="run-project-search")) == [f"framework:{run_id}"]
    assert ids(services.sessions.list_sessions(text="linked project title")) == [f"claude:{CLAUDE_ID}"]
    assert ids(services.sessions.list_sessions(text="linked-task-search")) == [f"claude:{CLAUDE_ID}"]
    summary = services.sessions.list_sessions(text="linked project title")["sessions"][0]
    assert (summary["project_id"], summary["project_title"]) == ("linked-project-search", "Linked project title")  # PRY-T11


@pytest.mark.parametrize("filters", [
    {"source": "nope"}, {"surface": "tablet"}, {"since": "yesterday-ish"}, {"until": "2026-13-45"},
    {"limit": 0}, {"limit": 501}, {"limit": True}, {"limit": "5"}, {"text": "x" * 201}, {"cwd": "x" * 1001},
])
def test_invalid_filters_are_422(services, filters):
    err = error_of(services.sessions.list_sessions, **filters)
    assert (err.code, err.status) == ("invalid_request", 422)


def test_list_pagination_covers_every_session_once(services):
    add_framework_run(services)
    everything = ids(services.sessions.list_sessions())
    assert len(everything) == 4
    seen, after, pages = [], None, 0
    while True:
        page = services.sessions.list_sessions(limit=3, after=after)
        seen += ids(page)
        pages += 1
        after = page["next_cursor"]
        if after is None:
            break
    assert pages == 2 and seen == everything
    assert services.sessions.list_sessions(limit=3, after="0:3")["next_cursor"] is None
    assert services.sessions.list_sessions(after="0:99")["sessions"] == []


@pytest.mark.parametrize("cursor", ["abc", "1", "0:-1", "5:3", "0:1:2", "0:", ":3"])
def test_invalid_list_cursor_is_400(services, cursor):
    err = error_of(services.sessions.list_sessions, after=cursor)
    assert (err.code, err.status) == ("invalid_cursor", 400)


# -------- one session and its items --------
def test_each_file_source_has_summary_and_items(services):
    claude = services.sessions.get_session(f"claude:{CLAUDE_ID}")
    assert claude["title"] == "Claude work" and claude["surface"] == "cli" and claude["models"] == ["claude-test"]
    assert claude["tokens"]["input"] == 10 and claude["open_in_app"]["kind"] == "command"
    codex = services.sessions.get_session(f"codex:{CODEX_ID}")
    assert codex["source"] == "codex" and codex["cwd"].endswith("proj")
    copilot = services.sessions.get_session(f"copilot:{COPILOT_ID}")
    assert copilot["title"] == "Copilot ws" and copilot["models"] == ["gpt-cop"]
    assert [i["text"] for i in services.sessions.read_items(f"codex:{CODEX_ID}")["items"]] == ["codex prompt", "codex answer"]
    assert [i["text"] for i in services.sessions.read_items(f"copilot:{COPILOT_ID}")["items"]] == ["copilot prompt", "copilot answer"]


def test_items_are_plain_text_and_reasoning_is_omitted(services):
    items = services.sessions.read_items(f"claude:{CLAUDE_ID}")["items"]
    assert [i["kind"] for i in items] == ["user_message", "reasoning_omitted", "assistant_message"]
    assert items[0]["text"] == "hola <b>mundo</b> <script>x()</script>"  # literal, never interpreted
    assert items[1]["text"] is None and "private reasoning" not in json.dumps(items)


def test_item_pagination_follows_next_cursor(services):
    sid = f"claude:{CLAUDE_ID}"
    full = services.sessions.read_items(sid)
    assert full["done"] is True and full["next_cursor"].startswith("0:")
    seen, after = [], None
    for _ in range(10):
        page = services.sessions.read_items(sid, after=after, limit=1)
        seen += [i["kind"] for i in page["items"]]
        after = page["next_cursor"]
        if page["done"]:
            break
    assert seen == [i["kind"] for i in full["items"]]
    assert services.sessions.read_items(sid, after=after)["items"] == []
    assert services.sessions.read_items(sid, limit=500)["items"] == full["items"]


@pytest.mark.parametrize("cursor", ["abc", "1", "0:-1", "5:3", "0:1:2", "framework"])
def test_invalid_item_cursor_is_400(services, cursor):
    err = error_of(services.sessions.read_items, f"claude:{CLAUDE_ID}", after=cursor)
    assert (err.code, err.status) == ("invalid_cursor", 400)
    run_id = add_framework_run(services)
    assert error_of(services.sessions.read_items, f"framework:{run_id}", after="0:5").code == "invalid_cursor"
    assert error_of(services.sessions.read_items, f"framework:{run_id}", after="-1").status == 400


@pytest.mark.parametrize("limit", [0, 501, -1, True, 2.5, "7"])
def test_invalid_items_limit_is_422(services, limit):
    err = error_of(services.sessions.read_items, f"claude:{CLAUDE_ID}", limit=limit)
    assert (err.code, err.status) == ("invalid_request", 422)


@pytest.mark.parametrize("session_id", [
    "", "nocolon", "evil:x", "claude:", "claude:not-a-uuid", "claude:../../etc/passwd", f"claude:{CLAUDE_ID}:zzz",
    f"claude:{CLAUDE_ID}:agent-../x", f"codex:{CODEX_ID}:agent-1", "framework:", "framework:a/b", "framework:" + "a" * 65,
    "x" * 201, None, 5,
])
def test_malformed_ids_are_400(services, session_id):
    for call in (services.sessions.get_session, services.sessions.read_items):
        err = error_of(call, session_id)
        assert (err.code, err.status) == ("invalid_session_id", 400)


def test_unknown_but_wellformed_ids_are_404(services):
    for session_id in (f"claude:{CLAUDE_OTHER}", f"codex:{COPILOT_ID}", f"copilot:{CLAUDE_ID}", "framework:" + "0" * 32):
        for call in (services.sessions.get_session, services.sessions.read_items):
            assert (error_of(call, session_id).code, error_of(call, session_id).status) == ("not_found", 404)


def test_claude_subagent_ids_validate_and_resolve(services, stores):
    from session_fixtures import jsonl

    main = write_claude(stores)
    jsonl(main.parent / CLAUDE_ID / "subagents" / "agent-abc123.jsonl", [
        {"type": "user", "timestamp": "2026-10-01T10:00:08Z", "message": {"role": "user", "content": "child task"}}])
    child = services.sessions.get_session(f"claude:{CLAUDE_ID}:agent-abc123")
    assert child["parent_id"] == f"claude:{CLAUDE_ID}"
    assert services.sessions.read_items(child["id"])["items"][0]["text"] == "child task"
    assert services.sessions.get_session(f"claude:{CLAUDE_ID}")["child_count"] == 1


# -------- following --------
def test_open_follow_validates_before_streaming(services):
    sid = f"claude:{CLAUDE_ID}"
    follow = services.sessions.open_follow(sid)
    assert (follow.cursor, follow.is_framework) == ("0:0", False)
    end = services.sessions.read_items(sid)["next_cursor"]
    assert services.sessions.open_follow(sid, after=end).cursor == end
    # the reconnect header wins when it is later; a bad header is ignored; a bad `after` is an error
    assert services.sessions.open_follow(sid, after="0:5", last_event_id=end).cursor == end
    assert services.sessions.open_follow(sid, after=end, last_event_id="0:5").cursor == end
    assert services.sessions.open_follow(sid, after=None, last_event_id="garbage").cursor == "0:0"
    assert error_of(services.sessions.open_follow, sid, after="nope").code == "invalid_cursor"
    assert error_of(services.sessions.open_follow, "claude:x").code == "invalid_session_id"
    assert error_of(services.sessions.open_follow, f"claude:{CLAUDE_OTHER}").code == "not_found"
    run_id = add_framework_run(services)
    framework = services.sessions.open_follow(f"framework:{run_id}", after="3", last_event_id="7")
    assert framework.is_framework and framework.run_id == run_id and framework.cursor == "7" and framework.store is not None
    assert error_of(services.sessions.open_follow, f"framework:{run_id}", after="0:1").code == "invalid_cursor"


def test_poll_items_reads_only_what_was_appended(services, stores):
    from session_fixtures import append, claude_user

    sid = f"claude:{CLAUDE_ID}"
    page = services.sessions.poll_items(sid, None)
    assert len(page["items"]) == 3
    assert services.sessions.poll_items(sid, page["next_cursor"])["items"] == []
    append(stores.claude / "projects" / "proj" / f"{CLAUDE_ID}.jsonl", [claude_user("later", "2026-10-01T10:01:00Z")])
    more = services.sessions.poll_items(sid, page["next_cursor"])
    assert [i["text"] for i in more["items"]] == ["later"]


# -------- configuration --------
def test_settings_default_to_no_roots_and_all_sources():
    settings = Settings(_env_file=None)
    assert settings.gui_session_roots == [] and settings.gui_session_sources == ["claude", "codex", "copilot", "antigravity", "framework"]
    assert settings.gui_claude_home is None and settings.gui_codex_home is None
    assert settings.gui_copilot_home is None and settings.gui_claude_desktop_dir is None


def test_settings_read_lists_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GUI_SESSION_ROOTS", f"{tmp_path / 'a'};{tmp_path / 'b'}; ")
    monkeypatch.setenv("GUI_SESSION_SOURCES", '["codex", "framework"]')
    monkeypatch.setenv("GUI_CODEX_HOME", str(tmp_path / "codex-home"))
    settings = Settings(_env_file=None)
    assert settings.gui_session_roots == [str(tmp_path / "a"), str(tmp_path / "b")]
    assert settings.gui_session_sources == ["codex", "framework"] and settings.gui_codex_home == tmp_path / "codex-home"
    monkeypatch.setenv("GUI_SESSION_ROOTS", '["x", "x", "y"]')
    assert Settings(_env_file=None).gui_session_roots == ["x", "y"]
    monkeypatch.setenv("GUI_SESSION_ROOTS", "")
    assert Settings(_env_file=None).gui_session_roots == []


def test_settings_reject_unknown_sources_and_blank_roots():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gui_session_sources=["claude", "slack"])
    with pytest.raises(ValidationError):
        Settings(_env_file=None, gui_session_roots=["ok", "  "])


def test_facade_exposes_sessions(services):
    assert isinstance(services, ApplicationServices) and services.sessions is services.sessions
    assert settings_for(make_stores(services.settings.local_dir.parent / "again")).gui_session_roots


def test_blank_filters_mean_no_filter(services):
    everything = services.sessions.list_sessions()
    blank = services.sessions.list_sessions(source="", surface="", cwd="", since="", until="", text="", after="")
    assert blank == everything and len(everything["sessions"]) == 3


def test_the_registry_follow_read_skips_the_folder_check_so_open_follow_must_run_first(services, stores):
    from multiagent.sessions import InvalidCursor, InvalidSessionId, SessionRegistry

    write_claude(stores, CLAUDE_OTHER, cwd=stores.other)  # outside the allowed folders
    registry = SessionRegistry(services.sessions._get_registry().config)
    outside = f"claude:{CLAUDE_OTHER}"
    assert registry.read_items_unchecked(outside).items  # documented: no folder check here
    assert error_of(services.sessions.open_follow, outside).code == "not_found"  # the check lives in open_follow
    with pytest.raises(InvalidCursor):
        registry.read_items_unchecked(f"claude:{CLAUDE_ID}", after="nope")
    with pytest.raises(InvalidSessionId):
        registry.read_items_unchecked("nope")
