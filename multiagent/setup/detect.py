# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INS detection using only fixed version/status commands from the approved contract.

Output is an allowlist of parsed facts. In particular, command output, account names,
authentication methods containing keys, and exception text never enter the report.
The existing catalog has capability evidence, but no CLI version compatibility
evidence: descriptor/adapter versions must not be treated as CLI versions.
"""
from __future__ import annotations

import json
import platform
import re
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..executors import process

CONTRACT_DATE = "2026-10-29"
CONTEXTS = ("user_console", "packaged_process")
APPS = ("claude", "codex", "copilot")
COMMANDS = {
    "claude": (("--version",), ("auth", "status")),
    "codex": (("--version",), ("login", "status")),
    "copilot": (("--version",), None),
    "agy": (("--version",), None),
}
VERSION = re.compile(r"(?<![\w.])v?(\d{1,6}\.\d{1,6}\.\d{1,6}(?:-(?:alpha|beta|rc|preview|dev)(?:[.-]?\d{1,6})?)?)(?!\w|\.\d)")


class _Steps:
    """Publish a plan before an action and only parsed facts afterwards."""

    def __init__(self, context, before, after):
        self.context, self.before, self.after = context, before, after

    def run(self, app, action, command, purpose, operation):
        plan = {"app": app, "action": action, "command": command, "purpose": purpose,
                "source": "INS-T01", "source_date": CONTRACT_DATE, "context": self.context,
                "human_acceptance": "not_required_read_only"}
        if self.before:
            self.before(dict(plan))
        result = operation()
        if self.after:
            self.after({**plan, "observed_at": datetime.now(timezone.utc).isoformat(), "result": result})
        return result


def _probe(executable: str, arguments: tuple[str, ...], cwd: Path) -> Any:
    # Existing process layer: validated absolute program outside cwd, clean
    # environment, no shell, bounded output/time and process-tree cleanup.
    return process.run([executable, *arguments], cwd=cwd, stdin_text=None,
                       timeout_seconds=8, output_cap_bytes=16384)


def _usable(outcome: Any) -> bool:
    return not (outcome.timed_out or outcome.cancelled or
                outcome.stdout_truncated or outcome.stderr_truncated)


def _version(executable: str, cwd: Path) -> tuple[str | None, str]:
    try:
        outcome = _probe(executable, ("--version",), cwd)
    except (process.ProcessLaunchError, OSError, ValueError):
        return None, "probe_failed"
    if outcome.timed_out:
        return None, "timed_out"
    if not _usable(outcome) or outcome.exit_code != 0:
        return None, "probe_failed"
    matches = VERSION.findall(outcome.stdout_tail)
    return (matches[0], "observed") if len(matches) == 1 else (None, "uninterpretable")


def _version_fact(executable: str, cwd: Path) -> dict[str, Any]:
    version, reason = _version(executable, cwd)
    return {"version": version, "version_result": reason,
            "next_step": None if version else "Check the version in the application's official console."}


def _session(app: str, executable: str, cwd: Path) -> dict[str, Any]:
    arguments = COMMANDS[app][1]
    unknown = {"state": "unknown", "message": "cannot be checked",
               "command": list(arguments) if arguments else None,
               "next_step": "Check the session in the application's official console."}
    if app == "copilot":
        return {**unknown, "message": "cannot be checked without spending credits"}
    if arguments is None:
        return unknown
    try:
        outcome = _probe(executable, arguments, cwd)
        if not _usable(outcome):
            return unknown
        if app == "claude":
            payload = json.loads(outcome.stdout_tail)
            # Require agreement with both documented signals; never infer from
            # a credential file, a method name or an account identifier.
            value = payload.get("loggedIn") if isinstance(payload, dict) else None
            state = ("signed_in" if value is True and outcome.exit_code == 0 else
                     "signed_out" if value is False and outcome.exit_code == 1 else "unknown")
        else:
            # Exit code is not documented for Codex. Match only known status
            # messages; discard the rest (API-key status can include a key).
            lines = (outcome.stdout_tail + "\n" + outcome.stderr_tail).splitlines()
            active = any(re.fullmatch(r"Logged in using (?:ChatGPT|an API key(?: - .+)?)", line.strip())
                         for line in lines)
            inactive = any(line.strip() == "Not logged in" for line in lines)
            state = "signed_in" if active and not inactive else "signed_out" if inactive and not active else "unknown"
        if state == "unknown":
            return unknown
        return {"state": state, "message": "signed in" if state == "signed_in" else "not signed in in this measurement environment",
                "command": list(arguments),
                "next_step": None if state == "signed_in" else unknown["next_step"],
                "measured_with": "without CLAUDE*/ANTHROPIC* variables" if app == "claude" else None}
    except (process.ProcessLaunchError, OSError, ValueError, RecursionError):
        return unknown


def _app(app: str, cwd: Path, declared_path: str | None, steps: _Steps) -> dict[str, Any]:
    result: dict[str, Any] = {
        "app": app, "detection": "not_detected", "origin": "declared_path" if declared_path else "PATH",
        "version": None, "version_status": "version not checked",
        "version_command": [app, "--version"], "compatibility": "compatibility not declared",
        "session": {"state": "unknown", "message": "cannot be checked"},
        "next_step": "Check PATH from the user's console.",
    }
    if app == "agy":
        result["informational_only"] = True
        result["warning"] = "Review the limited-use notice in the Antigravity FAQ; this does not enable an executor."
    try:
        if declared_path and not Path(declared_path).is_absolute():
            raise ValueError("absolute path required")
        if declared_path and app == "claude" and Path(declared_path).name.lower() not in {"claude", "claude.exe", "claude.cmd"}:
            raise ValueError("Claude executable name required")
        executable = process.resolve_executable(declared_path or app, cwd)
    except (process.ProcessLaunchError, OSError, ValueError):
        if declared_path:
            result.update(detection="invalid_declared_path", next_step="Declare an existing absolute path outside the working directory.")
        elif app == "claude":
            result["next_step"] = "The app-managed installation could not be checked; declare its absolute path."
        return result
    observation = steps.run(app, "version", [app, "--version"], "Check the version without modifying the app.",
                            lambda: _version_fact(executable, cwd))
    version, reason = observation["version"], observation["version_result"]
    arguments = COMMANDS[app][1]
    session = ({"state": "unknown", "message": "cannot be checked",
                "next_step": "Check the session in the application's official console."}
               if reason == "timed_out" else
               steps.run(app, "session", [app, *arguments] if arguments else None,
                         "Check the session with the fixed command; without a command, report without probing.",
                         lambda: _session(app, executable, cwd)))
    result.update(detection="detected", version=version,
                  version_status="checked" if version and app in {"claude", "codex"} else "version not checked",
                  version_result=reason, session=session,
                  next_step=None if version else "Check the version in the application's official console.")
    return result


def _requirements(cwd: Path, steps: _Steps) -> list[dict[str, Any]]:
    def check(name, minimum):
        requirement: dict[str, Any] = {"name": name, "minimum_major": minimum, "state": "missing",
                                      "next_step": f"Install {name} or make it available on PATH; it is not installed automatically."}
        try:
            executable = process.resolve_executable(name, cwd)
        except (process.ProcessLaunchError, OSError, ValueError):
            executable = None
        if executable is not None:
            requirement["state"] = "present"
            requirement["next_step"] = None
            if minimum is not None:
                observation = steps.run(name, "version", [name, "--version"], "Check the requirement version.",
                                        lambda: _version_fact(executable, cwd))
                version = observation["version"]
                requirement["version"] = version
                requirement["state"] = "unknown" if version is None else "satisfied" if int(version.split(".")[0]) >= minimum else "too_old"
                if requirement["state"] != "satisfied":
                    requirement["next_step"] = f"Check or install {name} >= {minimum} in the user's console."
        requirement["applies_to"] = "npm installations" if name in {"node", "npm"} else "Copilot CLI" if name == "pwsh" else "winget installations"
        return requirement

    return [steps.run(name, "requirement", None, "Check for presence and the minimum requirement without installing.",
                      lambda name=name, minimum=minimum: check(name, minimum))
            for name, minimum in (("node", 22), ("npm", None), ("pwsh", 6), ("winget", None))]


def detect(*, cwd: Path | str | None = None, context: str = "user_console",
           claude_path: str | None = None, include_antigravity: bool = False,
           system: str | None = None, before_step: Callable | None = None,
           after_step: Callable | None = None) -> dict[str, Any]:
    """Measure one explicitly identified context; never claim to have measured another.

    No credential store, account usage reader, catalog writer or executor-enabling
    action is called. Returned evidence can be consumed by a local GUI without
    exposing subprocess output. A version observation is not a readiness verdict.
    """
    if context not in CONTEXTS:
        raise ValueError("context must be user_console or packaged_process")
    folder = process.validate_cwd(Path.cwd() if cwd is None else cwd)
    operating_system = platform.system() if system is None else system
    contexts: dict[str, Any] = {name: {"state": "not_measured"} for name in CONTEXTS}
    report = {"source": "INS-T01", "source_date": CONTRACT_DATE,
              "observed_at": datetime.now(timezone.utc).isoformat(), "platform": operating_system,
              "reference_context": "user_console", "contexts": contexts}
    steps = _Steps(context, before_step, after_step)
    if operating_system != "Windows":
        contexts[context] = steps.run("platform", "detection", None, "Check whether a verified source exists for the system.",
            lambda: {"state": "unsupported", "message": "no verified source",
                     "apps": [], "requirements": [],
                     "next_step": "Use a Windows console; no verified source is available for other platforms."})
        return report
    apps = (*APPS, "agy") if include_antigravity else APPS
    contexts[context] = {"state": "measured", "apps": [
        steps.run(app, "detection", None, "Resolve PATH or the declared path; do not open credential stores.",
                  lambda app=app: _app(app, folder, claude_path if app == "claude" else None, steps))
        for app in apps], "requirements": _requirements(folder, steps)}
    return report
