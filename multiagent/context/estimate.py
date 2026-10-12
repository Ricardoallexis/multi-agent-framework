# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Estimating the size of a prompt against the model window, and the warning before the call (CMP-T02).

Contract CMP-T01 sections 3 (estimation, warning and threshold), 10 (the ``context_warning`` event) and 11
(configuration by profile).

* :func:`estimate_context` is a pure function: tokens estimated for the prompt (``estimate_tokens_conservative``,
  characters / 3.2) divided by the window the catalog has evidence for. Its ``level`` is ``ok``, ``warning``
  (at ``warning_at``, 62 % by default), ``threshold`` (at ``threshold``, 70 %: compaction is pending) or
  ``window_unknown``. A window is never invented: without evidence there is no ratio.
* :class:`ContextSettings` is the typed view of one ``context_profiles`` entry of ``catalog/models.yaml``
  (validated by the catalog; :meth:`ContextSettings.from_profile`, :func:`context_settings_for`). Its
  ``summary`` mapping is what ``SummaryPolicy.from_profile`` takes.
* :class:`ContextGuard` is the guard of the chain in ``multiagent.call_guards`` (order 100). It measures each
  call per candidate, because each candidate has its own window, and records a ``context_warning`` event the
  first time a level is reached for a step attempt and model (once per run and model for ``window_unknown``).
  It never stops a call because of size: at 70 % the call goes ahead and ``compaction_pending`` is set on the
  event, so the step can finish and be compacted afterwards (CMP-T04). The only stop is ``unknown_window:
  block``. It is an observer: a failure inside it is logged and never cuts a call.
* :func:`compaction_pending` reads the events of a step and says whether it crossed the threshold.

Nothing here knows about workflows or tasks beyond the ids on the call; the host supplies the event sink.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from ..call_guards import ORDER_CONTEXT, PROCEED, CallContext, RunState, Stop, Verdict
from ..catalog import (
    DEFAULT_CONTEXT_PROFILE, DEFAULT_CONTEXT_SUMMARY, Catalog, validate_context_profile, usable_context_window,
)
from ..schema_utils import estimate_tokens_conservative

_logger = logging.getLogger(__name__)

# Name of the estimator, stored in every event so the estimate can be calibrated against the real ``tokens_in``.
ESTIMATOR = "estimate_tokens_conservative"
EVENT_CONTEXT_WARNING = "context_warning"
CODE_CONTEXT_WINDOW_UNKNOWN = "context_window_unknown"

LEVEL_OK = "ok"
LEVEL_WARNING = "warning"
LEVEL_THRESHOLD = "threshold"
LEVEL_WINDOW_UNKNOWN = "window_unknown"
EVENT_LEVELS = (LEVEL_WARNING, LEVEL_THRESHOLD, LEVEL_WINDOW_UNKNOWN)

UNKNOWN_WINDOW_SKIP = "skip"
UNKNOWN_WINDOW_BLOCK = "block"

EventSink = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ContextSettings:
    """One context profile, validated. ``enabled`` is ``False`` unless the profile says otherwise.

    Build it from a catalog entry with :meth:`from_profile`; constructing it directly applies the same rules
    the catalog applies when it loads the profile (``0 < warning_at < threshold < 1`` and so on), so a value
    that would not load cannot be built either.
    """

    profile_id: str | None = None
    enabled: bool = bool(DEFAULT_CONTEXT_PROFILE["enabled"])
    warning_at: float = float(DEFAULT_CONTEXT_PROFILE["warning_at"])
    threshold: float = float(DEFAULT_CONTEXT_PROFILE["threshold"])
    unknown_window: str = str(DEFAULT_CONTEXT_PROFILE["unknown_window"])
    keep_recent: int = int(DEFAULT_CONTEXT_PROFILE["keep_recent"])
    # The ``summary`` block, every default filled in: pass it to ``SummaryPolicy.from_profile``.
    summary: Mapping[str, Any] = field(default_factory=lambda: dict(DEFAULT_CONTEXT_SUMMARY))
    # ``tasks.window_tokens``: the window to measure task context against; None = the requested model's.
    tasks_window_tokens: int | None = None

    def __post_init__(self) -> None:
        normalized = validate_context_profile(self.profile_id or "<inline>", {
            "version": 1, "enabled": self.enabled, "warning_at": self.warning_at, "threshold": self.threshold,
            "unknown_window": self.unknown_window, "keep_recent": self.keep_recent,
            "summary": dict(self.summary), "tasks": {"window_tokens": self.tasks_window_tokens},
        })
        for name in ("enabled", "warning_at", "threshold", "unknown_window", "keep_recent"):
            object.__setattr__(self, name, normalized[name])
        object.__setattr__(self, "summary", MappingProxyType(dict(normalized["summary"])))
        object.__setattr__(self, "tasks_window_tokens", normalized["tasks"]["window_tokens"])

    @classmethod
    def from_profile(cls, profile_id: str | None, profile: Mapping[str, Any]) -> "ContextSettings":
        """The settings of a ``context_profiles`` entry (a mapping as the catalog returns it, or as written)."""
        normalized = validate_context_profile(profile_id or "<inline>", dict(profile))
        return cls(
            profile_id=profile_id, enabled=normalized["enabled"], warning_at=normalized["warning_at"],
            threshold=normalized["threshold"], unknown_window=normalized["unknown_window"],
            keep_recent=normalized["keep_recent"], summary=normalized["summary"],
            tasks_window_tokens=normalized["tasks"]["window_tokens"],
        )


