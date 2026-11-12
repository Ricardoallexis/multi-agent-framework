# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The hand-off policy of a task held by a usage limit (CNT-T05, contract ``CNT-T01-contrato.md`` sections 7 to 9).

A task that hit a usage limit waits (``hold.py``). This module decides what to do with it: wait for the reset, ask a person,
or hand the task over to another executor. It is pure (no database, no clock of its own, no process): the caller gathers the
facts (``Candidate`` for each executor, the hold, the counters, the budget) and ``ExecutorServices`` carries out the answer.

Modes (``Policy.mode``; ``auto`` belongs to CNT-T07 and is only ever set per project, so an unknown mode here is treated as
``manual``, the strictest):

- ``manual``: always asks a person, except for the short backoff retries of a 429 (``rate_limited``), which just wait.
- ``semi_auto``: waits when the reset is reliable (``high`` or ``medium`` confidence) and not more than
  ``wait_threshold_hours`` away; when it is further, hands the task over without asking, but only to a candidate that is
  enabled, not paused and not used yet, that the policy marks ``no_ask``, that runs on a subscription (never a paid API),
  whose model is at the level of the original or cheaper, and only when the project is not private, a patch was saved, the
  budget allows it, the held session is not open in an app (12 hours) and a hand-off is left. Anything else asks, with the
  best candidate as the proposal.

The table of the contract, as ``decide`` applies it (A is automatic with a notice, P asks with a card):

=========================================  ======  ===========  =========================================
situation                                  manual  semi_auto    here
=========================================  ======  ===========  =========================================
``rate_limited`` with retries left         wait    wait         ``rate_limited_retry``
``plan_limit``, reliable, reset <= limit   ask     wait         ``reset_within_threshold``
same, beyond the limit, a candidate        ask     delegate     ``reset_beyond_threshold``
same, beyond the limit, no candidate       ask     ask          ``no_candidate_without_asking``
no reliable time, ``limit_unknown``        ask     ask          ``no_reliable_reset`` / ``limit_unknown``
``credits_exhausted``                      ask     ask          ``credits_exhausted`` (with a proposal)
hand-offs used up                          ask     wait if reliable, else ask   ``handoffs_exhausted``
=========================================  ======  ===========  =========================================

Each level (profile, then task; the project level comes with CNT-T07) only restricts what the one above allows
(``resolve_policy``): a lower mode, fewer hand-offs, a longer wait threshold (waiting instead of delegating) and a stricter
policy for each candidate (``never`` < ``ask`` < ``no_ask``).

A person's own decision (the card of CNT-T06) is not decided here: it is recorded in the hold as ``decision`` ``delegate``,
``candidate`` and ``blocked`` ``delegation_requested`` (``requested_delegation``) and the service carries it out, whatever the
mode, within the hard limits (section 8) that do not depend on the mode.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import CONTINUITY_CANDIDATE_POLICIES, CONTINUITY_MODES, DEFAULT_CONTINUITY_CANDIDATES
from ..routing_policy import MODEL_LEVELS
from . import hold as _hold

__all__ = [
    "Candidate", "DELEGATION_BLOCK", "Decision", "MAX_HANDOFFS", "Policy", "REPLACE_OPEN_SESSION", "SUBSCRIPTION_SURFACES",
    "best_candidate", "decide", "requested_delegation", "resolve_policy",
]

DELEGATION_BLOCK = "delegation_requested"        # the ``hold.blocked`` a person's delegate decision sets (CNT-T06)
# The key of the hold in which the card says explicitly that the person accepts replacing a session that is open in an app
# (a hand-off retires it). Without it a session opened in the last 12 hours is never replaced (``ExecutorServices``).
REPLACE_OPEN_SESSION = "replace_open_session"
MAX_HANDOFFS = 3                                 # contract section 7: up to three
# The apps the framework drives through their official CLI with the user's own subscription (ORQ-6). Any other surface is
# treated as a paid API: it is never handed a task without asking.
SUBSCRIPTION_SURFACES = ("claude_cli", "codex_cli", "copilot_cli")
_MODE_RANK = {"manual": 0, "semi_auto": 1}
_POLICY_RANK = {"never": 0, "ask": 1, "no_ask": 2}


