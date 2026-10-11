"""ORQ-T49: the Claude Code adapter, driven with synthetic outputs and a fake ``claude`` generated in ``tmp_path``.

No real ``claude`` is ever started: every launch uses ``command=(sys.executable, fake_claude.py)`` and an autouse guard
fails loudly if the bare name ``claude`` is looked up on PATH. The fake records its argv, stdin, working folder and
environment, and prints whatever the test configured.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.executors import claude, process
from multiagent.executors.claude import ClaudeAdapter, extract_envelope, parse
from multiagent.executors.contract import (
    ExecutorApplicationAdapter, ExecutorLaunchRequest, FailureKind, OpenTarget, Permission, RunStatus,
)
from multiagent.executors.launch_safety import NO_CURRENT_DIR_VARIABLE
from multiagent.executors.process import ProcessLaunchError
from multiagent.executors.registry import ExecutorRegistry
from multiagent.gui import open_in_app
from multiagent.routing import SupportState, validate_executor_config
from multiagent.sessions.models import OpenInApp
from multiagent.tasks.envelopes import (
    TASK_RESULT_SCHEMA, EnvelopeError, build_task_envelope, canonical_json, validate_task_result,
)

SID = "11111111-2222-4333-8444-555555555555"
OTHER_SID = "99999999-8888-4777-8666-555555555555"
RESULT = {"schema": TASK_RESULT_SCHEMA, "base_commit": "e71df4f", "patch": None, "summary": "Read the module.",
          "decisions": ["kept argv free of text"], "contract_changes": "", "tests_not_run": [], "requests": []}
READ_TAIL = ["--permission-mode", "plan", "--allowedTools", "Read", "Grep", "Glob",
             "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit"]
WRITE_TAIL = ["--permission-mode", "acceptEdits", "--allowedTools", "Read", "Grep", "Glob", "Edit", "Write",
              "--disallowedTools", "Bash"]
HOSTILE_OBJECTIVE = 'Report on x.py"; rm -rf / && echo %PATH% | calc\nSecond line with `ticks` and \'quotes\'.'

FAKE_CLAUDE = r'''
import json, os, sys, time

here = os.path.dirname(os.path.abspath(__file__))
config = json.load(open(os.path.join(here, "fake.json"), encoding="utf-8"))
stdin = sys.stdin.buffer.read().decode("utf-8")
with open(os.path.join(here, "calls.jsonl"), "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin, "cwd": os.getcwd(), "env": dict(os.environ)}) + "\n")
args = sys.argv[1:]
sid = ""
for flag in ("--session-id", "--resume"):
    if flag in args:
        sid = args[args.index(flag) + 1]
if config.get("sleep"):
    time.sleep(config["sleep"])
sys.stdout.buffer.write(config.get("stdout", "").replace("__SID__", sid).encode("utf-8"))
sys.stderr.buffer.write(config.get("stderr", "").encode("utf-8"))
sys.exit(config.get("exit", 0))
'''


def success_stdout(result_text: str | None = None, *, session_id: str = "__SID__", **extra) -> str:
    body = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 4321, "num_turns": 3,
            "result": json.dumps(RESULT) if result_text is None else result_text, "session_id": session_id,
            "total_cost_usd": 0.0421, "usage": {"input_tokens": 120, "output_tokens": 45,
                                                "cache_read_input_tokens": 900, "cache_creation_input_tokens": 30,
                                                "service_tier": "standard"}, **extra}
    return json.dumps(body)


class Fake:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir()
        self.script = folder / "fake_claude.py"
        self.script.write_text(FAKE_CLAUDE, encoding="utf-8")
        self.configure(stdout=success_stdout())

    def configure(self, **config) -> None:
        (self.folder / "fake.json").write_text(json.dumps(config), encoding="utf-8")

    def adapter(self, **kwargs) -> ClaudeAdapter:
        return ClaudeAdapter(command=(sys.executable, str(self.script)), **kwargs)

    def calls(self) -> list[dict]:
        path = self.folder / "calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.fixture(autouse=True)
def never_the_real_claude(monkeypatch):
    real = process.find_executable

    def guard(name, **kwargs):
        if name == "claude":
            raise AssertionError("the bare name 'claude' was looked up: a test would start the real program")
        return real(name, **kwargs)

    monkeypatch.setattr(process, "find_executable", guard)


@pytest.fixture
def fake(tmp_path) -> Fake:
    return Fake(tmp_path / "bin")


@pytest.fixture
def workdir(tmp_path) -> Path:
    folder = tmp_path / "worktree"
    folder.mkdir()
    return folder


def make_envelope(**overrides) -> dict:
    fields = {"task_id": "ORQ-T49", "group_id": "ORQ", "revision": 2, "attempt": 1, "assignee": "claude_cli",
              "status": "running", "objective": HOSTILE_OBJECTIVE, "file_scope": ["multiagent/executors/claude.py"],
              "constraints": ["no commits"], "gate": ["tests"], "base_commit": "e71df4f", "events_after": 7}
    fields.update(overrides)
    return build_task_envelope(**fields)


def make_request(workdir: Path, **overrides) -> ExecutorLaunchRequest:
    fields = {"task_id": "ORQ-T49", "task_envelope": make_envelope(), "workdir": workdir, "model": "claude-sonnet-5-5",
              "permission": Permission.READ, "timeout_seconds": 60, "session_id": SID}
    fields.update(overrides)
    return ExecutorLaunchRequest(**fields)


# -- build_command -------------------------------------------------------------------------------------------

def test_new_session_argv_for_read_is_exact(workdir):
    argv = ClaudeAdapter().build_command(make_request(workdir))
    assert argv == ["claude", "-p", "--output-format", "json", "--model", "claude-sonnet-5-5",
                    "--session-id", SID, *READ_TAIL]


def test_new_session_argv_for_write_is_exact_and_denies_bash(workdir):
    argv = ClaudeAdapter().build_command(make_request(workdir, permission=Permission.WRITE))
    assert argv == ["claude", "-p", "--output-format", "json", "--model", "claude-sonnet-5-5",
                    "--session-id", SID, *WRITE_TAIL]
    assert "bypassPermissions" not in argv and "--dangerously-skip-permissions" not in argv


def test_model_is_optional_and_limits_are_added_only_when_configured(workdir):
    assert ClaudeAdapter().build_command(make_request(workdir, model=None)) == [
        "claude", "-p", "--output-format", "json", "--session-id", SID, *READ_TAIL]
    limited = ClaudeAdapter(max_turns=12, max_budget_usd=0.5).build_command(make_request(workdir, model="opus"))
    assert limited == ["claude", "-p", "--output-format", "json", "--model", "opus", "--session-id", SID,
                       *READ_TAIL, "--max-turns", "12", "--max-budget-usd", "0.50"]


def test_effort_never_reaches_argv(workdir):
    argv = ClaudeAdapter().build_command(make_request(workdir, effort="max"))
    assert "--effort" not in argv and "max" not in argv


def test_argv_never_holds_prompt_text_or_the_envelope(workdir):
    request = make_request(workdir)
    for argv in (ClaudeAdapter().build_command(request), ClaudeAdapter().build_resume_command(request, SID)):
        joined = " ".join(argv)
        assert "rm -rf" not in joined and "ORQ-T49" not in joined and "multiagent" not in joined
        assert all("\n" not in item and '"' not in item and "," not in item for item in argv)


def test_resume_argv_is_exact_and_has_no_session_id_flag(workdir):
    adapter = ClaudeAdapter()
    request = make_request(workdir, session_id=OTHER_SID)
    argv = adapter.build_resume_command(request, SID)
    assert argv == ["claude", "-p", "--output-format", "json", "--model", "claude-sonnet-5-5", "--resume", SID,
                    *READ_TAIL]
    assert "--session-id" not in argv and OTHER_SID not in argv


def test_build_command_needs_the_session_id_fixed_by_the_framework(workdir):
    with pytest.raises(ProcessLaunchError) as caught:
        ClaudeAdapter().build_command(make_request(workdir, session_id=None))
    assert caught.value.code == process.INVALID_ARGV


@pytest.mark.parametrize("model", ["", " ", "opus; calc", "-rf", "--dangerously-skip-permissions", "a b", "m\nn", "m\"n",
                                   "m&n", "m%PATH%", "opus[1m]", "x" * 101, 5, ["opus"]])
def test_invalid_model_is_rejected_before_argv(workdir, model):
    with pytest.raises(ProcessLaunchError) as caught:
        ClaudeAdapter().build_command(make_request(workdir, model=model))
    assert caught.value.code == process.INVALID_ARGV


@pytest.mark.parametrize("session_id", ["", "abc", f"{SID} --dangerously-skip-permissions", f"{SID}\n", f"{SID}:agent-1",
                                        SID[:-1], "../" + SID, 12345, None])
def test_invalid_session_ids_are_rejected_for_new_and_resumed_sessions(workdir, session_id):
    adapter = ClaudeAdapter()
    with pytest.raises(ProcessLaunchError):
        adapter.build_command(make_request(workdir, session_id=session_id))
    with pytest.raises(ProcessLaunchError):
        adapter.build_resume_command(make_request(workdir), session_id)


@pytest.mark.parametrize("turns", [0, -1, 1001, True, "5", 2.5])
def test_invalid_max_turns_is_rejected(turns):
    with pytest.raises(ProcessLaunchError) as caught:
        ClaudeAdapter(max_turns=turns)
    assert caught.value.code == process.INVALID_ARGV


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf"), True, "1", 10_000])
def test_invalid_budget_is_rejected(budget):
    with pytest.raises(ProcessLaunchError):
        ClaudeAdapter(max_budget_usd=budget)


@pytest.mark.parametrize("command", [(), [], "claude", b"claude", ("claude", ""), ("claude", 3), None])
def test_invalid_command_prefix_is_rejected(command):
    with pytest.raises(ProcessLaunchError):
        ClaudeAdapter(command=command)


def test_unknown_permission_is_rejected_not_widened(workdir):
    with pytest.raises(ProcessLaunchError):
        ClaudeAdapter().build_command(make_request(workdir, permission="bypass"))
    # the plain strings of the enum are accepted and mean the same
    assert ClaudeAdapter().build_command(make_request(workdir, permission="write"))[-len(WRITE_TAIL):] == WRITE_TAIL


# -- render_prompt -------------------------------------------------------------------------------------------

def test_prompt_is_template_plus_canonical_envelope_plus_result_instruction(workdir):
    request = make_request(workdir)
    prompt = ClaudeAdapter().render_prompt(request)
    assert prompt == ClaudeAdapter().render_prompt(request)                    # deterministic
    assert prompt.startswith(claude.PROMPT_HEADER)
    assert canonical_json(request.task_envelope).decode("utf-8") in prompt
    assert "task result v1 envelope" in prompt and TASK_RESULT_SCHEMA in prompt
    assert "never add task_id, attempt or agent" in prompt and "patch is always null" in prompt
    assert "ONE JSON object" in prompt


def test_prompt_carries_only_the_fields_of_the_envelope_v1(workdir):
    envelope = {**make_envelope(objective="Plain objective."), "command": "evil-command-xyz", "api_key": "sk-secret"}
    prompt = ClaudeAdapter().render_prompt(make_request(workdir, task_envelope=envelope))
    assert "evil-command-xyz" not in prompt and "sk-secret" not in prompt and "Plain objective." in prompt


def test_prompt_rejects_an_envelope_of_another_task_or_an_invalid_one(workdir):
    with pytest.raises(EnvelopeError):
        ClaudeAdapter().render_prompt(make_request(workdir, task_id="ORQ-T50"))
    with pytest.raises(EnvelopeError):
        ClaudeAdapter().render_prompt(make_request(workdir, task_envelope={"task_id": "ORQ-T49"}))


# -- parse_output --------------------------------------------------------------------------------------------

def test_parse_output_reads_session_envelope_and_usage_with_cost():
    session_id, envelope, usage = ClaudeAdapter().parse_output(success_stdout(session_id=SID))
    assert session_id == SID and envelope == RESULT
    assert usage == {"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 900,
                     "cache_creation_input_tokens": 30, "cost_usd": 0.0421, "num_turns": 3, "duration_ms": 4321}


def test_parse_output_without_usage_or_cost_gives_none():
    stdout = json.dumps({"type": "result", "is_error": False, "result": json.dumps(RESULT), "session_id": SID})
    assert ClaudeAdapter().parse_output(stdout) == (SID, RESULT, None)


def test_parse_output_ignores_bad_numbers():
    stdout = success_stdout(session_id=SID, total_cost_usd="free", num_turns=True, duration_ms=-5)
    usage = ClaudeAdapter().parse_output(stdout)[2]
    assert usage == {"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 900,
                     "cache_creation_input_tokens": 30}
    broken = success_stdout(session_id=SID, usage={"input_tokens": 1.5, "output_tokens": "9"}, total_cost_usd=None,
                            num_turns=None, duration_ms=None)
    assert ClaudeAdapter().parse_output(broken)[2] is None


@pytest.mark.parametrize("text", [
    json.dumps(RESULT),
    "```json\n" + json.dumps(RESULT, indent=2) + "\n```",
    "Done. Here is the result envelope:\n\n" + json.dumps(RESULT) + "\n\nLet me know if you need more.",
    'Example {"a": 1} and the real one: ' + json.dumps(RESULT) + " {not json",
])
def test_the_envelope_is_found_bare_fenced_or_inside_prose(text):
    assert ClaudeAdapter().parse_output(success_stdout(text, session_id=SID))[1] == RESULT


def test_the_last_envelope_wins_and_a_schema_less_one_needs_a_summary():
    draft = {**RESULT, "summary": "draft"}
    assert extract_envelope(json.dumps(draft) + "\nFinal:\n" + json.dumps(RESULT)) == RESULT
    assert extract_envelope('{"summary": "no schema is read as v1"}') == {"summary": "no schema is read as v1"}
    assert extract_envelope('{"schema": "other/9", "summary": "wrong schema"}') is None
    assert extract_envelope('{"decisions": []}') is None


@pytest.mark.parametrize("stdout", ["", "   \n", "not json at all", '{"type": "result", "result": ', "[1, 2, 3]", '"text"',
                                    '{"unrelated": true}', "{" * 5000])
def test_broken_or_unrelated_output_gives_nothing_and_never_raises(stdout):
    assert ClaudeAdapter().parse_output(stdout) == (None, None, None)
    assert parse(stdout).problem


def test_missing_envelope_keeps_session_and_usage_and_says_why():
    parsed = parse(success_stdout("I did the task but forgot the JSON.", session_id=SID))
    assert (parsed.session_id, parsed.envelope) == (SID, None)
    assert parsed.usage["cost_usd"] == 0.0421 and "no task result envelope" in parsed.problem
    assert "empty" in parse(success_stdout("", session_id=SID)).problem


def test_an_error_result_never_yields_an_envelope():
    stdout = success_stdout(json.dumps(RESULT), session_id=SID, is_error=True, subtype="error_max_turns")
    parsed = parse(stdout)
    assert parsed.is_error and parsed.envelope is None and parsed.subtype == "error_max_turns"
    assert ClaudeAdapter().parse_output(stdout)[1] is None


def test_a_non_uuid_session_id_in_the_output_is_ignored():
    assert ClaudeAdapter().parse_output(success_stdout(session_id="not-a-uuid"))[0] is None


def test_message_list_and_json_lines_forms_are_read():
    message = {"type": "assistant", "message": {"content": []}}
    result = json.loads(success_stdout(session_id=SID))
    assert parse(json.dumps([message, result])).envelope == RESULT
    assert parse("warning: something\n" + json.dumps(message) + "\n" + json.dumps(result)).envelope == RESULT
    assert parse("﻿" + json.dumps(result) + "\n").session_id == SID


# -- classify_failure ----------------------------------------------------------------------------------------

QUOTA_TEXTS = [
    "Claude AI usage limit reached|1760000000",
    "You've hit your limit · resets 5pm (America/Bogota)",
    "5-hour limit reached ∙ resets 3pm",
    "Weekly limit reached ∙ resets Oct 14",
    "You've reached your weekly usage limit",
    "You're out of extra usage · resets 3pm",
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed your rate limit"}}',
    "HTTP 429 Too Many Requests",
    "Your credit balance is too low to access the Anthropic API",
    '{"error": {"type": "insufficient_quota", "message": "You exceeded your current quota"}}',
]
AUTH_TEXTS = [
    "Invalid API key · Please run /login",
    "Not logged in · Please run /login",
    'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}',
    "OAuth token has expired. Please obtain a new token or refresh your existing token.",
    "Error: 401 Unauthorized",
]
OTHER_TEXTS = ["", "boom", "Traceback (most recent call last):\n  File x.py\nValueError", "API Error: 529 overloaded_error",
               "Prompt is too long", "Error: File size limit exceeded", "exceeded the context window limit",
               "Session ID 11111111 is already in use", "Reached max turns (5)", "Error: ENOENT, file 4290 not found"]


@pytest.mark.parametrize("text", QUOTA_TEXTS)
def test_quota_and_rate_limit_messages_are_quota(text):
    assert ClaudeAdapter().classify_failure(1, text) is FailureKind.QUOTA
    assert ClaudeAdapter().classify_failure(None, "prefix\n" + text.upper()) is FailureKind.QUOTA


@pytest.mark.parametrize("text", AUTH_TEXTS)
def test_login_problems_are_authentication(text):
    assert ClaudeAdapter().classify_failure(1, text) is FailureKind.AUTHENTICATION


@pytest.mark.parametrize("text", OTHER_TEXTS)
def test_anything_else_is_other_and_claude_never_reports_an_open_session_block(text):
    assert ClaudeAdapter().classify_failure(1, text) is FailureKind.OTHER


def test_classify_failure_tolerates_missing_text():
    assert ClaudeAdapter().classify_failure(None, None) is FailureKind.OTHER      # type: ignore[arg-type]
    assert ClaudeAdapter().classify_failure(0, "") is FailureKind.OTHER


# -- open_target ---------------------------------------------------------------------------------------------

def test_open_target_is_the_command_the_gui_launches():
    target = ClaudeAdapter().open_target(SID)
    assert target == OpenTarget(kind="command", value=["claude", "--desktop", "--resume", SID], needs_confirmation=False)
    assert tuple(target.value[:3]) == open_in_app.COMMAND_PREFIX == claude.OPEN_COMMAND_PREFIX
    # and it is the very value the session reader gives the GUI for a Claude session
    assert OpenInApp("command", ["claude", "--desktop", "--resume", SID], False).value == target.value


@pytest.mark.parametrize("session_id", [None, "", "abc", f"{SID} && calc", f"{SID}:agent-1", f"{SID}\n", 7])
def test_open_target_rejects_anything_but_a_uuid(session_id):
    assert ClaudeAdapter().open_target(session_id) is None


# -- describe (never launches) ----------------------------------------------------------------------------------

def _bundled_declaration(tmp_path: Path) -> dict:
    catalog = Catalog(Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                               gemini_api_key="", openai_api_key="", openai_enabled=False))
    return dict(validate_executor_config(catalog.executor_routing)["executors"]["claude_cli"])


def test_describe_reflects_the_catalog_and_keeps_effort_unknown(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("describe must not start a process")
    monkeypatch.setattr(process, "run", refuse)
    monkeypatch.setattr(process, "start", refuse)
    declaration = _bundled_declaration(tmp_path)
    descriptor = ClaudeAdapter().describe(declaration)
    assert descriptor.executor_id == "claude_cli" and descriptor.adapter_id == "claude_cli"
    assert descriptor.enabled is False and descriptor.mode == "manual"
    assert descriptor.noninteractive_launch.status is SupportState.SUPPORTED
    assert "--resume <id>" in descriptor.resume.values and descriptor.blocks_when_open.status is SupportState.UNSUPPORTED
    assert "target:claude --desktop --resume <id>" in descriptor.open_in_app.values
    assert descriptor.effort_support.status is SupportState.UNKNOWN and descriptor.quota_signal.status is SupportState.UNKNOWN
    declaration["effort_support"] = {"status": "supported", "source": "s", "provenance": "operator_test",
                                     "scope": "claude_cli", "observed_at": "2026-10-11", "values": ["high"]}
    assert ClaudeAdapter().describe(declaration).effort_support.status is SupportState.UNKNOWN


def test_registry_resolves_the_adapter_and_a_disabled_declaration_still_wins(tmp_path):
    declaration = {**_bundled_declaration(tmp_path), "adapter_id": "claude_cli"}
    adapter = ClaudeAdapter()
    registry = ExecutorRegistry({"version": 1, "executors": {"claude_cli": declaration}}, [adapter])
    assert registry.resolve("claude_cli") is adapter
    descriptor = registry.get("claude_cli")
    assert descriptor.adapter_id == "claude_cli" and descriptor.provenance == "adapter_declaration"
    assert descriptor.enabled is False and descriptor.effort_support.status is SupportState.UNKNOWN
    automatic = ExecutorRegistry({"version": 1, "executors": {"claude_cli": {**declaration, "enabled": True,
                                                                              "mode": "automatic"}}}, [adapter])
    assert automatic.get("claude_cli").enabled is True and automatic.get("claude_cli").mode == "automatic"


def test_adapter_satisfies_the_executor_application_protocol():
    adapter = ClaudeAdapter()
    assert isinstance(adapter, ExecutorApplicationAdapter)
    assert (adapter.adapter_id, adapter.version) == ("claude_cli", "1")


# -- launch with the fake claude ----------------------------------------------------------------------------------

def test_launch_runs_the_fake_with_exact_argv_stdin_prompt_and_worktree_cwd(fake, workdir):
    adapter = fake.adapter()
    request = make_request(workdir, permission=Permission.WRITE)
    result = adapter.launch(request)
    assert result.status is RunStatus.COMPLETED
    (call,) = fake.calls()
    assert call["argv"] == ["-p", "--output-format", "json", "--model", "claude-sonnet-5-5", "--session-id", SID,
                            *WRITE_TAIL]
    assert call["stdin"] == adapter.render_prompt(request)
    assert json.dumps(HOSTILE_OBJECTIVE, ensure_ascii=False)[1:-1] in call["stdin"]
    assert os.path.normcase(call["cwd"]) == os.path.normcase(str(workdir))
    assert result.session_id == SID and result.result_envelope == RESULT
    assert result.usage["cost_usd"] == 0.0421 and result.usage["output_tokens"] == 45
    assert result.command.argv[1:3] == (str(fake.script), "-p") and result.command.cwd == str(workdir)
    assert result.command.exit_code == 0 and result.command.finished_at
    assert result.notes == ()
    assert validate_task_result(result.result_envelope, task_id="ORQ-T49", attempt=1, agent="claude_cli")["summary"]


def test_launch_without_a_fixed_session_id_generates_one_and_uses_it(fake, workdir):
    result = fake.adapter().launch(make_request(workdir, session_id=None))
    (call,) = fake.calls()
    generated = call["argv"][call["argv"].index("--session-id") + 1]
    assert str(uuid.UUID(generated)) == generated and result.session_id == generated


def test_the_session_id_comes_from_the_app_output_when_it_gives_one(fake, workdir):
    fake.configure(stdout=success_stdout(session_id=OTHER_SID))
    assert fake.adapter().launch(make_request(workdir)).session_id == OTHER_SID


def test_resume_continues_the_session_with_resume_and_not_session_id(fake, workdir):
    result = fake.adapter().resume(make_request(workdir, session_id=OTHER_SID), SID)
    (call,) = fake.calls()
    assert call["argv"] == ["-p", "--output-format", "json", "--model", "claude-sonnet-5-5", "--resume", SID, *READ_TAIL]
    assert result.status is RunStatus.COMPLETED and result.session_id == SID and result.result_envelope == RESULT
    assert call["stdin"].startswith(claude.PROMPT_HEADER)


def test_resume_with_an_invalid_session_id_starts_nothing(fake, workdir):
    with pytest.raises(ProcessLaunchError):
        fake.adapter().resume(make_request(workdir), f"{SID} --dangerously-skip-permissions")
    assert fake.calls() == []


def test_environment_is_scrubbed_of_claude_and_anthropic_variables(fake, workdir, monkeypatch):
    for name in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "anthropic_base_url"):
        monkeypatch.setenv(name, "leak")
    monkeypatch.setenv("MAF_PLAIN_VARIABLE", "kept")
    monkeypatch.delenv(NO_CURRENT_DIR_VARIABLE, raising=False)
    assert fake.adapter().launch(make_request(workdir)).status is RunStatus.COMPLETED
    (call,) = fake.calls()
    assert not [name for name in call["env"] if name.upper().startswith(("CLAUDE", "ANTHROPIC"))]
    env = {name.upper(): value for name, value in call["env"].items()}     # Windows upper-cases the names it reports
    assert env[NO_CURRENT_DIR_VARIABLE.upper()] == "1" and env["MAF_PLAIN_VARIABLE"] == "kept"


def test_effort_is_ignored_with_a_note(fake, workdir):
    result = fake.adapter().launch(make_request(workdir, effort="high"))
    assert result.status is RunStatus.COMPLETED and result.notes == (claude.EFFORT_NOTE,)
    assert "--effort" not in fake.calls()[0]["argv"]


def test_configured_limits_reach_the_fake(fake, workdir):
    fake.adapter(max_turns=7, max_budget_usd=2).launch(make_request(workdir))
    assert fake.calls()[0]["argv"][-4:] == ["--max-turns", "7", "--max-budget-usd", "2.00"]


@pytest.mark.parametrize("stdout, expected_note", [
    ('{"type": "result", "is_error": false, "result": "{\\"schema\\"', "not a Claude JSON result"),
    ("this is not json", "not a Claude JSON result"),
    ("", "empty output"),
    (success_stdout("Finished, nothing else to say."), "no task result envelope found"),
    (success_stdout(""), "the result text is empty"),
])
def test_exit_zero_without_a_valid_envelope_is_failed_with_the_text_as_evidence(fake, workdir, stdout, expected_note):
    fake.configure(stdout=stdout)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and result.result_envelope is None
    assert any(expected_note in note for note in result.notes)
    assert result.stdout_tail == stdout.replace("__SID__", SID)
    assert len(fake.calls()) == 1                                         # no retry


def test_quota_in_the_json_result_is_quota_exhausted_and_not_retried(fake, workdir):
    fake.configure(stdout=json.dumps({"type": "result", "subtype": "success", "is_error": True, "session_id": SID,
                                      "result": "Claude AI usage limit reached|1760000000", "total_cost_usd": 0}),
                   exit=1)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.result_envelope is None
    assert result.command.exit_code == 1 and result.session_id == SID
    assert any("failure: quota" in note for note in result.notes)
    assert len(fake.calls()) == 1


def test_quota_on_stderr_or_in_plain_stdout_is_quota_exhausted(fake, workdir):
    fake.configure(stderr="API Error: 429 rate_limit_error", exit=1)
    assert fake.adapter().launch(make_request(workdir)).status is RunStatus.QUOTA_EXHAUSTED
    fake.configure(stdout="5-hour limit reached ∙ resets 3pm\n", exit=1)
    assert fake.adapter().launch(make_request(workdir)).status is RunStatus.QUOTA_EXHAUSTED


def test_login_failure_is_failed_with_an_authentication_note(fake, workdir):
    fake.configure(stderr="Invalid API key · Please run /login", exit=1)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and any("failure: authentication" in note for note in result.notes)
    assert "Please run /login" in result.stderr_tail


def test_other_failures_keep_exit_code_and_tails(fake, workdir):
    fake.configure(stdout="partial", stderr="Traceback: boom", exit=3)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and result.command.exit_code == 3
    assert result.stdout_tail == "partial" and result.stderr_tail == "Traceback: boom"
    assert any("failure: other (exit code 3)" in note for note in result.notes)


def test_an_envelope_in_a_failed_run_is_not_returned(fake, workdir):
    fake.configure(stdout=success_stdout(session_id=SID), exit=1)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and result.result_envelope is None and result.usage["cost_usd"] == 0.0421


def test_error_result_with_exit_zero_is_a_failure(fake, workdir):
    fake.configure(stdout=success_stdout("Reached max turns (5)", session_id=SID, is_error=True,
                                         subtype="error_max_turns"))
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.FAILED and any("error_max_turns" in note for note in result.notes)


def test_a_hanging_claude_times_out_and_is_killed(fake, workdir):
    fake.configure(sleep=60)
    started = time.monotonic()
    result = fake.adapter().launch(make_request(workdir, timeout_seconds=1))
    assert result.status is RunStatus.TIMEOUT and result.result_envelope is None
    assert time.monotonic() - started < 30
    assert result.session_id == SID and any("timed out after 1 s" in note for note in result.notes)
    assert len(fake.calls()) == 1


def test_tails_in_a_result_are_trimmed(fake, workdir):
    fake.configure(stdout="x" * (claude.MAX_TAIL_CHARS * 3) + "END", stderr="y" * (claude.MAX_TAIL_CHARS * 3), exit=2)
    result = fake.adapter().launch(make_request(workdir))
    assert len(result.stdout_tail) == claude.MAX_TAIL_CHARS and result.stdout_tail.endswith("END")
    assert len(result.stderr_tail) == claude.MAX_TAIL_CHARS


def test_a_launch_that_cannot_start_raises_and_runs_nothing(fake, workdir, tmp_path, monkeypatch):
    with pytest.raises(ProcessLaunchError) as caught:
        fake.adapter().launch(make_request(tmp_path / "missing"))
    assert caught.value.code == process.INVALID_CWD
    with pytest.raises(EnvelopeError):
        fake.adapter().launch(make_request(workdir, task_id="OTHER-1"))
    assert fake.calls() == []
    # with the default program name and an empty PATH there is nothing to run: refused, not searched elsewhere
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(process, "find_executable", lambda name, **kwargs: None)
    with pytest.raises(ProcessLaunchError) as missing:
        ClaudeAdapter().launch(make_request(workdir))
    assert missing.value.code == process.EXECUTABLE_NOT_FOUND
