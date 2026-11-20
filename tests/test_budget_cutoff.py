# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""USO-T04: the budget cut guard of the call chain (order 300) and the wiring of ``notify_budgets``.

Synthetic and offline: scripted providers, invented prices (a rate card that exists only in these tests) and invented
budgets. Three layers:

* the guard on its own, with a fake budget reader (``notify_budgets`` answering a state written by the test, and a
  ``reserving`` section that keeps the reservations in memory), so the arithmetic, the unknown-price rule, the modes, the
  approvals and the reservations are exercised without a database;
* the real services: the reservation as a write transaction (two calls against one budget, a failed call, a crashed one),
  the route that approves a spend (the unauthenticated ``/api/v1`` never does), and the exact figures of ``budgets()``;
* the real engine: a paid call that would pass a budget in cut mode waits as ``WAITING_HUMAN`` until a person approves it
  (one approval, one call) or rejects it (the run fails); a run with no budget is what it always was; and
  ``notify_budgets`` is announced by the guard and after every recorded call.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import threading
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cmp_harness import Provider, events, get, names, start
from multiagent import cli
from multiagent.api import create_app
from multiagent.budget_cut import (
    CAUSE_OUTPUT_UNBOUNDED, EVENT_APPROVED, EVENT_REJECTED, EVENT_RELEASED, GUARD_NAME, REASON_CURRENCY_NOT_PRICED,
    REASON_OVER_BUDGET, REASON_PRICE_UNKNOWN, BudgetGuard, BudgetHold, BudgetPolicy, BudgetUnavailable, granted_approvals,
    policy_from_settings,
)
from multiagent.call_guards import (
    ORDER_BUDGET, PROCEED, CallContext, CallGuardChain, GuardEnvironment, Pause, build_call_guards,
)
from multiagent.catalog import CatalogError, ModelSpec
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest, WaitingReason
from multiagent.errors import InvalidStateTransition, ProviderUnavailable
from multiagent.gui.token import ensure_token
from multiagent.observability import TokenUsageRecord
from multiagent.services import ApplicationServices
from multiagent.services import usage as usage_module
from multiagent.services.usage import BUDGET_EVENT, RESERVED_EVENT, SETTLED_EVENT

# --- synthetic prices, calls and a fake budget reader --------------------------------------------------------------


def rate(amount: float, currency: str = "USD", **overrides: Any) -> dict[str, Any]:
    side = {"amount": amount, "denominator": 1, "unit": "token", "currency": currency,
            "source": "synthetic rate card of the tests", "provenance": "catalog_declared",
            "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2036-01-01T00:00:00Z"}
    side.update(overrides)
    return side


def pricing(input_rate: float, output_rate: float, **overrides: Any) -> dict[str, Any]:
    return {"input": rate(input_rate, **overrides), "output": rate(output_rate, **overrides)}


def spec(model_id: str = "paid", block: dict[str, Any] | None = None, **fields: Any) -> ModelSpec:
    return ModelSpec(id=model_id, provider="lab", model=model_id, tier="cloud", structured_output=True, web=False,
                     pricing=block, **fields)


# 1000 input tokens at 0.001 and the 4096 output tokens the model declares at 0.001 are 5.096 in the currency of the card.
PAID = spec("paid", pricing(0.001, 0.001), max_output_tokens=4096)


def call(model: ModelSpec = PAID, **fields: Any) -> CallContext:
    values: dict[str, Any] = {"model_id": model.id, "spec": model, "run_id": "run-1", "step_id": "create", "attempt": 1,
                              "ordinal": 1, "estimated_input_tokens": 1000}
    values.update(fields)
    return CallContext(**values)


def row(*, budget_id: str = "cap", scope: str = "run", amount: float = 10.0, used: float = 0.0, mode: str = "cut",
        currency: str = "USD", run_id: str | None = "run-1", project: str | None = None, complete: bool = True,
        period: str | None = None, used_exact: float | None = None) -> dict[str, Any]:
    found = {"budget_id": budget_id, "scope": scope, "project": project, "run_id": run_id if scope == "run" else None,
             "period": period or ("all" if scope == "project" else "2026-10" if scope == "month" else None),
             "currency": currency, "amount": amount, "used": used, "remaining": max(0.0, round(amount - used, 6)),
             "percent": round(used / amount * 100, 2), "state": "ok", "mode": mode, "complete": complete}
    if used_exact is not None:
        found["used_exact"] = used_exact
    return found


class Reader:
    """The usage service as the guard sees it: ``notify_budgets`` answers the state written by the test and ``reserving``
    is the exclusive section, with the reservations kept in memory (a booking counts for the next section at once)."""

    def __init__(self, *rows: dict[str, Any], raises: Exception | None = None, reserved: dict[str, Any] | None = None):
        self.rows, self.raises, self.asked = list(rows), raises, []
        self.reserved = {key: Decimal(str(value)) for key, value in (reserved or {}).items()}
        self.booked: list[dict[str, Any]] = []
        self.settled: list[str] = []
        self.sections = 0

    def notify_budgets(self, *, run_id=None):
        self.asked.append(run_id)
        if self.raises is not None:
            raise self.raises
        return {"status": self.rows, "emitted": []}

    @contextmanager
    def reserving(self, *, run_id=None):
        self.sections += 1
        yield BudgetHold(status={"status": self.rows}, reserved=dict(self.reserved), reserve=self._book)

    def _book(self, payload):
        self.booked.append(dict(payload))
        for key in payload["budgets"]:
            self.reserved[key] = self.reserved.get(key, Decimal(0)) + Decimal(payload["amount"])
        return f"r{len(self.booked)}"

    def settle(self, reservation_id):
        self.settled.append(reservation_id)
        return True


class PlainReader:
    """A reader that cannot reserve (only ``notify_budgets``): the guard compares without reservations."""

    def __init__(self, *rows: dict[str, Any]):
        self.rows = list(rows)

    def notify_budgets(self, *, run_id=None):
        return {"status": self.rows, "emitted": []}


def guard(*rows: dict[str, Any], cut: bool = True, emitted: list | None = None, **reader):
    sink = emitted if emitted is not None else []
    made = BudgetGuard(BudgetPolicy(reader=Reader(*rows, **reader), cut=cut),
                       emit=lambda event_type, payload: sink.append((event_type, payload)))
    made.sink, made.reader = sink, made.policy.reader
    return made


STATE = {"id": "run-1", "events": [], "llm_calls": 0, "max_llm_calls": 10}
# The fingerprint of a breach: budget, run, period, currency, amount (as written) and reason.
KEY = "cap|run-1|None|USD|10|over_budget"
MONTH_KEY = "month||2026-10|USD|1|over_budget"


def approved(*, approval_id: str = "a1", breaches=(KEY,), step_id="create", attempt=1, model_id="paid", ordinal=1, retry=0,
             estimate_amount: str | None = "5.096"):
    return {"event_type": EVENT_APPROVED, "payload": {"approval_id": approval_id, "step_id": step_id, "attempt": attempt,
                                                      "model_id": model_id, "ordinal": ordinal, "retry": retry,
                                                      "breaches": list(breaches), "estimate_amount": estimate_amount}}


def released(approval_id: str = "a1"):
    return {"event_type": EVENT_RELEASED, "payload": {"approval_id": approval_id}}


# --- below, at and above the budget --------------------------------------------------------------------------------


def test_a_call_that_stays_below_the_budget_goes_ahead_and_books_its_estimate():
    made = guard(row(amount=100, used=10))
    assert made.check(call(), STATE) is PROCEED
    assert made.sink == []                                           # nothing held, nothing released
    (booking,) = made.reader.booked
    assert (booking["run_id"], booking["step_id"], booking["attempt"], booking["model_id"]) == ("run-1", "create", 1, "paid")
    assert (booking["currency"], booking["amount"], booking["budgets"]) == ("USD", "5.096", ["cap|run-1|USD"])


