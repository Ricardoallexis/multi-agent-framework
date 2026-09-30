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

When the timeout expires the whole process tree of the command is terminated
(``taskkill /T /F`` on Windows, a process group on POSIX), so a child process
that keeps the output open cannot hold the caller past the timeout. The output
is read on a thread of its own, so what the command printed before the timeout
is kept, and the call returns at most ``KILL_GRACE_SECONDS`` after it (or after
the command ends) even if a process that left the tree still holds the output
open. A command that ends while a child still holds its output is reported by its
exit code after the same grace period; on POSIX the leftover group is killed, while
on Windows a descendant that outlived its parent is no longer in the tree and stays.

Outcomes: exit code 0 -> ``passed``; another exit code or the timeout ->
``failed``; an unknown name or a process that cannot start -> ``error`` (a
configuration problem, not a defect of the delivered work).

No database or service imports.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
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
KILL_GRACE_SECONDS = 5  # after a timeout, how long to wait for the output to close once the tree is killed


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
        # POSIX: a session of its own, so the whole tree can be signalled as one group.
        process = subprocess.Popen(argv, cwd=command.cwd, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=os.name != "nt")
    except OSError as exc:  # not found, not executable, missing working directory
        return done("error", "cannot_start", output=f"Project command {name!r} could not start: {exc}")
    tail = _Tail(process.stdout)
    try:
        process.wait(timeout=command.timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        note = f"\n[timed out after {command.timeout} s]"
        try:
            process.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        if not tail.wait(KILL_GRACE_SECONDS):
            # A process that left the tree still holds the output: stop waiting for it.
            note += "\n[a process that outlived the command kept its output open; not waiting for it]"
        return done("failed", "timeout", output=tail.text() + note)
    if not tail.wait(KILL_GRACE_SECONDS):
        # The command ended but something it started still holds the output: take what was printed, do not wait.
        _kill_tree(process)
    if process.returncode == 0:
        return done("passed", "", 0, tail.text())
    return done("failed", "exit_code", process.returncode, tail.text())


class _Tail:
    """The last bytes a command wrote, read on a thread so a timeout can still return them.

    ``communicate()`` only hands the output over at end of file, which never comes while a
    child process keeps the pipe open; reading incrementally also bounds the memory used.
    """

    LIMIT = OUTPUT_EXCERPT_CHARS * 8  # bytes: more than enough for that many characters of UTF-8

    def __init__(self, stream):
        self._stream = stream
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                data = self._stream.read1(8192)
                if not data:
                    return
                with self._lock:
                    self._buffer += data
                    if len(self._buffer) > self.LIMIT:
                        del self._buffer[:len(self._buffer) - self.LIMIT]
        except (OSError, ValueError):
            return

    def wait(self, seconds: float) -> bool:
        """True when the output reached its end; also closes the pipe in that case."""
        self._thread.join(seconds)
        if self._thread.is_alive():
            return False
        try:
            self._stream.close()
        except OSError:
            pass
        return True

    def text(self) -> str:
        with self._lock:
            return bytes(self._buffer).decode("utf-8", errors="replace")

def _kill_tree(process: subprocess.Popen) -> None:
    """Terminate the process and the descendants it started; best effort, never raises."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)  # the command leads its own session: pgid == pid
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass
