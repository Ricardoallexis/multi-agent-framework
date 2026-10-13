# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Waits by condition (CNT-T12, contract ``CNT-T01-contrato.md`` Amendment 2): the pure core.

A task can wait for something other than a usage limit. Its hold (``continuity.hold`` in ``tasks.execution_json``, see
``hold.py``) then carries a sibling block, ``hold.condition``, and a ``reason`` that says what the wait is for. This module
decides, with no model of any kind, *whether the condition holds now* and *how far along the wait honestly is*. It is
pure: no database, no clock of its own, no file system, no process. Everything it reads comes in through a
:class:`WaitFacts` that the caller gathers (``ExecutorServices`` from the task store, a watcher process from whatever it
can read), so a test fixes the time and the world and gets the same answer every time.

The ``reason`` of a hold is one of ``hold.LIMIT_REASONS`` (a usage limit, CNT-T04) or one of ``WAIT_REASONS``:

- ``dependency``: tasks that must be ``accepted`` (or ``submitted``) first, with the rule of ``tasks/graph.py``;
- ``integration_window``: an integration that must finish well (a window that opens, runs and closes);
- ``approval``: a person's decision, never one a program makes.

The ``condition`` block::

    {"type": "dependency",                    # time | dependency | integration_window | approval | all
     "params": {"task_ids": ["T1"], "requires": "accepted"},
     "since": "...Z", "deadline_at": "...Z",  # the wait began; it gives up at the deadline (default 12 hours)
     "met_at": None,                          # the instant the condition was seen to hold (the only way to 100 %)
     "expired_at": None,                      # the instant the deadline passed with the condition unmet
     "checked_at": None, "next_check_at": None, "estimate": None,   # the last check, as a watcher reported it
     "watcher_id": None,                      # the process that watches it (CNT-T13)
     "decisions": {}}                         # approvals decided so far: {approval_id: {status, by, at}}

``all`` is several conditions that must all hold: ``params.conditions`` is a list of leaves, each with its own ``type``,
``params`` and ``met_at``. A limit hold (``plan_limit`` ...) may also carry a condition: its own reset time is then one
more thing that must hold, and ``resume_met`` checks both.

Checkers (one per type) answer ``met``, ``blocked`` (the reason waiting cannot help, for example a task that failed),
and an honest progress. Progress (Amendment 2):

- ``time`` is exact: elapsed over total, with the end as the estimate. A time that came from a limit and is not ``high``
  is approximate and says so.
- ``integration_window`` has two phases. Until it starts there is no percentage at all. Once it runs, the estimate is the
  median of the last five windows with their usual band (the quartiles), capped at 95 %; past the median the time is
  dropped and the percentage freezes; past the 90th percentile it says it is taking longer than usual. With fewer than
  three earlier windows there is no estimate.
- anything that depends on an agent or a person (``dependency``, ``approval``) has no estimate. A dependency may say it
  cannot finish *before* a time (``not_before``) when the task it waits for is itself waiting for one.
- with several conditions the unknown one dominates: if any pending condition has no estimate, the wait has none;
  otherwise it is the estimate of the slowest pending one.
- 100 % exists only with ``met_at``. A percentage never goes without its ``basis`` (a stable code; the interfaces write
  the words). An estimate that gets worse is marked ``revised`` instead of silently going backwards.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from ..executors.limits import utc_iso
from ..tasks import graph
from .hold import DEFAULT_MODE, LIMIT_REASONS, hold_due, is_limit_hold, parse_utc

__all__ = [
    "ALL", "APPROVAL", "CHECKERS", "CHECK_INTERVAL", "CONDITION_TYPES", "DEFAULT_DEADLINE", "DEPENDENCY",
    "EVENT_BUDGET", "HOLD_REASONS", "INTEGRATION_WINDOW", "MAX_DEADLINE", "ApprovalFact", "Check", "Evaluation",
    "IntegrationFacts", "PartResult", "Progress", "TIME", "WAIT_REASONS", "WaitError", "WaitFacts", "apply_evaluation",
    "build_wait_hold", "check_approval", "check_dependency", "check_integration_window", "check_time", "check_token",
    "combine_progress", "condition_leaves", "expired_payload", "evaluate", "integration_progress", "mark_revised",
    "met_payload",
    "normalize_condition", "not_before_of_hold", "percentile", "reason_for", "summarize", "wait_due",
]

TIME, DEPENDENCY, INTEGRATION_WINDOW, APPROVAL = "time", "dependency", "integration_window", "approval"
CONDITION_TYPES = (TIME, DEPENDENCY, INTEGRATION_WINDOW, APPROVAL)
ALL = "all"                                           # several conditions that must all hold
WAIT_REASONS = (DEPENDENCY, INTEGRATION_WINDOW, APPROVAL)
HOLD_REASONS = (*LIMIT_REASONS, *WAIT_REASONS)

CHECK_INTERVAL = timedelta(seconds=120)               # how often a watcher looks (the next check is never later than this)
DEFAULT_DEADLINE = timedelta(hours=12)                # a wait gives up after this, apart from its progress
MAX_DEADLINE = timedelta(days=9)                      # a limit reset may be 8 days away (limits.MAX_RESET_AHEAD) plus margin
MAX_PARTS = 4                                         # conditions in one ``all``
MAX_TASK_IDS = 8                                      # tasks one dependency waits for
MIN_SAMPLES = 3                                       # earlier windows needed before an integration is estimated
RECENT_ROUNDS = 5                                     # earlier windows the estimate looks at
MAX_WINDOW_SECONDS = 4 * 3600                         # an earlier window longer than this is not a typical sample
TIME_CAP = 99                                         # a time that is not reached yet is at most 99 %
APPROXIMATE_CAP = 95                                  # an estimate that is not exact never goes past 95 %
REVISION_TOLERANCE = timedelta(seconds=60)            # an estimate that moves later by more than this is marked revised
EVENT_BUDGET = 1900                                   # the team event payload limit is 2048 bytes
CONFIDENCES = ("high", "medium", "low", "none")

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DEAD_STATES = ("failed", "cancelled")                # a task that ends here never satisfies a dependency


class WaitError(ValueError):
    """A condition that is malformed; ``field`` says which part."""

    def __init__(self, field_name: str, message: str):
        super().__init__(message)
        self.field = field_name


# --------------------------------------------------------------------------- what the checkers read

@dataclass(frozen=True)
class IntegrationFacts:
    """The latest run of an integration window, as the caller read it from wherever integrations are recorded.

    ``started_at`` is when the window opened (``None``: it has not started); ``ended_at`` and ``outcome`` (``ok`` or
    ``failed``) say how it closed (``None``: still open). ``durations`` are the seconds earlier windows took, oldest
    first; only the good ones should be given, they are what the estimate is made from.
    """
    started_at: datetime | None = None
    ended_at: datetime | None = None
    outcome: str | None = None
    durations: tuple[float, ...] = ()


@dataclass(frozen=True)
class ApprovalFact:
    """A decision about an approval: ``status`` is ``approved``, ``rejected`` or ``pending``."""
    status: str = "pending"
    by: str | None = None
    decided_at: datetime | None = None


@dataclass(frozen=True)
class WaitFacts:
    """Everything the checkers may read. ``now`` is the observation instant; the rest is the world at that instant.

    ``task_states`` maps a task id to its stored state (a task that is missing is not there); ``not_before`` maps a task id
    to the earliest instant it can possibly get going again (a task that waits for a time); ``integrations`` maps a window
    id to its latest run; ``approvals`` maps an approval id to its decision.
    """
    now: datetime
    task_states: Mapping[str, str] = field(default_factory=dict)
    not_before: Mapping[str, datetime] = field(default_factory=dict)
    integrations: Mapping[str, IntegrationFacts] = field(default_factory=dict)
    approvals: Mapping[str, ApprovalFact] = field(default_factory=dict)


# --------------------------------------------------------------------------- progress

@dataclass(frozen=True)
class Progress:
    """How far along a wait honestly is. ``percent`` ``None`` means there is no estimate.

    ``kind`` is ``exact``, ``approximate``, ``none`` or ``met``. ``basis`` is a stable code saying how the figure was
    reached (or why there is none) and ``detail`` carries the numbers behind it, so an interface writes the words and
    nobody shows a percentage without its basis. ``frozen`` says the percentage is being held while the work takes longer
    than usual; ``revised`` says the estimate got worse since the last check. 100 % exists only with ``met_at``.
    """
    percent: int | None
    eta: datetime | None
    kind: str
    basis: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    frozen: bool = False
    revised: bool = False
    met_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.percent is not None and not 0 <= self.percent <= 100:
            raise ValueError("percent must be between 0 and 100")
        if (self.percent == 100) != (self.met_at is not None):
            raise ValueError("a wait is at 100 % only with met_at (and only then)")

    @classmethod
    def unknown(cls, basis: str, **detail: Any) -> "Progress":
        """No estimate: it depends on someone, nothing started yet or there is nothing to estimate from."""
        return cls(None, None, "none", basis, dict(detail))

    @classmethod
    def done(cls, met_at: datetime) -> "Progress":
        return cls(100, met_at, "met", "met", {}, met_at=met_at)

    def to_dict(self) -> dict[str, Any]:
        return {"percent": self.percent, "eta": utc_iso(self.eta) if self.eta else None, "kind": self.kind,
                "basis": self.basis, "detail": dict(self.detail), "frozen": self.frozen, "revised": self.revised,
                "met_at": utc_iso(self.met_at) if self.met_at else None}


