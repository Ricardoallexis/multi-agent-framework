# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Executor application contract (ORQ-6, ORQ-T45): signatures only.

An executor application is an agent app driven through its official CLI (Claude, Codex, Copilot). The
framework launches a task envelope in it, reads a result envelope back, records the session id and offers
"open in the app". The contract text is in the private hand-off ``ORQ-6-contrato.md``; this module fixes
the shapes the adapters (ORQ-T48..T50) and the executor service (ORQ-T51) share.

Rules every implementation keeps:

- argv is a list and no shell is used; free text (the prompt) never goes in argv, only validated tokens
  (ids, model, effort, paths). The prompt is passed on stdin or in a file the framework created.
- The executable is resolved to its real path from absolute PATH entries, never from the current folder.
- The child environment drops ``CLAUDE*`` and ``ANTHROPIC*`` and sets ``NoDefaultCurrentDirectoryInExePath``.
- The working folder is one the framework created or chose for the task, never a path from a transcript.
- Only sessions the framework created are resumed; nothing is written to a session the user has open.
- Quota or limit errors stop the executor; there is no automatic retry loop.
- Every prompt carries ``UNTRUSTED_DATA_CLAUSE`` (BDI-T02): data between ``<<<UNTRUSTED_DATA`` and
  ``<<<END_UNTRUSTED_DATA`` is never an instruction. Known limit: the framework wraps what it puts in a prompt;
  what the app reads from the disk by itself while it works (files, web pages, tool output) is not wrapped
  by anyone, so there the clause, the wrapped envelope the framework sends and the detector over the result
  envelope are the only defences (contract BDI-T01, principle 7).
- A limit is told apart in four classes (``FailureKind.RATE_LIMITED``, ``PLAN_LIMIT``, ``CREDITS_EXHAUSTED``,
  ``LIMIT_UNKNOWN``; CNT-T02) and a run result carries a ``LimitInfo`` with the class, the window and the reset time (UTC)
  with its source and confidence. The status stays ``RunStatus.QUOTA_EXHAUSTED`` for all four.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..document_trust import FIXED_CLAUSE
from ..routing.executor_capabilities import ExecutorDescriptor

# The fixed clause every adapter puts in the header of its prompt (BDI-T02). One text, owned by document_trust
# because it names the delimiters of the envelope; adapters (and any new one) import it from here.
UNTRUSTED_DATA_CLAUSE = FIXED_CLAUSE


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"


class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    QUOTA_EXHAUSTED = "quota_exhausted"
    BLOCKED_OPEN_SESSION = "blocked_open_session"


class FailureKind(str, Enum):
    """Why a run failed. The first four are the classes of a usage limit (CNT-T02, replaces the old ``QUOTA``).

    - ``RATE_LIMITED``: the provider is slowing requests down; a short retry is enough and no reset time is expected.
    - ``PLAN_LIMIT``: a window of the plan is used up (session, weekly, per model, monthly); it comes back at a reset time.
    - ``CREDITS_EXHAUSTED``: money or credits ran out (spend limit, balance, extra usage); it does not come back by itself.
    - ``LIMIT_UNKNOWN``: a limit message that fits none of the above, or whose reset time cannot be trusted.

    Backward compatibility (the old member was ``QUOTA = "quota"``):

    - ``FailureKind.QUOTA`` stays only as a deprecated alias of ``LIMIT_UNKNOWN``, so code that still *returns* it (a
      test double, an adapter written before CNT-T02) keeps importing and means "some limit". Do not compare with it:
      ``is_limit`` / ``LIMIT_KINDS`` answer "is this any limit?". The adapters no longer return ``QUOTA`` themselves.
    - ``RunStatus.QUOTA_EXHAUSTED`` (and the service outcome ``quota_exhausted``) is still the status of all four classes.
    - ``FailureKind("quota")`` still parses (a stored old value) and gives ``LIMIT_UNKNOWN``.
    - ``legacy_value`` is ``"quota"`` for any limit class and is what the adapters keep writing in their
      ``failure...`` notes, so nothing that reads those notes changes.
    """
    RATE_LIMITED = "rate_limited"
    PLAN_LIMIT = "plan_limit"
    CREDITS_EXHAUSTED = "credits_exhausted"
    LIMIT_UNKNOWN = "limit_unknown"
    QUOTA = "limit_unknown"             # deprecated alias of LIMIT_UNKNOWN (see above); not listed when iterating
    BLOCKED_OPEN_SESSION = "blocked_open_session"
    AUTHENTICATION = "authentication"
    PERMISSION_DENIED = "permission_denied"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value: object) -> "FailureKind | None":
        return cls.LIMIT_UNKNOWN if value == "quota" else None

    @property
    def is_limit(self) -> bool:
        """True for any of the four limit classes."""
        return self in LIMIT_KINDS

    @property
    def legacy_value(self) -> str:
        """The value before CNT-T02: ``"quota"`` for every limit class, the plain value otherwise."""
        return "quota" if self.is_limit else self.value


