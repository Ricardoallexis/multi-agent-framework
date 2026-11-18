# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Antigravity (agy) reader and ``/usage`` parser: synthetic stores in ``tmp_path`` only, never the real folder."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from session_fixtures import AGY_CHILD, AGY_ID, agy_gen_blob, agy_row, pb_field, write_antigravity

from multiagent.sessions import (
    InvalidSessionId,
    SessionFilters,
    SessionNotFound,
    SessionRegistry,
    SessionsConfig,
    Source,
    Status,
    Surface,
)
from multiagent.sessions.antigravity import NO_CONTENT, TOKENS_UNKNOWN, decode_gen_usage, workspace_path
from multiagent.sessions.usage_sources import (
    FIVE_HOUR,
    MODEL_WEEKLY,
    RESET_APP_EPOCH,
    RESET_NONE,
    WEEKLY,
    parse_agy_usage,
)

SECRETS = ("PREVIEW-SECRET", "RAW-SECRET", "STEP-SECRET", "GEN-SECRET")


def registry(tmp_path: Path, home: Path | None, roots=None, sources=("antigravity",)) -> SessionRegistry:
    cfg = SessionsConfig(allowed_roots=tuple(roots if roots is not None else (str(tmp_path / "work"),)),
                         sources=tuple(sources), antigravity_home=home)
    return SessionRegistry(cfg)


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work" / "proj"
    folder.mkdir(parents=True)
    return folder


def listing(reg: SessionRegistry):
    return reg.list_sessions(SessionFilters())


# ---------------- reader ----------------
def test_lists_a_session_with_metadata_only(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "agy synthetic title", work.as_uri())])
    page = listing(registry(tmp_path, home))
    assert [s.id for s in page.sessions] == [f"antigravity:{AGY_ID}"]
    s = page.sessions[0]
    assert (s.source, s.surface, s.native_id) == (Source.ANTIGRAVITY, Surface.CLI, AGY_ID)
    assert s.title == "agy synthetic title"
    assert os.path.normcase(s.cwd) == os.path.normcase(str(work))
    assert s.updated_at == "2026-11-17T21:11:05.123Z"  # +01:00 and 7 fractional digits normalized to UTC, milliseconds
    assert s.models == [] and s.tokens is None and s.open_in_app is None
    assert s.status is Status.IDLE and s.parent_id is None and s.child_count == 0
    assert s.reader == "antigravity-summaries/1"
    assert not any(secret in json.dumps(page.to_dict()) for secret in SECRETS)


def test_only_allow_listed_columns_are_selected(tmp_path, work, monkeypatch):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])
    seen: list[str] = []
    real = sqlite3.connect

    def spy(database, *args, **kwargs):
        conn = real(database, *args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr("multiagent.sessions._common.sqlite3.connect", spy)
    listing(registry(tmp_path, home))
    selects = [sql for sql in seen if sql.lstrip().upper().startswith("SELECT") and "conversation_summaries" in sql]
    assert selects
    for sql in selects:
        assert "preview" not in sql and "raw_summary" not in sql and "*" not in sql


def test_databases_are_opened_read_only(tmp_path, work, monkeypatch):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])
    uris: list[str] = []
    real = sqlite3.connect

    def spy(database, *args, **kwargs):
        uris.append(str(database))
        return real(database, *args, **kwargs)

    monkeypatch.setattr("multiagent.sessions._common.sqlite3.connect", spy)
    reg = registry(tmp_path, home)
    listing(reg)
    reg.read_items(f"antigravity:{AGY_ID}")
    assert uris and all(uri.endswith("?mode=ro") for uri in uris)


def test_listing_changes_nothing_on_disk(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])

    def snapshot():
        return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file()}

    before = snapshot()
    reg = registry(tmp_path, home)
    listing(reg)
    reg.get_session(f"antigravity:{AGY_ID}")
    reg.read_items(f"antigravity:{AGY_ID}")
    assert snapshot() == before


def test_read_items_never_returns_conversation_text(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])
    page = registry(tmp_path, home).read_items(f"antigravity:{AGY_ID}")
    assert page.items == [] and page.done is True and page.next_cursor == "0:0"
    assert page.warnings == [NO_CONTENT]


