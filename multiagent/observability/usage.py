# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenUsageRecord:
    run_id: str
    step_id: str
    agent_id: str
    attempt: int
    provider: str
    model: str
    model_digest: str
    prompt_id: str
    prompt_version: int
    prompt_sha256: str
    brand_version: int | None
    tokens_in: int | None
    tokens_out: int | None
    num_ctx: int | None
    duration_ms: int | None
    estimated_cost_usd: float | None
    success: bool
    error_type: str = ""
    # Evidence behind ``estimated_cost_usd`` (see ``observability.cost``); None when no cost was computed.
    cost_basis: dict[str, Any] | None = None
    # Provider-reported cached input tokens (CAP-T04); None = not reported. Already counted in tokens_in.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    def as_store_data(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "step_id": self.step_id,
            "agent_id": self.agent_id,
            "attempt": self.attempt,
            "provider": self.provider,
            "model": self.model,
            "model_digest": self.model_digest,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "brand_version": self.brand_version,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "num_ctx": self.num_ctx,
            "latency_ms": self.duration_ms,
            "duration_ms": self.duration_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cost_basis": (json.dumps(self.cost_basis, ensure_ascii=False, sort_keys=True)
                           if self.cost_basis else None),
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "success": int(self.success),
            "error_type": self.error_type,
        }


class UsageRecorder:
    """Persist provider usage without making telemetry part of execution."""

    def __init__(self, store):
        self.store = store
        # Called with the record after it is stored (USO-T04: ``UsageServices.attach`` announces the budget alerts that
        # call reached). Best effort: whatever it raises is logged and never touches the call or its telemetry.
        self.after_record: Callable[[TokenUsageRecord], None] | None = None

    def record(self, record: TokenUsageRecord) -> bool:
        try:
            self.store.record_telemetry(**record.as_store_data())
        except Exception as exc:
            try:
                self.store.db.log_event(
                    record.run_id,
                    "telemetry_failed",
                    {"step_id": record.step_id, "attempt": record.attempt, "error": str(exc)},
                )
            except Exception:
                pass
            return False
        if self.after_record is not None:
            try:
                self.after_record(record)
            except Exception:
                _logger.warning("The after-record hook failed; the telemetry was stored", exc_info=True)
        return True


_RECORD_FIELDS = (
    "id", "step_id", "agent_id", "attempt", "provider", "model", "model_digest",
    "prompt_id", "prompt_version", "tokens_in", "tokens_out", "num_ctx",
    "duration_ms", "estimated_cost_usd", "cost_basis", "success", "error_type", "created_at",
)


def _load_basis(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        basis = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return basis if isinstance(basis, dict) else None


def read_usage(store, run_id: str) -> list[dict[str, Any]]:
    """Usage records of one run, oldest first.

    ``tokens_in``/``tokens_out`` stay ``None`` when the provider did not report
    them; ``tokens_total`` is ``None`` unless both are reported.
    ``estimated_cost_usd`` is ``None`` when the cost is unknown or was priced in another currency;
    ``cost_basis`` is the evidence of a computed cost (a dict) or ``None``.
    """
    with store.db.connect() as conn:
        rows = conn.execute(
            f"SELECT {','.join(_RECORD_FIELDS)} FROM telemetry WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
    records = []
    for row in rows:
        record = {field: row[field] for field in _RECORD_FIELDS}
        record["success"] = bool(record["success"])
        record["cost_basis"] = _load_basis(record["cost_basis"])
        reported = record["tokens_in"] is not None and record["tokens_out"] is not None
        record["tokens_total"] = record["tokens_in"] + record["tokens_out"] if reported else None
        records.append(record)
    return records


def _sum(values: list[Any]) -> Any:
    """Sum of the reported values; ``None`` when none was reported (never a fake 0)."""
    reported = [value for value in values if value is not None]
    return sum(reported) if reported else None


def _cost_totals(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Cost of the records with the unknown ones kept apart; an unknown cost is never added as 0.

    * ``estimated_cost_usd``: sum of the USD costs that are known (``None`` when there is none), so
      it is only the whole cost when ``cost_complete`` is true.
    * ``cost_known_records`` / ``cost_unknown_records``: records with a USD cost / with no cost at all
      (no price evidence in force, no tokens reported, or a human step).
    * ``cost_other_currencies``: subtotal per currency for records priced in a currency other than USD.
    * ``cost_complete``: every record has a USD cost (and there is at least one record).
    """
    usd = [r["estimated_cost_usd"] for r in records if r["estimated_cost_usd"] is not None]
    other: dict[str, float] = {}
    unknown = 0
    for record in records:
        if record["estimated_cost_usd"] is not None:
            continue
        basis = record.get("cost_basis") or {}
        cost, currency = basis.get("cost"), basis.get("currency")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and isinstance(currency, str) and currency:
            other[currency] = other.get(currency, 0) + cost
        else:
            unknown += 1
    return {
        "estimated_cost_usd": round(sum(usd), 6) if usd else None,
        "cost_known_records": len(usd),
        "cost_unknown_records": unknown,
        "cost_other_currencies": {currency: round(total, 6) for currency, total in sorted(other.items())},
        "cost_complete": bool(records) and unknown == 0 and not other,
    }


def _totals(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "records": len(records),
        "failed": sum(1 for record in records if not record["success"]),
        "records_without_usage": sum(1 for record in records if record["tokens_total"] is None),
        "tokens_in": _sum([record["tokens_in"] for record in records]),
        "tokens_out": _sum([record["tokens_out"] for record in records]),
        "tokens_total": _sum([record["tokens_total"] for record in records]),
        "duration_ms": _sum([record["duration_ms"] for record in records]),
        **_cost_totals(records),
    }


def _group(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record[key], []).append(record)
    return [{key: name, **_totals(group)} for name, group in groups.items()]


def summarize_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals for the whole run, per agent, per step and per model (in order of first use).

    Token sums include only reported values; ``records_without_usage`` counts
    the records that did not report usage (for example human steps). Cost totals
    follow ``_cost_totals``: the known subtotal plus how many records are unknown.
    """
    return {
        "run": _totals(records),
        "by_agent": _group(records, "agent_id"),
        "by_step": _group(records, "step_id"),
        "by_model": _group(records, "model"),
    }


# Public names for the aggregators that group telemetry records their own way (``services.usage``).
load_basis = _load_basis
totals_of = _totals
