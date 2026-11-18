# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""USO-T02: the usage service and its parity between the facade, the HTTP routes and the CLI.

Synthetic app stores and telemetry in ``tmp_path`` only (``usage_fixtures``); the clock is fixed at ``NOW``.
"""
from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.gui.token import ensure_token
from multiagent.services import ServiceError
from multiagent.services.usage import UsageServices
from session_fixtures import make_services, make_stores
from usage_fixtures import (
    CLAUDE_RESUMED,
    CLAUDE_SESSION,
    CODEX_MAIN,
    CODEX_SPAWNED,
    COPILOT_SESSION,
    NOW,
    SECRET,
    fill_telemetry,
    statusline_snapshot,
    write_claude,
    write_codex,
    write_copilot,
)


def build(tmp_path, *, claude=True, codex=True, copilot=True, **settings):
    stores = make_stores(tmp_path)
    if claude:
        write_claude(stores.claude)
    if codex:
        write_codex(stores.codex)
    if copilot:
        write_copilot(stores.copilot)
    services = make_services(stores, **settings)
    fill_telemetry(services.system.store)
    services.usage.clock = lambda: NOW
    return services


@pytest.fixture
def services(tmp_path):
    built = build(tmp_path)
    yield built
    built.runs.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    built = build(tmp_path)
    token = ensure_token(built.settings.gui_token_file)
    monkeypatch.setattr(cli, "_usage_services", lambda: built.usage)
    with TestClient(create_app(services=built), base_url="http://localhost") as http:
        yield built, http, {"Authorization": f"Bearer {token}"}
    built.runs.close()


def app_entry(plans, app):
    return next(entry for entry in plans["apps"] if entry["app"] == app)


def window_of(entry, name, detail=None):
    return next(w for w in entry["windows"] if w["window"] == name and (detail is None or w["detail"] == detail))


def rows_where(result, **dims):
    return [row for row in result["rows"] if all(row.get(k) == v for k, v in dims.items())]


# --- plans: Codex ---------------------------------------------------------------------------------------------

def test_codex_plan_shows_percent_reset_pace_and_credits(services):
    codex = app_entry(services.usage.plans(), "codex")

    assert codex["status"] == "ok" and codex["plan"] == "plus"
    assert codex["source"] == {"kind": "codex_rollout", "observed_at": "2026-10-11T11:00:00Z"}
    five, weekly = window_of(codex, "five_hour"), window_of(codex, "weekly")
    assert (five["used_percent"], five["state"], five["resets_at"]) == (60.0, "current", "2026-10-11T14:00:00Z")
    assert (five["reset_source"], five["reset_confidence"], five["window_minutes"]) == ("app_epoch", "high", 300)
    assert five["elapsed_percent"] == 60.0            # 09:00 to 14:00, and it is 12:00
    assert (weekly["used_percent"], weekly["elapsed_percent"]) == (11.0, 35.7)
    assert codex["credits"] == {"has_credits": False, "unlimited": False, "balance": "0"}
    assert "five_hour_percent" not in codex["not_available"] and "model_limits" in codex["not_available"]


def test_codex_window_that_reset_after_it_was_observed_has_no_percent(services):
    services.usage.clock = lambda: NOW + timedelta(hours=3)         # 15:00, the 5 hour window reset at 14:00
    codex = app_entry(services.usage.plans(), "codex")

    five = window_of(codex, "five_hour")
    assert (five["state"], five["used_percent"], five["observed_used_percent"]) == ("window_reset", None, 60.0)
    assert five["elapsed_percent"] is None
    assert window_of(codex, "weekly")["state"] == "current"
    assert "window_reset_since_observation" in codex["notes"]
    assert "five_hour_percent" in codex["not_available"]            # the real percent is unknown, not 60 and not 0


def test_codex_at_100_percent_is_a_reached_limit(tmp_path):
    stores = make_stores(tmp_path)
    from usage_fixtures import codex_count, codex_totals, write_jsonl
    write_jsonl(stores.codex / "sessions" / "r" / f"rollout-x-{CODEX_MAIN}.jsonl", [
        codex_count("2026-10-11T11:00:00Z", codex_totals(9, 0, 9, 0), used=100.0)], mtime="2026-10-11T11:00:01Z")
    built = make_services(stores)
    built.usage.clock = lambda: NOW
    try:
        five = window_of(app_entry(built.usage.plans(), "codex"), "five_hour")
        assert (five["state"], five["used_percent"]) == ("limit_reached", 100.0)
    finally:
        built.runs.close()


# --- plans: Claude --------------------------------------------------------------------------------------------

def test_claude_without_a_snapshot_has_only_the_reset_after_a_rejection(services):
    claude = app_entry(services.usage.plans(), "claude")

    assert claude["status"] == "partial" and claude["plan"] is None and claude["source"] is None
    five = window_of(claude, "five_hour")
    assert (five["used_percent"], five["state"], five["resets_at"]) == (None, "limit_reached", "2026-10-11T14:30:00Z")
    assert five["last_hit_at"] == "2026-10-11T11:00:00Z" and five["reset_confidence"] == "high"
    assert "statusline_not_configured" in claude["notes"]
    for missing in ("five_hour_percent", "weekly_percent", "model_limits", "credits_balance", "plan_name"):
        assert missing in claude["not_available"]
    assert "reset_time" not in claude["not_available"]              # a reset instant is known


def test_claude_rejection_that_has_since_reset_is_not_shown_as_current(services):
    services.usage.clock = lambda: NOW + timedelta(hours=4)         # 16:00, reset was 14:30
    five = window_of(app_entry(services.usage.plans(), "claude"), "five_hour")
    assert five["state"] == "window_reset" and five["used_percent"] is None


def test_claude_with_a_statusline_snapshot_shows_the_percent(tmp_path):
    snapshot = statusline_snapshot(tmp_path / "saved" / "status.json")
    built = build(tmp_path, usage_claude_statusline_file=snapshot)
    try:
        claude = app_entry(built.usage.plans(), "claude")
        assert claude["status"] == "ok"
        assert claude["source"] == {"kind": "claude_statusline", "observed_at": "2026-10-11T11:55:00Z"}
        five, weekly = window_of(claude, "five_hour"), window_of(claude, "weekly")
        assert (five["used_percent"], five["state"], five["elapsed_percent"]) == (23.5, "current", 40.0)
        assert weekly["used_percent"] == 41.2
        assert five["last_hit_at"] == "2026-10-11T11:00:00Z"        # the rejection is noted on the same window, not repeated
        assert len([w for w in claude["windows"] if w["window"] == "five_hour"]) == 1
        assert "five_hour_percent" not in claude["not_available"] and "weekly_percent" not in claude["not_available"]
        assert "model_limits" in claude["not_available"]
        assert "statusline_not_configured" not in claude["notes"]
    finally:
        built.runs.close()


def test_claude_with_nothing_on_disk_is_not_available(tmp_path):
    built = build(tmp_path, claude=False)
    try:
        claude = app_entry(built.usage.plans(), "claude")
        assert claude["status"] == "not_available" and claude["windows"] == []
        assert any(note.startswith("source_unavailable") for note in claude["notes"])
        assert "reset_time" in claude["not_available"]
    finally:
        built.runs.close()


def test_a_missing_statusline_file_is_a_note_not_a_failure(tmp_path):
    built = build(tmp_path, usage_claude_statusline_file=tmp_path / "never-written.json")
    try:
        claude = app_entry(built.usage.plans(), "claude")
        assert "statusline_snapshot_missing" in claude["notes"] and claude["status"] == "partial"
    finally:
        built.runs.close()


# --- plans: Copilot -------------------------------------------------------------------------------------------

def test_copilot_shows_credits_this_month_and_the_documented_reset_without_inventing_a_cap(services):
    copilot = app_entry(services.usage.plans(), "copilot")

    assert copilot["status"] == "partial" and copilot["plan"] is None
    assert copilot["credits"] == {"unit": "ai_credit", "used": 5.0, "cap": None, "cap_source": None, "calls": 2,
                                  "month_start": "2026-10-01T00:00:00Z", "unit_confidence": "medium"}
    monthly = window_of(copilot, "monthly")
    assert monthly["resets_at"] == "2026-11-01T00:00:00Z" and monthly["reset_source"] == "rule"
    assert monthly["reset_confidence"] == "medium" and monthly["used_percent"] is None
    assert monthly["last_hit_at"] == "2026-10-10T20:00:00Z" and monthly["state"] == "limit_reached"
    assert monthly["elapsed_percent"] == 33.9                       # 10 days 12 hours of 31 days
    assert "monthly_cap" in copilot["not_available"] and "monthly_percent" in copilot["not_available"]


def test_copilot_percent_appears_only_with_a_configured_cap(tmp_path):
    built = build(tmp_path, usage_copilot_monthly_credits=20)
    try:
        copilot = app_entry(built.usage.plans(), "copilot")
        assert copilot["status"] == "ok" and copilot["credits"]["cap"] == 20 and copilot["credits"]["cap_source"] == "configured"
        assert window_of(copilot, "monthly")["used_percent"] == 25.0
        assert "monthly_cap" not in copilot["not_available"] and "monthly_percent" not in copilot["not_available"]
    finally:
        built.runs.close()


def test_copilot_without_a_database_shows_no_consumption_figure_not_a_zero(tmp_path):
    built = build(tmp_path, copilot=False)
    try:
        copilot = app_entry(built.usage.plans(), "copilot")
        assert copilot["status"] == "not_available" and copilot["credits"] is None
        assert any(note.startswith("source_unavailable") for note in copilot["notes"])
        assert window_of(copilot, "monthly")["resets_at"] == "2026-11-01T00:00:00Z"   # the rule still holds
    finally:
        built.runs.close()


def test_apps_the_viewer_does_not_show_are_disabled(tmp_path):
    built = build(tmp_path, gui_session_sources=["codex", "framework"])
    try:
        plans = built.usage.plans()
        assert [(a["app"], a["status"]) for a in plans["apps"]] == [("claude", "disabled"), ("codex", "ok"), ("copilot", "disabled"), ("antigravity", "disabled")]
        tokens = built.usage.tokens()
        assert set(tokens["apps"]) == {"codex", "framework"}               # a disabled app is left out unless it is asked for
        assert {row["app"] for row in tokens["rows"]} == {"codex", "framework"}
        asked = built.usage.tokens(apps="claude")
        assert asked["apps"]["claude"] == {"status": "disabled", "measurement": "per_message", "events": 0, "warnings": []}
        assert asked["rows"] == []
    finally:
        built.runs.close()


# --- tokens ---------------------------------------------------------------------------------------------------

def test_tokens_per_app_model_level_and_role(services):
    result = services.usage.tokens()

    assert result["window"] == {"since": "2026-10-04T12:00:00Z", "until": "2026-10-11T12:00:00Z"}
    assert result["group_by"] == ["app", "model", "level", "role"]
    big = rows_where(result, app="claude", model="claude-test-big")[0]
    assert (big["role"], big["level"], big["events"]) == ("primary", None, 1)
    assert (big["input"], big["output"], big["cache_read"], big["cache_write"], big["reasoning"], big["total"]) == (
        10, 5, 100, 20, None, 135)
    assert rows_where(result, app="claude", model="claude-test-small", role="delegated")[0]["total"] == 6
    assert rows_where(result, app="claude", model="claude-test-small", role="primary")[0]["total"] == 10
    local = rows_where(result, app="codex", model="local_default")[0]               # catalog id of a classified model
    assert (local["level"], local["role"], local["total"], local["reasoning"]) == ("T1", "primary", 370, 15)
    assert rows_where(result, app="codex", role="delegated", model=None)[0]["total"] == 10
    copilot = rows_where(result, app="copilot")[0]
    assert (copilot["role"], copilot["total"], copilot["input"], copilot["cache_read"]) == ("unknown", 1200, 1000, 50)
    qwen = rows_where(result, app="framework", model="qwen3:8b")[0]                   # a catalog model name
    assert (qwen["level"], qwen["role"], qwen["input"], qwen["output"], qwen["total"]) == ("T1", "primary", 100, 50, 150)


def test_tokens_keep_unreported_numbers_as_none_never_zero(services):
    result = services.usage.tokens()

    partial = rows_where(result, app="framework", model="m-partial")[0]
    assert (partial["input"], partial["output"], partial["total"]) == (70, None, 70)
    assert partial["cache_read"] is None and partial["reasoning"] is None
    # The human step reported no tokens at all: it is counted as an unused record, not as a zero row.
    assert result["apps"]["framework"]["records_without_usage"] == 1
    assert not rows_where(result, app="framework", model=None)


def test_tokens_totals_are_the_sum_of_the_rows(services):
    result = services.usage.tokens()

    assert result["totals"]["events"] == sum(row["events"] for row in result["rows"])
    assert result["totals"]["total"] == sum(row["total"] for row in result["rows"] if row["total"] is not None)
    grouped = services.usage.tokens(group_by="app")
    assert [row["app"] for row in grouped["rows"]] == ["claude", "codex", "copilot", "framework"]
    assert grouped["totals"] == result["totals"]
    by_app = {row["app"]: row["total"] for row in grouped["rows"]}
    assert by_app["claude"] == 135 + 6 + 10 and by_app["copilot"] == 1200
    assert by_app["codex"] == 440 + 30 + 10


def test_tokens_window_is_exact_for_every_source(services):
    result = services.usage.tokens(since="2026-10-11T09:30:00Z", until="2026-10-11T10:30:00Z", group_by="app,role")

    got = {(row["app"], row["role"]): row["total"] for row in result["rows"]}
    assert got == {("claude", "primary"): 135 + 10, ("claude", "delegated"): 6,    # m1 10:00, m2 10:05, m4 10:10
                   ("codex", "delegated"): 30 + 10, ("codex", "primary"): 250,     # growth 09:30-10:30 only
                   ("framework", "primary"): 2000}                                # run-d at 10:00
    assert result["window"] == {"since": "2026-10-11T09:30:00Z", "until": "2026-10-11T10:30:00Z"}


def test_tokens_group_by_day_and_month(services):
    result = services.usage.tokens(since="2026-09-01", until="2026-11-01", group_by="app,month", apps="copilot")
    assert [(r["app"], r["month"], r["total"]) for r in result["rows"]] == [
        ("copilot", "2026-09", 990), ("copilot", "2026-10", 1200 + 600)]
    daily = services.usage.tokens(since="2026-10-01", until="2026-10-12", group_by=["day"], apps=["claude"])
    assert [r["day"] for r in daily["rows"]] == ["2026-10-04", "2026-10-11"]


def test_tokens_apps_filter_accepts_a_list_or_comma_separated_text(services):
    as_text = services.usage.tokens(apps="codex,copilot", group_by="app")
    as_list = services.usage.tokens(apps=["codex", "copilot"], group_by="app")
    assert as_text == as_list and [r["app"] for r in as_text["rows"]] == ["codex", "copilot"]
    assert set(as_text["apps"]) == {"codex", "copilot"}


def test_tokens_say_how_each_source_measures_and_which_one_gave_nothing(tmp_path):
    built = build(tmp_path, copilot=False)
    try:
        result = built.usage.tokens(group_by="app")
        assert result["apps"]["claude"]["measurement"] == "per_message" and result["apps"]["claude"]["status"] == "ok"
        assert result["apps"]["codex"]["measurement"] == "counter_growth"
        assert result["apps"]["copilot"]["status"] == "unavailable"
        assert result["apps"]["copilot"]["warnings"][0].startswith("source_unavailable")
        assert [r["app"] for r in result["rows"]] == ["claude", "codex", "framework"]
        assert "total_tokens" in result["conventions"]["codex"] and "cache_read + cache_write" in result["conventions"]["claude"]
    finally:
        built.runs.close()


@pytest.mark.parametrize("kwargs", [
    {"since": "yesterday"}, {"until": "2026-13-45"}, {"since": 5}, {"since": "2026-10-11T12:00:00Z", "until": "2026-10-11T12:00:00Z"},
    {"since": "2026-10-12T00:00:00Z", "until": "2026-10-11T00:00:00Z"}, {"since": "2024-01-01", "until": "2026-10-11"},
    {"apps": "claude,gemini"}, {"apps": 3}, {"group_by": "app,colour"}, {"group_by": [1]},
])
def test_tokens_reject_bad_windows_apps_and_dimensions(services, kwargs):
    with pytest.raises(ServiceError) as caught:
        services.usage.tokens(**kwargs)
    assert caught.value.code == "invalid_request" and caught.value.status == 422
    assert caught.value.details["field"] in {"since", "until", "apps", "group_by"}


def test_a_naive_window_is_read_as_utc_and_an_offset_is_converted(services):
    naive = services.usage.tokens(since="2026-10-11T09:30:00", until="2026-10-11T10:30:00", group_by="app")
    offset = services.usage.tokens(since="2026-10-11T03:30:00-06:00", until="2026-10-11T10:30:00Z", group_by="app")
    assert naive["window"]["since"] == offset["window"]["since"] == "2026-10-11T09:30:00Z"
    assert naive["rows"] == offset["rows"]


# --- costs ----------------------------------------------------------------------------------------------------

def test_costs_keep_unknown_apart_and_never_add_it_as_zero(services):
    result = services.usage.costs()
    totals = result["totals"]

    assert result["cost_is_estimate"] is True
    assert result["window"] == {"since": "2026-09-10T12:00:00Z", "until": "2026-10-11T12:00:00Z"}
    assert totals["records"] == 7
    assert totals["estimated_cost_usd"] == pytest.approx(0.005 + 0.0046 + 0.0024)
    assert (totals["cost_known_records"], totals["cost_unknown_records"], totals["cost_complete"]) == (3, 3, False)
    assert totals["cost_unknown_reasons"] == {"human_step": 1, "no_price_evidence": 1, "tokens_not_reported": 1}
    assert totals["cost_other_currencies"] == {"EUR": pytest.approx(0.004)}


def test_costs_per_run_most_recent_first_each_with_its_evidence(services):
    runs = services.usage.costs()["by_run"]

    assert [r["run_id"] for r in runs] == ["run-d", "run-b", "run-a", "run-c"]
    run_a = runs[2]
    assert (run_a["records"], run_a["cost_known_records"], run_a["cost_unknown_records"]) == (3, 2, 1)
    assert run_a["estimated_cost_usd"] == pytest.approx(0.0096) and run_a["cost_unknown_reasons"] == {"human_step": 1}
    assert (run_a["first_at"], run_a["last_at"]) == ("2026-10-10T08:00:00Z", "2026-10-10T08:02:00Z")
    assert len(run_a["evidence"]) == 1                             # both calls used the same price evidence
    evidence = run_a["evidence"][0]
    assert (evidence["model_id"], evidence["currency"]) == ("m-priced", "USD")
    assert evidence["input"]["source"] == "unit-test rate card" and evidence["output"]["valid_until"] == "2027-01-01T00:00:00Z"
    assert evidence["input"]["provenance"] == "catalog_declared" and evidence["input"]["amount"] == 2.0
    run_b = runs[1]
    assert run_b["estimated_cost_usd"] is None and run_b["cost_known_records"] == 0     # unknown stays null, not 0
    assert run_b["evidence"] == [] and run_b["cost_unknown_reasons"] == {"no_price_evidence": 1, "tokens_not_reported": 1}
    assert runs[0]["estimated_cost_usd"] is None and runs[0]["cost_other_currencies"] == {"EUR": pytest.approx(0.004)}
    assert runs[0]["evidence"][0]["currency"] == "EUR"


def test_costs_per_model_day_and_month(services):
    result = services.usage.costs()

    assert [m["model"] for m in result["by_model"]] == ["m-euro", "m-partial", "m-priced", "qwen3:8b", ""] or \
        [m["model"] for m in result["by_model"]] == ["", "m-euro", "m-partial", "m-priced", "qwen3:8b"]
    priced = next(m for m in result["by_model"] if m["model"] == "m-priced")
    assert priced["records"] == 3 and priced["cost_complete"] is True
    assert priced["estimated_cost_usd"] == pytest.approx(0.005 + 0.0046 + 0.0024)
    assert [d["day"] for d in result["by_day"]] == ["2026-09-30", "2026-10-10", "2026-10-11"]
    assert [m["month"] for m in result["by_month"]] == ["2026-09", "2026-10"]
    september, october = result["by_month"]
    assert (september["records"], september["estimated_cost_usd"]) == (1, pytest.approx(0.0024))
    assert (october["records"], october["cost_known_records"], october["cost_unknown_records"]) == (6, 2, 3)
    assert october["cost_other_currencies"] == {"EUR": pytest.approx(0.004)}


def test_costs_window_and_run_limit(services):
    narrow = services.usage.costs(since="2026-10-11", until="2026-10-12")
    assert [r["run_id"] for r in narrow["by_run"]] == ["run-d", "run-b"] and narrow["totals"]["records"] == 3

    one = services.usage.costs(limit=1)
    assert [r["run_id"] for r in one["by_run"]] == ["run-d"] and one["runs"] == 4 and one["runs_truncated"] is True
    assert one["totals"]["records"] == 7                           # the totals are never truncated

    empty = services.usage.costs(since="2020-01-01", until="2020-01-02")
    assert empty["by_run"] == [] and empty["totals"]["records"] == 0
    assert empty["totals"]["estimated_cost_usd"] is None and empty["totals"]["cost_complete"] is False


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 501}, {"limit": "5"}, {"limit": True}, {"since": "soon"}])
def test_costs_reject_bad_arguments(services, kwargs):
    with pytest.raises(ServiceError) as caught:
        services.usage.costs(**kwargs)
    assert caught.value.code == "invalid_request" and caught.value.status == 422


def test_costs_read_every_store_including_bundle_stores(services, tmp_path):
    from multiagent.config import Settings
    from multiagent.db import Database
    from multiagent.db.store import Store
    from usage_fixtures import add_telemetry

    settings = Settings(_env_file=None, local_dir=tmp_path / "bundle-local")
    settings.ensure_directories()
    database = Database(settings)
    database.migrate()
    bundle_store = Store(database)
    add_telemetry(bundle_store, run_id="run-bundle", when="2026-10-11T08:00:00Z", model="m-priced", tokens_in=10,
                  tokens_out=10)
    original = services.usage._stores
    services.usage._stores = lambda: [*original(), ("my_bundle", bundle_store)]

    runs = services.usage.costs()["by_run"]
    assert next(r for r in runs if r["run_id"] == "run-bundle")["bundle"] == "my_bundle"
    assert next(r for r in runs if r["run_id"] == "run-a")["bundle"] is None


# --- read-only and private --------------------------------------------------------------------------------------

def snapshot_of(root):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_the_service_changes_nothing_it_reads(services, tmp_path):
    before = snapshot_of(tmp_path / "claude"), snapshot_of(tmp_path / "codex"), snapshot_of(tmp_path / "copilot")
    with services.system.store.db.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]

    services.usage.plans(), services.usage.tokens(), services.usage.costs()

    after = snapshot_of(tmp_path / "claude"), snapshot_of(tmp_path / "codex"), snapshot_of(tmp_path / "copilot")
    assert before == after
    with services.system.store.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0] == rows


def test_no_path_id_prompt_or_secret_leaves_the_service(services, tmp_path):
    answers = [services.usage.plans(), services.usage.tokens(), services.usage.tokens(group_by="day,month"),
               services.usage.costs()]
    text = json.dumps(answers)

    for private in (str(tmp_path), tmp_path.as_posix(), SECRET, CLAUDE_SESSION, CLAUDE_RESUMED, CODEX_MAIN, CODEX_SPAWNED,
                    COPILOT_SESSION, "private answer", "private prompt", "SYNTHETIC", "Request ID", "agent-abc123"):
        assert private not in text, private
    assert json.loads(text) == answers                       # JSON-ready as returned


def test_default_clock_is_the_current_utc_time(services):
    fresh = UsageServices(services)
    assert abs((fresh.clock() - datetime.now(timezone.utc)).total_seconds()) < 5 and fresh.clock().tzinfo is not None
    assert isinstance(services.usage, UsageServices)


def test_settings_reject_a_cap_below_one():
    from multiagent.config import Settings
    with pytest.raises(ValueError):
        Settings(_env_file=None, usage_copilot_monthly_credits=0)
    assert Settings(_env_file=None).usage_copilot_monthly_credits is None
    assert Settings(_env_file=None).usage_claude_statusline_file is None


# --- parity: facade, HTTP and CLI -----------------------------------------------------------------------------

def via_http(http, headers, path, params=None):
    return http.get(path, params=params, headers=headers)


def via_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    status = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(["usage-report", *argv])
        except SystemExit as exc:
            status = exc.code
    return status, out.getvalue(), err.getvalue()


def test_plans_are_the_same_through_the_facade_http_and_cli(world):
    built, http, headers = world
    expected = built.usage.plans()

    response = via_http(http, headers, "/gui/api/usage/plans")
    status, out, _ = via_cli("plans")

    assert response.status_code == 200 and status == 0
    assert response.json() == expected == json.loads(out)
    status, out, _ = via_cli("plans", "--jsonl")
    assert [json.loads(line) for line in out.splitlines()] == expected["apps"]


def test_tokens_are_the_same_through_the_facade_http_and_cli(world):
    built, http, headers = world
    window = {"since": "2026-10-11T09:30:00Z", "until": "2026-10-11T10:30:00Z"}
    expected = built.usage.tokens(**window, apps=["claude", "codex"], group_by=["app", "role", "model"])

    response = via_http(http, headers, "/gui/api/usage/tokens", {**window, "apps": ["claude", "codex"],
                                                                 "group_by": ["app", "role", "model"]})
    status, out, _ = via_cli("tokens", "--since", window["since"], "--until", window["until"],
                             "--app", "claude", "--app", "codex", "--group-by", "app,role,model")

    assert response.status_code == 200 and status == 0
    assert response.json() == expected == json.loads(out)
    assert expected["rows"], "the parity data should not be empty"
    default = built.usage.tokens()
    assert via_http(http, headers, "/gui/api/usage/tokens").json() == default == json.loads(via_cli("tokens")[1])
    status, out, _ = via_cli("tokens", "--jsonl")
    assert [json.loads(line) for line in out.splitlines()] == default["rows"]


def test_costs_are_the_same_through_the_facade_both_http_routes_and_cli(world):
    built, http, headers = world
    expected = built.usage.costs(since="2026-10-01", until="2026-10-12", limit=2)
    params = {"since": "2026-10-01", "until": "2026-10-12", "limit": 2}

    gui = via_http(http, headers, "/gui/api/usage/costs", params)
    public = http.get("/api/v1/usage/costs", params=params)                     # no token: framework data only
    status, out, _ = via_cli("costs", "--since", "2026-10-01", "--until", "2026-10-12", "--limit", "2")

    assert gui.status_code == public.status_code == 200 and status == 0
    assert gui.json() == public.json() == expected == json.loads(out)
    assert expected["runs_truncated"] is True
    assert http.get("/api/v1/usage/costs").json() == built.usage.costs()
    status, out, _ = via_cli("costs", "--jsonl")
    assert [json.loads(line) for line in out.splitlines()] == built.usage.costs()["by_run"]


@pytest.mark.parametrize("path, params, call", [
    ("/gui/api/usage/tokens", {"since": "not a date"}, lambda s: s.tokens(since="not a date")),
    ("/gui/api/usage/tokens", {"apps": ["claude", "gemini"]}, lambda s: s.tokens(apps=["claude", "gemini"])),
    ("/gui/api/usage/tokens", {"group_by": ["colour"]}, lambda s: s.tokens(group_by=["colour"])),
    ("/gui/api/usage/costs", {"limit": 0}, lambda s: s.costs(limit=0)),
    ("/api/v1/usage/costs", {"until": "2026-10-11", "since": "2026-10-12"}, lambda s: s.costs(since="2026-10-12", until="2026-10-11")),
])
def test_errors_are_the_same_through_every_interface(world, path, params, call):
    built, http, headers = world
    with pytest.raises(ServiceError) as caught:
        call(built.usage)
    response = via_http(http, headers, path, params)

    assert response.status_code == caught.value.status == 422
    assert response.json() == {"detail": caught.value.to_payload()}
    argv = {"/gui/api/usage/tokens": ["tokens"], "/gui/api/usage/costs": ["costs"], "/api/v1/usage/costs": ["costs"]}[path]
    for key, value in params.items():
        flag = "--" + key.replace("_", "-") if key != "apps" else "--app"
        for item in (value if isinstance(value, list) else [value]):
            argv += [flag, str(item)]
    status, out, err = via_cli(*argv)
    assert status == 1 and out == "" and f"{caught.value.code}: {caught.value.message}" in err


def test_the_private_usage_routes_need_the_gui_token(world):
    built, http, headers = world
    for path in ("/gui/api/usage/plans", "/gui/api/usage/tokens", "/gui/api/usage/costs"):
        assert http.get(path).status_code == 401
        assert http.get(path, headers={"Authorization": "Bearer wrong-token-value-1234567890"}).status_code == 401
        assert http.get(path, headers=headers).status_code == 200
    # Plan usage and the apps' own tokens are not part of the public API: only the framework's costs are.
    assert http.get("/api/v1/usage/plans").status_code == 404
    assert http.get("/api/v1/usage/tokens").status_code == 404
    assert http.get("/api/v1/usage/costs").status_code == 200


def test_the_cli_command_for_one_run_is_unchanged(world):
    parser = cli.build_parser()
    args = parser.parse_args(["usage", "run-123", "--jsonl"])
    assert (args.command, args.run_id, args.jsonl) == ("usage", "run-123", True)
    assert parser.parse_args(["usage-report", "plans"]).usage_report_command == "plans"
