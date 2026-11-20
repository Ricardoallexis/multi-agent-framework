# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Find SkillSpector, run it on a folder and read its verdict (SKL-T01).

This module has no network code of its own and knows nothing about registered bundles or approvals (SKL-T02).
It never looks for or launches ``skillspector.exe`` or any other uv launcher: only the ``python.exe`` of the tool
environment, and only when its Authenticode signature is valid and belongs to the Python Software Foundation
(Decisions 14 and 15). Without that signature nothing runs (``unsigned_interpreter``).

A local scan sends package names and versions to ``api.osv.dev`` and the tool cannot be told not to. So ``scan``
refuses to run unless the caller says the person approved it (``osv_approved=True``).

``scan(folder, osv_approved=..., settings=...)`` returns a ``ScanResult`` (``as_dict()`` for JSON) whose ``state`` is
one of ``STATES``. ``safe``, ``caution`` and ``do_not_install`` are the tool's verdict, and only when every folder was
fully covered; everything else is ``not_analyzed`` with a ``reason``, or ``not_installed``, ``unsigned_interpreter``
or ``off``. The pieces (``detect``, ``build_command``, ``run_scan``, ``parse_report``) can be used on their own, with
a fake signature checker and a fake runner in tests: no test runs the real tool.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .executors import process
from .executors.launch_safety import find_executable, launch_environment

OFF = "off"
NOT_INSTALLED = "not_installed"
UNSIGNED_INTERPRETER = "unsigned_interpreter"
NOT_ANALYZED = "not_analyzed"
SAFE = "safe"
CAUTION = "caution"
DO_NOT_INSTALL = "do_not_install"
STATES = (SAFE, CAUTION, DO_NOT_INSTALL, NOT_ANALYZED, NOT_INSTALLED, UNSIGNED_INTERPRETER, OFF)

VERDICTS = {"SAFE": SAFE, "CAUTION": CAUTION, "DO_NOT_INSTALL": DO_NOT_INSTALL}
SIGNER = "Python Software Foundation"
LAUNCH_CODE = "from skillspector.cli_entry import app; app()"
OUTPUT_CAP_BYTES = 20_000_000
MAX_FINDINGS = 20
SIGNATURE_TIMEOUT_SECONDS = 30
_LEVELS = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass(frozen=True)
class ScanResult:
    state: str
    findings: tuple[dict, ...] = ()
    coverage: dict[str, float] = field(default_factory=dict)
    tool_version: str | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "findings": [dict(item) for item in self.findings],
                "coverage": dict(self.coverage), "tool_version": self.tool_version, "reason": self.reason}


@dataclass(frozen=True)
class Detection:
    state: str                      # ``ready``, ``not_installed`` or ``unsigned_interpreter``
    python: Path | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- detection

def default_python(env: Mapping[str, str] | None = None) -> Path | None:
    """``%APPDATA%\\uv\\tools\\skillspector\\Scripts\\python.exe``, or None when APPDATA is not set."""
    appdata = (os.environ if env is None else env).get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "uv" / "tools" / "skillspector" / "Scripts" / "python.exe"


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def verify_signature(path: Path, *, run: Callable[..., Any] = subprocess.run,
                     find: Callable[[str], str | None] = find_executable) -> bool:
    """True when ``Get-AuthenticodeSignature`` says Valid and the signer is the Python Software Foundation.

    Same pattern as ``setup/guided.py``. False when it cannot be checked (not Windows, no PowerShell).
    """
    if sys.platform != "win32":
        return False
    powershell = find("powershell")
    if not powershell:
        return False
    env = launch_environment()
    env["MAF_SKILLSPECTOR_VERIFY_PATH"] = str(path)
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:MAF_SKILLSPECTOR_VERIFY_PATH; "
              f"if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'O={SIGNER}(,|$)') "
              "{ exit 0 } else { exit 1 }")
    done = run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], cwd=str(Path.home()), env=env,
               shell=False, check=False, timeout=SIGNATURE_TIMEOUT_SECONDS, capture_output=True)
    return done.returncode == 0


