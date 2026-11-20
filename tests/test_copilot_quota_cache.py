# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Copilot quota checks use only synthetic cache files under tmp_path."""
from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.sessions.usage_sources import MAX_STATUSLINE_BYTES, read_copilot_quota_cache
from session_fixtures import make_services, make_stores

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
RESET = "2026-12-01T00:00:00Z"
PRIVATE = "SYNTHETIC_PRIVATE_MARKER_731"


COMMENTS = "// synthetic cache header, not real" + chr(10) + "// second synthetic comment line" + chr(10)


def entry(*, when="2026-11-20T10:00:00Z", remaining=40, unlimited=False, overage=False):
    """The real shape: ``retrievedAt`` beside ``response``, which holds the quota; invented values only."""
    return {"retrievedAt": when, "response": {
        "login": PRIVATE, "analytics_tracking_id": PRIVATE, "endpoints": {"api": PRIVATE},
        "organization_list": [PRIVATE], "copilot_plan": PRIVATE,
        "quota_reset_date_utc": RESET,
        "quota_snapshots": {"premium_interactions": {
            "entitlement": 100, "remaining": remaining, "quota_remaining": remaining,
            "percent_remaining": remaining, "unlimited": unlimited,
            "overage_permitted": overage, "timestamp_utc": when, "secret_extra": PRIVATE}}}}


def wrap(*entries, comments=True):
    """Wrapper object with one entry per period, each under its own (synthetic) key."""
    body = {"copilotUserCache": {f"period-{index}-{PRIVATE}": item for index, item in enumerate(entries)}}
    return (COMMENTS if comments else "") + json.dumps(body)


def write(path, data):
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def copilot(plans):
    return next(row for row in plans["apps"] if row["app"] == "copilot")


def make_world(tmp_path, data):
    path = write(tmp_path / "copilot-user-cache.json", data)
    services = make_services(make_stores(tmp_path), usage_copilot_cache_file=path)
    services.usage.clock = lambda: NOW
    services.connections.clock = lambda: NOW
    services.connections.detector = lambda **kw: {"contexts": {"packaged_process": {"apps": [
        {"app": "copilot", "detection": "detected", "version": "1", "session": {"state": "unknown"}}]}}}
    services.executors.status = lambda: {"executors": [
        {"executor_id": "copilot_cli", "enabled": True, "pause_reason": None}]}
    return services, path


def test_cache_reader_selects_latest_entry_and_only_keeps_allowed_facts(tmp_path):
    path = write(tmp_path / "cache.json", wrap(entry(when="2026-11-19T10:00:00Z", remaining=70),
                                               entry(remaining=40), entry(when="2026-10-01T10:00:00Z", remaining=5)))
    result = read_copilot_quota_cache(path, NOW)
    assert result is not None and result.remaining == 40 and result.entitlement == 100
    assert result.resets_at.isoformat() == "2026-12-01T00:00:00+00:00"
    assert PRIVATE not in repr(result)


def test_comment_lines_are_skipped_and_plain_json_still_reads(tmp_path):
    assert read_copilot_quota_cache(write(tmp_path / "a.json", wrap(entry())), NOW).remaining == 40
    assert read_copilot_quota_cache(write(tmp_path / "b.json", wrap(entry(), comments=False)), NOW).remaining == 40
    flat = {"retrievedAt": "2026-11-20T10:00:00Z", **entry()["response"]}
    assert read_copilot_quota_cache(write(tmp_path / "c.json", flat), NOW).remaining == 40


def test_entries_deeper_than_the_cap_are_not_found(tmp_path):
    deep = {"a": {"b": {"c": {"d": entry()}}}}
    assert read_copilot_quota_cache(write(tmp_path / "cache.json", COMMENTS + json.dumps(deep)), NOW) is None
    assert read_copilot_quota_cache(write(tmp_path / "ok.json", {"a": {"b": entry()}}), NOW).remaining == 40


def test_negative_remaining_reads_and_is_a_limit(tmp_path):
    result = read_copilot_quota_cache(write(tmp_path / "cache.json", wrap(entry(remaining=-3))), NOW)
    assert result is not None and result.remaining == -3