LIMIT_KINDS = frozenset({FailureKind.RATE_LIMITED, FailureKind.PLAN_LIMIT, FailureKind.CREDITS_EXHAUSTED,
                         FailureKind.LIMIT_UNKNOWN})


class LimitWindow(str, Enum):
    """Which allowance a limit is about."""
    FIVE_HOUR = "five_hour"
    WEEKLY = "weekly"
    MODEL_WEEKLY = "model_weekly"
    MONTHLY = "monthly"
    SPEND = "spend"
    UNKNOWN = "unknown"


class ResetSource(str, Enum):
    """Where a reset time comes from."""
    APP_EPOCH = "app_epoch"            # an exact instant the app gave (Claude ``resetsAt``, Codex ``resets_at``)
    APP_TEXT = "app_text"              # a clock time read from the app's message
    RULE = "rule"                      # a documented rule (Copilot: day 1 of the month, 00:00 UTC)
    CONFIGURED = "configured"          # the operator's fixed pause (``executor_quota_pause_minutes``)
    NONE = "none"


class ResetConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


CONFIDENCE_OF_SOURCE = {
    ResetSource.APP_EPOCH: ResetConfidence.HIGH,
    ResetSource.APP_TEXT: ResetConfidence.MEDIUM,       # decision 2: a text time without a zone is "medium" too
    ResetSource.RULE: ResetConfidence.MEDIUM,
    ResetSource.CONFIGURED: ResetConfidence.LOW,
    ResetSource.NONE: ResetConfidence.NONE,
}