def context_settings_for(catalog: Catalog, workflow_id: str) -> ContextSettings | None:
    """The settings of the profile assigned to ``workflow_id``, or ``None`` when it has none (compaction off).

    A disabled profile is returned as such (``enabled`` is ``False``); callers act only on an enabled one.
    """
    finder = getattr(catalog, "context_profile_for", None)
    if not callable(finder):
        return None
    profile_id, profile = finder(workflow_id)
    return ContextSettings.from_profile(profile_id, profile) if profile is not None else None


def context_settings_for_tasks(catalog: Catalog) -> ContextSettings | None:
    """The settings of the profile assigned to task envelopes (``assignments.tasks``), or ``None``."""
    finder = getattr(catalog, "context_profile_for_tasks", None)
    if not callable(finder):
        return None
    profile_id, profile = finder()
    return ContextSettings.from_profile(profile_id, profile) if profile is not None else None


@dataclass(frozen=True)
class ContextEstimate:
    """The size of one prompt against one model's window."""

    model_id: str
    estimated_tokens: int
    window: Mapping[str, Any] | None   # the catalog's evidence ({tokens, source, checked_at, provenance, ...})
    ratio: float | None                # estimated_tokens / window tokens; None when the window is unknown
    level: str                         # ok | warning | threshold | window_unknown
    warning_at: float
    threshold: float

    @property
    def compaction_pending(self) -> bool:
        """True from the threshold up: the step may finish, then its context should be compacted."""
        return self.level == LEVEL_THRESHOLD

    def window_fields(self) -> dict[str, Any] | None:
        """``{tokens, source, provenance}`` of the window for the event, ``None`` when it is unknown."""
        if self.window is None:
            return None
        return {"tokens": self.window["tokens"], "source": self.window["source"],
                "provenance": self.window["provenance"]}


def estimate_context(*, model_id: str, estimated_tokens: int, window: Mapping[str, Any] | None,
                     settings: ContextSettings) -> ContextEstimate:
    """Classify ``estimated_tokens`` against ``window`` (``Catalog.context_window``) with the profile's thresholds.

    ``window`` is used only when it is usable evidence (``usable_context_window``); anything else is an unknown
    window, whose level is ``window_unknown`` and whose ratio is ``None``: it is never replaced by a number.
    """
    usable = usable_context_window(window)
    if usable is None:
        return ContextEstimate(model_id, estimated_tokens, None, None, LEVEL_WINDOW_UNKNOWN,
                               settings.warning_at, settings.threshold)
    ratio = estimated_tokens / usable["tokens"]
    if ratio >= settings.threshold:
        level = LEVEL_THRESHOLD
    elif ratio >= settings.warning_at:
        level = LEVEL_WARNING
    else:
        level = LEVEL_OK
    return ContextEstimate(model_id, estimated_tokens, usable, ratio, level, settings.warning_at, settings.threshold)


def estimate_prompt_tokens(text: str) -> int:
    """The framework's conservative token estimate for ``text`` (the one every CMP measure uses)."""
    return estimate_tokens_conservative(text)


def estimate_call(call: CallContext, settings: ContextSettings) -> ContextEstimate:
    """The estimate for one call: its prompt against the window of the candidate model (``call.spec``)."""
    window = usable_context_window(call.spec.context_window) if call.spec is not None else None
    return estimate_context(model_id=call.model_id, estimated_tokens=call.estimated_input_tokens,
                            window=window, settings=settings)