def test_newest_incomplete_entry_does_not_revive_an_older_quota(tmp_path):
    older = entry(when="2026-11-19T10:00:00Z")
    newest = {"retrievedAt": "2026-11-20T10:00:00Z", "response": {"quota_reset_date_utc": RESET}}
    assert read_copilot_quota_cache(write(tmp_path / "cache.json", wrap(older, newest)), NOW) is None


@pytest.mark.parametrize("data", [
    wrap(entry(when="2026-10-20T10:00:00Z")),
    wrap({"retrievedAt": "2026-11-20T10:00:00Z", "response": {"quota_reset_date_utc": RESET}}),
    wrap({**entry(), "response": {**entry()["response"], "quota_reset_date_utc": None}}),
])
def test_invalid_cache_has_no_quota(tmp_path, data):
    assert read_copilot_quota_cache(write(tmp_path / "cache.json", data), NOW) is None


def test_broken_and_oversized_cache_have_no_quota(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text("{broken", encoding="utf-8")
    assert read_copilot_quota_cache(path, NOW) is None
    path.write_bytes(b"x" * (MAX_STATUSLINE_BYTES + 1))
    assert read_copilot_quota_cache(path, NOW) is None


def test_stale_cache_keeps_existing_unknown_session_result(tmp_path):
    services, _ = make_world(tmp_path, wrap(entry(when="2026-10-20T10:00:00Z")))
    try:
        plan = copilot(services.usage.plans())
        row = services.connections.check("copilot_cli")
        assert plan["source"] is None and plan["status"] == "not_available"
        assert row["reason"] == "session_unverifiable" and row["verification"] == "unverified"
        assert row["limit"] is None
    finally:
        services.runs.close()


@pytest.mark.parametrize("remaining,unlimited,overage,expected", [
    (40, False, False, None), (0, False, False, "limit_reached"),
    (0, False, True, "limit_reached"), (0, True, False, None),
    (-3, False, False, "limit_reached"), (-3, False, True, "limit_reached"), (-3, True, False, None),
])
def test_cache_controls_eligibility_and_hard_limit(tmp_path, remaining, unlimited, overage, expected):
    services, _ = make_world(tmp_path, wrap(entry(remaining=remaining, unlimited=unlimited, overage=overage)))
    try:
        plan = copilot(services.usage.plans())
        row = services.connections.check("copilot_cli")
        assert plan["source"]["kind"] == "copilot_cache"
        assert plan["credits"]["remaining"] == remaining
        assert plan["windows"][0]["resets_at"] == RESET
        assert row["session"] == "signed_in" and row["verification"] == "verified"
        assert row["reason"] == expected and row["available"] is (expected is None)
        assert row["limit"] is None if expected is None else row["limit"]["resets_at"] == RESET
    finally:
        services.runs.close()


def test_cache_excludes_private_fields_from_surfaces_logs_and_telemetry(tmp_path, monkeypatch, caplog):
    services, _ = make_world(tmp_path, wrap(entry()))
    token = ensure_token(services.settings.gui_token_file)
    monkeypatch.setattr(cli, "_usage_services", lambda: services.usage)
    monkeypatch.setattr(cli, "get_settings", lambda: services.settings)
    monkeypatch.setattr(ApplicationServices, "create", lambda *args, **kwargs: services)
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as http:
            api = http.get("/api/v1/connections").json()
            gui = http.get("/gui/api/usage/plans", headers={"Authorization": f"Bearer {token}"}).json()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_usage_report(cli.build_parser().parse_args(["usage-report", "plans"]))
            cli.cmd_connections(cli.build_parser().parse_args(["connections", "--check", "copilot_cli"]))
        with services.system.store.db.connect() as conn:
            telemetry = [tuple(row) for row in conn.execute("SELECT * FROM telemetry")]
        exposed = json.dumps([api, gui, services.usage.costs(), out.getvalue(), caplog.text, telemetry], default=str)
        assert PRIVATE not in exposed
        assert copilot(gui)["credits"]["remaining"] == 40
    finally:
        services.runs.close()