def test_a_call_that_would_pass_the_budget_pauses_for_a_person_and_books_nothing():
    made = guard(row(amount=10, used=5.0))                           # 5.0 used + about 5.096 for this call
    verdict = made.check(call(), STATE)
    assert isinstance(verdict, Pause) and verdict.waiting_reason == WaitingReason.BUDGET_CUT.value
    request = verdict.request
    assert (request["step_id"], request["attempt"], request["model_id"], request["reasons"]) == (
        "create", 1, "paid", [REASON_OVER_BUDGET])
    (breach,) = request["breaches"]
    assert breach["key"] == KEY and breach["projected"] == pytest.approx(10.096)
    assert (breach["amount"], breach["used"], breach["remaining"], breach["currency"]) == (10.0, 5.0, 5.0, "USD")
    estimate = request["estimate"]
    assert estimate["amount"] == pytest.approx(5.096) and estimate["exact"] == "5.096" and estimate["currency"] == "USD"
    assert (estimate["input_tokens"], estimate["output_tokens"], estimate["output_tokens_source"]) == (
        1000, 4096, "catalog_max_output_tokens")
    assert estimate["price_evidence"]["input"]["source"] == "synthetic rate card of the tests"   # the evidence is shown
    assert "approve" in verdict.message and "'cap'" in verdict.message and "exactly this one call" in verdict.message
    json.dumps(request)                                              # the request travels in an event: it must serialize
    assert made.sink == [] and made.reader.booked == []


def test_spending_exactly_the_amount_is_not_passing_it():
    made = guard(row(amount=10.096, used=5.0))                       # 5.0 + 5.096 == 10.096
    assert made.check(call(), STATE) is PROCEED


def test_a_budget_already_spent_holds_any_priced_call():
    verdict = guard(row(amount=10, used=10)).check(call(), STATE)
    assert isinstance(verdict, Pause) and verdict.request["breaches"][0]["reason"] == REASON_OVER_BUDGET


def test_every_budget_in_breach_is_listed_and_a_budget_below_is_not():
    made = guard(row(budget_id="run-cap", amount=10, used=6), row(budget_id="month-cap", scope="month", amount=500, used=1),
                 row(budget_id="project-cap", scope="project", project="alpha", amount=7, used=3))
    verdict = made.check(call(), {**STATE, "project_id": "p-1", "request": {"project_name": "alpha"}})
    assert [b["budget_id"] for b in verdict.request["breaches"]] == ["run-cap", "project-cap"]


# --- the figures are compared exactly, rounding is for display -----------------------------------------------------

TINY = spec("tiny", pricing(1e-12, 1e-12), max_output_tokens=1)      # 1000 + 1 tokens: 1.001e-09


def test_the_decision_uses_the_unrounded_consumption_not_the_one_cut_for_display():
    # used is shown as 1.0 (six decimals) but is 1.0000004 and the amount is 1.0000001: the budget is already over, and
    # the call, however small, must be held. Compared on the displayed 1.0 it would pass.
    verdict = guard(row(amount=1.0000001, used=1.0, used_exact=1.0000004)).check(call(TINY), STATE)
    assert isinstance(verdict, Pause) and verdict.request["reasons"] == [REASON_OVER_BUDGET]
    assert guard(row(amount=1.0000001, used=1.0, used_exact=1.0)).check(call(TINY), STATE) is PROCEED


def test_no_tolerance_hides_an_excess_however_small():
    assert guard(row(amount=10.096, used=5.0)).check(call(), STATE) is PROCEED                  # exactly the amount
    for amount in (10.0959999995, 10.095999999999):                       # 5e-10 and 1e-12 short: the old 1e-9 slack hid both
        verdict = guard(row(amount=amount, used=5.0)).check(call(), STATE)
        assert isinstance(verdict, Pause), amount


def test_a_row_without_the_exact_consumption_falls_back_to_the_displayed_one():
    assert isinstance(guard(row(amount=10, used=5.0)).check(call(), STATE), Pause)


def test_the_money_of_the_estimate_has_no_binary_noise():
    # 0.1 * 3 is 0.30000000000000004 in binary floats; in decimals a budget of exactly 0.3 is met, not passed.
    triple = spec("triple", pricing(0.1, 0.1), max_output_tokens=2)       # (1 + 2) tokens at 0.1
    assert guard(row(amount=0.3, used=0.0)).check(call(triple, estimated_input_tokens=1), STATE) is PROCEED
    assert isinstance(guard(row(amount=0.29999, used=0.0)).check(call(triple, estimated_input_tokens=1), STATE), Pause)


# --- the cost bound of the answer ----------------------------------------------------------------------------------


def test_a_call_is_priced_at_the_largest_answer_the_model_declares():
    big = spec("big", pricing(0.001, 0.001), max_output_tokens=10_000)
    verdict = guard(row(amount=10, used=0)).check(call(big), STATE)
    assert isinstance(verdict, Pause) and verdict.request["estimate"]["amount"] == pytest.approx(11.0)   # 1000 + 10000 tokens
    assert verdict.request["estimate"]["output_tokens"] == 10_000


def test_a_paid_answer_without_a_declared_maximum_cannot_be_bounded_so_the_call_is_held():
    open_ended = spec("open", pricing(0.001, 0.001))                 # a price, but no max_output_tokens
    verdict = guard(row(amount=1_000_000, used=0)).check(call(open_ended), STATE)
    assert isinstance(verdict, Pause) and verdict.request["reasons"] == [REASON_PRICE_UNKNOWN]
    (breach,) = verdict.request["breaches"]
    assert breach["unknown_cause"] == CAUSE_OUTPUT_UNBOUNDED and breach["projected"] is None
    estimate = verdict.request["estimate"]
    assert estimate["amount"] is None and estimate["output_tokens"] is None and estimate["unknown_cause"] == CAUSE_OUTPUT_UNBOUNDED
    assert estimate["price_evidence"]["output"]["amount"] == 0.001 and "max_output_tokens" in verdict.message


def test_a_free_answer_needs_no_maximum_and_only_the_prompt_is_priced():
    free_output = spec("free-output", pricing(0.001, 0))
    assert guard(row(amount=10, used=0)).check(call(free_output), STATE) is PROCEED              # 1000 tokens: 1.0
    verdict = guard(row(amount=10, used=9.5)).check(call(free_output), STATE)
    assert isinstance(verdict, Pause) and verdict.request["breaches"][0]["reason"] == REASON_OVER_BUDGET
    assert verdict.request["estimate"]["amount"] == pytest.approx(1.0)


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "4096"])
def test_a_declared_maximum_is_a_positive_whole_number(bad):
    with pytest.raises(CatalogError, match="max_output_tokens"):
        spec("bad", None, max_output_tokens=bad)
    assert spec("none", None).max_output_tokens is None


