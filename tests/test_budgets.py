# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""USO-T03: budgets and alerts over the estimated cost of the framework's own provider calls.

Synthetic telemetry in ``tmp_path`` only (``usage_fixtures``); every amount, project and run is invented; the clock is
fixed at ``NOW`` (2026-10-11 12:00 UTC) unless a test moves it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from multiagent.config import Settings, UsageBudget
from multiagent.run_request import RunRequest
from multiagent.services import ServiceError
from multiagent.services import usage as usage_module
from multiagent.services.usage import BUDGET_EVENT, UsageServices
from session_fixtures import make_services, make_stores
from usage_fixtures import NOW, add_telemetry

OCT = "2026-10-05T10:00:00Z"
SEP = "2026-09-20T10:00:00Z"
MONTHLY = {"id": "monthly", "scope": "month", "amount": 10}


def build(tmp_path, budgets=(), **settings):
    services = make_services(make_stores(tmp_path), usage_budgets=list(budgets), **settings)
    services.usage.clock = lambda: NOW
    return services


@pytest.fixture
def make(tmp_path):
    built = []

    def factory(budgets=(), **settings):
        services = build(tmp_path / f"world{len(built)}", budgets, **settings)
        built.append(services)
        return services

    yield factory
    for services in built:
        services.runs.close()


def spend(services, run_id, when, amount, *, currency="USD", store=None):
    """A priced call: USD sits in ``estimated_cost_usd`` too, any other currency only in the evidence."""
    basis = json.dumps({"model_id": "m", "currency": currency, "cost": amount})
    add_telemetry(store or services.system.store, run_id=run_id, when=when, model="m", tokens_in=10, tokens_out=10,
                  cost=(amount if currency == "USD" else None, basis))


def new_run(services, project):
    return services.system.store.create_run(RunRequest(workflow_id="w", project_name=project), 4, 300)["id"]


def row_of(result, budget_id, **where):
    found = [r for r in result["status"] if r["budget_id"] == budget_id and all(r[k] == v for k, v in where.items())]
    assert len(found) == 1, found
    return found[0]


def alert_events(services):
    return [e for e in services.tasks.store.events(limit=500) if e["type"] == BUDGET_EVENT]


# --- configuration ---------------------------------------------------------------------------------------------

def test_no_budget_is_configured_by_default():
    settings = Settings(_env_file=None)
    assert settings.usage_budgets == []
    assert settings.usage_budget_mode == "warn"
    assert settings.usage_budget_alerts == (80.0, 95.0)


def test_budgets_are_read_from_a_list_or_from_a_json_text_in_the_environment(monkeypatch):
    listed = Settings(_env_file=None, usage_budgets=[{"id": "a", "scope": "month", "amount": 5, "currency": "eur"}])
    assert listed.usage_budgets == [UsageBudget(id="a", scope="month", amount=5, currency="EUR")]
    monkeypatch.setenv("USAGE_BUDGETS", json.dumps([{"id": "p", "scope": "project", "project": "alpha", "amount": 2.5,
                                                    "mode": "cut", "alerts": "70;90"}]))
    monkeypatch.setenv("USAGE_BUDGET_MODE", "cut")
    monkeypatch.setenv("USAGE_BUDGET_ALERTS", "50, 75")
    settings = Settings(_env_file=None)
    assert settings.usage_budgets[0].project == "alpha" and settings.usage_budgets[0].mode == "cut"
    assert settings.usage_budgets[0].alerts == (70.0, 90.0) and settings.usage_budgets[0].currency == "USD"
    assert settings.usage_budget_mode == "cut" and settings.usage_budget_alerts == (50.0, 75.0)
    monkeypatch.setenv("USAGE_BUDGETS", "")
    assert Settings(_env_file=None).usage_budgets == []


def test_a_project_name_is_stripped_so_it_can_match(make):
    budget = UsageBudget(id="a", scope="project", project="  alpha ", amount=1)
    assert budget.project == "alpha"
    assert UsageBudget(id="a", scope="month", amount=1, project="  beta  ").project == "beta"
    services = make([{"id": "padded", "scope": "project", "project": " alpha ", "amount": 10}])
    spend(services, new_run(services, "alpha"), OCT, 4)
    assert row_of(services.usage.budgets(), "padded")["used"] == 4.0


def test_integer_and_float_amounts_are_accepted():
    assert UsageBudget(id="a", scope="month", amount=5).amount == 5.0
    assert UsageBudget(id="a", scope="month", amount=0.5).amount == 0.5


