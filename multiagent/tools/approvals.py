"""One-call tool decisions issued only by the authenticated run approval path."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from multiagent.call_guards import Pause
from multiagent.contracts import WaitingReason
from multiagent.document_actions import Action, Approval, AuthorizationResult, authorize
from multiagent.document_trust import Provenance, SourceKind
from multiagent.tasks.envelopes import envelope_sha256

from .contracts import ImpactClass, PermissionRequest, ToolRequest, ToolSpec, spec_sha256

from multiagent.errors import InvalidStateTransition

_ISSUER = object()
EVENT_REQUESTED = "tool_approval_requested"
EVENT_APPROVED = "tool_approval_approved"
EVENT_REJECTED = "tool_approval_rejected"
EVENT_RELEASED = "tool_approval_released"


def operating_mode_decision(spec: ToolSpec, mode: str | None) -> str | None:
    """The one mode-specific decision for a tool call; other permission checks remain in force."""
    if mode != "autonomous":
        return None
    if ImpactClass.EXTERNAL in spec.impacts:
        return "require_approval"
    if ImpactClass.WRITE in spec.impacts and spec.impacts <= {ImpactClass.READ, ImpactClass.WRITE}:
        return "allow_write"
    return None


class ToolApprovalMismatch(InvalidStateTransition):
    """The approval did not name the pending tool request (``code`` is the stable reason)."""

    def __init__(self, message: str, *, code: str = "permission_request_mismatch"):
        super().__init__(message)
        self.code = code


APPROVAL_TTL = timedelta(minutes=10)


def _now() -> datetime:
    """The clock of tool approvals (injectable in tests, like the engine's ``_now``)."""
    return datetime.now(timezone.utc)


class _Spend:
    """The one-use mark of a grant: shared by every copy of it and flipped atomically."""
    __slots__ = ("lock", "used")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.used = False

    def take(self) -> bool:
        with self.lock:
            if self.used:
                return False
            self.used = True
            return True


class ToolCallApproval:
    """One person's approval of one call. Immutable and not a dataclass: ``dataclasses.replace`` and attribute
    assignment cannot reset its use mark. It exposes no ``Approval``: ``proof`` is built by ``check_call`` after
    ``valid_for`` succeeded and is never handed out (CAP-T15 B3)."""
    __slots__ = ("_permission", "_expires_at", "_approved_at", "_issuer", "_spend")

    def __init__(self, permission: PermissionRequest, expires_at: datetime, issuer: object,
                 approved_at: datetime):
        for name, value in (("_permission", permission), ("_expires_at", expires_at), ("_approved_at", approved_at),
                            ("_issuer", issuer), ("_spend", _Spend())):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError("a tool approval is immutable")

    def __delattr__(self, name):
        raise AttributeError("a tool approval is immutable")

    def __reduce__(self):
        raise TypeError("a tool approval cannot be copied or pickled")

    def __repr__(self) -> str:
        return "ToolCallApproval(<redacted>)"

    @property
    def permission(self) -> PermissionRequest:
        return self._permission

    def valid_for(self, spec: ToolSpec, request: ToolRequest) -> bool:
        if self._issuer is not _ISSUER:
            return False
        # Spend the grant even when the caller supplied a mismatched request (atomic across threads).
        if not self._spend.take():
            return False
        p = self._permission
        now = _now()
        expiry = self._expires_at
        if expiry.tzinfo is None or now >= expiry or expiry - now > APPROVAL_TTL:
            return False
        if now < self._approved_at:
            return False
        return (p.context.scope == "run_step" and p.context == request.context and p.origin == request.origin
                and p.request_id == request.request_id and p.tool_id == request.tool_id
                and p.tool_version == spec.version and p.spec_sha256 == spec_sha256(spec)
                and p.arguments_sha256 == envelope_sha256(request.arguments))

    def _decide(self, spec: ToolSpec, request: ToolRequest, actions: tuple[Action, ...]) -> list[AuthorizationResult] | None:
        """Validate, spend and authorize in one step (``check_call`` only). ``None`` when the grant does not fit this call.
        The ``Approval`` it builds lives only inside this method: nothing public returns a reusable one (CAP-T15, baja 1)."""
        if not self.valid_for(spec, request) or tuple(ref.to_action() for ref in self._permission.actions) != actions:
            return None
        targets = None if any(action.target is None for action in actions) else frozenset(a.target for a in actions)
        proof = Approval(Provenance(SourceKind.USER, ref=f"tool-approval:{self._permission.permission_request_id}"),
                         frozenset(action.kind for action in actions), targets)
        return [authorize(action, (proof,)) for action in actions]


def _approval_from_decision(permission: PermissionRequest, expires_at: datetime,
                            approved_at: datetime) -> ToolCallApproval:
    """Host-only factory: caller must obtain this after Store atomically releases a human decision."""
    return ToolCallApproval(permission, expires_at, _ISSUER, approved_at)


def pause_for_result(result: Any) -> Pause:
    """Translate an approval_required result into the existing run wait protocol."""
    permission = getattr(result, "permission_request", None)
    if permission is None or getattr(getattr(result, "error", None), "code", None) != "approval_required":
        raise ValueError("A tool permission request is required")
    return Pause(WaitingReason.TOOL_APPROVAL, permission.model_dump(mode="json"),
                 message=permission.summary, guard="tool_approval")
