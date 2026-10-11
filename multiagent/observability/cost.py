# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..catalog import current_price

# The contract writes the unit as ``token``; older catalogs and tests write ``tokens``. Both mean
# "price per token" and are the only units a token count can be multiplied by.
TOKEN_UNITS = frozenset({"token", "tokens"})
USD = "USD"


@dataclass(frozen=True)
class CostEstimate:
    """Cost of one provider call in the currency of the price evidence, plus the evidence used."""

    amount: float
    currency: str
    basis: dict[str, Any]

    @property
    def estimated_cost_usd(self) -> float | None:
        """The amount for the ``estimated_cost_usd`` column: only when the evidence is in USD."""
        return self.amount if self.currency.upper() == USD else None


def _reported(value: Any) -> int | None:
    """A token count that was really reported; 0, negatives and non-integers mean "not reported"."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _side(price: dict[str, Any]) -> dict[str, Any]:
    return {
        "amount": price["amount"],
        "denominator": price["denominator"],
        "unit": str(price["unit"]),
        "source": str(price["source"]),
        "valid_from": str(price["valid_from"]),
        "valid_until": str(price["valid_until"]),
        "provenance": str(price["provenance"]),
    }


def estimate_call_cost(spec: Any, *, tokens_in: Any, tokens_out: Any,
                       at: datetime | None = None) -> CostEstimate | None:
    """Estimate one call: ``tokens_in x input price + tokens_out x output price``, or ``None``.

    ``None`` (unknown, never 0) when the model has no ``pricing``, the pricing is incomplete or not in
    force at ``at`` (the call time; now by default), the price unit is not per token, or either token
    count was not reported. A declared price of 0 with evidence (``local_no_charge``) is a real 0.
    """
    pricing = getattr(spec, "pricing", None)
    if pricing is None:
        return None
    tin, tout = _reported(tokens_in), _reported(tokens_out)
    if tin is None or tout is None:
        return None
    moment = at if at is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    price = current_price(pricing, now=moment)
    if price is None:
        return None
    (in_amount, currency, in_denominator, unit), (out_amount, _, out_denominator, _) = price
    if unit.casefold() not in TOKEN_UNITS:
        return None
    amount = tin * in_amount / in_denominator + tout * out_amount / out_denominator
    basis = {
        "model_id": str(getattr(spec, "id", "")),
        "currency": currency,
        "cost": amount,
        "tokens_in": tin,
        "tokens_out": tout,
        "priced_at": moment.isoformat(),
        "input": _side(pricing["input"]),
        "output": _side(pricing["output"]),
    }
    return CostEstimate(amount=amount, currency=currency, basis=basis)
