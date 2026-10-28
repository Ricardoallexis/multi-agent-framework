# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The content-review guard and the events that settle it (BDI-T04, contract BDI-T01 sections 6 to 8).

With the policy ``pause`` the engine (BDI-T02) screens the untrusted parts of a step's context and, when the
detector finds something, records one ``document_review_requested`` event per part *before* the step starts.
This module is the other half: before the first model call of that step the :class:`UntrustedContentGuard`
reads those events and, while one is still open, answers ``Pause(WaitingReason.CONTENT_REVIEW)``. The engine
turns the verdict into ``WAITING_HUMAN`` the way it does for every call guard (``call_paused``).

A review is settled through the existing approval path of runs (``RunService.approve`` and ``reject``; the
transaction is ``Store.finish_content_review``). Settling records ``document_review_resolved`` with the same four
keys as the request (:data:`KEY_FIELDS`), so the key identifies the pair and a resumed run does not ask again, and
with ``by`` and ``channel``, which say who the caller declared itself to be and through which door it came.
Nothing in the framework settles a review by itself: no model, detector or guard does.

KNOWN LIMIT (to be closed in BDI-T07): that approval path is the local run API, which does not authenticate who
calls it. Any local process that can reach it can approve (or reject) a review, exactly as it can approve any other
human checkpoint. ``by`` and ``channel`` are what the caller *declared* (``api`` unless the GUI router says ``gui``);
they are an audit trail, not a proof of identity, and nothing here decides who may approve.

Rules, all fail-closed:

* the guard pauses only with the policy ``pause`` and only for the step attempt of the call; a ``None`` policy
  is "barrier off" (``build_call_guards`` does not even register the guard) and every other policy never pauses;
* a request is open until a *later* ``document_review_resolved`` with the same key and ``decision`` ``approved``
  settles it; a ``rejected`` resolution, or one that comes before the request, leaves it open;
* a request that names the step but has no usable key cannot be settled by key: it blocks the step (a person may
  reject or cancel the run, but not approve past it);
* with the policy ``pause`` a state without its ``events`` makes the guard raise, so the call does not happen (the
  guard is not an observer: if it cannot decide, the call does not happen).

Approving does not release the quarantined text: the fragment stays replaced by its quarantine mark and its
original stays in the blob store. It only lets the step go on with the prompt as it was built.

Pure: it reads the events it is given and writes nothing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .call_guards import ORDER_CONTENT, PROCEED, CallContext, Pause, RunState, Verdict
from .contracts import WaitingReason
from .document_trust import EVENT_REVIEW_REQUESTED, EVENT_REVIEW_RESOLVED, DetectionPolicy

__all__ = ["CHANNELS", "DECISION_APPROVED", "DECISION_REJECTED", "KEY_FIELDS", "REVIEW_SCHEMA",
           "UntrustedContentGuard", "request_summary", "resolution_payload", "resolution_source", "review_key",
           "unresolved_requests"]

REVIEW_SCHEMA = "multiagent.document_review/1"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
# What identifies a review: the same four keys in the request and in its resolution (BDI-T02, ``_record_screening``).
KEY_FIELDS = ("step_id", "attempt", "part", "original_sha256")
CHANNEL_API = "api"
CHANNEL_GUI = "gui"
CHANNELS = (CHANNEL_API, CHANNEL_GUI)       # the doors a decision can come through; anything else is "api"
MAX_BY_CHARS = 100
MAX_EXCERPT_CHARS = 160                     # as in the screening event
MAX_EXCERPTS = 10                           # per request, in ``Pause.request``

ReviewKey = tuple[str, int, str, str]


def review_key(payload: Mapping[str, Any]) -> ReviewKey | None:
    """``(step_id, attempt, part, original_sha256)`` of an event payload, or ``None`` when one is missing or odd."""
    if not isinstance(payload, Mapping):
        return None
    step_id, attempt, part, digest = (payload.get(name) for name in KEY_FIELDS)
    if (not isinstance(step_id, str) or not step_id or type(attempt) is not int or attempt < 1
            or not isinstance(part, str) or not part or not isinstance(digest, str) or not digest):
        return None
    return step_id, attempt, part, digest


def _names_step(payload: Mapping[str, Any], step_id: str | None, attempt: int | None) -> bool:
    """True when a request without a usable key still says it is for ``step_id`` (and not for another attempt)."""
    if step_id is None or payload.get("step_id") != step_id:
        return False
    seen = payload.get("attempt")
    return attempt is None or type(seen) is not int or seen < 1 or seen == attempt


