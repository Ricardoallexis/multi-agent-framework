# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Attach optional OpenTelemetry to an execution backend."""
from __future__ import annotations

from typing import Any

from ..contracts import RunStatus
from ..observability.otel import build_telemetry


def attach_telemetry(backend: Any, settings: Any, *, store: Any = None) -> Any:
    """Observe a backend's store, usage and queued runs when telemetry is enabled."""
    if getattr(backend, "telemetry", None) is not None:
        return backend
    telemetry = build_telemetry(settings)
    backend.telemetry = telemetry
    if telemetry is None:
        return backend

    store = store if store is not None else backend.store
    store.db.event_listeners.append(telemetry.on_event)
    backend.usage_recorder.observers.append(telemetry.on_usage)
    process_run = backend.process_run

    def traced_process_run(run_id: str) -> dict[str, Any]:
        run = store.get_run(run_id)
        if run["status"] != RunStatus.QUEUED.value:
            return process_run(run_id)
        telemetry.begin_run(run_id, str(run.get("workflow_id", "")))
        final: dict[str, Any] = {}
        try:
            final = process_run(run_id)
            return final
        finally:
            telemetry.end_run(run_id, str(final.get("status") or "unknown"))

    backend.process_run = traced_process_run
    return backend