def test_the_catalog_loads_a_declared_maximum(tmp_path):
    from multiagent.catalog import Catalog

    models = tmp_path / "models.yaml"
    models.write_text("models:\n  m:\n    provider: lab\n    model: m\n    tier: cloud\n    structured_output: true\n"
                      "    web: false\n    max_output_tokens: 8192\n", encoding="utf-8")
    (tmp_path / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    assert Catalog(Settings(_env_file=None), catalog_dir=tmp_path).models["m"].max_output_tokens == 8192


# --- no known price is a risk --------------------------------------------------------------------------------------


@pytest.mark.parametrize("model", [
    spec("no-pricing", None),
    spec("expired", pricing(0.001, 0.001, valid_until="2026-01-02T00:00:00Z"), max_output_tokens=4096),
    spec("not-yet", pricing(0.001, 0.001, valid_from="2035-01-01T00:00:00Z"), max_output_tokens=4096),
    spec("no-evidence", pricing(0.001, 0.001, provenance="unknown"), max_output_tokens=4096),
    spec("per-character", pricing(0.001, 0.001, unit="character"), max_output_tokens=4096),
])
def test_a_call_without_usable_price_evidence_pauses_even_far_below_the_budget(model):
    made = guard(row(amount=1_000_000, used=0))
    verdict = made.check(call(model), STATE)
    assert isinstance(verdict, Pause)
    (breach,) = verdict.request["breaches"]
    assert breach["reason"] == REASON_PRICE_UNKNOWN and breach["projected"] is None
    assert verdict.request["estimate"]["amount"] is None and verdict.request["estimate"]["price_evidence"] is None
    assert verdict.request["reasons"] == [REASON_PRICE_UNKNOWN] and made.reader.booked == []


def test_a_call_whose_prompt_was_not_estimated_is_unknown_too():
    verdict = guard(row(amount=1_000_000)).check(call(estimated_input_tokens=0), STATE)
    assert isinstance(verdict, Pause) and verdict.request["reasons"] == [REASON_PRICE_UNKNOWN]
    assert verdict.request["breaches"][0]["unknown_cause"] == "input_tokens_unknown"


def test_a_price_in_another_currency_than_the_budget_cannot_be_compared_so_it_pauses():
    euro = spec("euro", pricing(0.001, 0.001, currency="EUR"), max_output_tokens=4096)
    made = guard(row(amount=1_000_000, currency="USD"))
    verdict = made.check(call(euro), STATE)
    assert isinstance(verdict, Pause) and verdict.request["reasons"] == [REASON_CURRENCY_NOT_PRICED]
    assert made.reader.booked == []                                  # nothing to reserve against a currency it cannot compare
    same = spec("same", pricing(0.001, 0.001), max_output_tokens=4096)
    assert guard(row(amount=1_000_000, currency="usd")).check(call(same), STATE) is PROCEED


def test_a_declared_zero_price_costs_nothing_and_is_never_held_not_even_by_a_spent_budget():
    free = spec("local", pricing(0, 0, provenance="local_no_charge"))
    made = guard(row(amount=10, used=10))
    assert made.check(call(free), STATE) is PROCEED
    assert guard(row(amount=10, used=10, currency="EUR")).check(call(free), STATE) is PROCEED   # zero in any currency
    assert made.reader.booked == [] and made.reader.sections == 0     # a call that costs nothing reserves nothing


def test_an_estimator_that_fails_counts_as_unknown(monkeypatch):
    import multiagent.observability.cost as cost

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(cost, "estimate_call_cost", broken)
    verdict = guard(row(amount=1_000_000)).check(call(), STATE)
    assert isinstance(verdict, Pause) and verdict.request["reasons"] == [REASON_PRICE_UNKNOWN]
    assert verdict.request["breaches"][0]["unknown_cause"] == "estimator_failed"


# --- modes ---------------------------------------------------------------------------------------------------------


def test_a_budget_in_warn_mode_never_holds_a_call_even_when_it_is_passed():
    reader_rows = (row(amount=1, used=5, mode="warn"),)
    made = guard(*reader_rows, cut=False)
    assert made.check(call(), STATE) is PROCEED
    assert made.check(call(spec("unpriced")), STATE) is PROCEED      # not even an unpriced call: it is only a warning
    assert made.reader.sections == 0 and made.reader.booked == []    # and nothing is reserved for a warning


def test_only_the_rows_in_cut_mode_count_when_modes_are_mixed():
    made = guard(row(budget_id="soft", amount=1, used=5, mode="warn"), row(budget_id="hard", amount=100, used=1))
    assert made.check(call(), STATE) is PROCEED
    assert made.reader.booked[0]["budgets"] == ["hard|run-1|USD"]     # only the cut budget is booked against
    verdict = guard(row(budget_id="soft", amount=100, mode="warn"), row(budget_id="hard", amount=3, used=1)).check(call(), STATE)
    assert [b["budget_id"] for b in verdict.request["breaches"]] == ["hard"]


def test_the_budgets_of_other_runs_and_other_projects_do_not_limit_this_call():
    made = guard(row(budget_id="other-run", run_id="run-2", amount=1, used=1),
                 row(budget_id="other-project", scope="project", project="beta", amount=1, used=1))
    state = {**STATE, "project_id": "p-1", "request": {"project_name": "alpha"}}
    assert made.check(call(), state) is PROCEED
    for tied in ("alpha", "p-1"):                                    # a project is matched by its name or its id
        verdict = guard(row(budget_id="mine", scope="project", project=tied, amount=1, used=1)).check(call(), state)
        assert isinstance(verdict, Pause), tied


def test_a_run_whose_project_is_unknown_is_limited_by_every_project_budget():
    made = guard(row(budget_id="alpha-cap", scope="project", project="alpha", amount=1, used=1))
    assert isinstance(made.check(call(), STATE), Pause)              # fail closed: the state names no project


def test_a_month_budget_limits_every_run_and_one_tied_to_a_project_only_its_runs():
    assert isinstance(guard(row(scope="month", amount=1, used=1)).check(call(), STATE), Pause)
    tied = row(scope="month", project="beta", amount=1, used=1)
    state = {**STATE, "project_id": "p-1", "request": {"project_name": "alpha"}}
    assert guard(tied).check(call(), state) is PROCEED


# --- two calls, one budget -----------------------------------------------------------------------------------------


def test_a_call_in_flight_counts_against_the_next_one_so_the_two_cannot_pass_together():
    made = guard(row(scope="month", amount=10, used=0))              # each call is about 5.096: one fits, two do not
    assert made.check(call(run_id="run-1"), STATE) is PROCEED
    verdict = made.check(call(run_id="run-2"), {**STATE, "id": "run-2"})
    assert isinstance(verdict, Pause)
    (breach,) = verdict.request["breaches"]
    assert breach["reason"] == REASON_OVER_BUDGET and breach["reserved"] == pytest.approx(5.096)
    assert breach["projected"] == pytest.approx(10.192) and "reserved by calls in flight" in verdict.message
    assert len(made.reader.booked) == 1                              # the held call booked nothing


def test_reservations_already_in_flight_are_added_to_the_consumption():
    made = guard(row(amount=10, used=0), reserved={"cap|run-1|USD": "5.0"})
    verdict = made.check(call(), STATE)                              # 0 used + 5.0 in flight + 5.096 > 10
    assert isinstance(verdict, Pause) and verdict.request["breaches"][0]["reserved"] == 5.0
    assert guard(row(amount=10, used=0), reserved={"cap|run-1|USD": "4.9"}).check(call(), STATE) is PROCEED
    assert guard(row(amount=10, used=0), reserved={"cap|run-1|EUR": "99", "other|run-1|USD": "99"}).check(call(), STATE) is PROCEED


def test_a_call_books_against_every_cut_budget_it_was_compared_with():
    made = guard(row(budget_id="run-cap", amount=100), row(budget_id="month-cap", scope="month", amount=500),
                 row(budget_id="soft", amount=1, mode="warn"))
    assert made.check(call(), STATE) is PROCEED
    assert made.reader.booked[0]["budgets"] == ["month-cap||USD", "run-cap|run-1|USD"]


def test_a_reader_that_cannot_reserve_is_compared_without_reservations():
    made = BudgetGuard(BudgetPolicy(reader=PlainReader(row(amount=10, used=5.0)), cut=True), emit=lambda *a: None)
    assert isinstance(made.check(call(), STATE), Pause)
    assert BudgetGuard(BudgetPolicy(reader=PlainReader(row(amount=100)), cut=True)).check(call(), STATE) is PROCEED


# --- notify_budgets is wired ---------------------------------------------------------------------------------------


def test_every_check_announces_the_alerts_through_notify_budgets_with_the_run_of_the_call():
    reader = Reader(row(amount=1000))
    made = BudgetGuard(BudgetPolicy(reader=reader, cut=True), emit=lambda *a: None)
    made.check(call(run_id="run-9"), {**STATE, "id": "run-9"})
    made.check(call(run_id=None), {**STATE, "id": "run-7"})          # a call without a run id uses the id of the state
    assert reader.asked == ["run-9", "run-7"]


def test_the_alerts_are_announced_even_when_every_budget_is_in_warn_mode_and_nothing_is_held():
    reader = Reader(row(amount=1, used=5, mode="warn"))
    made = BudgetGuard(BudgetPolicy(reader=reader, cut=False))
    assert made.check(call(), STATE) is PROCEED and reader.asked == ["run-1"]


def test_a_reader_that_fails_never_cuts_a_call_in_warn_mode(caplog):
    made = guard(raises=RuntimeError("database is locked"), cut=False)
    with caplog.at_level(logging.WARNING, logger="multiagent.budget_cut"):
        assert made.check(call(), STATE) is PROCEED
    assert any("could not be read" in record.getMessage() for record in caplog.records)


def test_a_reader_that_fails_stops_the_call_when_a_budget_is_in_cut_mode():
    with pytest.raises(RuntimeError, match="database is locked"):
        guard(raises=RuntimeError("database is locked"), cut=True).check(call(), STATE)


def test_an_answer_that_is_not_the_state_of_the_budgets_is_unreadable():
    class Odd:
        def notify_budgets(self, *, run_id=None):
            return ["not", "a", "mapping"]

    class NoRows:
        def notify_budgets(self, *, run_id=None):
            return {"emitted": []}

    for reader in (Odd(), NoRows()):
        with pytest.raises(BudgetUnavailable):
            BudgetGuard(BudgetPolicy(reader=reader, cut=True)).check(call(), STATE)
        assert BudgetGuard(BudgetPolicy(reader=reader, cut=False)).check(call(), STATE) is PROCEED


def test_without_a_usage_service_a_cut_budget_fails_closed_and_a_warning_one_goes_on():
    with pytest.raises(BudgetUnavailable):
        BudgetGuard(BudgetPolicy(reader=None, cut=True)).check(call(), STATE)
    assert BudgetGuard(BudgetPolicy(reader=None, cut=False)).check(call(), STATE) is PROCEED


# --- an approval is for one call -----------------------------------------------------------------------------------


def over() -> BudgetGuard:
    return guard(row(amount=10, used=5.0))


def test_an_approval_of_the_person_lets_exactly_that_call_through_and_is_recorded_as_released():
    made = over()
    state = {**STATE, "events": [approved(ordinal=3, retry=1)]}
    assert made.check(call(ordinal=3, retry=1), state) is PROCEED
    (kind, payload), = made.sink
    assert kind == EVENT_RELEASED
    assert (payload["approval_id"], payload["step_id"], payload["attempt"], payload["model_id"]) == ("a1", "create", 1, "paid")
    assert (payload["ordinal"], payload["retry"]) == (3, 1) and payload["estimate"]["amount"] == pytest.approx(5.096)
    assert payload["breaches"] == [KEY] and payload["estimate_amount"] == "5.096"
    assert len(made.reader.booked) == 1                              # an approved overspend is booked: the others see it


def test_the_approval_is_spent_by_the_call_it_released_so_the_next_call_is_held_again():
    made = over()
    spent = {**STATE, "events": [approved(), released()]}
    verdict = made.check(call(), spent)                              # a retry, a later call, the same call again: all held
    assert isinstance(verdict, Pause) and made.sink == []
    assert isinstance(made.check(call(ordinal=2, retry=1), spent), Pause)
    elsewhere = {**STATE, "events": [approved(), released("another-approval")]}
    assert made.check(call(), elsewhere) is PROCEED                  # spending a different approval does not spend this one


def test_one_approval_lets_one_call_through_even_if_the_same_check_is_repeated_in_one_state():
    made = over()
    events_so_far: list[dict[str, Any]] = [approved()]
    assert made.check(call(), {**STATE, "events": list(events_so_far)}) is PROCEED
    events_so_far.append({"event_type": made.sink[-1][0], "payload": made.sink[-1][1]})
    assert isinstance(made.check(call(), {**STATE, "events": list(events_so_far)}), Pause)


def test_the_oldest_unspent_approval_that_covers_the_breaches_is_the_one_spent():
    made = over()
    two = {**STATE, "events": [approved(approval_id="a1", breaches=(MONTH_KEY,)), approved(approval_id="a2")]}
    assert made.check(call(), two) is PROCEED and made.sink[0][1]["approval_id"] == "a2"
    both = {**STATE, "events": [approved(approval_id="b1"), approved(approval_id="b2")]}
    made = over()
    assert made.check(call(), both) is PROCEED and made.sink[0][1]["approval_id"] == "b1"
    assert made.check(call(), {**both, "events": both["events"] + [released("b1")]}) is PROCEED
    assert made.sink[1][1]["approval_id"] == "b2"


@pytest.mark.parametrize("other", [
    {"model_id": "other-model"}, {"step_id": "review"}, {"attempt": 2}, {"ordinal": 2}, {"retry": 1},
], ids=["other model", "other step", "other attempt", "other position", "other retry"])
def test_an_approval_covers_only_the_call_it_was_given_for(other):
    made = over()
    assert isinstance(made.check(call(**other), {**STATE, "events": [approved()]}), Pause)
    assert made.sink == []


def test_an_approval_does_not_cover_a_budget_that_was_not_in_breach_when_it_was_given():
    made = guard(row(budget_id="cap", amount=10, used=5), row(budget_id="month", scope="month", amount=1, used=1))
    verdict = made.check(call(), {**STATE, "events": [approved()]})   # the person approved "cap" only
    assert isinstance(verdict, Pause) and [b["budget_id"] for b in verdict.request["breaches"]] == ["cap", "month"]
    both = approved(breaches=(KEY, MONTH_KEY))
    assert made.check(call(), {**STATE, "events": [both]}) is PROCEED


@pytest.mark.parametrize("stale", [
    approved(breaches=("cap|run-1|None|USD|20|over_budget",)),       # the amount of the budget changed
    approved(breaches=("cap|run-1|None|EUR|10|over_budget",)),       # so did its currency
    approved(breaches=("cap|run-2|None|USD|10|over_budget",)),       # or the run it limits
    approved(estimate_amount="4.0"),                                 # the call is priced differently from what was approved
    approved(estimate_amount="5.097"),
    approved(estimate_amount=None),                                  # an approval of an unknown cost is not one of a known cost
], ids=["amount", "currency", "run", "lower estimate", "higher estimate", "unknown estimate"])
def test_an_approval_is_tied_to_the_budget_and_the_estimate_it_was_given_for(stale):
    made = over()
    assert isinstance(made.check(call(), {**STATE, "events": [stale]}), Pause)
    assert made.check(call(), {**STATE, "events": [approved(estimate_amount="5.0960")]}) is PROCEED   # same decimal, other text


def test_an_approval_is_tied_to_the_period_of_a_month_budget():
    month = row(budget_id="month", scope="month", amount=10, used=5.0, period="2026-10")
    covered = approved(breaches=("month||2026-10|USD|10|over_budget",))
    last_month = approved(breaches=("month||2026-09|USD|10|over_budget",))
    assert guard(month).check(call(), {**STATE, "events": [covered]}) is PROCEED
    assert isinstance(guard(month).check(call(), {**STATE, "events": [last_month]}), Pause)


def test_an_approval_of_an_overspend_does_not_cover_an_unknown_price():
    made = guard(row(amount=1_000_000))
    assert isinstance(made.check(call(spec("unpriced")), {**STATE, "events": [approved(model_id="unpriced")]}), Pause)


def test_an_approval_of_an_unknown_price_covers_that_one_call_and_books_nothing():
    unknown = approved(model_id="unpriced", breaches=("cap|run-1|None|USD|1000000|price_unknown",), estimate_amount=None)
    made = guard(row(amount=1_000_000))
    assert made.check(call(spec("unpriced")), {**STATE, "events": [unknown]}) is PROCEED
    assert made.sink[0][1]["estimate_amount"] is None and made.reader.booked == []
    assert isinstance(made.check(call(spec("unpriced")), {**STATE, "events": [unknown, released()]}), Pause)


def test_without_a_way_to_record_the_release_an_approval_is_never_used():
    made = BudgetGuard(BudgetPolicy(reader=Reader(row(amount=10, used=5)), cut=True), emit=None)
    assert isinstance(made.check(call(), {**STATE, "events": [approved()]}), Pause)


def test_events_without_a_usable_id_or_from_other_kinds_are_not_approvals():
    junk = [{"event_type": EVENT_APPROVED, "payload": {"step_id": "create", "attempt": 1, "model_id": "paid"}},
            {"event_type": "escalation_approved", "payload": {"approval_id": "x", "step_id": "create", "attempt": 1,
                                                              "model_id": "paid", "ordinal": 1, "retry": 0}},
            {"event_type": EVENT_APPROVED, "payload": "text"}, "text", {}]
    assert granted_approvals(junk, call()) == []
    assert isinstance(over().check(call(), {**STATE, "events": junk}), Pause)
    assert isinstance(over().check(call(), {"id": "run-1"}), Pause)  # a state without events approves nothing


def test_a_failure_to_record_the_release_stops_the_call_and_ends_its_reservation():
    def refuses(event_type, payload):
        raise RuntimeError("cannot write the event")

    reader = Reader(row(amount=10, used=5))
    made = BudgetGuard(BudgetPolicy(reader=reader, cut=True), emit=refuses)
    with pytest.raises(RuntimeError, match="cannot write"):
        made.check(call(), {**STATE, "events": [approved()]})
    assert reader.settled == ["r1"]                                  # the call did not happen: its reservation ends


# --- off by default ------------------------------------------------------------------------------------------------


def test_without_configured_budgets_there_is_no_policy_and_no_guard():
    assert Settings(_env_file=None).usage_budgets == []
    assert policy_from_settings(Settings(_env_file=None), Reader()) is None
    assert build_call_guards(GuardEnvironment(budgets=policy_from_settings(Settings(_env_file=None)))).names == ("limits", "secret")
    assert build_call_guards(GuardEnvironment()).names == ("limits", "secret")


def test_the_policy_is_cut_when_any_budget_is_in_cut_mode_by_itself_or_by_default():
    month = {"id": "m", "scope": "month", "amount": 5}
    assert policy_from_settings(Settings(_env_file=None, usage_budgets=[month]), None).cut is False
    assert policy_from_settings(Settings(_env_file=None, usage_budgets=[{**month, "mode": "cut"}])).cut is True
    assert policy_from_settings(Settings(_env_file=None, usage_budgets=[month], usage_budget_mode="cut")).cut is True
    soft = {**month, "mode": "warn"}
    assert policy_from_settings(Settings(_env_file=None, usage_budgets=[soft], usage_budget_mode="cut")).cut is False
    reader = Reader()
    assert policy_from_settings(Settings(_env_file=None, usage_budgets=[month]), reader).reader is reader


# --- in the chain --------------------------------------------------------------------------------------------------


def test_the_budget_guard_runs_last_before_the_reservation_and_a_pause_reserves_nothing():
    reserved: list[CallContext] = []
    chain = build_call_guards(GuardEnvironment(budgets=BudgetPolicy(reader=Reader(row(amount=10, used=5)), cut=True),
                                               emit=lambda *a: None), reserve=reserved.append)
    assert chain.names == ("limits", "secret", GUARD_NAME)
    assert [g.order for g in chain.guards] == [200, 240, ORDER_BUDGET]
    verdict = chain.check(call(outgoing_text="A plain request"), STATE)
    assert isinstance(verdict, Pause) and verdict.guard == GUARD_NAME and reserved == []
    ok = build_call_guards(GuardEnvironment(budgets=BudgetPolicy(reader=Reader(row(amount=999)), cut=True)),
                           reserve=reserved.append)
    assert ok.check(call(outgoing_text="A plain request"), STATE) is PROCEED and len(reserved) == 1


def test_the_budget_guard_fails_closed_cannot_be_skipped_and_the_summarizer_cannot_leave_it_out():
    made = BudgetGuard(BudgetPolicy(reader=Reader()))
    assert (made.observer, made.skippable) == (False, False)
    chain = CallGuardChain([made])
    with pytest.raises(ValueError, match="cannot be skipped"):
        chain.check(call(), STATE, skip=(GUARD_NAME,))


def test_a_limit_stops_the_call_before_the_budget_is_even_read():
    reader = Reader(row(amount=10, used=5))
    chain = build_call_guards(GuardEnvironment(budgets=BudgetPolicy(reader=reader, cut=True)))
    verdict = chain.check(call(), {**STATE, "llm_calls": 10})
    assert verdict.guard == "limits" and reader.asked == []


# --- the real engine and services ----------------------------------------------------------------------------------

CARD = pricing(0.0001, 0.0001)      # the provider answers 100 tokens in and 50 out: 0.015 per call, about 0.45 estimated


def world(tmp_path, budgets=(), *, ollama=None, gemini=None, card=CARD, **settings):
    """The services of a real system whose providers are scripted; ``local_default`` is priced with the synthetic card
    and declares the 4096 output tokens the estimate is bounded with."""
    ollama, gemini = ollama or Provider("ollama"), gemini or Provider("gemini")
    options = dict(_env_file=None, data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                   runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets", worker_enabled=False,
                   gemini_api_key="", usage_budgets=list(budgets), gui_token_file=tmp_path / "gui-token", **settings)
    services = ApplicationServices.create(Settings(**options), adapter_overrides={"ollama": ollama, "gemini": gemini})
    system = services.system
    system.ollama, system.gemini = ollama, gemini
    if card is not None:
        system.catalog.models["local_default"] = dataclasses.replace(
            system.catalog.models["local_default"], pricing=card, max_output_tokens=4096)
    return services


def begin(services) -> str:
    return start(services.system)


def resume(services) -> None:
    assert services.system.worker.process_once()


def alerts(services) -> list[dict[str, Any]]:
    return [event for event in services.tasks.store.events(limit=500) if event["type"] == BUDGET_EVENT]


def reservations(services) -> dict[str, list[dict[str, Any]]]:
    found = {"reserved": [], "settled": []}
    for event in services.tasks.store.events(limit=5000):
        if event["type"] == RESERVED_EVENT:
            found["reserved"].append(event)
        elif event["type"] == SETTLED_EVENT:
            found["settled"].append(event)
    return found


def outstanding(services) -> list[str]:
    seen = reservations(services)
    done = {event["cause"] for event in seen["settled"]}
    return [event["cause"] for event in seen["reserved"] if event["cause"] not in done]


CUT = {"id": "cap", "scope": "run", "amount": 0.3, "mode": "cut"}


def test_a_call_that_would_pass_the_budget_waits_for_a_person_and_nothing_is_called(tmp_path):
    services = world(tmp_path, [CUT])
    run = get(services.system, begin(services))
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "budget_cut_approval"
    assert (run["waiting_step"], run["waiting_attempt"], run["llm_calls"]) == ("create", 1, 0)
    (paused,) = events(run, "call_paused")
    assert paused["guard"] == "budget" and paused["model_id"] == "local_default"
    breach = paused["request"]["breaches"][0]
    assert breach["budget_id"] == "cap" and breach["reason"] == REASON_OVER_BUDGET and breach["projected"] > 0.3
    assert paused["request"]["estimate"]["amount"] > 0.3 and "approve" in paused["message"]
    assert services.system.ollama.prompts == [] and services.system.gemini.prompts == []   # no paid call was made
    assert not run["error"] and "workflow_failed" not in names(run)
    assert not services.system.worker.process_once()                  # nothing approves it by itself: the run keeps waiting
    assert reservations(services)["reserved"] == []                   # and a held call reserves nothing


def test_approving_lets_exactly_the_held_call_through(tmp_path):
    services = world(tmp_path, [CUT])
    run_id = begin(services)
    queued = services.runs.approve(run_id, by="Ana", channel="gui")
    assert queued["status"] == RunStatus.QUEUED.value and queued["waiting_reason"] == ""
    resume(services)
    run = get(services.system, run_id)
    assert services.system.ollama.models == ["local_default"] and run["llm_calls"] == 1     # the one held call was made
    (approval,) = events(run, EVENT_APPROVED)
    assert (approval["step_id"], approval["attempt"], approval["model_id"], approval["by"], approval["channel"]) == (
        "create", 1, "local_default", "Ana", "gui")
    assert (approval["ordinal"], approval["retry"]) == (1, 0)
    assert approval["breaches"] == [f"cap|{run_id}|None|USD|0.3|over_budget"]
    assert approval["estimate_amount"] == events(run, "call_paused")[0]["request"]["estimate"]["exact"]
    (release,) = events(run, EVENT_RELEASED)
    assert release["approval_id"] == approval["approval_id"] and release["model_id"] == "local_default"
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"   # the step went on to its review
    assert names(run).index(EVENT_APPROVED) < names(run).index(EVENT_RELEASED)
    assert len(reservations(services)["reserved"]) == 1 and outstanding(services) == []   # booked, then ended by the record


def test_each_call_needs_its_own_approval_and_one_for_a_later_call_waits_until_the_step_reaches_it(tmp_path):
    # The first model fails twice. The step starts its router call over after every pause, so: the first call is held
    # (A1), spent, fails, and the fallback is held at position 2 (A2). The step starts over: position 1 is held again
    # (A3, a new call that costs money again) while A2 waits for position 2; A3 is spent, fails, and A2 is spent there.
    down = [ProviderUnavailable("down"), ProviderUnavailable("down")]
    services = world(tmp_path, [CUT], ollama=Provider("ollama", down))
    run_id = begin(services)
    services.runs.approve(run_id)
    resume(services)
    run = get(services.system, run_id)
    assert services.system.ollama.models == ["local_default"] and services.system.gemini.prompts == []
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "budget_cut_approval"
    second = events(run, "call_paused")[-1]
    assert (second["model_id"], second["ordinal"], second["request"]["reasons"]) == ("cloud_fast", 2, [REASON_PRICE_UNKNOWN])
    assert run["llm_calls"] == 1                                      # the held fallback was not booked

    services.runs.approve(run_id)                                     # A2: for the fallback, position 2
    resume(services)
    run = get(services.system, run_id)
    third = events(run, "call_paused")[-1]
    assert (third["model_id"], third["ordinal"]) == ("local_default", 1)     # A2 does not cover position 1
    assert services.system.ollama.models == ["local_default"] and run["llm_calls"] == 1

    services.runs.approve(run_id)                                     # A3: position 1 again
    resume(services)
    run = get(services.system, run_id)
    assert services.system.ollama.models == ["local_default"] * 2 and services.system.gemini.models == ["cloud_fast"]
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review" and run["llm_calls"] == 3
    granted = [item["approval_id"] for item in events(run, EVENT_APPROVED)]
    spent = [(item["approval_id"], item["model_id"], item["ordinal"]) for item in events(run, EVENT_RELEASED)]
    assert len(granted) == 3 and sorted(i for i, _, _ in spent) == sorted(granted)      # every approval was spent exactly once
    assert [(model, ordinal) for _, model, ordinal in spent] == [("local_default", 1), ("local_default", 1), ("cloud_fast", 2)]
    assert outstanding(services) == []


def test_a_new_attempt_of_the_step_needs_a_new_approval(tmp_path):
    services = world(tmp_path, [CUT])
    run_id = begin(services)
    services.runs.approve(run_id)
    resume(services)
    assert get(services.system, run_id)["waiting_reason"] == "review"
    services.runs.regenerate(run_id)                                   # the person asks for the step again: attempt 2
    resume(services)
    run = get(services.system, run_id)
    assert run["waiting_reason"] == "budget_cut_approval" and run["waiting_attempt"] == 2
    assert events(run, "call_paused")[-1]["attempt"] == 2 and len(services.system.ollama.models) == 1


def test_rejecting_fails_the_run_and_the_call_is_never_made(tmp_path):
    services = world(tmp_path, [CUT])
    run_id = begin(services)
    failed = services.runs.reject(run_id, "too expensive", by="Ana", channel="gui")
    assert failed["status"] == RunStatus.FAILED.value and failed["waiting_reason"] == ""
    assert "rejected by a human: too expensive" in failed["error"] and "cap" in failed["error"]
    run = get(services.system, run_id)
    (refusal,) = events(run, EVENT_REJECTED)
    assert (refusal["feedback"], refusal["by"], refusal["channel"], refusal["model_id"]) == (
        "too expensive", "Ana", "gui", "local_default")
    assert "approval_id" not in refusal and not events(run, EVENT_APPROVED)
    assert events(run, "workflow_failed")[0]["type"] == "BudgetCutRejected"
    assert services.system.ollama.prompts == [] and run["llm_calls"] == 0


def test_a_decision_after_a_cancellation_or_a_second_decision_is_refused(tmp_path):
    services = world(tmp_path, [CUT])
    run_id = begin(services)
    services.runs.cancel(run_id)
    with pytest.raises(Exception):
        services.runs.approve(run_id)
    assert not events(get(services.system, run_id), EVENT_APPROVED)
    other = begin(services)
    services.runs.approve(other)
    with pytest.raises(Exception):                                    # it is queued now, no longer waiting for the cut
        services.runs.approve(other)
    assert len(events(get(services.system, other), EVENT_APPROVED)) == 1


def test_the_store_refuses_to_settle_a_cut_the_run_is_not_waiting_for(tmp_path):
    services = world(tmp_path, [CUT])
    run_id = begin(services)
    run = services.system.store.get_run(run_id)
    services.runs.approve(run_id)                                     # the persisted wait moves on
    with pytest.raises(InvalidStateTransition, match="changed"):
        services.system.store.finish_budget_cut(run, approve=True)


def test_a_call_below_the_budget_goes_ahead_and_the_budget_is_read_for_it(tmp_path):
    services = world(tmp_path, [{**CUT, "amount": 100}])
    run = get(services.system, begin(services))
    assert services.system.ollama.models == ["local_default"] and run["llm_calls"] == 1
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert not events(run, "call_paused") and not events(run, EVENT_APPROVED) and not events(run, EVENT_RELEASED)


def test_a_model_with_no_price_is_held_in_a_real_run_however_large_the_budget(tmp_path):
    services = world(tmp_path, [{**CUT, "amount": 1_000_000}], card=None)
    services.system.catalog.models["local_default"] = dataclasses.replace(services.system.catalog.models["local_default"],
                                                                         pricing=None)
    run = get(services.system, begin(services))
    assert run["waiting_reason"] == "budget_cut_approval" and services.system.ollama.prompts == []
    (paused,) = events(run, "call_paused")
    assert paused["request"]["reasons"] == [REASON_PRICE_UNKNOWN] and paused["request"]["estimate"]["amount"] is None


def test_a_priced_model_that_declares_no_maximum_answer_is_held_in_a_real_run(tmp_path):
    services = world(tmp_path, [{**CUT, "amount": 1_000_000}])
    spec_now = services.system.catalog.models["local_default"]
    services.system.catalog.models["local_default"] = dataclasses.replace(spec_now, max_output_tokens=None)
    run = get(services.system, begin(services))
    assert run["waiting_reason"] == "budget_cut_approval" and services.system.ollama.prompts == []
    (paused,) = events(run, "call_paused")
    assert paused["request"]["breaches"][0]["unknown_cause"] == CAUSE_OUTPUT_UNBOUNDED


def test_the_bundled_local_model_declares_a_zero_price_so_it_is_not_held(tmp_path):
    services = world(tmp_path, [{**CUT, "amount": 0.01}], card=None)    # the catalog's own local_no_charge evidence
    run = get(services.system, begin(services))
    assert services.system.ollama.models == ["local_default"] and not events(run, "call_paused")
    assert reservations(services)["reserved"] == []                   # a call that costs nothing reserves nothing


def test_budgets_in_warn_mode_never_hold_a_call_and_still_announce_their_alerts(tmp_path):
    services = world(tmp_path, [{"id": "soft", "scope": "run", "amount": 0.01}])       # warn is the default mode
    run = get(services.system, begin(services))
    assert services.system.ollama.models == ["local_default"] and not events(run, "call_paused")
    # The call cost 0.015 against 0.01: the alerts reached are announced after the call was recorded.
    found = alerts(services)
    assert sorted(item["payload"]["threshold"] for item in found) == [80.0, 95.0]
    assert {item["payload"]["budget"] for item in found} == {"soft"} and found[0]["payload"]["run_id"] == run["id"]
    assert reservations(services)["reserved"] == []                   # a warning reserves nothing


def test_notify_budgets_is_called_before_the_call_and_after_every_recorded_call(tmp_path, monkeypatch):
    services = world(tmp_path, [{**CUT, "amount": 100}])
    asked: list[Any] = []
    original = services.usage.notify_budgets

    def spy(*, run_id=None):
        asked.append((run_id, len(services.system.ollama.prompts)))   # how many calls had been made when it was asked
        return original(run_id=run_id)

    monkeypatch.setattr(services.usage, "notify_budgets", spy)
    run_id = begin(services)
    assert asked == [(run_id, 0), (run_id, 1)]                        # by the guard before the call, by the hook after it


def test_a_failing_announcement_never_touches_the_call_or_its_telemetry(tmp_path, monkeypatch):
    services = world(tmp_path, [{"id": "soft", "scope": "run", "amount": 0.01}])

    def broken(*, run_id=None):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(services.usage, "notify_budgets", broken)
    run_id = begin(services)
    run = get(services.system, run_id)
    assert services.system.ollama.models == ["local_default"] and run["waiting_reason"] == "review"
    with services.system.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM telemetry WHERE run_id=?", (run_id,)).fetchone()["c"] == 1


def test_without_budgets_nothing_changes_and_nothing_is_read_or_announced(tmp_path, monkeypatch):
    services = world(tmp_path, [])
    asked: list[Any] = []
    monkeypatch.setattr(services.usage, "notify_budgets", lambda **kw: asked.append(kw))
    engine = services.system.engine
    assert engine._budget_policy() is None
    run = get(services.system, begin(services))
    assert asked == [] and alerts(services) == [] and reservations(services) == {"reserved": [], "settled": []}
    assert run["waiting_reason"] == "review" and services.system.ollama.models == ["local_default"]
    assert not ({"call_paused", EVENT_APPROVED, EVENT_RELEASED} & set(names(run)))


def test_a_bare_engine_with_a_cut_budget_and_no_usage_service_makes_no_call(tmp_path):
    from multiagent.bootstrap import build_system

    ollama = Provider("ollama")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets", worker_enabled=False,
                        gemini_api_key="", usage_budgets=[CUT])
    system = build_system(settings, adapter_overrides={"ollama": ollama, "gemini": Provider("gemini")})
    assert system.engine.budgets_source is None
    run = get(system, start(system))
    assert ollama.prompts == [] and run["status"] == RunStatus.FAILED.value        # fail closed: it could not read the budgets


