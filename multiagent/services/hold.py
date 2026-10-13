# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The hold of a task that waits (CNT-T04, contract ``CNT-T01-contrato.md`` sections 4 and 10).

A held task stays ``paused``; no state is added and no migration is needed. The wait lives in the reserved
``tasks.execution_json`` as ``{"continuity": {"hold": {...}, ...}}``. This module is pure (no database, no clock of its
own, no process): it decides *how long* a limit holds a task and *when* a hold is due, and builds and merges the
blocks. ``ExecutorServices`` stores them and acts on them.

The ``continuity`` block::

    {"hold": {...},              # present while the task waits
     "failed_resumes": 0,        # consecutive automatic resumptions that hit a limit again (3 -> a person decides)
     "rate_retries": 0,          # automatic retries used in the current streak of ``rate_limited``
     "config_drift": [...],      # what an earlier resume reported as different from what was asked (relaunch then)
     "handoffs": 0, "chain": []} # owned by the hand-off (CNT-T05); T04 only gives them a first value

The ``hold``:

- ``reason`` is why the task waits. It is one of ``LIMIT_REASONS`` (the four limit classes of CNT-T02) or, for a wait by
  condition (CNT-T12, ``waits.py``), ``dependency``, ``integration_window`` or ``approval``. ``is_limit_hold`` tells them
  apart. A hold that carries a ``condition`` block (``is_condition_hold``), whatever its reason, is released by
  ``resume_met`` after the condition is checked again, never by ``resume_due``; ``hold_due`` says ``has_condition`` for it
  unless the caller has already checked the condition and passes ``condition_met``.
- ``window``, ``reset_at``, ``reset_source``, ``reset_confidence``, ``reset_zone``: what the limit said (UTC, with source
  and confidence).
