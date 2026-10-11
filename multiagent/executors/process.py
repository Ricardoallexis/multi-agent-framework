# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Safe process executor for agent CLIs (ORQ-6, ORQ-T47).

``run`` launches one program with an argument list and waits for it; ``start`` returns a ``ProcessHandle`` for long
runs that another thread may ``cancel``. Both apply the same rules, and refuse (``ProcessLaunchError``) before
anything is started when one is broken:

- ``argv`` is a ``list`` of ``str``; there is never a shell (``shell=False``), ``cmd /c`` or ``-EncodedCommand``.
  Free text (the prompt) never goes in ``argv``: it goes in ``stdin_text``. As a guard, an argument may not hold a
  line break or a NUL and may not be longer than ``MAX_ARGUMENT_CHARS``. When the program is a Windows ``.cmd``/``.bat``
  shim (npm), which runs under ``cmd.exe``, every argument must also be made of plain characters only (BatBadBut).
- ``argv[0]`` is turned into a real absolute path of an existing file: an absolute path is used as it is, a bare name
  is looked up only in the absolute ``PATH`` entries that are not the current folder (``launch_safety.find_executable``).
  A relative path (``.\\tool``, ``bin/tool``) is refused, and so is a program that sits inside ``cwd`` (a repository
  could plant it). The resolved path replaces ``argv[0]`` and is what is run and recorded.
- ``cwd`` must be an existing absolute folder, chosen by the framework (a worktree), never taken from a transcript.
- The child environment is ``os.environ`` without ``CLAUDE*`` / ``ANTHROPIC*``, plus
  ``NoDefaultCurrentDirectoryInExePath=1``, plus ``env_extra`` (which may not bring those names back nor change that
  variable). The child has no terminal: ``stdin`` is the pipe of ``stdin_text``, or closed when it is ``None``.
- ``timeout_seconds`` and ``cancel`` kill the whole process tree: a new process group plus ``taskkill /T /F`` and a
  Job Object (which also kills what the program left running) on Windows, a new session and ``killpg`` on POSIX.
  Anything still holding the output pipes after the program exits is killed too.
- ``stdout`` and ``stderr`` are read while the program runs and only the last ``output_cap_bytes`` of each are kept
  (decoded as UTF-8 with replacement); ``*_truncated`` says that bytes were dropped from the front.
- The exact argv is logged (``multiagent.executors.process``) when the program starts and its outcome when it ends:
  never ``stdin_text``. The callers keep secrets out of argv; the ``CommandRecord`` carries the same argv.

Outcome flags: ``timed_out`` (killed because the time ran out), ``cancelled`` (killed by ``cancel`` while it was
running; cancelling a program that already finished does nothing). ``exit_code`` is the one the operating system
reports, which for a killed program is its kill status.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from ..errors import MultiAgentError
from .contract import CommandRecord
from .launch_safety import (
    NO_CURRENT_DIR_VARIABLE, find_executable, is_scrubbed_name, is_within, launch_environment,
)

_logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_CAP_BYTES = 1_000_000
MAX_ARGUMENT_CHARS = 4096
SHIM_SUFFIXES = (".cmd", ".bat")
SHIM_ARGUMENT = re.compile(r"[\w .:@/\\+=-]*")   # no quote, %, ^, &, |, <, >, !, parentheses, ; or ,
READ_CHUNK = 65536
READER_GRACE_SECONDS = 1.0       # how long output pipes may stay open after the program has exited
TASKKILL_TIMEOUT_SECONDS = 15

# error codes (``ProcessLaunchError.code``)
INVALID_ARGV = "invalid_argv"
RELATIVE_EXECUTABLE = "relative_executable"
EXECUTABLE_NOT_FOUND = "executable_not_found"
EXECUTABLE_IN_WORKDIR = "executable_in_workdir"
UNSAFE_SHIM_ARGUMENT = "unsafe_shim_argument"
INVALID_CWD = "invalid_cwd"
INVALID_ENV = "invalid_env"
INVALID_LIMIT = "invalid_limit"
LAUNCH_FAILED = "launch_failed"


