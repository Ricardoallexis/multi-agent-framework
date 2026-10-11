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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from ..routing.executor_capabilities import ExecutorDescriptor


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
    QUOTA = "quota"
    BLOCKED_OPEN_SESSION = "blocked_open_session"
    AUTHENTICATION = "authentication"
    OTHER = "other"


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
        """Quota, open-session block, authentication or other (pure)."""
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
