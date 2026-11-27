# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Open a visible Windows console for a fixed install or sign-in step.

The parent only opens and waits for the console. The console shows the official
command and waits for the person before running it. No credential output is
captured or returned to the parent.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..config import LOCAL_ROOT
from ..document_actions import Action, ActionKind
from ..executors import launch_safety
from .detect import CONTEXTS, detect

SOURCE_DATE = "2026-11-19"
CREATE_NEW_CONSOLE = 0x00000010
_console_lock = threading.Lock()
_state_lock = threading.Lock()
_console_state: dict | None = None
_console_thread: threading.Thread | None = None
STEPS = {
    "claude": {
        "install": (("winget", "install", "Anthropic.ClaudeCode"),
                    "https://support.claude.com/en/articles/14554922-claude-code-user-faq"),
        "login": (("claude", "auth", "login"), "https://code.claude.com/docs/en/claude-code-on-the-web"),
    },
    "codex": {
        "install": (("npm", "install", "-g", "@openai/codex"),
                    "https://github.com/openai/codex/blob/main/README.md"),
        "login": (("codex", "login"), "https://github.com/openai/codex/blob/main/README.md"),
    },
    "copilot": {
        "install": (("winget", "install", "GitHub.Copilot"),
                    "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli"),
        "login": (("copilot", "login"),
                  "https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli"),
    },
}


# EXA-T05: only four npm failures are explained, each by stable markers of npm itself. Any other text is shown as a technical
# detail and never interpreted. The order matters: a missing package is told apart before the network families.
NPM_FAILURES: tuple[tuple[str, str, re.Pattern], ...] = (
    ("npm_missing", "npm is not installed or not on PATH. Install Node.js, which includes npm, and try again.",
     re.compile(r"npm['\"]? is not recognized|npm: (?:command )?not found|spawn npm ENOENT", re.I)),
    ("permissions", "npm was not allowed to write where it installs global packages. Check the folder permissions.",
     re.compile(r"\bEACCES\b|\bEPERM\b|permission denied|operation not permitted", re.I)),
    ("package_not_found", "npm could not find the package. Check the package name in the source shown above.",
     re.compile(r"\bE404\b|404 Not Found|No match found for version", re.I)),
    ("network", "npm could not reach the registry. Check the network, the proxy and the certificates.",
     re.compile(r"\bENOTFOUND\b|\bECONNREFUSED\b|\bECONNRESET\b|\bETIMEDOUT\b|\bEAI_AGAIN\b|getaddrinfo|"
                r"\bproxy\b|SELF_SIGNED_CERT|UNABLE_TO_VERIFY_LEAF_SIGNATURE|CERT_[A-Z_]+|ERR_SOCKET_TIMEOUT", re.I)),
)
NPM_DETAIL_LIMIT = 1000


def explain_npm_failure(text: str | None) -> dict:
    """``{family, message, detail}`` for the output of a failed npm command.

    ``family`` is one of ``npm_missing``, ``permissions``, ``package_not_found`` and ``network``; any other text is
    ``technical_detail`` with no message: it is shown as it is, never interpreted.
    """
    detail = (text or "").strip()[-NPM_DETAIL_LIMIT:]
    for family, message, pattern in NPM_FAILURES:
        if pattern.search(detail):
            return {"family": family, "message": message, "detail": detail}
    return {"family": "technical_detail", "message": None, "detail": detail}


def _print_npm_failure(text: str | None) -> None:
    explained = explain_npm_failure(text)
    if explained["message"]:
        print(explained["message"], flush=True)
    if explained["detail"]:
        print("Technical detail:\n" + explained["detail"], flush=True)


def step_plan(app: str, step: str) -> dict:
    """Return only dated, fixed commands; there is no caller supplied command."""
    if app == "agy" and step == "login":
        return {"app": app, "step": step, "command": None,
                "message": "Sign in to agy yourself; its session cannot be checked without spending credits."}
    if app not in STEPS or step not in {"install", "login"}:
        raise ValueError("Unsupported app or setup step")
    command, source = STEPS[app][step]
    return {"app": app, "step": step, "command": list(command),
            "source": source, "source_date": SOURCE_DATE}


def console_step(app: str, step: str, *, wait=input, run=subprocess.run,
                 find=launch_safety.find_executable) -> int:
    """Run only inside the new console, after the person's Enter key."""
    plan = step_plan(app, step)
    if plan["command"] is None:
        print(plan["message"], flush=True)
        return 0
    command = plan["command"]
    print(f"{app} · {step}\nSource ({SOURCE_DATE}): {plan['source']}", flush=True)
    print("Command: " + " ".join(command), flush=True)
    wait("Press Enter to run this command here, or close this window to cancel... ")
    executable = find(command[0])
    if not executable:
        if command[0] == "npm":
            print(NPM_FAILURES[0][1], flush=True)
        else:
            print(f"{command[0]} is not on PATH. Install the prerequisite and try again.", flush=True)
        return 1
    # These sources are winget/npm packages and official CLI commands, never
    # remote scripts. A script source would require a pinned SHA-256 first.
    # npm keeps its errors on stderr: they are read here so a failure can be explained, then printed as they were.
    captured = {"stderr": subprocess.PIPE, "text": True, "errors": "replace"} if command[0] == "npm" else {}
    result = run([executable, *command[1:]], cwd=str(Path.home()),
                 env=launch_safety.launch_environment(), shell=False, check=False, **captured)
    exit_code = result.returncode
    if captured:
        errors = getattr(result, "stderr", None)
        if exit_code != 0:
            _print_npm_failure(errors)
        elif errors:
            print(errors, flush=True)
    if app == "claude" and step == "install" and exit_code == 0:
        claude = find("claude")
        powershell = find("powershell")
        if claude and powershell:
            env = launch_safety.launch_environment()
            env["MAF_CLAUDE_VERIFY_PATH"] = claude
            script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:MAF_CLAUDE_VERIFY_PATH; "
                      "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'Anthropic, PBC') "
                      "{ exit 0 } else { exit 1 }")
            verified = run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                           cwd=str(Path.home()), env=env, shell=False, check=False)
            exit_code = verified.returncode
        else:
            exit_code = 1
        print("Claude publisher signature verified." if exit_code == 0 else
              "Claude publisher signature could not be verified. Check the official installer before use.", flush=True)
    print(f"Command finished (exit {exit_code}). Return to the app to recheck.", flush=True)
    wait("Press Enter to close this window... ")
    return exit_code