def detect(settings: Any = None, *, env: Mapping[str, str] | None = None,
           verifier: Callable[[Path], bool] | None = None) -> Detection:
    """Find the tool's ``python.exe`` (the setting, else the uv default) and check it, without running anything."""
    configured = str(getattr(settings, "skillspector_python", "") or "").strip()
    candidate = Path(configured) if configured else default_python(env)
    if candidate is None:
        return Detection(NOT_INSTALLED, None, "no_interpreter_path")
    if not candidate.is_absolute():
        return Detection(NOT_INSTALLED, candidate, "interpreter_path_not_absolute")
    if _is_link(candidate):
        return Detection(NOT_INSTALLED, candidate, "interpreter_is_a_link")
    if not candidate.is_file():
        return Detection(NOT_INSTALLED, candidate, "interpreter_missing")
    try:
        valid = (verifier or verify_signature)(candidate)
    except Exception as exc:  # noqa: BLE001 - an unverifiable interpreter is never run
        return Detection(UNSIGNED_INTERPRETER, candidate, f"signature_check_failed: {type(exc).__name__}")
    if valid is not True:
        return Detection(UNSIGNED_INTERPRETER, candidate, "signature_not_valid")
    return Detection("ready", candidate, None)


# --------------------------------------------------------------------------- command

def count_skills(folder: Path) -> int:
    """How many ``SKILL.md`` files the folder holds (at least 1): the value for ``--max-skills``."""
    total = sum(1 for _root, _dirs, files in os.walk(folder) for name in files if name.upper() == "SKILL.MD")
    return max(total, 1)


def build_command(python: Path | str, folder: Path | str, max_skills: int) -> list[str]:
    """The exact argv. Isolated mode, no model (``--no-llm``), JSON on stdout, never ``--output``."""
    return [str(python), "-I", "-c", LAUNCH_CODE, "scan", str(folder), "--no-llm", "--format", "json",
            "--recursive", "--max-skills", str(int(max_skills))]


# --------------------------------------------------------------------------- report

