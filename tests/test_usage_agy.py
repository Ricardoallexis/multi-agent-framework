# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""``usage-report plans`` for Antigravity: a fake runner stands in for ``agy -p /usage``; agy is never started."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from session_fixtures import make_services, make_stores
from usage_fixtures import NOW

from multiagent.services import usage as usage_module

TEXT = (
    "Gemini Models\tWeekly Limit Remaining\t90%\t2026-10-15T10:00:00Z\n"
    "Gemini Models\tFive Hour Limit Remaining\t75%\t2026-10-11T15:00:00Z\n"
    "Claude and GPT models\tWeekly Limit Remaining\t100%\t2026-10-16T10:00:00Z\n"
    "Claude and GPT models\tFive Hour Limit Remaining\t100%\t2026-10-11T16:00:00Z\n"
    "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t40%\t2026-10-15T10:00:00Z\n")


@pytest.fixture
def agy(tmp_path, monkeypatch):
    calls: list[tuple[list[str], float]] = []
    reply = {"value": (0, TEXT)}

    def runner(argv, timeout):
        calls.append((list(argv), timeout))
        value = reply["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(usage_module.shutil, "which", lambda name: "C:/bin/agy.exe" if name == "agy" else None)
    services = make_services(make_stores(tmp_path), mock_mode=False)
    services.usage.clock = lambda: NOW
    services.usage.agy_runner = runner
    yield services, calls, reply, monkeypatch
    services.runs.close()


def entry(services):
    return next(e for e in services.usage.plans()["apps"] if e["app"] == "antigravity")


def test_plans_include_antigravity_with_windows_percent_and_reset(agy):
    services, calls, _, _ = agy
    result = entry(services)
    assert result["status"] == "ok"
    assert result["source"] == {"kind": "agy_usage_command", "observed_at": "2026-10-11T12:00:00Z"}
    by = {(w["group"], w["window"]): w for w in result["windows"]}
    gem_five = by[("Gemini Models", "five_hour")]
    assert (gem_five["used_percent"], gem_five["resets_at"], gem_five["state"]) == (25.0, "2026-10-11T15:00:00Z", "current")
    assert by[("Gemini Models", "weekly")]["used_percent"] == 10.0
    assert by[("Claude and GPT models", "weekly")]["used_percent"] == 0.0
    assert by[("Gemini 4 Argon", "model_weekly")]["used_percent"] == 60.0
    assert len(result["windows"]) == 5
    # one command, no shell, a time limit, only /usage
    assert len(calls) == 1
    argv, timeout = calls[0]
    assert argv == ["C:/bin/agy.exe", "-p", "/usage", "--output-format", "text", "--print-timeout", "12s"]
    assert timeout == 15.0


def test_the_answer_is_reused_for_a_minute(agy):
    services, calls, _, monkeypatch = agy
    entry(services)
    entry(services)
    assert len(calls) == 1
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: 10_000_000.0)
    entry(services)
    assert len(calls) == 2


def test_cached_windows_recompute_reset_and_do_not_share_lists(agy):
    services, calls, _, monkeypatch = agy
    clock = {"now": NOW, "mono": 100.0}
    services.usage.clock = lambda: clock["now"]
    monkeypatch.setattr(usage_module.time, "monotonic", lambda: clock["mono"])

    first = entry(services)
    by_first = {(w["group"], w["window"]): w for w in first["windows"]}
    assert by_first[("Gemini Models", "five_hour")]["state"] == "current"
    assert first["notes"] == []

    first["windows"].clear()
    first["notes"].append("mutated")
    first["not_available"].append("mutated")

    clock["mono"] = 130.0
    clock["now"] = datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
    second = entry(services)
    assert len(calls) == 1
    assert len(second["windows"]) == 5
    by_second = {(w["group"], w["window"]): w for w in second["windows"]}
    assert by_second[("Gemini Models", "five_hour")]["state"] == "window_reset"
    assert by_second[("Gemini Models", "five_hour")]["used_percent"] is None
    assert by_second[("Claude and GPT models", "five_hour")]["state"] == "current"
    assert "window_reset_since_observation" in second["notes"]
    assert "mutated" not in second["notes"]
    assert second["not_available"] == ["plan_name", "credits_balance"]
    assert second["source"] == {"kind": "agy_usage_command", "observed_at": "2026-10-11T12:00:00Z"}


def test_agy_missing_from_path_is_a_note_not_an_error(agy):
    services, calls, _, monkeypatch = agy
    monkeypatch.setattr(usage_module.shutil, "which", lambda name: None)
    result = entry(services)
    assert result["status"] == "not_available" and result["windows"] == []
    assert any(n.startswith("source_unavailable: agy not found") for n in result["notes"]) and calls == []


@pytest.mark.parametrize("value, note", [
    ((2, "boom"), "exit 2"), (RuntimeError("x"), "RuntimeError"), ((0, "Error: not signed in"), "usage_no_limits_found")])
def test_a_failing_agy_never_breaks_the_report(agy, value, note):
    services, _, reply, _ = agy
    reply["value"] = value
    plans = services.usage.plans()
    result = next(e for e in plans["apps"] if e["app"] == "antigravity")
    assert result["status"] == "not_available" and any(note in n for n in result["notes"])
    assert {e["app"] for e in plans["apps"]} >= {"claude", "codex", "copilot", "antigravity"}


def test_mock_mode_never_starts_agy(tmp_path):
    services = make_services(make_stores(tmp_path))
    services.usage.agy_runner = lambda argv, timeout: pytest.fail("agy must not be started in mock mode")
    result = next(e for e in services.usage.plans()["apps"] if e["app"] == "antigravity")
    assert result["status"] == "not_available"
    assert result["not_available"] == ["plan_name", "credits_balance"]
    services.runs.close()


def test_a_disabled_source_is_reported_disabled(tmp_path):
    services = make_services(make_stores(tmp_path), mock_mode=False, gui_session_sources=["claude"])
    result = next(e for e in services.usage.plans()["apps"] if e["app"] == "antigravity")
    assert result == {"app": "antigravity", "status": "disabled"}
    services.runs.close()


def test_tokens_report_no_data_for_antigravity(agy):
    services, _, _, _ = agy
    result = services.usage.tokens(apps="antigravity")
    assert result["rows"] == [] and result["apps"]["antigravity"]["measurement"] == "not_recorded"
    assert result["apps"]["antigravity"]["warnings"][0].startswith("tokens_not_recorded")


def test_run_agy_usage_survives_a_missing_program():
    code, text = usage_module.run_agy_usage(["definitely-not-a-program-xyz", "-p", "/usage"], 5)
    assert code == 1 and text


def test_run_agy_usage_stops_at_the_timeout():
    import sys
    code, text = usage_module.run_agy_usage([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert code == 1 and "timed out" in text