def unresolved_requests(events: Iterable[Mapping[str, Any]], *, step_id: str | None = None,
                        attempt: int | None = None, include_unkeyed: bool = False) -> list[Mapping[str, Any]]:
    """The payloads of the ``document_review_requested`` events that no later approval has settled, oldest first.

    ``events`` are the run's events in order (``{"event_type", "payload"}``). ``step_id`` and ``attempt`` narrow
    the answer to one step attempt. A payload without a usable key is skipped, unless ``include_unkeyed`` is true and
    it names ``step_id`` (and not another attempt): then it is listed too, because nothing can settle it by key.
    """
    open_requests: dict[Any, Mapping[str, Any]] = {}
    for position, event in enumerate(events):
        if not isinstance(event, Mapping):
            continue
        kind, payload = event.get("event_type"), event.get("payload")
        if kind not in (EVENT_REVIEW_REQUESTED, EVENT_REVIEW_RESOLVED) or not isinstance(payload, Mapping):
            continue
        key = review_key(payload)
        if key is None:
            if include_unkeyed and kind == EVENT_REVIEW_REQUESTED and _names_step(payload, step_id, attempt):
                open_requests[(None, position)] = payload
            continue
        if kind == EVENT_REVIEW_REQUESTED:
            open_requests.setdefault(key, payload)
        elif payload.get("decision") == DECISION_APPROVED:
            open_requests.pop(key, None)        # only a resolution that comes after the request settles it
    return [payload for key, payload in open_requests.items()
            if key[0] is None or ((step_id is None or key[0] == step_id) and (attempt is None or key[1] == attempt))]


def resolution_source(by: Any = None, channel: Any = None) -> tuple[str, str]:
    """``(by, channel)`` as recorded: who the caller declared and the door (``api`` unless it is a known one).

    ``by`` is free text the caller declared (nothing authenticates it); without a usable one it is the channel.
    """
    door = channel if channel in CHANNELS else CHANNEL_API
    declared = by.strip() if isinstance(by, str) else ""
    if not declared or len(declared) > MAX_BY_CHARS or not declared.isprintable():
        declared = door
    return declared, door


def resolution_payload(request: Mapping[str, Any], decision: str, *, by: Any = None,
                       channel: Any = None) -> dict[str, Any]:
    """The payload of ``document_review_resolved`` for one request: its four keys, the decision and who/where from."""
    if decision not in (DECISION_APPROVED, DECISION_REJECTED):
        raise ValueError(f"decision must be {DECISION_APPROVED!r} or {DECISION_REJECTED!r}, got {decision!r}")
    key = review_key(request)
    if key is None:
        raise ValueError("the request has no step_id, attempt, part and original_sha256 to be settled by")
    declared, door = resolution_source(by, channel)
    return {"schema": REVIEW_SCHEMA, "decision": decision, **dict(zip(KEY_FIELDS, key)), "by": declared,
            "channel": door}


def request_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The keys of one open request and the excerpts of its fragments (each at most 160 characters)."""
    excerpts: list[str] = []
    fragments = payload.get("fragments")
    for item in fragments if isinstance(fragments, list) else ():
        if isinstance(item, Mapping) and isinstance(item.get("excerpt"), str) and len(excerpts) < MAX_EXCERPTS:
            excerpts.append(item["excerpt"][:MAX_EXCERPT_CHARS])
    return {**{name: payload.get(name) for name in KEY_FIELDS}, "max_severity": payload.get("max_severity"),
            "keyed": review_key(payload) is not None, "excerpts": excerpts}


class UntrustedContentGuard:
    """Holds the model call of a step while a content review of that step attempt is open (policy ``pause``)."""

    name = "untrusted_content"
    order = ORDER_CONTENT
    observer = False            # fails closed: if it cannot decide, the call does not happen
    skippable = False

    def __init__(self, policy: str | DetectionPolicy | None) -> None:
        # ``None`` is "no barrier"; a value that is not a policy raises ``ValueError`` (nobody guesses).
        self.policy: DetectionPolicy | None = None if policy is None else DetectionPolicy(policy)

    def check(self, call: CallContext, state: RunState) -> Verdict:
        if self.policy is not DetectionPolicy.PAUSE or not call.step_id:
            return PROCEED
        # ``state["events"]``, not ``.get``: a state without its events is one this guard cannot read, and then the
        # call must not happen (the chain lets the exception through because the guard is not an observer).
        pending = unresolved_requests(state["events"], step_id=call.step_id, attempt=call.attempt,
                                      include_unkeyed=True)
        if not pending:
            return PROCEED
        # One decision settles every open request of the step attempt, so the person is shown all of them.
        request = {**dict(pending[0]), "open_requests": [request_summary(item) for item in pending]}
        return Pause(
            WaitingReason.CONTENT_REVIEW, request=request,
            message=(f"The context of step {call.step_id!r} (attempt {call.attempt}) has content from untrusted "
                     f"sources that a person must review before the model reads it ({len(pending)} open)"))
