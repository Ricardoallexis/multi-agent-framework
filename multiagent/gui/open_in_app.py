# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Open a Claude session in the Claude app from the GUI (GUI-T06).

Codex and Copilot sessions open through a ``codex://`` / ``ghapp://`` link that the browser follows
(the client validates it). Claude has no such link: ``POST /gui/api/sessions/{id}/open`` launches
``claude --desktop --resume <uuid>`` in a new, visible console. It lives in the token-protected GUI router.

The request carries nothing the server uses: it looks the session up itself, takes the UUID from its own
``SessionSummary`` and checks it again before building the argument list. The process is started with an
argument list (never a shell), in a new console, with an environment that has no ``CLAUDE*`` or
``ANTHROPIC*`` variable (the viewer may itself have been started from a Claude session) and with
``NoDefaultCurrentDirectoryInExePath=1``, always in the user's home folder: never in a folder taken from a
transcript, because an npm shim (``claude.cmd``) runs under ``cmd.exe``, which looks in the current folder first
for the ``node`` it calls. ``claude`` is looked up only in absolute ``PATH`` entries other than the server's own
current folder, and launches are at least 2 seconds apart. Nothing controls windows and nothing reads or writes
the apps' data. ``gui_allow_launch = False`` turns the launch off.

Errors (``ServiceError``): ``not_found`` (404, unknown session), ``invalid_session_id`` (400),
``unsupported_source`` (400, not a Claude session), ``invalid_open_target`` (400, the session has no
valid UUID to resume), ``launch_disabled`` (403), ``claude_not_found`` (409, ``claude`` is not on PATH),
``launch_unsupported`` (409, not Windows), ``too_many_launches`` (429, another launch less than 2 seconds ago) and
``launch_failed`` (409).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from ..executors import launch_safety
from ..services.errors import ServiceError
from ..services.sessions import SessionServices
from ..sessions import is_uuid

CREATE_NEW_CONSOLE = 0x00000010  # the Windows process-creation flag (``subprocess.CREATE_NEW_CONSOLE``)
SCRUBBED_PREFIXES = launch_safety.SCRUBBED_PREFIXES
COMMAND_PREFIX = ("claude", "--desktop", "--resume")
NO_CURRENT_DIR_VARIABLE = launch_safety.NO_CURRENT_DIR_VARIABLE
MIN_LAUNCH_INTERVAL = 2.0  # seconds between two launches by this process

UNSUPPORTED_SOURCE = "unsupported_source"
INVALID_OPEN_TARGET = "invalid_open_target"
LAUNCH_DISABLED = "launch_disabled"
CLAUDE_NOT_FOUND = "claude_not_found"
LAUNCH_UNSUPPORTED = "launch_unsupported"
LAUNCH_FAILED = "launch_failed"
TOO_MANY_LAUNCHES = "too_many_launches"

_launch_lock = threading.Lock()
_last_launch: float | None = None  # ``time.monotonic()`` of the last launch attempt of this process


def launch_supported() -> bool:
    """A visible console exists only on Windows (``CREATE_NEW_CONSOLE``)."""
    return sys.platform == "win32"


def claude_resume_uuid(summary: dict[str, Any]) -> str:
    """The UUID to resume, taken from the server's own summary of a Claude session, or an error."""
    if summary.get("source") != "claude":
        raise ServiceError(UNSUPPORTED_SOURCE, "Only Claude sessions are opened by launching a command; "
                           "Codex and Copilot sessions open through their own link", status=400)
    native = summary.get("native_id")
    open_in = summary.get("open_in_app")
    expected = [*COMMAND_PREFIX, native]
    if (not is_uuid(native) or summary.get("id") != f"claude:{native}" or not isinstance(open_in, dict)
            or open_in.get("kind") != "command" or open_in.get("value") != expected):
        raise ServiceError(INVALID_OPEN_TARGET, "This session has no valid identifier to resume "
                           "(subagent sessions cannot be opened on their own)", status=400)
    return str(native)


def scrubbed_environment() -> dict[str, str]:
    """A copy of the environment without any ``CLAUDE*`` or ``ANTHROPIC*`` variable."""
    return launch_safety.scrubbed_environment()


def launch_environment() -> dict[str, str]:
    """The scrubbed environment plus ``NoDefaultCurrentDirectoryInExePath=1`` (the current folder is not searched)."""
    return launch_safety.launch_environment()


def working_folder() -> tuple[str, str]:
    """``(folder, "home")``: the user's home, always (a folder from a transcript is never used to run a program)."""
    return str(Path.home()), "home"


_normal = launch_safety.normal_path


def find_claude() -> str | None:
    """The ``claude`` program from the absolute ``PATH`` entries that are not the server's current folder, or None."""
    return launch_safety.find_executable("claude", which=shutil.which)


def reserve_launch() -> None:
    """Record a launch now, or fail with 429 when the previous one was less than ``MIN_LAUNCH_INTERVAL`` ago."""
    global _last_launch
    with _launch_lock:
        now = time.monotonic()
        if _last_launch is not None and now - _last_launch < MIN_LAUNCH_INTERVAL:
            raise ServiceError(TOO_MANY_LAUNCHES, "Another launch just happened; wait a couple of seconds and try "
                               "again", status=429)
        _last_launch = now


def launch_claude_desktop(summary: dict[str, Any], *, allow: bool) -> dict[str, Any]:
    """Start ``claude --desktop --resume <uuid>`` for a Claude session summary; returns what was started."""
    uuid = claude_resume_uuid(summary)
    if not allow:
        raise ServiceError(LAUNCH_DISABLED, "Launching is turned off (gui_allow_launch); copy the command and "
                           "run it yourself", status=403)
    if not launch_supported():
        raise ServiceError(LAUNCH_UNSUPPORTED, "Opening a Claude session needs a new console, which is only "
                           "available on Windows; copy the command and run it yourself", status=409)
    executable = find_claude()
    if not executable:
        raise ServiceError(CLAUDE_NOT_FOUND, "The 'claude' command was not found on PATH; install Claude Code or "
                           "add it to PATH, then try again", status=409)
    folder, folder_kind = working_folder()
    argv = [executable, *COMMAND_PREFIX[1:], uuid]
    reserve_launch()
    try:
        subprocess.Popen(argv, cwd=folder, env=launch_environment(), creationflags=CREATE_NEW_CONSOLE, shell=False)
    except (OSError, ValueError) as exc:
        raise ServiceError(LAUNCH_FAILED, "The 'claude' command could not be started", status=409) from exc
    return {"launched": True, "session_id": summary["id"], "command": [*COMMAND_PREFIX, uuid], "cwd": folder_kind}


def add_open_routes(api: APIRouter, sessions: SessionServices) -> None:
    def allow_launch(request: Request) -> bool:
        return bool(request.app.state.services.system.settings.gui_allow_launch)

    @api.get("/open-in-app")
    def open_in_app_options(request: Request):
        """What the screen needs to choose between the launch button and the command to copy."""
        return {"allow_launch": allow_launch(request) and launch_supported()}

    @api.post("/sessions/{session_id}/open")
    def open_session(session_id: str, request: Request):
        """Launch the Claude app on this session. The body is not read: the id in the path is only a key."""
        summary = sessions.get_session(session_id)  # 400 for a malformed id, 404 for an unknown one
        return launch_claude_desktop(summary, allow=allow_launch(request))
