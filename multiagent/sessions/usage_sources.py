# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only readers of the usage the apps leave on disk (USO-T02; sources measured by USO-T01).

Pure functions: each takes the folder or file it reads as a parameter, and the time window or ``now`` as
arguments. None looks at the real home folder, the environment or the clock, so a test points them at
a temporary folder and nothing real is ever read. Only numbers, model names, times and limit facts leave
here: never a message, a title, a working folder, a session id or a request id.

What each app gives (USO-T01):

* Claude: assistant records of ``projects/**/*.jsonl`` (tokens per message, deduplicated by message id),
  the ``quotaLimits`` record written when a limit is hit (reset instant, no percent), and, only if the
  operator configured it, the JSON a ``statusLine`` script received (``rate_limits`` with percent and reset).
* Codex: ``token_count`` events of ``sessions/**/rollout-*.jsonl`` (cumulative tokens, plus ``rate_limits``
  with the percent and reset of the 5 hour and weekly windows).
* Copilot: ``session-store.db`` table ``assistant_usage_events`` (tokens and AI credits per call) and
  ``session.error`` ``quota_exceeded`` events; the monthly reset is a documented rule, not on disk.
* Antigravity (agy): nothing on disk; the limits come from the text of ``agy -p /usage --output-format text``
  (``parse_agy_usage``, a pure parser: this module never starts agy).

Never opened, because they hold credentials or tokens: ``~/.codex/auth.json``, ``~/.claude.json``, the
keyring and Copilot's ``data.db`` (``accounts`` and ``settings``). Databases are opened ``mode=ro`` with a
column allow-list. Every reader skips what it cannot read and reports it as a short warning instead of failing.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._common import MAX_LINE_BYTES, int_or_none, normalize_ts, open_ro, select_rows
from .models import is_uuid

ROLE_PRIMARY = "primary"      # a top-level session
ROLE_DELEGATED = "delegated"  # a subagent, spawned thread or worker started by another session
ROLE_UNKNOWN = "unknown"      # the source does not say

# Window names (the values of ``executors.contract.LimitWindow``).
FIVE_HOUR, WEEKLY, MODEL_WEEKLY, MONTHLY, SPEND, UNKNOWN_WINDOW = (
    "five_hour", "weekly", "model_weekly", "monthly", "spend", "unknown")
# Where a reset time comes from (the values of ``executors.contract.ResetSource``).
RESET_APP_EPOCH, RESET_RULE, RESET_NONE = "app_epoch", "rule", "none"

MTIME_SLACK_SECONDS = 60          # a file whose last write is older than the window start holds nothing in it
MAX_STATUSLINE_BYTES = 256 * 1024
NANO_AIU_PER_CREDIT = 1_000_000_000  # inferred (USO-T01, confidence medium): 1 AI credit = 1e9 nano AIU
_ROLLOUT_UUID = re.compile(
    r"rollout-.*?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl")
_TAIL_STEPS = (256 * 1024, 2 * 1024 * 1024, 16 * 1024 * 1024)
_CLAUDE_SYNTHETIC = "<synthetic>"


@dataclass(frozen=True, slots=True)
class TokenEvent:
    """Tokens of one call (Claude, Copilot) or the growth of a session's counters at one point (Codex)."""

    app: str
    at: datetime
    model: str | None
    role: str
    input: int | None = None
    output: int | None = None
    cache_read: int | None = None
    cache_write: int | None = None
    reasoning: int | None = None
    total: int | None = None
    nano_aiu: int | None = None   # Copilot's cost unit; None elsewhere


@dataclass(frozen=True)
class RateWindow:
    """One allowance of a plan as the app reported it."""

    window: str
    used_percent: float | None
    resets_at: datetime | None
    window_minutes: int | None
    reset_source: str


@dataclass(frozen=True)
class PlanObservation:
    """What an app reported about its plan at ``observed_at``."""

    app: str
    source: str
    observed_at: datetime | None
    plan: str | None = None
    windows: tuple[RateWindow, ...] = ()
    credits: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LimitHit:
    """A limit the app rejected a request for (the latest one per window)."""

    app: str
    at: datetime
    window: str
    resets_at: datetime | None
    reset_source: str
    detail: str | None = None


# --------------------------------------------------------------------------- helpers

