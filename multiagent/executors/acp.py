# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Bounded ACP v1 JSON-RPC transport for an explicitly selected Codex session."""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from . import process
from .acp_probe import CODEX_ACP_EXECUTABLE, PROTOCOL_VERSION, _clean_env_extra
from .contract import (ExecutorLaunchRequest, ExecutorRunResult, FailureKind,
                       Permission, RunStatus)
from .transport import AgentEvent, EventSink, PermissionAsk, TransportCapabilities, TransportKind

LINE_CAP = 65_536
MAX_HANDSHAKE_MESSAGES = 4096
CANCEL_GRACE_SECONDS = 1.0
_KINDS = {"agent_message_chunk": "message", "agent_thought_chunk": "thought",
          "tool_call": "tool", "tool_call_update": "tool", "plan": "plan",
          "usage_update": "usage"}


class AcpTransportError(RuntimeError):
    """An ACP failure that must be shown to the caller without CLI fallback."""

    failure_kind = FailureKind.TRANSPORT_UNAVAILABLE


@dataclass(frozen=True)
class AcpConsent:
    """Trusted office/area association and its configured ACP permissions."""

    scope_id: str
    allow_write: bool = False
    allow_always: bool = False


class AcpTransport:
    kind = TransportKind.ACP

    def __init__(self, executable: str = CODEX_ACP_EXECUTABLE):
        self.executable = executable

    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(multi_turn=True, cancel=True, live_events=True, permission_requests=True)

    def open_session(self, request: ExecutorLaunchRequest, sink: EventSink) -> AcpSession:
        try:
            folder = process.validate_cwd(request.workdir).resolve(strict=True)
            handle = process.start([self.executable], cwd=folder, stdin_text=None,
                                   timeout_seconds=request.timeout_seconds, output_cap_bytes=LINE_CAP,
                                   env_extra=_clean_env_extra(folder), duplex=True)
        except (process.ProcessLaunchError, OSError, ValueError) as exc:
            raise AcpTransportError("ACP transport could not start") from exc
        session = AcpSession(handle, request, sink, folder)
        try:
            consent = getattr(sink, "acp_consent", None)
            associated = isinstance(consent, AcpConsent) and bool(consent.scope_id.strip())
            result = session._call("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": associated,
                                               "writeTextFile": associated and request.permission is Permission.WRITE
                                               and consent.allow_write}},
            })
            if type(result.get("protocolVersion")) is not int or result["protocolVersion"] != PROTOCOL_VERSION:
                raise AcpTransportError("ACP protocol version is incompatible")
            if not isinstance(result.get("agentCapabilities"), dict):
                raise AcpTransportError("ACP capabilities are invalid")
            created = session._call("session/new", {"cwd": str(folder), "mcpServers": []})
            identifier = created.get("sessionId")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 256:
                raise AcpTransportError("ACP session id is invalid")
            session.session_id = identifier
            return session
        except Exception:
            handle.cancel()
            raise


