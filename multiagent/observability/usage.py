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