def test_alert_percents_are_sorted_without_repeats_and_may_be_empty():
    assert Settings(_env_file=None, usage_budget_alerts=[95, 80, 80, 100]).usage_budget_alerts == (80.0, 95.0, 100.0)
    assert Settings(_env_file=None, usage_budget_alerts="[10, 20]").usage_budget_alerts == (10.0, 20.0)
    assert Settings(_env_file=None, usage_budget_alerts=[]).usage_budget_alerts == ()


@pytest.mark.parametrize("budget", [
    {"id": "a", "scope": "week", "amount": 1},                                      # unknown scope
    {"id": "a", "scope": "project", "amount": 1},                                   # a project budget needs a project
    {"id": "a", "scope": "month", "amount": 0},
    {"id": "a", "scope": "month", "amount": -3},
    {"id": "a", "scope": "month", "amount": float("nan")},
    {"id": "a", "scope": "month", "amount": float("inf")},
    {"id": "a", "scope": "month", "amount": True},                                  # True would become 1.0
    {"id": "a", "scope": "month", "amount": "10"},                                  # a text is not a number
    {"id": "a", "scope": "month", "amount": None},
    {"id": "a b", "scope": "month", "amount": 1},                                   # id with a space
    {"id": "", "scope": "month", "amount": 1},
    {"id": "a", "scope": "month", "amount": 1, "currency": "$"},
    {"id": "a", "scope": "month", "amount": 1, "mode": "stop"},
    {"id": "a", "scope": "month", "amount": 1, "project": " "},
    {"id": "a", "scope": "month", "amount": 1, "alerts": [0]},
    {"id": "a", "scope": "month", "amount": 1, "alerts": [101]},
    {"id": "a", "scope": "month", "amount": 1, "alerts": [True]},
    {"id": "a", "scope": "month", "amount": 1, "alerts": ["eighty"]},
    {"id": "a", "scope": "month", "amount": 1, "unknown_key": 1},
    {"scope": "month", "amount": 1},
])
def test_a_bad_budget_is_rejected(budget):
    with pytest.raises(ValueError):
        Settings(_env_file=None, usage_budgets=[budget])


def test_budget_ids_must_be_unique_and_the_json_must_be_an_array(monkeypatch):
    twice = [{"id": "a", "scope": "month", "amount": 1}, {"id": "a", "scope": "run", "amount": 2}]
    with pytest.raises(ValueError, match="unique"):
        Settings(_env_file=None, usage_budgets=twice)
    monkeypatch.setenv("USAGE_BUDGETS", "{not json")
    with pytest.raises(ValueError):
        Settings(_env_file=None)
    monkeypatch.setenv("USAGE_BUDGETS", json.dumps({"id": "a"}))
    with pytest.raises(ValueError):
        Settings(_env_file=None)


@pytest.mark.parametrize("name,value", [("usage_budget_mode", "stop"), ("usage_budget_alerts", [0]),
                                        ("usage_budget_alerts", [150]), ("usage_budget_alerts", "x")])
def test_bad_budget_defaults_are_rejected(name, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{name: value})


# --- monthly budgets: percent, remaining, state, alerts ------------------------------------------------------------------

def test_without_budgets_the_answer_is_empty_and_nothing_is_announced(make):
    services = make()
    spend(services, "run-1", OCT, 50)
    result = services.usage.budgets()
    assert result["budgets"] == [] and result["status"] == [] and result["worst_percent"] is None
    assert result["counts"] == {"ok": 0, "alert": 0, "limit_reached": 0} and result["recent_alerts"] == []
    assert services.usage.notify_budgets()["emitted"] == [] and alert_events(services) == []


def test_a_monthly_budget_reports_used_remaining_percent_and_state(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 2.5)
    spend(services, "run-1", "2026-10-11T09:00:00Z", 3.0)
    result = services.usage.budgets()
    row = row_of(result, "monthly")
    assert (row["used"], row["remaining"], row["percent"], row["state"]) == (5.5, 4.5, 55.0, "ok")
    assert (row["amount"], row["currency"], row["scope"], row["project"]) == (10.0, "USD", "month", None)
    assert (row["period"], row["since"], row["until"]) == ("2026-10", "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")
    assert row["crossed"] == [] and row["next_alert"] == 80.0 and row["alerts"] == [80.0, 95.0]
    assert row["records"] == 2 and row["cost_known_records"] == 2 and row["complete"] is True
    assert result["month"] == {"key": "2026-10", "since": "2026-10-01T00:00:00Z", "until": "2026-11-01T00:00:00Z"}
    assert result["counts"] == {"ok": 1, "alert": 0, "limit_reached": 0} and result["worst_percent"] == 55.0
    assert result["budgets"] == [{"id": "monthly", "scope": "month", "project": None, "currency": "USD", "amount": 10.0,
                                  "mode": "warn", "alerts": [80.0, 95.0]}]
    json.dumps(result)   # JSON-ready for the GUI


