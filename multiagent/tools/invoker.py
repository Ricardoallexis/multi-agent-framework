"""Synchronous cooperative tool invocation with audit events and injected sinks."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from multiagent.call_guards import Pause, Proceed, Stop
from multiagent.document_trust import BlobSink
from multiagent.tasks.envelopes import blob_ref, canonical_json, envelope_sha256
from .contracts import (RETRYABLE_CODES, ToolError, ToolErrorCode, ToolExecutionError, ToolRequest, ToolResult,
                        ToolSpec)
from .permissions import InvalidArguments, check_call
from .registry import ToolLookupError, ToolRegistry, validate_structure

ToolEventSink = Callable[[str, Mapping[str, Any]], None]
EVENT_REQUESTED = "tool_call_requested"
EVENT_PERMISSION_REQUESTED = "tool_permission_requested"
EVENT_DENIED = "tool_call_denied"
EVENT_COMPLETED = "tool_call_completed"
EVENT_FAILED = "tool_call_failed"


class ToolGuard(Protocol):
    name: str
    order: int
    observer: bool
    def check(self, spec: ToolSpec, request: ToolRequest, state: Any) -> Proceed | Stop | Pause: ...


class ToolCallPaused(Exception):
    def __init__(self, verdict: Pause, request: ToolRequest) -> None:
        super().__init__(verdict.message or verdict.waiting_reason)
        self.verdict = verdict
        self.request = request


def event_payload(request: ToolRequest, version: int | None, digest: str | None, **fields: Any) -> dict[str, Any]:
    context = request.context
    return {"schema": "multiagent.tool_event/1", "request_id": request.request_id,
            "tool_id": request.tool_id, "tool_version": version, "spec_sha256": digest,
            "scope": context.scope, "run_id": context.run_id, "task_id": context.task_id,
            "step_id": context.step_id, "attempt": context.attempt, "ordinal": context.ordinal,
            "model_call_ordinal": context.model_call_ordinal,
            "actor": {"kind": "tool", "id": request.tool_id, "version": version},
            "origin": request.origin.model_dump(mode="json"), **fields}


def _structure(value: Any, schema: Any) -> str | None:
    try:
        return validate_structure(value, schema)
    except Exception:
        return "$"


class ToolInvoker:
    def __init__(self, registry: ToolRegistry, *, event_sink: ToolEventSink | None = None,
                 blob_sink: BlobSink | None = None, clock: Callable[[], float] = time.monotonic,
                 guards: Iterable[ToolGuard] = ()) -> None:
        self.registry = registry
        self.event_sink = event_sink
        self.blob_sink = blob_sink
        self.clock = clock
        checked = tuple(guards)
        names: set[str] = set()
        for guard in checked:
            name = getattr(guard, "name", None)
            if (not isinstance(name, str) or not name or name in names
                    or not isinstance(getattr(guard, "order", None), int) or isinstance(guard.order, bool)
                    or not callable(getattr(guard, "check", None))):
                raise TypeError("A tool guard needs a unique name, an integer order and a check method")
            names.add(name)
        self.guards = tuple(sorted(checked, key=lambda guard: guard.order))

    def _emit(self, kind: str, request: ToolRequest, version: int | None, digest: str | None, **fields: Any) -> None:
        if self.event_sink:
            self.event_sink(kind, event_payload(request, version, digest, **fields))

    def _blob(self, data: bytes) -> str | None:
        if self.blob_sink is None:
            return None
        expected = blob_ref(hashlib.sha256(data).hexdigest())
        actual = self.blob_sink(data, "application/json")
        if actual != expected:
            raise ValueError("Blob sink returned a mismatched reference")
        return actual

    def invoke(self, request: ToolRequest, *, grants: Iterable[str] | None = None,
               evidence: Iterable[Any] | None = None, should_cancel: Callable[[], bool] = lambda: False,
               state: Any = None) -> ToolResult:
        if should_cancel():
            raise RuntimeError("Tool call cancelled by host")
        if not isinstance(request, ToolRequest):
            raise TypeError("request must be a ToolRequest")
        try:
            registered = self.registry.lookup(request.tool_id, request.tool_version)
        except ToolLookupError as exc:
            return self._failure(request, None, None, ToolErrorCode(exc.code), "denied")
        spec, digest = registered.spec, registered.spec_sha256
        if registered.executor is None:
            return self._failure(request, spec.version, digest, ToolErrorCode.NOT_INVOCABLE, "denied")
        try:
            request = ToolRequest.model_validate(request.model_dump(mode="json"))
            arg_bytes = canonical_json(request.arguments)
        except (ValidationError, ValueError, TypeError):
            return self._failure(request, spec.version, digest, ToolErrorCode.INVALID_REQUEST, "denied")
        blobs: dict[str, str] = {}
        try:
            arg_ref = self._blob(arg_bytes)
        except Exception:
            return self._failure(request, spec.version, digest, ToolErrorCode.EXECUTION_FAILED, "failed",
                                 details={"stage": "blob_sink", "executed": False})
        if arg_ref:
            blobs["arguments"] = arg_ref
        self._emit(EVENT_REQUESTED, request, spec.version, digest,
                   arguments_sha256=envelope_sha256(request.arguments), arguments_bytes=len(arg_bytes),
                   impacts=sorted(x.value for x in spec.impacts), blob_refs=blobs.copy())
        if registered.input_model is not None:
            try:
                registered.input_model.model_validate(request.arguments)
                problem = None
            except ValidationError:
                problem = "$"
        else:
            problem = _structure(request.arguments, spec.input_schema.json_schema)
        if problem:
            return self._failure(request, spec.version, digest, ToolErrorCode.INVALID_ARGUMENTS, "denied",
                                 details={"path": problem}, blobs=blobs)
        try:
            code, permission = check_call(spec, request, grants=grants, evidence=evidence)
        except InvalidArguments:
            return self._failure(request, spec.version, digest, ToolErrorCode.INVALID_ARGUMENTS, "denied", blobs=blobs)
        if code:
            if permission:
                self._emit(EVENT_PERMISSION_REQUESTED, request, spec.version, digest,
                           permission_request=permission.model_dump(mode="json"))
            return self._failure(request, spec.version, digest, ToolErrorCode(code), "denied",
                                 permission=permission, blobs=blobs)
        # Same rules as CallGuardChain: an explicit Stop or Pause always counts; an observer that raises or
        # answers something else is ignored; any other guard fails closed.
        for guard in self.guards:
            observer = bool(getattr(guard, "observer", False))
            try:
                verdict = guard.check(spec, request.model_copy(update={"arguments": json.loads(arg_bytes)}), state)
            except Exception:
                if observer:
                    continue
                return self._failure(request, spec.version, digest, ToolErrorCode.GUARD_STOPPED, "denied",
                                     details={"guard": guard.name}, blobs=blobs)
            if isinstance(verdict, Pause):
                raise ToolCallPaused(verdict if verdict.guard else dataclasses.replace(verdict, guard=guard.name),
                                     request)
            if isinstance(verdict, Stop):
                return self._failure(request, spec.version, digest, ToolErrorCode.GUARD_STOPPED, "denied",
                                     details={"guard": guard.name, "code": verdict.code}, blobs=blobs)
            if not isinstance(verdict, Proceed) and not observer:
                return self._failure(request, spec.version, digest, ToolErrorCode.GUARD_STOPPED, "denied",
                                     details={"guard": guard.name, "reason": "invalid_verdict"}, blobs=blobs)
        deadline = min(spec.timeout_s, request.timeout_s or spec.timeout_s)
        start = self.clock()
        try:
            output = registered.executor(json.loads(arg_bytes), request.context, should_cancel=should_cancel)
        except Exception as exc:
            # Only the framework's own executor error carries a stable code into the event; other exceptions stay opaque.
            details = {"code": exc.code} if isinstance(exc, ToolExecutionError) else None
            message = str(exc).encode("utf-8")
            digest_message = hashlib.sha256(message).hexdigest()
            try:
                if self.blob_sink:
                    expected = blob_ref(digest_message)
                    actual = self.blob_sink(message, "text/plain")
                    if actual != expected:
                        raise ValueError("Blob sink returned a mismatched reference")
                    blobs["tool_message"] = actual
            except Exception:
                return self._failure(request, spec.version, digest, ToolErrorCode.EXECUTION_FAILED, "failed",
                                     duration=int((self.clock()-start)*1000), message_sha=digest_message,
                                     details={"stage": "blob_sink", "executed": True}, blobs=blobs)
            return self._failure(request, spec.version, digest, ToolErrorCode.EXECUTION_FAILED, "failed",
                                 duration=int((self.clock()-start)*1000), message_sha=digest_message,
                                 details=details, blobs=blobs)
        duration = int((self.clock() - start) * 1000)
        if duration > deadline * 1000:
            return self._failure(request, spec.version, digest, ToolErrorCode.TIMEOUT, "failed", duration=duration, blobs=blobs)
        if should_cancel():
            return self._failure(request, spec.version, digest, ToolErrorCode.CANCELLED, "failed", duration=duration, blobs=blobs)
        try:
            output_bytes = canonical_json(output)
        except (TypeError, ValueError):
            return self._failure(request, spec.version, digest, ToolErrorCode.INVALID_OUTPUT, "failed", duration=duration, blobs=blobs)
        if len(output_bytes) > spec.max_output_bytes:
            return self._failure(request, spec.version, digest, ToolErrorCode.OUTPUT_TOO_LARGE, "failed", duration=duration, blobs=blobs)
        if registered.output_model is not None:
            try:
                registered.output_model.model_validate(output)
                problem = None
            except ValidationError:
                problem = "$"
        else:
            problem = _structure(output, spec.output_schema.json_schema)
        if problem:
            return self._failure(request, spec.version, digest, ToolErrorCode.INVALID_OUTPUT, "failed", duration=duration, blobs=blobs)
        try:
            if self.blob_sink:
                blobs["output"] = self._blob(output_bytes)
        except Exception:
            # The tool already ran: close the trace and say so. Not retryable, a retry would repeat the effect.
            return self._failure(request, spec.version, digest, ToolErrorCode.INVALID_OUTPUT, "failed",
                                 duration=duration, details={"stage": "blob_sink", "executed": True}, blobs=blobs)
        output = json.loads(output_bytes)
        result = ToolResult(request_id=request.request_id, tool_id=request.tool_id, tool_version=spec.version,
                            spec_sha256=digest, ok=True, output=output, output_sha256=hashlib.sha256(output_bytes).hexdigest(),
                            output_bytes=len(output_bytes), duration_ms=duration,
                            provenance_ref=f"tool:{request.request_id}", blob_refs=blobs)
        self._emit(EVENT_COMPLETED, request, spec.version, digest, output_sha256=result.output_sha256,
                   output_bytes=result.output_bytes, duration_ms=duration, blob_refs=blobs.copy())
        return result

    def _failure(self, request: ToolRequest, version: int | None, digest: str | None,
                 code: ToolErrorCode, stage: str, *, details: dict[str, Any] | None = None,
                 permission: Any = None, duration: int | None = None,
                 message_sha: str | None = None, blobs: dict[str, str] | None = None) -> ToolResult:
        error = ToolError(code=code, message=f"Tool call {code.value}.", retryable=code in RETRYABLE_CODES,
                          details=details or {}, tool_message_sha256=message_sha)
        result = ToolResult(request_id=request.request_id, tool_id=request.tool_id, tool_version=version,
                            spec_sha256=digest, ok=False, error=error, permission_request=permission,
                            duration_ms=duration, provenance_ref=f"tool:{request.request_id}", blob_refs=blobs or {})
        fields = {"code": code.value, "retryable": error.retryable, "duration_ms": duration,
                  "tool_message_sha256": message_sha, "details": error.details} if stage == "failed" else {
                      "code": code.value, "reason": error.message,
                      "permission_request_id": permission.permission_request_id if permission else None}
        self._emit(EVENT_FAILED if stage == "failed" else EVENT_DENIED, request, version, digest, **fields)
        return result
