"""GEM-T03: the supervised Antigravity CLI (agy) adapter, driven with a fake program and synthetic stream-json.

The fake ``agy`` is a Python script written in ``tmp_path`` and configured explicitly as the adapter's program
(``[sys.executable, script]``): the real binary is never run. An autouse fixture makes any attempt to resolve a program
called ``agy`` fail the test, so a slip cannot spend quota. The event shapes follow real runs of
``agy -p ... --output-format stream-json`` (agy 1.5.10, 2026-11-18): ``init`` with ``conversation_id``, ``step_update``
with a per-step ``usage``, a final ``result`` with ``status``, ``response``, ``usage`` and ``denied_actions``; the
``/usage`` lines follow ``agy -p /usage --output-format text``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import PROJECT_ROOT, Settings
from multiagent.executors import antigravity as agy
from multiagent.executors import process
from multiagent.executors.antigravity import (
    ADAPTER_ID, NOTICE_ID, AntigravityAdapter, AntigravityAdapterError, applicable_limits, classify_agy_limit,
    fixed_prompt, parse_stream, print_timeout_seconds, prompt_file_name, result_file_name, session_id_of,
)
from multiagent.executors.contract import (
    UNTRUSTED_DATA_CLAUSE, ExecutorApplicationAdapter, ExecutorLaunchRequest, FailureKind, LimitClassifier, LimitWindow,
    Permission, ResetSource, RunStatus,
)
from multiagent.executors.defaults import antigravity_adapter, default_adapters
from multiagent.executors.limits import LimitContext
from multiagent.executors.privacy import DATA_CLASSES
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing import validate_executor_config
from multiagent.services.assignment import AssignmentServices
from multiagent.services.errors import ServiceError
from multiagent.sessions.usage_sources import parse_agy_usage
from multiagent.tasks.envelopes import TASK_RESULT_SCHEMA, build_task_envelope

from test_executor_services import RESULT, attempt_events, make_services, refused, routing_config

CID = "6bf8389f-7773-4085-8db4-9cb3492c674b"
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
CONTEXT = LimitContext(now=lambda: NOW, zone=timezone.utc)
MODEL = "gemini-4-argon-high"


def usage_text(gemini_week=90, gemini_5h=75, other_week=100, other_5h=100, argon=40) -> str:
    return (f"Gemini Models\tWeekly Limit Remaining\t{gemini_week}%\t2026-11-20T10:00:00Z\n"
            f"Gemini Models\tFive Hour Limit Remaining\t{gemini_5h}%\t2026-11-18T15:00:00Z\n"
            f"Claude and GPT models\tWeekly Limit Remaining\t{other_week}%\t2026-11-21T10:00:00Z\n"
            f"Claude and GPT models\tFive Hour Limit Remaining\t{other_5h}%\t2026-11-18T16:00:00Z\n"
            f"Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t{argon}%\t2026-11-19T10:00:00Z\n")


FAKE_AGY = r'''
import json, os, re, sys, time

spec = json.load(open(os.environ["FAKE_AGY_SPEC"], encoding="utf-8"))
argv = sys.argv[1:]
usage_call = argv[:2] == ["-p", "/usage"]
record = {"argv": argv, "cwd": os.getcwd(), "usage_call": usage_call,
          "env": {name: os.environ.get(name) for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY",
                                                          "NoDefaultCurrentDirectoryInExePath")},
          "scrubbed_vars": sorted(k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC")))}
if not usage_call and "-p" in argv:
    match = re.search(r"Read (\S+) in the current folder", argv[argv.index("-p") + 1])
    if match and os.path.exists(match.group(1)):
        record["prompt_file"] = match.group(1)
        with open(match.group(1), encoding="utf-8") as handle:
            record["prompt_text"] = handle.read()
with open(os.environ["FAKE_AGY_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(record) + "\n")
part = spec["usage"] if usage_call else spec["run"]
for name, text in part.get("write_files", {}).items():
    with open(name, "w", encoding="utf-8") as handle:
        handle.write(text)
out = part.get("stdout", [])
for line in ([out] if isinstance(out, str) else out):
    sys.stdout.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
sys.stdout.flush()
sys.stderr.write(part.get("stderr", ""))
sys.stderr.flush()
time.sleep(part.get("sleep", 0))
sys.exit(part.get("exit_code", 0))
'''


# --------------------------------------------------------------------------- the events agy prints

def init_event(cid=CID, model=MODEL):
    return {"event": "init", "conversation_id": cid,
            "init": {"model": model, "cwd": "C:\\work", "tools": ["read_file"], "permission_mode": "request-review"}}


def step(index, kind="agent_response", state="DONE", usage=None, delta=None, cid=CID):
    data = {"conversation_id": cid, "step_index": index, "state": state, "step_type": kind}
    if usage is not None:
        data["usage"] = usage
    if delta is not None:
        data["text_delta"] = delta
    return {"event": "step_update", "step_update": data}


def tokens(inp, out, thinking, cache):
    return {"input_tokens": inp, "output_tokens": out, "thinking_tokens": thinking, "cache_read_tokens": cache,
            "total_tokens": inp + out}


STEP_A = tokens(9294, 713, 663, 0)
STEP_B = tokens(2211, 157, 105, 8598)


def result_event(status="SUCCESS", response="", usage=None, cid=CID, **extra):
    usage = usage or tokens(9294 + 2211, 713 + 157, 663 + 105, 8598)
    return {"event": "result", "result": {"conversation_id": cid, "status": status, "response": response,
                                          "duration_seconds": 7.9489031, "num_turns": 1, "usage": usage, **extra}}


def make_envelope(**over):
    fields = dict(task_id="GEM-T99", group_id="GEM", revision=3, attempt=1, assignee="executor:antigravity_cli",
                  status="running", objective="Review the module and list the defects.",
                  constraints=["Do not touch the tests."], file_scope=[], gate=["review"], base_commit="5c34b7d",
                  events_after=7)
    fields.update(over)
    return build_task_envelope(**fields)


def result_envelope(**over):
    data = {"schema": TASK_RESULT_SCHEMA, "task_id": "GEM-T99", "attempt": 1, "base_commit": "5c34b7d", "patch": None,
            "summary": "Reviewed: two defects found.", "decisions": ["Kept the API"], "contract_changes": "",
            "tests_not_run": ["full suite"], "requests": []}
    data.update(over)
    return data


def fenced(envelope, intro="Done.\n\n"):
    return f"{intro}```json\n{json.dumps(envelope, indent=2)}\n```\n"


def good_run(response=None, **extra):
    return [init_event(), step(0, "user_input"), step(1, usage=STEP_A), step(2, "tool", "ACTIVE"), step(2, "tool"),
            step(3, usage=STEP_B), result_event(response=fenced(result_envelope()) if response is None else response,
                                                **extra)]


def make_request(workdir, *, write=False, **kwargs):
    envelope = kwargs.pop("envelope", None) or make_envelope(file_scope=["src/app.py"] if write else [])
    kwargs.setdefault("model", MODEL)
    kwargs.setdefault("timeout_seconds", 60)
    return ExecutorLaunchRequest(task_id=envelope["task_id"], task_envelope=envelope, workdir=Path(workdir),
                                 permission=Permission.WRITE if write else Permission.READ, **kwargs)


class FakeAgy:
    """The generated program, the scenario it plays and the calls it recorded."""

    def __init__(self, folder: Path, monkeypatch):
        folder.mkdir(parents=True, exist_ok=True)
        self.script = folder / "fake_agy.py"
        self.script.write_text(FAKE_AGY, encoding="utf-8")
        self.spec_path = folder / "spec.json"
        self.log = folder / "calls.jsonl"
        monkeypatch.setenv("FAKE_AGY_SPEC", str(self.spec_path))
        monkeypatch.setenv("FAKE_AGY_LOG", str(self.log))
        self.set()

    @property
    def program(self) -> list[str]:
        return [sys.executable, str(self.script)]

    def set(self, *, usage=None, run=None) -> None:
        spec = {"usage": usage if usage is not None else {"stdout": usage_text()},
                "run": run if run is not None else {"stdout": good_run()}}
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def runs(self) -> list[dict]:
        return [call for call in self.calls() if not call["usage_call"]]


@pytest.fixture(autouse=True)
def never_the_real_agy(monkeypatch):
    real = process.find_executable

    def guard(name, **kwargs):
        if name in ("agy", "claude", "codex", "copilot"):
            raise AssertionError(f"the bare name {name!r} was looked up: a test would start the real program")
        return real(name, **kwargs)

    monkeypatch.setattr(process, "find_executable", guard)
    for name in ("CLAUDE_CODE_TEST", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "not-for-the-child")


@pytest.fixture
def fake(tmp_path, monkeypatch) -> FakeAgy:
    return FakeAgy(tmp_path / "bin", monkeypatch)


@pytest.fixture
def workdir(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "module.py").write_text("print('hello')\n", encoding="utf-8")
    return folder


def make_adapter(fake: FakeAgy, tmp_path: Path, **kwargs) -> AntigravityAdapter:
    kwargs.setdefault("notice_accepted", NOTICE_ID)
    kwargs.setdefault("kill_switch", tmp_path / "state" / "antigravity-off")
    kwargs.setdefault("lock_path", tmp_path / "state" / "antigravity-run.lock")
    kwargs.setdefault("kill_poll_seconds", 0.05)
    kwargs.setdefault("limit_context", CONTEXT)
    return AntigravityAdapter(fake.program, **kwargs)


@pytest.fixture
def adapter(fake, tmp_path) -> AntigravityAdapter:
    return make_adapter(fake, tmp_path)


def refused_with(code, call):
    with pytest.raises(AntigravityAdapterError) as caught:
        call()
    assert caught.value.code == code, caught.value
    return caught.value


# --------------------------------------------------------------------------- contract, wiring, off by default

def bundled_declaration() -> dict:
    data = yaml.safe_load((PROJECT_ROOT / "catalog" / "models.yaml").read_text(encoding="utf-8"))
    return validate_executor_config(data["executor_routing"])["executors"]["antigravity_cli"]


def test_the_adapter_has_the_contract_of_the_other_executors_and_ships_in_the_default_wiring():
    adapter = AntigravityAdapter()
    assert isinstance(adapter, ExecutorApplicationAdapter) and isinstance(adapter, LimitClassifier)
    assert adapter.adapter_id == ADAPTER_ID == "antigravity_cli"
    assert [type(item) for item in default_adapters()][-1] is AntigravityAdapter
    registry = ExecutorRegistry(validate_executor_config({"version": 1, "executors": {
        "antigravity_cli": {**bundled_declaration()}}}), [adapter])
    assert registry.resolve("antigravity_cli") is adapter


def test_it_is_off_by_default_and_needs_both_the_catalog_and_the_accepted_notice():
    declaration = bundled_declaration()
    assert declaration["enabled"] is False and declaration["adapter_id"] == "antigravity_cli"
    assert AntigravityAdapter(notice_accepted=NOTICE_ID).describe(declaration).enabled is False   # catalog: off
    enabled = {**declaration, "enabled": True}
    assert AntigravityAdapter().describe(enabled).enabled is False                                # no notice
    assert AntigravityAdapter(notice_accepted="yes").describe(enabled).enabled is False           # not the notice id
    assert AntigravityAdapter(notice_accepted="agy-third-party-notice/0").describe(enabled).enabled is False
    descriptor = AntigravityAdapter(notice_accepted=NOTICE_ID).describe(enabled)
    assert descriptor.enabled is True and descriptor.mode == "automatic" and descriptor.adapter_id == ADAPTER_ID
    assert descriptor.effort_support.status.value == "unsupported" and descriptor.resume.status.value == "unknown"
    assert "gemini-4-argon-high" in descriptor.model_support.values


def test_the_settings_configure_the_default_adapter(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", antigravity_add_dirs=str(tmp_path),
                        antigravity_kill_switch="")
    adapter = antigravity_adapter(settings)
    assert adapter.notice_accepted is False and adapter.launch_blocker() == agy.NOTICE_NOT_ACCEPTED
    assert adapter.kill_switch == settings.data_dir / "antigravity-off"
    assert settings.antigravity_lock_file == settings.data_dir / "antigravity-run.lock"
    assert sys.prefix in adapter._add_dirs and sys.base_prefix in adapter._add_dirs
    assert str(tmp_path) in adapter._add_dirs
    accepted = antigravity_adapter(settings.model_copy(update={"antigravity_notice_accepted": NOTICE_ID}))
    assert accepted.notice_accepted is True and accepted.launch_blocker() is None
    with pytest.raises(ValueError):
        Settings(_env_file=None, local_dir=tmp_path / "local", antigravity_add_dirs="relative/folder")


def test_a_launch_without_the_accepted_notice_starts_nothing(fake, tmp_path, workdir):
    adapter = make_adapter(fake, tmp_path, notice_accepted="")
    refused_with(agy.NOTICE_NOT_ACCEPTED, lambda: adapter.launch(make_request(workdir)))
    assert fake.calls() == []


# --------------------------------------------------------------------------- the command

def test_read_runs_in_plan_mode_write_in_auto_mode_and_no_dangerous_flag_is_ever_passed(fake, tmp_path, workdir):
    extra = tmp_path / "venv"
    extra.mkdir()
    adapter = make_adapter(fake, tmp_path, add_dirs=[extra])
    read = adapter.build_command(make_request(workdir, effort="high"))
    name = prompt_file_name(make_request(workdir))
    assert read == [*fake.program, "-p", fixed_prompt(name), "--model", MODEL, "--mode", "plan",
                    "--output-format", "stream-json", "--print-timeout", "48s"]
    write = adapter.build_command(make_request(workdir, write=True, model=None, timeout_seconds=1800))
    assert write == [*fake.program, "-p", fixed_prompt(name), "--mode", "auto", "--output-format", "stream-json",
                     "--print-timeout", "1740s", "--add-dir", str(extra)]
    for argv in (read, write):
        assert not any(flag in argv for flag in ("--dangerously-skip-permissions", "--sandbox", "--settings",
                                                 "--effort", "--conversation", "--continue"))
        assert all("\n" not in part for part in argv)
    assert print_timeout_seconds(5) == 4 and print_timeout_seconds(1) == 1
    refused_with("invalid_model", lambda: adapter.build_command(make_request(workdir, model="gemini 4; rm")))
    refused_with(agy.RESUME_NOT_SUPPORTED, lambda: adapter.build_resume_command(make_request(workdir), CID))
    refused_with(agy.RESUME_NOT_SUPPORTED, lambda: adapter.resume(make_request(workdir), CID))
    assert adapter.open_target(CID) is None


def test_the_prompt_carries_the_envelope_the_rules_and_the_result_file_only_for_write(adapter, workdir):
    read = adapter.render_prompt(make_request(workdir))
    assert "READ ONLY (agy plan mode)" in read and UNTRUSTED_DATA_CLAUSE in read and '"task_id":"GEM-T99"' in read
    assert result_file_name(make_request(workdir)) not in read
    write = adapter.render_prompt(make_request(workdir, write=True))
    assert "Permission for this run: WRITE" in write and result_file_name(make_request(workdir)) in write


# --------------------------------------------------------------------------- a run

def test_a_run_returns_the_envelope_the_conversation_and_the_usage_summed_from_the_steps(fake, adapter, workdir):
    result = adapter.launch(make_request(workdir, effort="high"))

    assert result.status is RunStatus.COMPLETED and result.session_id == CID
    assert result.result_envelope["summary"] == "Reviewed: two defects found."
    inp, cache, out = 9294 + 2211, 8598, 713 + 157
    assert result.usage == {"input_tokens": inp, "cache_read_tokens": cache, "output_tokens": out,
                            "thinking_tokens": 663 + 105, "total_tokens": inp + cache + out,
                            "agy_total_tokens": inp + out, "usage_source": "step_update", "num_turns": 1,
                            "duration_seconds": 7.949}
    assert agy.EFFORT_NOTE in result.notes
    assert any(note.startswith("quota_checked: agy /usage: Gemini 4 Argon") for note in result.notes)
    usage_call, run = fake.calls()
    assert usage_call["usage_call"] and usage_call["argv"] == list(agy.USAGE_ARGS)
    assert Path(run["cwd"]).resolve() == workdir.resolve()
    assert run["prompt_file"] == prompt_file_name(make_request(workdir))
    assert '"task_id":"GEM-T99"' in run["prompt_text"]
    assert not (workdir / run["prompt_file"]).exists()                    # the prompt file is removed after the run
    assert result.command.argv[0] == sys.executable and "--mode" in result.command.argv


def test_the_stream_parser_never_counts_a_step_twice_and_falls_back_to_the_result_usage():
    lines = [json.dumps(event) for event in (
        init_event(), step(1, usage=STEP_A), step(1, usage=STEP_A), step(3, usage=STEP_B),
        result_event(response=""))]
    parsed = parse_stream("\n".join(["not json", '{"broken', *lines]))
    assert parsed.usage["input_tokens"] == 9294 + 2211 and parsed.usage["usage_source"] == "step_update"
    cut = parse_stream("\n".join(lines[2:]), truncated=True)                 # the head was lost: the result's usage
    assert cut.usage["usage_source"] == "result" and cut.usage["input_tokens"] == 9294 + 2211
    only_result = parse_stream(json.dumps(result_event(usage=tokens(10, 5, 1, 20))))
    assert only_result.usage["total_tokens"] == 35 and only_result.session_id == CID
    assert parse_stream("").usage is None and parse_stream("").result_status is None
    deltas = parse_stream("\n".join(json.dumps(e) for e in (
        step(5, delta="Part one, "), step(5, delta="part two."), result_event(response=""))))
    assert deltas.response == "Part one, part two."
    assert session_id_of('lost head"}\n' + json.dumps(step(9))) == CID and session_id_of("plain text") is None


def test_an_empty_final_answer_fails_the_attempt_and_a_write_run_may_use_its_result_file(fake, adapter, workdir):
    fake.set(run={"stdout": good_run(response="")})
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and result.result_envelope is None
    assert "no result envelope: the final response was empty" in result.notes

    name = result_file_name(make_request(workdir))
    fake.set(run={"stdout": good_run(response=""), "write_files": {name: json.dumps(result_envelope())}})
    written = adapter.launch(make_request(workdir, write=True))
    assert written.status is RunStatus.COMPLETED and written.result_envelope["summary"].startswith("Reviewed")
    assert "result_from_file" in written.notes and not (workdir / name).exists()   # never left for the patch

    (workdir / name).write_text(json.dumps(result_envelope(summary="planted")), encoding="utf-8")
    run_count = len(fake.runs())
    refused_with("result_file_occupied", lambda: adapter.launch(make_request(workdir, write=True)))
    assert (workdir / name).exists() and len(fake.runs()) == run_count
    (workdir / name).unlink()

    prompt = workdir / prompt_file_name(make_request(workdir))
    prompt.write_text("keep me", encoding="utf-8")
    refused_with("prompt_file_occupied", lambda: adapter.launch(make_request(workdir)))
    assert prompt.read_text(encoding="utf-8") == "keep me" and len(fake.runs()) == run_count
    prompt.unlink()


def test_a_timeout_kills_the_run_and_agy_print_timeout_is_a_timeout_too(fake, adapter, workdir):
    fake.set(run={"stdout": [init_event()], "sleep": 30})
    started = time.monotonic()
    result = adapter.launch(make_request(workdir, timeout_seconds=2))
    assert result.status is RunStatus.TIMEOUT and time.monotonic() - started < 20
    assert result.session_id == CID

    fake.set(run={"stdout": good_run(response="partial", status="ERROR", finished_reason="print_timeout",
                                     error="print timeout after 40m0s"), "exit_code": 3,
                  "stderr": 'AGY_ERROR: {"status":"DEADLINE_EXCEEDED","reason":"print_timeout","retryable":false}\n'})
    printed = adapter.launch(make_request(workdir))
    assert printed.status is RunStatus.TIMEOUT and "agy print timeout: the response may be incomplete" in printed.notes


def test_a_denied_action_is_a_permission_denial_with_blank_tails_even_with_a_result(fake, adapter, workdir):
    fake.set(run={"stdout": good_run(response="", denied_actions=[{"action": "command", "display_name": "Bash"},
                                                                   {"action": "bad name!"}]),
                  "stderr": 'jetski: no output produced - a tool required the "command" permission\n'})
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and result.failure_kind is FailureKind.PERMISSION_DENIED
    assert result.stdout_tail == "" and result.stderr_tail == "" and "denied_actions: command" in result.notes

    # With a valid envelope too: the envelope is the model's text and does not show that the refused work was done.
    fake.set(run={"stdout": good_run(denied_actions=[{"action": "read_file", "display_name": "ReadFile"}])})
    refused = adapter.launch(make_request(workdir))
    assert refused.status is RunStatus.FAILED and refused.failure_kind is FailureKind.PERMISSION_DENIED
    assert refused.result_envelope is None and "denied_actions: read_file" in refused.notes


def test_a_read_run_that_changes_the_folder_fails_and_so_does_one_without_plan_mode(fake, adapter, workdir):
    fake.set(run={"stdout": good_run(), "write_files": {"escape.txt": "x"}})
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and result.result_envelope is None
    assert any(note.startswith("read_only_folder_changed") for note in result.notes)
    (workdir / "escape.txt").unlink()

    warning = "warning: --mode plan is not available for this account; continuing in the default mode.\n"
    fake.set(run={"stdout": good_run(), "stderr": warning})
    plan_missing = adapter.launch(make_request(workdir))
    assert plan_missing.status is RunStatus.FAILED and any(
        note.startswith("read_only_mode_unavailable") for note in plan_missing.notes)

    fake.set(run={"stdout": good_run(), "stderr": warning.replace("plan", "auto")})
    auto_missing = adapter.launch(make_request(workdir, write=True))
    assert auto_missing.status is RunStatus.COMPLETED
    assert "mode_unavailable: agy ran 'auto' in its default mode" in auto_missing.notes


def test_a_read_run_refuses_an_incomplete_folder_snapshot(fake, adapter, workdir, monkeypatch):
    monkeypatch.setattr(agy, "MAX_SNAPSHOT_ENTRIES", 2)
    refused_with("read_snapshot_too_large", lambda: adapter.launch(make_request(workdir)))
    assert fake.runs() == []


# --------------------------------------------------------------------------- caps: kill switch, quota, one at a time

def test_the_kill_switch_stops_every_launch_and_a_running_agy(fake, adapter, workdir):
    adapter.kill_switch.parent.mkdir(parents=True, exist_ok=True)
    adapter.kill_switch.write_text("", encoding="utf-8")
    assert adapter.launch_blocker() == agy.KILL_SWITCH_ON
    refused_with(agy.KILL_SWITCH_ON, lambda: adapter.launch(make_request(workdir)))
    assert fake.calls() == []                                              # not even /usage
    adapter.kill_switch.unlink()

    fake.set(run={"stdout": [init_event()], "write_files": {str(adapter.kill_switch): "stop"}, "sleep": 30})
    started = time.monotonic()
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.CANCELLED and "kill_switch: the run was stopped" in result.notes
    assert time.monotonic() - started < 20
    assert not agy._PROCESS_LOCK.locked() and not adapter._lock.busy()          # the lock was released


def test_the_kill_switch_stops_the_usage_probe(fake, adapter, workdir):
    fake.set(usage={"stdout": usage_text(), "write_files": {str(adapter.kill_switch): "stop"}, "sleep": 30})
    started = time.monotonic()
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.CANCELLED and fake.runs() == []
    assert time.monotonic() - started < 20
    assert not agy._PROCESS_LOCK.locked() and not adapter._lock.busy()


def test_under_the_launch_floor_nothing_is_launched(fake, adapter, workdir):
    fake.set(usage={"stdout": usage_text(gemini_week=12)})
    error = refused_with(agy.QUOTA_BELOW_LAUNCH_FLOOR, lambda: adapter.launch(make_request(workdir)))
    assert "Gemini Models, weekly limit, 12% remaining" in str(error)
    assert fake.runs() == [] and len(fake.calls()) == 1
    fake.set(usage={"stdout": usage_text(gemini_week=15)})                # exactly 15% still launches
    assert adapter.launch(make_request(workdir)).status is RunStatus.COMPLETED


@pytest.mark.parametrize("remaining", [12, 8])
def test_an_explicit_quota_warning_acceptance_launches_with_plan_quota(fake, adapter, workdir, remaining):
    fake.set(usage={"stdout": usage_text(gemini_week=remaining)})
    result = adapter.launch(make_request(workdir, quota_warning_accepted=True))
    assert result.status is RunStatus.COMPLETED and len(fake.runs()) == 1


@pytest.mark.parametrize("accepted", [False, True])
def test_zero_plan_quota_is_a_hard_limit_even_when_accepted(fake, adapter, workdir, accepted):
    fake.set(usage={"stdout": usage_text(gemini_5h=0)})
    result = adapter.launch(make_request(workdir, quota_warning_accepted=accepted))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and fake.runs() == []
    assert result.limit.kind is FailureKind.PLAN_LIMIT
    assert result.limit.reset_at == "2026-11-18T15:00:00Z"
    assert "not_started: plan limit reached" in result.notes


def test_acceptance_does_not_skip_an_unavailable_usage_probe(fake, adapter, workdir):
    fake.set(usage={"stdout": "", "exit": 1})
    refused_with(agy.USAGE_UNAVAILABLE,
                 lambda: adapter.launch(make_request(workdir, quota_warning_accepted=True)))
    assert fake.runs() == []


def test_under_the_pause_floor_the_result_is_a_plan_limit_at_the_reset_usage_gave(fake, adapter, workdir):
    fake.set(usage={"stdout": usage_text(gemini_5h=8)})
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and fake.runs() == []
    assert result.limit.kind is FailureKind.PLAN_LIMIT and result.limit.window is LimitWindow.FIVE_HOUR
    assert result.limit.reset_at == "2026-11-18T15:00:00Z" and result.limit.reset_source is ResetSource.APP_EPOCH
    assert "failure:quota" in result.notes and result.command.argv[-5:] == ("/usage", "--output-format", "text",
                                                                            "--print-timeout", "30s")


def test_the_floors_read_the_model_group_and_the_model_line_only():
    rows, _ = parse_agy_usage(usage_text(gemini_week=5, argon=9))
    assert {row.name for row in applicable_limits(rows, "claude-opus-5-5-high")} == {"Claude and GPT models"}
    assert {row.name for row in applicable_limits(rows, "gpt-oss-120b-medium")} == {"Claude and GPT models"}
    assert {row.name for row in applicable_limits(rows, "gemini-3.8-flash-high")} == {"Gemini Models"}
    assert {row.name for row in applicable_limits(rows, MODEL)} == {"Gemini Models", "Gemini 4 Argon"}
    assert {row.name for row in applicable_limits(rows, "gemini-pro-orchestrator-g1-pro")} == {
        "Gemini Models", "Gemini 4 Argon"}
    assert len(applicable_limits(rows, None)) == len(rows) == len(applicable_limits(rows, "kimi-k3"))
    assert agy.check_quota(rows, "claude-sonnet-5-5-low").decision == "launch"
    rows, _ = parse_agy_usage(usage_text(argon=9))
    paused = agy.check_quota(rows, MODEL)
    assert paused.decision == "pause" and paused.binding.window == "model_weekly"
    assert agy.check_quota(rows, "gemini-3.8-flash-low").decision == "launch"
    assert agy.check_quota(rows, None).decision == "pause"                 # agy's default model: every line counts
    assert agy.check_quota(rows, MODEL, warning_accepted=True).decision == "launch"
    zero, _ = parse_agy_usage(usage_text(argon=0))
    assert agy.check_quota(zero, MODEL, warning_accepted=True).decision == "pause"
    only_gemini, _ = parse_agy_usage("Gemini Models\tWeekly Limit Remaining\t90%\t2026-11-20T10:00:00Z\n")
    refused_with(agy.USAGE_UNAVAILABLE, lambda: agy.check_quota(only_gemini, "claude-opus-5-5-high"))
    refused_with(agy.USAGE_UNAVAILABLE, lambda: agy.check_quota(only_gemini, MODEL))
    mixed, _ = parse_agy_usage(
        "Gemini Models\tWeekly Limit Remaining\t90%\t2026-11-20T10:00:00Z\n"
        "Claude and GPT models\tFive Hour Limit Remaining\t90%\t2026-11-18T16:00:00Z\n")
    refused_with(agy.USAGE_UNAVAILABLE, lambda: agy.check_quota(mixed, None))


@pytest.mark.parametrize("usage", [{"stdout": "", "exit_code": 1}, {"stdout": "Sign in to continue."},
                                   {"stdout": usage_text(), "stderr": "RESOURCE_EXHAUSTED: quota reached\n"},
                                   {"stdout": usage_text(), "sleep": 30}])
def test_a_usage_that_cannot_be_read_refuses_the_launch(fake, tmp_path, workdir, usage):
    adapter = make_adapter(fake, tmp_path, usage_timeout_seconds=2)
    fake.set(usage=usage)
    refused_with(agy.USAGE_UNAVAILABLE, lambda: adapter.launch(make_request(workdir)))
    assert fake.runs() == []


def test_a_truncated_usage_output_never_authorizes_a_launch(fake, adapter, workdir):
    fake.set(usage={"stdout": "X" * 70_000 + "\n" + usage_text()})
    refused_with(agy.USAGE_UNAVAILABLE, lambda: adapter.launch(make_request(workdir)))
    assert fake.runs() == []


def test_one_run_at_a_time_in_this_process_and_across_processes(fake, tmp_path, workdir):
    first, second = make_adapter(fake, tmp_path), make_adapter(fake, tmp_path)
    fake.set(run={"stdout": good_run(), "sleep": 2})
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.setdefault("result", first.launch(make_request(workdir))))
    thread.start()
    deadline = time.monotonic() + 15
    while not (first._lock.path.exists() and fake.calls()) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert second.launch_blocker() == agy.RUN_IN_PROGRESS
    refused_with(agy.RUN_IN_PROGRESS, lambda: second.launch(make_request(workdir)))
    thread.join(30)
    assert outcome["result"].status is RunStatus.COMPLETED
    assert second.launch_blocker() is None


HOLDER = (
    "import os, sys\n"
    "from multiagent.executors.antigravity import lock_file\n"
    "descriptor = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
    "print('held' if lock_file(descriptor) else 'busy', flush=True)\n"
    "sys.stdin.readline()\n"
)


def test_another_process_holds_the_lock_until_it_ends_even_when_it_dies(fake, tmp_path, workdir):
    adapter = make_adapter(fake, tmp_path)
    lock = adapter._lock.path
    lock.parent.mkdir(parents=True, exist_ok=True)
    root = str(Path(__file__).resolve().parents[1])
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(lock)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, cwd=root, env={**os.environ, "PYTHONPATH": root})
    try:
        assert holder.stdout.readline().strip() == "held"
        assert adapter.launch_blocker() == agy.RUN_IN_PROGRESS
        refused_with(agy.RUN_IN_PROGRESS, lambda: adapter.launch(make_request(workdir)))
        assert fake.calls() == []
    finally:
        holder.kill()                                                       # the holder dies without releasing
        holder.wait(30)
    fake.set()
    deadline = time.monotonic() + 20                                        # Windows frees it shortly after the exit
    while adapter.launch_blocker() is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert adapter.launch_blocker() is None                                 # the system freed the lock
    assert adapter.launch(make_request(workdir)).status is RunStatus.COMPLETED
    assert lock.exists() and not adapter._lock.busy()                       # the file stays; nobody holds it


def test_a_lock_file_left_behind_without_a_holder_does_not_block(fake, adapter, workdir):
    adapter._lock.path.parent.mkdir(parents=True, exist_ok=True)
    adapter._lock.path.write_text('{"pid": 1, "expires_at": 9999999999}', encoding="utf-8")
    assert adapter.launch_blocker() is None
    assert adapter.launch(make_request(workdir)).status is RunStatus.COMPLETED


# --------------------------------------------------------------------------- limits (CNT-T02) and failures

@pytest.mark.parametrize("text, kind, window", [
    ("Error: RESOURCE_EXHAUSTED: you have exhausted your capacity on this model", FailureKind.PLAN_LIMIT,
     LimitWindow.UNKNOWN),
    ("You have exhausted your capacity. Use your AI credits to fulfill model requests once you're out of quota",
     FailureKind.PLAN_LIMIT, LimitWindow.UNKNOWN),
    ("weekly quota exceeded for Gemini Models", FailureKind.PLAN_LIMIT, LimitWindow.WEEKLY),
    ("five hour quota reached", FailureKind.PLAN_LIMIT, LimitWindow.FIVE_HOUR),
    ("HTTP 429 Too Many Requests", FailureKind.LIMIT_UNKNOWN, LimitWindow.UNKNOWN),
    ("rate limit hit, slow down", FailureKind.LIMIT_UNKNOWN, LimitWindow.UNKNOWN),
    ("operation cancelled: actual cost limit exceeded", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
    ("your AI credits are exhausted", FailureKind.CREDITS_EXHAUSTED, LimitWindow.SPEND),
])
def test_agy_limit_messages_are_classified_and_never_rate_limited(text, kind, window):
    limit = classify_agy_limit(text, observed_at=NOW, context=CONTEXT)
    assert limit is not None and limit.kind is kind and limit.window is window
    assert limit.kind is not FailureKind.RATE_LIMITED and limit.evidence


def test_ordinary_failures_are_not_limits_and_a_login_problem_is_authentication(adapter):
    assert classify_agy_limit("error: the tool failed", observed_at=NOW, context=CONTEXT) is None
    assert adapter.classify_failure(0, "quota") is FailureKind.OTHER
    assert adapter.classify_failure(1, "error: not signed in, run agy login") is FailureKind.AUTHENTICATION
    assert adapter.classify_failure(1, "panic: something else") is FailureKind.OTHER


def test_a_run_that_hits_the_quota_is_a_quota_stop_from_stderr_not_from_the_answer(fake, adapter, workdir):
    fake.set(run={"stdout": good_run(response="I read about quota limits.", status="ERROR",
                                     error="RESOURCE_EXHAUSTED: exhausted your capacity"), "exit_code": 1})
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.limit.kind is FailureKind.PLAN_LIMIT
    assert "failure:quota" in result.notes and "result_status:ERROR" in result.notes

    fake.set(run={"stdout": good_run(status="RESOURCE_EXHAUSTED", error="")})
    status_only = adapter.launch(make_request(workdir))
    assert status_only.status is RunStatus.QUOTA_EXHAUSTED
    assert status_only.limit.kind is FailureKind.PLAN_LIMIT

    fake.set(run={"stdout": good_run(), "stderr": "RESOURCE_EXHAUSTED: quota reached\n"})
    stderr_only = adapter.launch(make_request(workdir))
    assert stderr_only.status is RunStatus.QUOTA_EXHAUSTED
    assert stderr_only.limit.kind is FailureKind.PLAN_LIMIT

    fake.set(run={"stdout": good_run(response="Out of quota? 429? RESOURCE_EXHAUSTED?", status="ERROR",
                                     error="the tool crashed"), "exit_code": 1})
    crashed = adapter.launch(make_request(workdir))                      # the answer's words are never classified
    assert crashed.status is RunStatus.FAILED and crashed.limit is None and "failure:other" in crashed.notes

    fake.set(run={"stdout": good_run(response=fenced(result_envelope(summary="quota checked: 429 is fine")))})
    clean = adapter.launch(make_request(workdir))
    assert clean.status is RunStatus.COMPLETED and clean.limit is None

    fake.set(run={"stdout": [init_event()], "stderr": "error: not authenticated\n", "exit_code": 2})
    login = adapter.launch(make_request(workdir))
    assert login.status is RunStatus.FAILED and login.failure_kind is FailureKind.AUTHENTICATION
    assert "no_result_event" in login.notes


# --------------------------------------------------------------------------- credentials and privacy

def test_no_credential_reaches_agy_and_nothing_under_the_home_folder_is_read(fake, adapter, workdir, monkeypatch):
    def no_home(*args, **kwargs):
        raise AssertionError("the adapter looked at the home folder")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: no_home()))
    monkeypatch.setattr(os.path, "expanduser", no_home)
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.COMPLETED
    for call in fake.calls():
        assert call["scrubbed_vars"] == [] and call["env"]["GEMINI_API_KEY"] == ""
        assert call["env"]["NoDefaultCurrentDirectoryInExePath"] == "1"
        assert "not-for-the-child" not in json.dumps(call)
    assert "--settings" not in result.command.argv


def test_the_privacy_check_refuses_before_anything_is_written_or_started(fake, adapter, workdir):
    request = make_request(workdir, data_class=DATA_CLASSES[-1])
    refused_with("privacy_class_unknown", lambda: adapter.launch(request))
    assert fake.calls() == [] and sorted(path.name for path in workdir.iterdir()) == ["module.py"]


# --------------------------------------------------------------------------- through the executor service

def agy_world(tmp_path, fake, *, notice=NOTICE_ID, enabled=True):
    services = make_services(tmp_path / "local")
    config = routing_config(enabled=())
    config["executors"]["antigravity_cli"] = {**bundled_declaration(), "enabled": enabled}
    settings = services.settings
    adapter = AntigravityAdapter(fake.program, notice_accepted=notice, kill_switch=settings.antigravity_kill_switch,
                                 lock_path=settings.antigravity_lock_file, kill_poll_seconds=0.05,
                                 limit_context=CONTEXT)
    services.system.catalog.executor_routing = config
    services.system.catalog.executor_routing_valid = True
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, [adapter]))
    services.executors.clock = lambda: NOW
    services.tasks.create_group("GEM", "Antigravity", actor="user", authorized=True)
    services.tasks.create_task("GEM", "T1", "Read the module", actor="user", objective="Summarize the module.")
    return services, adapter


def service_run(result=RESULT):
    return {"stdout": [init_event(), step(1, usage=STEP_A), result_event(response=fenced(result), usage=STEP_A)]}


def test_the_executor_service_runs_a_task_through_agy_and_submits_its_result(fake, tmp_path):
    services, _ = agy_world(tmp_path, fake)
    fake.set(run=service_run())
    out = services.executors.run_task("T1", "antigravity_cli", model=MODEL)
    assert out["outcome"] == "submitted" and out["run_status"] == "completed" and out["session_id"] == CID
    assert out["usage"]["total_tokens"] == 9294 + 713 and out["actor"] == "executor:antigravity_cli"
    assert services.tasks.get_task("T1")["state"] == "submitted"
    (run,) = fake.runs()
    assert run["argv"][run["argv"].index("--mode") + 1] == "plan"
    assert Path(run["cwd"]).resolve() == (services.settings.executor_workdir_root / "task-T1").resolve()
    (finished,) = attempt_events(services, "T1")
    assert finished["adapter_id"] == "antigravity_cli" and finished["outcome"] == "submitted"
    status = next(e for e in services.executors.status()["executors"] if e["executor_id"] == "antigravity_cli")
    assert status["enabled"] is True


def test_service_acceptance_applies_to_one_agy_launch(fake, tmp_path):
    services, _ = agy_world(tmp_path, fake)
    fake.set(usage={"stdout": usage_text(gemini_week=8)}, run=service_run())
    out = services.executors.run_task("T1", "antigravity_cli", model=MODEL, accept_quota_warning=True)
    assert out["outcome"] == "submitted" and len(fake.runs()) == 1


def test_resume_due_does_not_inherit_an_accepted_quota_warning(fake, tmp_path):
    services, _ = agy_world(tmp_path, fake)
    low_quota = {"stdout": usage_text(gemini_5h=8)}
    fake.set(usage=low_quota, run={"stdout": good_run(status="ERROR", error="RESOURCE_EXHAUSTED: quota reached"),
                                   "exit_code": 1})
    first = services.executors.run_task("T1", "antigravity_cli", model=MODEL, accept_quota_warning=True)
    assert first["outcome"] == "quota_exhausted" and len(fake.runs()) == 1
    services.executors.clock = lambda: datetime(2027, 1, 1, tzinfo=timezone.utc)
    fake.set(usage=low_quota, run=service_run())
    report = services.executors.resume_due(task_id="T1")
    assert report["resumed"] and len(fake.runs()) == 1
    assert any(call["usage_call"] for call in fake.calls()[2:])


def test_manual_agy_resume_after_an_accepted_launch_stays_unsupported(fake, tmp_path):
    services, _ = agy_world(tmp_path, fake)
    fake.set(usage={"stdout": usage_text(gemini_5h=8)},
             run={"stdout": good_run(status="ERROR", error="RESOURCE_EXHAUSTED: quota reached"), "exit_code": 1})
    first = services.executors.run_task("T1", "antigravity_cli", model=MODEL, accept_quota_warning=True)
    assert first["outcome"] == "quota_exhausted"
    services.executors.clock = lambda: datetime(2027, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ServiceError) as error:
        services.executors.resume_task("T1")
    assert error.value.details["code"] == agy.RESUME_NOT_SUPPORTED
    assert len(fake.runs()) == 1


def test_api_acceptance_reaches_one_agy_launch(fake, tmp_path):
    services, _ = agy_world(tmp_path, fake)
    fake.set(usage={"stdout": usage_text(gemini_week=12)}, run=service_run())
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/executors/run", json={
            "task_id": "T1", "executor_id": "antigravity_cli", "model": MODEL,
            "accept_quota_warning": True})
    assert response.status_code == 200 and response.json()["outcome"] == "submitted"
    assert len(fake.runs()) == 1


def test_cli_quota_option_is_sent_only_on_run(monkeypatch):
    sent = []
    monkeypatch.setattr(cli, "_api_json", lambda method, path, **kwargs: sent.append((path, kwargs["json_body"])) or {})
    cli.main(["executors", "run", "T1", "--executor", "antigravity_cli", "--accept-quota-warning"])
    assert sent[-1][0] == "/api/v1/executors/run" and sent[-1][1]["accept_quota_warning"] is True
    cli.main(["executors", "run", "T2", "--executor", "antigravity_cli"])
    assert "accept_quota_warning" not in sent[-1][1]
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["executors", "resume", "T1", "--accept-quota-warning"])


def test_the_service_refuses_before_the_task_changes_when_agy_is_off(fake, tmp_path):
    services, adapter = agy_world(tmp_path, fake, notice="")
    error = refused(lambda: services.executors.run_task("T1", "antigravity_cli"), reason="executor_disabled")
    assert error.details["code"] == agy.NOTICE_NOT_ACCEPTED
    services, adapter = agy_world(tmp_path / "second", fake)
    adapter.kill_switch.parent.mkdir(parents=True, exist_ok=True)
    adapter.kill_switch.write_text("", encoding="utf-8")
    error = refused(lambda: services.executors.run_task("T1", "antigravity_cli"), reason="executor_unavailable")
    assert error.details["code"] == agy.KILL_SWITCH_ON
    assert services.tasks.get_task("T1")["state"] == "open" and fake.calls() == []


def test_under_the_pause_floor_the_service_pauses_agy_until_the_window_resets(fake, tmp_path):
    services, _ = agy_world(tmp_path, fake)
    fake.set(usage={"stdout": usage_text(gemini_5h=5)})
    out = services.executors.run_task("T1", "antigravity_cli", model=MODEL)
    until = datetime.fromisoformat(out["executor_paused_until"])           # the reset /usage gave, plus the margin
    assert out["outcome"] == "quota_exhausted"
    assert datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc) <= until
    assert until <= datetime(2026, 11, 18, 15, 10, tzinfo=timezone.utc)
    assert fake.runs() == [] and services.tasks.get_task("T1")["state"] == "paused"
    refused(lambda: services.executors.run_task("T1", "antigravity_cli"), reason="executor_paused")


@pytest.mark.parametrize("relative", ["README.md", "docs/EXECUTORS.md", ".env.example"])
def test_the_third_party_notice_id_is_documented_in_each_visible_place(relative):
    text = (Path(__file__).resolve().parents[1] / relative).read_text(encoding="utf-8")
    assert NOTICE_ID in text
