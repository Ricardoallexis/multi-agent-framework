# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The opt-in availability policy (AVA-T01, contract ``AVA-T01-contrato.md``).

An orchestrator that never blocks its caller: a long attempt is launched in the background from the start and the
synchronous caller waits at most ``max_wait_seconds`` (30 s by default) before it is told ``running``; messages from the
person go to an inbox that is consumed by priority at the boundary of each step and never approve anything.

This module is pure (no database, no clock, no process) and domain and provider neutral. It only holds and validates
the policy and orders inbox messages; the services act on it:

- AVA-T02 (background launch and heartbeat) asks ``foreground_wait`` how long the caller waits, answers
  ``FOREGROUND_RUNNING`` when the attempt is still going, and emits ``HEARTBEAT_EVENT`` every ``heartbeat_seconds`` and
  ``ATTEMPT_FINISHED_EVENT`` at the end;
- AVA-T03 (user inbox) records ``USER_MESSAGE_EVENT`` and consumes it with ``next_message``.

Without configuration the policy is off (``DISABLED``): ``foreground_wait`` is ``None`` and the caller waits for the
whole attempt, as before. ``orchestrator_threshold`` is reserved for S8: it is validated and kept, and nothing reads it.
The context hand-off of a task uses the CMP context profile only, never this policy.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ATTEMPT_FINISHED_EVENT", "DEFAULT_HEARTBEAT_SECONDS", "DEFAULT_MAX_WAIT_SECONDS", "DEFAULT_PRIORITIES", "DISABLED",
    "FOREGROUND_FINISHED", "FOREGROUND_RUNNING", "HEARTBEAT_EVENT", "MAX_HEARTBEAT_SECONDS", "MAX_MAX_WAIT_SECONDS",
    "MIN_HEARTBEAT_SECONDS", "MIN_MAX_WAIT_SECONDS", "ORCHESTRATOR_THRESHOLD_RANGE", "STALL_SECONDS", "USER_MESSAGE_EVENT",
    "AvailabilityPolicy", "AvailabilityPolicyError", "MessageKind", "UserMessage", "next_message", "order_messages",
    "policy_from_mapping", "policy_from_settings",
]

DEFAULT_MAX_WAIT_SECONDS = 30                    # what the synchronous caller waits before ``running`` (audit decision 5)
MIN_MAX_WAIT_SECONDS = 1
MAX_MAX_WAIT_SECONDS = 120
DEFAULT_HEARTBEAT_SECONDS = 60
MIN_HEARTBEAT_SECONDS = 5
MAX_HEARTBEAT_SECONDS = 300
STALL_SECONDS = 15 * 60                          # the 15 minute stall of CNT-T09: a heartbeat always comes sooner
ORCHESTRATOR_THRESHOLD_RANGE = (0.50, 0.60)      # reserved for S8

HEARTBEAT_EVENT = "executor.heartbeat"
ATTEMPT_FINISHED_EVENT = "executor.attempt_finished"
USER_MESSAGE_EVENT = "user.message"
FOREGROUND_RUNNING = "running"
FOREGROUND_FINISHED = "finished"


class AvailabilityPolicyError(ValueError):
    """The availability configuration or a message is not valid (the message names the key)."""


class MessageKind(str, Enum):
    """What a message from the person asks for. None of them approves anything."""
    CANCEL = "cancel"
    INTERRUPT = "interrupt"
    ADJUST = "adjust"
    NOTE = "note"


DEFAULT_PRIORITIES: tuple[str, ...] = tuple(kind.value for kind in MessageKind)
_KINDS = frozenset(DEFAULT_PRIORITIES)
_KEYS = ("enabled", "max_wait_seconds", "heartbeat_seconds", "priorities", "orchestrator_threshold")