def test_missing_folder_lists_nothing_and_does_not_fail(tmp_path):
    for home in (tmp_path / "nothing-here", None):
        reg = registry(tmp_path, home)
        page = listing(reg)
        assert page.sessions == []
        assert reg.count_sessions() == {"antigravity": {"total": 0, "active": 0, "idle": 0, "archived": 0, "unknown": 0}}
        with pytest.raises(SessionNotFound):
            reg.get_session(f"antigravity:{AGY_ID}")


def test_without_the_summary_database_the_conversation_files_are_listed(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])
    (home / "conversation_summaries.db").unlink()
    reader = registry(tmp_path, home)._readers["antigravity"]
    summaries, warnings = reader.list_summaries()
    assert [s.native_id for s in summaries] == [AGY_ID]
    assert summaries[0].title is None and summaries[0].cwd is None and "no_summary_row" in summaries[0].warnings
    assert summaries[0].updated_at is not None  # the file's modification time
    assert any(w.startswith("summaries_unavailable") for w in warnings)


def test_a_corrupt_summary_database_is_a_warning_not_a_failure(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])
    (home / "conversation_summaries.db").write_bytes(b"this is not a sqlite file" * 40)
    summaries, warnings = registry(tmp_path, home)._readers["antigravity"].list_summaries()
    assert [s.native_id for s in summaries] == [AGY_ID]
    assert any(w.startswith("summaries_unavailable") for w in warnings)


def test_a_summary_table_with_other_columns_is_tolerated(tmp_path):
    home = tmp_path / "agy"
    home.mkdir()
    conn = sqlite3.connect(home / "conversation_summaries.db")
    conn.execute("CREATE TABLE conversation_summaries (conversation_id text, title text)")
    conn.execute("INSERT INTO conversation_summaries VALUES (?, ?)", (AGY_ID, "only two columns"))
    conn.commit()
    conn.close()
    summaries, _ = registry(tmp_path, home)._readers["antigravity"].list_summaries()
    assert [(s.native_id, s.title, s.cwd, s.updated_at) for s in summaries] == [(AGY_ID, "only two columns", None, None)]


def test_status_follows_the_run_state_and_the_clock(tmp_path, work):
    recent = (datetime.now(UTC) - timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")
    stale = "2026-01-01 00:00:00+00:00"
    other = "44444444-4444-4444-8444-666666666666"
    third = "44444444-4444-4444-8444-777777777777"
    home = write_antigravity(tmp_path / "agy", [
        agy_row(AGY_ID, "running now", work.as_uri(), modified=recent, status="CASCADE_RUN_STATUS_RUNNING"),
        agy_row(other, "running long ago", work.as_uri(), modified=stale, status="CASCADE_RUN_STATUS_RUNNING"),
        agy_row(third, "idle but busy flag", work.as_uri(), modified=recent, busy=1)])
    status = {s.native_id: s.status for s in listing(registry(tmp_path, home)).sessions}
    assert status == {AGY_ID: Status.ACTIVE, other: Status.IDLE, third: Status.ACTIVE}


def test_children_come_from_the_parent_column(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [
        agy_row(AGY_ID, "parent", work.as_uri()),
        agy_row(AGY_CHILD, "child", work.as_uri(), parent=AGY_ID, agent="researcher")])
    reg = registry(tmp_path, home)
    sessions = {s.native_id: s for s in listing(reg).sessions}
    assert sessions[AGY_ID].child_count == 1 and sessions[AGY_CHILD].parent_id == f"antigravity:{AGY_ID}"
    children, warnings = reg.list_children(f"antigravity:{AGY_ID}")
    assert warnings == [] and len(children) == 1
    child = children[0]
    assert (child.source, child.kind, child.name, child.link_id) == (
        "antigravity", "subagent", "researcher", f"antigravity:{AGY_CHILD}")
    assert child.tokens is None and child.model is None
    assert reg.list_children(f"antigravity:{AGY_CHILD}")[0] == []


def test_the_allowed_folders_filter_applies(tmp_path, work):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    home = write_antigravity(tmp_path / "agy", [
        agy_row(AGY_ID, "inside", work.as_uri()), agy_row(AGY_CHILD, "outside", elsewhere.as_uri())])
    reg = registry(tmp_path, home)
    assert [s.native_id for s in listing(reg).sessions] == [AGY_ID]
    with pytest.raises(SessionNotFound):
        reg.get_session(f"antigravity:{AGY_CHILD}")
    assert listing(registry(tmp_path, home, roots=())).sessions == []


def test_bad_ids_are_rejected(tmp_path, work):
    reg = registry(tmp_path, write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())]))
    with pytest.raises(SessionNotFound):
        reg.get_session("antigravity:not-a-uuid")
    with pytest.raises(SessionNotFound):
        reg.get_session("antigravity:99999999-9999-4999-8999-999999999999")
    with pytest.raises(InvalidSessionId):
        reg.get_session("antigravity:")


