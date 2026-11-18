# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Portable, run-scoped reports. The default schema admits only named metadata.

Event payloads, prompts, artifacts and error messages are untrusted and may contain
private input. They are included only with the explicit ``include_raw`` option.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Mapping


_TOOL_EVENTS = {
    "tool_call_requested", "tool_permission_requested", "tool_call_denied",
    "tool_call_completed", "tool_call_failed",
}
_REVIEW_EVENTS = ("checkpoint", "human_approved", "human_revision", "human_rejected")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BLOB_REF = re.compile(r"blob:sha256:[0-9a-f]{64}\Z")

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
    "tool_call_requested": ("request_id", "tool_id", "tool_version", "spec_sha256", "scope",
                            "run_id", "step_id", "attempt", "ordinal", "model_call_ordinal",
                            "origin", "arguments_sha256", "arguments_bytes", "impacts", "blob_refs"),
    "tool_permission_requested": ("request_id", "tool_id", "tool_version", "spec_sha256", "scope",
                                  "run_id", "step_id", "attempt", "ordinal", "model_call_ordinal",
                                  "origin", "permission_request_id"),
    "tool_call_denied": ("request_id", "tool_id", "tool_version", "spec_sha256", "scope",
                         "run_id", "step_id", "attempt", "ordinal", "model_call_ordinal",
                         "origin", "code", "permission_request_id"),
    "tool_call_completed": ("request_id", "tool_id", "tool_version", "spec_sha256", "scope",
                            "run_id", "step_id", "attempt", "ordinal", "model_call_ordinal",
                            "origin", "output_sha256", "output_bytes", "duration_ms", "blob_refs"),
    "tool_call_failed": ("request_id", "tool_id", "tool_version", "spec_sha256", "scope",
                         "run_id", "step_id", "attempt", "ordinal", "model_call_ordinal",
                         "origin", "code", "retryable", "duration_ms", "tool_message_sha256"),
}
_GROUPS = {
    "step_started": "steps", "step_completed": "steps", "step_skipped": "steps",
    "model_attempt": "attempts", "routing_decision": "routing_decisions",
    "call_paused": "human_waits", "human_step_requested": "human_waits",
    "human_step_submitted": "human_waits", "workflow_failed": "errors",
    "human_rejected": "errors", "budget_exhausted": "errors",
    **{kind: "tools" for kind in _TOOL_EVENTS},
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


def _safe_tool_fields(payload: Any, names: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, Any] = {}
    for name in names:
        value = payload.get(name)
        if name.endswith("_sha256"):
            if isinstance(value, str) and _SHA256.fullmatch(value):
                result[name] = value
        elif name == "blob_refs" and isinstance(value, Mapping):
            refs = {key: ref for key, ref in value.items()
                    if key in {"arguments", "output", "tool_message"}
                    and isinstance(ref, str) and _BLOB_REF.fullmatch(ref)}
            if refs:
                result[name] = refs
        elif name == "origin" and isinstance(value, Mapping):
            kind, ref = value.get("kind"), value.get("ref")
            if (isinstance(kind, str) and 0 < len(kind) <= 80
                    and (ref is None or isinstance(ref, str) and len(ref) <= 200)):
                result[name] = {"kind": kind, "ref": ref}
        elif name == "impacts" and isinstance(value, (tuple, list)):
            result[name] = [item for item in value if isinstance(item, str) and len(item) <= 40][:16]
        elif name in {"tool_version", "attempt", "ordinal", "model_call_ordinal",
                      "arguments_bytes", "output_bytes", "duration_ms"}:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                result[name] = value
        elif name == "retryable":
            if isinstance(value, bool):
                result[name] = value
        elif isinstance(value, str) and len(value) <= 200:
            result[name] = value
    if "permission_request_id" not in result:
        permission = payload.get("permission_request")
        if isinstance(permission, Mapping):
            permission_id = permission.get("permission_request_id")
            if isinstance(permission_id, str) and len(permission_id) <= 80:
                result["permission_request_id"] = permission_id
    return result


def _tool_raw_extras(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    permission = payload.get("permission_request")
    if not isinstance(permission, Mapping):
        return {}
    extras: dict[str, Any] = {}
    summary = permission.get("summary")
    if isinstance(summary, str) and len(summary) <= 300:
        extras["summary"] = summary
    actions = permission.get("actions")
    if isinstance(actions, (tuple, list)):
        destinations = [action["target"] for action in actions[:32]
                        if isinstance(action, Mapping) and isinstance(action.get("target"), str)
                        and len(action["target"]) <= 200]
        if destinations:
            extras["destinations"] = destinations
    return extras


def _safe_raw_run(run: Mapping[str, Any], *, include_raw: bool) -> dict[str, Any]:
    safe_run = dict(run)
    events = run.get("events")
    if isinstance(events, (tuple, list)):
        safe_events = []
        for event in events:
            if not isinstance(event, Mapping) or not (
                    isinstance(event.get("event_type"), str)
                    and event.get("event_type", "").startswith("tool_")):
                safe_events.append(event)
                continue
            kind, payload = event.get("event_type"), event.get("payload")
            names = _EVENT_FIELDS.get(kind, ())
            safe_event = dict(event)
            safe_event["payload"] = _safe_tool_fields(payload, names)
            if include_raw:
                safe_event["payload"].update(_tool_raw_extras(payload))
            safe_events.append(safe_event)
        safe_run["events"] = safe_events
    return safe_run


def _review_actor(payload: Any, *, kind: str) -> str:
    if isinstance(payload, Mapping):
        for key in ("by", "actor"):
            value = payload.get(key)
            if isinstance(value, str) and 0 < len(value.strip()) <= 200:
                return value.strip()
    return "system" if kind == "checkpoint" else "operator"


def build_run_report(run: Mapping[str, Any], usage: Mapping[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Summarize one run snapshot and its usage; never load another run."""
    report: dict[str, Any] = {
        "schema": "multiagent.run_report/1", "run_id": run["id"], "bundle": run.get("bundle"),
        "workflow_id": run.get("workflow_id"), "status": run.get("status"),
        "created_at": run.get("created_at"), "started_at": run.get("started_at"),
        "updated_at": run.get("updated_at"), "waiting_reason": run.get("waiting_reason"),
        "steps": [], "attempts": [], "routing_decisions": [], "human_waits": [], "errors": [], "tools": [],
        "reviews": [],
        "usage": {"cost_is_estimate": usage.get("cost_is_estimate", True),
                  "totals": usage.get("totals"), "records": []},
    }
    open_checkpoint: tuple[str | None, int | None] | None = None
    for event in run.get("events", []):
        if not isinstance(event, Mapping):
            continue
        kind = event.get("event_type")
        if not isinstance(kind, str):
            continue
        payload = event.get("payload") or {}
        if kind in _REVIEW_EVENTS:
            raw_step = payload.get("step_id") if isinstance(payload, Mapping) else None
            raw_attempt = payload.get("attempt") if isinstance(payload, Mapping) else None
            step_id = raw_step if isinstance(raw_step, str) else None
            attempt = raw_attempt if isinstance(raw_attempt, int) and not isinstance(raw_attempt, bool) else None
            if kind == "checkpoint":
                open_checkpoint = (step_id, attempt)
            else:
                if step_id is None and open_checkpoint is not None:
                    step_id = open_checkpoint[0]
                if attempt is None and open_checkpoint is not None:
                    attempt = open_checkpoint[1]
                open_checkpoint = None
            review_row: dict[str, Any] = {
                "seq": event.get("seq"),
                "timestamp": event.get("ts"),
                "event_type": kind,
                "step_id": step_id,
                "attempt": attempt,
                "actor": _review_actor(payload, kind=kind),
            }
            if kind == "human_revision" and isinstance(payload, Mapping):
                mode = payload.get("mode")
                if mode in ("revise", "regenerate"):
                    review_row["mode"] = mode
            if include_raw:
                review_row["raw_payload"] = payload
            report["reviews"].append(review_row)
        group = _GROUPS.get(kind)
        if not group:
            continue
        if kind in _TOOL_EVENTS and (
                not isinstance(payload, Mapping) or payload.get("schema") != "multiagent.tool_event/1"):
            continue
        row = {"seq": event.get("seq"), "timestamp": event.get("ts"), "event_type": kind,
               **(_safe_tool_fields(payload, _EVENT_FIELDS[kind]) if kind in _TOOL_EVENTS
                  else _safe_fields(payload, _EVENT_FIELDS[kind]))}
        if include_raw:
            if kind in _TOOL_EVENTS:
                row.update(_tool_raw_extras(payload))
            else:
                row["raw_payload"] = payload
        report[group].append(row)
    for record in usage.get("records", []):
        row = _safe_fields(record, _USAGE_FIELDS)
        if include_raw:
            row["raw_record"] = record
        report["usage"]["records"].append(row)
    if include_raw:
        report["raw_run"] = _safe_raw_run(run, include_raw=True)
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
                       ("errors", "Errors"), ("tools", "Tools"), ("reviews", "Reviews")):
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
