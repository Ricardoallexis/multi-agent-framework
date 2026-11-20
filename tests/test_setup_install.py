# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INS-T04 uses synthetic installers and console launchers only."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from multiagent import cli
from multiagent.executors.launch_safety import EffectNotAuthorized
from multiagent.setup import guided


@pytest.mark.parametrize("app,step,command", [
    ("claude", "install", ["winget", "install", "Anthropic.ClaudeCode"]),
    ("codex", "install", ["npm", "install", "-g", "@openai/codex"]),
    ("copilot", "install", ["winget", "install", "GitHub.Copilot"]),
    ("claude", "login", ["claude", "auth", "login"]),
    ("codex", "login", ["codex", "login"]),
    ("copilot", "login", ["copilot", "login"]),
])
def test_fixed_source_and_command(app, step, command):
    plan = guided.step_plan(app, step)
    assert plan["command"] == command
    assert plan["source"].startswith("https://")
    assert plan["source_date"] == guided.SOURCE_DATE
    assert not any(".ps1" in arg or "iex" in arg for arg in command)


def test_console_requires_enter_and_runs_fake_installer_only(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(guided.launch_safety, "launch_environment", lambda: {"SAFE": "1"})

    def wait(message):
        events.append(("enter", message))

    def find(name):
        events.append(("find", name))
        return "C:/fake/winget.exe"

    def run(argv, **kwargs):
        events.append(("run", argv, kwargs))
        return SimpleNamespace(returncode=0)

    assert guided.console_step("copilot", "install", wait=wait, run=run, find=find) == 0
    assert [kind for kind, *_ in events] == ["enter", "find", "run", "enter"]
    assert events[2][1] == ["C:/fake/winget.exe", "install", "GitHub.Copilot"]
    assert events[2][2]["shell"] is False and events[2][2]["env"] == {"SAFE": "1"}
    printed = capsys.readouterr().out
    assert "Source (" in printed and "Command: winget install GitHub.Copilot" in printed


def test_claude_signature_is_required_after_fake_install(monkeypatch):
    calls = []
    monkeypatch.setattr(guided.launch_safety, "launch_environment", lambda: {"SAFE": "1"})
    binaries = {"winget": "C:/fake/winget.exe", "claude": "C:/fake/claude.exe",
                "powershell": "C:/fake/powershell.exe"}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0 if len(calls) == 1 else 1)

    code = guided.console_step("claude", "install", wait=lambda _: None, run=run,
                               find=binaries.get)
    assert code == 1
    assert calls[0][0] == [binaries["winget"], "install", "Anthropic.ClaudeCode"]
    assert calls[1][0][:4] == [binaries["powershell"], "-NoProfile", "-NonInteractive", "-Command"]
    assert calls[1][1]["env"]["MAF_CLAUDE_VERIFY_PATH"] == binaries["claude"]
    assert "Get-AuthenticodeSignature" in calls[1][0][4]


