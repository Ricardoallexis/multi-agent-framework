# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project commands (ORQ-T12): named commands from a local, trusted file.

A gate check ``command:<name>`` runs the project command called ``<name>``.
Commands are defined only in a local TOML file that the user controls, never in
a task, a group or a request::

    [commands.test]
    argv = ["python", "-m", "pytest", "-q"]
    timeout = 1800            # seconds, 1..7200; 600 by default
    cwd = "C:/work/project"   # optional absolute folder

``argv`` runs without a shell. The only placeholders are ``{task_id}``,
``{group_id}`` and ``{attempt}``, each as a whole argument. The command
inherits the environment of the framework process. Its combined output is cut
to the last 4000 characters.

Outcomes: exit code 0 -> ``passed``; another exit code or the timeout ->
``failed``; an unknown name or a process that cannot start -> ``error`` (a
configuration problem, not a defect of the delivered work).

No database or service imports.
"""
from __future__ import annotations

import re
import subprocess
import time
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMMAND_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
PLACEHOLDERS = ("{task_id}", "{group_id}", "{attempt}")
_PLACEHOLDER_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DEFAULT_TIMEOUT_SECONDS = 600
MAX_TIMEOUT_SECONDS = 7200
MAX_ARGV = 50
OUTPUT_EXCERPT_CHARS = 4000


class CommandConfigError(ValueError):
    """The local commands file is malformed; ``field`` points at the problem."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class ProjectCommand:
    name: str
    argv: tuple[str, ...]
    timeout: int
    cwd: Path


@dataclass(frozen=True)
class CommandResult:
    name: str
    status: str                 # "passed" | "failed" | "error"
    exit_code: int | None
    duration_ms: int
    output: str                 # last OUTPUT_EXCERPT_CHARS characters
    reason: str                 # "", "exit_code", "timeout", "not_configured", "cannot_start", "missing_value"
    started_at: str
    ended_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "exit_code": self.exit_code,
                "duration_ms": self.duration_ms, "output": self.output, "reason": self.reason,
                "started_at": self.started_at, "ended_at": self.ended_at}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_command(name: str, raw: Any, default_cwd: Path) -> ProjectCommand:
    where = f"commands.{name}"
    if not COMMAND_NAME.fullmatch(name):
        raise CommandConfigError(f"Invalid command name {name!r}: use lowercase letters, digits, '_' or '-'", where)
    if not isinstance(raw, dict):
        raise CommandConfigError(f"{where} must be a table", where)
    unknown = sorted(set(raw) - {"argv", "timeout", "cwd"})
    if unknown:
        raise CommandConfigError(f"{where} has unknown keys: {', '.join(unknown)}", where)
    argv = raw.get("argv")
    if (not isinstance(argv, list) or not 1 <= len(argv) <= MAX_ARGV
            or not all(isinstance(item, str) and item for item in argv)):
        raise CommandConfigError(f"{where}.argv must be a list of 1 to {MAX_ARGV} non-empty strings", f"{where}.argv")
    for item in argv:
        if ("{" in item or "}" in item) and item not in PLACEHOLDERS:
            raise CommandConfigError(
                f"{where}.argv: placeholders are {', '.join(PLACEHOLDERS)}, each as a whole argument; got {item!r}",
                f"{where}.argv")
    timeout = raw.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise CommandConfigError(f"{where}.timeout must be an integer from 1 to {MAX_TIMEOUT_SECONDS}", f"{where}.timeout")
    cwd_raw = raw.get("cwd")
    if cwd_raw is None:
        cwd = default_cwd
    else:
        if not isinstance(cwd_raw, str) or not Path(cwd_raw).is_absolute():
            raise CommandConfigError(f"{where}.cwd must be an absolute path", f"{where}.cwd")
        cwd = Path(cwd_raw)
    return ProjectCommand(name, tuple(argv), timeout, cwd)


def load_commands(path: Path, *, default_cwd: Path) -> dict[str, ProjectCommand]:
    """Read and validate the local commands file; ``{}`` when it does not exist.

    Raises ``CommandConfigError`` for invalid TOML, unknown keys, bad names,
    argv, placeholders, timeouts or working directories.
    """
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise CommandConfigError(f"Invalid TOML in the commands file: {exc}") from None
    unknown = sorted(set(data) - {"commands"})
    if unknown:
        raise CommandConfigError(f"Unknown top-level keys: {', '.join(unknown)}; only [commands.<name>] is allowed")
    commands = data.get("commands", {})
    if not isinstance(commands, dict):
        raise CommandConfigError("[commands] must be a table", "commands")
    return {name: _parse_command(name, raw, default_cwd) for name, raw in commands.items()}


def _argv(command: ProjectCommand, values: Mapping[str, str]) -> list[str] | None:
    resolved = []
    for item in command.argv:
        if item in PLACEHOLDERS:
            value = values.get(item[1:-1])
            if value is None or not _PLACEHOLDER_VALUE.fullmatch(str(value)):
                return None
            resolved.append(str(value))
        else:
            resolved.append(item)
    return resolved


def run_command(commands: Mapping[str, ProjectCommand], name: str, values: Mapping[str, str] | None = None) -> CommandResult:
    """Run the project command ``name`` without a shell and report its outcome; never raises for the command itself."""
    started = _now()
    clock = time.monotonic()

    def done(status: str, reason: str, exit_code: int | None = None, output: str = "") -> CommandResult:
        return CommandResult(name, status, exit_code, int((time.monotonic() - clock) * 1000),
                             output[-OUTPUT_EXCERPT_CHARS:], reason, started, _now())

    command = commands.get(name)
    if command is None:
        return done("error", "not_configured", output=f"Project command {name!r} is not configured")
    argv = _argv(command, values or {})
    if argv is None:
        return done("error", "missing_value", output=f"Project command {name!r} needs a valid value for each placeholder")
    try:
        completed = subprocess.run(argv, cwd=command.cwd, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=command.timeout)
    except subprocess.TimeoutExpired as exc:
        partial = (exc.output or b"").decode("utf-8", errors="replace") if isinstance(exc.output, bytes) else (exc.output or "")
        return done("failed", "timeout", output=partial + f"\n[timed out after {command.timeout} s]")
    except OSError as exc:  # not found, not executable, missing working directory
        return done("error", "cannot_start", output=f"Project command {name!r} could not start: {exc}")
    output = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode == 0:
        return done("passed", "", 0, output)
    return done("failed", "exit_code", completed.returncode, output)
