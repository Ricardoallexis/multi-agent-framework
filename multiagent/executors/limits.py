# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared, pure pieces for reading a usage limit (CNT-T02, contract CNT-T01 sections 2 and 3).

The three adapters (Claude, Codex, Copilot) decide the *class* of a limit with their own message patterns; this module
holds what they share: reading a reset time out of a message, turning it into UTC, choosing between an exact epoch and
a clock time, and building the :class:`~multiagent.executors.contract.LimitInfo`. Nothing here reads a file, a
process or the user's application folders; the only outside inputs are the observation time and the machine zone,
both carried by :class:`LimitContext` so a test can fix them.

Reading a reset time (CNT-T01 section 3):

- ``reset_at`` is UTC. An exact epoch from the app (``app_epoch``) is ``high`` and beats any text.
- A clock time in the message (``resets 3:45pm``, ``try again at 4:12 PM``, ``resets Mon 12:00am``) is ``app_text`` and
  ``medium`` (decision 2: a time without a zone counts as medium). It is anchored to the observation instant T: the
  zone is the IANA name in the text (``(America/Mexico_City)``) or, when there is none, the machine zone; if the result
  is not after T one day is added (with a weekday, the next occurrence); a time of day that is missing, or a zone
  name this machine cannot resolve, lowers the confidence to ``low``. An ambiguous local time (clocks going back)
  takes its first occurrence (``fold=0``).
- A reset more than 8 days ahead is not trusted: a plan limit with only such a time becomes ``limit_unknown``.
- When the epoch and the text differ by more than 2 minutes the epoch wins and the note ``reset_mismatch`` is added.
- ``with_configured_reset`` marks the operator's fixed pause (``configured``, ``low``) for a limit with no usable time.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any

from .contract import (
    CONFIDENCE_OF_SOURCE, FailureKind, LimitInfo, LimitWindow, ResetConfidence, ResetSource,
)

MAX_RESET_AHEAD = timedelta(days=8)            # a time farther away than this is not believed
MISMATCH_SECONDS = 120                         # epoch vs text: more than this is noted as ``reset_mismatch``
EVIDENCE_CHARS = 240                           # the part of a message kept as evidence
MAX_SCAN_CHARS = 60_000                        # the part of a message that is searched (the end, where errors are)