@pytest.mark.parametrize("used,percent,state,crossed,next_alert,remaining", [
    (0, 0.0, "ok", [], 80.0, 10.0),
    (7.9, 79.0, "ok", [], 80.0, 2.1),
    (8, 80.0, "alert", [80.0], 95.0, 2.0),
    (9.4, 94.0, "alert", [80.0], 95.0, 0.6),
    (9.5, 95.0, "alert", [80.0, 95.0], None, 0.5),
    (10, 100.0, "limit_reached", [80.0, 95.0], None, 0.0),
    (12, 120.0, "limit_reached", [80.0, 95.0], None, 0.0),
])
def test_percent_state_and_alerts_follow_the_thresholds(make, used, percent, state, crossed, next_alert, remaining):
    services = make([MONTHLY])
    if used:
        spend(services, "run-1", OCT, used)
    row = row_of(services.usage.budgets(), "monthly")
    assert (row["percent"], row["state"], row["crossed"], row["next_alert"], row["remaining"]) == (
        percent, state, crossed, next_alert, remaining)


def test_a_percent_is_never_shown_above_what_decided_the_state(make):
    services = make([{"id": "big", "scope": "month", "amount": 100000, "alerts": [80]}])
    spend(services, "run-1", OCT, 79999.6)
    row = row_of(services.usage.budgets(), "big")
    assert row["percent"] == 79.99 and row["state"] == "ok" and row["crossed"] == []


@pytest.mark.parametrize("amount,alert,used", [(1.1, 90, 0.99), (1.1, 95, 1.045), (9, 90, 8.1)])
def test_float_noise_does_not_hide_an_alert(make, amount, alert, used):
    assert used / amount * 100 < alert    # in plain floating point the exact percent falls just short
    services = make([{"id": "tiny", "scope": "month", "amount": amount, "alerts": [alert]}])
    spend(services, "run-1", OCT, used / 3)
    spend(services, "run-1", OCT, used / 3)
    spend(services, "run-1", OCT, used / 3)
    row = row_of(services.usage.budgets(), "tiny")
    assert row["percent"] == float(alert) and row["crossed"] == [float(alert)] and row["state"] == "alert"


def test_alerts_are_configurable_per_budget_and_for_every_budget(make):
    services = make([{**MONTHLY, "alerts": [50]}, {"id": "other", "scope": "month", "amount": 10, "alerts": []},
                     {"id": "default", "scope": "month", "amount": 10}], usage_budget_alerts=[60, 90])
    spend(services, "run-1", OCT, 6)
    result = services.usage.budgets()
    assert row_of(result, "monthly")["crossed"] == [50.0] and row_of(result, "monthly")["next_alert"] is None
    assert row_of(result, "other")["crossed"] == [] and row_of(result, "other")["state"] == "ok"
    assert row_of(result, "default")["alerts"] == [60.0, 90.0] and row_of(result, "default")["crossed"] == [60.0]
    assert result["defaults"] == {"mode": "warn", "alerts": [60.0, 90.0]}


def test_the_mode_is_warn_unless_a_budget_or_the_settings_say_cut(make):
    services = make([MONTHLY, {"id": "hard", "scope": "month", "amount": 10, "mode": "cut"}])
    spend(services, "run-1", OCT, 11)
    result = services.usage.budgets()
    assert row_of(result, "monthly")["mode"] == "warn" and row_of(result, "hard")["mode"] == "cut"
    # Reported, never applied here: the answer comes back and nothing is stopped or raised.
    assert row_of(result, "hard")["state"] == "limit_reached"
    assert {b["id"]: b["mode"] for b in result["budgets"]} == {"monthly": "warn", "hard": "cut"}
    cut_by_default = make([MONTHLY], usage_budget_mode="cut")
    assert row_of(cut_by_default.usage.budgets(), "monthly")["mode"] == "cut"