class ProcessLaunchError(MultiAgentError):
    """The program was not started because a rule of the executor was not met."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProcessOutcome:
    exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    cancelled: bool
    command: CommandRecord


# --------------------------------------------------------------------------- validation

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_cwd(cwd: Path | str) -> Path:
    """The working folder as an absolute ``Path``, or ``ProcessLaunchError``: it must exist and be a folder."""
    if not isinstance(cwd, (str, os.PathLike)) or not str(os.fspath(cwd)).strip() or "\x00" in str(os.fspath(cwd)):
        raise ProcessLaunchError(INVALID_CWD, "cwd must be a path")
    folder = Path(os.fspath(cwd))
    if not folder.is_absolute():
        raise ProcessLaunchError(INVALID_CWD, f"cwd must be an absolute path, got {str(folder)!r}")
    if not folder.is_dir():
        raise ProcessLaunchError(INVALID_CWD, f"cwd is not an existing folder: {str(folder)!r}")
    return folder


def validate_argv(argv: object) -> list[str]:
    """``argv`` as a new list of ``str`` with no line break, NUL or oversized argument, or ``ProcessLaunchError``."""
    if not isinstance(argv, list) or not argv:
        raise ProcessLaunchError(INVALID_ARGV, "argv must be a non-empty list of strings")
    for index, item in enumerate(argv):
        if not isinstance(item, str):
            raise ProcessLaunchError(INVALID_ARGV, f"argv[{index}] must be a string")
        if "\x00" in item or "\n" in item or "\r" in item:
            raise ProcessLaunchError(INVALID_ARGV, f"argv[{index}] holds a line break or NUL: free text goes in stdin")
        if len(item) > MAX_ARGUMENT_CHARS:
            raise ProcessLaunchError(INVALID_ARGV, f"argv[{index}] is longer than {MAX_ARGUMENT_CHARS} characters: "
                                     "free text goes in stdin")
    if not argv[0].strip():
        raise ProcessLaunchError(INVALID_ARGV, "argv[0] is empty")
    return list(argv)


def resolve_executable(name: str, cwd: Path) -> str:
    """The absolute path of the program ``name`` (``argv[0]``), or ``ProcessLaunchError``."""
    has_folder = bool(os.path.dirname(name) or os.path.splitdrive(name)[0])
    if has_folder:
        if not Path(name).is_absolute():  # on Windows that needs a drive and a root: ``\tool.exe`` is relative
            raise ProcessLaunchError(RELATIVE_EXECUTABLE, f"the program must be a bare name or an absolute path, "
                                     f"not {name!r}")
        found = shutil.which(name)  # an explicit path: existence, execute permission and PATHEXT only
    else:
        found = find_executable(name)
    if not found:
        raise ProcessLaunchError(EXECUTABLE_NOT_FOUND, f"program {name!r} was not found in an absolute PATH entry")
    found = os.path.abspath(found)
    if not os.path.isfile(found):
        raise ProcessLaunchError(EXECUTABLE_NOT_FOUND, f"program {found!r} is not a file")
    if is_within(found, cwd):
        raise ProcessLaunchError(EXECUTABLE_IN_WORKDIR, f"program {found!r} is inside the working folder")
    return found


def _is_shim(executable: str) -> bool:
    return sys.platform == "win32" and executable.lower().endswith(SHIM_SUFFIXES)


def _check_shim_arguments(args: Sequence[str]) -> None:
    for index, item in enumerate(args, start=1):
        if not SHIM_ARGUMENT.fullmatch(item):
            raise ProcessLaunchError(UNSAFE_SHIM_ARGUMENT, f"argv[{index}] has characters that cmd.exe would "
                                     "interpret; a .cmd/.bat program only takes plain tokens")


def build_environment(env_extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child environment: ``launch_environment()`` plus a validated ``env_extra``."""
    env = launch_environment()
    for name, value in (env_extra or {}).items():
        if not isinstance(name, str) or not isinstance(value, str) or not name or "=" in name or "\x00" in name + value:
            raise ProcessLaunchError(INVALID_ENV, "env_extra must map non-empty names (no '=' or NUL) to strings")
        if is_scrubbed_name(name):
            raise ProcessLaunchError(INVALID_ENV, f"env_extra may not set {name!r}")
        if name.upper() == NO_CURRENT_DIR_VARIABLE.upper():
            raise ProcessLaunchError(INVALID_ENV, f"env_extra may not set {NO_CURRENT_DIR_VARIABLE}")
        if sys.platform == "win32":  # names are case-insensitive there: replace, never duplicate
            for existing in [key for key in env if key.upper() == name.upper()]:
                del env[existing]
        env[name] = value
    return env