def test_missing_prerequisite_does_not_run(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No installer may run")

    assert guided.console_step("codex", "install", wait=lambda _: None, run=forbidden,
                               find=lambda _: None) == 1


def test_parent_only_launches_helper_then_rechecks(monkeypatch, tmp_path):
    events = []
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(guided.launch_safety, "launch_environment", lambda: {"SAFE": "1"})

    class Child:
        def wait(self):
            events.append("closed")
            return 0

    def launch(argv, **kwargs):
        events.append(("launch", argv, kwargs))
        return Child()

    def measure(**kwargs):
        events.append(("detect", kwargs))
        return {"contexts": {kwargs["context"]: {"state": "measured"}}}

    result = guided.guide_step("copilot", "login", launcher=launch,
                               detector=measure, system="Windows")
    assert [item if isinstance(item, str) else item[0] for item in events] == ["launch", "closed", "detect"]
    argv, kwargs = events[0][1:]
    assert argv[1:] == ["-m", "multiagent.setup.guided", "--console", "copilot", "login"]
    assert kwargs["creationflags"] == guided.CREATE_NEW_CONSOLE and kwargs["shell"] is False
    assert kwargs["env"]["SAFE"] == "1"
    assert events[2][1] == {"context": "packaged_process", "include_antigravity": False}
    assert result["console_exit_code"] == 0 and result["recheck"]["contexts"]["packaged_process"]["state"] == "measured"
    evidence = list((tmp_path / "setup-evidence").glob("*.jsonl"))
    assert len(evidence) == 1
    records = [json.loads(line) for line in evidence[0].read_text(encoding="utf-8").splitlines()]
    assert records[0]["before_action"]["command"] == ["copilot", "login"]
    assert records[1]["console_exit_code"] == 0


def test_background_returns_before_close_and_cli_default_still_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path))
    monkeypatch.setattr(guided.launch_safety, "launch_environment", lambda: {"SAFE": "1"})
    monkeypatch.setattr(guided, "_console_state", None)
    release = threading.Event()

    class Child:
        def wait(self):
            assert release.wait(timeout=5)
            return 0

    detector = lambda **kw: {"contexts": {kw["context"]: {"state": "measured"}}}
    try:
        result = guided.guide_step("codex", "login", launcher=lambda *a, **kw: Child(), detector=detector,
                                   system="Windows", background=True)
        assert set(result) == {"plan", "console_id", "state"} and result["state"] == "open"
        assert guided.console_status()["state"] == "open"
        # The lock stays held while the thread waits, and is released when it ends.
        assert not guided._console_lock.acquire(blocking=False)
    finally:
        release.set()
        guided._console_thread.join(timeout=5)
    assert not guided._console_thread.is_alive()
    status = guided.console_status()
    assert status["state"] == "closed" and status["console_exit_code"] == 0
    assert guided._console_lock.acquire(blocking=False)
    guided._console_lock.release()

    # Without background=True the call returns only after the close, as `multiagent setup` expects.
    order = []

    class SlowChild:
        def wait(self):
            order.append("closed")
            return 0

    blocked = guided.guide_step("codex", "login", launcher=lambda *a, **kw: SlowChild(), detector=detector,
                                system="Windows")
    order.append("returned")
    assert order == ["closed", "returned"] and blocked["console_exit_code"] == 0


def test_unsupported_steps_never_open_console():
    def forbidden(*args, **kwargs):
        pytest.fail("No console may open")

    for app, step in (("agy", "install"), ("unknown", "login"), ("claude", "remove")):
        with pytest.raises(ValueError):
            guided.guide_step(app, step, launcher=forbidden, detector=forbidden, system="Windows")
    result = guided.guide_step("agy", "login", launcher=forbidden, detector=forbidden, system="Windows")
    assert "Inicia sesión tú en agy" in result["plan"]["message"]
    assert result["recheck"] is None


def test_non_windows_never_opens_console():
    with pytest.raises(RuntimeError, match="Windows"):
        guided.guide_step("codex", "install", launcher=lambda *a, **kw: pytest.fail("opened"),
                          detector=lambda **kw: pytest.fail("detected"), system="Linux")


def test_active_document_policy_requires_open_approval():
    with pytest.raises(EffectNotAuthorized):
        guided.guide_step("codex", "login", policy=object(), evidence=(), system="Windows",
                          launcher=lambda *a, **kw: pytest.fail("opened"),
                          detector=lambda **kw: pytest.fail("detected"))


def test_only_one_console_at_a_time():
    assert guided._console_lock.acquire(blocking=False)
    try:
        with pytest.raises(RuntimeError, match="already open"):
            guided.guide_step("codex", "login", system="Windows",
                              launcher=lambda *a, **kw: pytest.fail("opened"),
                              detector=lambda **kw: pytest.fail("detected"))
    finally:
        guided._console_lock.release()


def test_cli_subcommands_reach_guided_step(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(guided, "guide_step", lambda app, step, **kw: seen.append((app, step, kw)) or {"ok": True})
    args = cli.build_parser().parse_args(["setup", "login", "agy"])
    args.func(args)
    assert seen == [("agy", "login", {"context": "user_console"})]
    assert json.loads(capsys.readouterr().out) == {"ok": True}
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["setup", "install", "agy"])
