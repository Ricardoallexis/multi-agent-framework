# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
            "success": int(self.success),
            "error_type": self.error_type,
        }


class UsageRecorder:
    """Persist provider usage without making telemetry part of execution."""

    def __init__(self, store):
        self.store = store

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
        return True


_RECORD_FIELDS = (
    "id", "step_id", "agent_id", "attempt", "provider", "model", "model_digest",
    "prompt_id", "prompt_version", "tokens_in", "tokens_out", "num_ctx",
    "duration_ms", "estimated_cost_usd", "success", "error_type", "created_at",
)


def read_usage(store, run_id: str) -> list[dict[str, Any]]:
    """Usage records of one run, oldest first.

    ``tokens_in``/``tokens_out`` stay ``None`` when the provider did not report
    them; ``tokens_total`` is ``None`` unless both are reported.
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
        reported = record["tokens_in"] is not None and record["tokens_out"] is not None
        record["tokens_total"] = record["tokens_in"] + record["tokens_out"] if reported else None
        records.append(record)
    return records


def _sum(values: list[Any]) -> Any:
    """Sum of the reported values; ``None`` when none was reported (never a fake 0)."""
    reported = [value for value in values if value is not None]
    return sum(reported) if reported else None


def _totals(records: list[dict[str, Any]]) -> dict[str, Any]:
    cost = _sum([record["estimated_cost_usd"] for record in records])
    return {
        "records": len(records),
        "failed": sum(1 for record in records if not record["success"]),
        "records_without_usage": sum(1 for record in records if record["tokens_total"] is None),
        "tokens_in": _sum([record["tokens_in"] for record in records]),
        "tokens_out": _sum([record["tokens_out"] for record in records]),
        "tokens_total": _sum([record["tokens_total"] for record in records]),
        "duration_ms": _sum([record["duration_ms"] for record in records]),
        "estimated_cost_usd": round(cost, 6) if cost is not None else None,
    }


def _group(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record[key], []).append(record)
    return [{key: name, **_totals(group)} for name, group in groups.items()]


def summarize_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals for the whole run, per agent and per step (in order of first use).

    Token sums include only reported values; ``records_without_usage`` counts
    the records that did not report usage (for example human steps).
    """
    return {
        "run": _totals(records),
        "by_agent": _group(records, "agent_id"),
        "by_step": _group(records, "step_id"),
    }
