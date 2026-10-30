# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The budget guard of the call chain (USO-T04, contract CMP-T01 section 4: ``budget``, order 300).

USO-T03 left the budgets as numbers (``UsageServices.budgets()``) and one writer (``notify_budgets()``) that nobody
called. This module is the part that acts on them, as one more guard of ``call_guards`` (it edits neither the engine's
loop nor the router):

* **Alerts.** Before every model call the guard calls ``notify_budgets(run_id)``, so each alert percent reached by the
  calls already made is announced as a ``budget.threshold`` event (idempotent); ``UsageServices.attach`` also calls it
  after every recorded call, so the last call of a run is announced too. In ``warn`` mode that is all it does, and a
  failure to read the budgets never cuts a call.
* **Cut.** A budget in ``cut`` mode holds a paid call that would pass it. The guard prices the call with the price
  evidence of the model (``observability.cost.estimate_call_cost``: the exact input tokens of the prompt about to be
  sent, plus the most output tokens the model can return, ``ModelSpec.max_output_tokens``, which is a real bound) and
  compares ``used + reserved + estimate`` with the amount, in exact decimals: nothing is rounded to decide and no
  tolerance hides an excess (the rounding of ``used`` is only for display). If it passes, the guard answers
  ``Pause(WaitingReason.BUDGET_CUT)``: the step waits as ``WAITING_HUMAN``, exactly like a supervised escalation (ECO-T06).
* **Unknown price is a risk.** No price evidence in force, a price in another currency than the budget, a missing token
  estimate, or a paid output that has no declared maximum means the cost cannot be bounded, so the call is held the
  same way. A price that is declared and is really zero (``local_no_charge``) costs nothing and goes through. Nothing
  here ever lets an unpriced call skip. The router cannot yet ask a provider for fewer output tokens, so the bound is
  the model's own declared maximum, never an assumed number.
* **Two calls, one budget.** The decision and the booking are one write transaction (``UsageServices.reserving``,
  ``BEGIN IMMEDIATE``): a call that passes reserves its estimate as a ``budget.reserved`` team event, and the next call
  sees ``used + reserved``. The reservation ends when the call's real usage is recorded (the same hook that announces
  the alerts, also for a failed call), or after ``RESERVATION_TTL`` if nothing ever records it (a crash).
* **Never approves for the person.** Only the approval path of runs settles the wait (``RunService.approve`` /
  ``reject`` -> ``Store.finish_budget_cut``; ``/api/v1`` has no token, so it cannot approve a spend, see
  ``RunService.approve``). Approving records ``budget_cut_approved`` with a fresh ``approval_id``, the fingerprints of
  the breaches it covers (budget, run, period, currency, amount, reason) and the estimated amount the person saw.
  **An approval is for one call**: the call of that step attempt to that model at that position of the router (its
  ``ordinal`` and ``retry``). The resumed step finds it, records ``budget_cut_released`` and the approval is spent.
  Another call (a retry, the next call of the step, a fallback model), another model, step or attempt, another
  amount, period or estimate is held again. A pause makes the step start its router call over, so the first calls are made
  again and each one that costs money needs its own approval; an approval for a later position stays valid until the
  step reaches it. Without a way to record the release (no ``emit``) the guard never uses an approval. Rejecting fails the
  run (``budget_cut_rejected``).

Fail closed: with a budget in cut mode, a guard that cannot read the budgets, cannot estimate the call or cannot
record a release does not let the call happen (the guard is not an observer). Off by default: with no budget
configured ``policy_from_settings`` returns ``None``, ``build_call_guards`` registers nothing and no call is read,
estimated, reserved or paused.

Pure, apart from what it asks of its ``reader`` (``notify_budgets``, ``reserving``) and of ``emit``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from .call_guards import ORDER_BUDGET, PROCEED, CallContext, Pause, RunState, Verdict
from .contracts import WaitingReason

__all__ = ["BudgetGuard", "BudgetHold", "BudgetPolicy", "BudgetReader", "BudgetUnavailable", "CAUSE_NO_PRICE",
           "CAUSE_OUTPUT_UNBOUNDED", "EVENT_APPROVED", "EVENT_REJECTED", "EVENT_RELEASED", "GUARD_NAME", "MODE_CUT",
           "REASON_CURRENCY_NOT_PRICED", "REASON_OVER_BUDGET", "REASON_PRICE_UNKNOWN", "SCHEMA", "approval_from_pause",
           "granted_approvals", "is_budget_pause", "policy_from_settings", "rejection_message", "reservation_key"]