@dataclass(frozen=True)
class Policy:
    """What the framework may do by itself with a held task (contract section 7)."""
    mode: str = _hold.DEFAULT_MODE
    wait_threshold_hours: int = 5
    max_handoffs: int = 1
    candidates: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_CONTINUITY_CANDIDATES))

    @property
    def threshold(self) -> timedelta:
        return timedelta(hours=self.wait_threshold_hours)

    def candidate_policy(self, executor_id: str, surface: str | None = None) -> str:
        """``no_ask``, ``ask`` or ``never`` for an executor: by its id, else by its surface, else ``ask``."""
        found = self.candidates.get(executor_id)
        if found is None and surface:
            found = self.candidates.get(surface)
        return found if found in CONTINUITY_CANDIDATE_POLICIES else "ask"

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "wait_threshold_hours": self.wait_threshold_hours, "max_handoffs": self.max_handoffs,
                "candidates": dict(self.candidates)}


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def resolve_policy(profile: Policy, task: Mapping[str, Any] | None = None) -> Policy:
    """The policy in force for a task: ``profile`` (the settings) restricted by what the task's continuity block says.

    A level only restricts (contract section 7): the lower mode wins, the lower ``max_handoffs`` wins, the longer
    ``wait_threshold_hours`` wins (it waits instead of delegating) and, per executor, the stricter candidate policy wins. A
    value the task carries that is malformed or would loosen the profile is ignored.
    """
    block = task if isinstance(task, Mapping) else {}
    mode = profile.mode if profile.mode in _MODE_RANK else "manual"
    asked = block.get("mode")
    if asked in _MODE_RANK and _MODE_RANK[asked] < _MODE_RANK[mode]:
        mode = asked
    handoffs = profile.max_handoffs
    wanted = _count(block.get("max_handoffs"))
    if wanted is not None:
        handoffs = min(handoffs, wanted)
    hours = profile.wait_threshold_hours
    longer = _count(block.get("wait_threshold_hours"))
    if longer is not None:
        hours = max(hours, longer)
    candidates = dict(profile.candidates)
    extra = block.get("candidates")
    if isinstance(extra, Mapping):
        for key, value in extra.items():
            if value in _POLICY_RANK:
                current = candidates.get(str(key), "ask")
                candidates[str(key)] = value if _POLICY_RANK[value] < _POLICY_RANK[current] else current
    return Policy(mode=mode, wait_threshold_hours=hours, max_handoffs=min(handoffs, MAX_HANDOFFS), candidates=candidates)


# --------------------------------------------------------------------------- the candidates

@dataclass(frozen=True)
class Candidate:
    """What is known about an executor that could take a held task over (gathered by the service)."""
    executor_id: str
    surface: str | None = None
    policy: str = "ask"                 # ``no_ask``, ``ask`` or ``never`` (Policy.candidate_policy)
    enabled: bool = True
    paused: bool = False                # a pause in force on the executor (any kind)
    used: bool = False                  # it is the executor that hit the limit or is already in the chain of the task
    model: str | None = None            # what ``select_for_handoff`` would ask it to run
    level: str | None = None
    within_level: bool | None = None    # model at the original level or cheaper; None: it cannot be told
    same_folder: bool = True            # it works in the same folder as the first executor did

    @property
    def subscription(self) -> bool:
        return self.surface in SUBSCRIPTION_SURFACES

    def refusal(self) -> str | None:
        """Why this executor can never take the task over (a hard limit, section 8), whatever the mode; else ``None``."""
        if not self.enabled:
            return "executor_disabled"
        if self.used:
            return "executor_already_used"
        if self.paused:
            return "executor_paused"
        if self.policy == "never":
            return "candidate_never"
        return None

    def ask_reasons(self) -> list[str]:
        """What stops the task from being handed to this executor without asking a person (empty: it may be)."""
        reasons: list[str] = []
        if self.policy != "no_ask":
            reasons.append("candidate_asks")
        if not self.subscription:
            reasons.append("paid_api")
        if self.within_level is False:
            reasons.append("level_above")
        elif self.within_level is None:
            reasons.append("level_unknown")
        if not self.same_folder:
            reasons.append("workdir_differs")
        return reasons

    def to_dict(self) -> dict[str, Any]:
        return {"executor_id": self.executor_id, "surface": self.surface, "policy": self.policy,
                "subscription": self.subscription, "model": self.model, "level": self.level,
                "within_level": self.within_level, "same_folder": self.same_folder,
                "refusal": self.refusal(), "ask_reasons": self.ask_reasons()}


def _closeness(candidate: Candidate) -> tuple[int, int, str]:
    """Best first: a model within the level before one that is not, the highest level before the lower, then by id."""
    index = MODEL_LEVELS.index(candidate.level) if candidate.level in MODEL_LEVELS else -1
    return (0 if candidate.within_level else 1, -index, candidate.executor_id)


def best_candidate(candidates: Sequence[Candidate]) -> Candidate | None:
    """The candidate that keeps the quality of the work closest to the original: the level nearest to it, not above it.

    Candidates that can never take the task (``refusal``) are left out. Deterministic: equal levels go by executor id.
    """
    usable = [item for item in candidates if item.refusal() is None]
    return min(usable, key=_closeness) if usable else None


# --------------------------------------------------------------------------- the decision