def test_every_engine_of_the_services_is_attached_including_those_of_bundles(tmp_path):
    from test_context_compaction import WINDOW, write_bundle

    root = write_bundle(tmp_path / "bundle", window=WINDOW, assign=False)
    services = world(tmp_path / "w", [CUT])
    services._bundles["chain"] = root.resolve()
    try:
        engine = services.runs._bundle_runtime("chain").service.engine
        assert engine.budgets_source is services.usage and services.system.engine.budgets_source is services.usage
        assert engine.usage_recorder.after_record is not None and engine._budget_policy().cut is True
    finally:
        services.runs.close()


# --- two calls, one budget: the reservation is a write transaction --------------------------------------------------


def fresh_runs(services, count: int) -> list[str]:
    """``count`` queued runs that nobody processes (the budget is shared by them)."""
    return [services.system.run_service.create_social_post(SocialPostRequest(
        project_name="uso-t04", objective="Create an educational post", topic=f"topic {number}"))["id"]
        for number in range(count)]


def real_guard(services) -> BudgetGuard:
    return BudgetGuard(BudgetPolicy(reader=services.usage, cut=True), emit=lambda *a: None)


def real_call(services, run_id: str) -> tuple[CallContext, dict[str, Any]]:
    model = services.system.catalog.models["local_default"]
    return (CallContext(model_id=model.id, spec=model, run_id=run_id, step_id="create", attempt=1, ordinal=1,
                        estimated_input_tokens=1000), services.system.store.get_run(run_id))