def parse_instant(value: object) -> datetime | None:
    """An epoch (seconds or milliseconds) or an ISO-8601 text as an aware UTC datetime; ``None`` otherwise."""
    text = normalize_ts(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _epoch(value: object) -> datetime | None:
    """An epoch number of seconds (or milliseconds) as UTC; ``None`` for anything else, text included."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value <= 0:
        return None
    return parse_instant(value)


def _number(value: object) -> float | None:
    """A finite non-negative number (a bool is not one), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return float(value)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _recent_files(base: Path, pattern: str, since: datetime) -> list[Path]:
    """Files under ``base`` written at or after ``since`` (oldest first): earlier ones cannot hold newer events."""
    if not base.is_dir():
        return []
    floor = since.timestamp() - MTIME_SLACK_SECONDS
    found: list[tuple[float, str, Path]] = []
    try:
        for path in base.rglob(pattern):
            mtime = _mtime(path)
            if mtime is not None and mtime >= floor and path.is_file():
                found.append((mtime, str(path), path))
    except OSError:
        pass
    return [path for _, _, path in sorted(found)]


def _json_lines(path: Path, any_of: Sequence[bytes]) -> Iterator[dict[str, Any]]:
    """JSON objects of a JSONL file whose raw line holds one of ``any_of`` (a cheap filter before parsing).

    Lines over 1 MB, lines that are not JSON objects and a last line still being written are skipped.
    """
    try:
        handle = open(path, "rb")
    except OSError:
        return
    with handle:
        while True:
            line = handle.readline(MAX_LINE_BYTES + 1)
            if not line:
                return
            if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
                while True:  # drop the rest of an oversized line in bounded chunks
                    rest = handle.readline(MAX_LINE_BYTES)
                    if not rest or rest.endswith(b"\n"):
                        break
                continue
            if not any(needle in line for needle in any_of):
                continue
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(record, dict):
                yield record


def _tail_lines(path: Path, size: int) -> tuple[list[bytes], bool]:
    """The last lines of a file (about ``size`` bytes) and whether they reach the start of the file."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            length = handle.tell()
            start = max(0, length - size)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return [], True
    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]  # the first line is cut in the middle
    return lines, start == 0


def _sum(values: Sequence[int | None]) -> int | None:
    """Sum of the reported values; ``None`` when none was reported (never a made-up 0)."""
    reported = [value for value in values if value is not None]
    return sum(reported) if reported else None


def month_start(moment: datetime) -> datetime:
    """The first instant (00:00:00 UTC) of the month ``moment`` is in."""
    moment = moment.astimezone(timezone.utc)
    return datetime(moment.year, moment.month, 1, tzinfo=timezone.utc)


def next_month(moment: datetime) -> datetime:
    """The next first day of a month at 00:00:00 UTC after ``moment`` (Copilot's documented reset)."""
    start = month_start(moment)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- Claude

def read_claude_tokens(claude_home: Path, since: datetime, until: datetime) -> tuple[list[TokenEvent], list[str]]:
    """Tokens of the assistant messages in ``[since, until)`` from ``<claude_home>/projects/**/*.jsonl``.

    A message is written once per content block, so records are deduplicated by message id (the last record
    wins, as it carries the final usage), across files too: a resumed session repeats the history of the
    one it continues. Messages from a ``subagents`` folder or marked ``isSidechain`` are ``delegated``.
    Messages with no usage and the app's synthetic error messages are skipped.
    """
    base = Path(claude_home) / "projects"
    if not base.is_dir():
        return [], ["source_unavailable: claude projects folder not found"]
    latest: dict[str, TokenEvent] = {}
    anonymous = 0
    for path in _recent_files(base, "*.jsonl", since):
        try:
            nested = "subagents" in path.relative_to(base).parts
        except ValueError:
            nested = False
        for record in _json_lines(path, (b'"usage"',)):
            message = record.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if record.get("type") != "assistant" or not isinstance(usage, dict) or record.get("isApiErrorMessage"):
                continue
            model = message.get("model")
            if model == _CLAUDE_SYNTHETIC:
                continue
            at = parse_instant(record.get("timestamp"))
            if at is None:
                continue
            fields = [int_or_none(usage.get(name)) for name in (
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
            if all(value is None for value in fields):
                continue
            message_id = message.get("id")
            if not isinstance(message_id, str) or not message_id:
                anonymous += 1
                message_id = f"anonymous:{anonymous}"
            role = ROLE_DELEGATED if nested or record.get("isSidechain") is True else ROLE_PRIMARY
            latest[message_id] = TokenEvent(
                app="claude", at=at, model=model if isinstance(model, str) and model else None, role=role,
                input=fields[0], output=fields[1], cache_read=fields[2], cache_write=fields[3], total=_sum(fields))
    return [event for event in latest.values() if since <= event.at < until], []


def _claude_limits_of(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The structured limit record of a transcript line: ``quotaLimits`` or ``rate_limit_info`` (also under ``apiErrorParams``)."""
    for container in (record, record.get("apiErrorParams")):
        if isinstance(container, Mapping):
            for key in ("quotaLimits", "rate_limit_info"):
                if isinstance(container.get(key), Mapping):
                    return container[key]
    return None


def _claude_window(rate_type: object) -> str:
    if not isinstance(rate_type, str):
        return UNKNOWN_WINDOW
    if rate_type == "five_hour":
        return FIVE_HOUR
    if rate_type == "seven_day":
        return WEEKLY
    if rate_type.startswith("seven_day_"):
        return MODEL_WEEKLY
    if rate_type == "overage":
        return SPEND
    return UNKNOWN_WINDOW


def read_claude_limit_hits(claude_home: Path, since: datetime) -> tuple[list[LimitHit], list[str]]:
    """The latest rejected request per window since ``since``, from the ``quotaLimits`` records of the transcripts.

    The record gives the reset instant (an epoch, exact) but no percent, and only exists after the limit was hit.
    """
    base = Path(claude_home) / "projects"
    if not base.is_dir():
        return [], ["source_unavailable: claude projects folder not found"]
    latest: dict[str, LimitHit] = {}
    for path in _recent_files(base, "*.jsonl", since):
        for record in _json_lines(path, (b'"quotaLimits"', b'"rate_limit_info"')):
            limits = _claude_limits_of(record)
            at = parse_instant(record.get("timestamp"))
            if limits is None or at is None or at < since or limits.get("status") != "rejected":
                continue
            rate_type = limits.get("rateLimitType", limits.get("rate_limit_type"))
            resets = _epoch(limits.get("resetsAt", limits.get("resets_at")))
            window = _claude_window(rate_type)
            key = str(rate_type) if isinstance(rate_type, str) else window
            hit = LimitHit(app="claude", at=at, window=window, resets_at=resets,
                           reset_source=RESET_APP_EPOCH if resets else RESET_NONE,
                           detail=rate_type if isinstance(rate_type, str) else None)
            if key not in latest or hit.at >= latest[key].at:
                latest[key] = hit
    return sorted(latest.values(), key=lambda hit: (hit.window, hit.detail or "")), []


def read_claude_statusline(path: Path) -> tuple[PlanObservation | None, list[str]]:
    """The plan windows of a ``statusLine`` snapshot the operator chose to save (``rate_limits``), or ``None``.

    The documented input of a status line script has ``rate_limits.five_hour`` and ``rate_limits.seven_day``,
    each with ``used_percentage`` and ``resets_at`` (epoch). The framework never installs such a script: this
    only reads a JSON file at the path the operator configured. ``observed_at`` is the file's last write.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_STATUSLINE_BYTES:
            return None, ["statusline_snapshot_too_large"]
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ["statusline_snapshot_missing"]
    except (OSError, ValueError, RecursionError):
        return None, ["statusline_snapshot_unreadable"]
    limits = data.get("rate_limits") if isinstance(data, dict) else None
    if not isinstance(limits, Mapping):
        return None, ["statusline_snapshot_without_rate_limits"]
    windows = []
    for name, window in (("five_hour", FIVE_HOUR), ("seven_day", WEEKLY)):
        entry = limits.get(name)
        if not isinstance(entry, Mapping):
            continue
        percent, resets = _number(entry.get("used_percentage")), _epoch(entry.get("resets_at"))
        if percent is None and resets is None:
            continue
        windows.append(RateWindow(window, percent, resets,
                                  300 if window == FIVE_HOUR else 10080,
                                  RESET_APP_EPOCH if resets else RESET_NONE))
    if not windows:
        return None, ["statusline_snapshot_without_rate_limits"]
    modified = _mtime(path)
    observed = parse_instant(modified) if modified is not None else None
    return PlanObservation("claude", "claude_statusline", observed, windows=tuple(windows)), []


# --------------------------------------------------------------------------- Codex

def _codex_rollouts(home: Path) -> list[Path]:
    found: list[Path] = []
    for sub in ("sessions", "archived_sessions"):
        base = home / sub
        if base.is_dir():
            try:
                found.extend(base.rglob("rollout-*.jsonl"))
            except OSError:
                pass
    return found


def _codex_children(home: Path) -> set[str]:
    """Ids of the threads another thread spawned (``thread_spawn_edges``); empty when the index is missing."""
    db = home / "state_5.sqlite"
    if not db.is_file():
        return set()
    try:
        conn = open_ro(db)
        try:
            rows = select_rows(conn, "thread_spawn_edges", ("child_thread_id",))
        finally:
            conn.close()
    except sqlite3.Error:
        return set()
    return {str(row["child_thread_id"]).lower() for row in rows if is_uuid(row.get("child_thread_id"))}


def _is_spawned(source: object) -> bool:
    """Whether a rollout's ``session_meta.source`` says the thread was spawned by another one."""
    if isinstance(source, (dict, list)):
        return "subagent" in json.dumps(source).lower() or "thread_spawn" in json.dumps(source).lower()
    return isinstance(source, str) and "subagent" in source.lower()


_CODEX_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
                 "reasoning_output_tokens", "total_tokens")


def _growth(current: int | None, previous: int | None) -> int | None:
    """How much a cumulative counter grew; a counter that went down restarted, so its new value is all new."""
    if current is None:
        return None
    if previous is None or current < previous:
        return current
    return current - previous


def read_codex_tokens(codex_home: Path, since: datetime, until: datetime) -> tuple[list[TokenEvent], list[str]]:
    """Tokens Codex used in ``[since, until)``: the growth of each rollout's cumulative ``total_token_usage``.

    Counting growth (not the raw totals) makes a window exact and ignores the repeated ``token_count`` events
    Codex writes without new usage. The model is the last ``turn_context`` seen before the event. A rollout
    is ``delegated`` when ``state_5.sqlite`` lists it as a spawned thread or its ``session_meta`` says so.
    """
    home = Path(codex_home)
    if not home.is_dir():
        return [], ["source_unavailable: codex store not found"]
    spawned = _codex_children(home)
    floor = since.timestamp() - MTIME_SLACK_SECONDS
    events: list[TokenEvent] = []
    for path in sorted(_codex_rollouts(home), key=str):
        mtime = _mtime(path)
        if mtime is None or mtime < floor:
            continue
        match = _ROLLOUT_UUID.fullmatch(path.name)
        delegated = bool(match) and match.group(1).lower() in spawned
        model: str | None = None
        previous: dict[str, int | None] = {}
        for record in _json_lines(path, (b'"token_count"', b'"turn_context"', b'"session_meta"')):
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            kind = record.get("type")
            if kind == "turn_context":
                model = payload.get("model") if isinstance(payload.get("model"), str) else model
            elif kind == "session_meta":
                delegated = delegated or _is_spawned(payload.get("source"))
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                usage = info.get("total_token_usage") if isinstance(info, dict) else None
                if not isinstance(usage, dict):
                    continue
                current = {name: int_or_none(usage.get(name)) for name in _CODEX_FIELDS}
                grown = {name: _growth(current[name], previous.get(name)) for name in _CODEX_FIELDS}
                previous = {**previous, **{name: value for name, value in current.items() if value is not None}}
                at = parse_instant(record.get("timestamp"))
                moved = grown["total_tokens"] if grown["total_tokens"] is not None else _sum(
                    [grown["input_tokens"], grown["output_tokens"]])
                if at is None or not moved or not since <= at < until:
                    continue
                events.append(TokenEvent(
                    app="codex", at=at, model=model, role=ROLE_DELEGATED if delegated else ROLE_PRIMARY,
                    input=grown["input_tokens"], output=grown["output_tokens"], cache_read=grown["cached_input_tokens"],
                    cache_write=grown["cache_write_input_tokens"], reasoning=grown["reasoning_output_tokens"],
                    total=moved))
    return events, []


def _last_rate_limits(path: Path) -> tuple[datetime, Mapping[str, Any]] | None:
    """The newest ``token_count`` event of a rollout whose ``rate_limits`` has a usable 5 hour window, read from the end."""
    for size in _TAIL_STEPS:
        lines, whole = _tail_lines(path, size)
        for raw in reversed(lines):
            if b'"rate_limits"' not in raw:
                continue
            try:
                record = json.loads(raw)
            except (ValueError, RecursionError):
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            limits = payload.get("rate_limits") if isinstance(payload, dict) else None
            at = parse_instant(record.get("timestamp")) if isinstance(record, dict) else None
            primary = limits.get("primary") if isinstance(limits, Mapping) else None
            if (at is not None and isinstance(payload, dict) and payload.get("type") == "token_count"
                    and isinstance(primary, Mapping) and _number(primary.get("used_percent")) is not None):
                return at, limits
        if whole:
            return None
    return None


def _codex_window(minutes: int | None, fallback: str) -> str:
    return {300: FIVE_HOUR, 10080: WEEKLY}.get(minutes, fallback) if minutes else fallback


def read_codex_plan(codex_home: Path) -> tuple[PlanObservation | None, list[str]]:
    """The most recent ``rate_limits`` Codex wrote (by the event's own time, not the file's), or ``None``.

    Files are tried newest first and the search stops once a file is older than the best event found.
    """
    home = Path(codex_home)
    if not home.is_dir():
        return None, ["source_unavailable: codex store not found"]
    files = [(mtime, path) for path in _codex_rollouts(home) if (mtime := _mtime(path)) is not None]
    best: tuple[datetime, Mapping[str, Any]] | None = None
    for mtime, path in sorted(files, key=lambda item: (item[0], str(item[1])), reverse=True):
        if best is not None and mtime < best[0].timestamp() - MTIME_SLACK_SECONDS:
            break
        found = _last_rate_limits(path)
        if found is not None and (best is None or found[0] > best[0]):
            best = found
    if best is None:
        return None, ["no_rate_limit_events"]
    at, limits = best
    windows = []
    for name, fallback in (("primary", FIVE_HOUR), ("secondary", WEEKLY)):
        entry = limits.get(name)
        if not isinstance(entry, Mapping):
            continue
        minutes = int_or_none(entry.get("window_minutes"))
        resets = _epoch(entry.get("resets_at"))
        windows.append(RateWindow(_codex_window(minutes, fallback), _number(entry.get("used_percent")), resets, minutes,
                                  RESET_APP_EPOCH if resets else RESET_NONE))
    credits = limits.get("credits")
    shown = None
    if isinstance(credits, Mapping):
        balance = credits.get("balance")
        shown = {"has_credits": credits.get("has_credits") if isinstance(credits.get("has_credits"), bool) else None,
                 "unlimited": credits.get("unlimited") if isinstance(credits.get("unlimited"), bool) else None,
                 "balance": balance if isinstance(balance, str) else None}
    plan = limits.get("plan_type")
    return PlanObservation("codex", "codex_rollout", at, plan=plan if isinstance(plan, str) and plan else None,
                           windows=tuple(windows), credits=shown), []


# --------------------------------------------------------------------------- Copilot

def read_copilot_tokens(copilot_home: Path, since: datetime, until: datetime) -> tuple[list[TokenEvent], list[str]]:
    """Calls in ``[since, until)`` from ``session-store.db`` table ``assistant_usage_events`` (allow-listed columns).

    Nothing tells a subagent's call from the main one there, so the role is ``unknown``.
    """
    db = Path(copilot_home) / "session-store.db"
    if not db.is_file():
        return [], ["source_unavailable: copilot usage database not found"]
    columns = ("model", "created_at", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
               "reasoning_tokens", "total_nano_aiu")
    try:
        conn = open_ro(db)
        try:
            rows = select_rows(conn, "assistant_usage_events", columns)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [], [f"source_unavailable: copilot usage database ({type(exc).__name__})"]
    events = []
    for row in rows:
        at = parse_instant(row.get("created_at"))
        if at is None or not since <= at < until:
            continue
        model = row.get("model")
        fields = [int_or_none(row.get(name)) for name in ("input_tokens", "output_tokens")]
        events.append(TokenEvent(
            app="copilot", at=at, model=model if isinstance(model, str) and model else None, role=ROLE_UNKNOWN,
            input=fields[0], output=fields[1], cache_read=int_or_none(row.get("cache_read_tokens")),
            cache_write=int_or_none(row.get("cache_write_tokens")), reasoning=int_or_none(row.get("reasoning_tokens")),
            total=_sum(fields), nano_aiu=int_or_none(row.get("total_nano_aiu"))))
    return events, []


def read_copilot_limit_hit(copilot_home: Path, since: datetime) -> tuple[LimitHit | None, list[str]]:
    """The latest ``quota_exceeded`` error since ``since`` in the session event logs (it carries no reset time)."""
    base = Path(copilot_home) / "session-state"
    if not base.is_dir():
        return None, ["source_unavailable: copilot session-state folder not found"]
    latest: LimitHit | None = None
    for path in _recent_files(base, "events.jsonl", since):
        for record in _json_lines(path, (b'"quota_exceeded"',)):
            data = record.get("data") if isinstance(record.get("data"), dict) else record
            at = parse_instant(record.get("timestamp"))
            if at is None or at < since or data.get("errorCode") != "quota_exceeded":
                continue
            if latest is None or at >= latest.at:
                latest = LimitHit("copilot", at, MONTHLY, None, RESET_NONE, "quota_exceeded")
    return latest, []


# --------------------------------------------------------------------------- Antigravity (agy)

AGY_WINDOW_MINUTES = {FIVE_HOUR: 300, WEEKLY: 10080, MODEL_WEEKLY: 10080}
_AGY_LINE = re.compile(
    r"^(?P<head>.+?)\s+(?P<kind>Weekly|Five Hour) Limit(?P<rem> Remaining)?\s+"
    r"(?P<percent>\d+(?:\.\d+)?)\s*%\s+(?P<reset>\S+)$", re.IGNORECASE)


@dataclass(frozen=True)
class AgyLimit:
    """One line of ``agy /usage``: what is left of a window of a model group (or of one model) and its reset."""

    name: str                  # the group ("Gemini Models") or, for ``model_weekly``, the model
    window: str                # five_hour | weekly | model_weekly
    remaining_percent: float   # 0..100
    resets_at: datetime | None

    @property
    def used_percent(self) -> float:
        return round(100.0 - self.remaining_percent, 6)

    def to_rate_window(self) -> RateWindow:
        return RateWindow(self.window, self.used_percent, self.resets_at, AGY_WINDOW_MINUTES.get(self.window),
                          RESET_APP_EPOCH if self.resets_at else RESET_NONE)


def _agy_fields(line: str) -> tuple[str, str, str, str] | None:
    """``(name, label, percent, reset)`` of one line: split on tabs, else on the words (tabs lost in transit)."""
    parts = [part.strip() for part in line.split("\t")]
    if len(parts) == 4 and parts[2].endswith("%"):
        return parts[0], parts[1], parts[2][:-1].strip(), parts[3]
    match = _AGY_LINE.match(line.strip())
    if not match:
        return None
    words = match["head"].split()
    head = match["head"]
    if not match["rem"] and len(words) % 2 == 0 and words[:len(words) // 2] == words[len(words) // 2:]:
        head = " ".join(words[:len(words) // 2])  # "<model> <model> Weekly Limit": the model once
    return head, f"{match['kind']} Limit{match['rem'] or ''}", match["percent"], match["reset"]


def parse_agy_usage(text: str) -> tuple[list[AgyLimit], list[str]]:
    """The limits in the text of ``agy -p /usage --output-format text``; pure (no process, no clock, no file).

    Lines are ``<group> TAB <Weekly|Five Hour> Limit Remaining TAB <N>% TAB <ISO-8601>`` or
    ``<model> TAB <model> Weekly Limit TAB <N>% TAB <ISO-8601>``. Anything else is skipped and counted in one
    warning; a percent outside 0..100 drops the line; an unreadable reset time keeps the line without it.
    """
    limits: list[AgyLimit] = []
    warnings: list[str] = []
    skipped = 0
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        fields = _agy_fields(raw)
        label = fields[1].lower() if fields else ""
        if fields is None or not ("weekly limit" in label or "five hour limit" in label):
            skipped += 1
            continue
        name, _, percent, reset = fields
        try:
            remaining = float(percent)
        except ValueError:
            skipped += 1
            continue
        if not 0.0 <= remaining <= 100.0:
            warnings.append("usage_percent_out_of_range: a line was dropped")
            continue
        if "five hour" in label:
            window = FIVE_HOUR
        elif "remaining" in label:
            window = WEEKLY
        else:
            window = MODEL_WEEKLY
        at = parse_instant(reset)
        if at is None:
            warnings.append("usage_reset_unreadable: a limit has no reset time")
        limits.append(AgyLimit(name, window, remaining, at))
    if skipped:
        warnings.append(f"usage_lines_skipped: {skipped}")
    if not limits:
        warnings.append("usage_no_limits_found")
    return limits, list(dict.fromkeys(warnings))
