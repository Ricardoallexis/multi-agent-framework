"""INS-T02: synthetic executables/status only; no real agent CLI is launched."""
from __future__ import annotations

import builtins
import importlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from multiagent import cli
from multiagent.executors import process

detection = importlib.import_module("multiagent.setup.detect")


def outcome(text="1.2.3", *, code=0, stderr="", **kw):
    values = dict(stdout_tail=text, stderr_tail=stderr, exit_code=code, timed_out=False,
                  cancelled=False, stdout_truncated=False, stderr_truncated=False)
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.fixture
def probes(monkeypatch, tmp_path):
    calls = []
    binaries = tmp_path / "bin"
    binaries.mkdir()
    cwd = tmp_path / "work"
    cwd.mkdir()
    responses = {}

    def resolve(name, folder):
        if name not in detection.COMMANDS and name not in {"node", "npm", "pwsh", "winget"}:
            raise process.ProcessLaunchError("missing", "synthetic failure")
        return str(binaries / name)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        key = (Path(argv[0]).name, *argv[1:])
        if key in responses:
            value = responses[key]
            if isinstance(value, Exception):
                raise value
            return value
        if key[1:] == ("--version",):
            return outcome("22.1.0" if key[0] == "node" else "7.4.1" if key[0] == "pwsh" else "1.2.3")
        return outcome('{"loggedIn": true, "email": "person@example.invalid", "token": "SYNTHETIC_SECRET"}') if key[0] == "claude" else outcome("", stderr="Logged in using an API key - SYNTHETIC_SECRET")

    monkeypatch.setattr(process, "resolve_executable", resolve)
    monkeypatch.setattr(process, "run", run)
    return cwd, calls, responses


def measure(probes, **kw):
    return detection.detect(cwd=probes[0], system="Windows", **kw)


def apps(report, context="user_console"):
    return {item["app"]: item for item in report["contexts"][context]["apps"]}


def test_fixed_commands_no_raw_output_or_readiness(probes):
    report = measure(probes)
    found = apps(report)
    assert set(found) == {"claude", "codex", "copilot"}
    assert all(row["detection"] == "detected" for row in found.values())
    assert found["claude"]["session"]["state"] == "signed_in"
    assert found["codex"]["session"]["state"] == "signed_in"
    assert found["copilot"]["session"]["message"] == "no se puede comprobar sin gastar"
    assert found["copilot"]["version_status"] == "versión no comprobada"
    assert all(row["compatibility"] == "compatibilidad no declarada" for row in found.values())
    text = json.dumps(report)
    assert "SYNTHETIC_SECRET" not in text and "person@example.invalid" not in text
    assert "ready" not in text and "enabled" not in text
    called = [(Path(argv[0]).name, *argv[1:]) for argv, _ in probes[1]]
    assert set(called) == {("claude", "--version"), ("codex", "--version"), ("copilot", "--version"),
                           ("claude", "auth", "status"), ("codex", "login", "status"),
                           ("node", "--version"), ("pwsh", "--version")}
    for _, kwargs in probes[1]:
        assert kwargs["stdin_text"] is None and kwargs["timeout_seconds"] == 8
        assert kwargs["output_cap_bytes"] == 16384


@pytest.mark.parametrize("app,reply,state", [
    ("claude", outcome('{"loggedIn": false}', code=1), "signed_out"),
    ("claude", outcome('{"loggedIn": true}', code=1), "unknown"),
    ("claude", outcome("[" * 15000), "unknown"),
    ("claude", outcome('{"loggedIn": false}'), "unknown"),
    ("claude", outcome('{"loggedIn": "true"}'), "unknown"),
    ("claude", outcome('[]'), "unknown"),
    ("claude", outcome('not json'), "unknown"),
    ("claude", outcome('{"loggedIn": true}', stdout_truncated=True), "unknown"),
    ("codex", outcome("Not logged in", code=17), "signed_out"),
    ("codex", outcome("Logged in using ChatGPT", code=17), "signed_in"),
    ("codex", outcome("Logged in using ChatGPT\nNot logged in"), "unknown"),
    ("codex", outcome("unknown format SYNTHETIC_SECRET"), "unknown"),
    ("codex", outcome("Logged in using ChatGPT", timed_out=True), "unknown"),
])
def test_session_interpretation(probes, app, reply, state):
    probes[2][(app, *detection.COMMANDS[app][1])] = reply
    assert apps(measure(probes))[app]["session"]["state"] == state


@pytest.mark.parametrize("reply", [outcome("bad SYNTHETIC_SECRET"), outcome("1.2.3 4.5.6"),
                                    outcome("1.2.3", code=1), outcome("1.2.3", timed_out=True),
                                    outcome("1.2.3", stderr_truncated=True), OSError("SYNTHETIC_SECRET")])
def test_version_failures_discard_output(probes, reply):
    probes[2][("codex", "--version")] = reply
    row = apps(measure(probes))["codex"]
    assert row["version"] is None and row["next_step"]
    assert "SYNTHETIC_SECRET" not in json.dumps(row)


