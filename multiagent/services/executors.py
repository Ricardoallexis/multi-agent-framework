# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Executor services (ORQ-6, ORQ-T51): run an ORQ task in an executor application and bring its result back.

An executor application is an agent app driven through its official CLI (Codex, Claude, Copilot; adapters ORQ-T48 to
T50, process executor ORQ-T47, contract ``ORQ-6-contrato.md`` sections 5 to 9). Available as
``ApplicationServices.executors``.

``run_task`` takes a task that is ready (or claimed, paused or in ``needs_changes`` by the same actor), picks the
executor (the one asked for, or the one an assignment preview selects), builds the launch request (the task envelope
of ORQ-3, a work folder the framework owns, the permission the task scope allows) and launches it through the
adapter. The session id and the attempt are recorded as team events. The result envelope the app printed goes in
through ``envelopes.submit_result``: the task becomes ``submitted`` and the gate (ORQ-2) decides, never this service.
This module changes a task only through ``tasks.transition`` (``claim``, ``start``, ``resume``, ``pause``),
``tasks._hand_over`` (CNT-T05: ``unclaim`` and ``claim`` in one transaction), ``submit_result`` and, for a checkpoint,
``envelopes.set_task_context``; it never accepts, fails or reopens a task and writes no task row itself.

Failures are recorded, not retried. An attempt without a valid result envelope, a timeout, a cancellation or a block
by an open session leaves the task ``paused`` with trimmed evidence in the ``executor.attempt_finished`` event; a quota
or limit error also pauses the executor (``executor.paused``) and holds the task (below). Nothing loops: the next run is
asked for by a person or by the orchestrator.

Background launch and heartbeat (AVA-T02, contract ``AVA-T01-contrato.md``, ``availability.py``). Off unless the availability
policy is on (``ExecutorServices.availability``, else the ``availability_*`` settings): then nothing here changes and
``run_task`` waits for the whole attempt. With it on, ``run_task`` (not ``resume_task`` nor the automatic resumptions) does
every check, takes the task to ``running`` and records the routing in the caller's thread, then runs the adapter call and the
record of its end in a thread of its own (``background``: at most one attempt per task and ``MAX_BACKGROUND_ATTEMPTS`` in all)
while the caller waits at most ``policy.foreground_wait(timeout)``. If the attempt ended by then the caller gets the normal
answer; if not, ``outcome`` ``running`` (the HTTP route answers 202) and the attempt goes on. While it goes on, the process
watch that detects the 15 minute stall (CNT-T09) also writes ``executor.heartbeat`` every ``heartbeat_seconds``
(``{attempt, elapsed_seconds, idle_seconds, attention}``: numbers and the attention kind, never the prompt or the output;
``idle_seconds`` is the one the stall was judged on, from the process output or the session metadata, and null when it
cannot be known, as for a Claude CLI without session metadata).
Every background attempt ends with ``executor.attempt_finished`` (an internal failure is recorded as ``failed`` with the
exception type only) and its task ``submitted`` or ``paused`` as in a synchronous run. The program is the process executor's
(argument list, no shell, tree kill); ``background.cancel(task_id)`` kills its tree, and ``background.shutdown()`` (the API
on stop, and at interpreter exit) kills every one so no program outlives the process that launched it.

Hold and resume (CNT-T04, contract ``CNT-T01-contrato.md`` sections 4 and 10, ``hold.py``): an attempt that ends at a
usage limit leaves the task ``paused`` with a *hold* in ``tasks.execution_json`` (``continuity.hold``: the limit class,
window, ``reset_at`` with its source and confidence, ``resume_after``, the executor, the agent that owned the task, the
session, the attempt, the checkpoint and the settings the session started with). The task pause and its hold are one
lifecycle update (``tasks.transition(..., continuity=...)``), so a task is never paused by a limit without its hold; the
events ``executor.paused`` and ``continuity.held`` go with it. No state and no migration are added.

- ``plan_limit`` and ``limit_unknown`` with a ``high`` or ``medium`` reset time wait until ``reset_at`` plus 2 minutes
  (executor pause and hold alike); without a reliable time the operator's fixed pause applies (``with_configured_reset``:
  source ``configured``, confidence ``low``), as before.
- ``credits_exhausted`` pauses the executor with no end (``open_ended``) until a person calls ``lift_pause``
  (``executor.pause_lifted``); the held tasks are then due.
- ``rate_limited`` retries after 60 s and then 300 s (no checkpoint while retries remain, ``retries_left``); after that the
  fixed pause applies.
- ``resume_due`` (the orchestrator, or the CLI ``executors resume-due``) resumes every hold that is due, only when the task
  is still ``paused`` and still owned by the agent that held it, one task at a time. It continues the session with its
  history (``resume_task``, passing the recorded model, effort and permission again) when the executor declares ``resume``
  and nothing drifted; otherwise it relaunches from the checkpoint and retires the old session
  (``checkpoint_limit(retire="superseded")``). A session opened in an app in the last 12 hours is never resumed nor
  replaced. If the resumed run hits a limit again it is classified again and held again with ``failed_resumes`` one
  higher; three failed resumptions in a row stop the automatic resume (a person decides).
- Running or resuming a held task by hand clears its hold (``continuity.resumed`` with ``trigger`` ``manual``).

Waits by condition (CNT-T12, contract Amendment 2, ``waits.py``): a task can wait for something that is not a limit. Its
hold keeps the same keys and adds ``id`` and a sibling ``condition`` block; its ``reason`` is ``dependency``,
``integration_window`` or ``approval`` (a limit hold may carry a condition too: then the reset time and the condition must
both hold). Nothing in the activation uses a model: ``waits.py`` checks structured conditions against facts and a clock.

- ``hold_on_condition`` pauses a task (or adds the hold to a paused one) with a condition, a deadline (default 12 hours) and
  the executor and session that will be resumed. ``record_approval`` records a person's decision for an ``approval``
  condition (never one made by ``system:*`` or ``executor:*``).
- ``check_waits`` is what a watcher (CNT-T13) calls on each tick: it checks every condition, records the instant a
  condition is first seen to hold (``condition.met_at`` and ``continuity.condition_met``, once) or that its deadline passed
  (``continuity.condition_expired``, once; the hold is then ``blocked`` with ``condition_expired`` and a person decides),
  and returns the honest progress of each wait. Checks themselves are not events.
- ``resume_met`` sits next to ``resume_due`` and releases the holds that carry a condition. It checks the condition again
  at that moment, and revalidates the attempt before resuming: the task is still ``paused``, still owned by the agent that
  held it, in the same attempt and hold (``hold_id`` and ``attempt`` when the watcher names them), the condition holds
  now, the minimum time (``resume_after``) has passed, the executor is not paused, and the session is not open in an app.
  Then it resumes as ``resume_due`` does (``continuity.resumed`` with ``trigger`` ``condition``). ``resume_due`` leaves these
  holds to it (``has_condition``).
- The world a condition reads comes from the task store (dependencies, the approvals recorded in the condition) and from two
  hooks a watcher process sets: ``integration_source(window_id, now)`` and ``approval_source(task_id, approval_id)``.

Continuity policy and hand-off (CNT-T05, contract sections 7 to 9, ``handoff.py``, ``selection.select_for_handoff``): what to do
with a task held by a usage limit is a policy, not a habit. The settings ``continuity_mode`` (``manual`` always asks;
``semi_auto``, the default, waits for a reset that is ``continuity_wait_threshold_hours`` away or less and otherwise hands
the task over without asking), ``continuity_max_handoffs`` (1 to 3) and ``continuity_candidates`` (per executor ``no_ask``,
``ask`` or ``never``) are the profile level; a task's own continuity block can only restrict them (``continuity_policy`` shows
the result). The hold records the mode and the continuity block the limit of hand-offs (``max_handoffs``) when the limit hits.

- ``handoff_task`` hands a held task over to another executor on a person's decision: in one transaction it records
  ``continuity.decided`` (unless the card of CNT-T06 already did), retires the original session (``session.retired``,
  ``handoff``), unclaims the task and has ``executor:<delegate>`` claim it (``tasks._hand_over``), counts the hand-off,
  extends the ``chain`` and clears the hold, and records ``continuity.handoff``; then it launches the delegate with the model
  of its own at the original level or cheaper (never a higher one: a limit does not climb a level). The task envelope
  carries the checkpoint (patch reference and summary), the history of the session never travels. Repeating a
  ``decision_id`` returns the first result; a stale ``expected_revision`` is refused. Hard limits for everyone: only a task
  ``paused`` by a limit (never in a gate, an integration or a commit; ``integration_active`` says the host is integrating: by
  default the shared marker of the watcher, and without a hook, or with one that fails, it counts as integrating),
  hand-offs left, a delegate that is enabled, not paused and neither the first executor nor already in the chain, and no
  budget set to ``cut`` at its limit; those that are not in the task row are checked again inside the transaction. A
  person's decision from the card (``hold.decision`` ``delegate`` with ``hold.candidate``, ``hold.blocked``
  ``delegation_requested``) is the executor to hand over to. The local API passes ``recorded_only``: its caller is not
  authenticated, so it carries out only that recorded decision (``person_required`` for any other executor); choosing
  another one, even one marked ``never``, is the card's job, on the GUI route with the token.
- ``decide_holds`` (the orchestrator, or the CLI ``executors decide-holds``) applies the policy to every task held by a limit:
  it carries out a person's recorded delegate decision in any mode, leaves a person's wait to ``resume_due``, and otherwise
  asks ``handoff.decide``: ``wait`` (recorded once, ``continuity.decided`` ``by`` ``policy:<mode>``), ``delegate`` (only to
  an enabled, unpaused, unused ``no_ask`` subscription candidate at the original level or cheaper, and only with a saved
  patch, a project that is not private, an unexhausted budget, hand-offs left and the same work folder) or ``ask`` (nothing
  is written; the card of the person shows it). If the delegate also runs out, the next hand-off counts; with the maximum
  reached the policy waits when the reset is reliable and asks when it is not.

``resume_task`` continues the session the framework created for the task (the latest ``executor.session_recorded``
event), never one it did not create, and refuses a session that was opened in an app in the last 12 hours
(``session.opened_in_app``, written by ``record_app_open`` when the GUI launches Claude or ``open_target`` hands out a
target). Limitation (ORQ-6 section 6): a link the browser follows (``codex://``, ``ghapp://``) or an app the user opens
alone leaves no record, so the 12 hour rule cannot cover them; Codex blocks writes on a session open in its app by
itself. ``session_guard`` is the same check as a function, passed to the adapters that need one (Copilot).

``open_target`` returns the adapter's open target for the recorded session (the one the GUI uses) and records the open.

Effects and the document barrier (BDI-T04, contract ``BDI-T01-contrato.md`` section 8). While no document policy is
active (``WorkflowEngine.document_policy`` is ``None``, the default) nothing here changes. With one active, every
attempt (``run_task``, ``resume_task`` and the automatic resumptions all end in ``_execute``) first needs the approval of
the user or of an approved contract for the effects it has, checked with ``document_actions.authorize`` through
``launch_safety.require_effect`` before the task changes: launching the executor (``launch_executor``, target the
executor id) and, when ``file_scope`` is not empty, writing (``write``, one check per path). The same goes for
``open_target`` (an ``open_application`` effect); the result of an attempt carries ``open_target`` only if that effect
is approved too (otherwise it is ``None``). An automatic relaunch checks the launch effects before it retires the held
session, so a refusal leaves the session as it was. The evidence comes from ``effect_evidence``, a hook the host that
turns the barrier on sets (``effect_evidence(action, context) -> Iterable[Approval]``); built from the user's own
messages or from contracts the user approved, never from content. Without it, or without an approval that covers the
action, the call fails with ``effect_not_authorized`` and nothing was started or recorded. The detector's verdict is
never evidence.

Checkpoint (CNT-T03, contract ``CNT-T01-contrato.md`` section 5): when an attempt ends at a limit, ``checkpoint_limit``
saves what the agent did. The diff of the work folder (``git diff --binary HEAD`` plus the untracked files) is stored as a
blob, a summary is written without a model (changed files and last agent message; objective and gate), the blob goes in the
task's ``context_refs`` and the summary, as text, in up to three ``constraints`` (the next agent does not read blobs), and
the events ``continuity.limit_detected`` and ``task.checkpointed`` are recorded. It is idempotent per task, attempt and
session: a second detection returns the first checkpoint and writes nothing. ``latest_checkpoint`` reads it back.
``retire_session`` marks a session as retired (``session.retired``): the framework never resumes it
(``resume_task`` refuses it, ``session_guard`` is False) and never writes inside the app. The checkpoint does not retire
the session by itself, because a wait that ends can resume it with its history (contract section 10); the hand-off and the
relaunch retire it (``retire="handoff"`` or ``"superseded"``).

Model and effort (ECO-T08) come from the caller or, when a profile is given, from the model routing decision of the
assignment profile's routing profile (economic or not) for the task's ``step_kind``, through ``_routing_selection``.
The caller's values always win (source ``user``). A value from the profile is sent only when the executor declares it
takes it: a model that is not in its ``model_support`` values, or an effort its ``effort_support`` does not list (Claude's
effort is unknown, so it is never sent), is dropped and the reason goes in ``notes``. The router is asked per executor
with the models it cannot receive excluded (Copilot refuses every Claude model), so each executor gets the best model it
can take. The assignment preview shows the same selection for each executor (``decision.evidence.executor_requests``).

Events (team event log, no migration): ``executor.session_recorded`` (``ref`` is the session id),
``executor.routing_selected`` (``ref`` is the executor id: the model and effort of the attempt, where each came from,
the routing profile, mode, step kind, level, price source, effort source and the candidates skipped for this executor),
``executor.attempt_finished`` (outcome, exit code, usage, trimmed evidence), ``executor.heartbeat`` (``ref`` is the
executor id; a background attempt is alive), ``executor.paused`` (``ref`` is the
executor id; the limit class, ``reset_*`` and ``open_ended`` when a limit caused it), ``executor.pause_lifted`` (``ref``
is the executor id: who lifted it and why), ``session.opened_in_app`` (``ref`` is the session id),
``continuity.limit_detected`` (``ref`` is the executor id), ``task.checkpointed`` (``ref`` is the patch blob, empty
without a patch), ``session.retired`` (``ref`` is the session id; reason ``context_threshold`` means the next attempt
starts a new session). At that boundary the result's ``context_handoff`` is None or
``{ratio, level, snapshot_ref, retired_session, checkpoint, inbox_cursor}``; the new session reads messages after
that cursor. ``continuity.held`` (the task now waits: reason,
``resume_after``, mode, outcome, hand-offs, and the condition's summary for a wait by condition),
``continuity.resumed`` (``ref`` is the executor id: ``resume`` or ``relaunch``, minutes waited, ``config_drift``),
``continuity.condition_met`` and ``continuity.condition_expired`` (``ref`` is the executor id; the type, the parts with what
each said, minutes waited, the deadline; at most once per hold), ``continuity.decided`` (``ref`` is the decision id: the
decision, ``by`` ``user`` or ``policy:<mode>``, the candidate and what would be sent) and ``continuity.handoff`` (``ref`` is the
executor the task went to: where from, the number of the hand-off, the decision id, the model and level, whether the work folder
is the same).

Errors (``ServiceError``): ``not_found`` (task, executor), ``invalid_request`` (malformed ids or limits, a request the
adapter refuses) and ``invalid_state`` (``details.reason`` says why: ``executor_disabled``, ``executor_without_adapter``,
``executor_paused``, ``executor_unavailable``, ``task_not_runnable``, ``no_stored_session``, ``session_open_in_app``,
``assignment_not_selected``, ``model_not_supported``, ``effort_not_supported``, ``workdir_invalid``, ``session_retired``,
``hold_changed``, ``relaunch_failed``, ``effect_not_authorized``, for a hand-off ``person_required``, ``not_held``,
``stale_revision``, ``assignee_changed``, ``no_delegate_decision``, ``handoffs_exhausted``, ``executor_already_used``,
``budget_exhausted``, ``integration_in_progress``, ``policy_changed`` and ``decision_id_reused``, for a background run ``availability_policy_invalid``,
``background_attempt_active``, ``background_capacity`` and ``background_closed``, and for a wait by condition ``task_not_holdable``,
``already_held``, ``task_without_assignee``, ``not_waiting_for_approval``, ``approver_not_allowed``,
``approver_mismatch``, ``already_decided``, ``condition_not_met``). ``model_not_supported``
and ``effort_not_supported`` refuse a value the caller named; one that came from the profile is dropped instead.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from .. import executor_pins as _pins
from ..context.estimate import LEVEL_THRESHOLD
from ..db.envelope_store import HARD_MAX_BLOB_BYTES
from ..document_actions import Action, ActionKind, Approval
from ..errors import MultiAgentError
from ..executors import antigravity as _antigravity, codex as _codex, copilot as _copilot, launch_safety, process
from ..executors.contract import ExecutorApplicationAdapter, ExecutorLaunchRequest, FailureKind, OpenTarget, Permission
from ..executors.limits import utc_iso
from ..executors.privacy import decide_privacy, resolve_model_privacy
from ..executors.selection import (
    SOURCE_HANDOFF, ExecutorSelection, select_for_executor, select_for_handoff, select_for_resume,
)
from ..routing.executor_capabilities import CapabilityEvidence, SupportState
from ..routing_policy import MODEL_LEVELS, STEP_KINDS
from ..sessions.models import is_uuid
from ..tasks.envelopes import MAX_CONSTRAINTS, MAX_CONTEXT_REFS, EnvelopeError
from . import checkpoint as _checkpoint
from . import handoff as _handoff
from . import hold as _hold
from . import waits as _waits
from .availability import (
    ATTEMPT_FINISHED_EVENT, FOREGROUND_RUNNING, HEARTBEAT_EVENT, AvailabilityPolicy, AvailabilityPolicyError,
    policy_from_settings,
)
from .errors import INVALID_REQUEST, INVALID_STATE, NOT_FOUND, ServiceError, service_errors, translate
from .tasks import _TASK_ID, _text

if TYPE_CHECKING:
    from . import ApplicationServices

__all__ = ["ExecutorServices", "BackgroundAttempts", "EVENT_TYPES", "APP_OPEN_GUARD"]

_logger = logging.getLogger(__name__)

SESSION_EVENT = "executor.session_recorded"
ATTEMPT_EVENT = ATTEMPT_FINISHED_EVENT       # "executor.attempt_finished": the one name the availability policy shares
ATTENTION_EVENT = "executor.attention"
ROUTING_EVENT = "executor.routing_selected"
PAUSE_EVENT = "executor.paused"
OPEN_EVENT = "session.opened_in_app"
LIMIT_EVENT = "continuity.limit_detected"
CHECKPOINT_EVENT = "task.checkpointed"
RETIRED_EVENT = "session.retired"
PAUSE_LIFTED_EVENT = "executor.pause_lifted"
HELD_EVENT = "continuity.held"
RESUMED_EVENT = "continuity.resumed"
CONDITION_MET_EVENT = "continuity.condition_met"
CONDITION_EXPIRED_EVENT = "continuity.condition_expired"
DECIDED_EVENT = "continuity.decided"      # a decision about a held task; ``ref`` is the decision id (CNT-T05, CNT-T06)
HANDOFF_EVENT = "continuity.handoff"      # the task changed hands; ``ref`` is the executor it went to
EVENT_TYPES = (SESSION_EVENT, ROUTING_EVENT, ATTEMPT_EVENT, ATTENTION_EVENT, HEARTBEAT_EVENT, PAUSE_EVENT, OPEN_EVENT,
               LIMIT_EVENT, CHECKPOINT_EVENT, RETIRED_EVENT, PAUSE_LIFTED_EVENT, HELD_EVENT, RESUMED_EVENT,
               CONDITION_MET_EVENT, CONDITION_EXPIRED_EVENT, DECIDED_EVENT, HANDOFF_EVENT)
PIN_EVENT = "executor.version_changed"    # AXI-T07: an executor's version or digest differs from its pin (see executor_pins)
PIN_ACTOR = "system:executor-pins"
EVENT_TYPES += (PIN_EVENT,)
WATCHER_ACTOR = "system:watcher"          # who records the end of a wait by condition (CNT-T12); it resumes as the assignee
AUTO_TRIGGERS = ("auto", "condition")     # resumptions the service makes by itself: a limit that returns counts as a failed one
NOT_APPROVERS = ("system:", "executor:")  # the framework never approves in the name of a person
RETIRE_REASONS = ("handoff", "superseded", "context_threshold")
DEFAULT_MAX_HANDOFFS = 1                  # contract section 7: the default of ``continuity_max_handoffs``
_DECISION_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

APP_OPEN_GUARD = timedelta(hours=12)      # a session opened in an app is not resumed for this long (ORQ-6 section 6)
MAX_TIMEOUT_SECONDS = 86400
MAX_EVIDENCE_CHARS = 800                  # trimmed output kept as evidence of a failed attempt
MAX_NOTE_CHARS = 160
MAX_NOTES = 6
MAX_BACKGROUND_ATTEMPTS = 8               # attempts (and so programs) one service keeps in the background at once (AVA-T02)
BACKGROUND_SHUTDOWN_SECONDS = 10          # how long a shutdown waits for the cancelled attempts to record their end
BACKGROUND_EXIT_SECONDS = 5               # the same, at interpreter exit
EVENT_PAYLOAD_BUDGET = 1900               # the team event payload limit is 2048 bytes
_PROBE_SESSION = "00000000-0000-4000-8000-000000000000"   # only to build an argv for the pre-flight check of Claude
_EXECUTOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SOURCE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_FAILURE_OUTCOMES = ("timeout", "cancelled", "quota_exhausted", "blocked_open_session", "permission_blocked")


def _invalid(field_name: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field_name, **details})


def _conflict(reason: str, message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})