def _validate_limits(timeout_seconds: object, output_cap_bytes: object) -> tuple[float, int]:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not timeout_seconds > 0:
        raise ProcessLaunchError(INVALID_LIMIT, "timeout_seconds must be a positive number")
    if isinstance(output_cap_bytes, bool) or not isinstance(output_cap_bytes, int) or output_cap_bytes < 1:
        raise ProcessLaunchError(INVALID_LIMIT, "output_cap_bytes must be a positive integer")
    return float(timeout_seconds), output_cap_bytes


# --------------------------------------------------------------------------- output capture

class _TailBuffer:
    """The last ``cap`` bytes fed to it; a reader thread feeds it, the supervisor reads it after the thread ends."""

    def __init__(self, cap: int):
        self.cap = cap
        self.truncated = False
        self._data = bytearray()

    def feed(self, chunk: bytes) -> None:
        self._data += chunk
        if len(self._data) > 2 * self.cap:  # trim lazily: at most one copy per ``cap`` bytes read
            del self._data[:-self.cap]
            self.truncated = True

    def text(self) -> str:
        data = bytes(self._data)
        if len(data) > self.cap:
            data = data[-self.cap:]
            self.truncated = True
        if self.truncated:  # do not start in the middle of a UTF-8 character
            skip = 0
            while skip < 3 and skip < len(data) and (data[skip] & 0xC0) == 0x80:
                skip += 1
            data = data[skip:]
        return data.decode("utf-8", errors="replace")


def _pump(stream: IO[bytes], buffer: _TailBuffer) -> None:
    try:
        while True:
            chunk = stream.read1(READ_CHUNK)  # type: ignore[attr-defined]
            if not chunk:
                return
            buffer.feed(chunk)
    except (OSError, ValueError):
        return