_ZONE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2}")
_SECRETISH = re.compile(
    r"\b(?:gh[pousr]_|github_pat_|sk-|xox[abprs]-)[A-Za-z0-9_\-]{10,}|\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


# --------------------------------------------------------------------------- clock and zone

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_iana_zone(name: str) -> tzinfo | None:
    """The zone for an IANA name (``America/Mexico_City``), ``UTC`` and ``GMT``; ``None`` when this machine cannot resolve it.

    On Windows the IANA database is not part of the system: without the ``tzdata`` package a name other than UTC is not
    found, and the caller treats the time as "zone not resolved" (confidence ``low``) instead of guessing.
    """
    if not isinstance(name, str) or not _ZONE_NAME.fullmatch(name):
        return None
    if name.upper() in {"UTC", "GMT", "Z"}:
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                                  # not found, bad key or no database: all mean "cannot resolve"
        return None


@dataclass(frozen=True)
class LimitContext:
    """What a classification needs from the outside: the clock, the machine zone and the IANA resolver.

    ``zone`` is ``None`` for the machine zone, read through the operating system on each use so a daylight-saving change
    between now and the reset is honoured. A test passes a fixed ``tzinfo`` and its own resolver.
    """
    now: Callable[[], datetime] = utc_now
    zone: tzinfo | None = None
    resolve_zone: Callable[[str], tzinfo | None] = resolve_iana_zone

    def observed(self, observed_at: datetime | None = None) -> datetime:
        """The observation instant T as an aware UTC datetime (a naive value is read in the machine zone)."""
        value = observed_at if observed_at is not None else self.now()
        if value.tzinfo is None:
            value = value.astimezone()
        return value.astimezone(timezone.utc)


DEFAULT_CONTEXT = LimitContext()


def utc_iso(value: datetime) -> str:
    """``2026-10-11T22:12:32Z`` (whole seconds, UTC)."""
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label(zone: tzinfo | None, instant: datetime) -> str:
    """A short name for a zone: its IANA key when it has one, else the offset at ``instant`` as ``UTC-06:00``."""
    key = getattr(zone, "key", None)
    if isinstance(key, str) and key:
        return key
    offset = (instant.astimezone(zone) if zone is not None else instant.astimezone()).utcoffset() or timedelta(0)
    minutes = int(offset.total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    hours, rest = divmod(abs(minutes), 60)
    return "UTC" if minutes == 0 else f"UTC{sign}{hours:02d}:{rest:02d}"


def _wall_clock(instant: datetime, zone: tzinfo | None) -> datetime:
    """``instant`` as a naive wall-clock time in ``zone`` (``None`` = machine zone)."""
    local = instant.astimezone(zone) if zone is not None else instant.astimezone()
    return local.replace(tzinfo=None)


def _to_utc(wall: datetime, zone: tzinfo | None) -> datetime:
    """A naive wall-clock time in ``zone`` as UTC; an ambiguous time takes its first occurrence (``fold=0``)."""
    wall = wall.replace(fold=0)
    aware = wall.replace(tzinfo=zone) if zone is not None else wall
    return aware.astimezone(timezone.utc)


# --------------------------------------------------------------------------- evidence

def clip(text: object) -> str:
    """The end of a message, at most ``MAX_SCAN_CHARS`` characters (a non-text becomes empty)."""
    return text[-MAX_SCAN_CHARS:] if isinstance(text, str) else ""


def evidence_line(text: str, position: int) -> str:
    """The line of ``text`` that holds ``position``, collapsed, without secrets, at most ``EVIDENCE_CHARS``."""
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    line = text[start:end if end != -1 else len(text)]
    line = _SECRETISH.sub("[redacted]", _CONTROL.sub(" ", line))
    line = " ".join(line.split())
    return line if len(line) <= EVIDENCE_CHARS else line[:EVIDENCE_CHARS - 3] + "..."


# --------------------------------------------------------------------------- reading a reset time from text

_MARKER = r"(?:\bresets?\b(?:\s+(?:at|on))?|\btry\s+again\s+(?:at|on)|\bavailable\s+again\s+(?:at|on))"
_DOW = (r"(?P<dow>Mon(?:day)?|Tue(?:s(?:day)?)?|Wed(?:nesday)?|Thu(?:r(?:s(?:day)?)?)?|Fri(?:day)?|Sat(?:urday)?"
        r"|Sun(?:day)?)\b")
_MONTH = (r"(?P<month>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?"
          r"|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b")
_TIME = (r"(?:(?P<h12>\d{1,2})(?::(?P<m12>\d{2}))?\s*(?P<ap>[AaPp])\.?[Mm]\b\.?|(?P<h24>\d{1,2}):(?P<m24>\d{2})\b)")
_SPEC = re.compile(
    rf"\s*(?:{_DOW}\.?,?\s+)?"
    rf"(?:{_MONTH}\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{{4}}))?,?\s*(?:at\s+)?)?"
    rf"{_TIME}?"
    r"(?:\s*\((?P<zone>[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+){0,2})\))?", re.IGNORECASE)
_MARKER_RE = re.compile(_MARKER, re.IGNORECASE)
_RELATIVE = re.compile(
    r"\b(?:resets?|try\s+again|available\s+again|retry)\s+in\s+((?:\d+\s*(?:d(?:ays?)?|h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?"
    r"|s(?:ec(?:ond)?s?)?)\b[\s,]*(?:and\s+)?)+)", re.IGNORECASE)
_RELATIVE_PART = re.compile(r"(\d+)\s*(d|h|m|s)", re.IGNORECASE)
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTHS = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
EPOCH_AFTER_BAR = re.compile(r"\|\s*(\d{9,13})\b")                        # "Claude AI usage limit reached|1760000000"
EPOCH_IN_JSON = re.compile(r'"(?:resets_at|resetsAt)"\s*:\s*(\d{9,13})\b')


@dataclass(frozen=True)
class ResetText:
    """A reset time as the message wrote it (nothing resolved yet)."""
    hour: int | None = None                # 0-23; None when the message gives a date or weekday but no time of day
    minute: int | None = None
    weekday: int | None = None             # 0 = Monday
    month: int | None = None
    day: int | None = None
    year: int | None = None
    zone_name: str | None = None           # IANA name in parentheses, as written
    relative: timedelta | None = None      # "try again in 3 hours 4 minutes"
    position: int = 0                      # where the marker is in the text (for the evidence line)


def _hour_of(match: re.Match[str]) -> tuple[int | None, int | None]:
    if match.group("h12") is not None:
        hour, minute = int(match.group("h12")), int(match.group("m12") or 0)
        if not 1 <= hour <= 12 or minute > 59:
            return -1, -1
        hour = hour % 12 + (12 if match.group("ap").lower() == "p" else 0)
        return hour, minute
    if match.group("h24") is not None:
        hour, minute = int(match.group("h24")), int(match.group("m24"))
        return (hour, minute) if hour <= 23 and minute <= 59 else (-1, -1)
    return None, None


def find_reset_text(text: str) -> ResetText | None:
    """The first reset time a message states (``resets 3:45pm``, ``try again at 4:12 PM``, ``resets Mon 12:00am``,
    ``resets Oct 14``, ``try again at Mar 5th, 2026 4:12 PM``, ``resets 3:30am (America/Mexico_City)``,
    ``try again in 3 hours 4 minutes``), or ``None``."""
    text = clip(text)
    relative = _RELATIVE.search(text)
    for marker in _MARKER_RE.finditer(text):
        if relative is not None and relative.start() <= marker.start():
            break
        spec = _SPEC.match(text, marker.end())
        if spec is None:
            continue
        hour, minute = _hour_of(spec)
        if hour == -1:
            continue
        weekday = _WEEKDAYS[spec.group("dow")[:3].lower()] if spec.group("dow") else None
        month = _MONTHS[spec.group("month")[:3].lower()] if spec.group("month") else None
        if hour is None and weekday is None and month is None:
            continue
        return ResetText(hour=hour, minute=minute, weekday=weekday, month=month,
                         day=int(spec.group("day")) if spec.group("day") else None,
                         year=int(spec.group("year")) if spec.group("year") else None,
                         zone_name=spec.group("zone"), position=marker.start())
    if relative is not None:
        parts = _RELATIVE_PART.findall(relative.group(1))
        unit = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
        delta = timedelta(**{unit[u.lower()]: int(n) for n, u in parts}) if parts else None
        if delta:
            return ResetText(relative=delta, position=relative.start())
    return None


@dataclass(frozen=True)
class ResolvedText:
    """A :class:`ResetText` placed on the timeline."""
    at: datetime | None                    # UTC; None when the text cannot be placed
    zone: str | None
    confidence: ResetConfidence
    in_range: bool
    notes: tuple[str, ...] = ()


def resolve_reset_text(reset: ResetText, observed_at: datetime, context: LimitContext = DEFAULT_CONTEXT) -> ResolvedText:
    """Place a message time on the timeline, anchored to ``observed_at`` (UTC) as the module docstring says."""
    start = observed_at.astimezone(timezone.utc)
    notes: list[str] = []
    if reset.relative is not None:
        at = start + reset.relative
        return ResolvedText(at, "UTC", ResetConfidence.MEDIUM, start < at <= start + MAX_RESET_AHEAD)
    confidence = ResetConfidence.MEDIUM
    zone = context.zone
    label: str | None = None
    if reset.zone_name:
        found = context.resolve_zone(reset.zone_name)
        if found is not None:
            zone, label = found, reset.zone_name
        else:                                                # a zone this machine does not know: do not pretend
            confidence = ResetConfidence.LOW
            notes.append(f"zone_unresolved:{reset.zone_name}")
    hour, minute = (reset.hour, reset.minute) if reset.hour is not None else (0, 0)
    if reset.hour is None:
        confidence = ResetConfidence.LOW
        notes.append("time_missing")
    clock = time(hour, minute or 0)
    today = _wall_clock(start, zone).date()
    try:
        if reset.month is not None:
            year = reset.year or today.year
            wall_date = date(year, reset.month, reset.day or 1)
            at = _to_utc(datetime.combine(wall_date, clock), zone)
            if reset.year is None and at <= start:
                at = _to_utc(datetime.combine(date(year + 1, reset.month, reset.day or 1), clock), zone)
        elif reset.weekday is not None:
            wall_date = today + timedelta(days=(reset.weekday - today.weekday()) % 7)
            at = _to_utc(datetime.combine(wall_date, clock), zone)
            if at <= start:
                at = _to_utc(datetime.combine(wall_date + timedelta(days=7), clock), zone)
        else:
            at = _to_utc(datetime.combine(today, clock), zone)
            if at <= start:
                at = _to_utc(datetime.combine(today + timedelta(days=1), clock), zone)
    except (ValueError, OverflowError, OSError):             # 31 Feb, a year the platform cannot convert
        return ResolvedText(None, label, ResetConfidence.NONE, False, (*notes, "date_invalid"))
    return ResolvedText(at, label or _label(zone, at), confidence, start < at <= start + MAX_RESET_AHEAD, tuple(notes))


# --------------------------------------------------------------------------- epochs

def epoch_to_utc(value: object) -> datetime | None:
    """An epoch in seconds (milliseconds when it is clearly larger) as UTC; ``None`` for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, (int, float)) or value != value or value <= 0:
        return None
    seconds = value / 1000 if value > 1e11 else value
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# --------------------------------------------------------------------------- choosing the reset

@dataclass(frozen=True)
class ResetResult:
    reset_at: datetime | None = None
    source: ResetSource = ResetSource.NONE
    confidence: ResetConfidence = ResetConfidence.NONE
    zone: str | None = None
    out_of_range: bool = False             # something was stated but cannot be believed (too far, or already past)
    notes: tuple[str, ...] = ()


def resolve_reset(*, observed_at: datetime, context: LimitContext = DEFAULT_CONTEXT, epoch: object = None,
                  text: ResetText | None = None) -> ResetResult:
    """The best reset from an exact epoch and/or a message time; the epoch wins, the text is the fallback."""
    start = observed_at.astimezone(timezone.utc)
    notes: list[str] = []
    placed = resolve_reset_text(text, start, context) if text is not None else None
    exact = epoch_to_utc(epoch)
    far = False
    if exact is not None:
        if start < exact <= start + MAX_RESET_AHEAD:
            if placed is not None and placed.at is not None and abs((placed.at - exact).total_seconds()) > MISMATCH_SECONDS:
                notes.append("reset_mismatch")
            return ResetResult(exact, ResetSource.APP_EPOCH, CONFIDENCE_OF_SOURCE[ResetSource.APP_EPOCH], "UTC",
                               notes=tuple(notes))
        notes.append("epoch_stale" if exact <= start else "epoch_out_of_range")
        far = exact > start
    elif epoch is not None:
        notes.append("epoch_invalid")
    if placed is not None:
        notes.extend(placed.notes)
        if placed.at is not None and placed.in_range:
            return ResetResult(placed.at, ResetSource.APP_TEXT, placed.confidence, placed.zone, notes=tuple(notes))
        notes.append("reset_out_of_range")
        return ResetResult(out_of_range=True, notes=tuple(notes))
    return ResetResult(out_of_range=far, notes=tuple(notes))


def next_month_start(observed_at: datetime) -> datetime:
    """The next first day of a month at 00:00:00 UTC strictly after ``observed_at`` (Copilot's documented reset)."""
    start = observed_at.astimezone(timezone.utc)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- building a LimitInfo

def make_limit(kind: FailureKind, window: LimitWindow, *, observed_at: datetime, context: LimitContext = DEFAULT_CONTEXT,
               evidence: str = "", epoch: object = None, text: ResetText | None = None, wants_reset: bool = True,
               rule_reset: datetime | None = None, notes: tuple[str, ...] = ()) -> LimitInfo:
    """The :class:`LimitInfo` of a classified message.

    ``wants_reset`` is false for ``rate_limited`` (a short retry, no time). ``rule_reset`` is a documented reset used only
    when the app gave neither epoch nor text (``rule``, ``medium``). A plan limit whose only time is out of range
    becomes ``limit_unknown`` (CNT-T01 section 3, step 5).
    """
    found = ResetResult()
    if wants_reset:
        found = resolve_reset(observed_at=observed_at, context=context, epoch=epoch, text=text)
    reset_at, source, confidence, zone = found.reset_at, found.source, found.confidence, found.zone
    if reset_at is None and wants_reset and rule_reset is not None and not found.out_of_range:
        reset_at, source, zone = rule_reset, ResetSource.RULE, "UTC"
        confidence = CONFIDENCE_OF_SOURCE[ResetSource.RULE]
    if found.out_of_range and kind is FailureKind.PLAN_LIMIT:
        kind = FailureKind.LIMIT_UNKNOWN
    return LimitInfo(kind=kind, window=window, reset_at=utc_iso(reset_at) if reset_at is not None else None,
                     reset_source=source, reset_confidence=confidence, reset_zone=zone, evidence=evidence,
                     notes=(*notes, *found.notes))


def with_configured_reset(limit: LimitInfo, *, observed_at: datetime, pause_minutes: int) -> LimitInfo:
    """``limit`` with the operator's fixed pause as its reset when it has none (``configured``, ``low``).

    For a plan limit or an unknown limit that gave no usable time (CNT-T04 uses it with ``executor_quota_pause_minutes``);
    a limit that already has a reset is returned as it is.
    """
    if limit.reset_at is not None or pause_minutes <= 0:
        return limit
    at = observed_at.astimezone(timezone.utc) + timedelta(minutes=pause_minutes)
    return replace(limit, reset_at=utc_iso(at), reset_source=ResetSource.CONFIGURED,
                   reset_confidence=CONFIDENCE_OF_SOURCE[ResetSource.CONFIGURED], reset_zone="UTC")


def number_of(value: object) -> float | None:
    """A finite non-negative number (a bool is not one), else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return float(value)


def mapping_or_none(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


__all__ = [
    "DEFAULT_CONTEXT", "EPOCH_AFTER_BAR", "EPOCH_IN_JSON", "LimitContext", "MAX_RESET_AHEAD", "MISMATCH_SECONDS",
    "ResetResult", "ResetText", "ResolvedText", "clip", "epoch_to_utc", "evidence_line", "find_reset_text",
    "make_limit", "mapping_or_none", "next_month_start", "number_of", "resolve_iana_zone", "resolve_reset",
    "resolve_reset_text", "utc_iso", "utc_now", "with_configured_reset",
]