def percentile(values: Sequence[float], p: float) -> float:
    """The ``p`` (0..1) percentile of ``values``, with linear interpolation between the two nearest ones."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    h = (len(ordered) - 1) * p
    low, high = math.floor(h), math.ceil(h)
    return ordered[low] + (h - low) * (ordered[high] - ordered[low])


def _percent(elapsed: float, total: float, cap: int) -> int:
    if total <= 0:
        return cap
    return int(max(0, min(cap, math.floor(100 * elapsed / total))))


def integration_progress(window: IntegrationFacts, now: datetime) -> Progress:
    """The two phases of an integration that is running (``window.started_at`` is set).

    Fewer than ``MIN_SAMPLES`` earlier windows: no estimate. Otherwise the estimate is the median of the last
    ``RECENT_ROUNDS`` with their quartile band; the percentage is elapsed over the median, never past 95. Past the median
    the end time is dropped and 95 % is held; past the 90th percentile it says it takes longer than usual.
    """
    started = window.started_at
    samples = [float(item) for item in window.durations
               if isinstance(item, (int, float)) and not isinstance(item, bool) and 0 < item <= MAX_WINDOW_SECONDS]
    recent = samples[-RECENT_ROUNDS:]
    if started is None:
        return Progress.unknown("integration_not_started")
    if len(recent) < MIN_SAMPLES:
        return Progress.unknown("integration_few_samples", have=len(recent), need=MIN_SAMPLES, started_at=utc_iso(started))
    elapsed = max((now - started).total_seconds(), 0.0)
    median, p90 = percentile(recent, 0.5), percentile(recent, 0.9)
    detail = {"started_at": utc_iso(started), "samples": len(recent), "median_s": round(median),
              "band_s": [round(percentile(recent, 0.25)), round(percentile(recent, 0.75))], "p90_s": round(p90)}
    if elapsed >= p90:
        return Progress(APPROXIMATE_CAP, None, "approximate", "integration_longer_than_usual", detail, frozen=True)
    if elapsed >= median:
        return Progress(APPROXIMATE_CAP, None, "approximate", "integration_over_median", detail, frozen=True)
    return Progress(_percent(elapsed, median, APPROXIMATE_CAP), started + timedelta(seconds=median), "approximate",
                    "integration_median", detail)


def mark_revised(previous: Mapping[str, Any] | None, current: Progress) -> Progress:
    """``current``, marked ``revised`` when it is worse than the ``previous`` estimate (as ``Progress.to_dict`` wrote it).

    Worse is a lower percentage, an end time that moved later by more than a minute, or an estimate that disappeared.
    The figure is not changed: the estimate does not go backwards in silence, it says it was revised.
    """
    if not isinstance(previous, Mapping) or current.kind == "met":
        return current
    before_percent, before_eta = previous.get("percent"), parse_utc(previous.get("eta")) if previous.get("eta") else None
    worse = (isinstance(before_percent, int) and (current.percent is None or current.percent < before_percent)) or (
        before_eta is not None and current.eta is not None and current.eta > before_eta + REVISION_TOLERANCE)
    return replace(current, revised=True) if worse else current


# --------------------------------------------------------------------------- the checkers

@dataclass(frozen=True)
class Check:
    """What a checker answers about one condition: ``progress`` is ``None`` only when it is met."""
    met: bool
    detail: str
    progress: Progress | None = None
    blocked: str | None = None
    info: Mapping[str, Any] = field(default_factory=dict)


def _moment(value: Any, field_name: str) -> datetime:
    moment = parse_utc(value)
    if moment is None:
        raise WaitError(field_name, f"{field_name} is not a time")
    return moment


def check_time(params: Mapping[str, Any], since: datetime, facts: WaitFacts) -> Check:
    """Met when ``params.at`` has come. Exact: elapsed over total, the end as the estimate.

    A time that came from a limit (``params.confidence`` other than ``high``) is approximate and says so.
    """
    at = _moment(params.get("at"), "params.at")
    if facts.now >= at:
        return Check(True, "reached")
    confidence = params.get("confidence")
    exact = confidence in (None, "high")
    detail = {"at": utc_iso(at), **({} if exact else {"confidence": confidence, "source": params.get("source")})}
    percent = _percent((facts.now - since).total_seconds(), (at - since).total_seconds(), TIME_CAP)
    return Check(False, "waiting_for_time",
                 Progress(percent, at, "exact" if exact else "approximate", "exact_time" if exact else "time_from_limit",
                          detail))


def check_dependency(params: Mapping[str, Any], since: datetime, facts: WaitFacts) -> Check:
    """Met when every task in ``params.task_ids`` satisfies ``params.requires`` (``tasks/graph.is_satisfied``).

    A task that is missing, failed or cancelled can never satisfy it by waiting: the check says so in ``blocked``.
    There is never an estimate (the work belongs to an agent); when a pending task is itself waiting for a time the
    detail gives the ``not_before`` instant, which is a floor, not a forecast.
    """
    requires = params.get("requires", "accepted")
    ids = list(params.get("task_ids") or [])
    pending, missing, dead = [], [], []
    for task_id in ids:
        state = facts.task_states.get(task_id)
        try:
            if graph.is_satisfied(requires, state):
                continue
        except graph.GraphError as exc:
            raise WaitError("params.requires", str(exc)) from None
        pending.append(task_id)
        if state is None:
            missing.append(task_id)
        elif state in _DEAD_STATES:
            dead.append(task_id)
    if not pending:
        return Check(True, "satisfied")
    floors = [facts.not_before[task_id] for task_id in pending if task_id in facts.not_before]
    detail: dict[str, Any] = {"tasks": pending[:MAX_TASK_IDS]}
    if floors:
        detail["not_before"] = utc_iso(max(floors))
    blocked = "dependency_missing" if missing else ("dependency_failed" if dead else None)
    return Check(False, "waiting_for_tasks", Progress.unknown("depends_on_agent", **detail), blocked=blocked,
                 info={"pending": pending[:MAX_TASK_IDS]})


def check_integration_window(params: Mapping[str, Any], since: datetime, facts: WaitFacts) -> Check:
    """Met when the window closed well (``outcome`` ``ok``) after ``params.after`` (default: when the wait began).

    No percentage until the window starts; once it runs, the two-phase estimate of ``integration_progress``. A window that
    closed badly is not met: the wait goes on for the next one.
    """
    window = facts.integrations.get(str(params.get("window")))
    after = _moment(params["after"], "params.after") if params.get("after") else since
    if window is None:
        return Check(False, "no_evidence", Progress.unknown("integration_unknown"))
    if window.ended_at is not None and window.outcome == "ok" and window.ended_at > after and not (
            window.started_at is not None and window.started_at > window.ended_at):
        return Check(True, "closed_ok")
    running = window.started_at is not None and (window.ended_at is None or window.started_at > window.ended_at)
    if running:
        return Check(False, "running", integration_progress(window, facts.now), info={"started_at": utc_iso(window.started_at)})
    if window.ended_at is not None and window.ended_at > after and window.outcome == "failed":
        return Check(False, "last_failed", Progress.unknown("integration_not_started", last="failed"))
    return Check(False, "not_started", Progress.unknown("integration_not_started"))


def check_approval(params: Mapping[str, Any], since: datetime, facts: WaitFacts) -> Check:
    """Met when the approval was decided ``approved``. A rejection cannot be waited out. No estimate, ever: it is a person's."""
    fact = facts.approvals.get(str(params.get("approval")))
    if fact is not None and fact.status == "approved":
        return Check(True, "approved", info={"by": fact.by})
    if fact is not None and fact.status == "rejected":
        return Check(False, "rejected", Progress.unknown("needs_a_decision", waiting_since=utc_iso(since)),
                     blocked="approval_rejected", info={"by": fact.by})
    return Check(False, "awaiting_decision", Progress.unknown("needs_a_decision", waiting_since=utc_iso(since)))