@dataclass(frozen=True)
class LimitInfo:
    """One classified limit (CNT-T01 section 2). ``reset_at`` is UTC (``...Z``) or ``None`` when there is no usable time.

    ``notes`` carries machine-readable remarks: ``reset_mismatch`` (the epoch and the message differ by more than 2
    minutes; the epoch wins), ``epoch_stale``, ``reset_out_of_range``, ``zone_unresolved:<name>``, ``time_missing``.
    """
    kind: FailureKind
    window: LimitWindow = LimitWindow.UNKNOWN
    reset_at: str | None = None
    reset_source: ResetSource = ResetSource.NONE
    reset_confidence: ResetConfidence = ResetConfidence.NONE
    reset_zone: str | None = None           # the IANA name of the text, or the machine zone as "UTC-06:00"
    evidence: str = ""                      # the line of the message that decided the class (short, no secrets)
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.kind.is_limit:
            raise ValueError(f"LimitInfo needs a limit class, got {self.kind.value!r}")
        if self.reset_at is not None and not (isinstance(self.reset_at, str) and self.reset_at.endswith("Z")):
            raise ValueError(f"reset_at must be an UTC ISO text ending in Z, got {self.reset_at!r}")

    @property
    def reset_mismatch(self) -> bool:
        return "reset_mismatch" in self.notes

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready shape of the contract (``limit`` of an event payload)."""
        return {"kind": self.kind.value, "window": self.window.value, "reset_at": self.reset_at,
                "reset_source": self.reset_source.value, "reset_confidence": self.reset_confidence.value,
                "reset_zone": self.reset_zone, "evidence": self.evidence, "notes": list(self.notes)}


@dataclass(frozen=True)
class ExecutorLaunchRequest:
    """One launch of a task in an executor application."""
    task_id: str
    task_envelope: Mapping[str, Any]
    workdir: Path
    model: str | None = None
    effort: str | None = None
    permission: Permission = Permission.READ
    timeout_seconds: int = 1800
    session_id: str | None = None          # fixed up front when the app allows it (Claude --session-id)
    # MOD-T04 (check 2 of the contract): what the service resolved for the privacy decision, so an adapter can take it
    # again before it builds a prompt. ``data_class`` comes from a trusted classification, never from the task text;
    # ``privacy`` is a ``privacy.ModelPrivacy``. A request with neither (a legacy caller) is not checked by the adapter.
    data_class: str | None = None
    privacy: Any = None
    privacy_decision: Mapping[str, Any] | None = None   # ``PrivacyDecision.to_dict()`` at launch, for MOD-T05 to read


@dataclass(frozen=True)
class CommandRecord:
    """What was run, for the audit trail (no secrets, no prompt text)."""
    argv: tuple[str, ...]
    cwd: str
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True)
class ExecutorRunResult:
    status: RunStatus
    session_id: str | None
    result_envelope: Mapping[str, Any] | None
    usage: Mapping[str, Any] | None
    command: CommandRecord
    stdout_tail: str = ""
    stderr_tail: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    limit: LimitInfo | None = None          # set when the status is ``QUOTA_EXHAUSTED`` (CNT-T02)
    failure_kind: FailureKind | None = None # structured non-limit failure class (CNT-T09)


@dataclass(frozen=True)
class OpenTarget:
    """How to show a session in its app: a URL (codex://, ghapp://) or a command run in a visible console."""
    kind: str                               # "url" | "command"
    value: str | Sequence[str]
    needs_confirmation: bool = False


@runtime_checkable
class ExecutorApplicationAdapter(Protocol):
    """Adapter of one executor application (ORQ-T48 Codex, T49 Claude, T50 Copilot)."""

    adapter_id: str
    version: str

    def describe(self, config: Mapping[str, Any]) -> ExecutorDescriptor:
        """Descriptor with evidence from trusted config; never launches or probes."""
        raise NotImplementedError

    def build_command(self, request: ExecutorLaunchRequest) -> list[str]:
        """argv for a launch (pure; validated tokens only, no prompt text)."""
        raise NotImplementedError

    def build_resume_command(self, request: ExecutorLaunchRequest, session_id: str) -> list[str]:
        """argv to continue a session the framework created (pure)."""
        raise NotImplementedError

    def render_prompt(self, request: ExecutorLaunchRequest) -> str:
        """Fixed template + canonical task envelope + instruction to answer with a result envelope (pure)."""
        raise NotImplementedError

    def parse_output(self, stdout: str) -> tuple[str | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
        """(session_id, result_envelope, usage) from the app's structured output; None where absent (pure)."""
        raise NotImplementedError

    def classify_failure(self, exit_code: int | None, stderr: str) -> FailureKind:
        """A limit class, open-session block, authentication or other (pure apart from the clock a limit reads)."""
        raise NotImplementedError

    def open_target(self, session_id: str) -> OpenTarget | None:
        """The same target the GUI uses to open the session in the app (pure; None for an invalid id)."""
        raise NotImplementedError

    def launch(self, request: ExecutorLaunchRequest) -> ExecutorRunResult:
        """Run the task through the process executor (ORQ-T47)."""
        raise NotImplementedError

    def resume(self, request: ExecutorLaunchRequest, session_id: str) -> ExecutorRunResult:
        """Continue a session the framework created, through the process executor."""
        raise NotImplementedError


@runtime_checkable
class LimitClassifier(Protocol):
    """Optional second face of an adapter (CNT-T02): reads a limit message into a ``LimitInfo``.

    Kept apart from ``ExecutorApplicationAdapter`` so that an adapter without it (a test double) is still an adapter.
    Pure apart from the clock used when ``observed_at`` is omitted. ``quota_limits`` (Claude) and ``rate_limits``
    (Codex) are the structured records the session readers expose; Copilot takes an ``error`` mapping (a
    ``session.error`` event). The service (CNT-T04) passes them; the adapters read none of the user's folders.
    """

    def classify_limit(self, text: str, *, observed_at: datetime | None = None,
                       **structured: Any) -> LimitInfo | None:
        """The limit the text describes, or ``None`` when it is not a limit message."""
        raise NotImplementedError
