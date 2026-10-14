# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure authorization of effects (BDI-T03, contract BDI-T01 sections 1 and 8).

An effect (write, network, launching an executor, opening an application) is allowed only when the evidence
holds an :class:`Approval` whose provenance is the user or a task contract the user approved. Everything else
is data: a document, a context summary, a model output, an executor result, a preset's own kind, or anything
derived from them. Trust comes from :attr:`multiagent.document_trust.Provenance.trust`, which is computed and
only goes down along ``derived_from``.

The rules, all fail-closed:

* no evidence, or an empty collection, denies;
* evidence that is malformed or of an unknown type denies the whole request (nobody guesses what it meant);
* the result of the detector (:class:`Detection`, :class:`Signal`, :class:`Screening`) is not evidence, for or
  against: it is skipped, so an approval next to it decides alone, and it never authorizes by itself;
* an approval counts only when its provenance is a real :class:`Provenance` (no subclass or look-alike), is
  trusted, is of kind ``user`` or ``approved_contract`` and carries a ``ref`` that identifies the message or
  contract (the proof);
* an approval covers the action kinds it grants and, when it lists ``targets``, only those targets.

The module is pure: no I/O, no models, no clock. Hosts (BDI-T04) build the approvals from the user's messages
and the approved contracts; they never build them from content.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .document_trust import TRUSTED_KINDS, Detection, Provenance, QuarantinedFragment, Screening, Signal, TrustLevel


class ActionKind(str, Enum):
    """The neutral kinds of effect that need authorization."""

    WRITE = "write"
    NETWORK = "network"
    LAUNCH_EXECUTOR = "launch_executor"
    OPEN_APPLICATION = "open_application"


@dataclass(frozen=True)
class Action:
    """An effect to authorize. ``target`` names what it touches (a path, host, executor...) when known."""

    kind: ActionKind
    target: str | None = None

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value if isinstance(self.kind, ActionKind) else None
        return {"kind": kind, "target": self.target if isinstance(self.target, str) else None}


@dataclass(frozen=True)
class Approval:
    """Proof that the user or an approved contract allows some effects.

    ``provenance`` says who approved (its ``ref`` is the message or contract), ``grants`` the action kinds
    and ``targets`` the only targets covered (``None`` means any target of those kinds).
    """

    provenance: Provenance
    grants: frozenset[ActionKind]
    targets: frozenset[str] | None = None


@dataclass(frozen=True)
class AuthorizationResult:
    """The decision, with a readable reason and the provenance it rests on (``None`` when denied)."""

    allowed: bool
    reason: str
    action: Action
    basis: Provenance | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "action": self.action.to_dict(),
                "basis": self.basis.to_dict() if self.basis is not None else None}


# Detector outputs are not evidence in either direction (contract section 8).
_DETECTOR_TYPES = (Detection, Signal, Screening, QuarantinedFragment)


class _Malformed(Exception):
    pass


def _deny(action: Any, reason: str) -> AuthorizationResult:
    return AuthorizationResult(False, reason, action if isinstance(action, Action) else Action(ActionKind.WRITE))


def _check_approval(item: Approval) -> None:
    """Raise :class:`_Malformed` unless ``item`` is structurally sound."""
    provenance = item.provenance
    if type(provenance) is not Provenance:
        raise _Malformed("approval provenance is not a Provenance")
    grants = item.grants
    if not isinstance(grants, frozenset) or not grants or not all(type(kind) is ActionKind for kind in grants):
        raise _Malformed("approval grants must be a non-empty frozenset of ActionKind")
    targets = item.targets
    if targets is not None and (not isinstance(targets, frozenset)
                                or not all(isinstance(target, str) and target for target in targets)):
        raise _Malformed("approval targets must be None or a frozenset of non-empty strings")


def _trust_problem(provenance: Provenance) -> str | None:
    """Why ``provenance`` cannot approve, or ``None``. Checks the chain itself, not only ``trust``."""
    if provenance.trust is not TrustLevel.TRUSTED or provenance.kind not in TRUSTED_KINDS:
        return f"source {provenance.kind!r} is not trusted: only the user or an approved contract can authorize"
    pending = list(provenance.derived_from)
    while pending:
        parent = pending.pop()
        if type(parent) is not Provenance or parent.kind not in TRUSTED_KINDS:
            return "approval derives from content that is not trusted"
        pending.extend(parent.derived_from)
    if not provenance.ref:
        return "approval has no reference to the user message or contract that proves it"
    return None


def authorize(action: Action, evidence: Iterable[Any] | None) -> AuthorizationResult:
    """Decide whether ``action`` is allowed by ``evidence`` (an iterable of :class:`Approval`).

    Pure and deterministic. Anything that is not clearly an approval of the user or of an approved contract
    denies; detector results are skipped.
    """
    if not isinstance(action, Action) or type(action.kind) is not ActionKind or (
            action.target is not None and (not isinstance(action.target, str) or not action.target)):
        return _deny(action, "denied: the action is malformed")
    if evidence is None:
        return _deny(action, "denied: no evidence")
    if isinstance(evidence, (str, bytes, dict, Approval)) or not isinstance(evidence, Iterable):
        return _deny(action, "denied: evidence is malformed (not a collection of approvals)")
    items = list(evidence)
    approvals: list[Approval] = []
    for item in items:
        if isinstance(item, _DETECTOR_TYPES):
            continue
        if type(item) is not Approval:
            return _deny(action, f"denied: evidence is malformed (unknown type {type(item).__name__})")
        try:
            _check_approval(item)
        except _Malformed as error:
            return _deny(action, f"denied: evidence is malformed ({error})")
        approvals.append(item)
    if not approvals:
        return _deny(action, "denied: no evidence of an approval by the user or an approved contract")
    problem = "no approval covers this action"
    for approval in approvals:
        issue = _trust_problem(approval.provenance)
        if issue is not None:
            problem = issue
            continue
        if action.kind not in approval.grants:
            continue
        if approval.targets is not None and action.target not in approval.targets:
            problem = f"the approval does not cover target {action.target!r}"
            continue
        return AuthorizationResult(
            True, f"allowed: {approval.provenance.kind} approval {approval.provenance.ref} covers "
                  f"{action.kind.value}", action, approval.provenance)
    return _deny(action, f"denied: {problem}")
