# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Normalized session model (GUI-T02 contract, sections 2 and 4): pure data, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Source(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    COPILOT = "copilot"
    ANTIGRAVITY = "antigravity"
    FRAMEWORK = "framework"


class Surface(str, Enum):
    DESKTOP = "desktop"
    CLI = "cli"
    FRAMEWORK = "framework"
    UNKNOWN = "unknown"


class Status(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    ARCHIVED = "archived"
    UNKNOWN = "unknown"


class Kind(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    REASONING_OMITTED = "reasoning_omitted"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"
    SUBAGENT = "subagent"
    COMPACTION = "compaction"
    USAGE = "usage"
    UNKNOWN = "unknown"


class SessionError(Exception):
    """Base class of the session readers' errors."""


class SessionNotFound(SessionError):
    """Unknown id, or a session outside the allowed folders (indistinguishable on purpose)."""


class InvalidSessionId(SessionError, ValueError):
    pass


class InvalidCursor(SessionError, ValueError):
    pass


@dataclass(frozen=True)
class TokenUsage:
    input: int | None = None
    output: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    total: int | None = None  # only when the source gives a total and nothing finer

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"input": self.input, "output": self.output,
                                "cache_read": self.cache_read, "cache_write": self.cache_write}
        if self.total is not None:
            data["total"] = self.total
        return data


@dataclass(frozen=True)
class OpenInApp:
    kind: str  # "url" | "command"
    value: str | list[str]
    needs_confirmation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "value": self.value, "needs_confirmation": self.needs_confirmation}


@dataclass
class SessionSummary:
    id: str
    source: Source
    surface: Surface
    native_id: str
    title: str | None
    cwd: str | None
    created_at: str | None
    updated_at: str | None
    models: list[str] = field(default_factory=list)
    status: Status = Status.UNKNOWN
    parent_id: str | None = None
    child_count: int = 0
    tokens: TokenUsage | None = None
    open_in_app: OpenInApp | None = None
    reader: str = ""
    warnings: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)   # tasks this session works on (PRY); filled by the services

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source": self.source.value, "surface": self.surface.value,
                "native_id": self.native_id, "title": self.title, "cwd": self.cwd,
                "created_at": self.created_at, "updated_at": self.updated_at, "models": list(self.models),
                "status": self.status.value, "parent_id": self.parent_id, "child_count": self.child_count,
                "tokens": self.tokens.to_dict() if self.tokens else None,
                "open_in_app": self.open_in_app.to_dict() if self.open_in_app else None,
                "reader": self.reader, "warnings": list(self.warnings), "task_ids": list(self.task_ids)}


@dataclass
class SessionItem:
    kind: Kind
    cursor: str = ""
    timestamp: str | None = None
    model: str | None = None
    text: str | None = None
    truncated: bool = False
    tool: dict[str, Any] | None = None  # {name, call_id, summary}
    files: list[str] = field(default_factory=list)
    usage: TokenUsage | None = None
    child_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"cursor": self.cursor, "timestamp": self.timestamp, "kind": self.kind.value,
                "model": self.model, "text": self.text, "truncated": self.truncated, "tool": self.tool,
                "files": list(self.files), "usage": self.usage.to_dict() if self.usage else None,
                "child_id": self.child_id}


@dataclass
class ItemsPage:
    items: list[SessionItem]
    next_cursor: str
    done: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"items": [i.to_dict() for i in self.items], "next_cursor": self.next_cursor,
                "done": self.done, "warnings": list(self.warnings)}


@dataclass
class SessionPage:
    sessions: list[SessionSummary]
    next_cursor: str | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"sessions": [s.to_dict() for s in self.sessions], "next_cursor": self.next_cursor,
                "warnings": list(self.warnings)}


@dataclass(frozen=True)
class SessionFilters:
    source: str | None = None
    surface: str | None = None
    cwd: str | None = None  # only sessions whose cwd is under this folder
    since: str | None = None  # ISO-8601, compared with updated_at
    until: str | None = None
    text: str | None = None  # substring of the title, case-insensitive
    limit: int = 100
    after: str | None = None  # "0:<index>"


# -------- identifiers and cursors --------
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CURSOR_RE = re.compile(r"([0-9]{1,3}):([0-9]{1,15})")


def is_uuid(value: object) -> bool:
    return isinstance(value, str) and UUID_RE.fullmatch(value) is not None


def encode_cursor(n: int, offset: int) -> str:
    if n < 0 or offset < 0:
        raise InvalidCursor("cursor parts must be non-negative")
    return f"{n}:{offset}"


def decode_cursor(text: str | None, max_files: int = 1) -> tuple[int, int]:
    """Parse ``"<n>:<offset>"``; ``None``/empty means the start. ``n`` must be < ``max_files``."""
    if text is None or text == "":
        return 0, 0
    match = _CURSOR_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise InvalidCursor("cursor must look like '<n>:<offset>'")
    n, offset = int(match.group(1)), int(match.group(2))
    if n >= max(1, max_files):
        raise InvalidCursor("cursor file index out of range")
    return n, offset
