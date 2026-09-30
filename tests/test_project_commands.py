# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project commands (ORQ-T12): local configuration and shell-free execution.

The commands run the current Python interpreter with harmless one-line scripts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from multiagent.tasks import commands as commands_module
from multiagent.tasks.commands import (
    DEFAULT_TIMEOUT_SECONDS,
    OUTPUT_EXCERPT_CHARS,
    CommandConfigError,
    load_commands,
    run_command,
)

PY = json.dumps(sys.executable)  # a TOML/JSON string with the interpreter path


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "project_commands.toml"
    path.write_text(text, encoding="utf-8")
    return path


def commands(tmp_path: Path, text: str):
    return load_commands(write(tmp_path, text), default_cwd=tmp_path)


def test_missing_file_means_no_commands(tmp_path):
    assert load_commands(tmp_path / "absent.toml", default_cwd=tmp_path) == {}


def test_a_valid_file_is_loaded_with_defaults(tmp_path):
    cwd = json.dumps(str(tmp_path / "work"))
    loaded = commands(tmp_path, f"""
[commands.test]
argv = [{PY}, "-c", "print('ok')"]

[commands.lint]
argv = [{PY}, "-c", "print(1)", "{{task_id}}"]
timeout = 30
cwd = {cwd}
""")
    assert set(loaded) == {"test", "lint"}
    assert loaded["test"].timeout == DEFAULT_TIMEOUT_SECONDS and loaded["test"].cwd == tmp_path
    assert loaded["lint"].timeout == 30 and loaded["lint"].cwd == tmp_path / "work"
    assert loaded["lint"].argv[-1] == "{task_id}"


@pytest.mark.parametrize("text, field", [
    ("not = [valid", ""),
    ("[other]\nx = 1", ""),
    ('[commands.Test]\nargv = ["x"]', "commands.Test"),
    ("[commands.test]\nargv = []", "commands.test.argv"),
    ('[commands.test]\nargv = "python -c 1"', "commands.test.argv"),
    ('[commands.test]\nargv = ["python", ""]', "commands.test.argv"),
    ('[commands.test]\nargv = ["echo", "{secret}"]', "commands.test.argv"),
    ('[commands.test]\nargv = ["echo", "id={task_id}"]', "commands.test.argv"),
    ('[commands.test]\nargv = ["x"]\ntimeout = 0', "commands.test.timeout"),
    ('[commands.test]\nargv = ["x"]\ntimeout = 9999', "commands.test.timeout"),
    ('[commands.test]\nargv = ["x"]\ntimeout = true', "commands.test.timeout"),
    ('[commands.test]\nargv = ["x"]\ncwd = "relative/dir"', "commands.test.cwd"),
    ('[commands.test]\nargv = ["x"]\nshell = true', "commands.test"),
])
def test_invalid_files_are_rejected_with_the_field(tmp_path, text, field):
    with pytest.raises(CommandConfigError) as error:
        commands(tmp_path, text)
    assert error.value.field == field


def test_exit_code_zero_passes_and_other_codes_fail(tmp_path):
    loaded = commands(tmp_path, f"""
[commands.ok]
argv = [{PY}, "-c", "print('all good')"]
[commands.bad]
argv = [{PY}, "-c", "import sys; print('broken'); sys.exit(3)"]
""")
    ok = run_command(loaded, "ok")
    assert (ok.status, ok.exit_code, ok.reason) == ("passed", 0, "") and "all good" in ok.output
    bad = run_command(loaded, "bad")
    assert (bad.status, bad.exit_code, bad.reason) == ("failed", 3, "exit_code") and "broken" in bad.output
    assert bad.duration_ms >= 0 and bad.started_at <= bad.ended_at


def test_a_timeout_is_a_failure(tmp_path):
    loaded = commands(tmp_path, f"""
[commands.slow]
argv = [{PY}, "-c", "import time; time.sleep(30)"]
timeout = 1
""")
    result = run_command(loaded, "slow")
    assert (result.status, result.reason, result.exit_code) == ("failed", "timeout", None)
    assert "timed out after 1 s" in result.output


def test_unknown_or_unstartable_commands_are_errors(tmp_path):
    missing = json.dumps(str(tmp_path / "missing-folder"))  # absolute on every platform
    loaded = commands(tmp_path, f"""
[commands.ghost]
argv = ["this-program-does-not-exist-9f2c"]
[commands.nowhere]
argv = [{PY}, "-c", "print(1)"]
cwd = {missing}
""")
    assert (run_command(loaded, "test").status, run_command(loaded, "test").reason) == ("error", "not_configured")
    ghost = run_command(loaded, "ghost")
    assert (ghost.status, ghost.reason) == ("error", "cannot_start")
    assert run_command(loaded, "nowhere").reason == "cannot_start"


