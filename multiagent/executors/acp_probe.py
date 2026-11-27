# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""A bounded ACP initialize-only probe for an already installed Codex adapter."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Sequence

from . import process

CODEX_ACP_EXECUTABLE = "codex-acp"
PROTOCOL_VERSION = 1
PROBE_TIMEOUT_SECONDS = 8
PROBE_OUTPUT_CAP_BYTES = 16_384
_REQUEST = json.dumps({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}},
}, separators=(",", ":")) + "\n"
_SAFE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_ENV_KEEP = frozenset({
    "APPDATA", "COMSPEC", "HOMEDRIVE", "HOMEPATH", "HOME", "LOCALAPPDATA", "OS",
    "PATHEXT", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMROOT", "TEMP",
    "TMP", "USERPROFILE", "WINDIR",
})


def resolve_codex_acp(cwd: Path | str) -> str | None:
    """Find an installed adapter without letting a worktree or working folder shadow it."""
    try:
        folder = process.validate_cwd(cwd)
    except (process.ProcessLaunchError, OSError, ValueError):
        return None
    worktree = Path(__file__).resolve().parents[2]
    for entry in _safe_path_entries(folder):
        found = shutil.which(CODEX_ACP_EXECUTABLE, path=entry)
        if not found or any(process.is_within(found, root) for root in (folder, worktree)):
            continue
        try:
            return process.resolve_executable(found, folder)
        except (process.ProcessLaunchError, OSError, ValueError):
            continue
    return None


def _safe_path_entries(folder: Path) -> list[str]:
    worktree = Path(__file__).resolve().parents[2]
    result = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        if any(process.is_within(entry, root) for root in (folder, worktree)):
            continue
        result.append(entry)
    return result


def _clean_env_extra(folder: Path) -> dict[str, str]:
    """Clear inherited values other than the paths a locally installed adapter needs."""
    extra = {name: "" for name in os.environ
            if name.upper() not in _ENV_KEEP and not process.is_scrubbed_name(name)
            and name.upper() != process.NO_CURRENT_DIR_VARIABLE.upper()}
    extra["PATH"] = os.pathsep.join(_safe_path_entries(folder))
    return extra


def _bounded_value(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    """Keep only small JSON values from the process response; never pass through raw output."""
    if budget is None:
        budget = [128]
    budget[0] -= 1
    if budget[0] < 0:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value if -(2**31) <= value <= 2**31 - 1 else None
    if isinstance(value, str):
        return value[:256]
    if depth >= 3:
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:32]:
            if isinstance(key, str) and _SAFE_KEY.fullmatch(key):
                result[key] = _bounded_value(item, depth=depth + 1, budget=budget)
        return result
    if isinstance(value, list):
        return [_bounded_value(item, depth=depth + 1, budget=budget) for item in value[:16]]
    return None


def _invalid(reason: str) -> dict[str, Any]:
    return {"status": "invalid_response", "reason": reason}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def probe_codex_acp(executable: str | None, *, cwd: Path | str,
                    run: Callable[..., Any] | None = None, args: Sequence[str] = ()) -> dict[str, Any]:
    """Send exactly one fixed ACP initialize request and return bounded, parsed facts.

    ``args`` (EXA-T06) are the fixed, literal arguments of an executable the person declared; the default keeps Codex ACP as it was.
    """
    if executable is None:
        return {"status": "not_installed"}
    runner = run or process.run
    try:
        folder = process.validate_cwd(cwd)
        outcome = runner([executable, *args], cwd=folder, stdin_text=_REQUEST,
                         timeout_seconds=PROBE_TIMEOUT_SECONDS, output_cap_bytes=PROBE_OUTPUT_CAP_BYTES,
                         env_extra=_clean_env_extra(folder))
    except (process.ProcessLaunchError, OSError, ValueError):
        return {"status": "no_response", "reason": "launch_failed"}
    if outcome.cancelled:
        return {"status": "no_response", "reason": "cancelled"}
    if outcome.stdout_truncated or outcome.stderr_truncated:
        return _invalid("output_too_large")
    output = outcome.stdout_tail
    if not isinstance(output, str) or len(output.encode("utf-8", errors="replace")) > PROBE_OUTPUT_CAP_BYTES:
        return _invalid("output_too_large")
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return {"status": "no_response", "reason": "timeout" if outcome.timed_out else "empty_output"}
    if len(lines) != 1:
        return _invalid("unexpected_message_count")
    try:
        response = json.loads(lines[0], parse_constant=_reject_json_constant)
    except (ValueError, RecursionError):
        return _invalid("invalid_json")
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        return _invalid("invalid_jsonrpc")
    if type(response.get("id")) is not int or response["id"] != 1:
        return _invalid("unmatched_id")
    if "error" in response or not isinstance(response.get("result"), dict):
        return _invalid("invalid_result")
    result = response["result"]
    version = result.get("protocolVersion")
    if type(version) is not int or version < 1:
        return _invalid("invalid_protocol_version")
    if not isinstance(result.get("agentCapabilities"), dict):
        return _invalid("invalid_capabilities")
    auth_methods = result.get("authMethods", [])
    if not isinstance(auth_methods, list):
        return _invalid("invalid_auth_methods")
    if version != PROTOCOL_VERSION:
        return {"status": "incompatible", "protocol_version": version}
    safe_result = {
        "status": "compatible",
        "protocol_version": version,
        "capabilities": _bounded_value(result["agentCapabilities"]),
        "auth_methods": _bounded_value(auth_methods),
    }
    if isinstance(result.get("agentInfo"), dict):
        safe_result["agent_info"] = _bounded_value(result["agentInfo"])
    return safe_result
