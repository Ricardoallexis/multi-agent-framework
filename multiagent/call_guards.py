# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The chain of checks that runs before every model call (CMP-T02, contract CMP-T01 section 4).

Before this module the router called ``before_call()`` with no arguments and the workflow engine put every
rule in one closure. Now the router hands the candidate it is about to call (:class:`CallContext`) to a
chain of guards, so each stage adds its own check without editing the engine or the router.

The fixed order of one check (:meth:`CallGuardChain.check`):

1. **cancel**: the host's own cancellation check (it raises to stop);
2. **guards**, sorted by ``order``: ``50`` the person's inbox (AVA), ``100`` context (CMP), ``150`` untrusted content
   (BDI), ``200`` limits, ``240`` secrets, ``250`` egress (CAP), ``300`` budget (USO);
3. **reserve**: the host books the call (``routing_dispatch_reserved``, ``increment_llm_calls``), and only
   when every guard let the call through.

A guard answers with a verdict: :class:`Proceed`, :class:`Stop` (the call must not happen; the engine turns it
into ``BudgetExceeded``) or :class:`Pause` (the call waits for a human; the engine turns it into
``WAITING_HUMAN`` with the guard's ``waiting_reason``). The first ``Stop`` or ``Pause`` ends the check and
nothing is reserved.

Adding a guard (USO-T04, CNT-T04/T10): write it in its own module, then add one line to
:func:`build_call_guards`. A guard needs ``name`` (unique), ``order``, ``observer`` and ``check(call, state)``:

* ``observer=True``: it observes more than it governs. An exception inside ``check`` is logged and ignored, so
  a bug in it never cuts a call (an explicit ``Stop`` or ``Pause`` it returns is still honoured: the context
  guard returns ``Stop`` only when the profile says ``unknown_window: block``). A guard with
  ``observer=False`` fails closed: its exception propagates and the call does not happen.
* ``skippable=True`` (optional attribute, ``False`` when absent): callers may ask to leave it out with
  ``check(..., skip=("name",))``. The calls the summarizer makes skip the context guard so that measuring a
  summary does not start another compaction; they still pass the limits and the budget guards, which cannot
  be skipped.

This module is neutral: it knows nothing about workflows, tasks or compaction, and it imports nothing from
the rest of the package at run time (the context guard is imported inside :func:`build_call_guards`).
"""
from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Protocol

if TYPE_CHECKING:
    from .budget_cut import BudgetPolicy
    from .catalog import Catalog, ModelSpec
    from .context.estimate import ContextSettings
    from .document_trust import DetectionPolicy
    from .services.availability import AvailabilityPolicy

_logger = logging.getLogger(__name__)

SCOPE_RUN_STEP = "run_step"
SCOPE_TASK = "task"
SCOPES = (SCOPE_RUN_STEP, SCOPE_TASK)

# ``Stop.code`` of a ``cancel`` message of the person (AVA-T03): the host ends the work as cancelled, not as over budget.
STOP_USER_CANCEL = "user_cancel"

# Where each guard runs in the chain; guards with the same order keep their registration order.
ORDER_INBOX = 50         # the person's messages, read at the boundary of a step (AVA-T03); first, so a cancel wins
ORDER_CONTEXT = 100
ORDER_CONTENT = 150      # untrusted content held for a review (BDI-T04)
ORDER_LIMITS = 200
ORDER_SECRET = 240
ORDER_EGRESS = 250      # screen the outbound text before the budget guard books a paid call
ORDER_BUDGET = 300

# The persisted state of the run (or task) the call belongs to, as the store returns it. Guards read it and
# never change it.
RunState = Mapping[str, Any]


@dataclass(frozen=True, kw_only=True)
class CallContext:
    """What is known about one model call before it is made.

    The router fills the candidate part (``model_id``, ``spec``, ``retry``, ``ordinal`` and the estimate of the
    prompt it is about to send); the host (the workflow engine, a task runner) adds the rest with
    :meth:`with_fields` before the chain runs. ``estimated_input_tokens`` comes from the exact prompt sent.
    """

    model_id: str
    spec: "ModelSpec"
    scope: str = SCOPE_RUN_STEP          # "run_step" | "task"
    run_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    step_kind: str | None = None
    attempt: int = 1                     # the host's attempt at the step (revisions), not the router's retry
    retry: int = 0                       # the router's retry of this candidate
    ordinal: int = 0                     # the router's call counter inside one execute()
    profile_id: str | None = None        # the routing profile in force, if any
    estimated_input_tokens: int = 0
    data_class: str | None = None
    outgoing_text: str | None = field(default=None, repr=False, compare=False)
    replace_outgoing: Callable[[str], None] | None = field(default=None, repr=False, compare=False)

    def with_fields(self, **fields: Any) -> "CallContext":
        """A copy with ``fields`` replaced (the dataclass is frozen)."""
        return dataclasses.replace(self, **fields)


@dataclass(frozen=True)
class Proceed:
    """The call may go ahead."""


@dataclass(frozen=True)
class Stop:
    """The call must not happen. ``code`` is machine readable, ``message`` is for people.

    ``guard`` is filled in by the chain with the name of the guard that stopped the call.
    """

    code: str
    message: str
    guard: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("Stop.code must be a non-empty string")


@dataclass(frozen=True)
class Pause:
    """The call waits for a person. ``waiting_reason`` is what the run reports while it waits and ``request``
    says what is being asked (data for the event and for whoever approves; it must be JSON serializable).

    A ``WaitingReason`` member is stored as its value. ``guard`` is filled in by the chain.
    """

    waiting_reason: str
    request: Mapping[str, Any] = field(default_factory=dict)
    message: str = ""
    guard: str = ""

    def __post_init__(self) -> None:
        reason = self.waiting_reason.value if isinstance(self.waiting_reason, Enum) else self.waiting_reason
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Pause.waiting_reason must be a non-empty string")
        object.__setattr__(self, "waiting_reason", reason)


Verdict = Proceed | Stop | Pause
PROCEED = Proceed()
_VERDICTS = (Proceed, Stop, Pause)


class CallPaused(Exception):
    """Raised by a host from inside ``before_call`` once it has put the work in its waiting state for a ``Pause``.

    It unwinds the router (which records the preflight attempt) up to the host's own handler. The engine
    handles it in ``process_run``; a task runner can define the same pattern.
    """

    def __init__(self, verdict: Pause, call: "CallContext") -> None:
        super().__init__(verdict.message or f"The call to {call.model_id!r} waits for {verdict.waiting_reason}")
        self.verdict = verdict
        self.call = call


class CallGuard(Protocol):
    """One check of the chain. See the module docstring for ``observer`` and ``skippable``."""

    name: str
    order: int
    observer: bool

    def check(self, call: CallContext, state: RunState) -> Verdict:
        ...


class CallGuardChain:
    """Cancel, then the guards in ``order``, then the reservation (see the module docstring).

    ``cancel(call)`` raises when the work was cancelled; ``reserve(call)`` books the call. Both are optional
    and belong to the host. The chain keeps no state between calls: the guards read the ``state`` they are given.
    """

    def __init__(self, guards: Iterable[CallGuard] = (), *,
                 cancel: Callable[[CallContext], None] | None = None,
                 reserve: Callable[[CallContext], None] | None = None) -> None:
        self._guards: list[CallGuard] = []
        self._cancel = cancel
        self._reserve = reserve
        for guard in guards:
            self.register(guard)

    # -- registration -----------------------------------------------------------------------------------

    def register(self, guard: CallGuard) -> CallGuard:
        """Add a guard. Names are unique; a guard without a usable name, order or ``check`` is refused."""
        name = getattr(guard, "name", None)
        order = getattr(guard, "order", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"A call guard needs a non-empty string name, got {name!r}")
        if type(order) is not int:
            raise ValueError(f"Call guard {name!r} needs an integer order, got {order!r}")
        if not callable(getattr(guard, "check", None)):
            raise ValueError(f"Call guard {name!r} needs a check(call, state) method")
        if any(existing.name == name for existing in self._guards):
            raise ValueError(f"A call guard named {name!r} is already registered")
        self._guards.append(guard)
        self._guards.sort(key=lambda item: item.order)  # stable: equal orders keep their registration order
        return guard

    def unregister(self, name: str) -> bool:
        """Remove the guard called ``name``; ``False`` when there was none."""
        before = len(self._guards)
        self._guards = [guard for guard in self._guards if guard.name != name]
        return len(self._guards) != before

    @property
    def guards(self) -> tuple[CallGuard, ...]:
        """The registered guards in the order they run."""
        return tuple(self._guards)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(guard.name for guard in self._guards)

    # -- the check --------------------------------------------------------------------------------------

    def check(self, call: CallContext, state: RunState, *, skip: Iterable[str] | str = ()) -> Verdict:
        """Run the chain for one call and return the verdict (``PROCEED`` after reserving the call).

        ``skip`` names guards to leave out; only guards marked ``skippable`` may be left out (``ValueError``
        otherwise), and a name that matches no registered guard is ignored. ``cancel`` always runs first, so a
        cancelled run raises before anything is measured, and ``reserve`` runs only when every guard proceeded.
        """
        skipped = self._skipped(skip)
        if self._cancel is not None:
            self._cancel(call)
        for guard in self._guards:
            if guard.name in skipped:
                continue
            verdict = self._run(guard, call, state)
            if isinstance(verdict, Proceed):
                continue
            return dataclasses.replace(verdict, guard=guard.name) if not verdict.guard else verdict
        if self._reserve is not None:
            self._reserve(call)
        return PROCEED

    def _skipped(self, skip: Iterable[str] | str) -> frozenset[str]:
        names = frozenset((skip,) if isinstance(skip, str) else skip)
        for guard in self._guards:
            if guard.name in names and not getattr(guard, "skippable", False):
                raise ValueError(f"The call guard {guard.name!r} cannot be skipped")
        return names

    @staticmethod
    def _run(guard: CallGuard, call: CallContext, state: RunState) -> Verdict:
        observer = bool(getattr(guard, "observer", False))
        try:
            verdict = guard.check(call, state)
        except Exception:
            if not observer:
                raise  # fails closed: a guard that cannot decide must not let the call through
            _logger.warning("Observer call guard %r failed; the call goes on", guard.name, exc_info=True)
            return PROCEED
        if not isinstance(verdict, _VERDICTS):
            if observer:
                _logger.warning("Observer call guard %r returned %r instead of a verdict; the call goes on",
                                guard.name, verdict)
                return PROCEED
            raise TypeError(f"Call guard {guard.name!r} must return Proceed, Stop or Pause, got {verdict!r}")
        return verdict


# ---------------------------------------------------------------------------------------------------------
# The limits guard (moved out of the workflow engine without changing what it does)
# ---------------------------------------------------------------------------------------------------------

class RunLimitsGuard:
    """The limits of the current call: the routing profile's ``max_provider_invocations`` and the run's
    ``max_llm_calls``. Messages and order of the two checks are the ones the engine always had."""

    name = "limits"
    order = ORDER_LIMITS
    observer = False
    skippable = False

    def __init__(self, profile_id: str | None = None, profile: Mapping[str, Any] | None = None) -> None:
        self.profile_id = profile_id
        self.profile = profile

    def check(self, call: CallContext, state: RunState) -> Verdict:
        if self.profile_id is not None:
            # A correction the person asked for has its own margin: only the dispatches after the last ``human_revision``
            # count. A run nobody corrected counts them all, as it always did.
            events = list(state.get("events", []))
            since = max((position for position, event in enumerate(events) if event.get("event_type") == "human_revision"),
                        default=-1) + 1
            used = sum(
                1 for event in events[since:]
                if event.get("event_type") == "routing_dispatch_reserved"
                and event.get("payload", {}).get("profile_id") == self.profile_id
            )
            limit = self.profile.get("max_provider_invocations") if isinstance(self.profile, Mapping) else None
            if limit is not None and used >= int(limit):
                return Stop("max_provider_invocations", "routing profile max_provider_invocations reached")
        if int(state["llm_calls"]) >= int(state["max_llm_calls"]):
            return Stop("max_llm_calls", "max_llm_calls reached")
        return PROCEED


# ---------------------------------------------------------------------------------------------------------
# Corrections the person asks for (LIM-T01)
# ---------------------------------------------------------------------------------------------------------

def cut_by_run_limits(run: Mapping[str, Any]) -> bool:
    """Whether the cut of an exhausted run came from the run's own call or time limits (LIM-T01).

    Those are automatic limits, so the person may still ask for a correction. A cut by the provider's quota (the last
    model attempt that reached the provider failed with ``QuotaExceeded`` or ``RateLimitExceeded``) or by any other guard
    is not. An attempt a guard stopped before the call (``provider_invoked`` false, ``stage`` ``preflight``) says nothing
    about the provider and is skipped. The two error names are those of ``services.limits.LIMIT_ERRORS``, written here
    because the engine does not import services. One criterion for ``RunService.changes`` and for the actions the
    thread offers."""
    if not str(run.get("error") or "").startswith(("max_llm_calls reached", "max_run_seconds reached")):
        return False
    attempts = [payload for event in run.get("events") or [] if event.get("event_type") == "model_attempt"
                for payload in [event.get("payload") or {}]
                if payload.get("provider_invoked") is not False and payload.get("stage") != "preflight"]
    return not (attempts and attempts[-1].get("error_type") in ("QuotaExceeded", "RateLimitExceeded"))


def person_correction_pending(events: Iterable[Mapping[str, Any]] | None, step_id: str, attempt: int) -> bool:
    """Whether ``attempt`` of ``step_id`` is the answer to a correction the person asked for.

    A ``human_revision`` of the step reviewed at attempt N makes attempt N+1. Such an attempt does not count against the
    workflow's ``max_iterations``, which bounds automatic revision loops only. Any later attempt that no person
    asked for (N+2 and so on) is not covered, so those loops stay bounded as before."""
    last = None
    for event in events or ():
        payload = event.get("payload") or {}
        if event.get("event_type") == "human_revision" and payload.get("step_id") == step_id:
            last = payload.get("attempt")
    return type(last) is int and attempt <= last + 1


def grant_correction_allowance(store: Any, settings: Any, run_id: str, run: Mapping[str, Any], base_active: float) -> None:
    """A correction the person asked for gets its own call and time allowance.

    ``max_llm_calls`` and ``max_run_seconds`` stop *automatic* work. Each ``human_revision`` that no earlier
    ``correction_allowance`` covers raises the two limits of the run to ``used + the configured value`` (never lowers
    them) and clears the run's ``budget_exhausted`` flag, which a cut by those limits had set: the corrected result is
    reviewed like any other. The allowance is still a limit, spent by the retries and escalations of that correction, and
    the provider's own quota is untouched (a ``QuotaExceeded`` still stops the model). One event per revision."""
    last_revision = last_grant = 0
    for position, event in enumerate(run.get("events") or [], start=1):
        if event.get("event_type") == "human_revision":
            last_revision = position
        elif event.get("event_type") == "correction_allowance":
            last_grant = position
    if not last_revision or last_grant > last_revision:
        return
    calls = max(int(run["max_llm_calls"]), int(run["llm_calls"]) + int(settings.max_llm_calls))
    seconds = max(int(run["max_run_seconds"]), int(base_active) + 1 + int(settings.max_run_seconds))
    payload = {"max_llm_calls": calls, "max_run_seconds": seconds}
    from .db.database import utcnow          # imported here: the database module does not import this one

    with store.db.tx() as conn:               # the new limits and the event that records them, or neither
        conn.execute("UPDATE runs SET max_llm_calls=?, max_run_seconds=?, budget_exhausted=0 WHERE id=?",
                     (calls, seconds, run_id))
        cursor = conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                              (run_id, utcnow(), "correction_allowance", json.dumps(payload, ensure_ascii=False)))
        event_id = cursor.lastrowid
    for listener in tuple(getattr(store.db, "event_listeners", ())):      # what ``Database.log_event`` tells the live views
        try:
            listener(run_id, "correction_allowance", payload, event_id)
        except Exception:  # noqa: BLE001 - a listener never fails a run
            pass