def compaction_pending(events: Iterable[Mapping[str, Any]], *, step_id: str, attempt: int | None = None) -> bool:
    """Whether a step crossed the threshold, from its events (no column is kept for it).

    ``events`` are run or task events as the store returns them (``event_type`` and ``payload``). True when a
    ``context_warning`` of ``level: threshold`` with ``compaction_pending`` exists for ``step_id`` (and
    ``attempt`` when given). Once true it stays true for that step, whichever candidate model measured it.
    """
    for event in events:
        if event.get("event_type") != EVENT_CONTEXT_WARNING:
            continue
        payload = event.get("payload") or {}
        if (payload.get("level") == LEVEL_THRESHOLD and payload.get("compaction_pending")
                and payload.get("step_id") == step_id and (attempt is None or payload.get("attempt") == attempt)):
            return True
    return False


class ContextGuard:
    """The CMP guard of the call chain (order 100): measure, warn once, never cut for size.

    ``emit(event_type, payload)`` records the event in the run or task in flight; without it nothing is recorded
    (the verdicts do not change). A level is reported once per step attempt and model, and ``window_unknown``
    once per run and model; "already reported" is read from the events in ``state`` (so a rebuilt chain, a
    resumed run or another process does not repeat it) and from this guard's own memory.
    """

    name = "context"
    order = ORDER_CONTEXT
    observer = True
    skippable = True  # the summarizer's own calls skip it, so measuring a summary never starts another compaction

    def __init__(self, settings: ContextSettings, *, emit: EventSink | None = None) -> None:
        self.settings = settings
        self._emit = emit
        self._reported: set[tuple[Any, ...]] = set()

    def check(self, call: CallContext, state: RunState) -> Verdict:
        estimate = estimate_call(call, self.settings)
        if estimate.level == LEVEL_OK:
            return PROCEED
        if not self._was_reported(call, estimate, state):
            self._report(call, estimate)
        if estimate.level == LEVEL_WINDOW_UNKNOWN and self.settings.unknown_window == UNKNOWN_WINDOW_BLOCK:
            return Stop(
                CODE_CONTEXT_WINDOW_UNKNOWN,
                f"The context window of model {call.model_id!r} is unknown, so its context cannot be measured, and "
                f"context profile {self.settings.profile_id!r} blocks calls to such models (unknown_window: block)",
                details={"model_id": call.model_id, "profile_id": self.settings.profile_id},
            )
        return PROCEED

    # -- reporting ----------------------------------------------------------------------------------------

    @staticmethod
    def _key(call: CallContext, level: str) -> tuple[Any, ...]:
        owner = call.run_id if call.run_id is not None else call.task_id
        if level == LEVEL_WINDOW_UNKNOWN:
            return (level, call.scope, owner, call.model_id)
        return (level, call.scope, owner, call.step_id, call.attempt, call.model_id)

    def _was_reported(self, call: CallContext, estimate: ContextEstimate, state: RunState) -> bool:
        if self._key(call, estimate.level) in self._reported:
            return True
        for event in state.get("events") or ():
            if event.get("event_type") != EVENT_CONTEXT_WARNING:
                continue
            payload = event.get("payload") or {}
            if payload.get("level") != estimate.level or payload.get("model_id") != call.model_id:
                continue
            if estimate.level == LEVEL_WINDOW_UNKNOWN or (
                    payload.get("step_id") == call.step_id and payload.get("attempt") == call.attempt):
                return True
        return False

    def _report(self, call: CallContext, estimate: ContextEstimate) -> None:
        payload: dict[str, Any] = {"scope": call.scope}
        if call.run_id is not None:
            payload["run_id"] = call.run_id
        if call.task_id is not None:
            payload["task_id"] = call.task_id
        payload.update({
            "step_id": call.step_id, "attempt": call.attempt, "ordinal": call.ordinal, "model_id": call.model_id,
            "context_window": estimate.window_fields(),
            "estimated_tokens": estimate.estimated_tokens,
            "ratio": round(estimate.ratio, 4) if estimate.ratio is not None else None,
            "warning_at": estimate.warning_at, "threshold": estimate.threshold,
            "level": estimate.level, "compaction_pending": estimate.compaction_pending, "estimator": ESTIMATOR,
        })
        if self._emit is not None:
            try:
                self._emit(EVENT_CONTEXT_WARNING, payload)
            except Exception:
                # An event that cannot be written must not cut the call or hide a block verdict.
                _logger.warning("Could not record a context_warning for model %r", call.model_id, exc_info=True)
                return
        self._reported.add(self._key(call, estimate.level))