def _text(value: Any, limit: int = 200) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def _pick(mapping: Mapping, *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _finding(raw: Any) -> dict | None:
    if not isinstance(raw, Mapping):
        return None
    level = _text(_pick(raw, "level", "severity"), 20)
    rule = _text(_pick(raw, "rule", "rule_id", "id", "check"), 80)
    if not level or not rule:
        return None
    line = _pick(raw, "line", "line_number")
    return {"level": level.lower(), "rule": rule,
            "file": _text(_pick(raw, "file", "path", "filename"), 260),
            "line": line if isinstance(line, int) and not isinstance(line, bool) else None,
            "message": _text(_pick(raw, "message", "description", "title"))}


def parse_report(text: str) -> ScanResult:
    """Read the JSON a scan printed. Anything it does not understand is ``not_analyzed`` with a reason."""
    text = text if isinstance(text, str) else ""
    try:
        report = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        try:
            report = json.loads(text[start:end + 1]) if 0 <= start < end else None
        except ValueError:
            report = None
    if not isinstance(report, Mapping):
        return ScanResult(NOT_ANALYZED, reason="report_not_json")
    version = _text(_pick(report, "tool_version", "version", "skillspector_version"), 40)
    skills = _pick(report, "skills", "results")
    skills = [item for item in skills if isinstance(item, Mapping)] if isinstance(skills, list) else []
    top_findings = _pick(report, "findings")
    raw_findings = list(top_findings) if isinstance(top_findings, list) else []
    coverage: dict[str, float] = {}
    for index, item in enumerate(skills):
        nested = item.get("findings")
        raw_findings.extend(nested if isinstance(nested, list) else [])
        name = _text(_pick(item, "path", "name", "folder", "skill"), 260) or f"skill-{index + 1}"
        percent = _number(_pick(item, "coverage_percent", "coverage"))
        coverage[name] = percent if percent is not None else -1.0
    top = _number(_pick(report, "coverage_percent"))
    if not skills and top is not None:
        coverage["."] = top
    findings = [item for item in map(_finding, raw_findings) if item]
    findings.sort(key=lambda item: _LEVELS.get(item["level"], len(_LEVELS)))
    findings = tuple(findings[:MAX_FINDINGS])
    verdict_text = _text(_pick(report, "verdict", "overall_verdict", "recommendation"), 40)
    state = VERDICTS.get((verdict_text or "").upper().replace(" ", "_").replace("-", "_"))
    if state is None:
        return ScanResult(NOT_ANALYZED, findings, coverage, version, "verdict_not_understood")
    if not coverage or any(value < 0 for value in coverage.values()):
        return ScanResult(NOT_ANALYZED, findings, coverage, version, "coverage_unknown")
    if any(value < 100 for value in coverage.values()):
        return ScanResult(NOT_ANALYZED, findings, coverage, version, "coverage_below_100")
    return ScanResult(state, findings, coverage, version, None)


# --------------------------------------------------------------------------- run

def _start_and_wait(argv: list[str], **kwargs: Any) -> Any:
    return process.start(argv, **kwargs).wait()


def run_scan(python: Path | str, folder: Path | str, *, timeout_seconds: float, max_skills: int | None = None,
             runner: Callable[..., Any] | None = None) -> ScanResult:
    """Run the scan with ``python`` (already checked) in a temporary working folder and read its stdout.

    ``runner(argv, cwd=, stdin_text=, timeout_seconds=, output_cap_bytes=)`` returns an object with ``exit_code``,
    ``stdout_tail``, ``stdout_truncated`` and ``timed_out``; the default starts the program with
    ``executors.process`` (no shell, scrubbed environment, the whole process tree killed on timeout).
    """
    target = Path(folder)
    if not target.is_absolute() or not target.is_dir():
        return ScanResult(NOT_ANALYZED, reason="invalid_folder")
    count = max_skills if max_skills is not None else count_skills(target)
    argv = build_command(python, target, count)
    with tempfile.TemporaryDirectory(prefix="maf-bundle-scan-") as work:
        try:
            outcome = (runner or _start_and_wait)(argv, cwd=Path(work), stdin_text=None,
                                                  timeout_seconds=timeout_seconds, output_cap_bytes=OUTPUT_CAP_BYTES)
        except process.ProcessLaunchError as exc:
            return ScanResult(NOT_ANALYZED, reason=f"launch_failed: {exc.code}")
        except OSError as exc:
            return ScanResult(NOT_ANALYZED, reason=f"launch_failed: {type(exc).__name__}")
    if outcome.timed_out:
        return ScanResult(NOT_ANALYZED, reason="timeout")
    if outcome.exit_code != 0:
        return ScanResult(NOT_ANALYZED, reason=f"exit_code_{outcome.exit_code}")
    if outcome.stdout_truncated:
        return ScanResult(NOT_ANALYZED, reason="output_truncated")
    return parse_report(outcome.stdout_tail)


def scan(folder: Path | str, *, osv_approved: bool, settings: Any = None, env: Mapping[str, str] | None = None,
         verifier: Callable[[Path], bool] | None = None, runner: Callable[..., Any] | None = None,
         max_skills: int | None = None) -> ScanResult:
    """Detect, check the signature and scan ``folder``. Runs nothing when off, missing, unsigned or not approved."""
    if str(getattr(settings, "bundle_scan", "auto")) == "off":
        return ScanResult(OFF, reason="bundle_scan_off")
    found = detect(settings, env=env, verifier=verifier)
    if found.state != "ready":
        return ScanResult(found.state, reason=found.reason)
    if osv_approved is not True:
        return ScanResult(NOT_ANALYZED, reason="osv_not_approved")
    timeout = float(getattr(settings, "bundle_scan_timeout_seconds", 300))
    return run_scan(found.python, folder, timeout_seconds=timeout, max_skills=max_skills, runner=runner)