def record_usage(services, run_id: str, *, cost: float = 0.015, success: bool = True) -> None:
    services.system.engine.usage_recorder.record(TokenUsageRecord(
        run_id=run_id, step_id="create", agent_id="writer", attempt=1, provider="ollama", model="qwen", model_digest="",
        prompt_id="p", prompt_version=1, prompt_sha256="0" * 64, brand_version=None, tokens_in=100, tokens_out=50,
        num_ctx=None, duration_ms=1, estimated_cost_usd=cost, success=success))


MONTH = {"id": "month", "scope": "month", "amount": 1.0, "mode": "cut"}    # a call is about 0.5096: one fits, two do not


def test_a_second_call_against_the_same_budget_sees_the_reservation_of_the_first(tmp_path):
    services = world(tmp_path, [MONTH])
    first, second = fresh_runs(services, 2)
    made = real_guard(services)
    assert made.check(*real_call(services, first)) is PROCEED
    verdict = made.check(*real_call(services, second))
    assert isinstance(verdict, Pause)                                 # 0.5096 reserved + 0.5096 > 1.0, though nothing is used
    (breach,) = verdict.request["breaches"]
    assert breach["used"] == 0.0 and breach["reserved"] == pytest.approx(0.5096) and breach["projected"] == pytest.approx(1.0192)
    assert len(outstanding(services)) == 1