class AcpSession:
    def __init__(self, handle: process.ProcessHandle, request: ExecutorLaunchRequest,
                 sink: EventSink, folder: Path):
        self._handle = handle
        self._request = request
        self._sink = sink
        self._folder = folder
        self._next_id = 0
        self._write_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_turn = False
        self._dead = False
        self._closed = False
        self._cancel_requested = False
        self._turn_done = threading.Event()
        self._tool_calls: dict[str, dict[str, Any]] = {}
        self._result: ExecutorRunResult | None = None
        self._usage: Mapping[str, Any] | None = None
        self._last_message = ""
        self.session_id: str | None = None

    def _send(self, payload: Mapping[str, Any]) -> None:
        try:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            with self._write_lock:
                self._handle.write_line(raw)
        except (OSError, ValueError) as exc:
            raise AcpTransportError("ACP pipe is unavailable") from exc

    def _call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        messages = 0
        while method == "session/prompt" or messages < MAX_HANDSHAKE_MESSAGES:
            try:
                raw = self._handle.read_line(timeout=0.2)
            except TimeoutError:
                if self._handle.done():
                    raise AcpTransportError("ACP process ended before a response")
                continue
            except (UnicodeError, ValueError) as exc:
                raise AcpTransportError("ACP response exceeds limits or is not UTF-8") from exc
            if raw is None:
                raise AcpTransportError("ACP process ended before a response")
            messages += 1
            try:
                message = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, RecursionError) as exc:
                raise AcpTransportError("ACP response is not JSON") from exc
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise AcpTransportError("ACP response is not JSON-RPC 2.0")
            if "method" in message:
                self._incoming(message)
                continue
            if type(message.get("id")) is not int or message["id"] != request_id:
                raise AcpTransportError("ACP response id does not match")
            if "error" in message:
                error = message["error"]
                if not isinstance(error, dict) or type(error.get("code")) is not int or not isinstance(error.get("message"), str):
                    raise AcpTransportError("ACP error object is invalid")
                raise AcpTransportError("ACP request failed")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AcpTransportError("ACP result is invalid")
            return result
        raise AcpTransportError("ACP message limit exceeded")

    def _incoming(self, message: dict[str, Any]) -> None:
        method, params = message.get("method"), message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise AcpTransportError("ACP request is invalid")
        if method == "session/update":
            if "id" in message or params.get("sessionId") != self.session_id:
                raise AcpTransportError("ACP update is invalid")
            update = params.get("update")
            if not isinstance(update, dict):
                raise AcpTransportError("ACP update is invalid")
            kind = _KINDS.get(update.get("sessionUpdate"))
            if kind:
                data = {key: value for key, value in update.items() if key in
                        ("content", "toolCallId", "title", "status", "entries", "kind", "locations",
                         "used", "size", "cost")}
                if kind == "tool" and isinstance(update.get("toolCallId"), str):
                    known = self._tool_calls.setdefault(update["toolCallId"], {})
                    for field in ("kind", "locations"):
                        if update.get(field) is not None:
                            known[field] = update[field]
                if kind == "message" and isinstance(update.get("content"), dict):
                    content = update["content"]
                    if isinstance(content.get("text"), str):
                        self._last_message += content["text"][:LINE_CAP]
                        self._last_message = self._last_message[-LINE_CAP:]
                if kind == "usage":
                    usage = {k: update[k] for k in ("used", "size")
                             if type(update.get(k)) is int and update[k] >= 0}
                    cost = update.get("cost")
                    if (isinstance(cost, dict) and type(cost.get("amount")) in (int, float)
                            and math.isfinite(cost["amount"]) and cost["amount"] >= 0
                            and isinstance(cost.get("currency"), str) and cost["currency"].strip()):
                        usage["cost"] = {"amount": cost["amount"], "currency": cost["currency"]}
                    self._usage = usage or None
                status = update.get("status") if kind == "tool" else None
                tool_status = status if status in ("pending", "in_progress", "completed", "failed") else None
                self._sink.on_event(AgentEvent(kind, tool_status=tool_status, data=data))
            return
        inbound_id = message.get("id")
        if type(inbound_id) is not int and (not isinstance(inbound_id, str) or not inbound_id):
            raise AcpTransportError("ACP request id is invalid")
        if self.session_id is None:
            self._send({"jsonrpc": "2.0", "id": message["id"], "error":
                        {"code": -32000, "message": "Session unavailable"}})
            return
        try:
            if method == "session/request_permission":
                result = self._permission(params)
            elif method == "fs/read_text_file":
                result = self._read(params)
            elif method == "fs/write_text_file":
                result = self._write(params)
            else:
                raise KeyError(method)
        except (AcpTransportError, OSError, UnicodeError, KeyError):
            self._send({"jsonrpc": "2.0", "id": message["id"], "error":
                        {"code": -32601 if method not in
                         ("session/request_permission", "fs/read_text_file", "fs/write_text_file") else -32000,
                         "message": "Method unavailable"}})
            return
        self._send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def _permission(self, params: dict[str, Any]) -> dict[str, Any]:
        options = params.get("options")
        consent = getattr(self._sink, "acp_consent", None)
        if not isinstance(options, list) or not all(isinstance(x, dict) and
                isinstance(x.get("optionId"), str) and bool(x["optionId"]) and x.get("kind") in
                ("allow_once", "allow_always", "reject_once", "reject_always") for x in options):
            raise AcpTransportError("ACP permission options are invalid")
        if len({x["optionId"] for x in options}) != len(options) or params.get("sessionId") != self.session_id:
            raise AcpTransportError("ACP permission request is invalid")
        if not isinstance(consent, AcpConsent) or not consent.scope_id.strip():
            return {"outcome": {"outcome": "cancelled"}}
        with self._state_lock:
            if self._cancel_requested:
                return {"outcome": {"outcome": "cancelled"}}
        tool_call = params.get("toolCall")
        if not isinstance(tool_call, dict):
            return {"outcome": {"outcome": "cancelled"}}
        call_id = tool_call.get("toolCallId")
        known = self._tool_calls.get(call_id, {}) if isinstance(call_id, str) else {}
        tool = tool_call.get("kind")
        if tool is None:
            tool = known.get("kind")
        if not isinstance(tool, str) or not tool:
            tool = "other"
        if tool not in ("read", "search", "think") and (self._request.permission is not Permission.WRITE or
                                                        not consent.allow_write):
            return {"outcome": {"outcome": "cancelled"}}
        targets: list[str] = []
        locations = tool_call.get("locations")
        if locations is None:
            locations = known.get("locations")
        if locations is not None:
            if not isinstance(locations, list) or not all(isinstance(x, dict) for x in locations):
                return {"outcome": {"outcome": "cancelled"}}
            for entry in locations:
                location = self._path(entry)
                if location is None:
                    return {"outcome": {"outcome": "cancelled"}}
                targets.append(str(location))
        allowed = [x for x in options if x["kind"] != "allow_always" or consent.allow_always]
        if not allowed:
            return {"outcome": {"outcome": "cancelled"}}
        ask = PermissionAsk(tool=tool, target=targets[0] if targets else None,
                            options=tuple(x["kind"] for x in allowed), targets=tuple(targets))
        try:
            chosen = self._sink.on_permission(ask)
        except Exception:
            chosen = "reject_once"
        with self._state_lock:
            if self._cancel_requested:
                return {"outcome": {"outcome": "cancelled"}}
        selected = next((x for x in allowed if x["kind"] == chosen), None)
        if selected is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": selected["optionId"]}}

    def _path(self, params: dict[str, Any]) -> Path | None:
        raw = params.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
        try:
            resolved.relative_to(self._folder)
        except ValueError:
            return None
        return resolved

    def _read(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        consent = getattr(self._sink, "acp_consent", None)
        if (params.get("sessionId") != self.session_id or path is None or not path.is_file()
                or not isinstance(consent, AcpConsent) or not consent.scope_id.strip()):
            raise AcpTransportError("ACP file read is denied")
        for key in ("line", "limit"):
            if key in params and (type(params[key]) is not int or params[key] < 1):
                raise AcpTransportError("ACP file range is invalid")
        start, limit = params.get("line", 1), params.get("limit")
        pieces: list[str] = []
        size = 0
        with path.open("r", encoding="utf-8", newline="") as stream:
            for number, part in enumerate(stream, 1):
                if number < start:
                    continue
                size += len(part.encode("utf-8"))
                if size > LINE_CAP // 2:
                    raise AcpTransportError("ACP file read is denied")
                pieces.append(part)
                if limit is not None and len(pieces) >= limit:
                    break
        content = "".join(pieces)
        if len(json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")) > LINE_CAP - 128:
            raise AcpTransportError("ACP file read is denied")
        return {"content": content}

    def _write(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(params)
        content = params.get("content")
        consent = getattr(self._sink, "acp_consent", None)
        if (params.get("sessionId") != self.session_id or path is None or
                self._request.permission is not Permission.WRITE or
                not isinstance(consent, AcpConsent) or not consent.scope_id.strip() or
                not consent.allow_write or not isinstance(content, str) or
                len(content.encode("utf-8")) > LINE_CAP // 2 or not path.parent.is_dir()):
            raise AcpTransportError("ACP file write is denied")
        path.write_bytes(content.encode("utf-8"))
        return {}

    def prompt(self, text: str) -> AgentEvent:
        if not isinstance(text, str) or self.session_id is None:
            raise RuntimeError("ACP session is closed or prompt is invalid")
        if not self._turn_lock.acquire(blocking=False):
            raise RuntimeError("ACP session is already running")
        try:
            with self._state_lock:
                if self._closed or self._dead or self._handle.done():
                    raise RuntimeError("ACP session is closed or process has ended")
                self._cancel_requested = False
                self._turn_done.clear()
                self._last_message = ""
                self._usage = None
                self._result = None
                self._tool_calls.clear()
                self._active_turn = True
            result = self._call("session/prompt", {"sessionId": self.session_id,
                                                   "prompt": [{"type": "text", "text": text}]})
            reason = result.get("stopReason")
            if not isinstance(reason, str) or not reason:
                raise AcpTransportError("ACP turn has no stopReason")
            event = AgentEvent("end_turn", data={"stop_reason": reason})
            self._sink.on_event(event)
            self._finish(RunStatus.CANCELLED if reason == "cancelled" else RunStatus.COMPLETED)
            return event
        except AcpTransportError:
            try:
                outcome = self._handle.wait(timeout=1)
            except TimeoutError:
                self._handle.cancel()
                outcome = self._handle.wait(timeout=3)
            self._dead = True
            timed_out = outcome.timed_out
            with self._state_lock:
                cancelled = self._cancel_requested
            status = "cancelled" if cancelled else "timeout" if timed_out else "transport_unavailable"
            event = AgentEvent("error", data={"status": status})
            self._sink.on_event(event)
            self._finish(RunStatus.CANCELLED if cancelled else RunStatus.TIMEOUT if timed_out else RunStatus.FAILED,
                         None if cancelled else FailureKind.TRANSPORT_UNAVAILABLE)
            return event
        finally:
            with self._state_lock:
                self._active_turn = False
                self._turn_done.set()
            self._turn_lock.release()

    def _finish(self, status: RunStatus, failure: FailureKind | None = None) -> None:
        envelope = None
        try:
            parsed = json.loads(self._last_message)
            if isinstance(parsed, dict):
                envelope = parsed
        except (ValueError, RecursionError):
            pass
        self._result = ExecutorRunResult(status=status, session_id=self.session_id,
                                         result_envelope=envelope, usage=self._usage,
                                         command=self._handle.command, failure_kind=failure)

    def cancel(self) -> bool:
        with self._state_lock:
            if self._closed or not self._active_turn:
                return False
            self._cancel_requested = True
            try:
                self._send({"jsonrpc": "2.0", "method": "session/cancel",
                            "params": {"sessionId": self.session_id}})
            except AcpTransportError:
                pass
        if not self._turn_done.wait(CANCEL_GRACE_SECONDS):
            self._handle.cancel()
        return True

    def close(self) -> ExecutorRunResult:
        if self._turn_lock.locked():
            raise RuntimeError("ACP session is still running")
        self._closed = True
        if not self._handle.done():
            self._handle.cancel()
        outcome = self._handle.wait(timeout=3)
        if self._result is None:
            self._finish(RunStatus.CANCELLED)
        assert self._result is not None
        self._result = replace(self._result, command=outcome.command)
        return self._result