def test_placeholders_are_whole_arguments_and_never_reach_a_shell(tmp_path):
    script = "import sys; print('|'.join(sys.argv[1:]))"
    loaded = commands(tmp_path, f"""
[commands.echo]
argv = [{PY}, "-c", {json.dumps(script)}, "{{task_id}}", "{{attempt}}", "{{group_id}}", "&", "echo", "injected"]
""")
    result = run_command(loaded, "echo", {"task_id": "ORQ-T12", "attempt": "2", "group_id": "ORQ"})
    # The shell operator is an ordinary argument: nothing else runs.
    assert result.status == "passed" and result.output.strip() == "ORQ-T12|2|ORQ|&|echo|injected"
    missing = run_command(loaded, "echo", {"task_id": "ORQ-T12"})
    assert (missing.status, missing.reason) == ("error", "missing_value")
    unsafe = run_command(loaded, "echo", {"task_id": "x; rm -rf /", "attempt": "1", "group_id": "G"})
    assert (unsafe.status, unsafe.reason) == ("error", "missing_value")


def test_output_keeps_only_the_last_characters(tmp_path):
    script = f"print('a' * 100 + 'b' * {OUTPUT_EXCERPT_CHARS})"
    loaded = commands(tmp_path, f"""
[commands.loud]
argv = [{PY}, "-c", {json.dumps(script)}]
""")
    output = run_command(loaded, "loud").output
    assert len(output) == OUTPUT_EXCERPT_CHARS and "a" not in output


def test_stderr_is_included_in_the_output(tmp_path):
    loaded = commands(tmp_path, f"""
[commands.warn]
argv = [{PY}, "-c", "import sys; sys.stderr.write('to stderr')"]
""")
    assert "to stderr" in run_command(loaded, "warn").output


# The command starts a child that sleeps for a minute and reports its pid.
_WITH_CHILD = ("import subprocess, sys, time; "
               "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
               "print('child', child.pid, flush=True); time.sleep(60)")
# The command starts a child and exits at once, leaving the child holding the output open.
_ORPHANING = ("import subprocess, sys; "
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
              "print('child', child.pid, flush=True)")


def _pid_of(output: str) -> int:
    return int(output.split("child", 1)[1].split()[0])


def _alive(pid: int) -> bool:
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in listing.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _stop(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


def test_a_timeout_ends_the_whole_process_tree(tmp_path):
    loaded = commands(tmp_path, f"""
[commands.tree]
argv = [{PY}, "-c", {json.dumps(_WITH_CHILD)}]
timeout = 2
""")
    started = time.monotonic()
    result = run_command(loaded, "tree")
    elapsed = time.monotonic() - started
    pid = _pid_of(result.output)
    try:
        assert (result.status, result.reason, result.exit_code) == ("failed", "timeout", None)
        assert "timed out after 2 s" in result.output and "child" in result.output  # the partial output survives
        # Before the fix the call waited for the child (60 s here) instead of the 2 s timeout.
        assert elapsed < 30
        deadline = time.monotonic() + 10
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert not _alive(pid), "the child of the command is still running"
    finally:
        _stop(pid)


def test_a_command_that_ends_is_not_held_by_a_child_that_keeps_the_output_open(tmp_path, monkeypatch):
    monkeypatch.setattr(commands_module, "KILL_GRACE_SECONDS", 1)
    loaded = commands(tmp_path, f"""
[commands.orphan]
argv = [{PY}, "-c", {json.dumps(_ORPHANING)}]
timeout = 30
""")
    started = time.monotonic()
    result = run_command(loaded, "orphan")
    elapsed = time.monotonic() - started
    pid = _pid_of(result.output)
    try:
        # The exit code decides; the call does not wait for the child (60 s) nor for the timeout (30 s).
        assert (result.status, result.exit_code) == ("passed", 0) and "child" in result.output
        assert elapsed < 20
    finally:
        _stop(pid)


@pytest.mark.skipif(os.name == "nt", reason="on Windows taskkill /T reaches every descendant, so none escapes the tree")
def test_a_timeout_does_not_wait_for_a_process_that_left_the_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(commands_module, "KILL_GRACE_SECONDS", 1)
    escaping = ("import subprocess, sys, time; "
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True); "
                "print('child', child.pid, flush=True); time.sleep(60)")
    loaded = commands(tmp_path, f"""
[commands.escape]
argv = [{PY}, "-c", {json.dumps(escaping)}]
timeout = 2
""")
    started = time.monotonic()
    result = run_command(loaded, "escape")
    elapsed = time.monotonic() - started
    pid = _pid_of(result.output)
    try:
        assert (result.status, result.reason) == ("failed", "timeout")
        assert "child" in result.output and "not waiting for it" in result.output
        assert elapsed < 20
    finally:
        _stop(pid)