def test_the_month_is_the_utc_calendar_month_and_resets_with_it(make):
    services = make([MONTHLY])
    spend(services, "run-sep", "2026-09-30T23:59:59Z", 4)
    spend(services, "run-oct", "2026-10-01T00:00:00Z", 1)
    spend(services, "run-oct", "2026-10-31T23:59:59Z", 2)
    spend(services, "run-nov", "2026-11-01T00:00:00Z", 8)
    assert row_of(services.usage.budgets(), "monthly")["used"] == 3.0
    services.usage.clock = lambda: datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
    november = row_of(services.usage.budgets(), "monthly")
    assert (november["period"], november["used"], november["percent"]) == ("2026-11", 8.0, 80.0)
    services.usage.clock = lambda: datetime(2026, 12, 2, tzinfo=timezone.utc)   # a month with no spend
    december = row_of(services.usage.budgets(), "monthly")
    assert (december["period"], december["used"]) == ("2026-12", 0.0)


# --- currency and unknown costs -------------------------------------------------------------------------------------

def test_a_budget_counts_only_costs_priced_in_its_own_currency(make):
    services = make([{"id": "usd", "scope": "month", "amount": 10},
                     {"id": "eur", "scope": "month", "amount": 10, "currency": "EUR"},
                     {"id": "gbp", "scope": "month", "amount": 10, "currency": "gbp"}])
    spend(services, "run-1", OCT, 2)
    spend(services, "run-1", OCT, 5, currency="EUR")
    spend(services, "run-1", OCT, 1.5, currency="eur")
    result = services.usage.budgets()
    usd, eur, gbp = row_of(result, "usd"), row_of(result, "eur"), row_of(result, "gbp")
    assert (usd["used"], usd["cost_other_currencies"], usd["complete"]) == (2.0, {"EUR": 6.5}, False)
    assert (eur["used"], eur["cost_other_currencies"], eur["cost_known_records"]) == (6.5, {"USD": 2.0}, 2)
    assert (gbp["used"], gbp["percent"], gbp["cost_other_currencies"]) == (0.0, 0.0, {"EUR": 6.5, "USD": 2.0})
    assert gbp["currency"] == "GBP"


def test_calls_with_an_unknown_cost_are_counted_apart_never_as_zero(make):
    services = make([MONTHLY])
    store = services.system.store
    spend(services, "run-1", OCT, 4)
    add_telemetry(store, run_id="run-1", when=OCT, model="local", tokens_in=10, tokens_out=10)                  # no price
    add_telemetry(store, run_id="run-1", when=OCT, model="m", tokens_in=None, tokens_out=None)                 # no tokens
    add_telemetry(store, run_id="run-1", when=OCT, model="", tokens_in=None, tokens_out=None, provider="human")
    row = row_of(services.usage.budgets(), "monthly")
    assert (row["used"], row["records"], row["cost_known_records"], row["cost_unknown_records"]) == (4.0, 4, 1, 3)
    assert row["cost_unknown_reasons"] == {"human_step": 1, "no_price_evidence": 1, "tokens_not_reported": 1}
    assert row["complete"] is False and row["percent"] == 40.0


def test_a_budget_with_no_calls_has_nothing_missing(make):
    services = make([MONTHLY])
    row = row_of(services.usage.budgets(), "monthly")
    assert (row["used"], row["records"], row["percent"], row["complete"]) == (0.0, 0, 0.0, True)


def test_a_cost_saved_before_the_evidence_column_existed_still_counts_as_usd(make):
    services = make([MONTHLY])
    add_telemetry(services.system.store, run_id="run-old", when=OCT, model="m", tokens_in=10, tokens_out=10,
                  cost=(3.0, None))
    row = row_of(services.usage.budgets(), "monthly")
    assert (row["used"], row["cost_known_records"], row["complete"]) == (3.0, 1, True)


def test_every_store_counts_including_bundle_stores(make, tmp_path):
    from multiagent.db import Database
    from multiagent.db.store import Store

    services = make([MONTHLY])
    settings = Settings(_env_file=None, local_dir=tmp_path / "bundle-local")
    settings.ensure_directories()
    database = Database(settings)
    database.migrate()
    bundle_store = Store(database)
    spend(services, "run-1", OCT, 1)
    spend(services, "run-bundle", OCT, 2, store=bundle_store)
    original = services.usage._stores
    services.usage._stores = lambda: [*original(), ("my_bundle", bundle_store)]
    assert row_of(services.usage.budgets(), "monthly")["used"] == 3.0