def test_timed_out_version_skips_session(probes):
    probes[2][("claude", "--version")] = outcome("", timed_out=True)
    row = apps(measure(probes))["claude"]
    assert row["session"]["state"] == "unknown"
    assert ("claude", "auth", "status") not in [(Path(argv[0]).name, *argv[1:]) for argv, _ in probes[1]]


def test_version_punctuation_and_bounded_suffix(probes):
    probes[2][("copilot", "--version")] = outcome("GitHub Copilot CLI 1.0.2.")
    probes[2][("codex", "--version")] = outcome("2.1.0-sk-ant-oat01-" + "X" * 100)
    found = apps(measure(probes))
    assert found["copilot"]["version"] == "1.0.2"
    assert found["codex"]["version"] == "2.1.0"
    assert "sk-ant" not in json.dumps(found)


def test_long_version_suffix_is_not_reported(probes):
    probes[2][("codex", "--version")] = outcome("2.1.0-sk-ant-oat01-" + "X" * 100)
    row = apps(measure(probes))["codex"]
    assert row["version"] == "2.1.0"


def test_declared_path_requires_claude_name(probes, monkeypatch):
    resolver = process.resolve_executable
    path = str(probes[0].parent / "bin" / "other.cmd")
    monkeypatch.setattr(process, "resolve_executable", lambda name, cwd: path if name == path else resolver(name, cwd))
    row = apps(measure(probes, claude_path=path))["claude"]
    assert row["detection"] == "invalid_declared_path"
    assert ("other.cmd", "--version") not in [(Path(argv[0]).name, *argv[1:]) for argv, _ in probes[1]]


def test_signed_out_describes_clean_measurement_environment(probes):
    probes[2][("claude", "auth", "status")] = outcome('{"loggedIn": false}', code=1)
    session = apps(measure(probes))["claude"]["session"]
    assert session["state"] == "signed_out"
    assert session["measured_with"] == "sin variables CLAUDE*/ANTHROPIC*"


def test_package_detect_name_is_submodule():
    import multiagent.setup.detect as module
    assert module is detection


def test_setup_import_failure_is_local(monkeypatch):
    import sys
    with monkeypatch.context() as guard:
        guard.setitem(sys.modules, "multiagent.setup.cli", None)
        parser = cli.build_parser()
        assert parser.parse_args(["doctor"]).func is cli.cmd_doctor
        with pytest.raises(SystemExit, match="0"):
            parser.parse_args(["--version"])
        with pytest.raises(RuntimeError, match="setup"):
            parser.parse_args(["setup"]).func(None)


def test_contexts_are_separate_not_invented(probes):
    console = measure(probes)
    probes[2][("codex", "--version")] = outcome("2.0.0")
    packaged = measure(probes, context="packaged_process")
    assert console["reference_context"] == packaged["reference_context"] == "user_console"
    assert console["contexts"]["packaged_process"]["state"] == "not_measured"
    assert packaged["contexts"]["user_console"]["state"] == "not_measured"
    assert apps(console)["codex"]["version"] == "1.2.3"
    assert apps(packaged, "packaged_process")["codex"]["version"] == "2.0.0"


def test_antigravity_is_optional_detection_only(probes):
    report = measure(probes, include_antigravity=True)
    row = apps(report)["agy"]
    assert row["informational_only"] is True and "FAQ" in row["warning"]
    assert row["session"]["state"] == "unknown"
    assert [(Path(argv[0]).name, *argv[1:]) for argv, _ in probes[1] if Path(argv[0]).name == "agy"] == [("agy", "--version")]


def test_missing_and_invalid_paths_are_not_installation_verdicts(monkeypatch, tmp_path):
    # Real resolver, isolated empty PATH: no CLI lookup can escape to the host.
    monkeypatch.setenv("PATH", "")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(process, "run", lambda *a, **kw: pytest.fail("unexpected execution"))
    row = apps(detection.detect(cwd=tmp_path, system="Windows"))["claude"]
    assert row["detection"] == "not_detected" and "administrada" in row["next_step"]
    for declared in ("relative/claude.exe", str(tmp_path / "missing.exe")):
        row = apps(detection.detect(cwd=tmp_path, system="Windows", claude_path=declared))["claude"]
        assert row["detection"] == "invalid_declared_path"