def _missing(kind: str, identifier: str) -> ServiceError:
    return ServiceError(NOT_FOUND, f"{kind} not found: {identifier}", status=404, details={kind.lower(): identifier})


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _collapse(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _tail(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else "..." + flat[-(limit - 3):]


def _fit(payload: dict[str, Any]) -> dict[str, Any]:
    """Shrink an event payload to the budget: the evidence first, then the notes and the usage."""
    payload = dict(payload)

    def size() -> int:
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    while size() > EVENT_PAYLOAD_BUDGET and len(str(payload.get("evidence", ""))) > 40:
        payload["evidence"] = str(payload["evidence"])[: len(str(payload["evidence"])) // 2]
    for key in ("skipped", "notes", "usage"):
        if size() > EVENT_PAYLOAD_BUDGET:
            payload.pop(key, None)
    return payload


def _command_view(record: Any) -> dict[str, Any]:
    """The command that ran without local paths: the program's name and its arguments, folders as ``<path>``."""
    argv = list(getattr(record, "argv", ()) or ())
    shown = [os.path.basename(argv[0])] if argv else []
    shown += ["<path>" if os.path.isabs(str(item)) else str(item) for item in argv[1:]]
    return {"argv": shown, "exit_code": getattr(record, "exit_code", None)}


def _effective_of(value: Any) -> dict[str, Any] | None:
    """What an adapter result says the app ran with: only the names a resume must keep (``hold.DRIFT_KEYS``)."""
    if not isinstance(value, Mapping):
        return None
    found = {name: getattr(value[name], "value", value[name]) for name in _hold.DRIFT_KEYS if name in value}
    return found or None


def _session_settings(session: Mapping[str, Any]) -> dict[str, Any]:
    """The settings a recorded session started with (the ``executor.session_recorded`` payload), as ``hold.DRIFT_KEYS``."""
    return {"model": session.get("model"), "effort": session.get("effort"), "permission": session.get("permission"),
            "adapter": session.get("adapter_id")}


def _workdir_id(folder: Path | str) -> str:
    """A short fingerprint of a work folder: the events say whether it changed without carrying a local path."""
    return hashlib.sha256(os.path.normcase(os.path.abspath(str(folder))).encode("utf-8")).hexdigest()[:12]


def _supports(evidence: CapabilityEvidence, value: str | None) -> bool:
    """False only when the executor's evidence says it does not take ``value`` (unknown evidence passes)."""
    if value is None:
        return True
    if evidence.status is SupportState.UNSUPPORTED:
        return False
    return not (evidence.status is SupportState.SUPPORTED and evidence.values is not None and value not in evidence.values)


def _target_view(target: OpenTarget) -> dict[str, Any]:
    value = list(target.value) if not isinstance(target.value, str) else target.value
    return {"kind": target.kind, "value": value, "needs_confirmation": bool(target.needs_confirmation)}


def _launch_blocker(adapter: Any) -> str | None:
    """The reason code an adapter gives for refusing a launch right now (``launch_blocker``, GEM-T03), or None."""
    check = getattr(adapter, "launch_blocker", None)
    if not callable(check):
        return None
    try:
        reason = check()
    except Exception:  # noqa: BLE001 - a check that breaks proves nothing: refuse
        _logger.warning("the launch check of an executor failed", exc_info=True)
        return "launch_check_failed"
    return reason if isinstance(reason, str) and reason else None


def _check_support(evidence: CapabilityEvidence, value: str | None, what: str, executor_id: str) -> None:
    """Refuse a model or effort the executor's evidence says it does not take; unknown evidence passes through."""
    if value is None:
        return
    if evidence.status is SupportState.UNSUPPORTED:
        raise _conflict(f"{what}_not_supported", f"{executor_id} does not take a {what} per call", executor_id=executor_id)
    if evidence.status is SupportState.SUPPORTED and evidence.values is not None and value not in evidence.values:
        raise _conflict(f"{what}_not_supported", f"{executor_id} has no evidence for {what} {value!r}",
                        executor_id=executor_id, supported=sorted(evidence.values))


@dataclass
class _Plan:
    executor_id: str
    descriptor: Any
    adapter: Any
    model: str | None
    effort: str | None
    declaration: Mapping[str, Any]
    decision: Mapping[str, Any] | None = None
    selection: ExecutorSelection | None = None


@dataclass
class _Attempt:
    """What one launch or resume produced, in the shape the service records."""
    status: str                                   # a RunStatus value, or "launch_refused"
    session_id: str | None = None
    envelope: Mapping[str, Any] | None = None
    usage: Mapping[str, Any] | None = None
    command: dict[str, Any] | None = None
    exit_code: int | None = None
    notes: list[str] = field(default_factory=list)
    evidence: str = ""
    limit: Mapping[str, Any] | None = None        # what the adapter says about a limit (CNT-T02), as it came
    failure_kind: FailureKind | None = None
    last_message: str = ""                        # the last text the agent wrote, from the tail of its output
    effective: Mapping[str, Any] | None = None    # what the app says it ran with (model, effort, permission), if it does


@dataclass
class _Launch:
    """One attempt once its task is ``running``: what the adapter call and the record of its end need."""
    task: dict[str, Any]
    plan: _Plan
    actor: str
    timeout: int
    resume_session: str | None
    recorded: Mapping[str, Any] | None
    trigger: str
    prior: dict[str, Any]
    permission: Permission
    workdir: Path
    request: ExecutorLaunchRequest
    claude_session_id: str | None
    cause: str
    mode: str
    view: dict[str, Any]
    call: Callable[[ExecutorLaunchRequest], "_Attempt"]    # the adapter call, named once in ``_execute`` (BDI-T04 order check)
    # True once this attempt took its task out of ``running`` (submitted or paused it). From then on the task is no longer
    # this attempt's to touch: a resume keeps the attempt number, so a later run of the same task looks the same as this one.
    settled: bool = False


class _Observation(NamedTuple):
    """What the process watch found about an attempt: the attention kind (``stalled`` or ``approval_pending``) or None, and
    the seconds since its last sign of activity, from the process output or the session metadata, whichever is later.
    ``idle_seconds`` is None when it cannot be known: the Claude CLI prints nothing while it works, so without session
    metadata its silence says nothing; and while the session has tests open it is not idle."""
    attention: str | None
    idle_seconds: float | None


class _Heartbeat:
    """When the next ``executor.heartbeat`` of an attempt is due (monotonic clock; the process watch asks)."""

    def __init__(self, interval_seconds: float, started: float):
        self.interval = float(interval_seconds)
        self.started = started
        self._last = started

    def due(self) -> bool:
        now = time.monotonic()
        if now - self._last < self.interval:
            return False
        self._last = now
        return True

    def elapsed(self) -> float:
        return round(max(0.0, time.monotonic() - self.started), 1)


class BackgroundAttempt:
    """One attempt that runs in a thread of its own so that the caller of ``run_task`` can be told ``running`` (AVA-T02).

    ``done`` is set once the attempt ended and recorded how (``result`` is then the dictionary ``run_task`` would have
    returned, or the failed shape when the framework itself broke). ``cancel`` kills the process tree of the program the
    attempt started; asked before the program exists, it kills it the moment it starts.
    """

    def __init__(self, task_id: str, executor_id: str):
        self.task_id = task_id
        self.executor_id = executor_id
        self.attempt: int | None = None
        self.started = time.monotonic()
        self.done = threading.Event()
        self.result: dict[str, Any] | None = None
        self.attempt_recorded = False             # ``executor.attempt_finished`` was written: a failure must not write a second
        self.outcome: str | None = None           # ...and the outcome and run status it recorded
        self.run_status: str | None = None
        self._lock = threading.Lock()
        self._handle: process.ProcessHandle | None = None
        self._cancel_requested = False

    def adopt(self, handle: process.ProcessHandle) -> None:
        """Called (``process.capturing``) with the handle of each program the attempt starts."""
        with self._lock:
            self._handle = handle
            cancel = self._cancel_requested
        if cancel:
            handle.cancel()

    def cancel(self) -> bool:
        """Ask the attempt to stop. True when a running program was killed now; the outcome says ``cancelled``."""
        with self._lock:
            self._cancel_requested = True
            handle = self._handle
        return handle.cancel() if handle is not None else False


class BackgroundAttempts:
    """The attempts one ``ExecutorServices`` keeps in the background: at most one per task and ``limit`` in all.

    Nothing here waits without a limit. ``shutdown`` is what the API calls when it stops and what runs at interpreter
    exit: it cancels every attempt, so no program outlives the process that launched it.
    """

    def __init__(self, limit: int = MAX_BACKGROUND_ATTEMPTS):
        self._limit = limit
        self._lock = threading.Lock()
        self._active: dict[str, BackgroundAttempt] = {}
        self._closed = False
        _REGISTRIES.add(self)

    def reserve(self, task_id: str, executor_id: str) -> BackgroundAttempt:
        """A place for the attempt of ``task_id``; refused (``invalid_state``) when it already has one or the limit is reached."""
        with self._lock:
            if self._closed:
                raise _conflict("background_closed", "The background is closed (the service is stopping): "
                                "nothing new is launched")
            if task_id in self._active:
                raise _conflict("background_attempt_active", f"{task_id} already has an attempt in the background",
                                task_id=task_id)
            if len(self._active) >= self._limit:
                raise _conflict("background_capacity", f"{self._limit} attempts already run in the background; "
                                "wait for one to end", limit=self._limit)
            attempt = BackgroundAttempt(task_id, executor_id)
            self._active[task_id] = attempt
            return attempt

    def release(self, attempt: BackgroundAttempt) -> None:
        with self._lock:
            if self._active.get(attempt.task_id) is attempt:
                del self._active[attempt.task_id]

    def get(self, task_id: str) -> BackgroundAttempt | None:
        with self._lock:
            return self._active.get(task_id)

    def active(self) -> list[dict[str, Any]]:
        """The attempts running now, without any content: task, executor, attempt number and seconds elapsed."""
        with self._lock:
            attempts = list(self._active.values())
        return [{"task_id": a.task_id, "executor_id": a.executor_id, "attempt": a.attempt,
                 "elapsed_seconds": round(max(0.0, time.monotonic() - a.started), 1)} for a in attempts]

    def cancel(self, task_id: str) -> bool:
        """Cancel the attempt of a task; False when it has none running (or its program is not up yet)."""
        attempt = self.get(task_id)
        return attempt.cancel() if attempt is not None else False

    def join(self, timeout: float = BACKGROUND_SHUTDOWN_SECONDS) -> bool:
        """Wait up to ``timeout`` seconds for the attempts running now; True when none is left."""
        with self._lock:
            attempts = list(self._active.values())
        deadline = time.monotonic() + timeout
        for attempt in attempts:
            attempt.done.wait(max(0.0, deadline - time.monotonic()))
        return all(attempt.done.is_set() for attempt in attempts)

    def reopen(self) -> None:
        """Accept new attempts again after a ``shutdown`` (the API does it when it starts)."""
        with self._lock:
            self._closed = False

    def shutdown(self, timeout: float = BACKGROUND_SHUTDOWN_SECONDS) -> int:
        """Close the background (``reserve`` is refused from now on: ``background_closed``), cancel every attempt and wait
        up to ``timeout`` seconds for them to record their end; returns how many still run."""
        with self._lock:
            self._closed = True
            attempts = list(self._active.values())
        for attempt in attempts:
            attempt.cancel()
        deadline = time.monotonic() + timeout
        for attempt in attempts:
            attempt.done.wait(max(0.0, deadline - time.monotonic()))
        return sum(1 for attempt in attempts if not attempt.done.is_set())


_REGISTRIES: weakref.WeakSet[BackgroundAttempts] = weakref.WeakSet()


def _stop_background_at_exit() -> None:
    """No program started in the background outlives the interpreter (POSIX children are not tied to their parent)."""
    for registry in list(_REGISTRIES):
        try:
            registry.shutdown(BACKGROUND_EXIT_SECONDS)
        except Exception:  # noqa: BLE001 - exiting: nothing may keep the interpreter from finishing
            _logger.warning("stopping the background attempts failed", exc_info=True)


atexit.register(_stop_background_at_exit)


class ExecutorServices:
    def __init__(self, services: ApplicationServices, *, registry: Any = None,
                 clock: Callable[[], datetime] | None = None):
        self._services = services
        self._registry = registry
        # A test sets ``clock`` to move time; every "until" and the 12 hour rule read it.
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self._checkpoint_lock = threading.Lock()      # one checkpoint at a time: the second detection finds the first
        # What a wait by condition cannot read from the task store (CNT-T12). A watcher process sets them to read the
        # integrations and the approvals wherever they are recorded; ``None`` means the service knows nothing of them.
        self.integration_source: Callable[[str, datetime], _waits.IntegrationFacts | None] | None = None
        self.approval_source: Callable[[str, str], _waits.ApprovalFact | None] | None = None
        # The approvals that allow an effect while a document policy is active (BDI-T04); None means "no evidence",
        # and then every gated effect is refused. Set by the host that turns the barrier on.
        self.effect_evidence: Callable[[Action, Mapping[str, Any]], Iterable[Approval] | None] | None = None
        # AXI-T07: before a launch the executor's version and digest are compared with its pin (``executor_pins``). ``None``
        # leaves the check off; a host that turns autonomy on sets a ``PinGuard``, and then a drift refuses the launch.
        self.pin_guard: _pins.PinGuard | None = None
        # The availability policy (AVA-T01). ``None`` reads it from the settings (``availability_*``), which are off unless
        # configured; a host or a test may set a policy here. With it on, ``run_task`` launches in the background.
        self.availability: AvailabilityPolicy | None = None
        # The attempts launched in the background (AVA-T02): ``cancel``, ``shutdown``, ``active`` and ``join``.
        self.background = BackgroundAttempts()
        # True while an integration of the work is going on (CNT-T05): a task is never handed over during one. By default it
        # reads the marker of the installation (``Settings.integration_marker``, the one ``multiagent watcher`` takes), so
        # the API and the CLI honour it without being told; a host that coordinates integrations another way replaces it.
        # It fails closed: ``None`` (nobody can tell), a hook that fails and a marker that cannot be read all count as
        # "integrating". Whoever integrates must create that file (exclusively) and remove it when done.
        self.integration_active: Callable[[], bool] | None = self._integration_marker_exists

    # -------- wiring --------
    @property
    def _tasks(self):
        return self._services.tasks

    @property
    def _store(self):
        return self._services.tasks.store

    @property
    def registry(self):
        """The executor registry of the assignment service (the same descriptors the selector sees)."""
        return self._registry if self._registry is not None else self._services.assignment.registry

    def build_adapters(self) -> list:
        """The adapters that ship with the framework, with this service's session guard and settings (see
        ``default_adapters``)."""
        from ..executors.defaults import default_adapters

        return default_adapters(session_guard=self.session_guard, settings=getattr(self._services, "settings", None))

    # -------- effects (BDI-T04) --------
    @property
    def document_policy(self):
        """The document policy in force (the engine's ``document_policy``), or ``None`` when the barrier is off."""
        return getattr(getattr(self._services.system, "engine", None), "document_policy", None)

    def _require_effect(self, action: Action, context: Mapping[str, Any]) -> None:
        """Refuse ``action`` unless the barrier is off or the evidence covers it (``effect_not_authorized``).

        The hook runs only with a policy active; if it fails, that is no evidence (fail closed).
        """
        policy = self.document_policy
        evidence = None
        if policy is not None and self.effect_evidence is not None:
            try:
                evidence = self.effect_evidence(action, context)
            except Exception:  # noqa: BLE001 - an evidence source that breaks proves nothing
                _logger.warning("the effect evidence source failed; the effect is refused", exc_info=True)
        try:
            launch_safety.require_effect(action, evidence, policy=policy)
        except launch_safety.EffectNotAuthorized as exc:
            raise _conflict(launch_safety.EFFECT_NOT_AUTHORIZED, f"{action.kind.value} is not authorized: "
                            f"{exc.result.reason}", action=action.kind.value, target=action.target,
                            task_id=context.get("task_id"), executor_id=context.get("executor_id")) from None

    def _require_launch_effects(self, task: Mapping[str, Any], executor_id: str, permission: Permission,
                                mode: str) -> None:
        """The effects of one attempt: launching ``executor_id`` and, with write permission, each path of ``file_scope``."""
        context = {"task_id": task["id"], "executor_id": executor_id, "mode": mode}
        self._require_effect(Action(ActionKind.LAUNCH_EXECUTOR, executor_id), context)
        if permission is Permission.WRITE:
            for path in task["file_scope"]:
                self._require_effect(Action(ActionKind.WRITE, path), context)
        self._require_pin(task, executor_id)         # AXI-T07 (both call sites: a relaunch is refused before it retires)

    def _require_pin(self, task: Mapping[str, Any], executor_id: str) -> None:
        """AXI-T07: refuse the launch when the executor's version or digest is not its pin (``executor_pin_blocked``).

        Off while ``pin_guard`` is None. A drift writes one ``executor.version_changed`` per observed change; if that
        cannot be written the launch is refused all the same. Nothing is accepted, updated or started here. The event is
        about the executor, not about a task: it carries no ``task_id`` or ``group_id`` (``ref`` is the executor).
        """
        guard = self.pin_guard
        if guard is None:
            return
        now = self.clock()
        check = guard.check(executor_id, now)
        if check.status == _pins.MATCHED:
            return
        details: dict[str, Any] = {"executor_id": executor_id, "status": check.status, "changed": list(check.changed),
                                   "candidate": check.candidate, "task_id": task["id"]}
        if check.status == _pins.DRIFT:
            try:
                with self._store.transaction() as conn:       # read, decide and write in ONE write transaction (two
                    previous = self._latest(conn, PIN_EVENT, ref=executor_id)        # launches cannot both record it)
                    if _pins.should_record(previous["payload"] if previous else None, check):
                        self._store.append_event(conn, PIN_EVENT, actor=PIN_ACTOR, ref=executor_id,    # no task data
                                                 cause=f"{executor_id} differs from its pin",
                                                 payload=_fit(_pins.event_payload(check, now)))
                details["event_recorded"] = True
            except Exception:  # noqa: BLE001 - the launch is refused either way; the answer says the record failed
                _logger.warning("the version change of %s could not be recorded", executor_id, exc_info=True)
                details["event_recorded"] = False
        raise _conflict("executor_pin_blocked", f"{executor_id} is blocked: {check.reason}", **details)

    def _may_hand_out_target(self, task_id: str, executor_id: str, source: str, session_id: str, actor: str) -> bool:
        """True when the open target of a session may be given to the caller (always, while no policy is active).

        With a policy, handing it out is an ``open_application`` effect: without an approval the result of an attempt
        simply carries no target (``open_target`` itself refuses with ``effect_not_authorized``).
        """
        if self.document_policy is None:
            return True
        try:
            self._require_effect(Action(ActionKind.OPEN_APPLICATION, f"{source}:{session_id}"),
                                 {"task_id": task_id, "executor_id": executor_id, "actor": actor, "mode": "open"})
        except ServiceError:
            return False
        return True

    # -------- events --------
    def _append(self, event_type: str, *, actor: str, task: Mapping[str, Any] | None = None, ref: str = "",
                cause: str = "", payload: dict[str, Any]) -> int:
        """Append one team event and return its ``seq``."""
        with self._store.transaction() as conn:
            return self._store.append_event(conn, event_type, actor=actor,
                                            group_id=str(task["group_id"]) if task else "",
                                            task_id=str(task["id"]) if task else "", cause=cause, ref=ref,
                                            payload=_fit(payload))

    def _write_attention(self, task_id: str, attempt: int, executor_id: str, attention: Mapping[str, Any] | None) -> bool:
        """Atomically record the attention sibling and its one change/clear event for the live attempt only."""
        with self._store.transaction() as conn:
            task = self._store.get_task(task_id, conn=conn)
            if task is None or task.get("state") != "running" or task.get("attempt") != attempt:
                return False
            continuity = _hold.continuity_of(task)
            previous = continuity.get("attention")
            next_value = dict(attention) if attention is not None else None
            if previous == next_value or (previous is None and next_value is None):
                return False
            execution = _hold.merge_continuity(task.get("execution"), {"attention": next_value})
            self._store.update_task(conn, task_id, expected_revision=task["revision"], execution=execution)
            reference = next_value if next_value is not None else previous
            reference = reference if isinstance(reference, Mapping) else {}
            payload = {"kind": reference.get("kind"), "since": reference.get("since"),
                       "source": reference.get("source"), "cleared": next_value is None}
            self._store.append_event(
                conn, ATTENTION_EVENT, actor=WATCHER_ACTOR, group_id=str(task["group_id"]), task_id=task_id,
                cause="executor attention changed", ref=executor_id, payload=_fit(payload))
            return True

    def _session_activity(self, adapter: Any, session_id: str | None) -> Mapping[str, Any] | None:
        """Read only allowlisted session metadata; reader failures must not affect a running executor."""
        if not session_id:
            return None
        source = {"claude_cli": "claude", "codex_cli": "codex", "copilot_cli": "copilot",
                  "antigravity_cli": "antigravity"}.get(getattr(adapter, "adapter_id", ""))
        if source is None:
            return None
        try:
            activity = self._services.sessions.activity(f"{source}:{session_id}")
            return activity if isinstance(activity, Mapping) else None
        except BaseException:
            _logger.debug("session activity unavailable for %s", source, exc_info=True)
            return None

    def _observe_process(self, *, task_id: str, attempt: int, executor_id: str, adapter: Any,
                         session_id: str | None, process_activity: process.ProcessActivity,
                         stall_seconds: float, deadline: str) -> _Observation:
        """Record whether the attempt looks stalled and return what was found (``_Observation``): the attention kind now and
        the idle time it was judged on. The heartbeat of a background attempt shows both."""
        metadata = self._session_activity(adapter, session_id)
        # Claude stdout is silent by design; unavailable metadata is unknown, not proof of activity.
        if getattr(adapter, "adapter_id", "") == "claude_cli" and metadata is None:
            return _Observation(None, None)
        if metadata and metadata.get("open_tests") is True:
            self._write_attention(task_id, attempt, executor_id, None)
            return _Observation(None, None)
        current = self.clock().astimezone(timezone.utc)
        process_last = _parse_time(process_activity.last_output_at)
        metadata_time = _parse_time(metadata.get("last_output_at")) if metadata else None
        if metadata_time is not None and (process_last is None or metadata_time >= process_last):
            last, idle = metadata_time, max(0.0, (current - metadata_time).total_seconds())
            source = "session_metadata"
        elif process_last is not None:
            last, idle, source = process_last, process_activity.idle_seconds, "stdout"
        else:
            # Startup age can detect an initially silent process, but is never presented as observed output.
            last, idle, source = None, process_activity.idle_seconds, "process_start"
        if idle < stall_seconds:
            self._write_attention(task_id, attempt, executor_id, None)
            return _Observation(None, idle)
        basis = last or _parse_time(process_activity.started_at)
        if basis is None:
            self._write_attention(task_id, attempt, executor_id, None)
            return _Observation(None, idle)
        kind = "approval_pending" if metadata and metadata.get("approval_pending") is True else "stalled"
        attention = {"kind": kind, "since": _iso(basis + timedelta(seconds=stall_seconds)),
                     "last_output_at": _iso(last) if last else None, "source": source, "deadline": deadline}
        self._write_attention(task_id, attempt, executor_id, attention)
        return _Observation(kind, idle)

    def _write_heartbeat(self, task_id: str, attempt: int, executor_id: str, *, elapsed_seconds: float,
                         idle_seconds: float | None, attention: str | None) -> bool:
        """Record ``executor.heartbeat`` for the live attempt only (the task is ``running`` in this attempt).

        The payload is numbers and the attention kind: never the prompt, the output or a path. A failure to write it is
        logged and never touches the program.
        """
        try:
            with self._store.transaction() as conn:
                task = self._store.get_task(task_id, conn=conn)
                if task is None or task.get("state") != "running" or task.get("attempt") != attempt:
                    return False
                self._store.append_event(
                    conn, HEARTBEAT_EVENT, actor=WATCHER_ACTOR, group_id=str(task["group_id"]), task_id=task_id,
                    cause="executor heartbeat", ref=executor_id,
                    payload=_fit({"attempt": attempt, "elapsed_seconds": elapsed_seconds,
                                  "idle_seconds": None if idle_seconds is None else round(max(0.0, idle_seconds), 1),
                                  "attention": attention}))
            return True
        except Exception:  # noqa: BLE001 - telemetry about a running program must never reach it
            _logger.warning("heartbeat of %s could not be recorded", task_id, exc_info=True)
            return False

    @staticmethod
    def _latest(conn: Any, event_type: str, *, task_id: str | None = None, ref: str | None = None) -> dict[str, Any] | None:
        sql, params = "SELECT * FROM team_events WHERE type=?", [event_type]
        if task_id is not None:
            sql, params = sql + " AND task_id=?", params + [task_id]
        if ref is not None:
            sql, params = sql + " AND ref=?", params + [ref]
        row = conn.execute(sql + " ORDER BY seq DESC LIMIT 1", params).fetchone()
        return ExecutorServices._row(row) if row is not None else None

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def _stored_session(self, task_id: str) -> dict[str, Any] | None:
        """The latest session the framework recorded for the task: ``{session_id, executor_id, model, effort, ...}``."""
        with self._store.db.connect() as conn:
            event = self._latest(conn, SESSION_EVENT, task_id=task_id)
        if event is None:
            return None
        return {**event["payload"], "session_id": event["ref"], "seq": event["seq"], "created_at": event["created_at"]}

    def _opened_until(self, conn: Any, session_id: str) -> datetime | None:
        """When the 12 hour rule for an open session ends, or None when it is not (or no longer) in force."""
        event = self._latest(conn, OPEN_EVENT, ref=session_id)
        opened = _parse_time(event["payload"].get("opened_at") or event["created_at"]) if event else None
        if opened is None:
            return None
        until = opened + APP_OPEN_GUARD
        return until if self.clock() < until else None

    def _pause_of(self, conn: Any, executor_id: str) -> dict[str, Any] | None:
        """The pause in force on an executor, or None: ``{until, open_ended, seq, payload}``.

        The latest of its ``executor.paused`` and ``executor.pause_lifted`` events decides: a lift ends the pause, whatever its
        time. A pause with an ``until`` ends at that time; an ``open_ended`` one (``credits_exhausted``, decision 3) has no
        time and only a lift ends it.
        """
        row = conn.execute("SELECT * FROM team_events WHERE type IN (?,?) AND ref=? ORDER BY seq DESC LIMIT 1",
                           (PAUSE_EVENT, PAUSE_LIFTED_EVENT, executor_id)).fetchone()
        event = self._row(row) if row is not None else None
        if event is None or event["type"] == PAUSE_LIFTED_EVENT:
            return None
        payload = event["payload"]
        if payload.get("open_ended") is True:
            return {"until": None, "open_ended": True, "seq": event["seq"], "payload": payload}
        until = _parse_time(payload.get("until"))
        if until is not None and self.clock() < until:
            return {"until": until, "open_ended": False, "seq": event["seq"], "payload": payload}
        return None

    @staticmethod
    def _lifted_since(conn: Any, executor_id: str, seq: int | None) -> bool:
        """True when a person lifted the executor's pause after the event ``seq`` (the pause that went with a hold)."""
        if seq is None:
            return False
        return conn.execute("SELECT 1 FROM team_events WHERE type=? AND ref=? AND seq>? LIMIT 1",
                            (PAUSE_LIFTED_EVENT, executor_id, seq)).fetchone() is not None

    def session_guard(self, session_id: str) -> bool:
        """True when the framework created ``session_id`` (it is recorded), it is not retired and no app opened it
        in the last 12 hours.

        This is the check handed to the adapters that need one (``CopilotAdapter(session_guard=...)``) and the one
        ``resume_task`` applies before it continues a session (ORQ-6 section 6).
        """
        if not is_uuid(session_id):
            return False
        sid = session_id.lower()
        with self._store.db.connect() as conn:
            if self._latest(conn, SESSION_EVENT, ref=sid) is None or self._latest(conn, RETIRED_EVENT, ref=sid):
                return False
            return self._opened_until(conn, sid) is None

    # -------- input checks --------
    @staticmethod
    def _task_id(value: Any) -> str:
        if not isinstance(value, str) or not _TASK_ID.fullmatch(value):
            raise _invalid("task_id", f"Invalid task_id: {value!r}")
        return value

    @staticmethod
    def _optional_token(field_name: str, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise _invalid(field_name, f"{field_name} must be a short non-empty text")
        return value.strip()

    @staticmethod
    def _timeout(value: Any, default: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TIMEOUT_SECONDS:
            raise _invalid("timeout_seconds", f"timeout_seconds must be an integer from 1 to {MAX_TIMEOUT_SECONDS}")
        return value

    # -------- planning --------
    def _executor(self, executor_id: str) -> tuple[Any, Any, Mapping[str, Any]]:
        """``(descriptor, adapter, declaration)`` of an executor that may run now, or an error saying why not."""
        registry = self.registry
        descriptor = registry.get(executor_id)
        if descriptor is None:
            raise _missing("Executor", executor_id)
        if descriptor.enabled is not True:
            blocker = _launch_blocker(registry.resolve(executor_id))     # GEM-T03: agy also needs its notice accepted
            extra = {"code": blocker} if blocker == _antigravity.NOTICE_NOT_ACCEPTED else {}
            raise _conflict("executor_disabled", f"{executor_id} is disabled; the operator enables it in the catalog"
                            + (" and accepts its third-party notice" if extra else ""), executor_id=executor_id, **extra)
        adapter = registry.resolve(executor_id)
        if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
            raise _conflict("executor_without_adapter", f"{executor_id} has no executor adapter registered",
                            executor_id=executor_id)
        with self._store.db.connect() as conn:
            pause = self._pause_of(conn, executor_id)
        if pause is not None:
            until = pause["until"]
            raise _conflict("executor_paused",
                            f"{executor_id} is paused until {_iso(until)}" if until is not None
                            else f"{executor_id} is paused with no end until a person lifts it",
                            executor_id=executor_id, until=_iso(until) if until is not None else None,
                            open_ended=pause["open_ended"])
        declaration = (registry.config.get("executors", {}) or {}).get(executor_id, {})
        return descriptor, adapter, declaration if isinstance(declaration, Mapping) else {}

    def _routing_selection(self, decision: Mapping[str, Any] | None, model: str | None, effort: str | None, *,
                           executor_id: str, descriptor: Any, adapter: Any = None) -> ExecutorSelection:
        """Model and effort for the launch (ECO-T08): the caller's, else the routing decision's, within the executor's evidence.

        ``decision`` is the assignment decision (None when no profile was given: then only the caller's values apply).
        Its ``routing_decision`` is the model routing decision made for the selected executor, so the economic profile's
        choice (level, price evidence, mode) and the effort the profile sets for the step kind come with their
        evidence. ``model`` and ``effort`` are the caller's: they win and are returned as given. Anything taken from
        the profile that the executor does not declare it takes is dropped and said in the selection's ``notes``;
        it is the same function the assignment preview uses (``decision.evidence.executor_requests``).
        """
        route = decision.get("routing_decision") if decision else None
        return select_for_executor(executor_id, descriptor, route, requested_model=model, requested_effort=effort,
                                   adapter=adapter)

    def _plan(self, task_id: str, executor_id: str | None, model: str | None, effort: str | None,
              profile_id: str | None, operator_context: Any, step_kind: str | None = None) -> _Plan:
        decision: Mapping[str, Any] | None = None
        if profile_id is not None:
            request = {key: value for key, value in (("profile_id", profile_id), ("requested_executor", executor_id),
                                                     ("requested_model", model), ("requested_effort", effort),
                                                     ("step_kind", step_kind))
                       if value is not None}
            decision = self._services.assignment.preview(task_id, request, operator_context)["decision"]
            if decision.get("status") != "selected" or not decision.get("selected_executor"):
                raise _conflict("assignment_not_selected", "The assignment preview did not select an executor",
                                reason_codes=list(decision.get("reason_codes", [])), status=decision.get("status"))
            if executor_id is not None and decision["selected_executor"] != executor_id:
                raise _conflict("assignment_not_selected", "The assignment preview selected another executor",
                                selected=decision["selected_executor"])
            executor_id = decision["selected_executor"]
        if executor_id is None:
            raise _invalid("executor_id", "Give executor_id, or profile_id to let the assignment preview choose")
        descriptor, adapter, declaration = self._executor(executor_id)
        # What the caller named is checked against the executor's evidence and refused if it does not take it;
        # what the profile chose was already filtered by that evidence and is never an error.
        _check_support(descriptor.model_support, model, "model", executor_id)
        _check_support(descriptor.effort_support, effort, "effort", executor_id)
        selection = self._routing_selection(decision, model, effort, executor_id=executor_id, descriptor=descriptor,
                                            adapter=adapter)
        return _Plan(executor_id, descriptor, adapter, selection.model, selection.effort, declaration, decision,
                     selection)

    def _workdir(self, task_id: str, plan: _Plan) -> Path:
        """The folder the app works in: configured for the task or the executor, else one the framework creates."""
        catalog = getattr(self._services.system, "catalog", None)
        routing = getattr(catalog, "executor_routing", {}) or {}
        per_task = routing.get("task_workdirs", {}) if isinstance(routing, Mapping) else {}
        configured = per_task.get(task_id) if isinstance(per_task, Mapping) else None
        configured = configured or plan.declaration.get("workdir")
        if configured:
            try:
                return process.validate_cwd(str(configured))
            except process.ProcessLaunchError:
                raise _conflict("workdir_invalid", "The configured work folder is not an existing absolute folder",
                                executor_id=plan.executor_id) from None
        folder = self._services.settings.executor_workdir_root / f"task-{task_id}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _privacy_decision(self, task: Mapping[str, Any], executor_id: str, descriptor: Any, model: str | None,
                          operator_context: Any, profile_id: str | None = None) -> tuple[Any, str | None, Any]:
        """The privacy decision for a task and a model, with no effect on anything: ``(decision, data_class, ModelPrivacy)``.

        The class comes from the trusted scoped classification (``AssignmentServices._trusted_data_class``), never from the
        text of the task; sources that contradict each other give no class.
        """
        assignment = self._services.assignment
        envelope = self._services.envelopes.get_envelope(task["id"])
        data_class, _scope, conflict = assignment._trusted_data_class(task, envelope, operator_context)
        profile = assignment._config.get("profiles", {}).get(profile_id) if profile_id else None
        catalog = getattr(self._services.system, "catalog", None)
        privacy = resolve_model_privacy(
            getattr(catalog, "models", None), model, descriptor=descriptor,
            profile_data_classes=profile.get("allowed_data_classes") if isinstance(profile, Mapping) else None)
        # A value outside the vocabulary is ``privacy_class_unknown``, which is what contradicting sources are.
        decision = decide_privacy("conflict" if conflict else data_class, privacy)
        return decision, data_class, privacy

    @staticmethod
    def _privacy_refusal(task_id: str, executor_id: str, decision: Any) -> ServiceError:
        return _conflict(str(decision.reason), "The model cannot receive this task: the privacy check refused it",
                         task_id=task_id, executor_id=executor_id, action="stop", level=decision.level)

    def _privacy_precheck(self, task: Mapping[str, Any], executor_id: str, descriptor: Any, model: str | None) -> None:
        """MOD-T04 (contract section 9): refuse BEFORE anything irreversible (a session retired, a hand-off committed).

        Changes nothing and writes nothing. ``_execute`` still takes the decision again (``_privacy_gate``), so this is a
        second line, never the only one. It sees no operator context and no profile, like a resume.
        """
        decision, _class, _privacy = self._privacy_decision(task, executor_id, descriptor, model, None)
        if not decision.allowed:
            raise self._privacy_refusal(str(task["id"]), executor_id, decision)

    def _privacy_gate(self, task: Mapping[str, Any], plan: _Plan, operator_context: Any) -> tuple[str | None, Any, dict[str, Any]]:
        """MOD-T04, check 1 of the contract: may this model receive this task's data? ``(data_class, ModelPrivacy,
        decision as a dict)`` or a refusal.

        Launch and resume both come through here (a resume has no operator context, so only the configured classification
        can give its class). The refusal is ``invalid_state`` with the code in ``details.reason`` and no prompt, path or
        class in it; nothing has been created or changed yet. The warnings (an unresolved class, an unknown host or
        retention) are logged at WARNING, and the decision itself (with the warnings) rides on the launch request as
        ``privacy_decision`` for MOD-T05 to read. No event is created here (the selection notes are not used: tests pin them).
        """
        decision, data_class, privacy = self._privacy_decision(
            task, plan.executor_id, plan.descriptor, plan.model, operator_context,
            (plan.decision or {}).get("profile_id"))
        for code in decision.warnings:
            _logger.warning("model privacy: %s (task %s, executor %s)", code, task["id"], plan.executor_id)
        if not decision.allowed:
            raise self._privacy_refusal(task["id"], plan.executor_id, decision)
        return data_class, privacy, decision.to_dict()

    def _limit(self, plan: _Plan, name: str, default: int) -> int:
        value = plan.declaration.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default

    # -------- running --------
    @service_errors
    def run_task(self, task_id: str, executor_id: str | None = None, model: str | None = None,
                 effort: str | None = None, actor: str | None = None, *, profile_id: str | None = None,
                 timeout_seconds: int | None = None, operator_context: Any = None,
                 step_kind: str | None = None, wait: bool | None = None,
                 accept_quota_warning: bool = False) -> dict[str, Any]:
        """Launch the task in an executor application in a new session; returns the outcome of the attempt.

        ``executor_id`` names the executor; without it ``profile_id`` makes the assignment preview choose (it needs a
        verified ``operator_context``). Only an enabled executor with an adapter runs, and not one paused by a quota
        stop. ``actor`` is the identity that claims, starts and submits (default ``executor:<executor_id>``); it is
        also the ``agent`` of the result. The task must be ``ready``, or claimed, paused or in ``needs_changes`` by
        that actor. ``model`` and ``effort`` are the caller's and always win; without them the routing decision of the
        assignment profile for ``step_kind`` (default ``work``) supplies them, within what the executor declares it
        takes (see ``_routing_selection``).

        Returns ``{outcome, run_status, task_id, executor_id, attempt, actor, session_id, open_target, model, effort,
        selection, permission, usage, command, evidence, notes, executor_paused_until, checkpoint, hold, result, gate,
        task, context_handoff}``. ``context_handoff`` is None for a normal launch. ``checkpoint`` is what ``checkpoint_limit`` saved when the attempt ended at a limit, else None, and
        ``hold`` the hold the limit put on the task (``continuity.hold``), else None. ``selection`` says where ``model``
        and ``effort`` came from (``user``, ``profile`` or none), what was not sent and why, and the routing evidence
        (profile, mode, step kind, level, price source, effort source). ``outcome`` is ``submitted`` (the result is
        stored and the gate decides) or what stopped the attempt: ``failed``, ``timeout``, ``cancelled``,
        ``quota_exhausted`` or ``blocked_open_session``; those leave the task ``paused``. Running a task that is held
        by a limit clears its hold (``continuity.resumed``, ``trigger`` ``manual``).

        ``wait`` (AVA-T02, off unless the availability policy is on): ``None`` follows the policy, ``True`` waits for the
        whole attempt whatever the policy says, ``False`` has no effect while the policy is off and is the same as ``None``
        while it is on (the caller never waits more than ``max_wait_seconds``). With the policy on, every
        check and the change of the task to ``running`` still happen here, in the caller's thread; only then the program
        is launched in the background and the caller waits at most the policy's ``max_wait_seconds`` (never more than
        ``timeout_seconds``). An attempt that ends in that time answers as always. One that does not answers
        ``outcome`` ``running`` (``run_status`` ``running``, the other keys empty) and goes on: it writes
        ``executor.heartbeat`` every ``heartbeat_seconds`` and, whatever happens, ends with ``executor.attempt_finished``
        and the task ``submitted`` or ``paused`` as in a synchronous run. ``resume_task`` and the automatic resumptions are
        always synchronous. Errors added: ``invalid_request`` (``wait``), ``invalid_state`` with ``availability_policy_invalid``,
        ``background_attempt_active`` and ``background_capacity``.

        ``accept_quota_warning`` applies only to this new agy attempt. Resumes and automatic launches never inherit it.
        """
        if not isinstance(accept_quota_warning, bool):
            raise _invalid("accept_quota_warning", "accept_quota_warning must be true or false")
        return self._run_task(task_id, executor_id, model, effort, actor, profile_id=profile_id,
                              timeout_seconds=timeout_seconds, operator_context=operator_context, step_kind=step_kind,
                               wait=wait, accept_quota_warning=accept_quota_warning)

    def _background_policy(self, wait: Any) -> AvailabilityPolicy | None:
        """The availability policy when this run goes to the background, else None (a synchronous run, as always)."""
        if wait is not None and not isinstance(wait, bool):
            raise _invalid("wait", "wait must be true, false or omitted")
        if wait is True:
            return None
        policy = self.availability
        if policy is None:
            try:
                policy = policy_from_settings(self._services.settings)
            except AvailabilityPolicyError as exc:
                raise _conflict("availability_policy_invalid",
                                f"The availability configuration is not valid: {_collapse(str(exc), 200)}") from None
        return policy if policy.enabled else None

    def _run_task(self, task_id: str, executor_id: str | None, model: str | None, effort: str | None,
                  actor: str | None, *, profile_id: str | None = None, timeout_seconds: int | None = None,
                  operator_context: Any = None, step_kind: str | None = None, hold: Mapping[str, Any] | None = None,
                  trigger: str = "manual", drift: tuple[str, ...] = (), wait: bool | None = True,
                  source: str | None = None, accept_quota_warning: bool = False) -> dict[str, Any]:
        task_id = self._task_id(task_id)
        if executor_id is not None and (not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id)):
            raise _invalid("executor_id", f"Invalid executor_id: {executor_id!r}")
        policy = self._background_policy(wait)       # only ``run_task`` passes anything but ``True`` (the automatic runs wait)
        model, effort = self._optional_token("model", model), self._optional_token("effort", effort)
        profile_id = self._optional_token("profile_id", profile_id)
        if step_kind is not None and step_kind not in STEP_KINDS:
            raise _invalid("step_kind", f"step_kind must be one of {', '.join(STEP_KINDS)}")
        task = self._tasks.get_task(task_id)
        plan = self._plan(task_id, executor_id, model, effort, profile_id, operator_context, step_kind)
        # A task held by a usage limit changes executor only through the hand-off (CNT-T05): ``run_task`` of another
        # executor, also after an ``unclaim`` and a ``claim`` by it, would skip every limit of the hand-off (``never``, a
        # private project, the budget, the chain) and leave the original session alive. ``hold`` is set only by the resume
        # of that same executor; the hand-off clears the hold before it launches the delegate.
        held_now = self._view_hold(task)
        if hold is None and _hold.is_limit_hold(held_now) and plan.executor_id != held_now.get("executor_id"):
            way_out = ("" if task["state"] == "paused" else
                       ". The task is not paused: claim it as its original actor, start it and pause it so that the card "
                       "returns, then hand it over")
            raise _conflict("person_required", "A task held by a usage limit changes executor only through the hand-off"
                            + way_out, task_id=task_id, executor_id=plan.executor_id, held_by=held_now.get("executor_id"))
        if source is not None and plan.selection is not None:
            # The values came from a hand-off (CNT-T05), not from the caller: the record says so.
            plan.selection = replace(plan.selection, model_source=source if plan.selection.model else None,
                                     effort_source=source if plan.selection.effort else None)
        actor = _text("actor", actor if actor is not None else f"executor:{plan.executor_id}", maximum=100)
        timeout = self._timeout(timeout_seconds, self._limit(plan, "timeout_seconds",
                                                             self._services.settings.executor_timeout_seconds))
        return self._execute(task, plan, actor, timeout, resume_session=None, hold=hold, trigger=trigger, drift=drift,
                             policy=policy, operator_context=operator_context,
                             accept_quota_warning=accept_quota_warning)

    @service_errors
    def resume_task(self, task_id: str, model: str | None = None, effort: str | None = None,
                    actor: str | None = None, *, timeout_seconds: int | None = None) -> dict[str, Any]:
        """Continue the session the framework created for the task, with the same executor.

        Only a session recorded by this service is continued, never one the GUI or ``open_target`` opened in an app
        in the last 12 hours. The task must be paused, claimed or in ``needs_changes`` (``actor`` defaults to its
        assignee). ``model`` and ``effort`` default to those of the recorded session. Returns the same shape as
        ``run_task``, including ``context_handoff``: None when the session continues, otherwise
        ``{ratio, level, snapshot_ref, retired_session, checkpoint, inbox_cursor}`` for a new session.
        Errors: ``no_stored_session``, ``session_retired``, ``session_open_in_app`` and the ones of
        ``run_task``. Resuming a task that is held by a limit clears its hold (``continuity.resumed``, ``trigger``
        ``manual``).
        """
        return self._resume_task(task_id, model, effort, actor, timeout_seconds=timeout_seconds)

    def _resume_task(self, task_id: str, model: str | None, effort: str | None, actor: str | None, *,
                     timeout_seconds: int | None = None, hold: Mapping[str, Any] | None = None,
                     trigger: str = "manual", drift: tuple[str, ...] = ()) -> dict[str, Any]:
        task_id = self._task_id(task_id)
        model, effort = self._optional_token("model", model), self._optional_token("effort", effort)
        task = self._tasks.get_task(task_id)
        session = self._stored_session(task_id)
        if session is None:
            raise _conflict("no_stored_session", "No session of this task was created by the framework; "
                            "only those are resumed", task_id=task_id)
        session_id = str(session["session_id"])
        with self._store.db.connect() as conn:
            retired = self._latest(conn, RETIRED_EVENT, ref=session_id)
            blocked = self._opened_until(conn, session_id)
        if retired is not None:
            raise _conflict("session_retired", "The session was retired (hand-off or relaunch): the framework never "
                            "resumes it", task_id=task_id, session_id=session_id, retired_as=retired["payload"].get("reason"))
        if blocked is not None:
            raise _conflict("session_open_in_app", "The session was opened in an app in the last 12 hours",
                            task_id=task_id, session_id=session_id, blocked_until=_iso(blocked))
        executor_id = str(session.get("executor_id", ""))
        descriptor, adapter, declaration = self._executor(executor_id)
        selection = select_for_resume(executor_id, model=model, effort=effort, recorded_model=session.get("model"),
                                      recorded_effort=session.get("effort"))
        _check_support(descriptor.model_support, selection.model, "model", executor_id)
        _check_support(descriptor.effort_support, selection.effort, "effort", executor_id)
        plan = _Plan(executor_id, descriptor, adapter, selection.model, selection.effort, declaration,
                     selection=selection)
        actor = _text("actor", actor or task.get("assignee") or f"executor:{executor_id}", maximum=100)
        timeout = self._timeout(timeout_seconds, self._limit(plan, "timeout_seconds",
                                                             self._services.settings.executor_timeout_seconds))
        self._privacy_precheck(task, executor_id, descriptor, selection.model)     # MOD-T04: before a session is retired
        handoff = self._context_handoff(task, session, actor, plan, timeout)
        # Measuring (a compaction links a snapshot), the checkpoint and the guard all change the task's revision, also
        # when no hand-off follows: the attempt always starts from the task as it is now.
        task = self._tasks.get_task(task_id)
        result = self._execute(task, plan, actor, timeout, resume_session=None if handoff else session_id,
                               recorded=None if handoff else _session_settings(session), hold=hold,
                               trigger=trigger, drift=drift)
        result["context_handoff"] = handoff
        return result

    def _context_handoff(self, task: Mapping[str, Any], session: Mapping[str, Any], actor: str,
                         plan: _Plan, timeout: int) -> dict[str, Any] | None:
        """At a resume boundary, replace a session only when CMP reports the task profile's threshold."""
        task_id, sid = str(task["id"]), str(session["session_id"])
        try:
            report = self._services.envelopes.refresh_task_context(task_id, model_id=session.get("model"))
        except Exception:
            _logger.warning("Context measurement for %s failed; resuming its session", task_id, exc_info=True)
            return None
        if not report.get("enabled") or report.get("level") != LEVEL_THRESHOLD or report.get("ratio") is None:
            return None
        with self._store.db.connect() as conn:
            previous = self._latest(conn, RETIRED_EVENT, task_id=task_id)
        if (previous is not None and previous["payload"].get("reason") == "context_threshold"
                and previous["seq"] < session["seq"]
                and session.get("attempt") == task.get("attempt")):
            # A retry of the fresh session in this attempt is still the same hand-off decision.
            return None
        # The same guard as CNT-T05: a pending cancel or an app-open session cannot be replaced.
        self._services.inbox.require_no_cancel("task", task_id)
        with self._store.db.connect() as conn:
            blocked = self._opened_until(conn, sid)
        if blocked is not None:
            raise _conflict("session_open_in_app", "The session was opened in an app in the last 12 hours",
                            task_id=task_id, session_id=sid, blocked_until=_iso(blocked))
        # Check the replacement can launch before the old session is made unusable.
        permission = Permission.WRITE if task["file_scope"] else Permission.READ
        self._require_launch_effects(task, plan.executor_id, permission, "launch")
        workdir = self._workdir(task_id, plan)
        request = ExecutorLaunchRequest(task_id=task_id, task_envelope=self._services.envelopes.get_envelope(task_id),
                                        workdir=workdir, model=plan.model, effort=plan.effort,
                                        permission=permission, timeout_seconds=timeout)
        self._preflight(plan, request, None)
        snapshot_ref = report.get("snapshot_ref")
        if snapshot_ref is None:
            # Before the checkpoint: a failure here must leave nothing behind that moves the task's revision.
            try:
                snapshot = self._services.envelopes.get_context_snapshot(str(report.get("scope") or f"task:{task_id}"))
            except Exception:
                _logger.warning("Context snapshot lookup for %s failed; resuming its session", task_id, exc_info=True)
                return None
            snapshot_ref = snapshot.get("ref") if snapshot else None
        # Always this session's own checkpoint (idempotent per task, attempt, session and patch), never an older one.
        checkpoint = self.checkpoint_limit(task_id, session_id=sid, executor_id=session.get("executor_id"),
                                           attempt=int(task["attempt"]), actor=actor, record_limit=False)
        with self._store.transaction() as conn:
            self._services.inbox.require_no_cancel("task", task_id)
            blocked = self._opened_until(conn, sid)
            if blocked is not None:
                raise _conflict("session_open_in_app", "The session was opened in an app in the last 12 hours",
                                task_id=task_id, session_id=sid, blocked_until=_iso(blocked))
            current = self._latest(conn, SESSION_EVENT, task_id=task_id)
            inbox_cursor = self._services.inbox.cursor("task", task_id)
            # A concurrent resume or hand-off may have replaced or retired the session while this one measured.
            if current is None or current["ref"] != sid or not self._retire_in(
                    conn, sid, "context_threshold", actor,
                    extra={"ratio": report["ratio"], "level": report["level"], "snapshot_ref": snapshot_ref,
                           "inbox_cursor": inbox_cursor, "checkpoint_seq": checkpoint.get("seq")}):
                raise _conflict("session_retired", "The session was replaced or retired while its context was measured",
                                task_id=task_id, session_id=sid)
        return {"ratio": report["ratio"], "level": report["level"], "snapshot_ref": snapshot_ref,
                "retired_session": sid, "checkpoint": checkpoint, "inbox_cursor": inbox_cursor}

    def _execute(self, task: dict[str, Any], plan: _Plan, actor: str, timeout: int, *,
                 resume_session: str | None, recorded: Mapping[str, Any] | None = None,
                 hold: Mapping[str, Any] | None = None, trigger: str = "manual",
                 drift: tuple[str, ...] = (), policy: AvailabilityPolicy | None = None,
                 operator_context: Any = None, accept_quota_warning: bool = False) -> dict[str, Any]:
        """One attempt. ``hold`` and ``trigger`` (``auto`` from ``resume_due``, ``condition`` from ``resume_met``, else
        ``manual``) say what hold this run ends; ``drift`` is what ``resume_due`` found different before relaunching; ``recorded`` the settings of the
        session being resumed (to compare with what the app reports).

        Three steps: the checks and the change of the task to ``running`` (always here, in the caller's thread), the
        adapter call (``_call_with_watch``) and the record of how it ended (``_finish_attempt``). With ``policy`` (the
        availability policy, on) the last two run in a thread of their own and this returns ``running`` if they
        did not end in the policy's wait (``_launch_in_background``); without it they run here and the caller waits."""
        task_id = task["id"]
        privacy = self._privacy_gate(task, plan, operator_context)      # MOD-T04: before anything is created or changed
        mode = "resume" if resume_session else "launch"
        prior = dict(task.get("continuity") or {})
        held = dict(hold) if hold else (dict(prior["hold"]) if isinstance(prior.get("hold"), Mapping) else None)
        permission = Permission.WRITE if task["file_scope"] else Permission.READ
        # BDI-T04: with a document policy active, launching the executor and writing need an approval (the barrier
        # is off by default). Before the work folder is created and before the task changes.
        self._require_launch_effects(task, plan.executor_id, permission, mode)
        workdir = self._workdir(task_id, plan)
        claude_session_id = (resume_session or str(uuid.uuid4())) \
            if getattr(plan.adapter, "adapter_id", "") == "claude_cli" else None
        request = ExecutorLaunchRequest(
            task_id=task_id, task_envelope=self._services.envelopes.get_envelope(task_id), workdir=workdir,
            model=plan.model, effort=plan.effort, permission=permission, timeout_seconds=timeout,
            session_id=claude_session_id, data_class=privacy[0], privacy=privacy[1],
            privacy_decision=privacy[2],
            quota_warning_accepted=accept_quota_warning and plan.executor_id == "antigravity_cli"
            and resume_session is None)
        self._preflight(plan, request, resume_session)
        cause = f"executor {mode} in {plan.executor_id}"
        # A place in the background is taken before the task changes, so a refusal leaves the task as it was.
        background = self.background.reserve(task_id, plan.executor_id) if policy is not None else None
        try:
            view = self._enter_running(task, actor, cause, hold=held, mode=mode, trigger=trigger, drift=drift,
                                       executor_id=plan.executor_id)
            self._write_attention(task_id, int(view["attempt"]), plan.executor_id, None)
            if plan.selection is not None:
                # What this attempt was asked to run with and why, kept even if the launch then fails.
                self._append(ROUTING_EVENT, actor=actor, task=view, ref=plan.executor_id, cause=cause,
                             payload=plan.selection.event_payload(attempt=view["attempt"], mode=mode))
        except BaseException:
            if background is not None:
                self.background.release(background)
            raise

        def call(launch_request: ExecutorLaunchRequest) -> _Attempt:
            # The one place the service reaches an adapter: after the effects were checked and the task is running.
            return self._call_adapter(plan.adapter, launch_request, resume_session)

        launch = _Launch(task=task, plan=plan, actor=actor, timeout=timeout, resume_session=resume_session,
                         recorded=recorded, trigger=trigger, prior=prior, permission=permission, workdir=workdir,
                         request=request, claude_session_id=claude_session_id, cause=cause, mode=mode, view=view,
                         call=call)
        if background is None or policy is None:
            return self._finish_attempt(launch, self._call_with_watch(launch))
        return self._launch_in_background(launch, background, policy)

    def _call_with_watch(self, launch: _Launch, *, heartbeat: _Heartbeat | None = None,
                         background: BackgroundAttempt | None = None) -> _Attempt:
        """Call the adapter while the process watch observes it (stall detection, CNT-T09).

        With ``heartbeat`` (a background attempt) the same watch writes ``executor.heartbeat`` when one is due, and with
        ``background`` the handle of the program is kept so the attempt can be cancelled. A failure here that is not an
        adapter refusal pauses the task (never a task ``running`` with no program behind it) and is raised.
        """
        plan, view, task_id = launch.plan, launch.view, launch.task["id"]
        attempt_number = int(view["attempt"])
        try:
            # Built again: the attempt number, the status and the assignee changed with the transitions above.
            request = replace(launch.request, task_envelope=self._services.envelopes.get_envelope(task_id))
            _logger.info("executor %s %s of %s (attempt %s)", plan.executor_id, launch.mode, task_id, view["attempt"])
            stall_seconds = self._limit(plan, "stall_minutes", 15) * 60
            watch_interval = max(0.05, min(1.0, stall_seconds / 4))
            if heartbeat is not None:
                watch_interval = max(0.05, min(watch_interval, heartbeat.interval / 2))
            watch_deadline = _iso(self.clock().astimezone(timezone.utc) + timedelta(seconds=launch.timeout))
            live_session = [launch.claude_session_id or launch.resume_session]
            last_probe = [None]

            def callback(activity: process.ProcessActivity) -> None:
                if live_session[0] is None and activity._stdout_probe and activity._stdout_probe != last_probe[0]:
                    last_probe[0] = activity._stdout_probe
                    try:
                        if plan.adapter.adapter_id == "codex_cli":
                            live_session[0] = _codex.parse_stream(activity._stdout_probe).session_id
                        elif plan.adapter.adapter_id == "copilot_cli":
                            live_session[0] = _copilot._scan(activity._stdout_probe).session_id
                        elif plan.adapter.adapter_id == _antigravity.ADAPTER_ID:
                            live_session[0] = _antigravity.session_id_of(activity._stdout_probe)
                    except BaseException:
                        _logger.debug("live session id probe unavailable", exc_info=True)
                observed = self._observe_process(
                    task_id=task_id, attempt=attempt_number, executor_id=plan.executor_id,
                    adapter=plan.adapter, session_id=live_session[0], process_activity=activity,
                    stall_seconds=stall_seconds, deadline=watch_deadline)
                if heartbeat is not None and heartbeat.due():
                    self._write_heartbeat(task_id, attempt_number, plan.executor_id, elapsed_seconds=heartbeat.elapsed(),
                                          idle_seconds=observed.idle_seconds, attention=observed.attention)
                if heartbeat is not None:         # AVA-T03: a background attempt reads the person's inbox at each tick
                    inbox = getattr(self._services, "inbox", None)
                    if inbox is not None:
                        inbox.on_attempt_tick(task_id, attempt_number, plan.executor_id, live_session[0])
            with ExitStack() as stack:
                stack.enter_context(process.watching(watch_interval, callback))
                if background is not None:
                    stack.enter_context(process.capturing(background.adopt))
                return launch.call(request)
        except BaseException:
            # Nothing ran or the run was interrupted: do not leave a task "running" with no program behind it.
            self._write_attention(task_id, attempt_number, plan.executor_id, None)
            self._pause_task(task_id, launch.actor, f"executor {launch.mode} interrupted by an error",
                             attempt=attempt_number)
            launch.settled = True
            raise

    def _launch_in_background(self, launch: _Launch, background: BackgroundAttempt,
                              policy: AvailabilityPolicy) -> dict[str, Any]:
        """Run the adapter call and the record of its end in a thread; wait for it at most the policy's foreground wait.

        Returns what a synchronous run returns when the attempt ended in that time, else the ``running`` answer; the
        thread goes on, writes the heartbeat and always ends with ``executor.attempt_finished`` (see ``_background_main``).
        The wait is bounded by the policy (``max_wait_seconds``, at most 120) and by the attempt's own timeout.
        """
        task_id, attempt_number = launch.task["id"], int(launch.view["attempt"])
        wait = policy.foreground_wait(launch.timeout)
        wait = policy.max_wait_seconds if wait is None else wait
        background.attempt = attempt_number
        background.started = time.monotonic()
        heartbeat = _Heartbeat(policy.heartbeat_seconds, background.started)
        thread = threading.Thread(target=self._background_main, args=(launch, background, heartbeat),
                                  name=f"attempt-{task_id}-{attempt_number}", daemon=True)
        try:
            thread.start()
        except BaseException:
            self.background.release(background)
            self._write_attention(task_id, attempt_number, launch.plan.executor_id, None)
            self._pause_task(task_id, launch.actor, f"executor {launch.mode} could not start in the background",
                             attempt=attempt_number)
            raise
        if background.done.wait(wait):
            return background.result if background.result is not None else self._bare_result(
                launch, outcome="failed", run_status="failed", evidence="internal_error")
        result = self._bare_result(launch, outcome=FOREGROUND_RUNNING, run_status="running")
        result["background"] = {"max_wait_seconds": wait, "heartbeat_seconds": policy.heartbeat_seconds}
        return result

    def _background_main(self, launch: _Launch, background: BackgroundAttempt, heartbeat: _Heartbeat) -> None:
        """The thread of a background attempt. Whatever happens, the attempt ends recorded and its place is released."""
        try:
            attempt = self._call_with_watch(launch, heartbeat=heartbeat, background=background)
            background.result = self._finish_attempt(launch, attempt, background)
        except BaseException as exc:  # noqa: BLE001 - nobody is waiting to receive it: record it
            _logger.exception("background attempt %s of %s failed", launch.view.get("attempt"), launch.task["id"])
            try:
                background.result = self._record_background_failure(launch, background, exc)
            except BaseException:  # noqa: BLE001
                _logger.exception("the failure of the background attempt of %s could not be recorded", launch.task["id"])
        finally:
            try:
                inbox = getattr(self._services, "inbox", None)
                if inbox is not None:       # AVA-T03: an interrupt of the person stopped the program; now hold the task
                    inbox.finish_interrupt(launch.task["id"], int(launch.view["attempt"]))
            except Exception:  # noqa: BLE001 - the place of the attempt is released whatever happens
                _logger.exception("the inbox could not finish the interrupt of %s", launch.task["id"])
            self.background.release(background)
            background.done.set()

    def _record_background_failure(self, launch: _Launch, background: BackgroundAttempt,
                                   exc: BaseException) -> dict[str, Any]:
        """The framework itself failed in a background attempt: pause the task (only if it is still running in this attempt)
        and write the end it never wrote.

        Only the type of the exception goes in the record (its text could hold a path or part of a prompt).
        """
        plan, view, task_id = launch.plan, launch.view, launch.task["id"]
        evidence = f"internal_error: {type(exc).__name__}"
        note = None
        if not launch.settled:
            # The task is still this attempt's. Once it settled it, it may already run again (a resume or a run keeps the
            # attempt number), and neither its attention nor its state is touched from here.
            self._write_attention(task_id, int(view["attempt"]), plan.executor_id, None)
            note = self._pause_task(task_id, launch.actor, f"attempt {view['attempt']} failed: internal error",
                                    attempt=int(view["attempt"]))
            launch.settled = True
        if background.attempt_recorded:
            # The end is already in the log, and the task is as that end left it: the answer says the same, with the
            # failure of what came after as a note, instead of a ``failed`` that the log and the task do not show.
            return self._bare_result(launch, outcome=background.outcome or "failed",
                                     run_status=background.run_status or "failed",
                                     notes=[f"internal_error after the end was recorded: {type(exc).__name__}"])
        self._append(ATTEMPT_EVENT, actor=launch.actor, task=view, cause=launch.cause, payload={
            "executor_id": plan.executor_id, "adapter_id": plan.adapter.adapter_id, "attempt": view["attempt"],
            "mode": launch.mode, "run_status": "failed", "outcome": "failed", "session_id": None,
            "exit_code": None, "model": plan.model, "effort": plan.effort, "failure_kind": None,
            "permission": launch.permission.value, "notes": [note] if note else [], "evidence": evidence,
            "result_ref": None})
        background.attempt_recorded, background.outcome, background.run_status = True, "failed", "failed"
        return self._bare_result(launch, outcome="failed", run_status="failed", evidence=evidence,
                                 notes=[note] if note else [])

    def _bare_result(self, launch: _Launch, *, outcome: str, run_status: str, evidence: str | None = None,
                     notes: Iterable[str] = ()) -> dict[str, Any]:
        """The shape ``run_task`` returns with only what is known: for an attempt still running or one that broke."""
        plan, task_id = launch.plan, launch.task["id"]
        return {
            "outcome": outcome, "run_status": run_status, "task_id": task_id, "executor_id": plan.executor_id,
            "attempt": launch.view["attempt"], "actor": launch.actor, "mode": launch.mode, "session_id": None,
            "open_target": None, "model": plan.model, "effort": plan.effort,
            "selection": plan.selection.to_dict() if plan.selection else None, "permission": launch.permission.value,
            "usage": None, "command": None, "evidence": evidence, "notes": list(notes), "executor_paused_until": None,
            "checkpoint": None, "hold": None, "context_handoff": None, "result": None, "gate": self._services.gates.get_gate(task_id),
            "task": self._tasks.get_task(task_id)}

    def _finish_attempt(self, launch: _Launch, attempt: _Attempt,
                        background: BackgroundAttempt | None = None) -> dict[str, Any]:
        """Record how the attempt ended and return what ``run_task`` returns: the session, the result through
        ``submit_result``, ``executor.attempt_finished``, and the pause or hold of the task when it did not submit."""
        task_id, view, plan, actor = launch.task["id"], launch.view, launch.plan, launch.actor
        mode, cause, permission, workdir = launch.mode, launch.cause, launch.permission, launch.workdir
        resume_session, recorded, prior, trigger = launch.resume_session, launch.recorded, launch.prior, launch.trigger
        timeout = launch.timeout
        attention_deadline = _iso(self.clock().astimezone(timezone.utc) + timedelta(seconds=timeout))
        if attempt.failure_kind is FailureKind.PERMISSION_DENIED:
            now = _iso(self.clock().astimezone(timezone.utc))
            self._write_attention(task_id, int(view["attempt"]), plan.executor_id, {
                "kind": "permission_blocked", "since": now, "last_output_at": None,
                "source": "executor_result", "deadline": None})
        else:
            # Clear while the task is still running; result submission can make it terminal immediately.
            self._write_attention(task_id, int(view["attempt"]), plan.executor_id, None)
        if attempt.session_id and attempt.session_id != resume_session:
            self._append(SESSION_EVENT, actor=actor, task=view, ref=attempt.session_id, cause=cause, payload={
                "executor_id": plan.executor_id, "adapter_id": plan.adapter.adapter_id, "attempt": view["attempt"],
                "mode": mode, "model": plan.model, "effort": plan.effort, "permission": permission.value,
                "model_source": plan.selection.model_source if plan.selection else None,
                "effort_source": plan.selection.effort_source if plan.selection else None})
        # What the app says it ran a resumed session with, against what the session started with (contract section 10).
        reported = (_hold.detect_drift(recorded or {}, attempt.effective)
                    if resume_session and attempt.effective and recorded else [])
        submission, problem = None, None
        if attempt.failure_kind is FailureKind.PERMISSION_DENIED:
            problem = "the executor reported a permission denial; its result was not submitted"
        elif attempt.status == "completed" and attempt.envelope is not None:
            try:
                submission = self._services.envelopes.submit_result(task_id, attempt.envelope, actor=actor)
            except ServiceError as exc:
                problem = f"the result envelope was rejected ({exc.code}): {exc.message}"
        elif attempt.status == "completed":
            problem = "the run completed without a task result envelope"
        if submission is not None:
            launch.settled = True                                  # the task is ``submitted``: not running any more
        outcome = ("permission_blocked" if attempt.failure_kind is FailureKind.PERMISSION_DENIED else
                   "submitted" if submission else (attempt.status if attempt.status in _FAILURE_OUTCOMES else "failed"))
        evidence = ""
        if submission is None:
            evidence = _collapse(" | ".join(part for part in (problem, attempt.evidence) if part), MAX_EVIDENCE_CHARS)
        self._append(ATTEMPT_EVENT, actor=actor, task=view, cause=cause, ref=attempt.session_id or "", payload={
            "executor_id": plan.executor_id, "adapter_id": plan.adapter.adapter_id, "attempt": view["attempt"],
            "mode": mode, "run_status": attempt.status, "outcome": outcome, "session_id": attempt.session_id,
            "exit_code": attempt.exit_code, "model": plan.model, "effort": plan.effort,
            "failure_kind": attempt.failure_kind.value if attempt.failure_kind else None,
            "model_source": plan.selection.model_source if plan.selection else None,
            "effort_source": plan.selection.effort_source if plan.selection else None,
            "permission": permission.value, "usage": dict(attempt.usage) if attempt.usage else None,
            "notes": attempt.notes, "evidence": evidence,
            "result_ref": submission["result"]["ref"] if submission else None,
            **({"config_drift": reported} if reported else {})})
        if background is not None:
            background.attempt_recorded, background.outcome, background.run_status = True, outcome, attempt.status
        paused_until: str | None = None
        task_note: str | None = None
        saved: dict[str, Any] | None = None
        checkpoint_note: str | None = None
        new_hold: dict[str, Any] | None = None
        if submission is None and outcome == "quota_exhausted":
            outcome_hold = self._hold_after_limit(attempt, plan, view, actor, workdir, permission, prior=prior,
                                                  auto=trigger in AUTO_TRIGGERS, drift=reported)
            paused_until, new_hold, saved = outcome_hold["until"], outcome_hold["hold"], outcome_hold["checkpoint"]
            task_note, checkpoint_note = outcome_hold["task_note"], outcome_hold["checkpoint_note"]
            launch.settled = True                                  # paused, with its hold
        else:
            if submission is None:
                task_note = self._pause_task(task_id, actor, f"attempt {view['attempt']} {outcome}",
                                             attempt=int(view["attempt"]))
                launch.settled = True
            self._clear_streaks(task_id, prior, actor, reported)
        notes = list(attempt.notes) + [note for note in (task_note, checkpoint_note) if note]
        target = plan.adapter.open_target(attempt.session_id) if attempt.session_id else None
        if target is not None and not self._may_hand_out_target(
                task_id, plan.executor_id, str(plan.adapter.adapter_id).removesuffix("_cli"), attempt.session_id, actor):
            target = None       # BDI-T04: with a policy, the app is not offered without an approval to open it
        return {
            "outcome": outcome, "run_status": attempt.status, "task_id": task_id, "executor_id": plan.executor_id,
            "attempt": view["attempt"], "actor": actor, "mode": mode, "session_id": attempt.session_id,
            "open_target": _target_view(target) if target else None, "model": plan.model, "effort": plan.effort,
            "selection": plan.selection.to_dict() if plan.selection else None, "permission": permission.value, "usage": dict(attempt.usage) if attempt.usage else None,
            "command": attempt.command, "evidence": evidence or None, "notes": notes,
            "executor_paused_until": paused_until, "checkpoint": saved, "hold": new_hold, "context_handoff": None,
            "result": submission,
            "gate": self._services.gates.get_gate(task_id), "task": self._tasks.get_task(task_id)}

    def _preflight(self, plan: _Plan, request: ExecutorLaunchRequest, resume_session: str | None) -> None:
        """Build the command and find the program before the task changes: a refusal here leaves the task as it was."""
        blocker = _launch_blocker(plan.adapter)      # GEM-T03: agy's kill switch, notice and one-run-at-a-time lock
        if blocker is not None:
            raise _conflict("executor_unavailable", f"{plan.executor_id} cannot be launched now ({blocker})",
                            executor_id=plan.executor_id, code=blocker)
        try:
            if resume_session:
                argv = plan.adapter.build_resume_command(request, resume_session)
            else:
                argv = plan.adapter.build_command(replace(request, session_id=_PROBE_SESSION))
            process.check_launch(argv, cwd=request.workdir)
        except process.ProcessLaunchError as exc:
            if exc.code == process.INVALID_ARGV:
                raise _invalid("request", f"The executor refused the request: {_collapse(str(exc), 200)}",
                               executor_id=plan.executor_id, code=exc.code) from None
            raise _conflict("executor_unavailable", f"The program of {plan.executor_id} cannot be started ({exc.code})",
                            executor_id=plan.executor_id, code=exc.code) from None
        except (MultiAgentError, EnvelopeError, ValueError) as exc:
            raise _invalid("request", f"The executor refused the request: {_collapse(str(exc), 200)}",
                           executor_id=plan.executor_id, code=str(getattr(exc, "code", None) or getattr(exc, "field", "") or "")
                           ) from None

    def _enter_running(self, task: dict[str, Any], actor: str, cause: str, *, hold: Mapping[str, Any] | None = None,
                       mode: str = "launch", trigger: str = "manual", drift: tuple[str, ...] = (),
                       executor_id: str = "") -> dict[str, Any]:
        """Take the task to ``running`` with the lifecycle's own transitions (the single function of ORQ-1).

        A paused task that is held leaves its hold in the same update as the ``resume`` (``continuity.hold`` removed,
        and ``config_drift`` too when a new session starts) and ``continuity.resumed`` records how it ended.
        """
        status = task["status"]
        steps = {"ready": ("claim", "start"), "claimed": ("start",), "needs_changes": ("start",),
                 "paused": ("resume",)}.get(status)
        if steps is None:
            raise _conflict("task_not_runnable", f"Task {task['id']} is {status}; a run needs a ready, claimed, paused "
                            "or needs_changes task", task_id=task["id"], status=status)
        view = task
        for action in steps:
            if hold is not None and action in ("resume", "start"):
                changes: dict[str, Any] = {"hold": None}
                if mode == "launch":
                    changes["config_drift"] = None              # a new session starts clean
                view = self._tasks.transition(task["id"], action, actor=actor, cause=cause,
                                              expected_revision=view["revision"], continuity=changes)
                self._append(RESUMED_EVENT, actor=actor, task=view, ref=executor_id or str(hold.get("executor_id") or ""),
                             cause=cause, payload=self._resumed_payload(hold, mode, trigger, drift, executor_id))
            else:
                view = self._tasks.transition(task["id"], action, actor=actor, cause=cause,
                                              expected_revision=view["revision"])
        return view

    def _resumed_payload(self, hold: Mapping[str, Any], mode: str, trigger: str, drift: tuple[str, ...],
                         executor_id: str) -> dict[str, Any]:
        since = _hold.parse_utc(hold.get("since"))
        minutes = max(int((self.clock() - since).total_seconds() // 60), 0) if since is not None else None
        return {"mode": "resume" if mode == "resume" else "relaunch", "trigger": trigger, "minutes": minutes,
                "config_drift": list(drift), "reason": hold.get("reason"),
                "executor_id": executor_id or hold.get("executor_id"), "previous_session": hold.get("session_id"),
                "attempt": hold.get("attempt")}

    def _call_adapter(self, adapter: Any, request: ExecutorLaunchRequest, resume_session: str | None) -> _Attempt:
        try:
            result = adapter.resume(request, resume_session) if resume_session else adapter.launch(request)
        except (MultiAgentError, EnvelopeError, ValueError, OSError) as exc:
            code = str(getattr(exc, "code", None) or getattr(exc, "field", None) or type(exc).__name__)
            return _Attempt(status="launch_refused", notes=[f"launch_refused: {code}"],
                            evidence=f"The program was not started ({code}): {_collapse(str(exc), 240)}")
        notes = [_collapse(note, MAX_NOTE_CHARS) for note in result.notes][:MAX_NOTES]
        evidence = ""
        if result.status.value != "completed" or result.result_envelope is None:
            parts = [f"stderr: {_tail(result.stderr_tail, 400)}" if result.stderr_tail.strip() else "",
                     f"stdout: {_tail(result.stdout_tail, 400)}" if result.stdout_tail.strip() else "",
                     f"notes: {'; '.join(notes)}" if notes else ""]
            evidence = " | ".join(part for part in parts if part)
        return _Attempt(status=result.status.value, session_id=result.session_id, envelope=result.result_envelope,
                        usage=result.usage, command=_command_view(result.command), exit_code=result.command.exit_code,
                        notes=notes, evidence=evidence, limit=getattr(result, "limit", None),
                        failure_kind=getattr(result, "failure_kind", None),
                        last_message=_checkpoint.extract_last_message(result.stdout_tail),
                        effective=_effective_of(getattr(result, "effective", None)))

    def _hold_after_limit(self, attempt: _Attempt, plan: _Plan, view: Mapping[str, Any], actor: str, workdir: Path,
                          permission: Permission, *, prior: Mapping[str, Any], auto: bool,
                          drift: list[str]) -> dict[str, Any]:
        """An attempt ended at a limit: pause the executor, hold the task and save the checkpoint (CNT-T04).

        The executor pause is written first (``executor.paused``). The task pause and its ``continuity.hold`` are one
        lifecycle ``pause`` (``tasks.transition(..., continuity=...)``: a single update, so a task is never ``paused``
        by a limit without its hold), followed by ``continuity.held``; a task that is no longer ``running`` (it moved on
        meanwhile) only has the executor paused and says so in ``task_note``. The checkpoint is saved afterwards, with the real ``retries_left`` (a ``rate_limited`` limit with
        retries left saves nothing), and its patch blob is written into the hold. ``auto`` is true when ``resume_due`` made
        the attempt: then a limit that returns counts as a failed resume.
        """
        task_id = str(view["id"])
        now = self.clock()
        info = _checkpoint.normalize_limit(attempt.limit)
        rate_used = int(prior.get("rate_retries") or 0) if info["kind"] == "rate_limited" else 0
        configured = self._limit(plan, "quota_pause_minutes", self._services.settings.executor_quota_pause_minutes)
        policy = self._policy_of(prior)
        held = _hold.plan_hold(info, now=now, configured_minutes=configured, rate_retries=rate_used)
        retrying = info["kind"] == "rate_limited" and held.retries_left > 0     # a backoff retry is not a failed resume
        failed = (int(prior.get("failed_resumes") or 0) + (0 if retrying else 1)) if auto else 0
        blocked = "failed_resumes" if failed >= _hold.MAX_FAILED_RESUMES else None
        settings = {"model": plan.model, "effort": plan.effort, "permission": permission.value,
                    "adapter": plan.adapter.adapter_id, "workdir": _workdir_id(workdir)}
        changes: dict[str, Any] = {
            "failed_resumes": failed,
            "rate_retries": (rate_used + 1 if retrying else rate_used) if info["kind"] == "rate_limited" else 0}
        if "handoffs" not in prior:
            changes["handoffs"] = 0                         # ``handoff_task`` (CNT-T05) counts them from here
        if "max_handoffs" not in prior:
            changes["max_handoffs"] = policy.max_handoffs   # what the card shows; a task can only lower it later
        if not prior.get("chain"):
            changes["chain"] = [plan.executor_id]
        if drift:
            changes["config_drift"] = list(drift)
        minutes = None if held.until is None else -(-int((held.until - now).total_seconds()) // 60)
        pause_payload = {
            "executor_id": plan.executor_id, "until": _iso(held.until) if held.until is not None else None,
            "minutes": minutes, "reason": "quota_exhausted", "limit_kind": held.reason, "window": held.window,
            "reset_at": held.reset_at, "reset_source": held.reset_source, "reset_confidence": held.reset_confidence,
            "open_ended": held.open_ended, "outcome": held.outcome}
        # The executor is out of quota whatever happens to the task, so its pause is written first; its ``seq`` goes in the
        # hold, which is how a later ``lift_pause`` is told apart from the pause that belongs to this hold.
        seq = self._append(PAUSE_EVENT, actor=actor, task=view, ref=plan.executor_id, cause="quota or limit reached",
                           payload=pause_payload)
        block: dict[str, Any] | None = _hold.build_hold(
            held, now=now, executor_id=plan.executor_id, actor=actor, session_id=attempt.session_id,
            attempt=int(view["attempt"]), settings=settings, pause_seq=seq, blocked=blocked, mode=policy.mode)
        task_note: str | None = None
        try:
            # One update: the task goes to ``paused`` and the hold is written with it (no state, no migration).
            self._tasks.transition(task_id, "pause", actor=actor, cause=f"attempt {view['attempt']} quota_exhausted",
                                   continuity={**changes, "hold": block})
            self._append(HELD_EVENT, actor=actor, task=view, cause="held by a limit",
                         payload=self._held_payload(block, held, prior, failed, policy.max_handoffs))
        except ServiceError as exc:             # the task is not running any more: only the executor stays paused
            block, task_note = None, f"task_not_paused: {exc.code}"
        saved, checkpoint_note = self._save_checkpoint(attempt, plan, view, actor, workdir,
                                                       retries_left=held.retries_left)
        if saved is not None and saved.get("skipped"):
            # The checkpoint saved nothing (a retry is coming) and so wrote no detection: this is the event of it.
            self._append(LIMIT_EVENT, actor=actor, task=view, ref=plan.executor_id, cause="limit detected",
                         payload=_checkpoint.fit_payload(
                             {"attempt": int(view["attempt"]), "session_id": attempt.session_id,
                              "executor_id": plan.executor_id, **info}, EVENT_PAYLOAD_BUDGET, ("evidence",)))
        if block is not None and saved is not None and saved.get("patch"):
            block = self._set_hold_checkpoint(task_id, block, str(saved["patch"]["ref"]), actor)
        return {"until": _iso(held.until) if held.until is not None else None, "hold": block, "checkpoint": saved,
                "task_note": task_note, "checkpoint_note": checkpoint_note}

    @staticmethod
    def _held_payload(block: Mapping[str, Any], held: _hold.HoldPlan, prior: Mapping[str, Any],
                      failed: int, maximum: int = DEFAULT_MAX_HANDOFFS) -> dict[str, Any]:
        payload = {
            "reason": block["reason"], "window": block["window"], "resume_after": block["resume_after"],
            "open_ended": block["open_ended"], "outcome": block["outcome"], "mode": block["mode"],
            "executor_id": block["executor_id"], "attempt": block["attempt"], "reset_source": block["reset_source"],
            "reset_confidence": block["reset_confidence"], "handoffs": int(prior.get("handoffs") or 0),
            "max_handoffs": maximum, "failed_resumes": failed,
            "retry": held.retry, "blocked": block.get("blocked")}
        return {key: value for key, value in payload.items() if value is not None}

    def _set_hold_checkpoint(self, task_id: str, block: dict[str, Any], ref: str, actor: str) -> dict[str, Any]:
        """Write the patch blob into the hold it belongs to; a hold that changed meanwhile is left as it is."""
        try:
            task = self._tasks.get_task(task_id)
            current = (task.get("continuity") or {}).get("hold")
            if not current or current.get("pause_seq") != block.get("pause_seq"):
                return block
            updated = {**current, "checkpoint": ref}
            self._tasks._edit_continuity(task_id, {"hold": updated}, actor=actor, expected_revision=task["revision"])
            return updated
        except ServiceError:
            return block

    def _clear_streaks(self, task_id: str, prior: Mapping[str, Any], actor: str, drift: list[str]) -> None:
        """An attempt that did not end at a limit ends the streaks: ``failed_resumes`` and ``rate_retries`` go back to zero,
        and the settings the app reported different (``config_drift``) are kept for the next resume. Writes nothing when
        there is nothing to change; a task that cannot take the change is left alone."""
        changes: dict[str, Any] = {}
        for key in ("failed_resumes", "rate_retries"):
            if prior.get(key):
                changes[key] = 0
        if drift:
            changes["config_drift"] = list(drift)
        if not changes:
            return
        try:
            self._tasks._edit_continuity(task_id, changes, actor=actor)
        except ServiceError:
            _logger.info("continuity counters of %s were not reset", task_id)

    def _pause_task(self, task_id: str, actor: str, cause: str, *, attempt: int | None = None) -> str | None:
        """Pause the task after a failed attempt; a task that moved on meanwhile is left alone and said so.

        With ``attempt`` only a task that is still ``running`` in that attempt is paused, and only at the revision that was
        read (read again, up to three times, if something else changed the task in between). It is a second guard: a
        resume keeps the attempt number, so what stops the end of an attempt from pausing a later run of the same task is
        ``_Launch.settled``.
        """
        error: ServiceError | None = None
        for _ in range(1 if attempt is None else 3):
            try:
                expected = None
                if attempt is not None:
                    task = self._tasks.get_task(task_id)
                    if task.get("state") != "running" or task.get("attempt") != attempt:
                        return "task_not_paused: attempt_changed"
                    expected = task["revision"]
                self._tasks.transition(task_id, "pause", actor=actor, cause=cause, expected_revision=expected)
                return None
            except ServiceError as exc:
                error = exc
        return f"task_not_paused: {error.code}" if error is not None else None

    # -------- checkpoint (CNT-T03) --------
    def _save_checkpoint(self, attempt: _Attempt, plan: _Plan, view: Mapping[str, Any], actor: str,
                         workdir: Path, *, retries_left: int = 0) -> tuple[dict[str, Any] | None, str | None]:
        """Checkpoint an attempt that ended at a limit; a failure here is a note of the run, never an error of it."""
        try:
            saved = self.checkpoint_limit(str(view["id"]), attempt.limit, session_id=attempt.session_id or "",
                                          executor_id=plan.executor_id, attempt=int(view["attempt"]), workdir=workdir,
                                          last_message=attempt.last_message, actor=actor,
                                          retries_left=retries_left)
        except ServiceError as exc:
            return None, f"checkpoint_failed: {exc.code}"
        except Exception as exc:        # the attempt is already recorded: nothing the checkpoint does may lose it
            _logger.exception("checkpoint of %s failed", view["id"])
            return None, f"checkpoint_failed: {type(exc).__name__}"
        return saved, None

    @staticmethod
    def _checkpoint_of(conn: Any, task_id: str, attempt: int, session_id: str, ref: str | None,
                       reason: str | None) -> dict[str, Any] | None:
        """The latest checkpoint of this task, attempt and session when it holds the same work (``session_id`` empty when none).

        The same work is the same patch blob or, without a patch, the same reason for having none. A session that
        was resumed and did more work after a limit has the same attempt and session but another patch: that is a new
        checkpoint, not a second detection of the old one.
        """
        for row in conn.execute("SELECT * FROM team_events WHERE type=? AND task_id=? ORDER BY seq DESC",
                                (CHECKPOINT_EVENT, task_id)):
            event = ExecutorServices._row(row)
            payload = event["payload"]
            if payload.get("attempt") == attempt and (payload.get("session_id") or "") == session_id:
                same = payload.get("patch") == ref and (ref is not None or payload.get("patch_reason") == reason)
                return event if same else None
        return None

    def _known_workdir(self, task_id: str, executor_id: str) -> Path | None:
        """The work folder of the task as ``_workdir`` would pick it, without creating it; None when there is none yet."""
        catalog = getattr(self._services.system, "catalog", None)
        routing = getattr(catalog, "executor_routing", {}) or {}
        per_task = routing.get("task_workdirs", {}) if isinstance(routing, Mapping) else {}
        declaration = (self.registry.config.get("executors", {}) or {}).get(executor_id, {}) if executor_id else {}
        configured = (per_task.get(task_id) if isinstance(per_task, Mapping) else None) or (
            declaration.get("workdir") if isinstance(declaration, Mapping) else None)
        folder = (Path(str(configured)) if configured
                  else self._services.settings.executor_workdir_root / f"task-{task_id}")
        return folder if folder.is_absolute() and folder.is_dir() else None

    @staticmethod
    def _checkpoint_view(event: Mapping[str, Any], *, created: bool) -> dict[str, Any]:
        """The checkpoint a ``task.checkpointed`` event records, in the shape ``checkpoint_limit`` returns."""
        payload = event["payload"]
        return {
            "task_id": event["task_id"], "saved": True, "created": created, "skipped": None,
            "attempt": payload.get("attempt"), "session_id": payload.get("session_id"),
            "executor_id": payload.get("executor_id"), "limit_kind": payload.get("limit_kind"),
            "base_commit": payload.get("base_commit", ""),
            "patch": {"ref": payload["patch"], "size": payload.get("patch_size")} if payload.get("patch") else None,
            "patch_reason": payload.get("patch_reason"), "summary": payload.get("summary"),
            "files": payload.get("files", []), "files_total": payload.get("files_total", 0),
            "seq": event["seq"], "created_at": event["created_at"]}

    @service_errors
    def checkpoint_limit(self, task_id: str, limit: Any = None, *, session_id: str | None = None,
                         executor_id: str | None = None, attempt: int | None = None, workdir: Path | str | None = None,
                         last_message: str = "", actor: str | None = None, retries_left: int = 0,
                         retire: str | None = None, record_limit: bool = True) -> dict[str, Any]:
        """Save the work of an executor that hit a limit (contract section 5); idempotent per task, attempt and session.

        ``limit`` is what the adapter reported (CNT-T02: ``{kind, window, reset_at, reset_source, reset_confidence,
        reset_zone, evidence}``, a mapping or an object; none means ``limit_unknown``). The diff of ``workdir`` (default:
        the task's work folder) is stored as a blob; a summary is written without a model; the blob is added to the
        task's ``context_refs`` (the patch of an earlier checkpoint is replaced) and the summary, as text, to up to three
        ``constraints`` (those of an earlier checkpoint are replaced); ``continuity.limit_detected`` and
        ``task.checkpointed`` are recorded. ``session_id``, ``executor_id`` and ``attempt`` default to the task's latest
        recorded session and its current attempt (``session_id=""`` says the attempt has no session);
        ``last_message`` is the last text the agent wrote.

        Idempotent: a second detection with the same work (the same patch, or the same reason for having none) for the
        same task, attempt and session returns the first checkpoint (``created`` false) and writes nothing. A session
        resumed after a wait that did more work before the next limit has the same attempt and session but another
        patch, and that is a new checkpoint.

        A ``rate_limited`` limit with ``retries_left`` above zero saves nothing (``skipped``): the attempt will be retried.
        ``retire`` (``handoff`` or ``superseded``) also retires the session (``retire_session``); without it the session
        stays resumable, because a wait that ends may continue it with its history. ``record_limit`` false (a wait that is
        not a limit, CNT-T12) saves the checkpoint without ``continuity.limit_detected`` and without a ``limit_kind``.

        Returns ``{task_id, saved, created, skipped, attempt, session_id, executor_id, limit_kind, base_commit, patch,
        patch_reason, summary, files, files_total, seq, created_at, limit, constraints, context_refs, context, retired}``.
        ``patch`` is ``{ref, size}`` or None, then ``patch_reason`` says why (``not_git``, ``empty``, ``too_large`` or
        ``git_error``); a task with no patch is not handed off automatically. ``created`` is false when the checkpoint
        already existed (``limit``, ``constraints``, ``context_refs`` and ``context`` are then absent). ``context`` says
        whether the references and constraints were ``attached``, ``unchanged`` or ``skipped`` (the task is closed or
        being validated). Errors: ``not_found``, ``invalid_request``.
        """
        task_id = self._task_id(task_id)
        task = self._tasks.get_task(task_id)
        info = _checkpoint.normalize_limit(limit)
        if retire is not None and retire not in RETIRE_REASONS:
            raise _invalid("retire", f"retire must be one of {', '.join(RETIRE_REASONS)}")
        if isinstance(retries_left, bool) or not isinstance(retries_left, int) or retries_left < 0:
            raise _invalid("retries_left", "retries_left must be an integer of zero or more")
        if attempt is not None and (isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1):
            raise _invalid("attempt", "attempt must be an integer of one or more")
        if executor_id is not None and (not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id)):
            raise _invalid("executor_id", f"Invalid executor_id: {executor_id!r}")
        if session_id is not None and (not isinstance(session_id, str)
                                       or (session_id and not _SESSION_ID.fullmatch(session_id))):
            raise _invalid("session_id", "session_id must be a short token such as a UUID, or empty for none")
        if workdir is not None and not (isinstance(workdir, (str, os.PathLike)) and Path(workdir).is_absolute()):
            raise _invalid("workdir", "workdir must be an absolute path")
        stored = self._stored_session(task_id)
        # Without session_id the latest recorded session is the one; an empty one says the attempt has none.
        sid = (session_id if session_id is not None else (str(stored["session_id"]) if stored else "")).lower()
        executor_id = executor_id or (str(stored.get("executor_id", "")) if stored else "")
        attempt = attempt or int(task["attempt"])
        actor = _text("actor", actor if actor is not None else (f"executor:{executor_id}" if executor_id else "framework"),
                      maximum=100)
        if info["kind"] == "rate_limited" and retries_left > 0:
            return {"task_id": task_id, "saved": False, "created": False, "skipped": "rate_limited_retry",
                    "attempt": attempt, "session_id": sid or None, "executor_id": executor_id or None, "limit": info,
                    "retired": None}
        with self._checkpoint_lock:
            result = self._write_checkpoint(task, info, sid, executor_id, attempt, workdir, last_message, actor,
                                            record_limit)
        result["retired"] = (self.retire_session(sid, reason=retire, actor=actor)
                             if retire and sid and is_uuid(sid) else None)
        return result

    def _write_checkpoint(self, task: Mapping[str, Any], info: dict[str, Any], sid: str, executor_id: str, attempt: int,
                          workdir: Path | str | None, last_message: str, actor: str,
                          record_limit: bool = True) -> dict[str, Any]:
        task_id = str(task["id"])
        envelopes = self._services.envelopes
        folder = Path(workdir) if workdir is not None else self._known_workdir(task_id, executor_id)
        limit_bytes = min(envelopes.blob_max_bytes, HARD_MAX_BLOB_BYTES)
        patch = (_checkpoint.collect_patch(folder, max_bytes=limit_bytes) if folder is not None
                 else _checkpoint.WorkPatch(None, _checkpoint.NOT_GIT))
        ref, size = None, None
        if patch.content is not None:
            blob = envelopes.put_blob(patch.content, actor=actor, media_type="text/x-diff")
            ref, size = blob["ref"], blob["size"]
        with self._store.db.connect() as conn:
            done = self._checkpoint_of(conn, task_id, attempt, sid, ref, patch.reason)
        if done is not None:                # a second detection of the same work: nothing more is written
            return self._checkpoint_view(done, created=False)
        try:
            envelope = envelopes.get_envelope(task_id)
        except ServiceError:                # for example a task with legacy release dependencies: no gate to show
            envelope = {}
        objective = str(envelope.get("objective") or task.get("objective") or task.get("title") or "")
        gate = [str(check) for check in envelope.get("gate", [])]
        files, message = list(patch.files), _checkpoint.clip_message(last_message)
        summary = _checkpoint.build_summary(files=files, files_total=len(files), last_message=message,
                                            objective=objective, gate=gate)
        constraints = _checkpoint.checkpoint_constraints(attempt=attempt, files=files, files_total=len(files),
                                                         last_message=message, objective=objective, gate=gate)
        with self._store.db.connect() as conn:
            earlier = self._latest(conn, CHECKPOINT_EVENT, task_id=task_id)
        context = self._attach_context(task_id, ref, constraints, earlier["payload"].get("patch") if earlier else None,
                                       actor)
        payload = _checkpoint.fit_payload({
            "attempt": attempt, "session_id": sid or None, "executor_id": executor_id or None,
            "base_commit": patch.base_commit, "patch": ref, "patch_size": size, "patch_reason": patch.reason,
            **({"limit_kind": info["kind"]} if record_limit else {}), "summary": summary, "files": files[:20],
            "files_total": len(files)},
            EVENT_PAYLOAD_BUDGET, ("files", "summary"))
        detected = _checkpoint.fit_payload({"attempt": attempt, "session_id": sid or None,
                                            "executor_id": executor_id or None, **info},
                                           EVENT_PAYLOAD_BUDGET, ("evidence",))
        group_id = str(task["group_id"])
        with self._store.transaction() as conn:
            again = self._checkpoint_of(conn, task_id, attempt, sid, ref, patch.reason)
            if again is not None:           # another process wrote it between the check and now
                return self._checkpoint_view(again, created=False)
            # Both events in one transaction: a checkpoint never lacks its detection, nor the reverse.
            if record_limit:
                self._store.append_event(conn, LIMIT_EVENT, actor=actor, group_id=group_id, task_id=task_id,
                                         cause="limit detected", ref=executor_id, payload=detected)
            seq = self._store.append_event(conn, CHECKPOINT_EVENT, actor=actor, group_id=group_id, task_id=task_id,
                                           cause="checkpoint saved", ref=ref or "", payload=payload)
        event = {"task_id": task_id, "payload": payload, "seq": seq, "created_at": _iso(self.clock())}
        return {**self._checkpoint_view(event, created=True), "limit": info if record_limit else None,
                "constraints": constraints,
                "context_refs": context["context_refs"], "context": context["state"]}

    def _attach_context(self, task_id: str, ref: str | None, constraints: list[str], earlier_ref: str | None,
                        actor: str) -> dict[str, Any]:
        """Put the patch reference and the summary constraints in the task, replacing those of an earlier checkpoint."""
        current = self._services.envelopes.store.task_context(task_id) or {}
        refs, texts = list(current.get("context_refs", [])), list(current.get("constraints", []))
        if ref is not None:
            refs = [item for item in refs if item not in (earlier_ref, ref)] + [ref]
            refs = refs[-MAX_CONTEXT_REFS:]
        texts = [item for item in texts if not item.startswith(_checkpoint.CHECKPOINT_TAG)]
        texts = texts[: MAX_CONSTRAINTS - len(constraints)] + constraints
        if refs == current.get("context_refs", []) and texts == current.get("constraints", []):
            return {"context_refs": refs, "state": "unchanged"}
        try:
            self._services.envelopes.set_task_context(task_id, actor=actor, context_refs=refs, constraints=texts)
        except ServiceError as exc:         # the task is closed or being validated: the checkpoint is still kept
            return {"context_refs": current.get("context_refs", []), "state": f"skipped: {exc.code}"}
        return {"context_refs": refs, "state": "attached"}

    @service_errors
    def latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """The latest checkpoint of the task in the shape of ``checkpoint_limit`` (without ``limit``, ``constraints`` or
        ``context``), or None when it has none. The patch is read with ``envelopes.get_artifact(patch["ref"])``."""
        task_id = self._task_id(task_id)
        self._tasks.get_task(task_id)
        with self._store.db.connect() as conn:
            event = self._latest(conn, CHECKPOINT_EVENT, task_id=task_id)
        return self._checkpoint_view(event, created=False) if event else None

    @service_errors
    def retire_session(self, session_id: str, *, reason: str, actor: str | None = None) -> dict[str, Any]:
        """Retire a session the framework created: it is never resumed again and nothing is written inside the app.

        ``reason`` is ``handoff`` (the task went to another agent) or ``superseded`` (a new session continues it).
        ``resume_task`` then refuses the session (``session_retired``) and ``session_guard`` is False; ``open_target``
        still shows it, so the user can read it. Idempotent: a session retired already keeps its first reason and writes
        no second event. Returns ``{session_id, retired, created, reason, task_id, executor_id, attempt}``. Errors:
        ``invalid_request``, ``not_found`` (a session the framework did not record).
        """
        if not is_uuid(session_id):
            raise _invalid("session_id", "session_id must be a UUID")
        if reason not in RETIRE_REASONS:
            raise _invalid("reason", f"reason must be one of {', '.join(RETIRE_REASONS)}")
        actor = _text("actor", actor if actor is not None else "framework", maximum=100)
        sid = session_id.lower()
        with self._store.transaction() as conn:
            stored = self._latest(conn, SESSION_EVENT, ref=sid)
            if stored is None:
                raise _missing("Session", sid)
            event = self._latest(conn, RETIRED_EVENT, ref=sid)
            created = event is None
            if event is None:
                payload = {"reason": reason, "executor_id": stored["payload"].get("executor_id"),
                           "attempt": stored["payload"].get("attempt")}
                self._store.append_event(conn, RETIRED_EVENT, actor=actor, group_id=stored["group_id"],
                                         task_id=stored["task_id"], ref=sid, cause=f"session retired: {reason}",
                                         payload=payload)
                event = {"payload": payload, "task_id": stored["task_id"]}
        data = event["payload"]
        return {"session_id": sid, "retired": True, "created": created, "reason": data.get("reason"),
                "task_id": event["task_id"], "executor_id": data.get("executor_id"), "attempt": data.get("attempt")}

    # -------- hold and resume (CNT-T04) --------
    @service_errors
    def lift_pause(self, executor_id: str, *, actor: str = "user", reason: str = "") -> dict[str, Any]:
        """Lift the pause of an executor (``executor.pause_lifted``): the person says the executor can work again.

        This is the only end of a ``credits_exhausted`` pause (decision 3) and it also cuts a timed one short. The tasks
        held on that executor become due for ``resume_due`` at once, whatever their time. Idempotent: an executor that is
        not paused writes nothing. Returns ``{executor_id, lifted, was_open_ended, was_until, held_tasks}``
        (``held_tasks``: the tasks whose hold waits on this executor). Errors: ``invalid_request``, ``not_found``.
        """
        if not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id):
            raise _invalid("executor_id", f"Invalid executor_id: {executor_id!r}")
        if self.registry.get(executor_id) is None:
            raise _missing("Executor", executor_id)
        actor = _text("actor", actor, maximum=100)
        if not isinstance(reason, str):
            raise _invalid("reason", "reason must be a text")
        reason = _collapse(reason, 200)
        with self._store.transaction() as conn:
            pause = self._pause_of(conn, executor_id)
            if pause is not None:
                until = _iso(pause["until"]) if pause["until"] is not None else None
                self._store.append_event(
                    conn, PAUSE_LIFTED_EVENT, actor=actor, ref=executor_id, cause="pause lifted",
                    payload=_fit({"executor_id": executor_id, "by": actor, "reason": reason,
                                  "was": {"until": until, "open_ended": pause["open_ended"],
                                          "limit_kind": pause["payload"].get("limit_kind")}}))
        held = [str(task["id"]) for task in self._store.list_tasks(states=["paused"])
                if (hold := _hold.hold_of(task)) and _hold.is_limit_hold(hold) and hold.get("executor_id") == executor_id]
        return {"executor_id": executor_id, "lifted": pause is not None,
                "was_open_ended": bool(pause and pause["open_ended"]),
                "was_until": _iso(pause["until"]) if pause and pause["until"] is not None else None,
                "held_tasks": held}

    def _judge_hold(self, conn: Any, task: Mapping[str, Any], hold: Mapping[str, Any],
                    now: datetime) -> tuple[str, dict[str, Any]]:
        """``("due" | "waiting" | "skipped", info)`` for one held task."""
        info = {"task_id": task["id"], "reason": hold.get("reason"), "executor_id": hold.get("executor_id"),
                "resume_after": hold.get("resume_after"), "open_ended": bool(hold.get("open_ended"))}
        if _hold.is_condition_hold(hold):
            return "skipped", {**info, "why": "has_condition"}        # a wait by condition: ``resume_met`` releases it
        if not _hold.is_limit_hold(hold):
            return "skipped", {**info, "why": "not_a_limit_hold"}     # another wait: its own watcher resumes it
        if task["assignee"] != hold.get("actor"):
            return "skipped", {**info, "why": "assignee_changed", "assignee": task["assignee"]}
        executor_id = str(hold.get("executor_id") or "")
        pause = self._pause_of(conn, executor_id)
        lifted = self._lifted_since(conn, executor_id, hold.get("pause_seq"))
        due, why = _hold.hold_due(hold, now=now, executor_paused=pause is not None, lifted=lifted)
        if due:
            return "due", {**info, "why": why}
        if pause is not None and pause["until"] is not None:
            info["paused_until"] = _iso(pause["until"])
        return ("skipped" if why.startswith("blocked:") else "waiting"), {**info, "why": why}

    @service_errors
    def resume_due(self, *, task_id: str | None = None, limit: int | None = None, dry_run: bool = False,
                   timeout_seconds: int | None = None) -> dict[str, Any]:
        """Resume the tasks whose hold is due, one after the other (contract section 10); the orchestrator or the CLI calls it.

        A task is resumed only when it is still ``paused``, still owned by the agent that held it, its executor is not
        paused and its hold is due: ``resume_after`` has passed, or a person lifted the executor's pause
        (``lift_pause``). A ``credits_exhausted`` hold has no time and waits for that lift. A hold that is not a limit
        (another reason, CNT-T12) is left to its own watcher; three failed resumptions in a row (``failed_resumes``) stop
        the automatic resume until a person decides.

        The session is continued with its history (``resume_task``, passing the recorded model, effort and permission
        again) when the executor declares ``resume``, the session is still the held one and nothing drifted (the
        permission, model, effort, adapter or folder differ from the recorded ones, or an earlier resume reported
        ``config_drift``). Otherwise the task is relaunched from the checkpoint: ``checkpoint_limit(retire="superseded")``
        saves it if needed and retires the old session, and a new session starts with the recorded model and effort
        (dropped when the executor no longer takes them). A session opened in an app in the last 12 hours is neither
        resumed nor replaced (``session_open_in_app``). If the resumed run hits a limit again it is classified again and
        held again (``failed_resumes`` one higher).

        ``task_id`` limits the call to one task, ``limit`` to that many resumptions, ``dry_run`` only reports. Returns
        ``{at, dry_run, held, due, resumed, waiting, skipped}``: ``due`` and ``waiting`` list ``{task_id, reason,
        executor_id, resume_after, open_ended, why}``; ``resumed`` has ``{task_id, mode (resume or relaunch), outcome,
        executor_id, session_id, attempt, config_drift, minutes_waited, hold}`` (``hold`` is the new one when the limit
        came back; ``context_handoff`` is added only when CMP's threshold replaced the session: mode is then ``relaunch``);
        ``skipped`` has the ``why`` (``assignee_changed``, ``not_a_limit_hold``, ``blocked:failed_resumes``,
        ``hold_changed``, ``session_open_in_app``, ``executor_paused``, ...).
        """
        if task_id is not None:
            task_id = self._task_id(task_id)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise _invalid("limit", "limit must be an integer of one or more")
        now = self.clock()
        out: dict[str, Any] = {"at": _iso(now), "dry_run": bool(dry_run), "held": 0, "due": [], "resumed": [],
                               "waiting": [], "skipped": []}
        rows = [(task, hold) for task in self._store.list_tasks(states=["paused"])
                if (hold := _hold.hold_of(task)) is not None and (task_id is None or task["id"] == task_id)]
        out["held"] = len(rows)
        queue: list[tuple[dict[str, Any], dict[str, Any]]] = []
        with self._store.db.connect() as conn:
            for task, hold in rows:
                verdict, info = self._judge_hold(conn, task, hold, now)
                if verdict == "due":
                    queue.append((info, hold))
                else:
                    out[verdict].append(info)
        queue.sort(key=lambda item: (item[0]["resume_after"] or "", str(item[0]["task_id"])))
        for info, hold in queue:
            if limit is not None and len(out["due"]) >= limit:
                out["waiting"].append({**info, "why": "limit"})
                continue
            out["due"].append(info)
            if dry_run:
                continue
            try:
                out["resumed"].append(self._resume_one(info, hold, timeout_seconds))
            except ServiceError as exc:
                out["skipped"].append({**info, "why": str(exc.details.get("reason") or exc.code),
                                       "message": exc.message})
        return out

    def _resume_one(self, info: Mapping[str, Any], seen: Mapping[str, Any], timeout_seconds: int | None,
                    trigger: str = "auto") -> dict[str, Any]:
        task_id = str(info["task_id"])
        task = self._tasks.get_task(task_id)
        hold = dict((task.get("continuity") or {}).get("hold") or {})
        if (task["state"] != "paused" or not hold or not self._same_hold(hold, seen)
                or task["assignee"] != hold.get("actor")):
            raise _conflict("hold_changed", "The task or its hold changed since it was checked", task_id=task_id)
        if trigger == "condition" and not (_hold.condition_of(hold) or {}).get("met_at"):
            raise _conflict("condition_not_met", "The condition of the hold was not recorded as met", task_id=task_id)
        actor, executor_id = str(hold["actor"]), str(hold.get("executor_id") or "")
        descriptor, adapter, _declaration = self._executor(executor_id)         # a paused or disabled executor waits
        sid = str(hold["session_id"]).lower() if hold.get("session_id") else ""
        session = self._stored_session(task_id)
        with self._store.db.connect() as conn:
            retired = bool(sid) and self._latest(conn, RETIRED_EVENT, ref=sid) is not None
            blocked = self._opened_until(conn, sid) if sid else None
        if blocked is not None:
            raise _conflict("session_open_in_app", "The held session was opened in an app in the last 12 hours: it is "
                            "neither resumed nor replaced", task_id=task_id, session_id=sid, blocked_until=_iso(blocked))
        recorded = dict(hold.get("settings") or {})
        before = self._drift_before(task, executor_id, descriptor, adapter, recorded)
        earlier = [str(name) for name in (task.get("continuity") or {}).get("config_drift") or []]
        drift = tuple(dict.fromkeys([*before, *earlier]))
        history = (bool(sid) and session is not None and str(session["session_id"]).lower() == sid and not retired
                   and descriptor.resume.status is SupportState.SUPPORTED and not drift)
        if history:
            result = self._resume_task(task_id, None, None, actor, timeout_seconds=timeout_seconds, hold=hold,
                                       trigger=trigger)
        else:
            # BDI-T04: a relaunch that will be refused must not retire the held session first.
            self._require_launch_effects(task, executor_id, Permission.WRITE if task["file_scope"] else Permission.READ,
                                         "launch")
            # MOD-T04: nor may the privacy check refuse it after the retirement (the model is the one ``_relaunch`` sends).
            self._privacy_precheck(task, executor_id, descriptor,
                                   recorded.get("model") if _supports(descriptor.model_support, recorded.get("model")) else None)
            self._supersede(task_id, hold, actor)
            result = self._relaunch(task_id, executor_id, recorded.get("model"), recorded.get("effort"), actor, hold,
                                    drift, timeout_seconds, trigger)
        since = _hold.parse_utc(hold.get("since"))
        waited = max(int((self.clock() - since).total_seconds() // 60), 0) if since is not None else None
        item = {"task_id": task_id, "mode": "resume" if history and not result.get("context_handoff") else "relaunch",
                "outcome": result["outcome"], "executor_id": executor_id, "session_id": result["session_id"],
                "attempt": result["attempt"], "config_drift": list(drift), "minutes_waited": waited,
                "hold": result.get("hold")}
        if result.get("context_handoff"):
            item["context_handoff"] = result["context_handoff"]      # only a hand-off adds the key: the shape stays
        return item

    def _drift_before(self, task: Mapping[str, Any], executor_id: str, descriptor: Any, adapter: Any,
                      recorded: Mapping[str, Any]) -> list[str]:
        """What a resume would pass now that differs from what the session started with (contract section 10)."""
        current: dict[str, Any] = {
            "permission": Permission.WRITE.value if task["file_scope"] else Permission.READ.value,
            "adapter": adapter.adapter_id}
        if recorded.get("model") is not None:
            current["model"] = recorded["model"] if _supports(descriptor.model_support, recorded["model"]) else None
        if recorded.get("effort") is not None:
            current["effort"] = recorded["effort"] if _supports(descriptor.effort_support, recorded["effort"]) else None
        folder = self._known_workdir(str(task["id"]), executor_id)
        if folder is not None:
            current["workdir"] = _workdir_id(folder)
        return _hold.detect_drift(recorded, current)

    @staticmethod
    def _same_hold(current: Mapping[str, Any], seen: Mapping[str, Any]) -> bool:
        """True when ``current`` is the hold ``seen`` was read from (a hold is told by its ``since``, ``pause_seq`` and ``id``)."""
        return all(current.get(key) == seen.get(key) for key in ("since", "pause_seq", "id"))

    def _supersede(self, task_id: str, hold: Mapping[str, Any], actor: str) -> None:
        """Before a relaunch: make sure the checkpoint exists and retire the held session as ``superseded``."""
        sid = str(hold["session_id"]).lower() if hold.get("session_id") else ""
        limit = {"kind": hold.get("reason"), **{name: hold.get(name) for name in (
            "window", "reset_at", "reset_source", "reset_confidence", "reset_zone", "evidence")}}
        attempt = hold.get("attempt")
        try:
            self.checkpoint_limit(task_id, limit, session_id=sid, executor_id=hold.get("executor_id"),
                                  attempt=attempt if isinstance(attempt, int) and not isinstance(attempt, bool)
                                  and attempt >= 1 else None, actor=actor, retries_left=0, retire="superseded",
                                  record_limit=_hold.is_limit_hold(hold))
        except ServiceError:
            if sid and is_uuid(sid):
                try:
                    self.retire_session(sid, reason="superseded", actor=actor)
                except ServiceError:
                    _logger.info("session %s of %s was not retired", sid, task_id)

    def _relaunch(self, task_id: str, executor_id: str, model: str | None, effort: str | None, actor: str,
                  hold: Mapping[str, Any], drift: tuple[str, ...], timeout_seconds: int | None,
                  trigger: str = "auto") -> dict[str, Any]:
        """A new session with the recorded model and effort; one the executor no longer takes is dropped, not an error."""
        for _ in range(3):
            try:
                return self._run_task(task_id, executor_id, model, effort, actor, timeout_seconds=timeout_seconds,
                                      hold=hold, trigger=trigger, drift=drift)
            except ServiceError as exc:
                reason = exc.details.get("reason")
                if reason == "model_not_supported" and model is not None:
                    model = None
                elif reason == "effort_not_supported" and effort is not None:
                    effort = None
                else:
                    raise
        raise _conflict("relaunch_failed", "The relaunch could not choose a model and effort", task_id=task_id)

    # -------- waits by condition (CNT-T12) --------
    @service_errors
    def hold_on_condition(self, task_id: str, condition: Mapping[str, Any], *, reason: str | None = None,
                          actor: str | None = None, executor_id: str | None = None,
                          deadline_minutes: int | None = None, resume_after: Any = None,
                          watcher_id: str | None = None) -> dict[str, Any]:
        """Make a task wait for a condition (contract Amendment 2); no model decides when it ends.

        ``condition`` is ``{type, params}`` (see ``waits.normalize_condition``: ``time``, ``dependency``,
        ``integration_window``, ``approval``, or ``all`` of several). ``reason`` names the wait (``dependency``,
        ``integration_window`` or ``approval``); without it the first part that depends on someone names it. The deadline is
        ``deadline_minutes`` from now (default 12 hours): past it the wait is given up (``continuity.condition_expired``).
        ``resume_after`` is a minimum time: the hold is not released before it. ``watcher_id`` is the process that watches.

        A ``running`` task is paused with its hold in one update; a ``paused`` one without a hold only gets the hold. A paused
        task that is held by a usage limit (and has no condition yet) gets the condition added to its hold: then the reset
        time and the condition must both hold. The executor and the session that will be resumed are the task's last recorded
        ones, or ``executor_id`` (a session of another executor is not resumed: the task is relaunched). Returns ``{task_id,
        hold, task}``. Errors: ``invalid_request``, ``not_found``, ``invalid_state`` (``task_not_holdable``, ``already_held``,
        ``task_without_assignee``).
        """
        task_id = self._task_id(task_id)
        if deadline_minutes is not None and (isinstance(deadline_minutes, bool) or not isinstance(deadline_minutes, int)
                                             or deadline_minutes < 1):
            raise _invalid("deadline_minutes", "deadline_minutes must be an integer of one or more")
        if executor_id is not None and (not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id)):
            raise _invalid("executor_id", f"Invalid executor_id: {executor_id!r}")
        task = self._tasks.get_task(task_id)
        if task["state"] not in ("running", "paused"):
            raise _conflict("task_not_holdable", f"Task {task_id} is {task['state']}; only a running or paused task "
                            "waits on a condition", task_id=task_id, status=task["state"])
        existing = self._view_hold(task)
        if existing is not None and not (_hold.is_limit_hold(existing) and not _hold.is_condition_hold(existing)):
            raise _conflict("already_held", f"Task {task_id} already waits", task_id=task_id)
        assignee = str(task.get("assignee") or "")
        if not assignee:
            raise _conflict("task_without_assignee", f"Task {task_id} has no assignee to resume", task_id=task_id)
        actor = _text("actor", actor if actor is not None else assignee, maximum=100)
        now = self.clock()
        minimum = None
        if resume_after is not None:
            minimum = _hold.parse_utc(resume_after)
            if minimum is None:
                raise _invalid("resume_after", "resume_after must be a time")
        try:
            block = _waits.normalize_condition(
                condition, now=now, deadline=timedelta(minutes=deadline_minutes) if deadline_minutes else None,
                watcher_id=watcher_id)
            why = existing["reason"] if existing is not None else _waits.reason_for(block, reason)
            if existing is not None and reason is not None and reason != why:
                raise _waits.WaitError("reason", f"the task is held by {why}; the reason stays {why}")
        except _waits.WaitError as exc:
            raise _invalid(exc.field, str(exc)) from None
        prior = dict(task.get("continuity") or {})
        if existing is not None:
            # A usage limit hold gets a condition: the reset time and the condition must both hold.
            hold = {**existing, "id": existing.get("id") or uuid.uuid4().hex[:16], "condition": block}
            if minimum is not None:
                hold["resume_after"] = utc_iso(max(minimum, _hold.parse_utc(existing.get("resume_after")) or minimum))
            reset = _hold.parse_utc(hold.get("resume_after")) if hold.get("resume_after") else None
            if reset is not None and reset > _hold.parse_utc(block["deadline_at"]):
                raise _invalid("deadline_minutes", "The deadline of the condition is before the time the limit resets")
            self._tasks._edit_continuity(task_id, {"hold": hold}, actor=actor, expected_revision=task["revision"])
        else:
            stored = self._stored_session(task_id)
            executor_id = executor_id or (str(stored.get("executor_id") or "") if stored else "")
            if not executor_id:
                raise _invalid("executor_id", "Give executor_id: no session of this task was recorded by the framework")
            if self.registry.get(executor_id) is None:
                raise _missing("Executor", executor_id)
            same = stored is not None and str(stored.get("executor_id") or "") == executor_id
            settings = _session_settings(stored) if same and stored is not None else {}
            folder = self._known_workdir(task_id, executor_id)
            if folder is not None:
                settings["workdir"] = _workdir_id(folder)
            hold = _waits.build_wait_hold(
                block, reason=why, now=now, hold_id=uuid.uuid4().hex[:16], executor_id=executor_id, actor=assignee,
                session_id=str(stored["session_id"]) if same and stored is not None else None,
                attempt=int(task["attempt"]), settings=settings, resume_after=minimum)
            changes: dict[str, Any] = {"hold": hold}
            if "handoffs" not in prior:
                changes["handoffs"] = 0
            if not prior.get("chain"):
                changes["chain"] = [executor_id]
            if task["state"] == "running":
                self._tasks.transition(task_id, "pause", actor=assignee, cause=f"waiting for {why}",
                                       expected_revision=task["revision"], continuity=changes)
            else:
                self._tasks._edit_continuity(task_id, changes, actor=actor, expected_revision=task["revision"])
        payload = {"reason": hold["reason"], "resume_after": hold.get("resume_after"), "open_ended": False,
                   "outcome": hold.get("outcome") or "wait_condition", "mode": hold.get("mode"),
                   "executor_id": hold.get("executor_id"), "attempt": hold.get("attempt"), "hold_id": hold["id"],
                   "handoffs": int(prior.get("handoffs") or 0),
                   "max_handoffs": self._policy_of(prior).max_handoffs, "failed_resumes": 0,
                   "condition": _waits.summarize(block)}
        self._append(HELD_EVENT, actor=actor, task=task, ref=str(hold.get("executor_id") or ""),
                     cause=f"held: {hold['reason']}", payload={key: value for key, value in payload.items()
                                                              if value is not None})
        return {"task_id": task_id, "hold": hold, "task": self._tasks.get_task(task_id)}

    @service_errors
    def record_approval(self, task_id: str, approval_id: str, *, approved: bool, by: str) -> dict[str, Any]:
        """Record a person's decision for an ``approval`` condition of a held task; the next check sees it.

        The decision is kept in the condition (``decisions``) with who made it and when. The framework never approves in the
        name of a person: ``by`` cannot start with ``system:`` or ``executor:``, and when the condition names an ``approver``
        only that one decides. Deciding the same way again changes nothing; deciding the other way is refused. A rejection
        cannot be waited out (the check reports ``approval_rejected``). Returns ``{task_id, approval_id, status, by, created,
        hold_id}``. Errors: ``invalid_request``, ``invalid_state`` (``not_waiting_for_approval``, ``approver_not_allowed``,
        ``approver_mismatch``, ``already_decided``, ``hold_changed``).
        """
        task_id = self._task_id(task_id)
        try:
            approval_id = _waits.check_token(approval_id, "approval_id")
        except _waits.WaitError as exc:
            raise _invalid(exc.field, str(exc)) from None
        if not isinstance(approved, bool):
            raise _invalid("approved", "approved must be true or false")
        by = _text("by", by, maximum=100)
        if by.startswith(NOT_APPROVERS):
            raise _conflict("approver_not_allowed", "A program does not approve in the name of a person", task_id=task_id)
        task = self._tasks.get_task(task_id)
        hold = self._view_hold(task)
        condition = _hold.condition_of(hold)
        leaf = None
        if task["state"] == "paused" and hold is not None and condition is not None:
            try:
                leaf = next((item for item in _waits.condition_leaves(condition)
                             if item.get("type") == _waits.APPROVAL
                             and (item.get("params") or {}).get("approval") == approval_id), None)
            except _waits.WaitError:
                leaf = None
        if leaf is None or hold is None or condition is None:
            raise _conflict("not_waiting_for_approval", f"Task {task_id} does not wait for approval {approval_id}",
                            task_id=task_id, approval_id=approval_id)
        approver = (leaf.get("params") or {}).get("approver")
        if approver and by != approver:
            raise _conflict("approver_mismatch", f"Only {approver} decides this approval", task_id=task_id,
                            approval_id=approval_id)
        status = "approved" if approved else "rejected"
        earlier = (condition.get("decisions") or {}).get(approval_id)
        if isinstance(earlier, Mapping):
            if earlier.get("status") != status:
                raise _conflict("already_decided", f"Approval {approval_id} was already {earlier.get('status')}",
                                task_id=task_id, approval_id=approval_id)
            return {"task_id": task_id, "approval_id": approval_id, "status": status, "by": earlier.get("by"),
                    "created": False, "hold_id": hold.get("id")}
        decided_at = utc_iso(self.clock())

        def mutate(current: dict[str, Any]) -> dict[str, Any] | None:
            block = dict(_hold.condition_of(current) or {})
            decisions = dict(block.get("decisions") or {})
            if approval_id in decisions:
                return None
            decisions[approval_id] = {"status": status, "by": by, "at": decided_at}
            return {**current, "condition": {**block, "decisions": decisions}}

        if self._update_hold(task_id, hold, mutate, by) is None:
            raise _conflict("hold_changed", "The task or its hold changed since it was read", task_id=task_id)
        return {"task_id": task_id, "approval_id": approval_id, "status": status, "by": by, "created": True,
                "hold_id": hold.get("id")}

    @staticmethod
    def _view_hold(task: Mapping[str, Any]) -> dict[str, Any] | None:
        """The hold of a task as ``get_task`` shows it (under ``continuity``), or ``None``."""
        hold = (task.get("continuity") or {}).get("hold")
        return dict(hold) if isinstance(hold, Mapping) and hold else None

    def _update_hold(self, task_id: str, seen: Mapping[str, Any],
                     mutate: Callable[[dict[str, Any]], dict[str, Any] | None], actor: str) -> dict[str, Any] | None:
        """Rewrite the hold of a paused task when it is still the one that was seen; the new hold, or ``None``.

        ``mutate`` gets the current hold and returns the new one, or ``None`` to leave it (someone else already did what it
        was going to do). A task that moved on, a hold that is another one or a ``mutate`` that declines give ``None``; a
        revision that went stale between the read and the write is read again once.
        """
        for _ in range(2):
            task = self._tasks.get_task(task_id)
            current = self._view_hold(task)
            if task["state"] != "paused" or current is None or not self._same_hold(current, seen):
                return None
            updated = mutate(current)
            if updated is None:
                return None
            try:
                self._tasks._edit_continuity(task_id, {"hold": updated}, actor=actor, expected_revision=task["revision"])
            except ServiceError as exc:
                if exc.details.get("revision") is not None:      # stale: read it again
                    continue
                raise
            return updated
        return None

    def _condition_rows(self, task_id: str | None) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]],
                                                            dict[str, str], dict[str, datetime]]:
        """The paused tasks that hold a condition, with the states of all tasks and the earliest each held task can restart."""
        tasks = self._store.list_tasks()
        states = {str(task["id"]): str(task["state"]) for task in tasks}
        rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        floors: dict[str, datetime] = {}
        for task in tasks:
            hold = _hold.hold_of(task) if task["state"] == "paused" else None
            if hold is None:
                continue
            floor = _waits.not_before_of_hold(hold)
            if floor is not None:
                floors[str(task["id"])] = floor
            if _hold.is_condition_hold(hold) and (task_id is None or task["id"] == task_id):
                rows.append((task, hold))
        return rows, states, floors

    def _wait_facts(self, task: Mapping[str, Any], hold: Mapping[str, Any], now: datetime, states: Mapping[str, str],
                    floors: Mapping[str, datetime]) -> _waits.WaitFacts:
        """What a condition may read: the task store, plus the two hooks of a watcher process (a broken hook is no evidence)."""
        condition = _hold.condition_of(hold) or {}
        try:
            leaves = _waits.condition_leaves(condition)
        except _waits.WaitError:
            leaves = []
        decided = condition.get("decisions") if isinstance(condition.get("decisions"), Mapping) else {}
        integrations: dict[str, _waits.IntegrationFacts] = {}
        approvals: dict[str, _waits.ApprovalFact] = {}
        for leaf in leaves:
            params = leaf.get("params") or {}
            if leaf.get("type") == _waits.INTEGRATION_WINDOW and self.integration_source is not None:
                window = str(params.get("window"))
                try:
                    found = self.integration_source(window, now)
                except Exception:               # a watcher's reader must not stop the check: no evidence
                    _logger.exception("integration source failed for window %s", window)
                    found = None
                if found is not None:
                    integrations[window] = found
            elif leaf.get("type") == _waits.APPROVAL:
                key = str(params.get("approval"))
                fact = None
                if self.approval_source is not None:
                    try:
                        fact = self.approval_source(str(task["id"]), key)
                    except Exception:
                        _logger.exception("approval source failed for %s", key)
                if fact is None and isinstance(decided.get(key), Mapping):
                    stored = decided[key]
                    fact = _waits.ApprovalFact(str(stored.get("status") or "pending"), stored.get("by"),
                                               _hold.parse_utc(stored.get("at")) if stored.get("at") else None)
                if fact is not None:
                    approvals[key] = fact
        return _waits.WaitFacts(now=now, task_states=states, not_before=floors, integrations=integrations,
                                approvals=approvals)

    def _write_condition(self, task: Mapping[str, Any], hold: Mapping[str, Any], evaluation: _waits.Evaluation,
                         actor: str, *, volatile: bool, watcher_id: str | None,
                         guard: Callable[[Mapping[str, Any]], bool], blocked: str | None = None) -> dict[str, Any] | None:
        """Write an evaluation into the condition of the hold, if ``guard`` still allows it; the new hold, or ``None``."""
        def mutate(current: dict[str, Any]) -> dict[str, Any] | None:
            block = _hold.condition_of(current)
            if block is None or not guard(block):
                return None
            block = _waits.apply_evaluation(block, evaluation, volatile=volatile)
            if watcher_id is not None:
                block["watcher_id"] = watcher_id
            updated = {**current, "condition": block}
            if blocked:
                updated["blocked"] = blocked
            return updated

        return self._update_hold(str(task["id"]), hold, mutate, actor)

    def _assess(self, task: Mapping[str, Any], hold: Mapping[str, Any], now: datetime, states: Mapping[str, str],
                floors: Mapping[str, datetime], *, actor: str, dry_run: bool, record: bool, watcher_id: str | None,
                hold_id: str | None, attempt: int | None) -> tuple[str, dict[str, Any]]:
        """``("due" | "waiting" | "expired" | "skipped", info)`` for one task that holds a condition.

        Checks the condition now, writes the durable facts (the instant it is first seen to hold and its event, a revoked
        ``met_at``, an expiry and its event; nothing with ``dry_run``) and says whether the task may be resumed: the attempt
        is the one that was held, the condition holds, the minimum time has passed and the executor is not paused.
        """
        condition = _hold.condition_of(hold) or {}
        task_id, executor_id = str(task["id"]), str(hold.get("executor_id") or "")
        info: dict[str, Any] = {"task_id": task_id, "hold_id": hold.get("id"), "reason": hold.get("reason"),
                                "executor_id": executor_id, "attempt": hold.get("attempt")}
        if hold_id is not None and hold.get("id") != hold_id:
            return "skipped", {**info, "why": "hold_changed", "state": "skipped"}
        if (attempt is not None and hold.get("attempt") != attempt) or hold.get("attempt") != task["attempt"]:
            return "skipped", {**info, "why": "attempt_changed", "state": "skipped", "task_attempt": task["attempt"]}
        if task["assignee"] != hold.get("actor"):
            return "skipped", {**info, "why": "assignee_changed", "state": "skipped", "assignee": task["assignee"]}
        if hold.get("blocked"):
            expired = hold.get("blocked") == "condition_expired" or bool(condition.get("expired_at"))
            return "skipped", {**info, "why": f"blocked:{hold['blocked']}", "state": "expired" if expired else "skipped"}
        try:
            evaluation = _waits.evaluate(condition, self._wait_facts(task, hold, now, states, floors))
        except _waits.WaitError as exc:
            return "skipped", {**info, "why": "condition_invalid", "state": "skipped", "message": str(exc)}
        info.update(state=evaluation.state, evaluation=evaluation.to_dict(), deadline_at=condition.get("deadline_at"))
        stored_met = bool(condition.get("met_at"))
        if evaluation.expired:
            if not dry_run:
                written = self._write_condition(task, hold, evaluation, actor, volatile=True, watcher_id=watcher_id,
                                                guard=lambda block: not block.get("expired_at"),
                                                blocked="condition_expired")
                if written is not None:
                    self._append(CONDITION_EXPIRED_EVENT, actor=actor, task=task, ref=executor_id,
                                 cause="condition expired", payload=_waits.expired_payload(written, evaluation))
            return "expired", {**info, "why": "deadline_passed"}
        if not dry_run:
            if evaluation.met and not stored_met:
                written = self._write_condition(task, hold, evaluation, actor, volatile=record, watcher_id=watcher_id,
                                                guard=lambda block: not block.get("met_at"))
                if written is not None:
                    self._append(CONDITION_MET_EVENT, actor=actor, task=task, ref=executor_id, cause="condition met",
                                 payload=_waits.met_payload(written, evaluation))
            elif not evaluation.met and stored_met:
                self._write_condition(task, hold, evaluation, actor, volatile=record, watcher_id=watcher_id,
                                      guard=lambda block: bool(block.get("met_at")))
            elif record or (watcher_id is not None and condition.get("watcher_id") != watcher_id):
                self._write_condition(task, hold, evaluation, actor, volatile=record, watcher_id=watcher_id,
                                      guard=lambda block: True)
        with self._store.db.connect() as conn:
            pause = self._pause_of(conn, executor_id)
            lifted = self._lifted_since(conn, executor_id, hold.get("pause_seq"))
        due, why = _waits.wait_due(hold, now=now, executor_paused=pause is not None, condition_met=evaluation.met,
                                   lifted=lifted)
        return ("due" if due else "waiting"), {**info, "why": why}

    @service_errors
    def check_waits(self, *, task_id: str | None = None, watcher_id: str | None = None, dry_run: bool = False,
                    record: bool = False, actor: str = WATCHER_ACTOR) -> dict[str, Any]:
        """Check the condition of every held task (or of ``task_id``): what a watcher calls on each tick (CNT-T13).

        A model is never asked: each condition is checked by ``waits.evaluate`` against the task store and the two hooks
        (``integration_source``, ``approval_source``). The durable facts are written: the instant a condition is first seen to
        hold (``condition.met_at`` and ``continuity.condition_met``, once per streak), a ``met_at`` that no longer holds is
        cleared, and a deadline that passed marks the wait expired (``condition.expired_at``, the hold ``blocked`` with
        ``condition_expired`` and ``continuity.condition_expired``, once). The checks themselves are not events and, unless
        ``record`` is true, not writes either: a watcher keeps its heartbeat in its own state file. ``record`` also writes
        ``checked_at``, ``next_check_at`` and ``estimate`` into the condition. ``watcher_id`` is written into the condition.
        ``dry_run`` writes nothing.

        Returns ``{at, dry_run, held, waits}``; each wait is ``{task_id, hold_id, reason, executor_id, attempt, state, due,
        why, deadline_at, evaluation}`` where ``state`` is ``waiting``, ``met``, ``expired`` or ``skipped`` (``why`` says
        why: ``assignee_changed``, ``attempt_changed``, ``blocked:...``, ``condition_invalid``) and ``evaluation`` has the
        parts, the honest progress (``percent``, ``eta``, ``kind``, ``basis``, ``detail``, ``frozen``, ``revised``), the
        next check and the deadline.
        """
        if task_id is not None:
            task_id = self._task_id(task_id)
        if watcher_id is not None:
            try:
                watcher_id = _waits.check_token(watcher_id, "watcher_id")
            except _waits.WaitError as exc:
                raise _invalid(exc.field, str(exc)) from None
        actor = _text("actor", actor, maximum=100)
        now = self.clock()
        rows, states, floors = self._condition_rows(task_id)
        out: dict[str, Any] = {"at": _iso(now), "dry_run": bool(dry_run), "held": len(rows), "waits": []}
        for task, hold in rows:
            verdict, info = self._assess(task, hold, now, states, floors, actor=actor, dry_run=dry_run, record=record,
                                         watcher_id=watcher_id, hold_id=None, attempt=None)
            out["waits"].append({**info, "due": verdict == "due"})
        return out

    @service_errors
    def resume_met(self, *, task_id: str | None = None, hold_id: str | None = None, attempt: int | None = None,
                   limit: int | None = None, dry_run: bool = False, timeout_seconds: int | None = None,
                   actor: str = WATCHER_ACTOR) -> dict[str, Any]:
        """Resume the held tasks whose condition holds now, one after the other (Amendment 2); the watcher calls it.

        Next to ``resume_due``, which keeps the holds without a condition. For each task that holds a condition it checks the
        condition again at this moment (what was true when a watcher last looked is not trusted) and revalidates the attempt
        before resuming: the task is still ``paused``, still owned by the agent that held it, in the attempt that was
        held (``attempt``, and ``hold_id`` when the watcher names the hold it watches, must match), the deadline has not
        passed, the condition holds, the minimum time (``resume_after``) has passed, the executor is not paused (a limit hold
        also needs its own reset time) and the session was not opened in an app in the last 12 hours. Then the task is
        resumed as ``resume_due`` does: the session with its history when the executor declares ``resume`` and nothing
        drifted, otherwise a relaunch from the checkpoint. Running it by hand never resumes a task whose condition does not
        hold. The program is resumed as the agent that held the task; ``actor`` (``system:watcher``) is who records the
        events of the wait.

        ``task_id`` limits the call to one task, ``limit`` to that many resumptions, ``dry_run`` only reports. Returns ``{at,
        dry_run, held, due, resumed, waiting, expired, skipped}`` with the lists of ``resume_due`` plus ``expired`` (waits
        that gave up) and, in each item, the ``state`` and the ``evaluation`` (progress included).
        """
        if task_id is not None:
            task_id = self._task_id(task_id)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise _invalid("limit", "limit must be an integer of one or more")
        if attempt is not None and (isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1):
            raise _invalid("attempt", "attempt must be an integer of one or more")
        hold_id = self._optional_token("hold_id", hold_id)
        actor = _text("actor", actor, maximum=100)
        now = self.clock()
        rows, states, floors = self._condition_rows(task_id)
        out: dict[str, Any] = {"at": _iso(now), "dry_run": bool(dry_run), "held": len(rows), "due": [], "resumed": [],
                               "waiting": [], "expired": [], "skipped": []}
        queue: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for task, hold in rows:
            verdict, info = self._assess(task, hold, now, states, floors, actor=actor, dry_run=dry_run, record=False,
                                         watcher_id=None, hold_id=hold_id, attempt=attempt)
            if verdict == "due":
                queue.append((info, hold))
            else:
                out[verdict].append(info)
        queue.sort(key=lambda item: (str((item[0].get("evaluation") or {}).get("met_at") or ""), str(item[0]["task_id"])))
        for info, hold in queue:
            if limit is not None and len(out["due"]) >= limit:
                out["waiting"].append({**info, "why": "limit"})
                continue
            out["due"].append(info)
            if dry_run:
                continue
            try:
                out["resumed"].append({**self._resume_one(info, hold, timeout_seconds, trigger="condition"),
                                       "hold_id": info.get("hold_id")})
            except ServiceError as exc:
                out["skipped"].append({**info, "why": str(exc.details.get("reason") or exc.code),
                                       "message": exc.message})
        return out

    # -------- continuity policy and hand-off (CNT-T05) --------
    def _profile_policy(self) -> _handoff.Policy:
        settings = self._services.settings
        return _handoff.Policy(
            mode=settings.continuity_mode, wait_threshold_hours=settings.continuity_wait_threshold_hours,
            max_handoffs=settings.continuity_max_handoffs, candidates=dict(settings.continuity_candidates))

    def _policy_of(self, continuity: Mapping[str, Any] | None) -> _handoff.Policy:
        """The policy in force for a task: the settings, restricted by what the task's continuity block says (it only restricts)."""
        return _handoff.resolve_policy(self._profile_policy(), continuity)

    @service_errors
    def continuity_policy(self, task_id: str | None = None) -> dict[str, Any]:
        """The continuity policy in force (contract section 7): ``{mode, wait_threshold_hours, max_handoffs, candidates,
        source, never_during_integration, subscription_surfaces}``.

        From the settings (``continuity_*``, ``source`` ``settings``); with ``task_id`` it is that task's, restricted by its
        own continuity block (``source`` ``task``). ``candidates`` maps an executor id to ``no_ask``, ``ask`` or ``never``.
        """
        block: Mapping[str, Any] | None = None
        if task_id is not None:
            block = self._tasks.get_task(self._task_id(task_id)).get("continuity")
        policy = self._policy_of(block)
        return {**policy.to_dict(), "source": "task" if task_id is not None else "settings",
                "never_during_integration": True, "subscription_surfaces": list(_handoff.SUBSCRIPTION_SURFACES)}

    def _level_of(self) -> Callable[[str | None], str | None]:
        """Model name or catalog id -> level (T1 to T4) when the catalog classifies it, else None."""
        models = getattr(getattr(self._services.system, "catalog", None), "models", None) or {}
        table: dict[str, str] = {}
        for model_id, spec in models.items():
            level = getattr(spec, "level", None)
            if level in MODEL_LEVELS:
                table[str(model_id).casefold()] = level
                name = getattr(spec, "model", None)
                if isinstance(name, str) and name:
                    table.setdefault(name.casefold(), level)
        return lambda model: table.get(model.casefold()) if isinstance(model, str) and model else None

    def _integration_marker_exists(self) -> bool:
        """The default ``integration_active``: the marker of the installation (``Settings.integration_marker``, the one
        ``multiagent watcher`` takes) exists. It fails closed: only a marker that is not there is calm; a path that cannot
        be read (a folder, a file where the folder should be, no permission) counts as an integration."""
        marker = self._services.settings.integration_marker
        try:
            os.stat(marker)
        except FileNotFoundError:
            # Calm, unless the folder of the marker is a file (Windows says "not found" for that too).
            return marker.parent.exists() and not marker.parent.is_dir()
        except OSError:
            return True
        return True

    def _integrating(self) -> bool:
        """True while the host says an integration is going on, or cannot say: no hook, or one that fails, is not evidence
        of calm (CNT-T05 fails closed, whoever asks and whether the hand-off is automatic or not)."""
        if self.integration_active is None:
            return True
        try:
            return bool(self.integration_active())
        except Exception:  # noqa: BLE001
            _logger.warning("the integration hook failed; no hand-off is made", exc_info=True)
            return True

    def _integration_block(self, message: str) -> ServiceError:
        """The ``integration_in_progress`` refusal, saying what blocks and how it ends: with the default hook it names the
        marker file, its age and who wrote it, because a marker nobody removes (a crashed integration or watcher) would
        otherwise stop every hand-off with no clue. Nothing is deleted: only a person knows the integration is over."""
        details: dict[str, Any] = {}
        if self.integration_active is None:
            note = "no host says whether an integration is going on"
        elif self.integration_active == self._integration_marker_exists:
            marker = self._services.settings.integration_marker
            note = f"marker {marker}"
            try:
                info = os.stat(marker)
                age = max(int((time.time() - info.st_mtime) // 60), 0)
                details["marker_age_minutes"] = age
                note += f", {age} minutes old"
                if marker.is_file():
                    try:
                        owner = json.loads(marker.read_text(encoding="utf-8"))
                        if isinstance(owner, Mapping) and owner.get("pid") is not None:
                            details["marker_pid"] = owner.get("pid")
                            note += f", written by process {owner.get('pid')}"
                    except (OSError, ValueError):
                        pass
                else:
                    note += " (not a file)"
            except OSError:
                note += " (cannot be read)"
            details["marker"] = str(marker)
            note += (". If no integration or watcher is running, it is a leftover: `multiagent watcher sweep` removes the "
                     "one of a dead watcher; any other is deleted by hand")
        else:
            note = "the host's integration hook says so (or failed)"
        return _conflict("integration_in_progress", f"{message} ({note})", **details)

    def _budget_state(self) -> str:
        """``ok``, ``warn_reached`` (a budget in ``warn`` mode is at its limit), ``cut_reached`` or ``unknown`` (USO-T03).

        Only the estimated cost of the framework's own provider calls is measured, so a subscription app adds nothing to it;
        what counts here is that a budget at its limit means the operator asked to stop spending.
        """
        try:
            rows = self._services.usage.budgets().get("status") or []
        except Exception:  # noqa: BLE001 - a budget that cannot be read is not a green light
            _logger.warning("the budgets could not be read; a hand-off without asking is not made", exc_info=True)
            return "unknown"
        state = "ok"
        for row in rows:
            if row.get("scope") == "run":      # the cap of one run (an old one, as often as not) says nothing of a hand-off
                continue
            if row.get("state") == "limit_reached":
                if row.get("mode") == "cut":
                    return "cut_reached"
                state = "warn_reached"
        return state

    def _private_project(self, task: Mapping[str, Any], conn: Any = None) -> bool:
        """True when the task belongs to a private project (PRY-T01 decision 5): its work is never sent elsewhere unasked."""
        group = self._store.get_group(str(task["group_id"]), conn=conn)
        project_id = group.get("project_id") if group else None
        if not project_id:
            return False
        project = self._services.projects.store.get_project(str(project_id), conn=conn)
        return project is None or project.get("visibility") != "normal"

    def _handoff_candidates(self, conn: Any, task: Mapping[str, Any], hold: Mapping[str, Any],
                            policy: _handoff.Policy) -> list[_handoff.Candidate]:
        """Every executor with an adapter, with what the policy needs to know about it as a delegate of this held task."""
        continuity = task.get("continuity") or {}
        used = {str(item) for item in continuity.get("chain") or []} | {str(hold.get("executor_id") or "")}
        settings = hold.get("settings") or {}
        level_of = self._level_of()
        ceiling = level_of(settings.get("model"))
        recorded_folder = settings.get("workdir")
        found: list[_handoff.Candidate] = []
        for executor_id, descriptor in sorted(self.registry.snapshot().items()):
            adapter = self.registry.resolve(executor_id)
            if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
                continue
            pick = select_for_handoff(executor_id, descriptor, adapter, recorded_model=settings.get("model"),
                                      recorded_effort=settings.get("effort"), ceiling_level=ceiling, level_of=level_of)
            folder = self._known_workdir(str(task["id"]), executor_id)
            found.append(_handoff.Candidate(
                executor_id=executor_id, surface=descriptor.surface,
                policy=policy.candidate_policy(executor_id, descriptor.surface), enabled=descriptor.enabled is True,
                paused=self._pause_of(conn, executor_id) is not None, used=executor_id in used, model=pick.model,
                level=pick.level, within_level=pick.within_level,
                same_folder=bool(recorded_folder) and folder is not None and _workdir_id(folder) == recorded_folder))
        return found

    def _decision_for(self, task: Mapping[str, Any], hold: Mapping[str, Any], now: datetime,
                      conn: Any = None) -> tuple[_handoff.Decision, list[_handoff.Candidate]]:
        """What the policy does with one held task now, and the candidates it looked at (``task`` is a ``get_task`` view).

        With ``conn`` (the connection of a transaction in course) everything it reads from the task store comes through it.
        """
        continuity = task.get("continuity") or {}
        policy = self._policy_of(continuity)
        if conn is not None:
            candidates = self._handoff_candidates(conn, task, hold, policy)
            session_open = self._held_session_open(conn, hold) is not None
        else:
            with self._store.db.connect() as own:
                candidates = self._handoff_candidates(own, task, hold, policy)
                session_open = self._held_session_open(own, hold) is not None
        decision = _handoff.decide(
            policy, hold, handoffs=int(continuity.get("handoffs") or 0), now=now, candidates=candidates,
            has_patch=bool(hold.get("checkpoint")), private_project=self._private_project(task, conn),
            budget_ok=self._budget_state() == "ok", session_open=session_open)
        return decision, candidates

    def _held_session_open(self, conn: Any, hold: Mapping[str, Any]) -> datetime | None:
        """Until when the held session counts as open in an app (the 12 hour rule), else ``None``."""
        sid = str(hold["session_id"]).lower() if hold.get("session_id") and is_uuid(str(hold["session_id"])) else ""
        return self._opened_until(conn, sid) if sid else None

    def _require_session_free(self, conn: Any, task_id: str, hold: Mapping[str, Any], *, automatic: bool) -> None:
        """A session opened in an app in the last 12 hours is not retired and replaced (as ``resume_due`` does not): a person
        may still be working in it and the delegate would write in the same folder at the same time.

        The automatic hand-off always stops (``session_open_in_app``; the policy asks, see ``handoff.decide``). A person's
        goes ahead only when the card says explicitly that they accept replacing the open session
        (``hold.replace_open_session`` true); the card of CNT-T06 has no such confirmation yet, so for now it stops too.
        """
        opened = self._held_session_open(conn, hold)
        if opened is None or (not automatic and hold.get(_handoff.REPLACE_OPEN_SESSION) is True):
            return
        raise _conflict("session_open_in_app", "The held session was opened in an app in the last 12 hours: it is not "
                        "retired and replaced; try again after blocked_until (" + _iso(opened) + ")",
                        task_id=task_id, session_id=hold.get("session_id"), blocked_until=_iso(opened))

    def _recorded_decision(self, conn: Any, task_id: str, candidate: str | None = None) -> dict[str, Any] | None:
        """The latest ``continuity.decided`` event of the task that chose to delegate (to ``candidate`` when given) and
        that no hand-off has carried out yet: one already carried out is history, not a pending decision."""
        for row in conn.execute("SELECT * FROM team_events WHERE type=? AND task_id=? ORDER BY seq DESC",
                                (DECIDED_EVENT, task_id)):
            event = self._row(row)
            payload = event["payload"]
            if (payload.get("decision") == "delegate" and (candidate is None or payload.get("candidate") == candidate)
                    and self._handoff_event(conn, task_id, event["ref"]) is None):
                return event
        return None

    def _handoff_event(self, conn: Any, task_id: str, decision_id: str) -> dict[str, Any] | None:
        """The ``continuity.handoff`` event a decision already produced for this task, if any."""
        for row in conn.execute("SELECT * FROM team_events WHERE type=? AND task_id=? ORDER BY seq",
                                (HANDOFF_EVENT, task_id)):
            event = self._row(row)
            if event["payload"].get("decision_id") == decision_id:
                return event
        return None

    def _retire_in(self, conn: Any, session_id: str, reason: str, actor: str,
                   extra: Mapping[str, Any] | None = None) -> bool:
        """``session.retired`` inside the caller's transaction (what ``retire_session`` does); False when there was nothing to do."""
        sid = session_id.lower()
        stored = self._latest(conn, SESSION_EVENT, ref=sid)
        if stored is None or self._latest(conn, RETIRED_EVENT, ref=sid) is not None:
            return False
        self._store.append_event(
            conn, RETIRED_EVENT, actor=actor, group_id=stored["group_id"], task_id=stored["task_id"], ref=sid,
            cause=f"session retired: {reason}",
            payload={"reason": reason, "executor_id": stored["payload"].get("executor_id"),
                     "attempt": stored["payload"].get("attempt"), **(extra or {})})
        return True

    @service_errors
    def handoff_task(self, task_id: str, executor_id: str | None = None, *, decision_id: str | None = None,
                     expected_revision: int | None = None, actor: str = "user", run: bool = True,
                     timeout_seconds: int | None = None, recorded_only: bool = False) -> dict[str, Any]:
        """Hand a task held by a usage limit over to another executor, on a person's decision (contract section 9).

        ``executor_id`` is the delegate; without it the one a person chose on the card (CNT-T06 recorded it in the hold:
        ``decision`` ``delegate``, ``candidate``, ``blocked`` ``delegation_requested``). ``actor`` is that person: the
        framework never decides for one, so an ``executor:*``, ``system:*`` or ``policy:*`` actor is refused
        (``person_required``). The automatic hand-off of the semi-automatic policy goes through ``decide_holds``.

        ``recorded_only`` is for a caller nobody authenticated (the local HTTP API and the CLI behind it): an ``actor`` is
        only a claim there, so it carries out nothing but the delegation a person already recorded on the card (the token
        route of the GUI): ``executor_id`` must be the executor the hold records (``requested_delegation``), otherwise
        ``person_required``. Choosing any other executor (one marked ``never``, a paid API, a task of a private project)
        is not possible this way. The decision id and the actor of the events are the ones the card recorded, not the
        caller's.

        In one transaction (so nothing is half done): ``continuity.decided`` (unless the card already recorded it), the
        original session is retired (``session.retired``, reason ``handoff``), the task is unclaimed and claimed by
        ``executor:<executor_id>``, ``handoffs`` goes up, ``chain`` gets the delegate, the hold is cleared and
        ``continuity.handoff`` is recorded. The envelope of the task carries the checkpoint (the patch reference and the
        summary the checkpoint attached), so the delegate starts from it; the history of the session never travels. Then, with
        ``run``, the delegate is launched (``run_task``) with the model of its own that is at the original level or cheaper
        (a limit never climbs a level); without a model at that level the app uses its own default.

        Idempotent per ``decision_id`` (default: the id the card recorded, else a new one): repeating it returns the first
        result with ``repeated`` true and does nothing. ``expected_revision`` is the revision the person saw.

        A delegate the policy marks ``never`` is still taken when a person chooses it (the person knows; the policy is for
        what happens without asking). Hard limits, whoever asks (section 8): the task is ``paused`` by a usage limit (not in a gate, an integration or
        a commit; ``integration_in_progress`` while the host says one runs), still owned by the agent that held it, hand-offs
        are left, the delegate is enabled, not paused and neither the first executor nor in the ``chain``, no budget set
        to ``cut`` is at its limit (nor one that cannot be read), the person has not asked to cancel (``inbox_cancelled``,
        AVA-T03) and the held session was not opened in an app in the last 12 hours (``session_open_in_app``: the
        automatic hand-off always stops, a person's only goes ahead when the card confirms ``replace_open_session``).
        Errors (``invalid_state`` ``details.reason``): ``person_required``, ``not_held``,
        ``stale_revision``, ``hold_changed``, ``assignee_changed``, ``no_delegate_decision``, ``handoffs_exhausted``,
        ``executor_already_used``, ``budget_exhausted``, ``integration_in_progress``, ``decision_id_reused``,
        ``inbox_cancelled``, ``session_open_in_app``,
        ``policy_changed`` (an automatic hand-off whose policy decision no longer holds when it is made) and those of
        ``run_task`` for the delegate (``executor_paused``, ``executor_disabled``, ``effect_not_authorized``, ...). The
        conditions outside the task row (integration, cancel, open session, delegate enabled and unpaused, effects, budget
        and, for an automatic hand-off, the policy decision with the visibility of the project) are checked before the
        transaction and again inside it. A person's choice always wins over the policy: a hold where the person chose
        to wait is never handed over by the policy, and the policy never writes over a decision.
        Returns ``{task_id, from_executor, to_executor, decision_id, handoff_no, max_handoffs, by, repeated, model, level,
        effort, within_level, session_retired, same_folder, sends, task, run, run_error}``.
        """
        task_id = self._task_id(task_id)
        if executor_id is not None and (not isinstance(executor_id, str) or not _EXECUTOR_ID.fullmatch(executor_id)):
            raise _invalid("executor_id", f"Invalid executor_id: {executor_id!r}")
        if decision_id is not None and (not isinstance(decision_id, str) or not _DECISION_ID.fullmatch(decision_id)):
            raise _invalid("decision_id", "decision_id must be 8 to 64 letters, digits or dashes")
        if expected_revision is not None and (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)):
            raise _invalid("expected_revision", "expected_revision must be an integer")
        actor = _text("actor", actor, maximum=100)
        if actor.startswith(NOT_APPROVERS + ("policy:",)):
            raise _conflict("person_required", "Only a person hands a task over by hand; the policy does it through "
                            "decide_holds", task_id=task_id)
        if executor_id is None:
            executor_id = _handoff.requested_delegation(self._view_hold(self._tasks.get_task(task_id)))
            if executor_id is None:
                raise _conflict("no_delegate_decision", "Give executor_id: no person chose a delegate for this task",
                                task_id=task_id)
        return self._handoff(task_id, executor_id, by="user", actor=actor, decision_id=decision_id,
                             expected_revision=expected_revision, run=run, timeout_seconds=timeout_seconds,
                             automatic=False, recorded_only=bool(recorded_only))

    def _handoff_delegate(self, task: Mapping[str, Any], executor_id: str, *, automatic: bool) -> tuple[Any, Any]:
        """What a hand-off needs from the world outside the task row: ``(descriptor, adapter)`` of a delegate that may run
        now (enabled, not paused, with an adapter), the effects of launching it and a budget that allows it.

        ``_handoff`` asks it before the transaction (a refusal costs nothing) and again inside it, under the write lock:
        none of this is in the task row, so a task whose revision did not change can still have a delegate that was paused
        or a budget that was filled in the meantime.
        """
        descriptor, adapter, _declaration = self._executor(executor_id)       # disabled, paused, no adapter
        permission = Permission.WRITE if task["file_scope"] else Permission.READ
        self._require_launch_effects(task, executor_id, permission, "handoff")
        budget = self._budget_state()
        # A budget that cannot be read stops everybody like a ``cut`` one, a person included: it is not a green light.
        if budget in ("cut_reached", "unknown") or (automatic and budget != "ok"):
            raise _conflict("budget_exhausted", "A budget is at its limit or cannot be read: the task is not handed over",
                            task_id=task["id"], budget=budget)
        return descriptor, adapter

    def _handoff(self, task_id: str, executor_id: str, *, by: str, actor: str, decision_id: str | None,
                 expected_revision: int | None, run: bool, timeout_seconds: int | None, automatic: bool,
                 seen: Mapping[str, Any] | None = None, recorded_only: bool = False) -> dict[str, Any]:
        """The hand-off itself (see ``handoff_task``); ``automatic`` is true when the policy decided it and ``recorded_only``
        when the caller is not authenticated (only a delegation a person recorded is carried out)."""
        task = self._tasks.get_task(task_id)
        hold = self._view_hold(task)
        with self._store.db.connect() as conn:
            if decision_id is None:
                # The id a person recorded on the card is reused only for that person's own, still standing choice: the
                # policy never inherits the id of a decision that was taken back (the audit would show a person's id).
                known = (self._recorded_decision(conn, task_id, executor_id)
                         if by == "user" and _handoff.requested_delegation(hold) == executor_id else None)
                decision_id = known["ref"] if known is not None else str(uuid.uuid4())
            done = self._handoff_event(conn, task_id, decision_id)
            elsewhere = self._latest(conn, DECIDED_EVENT, ref=decision_id)
        if done is not None:                    # the same decision again: the first result, nothing more is done
            data = done["payload"]
            # The delegate may never have started (the launch failed after the hand-off stood): say so, a retry is not a start.
            unstarted = (task["state"] == "claimed" and task["assignee"] == f"executor:{done['ref']}")
            return {"task_id": task_id, "from_executor": data.get("from"), "to_executor": done["ref"],
                    "decision_id": decision_id, "handoff_no": data.get("handoff_no"),
                    "max_handoffs": data.get("max_handoffs"), "by": data.get("by"), "repeated": True,
                    "model": data.get("model"), "level": data.get("level"), "effort": data.get("effort"),
                    "within_level": data.get("within_level"), "session_retired": data.get("session_retired"),
                    "same_folder": data.get("same_folder"), "sends": data.get("sends"), "task": task, "run": None,
                    "run_error": ({"code": "invalid_state", "reason": "delegate_not_started",
                                   "message": f"The hand-off was made but {done['ref']} has not started the task "
                                   "(its launch failed or was not asked for): run it with `executors run`"}
                                  if unstarted else None)}
        if elsewhere is not None and elsewhere["task_id"] != task_id:
            raise _conflict("decision_id_reused", "That decision id was used for another task", task_id=task_id)
        if task["state"] != "paused" or hold is None or not _hold.is_limit_hold(hold) or _hold.is_condition_hold(hold):
            raise _conflict("not_held", f"Task {task_id} is not waiting for a usage limit; only a task paused by one "
                            "is handed over (never one in a gate, an integration or a commit)", task_id=task_id,
                            status=task["state"])
        if recorded_only:
            # Nobody authenticated this caller: only what a person recorded on the card (the token route) is carried out.
            if _handoff.requested_delegation(hold) != executor_id:
                raise _conflict("person_required", "Only the delegation a person recorded on the card is carried out "
                                "here: choose the executor on the card", task_id=task_id)
            with self._store.db.connect() as conn:
                known = self._recorded_decision(conn, task_id, executor_id)
            if known is None:        # a hold that says "delegate" with no event of the card behind it is not a person's word
                raise _conflict("person_required", "No person recorded this decision on the card", task_id=task_id)
            if decision_id != known["ref"]:
                raise _conflict("person_required", "The decision id is the one the card recorded", task_id=task_id)
            actor = str(known.get("actor") or actor)
        if seen is not None and not self._same_hold(hold, seen):
            raise _conflict("hold_changed", "The task or its hold changed since it was checked", task_id=task_id)
        if expected_revision is not None and task["revision"] != expected_revision:
            raise _conflict("stale_revision", "The task changed since the person saw it", task_id=task_id,
                            revision=task["revision"])
        if task["assignee"] != hold.get("actor"):
            raise _conflict("assignee_changed", "The task is no longer owned by the agent that held it", task_id=task_id,
                            assignee=task["assignee"])
        if self._integrating():
            raise self._integration_block("An integration is going on: nothing is handed over now")
        # A person who asked to cancel is not answered with a new session (AVA-T03); read again inside the transaction.
        self._services.inbox.require_no_cancel("task", task_id)
        with self._store.db.connect() as conn:
            self._require_session_free(conn, task_id, hold, automatic=automatic)
        continuity = dict(task.get("continuity") or {})
        policy = self._policy_of(continuity)
        used = int(continuity.get("handoffs") or 0)
        if used >= policy.max_handoffs:
            raise _conflict("handoffs_exhausted", f"The {policy.max_handoffs} hand-off(s) of this task are used up",
                            task_id=task_id, handoffs=used, max_handoffs=policy.max_handoffs)
        origin = str(hold.get("executor_id") or "")
        chain = [str(item) for item in continuity.get("chain") or []]
        if executor_id == origin or executor_id in chain:
            raise _conflict("executor_already_used", f"{executor_id} already worked on this task: no going back "
                            "and forth", task_id=task_id, executor_id=executor_id, chain=chain)
        descriptor, adapter = self._handoff_delegate(task, executor_id, automatic=automatic)
        settings = hold.get("settings") or {}
        level_of = self._level_of()
        pick = select_for_handoff(executor_id, descriptor, adapter, recorded_model=settings.get("model"),
                                  recorded_effort=settings.get("effort"), ceiling_level=level_of(settings.get("model")),
                                  level_of=level_of)
        if run:
            # MOD-T04: a delegate that could not receive this task is refused before the hand-off is committed.
            self._privacy_precheck(task, executor_id, descriptor, pick.model)
        folder = self._known_workdir(task_id, executor_id)
        same_folder = bool(settings.get("workdir")) and folder is not None and _workdir_id(folder) == settings["workdir"]
        sid = str(hold["session_id"]).lower() if hold.get("session_id") and is_uuid(str(hold["session_id"])) else ""
        with self._store.db.connect() as conn:
            saved = self._latest(conn, CHECKPOINT_EVENT, task_id=task_id)
        files = (saved["payload"].get("files_total") if saved else None) or 0
        sends = ("task envelope and patch" + (f" ({files} file{'' if files == 1 else 's'})" if files else "")
                 if hold.get("checkpoint") else "task envelope (no patch)")
        delegate_actor = f"executor:{executor_id}"
        group_id = str(task["group_id"])
        with self._store.transaction() as conn:
            current = self._store.get_task(task_id, conn=conn)
            now_hold = _hold.hold_of(current) if current is not None else None
            if (current is None or current["state"] != "paused" or now_hold is None
                    or not self._same_hold(now_hold, hold) or current["revision"] != task["revision"]):
                raise _conflict("hold_changed", "The task or its hold changed while it was being handed over",
                                task_id=task_id)
            # What the checks above read outside the task row can change after them: read it again under the write lock,
            # so that the task cannot change between this reading and the commit (the delegate is paused or disabled, a
            # budget fills up, the project turns private, the policy no longer picks this delegate, the person asks to
            # cancel, the session is opened in the app). The integration marker is a file outside the database: it is read
            # again here, but nothing stops one from appearing between this reading and the commit or the launch below.
            if self._integrating():
                raise self._integration_block("An integration started: nothing is handed over now")
            self._services.inbox.require_no_cancel("task", task_id)
            self._require_session_free(conn, task_id, now_hold, automatic=automatic)
            self._handoff_delegate(task, executor_id, automatic=automatic)
            if recorded_only and _handoff.requested_delegation(now_hold) != executor_id:
                raise _conflict("person_required", "The delegation a person recorded on the card changed", task_id=task_id)
            if automatic:
                decision, _unused = self._decision_for(task, hold, self.clock(), conn=conn)
                if decision.action != "delegate" or decision.candidate != executor_id:
                    raise _conflict("policy_changed", "The policy no longer hands this task over to that delegate",
                                    task_id=task_id, executor_id=executor_id, action=decision.action, why=decision.why)
            if self._latest(conn, DECIDED_EVENT, ref=decision_id) is None:
                self._store.append_event(
                    conn, DECIDED_EVENT, actor=actor, group_id=group_id, task_id=task_id,
                    cause="decision on the usage limit", ref=decision_id, payload=_fit({key: value for key, value in {
                        "decision": "delegate", "by": by, "executor_id": origin, "candidate": executor_id,
                        "sends": sends, "reason": hold.get("reason"), "resume_after": hold.get("resume_after"),
                        "reset_at": hold.get("reset_at")}.items() if value is not None}))
            retired = bool(sid) and self._retire_in(conn, sid, "handoff", actor)
            view = self._tasks._hand_over(
                conn, task_id, delegate_actor, actor=actor, cause=f"handed off from {origin} to {executor_id}",
                continuity={"hold": None, "handoffs": used + 1, "chain": [*chain, executor_id], "failed_resumes": 0,
                            "rate_retries": 0, "config_drift": None})
            payload = {key: value for key, value in {
                "from": origin, "handoff_no": used + 1, "max_handoffs": policy.max_handoffs, "decision_id": decision_id,
                "by": by, "reason": hold.get("reason"), "window": hold.get("window"), "mode": hold.get("mode"),
                "model": pick.model, "level": pick.level, "effort": pick.effort, "within_level": pick.within_level,
                "patch": hold.get("checkpoint"), "sends": sends, "session_retired": sid or None,
                "same_folder": same_folder, "attempt": hold.get("attempt")}.items() if value is not None}
            self._store.append_event(conn, HANDOFF_EVENT, actor=actor, group_id=group_id, task_id=task_id,
                                     ref=executor_id, cause=f"handed off from {origin}", payload=_fit(payload))
        result: dict[str, Any] = {
            "task_id": task_id, "from_executor": origin, "to_executor": executor_id, "decision_id": decision_id,
            "handoff_no": used + 1, "max_handoffs": policy.max_handoffs, "by": by, "repeated": False,
            "model": pick.model, "level": pick.level, "effort": pick.effort, "within_level": pick.within_level,
            "session_retired": sid if retired else None, "same_folder": same_folder, "sends": sends, "task": view,
            "run": None, "run_error": None}
        if run:
            try:
                result["run"] = self._run_task(task_id, executor_id, pick.model, pick.effort, delegate_actor,
                                               timeout_seconds=timeout_seconds, source=SOURCE_HANDOFF)
                result["task"] = self._tasks.get_task(task_id)
            except Exception as exc:          # noqa: BLE001 - the hand-off stands: the delegate owns the task, a person can run it
                error = exc if isinstance(exc, ServiceError) else translate(exc)
                if error is None:       # an unexpected error: the hand-off stands and its report must not be lost with it
                    _logger.exception("the delegate %s could not be launched for %s", executor_id, task_id)
                    result["run_error"] = {"code": "internal_error", "reason": type(exc).__name__,
                                           "message": "The delegate could not be launched; the cause is in the log"}
                else:
                    result["run_error"] = {"code": error.code, "reason": error.details.get("reason"),
                                           "message": error.message}
        return result

    @service_errors
    def decide_holds(self, *, task_id: str | None = None, limit: int | None = None, dry_run: bool = False,
                     run: bool = True, timeout_seconds: int | None = None) -> dict[str, Any]:
        """Apply the continuity policy to the tasks held by a usage limit (contract sections 7 to 9); the orchestrator or the
        CLI calls it next to ``resume_due``.

        For each task paused by a limit (a hold with a condition belongs to ``resume_met``):

        - a person's recorded decision to delegate (``requested_delegation``) is carried out, in any mode: the person
          decided, and the hard limits of ``handoff_task`` still apply;
        - a person's decision to wait leaves the task to ``resume_due``;
        - otherwise ``handoff.decide`` answers from the mode, the reset time, the threshold, the hand-offs left and the
          candidates. ``wait`` is recorded once (``hold.decision`` ``wait``, ``continuity.decided`` with ``by``
          ``policy:<mode>``) and ``resume_due`` resumes the task at the reset; ``delegate`` hands the task over (``by``
          ``policy:<mode>``; the executor chosen is the enabled, unpaused, unused ``no_ask`` subscription candidate whose
          model is the closest to the original level without passing it); ``ask`` writes nothing: the person decides on the
          card, and the entry says what the best proposal is and what stopped an automatic hand-off.

        Nothing is decided while an integration is going on (``integration``, with ``integration_message`` saying what
        blocks and how it ends). Each task is read again when its turn comes (a hand-off can take minutes): what a person
        decided meanwhile counts, and a task that changed is ``skipped`` with ``hold_changed``. ``task_id`` limits the call
        to one task, ``limit`` to that many hand-offs, ``dry_run`` only reports, ``run`` false hands over without launching
        the delegate. A failure of one task is reported in ``skipped`` and the others go on.
        Returns ``{at, dry_run, integration, held, waiting, asked, delegated, skipped}``; each entry has ``task_id``,
        ``reason``, ``executor_id``, ``why`` and, for ``asked``, ``candidate`` and ``ask``; for ``delegated``, ``to_executor``,
        ``by`` and ``executed`` (the hand-off result when executed).
        """
        if task_id is not None:
            task_id = self._task_id(task_id)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise _invalid("limit", "limit must be an integer of one or more")
        now = self.clock()
        out: dict[str, Any] = {"at": _iso(now), "dry_run": bool(dry_run), "integration": False, "held": 0,
                               "waiting": [], "asked": [], "delegated": [], "skipped": []}
        rows = [(row, hold) for row in self._store.list_tasks(states=["paused"])
                if (hold := _hold.hold_of(row)) is not None and _hold.is_limit_hold(hold)
                and not _hold.is_condition_hold(hold) and (task_id is None or row["id"] == task_id)]
        out["held"] = len(rows)
        if rows and self._integrating():
            out["integration"] = True
            out["integration_message"] = self._integration_block("An integration is going on: nothing is decided now").message
            out["skipped"] = [{"task_id": row["id"], "why": "integration_in_progress"} for row, _unused in rows]
            return out
        done = 0
        for row, listed in rows:
            info = {"task_id": row["id"], "reason": listed.get("reason"), "executor_id": listed.get("executor_id"),
                    "resume_after": listed.get("resume_after")}
            try:
                # The list was read before the loop and every hand-off waits for its delegate, perhaps for minutes: what a
                # person decided in the meantime is what counts, so the task and its hold are read again here (the person
                # always wins over the policy).
                view = self._tasks.get_task(str(row["id"]))
                hold = self._view_hold(view)
                if (view["state"] != "paused" or hold is None or not _hold.is_limit_hold(hold)
                        or _hold.is_condition_hold(hold) or not self._same_hold(hold, listed)):
                    out["skipped"].append({**info, "why": "hold_changed"})
                    continue
                if view["assignee"] != hold.get("actor"):
                    out["skipped"].append({**info, "why": "assignee_changed", "assignee": view["assignee"]})
                    continue
                requested = _handoff.requested_delegation(hold)
                if requested is None and hold.get("decision") == "wait":
                    out["waiting"].append({**info, "why": f"decided:wait:{hold.get('decided_by') or 'user'}"})
                    continue
                if requested is None:
                    decision, _candidates = self._decision_for(view, hold, self.clock())
                    if decision.action == "none":
                        out["skipped"].append({**info, "why": decision.why})
                        continue
                    if decision.action == "wait":
                        out["waiting"].append({**info, "why": decision.why, "by": decision.by})
                        if not dry_run:
                            self._record_wait(view, hold, decision)
                        continue
                    if decision.action == "ask":
                        out["asked"].append({**info, "why": decision.why, "candidate": decision.candidate,
                                             "ask": list(decision.ask)})
                        continue
                    target, by, why = str(decision.candidate), decision.by, decision.why
                else:
                    target, by, why = requested, "user", "person_decided"
                if limit is not None and done >= limit:
                    out["waiting"].append({**info, "why": "limit"})
                    continue
                entry = {**info, "to_executor": target, "by": by, "why": why, "executed": False}
                if dry_run:
                    out["delegated"].append(entry)
                    done += 1
                    continue
                try:
                    result = self._handoff(
                        str(row["id"]), target, by=by, actor=by if by != "user" else str(self._decided_actor(row) or "user"),
                        decision_id=None, expected_revision=None, run=run, timeout_seconds=timeout_seconds,
                        automatic=by != "user", seen=hold)
                except Exception as exc:  # noqa: BLE001 - one task that fails must not hide what the others already did
                    error = exc if isinstance(exc, ServiceError) else translate(exc)
                    if error is None:
                        _logger.exception("the hand-off of %s failed", row["id"])
                        out["skipped"].append({**entry, "why": "internal_error", "message": type(exc).__name__})
                    else:
                        out["skipped"].append({**entry, "why": str(error.details.get("reason") or error.code),
                                               "message": error.message})
                    continue
                out["delegated"].append({**entry, "executed": True, "decision_id": result["decision_id"],
                                         "handoff_no": result["handoff_no"], "model": result["model"],
                                         "run_outcome": (result["run"] or {}).get("outcome"),
                                         "run_error": result["run_error"]})
                done += 1
            except Exception as exc:  # noqa: BLE001 - one task's error must not abort the sweep after others moved
                error = exc if isinstance(exc, ServiceError) else translate(exc)
                if error is None:
                    _logger.exception("deciding the hold of %s failed", row["id"])
                    out["skipped"].append({**info, "why": "internal_error", "message": type(exc).__name__})
                else:
                    out["skipped"].append({**info, "why": str(error.details.get("reason") or error.code),
                                           "message": error.message})
        return out

    def _decided_actor(self, row: Mapping[str, Any]) -> str | None:
        """Who recorded the person's decision (the actor of the latest ``continuity.decided`` that delegates), else None."""
        with self._store.db.connect() as conn:
            event = self._recorded_decision(conn, str(row["id"]))
        return str(event["actor"]) if event is not None and event.get("actor") else None

    def _record_wait(self, task: Mapping[str, Any], seen: Mapping[str, Any], decision: _handoff.Decision) -> None:
        """Record once that the policy chose to wait: ``hold.decision`` and ``continuity.decided`` (a notice, not a question)."""
        now, task_id = self.clock(), str(task["id"])
        with self._store.transaction() as conn:          # the hold and the event, or neither
            current = self._store.get_task(task_id, conn=conn)
            hold = _hold.hold_of(current) if current is not None else None
            # Only a hold still waiting for a decision is written: a person who chose in the meantime (to wait, or to
            # delegate) or an earlier wait stays as it is. The policy never takes a person's choice back.
            if (current is None or current["state"] != "paused" or hold is None or not self._same_hold(hold, seen)
                    or hold.get("decision") not in (None, "pending")):
                return
            new_hold = {**hold, "decision": "wait", "decided_by": decision.by, "decided_at": utc_iso(now)}
            new_hold.pop("candidate", None)
            self._store.update_task(conn, task_id, expected_revision=current["revision"],
                                    execution=_hold.merge_continuity(current.get("execution"), {"hold": new_hold}))
            self._store.append_event(
                conn, DECIDED_EVENT, actor=decision.by, group_id=str(current["group_id"]), task_id=task_id,
                ref=str(uuid.uuid4()), cause="decision on the usage limit",
                payload=_fit({key: value for key, value in {
                    "decision": "wait", "by": decision.by, "why": decision.why, "executor_id": new_hold.get("executor_id"),
                    "reason": new_hold.get("reason"), "resume_after": new_hold.get("resume_after"),
                    "reset_at": new_hold.get("reset_at")}.items() if value is not None}))

    # -------- opening and status --------
    @service_errors
    def open_target(self, task_id: str, actor: str | None = None) -> dict[str, Any]:
        """The adapter's open target for the task's recorded session, and the record of that open.

        The target is the one the GUI uses (``claude --desktop --resume <id>``, ``codex://threads/<id>``,
        ``ghapp://sessions/<id>``). Handing it out is treated as an open: ``session.opened_in_app`` is recorded and
        ``resume_task`` refuses the session for 12 hours (``resume_blocked_until``). Errors: ``no_stored_session``.
        """
        task_id = self._task_id(task_id)
        actor = _text("actor", actor if actor is not None else "user", maximum=100)
        self._tasks.get_task(task_id)
        session = self._stored_session(task_id)
        if session is None:
            raise _conflict("no_stored_session", "No session of this task was recorded by the framework", task_id=task_id)
        executor_id = str(session.get("executor_id", ""))
        adapter = self.registry.resolve(executor_id)
        if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
            raise _conflict("executor_without_adapter", f"{executor_id} has no executor adapter registered",
                            executor_id=executor_id)
        target = adapter.open_target(str(session["session_id"]))
        if target is None:
            raise _conflict("invalid_open_target", "The recorded session has no valid open target",
                            task_id=task_id, session_id=session["session_id"])
        source = str(session.get("adapter_id") or executor_id).removesuffix("_cli")
        # BDI-T04: handing out the target is an open of the application (see above); it needs an approval too.
        self._require_effect(Action(ActionKind.OPEN_APPLICATION, f"{source}:{session['session_id']}"),
                             {"task_id": task_id, "executor_id": executor_id, "actor": actor, "mode": "open"})
        opened = self.record_app_open(str(session["session_id"]), source=source, actor=actor)
        return {"task_id": task_id, "executor_id": executor_id, "session_id": session["session_id"],
                "target": _target_view(target), "resume_blocked_until": opened["resume_blocked_until"]}

    @service_errors
    def record_app_open(self, session_id: str, *, source: str, actor: str = "gui") -> dict[str, Any]:
        """Record that a session was opened in an app: ``resume_task`` refuses it for the next 12 hours.

        Called by the GUI when it launches Claude (GUI-T06) and by ``open_target``. The record goes in the team event
        log (``session.opened_in_app``) with the task of the session when the framework created it.
        """
        if not is_uuid(session_id):
            raise _invalid("session_id", "session_id must be a UUID")
        if not isinstance(source, str) or not _SOURCE.fullmatch(source):
            raise _invalid("source", "source must be a short lowercase name such as claude, codex or copilot")
        actor = _text("actor", actor, maximum=100)
        sid, now = session_id.lower(), self.clock()
        with self._store.transaction() as conn:
            stored = self._latest(conn, SESSION_EVENT, ref=sid)
            self._store.append_event(conn, OPEN_EVENT, actor=actor, group_id=stored["group_id"] if stored else "",
                                     task_id=stored["task_id"] if stored else "", ref=sid, cause="opened in the app",
                                     payload={"source": source, "opened_at": _iso(now)})
        return {"session_id": sid, "source": source, "resume_blocked_until": _iso(now + APP_OPEN_GUARD)}

    @service_errors
    def status(self, task_id: str | None = None) -> dict[str, Any]:
        """The executors that have an adapter (enabled, paused until) and, with ``task_id``, that task's sessions and attempts."""
        registry = self.registry
        executors: list[dict[str, Any]] = []
        with self._store.db.connect() as conn:
            for executor_id, descriptor in sorted(registry.snapshot().items()):
                adapter = registry.resolve(executor_id)
                if adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
                    continue
                pause = self._pause_of(conn, executor_id)
                until = pause["until"] if pause else None
                executors.append({"executor_id": executor_id, "surface": descriptor.surface,
                                  "adapter_id": adapter.adapter_id, "mode": descriptor.mode,
                                  "enabled": descriptor.enabled, "paused_until": _iso(until) if until else None,
                                  "paused_open_ended": bool(pause and pause["open_ended"]),
                                  "pause_reason": pause["payload"].get("limit_kind") if pause else None,
                                  "available": descriptor.enabled is True and pause is None})
        result: dict[str, Any] = {"executors": executors}
        if task_id is not None:
            task_id = self._task_id(task_id)
            task = self._tasks.get_task(task_id)
            sessions: list[dict[str, Any]] = []
            attempts: list[dict[str, Any]] = []
            routings: list[dict[str, Any]] = []
            with self._store.db.connect() as conn:
                for row in conn.execute("SELECT * FROM team_events WHERE task_id=? AND type IN (?,?,?) ORDER BY seq",
                                        (task_id, SESSION_EVENT, ATTEMPT_EVENT, ROUTING_EVENT)):
                    event = self._row(row)
                    if event["type"] == SESSION_EVENT:
                        sessions.append({"session_id": event["ref"], "created_at": event["created_at"], **event["payload"]})
                    elif event["type"] == ROUTING_EVENT:
                        routings.append({"seq": event["seq"], "created_at": event["created_at"], **event["payload"]})
                    else:
                        attempts.append({"seq": event["seq"], "created_at": event["created_at"], **event["payload"]})
                for row in conn.execute("SELECT * FROM team_events WHERE task_id=? AND type=? ORDER BY seq",
                                        (task_id, RETIRED_EVENT)):
                    event = self._row(row)
                    for item in sessions:
                        if item["session_id"] == event["ref"]:
                            item["retired"] = {"reason": event["payload"].get("reason"), "at": event["created_at"]}
                blocked = None
                if sessions:
                    until = self._opened_until(conn, sessions[-1]["session_id"])
                    blocked = _iso(until) if until else None
            continuity = task.get("continuity") or {}
            result["task"] = {"task_id": task_id, "state": task["state"], "status": task["status"],
                              "attempt": task["attempt"], "assignee": task["assignee"], "sessions": sessions,
                              "attempts": attempts, "routings": routings, "resume_blocked_until": blocked,
                              "hold": continuity.get("hold"), "continuity": continuity or None}
        return result

    @service_errors
    def _composer_options(self) -> dict[str, Any]:
        """Only executors the user enabled and whose model and effort choices are evidenced."""
        registry = self.registry
        status_by_id = {row["executor_id"]: row for row in self.status()["executors"]}
        options = []
        for executor_id, descriptor in sorted(registry.snapshot().items()):
            adapter = registry.resolve(executor_id)
            if descriptor.enabled is not True or adapter is None or not isinstance(adapter, ExecutorApplicationAdapter):
                continue
            models = descriptor.model_support
            efforts = descriptor.effort_support
            options.append({
                "executor_id": executor_id,
                "surface": descriptor.surface,
                "mode": descriptor.mode,
                "available": bool(status_by_id.get(executor_id, {}).get("available"))
                    and models.status is SupportState.SUPPORTED and bool(models.values)
                    and (efforts.status is not SupportState.SUPPORTED or bool(efforts.values)),
                "models": sorted(models.values or ()) if models.status is SupportState.SUPPORTED else [],
                "efforts": sorted(efforts.values or ()) if efforts.status is SupportState.SUPPORTED else [],
            })
        return {"executors": options}