# --- project budgets ------------------------------------------------------------------------------------------------

def test_a_project_budget_counts_everything_the_project_ever_spent_by_name_or_by_id(make):
    services = make([{"id": "alpha-all", "scope": "project", "project": "alpha", "amount": 10},
                     {"id": "alpha-month", "scope": "month", "project": "alpha", "amount": 10},
                     {"id": "all-month", "scope": "month", "amount": 100}])
    alpha, beta = new_run(services, "alpha"), new_run(services, "beta")
    spend(services, alpha, SEP, 3)
    spend(services, alpha, OCT, 2)
    spend(services, beta, OCT, 7)
    spend(services, "run-without-a-project", OCT, 9)
    result = services.usage.budgets()
    total, month, everything = (row_of(result, name) for name in ("alpha-all", "alpha-month", "all-month"))
    assert (total["used"], total["period"], total["since"], total["project"]) == (5.0, "all", None, "alpha")
    assert (month["used"], month["period"]) == (2.0, "2026-10")
    assert everything["used"] == 18.0
    alpha_id = services.system.store.get_run(alpha)["project_id"]
    assert alpha_id != "alpha"
    services.settings.usage_budgets = [UsageBudget(id="by-id", scope="project", project=alpha_id, amount=10)]
    assert row_of(services.usage.budgets(), "by-id")["used"] == 5.0


def test_a_project_budget_for_an_unknown_project_is_zero_not_an_error(make):
    services = make([{"id": "ghost", "scope": "project", "project": "nobody", "amount": 10}])
    spend(services, new_run(services, "alpha"), OCT, 4)
    row = row_of(services.usage.budgets(), "ghost")
    assert (row["used"], row["records"], row["state"]) == (0.0, 0, "ok")


# --- run budgets ----------------------------------------------------------------------------------------------------

def test_a_run_budget_has_a_row_for_each_run_that_spent_this_month_most_consumed_first(make):
    services = make([{"id": "per-run", "scope": "run", "amount": 10}])
    cheap, dear, old = new_run(services, "alpha"), new_run(services, "alpha"), new_run(services, "beta")
    spend(services, cheap, OCT, 1)
    spend(services, dear, OCT, 8.5)
    spend(services, old, SEP, 9)     # no spend this month: no row
    result = services.usage.budgets()
    rows = result["status"]
    assert [r["run_id"] for r in rows] == [dear, cheap]
    assert (rows[0]["used"], rows[0]["percent"], rows[0]["state"], rows[0]["crossed"]) == (8.5, 85.0, "alert", [80.0])
    assert rows[0]["scope"] == "run" and rows[0]["period"] is None and rows[0]["bundle"] is None
    assert result["runs_truncated"] is False and result["worst_percent"] == 85.0


def test_a_run_budget_counts_all_of_a_runs_spend_not_only_this_months(make):
    services = make([{"id": "per-run", "scope": "run", "amount": 10}])
    run = new_run(services, "alpha")
    spend(services, run, SEP, 6)
    spend(services, run, OCT, 3)
    assert row_of(services.usage.budgets(), "per-run")["used"] == 9.0


def test_a_run_budget_for_one_run_and_the_errors_of_a_bad_run(make):
    services = make([{"id": "per-run", "scope": "run", "amount": 10}, MONTHLY])
    mine, other = new_run(services, "alpha"), new_run(services, "alpha")
    spend(services, mine, OCT, 2)
    spend(services, other, OCT, 3)
    result = services.usage.budgets(run_id=mine)
    assert [(r["budget_id"], r["run_id"], r["used"]) for r in result["status"]] == [
        ("per-run", mine, 2.0), ("monthly", None, 5.0)]
    fresh = new_run(services, "alpha")    # a run that has not spent anything yet still has its row
    assert row_of(services.usage.budgets(run_id=fresh), "per-run")["used"] == 0.0
    with pytest.raises(ServiceError) as missing:
        services.usage.budgets(run_id="no-such-run")
    assert missing.value.code == "not_found" and missing.value.status == 404
    for bad in (7, "x" * 129, ["a"]):
        with pytest.raises(ServiceError) as invalid:
            services.usage.budgets(run_id=bad)
        assert invalid.value.code == "invalid_request" and invalid.value.status == 422


