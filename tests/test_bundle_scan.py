# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SKL-T01: detection, command and report reading with a fake signature checker and a fake runner.

Nothing here runs SkillSpector, a uv launcher or PowerShell, and nothing opens a socket.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from multiagent import bundle_scan
from multiagent.config import Settings


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to start a real process or open a socket")
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def make_settings(tmp_path: Path, **values) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="", openai_api_key="", **values)


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    path = tmp_path / "tool" / "Scripts" / "python.exe"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a real interpreter")
    return path


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    folder = tmp_path / "bundle"
    for name in ("a", "b"):
        (folder / "skills" / name).mkdir(parents=True)
        (folder / "skills" / name / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    return folder


@dataclass
class Outcome:
    exit_code: int | None = 0
    stdout_tail: str = ""
    stdout_truncated: bool = False
    timed_out: bool = False


class FakeRunner:
    def __init__(self, outcome: Outcome | Exception):
        self.outcome, self.calls = outcome, []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def report(verdict="SAFE", coverage=(100,), findings=(), version="2.12.0") -> str:
    return json.dumps({"version": version, "verdict": verdict, "findings": list(findings),
                       "skills": [{"path": f"skills/s{i}", "coverage_percent": value}
                                  for i, value in enumerate(coverage)]})


def ok(text: str) -> Outcome:
    return Outcome(stdout_tail=text)


# --------------------------------------------------------------------------- settings

def test_settings_defaults_and_validation(tmp_path):
    settings = make_settings(tmp_path)
    assert (settings.bundle_scan, settings.skillspector_python, settings.bundle_scan_timeout_seconds) == ("auto", "", 300.0)
    with pytest.raises(ValueError):
        make_settings(tmp_path, bundle_scan="sometimes")
    with pytest.raises(ValueError):
        make_settings(tmp_path, bundle_scan_timeout_seconds=0)


# --------------------------------------------------------------------------- detection

def test_default_path_comes_from_appdata():
    expected = Path("C:/Users/x/AppData/Roaming") / "uv" / "tools" / "skillspector" / "Scripts" / "python.exe"
    assert bundle_scan.default_python({"APPDATA": "C:/Users/x/AppData/Roaming"}) == expected
    assert bundle_scan.default_python({}) is None


def test_setting_wins_over_default(tmp_path, fake_python):
    appdata = tmp_path / "appdata"
    default = appdata / "uv" / "tools" / "skillspector" / "Scripts" / "python.exe"
    default.parent.mkdir(parents=True)
    default.write_bytes(b"x")
    seen = []
    found = bundle_scan.detect(make_settings(tmp_path, skillspector_python=str(fake_python)),
                               env={"APPDATA": str(appdata)}, verifier=lambda p: seen.append(p) or True)
    assert (found.state, found.python, seen) == ("ready", fake_python, [fake_python])
    found = bundle_scan.detect(make_settings(tmp_path), env={"APPDATA": str(appdata)}, verifier=lambda p: True)
    assert found.python == default


def test_missing_file_directory_or_relative_path_is_not_installed(tmp_path):
    settings = make_settings(tmp_path, skillspector_python=str(tmp_path / "nope" / "python.exe"))
    assert bundle_scan.detect(settings, verifier=lambda p: True).state == "not_installed"
    assert bundle_scan.detect(make_settings(tmp_path), env={}, verifier=lambda p: True).state == "not_installed"
    folder = tmp_path / "dir"
    folder.mkdir()
    assert bundle_scan.detect(make_settings(tmp_path, skillspector_python=str(folder)),
                              verifier=lambda p: True).reason == "interpreter_missing"
    assert bundle_scan.detect(make_settings(tmp_path, skillspector_python="python.exe"),
                              verifier=lambda p: True).reason == "interpreter_path_not_absolute"


def test_a_link_is_not_followed(tmp_path, fake_python):
    link = tmp_path / "link.exe"
    try:
        os.symlink(fake_python, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")
    calls = []
    found = bundle_scan.detect(make_settings(tmp_path, skillspector_python=str(link)),
                               verifier=lambda p: calls.append(p) or True)
    assert (found.state, found.reason, calls) == ("not_installed", "interpreter_is_a_link", [])


@pytest.mark.parametrize("answer", [False, None, "yes"])
def test_signature_that_is_not_valid_blocks(tmp_path, fake_python, answer):
    found = bundle_scan.detect(make_settings(tmp_path, skillspector_python=str(fake_python)),
                               verifier=lambda p: answer)
    assert (found.state, found.reason) == ("unsigned_interpreter", "signature_not_valid")


def test_a_signature_check_that_fails_blocks(tmp_path, fake_python):
    def broken(path):
        raise RuntimeError("no powershell")
    found = bundle_scan.detect(make_settings(tmp_path, skillspector_python=str(fake_python)), verifier=broken)
    assert found.state == "unsigned_interpreter" and found.reason.startswith("signature_check_failed")


@dataclass
class Done:
    returncode: int


def test_verify_signature_uses_powershell_with_the_psf_signer(tmp_path, monkeypatch, fake_python):
    monkeypatch.setattr(bundle_scan.sys, "platform", "win32")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Done(0)
    assert bundle_scan.verify_signature(fake_python, run=run, find=lambda name: "C:/ps/powershell.exe") is True
    argv, kwargs = calls[0]
    assert argv[0] == "C:/ps/powershell.exe" and "Get-AuthenticodeSignature" in argv[-1]
    assert "Python Software Foundation" in argv[-1] and "'Valid'" in argv[-1]
    assert kwargs["shell"] is False and kwargs["env"]["MAF_SKILLSPECTOR_VERIFY_PATH"] == str(fake_python)
    assert str(fake_python) not in argv[-1]
    assert bundle_scan.verify_signature(fake_python, run=lambda *a, **k: Done(1), find=lambda n: "ps") is False
    assert bundle_scan.verify_signature(fake_python, run=run, find=lambda n: None) is False


def test_verify_signature_is_false_when_it_cannot_be_checked(monkeypatch, fake_python):
    monkeypatch.setattr(bundle_scan.sys, "platform", "linux")
    assert bundle_scan.verify_signature(fake_python, run=lambda *a, **k: Done(0), find=lambda n: "ps") is False


# --------------------------------------------------------------------------- command

def test_exact_command(tmp_path, bundle):
    argv = bundle_scan.build_command("C:/t/python.exe", bundle, 2)
    assert argv == ["C:/t/python.exe", "-I", "-c", "from skillspector.cli_entry import app; app()", "scan",
                    str(bundle), "--no-llm", "--format", "json", "--recursive", "--max-skills", "2"]
    assert "--output" not in argv and "-o" not in argv
    assert not any(part.lower().endswith(("skillspector.exe", "uv.exe", "uvx.exe")) for part in argv)


def test_max_skills_covers_every_skill_folder(bundle, tmp_path):
    assert bundle_scan.count_skills(bundle) == 2
    assert bundle_scan.count_skills(tmp_path / "empty-nonexistent") == 1
    runner = FakeRunner(ok(report()))
    result = bundle_scan.run_scan("C:/t/python.exe", bundle, timeout_seconds=60, runner=runner)
    argv = runner.calls[0][0]
    assert argv[argv.index("--max-skills") + 1] == "2" and result.state == "safe"


def test_run_uses_a_temporary_working_folder_and_limits(bundle):
    runner = FakeRunner(ok(report()))
    bundle_scan.run_scan("C:/t/python.exe", bundle, timeout_seconds=42, runner=runner)
    kwargs = runner.calls[0][1]
    assert kwargs["timeout_seconds"] == 42 and kwargs["stdin_text"] is None
    assert kwargs["output_cap_bytes"] >= 1_000_000
    assert not Path(kwargs["cwd"]).exists() and Path(kwargs["cwd"]) != bundle


def test_default_runner_goes_through_the_process_executor(monkeypatch, bundle):
    seen = {}

    class Handle:
        def wait(self):
            return Outcome(stdout_tail=report())

    def start(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return Handle()
    monkeypatch.setattr(bundle_scan.process, "start", start)
    assert bundle_scan.run_scan("C:/t/python.exe", bundle, timeout_seconds=9).state == "safe"
    assert seen["argv"][1] == "-I" and seen["timeout_seconds"] == 9


def test_launch_errors_are_not_analyzed(bundle):
    error = bundle_scan.process.ProcessLaunchError("executable_not_found", "gone")
    result = bundle_scan.run_scan("C:/t/python.exe", bundle, timeout_seconds=5, runner=FakeRunner(error))
    assert (result.state, result.reason) == ("not_analyzed", "launch_failed: executable_not_found")
    assert bundle_scan.run_scan("C:/t/python.exe", bundle / "missing", timeout_seconds=5,
                                runner=FakeRunner(ok(report()))).reason == "invalid_folder"


# --------------------------------------------------------------------------- report

@pytest.mark.parametrize("verdict,state", [("SAFE", "safe"), ("CAUTION", "caution"), ("DO_NOT_INSTALL", "do_not_install")])
def test_three_verdicts(verdict, state):
    result = bundle_scan.parse_report(report(verdict, (100, 100)))
    assert (result.state, result.reason, result.tool_version) == (state, None, "2.12.0")
    assert result.coverage == {"skills/s0": 100.0, "skills/s1": 100.0}


def test_findings_are_read_and_sorted_by_level():
    findings = [{"severity": "low", "rule_id": "R2", "file": "a.md", "line": 3, "message": "minor"},
                {"level": "HIGH", "rule": "R1", "path": "b.py", "line": 9, "description": "bad"},
                {"level": "high"}, "junk"]
    result = bundle_scan.parse_report(report("CAUTION", findings=findings))
    assert [item["rule"] for item in result.findings] == ["R1", "R2"]
    assert result.findings[0] == {"level": "high", "rule": "R1", "file": "b.py", "line": 9, "message": "bad"}
    assert result.as_dict()["findings"][1]["level"] == "low"


def test_findings_are_capped():
    many = [{"level": "low", "rule": f"R{i}"} for i in range(50)]
    assert len(bundle_scan.parse_report(report("CAUTION", findings=many)).findings) == bundle_scan.MAX_FINDINGS


def test_zero_or_partial_coverage_is_not_analyzed():
    for coverage in ((0,), (100, 60)):
        result = bundle_scan.parse_report(report("SAFE", coverage))
        assert (result.state, result.reason) == ("not_analyzed", "coverage_below_100")


def test_missing_coverage_or_verdict_is_not_analyzed():
    assert bundle_scan.parse_report(json.dumps({"verdict": "SAFE"})).reason == "coverage_unknown"
    assert bundle_scan.parse_report(json.dumps({"verdict": "SAFE", "skills": [{"path": "a"}]})).reason == "coverage_unknown"
    assert bundle_scan.parse_report(report("MAYBE")).reason == "verdict_not_understood"
    assert bundle_scan.parse_report(json.dumps({"skills": []})).reason == "verdict_not_understood"


def test_broken_json_is_not_analyzed():
    for text in ("", "{not json", "[1, 2]", "null", None):
        assert bundle_scan.parse_report(text).state == "not_analyzed"
    assert bundle_scan.parse_report("{not json").reason == "report_not_json"


def test_json_after_progress_lines_is_read():
    assert bundle_scan.parse_report("scanning...\n" + report("CAUTION") + "\n").state == "caution"


def test_truncated_output_timeout_and_exit_code_are_not_analyzed(bundle):
    cases = [(Outcome(stdout_tail=report(), stdout_truncated=True), "output_truncated"),
             (Outcome(stdout_tail=report(), timed_out=True, exit_code=1), "timeout"),
             (Outcome(stdout_tail=report(), exit_code=2), "exit_code_2"),
             (Outcome(stdout_tail=report(), exit_code=None), "exit_code_None")]
    for outcome, reason in cases:
        result = bundle_scan.run_scan("C:/t/python.exe", bundle, timeout_seconds=5, runner=FakeRunner(outcome))
        assert (result.state, result.reason) == ("not_analyzed", reason)


# --------------------------------------------------------------------------- scan

def test_off_runs_nothing(tmp_path, bundle, fake_python):
    checked, runner = [], FakeRunner(ok(report()))
    result = bundle_scan.scan(bundle, osv_approved=True, runner=runner, verifier=lambda p: checked.append(p) or True,
                              settings=make_settings(tmp_path, bundle_scan="off", skillspector_python=str(fake_python)))
    assert (result.state, runner.calls, checked) == ("off", [], [])


def test_unsigned_or_missing_runs_nothing(tmp_path, bundle, fake_python):
    runner = FakeRunner(ok(report()))
    unsigned = bundle_scan.scan(bundle, osv_approved=True, runner=runner, verifier=lambda p: False,
                                settings=make_settings(tmp_path, skillspector_python=str(fake_python)))
    missing = bundle_scan.scan(bundle, osv_approved=True, runner=runner, verifier=lambda p: True, env={},
                               settings=make_settings(tmp_path))
    assert (unsigned.state, missing.state, runner.calls) == ("unsigned_interpreter", "not_installed", [])


@pytest.mark.parametrize("approved", [False, None, "yes", 1])
def test_without_osv_approval_nothing_runs(tmp_path, bundle, fake_python, approved):
    runner = FakeRunner(ok(report()))
    result = bundle_scan.scan(bundle, osv_approved=approved, runner=runner, verifier=lambda p: True,
                              settings=make_settings(tmp_path, skillspector_python=str(fake_python)))
    assert (result.state, result.reason, runner.calls) == ("not_analyzed", "osv_not_approved", [])


def test_approved_scan_end_to_end_with_fakes(tmp_path, bundle, fake_python):
    runner = FakeRunner(ok(report("CAUTION", findings=[{"level": "medium", "rule": "R7", "file": "x.md"}])))
    settings = make_settings(tmp_path, skillspector_python=str(fake_python), bundle_scan_timeout_seconds=77)
    result = bundle_scan.scan(bundle, osv_approved=True, settings=settings, runner=runner, verifier=lambda p: True)
    assert result.state == "caution" and result.findings[0]["rule"] == "R7"
    argv, kwargs = runner.calls[0]
    assert argv[0] == str(fake_python) and argv[1] == "-I" and kwargs["timeout_seconds"] == 77
    assert set(result.as_dict()) == {"state", "findings", "coverage", "tool_version", "reason"}
    assert result.as_dict()["state"] in bundle_scan.STATES