@dataclass(frozen=True)
class Decision:
    """What the policy does with a held task: ``wait``, ``delegate``, ``ask`` a person or ``none`` (not for this module).

    ``why`` is a code (see the table in the module notes). For ``delegate`` ``candidate`` is the executor chosen; for ``ask``
    it is the best proposal (or ``None``) and ``ask`` lists what stopped an automatic hand-off. ``wait_until`` is the
    ``resume_after`` of the hold for a wait.
    """
    action: str
    why: str
    by: str
    candidate: str | None = None
    wait_until: str | None = None
    ask: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = {"action": self.action, "why": self.why, "by": self.by, "candidate": self.candidate,
                "wait_until": self.wait_until, "ask": list(self.ask)}
        return {key: value for key, value in data.items() if value not in (None, ())}


def requested_delegation(hold: Mapping[str, Any] | None) -> str | None:
    """The executor a person asked the task to be delegated to (CNT-T06 recorded it in the hold), else ``None``."""
    if not hold or hold.get("decision") != "delegate" or hold.get("blocked") != DELEGATION_BLOCK:
        return None
    candidate = hold.get("candidate")
    return candidate if isinstance(candidate, str) and candidate else None


def decide(policy: Policy, hold: Mapping[str, Any], *, handoffs: int, now: datetime, candidates: Sequence[Candidate],
           has_patch: bool, private_project: bool, budget_ok: bool, session_open: bool = False) -> Decision:
    """What to do with a task held by a usage limit, by the table of the contract (section 7).

    ``handoffs`` is how many hand-offs the task has used; ``candidates`` every other executor with what is known about it;
    ``has_patch`` that the checkpoint saved a patch; ``private_project`` that the task belongs to a private project;
    ``budget_ok`` that no budget stops a hand-off; ``session_open`` that the held session was opened in an app in the last
    12 hours (it is not replaced without asking). Never decides for a hold that is not a limit hold or carries a condition
    (``none``), and a hold blocked for another reason (three failed resumes) asks. A person who chose to wait on the card
    (``hold.decision`` ``wait``) is always obeyed: the policy waits too and never hands the task over.
    """
    by = f"policy:{policy.mode}"
    if not _hold.is_limit_hold(hold) or _hold.is_condition_hold(hold):
        return Decision("none", "not_a_limit_hold", by)
    wait_until = hold.get("resume_after")
    if hold.get("decision") == "wait":
        return Decision("wait", f"decided:wait:{hold.get('decided_by') or 'user'}", by, wait_until=wait_until)
    if hold.get("blocked"):
        return Decision("ask", f"blocked:{hold['blocked']}", by, ask=(f"blocked:{hold['blocked']}",))
    reason = hold.get("reason")
    if reason == "rate_limited" and hold.get("outcome") == "wait_retry":
        return Decision("wait", "rate_limited_retry", by, wait_until=wait_until)    # a backoff retry is never a decision
    left = handoffs < policy.max_handoffs
    proposal = best_candidate(candidates) if left else None
    proposed = proposal.executor_id if proposal is not None else None
    reset = _hold.parse_utc(hold.get("reset_at") or hold.get("resume_after"))
    reliable = reason == "plan_limit" and hold.get("outcome") == "wait_until_reset" and reset is not None
    if policy.mode != "semi_auto":                    # ``manual``, and a mode this module does not know, always ask
        why = "manual_mode" if left else "handoffs_exhausted"
        return Decision("ask", why, by, candidate=proposed, ask=(why,))
    if reason == "credits_exhausted":
        return Decision("ask", "credits_exhausted", by, candidate=proposed, ask=("credits_exhausted",))
    if reason != "plan_limit":
        why = "limit_unknown" if reason == "limit_unknown" else "no_reliable_reset"
        return Decision("ask", why, by, candidate=proposed, ask=(why,))
    if not reliable:
        return Decision("ask", "no_reliable_reset", by, candidate=proposed, ask=("no_reliable_reset",))
    assert reset is not None
    if reset <= now.astimezone(timezone.utc) + policy.threshold:
        return Decision("wait", "reset_within_threshold", by, wait_until=wait_until)
    if not left:
        return Decision("wait", "handoffs_exhausted", by, wait_until=wait_until)
    stops = []
    if private_project:
        stops.append("private_project")
    if not has_patch:
        stops.append("no_patch")
    if not budget_ok:
        stops.append("budget")
    if session_open:
        stops.append("session_open_in_app")
    usable = [item for item in candidates if item.refusal() is None]
    if not stops:
        automatic = [item for item in usable if not item.ask_reasons()]
        if automatic:
            return Decision("delegate", "reset_beyond_threshold", by, candidate=best_candidate(automatic).executor_id)  # type: ignore[union-attr]
        stops = sorted({reason for item in usable for reason in item.ask_reasons()}) or ["no_candidate"]
    return Decision("ask", "no_candidate_without_asking", by, candidate=proposed, ask=tuple(stops))