def test_a_run_budget_limited_to_a_project_skips_the_runs_of_other_projects(make):
    services = make([{"id": "alpha-runs", "scope": "run", "project": "alpha", "amount": 10}])
    mine, other = new_run(services, "alpha"), new_run(services, "beta")
    spend(services, mine, OCT, 2)
    spend(services, other, OCT, 5)
    assert [r["run_id"] for r in services.usage.budgets()["status"]] == [mine]
    assert [r["run_id"] for r in services.usage.budgets(run_id=mine)["status"]] == [mine]
    assert services.usage.budgets(run_id=other)["status"] == []


def test_a_run_budget_shows_only_the_most_consumed_runs_and_says_so(make, monkeypatch):
    services = make([{"id": "per-run", "scope": "run", "amount": 10}])
    monkeypatch.setattr(usage_module, "MAX_BUDGET_RUNS", 2)
    runs = [new_run(services, "alpha") for _ in range(3)]
    for index, (run, amount) in enumerate(zip(runs, (1, 3, 2))):
        spend(services, run, f"2026-10-05T10:0{index}:00Z", amount)    # the last run is the most recently active
    result = services.usage.budgets()
    assert [r["run_id"] for r in result["status"]] == [runs[1], runs[2]] and result["runs_truncated"] is True
    monkeypatch.setattr(usage_module, "MAX_RUN_LIMIT", 1)     # only the most recently active run is even read
    result = services.usage.budgets()
    assert [r["run_id"] for r in result["status"]] == [runs[2]] and result["runs_truncated"] is True


# --- alerts as team events ------------------------------------------------------------------------------------------

def test_notify_announces_each_alert_reached_once_as_a_team_event(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 8.2)
    first = services.usage.notify_budgets()
    assert [(e["budget_id"], e["threshold"], e["period"]) for e in first["emitted"]] == [("monthly", 80.0, "2026-10")]
    events = alert_events(services)
    assert len(events) == 1 and events[0]["seq"] == first["emitted"][0]["seq"]
    event = events[0]
    assert (event["actor"], event["ref"], event["group_id"], event["task_id"]) == ("system:budgets", "budget:monthly", "", "")
    assert event["cause"] == "budget:monthly:80:2026-10:10USD:*"
    assert event["payload"] == {"budget": "monthly", "scope": "month", "threshold": 80.0, "period": "2026-10",
                                "run_id": None, "project": None, "project_id": None, "currency": "USD",
                                "amount": 10.0, "used": 8.2, "percent": 82.0, "state": "alert", "mode": "warn",
                                "complete": True}
    assert first["recent_alerts"][0]["threshold"] == 80.0 and first["recent_alerts"][0]["seq"] == event["seq"]
    assert first["recent_alerts"][0]["at"] is not None and first["status"][0]["state"] == "alert"

    again = services.usage.notify_budgets()
    assert again["emitted"] == [] and len(alert_events(services)) == 1

    spend(services, "run-1", OCT, 1.5)    # 97 %: only the 95 % alert is new
    second = services.usage.notify_budgets()
    assert [e["threshold"] for e in second["emitted"]] == [95.0]
    assert [e["payload"]["threshold"] for e in alert_events(services)] == [80.0, 95.0]
    assert [a["threshold"] for a in second["recent_alerts"]] == [95.0, 80.0]    # newest first


def test_a_jump_past_several_alerts_announces_all_of_them_in_order(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 5)
    assert services.usage.notify_budgets()["emitted"] == []
    spend(services, "run-1", OCT, 4.7)
    assert [e["threshold"] for e in services.usage.notify_budgets()["emitted"]] == [80.0, 95.0]


def test_reading_the_budgets_never_writes_and_a_budget_below_its_alerts_announces_nothing(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 9)
    for _ in range(3):
        services.usage.budgets()
    assert alert_events(services) == []
    quiet = make([MONTHLY])
    spend(quiet, "run-1", OCT, 1)
    assert quiet.usage.notify_budgets()["emitted"] == [] and alert_events(quiet) == []


def test_an_alert_is_announced_once_even_from_another_service_instance(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 9.6)
    assert [e["threshold"] for e in services.usage.notify_budgets()["emitted"]] == [80.0, 95.0]
    other = UsageServices(services, clock=lambda: NOW)      # no memory of what was announced: the events decide
    assert other.notify_budgets()["emitted"] == [] and len(alert_events(services)) == 2