def test_activity_reports_only_a_time(tmp_path, work):
    reg = registry(tmp_path, write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())]))
    data = reg.activity(f"antigravity:{AGY_ID}")
    assert set(data) == {"last_output_at", "source"}
    assert data["source"] == "antigravity_conversation_mtime" and data["last_output_at"]


def test_defaults_point_at_the_gemini_folder_and_the_source_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cfg = SessionsConfig.defaults()
    assert cfg.antigravity_home == tmp_path / ".gemini" / "antigravity-cli"
    assert "antigravity" in cfg.sources
    assert SessionsConfig.defaults(antigravity_home=tmp_path / "x").antigravity_home == tmp_path / "x"


@pytest.mark.parametrize("uris, expected", [
    ('["file:///d:/proj/app"]', "d:/proj/app"),
    ('["file:///d:/my%20proj"]', "d:/my proj"),
    ('["vscode-remote://host/x", "file:///c:/second"]', "c:/second"),
    ('["file:///home/user/app"]', "/home/user/app"),
    ('["https://example.com/x"]', None), ("[]", None), ("not json", None), ('{"a": 1}', None), ("[1, null]", None),
    (None, None),
])
def test_workspace_path(uris, expected):
    got = workspace_path(uris)
    assert (got.replace("\\", "/") if got else got) == expected


def test_workspace_path_prefers_the_git_checkout_over_added_folders(tmp_path):
    # agy stores --add-dir folders next to the launch folder, sorted by name: the checkout is the working folder.
    added, work, venv = tmp_path / "a-python", tmp_path / "b-worktree", tmp_path / "c-venv"
    for folder in (added, work, venv):
        folder.mkdir()
    (work / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")   # a worktree's .git is a file
    uris = json.dumps([added.as_uri(), work.as_uri(), venv.as_uri()])
    assert Path(workspace_path(uris)) == work
    (work / ".git").unlink()
    assert Path(workspace_path(uris)) == added                               # no checkout: the first one


def test_workspace_path_finds_git_in_parent_folder_over_added_dir(tmp_path):
    powershell = tmp_path / "a-program-files" / "PowerShell" / "7"
    powershell.mkdir(parents=True)
    repo = tmp_path / "z-repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / ".local" / "canal"
    sub.mkdir(parents=True)
    uris = json.dumps([powershell.as_uri(), sub.as_uri()])
    assert Path(workspace_path(uris)) == sub


def test_workspace_path_and_registry_prefer_allowed_root_for_deleted_worktree(tmp_path):
    powershell = tmp_path / "a-program-files" / "PowerShell" / "7"
    powershell.mkdir(parents=True)
    allowed_root = tmp_path / "work"
    allowed_root.mkdir()
    deleted_wt = allowed_root / "wt-removed"
    uris = json.dumps([powershell.as_uri(), deleted_wt.as_uri()])
    assert Path(workspace_path(uris, (str(allowed_root),))) == deleted_wt
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "removed worktree", deleted_wt.as_uri())])
    conn = sqlite3.connect(home / "conversation_summaries.db")
    conn.execute("UPDATE conversation_summaries SET workspace_uris = ? WHERE conversation_id = ?", (uris, AGY_ID))
    conn.commit()
    conn.close()
    page = listing(registry(tmp_path, home, roots=(str(allowed_root),)))
    assert [s.native_id for s in page.sessions] == [AGY_ID]
    assert Path(page.sessions[0].cwd) == deleted_wt