# ---------------------------------------------------------------------------------------------------------
# Building the chain
# ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class GuardEnvironment:
    """What the guards of one scope need from their host. A new guard reads what it needs from here, so adding
    one never changes the engine's signature: add a field and use it in :func:`build_call_guards`."""

    scope: str = SCOPE_RUN_STEP
    catalog: "Catalog | None" = None
    routing_profile_id: str | None = None
    routing_profile: Mapping[str, Any] | None = None
    # The context profile in force, or None for "no compaction guard". Off by default (a disabled profile
    # adds no guard either).
    context_settings: "ContextSettings | None" = None
    # Records an event of the run or task in flight (``emit("context_warning", payload)``); None records nothing.
    emit: Callable[[str, dict[str, Any]], None] | None = None
    # The document policy in force (``WorkflowEngine.document_policy``, BDI-T02), or None for "the barrier is off":
    # then there is no content guard and nothing changes (contract BDI-T01 section 8).
    document_policy: "DetectionPolicy | str | None" = None
    # The availability policy in force (AVA-T01), or None for "off": then there is no inbox guard and nothing changes.
    availability: "AvailabilityPolicy | None" = None
    # How the budgets are read (USO-T04, ``budget_cut.policy_from_settings``), or None for "no budget is configured":
    # then there is no budget guard and nothing changes.
    budgets: "BudgetPolicy | None" = None
    egress: Mapping[str, Any] | None = None
    secret_approvals: Any = None
    secret_approval_id: str | None = None