def _number(key: str, value: Any, low: float, high: float) -> float:
    """``value`` when it is a finite int or float within ``[low, high]``; ``bool`` and text are refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AvailabilityPolicyError(f"{key} must be a number between {low} and {high}, got {value!r}")
    if not low <= value <= high:
        raise AvailabilityPolicyError(f"{key} must be between {low} and {high}, got {value!r}")
    return value


def _priorities(value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise AvailabilityPolicyError(f"priorities must be a list of message kinds, got {value!r}")
    items = tuple(item.value if isinstance(item, MessageKind) else item for item in value)
    if len(items) != len(_KINDS) or set(items) != _KINDS:
        raise AvailabilityPolicyError(
            f"priorities must list each of {', '.join(DEFAULT_PRIORITIES)} exactly once, got {list(items)!r}")
    if items[0] != MessageKind.CANCEL.value:
        raise AvailabilityPolicyError(f"priorities must start with 'cancel', got {list(items)!r}")
    return items


@dataclass(frozen=True)
class AvailabilityPolicy:
    """The availability policy; the default instance is off and changes nothing."""
    enabled: bool = False
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
    priorities: tuple[str, ...] = field(default=DEFAULT_PRIORITIES)
    orchestrator_threshold: float | None = None  # reserved for S8: validated, never read by any flow

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise AvailabilityPolicyError(f"enabled must be true or false, got {self.enabled!r}")
        _number("max_wait_seconds", self.max_wait_seconds, MIN_MAX_WAIT_SECONDS, MAX_MAX_WAIT_SECONDS)
        _number("heartbeat_seconds", self.heartbeat_seconds, MIN_HEARTBEAT_SECONDS, MAX_HEARTBEAT_SECONDS)
        object.__setattr__(self, "priorities", _priorities(self.priorities))
        if self.orchestrator_threshold is not None:
            _number("orchestrator_threshold", self.orchestrator_threshold, *ORCHESTRATOR_THRESHOLD_RANGE)

    def foreground_wait(self, timeout_seconds: float | None = None) -> float | None:
        """How long the synchronous caller waits for an attempt.

        ``None`` when the policy is off: the caller waits for the whole attempt, as before. Otherwise
        ``max_wait_seconds``, never more than the attempt's own ``timeout_seconds``. The attempt's limit is unchanged.
        """
        if not self.enabled:
            return None
        if timeout_seconds is None:
            return self.max_wait_seconds
        return min(self.max_wait_seconds, timeout_seconds)

    def foreground_outcome(self, finished: bool) -> str:
        """What the caller is told when its wait ends: ``finished``, or ``running`` while the attempt goes on."""
        return FOREGROUND_RUNNING if self.enabled and not finished else FOREGROUND_FINISHED

    def priority_rank(self, kind: MessageKind | str) -> int:
        """The place of a message kind in the inbox order (0 is consumed first)."""
        value = kind.value if isinstance(kind, MessageKind) else kind
        try:
            return self.priorities.index(value)
        except ValueError:
            raise AvailabilityPolicyError(f"Unknown message kind: {kind!r}") from None

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready copy (numbers, flags and kind names only); ``policy_from_mapping`` reads it back."""
        return {"enabled": self.enabled, "max_wait_seconds": self.max_wait_seconds,
                "heartbeat_seconds": self.heartbeat_seconds, "priorities": list(self.priorities),
                "orchestrator_threshold": self.orchestrator_threshold}


DISABLED = AvailabilityPolicy()


def policy_from_mapping(data: Mapping[str, Any] | None) -> AvailabilityPolicy:
    """The policy a configuration block declares; ``None`` or ``{}`` is the off policy. Unknown keys are refused."""
    if data is None:
        return DISABLED
    if not isinstance(data, Mapping):
        raise AvailabilityPolicyError(f"The availability configuration must be a mapping, got {type(data).__name__}")
    unknown = sorted(str(key) for key in data if key not in _KEYS)
    if unknown:
        raise AvailabilityPolicyError(f"Unknown availability keys {unknown}; expected {', '.join(_KEYS)}")
    return AvailabilityPolicy(**dict(data))


def policy_from_settings(settings: Any) -> AvailabilityPolicy:
    """The policy from the optional ``availability_*`` attributes of ``settings``; none of them means off.

    Reads ``availability_enabled``, ``availability_max_wait_seconds``, ``availability_heartbeat_seconds``,
    ``availability_priorities`` and ``availability_orchestrator_threshold`` when they exist (AVA-T02 adds them to
    ``Settings``); an attribute that does not exist keeps its default.
    """
    missing = object()
    data = {}
    for key in _KEYS:
        value = getattr(settings, f"availability_{key}", missing)
        if value is not missing:
            data[key] = value
    return policy_from_mapping(data)


@dataclass(frozen=True)
class UserMessage:
    """One message of the inbox, as far as ordering needs it. Its text is not here: the event of AVA-T03 holds it."""
    message_id: str
    kind: MessageKind
    seq: int

    def __post_init__(self) -> None:
        if not isinstance(self.message_id, str) or not self.message_id.strip():
            raise AvailabilityPolicyError(f"message_id must be a non-empty text, got {self.message_id!r}")
        if not isinstance(self.kind, MessageKind):
            raise AvailabilityPolicyError(f"kind must be a MessageKind, got {self.kind!r}")
        if isinstance(self.seq, bool) or not isinstance(self.seq, int) or self.seq < 0:
            raise AvailabilityPolicyError(f"seq must be a non-negative integer, got {self.seq!r}")


def order_messages(messages: Iterable[UserMessage], policy: AvailabilityPolicy = DISABLED) -> list[UserMessage]:
    """The inbox in the order it is consumed: by the policy's priority of the kind, then by ``seq``.

    A ``message_id`` seen more than once is kept once, with its lowest ``seq``, so a repeated message is never handled
    twice. The order is the same whether the policy is on or off.
    """
    first: dict[str, UserMessage] = {}
    for message in messages:
        kept = first.get(message.message_id)
        if kept is None or message.seq < kept.seq:
            first[message.message_id] = message
    return sorted(first.values(), key=lambda m: (policy.priority_rank(m.kind), m.seq, m.message_id))


def next_message(messages: Iterable[UserMessage], policy: AvailabilityPolicy = DISABLED) -> UserMessage | None:
    """The message consumed next, or ``None`` when the inbox is empty."""
    ordered = order_messages(messages, policy)
    return ordered[0] if ordered else None