def test_the_same_alert_is_announced_again_in_the_next_month(make):
    services = make([MONTHLY])
    spend(services, "run-oct", OCT, 8.5)
    spend(services, "run-nov", "2026-11-03T09:00:00Z", 8.5)
    assert len(services.usage.notify_budgets()["emitted"]) == 1
    services.usage.clock = lambda: datetime(2026, 11, 15, tzinfo=timezone.utc)
    assert [e["period"] for e in services.usage.notify_budgets()["emitted"]] == ["2026-11"]
    assert [e["cause"] for e in alert_events(services)] == ["budget:monthly:80:2026-10:10USD:*",
                                                            "budget:monthly:80:2026-11:10USD:*"]
    assert services.usage.notify_budgets()["emitted"] == []


def test_raising_the_amount_announces_its_own_alerts_again(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 8.5)
    assert len(services.usage.notify_budgets()["emitted"]) == 1
    services.settings.usage_budgets = [UsageBudget(id="monthly", scope="month", amount=10.4)]   # still 81 %
    assert services.usage.notify_budgets()["emitted"] and len(alert_events(services)) == 2
    services.settings.usage_budgets = [UsageBudget(id="monthly", scope="month", amount=20)]     # 42 %: nothing yet
    assert services.usage.notify_budgets()["emitted"] == []
    spend(services, "run-1", OCT, 8)
    assert [e["threshold"] for e in services.usage.notify_budgets()["emitted"]] == [80.0]
    assert alert_events(services)[-1]["payload"]["amount"] == 20.0


def test_a_run_budget_announces_per_run_and_a_project_budget_once_tied_to_its_project(make):
    services = make([{"id": "per-run", "scope": "run", "amount": 10},
                     {"id": "alpha-all", "scope": "project", "project": "alpha", "amount": 10, "alerts": [50]}])
    one, two = new_run(services, "alpha"), new_run(services, "alpha")
    spend(services, one, OCT, 8.5)
    spend(services, two, OCT, 8.1)
    emitted = services.usage.notify_budgets()["emitted"]
    assert sorted((e["budget_id"], e["run_id"]) for e in emitted) == sorted(
        [("per-run", one), ("per-run", two), ("alpha-all", None)])
    by_budget = {(e["payload"]["budget"], e["payload"]["run_id"]): e for e in alert_events(services)}
    project_event = by_budget[("alpha-all", None)]
    alpha_id = services.system.store.get_run(one)["project_id"]
    assert alpha_id != "alpha"      # a project made by a run has a generated id, not its name
    assert project_event["ref"] == f"project:{alpha_id}" and project_event["payload"]["project_id"] == alpha_id
    assert project_event["payload"]["period"] == "all" and project_event["cause"] == "budget:alpha-all:50:all:10USD:alpha"
    assert project_event["payload"]["used"] == 16.6 and project_event["payload"]["threshold"] == 50.0
    assert by_budget[("per-run", one)]["cause"] == f"budget:per-run:80:run={one}:10USD:*"
    assert by_budget[("per-run", one)]["ref"] == "budget:per-run" and by_budget[("per-run", one)]["payload"]["period"] is None
    assert services.usage.notify_budgets()["emitted"] == []
    spend(services, one, OCT, 1.2)    # the first run now passes 95 %: only that run announces it
    assert [(e["budget_id"], e["run_id"], e["threshold"]) for e in services.usage.notify_budgets()["emitted"]] == [
        ("per-run", one, 95.0)]
    # Asking about one run announces only what that run (and the budgets that do not depend on a run) reached.
    spend(services, two, OCT, 1.5)    # 96 % for the second run
    assert services.usage.notify_budgets(run_id=one)["emitted"] == []
    assert [(e["run_id"], e["threshold"]) for e in services.usage.notify_budgets(run_id=two)["emitted"]] == [(two, 95.0)]


def test_the_events_of_a_project_budget_show_in_that_projects_event_feed(make):
    services = make([{"id": "alpha-all", "scope": "project", "project": "alpha", "amount": 10}])
    run = new_run(services, "alpha")
    spend(services, run, OCT, 8.1)
    services.usage.notify_budgets()
    project_id = services.system.store.get_run(run)["project_id"]
    assert project_id != "alpha"       # the feed is keyed by the project's id, which a run's project does not share with its name
    assert [e["type"] for e in services.tasks.store.events(project_id=project_id)] == [BUDGET_EVENT]
    assert services.tasks.store.events(project_id="alpha") == []
    assert row_of(services.usage.budgets(), "alpha-all")["project_id"] == project_id


