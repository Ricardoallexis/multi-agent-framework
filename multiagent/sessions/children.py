# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Unified list of the children of one session (GUI2-T04): subagents, spawned threads and workers.

Every source (Claude ``subagents/``, Codex ``thread_spawn_edges``, Copilot ``subagent.*`` events and the
steps of a framework run) is mapped to the same ``ChildAgent``: who it is, which model, how far along it is
and how many tokens it used. Only metadata leaves here: names and labels the app itself gives the child
(agent type, nickname, one-line description), never a message, a prompt, a tool result or a command.

Status rules (the tracker's, with an injectable clock so they can be tested):

* ``done``: the child finished (its source says so);
* ``running``: less than 15 minutes since its last record;
* ``testing``: as ``running`` but its last tool call is ``pytest``, ``probar`` or ``integrar`` and has no
  result yet (a test run is in flight);
* ``idle``: between 15 and 60 minutes without records;
* ``stopped``: more than 60 minutes without records, or it failed;
* ``unknown``: the source gave no usable timestamp.

``active`` (the "Activos" tab) is ``running``, ``testing`` and ``idle``; ``done`` (the "Terminados" tab) is
``done``, ``stopped`` and ``unknown``.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .models import TokenUsage

RUNNING_SECONDS = 15 * 60
STOPPED_SECONDS = 60 * 60
MAX_CHILDREN = 500
MAX_NAME_CHARS = 80
MAX_LABEL_CHARS = 120
MAX_COMMAND_CHARS = 2000
TEST_COMMAND = re.compile(r"pytest|\bprobar\b|\bintegrar\b", re.IGNORECASE)
_COMMAND_KEYS = ("command", "cmd", "script")
_SAFE_ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")

LevelOf = Callable[["str | None"], "str | None"]


def no_level(_model: str | None) -> str | None:
    return None


class ChildStatus(str, Enum):
    RUNNING = "running"
    TESTING = "testing"
    IDLE = "idle"
    DONE = "done"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


ACTIVE_STATUSES = frozenset({ChildStatus.RUNNING.value, ChildStatus.TESTING.value, ChildStatus.IDLE.value})
GROUPS = ("active", "done")


def group_of(status: str) -> str:
    return "active" if status in ACTIVE_STATUSES else "done"


@dataclass
class ChildAgent:
    id: str                       # unique inside the list (not always an openable session)
    source: str                   # claude | codex | copilot | framework
    kind: str                     # subagent | thread | step
    name: str | None              # agent type, nickname or step id
    label: str | None             # one-line description the app gave the child
    model: str | None
    level: str | None             # T1..T4 when the catalog maps the model, else None
    status: ChildStatus
    started_at: str | None
    last_activity_at: str | None
    tokens: TokenUsage | None     # None = unknown (never an invented 0)
    link_id: str | None           # session id that opens the child's conversation, if it has one
    note: str | None = None       # short code that refines the status ("waiting_human")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "source": self.source, "kind": self.kind, "name": self.name, "label": self.label,
                "model": self.model, "level": self.level, "status": self.status.value, "note": self.note,
                "started_at": self.started_at, "last_activity_at": self.last_activity_at,
                "tokens": self.tokens.to_dict() if self.tokens else None, "link_id": self.link_id}


# -------- clock and status --------
def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def age_seconds(ts: str | None, now: datetime) -> float | None:
    dt = parse_ts(ts)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds())  # a timestamp from the future counts as "just now"


def derive_status(*, now: datetime, last_activity: str | None, finished: bool = False, failed: bool = False,
                  testing: bool = False) -> ChildStatus:
    if finished and not failed:
        return ChildStatus.DONE
    if failed:
        return ChildStatus.STOPPED
    age = age_seconds(last_activity, now)
    if age is None:
        return ChildStatus.UNKNOWN
    if age < RUNNING_SECONDS:
        return ChildStatus.TESTING if testing else ChildStatus.RUNNING
    if age <= STOPPED_SECONDS:
        return ChildStatus.IDLE
    return ChildStatus.STOPPED


# -------- test commands (without keeping or returning the command) --------
def command_text(args: Any) -> str:
    """The shell command inside a tool call's arguments (``command``/``cmd``/``script``), bounded; else ``""``.

    A plain string that is not JSON is never searched: it may be a patch or a file body.
    """
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return ""
    if isinstance(args, dict) and "action" in args and isinstance(args["action"], dict):
        args = args["action"]
    if not isinstance(args, dict):
        return ""
    for key in _COMMAND_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            return value[:MAX_COMMAND_CHARS]
        if isinstance(value, list) and value:
            return " ".join(str(part) for part in value)[:MAX_COMMAND_CHARS]
    return ""


def is_test_call(args: Any) -> bool:
    return TEST_COMMAND.search(command_text(args)) is not None


# -------- small helpers shared by the readers --------
def clean_text(value: object, limit: int) -> str | None:
    """A short single-line text from the app (type, nickname, description), or ``None``."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:limit] if text else None


def safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def token_total(tokens: TokenUsage | None) -> int | None:
    """One number to compare or show: the reported total, else input + output, else ``None``."""
    if tokens is None:
        return None
    if tokens.total is not None:
        return tokens.total
    parts = [v for v in (tokens.input, tokens.output) if v is not None]
    return sum(parts) if parts else None


def sort_children(children: list[ChildAgent]) -> list[ChildAgent]:
    """Most recent activity first (unknown times last), then by id so the order is stable."""
    return sorted(children, key=lambda c: (c.last_activity_at or c.started_at or "", c.id), reverse=True)
