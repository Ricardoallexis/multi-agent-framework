"""Deterministic outbound screening. Events contain only finding kinds and counts.

Patterns deliberately favor recall for block and escalate; formatted personal data can
produce false positives. Redaction never persists the source or a reversal map.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any, Callable, Mapping

from .call_guards import ORDER_EGRESS, PROCEED, CallContext, Pause, RunState, Stop, Verdict
from .contracts import EgressBlocked, WaitingReason

GUARD_NAME = "egress"
SCHEMA = "multiagent.egress/1"
EVENT_APPROVED = "egress_approved"
EVENT_RELEASED = "egress_released"
EVENT_REJECTED = "egress_rejected"

_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z]+ )*PRIVATE KEY-----|\Z)", re.I)),
    ("secret_key", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{24,}|sk_live_[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_-]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b")),
    ("secret_key", re.compile(r"\b(?:aws_secret_access_key|password|passwd|api_key|client_secret|access_token)\s*[:=]\s*[^\s,;]+", re.I)),
    ("bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}\b", re.I)),
    ("bearer", re.compile(r"\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/]{12,}={0,2}\b", re.I)),
    ("bearer", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("financial", re.compile(r"\b(?:[A-Z]{2}\d{2}[A-Z0-9]{11,30}|(?:\d[ -]?){15,18}\d)\b", re.I)),
    ("phone", re.compile(r"(?<!\w)(?<![0-9a-fA-F]{4}-)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)|\d{2,4})[ .-]?\d{3,4}[ .-]?\d{4}(?!\w)")),
    ("phone", re.compile(r"(?<!\w)(?<![0-9a-fA-F]{4}-)(?:\+\d{1,3}[ .-]?)?\d{3}[ .-]?\d{3}[ .-]?\d{3}(?!\w)")),
    ("document", re.compile(r"\b(?:[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]{2}|[A-Z&Ñ]{3,4}\d{6}[A-Z0-9]{3}|\d{3}-\d{2}-\d{4}|\d{8}[A-Z])\b", re.I)),
)
_LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9+/=_-]{32,}(?![A-Za-z0-9_])")
_HEX = re.compile(r"[0-9a-fA-F]+\Z")
_HEX_RUN = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{32,}(?![A-Za-z0-9])")
_UUID = re.compile(r"(?<![0-9a-fA-F-])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F-])")
_BLOB_REF = re.compile(r"blob:sha256:[0-9a-fA-F]{64}\b")
_URL_PATH = re.compile(r"https?://[^/\s]+(/[^\s?#]*)", re.I)
_URL_SEGMENT = re.compile(r"[A-Za-z0-9+=_-]{32,}\Z")


def _has_mixed_case_digits(value: str) -> bool:
    return any(char.isupper() for char in value) and any(char.islower() for char in value) and any(char.isdigit() for char in value)


def _looks_secret_token(value: str) -> bool:
    if _HEX.fullmatch(value) or _UUID.fullmatch(value):
        return False
    if _has_mixed_case_digits(value):
        return True
    counts = Counter(value)
    entropy = -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())
    return entropy >= 4.0


def findings(text: str, *, include_entropy: bool = False) -> list[tuple[int, int, str]]:
    matches = [(m.start(), m.end(), kind) for kind, pattern in _PATTERNS for m in pattern.finditer(text)]
    if include_entropy:
        uuid_spans = [m.span() for m in _UUID.finditer(text)]
        uuid_spans.extend(m.span() for m in _HEX_RUN.finditer(text))
        excluded = [m.span() for m in _BLOB_REF.finditer(text)]
        excluded.extend(uuid_spans)
        url_paths = [m.span(1) for m in _URL_PATH.finditer(text)]
        excluded.extend(url_paths)
        matches.extend((m.start(), m.end(), "long_token") for m in _LONG_TOKEN.finditer(text)
                       if _looks_secret_token(m.group())
                       and not any(start <= m.start() and m.end() <= end for start, end in excluded)
                       and not any(m.start() < end and start < m.end() for start, end in url_paths))
        for start, end in url_paths:
            path = text[start:end]
            for segment in re.finditer(r"[^/]+", path):
                value = segment.group()
                if _URL_SEGMENT.fullmatch(value) and _has_mixed_case_digits(value):
                    matches.append((start + segment.start(), start + segment.end(), "long_token"))
    matches.sort(key=lambda item: (item[0], -item[1]))
    accepted: list[tuple[int, int, str]] = []
    for start, end, kind in matches:
        if accepted and start < accepted[-1][1]:
            previous_start, previous_end, previous_kind = accepted[-1]
            accepted[-1] = (previous_start, max(previous_end, end), previous_kind)
            continue
        accepted.append((start, end, kind))
    return accepted


def redact(text: str, matches: list[tuple[int, int, str]]) -> str:
    parts: list[str] = []
    counters: Counter[str] = Counter()
    cursor = 0
    merged: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(matches):
        if merged and start < merged[-1][1]:
            prior_start, prior_end, prior_kind = merged[-1]
            merged[-1] = (prior_start, max(prior_end, end), prior_kind)
        else:
            merged.append((start, end, kind))
    for start, end, kind in merged:
        counters[kind] += 1
        parts.extend((text[cursor:start], f"[REDACTED:{kind}:{counters[kind]}]"))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _identity(call: CallContext, digest: str) -> dict[str, Any]:
    return {"step_id": call.step_id, "attempt": call.attempt, "model_id": call.model_id,
            "ordinal": call.ordinal, "retry": call.retry, "text_sha256": digest}


def is_egress_pause(payload: Mapping[str, Any]) -> bool:
    return payload.get("guard") == GUARD_NAME and payload.get("waiting_reason") == WaitingReason.EGRESS_REVIEW.value


def approval_from_pause(paused: Mapping[str, Any], *, approval_id: str, by: str, channel: str) -> dict[str, Any]:
    request = paused.get("request") if isinstance(paused.get("request"), Mapping) else {}
    return {"schema": SCHEMA, "approval_id": approval_id, "step_id": paused.get("step_id"),
            "attempt": paused.get("attempt"), "model_id": paused.get("model_id"),
            "ordinal": request.get("ordinal"), "retry": request.get("retry"),
            "text_sha256": request.get("text_sha256"), "findings": request.get("findings"),
            "by": by, "channel": channel}


class EgressGuard:
    name = GUARD_NAME
    order = ORDER_EGRESS
    observer = False

    def __init__(self, policy: Mapping[str, Any], *, emit: Callable[[str, dict[str, Any]], None] | None = None):
        if policy.get("action") not in ("redact", "block", "escalate"):
            raise ValueError("Unknown egress action")
        self.policy = policy
        self.emit = emit

    def check(self, call: CallContext, state: RunState) -> Verdict:
        if call.outgoing_text is None:
            if self.policy["action"] in ("block", "escalate"):
                return Stop("egress_blocked", "Outbound text is unavailable for screening")
            return PROCEED
        if getattr(call.spec, "tier", "").casefold() == "local" and not self.policy.get("include_local", False):
            return PROCEED
        action = self.policy["action"]
        if action not in ("redact", "block", "escalate"):
            return Stop("egress_blocked", "Unknown egress action")
        matches = findings(call.outgoing_text, include_entropy=action != "redact")
        if not matches:
            return PROCEED
        counts = dict(Counter(kind for _, _, kind in matches))
        summary = {"step_id": call.step_id, "attempt": call.attempt, "action": action, "findings": counts}
        digest = hashlib.sha256(call.outgoing_text.encode("utf-8")).hexdigest()
        if action == "redact":
            if call.replace_outgoing is None:
                raise EgressBlocked("Outbound text cannot be redacted")
            call.replace_outgoing(redact(call.outgoing_text, matches))
            if self.emit:
                self.emit("egress_redacted", summary)
            return PROCEED
        if action == "block":
            if self.emit:
                self.emit("egress_blocked", summary)
            return Stop("egress_blocked", "Outbound content was blocked by the egress policy", details={"findings": counts})
        if action == "escalate":
            identity = _identity(call, digest)
            approvals: dict[str, Mapping[str, Any]] = {}
            spent: set[str] = set()
            for event in state.get("events") or ():
                payload = event.get("payload") or {}
                approval_id = payload.get("approval_id")
                if not isinstance(approval_id, str) or not approval_id:
                    continue
                if event.get("event_type") == EVENT_RELEASED:
                    spent.add(approval_id)
                elif event.get("event_type") == EVENT_APPROVED and all(payload.get(k) == v for k, v in identity.items()):
                    approvals[approval_id] = payload
            for approval_id in approvals:
                if approval_id not in spent:
                    if self.emit is None:
                        raise EgressBlocked("Cannot record egress approval use")
                    self.emit(EVENT_RELEASED, {**identity, "approval_id": approval_id})
                    return PROCEED
            return Pause(WaitingReason.EGRESS_REVIEW.value,
                         {**identity, "findings": counts}, "Outbound content needs a person's review")
        return Stop("egress_blocked", "Unknown egress action")
