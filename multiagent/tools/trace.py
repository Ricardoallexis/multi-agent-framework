"""Safe projection and host adapters for tool-call trace events."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from multiagent.document_trust import BlobSink
from .invoker import ToolEventSink

_EVENT_TYPES = {
    "tool_call_requested",
    "tool_permission_requested",
    "tool_call_denied",
    "tool_call_completed",
    "tool_call_failed",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BLOB_REF = re.compile(r"blob:sha256:[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _origin(value: Any) -> dict[str, str | None] | None:
    if not isinstance(value, Mapping):
        return None
    kind, ref = value.get("kind"), value.get("ref")
    if not isinstance(kind, str) or not kind or len(kind) > 80:
        return None
    if ref is not None and (not isinstance(ref, str) or len(ref) > 200):
        return None
    return {"kind": kind, "ref": ref}


def _blob_refs(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: ref for key, ref in value.items()
        if key in {"arguments", "output", "tool_message"}
        and isinstance(ref, str) and _BLOB_REF.fullmatch(ref)
    }


def _event_parts(event: Any) -> tuple[str, Mapping[str, Any]] | None:
    if not isinstance(event, Mapping):
        return None
    kind, payload = event.get("event_type"), event.get("payload")
    if not isinstance(kind, str) or kind not in _EVENT_TYPES or not isinstance(payload, Mapping):
        return None
    if payload.get("schema") != "multiagent.tool_event/1":
        return None
    return kind, payload


def _identity(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    request_id, tool_id = payload.get("request_id"), payload.get("tool_id")
    version, attempt, ordinal = payload.get("tool_version"), payload.get("attempt"), payload.get("ordinal")
    model_ordinal = payload.get("model_call_ordinal")
    scope, origin = payload.get("scope"), _origin(payload.get("origin"))
    if (not isinstance(request_id, str) or not 8 <= len(request_id) <= 64
            or not _IDENTIFIER.fullmatch(request_id)
            or not isinstance(tool_id, str) or not tool_id or len(tool_id) > 120
            or (version is not None and not _integer(version, minimum=1))
            or not _integer(attempt, minimum=1) or not _integer(ordinal)
            or (model_ordinal is not None and not _integer(model_ordinal))
            or not isinstance(scope, str) or scope not in {"run_step", "task"} or origin is None):
        return None
    fields: dict[str, Any] = {
        "actor": {"kind": "tool", "id": tool_id, "version": version},
        "run_id": payload.get("run_id") if isinstance(payload.get("run_id"), str) else None,
        "step_id": payload.get("step_id") if isinstance(payload.get("step_id"), str) else None,
        "attempt": attempt,
        "request_id": request_id,
        "ordinal": ordinal,
        "model_call_ordinal": model_ordinal,
        "origin": origin,
        "scope": scope,
    }
    task_id = payload.get("task_id")
    if isinstance(task_id, str):
        fields["task_id"] = task_id
    spec_hash = payload.get("spec_sha256")
    if isinstance(spec_hash, str) and _SHA256.fullmatch(spec_hash):
        fields["spec_sha256"] = spec_hash
    return fields


def project_tool_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate valid invoker events into one privacy-filtered record per request id.

    ``status`` is ``requested`` until a later event decides it: ``awaiting_approval``,
    ``denied``, ``completed`` or ``failed``. Events with another schema, a malformed
    identity, or an identity that disagrees with the first event of the same request
    are ignored. Arguments, outputs and tool text are never copied: only hashes,
    sizes, codes, durations and ``blob:sha256:`` references.
    """
    records: dict[str, dict[str, Any]] = {}
    for event in events:
        parts = _event_parts(event)
        if parts is None:
            continue
        kind, payload = parts
        identity = _identity(payload)
        if identity is None:
            continue
        request_id = identity["request_id"]
        record = records.get(request_id)
        if record is None:
            record = {**identity, "status": "requested"}
            records[request_id] = record
        elif any(record.get(field) != value for field, value in identity.items()):
            continue

        if kind == "tool_call_requested":
            digest = payload.get("arguments_sha256")
            size = payload.get("arguments_bytes")
            if isinstance(digest, str) and _SHA256.fullmatch(digest):
                record["arguments_sha256"] = digest
            if _integer(size):
                record["arguments_bytes"] = size
            refs = _blob_refs(payload.get("blob_refs"))
            if refs:
                record.setdefault("blob_refs", {}).update(refs)
        elif kind == "tool_permission_requested":
            permission = payload.get("permission_request")
            if isinstance(permission, Mapping):
                permission_id = permission.get("permission_request_id")
                if isinstance(permission_id, str) and len(permission_id) <= 80:
                    record["permission_request_id"] = permission_id
            record["status"] = "awaiting_approval"
        elif kind == "tool_call_denied":
            code = payload.get("code")
            if isinstance(code, str) and len(code) <= 80:
                record["code"] = code
                record["status"] = "awaiting_approval" if code == "approval_required" else "denied"
            permission_id = payload.get("permission_request_id")
            if isinstance(permission_id, str) and len(permission_id) <= 80:
                record["permission_request_id"] = permission_id
        elif kind == "tool_call_completed":
            record["status"] = "completed"
            for name in ("output_sha256",):
                value = payload.get(name)
                if isinstance(value, str) and _SHA256.fullmatch(value):
                    record[name] = value
            for name in ("output_bytes", "duration_ms"):
                value = payload.get(name)
                if _integer(value):
                    record[name] = value
            refs = _blob_refs(payload.get("blob_refs"))
            if refs:
                record.setdefault("blob_refs", {}).update(refs)
        elif kind == "tool_call_failed":
            record["status"] = "failed"
            code = payload.get("code")
            if isinstance(code, str) and len(code) <= 80:
                record["code"] = code
            retryable = payload.get("retryable")
            if isinstance(retryable, bool):
                record["retryable"] = retryable
            duration = payload.get("duration_ms")
            if _integer(duration):
                record["duration_ms"] = duration
            message_hash = payload.get("tool_message_sha256")
            if isinstance(message_hash, str) and _SHA256.fullmatch(message_hash):
                record["tool_message_sha256"] = message_hash
    return list(records.values())


def adapt_log_event_sink(
    log_event: Callable[[str, str, Mapping[str, Any]], None], run_id: str,
) -> ToolEventSink:
    """Bind the host run id to ``log_event(run_id, event_type, payload)``."""
    return lambda event_type, payload: log_event(run_id, event_type, payload)


def make_tool_invoker_sinks(
    log_event: Callable[[str, str, Mapping[str, Any]], None],
    run_id: str,
    blob_sink: BlobSink | None = None,
) -> tuple[ToolEventSink, BlobSink | None]:
    """Return ready-to-inject event and optional local blob sinks for a tool invoker."""
    return adapt_log_event_sink(log_event, run_id), blob_sink