_logger = logging.getLogger(__name__)

SCHEMA = "multiagent.budget_cut/1"
GUARD_NAME = "budget"
EVENT_APPROVED = "budget_cut_approved"      # a person let one held call through (written by ``Store.finish_budget_cut``)
EVENT_RELEASED = "budget_cut_released"      # the guard spent an approval to let one call through (an audit record)
EVENT_REJECTED = "budget_cut_rejected"      # a person refused: the run fails
MODE_CUT = "cut"
REASON_OVER_BUDGET = "over_budget"                     # used + reserved + the estimate of this call passes the amount
REASON_PRICE_UNKNOWN = "price_unknown"                 # no usable price evidence (or tokens) to bound the cost of the call
REASON_CURRENCY_NOT_PRICED = "currency_not_priced"     # the price is in another currency than the budget
# Why a price is unknown (``unknown_cause`` of the estimate and of the breach; the reason stays ``price_unknown``).
CAUSE_NO_PRICE = "no_price_evidence"          # no pricing, expired, not yet in force, or not per token
CAUSE_NO_TOKENS = "input_tokens_unknown"      # the prompt was not estimated
CAUSE_OUTPUT_UNBOUNDED = "output_unbounded"   # a paid output and the model declares no ``max_output_tokens``
CAUSE_ESTIMATOR_FAILED = "estimator_failed"
CAUSE_ROW_UNREADABLE = "budget_unreadable"    # the budget row has no readable amount or consumption
OUTPUT_BOUND_SOURCE = "catalog_max_output_tokens"


class BudgetUnavailable(RuntimeError):
    """A budget is in cut mode but nothing can read the budgets, so the call must not happen."""


@dataclass
class BudgetHold:
    """What the guard sees inside the write transaction of ``reserving``.

    ``status`` is ``budgets()`` read under the lock, ``reserved`` the outstanding reservations by ``reservation_key``
    and ``reserve(payload)`` books one (it returns the reservation id and commits with the transaction).
    """

    status: Mapping[str, Any]
    reserved: Mapping[str, Decimal]
    reserve: Callable[[Mapping[str, Any]], str]


class BudgetReader(Protocol):
    """What the guard asks of the usage service (``UsageServices``).

    ``notify_budgets`` returns ``budgets()`` plus ``emitted``. ``reserving`` (optional) is the exclusive section that
    makes the decision and the booking one write transaction, and ``settle`` (optional) ends a reservation by hand.
    A reader without ``reserving`` is compared without reservations.
    """

    def notify_budgets(self, *, run_id: Any = None) -> Mapping[str, Any]:
        ...

    def reserving(self, *, run_id: Any = None) -> AbstractContextManager[BudgetHold]:
        ...

    def settle(self, reservation_id: str) -> bool:
        ...


@dataclass(frozen=True)
class BudgetPolicy:
    """How the guard of one chain reads the budgets.

    ``reader`` is the usage service (``None``: the engine has none attached). ``cut`` says that some configured budget is
    in cut mode: then a failure to read is fatal for the call (fail closed); otherwise it is logged and the call goes on.
    """

    reader: BudgetReader | None = None
    cut: bool = False


def policy_from_settings(settings: Any, reader: BudgetReader | None = None) -> BudgetPolicy | None:
    """The policy for the configured budgets, or ``None`` when there is none (no guard, nothing changes)."""
    budgets = getattr(settings, "usage_budgets", None)
    if not budgets:
        return None
    default = getattr(settings, "usage_budget_mode", "warn")
    cut = any((getattr(budget, "mode", None) or default) == MODE_CUT for budget in budgets)
    return BudgetPolicy(reader=reader, cut=cut)


# ---------------------------------------------------------------------------------------------------------
# Exact money
# ---------------------------------------------------------------------------------------------------------

def _dec(value: Any) -> Decimal | None:
    """A number as the decimal it is written as (``0.1`` is 0.1, not its binary neighbour); ``None`` when it is not one."""
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _plain(value: Decimal) -> str:
    """A decimal as plain text without exponent or trailing zeros (``10``, ``5.096``)."""
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def reservation_key(row: Mapping[str, Any]) -> str:
    """What a reservation is booked against: the budget, the run of a run budget and the currency."""
    return f"{row.get('budget_id')}|{row.get('run_id') or ''}|{str(row.get('currency')).upper()}"