# ---------------- tokens (gen_metadata) ----------------
def tokens_of(tmp_path, gens, **extra):
    work = tmp_path / "work" / "proj"
    work.mkdir(parents=True, exist_ok=True)
    row = agy_row(AGY_ID, "t", work.as_uri(), **extra)
    if gens is not None:
        row["gens"] = gens
    home = write_antigravity(tmp_path / "agy", [row])
    return listing(registry(tmp_path, home)).sessions[0]


def test_tokens_are_the_sum_of_the_generation_rows(tmp_path):
    s = tokens_of(tmp_path, [agy_gen_blob(9295, 669, 0, 619), agy_gen_blob(2167, 73, 8599, 24)])
    assert (s.tokens.input, s.tokens.output, s.tokens.cache_read, s.tokens.cache_write, s.tokens.total) == (
        11462, 742, 8599, None, 20803)
    assert TOKENS_UNKNOWN not in s.warnings
    assert "GEN-SECRET" not in json.dumps(s.to_dict())


def test_tokens_are_unknown_without_the_table(tmp_path):
    s = tokens_of(tmp_path, None)
    assert s.tokens is None and TOKENS_UNKNOWN in s.warnings


def test_tokens_are_unknown_with_a_corrupt_or_unexpected_blob(tmp_path):
    bad_blobs = (bytes.fromhex("ffffff"), b"", agy_gen_blob(1, 2)[:-3], pb_field(1, pb_field(5, b"x")),
                 pb_field(1, pb_field(4, pb_field(3, 5))))
    for number, bad in enumerate(bad_blobs):
        s = tokens_of(tmp_path / str(number), [agy_gen_blob(10, 20), bad])
        assert s.tokens is None and TOKENS_UNKNOWN in s.warnings


def test_tokens_sum_when_a_row_omits_output_field(tmp_path):
    no_output_blob = pb_field(1, pb_field(4, pb_field(2, 50) + pb_field(5, 10)))
    s = tokens_of(tmp_path, [agy_gen_blob(100, 20, 30), no_output_blob])
    assert (s.tokens.input, s.tokens.output, s.tokens.cache_read, s.tokens.total) == (150, 20, 40, 210)
    assert TOKENS_UNKNOWN not in s.warnings


def test_tokens_are_unknown_with_no_rows_or_a_missing_conversation_file(tmp_path):
    assert tokens_of(tmp_path, []).tokens is None
    home = tmp_path / "agy"
    (home / "conversations" / f"{AGY_ID}.db").unlink()
    assert listing(registry(tmp_path, home)).sessions[0].tokens is None


def test_decode_gen_usage_shapes():
    assert decode_gen_usage(agy_gen_blob(5, 6, 7, 8)) == {"input": 5, "output": 6, "cache_read": 7, "thinking": 8}
    assert decode_gen_usage(agy_gen_blob(5, 6)) == {"input": 5, "output": 6, "cache_read": 0, "thinking": 0}
    assert decode_gen_usage(pb_field(1, pb_field(4, pb_field(2, 5)))) == {
        "input": 5, "output": 0, "cache_read": 0, "thinking": 0}
    assert decode_gen_usage(pb_field(1, pb_field(4, pb_field(3, 5)))) is None
    for bad in (None, "text", 5, b"", bytes.fromhex("08"), bytes.fromhex("0a05") + b"ab", bytes.fromhex("1b"),
                b"x" * 5 + bytes(1)):
        assert decode_gen_usage(bad) is None
    assert decode_gen_usage(bytes(5 * 1024 * 1024)) is None


def test_list_children_preserves_load_warnings(tmp_path, work):
    home = write_antigravity(tmp_path / "agy", [agy_row(AGY_ID, "t", work.as_uri())])
    (home / "conversation_summaries.db").unlink()
    reader = registry(tmp_path, home)._readers["antigravity"]
    children, warnings = reader.list_children(AGY_ID, datetime.now(UTC), lambda _: None)
    assert children == []
    assert any(w.startswith("summaries_unavailable") for w in warnings)