def test_the_real_usage_of_the_first_call_replaces_its_reservation(tmp_path):
    services = world(tmp_path, [MONTH])
    first, second = fresh_runs(services, 2)
    made = real_guard(services)
    assert made.check(*real_call(services, first)) is PROCEED
    record_usage(services, first)                                     # the call finished: 0.015 used, the reservation ends
    assert outstanding(services) == [] and len(reservations(services)["settled"]) == 1
    assert made.check(*real_call(services, second)) is PROCEED        # 0.015 used + 0.5096 fits


def test_a_failed_call_ends_its_reservation_too(tmp_path):
    services = world(tmp_path, [MONTH])
    first, second = fresh_runs(services, 2)
    made = real_guard(services)
    assert made.check(*real_call(services, first)) is PROCEED
    record_usage(services, first, cost=0.0, success=False)
    assert outstanding(services) == []
    assert made.check(*real_call(services, second)) is PROCEED


def test_a_record_ends_the_latest_reservation_of_its_run_and_leaves_a_stale_one_to_expire(tmp_path):
    services = world(tmp_path, [{**MONTH, "amount": 100}])
    (run_id,) = fresh_runs(services, 1)
    made = real_guard(services)
    assert made.check(*real_call(services, run_id)) is PROCEED        # the process died after this: it never recorded
    assert made.check(*real_call(services, run_id)) is PROCEED        # the same step again after the restart
    stale, latest = outstanding(services)
    record_usage(services, run_id)
    assert outstanding(services) == [stale]


