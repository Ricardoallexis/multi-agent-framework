"""ORQ-T47: the safe process executor, driven with fake programs written in ``tmp_path``.

Every fake program is a small Python script run as ``[sys.executable, script, mode, ...]``: ``sys.executable`` is an
absolute path, so argv[0] is never relative. Bare-name and ``.cmd`` shim cases use a generated launcher in a folder
that the test puts on ``PATH``.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from multiagent.executors import process
from multiagent.executors.contract import CommandRecord
from multiagent.executors.launch_safety import NO_CURRENT_DIR_VARIABLE
from multiagent.executors.process import (
    ProcessLaunchError, ProcessOutcome, _TailBuffer, build_environment, cancel, run, start,
)

FAKE_CLI = r'''
import json, os, subprocess, sys, time

mode = sys.argv[1]
if mode == "report":
    json.dump({"env": dict(os.environ), "cwd": os.getcwd(), "argv": sys.argv[2:]}, sys.stdout)
elif mode == "echo":
    sys.stdout.buffer.write(sys.stdin.buffer.read())
elif mode == "exit":
    sys.stderr.write("bye")
    sys.exit(int(sys.argv[2]))
elif mode == "flood":
    size = int(sys.argv[2])
    sys.stdout.write("x" * (size - 3) + "END")
    sys.stderr.write("y" * (size - 3) + "ERR")
elif mode == "raw":
    sys.stdout.buffer.write(bytes.fromhex(sys.argv[2]))
elif mode == "sleep":
    time.sleep(float(sys.argv[2]))
elif mode == "sleeper":
    with open(sys.argv[2], "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(120)
elif mode == "tree":
    pidfile = sys.argv[2]
    subprocess.Popen([sys.executable, __file__, "sleeper", pidfile + ".grandchild"])
    while not os.path.exists(pidfile + ".grandchild") or not open(pidfile + ".grandchild").read():
        time.sleep(0.05)
    with open(pidfile, "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(120)
elif mode == "orphan":
    # leaves a grandchild running that keeps this program's stdout open, then exits at once
    subprocess.Popen([sys.executable, __file__, "sleeper", sys.argv[2]], stdout=1, stderr=2)
    while not os.path.exists(sys.argv[2]) or not open(sys.argv[2]).read():
        time.sleep(0.05)
'''

POLL_STEP = 0.05


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for(predicate, seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(POLL_STEP)
    raise AssertionError(f"timed out waiting for {what}")


def _read_pid(path: Path) -> int:
    _wait_for(lambda: path.exists() and path.read_text().strip().isdigit(), 30, f"{path.name}")
    return int(path.read_text().strip())


def _assert_dead(*pids: int) -> None:
    for pid in pids:
        _wait_for(lambda pid=pid: not _pid_alive(pid), 10, f"process {pid} to die")


@pytest.fixture()
def script(tmp_path) -> Path:
    path = tmp_path / "tools" / "fake_cli.py"
    path.parent.mkdir()
    path.write_text(FAKE_CLI, encoding="utf-8")
    return path


@pytest.fixture()
def workdir(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def cli(script):
    def build(*args: str) -> list[str]:
        return [sys.executable, str(script), *args]
    return build


def make_launcher(folder: Path, name: str, script: Path) -> Path:
    """A program called ``name`` (``name.cmd`` on Windows) that runs ``script`` with the given arguments."""
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path = folder / f"{name}.cmd"
        path.write_bytes(f'@"{sys.executable}" "{script}" %*\r\n'.encode())
    else:
        path = folder / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def go(argv, workdir, *, stdin_text=None, timeout=60, **kwargs) -> ProcessOutcome:
    return run(argv, cwd=workdir, stdin_text=stdin_text, timeout_seconds=timeout, **kwargs)


def report(outcome: ProcessOutcome) -> dict:
    assert outcome.exit_code == 0, outcome
    return json.loads(outcome.stdout_tail)


def refused(code: str, *args, **kwargs):
    with pytest.raises(ProcessLaunchError) as caught:
        go(*args, **kwargs)
    assert caught.value.code == code, caught.value
    return caught.value


# --------------------------------------------------------------------------- environment

def test_environment_is_scrubbed_and_the_current_folder_flag_is_set(cli, workdir, monkeypatch):
    for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "anthropic_base_url"):
        monkeypatch.setenv(name, "secret-value")
    monkeypatch.setenv("KEEP_ME_TEST", "yes")
    monkeypatch.setenv("NODEFAULTCURRENTDIRECTORYINEXEPATH", "0")  # a hostile value is replaced, in any letter case
    env = report(go(cli("report"), workdir))["env"]
    upper = {name.upper(): value for name, value in env.items()}
    assert [name for name in upper if name.startswith(("CLAUDE", "ANTHROPIC"))] == []
    assert "secret-value" not in env.values()
    assert upper["KEEP_ME_TEST"] == "yes" and "PATH" in upper
    assert upper["NODEFAULTCURRENTDIRECTORYINEXEPATH"] == "1"
    assert os.environ["CLAUDECODE"] == "secret-value"  # the parent's own environment is untouched


def test_build_environment_keeps_the_exact_flag_name_and_applies_env_extra(monkeypatch):
    monkeypatch.setenv("NODEFAULTCURRENTDIRECTORYINEXEPATH", "0")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-value")
    env = build_environment({"FAKE_EXTRA": "1", "ANOTHER": "two"})
    assert env[NO_CURRENT_DIR_VARIABLE] == "1"
    assert [name for name in env if name.upper() == NO_CURRENT_DIR_VARIABLE.upper()] == [NO_CURRENT_DIR_VARIABLE]
    assert env["FAKE_EXTRA"] == "1" and env["ANOTHER"] == "two"
    assert not any(name.upper().startswith("ANTHROPIC") for name in env)
    assert env is not os.environ


def test_env_extra_reaches_the_child(cli, workdir):
    env = report(go(cli("report"), workdir, env_extra={"FAKE_EXTRA_VAR": "from-extra"}))["env"]
    assert {name.upper(): value for name, value in env.items()}["FAKE_EXTRA_VAR"] == "from-extra"


@pytest.mark.parametrize("extra", [
    {"CLAUDE_CONFIG_DIR": "x"}, {"claudecode": "1"}, {"ANTHROPIC_API_KEY": "x"}, {"Anthropic_Base_Url": "x"},
    {NO_CURRENT_DIR_VARIABLE: "0"}, {"nodefaultcurrentdirectoryinexepath": "0"},
    {"A=B": "x"}, {"": "x"}, {"OK": 1}, {"NUL": "a\x00b"}, {1: "x"},
])
def test_env_extra_cannot_restore_scrubbed_names_or_break_the_flag(cli, workdir, extra):
    refused(process.INVALID_ENV, cli("report"), workdir, env_extra=extra)


# --------------------------------------------------------------------------- cwd

def test_the_child_runs_in_the_given_folder_and_a_string_path_is_accepted(cli, workdir):
    for given in (workdir, str(workdir)):
        seen = report(go(cli("report"), given))["cwd"]
        assert Path(seen).resolve() == workdir.resolve()


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "empty", "none", "number"])
def test_cwd_must_be_an_existing_absolute_folder(cli, workdir, tmp_path, kind):
    bad = {"relative": "work", "missing": tmp_path / "nowhere", "file": tmp_path / "tools" / "fake_cli.py",
           "empty": "", "none": None, "number": 7}[kind]
    refused(process.INVALID_CWD, cli("report"), bad)


# --------------------------------------------------------------------------- argv and executable

@pytest.mark.parametrize("argv", [
    [], (), "python script", None, [""], [" "], [sys.executable, 1], [sys.executable, None], [sys.executable, b"x"],
    [sys.executable, "line\nbreak"], [sys.executable, "carriage\rreturn"], [sys.executable, "nul\x00"],
    [sys.executable, "p" * (process.MAX_ARGUMENT_CHARS + 1)],
])
def test_argv_must_be_a_list_of_plain_strings_and_never_carry_free_text(workdir, argv):
    refused(process.INVALID_ARGV, argv, workdir)


def test_stdin_text_must_be_text(cli, workdir):
    refused(process.INVALID_ARGV, cli("echo"), workdir, stdin_text=b"bytes")


@pytest.mark.parametrize("name", ["./tool", "bin/tool", "../tool", "sub/dir/tool.exe"])
def test_a_relative_executable_is_refused(workdir, name):
    refused(process.RELATIVE_EXECUTABLE, [name, "x"], workdir)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path forms")
@pytest.mark.parametrize("name", [".\\tool.exe", "bin\\tool.exe", "\\tool.exe", "C:tool.exe"])
def test_a_relative_executable_is_refused_in_windows_forms(workdir, name):
    refused(process.RELATIVE_EXECUTABLE, [name, "x"], workdir)


def test_a_missing_program_or_a_folder_is_not_found(workdir, tmp_path):
    refused(process.EXECUTABLE_NOT_FOUND, ["definitely-not-a-program-orq-t47"], workdir)
    refused(process.EXECUTABLE_NOT_FOUND, [str(tmp_path / "nowhere" / "tool")], workdir)
    refused(process.EXECUTABLE_NOT_FOUND, [str(tmp_path)], workdir)


def test_the_resolved_absolute_path_is_what_runs_and_is_recorded(cli, workdir):
    outcome = go(cli("exit", "0"), workdir)
    assert os.path.normcase(outcome.command.argv[0]) == os.path.normcase(os.path.abspath(sys.executable))
    assert os.path.isabs(outcome.command.argv[0])


def test_a_bare_name_is_resolved_from_an_absolute_path_entry(script, workdir, tmp_path, monkeypatch):
    launcher = make_launcher(tmp_path / "bin", "fakecli-orq", script)
    monkeypatch.setenv("PATH", os.pathsep.join([str(launcher.parent), os.environ["PATH"]]))
    outcome = go(["fakecli-orq", "report", "a", "b"], workdir)
    assert report(outcome)["argv"] == ["a", "b"]
    assert os.path.normcase(outcome.command.argv[0]) == os.path.normcase(str(launcher))


def test_a_relative_path_entry_is_never_searched(script, workdir, tmp_path, monkeypatch):
    make_launcher(tmp_path / "bin", "fakecli-orq", script)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join(["bin", ".", ""]))
    refused(process.EXECUTABLE_NOT_FOUND, ["fakecli-orq"], workdir)


def test_a_program_in_the_servers_current_folder_is_not_found(script, workdir, tmp_path, monkeypatch):
    launcher = make_launcher(tmp_path / "bin", "fakecli-orq", script)
    monkeypatch.chdir(launcher.parent)
    monkeypatch.setenv("PATH", os.pathsep.join([str(launcher.parent), os.environ["PATH"]]))
    refused(process.EXECUTABLE_NOT_FOUND, ["fakecli-orq"], workdir)


@pytest.mark.parametrize("where", ["", "node_modules/.bin"])
def test_a_program_inside_the_working_folder_is_refused(script, workdir, monkeypatch, where):
    planted = make_launcher(workdir / where if where else workdir, "fakecli-orq", script)
    refused(process.EXECUTABLE_IN_WORKDIR, [str(planted), "report"], workdir)  # by absolute path
    monkeypatch.setenv("PATH", os.pathsep.join([str(planted.parent), os.environ["PATH"]]))
    refused(process.EXECUTABLE_IN_WORKDIR, ["fakecli-orq", "report"], workdir)  # found through PATH


def test_a_link_inside_the_working_folder_to_a_program_elsewhere_is_refused(workdir):
    link = workdir / ("linked-python" + Path(sys.executable).suffix)
    try:
        link.symlink_to(sys.executable)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available")
    refused(process.EXECUTABLE_IN_WORKDIR, [str(link), "-c", "pass"], workdir)


def test_a_program_that_cannot_be_started_is_a_launch_failure(workdir, tmp_path):
    broken = tmp_path / ("broken.exe" if sys.platform == "win32" else "broken")
    broken.write_text("this is not a program")
    broken.chmod(broken.stat().st_mode | stat.S_IXUSR)
    error = refused(process.LAUNCH_FAILED, [str(broken)], workdir)
    assert "could not be started" in str(error)


@pytest.mark.parametrize("timeout, cap", [(0, 10), (-1, 10), (True, 10), ("5", 10), (None, 10), (5, 0), (5, True), (5, 1.5)])
def test_limits_must_be_positive_numbers(cli, workdir, timeout, cap):
    refused(process.INVALID_LIMIT, cli("report"), workdir, timeout=timeout, output_cap_bytes=cap)


def test_nothing_is_started_when_a_rule_is_broken(cli, workdir, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Popen must not be called")

    monkeypatch.setattr(process.subprocess, "Popen", forbidden)
    refused(process.INVALID_ARGV, [sys.executable, "a\nb"], workdir)
    refused(process.INVALID_CWD, cli("report"), "relative")
    refused(process.INVALID_ENV, cli("report"), workdir, env_extra={"CLAUDE_X": "1"})
    refused(process.RELATIVE_EXECUTABLE, ["./tool"], workdir)


def test_there_is_never_a_shell_and_metacharacters_are_plain_text(cli, workdir, monkeypatch):
    calls: list[tuple[tuple, dict]] = []
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", spy)
    hostile = ["&", "echo", "hacked", "|", ">injected.txt", "%PATH%", "$(id)", "`id`", ";", "--x=\"y\"", "a b"]
    outcome = go(cli("report", *hostile), workdir)
    assert report(outcome)["argv"] == hostile
    assert not (workdir / "injected.txt").exists()
    (args, kwargs) = next(call for call in calls if call[0] and call[0][0][1:2] == [str(cli("x")[1])])
    assert isinstance(args[0], list) and kwargs["shell"] is False
    assert kwargs["cwd"] == str(workdir) and kwargs["stdout"] == subprocess.PIPE


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe shims exist only on Windows")
class TestCmdShim:
    def test_plain_tokens_are_passed_through_a_cmd_shim(self, script, workdir, tmp_path):
        shim = make_launcher(tmp_path / "bin", "shim", script)
        outcome = go([str(shim), "report", "--resume=0f3a-12", "--model", "opus-4", r"C:\work space\wt", "a.b_c:d@e"], workdir)
        assert report(outcome)["argv"] == ["--resume=0f3a-12", "--model", "opus-4", r"C:\work space\wt", "a.b_c:d@e"]

    @pytest.mark.parametrize("bad", ["a&b", "a|b", "a<b", "a>b", "^", "%PATH%", "!x", 'say "hi"', "(x)", "a;b", "a,b", "`x`"])
    def test_arguments_that_cmd_exe_would_interpret_are_refused(self, script, workdir, tmp_path, monkeypatch, bad):
        shim = make_launcher(tmp_path / "bin", "shim", script)
        monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **k: pytest.fail("a shim must not start"))
        refused(process.UNSAFE_SHIM_ARGUMENT, [str(shim), "report", bad], workdir)

    def test_a_real_executable_may_take_any_single_line_text(self, cli, workdir):
        assert report(go(cli("report", "a&b", "%PATH%"), workdir))["argv"] == ["a&b", "%PATH%"]


# --------------------------------------------------------------------------- stdin, output, exit codes

def test_stdin_text_is_delivered_as_utf8(cli, workdir):
    prompt = "Primera línea ñandú 日本語 \U0001F600\nsegunda línea\r\ntercera"
    assert go(cli("echo"), workdir, stdin_text=prompt).stdout_tail == prompt


def test_a_large_stdin_and_stdout_do_not_deadlock(cli, workdir):
    prompt = ("0123456789abcdef" * 4096 + "\n") * 40  # ~2.6 MB, far above any pipe buffer
    outcome = go(cli("echo"), workdir, stdin_text=prompt, output_cap_bytes=4_000_000)
    assert outcome.stdout_tail == prompt and not outcome.stdout_truncated


def test_without_stdin_text_the_child_reads_end_of_input(cli, workdir):
    outcome = go(cli("echo"), workdir, stdin_text=None, timeout=30)
    assert outcome.exit_code == 0 and outcome.stdout_tail == ""


def test_a_program_that_ignores_its_stdin_is_not_a_failure(cli, workdir):
    outcome = go(cli("exit", "0"), workdir, stdin_text="never read " * 200_000)
    assert outcome.exit_code == 0 and not outcome.timed_out


def test_output_is_capped_keeping_the_tail(cli, workdir):
    outcome = go(cli("flood", "200000"), workdir, output_cap_bytes=1000)
    assert len(outcome.stdout_tail) == len(outcome.stderr_tail) == 1000
    assert outcome.stdout_tail.endswith("END") and outcome.stderr_tail.endswith("ERR")
    assert set(outcome.stdout_tail[:-3]) == {"x"} and set(outcome.stderr_tail[:-3]) == {"y"}
    assert outcome.stdout_truncated and outcome.stderr_truncated


def test_output_under_the_cap_is_whole_and_not_flagged(cli, workdir):
    outcome = go(cli("flood", "500"), workdir, output_cap_bytes=1000)
    assert len(outcome.stdout_tail) == 500 and len(outcome.stderr_tail) == 500
    assert not outcome.stdout_truncated and not outcome.stderr_truncated


def test_the_default_cap_is_one_megabyte(cli, workdir):
    assert process.DEFAULT_OUTPUT_CAP_BYTES == 1_000_000
    outcome = go(cli("flood", "1500000"), workdir)
    assert len(outcome.stdout_tail) == 1_000_000 and outcome.stdout_truncated


def test_invalid_utf8_is_replaced_not_raised(cli, workdir):
    outcome = go(cli("raw", "41ff42fe43"), workdir)
    assert outcome.stdout_tail == "A\ufffdB\ufffdC"


def test_a_cut_never_starts_in_the_middle_of_a_character():
    buffer = _TailBuffer(5)
    buffer.feed("éééééé".encode())  # 12 bytes: the last 5 start on the second byte of a character
    assert buffer.text() == "éé" and buffer.truncated
    small = _TailBuffer(100)
    small.feed("hola".encode())
    assert small.text() == "hola" and not small.truncated


@pytest.mark.parametrize("code", [0, 1, 2, 3, 42, 255])
def test_exit_codes_are_reported_and_recorded(cli, workdir, code):
    outcome = go(cli("exit", str(code)), workdir)
    assert outcome.exit_code == code == outcome.command.exit_code
    assert outcome.stderr_tail == "bye" and outcome.stdout_tail == ""
    assert not outcome.timed_out and not outcome.cancelled


# --------------------------------------------------------------------------- record and logging

def test_the_command_record_has_the_exact_argv_cwd_and_times(cli, workdir):
    argv = cli("exit", "0")
    outcome = go(argv, workdir)
    record = outcome.command
    assert isinstance(record, CommandRecord)
    assert record.argv[1:] == tuple(argv[1:])
    assert os.path.normcase(record.argv[0]) == os.path.normcase(os.path.abspath(sys.executable))
    assert record.cwd == str(workdir) and record.exit_code == 0
    assert datetime.fromisoformat(record.started_at) <= datetime.fromisoformat(record.finished_at)


def test_the_exact_argv_is_logged_and_the_stdin_text_never_is(cli, workdir, caplog):
    caplog.set_level(logging.INFO, logger="multiagent.executors.process")
    secret_prompt = "PROMPT-TEXT-THAT-MUST-NOT-BE-LOGGED " * 5
    argv = cli("echo")
    outcome = go(argv, workdir, stdin_text=secret_prompt)
    assert outcome.stdout_tail == secret_prompt
    started = [record.getMessage() for record in caplog.records if record.getMessage().startswith("process start")]
    assert len(started) == 1 and str(list(outcome.command.argv)) in started[0]
    assert str(workdir) in started[0]
    assert any(record.getMessage().startswith("process") and "finished" in record.getMessage() for record in caplog.records)
    assert "PROMPT-TEXT" not in caplog.text and "PROMPT-TEXT" not in " ".join(outcome.command.argv)


# --------------------------------------------------------------------------- timeout and cancellation

def test_a_timeout_kills_the_program_and_its_grandchild(cli, workdir, tmp_path):
    pidfile = tmp_path / "pids" / "tree"
    pidfile.parent.mkdir()
    started = time.monotonic()
    outcome = go(cli("tree", str(pidfile)), workdir, timeout=8)
    elapsed = time.monotonic() - started
    assert outcome.timed_out and not outcome.cancelled and elapsed < 40
    parent, grandchild = _read_pid(pidfile), _read_pid(Path(str(pidfile) + ".grandchild"))
    assert parent != grandchild
    _assert_dead(parent, grandchild)
    assert outcome.command.finished_at is not None


def test_a_short_timeout_on_a_plain_program(cli, workdir):
    started = time.monotonic()
    outcome = go(cli("sleep", "60"), workdir, timeout=1)
    assert outcome.timed_out and not outcome.cancelled and time.monotonic() - started < 30


def test_a_program_that_finishes_in_time_is_not_timed_out(cli, workdir):
    outcome = go(cli("sleep", "0.2"), workdir, timeout=30)
    assert outcome.exit_code == 0 and not outcome.timed_out


def test_cancel_kills_the_program_and_its_grandchild(cli, workdir, tmp_path):
    pidfile = tmp_path / "pids-cancel"
    handle = start(cli("tree", str(pidfile)), cwd=workdir, stdin_text=None, timeout_seconds=120)
    parent, grandchild = _read_pid(pidfile), _read_pid(Path(str(pidfile) + ".grandchild"))
    assert not handle.done() and _pid_alive(parent) and _pid_alive(grandchild)
    assert cancel(handle) is True
    outcome = handle.wait(30)
    assert outcome.cancelled and not outcome.timed_out and handle.done()
    _assert_dead(parent, grandchild)
    assert cancel(handle) is False  # already over


def test_cancel_after_the_program_finished_does_nothing(cli, workdir):
    handle = start(cli("exit", "0"), cwd=workdir, stdin_text=None, timeout_seconds=60)
    outcome = handle.wait(30)
    assert cancel(handle) is False and not outcome.cancelled and outcome.exit_code == 0


def test_wait_with_a_timeout_does_not_stop_the_program(cli, workdir):
    handle = start(cli("sleep", "60"), cwd=workdir, stdin_text=None, timeout_seconds=120)
    try:
        with pytest.raises(TimeoutError):
            handle.wait(0.3)
        assert not handle.done() and handle.pid > 0
    finally:
        cancel(handle)
        assert handle.wait(30).cancelled


def test_what_the_program_leaves_holding_its_output_is_killed(cli, workdir, tmp_path):
    pidfile = tmp_path / "pid-orphan"
    started = time.monotonic()
    outcome = go(cli("orphan", str(pidfile)), workdir, timeout=60)
    assert outcome.exit_code == 0 and not outcome.timed_out and time.monotonic() - started < 30
    _assert_dead(_read_pid(pidfile))