def _write_stdin(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except (OSError, ValueError):  # the program closed its stdin or ended before reading it all
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------- killing a tree

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _KILL_ON_JOB_CLOSE = 0x2000
    _EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER), ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    class _JobObject:
        """A Windows Job Object that kills every process in it when terminated or when the last handle closes."""

        def __init__(self, pid: int):
            self._handle = _kernel32.CreateJobObjectW(None, None)
            if not self._handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
            try:
                limits = _ExtendedLimits()
                limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
                if not _kernel32.SetInformationJobObject(self._handle, _EXTENDED_LIMIT_INFORMATION,
                                                         ctypes.byref(limits), ctypes.sizeof(limits)):
                    raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
                process = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
                if not process:
                    raise OSError(ctypes.get_last_error(), "OpenProcess failed")
                try:
                    if not _kernel32.AssignProcessToJobObject(self._handle, process):
                        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
                finally:
                    _kernel32.CloseHandle(process)
            except OSError:
                self.close()
                raise

        def terminate(self) -> None:
            if self._handle:
                _kernel32.TerminateJobObject(self._handle, 1)

        def close(self) -> None:
            handle, self._handle = self._handle, None
            if handle:
                _kernel32.CloseHandle(handle)  # KILL_ON_JOB_CLOSE: whatever is still in the job dies here

    def _open_job(pid: int) -> "_JobObject | None":
        try:
            return _JobObject(pid)
        except OSError as exc:  # e.g. the parent is in a job that forbids nesting: taskkill still covers the tree
            _logger.debug("no job object for pid %s: %s", pid, exc)
            return None

    def _taskkill(pid: int) -> None:
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "taskkill.exe")
        try:
            subprocess.run([exe, "/PID", str(pid), "/T", "/F"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=TASKKILL_TIMEOUT_SECONDS, shell=False,
                           creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.warning("taskkill for pid %s failed: %s", pid, exc)


# --------------------------------------------------------------------------- the handle

class ProcessHandle:
    """A running program. ``wait`` gives its ``ProcessOutcome``; ``cancel`` kills its process tree."""

    def __init__(self, proc: subprocess.Popen[bytes], command: CommandRecord, timeout_seconds: float,
                 output_cap_bytes: int, stdin_data: bytes | None):
        self._proc = proc
        self._command = command
        self._timeout = timeout_seconds
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._outcome: ProcessOutcome | None = None
        self._timed_out = False
        self._cancelled = False
        self._out = _TailBuffer(output_cap_bytes)
        self._err = _TailBuffer(output_cap_bytes)
        self._job: Any = _open_job(proc.pid) if sys.platform == "win32" else None  # a Windows Job Object
        assert proc.stdout is not None and proc.stderr is not None
        self._readers = [
            threading.Thread(target=_pump, args=(proc.stdout, self._out), name=f"proc-{proc.pid}-out", daemon=True),
            threading.Thread(target=_pump, args=(proc.stderr, self._err), name=f"proc-{proc.pid}-err", daemon=True),
        ]
        if stdin_data is not None:
            assert proc.stdin is not None
            self._readers.append(threading.Thread(target=_write_stdin, args=(proc.stdin, stdin_data),
                                                  name=f"proc-{proc.pid}-in", daemon=True))
        for thread in self._readers:
            thread.start()
        threading.Thread(target=self._supervise, name=f"proc-{proc.pid}-watch", daemon=True).start()

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def command(self) -> CommandRecord:
        """What was started (``exit_code`` and ``finished_at`` are filled in the outcome's record)."""
        return self._command

    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        """The outcome once the program has ended and its output was collected; ``TimeoutError`` after ``timeout``."""
        if not self._done.wait(timeout):
            raise TimeoutError("the process is still running")
        assert self._outcome is not None
        return self._outcome

    def cancel(self) -> bool:
        """Kill the process tree. True when the program was still running (the outcome will say ``cancelled``)."""
        with self._lock:
            if self._done.is_set() or self._proc.poll() is not None:
                return False
            self._cancelled = True
        _logger.warning("process %s cancelled: killing its process tree", self._proc.pid)
        self._kill_tree()
        return True

    # -- internals

    def _kill_tree(self) -> None:
        """Kill the program and every descendant. The tree is walked while the program is still alive."""
        pid = self._proc.pid
        if sys.platform == "win32":
            if self._proc.poll() is None:
                _taskkill(pid)
            if self._job is not None:
                self._job.terminate()
        else:
            try:
                os.killpg(pid, signal.SIGKILL)  # the program leads its own session and group
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self._proc.kill()
        except OSError:
            pass

    def _kill_leftovers(self) -> None:
        """The program has exited but something still holds its output pipes: kill what remains of its tree."""
        if sys.platform == "win32":
            if self._job is not None:
                self._job.terminate()
        else:
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def _supervise(self) -> None:
        try:
            try:
                self._proc.wait(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                with self._lock:
                    if self._proc.poll() is None and not self._cancelled:
                        self._timed_out = True
                if self._timed_out:
                    _logger.warning("process %s timed out after %ss: killing its process tree", self._proc.pid, self._timeout)
                self._kill_tree()
                self._proc.wait()
            self._collect()
        except BaseException:  # never leave ``wait`` blocked
            _logger.exception("process %s supervision failed", self._proc.pid)
            self._kill_tree()
            self._finish(exit_code=self._proc.poll())
        finally:
            self._done.set()

    def _collect(self) -> None:
        deadline = time.monotonic() + READER_GRACE_SECONDS
        for thread in self._readers:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self._readers):
            self._kill_leftovers()
            for thread in self._readers:
                thread.join(READER_GRACE_SECONDS)
        if self._job is not None:
            self._job.close()  # kills anything still in the job
        for stream in (self._proc.stdout, self._proc.stderr):
            if stream is not None and not any(thread.is_alive() for thread in self._readers):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        self._finish(exit_code=self._proc.returncode)

    def _finish(self, *, exit_code: int | None) -> None:
        record = CommandRecord(argv=self._command.argv, cwd=self._command.cwd, started_at=self._command.started_at,
                               finished_at=_now(), exit_code=exit_code)
        self._outcome = ProcessOutcome(
            exit_code=exit_code, stdout_tail=self._out.text(), stderr_tail=self._err.text(),
            stdout_truncated=self._out.truncated, stderr_truncated=self._err.truncated,
            timed_out=self._timed_out, cancelled=self._cancelled, command=record)
        _logger.info("process %s finished: exit_code=%s timed_out=%s cancelled=%s seconds=%.2f argv=%s",
                     self._proc.pid, exit_code, self._timed_out, self._cancelled, time.monotonic() - self._started,
                     list(record.argv))


# --------------------------------------------------------------------------- public API

def check_launch(argv: list[str], *, cwd: Path | str) -> str:
    """The checks ``start`` applies before it spawns, without starting anything; returns the resolved program path.

    Raises ``ProcessLaunchError`` for a bad argv or folder, a program that is not found (or sits in the folder), or a
    ``.cmd`` shim argument cmd.exe would interpret. The executor service (ORQ-T51) calls it before it changes a task.
    """
    arguments = validate_argv(argv)
    executable = resolve_executable(arguments[0], validate_cwd(cwd))
    if _is_shim(executable):
        _check_shim_arguments(arguments[1:])
    return executable


def start(argv: list[str], *, cwd: Path | str, stdin_text: str | None, timeout_seconds: float,
          env_extra: Mapping[str, str] | None = None, output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES) -> ProcessHandle:
    """Validate and start the program; returns at once with a handle (``handle.wait()`` for the outcome)."""
    arguments = validate_argv(argv)
    folder = validate_cwd(cwd)
    timeout, cap = _validate_limits(timeout_seconds, output_cap_bytes)
    if stdin_text is not None and not isinstance(stdin_text, str):
        raise ProcessLaunchError(INVALID_ARGV, "stdin_text must be a string or None")
    executable = resolve_executable(arguments[0], folder)
    arguments[0] = executable
    if _is_shim(executable):
        _check_shim_arguments(arguments[1:])
    env = build_environment(env_extra)

    stdin_data = None if stdin_text is None else stdin_text.encode("utf-8", errors="replace")
    command = CommandRecord(argv=tuple(arguments), cwd=str(folder), started_at=_now())
    flags: dict[str, object]
    if sys.platform == "win32":
        flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        flags = {"start_new_session": True}
    _logger.info("process start: argv=%s cwd=%s timeout=%ss stdin=%s", arguments, folder, timeout,
                 "none" if stdin_data is None else f"{len(stdin_data)} bytes")
    try:
        proc = subprocess.Popen(
            arguments, cwd=str(folder), env=env, shell=False,
            stdin=subprocess.DEVNULL if stdin_data is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **flags)  # type: ignore[arg-type]
    except (OSError, ValueError) as exc:
        _logger.error("process could not start: argv=%s error=%s", arguments, exc)
        raise ProcessLaunchError(LAUNCH_FAILED, f"the program could not be started: {exc}") from exc
    return ProcessHandle(proc, command, timeout, cap, stdin_data)


def run(argv: list[str], *, cwd: Path | str, stdin_text: str | None, timeout_seconds: float,
        env_extra: Mapping[str, str] | None = None, output_cap_bytes: int = DEFAULT_OUTPUT_CAP_BYTES) -> ProcessOutcome:
    """Start the program and wait for it (see the module docstring for the rules and the outcome)."""
    return start(argv, cwd=cwd, stdin_text=stdin_text, timeout_seconds=timeout_seconds, env_extra=env_extra,
                 output_cap_bytes=output_cap_bytes).wait()


def cancel(handle: ProcessHandle) -> bool:
    """Kill the process tree of a handle returned by ``start``; True when it was still running."""
    return handle.cancel()