def test_a_failing_provider_in_a_real_run_ends_the_reservation_of_every_call(tmp_path):
    down = [ProviderUnavailable("down")]
    services = world(tmp_path, [{**CUT, "amount": 100}], ollama=Provider("ollama", down))
    run = get(services.system, begin(services))                       # the local call fails; the unpriced fallback is held
    assert services.system.ollama.models == ["local_default"] and run["waiting_reason"] == "budget_cut_approval"
    assert len(reservations(services)["reserved"]) == 1 and outstanding(services) == []


def test_a_reservation_nobody_settled_stops_counting_after_its_time_to_live(tmp_path, monkeypatch):
    services = world(tmp_path, [MONTH])
    first, second = fresh_runs(services, 2)
    made = real_guard(services)
    assert made.check(*real_call(services, first)) is PROCEED         # the process "dies" here: nothing is ever recorded
    assert isinstance(made.check(*real_call(services, second)), Pause)
    monkeypatch.setattr(usage_module, "RESERVATION_TTL", timedelta(0))
    assert made.check(*real_call(services, second)) is PROCEED
    assert len(reservations(services)["reserved"]) == 2               # the expired one stays in the log, only it stopped counting


def test_calls_that_run_at_the_same_time_cannot_pass_together(tmp_path):
    # The lock is the write lock of the system database, where every reservation lives (those of bundle runs too), so it
    # holds across threads and across processes that share that file. Known limit: two installations with different
    # system databases are two ledgers.
    services = world(tmp_path, [MONTH])
    count = 8
    runs = fresh_runs(services, count)
    made = real_guard(services)
    barrier = threading.Barrier(count)
    verdicts: list[Any] = [None] * count
    failures: list[BaseException] = []

    def work(slot: int) -> None:
        try:
            invocation = real_call(services, runs[slot])
            barrier.wait(timeout=30)
            verdicts[slot] = made.check(*invocation)
        except BaseException as exc:      # noqa: BLE001 - reported to the test below
            failures.append(exc)

    threads = [threading.Thread(target=work, args=(slot,)) for slot in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not failures, failures
    assert sum(1 for verdict in verdicts if verdict is PROCEED) == 1       # 1.0 fits one call of 0.5096, not two
    assert sum(1 for verdict in verdicts if isinstance(verdict, Pause)) == count - 1
    assert len(reservations(services)["reserved"]) == 1


def test_the_budget_rows_carry_the_consumption_unrounded_next_to_the_displayed_one(tmp_path):
    services = world(tmp_path, [{"id": "tight", "scope": "month", "amount": 1.0000001, "mode": "cut"}])
    (run_id,) = fresh_runs(services, 1)
    record_usage(services, run_id, cost=1.0000004)
    (found,) = services.usage.budgets()["status"]
    assert found["used"] == 1.0 and found["used_exact"] == 1.0000004     # six decimals for the eye, the whole sum to decide
    made = real_guard(services)
    assert isinstance(made.check(*real_call(services, run_id)), Pause)


# --- who may approve a spend ----------------------------------------------------------------------------------------


@pytest.fixture
def web(tmp_path):
    services = world(tmp_path, [CUT])
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, tmp_path=tmp_path)
    services.runs.close()