@dataclass(frozen=True)
class _Estimate:
    """The cost bound of one call: exact when ``cost`` is known, else ``cause`` says why it is unknown (never 0)."""

    cost: Any | None                  # observability.cost.CostEstimate, or None when unknown
    exact: Decimal | None
    cause: str | None
    output_tokens: int | None         # the bound the price was computed with (None: unknown, or the output is free)
    input_tokens: int

    @property
    def known(self) -> bool:
        return self.exact is not None

    @property
    def text(self) -> str | None:
        return _plain(self.exact) if self.exact is not None else None


def _exact_cost(cost: Any) -> Decimal:
    """The cost of an estimate recomputed in decimals from its own evidence (the float of ``amount`` carries noise)."""
    basis = cost.basis if isinstance(getattr(cost, "basis", None), Mapping) else {}
    try:
        tokens_in, tokens_out = Decimal(int(basis["tokens_in"])), Decimal(int(basis["tokens_out"]))
        paid_in, paid_out = basis["input"], basis["output"]
        return (tokens_in * _dec(paid_in["amount"]) / Decimal(int(paid_in["denominator"]))
                + tokens_out * _dec(paid_out["amount"]) / Decimal(int(paid_out["denominator"])))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return _dec(cost.amount) or Decimal(0)


# ---------------------------------------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------------------------------------

