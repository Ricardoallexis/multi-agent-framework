# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""One-use review of secrets before a non-local model receives text."""
from __future__ import annotations

import hashlib
import secrets
from typing import Any, Callable, Mapping

from .call_guards import ORDER_SECRET, PROCEED, CallContext, Pause, RunState, Stop, Verdict
from .contracts import WaitingReason
from .egress_guard import findings

SECRET_TYPES = frozenset({"private_key", "secret_key", "bearer", "long_token"})
EVENT_APPROVED = "secret_approved"
EVENT_RELEASED = "secret_released"


def scan_text(text: str) -> list[tuple[int, int, str]]:
    """Return secret spans in outbound text; personal data stays with egress."""
    return [match for match in findings(text, include_entropy=True) if match[2] in SECRET_TYPES]


def masked_sample(text: str, matches: list[tuple[int, int, str]]) -> str:
    """Show the finding kind and a few edge characters, never a complete value."""
    samples = []
    for start, end, kind in matches[:3]:
        value = text[start:end]
        samples.append(f"{kind}: {value[:3]}…{value[-3:]}" if len(value) > 8 else f"{kind}: ***")
    return "; ".join(samples)[:160]


def destination(call: CallContext) -> dict[str, str]:
    return {"model_id": call.model_id, "tier": str(getattr(call.spec, "tier", ""))}


def identity(call: CallContext, digest: str) -> dict[str, Any]:
    return {"step_id": call.step_id, "attempt": call.attempt, "model_id": call.model_id,
            "ordinal": call.ordinal, "retry": call.retry, "sha256": digest}


class SecretGuard:
    name = "secret"
    order = ORDER_SECRET
    observer = False
    skippable = False

    def __init__(self, *, egress: Mapping[str, Any] | None = None,
                 emit: Callable[[str, dict[str, Any]], None] | None = None,
                 approvals: Any = None, approval_id: str | None = None):
        self.egress = egress or {}
        self.emit = emit
        self.approvals = approvals
        self.approval_id = approval_id

    def check(self, call: CallContext, state: RunState) -> Verdict:
        if str(getattr(call.spec, "tier", "")).casefold() == "local":
            return PROCEED
        if call.outgoing_text is None:
            return Stop("secret_review_required", "Outbound text is unavailable for secret screening")
        matches = scan_text(call.outgoing_text)
        if not matches:
            return PROCEED
        action = self.egress.get("action", "off")
        if action == "block":
            return PROCEED  # the egress guard blocks this same text
        if action == "redact":
            if call.replace_outgoing is None:
                return Stop("secret_review_required", "Outbound text cannot be redacted")
            return PROCEED  # egress redacts all kinds, including entropy, in the chain

        digest = hashlib.sha256(call.outgoing_text.encode("utf-8")).hexdigest()
        wanted = identity(call, digest)
        target = destination(call)
        if self.approval_id and self.approvals is not None and self.approvals.consume(
                self.approval_id, sha256=digest, destination=target):
            return PROCEED

        released = {event.get("payload", {}).get("approval_id") for event in state.get("events") or ()
                    if event.get("event_type") == EVENT_RELEASED}
        for event in state.get("events") or ():
            payload = event.get("payload") or {}
            if (event.get("event_type") == EVENT_APPROVED and payload.get("approval_id") not in released
                    and all(payload.get(key) == value for key, value in wanted.items())
                    and payload.get("destination") == target):
                if self.emit is None:
                    return Stop("secret_review_required", "Cannot record secret approval use")
                self.emit(EVENT_RELEASED, {**wanted, "approval_id": payload["approval_id"], "destination": target})
                return PROCEED

        approval_id = secrets.token_urlsafe(24)
        request = {**wanted, "approval_id": approval_id, "destination": target,
                   "secret_types": sorted({kind for _, _, kind in matches}),
                   "masked_sample": masked_sample(call.outgoing_text, matches)}
        if self.approvals is not None:
            created = self.approvals.request(kind="secret_send", destination=target,
                                             location={"source": "model"},
                                             secret_types=request["secret_types"],
                                             masked_sample=request["masked_sample"], sha256=digest)
            request["approval_id"] = created["id"]
        return Pause(WaitingReason.SECRET_REVIEW.value, request, "Secret review required")