CHECKERS: dict[str, Callable[[Mapping[str, Any], datetime, WaitFacts], Check]] = {
    TIME: check_time, DEPENDENCY: check_dependency, INTEGRATION_WINDOW: check_integration_window,
    APPROVAL: check_approval}


# --------------------------------------------------------------------------- evaluating a condition

@dataclass(frozen=True)
class PartResult:
    """One condition checked: a leaf of the block."""
    type: str
    met: bool
    met_at: datetime | None
    detail: str
    progress: Progress
    blocked: str | None = None
    info: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "met": self.met, "met_at": utc_iso(self.met_at) if self.met_at else None,
                "detail": self.detail, "blocked": self.blocked, "info": dict(self.info),
                "progress": self.progress.to_dict()}


@dataclass(frozen=True)
class Evaluation:
    """A condition checked at an instant. ``state`` is ``met``, ``expired`` or ``waiting``.

    ``revoked`` is true when the stored block had a ``met_at`` but the condition no longer holds (a task was reopened):
    the old ``met_at`` is not trusted. ``blocked`` is the reason waiting cannot help, if any part has one.
    """
    type: str
    met: bool
    met_at: datetime | None
    expired: bool
    revoked: bool
    blocked: str | None
    parts: tuple[PartResult, ...]
    progress: Progress
    checked_at: datetime
    next_check_at: datetime | None
    deadline_at: datetime | None

    @property
    def state(self) -> str:
        return "met" if self.met else ("expired" if self.expired else "waiting")

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "state": self.state, "met": self.met,
                "met_at": utc_iso(self.met_at) if self.met_at else None, "expired": self.expired,
                "revoked": self.revoked, "blocked": self.blocked, "checked_at": utc_iso(self.checked_at),
                "next_check_at": utc_iso(self.next_check_at) if self.next_check_at else None,
                "deadline_at": utc_iso(self.deadline_at) if self.deadline_at else None,
                "progress": self.progress.to_dict(), "parts": [part.to_dict() for part in self.parts]}


