"""One-call tool decisions issued only by the authenticated run approval path."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from multiagent.call_guards import Pause
from multiagent.contracts import WaitingReason
from multiagent.document_actions import Approval
from multiagent.document_trust import Provenance, SourceKind
from multiagent.tasks.envelopes import envelope_sha256

from .contracts import PermissionRequest, ToolRequest, ToolSpec, spec_sha256

from multiagent.errors import InvalidStateTransition

_ISSUER = object()
EVENT_REQUESTED = "tool_approval_requested"
EVENT_APPROVED = "tool_approval_approved"
EVENT_REJECTED = "tool_approval_rejected"
EVENT_RELEASED = "tool_approval_released"


class ToolApprovalMismatch(InvalidStateTransition):
    """The approval did not name the pending tool request (``code`` is the stable reason)."""

    def __init__(self, message: str, *, code: str = "permission_request_mismatch"):
        super().__init__(message)
        self.code = code


@dataclass
class ToolCallApproval:
    permission: PermissionRequest
    expires_at: datetime
    approval: Approval
    _issuer: object = field(repr=False)
    _used: bool = field(default=False, init=False, repr=False)

    def valid_for(self, spec: ToolSpec, request: ToolRequest) -> bool:
        if self._issuer is not _ISSUER or self._used:
            return False
        # Spend the grant even when the caller supplied a mismatched request.
        self._used = True
        p = self.permission
        now = datetime.now(timezone.utc)
        return (self.expires_at.tzinfo is not None and now < self.expires_at
                and p.context.scope == "run_step" and p.context.run_id == request.context.run_id
                and p.context.step_id == request.context.step_id and p.context.attempt == request.context.attempt
                and p.request_id == request.request_id and p.tool_id == request.tool_id
                and p.tool_version == spec.version and p.spec_sha256 == spec_sha256(spec)
                and p.arguments_sha256 == envelope_sha256(request.arguments))


def _approval_from_decision(permission: PermissionRequest, expires_at: datetime) -> ToolCallApproval:
    """Host-only factory: caller must obtain this after Store atomically releases a human decision."""
    actions = tuple(ref.to_action() for ref in permission.actions)
    proof = Approval(Provenance(SourceKind.USER, ref=f"tool-approval:{permission.permission_request_id}"),
                     frozenset(action.kind for action in actions),
                     frozenset(action.target for action in actions))
    return ToolCallApproval(permission, expires_at, proof, _ISSUER)


def pause_for_result(result: Any) -> Pause:
    """Translate an approval_required result into the existing run wait protocol."""
    permission = getattr(result, "permission_request", None)
    if permission is None or getattr(getattr(result, "error", None), "code", None) != "approval_required":
        raise ValueError("A tool permission request is required")
    return Pause(WaitingReason.TOOL_APPROVAL, permission.model_dump(mode="json"),
                 message=permission.summary, guard="tool_approval")