class BudgetGuard:
    """Announces the alerts reached and holds a paid call that would pass a budget in cut mode (see the module docstring)."""

    name = GUARD_NAME
    order = ORDER_BUDGET
    observer = False        # fails closed when a budget is in cut mode
    skippable = False       # the summarizer's calls are paid too: they cannot leave it out

    def __init__(self, policy: BudgetPolicy, *, emit: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.policy = policy
        self.emit = emit

    # -- the check ---------------------------------------------------------------------------------------

    def check(self, call: CallContext, state: RunState) -> Verdict:
        run_id = call.run_id or (state.get("id") if isinstance(state.get("id"), str) else None)
        status = self._read(run_id)
        if status is None:
            return PROCEED
        if not self._limiting(status, call, state):
            return PROCEED
        estimate = self._estimate(call)
        if estimate.known and estimate.exact == 0:
            return PROCEED                                       # a declared zero price: it costs nothing
        reader = self.policy.reader
        scope = getattr(reader, "reserving", None) if estimate.known else None
        context = scope(run_id=run_id) if scope is not None else nullcontext(BudgetHold(status, {}, lambda payload: ""))
        reservation = ""
        with context as hold:
            rows = self._limiting(hold.status, call, state)
            breaches = self._breaches(rows, estimate, hold.reserved)
            approval = self._approval(call, state, estimate, breaches) if breaches else None
            if breaches and approval is None:
                return Pause(WaitingReason.BUDGET_CUT, request=self._request(call, estimate, breaches),
                             message=self._message(call, breaches))
            booked = self._booking(call, estimate, rows) if estimate.known else None
            if booked is not None:
                reservation = hold.reserve(booked)
        if approval is not None:
            self._spend(approval, call, estimate, breaches, reservation)
        return PROCEED

    def _read(self, run_id: str | None) -> Mapping[str, Any] | None:
        """``notify_budgets`` for the run: the alerts reached are announced and the answer is the budgets' state.

        ``None`` when it could not be read and no budget is in cut mode (an alert never costs a call). In cut mode
        the failure propagates, so the call does not happen.
        """
        try:
            if self.policy.reader is None:
                raise BudgetUnavailable("A budget is configured but no usage service is attached to read it")
            status = self.policy.reader.notify_budgets(run_id=run_id)
            if not isinstance(status, Mapping) or not isinstance(status.get("status"), list):
                raise BudgetUnavailable("The usage service answered something that is not the state of the budgets")
            return status
        except Exception:
            if self.policy.cut:
                raise
            _logger.warning("The budgets could not be read before a call; no budget is in cut mode, so it goes on",
                            exc_info=True)
            return None

    @classmethod
    def _limiting(cls, status: Mapping[str, Any], call: CallContext, state: RunState) -> list[Mapping[str, Any]]:
        """The rows in cut mode that limit this call."""
        rows = status.get("status")
        if not isinstance(rows, list):
            raise BudgetUnavailable("The usage service answered something that is not the state of the budgets")
        return [row for row in rows if isinstance(row, Mapping) and row.get("mode") == MODE_CUT
                and cls._applies(row, call, state)]

    @staticmethod
    def _applies(row: Mapping[str, Any], call: CallContext, state: RunState) -> bool:
        """Whether a status row limits this call: a run budget limits its own run, a project one the runs of that project.

        A run whose project is unknown is limited by every project budget (fail closed).
        """
        if row.get("scope") == "run":
            return call.run_id is not None and row.get("run_id") == call.run_id
        project = row.get("project")
        if project is None:
            return True
        request = state.get("request")
        names = {value for value in (state.get("project_id"),
                                     request.get("project_name") if isinstance(request, Mapping) else None)
                 if isinstance(value, str) and value}
        return not names or project in names

    # -- the cost bound ---------------------------------------------------------------------------------

    @staticmethod
    def _estimate(call: CallContext) -> _Estimate:
        """The cost bound of the call: the prompt about to be sent plus the most the model can answer, in exact decimals.

        The bound on the answer is ``ModelSpec.max_output_tokens`` (the provider's documented maximum). Without it the
        cost is unbounded unless the output is declared free (a price of 0 for output tokens); it is then unknown, not an
        assumed number: the router cannot yet ask the provider to stop earlier.
        """
        from .observability.cost import estimate_call_cost    # imported here: ``store`` imports this module
        tokens_in = call.estimated_input_tokens
        declared = getattr(call.spec, "max_output_tokens", None)
        bound = declared if type(declared) is int and declared > 0 else None
        try:
            cost = estimate_call_cost(call.spec, tokens_in=tokens_in, tokens_out=bound if bound is not None else 1)
        except Exception:
            _logger.warning("The cost of a call to %r could not be estimated; it counts as unknown", call.model_id,
                            exc_info=True)
            return _Estimate(None, None, CAUSE_ESTIMATOR_FAILED, bound, tokens_in)
        if cost is None:
            cause = CAUSE_NO_TOKENS if type(tokens_in) is not int or tokens_in <= 0 else CAUSE_NO_PRICE
            return _Estimate(None, None, cause, bound, tokens_in)
        exact = _exact_cost(cost)
        if bound is None:
            paid = _dec((cost.basis.get("output") or {}).get("amount")) if isinstance(cost.basis, Mapping) else None
            if paid is None or paid != 0:
                return _Estimate(cost, None, CAUSE_OUTPUT_UNBOUNDED, None, tokens_in)
        return _Estimate(cost, exact, None, bound, tokens_in)

    def _breaches(self, rows: Iterable[Mapping[str, Any]], estimate: _Estimate,
                  reserved: Mapping[str, Decimal]) -> list[dict[str, Any]]:
        breaches: list[dict[str, Any]] = []
        for row in rows:
            amount = _dec(row.get("amount"))
            used = _dec(row.get("used_exact")) if row.get("used_exact") is not None else _dec(row.get("used"))
            held = reserved.get(reservation_key(row), Decimal(0))
            projected: Decimal | None = None
            cause: str | None = None
            if not estimate.known:
                reason, cause = REASON_PRICE_UNKNOWN, estimate.cause
            elif str(estimate.cost.currency).upper() != str(row.get("currency")).upper():
                reason = REASON_CURRENCY_NOT_PRICED
            elif amount is None or used is None:
                reason, cause = REASON_PRICE_UNKNOWN, CAUSE_ROW_UNREADABLE    # a row we cannot read is a risk, not a pass
            else:
                projected = used + held + estimate.exact
                if projected <= amount:
                    continue
                reason = REASON_OVER_BUDGET
            run_part = row.get("run_id") or ""
            key = "|".join((str(row.get("budget_id")), run_part, str(row.get("period")), str(row.get("currency")).upper(),
                            _plain(amount) if amount is not None else "?", reason))
            breaches.append({
                "key": key, "budget_id": row.get("budget_id"),
                "scope": row.get("scope"), "run_id": row.get("run_id"), "project": row.get("project"),
                "period": row.get("period"), "currency": row.get("currency"),
                "amount": float(amount) if amount is not None else None, "used": float(used) if used is not None else None,
                "reserved": float(held), "remaining": _float(row.get("remaining")), "percent": _float(row.get("percent")),
                "complete": bool(row.get("complete")), "reason": reason, "unknown_cause": cause,
                "projected": round(float(projected), 6) if projected is not None else None})
        return breaches

    # -- booking and approvals --------------------------------------------------------------------------

    @staticmethod
    def _booking(call: CallContext, estimate: _Estimate, rows: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
        """The reservation of a call that goes ahead: its estimate against every budget it was compared with."""
        currency = str(estimate.cost.currency).upper()
        keys = sorted({reservation_key(row) for row in rows if str(row.get("currency")).upper() == currency
                       and _dec(row.get("amount")) is not None})
        if not keys:
            return None
        return {"run_id": call.run_id, "step_id": call.step_id, "attempt": call.attempt, "model_id": call.model_id,
                "ordinal": call.ordinal, "retry": call.retry, "currency": currency, "amount": estimate.text, "budgets": keys}

    def _approval(self, call: CallContext, state: RunState, estimate: _Estimate,
                  breaches: list[dict[str, Any]]) -> Mapping[str, Any] | None:
        """The oldest unspent approval for exactly this call: its step attempt and model, every breach fingerprint (budget,
        run, period, currency, amount, reason) and the estimated amount the person saw. ``None`` when there is none."""
        if self.emit is None:       # no way to record the release: never use an approval
            return None
        keys = {breach["key"] for breach in breaches}
        for item in granted_approvals(state.get("events") or (), call):
            if keys <= set(item.get("breaches") or ()) and _same_amount(item.get("estimate_amount"), estimate.exact):
                return item
        return None

    def _spend(self, approval: Mapping[str, Any], call: CallContext, estimate: _Estimate,
               breaches: list[dict[str, Any]], reservation: str) -> None:
        """Record that the approval was used (it is spent from now on). If that cannot be recorded the call does not happen
        and the reservation of the call is ended."""
        assert self.emit is not None
        try:
            self.emit(EVENT_RELEASED, {
                "schema": SCHEMA, "approval_id": approval["approval_id"], "step_id": call.step_id, "attempt": call.attempt,
                "model_id": call.model_id, "ordinal": call.ordinal, "retry": call.retry,
                "breaches": [breach["key"] for breach in breaches], "estimate_amount": estimate.text,
                "estimate": self._estimate_view(estimate)})
        except Exception:
            settle = getattr(self.policy.reader, "settle", None)
            if reservation and settle is not None:
                try:
                    settle(reservation)
                except Exception:
                    _logger.warning("The reservation of a call that did not happen could not be ended", exc_info=True)
            raise

    # -- what the person is shown --------------------------------------------------------------------------

    @staticmethod
    def _estimate_view(estimate: _Estimate) -> dict[str, Any]:
        cost = estimate.cost
        basis = cost.basis if cost is not None and isinstance(cost.basis, Mapping) else None
        evidence = {name: basis[name] for name in ("model_id", "currency", "input", "output", "priced_at")
                    if name in basis} if basis is not None else None
        return {"amount": float(estimate.exact) if estimate.exact is not None else None, "exact": estimate.text,
                "currency": cost.currency if cost is not None else None, "input_tokens": estimate.input_tokens,
                "output_tokens": estimate.output_tokens, "output_tokens_source": OUTPUT_BOUND_SOURCE
                if estimate.output_tokens is not None else None,
                "unknown_cause": estimate.cause, "price_evidence": evidence}

    def _request(self, call: CallContext, estimate: _Estimate, breaches: list[dict[str, Any]]) -> dict[str, Any]:
        return {"schema": SCHEMA, "step_id": call.step_id, "attempt": call.attempt, "model_id": call.model_id,
                "provider": getattr(call.spec, "provider", None), "ordinal": call.ordinal, "retry": call.retry,
                "estimate": self._estimate_view(estimate),
                "reasons": sorted({breach["reason"] for breach in breaches}), "breaches": breaches}

    @staticmethod
    def _message(call: CallContext, breaches: list[dict[str, Any]]) -> str:
        parts = []
        for breach in breaches:
            who = f"budget {breach['budget_id']!r}"
            if breach["reason"] == REASON_OVER_BUDGET:
                held = f", {breach['reserved']} reserved by calls in flight" if breach["reserved"] else ""
                parts.append(f"{who} would reach {breach['projected']} of {breach['amount']} {breach['currency']} "
                             f"({breach['used']} used{held})")
            elif breach["reason"] == REASON_CURRENCY_NOT_PRICED:
                parts.append(f"{who} is in {breach['currency']} and the price of the model is in another currency")
            elif breach["unknown_cause"] == CAUSE_OUTPUT_UNBOUNDED:
                parts.append(f"the cost of the call against {who} cannot be bounded (the model declares no "
                             f"max_output_tokens)")
            else:
                parts.append(f"the cost of the call against {who} cannot be bounded (no usable price evidence)")
        return (f"A person must approve the call to {call.model_id!r} (step {call.step_id!r}, attempt {call.attempt}) "
                f"before it is made: {'; '.join(parts)}. An approval lets exactly this one call go ahead.")


def _float(value: Any) -> float | None:
    number = _dec(value)
    return float(number) if number is not None else None


def _same_amount(approved: Any, estimated: Decimal | None) -> bool:
    """Whether the estimated amount the person approved is the one of this call (``None`` is an unknown cost)."""
    if approved is None or estimated is None:
        return approved is None and estimated is None
    seen = _dec(approved)
    return seen is not None and seen == estimated


# ---------------------------------------------------------------------------------------------------------
# The events that settle a wait (read by the guard, written by the approval path of runs)
# ---------------------------------------------------------------------------------------------------------

def granted_approvals(events: Iterable[Mapping[str, Any]], call: CallContext) -> list[Mapping[str, Any]]:
    """The ``budget_cut_approved`` payloads given for this very call that nobody has spent, oldest first.

    "This very call" is its step, attempt, model and position in the router (``ordinal`` and ``retry``). An approval needs a
    usable ``approval_id``. It is for one call: a ``budget_cut_released`` event with the same ``approval_id`` spends it for
    good.
    """
    identity_of_call = (call.step_id, call.attempt, call.model_id, call.ordinal, call.retry)
    granted: dict[str, Mapping[str, Any]] = {}
    spent: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping) or not isinstance(event.get("payload"), Mapping):
            continue
        payload = event["payload"]
        identity = payload.get("approval_id")
        if not isinstance(identity, str) or not identity:
            continue
        if event.get("event_type") == EVENT_RELEASED:
            spent.add(identity)
        elif (event.get("event_type") == EVENT_APPROVED
                and (payload.get("step_id"), payload.get("attempt"), payload.get("model_id"), payload.get("ordinal"),
                     payload.get("retry")) == identity_of_call):
            granted.setdefault(identity, payload)
    return [payload for identity, payload in granted.items() if identity not in spent]


def is_budget_pause(payload: Mapping[str, Any]) -> bool:
    """True for the ``call_paused`` event this guard caused."""
    return payload.get("guard") == GUARD_NAME and payload.get("waiting_reason") == WaitingReason.BUDGET_CUT.value


def approval_from_pause(paused: Mapping[str, Any], *, approval_id: str, by: str, channel: str) -> dict[str, Any]:
    """The payload of ``budget_cut_approved`` for a ``call_paused`` event: everything comes from what the guard recorded,
    nothing from the caller but who it says it is."""
    request = paused.get("request") if isinstance(paused.get("request"), Mapping) else {}
    breaches = request.get("breaches") if isinstance(request.get("breaches"), list) else []
    estimate = request.get("estimate") if isinstance(request.get("estimate"), Mapping) else {}
    return {"schema": SCHEMA, "approval_id": approval_id, "step_id": paused.get("step_id"),
            "attempt": paused.get("attempt"), "model_id": paused.get("model_id"), "ordinal": request.get("ordinal"),
            "retry": request.get("retry"),
            "breaches": [item["key"] for item in breaches if isinstance(item, Mapping) and isinstance(item.get("key"), str)],
            "reasons": list(request.get("reasons") or ()), "estimate_amount": estimate.get("exact"),
            "estimate": dict(estimate), "by": by, "channel": channel}


def rejection_message(paused: Mapping[str, Any], feedback: str = "") -> str:
    """Why the run failed when a person refused the held call."""
    request = paused.get("request") if isinstance(paused.get("request"), Mapping) else {}
    budgets = sorted({str(item.get("budget_id")) for item in request.get("breaches") or ()
                      if isinstance(item, Mapping)}) or ["(unknown)"]
    return (f"The call to {paused.get('model_id')!r} in step {paused.get('step_id')!r} was held by the budget cut "
            f"({', '.join(budgets)}) and rejected by a human"
            + (f": {feedback}" if feedback.strip() else "") + "; the step failed")