def guide_step(app: str, step: str, *, launcher=subprocess.Popen,
               detector=detect, system: str | None = None, policy: Any = None,
               evidence: Any = None, context: str = "packaged_process",
               background: bool = False) -> dict:
    """Open a console and remeasure with INS-T02 once the person closes it.

    By default this waits for the close, as the CLI expects. With
    ``background=True`` it returns as soon as the console is open, with a
    ``console_id``; a thread waits, remeasures and records the outcome, which
    ``console_status`` reports. The one-console lock is held until that thread ends.
    """
    plan = step_plan(app, step)
    if plan["command"] is None:
        return {"plan": plan, "recheck": None}
    if (system or sys.platform) not in {"Windows", "win32"}:
        raise RuntimeError("A visible setup console is available only on Windows")
    if context not in CONTEXTS:
        raise ValueError("Unknown detection context")
    launch_safety.require_effect(Action(ActionKind.OPEN_APPLICATION, f"setup:{app}:{step}"),
                                 evidence, policy=policy)
    if not _console_lock.acquire(blocking=False):
        raise RuntimeError("Another setup console is already open")
    handed_over = False
    try:
        if not background:
            return _open_and_recheck(plan, launcher=launcher, detector=detector, context=context)
        console_id = uuid4().hex
        _set_state({"console_id": console_id, "app": app, "step": step, "state": "open",
                    "console_exit_code": None, "recheck": None, "reason": None})
        try:
            handle, child = _open_console(plan, launcher)
        except Exception:
            _set_state({"state": "failed", "reason": "launch_failed"})
            raise
        global _console_thread
        _console_thread = threading.Thread(
            target=_wait_in_background, args=(plan, handle, child, detector, context, console_id),
            name="setup-console-wait", daemon=True)
        handed_over = True
        _console_thread.start()
        return {"plan": plan, "console_id": console_id, "state": "open"}
    finally:
        if not handed_over:
            _console_lock.release()


def console_status() -> dict:
    """State of the latest GUI console: ``none``, ``open``, ``closed`` or ``failed``."""
    with _state_lock:
        return dict(_console_state) if _console_state else {"state": "none"}


def _set_state(values: dict) -> None:
    global _console_state
    with _state_lock:
        _console_state = {**(_console_state or {}), **values} if "console_id" not in values else dict(values)


def _open_console(plan: dict, launcher):
    """Write the before-action evidence and open the console; returns the open file and the child."""
    app, step = plan["app"], plan["step"]
    folder = Path(os.environ.get("LOCAL_DIR") or LOCAL_ROOT).resolve() / "setup-evidence"
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or folder.is_junction():
        raise ValueError("The local setup evidence directory must be a real directory")
    destination = folder / (uuid4().hex + ".jsonl")
    # Use the current package location, even when the CLI was started from a
    # source checkout. The child works from home, away from project shims.
    package_root = str(Path(__file__).resolve().parents[2])
    env = launch_safety.launch_environment()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (package_root, env.get("PYTHONPATH"))))
    handle = destination.open("x", encoding="utf-8")
    try:
        handle.write(json.dumps({"before_action": plan}, ensure_ascii=False) + "\n")
        handle.flush()
        child = launcher([sys.executable, "-m", "multiagent.setup.guided", "--console", app, step],
                         cwd=str(Path.home()), env=env, shell=False,
                         creationflags=CREATE_NEW_CONSOLE)
    except BaseException:
        handle.close()
        raise
    return handle, child


def _wait_and_recheck(plan: dict, handle, child, detector, context: str) -> dict:
    with handle:
        exit_code = child.wait()
        report = detector(context=context, include_antigravity=False)
        handle.write(json.dumps({"console_exit_code": exit_code, "recheck": report}, ensure_ascii=False) + "\n")
        handle.flush()
    return {"plan": plan, "console_exit_code": exit_code, "recheck": report}


def _open_and_recheck(plan: dict, *, launcher, detector, context: str) -> dict:
    handle, child = _open_console(plan, launcher)
    return _wait_and_recheck(plan, handle, child, detector, context)


def _wait_in_background(plan: dict, handle, child, detector, context: str, console_id: str) -> None:
    values = {"state": "failed", "reason": "interrupted"}
    try:
        result = _wait_and_recheck(plan, handle, child, detector, context)
        values = {"state": "closed", "console_exit_code": result["console_exit_code"],
                  "recheck": result["recheck"]}
    except Exception as exc:  # the person's console is gone either way; report why the recheck failed
        values = {"state": "failed", "reason": type(exc).__name__}
    finally:
        with _state_lock:
            current = _console_state.get("console_id") if _console_state else None
        if current == console_id:
            _set_state(values)
        _console_lock.release()


def _main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "--console":
        raise ValueError("Console helper accepts only a fixed app and step")
    return console_step(argv[1], argv[2])


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