def test_real_resolver_rejects_workdir_binary(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    binary = tmp_path / ("claude.cmd" if os.name == "nt" else "claude")
    binary.write_text("@echo off\necho 1.2.3\n" if os.name == "nt" else "#!/bin/sh\necho 1.2.3\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(process, "run", lambda *a, **kw: pytest.fail("unsafe execution"))
    report = detection.detect(cwd=tmp_path, system="Windows", claude_path=str(binary))
    assert apps(report)["claude"]["detection"] == "invalid_declared_path"


def test_fake_binary_on_path_and_declared_app_copy(monkeypatch, tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    binary = binaries / ("claude.cmd" if os.name == "nt" else "claude")
    binary.write_text('@echo off\nif "%1"=="--version" (echo 1.2.3) else (echo {"loggedIn":true})\n' if os.name == "nt"
                      else '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 1.2.3; else echo \'{"loggedIn":true}\'; fi\n')
    binary.chmod(0o755)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", str(binaries))
    row = apps(detection.detect(cwd=cwd, system="Windows"))["claude"]
    assert row["version"] == "1.2.3" and row["session"]["state"] == "signed_in"
    monkeypatch.setenv("PATH", "")
    row = apps(detection.detect(cwd=cwd, system="Windows", claude_path=str(binary)))["claude"]
    assert row["origin"] == "declared_path" and row["version"] == "1.2.3"


def test_requirements_report_missing_and_minimums(probes):
    probes[2][("node", "--version")] = outcome("v21.0.0")
    probes[2][("pwsh", "--version")] = outcome("bad")
    rows = {row["name"]: row for row in measure(probes)["contexts"]["user_console"]["requirements"]}
    assert rows["node"]["state"] == "too_old" and rows["node"]["next_step"]
    assert rows["pwsh"]["state"] == "unknown" and rows["pwsh"]["next_step"]
    assert rows["npm"]["state"] == "present"


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_non_windows_never_probes(probes, system):
    result = detection.detect(cwd=probes[0], system=system)
    assert result["contexts"]["user_console"]["message"] == "sin fuente verificada"
    assert probes[1] == []


def test_never_opens_credentials_or_stores(probes, monkeypatch):
    # Fail on all file reads and writes in INS, including alternative open APIs,
    # not only the six forbidden credential paths. Resolution/probes are fake.
    def forbidden(*args, **kwargs):
        pytest.fail("INS must not open any file or credential store")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(io, "open", forbidden)
        guard.setattr(os, "open", forbidden)
        report = measure(probes, include_antigravity=True)
    assert apps(report)["claude"]["session"]["state"] == "signed_in"


def test_cli_wiring_only_setup(monkeypatch, capsys, probes):
    module = importlib.import_module("multiagent.setup.cli")
    monkeypatch.setenv("LOCAL_DIR", str(probes[0] / "local"))
    monkeypatch.setattr(module, "detect", lambda **kwargs: detection.detect(cwd=probes[0], system="Windows", **kwargs))
    args = cli.build_parser().parse_args(["setup", "--context", "packaged_process", "--include-antigravity"])
    original_open = io.open
    def write_only(path, mode="r", *a, **kw):
        assert mode == "x", "setup must not read any local file, including .env or credentials"
        return original_open(path, mode, *a, **kw)

    with monkeypatch.context() as guard:
        guard.setattr(io, "open", write_only)
        args.func(args)
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert apps(result, "packaged_process")["agy"]["informational_only"] is True
    plans = [json.loads(line)["before_action"] for line in captured.err.splitlines()]
    files = list((probes[0] / "local" / "setup-evidence").glob("*.jsonl"))
    assert len(files) == 1
    events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert len(plans) == len(events) == 18
    assert all(event["context"] == "packaged_process" and event["source_date"] == detection.CONTRACT_DATE
               and event["human_acceptance"] == "not_required_read_only" for event in events)
    assert "SYNTHETIC_SECRET" not in files[0].read_text(encoding="utf-8")
    assert "person@example.invalid" not in files[0].read_text(encoding="utf-8")
    assert cli.build_parser().parse_args(["doctor"]).func is cli.cmd_doctor


def test_pre_notice_precedes_every_probe_and_evidence_is_sanitized(probes, monkeypatch):
    timeline = []
    run = process.run

    def execute(argv, **kwargs):
        assert timeline[-1][0] == "before"
        plan = timeline[-1][1]
        assert plan["command"] == [Path(argv[0]).name, *argv[1:]]
        timeline.append(("probe", argv))
        return run(argv, **kwargs)

    monkeypatch.setattr(process, "run", execute)
    measure(probes, before_step=lambda p: timeline.append(("before", p)),
            after_step=lambda e: timeline.append(("after", e)))
    events = [value for kind, value in timeline if kind == "after"]
    assert {e["action"] for e in events} == {"detection", "version", "session", "requirement"}
    assert all(e["purpose"] and e["source"] == "INS-T01" and e["observed_at"] for e in events)
    assert "SYNTHETIC_SECRET" not in json.dumps(events)


@pytest.mark.parametrize("app,expected", [("claude", 1), ("node", 6)])
def test_evidence_failure_stops_further_probes(probes, app, expected):
    def fail(event):
        if event["app"] == app:
            raise OSError("synthetic evidence failure")

    with pytest.raises(OSError, match="synthetic evidence failure"):
        measure(probes, after_step=fail)
    assert len(probes[1]) == expected


def test_missing_app_and_requirement_record_next_steps(probes, monkeypatch):
    events = []
    def missing(*args):
        raise process.ProcessLaunchError("missing", "synthetic failure")

    monkeypatch.setattr(process, "resolve_executable", missing)
    measure(probes, after_step=events.append)
    assert len(events) == 7
    assert all(event["result"]["next_step"] for event in events)


def test_invalid_context_fails_before_probe(probes):
    with pytest.raises(ValueError, match="context"):
        measure(probes, context="invented")
    assert not probes[1]