def _leaves(condition: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The leaves of a block: the block itself, or the ``conditions`` of an ``all``."""
    if condition.get("type") == ALL:
        parts = (condition.get("params") or {}).get("conditions")
        if not isinstance(parts, list) or not parts or not all(isinstance(part, Mapping) for part in parts):
            raise WaitError("params.conditions", "an all condition needs a list of conditions")
        return list(parts)
    return [condition]


def condition_leaves(condition: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The leaves of a stored block (the block itself, or the conditions of an ``all``); ``WaitError`` if malformed."""
    return _leaves(condition)


def combine_progress(parts: Sequence[PartResult], *, met: bool, met_at: datetime | None) -> Progress:
    """The progress of a whole wait from its parts (Amendment 2).

    All met: 100 % with ``met_at``. Otherwise, if any pending part has no estimate the wait has none (the unknown one
    dominates; with exactly one unknown its basis is kept). If every pending part has one, it is the estimate of the slowest
    (a part with no end time, because it is past its median, counts as the slowest). Never 100 % without ``met_at``.
    """
    if met:
        return Progress.done(met_at or max(part.met_at for part in parts if part.met_at))
    pending = [part for part in parts if not part.met]
    unknown = [part for part in pending if part.progress.percent is None]
    several = len(parts) > 1
    if unknown:
        if len(unknown) == 1:
            base = unknown[0].progress
            extra = {"unknown": [unknown[0].type]} if several else {}
            return Progress(None, None, "none", base.basis, {**base.detail, **extra})
        return Progress.unknown("unknown_dominates", unknown=[part.type for part in unknown])
    overdue = [part for part in pending if part.progress.eta is None]
    slowest = overdue[0] if overdue else max(pending, key=lambda part: part.progress.eta)       # type: ignore[arg-type,return-value]
    base = slowest.progress
    detail = {**base.detail, **({"slowest": slowest.type} if len(pending) > 1 else {})}
    return replace(base, detail=detail)


def evaluate(condition: Mapping[str, Any], facts: WaitFacts, *, interval: timedelta = CHECK_INTERVAL) -> Evaluation:
    """Check a stored ``hold.condition`` against ``facts`` (pure: the same block and facts give the same answer).

    A part that already has ``met_at`` and still holds keeps it; one that holds for the first time gets ``facts.now``; one
    that no longer holds loses it. The wait is ``expired`` when its deadline has passed with the condition unmet (a
    condition met at the very instant of the deadline is met). ``next_check_at`` is the earlier of ``now + interval``, the
    next pending ``time`` and the deadline. Raises ``WaitError`` for a block that is malformed.
    """
    now = facts.now
    since = _moment(condition.get("since"), "since")
    kind = str(condition.get("type"))
    if kind != ALL and kind not in CHECKERS:
        raise WaitError("type", f"unknown condition type: {kind!r}")
    parts: list[PartResult] = []
    lost = False                                    # a part that was met and no longer holds
    for leaf in _leaves(condition):
        leaf_type = str(leaf.get("type"))
        checker = CHECKERS.get(leaf_type)
        if checker is None:
            raise WaitError("type", f"unknown condition type: {leaf_type!r}")
        check = checker(leaf.get("params") or {}, since, facts)
        stored = parse_utc(leaf.get("met_at")) if leaf.get("met_at") else None
        lost = lost or (stored is not None and not check.met)
        met_at = (stored or now) if check.met else None
        progress = Progress.done(met_at) if check.met and met_at else (check.progress or Progress.unknown("unknown"))
        parts.append(PartResult(leaf_type, check.met, met_at, check.detail, progress, check.blocked, check.info))
    met = all(part.met for part in parts)
    met_at = max(part.met_at for part in parts if part.met_at) if met else None
    deadline = _moment(condition["deadline_at"], "deadline_at") if condition.get("deadline_at") else None
    expired = not met and deadline is not None and now >= deadline
    revoked = not met and (lost or bool(condition.get("met_at")))
    blocked = next((part.blocked for part in parts if not part.met and part.blocked), None)
    progress = mark_revised(condition.get("estimate"), combine_progress(parts, met=met, met_at=met_at))
    following: datetime | None = None
    if not met and not expired:
        candidates = [now + interval]
        for leaf in _leaves(condition):
            if leaf.get("type") == TIME and not leaf.get("met_at"):
                at = parse_utc((leaf.get("params") or {}).get("at"))
                if at is not None and at > now:
                    candidates.append(at)
        if deadline is not None:
            candidates.append(deadline)
        following = min(candidates)
    return Evaluation(kind, met, met_at, expired, revoked, blocked, tuple(parts), progress, now, following, deadline)


def apply_evaluation(condition: Mapping[str, Any], evaluation: Evaluation, *, volatile: bool = True) -> dict[str, Any]:
    """The block with the evaluation written into it (the original is not changed).

    ``met_at`` (of every part and of the whole) and ``expired_at`` are the durable facts and are always written. The
    last check (``checked_at``, ``next_check_at``, ``estimate``) is written only with ``volatile``: a watcher normally keeps
    it in its own state file, because writing it to the task on every check would be a write every two minutes.
    """
    updated = {key: (list(value) if isinstance(value, list) else dict(value) if isinstance(value, Mapping) else value)
               for key, value in condition.items()}
    results = list(evaluation.parts)
    if condition.get("type") == ALL:
        params = dict(updated.get("params") or {})
        params["conditions"] = [{**leaf, "met_at": utc_iso(part.met_at) if part.met_at else None}
                                for leaf, part in zip(_leaves(condition), results)]
        updated["params"] = params
    updated["met_at"] = utc_iso(evaluation.met_at) if evaluation.met_at else None
    if evaluation.expired:
        updated.setdefault("expired_at", utc_iso(evaluation.checked_at))
    if volatile:
        updated["checked_at"] = utc_iso(evaluation.checked_at)
        updated["next_check_at"] = utc_iso(evaluation.next_check_at) if evaluation.next_check_at else None
        updated["estimate"] = evaluation.progress.to_dict()
    return updated


# --------------------------------------------------------------------------- building the condition and the hold

def _token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise WaitError(field_name, f"{field_name} must be a short token (letters, digits, . _ : -)")
    return value


def check_token(value: Any, field_name: str) -> str:
    """``value`` when it is a short token (letters, digits, ``. _ : -``); ``WaitError`` otherwise."""
    return _token(value, field_name)


def _only(params: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    extra = sorted(set(params) - set(allowed))
    if extra:
        raise WaitError(where, f"{where} has unknown keys: {', '.join(extra)}")


def _leaf(spec: Any, where: str) -> dict[str, Any]:
    """A condition leaf, checked and normalized: ``{type, params, met_at: None}``."""
    if not isinstance(spec, Mapping):
        raise WaitError(where, f"{where} must be a mapping with a type and params")
    kind = spec.get("type")
    params = spec.get("params") if spec.get("params") is not None else {}
    if kind not in CONDITION_TYPES:
        raise WaitError(f"{where}.type", f"type must be one of {', '.join(CONDITION_TYPES)}")
    if not isinstance(params, Mapping):
        raise WaitError(f"{where}.params", "params must be a mapping")
    if kind == TIME:
        _only(params, ("at", "confidence", "source"), f"{where}.params")
        clean: dict[str, Any] = {"at": utc_iso(_moment(params.get("at"), f"{where}.params.at"))}
        if params.get("confidence") is not None:
            if params["confidence"] not in CONFIDENCES:
                raise WaitError(f"{where}.params.confidence", f"confidence must be one of {', '.join(CONFIDENCES)}")
            clean["confidence"] = params["confidence"]
        if params.get("source") is not None:
            clean["source"] = _token(params["source"], f"{where}.params.source")
    elif kind == DEPENDENCY:
        _only(params, ("task_ids", "requires"), f"{where}.params")
        ids = params.get("task_ids")
        if not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= MAX_TASK_IDS:
            raise WaitError(f"{where}.params.task_ids", f"task_ids must list 1 to {MAX_TASK_IDS} task ids")
        if not all(isinstance(item, str) and _TASK_ID.fullmatch(item) for item in ids) or len(set(ids)) != len(ids):
            raise WaitError(f"{where}.params.task_ids", "task_ids must be distinct task ids")
        requires = params.get("requires", "accepted")
        if requires not in graph.REQUIRES:
            raise WaitError(f"{where}.params.requires", f"requires must be one of {', '.join(graph.REQUIRES)}")
        clean = {"task_ids": list(ids), "requires": requires}
    elif kind == INTEGRATION_WINDOW:
        _only(params, ("window", "after"), f"{where}.params")
        clean = {"window": _token(params.get("window"), f"{where}.params.window")}
        if params.get("after") is not None:
            clean["after"] = utc_iso(_moment(params["after"], f"{where}.params.after"))
    else:
        _only(params, ("approval", "approver"), f"{where}.params")
        clean = {"approval": _token(params.get("approval"), f"{where}.params.approval")}
        if params.get("approver") is not None:
            clean["approver"] = _token(params["approver"], f"{where}.params.approver")
    return {"type": kind, "params": clean, "met_at": None}


def normalize_condition(spec: Mapping[str, Any], *, now: datetime, deadline: timedelta | None = None,
                        watcher_id: str | None = None) -> dict[str, Any]:
    """A new ``hold.condition`` block from a spec ``{type, params}`` (or ``{type: "all", params: {conditions: [...]}}``).

    The parameters are structured and closed: unknown keys, free text and long lists are refused, so a condition can never
    carry a script or secrets. The deadline is ``deadline`` after ``now`` (default 12 hours, at most 9 days) unless the
    spec brings ``deadline_at``. A ``time`` after the deadline is refused (it could never be met). Raises ``WaitError``.
    """
    if not isinstance(spec, Mapping):
        raise WaitError("condition", "condition must be a mapping with a type and params")
    _only(spec, ("type", "params", "deadline_at"), "condition")
    now = now.astimezone(timezone.utc)
    if spec.get("deadline_at") is not None:
        end = _moment(spec["deadline_at"], "condition.deadline_at")
    else:
        end = now + (DEFAULT_DEADLINE if deadline is None else deadline)
    if end <= now or end > now + MAX_DEADLINE:
        raise WaitError("condition.deadline_at", "the deadline must be in the future and within 9 days")
    if spec.get("type") == ALL:
        _only(spec.get("params") or {}, ("conditions",), "condition.params")
        raw = (spec.get("params") or {}).get("conditions")
        if not isinstance(raw, (list, tuple)) or not 2 <= len(raw) <= MAX_PARTS:
            raise WaitError("condition.params.conditions", f"an all condition needs 2 to {MAX_PARTS} conditions")
        leaves = [_leaf(item, f"condition.params.conditions[{index}]") for index, item in enumerate(raw)]
        block: dict[str, Any] = {"type": ALL, "params": {"conditions": leaves}}
    else:
        leaf = _leaf(spec, "condition")
        leaves = [leaf]
        block = {"type": leaf["type"], "params": leaf["params"]}
    for leaf in leaves:
        if leaf["type"] == TIME and _moment(leaf["params"]["at"], "at") > end:
            raise WaitError("condition.params.at", "the time is later than the deadline: it could never be met")
    block.update({"since": utc_iso(now), "deadline_at": utc_iso(end), "met_at": None, "checked_at": None,
                  "next_check_at": None, "estimate": None,
                  "watcher_id": _token(watcher_id, "condition.watcher_id") if watcher_id is not None else None})
    return block


def reason_for(condition: Mapping[str, Any], reason: str | None = None) -> str:
    """The ``hold.reason`` of a new wait: one of ``WAIT_REASONS`` that the condition really waits for.

    Without ``reason`` the first part that depends on someone names the wait. A condition made only of ``time`` is a
    limit's reset, not a wait of its own: it needs a part that is a dependency, an integration window or an approval (a
    time may be added to a limit hold, see ``ExecutorServices.hold_on_condition``). Raises ``WaitError``.
    """
    kinds = list(dict.fromkeys(str(leaf.get("type")) for leaf in _leaves(condition) if leaf.get("type") in WAIT_REASONS))
    if not kinds:
        raise WaitError("reason", "a wait needs a dependency, an integration window or an approval; a time alone "
                        "is a limit's reset")
    if reason is None:
        return kinds[0]
    if reason not in kinds:
        raise WaitError("reason", f"reason must be one of the waits of the condition: {', '.join(kinds)}")
    return reason


def build_wait_hold(condition: Mapping[str, Any], *, reason: str, now: datetime, hold_id: str, executor_id: str,
                    actor: str, session_id: str | None, attempt: int, settings: Mapping[str, Any],
                    resume_after: datetime | None = None, checkpoint: str | None = None,
                    mode: str = DEFAULT_MODE) -> dict[str, Any]:
    """The ``continuity.hold`` of a wait by condition: the keys of a limit hold (CNT-T04) without the limit's ``window`` and
    ``reset_*``, plus ``id`` (what a watcher quotes to say which hold it means) and ``condition``.

    ``resume_after`` keeps its meaning of a minimum time: the hold is not released before it, whatever the condition says.
    """
    return {
        "id": hold_id, "reason": reason, "outcome": "wait_condition",
        "resume_after": utc_iso(resume_after) if resume_after is not None else None, "open_ended": False,
        "executor_id": executor_id, "actor": actor, "session_id": session_id, "attempt": attempt,
        "checkpoint": checkpoint, "mode": mode, "decision": "pending",
        "settings": {name: settings.get(name) for name in ("model", "effort", "permission", "adapter", "workdir")
                     if settings.get(name) is not None},
        "pause_seq": None, "since": utc_iso(now), "condition": dict(condition)}


def not_before_of_hold(hold: Mapping[str, Any]) -> datetime | None:
    """The earliest instant a held task can possibly get going again, from its own hold, or ``None`` when no time is known.

    It is a floor, not a forecast: the latest of its ``resume_after`` and the pending ``time`` parts of its condition.
    """
    found: list[datetime] = []
    if hold.get("resume_after"):
        after = parse_utc(hold["resume_after"])
        if after is not None:
            found.append(after)
    condition = hold.get("condition")
    if isinstance(condition, Mapping):
        try:
            leaves = _leaves(condition)
        except WaitError:
            leaves = []
        for leaf in leaves:
            if leaf.get("type") == TIME and not leaf.get("met_at"):
                at = parse_utc((leaf.get("params") or {}).get("at"))
                if at is not None:
                    found.append(at)
    return max(found) if found else None


def wait_due(hold: Mapping[str, Any], *, now: datetime, executor_paused: bool, condition_met: bool,
             lifted: bool = False) -> tuple[bool, str]:
    """Whether a held task with a condition may be resumed now, and why not: ``(due, reason)``.

    A limit hold also needs its own time (``hold_due``); any other reason needs its minimum time (``resume_after``) to have
    passed. A hold that is ``blocked`` (an expired condition, three failed resumes) is never due.
    """
    if not isinstance(hold.get("condition"), Mapping):
        return False, "no_condition"
    if is_limit_hold(hold):
        return hold_due(hold, now=now, executor_paused=executor_paused, lifted=lifted, condition_met=condition_met)
    if hold.get("blocked"):
        return False, f"blocked:{hold['blocked']}"
    if not condition_met:
        return False, "condition_pending"
    if executor_paused:
        return False, "executor_paused"
    if hold.get("resume_after"):
        after = parse_utc(hold["resume_after"])
        if after is None:
            return False, "resume_after_invalid"
        if now.astimezone(timezone.utc) < after:
            return False, "not_before"
    return True, "condition_met"


# --------------------------------------------------------------------------- events

def summarize(condition: Mapping[str, Any]) -> dict[str, Any]:
    """A small, safe view of a condition for events and lists: its types and deadline, never its parameters' text."""
    try:
        types = [str(leaf.get("type")) for leaf in _leaves(condition)]
    except WaitError:
        types = []
    return {"type": condition.get("type"), "parts": types, "since": condition.get("since"),
            "deadline_at": condition.get("deadline_at")}


def _minutes(since: Any, now: datetime) -> int | None:
    start = parse_utc(since) if since else None
    return max(int((now - start).total_seconds() // 60), 0) if start is not None else None


def _part_view(part: PartResult) -> dict[str, Any]:
    view: dict[str, Any] = {"type": part.type, "met": part.met, "detail": part.detail}
    if part.met_at:
        view["met_at"] = utc_iso(part.met_at)
    if part.blocked:
        view["blocked"] = part.blocked
    by = part.info.get("by") if part.info else None
    if by:
        view["by"] = str(by)[:100]
    pending = part.info.get("pending") if part.info else None
    if pending:
        view["pending"] = [str(item)[:128] for item in pending[:MAX_TASK_IDS]]
    return view


def met_payload(hold: Mapping[str, Any], evaluation: Evaluation) -> dict[str, Any]:
    """The ``continuity.condition_met`` event: the wait ended because its condition holds (at most 2048 bytes)."""
    condition = hold.get("condition") or {}
    payload = {
        "reason": hold.get("reason"), "type": evaluation.type, "hold_id": hold.get("id"),
        "executor_id": hold.get("executor_id"), "attempt": hold.get("attempt"),
        "since": condition.get("since"), "met_at": utc_iso(evaluation.met_at) if evaluation.met_at else None,
        "waited_minutes": _minutes(condition.get("since"), evaluation.checked_at),
        "deadline_at": condition.get("deadline_at"), "parts": [_part_view(part) for part in evaluation.parts]}
    return _fit({key: value for key, value in payload.items() if value is not None})


def expired_payload(hold: Mapping[str, Any], evaluation: Evaluation) -> dict[str, Any]:
    """The ``continuity.condition_expired`` event: the deadline passed with the condition unmet (at most 2048 bytes)."""
    condition = hold.get("condition") or {}
    payload = {
        "reason": hold.get("reason"), "type": evaluation.type, "hold_id": hold.get("id"),
        "executor_id": hold.get("executor_id"), "attempt": hold.get("attempt"),
        "since": condition.get("since"), "deadline_at": condition.get("deadline_at"),
        "expired_at": utc_iso(evaluation.checked_at), "blocked": evaluation.blocked,
        "waited_minutes": _minutes(condition.get("since"), evaluation.checked_at),
        "parts": [_part_view(part) for part in evaluation.parts]}
    return _fit({key: value for key, value in payload.items() if value is not None})


def _size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _fit(payload: dict[str, Any]) -> dict[str, Any]:
    """Cut the list of parts until the payload fits the event budget; the other fields are small by construction."""
    result = dict(payload)
    while _size(result) > EVENT_BUDGET and result.get("parts"):
        result["parts"] = result["parts"][: len(result["parts"]) // 2]
    return result