def build_call_guards(env: GuardEnvironment, *, cancel: Callable[[CallContext], None] | None = None,
                      reserve: Callable[[CallContext], None] | None = None) -> CallGuardChain:
    """The chain of one run step (or task): limits always, the others when their feature is on.

    This is the one place guards are registered. CNT-T04/T10 (limit hold) add theirs below with one line each. The
    inbox guard (AVA-T03) exists only with an enabled availability policy, and the budget guard (USO-T04, order 300)
    only when a budget is configured.
    """
    guards: list[CallGuard] = [RunLimitsGuard(env.routing_profile_id, env.routing_profile)]
    if env.availability is not None and env.availability.enabled:     # AVA-T03: the person's inbox, order 50
        from .services.inbox import UserInboxGuard
        guards.append(UserInboxGuard(env.availability, emit=env.emit))
    if env.context_settings is not None and env.context_settings.enabled:
        # Imported here: the context package builds on this module, not the other way round.
        from .context.estimate import ContextGuard
        guards.append(ContextGuard(env.context_settings, emit=env.emit))
    if env.document_policy is not None:      # BDI-T04: no policy, no barrier, no guard
        from .document_review import UntrustedContentGuard
        guards.append(UntrustedContentGuard(env.document_policy))
    if env.budgets is not None:              # USO-T04: no budget configured, no guard
        from .budget_cut import BudgetGuard
        guards.append(BudgetGuard(env.budgets, emit=env.emit))
    from .secret_gate import SecretGuard
    guards.append(SecretGuard(egress=env.egress, emit=env.emit,
                              approvals=env.secret_approvals, approval_id=env.secret_approval_id))
    if env.egress is not None and env.egress.get("action", "off") not in ("off", False):
        from .egress_guard import EgressGuard
        guards.append(EgressGuard(env.egress, emit=env.emit, secret_guarded=True))
    # CNT-T04/T10: guards.append(QuotaHoldGuard(env))
    return CallGuardChain(guards, cancel=cancel, reserve=reserve)