def test_month_and_run_budgets_tied_to_a_project_link_their_events_to_its_id(make):
    services = make([{"id": "alpha-month", "scope": "month", "project": "alpha", "amount": 10},
                     {"id": "alpha-runs", "scope": "run", "project": "alpha", "amount": 10},
                     {"id": "any-runs", "scope": "run", "amount": 10}])
    run = new_run(services, "alpha")
    spend(services, run, OCT, 9)
    spend(services, new_run(services, "beta"), OCT, 9)
    services.usage.notify_budgets()
    project_id = services.system.store.get_run(run)["project_id"]
    refs = sorted((e["payload"]["budget"], e["payload"]["run_id"] == run, e["ref"]) for e in alert_events(services))
    assert refs == sorted([("alpha-month", False, f"project:{project_id}"), ("alpha-runs", True, f"project:{project_id}"),
                           ("any-runs", True, "budget:any-runs"), ("any-runs", False, "budget:any-runs")])
    assert len(services.tasks.store.events(project_id=project_id)) == 2


def test_amounts_that_differ_only_after_six_digits_are_different_alerts(make):
    services = make([{"id": "big", "scope": "month", "amount": 1000000}])
    spend(services, "run-1", OCT, 900000)
    assert [e["threshold"] for e in services.usage.notify_budgets()["emitted"]] == [80.0]
    services.settings.usage_budgets = [UsageBudget(id="big", scope="month", amount=1000004)]    # 89.9996 %, still past 80
    assert [e["threshold"] for e in services.usage.notify_budgets()["emitted"]] == [80.0]
    assert [e["cause"] for e in alert_events(services)] == ["budget:big:80:2026-10:1000000USD:*",
                                                            "budget:big:80:2026-10:1000004USD:*"]
    assert services.usage.notify_budgets()["emitted"] == []


def test_the_same_budget_id_tied_to_another_project_is_another_alert(make):
    services = make([{"id": "cap", "scope": "month", "project": "alpha", "amount": 10}])
    spend(services, new_run(services, "alpha"), OCT, 9)
    spend(services, new_run(services, "beta"), OCT, 9)
    assert len(services.usage.notify_budgets()["emitted"]) == 1
    services.settings.usage_budgets = [UsageBudget(id="cap", scope="month", project="beta", amount=10)]
    assert len(services.usage.notify_budgets()["emitted"]) == 1
    assert [e["cause"] for e in alert_events(services)] == ["budget:cap:80:2026-10:10USD:alpha",
                                                            "budget:cap:80:2026-10:10USD:beta"]


def test_an_event_payload_with_the_longest_names_still_fits(make):
    long_project = "p" * 120
    services = make([{"id": "i" * 64, "scope": "project", "project": long_project, "amount": 10, "currency": "C" * 16}])
    add_telemetry(services.system.store, run_id=new_run(services, long_project), when=OCT, model="m", tokens_in=1,
                  tokens_out=1, cost=(None, json.dumps({"currency": "C" * 16, "cost": 9.9})))
    assert len(services.usage.notify_budgets()["emitted"]) == 2    # 80 % and 95 %


def test_alert_percents_with_decimals_make_a_stable_key(make):
    services = make([{"id": "fine", "scope": "month", "amount": 8, "alerts": [87.5]}])
    spend(services, "run-1", OCT, 7.5)
    services.usage.notify_budgets()
    assert alert_events(services)[0]["cause"] == "budget:fine:87.5:2026-10:8USD:*"
    assert services.usage.notify_budgets()["emitted"] == []


def test_the_service_is_listed_on_the_facade_and_budgets_leave_telemetry_untouched(make):
    services = make([MONTHLY])
    spend(services, "run-1", OCT, 9.6)
    store = services.system.store
    with store.db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS n, SUM(COALESCE(estimated_cost_usd,0)) AS s FROM telemetry").fetchone()
    services.usage.notify_budgets()
    with store.db.connect() as conn:
        after = conn.execute("SELECT COUNT(*) AS n, SUM(COALESCE(estimated_cost_usd,0)) AS s FROM telemetry").fetchone()
    assert tuple(before) == tuple(after)
    assert isinstance(services.usage, UsageServices)