- ``resume_after``: when the wait ends (UTC ``...Z``); ``None`` with ``open_ended`` true when only a person ends it.
- ``outcome``: ``wait_until_reset`` (a reliable reset time plus the 2 minute margin), ``wait_fixed_pause`` (no reliable
  time: the operator's fixed pause), ``wait_retry`` (a short backoff after ``rate_limited``) or ``open_ended``
  (``credits_exhausted``: no end until the person lifts the pause, decision 3).
- ``executor_id``, ``actor`` (the agent that owned the task: only it is resumed), ``session_id``, ``attempt``,
  ``checkpoint`` (the patch blob, ``None`` without one), ``settings`` (model, effort, permission and adapter the session
  was started with: everything a resume must pass again), ``pause_seq`` (the ``executor.paused`` event that goes with it),
  ``since``, ``mode`` and ``decision`` (``pending`` until the hand-off, CNT-T05, decides).

Reset time rules (CNT-T01 section 3 and decision 3):

- ``resume_after = reset_at + 2 minutes`` when the time is ``high`` or ``medium`` and not more than 8 days ahead;
- otherwise the operator's fixed pause is used (``with_configured_reset``: source ``configured``, confidence ``low``),
  and an unreliable time the app did give is kept as ``reported_reset_at``;
- ``credits_exhausted`` never ends by itself;
- ``rate_limited`` retries after 60 s and then 300 s; when those are used the fixed pause applies.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..executors.contract import FailureKind, LimitInfo, LimitWindow, ResetConfidence
from ..executors.limits import MAX_RESET_AHEAD, utc_iso, with_configured_reset

__all__ = [
    "DEFAULT_MODE", "DRIFT_KEYS", "HoldPlan", "LIMIT_REASONS", "MAX_FAILED_RESUMES", "RATE_LIMIT_BACKOFF",
    "RESUME_MARGIN", "build_hold", "condition_of", "continuity_of", "detect_drift", "hold_due", "hold_of",
    "is_condition_hold", "is_limit_hold", "merge_continuity", "parse_utc", "plan_hold",
]

RESUME_MARGIN = timedelta(minutes=2)             # resume_after = reset_at + this (CNT-T01 section 3)
RATE_LIMIT_BACKOFF = (60, 300)                   # seconds before the first and the second automatic retry of a 429
MAX_FAILED_RESUMES = 3                           # consecutive failed resumptions after which a person decides (section 8)
DEFAULT_MODE = "semi_auto"                       # the continuity mode until the policy (CNT-T05) supplies one
LIMIT_REASONS = ("rate_limited", "plan_limit", "credits_exhausted", "limit_unknown")
RELIABLE = (ResetConfidence.HIGH.value, ResetConfidence.MEDIUM.value)
DRIFT_KEYS = ("model", "effort", "permission", "adapter", "workdir")
_EVIDENCE_CHARS = 160


def parse_utc(value: Any) -> datetime | None:
    """An ISO time (``...Z`` or with an offset; a naive one is read as UTC) as an aware UTC datetime, else ``None``."""
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


@dataclass(frozen=True)
class HoldPlan:
    """How long a limit holds a task, before the task and the executor are written."""
    reason: str
    window: str
    outcome: str
    resume_after: datetime | None              # ``None`` when open ended
    open_ended: bool = False
    reset_at: str | None = None                # what the hold records as the reset (the configured one when none was reliable)
    reset_source: str | None = None
    reset_confidence: str | None = None
    reset_zone: str | None = None
    reported_reset_at: str | None = None       # an unreliable time the app gave, replaced by the fixed pause
    retry: dict[str, int] | None = None        # ``{"n": 1, "of": 2}`` for a ``rate_limited`` retry
    retries_left: int = 0                      # automatic retries that remain after this limit (``rate_limited`` only)
    evidence: str = ""

    @property
    def until(self) -> datetime | None:
        """When the executor stops being paused (the same instant as ``resume_after``)."""
        return self.resume_after


def _limit_info(info: Mapping[str, Any], reason: str) -> LimitInfo:
    """A ``LimitInfo`` without a reset, to ask ``with_configured_reset`` for the operator's pause."""
    window = info.get("window")
    return LimitInfo(kind=FailureKind(reason),
                     window=LimitWindow(window) if window in {item.value for item in LimitWindow} else LimitWindow.UNKNOWN)


def plan_hold(info: Mapping[str, Any], *, now: datetime, configured_minutes: int, rate_retries: int = 0) -> HoldPlan:
    """What a limit does to a task: wait until a time, wait with no end, or retry soon.

    ``info`` is the limit as ``checkpoint.normalize_limit`` returns it (``kind``, ``window``, ``reset_at`` and its source,
    confidence and zone, ``evidence``). ``now`` is the observation instant, ``configured_minutes`` the operator's fixed
    pause (``executor_quota_pause_minutes``) and ``rate_retries`` the automatic retries a streak of ``rate_limited`` has
    used so far.
    """
    now = now.astimezone(timezone.utc)
    reason = info.get("kind") if info.get("kind") in LIMIT_REASONS else "limit_unknown"
    window = str(info.get("window") or "unknown")
    evidence = str(info.get("evidence") or "")[:_EVIDENCE_CHARS]
    reported = info.get("reset_at")
    common = {"reason": reason, "window": window, "evidence": evidence}

    if reason == "credits_exhausted":
        # Decision 3: no end until a person lifts the pause. The time the app may have given is only information.
        return HoldPlan(outcome="open_ended", resume_after=None, open_ended=True, reset_at=reported,
                        reset_source=info.get("reset_source"), reset_confidence=info.get("reset_confidence"),
                        reset_zone=info.get("reset_zone"), **common)

    retries_left = 0
    if reason == "rate_limited":
        used = max(int(rate_retries), 0)
        retries_left = max(len(RATE_LIMIT_BACKOFF) - used, 0)
        if retries_left:
            return HoldPlan(outcome="wait_retry", resume_after=now + timedelta(seconds=RATE_LIMIT_BACKOFF[used]),
                            retry={"n": used + 1, "of": len(RATE_LIMIT_BACKOFF)}, retries_left=retries_left, **common)

    reset = parse_utc(reported)
    if (reset is not None and info.get("reset_confidence") in RELIABLE and reset <= now + MAX_RESET_AHEAD
            and reason != "rate_limited"):
        return HoldPlan(outcome="wait_until_reset", resume_after=reset + RESUME_MARGIN, reset_at=utc_iso(reset),
                        reset_source=info.get("reset_source"), reset_confidence=info.get("reset_confidence"),
                        reset_zone=info.get("reset_zone"), **common)

    # No reliable time: the operator's fixed pause, marked as such. A time the app gave but we do not trust is kept apart.
    fixed = with_configured_reset(_limit_info(info, reason), observed_at=now, pause_minutes=int(configured_minutes))
    resume = parse_utc(fixed.reset_at)
    return HoldPlan(outcome="wait_fixed_pause", resume_after=resume, reset_at=fixed.reset_at,
                    reset_source=fixed.reset_source.value, reset_confidence=fixed.reset_confidence.value,
                    reset_zone=fixed.reset_zone, reported_reset_at=reported if reported else None,
                    retries_left=0, **common)


# --------------------------------------------------------------------------- the block

def build_hold(plan: HoldPlan, *, now: datetime, executor_id: str, actor: str, session_id: str | None, attempt: int,
               settings: Mapping[str, Any], checkpoint: str | None = None, pause_seq: int | None = None,
               mode: str = DEFAULT_MODE, blocked: str | None = None) -> dict[str, Any]:
    """The ``continuity.hold`` block of a plan (contract section 4, plus ``settings``, ``outcome`` and ``since``)."""
    hold: dict[str, Any] = {
        "reason": plan.reason, "window": plan.window, "outcome": plan.outcome,
        "reset_at": plan.reset_at, "reset_source": plan.reset_source, "reset_confidence": plan.reset_confidence,
        "reset_zone": plan.reset_zone,
        "resume_after": utc_iso(plan.resume_after) if plan.resume_after is not None else None,
        "open_ended": plan.open_ended, "executor_id": executor_id, "actor": actor, "session_id": session_id,
        "attempt": attempt, "checkpoint": checkpoint, "mode": mode, "decision": "pending",
        "settings": {name: settings.get(name) for name in ("model", "effort", "permission", "adapter", "workdir")
                     if settings.get(name) is not None},
        "pause_seq": pause_seq, "since": utc_iso(now)}
    if plan.retry:
        hold["retry"] = dict(plan.retry)
    if plan.reported_reset_at:
        hold["reported_reset_at"] = plan.reported_reset_at
    if plan.evidence:
        hold["evidence"] = plan.evidence
    if blocked:
        hold["blocked"] = blocked
    return hold


def continuity_of(task: Mapping[str, Any]) -> dict[str, Any]:
    """The ``continuity`` block of a task (an empty one when it has none or it is malformed)."""
    execution = task.get("execution")
    block = execution.get("continuity") if isinstance(execution, Mapping) else None
    return dict(block) if isinstance(block, Mapping) else {}


def hold_of(task: Mapping[str, Any]) -> dict[str, Any] | None:
    """The hold of a task, or ``None``."""
    hold = continuity_of(task).get("hold")
    return dict(hold) if isinstance(hold, Mapping) and hold else None


def is_limit_hold(hold: Mapping[str, Any] | None) -> bool:
    """True when the hold waits for a usage limit; any other reason belongs to another watcher (CNT-T12)."""
    return bool(hold) and hold.get("reason") in LIMIT_REASONS      # type: ignore[union-attr]


def condition_of(hold: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The ``condition`` block of a hold (CNT-T12), or ``None``."""
    block = hold.get("condition") if isinstance(hold, Mapping) else None
    return dict(block) if isinstance(block, Mapping) and block else None


def is_condition_hold(hold: Mapping[str, Any] | None) -> bool:
    """True when the hold carries a condition: ``resume_met`` releases it, not ``resume_due``."""
    return condition_of(hold) is not None


def merge_continuity(execution: Mapping[str, Any] | None, changes: Mapping[str, Any]) -> dict[str, Any]:
    """``execution`` with ``changes`` merged into its ``continuity`` block; a ``None`` value removes that key.

    The other keys of ``execution`` (a sibling block such as the attention of CNT-T09) are kept as they are, and an
    empty ``continuity`` block is dropped.
    """
    merged = dict(execution) if isinstance(execution, Mapping) else {}
    block = dict(merged.get("continuity") or {}) if isinstance(merged.get("continuity"), Mapping) else {}
    for key, value in changes.items():
        if value is None:
            block.pop(key, None)
        else:
            block[key] = value
    if block:
        merged["continuity"] = block
    else:
        merged.pop("continuity", None)
    return merged


# --------------------------------------------------------------------------- due and drift

def hold_due(hold: Mapping[str, Any], *, now: datetime, executor_paused: bool, lifted: bool = False,
             condition_met: bool | None = None) -> tuple[bool, str]:
    """Whether a limit hold may be resumed now, and why not when it may not: ``(due, reason)``.

    - a hold with a condition (CNT-T12) is not due for ``resume_due`` (``has_condition``); a caller that has checked the
      condition passes ``condition_met`` and then the hold is due only when it holds (``condition_pending`` otherwise);
    - another pause that is still in force on the executor waits (``executor_paused``);
    - a pause lifted by a person since the hold makes it due now (``lifted``), whatever its time;
    - an open ended hold (``credits_exhausted``) is due only that way;
    - otherwise it is due when ``resume_after`` has passed (``reset_reached``) and not before (``not_due``).
    """
    if not is_limit_hold(hold):
        return False, "not_a_limit_hold"
    if hold.get("blocked"):
        return False, f"blocked:{hold['blocked']}"
    if is_condition_hold(hold):
        if condition_met is None:
            return False, "has_condition"
        if not condition_met:
            return False, "condition_pending"
    if executor_paused:
        return False, "executor_paused"
    if lifted:
        return True, "lifted"
    if hold.get("open_ended") or hold.get("resume_after") is None:
        return False, "open_ended"
    after = parse_utc(hold.get("resume_after"))
    if after is None:
        return False, "resume_after_invalid"
    return (True, "reset_reached") if now.astimezone(timezone.utc) >= after else (False, "not_due")


def detect_drift(recorded: Mapping[str, Any], current: Mapping[str, Any], keys: Sequence[str] = DRIFT_KEYS) -> list[str]:
    """The settings whose ``current`` value differs from the ``recorded`` one, in the order of ``keys``.

    A name missing from ``recorded`` or ``current`` is unknown and is not drift; a name present in ``current`` with another
    value, ``None`` included (the app fell back to its default), is. Used before a resume (what the service would
    pass now against what the session started with) and after it (what the app reported as effective).
    """
    return [name for name in keys
            if recorded.get(name) is not None and name in current and current[name] != recorded[name]]
