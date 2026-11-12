# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Portable, run-scoped reports. The default schema admits only named metadata.

Event payloads, prompts, artifacts and error messages are untrusted and may contain
private input. They are included only with the explicit ``include_raw`` option.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any, Mapping


_EVENT_FIELDS = {
    "step_started": ("step_id", "attempt", "agent_id", "execution_mode"),
    "step_completed": ("step_id", "attempt"),
    "step_skipped": ("step_id",),
    "model_attempt": ("step_id", "attempt", "model", "attempted_model", "requested_model",
                      "effective_model", "observed_model", "provider", "retry", "ordinal",
                      "stage", "status", "provider_invoked", "ok", "duration_ms", "error_type"),
    "routing_decision": ("step_id", "attempt", "status", "requested_model", "selected_model",
                         "planned_model", "effective_model", "observed_model", "profile_id",
                         "data_class", "reason_codes", "manual_required"),
    "call_paused": ("step_id", "attempt", "guard", "waiting_reason", "model_id", "ordinal"),
    "human_step_requested": ("step_id", "attempt"),
    "human_step_submitted": ("step_id", "attempt", "provider"),
    "workflow_failed": ("type",),
    "human_rejected": (),
    "budget_exhausted": (),
}
_GROUPS = {
    "step_started": "steps", "step_completed": "steps", "step_skipped": "steps",
    "model_attempt": "attempts", "routing_decision": "routing_decisions",
    "call_paused": "human_waits", "human_step_requested": "human_waits",
    "human_step_submitted": "human_waits", "workflow_failed": "errors",
    "human_rejected": "errors", "budget_exhausted": "errors",
}
_USAGE_FIELDS = ("step_id", "agent_id", "attempt", "provider", "model", "tokens_in", "tokens_out",
                 "duration_ms", "estimated_cost_usd", "success", "error_type")


def _safe_fields(payload: Any, names: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    result = {}
    for name in names:
        value = payload.get(name)
        if value is None or isinstance(value, (bool, int, float)):
            result[name] = value
        elif name == "reason_codes" and isinstance(value, (tuple, list)):
            result[name] = [str(item) for item in value if isinstance(item, str)]
        elif isinstance(value, str):
            result[name] = value
    return result


def build_run_report(run: Mapping[str, Any], usage: Mapping[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Summarize one run snapshot and its usage; never load another run."""
    report: dict[str, Any] = {
        "schema": "multiagent.run_report/1", "run_id": run["id"], "bundle": run.get("bundle"),
        "workflow_id": run.get("workflow_id"), "status": run.get("status"),
        "created_at": run.get("created_at"), "started_at": run.get("started_at"),
        "updated_at": run.get("updated_at"), "waiting_reason": run.get("waiting_reason"),
        "steps": [], "attempts": [], "routing_decisions": [], "human_waits": [], "errors": [],
        "usage": {"cost_is_estimate": usage.get("cost_is_estimate", True),
                  "totals": usage.get("totals"), "records": []},
    }
    for event in run.get("events", []):
        kind = event.get("event_type")
        group = _GROUPS.get(kind)
        if not group:
            continue
        payload = event.get("payload") or {}
        row = {"seq": event.get("seq"), "timestamp": event.get("ts"), "event_type": kind,
               **_safe_fields(payload, _EVENT_FIELDS[kind])}
        if include_raw:
            row["raw_payload"] = payload
        report[group].append(row)
    for record in usage.get("records", []):
        row = _safe_fields(record, _USAGE_FIELDS)
        if include_raw:
            row["raw_record"] = record
        report["usage"]["records"].append(row)
    if include_raw:
        report["raw_run"] = dict(run)
    return report


def render_run_report_markdown(report: Mapping[str, Any]) -> str:
    """Render the same report as Markdown without interpreting payload as markup."""
    def cell(value: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")

    lines = [f"# Run report {cell(report['run_id'])}", "", f"Status: {cell(report.get('status'))}",
             f"Workflow: {cell(report.get('workflow_id'))}", ""]
    for key, title in (("steps", "Steps"), ("attempts", "Attempts"),
                       ("routing_decisions", "Routing decisions"), ("human_waits", "Human waits"),
                       ("errors", "Errors")):
        rows = report.get(key, [])
        lines.extend([f"## {title} ({len(rows)})", ""])
        if rows:
            columns = list(OrderedDict.fromkeys(name for row in rows for name in row))
            lines.extend(["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"])
            lines.extend("| " + " | ".join(cell(row.get(name)) for name in columns) + " |" for row in rows)
            lines.append("")
    lines.extend(["## Usage", "", "```json", json.dumps(report.get("usage"), ensure_ascii=False,
                                                     indent=2, default=str), "```", ""])
    if "raw_run" in report:
        lines.extend(["## Raw run", "", "```json", json.dumps(report["raw_run"], ensure_ascii=False,
                                                            indent=2, default=str), "```", ""])
    return "\n".join(lines)