def test_tokens_of_children_come_from_their_own_conversation(tmp_path):
    work = tmp_path / "work" / "proj"
    work.mkdir(parents=True)
    home = write_antigravity(tmp_path / "agy", [
        {**agy_row(AGY_ID, "p", work.as_uri()), "gens": [agy_gen_blob(1, 1)]},
        {**agy_row(AGY_CHILD, "c", work.as_uri(), parent=AGY_ID), "gens": [agy_gen_blob(100, 20, 50)]}])
    child = registry(tmp_path, home).list_children(f"antigravity:{AGY_ID}")[0][0]
    assert (child.tokens.input, child.tokens.output, child.tokens.cache_read, child.tokens.total) == (100, 20, 50, 170)


# ---------------- /usage parser ----------------
USAGE = (
    "Gemini Models\tWeekly Limit Remaining\t99%\t2026-11-24T10:00:00Z\n"
    "Gemini Models\tFive Hour Limit Remaining\t99%\t2026-11-18T15:00:00Z\n"
    "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-11-25T10:00:00+00:00\n"
    "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-11-18T16:30:00Z\n"
    "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t42.5%\t2026-11-24T10:00:00Z\n")


def test_parse_usage_lines():
    limits, warnings = parse_agy_usage(USAGE)
    assert warnings == []
    assert [(l.name, l.window, l.remaining_percent) for l in limits] == [
        ("Gemini Models", WEEKLY, 99.0), ("Gemini Models", FIVE_HOUR, 99.0),
        ("Claude and GPT models", WEEKLY, 100.0), ("Claude and GPT models", FIVE_HOUR, 100.0),
        ("Gemini 4 Argon", MODEL_WEEKLY, 42.5)]
    assert limits[0].resets_at == datetime(2026, 11, 24, 10, tzinfo=UTC)
    assert limits[2].resets_at == datetime(2026, 11, 25, 10, tzinfo=UTC)
    window = limits[4].to_rate_window()
    assert (window.window, window.used_percent, window.window_minutes, window.reset_source) == (
        MODEL_WEEKLY, 57.5, 10080, RESET_APP_EPOCH)
    assert limits[1].to_rate_window().window_minutes == 300


def test_parse_usage_tolerates_lost_tabs_crlf_and_noise():
    text = ("Usage\r\n\r\nGemini Models Weekly Limit Remaining 7% 2026-11-24T10:00:00Z\r\n"
            "Gemini 4 Argon Gemini 4 Argon Weekly Limit 0% 2026-11-24T10:00:00Z\r\nfooter without numbers\r\n")
    limits, warnings = parse_agy_usage(text)
    assert [(l.name, l.window, l.remaining_percent) for l in limits] == [
        ("Gemini Models", WEEKLY, 7.0), ("Gemini 4 Argon", MODEL_WEEKLY, 0.0)]
    assert warnings == ["usage_lines_skipped: 2"]


def test_parse_usage_bad_values():
    text = ("A\tWeekly Limit Remaining\t140%\t2026-11-24T10:00:00Z\n"
            "B\tWeekly Limit Remaining\t50%\tsoon\n"
            "C\tWeekly Limit Remaining\tabc%\t2026-11-24T10:00:00Z\n")
    limits, warnings = parse_agy_usage(text)
    assert [(l.name, l.resets_at) for l in limits] == [("B", None)]
    assert limits[0].to_rate_window().reset_source == RESET_NONE
    assert "usage_percent_out_of_range: a line was dropped" in warnings
    assert "usage_reset_unreadable: a limit has no reset time" in warnings
    assert "usage_lines_skipped: 1" in warnings


@pytest.mark.parametrize("text", ["", "   \n", "Error: not signed in", None])
def test_parse_usage_without_limits(text):
    limits, warnings = parse_agy_usage(text)
    assert limits == [] and "usage_no_limits_found" in warnings


def test_the_cache_may_exceed_the_input(tmp_path):
    s = tokens_of(tmp_path, [agy_gen_blob(139402, 5000, 1349498, 900)])
    assert (s.tokens.input, s.tokens.cache_read, s.tokens.output, s.tokens.total) == (139402, 1349498, 5000, 1493900)