def bearer(web) -> dict[str, str]:
    return {"Authorization": f"Bearer {web.token}"}


def test_the_unauthenticated_api_cannot_approve_a_budget_cut(web):
    run_id = begin(web.services)
    response = web.client.post(f"/api/v1/runs/{run_id}/approve")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_state" and detail["details"]["reason"] == "person_required"
    run = get(web.services.system, run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "budget_cut_approval"
    assert not events(run, EVENT_APPROVED) and web.services.system.ollama.prompts == []   # nothing was approved or spent
    assert not web.services.system.worker.process_once()


def test_the_unauthenticated_api_can_still_reject_a_budget_cut_because_that_spends_nothing(web):
    run_id = begin(web.services)
    response = web.client.post(f"/api/v1/runs/{run_id}/reject", json={"feedback": "no"})
    assert response.status_code == 200 and response.json()["status"] == RunStatus.FAILED.value
    assert len(events(get(web.services.system, run_id), EVENT_REJECTED)) == 1


def test_the_token_route_of_the_gui_approves_a_budget_cut(web):
    run_id = begin(web.services)
    path = f"/gui/api/runs/{run_id}/approve"
    assert web.client.post(path, json={}).status_code == 401                            # no token, no approval
    assert web.client.post(path, json={}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert not events(get(web.services.system, run_id), EVENT_APPROVED)
    response = web.client.post(path, json={}, headers=bearer(web))
    assert response.status_code == 200, response.text
    assert get(web.services.system, run_id)["status"] == RunStatus.QUEUED.value
    resume(web.services)
    run = get(web.services.system, run_id)
    (approval,) = events(run, EVENT_APPROVED)
    assert approval["channel"] == "gui" and web.services.system.ollama.models == ["local_default"]
    assert len(events(run, EVENT_RELEASED)) == 1


def test_the_command_line_approves_a_budget_cut_through_the_token_route(web, monkeypatch, capsys):
    run_id = begin(web.services)
    monkeypatch.setattr(cli, "api_client", lambda: contextlib.nullcontext(web.client))
    monkeypatch.setattr(cli, "get_settings", lambda: web.services.settings)
    cli.cmd_approve(SimpleNamespace(run_id=run_id))
    assert json.loads(capsys.readouterr().out)["status"] == RunStatus.QUEUED.value
    assert len(events(get(web.services.system, run_id), EVENT_APPROVED)) == 1


def test_the_command_line_without_a_token_asks_for_one_and_approves_nothing(web, monkeypatch):
    run_id = begin(web.services)
    monkeypatch.setattr(cli, "api_client", lambda: contextlib.nullcontext(web.client))
    monkeypatch.setattr(cli, "get_settings", lambda: web.services.settings)
    web.services.settings.gui_token_file.unlink()
    with pytest.raises(RuntimeError, match="person_required"):
        cli.cmd_approve(SimpleNamespace(run_id=run_id))
    assert not events(get(web.services.system, run_id), EVENT_APPROVED)


def test_every_other_wait_is_approved_through_the_unauthenticated_route_as_before(web):
    other = world(web.tmp_path / "other", [{**CUT, "amount": 100}])
    run_id = begin(other)
    assert get(other.system, run_id)["waiting_reason"] == "review"
    approved_run = other.runs.approve(run_id, recorded_only=True)     # what /api/v1 does for a step under review
    assert approved_run["status"] != RunStatus.WAITING_HUMAN.value or approved_run["waiting_reason"] != "review"
    other.runs.close()


def test_the_service_refuses_an_unauthenticated_approval_of_a_cut_in_the_call_itself(web):
    run_id = begin(web.services)
    with pytest.raises(Exception) as caught:
        web.services.runs.approve(run_id, recorded_only=True)
    assert getattr(caught.value, "details", {}).get("reason") == "person_required"
    assert get(web.services.system, run_id)["waiting_reason"] == "budget_cut_approval"
