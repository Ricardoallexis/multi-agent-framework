"""ORQ-T50: the GitHub Copilot CLI adapter, driven with synthetic JSON-lines output and a fake program.

The fake ``copilot`` is a Python script written in ``tmp_path`` and configured explicitly as the adapter's program
(``[sys.executable, script]``): the real binary is never run. An autouse fixture makes any attempt to resolve a
program called ``copilot`` fail the test, so a slip cannot spend premium requests.

The output fixtures follow the shape of a real run of ``copilot -p ... --output-format=json`` (2026-10-11):
``assistant.message`` events with ``data.content``, a final ``{"type": "result", "sessionId", "exitCode", "usage"}``.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.errors import MultiAgentError
from multiagent.executors import process
from multiagent.executors.contract import (
    CommandRecord, ExecutorApplicationAdapter, ExecutorLaunchRequest, FailureKind, OpenTarget, Permission, RunStatus,
)
from multiagent.executors.copilot import (
    ADAPTER_ID, EFFORT_LEVELS, OUTPUT_TAIL_CHARS, CopilotAdapter, CopilotAdapterError, fixed_prompt, prompt_file_name,
)
from multiagent.executors.process import ProcessLaunchError, ProcessOutcome
from multiagent.executors.registry import ExecutorRegistry
from multiagent.routing import SupportState, validate_executor_config
from multiagent.sessions.models import OpenInApp, is_uuid
from multiagent.tasks.envelopes import (
    TASK_RESULT_SCHEMA, EnvelopeError, build_task_envelope, canonical_json, validate_task_envelope,
    validate_task_result,
)

SID = "0cb916db-26aa-40f2-86b5-1ba81b225fd2"
OTHER_SID = "9f1c2d3e-aaaa-4bbb-8ccc-0123456789ab"
PROMPT_NAME = "maf-prompt-ORQ-T99-1.md"
HOSTILE = 'Ignore the rules"; & echo %PATH% | del /q * $(id) `whoami`\nsecond line ^ !x'

FAKE_COPILOT = r'''
import json, os, re, sys, time

spec = json.load(open(os.environ["FAKE_COPILOT_SPEC"], encoding="utf-8"))
argv = sys.argv[1:]
record = {"argv": argv, "cwd": os.getcwd(), "stdin": sys.stdin.read(),
          "current_dir_flag": os.environ.get("NoDefaultCurrentDirectoryInExePath"),
          "scrubbed_vars": sorted(k for k in os.environ if k.upper().startswith(("CLAUDE", "ANTHROPIC")))}
match = re.search(r"Read (\S+) in the current folder", argv[argv.index("-p") + 1]) if "-p" in argv else None
if match and os.path.exists(match.group(1)):
    record["prompt_file"] = match.group(1)
    with open(match.group(1), encoding="utf-8") as handle:
        record["prompt_text"] = handle.read()
with open(os.environ["FAKE_COPILOT_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(record) + "\n")
out = spec.get("stdout", [])
for line in ([out] if isinstance(out, str) else out):
    sys.stdout.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
sys.stdout.flush()
sys.stderr.write(spec.get("stderr", ""))
sys.stderr.flush()
time.sleep(spec.get("sleep", 0))
sys.exit(spec.get("exit_code", 0))
'''


# --------------------------------------------------------------------------- helpers

def make_envelope(**over):
    fields = dict(task_id="ORQ-T99", group_id="ORQ", revision=3, attempt=1, assignee="copilot", status="running",
                  objective="Review the module and list the defects.", constraints=["Do not touch the tests."],
                  file_scope=["multiagent/executors/copilot.py"], gate=["review"], base_commit="e71df4f",
                  events_after=7)
    fields.update(over)
    return build_task_envelope(**fields)


def make_request(workdir, envelope=None, **kwargs):
    envelope = envelope or make_envelope()
    kwargs.setdefault("model", "gpt-6-luna")
    kwargs.setdefault("effort", "low")
    return ExecutorLaunchRequest(task_id=envelope["task_id"], task_envelope=envelope, workdir=Path(workdir), **kwargs)


def result_envelope(**over):
    data = {"schema": TASK_RESULT_SCHEMA, "task_id": "ORQ-T99", "attempt": 1, "base_commit": "e71df4f",
            "patch": None, "summary": "Reviewed: two defects found.", "decisions": ["Kept the API"],
            "contract_changes": "", "tests_not_run": ["full suite"], "requests": []}
    data.update(over)
    return data


def fenced(envelope, intro="Done. Here is the result.\n\n"):
    return f"{intro}```json\n{json.dumps(envelope, indent=2)}\n```\n"


def msg(content, **data):
    return {"type": "assistant.message", "data": {"content": content, **data}}


USAGE = {"premiumRequests": 1, "totalApiDurationMs": 2310, "sessionDurationMs": 8120,
         "codeChanges": {"linesAdded": 0, "linesRemoved": 0, "filesModified": []}}
USAGE_NORMALIZED = {"premium_requests": 1, "total_api_duration_ms": 2310, "session_duration_ms": 8120,
                    "code_changes": {"lines_added": 0, "lines_removed": 0}}


def result_event(session=SID, exit_code=0, usage=USAGE):
    event = {"type": "result", "timestamp": "2026-10-11T09:00:00.000Z", "sessionId": session, "exitCode": exit_code}
    if usage is not None:
        event["usage"] = usage
    return event


def good_stdout(envelope=None, session=SID):
    events = [{"type": "session.start", "data": {"sessionId": session, "copilotVersion": "1.0.95"}},
              msg("", toolRequests=[{"name": "view"}]),
              {"type": "tool.execution_complete", "data": {"toolCallId": "t1"}},
              msg(fenced(envelope or result_envelope())),
              result_event(session)]
    return "\n".join(json.dumps(event) for event in events) + "\n"


def expected_args(workdir, *, model="gpt-6-luna", effort="low", write=False, resume=None, name=PROMPT_NAME):
    args = ["-p", fixed_prompt(name)]
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    if resume:
        args.append(f"--resume={resume}")
    args += ["-C", str(workdir), "--no-ask-user", "--allow-tool=read"]
    if write:
        args.append("--allow-tool=write")
    return args + ["--output-format=json"]


def outcome(exit_code=0, stdout="", stderr="", *, timed_out=False, cancelled=False, truncated=False):
    record = CommandRecord(argv=("copilot",), cwd="x", started_at="t0", finished_at="t1", exit_code=exit_code)
    return ProcessOutcome(exit_code=exit_code, stdout_tail=stdout, stderr_tail=stderr, stdout_truncated=truncated,
                          stderr_truncated=False, timed_out=timed_out, cancelled=cancelled, command=record)


@pytest.fixture(autouse=True)
def never_the_real_copilot(monkeypatch):
    real = process.resolve_executable

    def guard(name, cwd):
        if Path(name).stem.lower() == "copilot":
            pytest.fail(f"a test tried to run the real copilot ({name!r})")
        return real(name, cwd)

    monkeypatch.setattr(process, "resolve_executable", guard)


@pytest.fixture()
def workdir(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def adapter() -> CopilotAdapter:
    return CopilotAdapter()


class Fake:
    """The fake copilot of one test: write a spec, make an adapter that runs it, read what it recorded."""

    def __init__(self, folder: Path):
        folder.mkdir()
        self.script = folder / "fake_copilot.py"
        self.script.write_text(FAKE_COPILOT, encoding="utf-8")
        self.spec = folder / "spec.json"
        self.log = folder / "calls.jsonl"
        self.respond()

    def respond(self, **spec) -> None:
        self.spec.write_text(json.dumps(spec), encoding="utf-8")

    @property
    def env(self) -> dict[str, str]:
        return {"FAKE_COPILOT_SPEC": str(self.spec), "FAKE_COPILOT_LOG": str(self.log)}

    def adapter(self, executable=None, **kwargs) -> CopilotAdapter:
        return CopilotAdapter(executable or [sys.executable, str(self.script)], env_extra=self.env, **kwargs)

    def calls(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []


@pytest.fixture()
def fake(tmp_path) -> Fake:
    return Fake(tmp_path / "tools")


# --------------------------------------------------------------------------- protocol and command (pure)

def test_the_adapter_satisfies_the_executor_application_contract(adapter):
    assert isinstance(adapter, ExecutorApplicationAdapter)
    assert adapter.adapter_id == ADAPTER_ID == "copilot_cli" and adapter.version == "1"


def test_the_exact_argv_of_a_read_launch(adapter, workdir):
    argv = adapter.build_command(make_request(workdir))
    assert argv == ["copilot", *expected_args(workdir)]
    assert argv[:2] == ["copilot", "-p"] and argv[2] == (
        "Read maf-prompt-ORQ-T99-1.md in the current folder and do exactly what it says.")


def test_the_exact_argv_of_a_write_launch(adapter, workdir):
    argv = adapter.build_command(make_request(workdir, permission=Permission.WRITE))
    assert argv == ["copilot", *expected_args(workdir, write=True)]
    assert argv.count("--allow-tool=read") == 1 and argv.index("--allow-tool=read") < argv.index("--allow-tool=write")


def test_permission_given_as_text_is_accepted_and_anything_else_is_refused(adapter, workdir):
    assert "--allow-tool=write" in adapter.build_command(make_request(workdir, permission="write"))
    assert "--allow-tool=write" not in adapter.build_command(make_request(workdir, permission="read"))
    with pytest.raises(CopilotAdapterError) as caught:
        adapter.build_command(make_request(workdir, permission="admin"))
    assert caught.value.code == "invalid_permission"


@pytest.mark.parametrize("permission", [Permission.READ, Permission.WRITE])
def test_no_broad_permission_flag_is_ever_used(adapter, workdir, permission):
    argv = adapter.build_command(make_request(workdir, permission=permission))
    for item in argv:
        assert not item.startswith(("--allow-all", "--yolo", "--allow-url", "--allow-tool=shell", "--add-dir"))
    assert [item for item in argv if item.startswith("--allow-tool")] == (
        ["--allow-tool=read"] + (["--allow-tool=write"] if permission is Permission.WRITE else []))


def test_no_free_text_reaches_argv(adapter, workdir):
    envelope = make_envelope(objective=HOSTILE, constraints=[HOSTILE[:150]])
    argv = adapter.build_command(make_request(workdir, envelope))
    assert argv == ["copilot", *expected_args(workdir)]
    joined = " ".join(argv)
    for fragment in ("Ignore", "echo", "del /q", "whoami", "second line", "Review"):
        assert fragment not in joined
    for item in argv[1:]:  # every token passes the strictest rule of the process executor (a .cmd shim)
        assert process.SHIM_ARGUMENT.fullmatch(item), item
        assert "\n" not in item and len(item) < 200 + len(str(workdir))


def test_the_prompt_file_name_is_plain_and_follows_the_task_and_attempt(workdir):
    assert prompt_file_name(make_request(workdir)) == PROMPT_NAME
    assert prompt_file_name(make_request(workdir), resume=True) == "maf-prompt-ORQ-T99-1-resume.md"
    assert prompt_file_name(make_request(workdir, make_envelope(attempt=4))) == "maf-prompt-ORQ-T99-4.md"
    for bad in ("", "a b", "../x", "a;b", "-x", "a&b", "x" * 200, "t\n1"):
        with pytest.raises(CopilotAdapterError) as caught:
            prompt_file_name(ExecutorLaunchRequest(task_id=bad, task_envelope={}, workdir=workdir))
        assert caught.value.code == "invalid_task_id"


def test_model_and_effort_are_optional(adapter, workdir):
    argv = adapter.build_command(make_request(workdir, model=None, effort=None))
    assert argv == ["copilot", *expected_args(workdir, model=None, effort=None)]
    assert "--model" not in argv and "--effort" not in argv


@pytest.mark.parametrize("model", ["gpt-6-luna", "gpt-5.4", "auto", "o4-mini", "gemini-3-pro", "gpt_x.1"])
def test_plain_model_ids_pass(adapter, workdir, model):
    argv = adapter.build_command(make_request(workdir, model=model))
    assert argv[argv.index("--model") + 1] == model


@pytest.mark.parametrize("model", ["", "  ", "gpt 6", "--allow-all", "-x", "a;b", "m\nx", "x" * 65, "a&b", 'a"b',
                                   "../x", "a%b", 5])
def test_a_model_that_is_not_a_plain_id_is_refused(adapter, workdir, model):
    with pytest.raises(CopilotAdapterError) as caught:
        adapter.build_command(make_request(workdir, model=model))
    assert caught.value.code == "invalid_model"


@pytest.mark.parametrize("model", ["claude-sonnet-5.5", "Claude-Opus-4", "CLAUDE", "anthropic-claude-x", "x-claude"])
def test_a_claude_model_is_never_run_through_copilot(adapter, workdir, model, monkeypatch):
    monkeypatch.setattr(process, "run", lambda *a, **k: pytest.fail("nothing may start"))
    request = make_request(workdir, model=model)
    for call in (lambda: adapter.build_command(request), lambda: adapter.launch(request),
                 lambda: CopilotAdapter(session_guard=lambda s: True).build_resume_command(request, SID)):
        with pytest.raises(CopilotAdapterError, match="Claude model") as caught:
            call()
        assert caught.value.code == "claude_model_rejected" and isinstance(caught.value, MultiAgentError)
    assert list(workdir.iterdir()) == []


@pytest.mark.parametrize("effort", EFFORT_LEVELS)
def test_documented_effort_levels_pass_as_effort(adapter, workdir, effort):
    argv = adapter.build_command(make_request(workdir, effort=effort))
    assert argv[argv.index("--effort") + 1] == effort


def test_effort_is_normalised_and_unknown_levels_are_refused(adapter, workdir):
    argv = adapter.build_command(make_request(workdir, effort=" HIGH "))
    assert argv[argv.index("--effort") + 1] == "high"
    for bad in ("ultra", "", "low;x", "--x", 3, "l ow"):
        with pytest.raises(CopilotAdapterError) as caught:
            adapter.build_command(make_request(workdir, effort=bad))
        assert caught.value.code == "invalid_effort"


def test_the_working_folder_must_be_an_absolute_path(adapter, workdir):
    for bad in ("relative/folder", "", "C:work" if sys.platform == "win32" else "work", "bad\nfolder"):
        request = ExecutorLaunchRequest(task_id="ORQ-T99", task_envelope=make_envelope(), workdir=bad)  # type: ignore[arg-type]
        with pytest.raises(CopilotAdapterError) as caught:
            adapter.build_command(request)
        assert caught.value.code == "invalid_workdir"


def test_a_session_id_in_the_request_does_not_change_the_argv(adapter, workdir):
    argv = adapter.build_command(make_request(workdir, session_id=OTHER_SID))
    assert argv == adapter.build_command(make_request(workdir))
    assert "--session-id" not in argv and OTHER_SID not in " ".join(argv)


def test_the_program_can_be_a_launcher_with_fixed_leading_arguments(workdir):
    argv = CopilotAdapter(["/opt/py", "script.py"]).build_command(make_request(workdir))
    assert argv[:3] == ["/opt/py", "script.py", "-p"]
    for bad in ([], [""], ["x", 3], ["  "]):
        with pytest.raises(ValueError):
            CopilotAdapter(bad)


# --------------------------------------------------------------------------- resume (pure)

def test_resume_only_continues_sessions_the_framework_created(adapter, workdir):
    with pytest.raises(CopilotAdapterError) as caught:
        adapter.build_resume_command(make_request(workdir), SID)
    assert caught.value.code == "session_not_created_by_framework" and SID in str(caught.value)


def test_a_session_guard_vouches_for_stored_sessions(workdir):
    seen = []
    adapter = CopilotAdapter(session_guard=lambda sid: seen.append(sid) or sid == SID)
    argv = adapter.build_resume_command(make_request(workdir), SID)
    assert argv == ["copilot", *expected_args(workdir, resume=SID, name="maf-prompt-ORQ-T99-1-resume.md")]
    assert argv.index("--effort") < argv.index(f"--resume={SID}") < argv.index("-C")
    assert seen == [SID]
    with pytest.raises(CopilotAdapterError) as caught:
        adapter.build_resume_command(make_request(workdir), OTHER_SID)
    assert caught.value.code == "session_not_created_by_framework"


def test_the_resume_id_is_normalised_and_validated(workdir):
    adapter = CopilotAdapter(session_guard=lambda sid: True)
    assert f"--resume={SID}" in adapter.build_resume_command(make_request(workdir), SID.upper())
    for bad in ("", "not-a-uuid", "../x", SID + "x", "--x", None, 7, f"{SID}\n", "latest", "0cb916d"):
        with pytest.raises(CopilotAdapterError) as caught:
            adapter.build_resume_command(make_request(workdir), bad)  # type: ignore[arg-type]
        assert caught.value.code == "invalid_session_id"


def test_resume_keeps_the_same_safety_rules(workdir):
    adapter = CopilotAdapter(session_guard=lambda sid: True)
    argv = adapter.build_resume_command(make_request(workdir, permission=Permission.WRITE), SID)
    assert "--allow-tool=write" in argv and not any(item.startswith("--allow-all") for item in argv)
    for item in argv[1:]:
        assert process.SHIM_ARGUMENT.fullmatch(item), item


# --------------------------------------------------------------------------- prompt (pure)

def test_the_prompt_is_a_fixed_template_plus_the_canonical_envelope(adapter, workdir):
    request = make_request(workdir)
    prompt = adapter.render_prompt(request)
    assert prompt == adapter.render_prompt(make_request(workdir))
    assert canonical_json(validate_task_envelope(request.task_envelope)).decode() in prompt
    assert "READ ONLY" in prompt and TASK_RESULT_SCHEMA in prompt and "```json" in prompt
    write = adapter.render_prompt(make_request(workdir, permission=Permission.WRITE))
    assert "WRITE" in write and "READ ONLY" not in write
    assert "Do not commit, push" in prompt


def test_the_prompt_drops_unknown_envelope_fields_and_refuses_bad_envelopes(adapter, workdir):
    envelope = {**make_envelope(), "command": "rm -rf /", "token": "secret"}
    prompt = adapter.render_prompt(make_request(workdir, envelope))
    assert "rm -rf" not in prompt and "secret" not in prompt
    with pytest.raises(EnvelopeError):
        adapter.render_prompt(make_request(workdir, {**make_envelope(), "status": "bogus"}))
    mismatch = ExecutorLaunchRequest(task_id="ORQ-T99", task_envelope=make_envelope(task_id="ORQ-T98"), workdir=workdir)
    with pytest.raises(CopilotAdapterError) as caught:
        adapter.render_prompt(mismatch)
    assert caught.value.code == "task_id_mismatch"


def test_the_answer_skeleton_in_the_prompt_becomes_a_valid_task_result(adapter, workdir):
    prompt = adapter.render_prompt(make_request(workdir))
    block = re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL).group(1)
    skeleton = json.loads(block)
    assert "agent" not in skeleton and skeleton["patch"] is None
    skeleton["summary"] = "Found two defects."
    stdout = "\n".join(json.dumps(event) for event in (msg(fenced(skeleton)), result_event())) + "\n"
    session_id, envelope, _ = adapter.parse_output(stdout)
    assert session_id == SID
    checked = validate_task_result(envelope, task_id="ORQ-T99", attempt=1, agent="copilot")
    assert checked["summary"] == "Found two defects." and checked["base_commit"] == "e71df4f"


# --------------------------------------------------------------------------- parse_output (pure)

def test_a_good_run_gives_session_envelope_and_usage(adapter):
    session_id, envelope, usage = adapter.parse_output(good_stdout())
    assert session_id == SID
    assert envelope == result_envelope()
    assert usage == USAGE_NORMALIZED


def test_the_session_id_is_normalised_to_lower_case(adapter):
    assert adapter.parse_output(json.dumps(result_event(SID.upper())))[0] == SID


@pytest.mark.parametrize("final", [
    json.dumps(result_envelope()),                                                     # the whole message
    "Done.\n\n```json\n" + json.dumps(result_envelope(), indent=2) + "\n```\n",        # fenced block
    "Done.\n\n```\n" + json.dumps(result_envelope()) + "\n```\nThanks.",               # fence without label
    "Result follows. " + json.dumps(result_envelope()) + " That is all.",              # object inside prose
    "Draft: {\"a\": 1}\n```json\n{\"b\": 2}\n```\nFinal:\n```json\n" + json.dumps(result_envelope()) + "\n```",
], ids=["whole", "fenced", "fence-no-label", "inline", "last-of-several"])
def test_the_result_envelope_is_found_in_the_final_message(adapter, final):
    assert adapter.parse_output(json.dumps(msg(final)))[1] == result_envelope()


def test_the_last_fenced_block_wins_over_an_earlier_one(adapter):
    early = fenced(result_envelope(summary="early"))
    final = early + "Corrected:\n" + fenced(result_envelope(summary="late"), intro="")
    assert adapter.parse_output(json.dumps(msg(final)))[1]["summary"] == "late"


def test_only_the_last_non_empty_message_of_the_main_agent_counts(adapter):
    lines = [msg(fenced(result_envelope(summary="first"))), msg("   "), msg(""),
             msg(fenced(result_envelope(summary="final"))),
             msg(fenced(result_envelope(summary="sub-agent")), parentToolCallId="call_1")]
    stdout = "\n".join(json.dumps(line) for line in lines)
    assert adapter.parse_output(stdout)[1]["summary"] == "final"
    stdout = "\n".join(json.dumps(line) for line in [msg(fenced(result_envelope())), msg("I could not finish.")])
    assert adapter.parse_output(stdout)[1] is None   # the final message has no envelope; an old draft is not used


def test_content_may_be_a_list_of_text_blocks(adapter):
    content = [{"type": "text", "text": "Done.\n"}, {"type": "text", "text": fenced(result_envelope(), intro="")}]
    assert adapter.parse_output(json.dumps(msg(content)))[1] == result_envelope()


def test_a_final_message_without_an_envelope_still_gives_session_and_usage(adapter):
    stdout = "\n".join(json.dumps(event) for event in (msg("All good, nothing to report."), result_event()))
    assert adapter.parse_output(stdout) == (SID, None, USAGE_NORMALIZED)


@pytest.mark.parametrize("final", [
    "not json at all",
    "```json\n{broken: json,}\n```",
    json.dumps({"schema": "multiagent.task_envelope/1", "task_id": "ORQ-T99"}),           # echoes the task, not a result
    json.dumps({"schema": "multiagent.task_result/2", "summary": "x"}),                    # another version
    json.dumps({"verdict": "ok"}),                                                         # an object that is no result
    "",
])
def test_a_missing_or_wrong_envelope_is_none(adapter, final):
    assert adapter.parse_output(json.dumps(msg(final)))[1] is None


def test_an_envelope_without_schema_but_with_summary_is_returned_for_the_service_to_judge(adapter):
    bare = {key: value for key, value in result_envelope().items() if key != "schema"}
    assert adapter.parse_output(json.dumps(msg(json.dumps(bare))))[1] == bare


def test_garbage_lines_are_skipped(adapter):
    stdout = "\n".join([
        'oken":"cut by the capture"}',            # the first line of a tail may be cut
        "", "   ", "Copilot 1.0.95", "[1, 2, 3]", "{", '{"type": ', "null", '"text"',
        json.dumps(msg(fenced(result_envelope()))),
        "{not json}",
        json.dumps(result_event()),
        "trailing banner",
    ])
    assert adapter.parse_output(stdout) == (SID, result_envelope(), USAGE_NORMALIZED)


@pytest.mark.parametrize("stdout", ["", "garbage only\nmore garbage", None, 42, b"{}"])
def test_unreadable_output_gives_nothing(adapter, stdout):
    assert adapter.parse_output(stdout) == (None, None, None)  # type: ignore[arg-type]


def test_the_session_id_falls_back_to_session_start_and_must_be_a_uuid(adapter):
    start = {"type": "session.start", "data": {"sessionId": OTHER_SID}}
    assert adapter.parse_output(json.dumps(start))[0] == OTHER_SID                         # the run died before result
    stdout = "\n".join(json.dumps(event) for event in (start, result_event(SID)))
    assert adapter.parse_output(stdout)[0] == SID                                          # result wins
    stdout = "\n".join(json.dumps(event) for event in (start, result_event("../../etc")))
    assert adapter.parse_output(stdout)[0] == OTHER_SID                                    # invalid ids are ignored
    assert adapter.parse_output(json.dumps(result_event("not-a-uuid")))[0] is None
    assert adapter.parse_output(json.dumps({"type": "result", "sessionId": 5}))[0] is None


def test_the_last_result_event_decides_usage(adapter):
    stdout = "\n".join(json.dumps(event) for event in (result_event(usage={"premiumRequests": 1}),
                                                       result_event(usage={"premiumRequests": 3})))
    assert adapter.parse_output(stdout)[2] == {"premium_requests": 3}


# --------------------------------------------------------------------------- usage (pure)

def test_usage_is_snake_case_and_keeps_premium_requests(adapter):
    usage = {"premiumRequests": 2.5, "totalApiDurationMs": 10, "model": "gpt-6-luna", "cached": True,
             "codeChanges": {"linesAdded": 4, "filesModified": ["a.py"], "note": "x"}, "tags": ["a"], "none": None}
    assert adapter.parse_output(json.dumps(result_event(usage=usage)))[2] == {
        "premium_requests": 2.5, "total_api_duration_ms": 10, "model": "gpt-6-luna", "cached": True,
        "code_changes": {"lines_added": 4}}


def test_usage_may_report_ai_credits_instead_of_premium_requests(adapter):
    usage = {"aiCredits": 12.5, "inputTokens": 1000, "outputTokens": 200, "cacheReadTokens": 50}
    assert adapter.parse_output(json.dumps(result_event(usage=usage)))[2] == {
        "ai_credits": 12.5, "input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 50}


@pytest.mark.parametrize("usage", [None, {}, [], "x", 3, {"list": [1]}, {"nested": {"text": "x"}}, {"nan": float("nan")}])
def test_missing_or_unusable_usage_is_none(adapter, usage):
    event = result_event(usage=usage)
    if usage is None:
        event.pop("usage", None)
    assert adapter.parse_output(json.dumps(event, allow_nan=True))[2] is None


def test_usage_is_bounded(adapter):
    usage = {f"key{i}": i for i in range(200)}
    assert len(adapter.parse_output(json.dumps(result_event(usage=usage)))[2]) == 50


# --------------------------------------------------------------------------- classify_failure (pure)

QUOTA_FIXTURES = [
    "Error: You've hit your rate limit. Please try again later.",
    "Sorry, you've hit a rate limit that restricts the number of Copilot model requests you can make",
    "402 You have no quota remaining",
    "You have exceeded your monthly premium request allowance.",
    "Quota exceeded for premium requests",
    "You've used all of your premium requests for this billing period",
    "Session AI credit limit reached. Increase or unset the session limit before trying again.",
    "Error: 429 Too Many Requests",
    "HTTP 402 Payment Required",
    "Monthly usage limit exceeded",
    "You have run out of AI credits",
    "Insufficient AI credits to run this model",
    "QUOTA EXHAUSTED",
]
AUTH_FIXTURES = [
    "Error: Not authenticated. Run `copilot login` to authenticate.",
    "You are not logged in. Please run /login",
    "Authentication required",
    "authentication failed for github.com",
    "HTTP 401 Unauthorized",
    "Bad credentials",
    "Your token has expired",
    "invalid token",
    "No authentication information found. To authenticate, use COPILOT_GITHUB_TOKEN or `gh auth login`.",
]
OTHER_FIXTURES = [
    "", "Error: Unknown model 'gpt-9'", "Segmentation fault", "cannot read quota.json", "the notice said no known file",
    "premium feature disabled", "visit https://example.test/login now", "error code 500", "ENOENT: no such file",
]


@pytest.mark.parametrize("text", QUOTA_FIXTURES)
def test_quota_and_limit_messages(adapter, text):
    assert adapter.classify_failure(1, text) is FailureKind.QUOTA


@pytest.mark.parametrize("text", AUTH_FIXTURES)
def test_authentication_messages(adapter, text):
    assert adapter.classify_failure(1, text) is FailureKind.AUTHENTICATION


@pytest.mark.parametrize("text", OTHER_FIXTURES)
def test_other_failures(adapter, text):
    assert adapter.classify_failure(1, text) is FailureKind.OTHER


def test_quota_is_checked_before_authentication_and_exit_zero_is_no_failure(adapter):
    assert adapter.classify_failure(1, "401 Unauthorized: rate limit exceeded") is FailureKind.QUOTA
    assert adapter.classify_failure(0, "rate limit exceeded") is FailureKind.OTHER
    assert adapter.classify_failure(None, "rate limit exceeded") is FailureKind.QUOTA
    assert adapter.classify_failure(1, None) is FailureKind.OTHER  # type: ignore[arg-type]
    assert FailureKind.BLOCKED_OPEN_SESSION not in {adapter.classify_failure(1, t) for t in QUOTA_FIXTURES + AUTH_FIXTURES}


# --------------------------------------------------------------------------- open target (pure)

def test_the_open_target_is_the_one_the_gui_uses(adapter):
    target = adapter.open_target(SID)
    assert target == OpenTarget("url", f"ghapp://sessions/{SID}", needs_confirmation=True)
    gui = OpenInApp("url", f"ghapp://sessions/{SID}", True)   # what sessions/copilot.py builds for the viewer
    assert (target.kind, target.value, target.needs_confirmation) == (gui.kind, gui.value, gui.needs_confirmation)
    assert adapter.open_target(SID.upper()) == target


@pytest.mark.parametrize("bad", ["", "latest", "../x", SID + "/..", "ghapp://sessions/x", None, 3, f"{SID}\n"])
def test_an_invalid_session_id_has_no_open_target(adapter, bad):
    assert adapter.open_target(bad) is None  # type: ignore[arg-type]
    assert not is_uuid(bad) or bad == SID


# --------------------------------------------------------------------------- describe

def _declaration(tmp_path):
    catalog = Catalog(Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                               gemini_api_key="", openai_api_key="", openai_enabled=False))
    return catalog.executor_routing


def test_describe_uses_the_catalog_evidence_without_the_claude_models(tmp_path, monkeypatch):
    monkeypatch.setattr(process, "run", lambda *a, **k: pytest.fail("describe must not launch"))
    monkeypatch.setattr(process, "start", lambda *a, **k: pytest.fail("describe must not launch"))
    declaration = _declaration(tmp_path)["executors"]["copilot_cli"]
    assert "claude-sonnet-5.5" in declaration["model_support"]["values"]            # what the catalog declares today
    descriptor = CopilotAdapter().describe(declaration)
    assert descriptor.executor_id == "copilot_cli" and descriptor.surface == "copilot_cli"
    assert descriptor.adapter_id == "copilot_cli" and descriptor.adapter_version == "1"
    assert descriptor.enabled is False                                              # the user enables it, not the adapter
    assert descriptor.mode == "automatic" and descriptor.provenance == "adapter_declaration"
    assert descriptor.model_support.status is SupportState.SUPPORTED
    assert descriptor.model_support.values == frozenset({"gpt-6-luna"})
    assert descriptor.resume.values == frozenset({"--resume=<id>"})
    assert descriptor.open_in_app.status is SupportState.SUPPORTED
    assert descriptor.blocks_when_open.status is SupportState.UNSUPPORTED
    assert descriptor.quota_signal.status is SupportState.UNKNOWN


def test_describe_enabled_defaults_to_false_and_needs_a_literal_true(tmp_path):
    declaration = _declaration(tmp_path)["executors"]["copilot_cli"]
    adapter = CopilotAdapter()
    assert adapter.describe({**declaration, "enabled": True}).enabled is True
    for value in (False, "yes", 1):
        assert adapter.describe({**declaration, "enabled": value}).enabled is False
    without = {key: value for key, value in declaration.items() if key != "enabled"}
    assert adapter.describe(without).enabled is False
    assert adapter.describe({}).mode == "manual" and adapter.describe({}).surface == "copilot_cli"


def test_describe_leaves_no_model_evidence_when_only_claude_models_were_declared(tmp_path):
    declaration = dict(_declaration(tmp_path)["executors"]["copilot_cli"])
    declaration["model_support"] = {**declaration["model_support"], "values": ["claude-sonnet-5.5"]}
    assert CopilotAdapter().describe(declaration).model_support.status is SupportState.UNKNOWN


def test_the_registry_resolves_the_adapter_once_the_catalog_names_it(tmp_path):
    config = validate_executor_config(_declaration(tmp_path))
    declaration = config["executors"]["copilot_cli"]
    registry = ExecutorRegistry(config, [CopilotAdapter()])
    assert registry.get("copilot_cli").enabled is False and registry.resolve("copilot_cli") is None   # adapter_id null

    wired = {**config, "executors": {"copilot_cli": {**declaration, "adapter_id": "copilot_cli", "enabled": True}}}
    registry = ExecutorRegistry(wired, [CopilotAdapter()])
    descriptor = registry.get("copilot_cli")
    assert descriptor.enabled is True and descriptor.mode == "automatic" and descriptor.adapter_id == "copilot_cli"
    assert registry.resolve("copilot_cli") is not None
    disabled = {**config, "executors": {"copilot_cli": {**declaration, "adapter_id": "copilot_cli"}}}
    assert ExecutorRegistry(disabled, [CopilotAdapter()]).get("copilot_cli").enabled is False


# --------------------------------------------------------------------------- launch with the fake program

def test_launch_runs_the_fake_and_reads_everything_back(fake, workdir, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_TEST", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-key")
    fake.respond(stdout=good_stdout())
    adapter = fake.adapter()
    request = make_request(workdir, make_envelope(objective=HOSTILE))

    result = adapter.launch(request)

    assert result.status is RunStatus.COMPLETED and result.notes == ()
    assert result.session_id == SID and result.result_envelope == result_envelope() and result.usage == USAGE_NORMALIZED
    assert result.command.exit_code == 0 and result.command.finished_at and Path(result.command.cwd) == workdir
    assert list(result.command.argv[1:]) == [str(fake.script), *expected_args(workdir)]
    assert result.command.argv[0] == os.path.abspath(sys.executable)

    (call,) = fake.calls()
    assert call["argv"] == expected_args(workdir)
    assert "Ignore the rules" not in " ".join(call["argv"])
    assert Path(call["cwd"]).resolve() == workdir.resolve()
    assert call["stdin"] == "" and call["current_dir_flag"] == "1" and call["scrubbed_vars"] == []
    # the prompt travelled in a file the framework created in the task folder, and is gone afterwards
    assert call["prompt_file"] == PROMPT_NAME and call["prompt_text"] == adapter.render_prompt(request)
    assert HOSTILE.split("\n")[0].replace('"', '\\"') in call["prompt_text"]
    assert list(workdir.iterdir()) == []
    assert adapter.open_target(result.session_id).value == f"ghapp://sessions/{SID}"


def test_launch_with_write_permission_passes_the_write_tool(fake, workdir):
    fake.respond(stdout=good_stdout())
    result = fake.adapter().launch(make_request(workdir, permission=Permission.WRITE, model=None, effort=None))
    assert result.status is RunStatus.COMPLETED
    (call,) = fake.calls()
    assert call["argv"] == expected_args(workdir, model=None, effort=None, write=True)
    assert "WRITE" in call["prompt_text"]


def test_launch_without_a_result_envelope_is_completed_but_says_so(fake, workdir):
    fake.respond(stdout="\n".join(json.dumps(e) for e in (
        {"type": "session.start", "data": {"sessionId": SID}}, msg("I looked at it; nothing to report."),
        result_event())))
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.COMPLETED and result.result_envelope is None
    assert result.session_id == SID and result.usage == USAGE_NORMALIZED
    assert result.notes == ("no_result_envelope",)
    assert "nothing to report" in result.stdout_tail


def test_launch_with_broken_output_keeps_the_text_as_evidence(fake, workdir):
    fake.respond(stdout=["<<<garbage>>>", '{"type": "result", "sessionId": ', "more garbage"], stderr="warning: x\n")
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.COMPLETED
    assert (result.session_id, result.result_envelope, result.usage) == (None, None, None)
    assert result.notes == ("no_result_envelope", "no_session_id", "usage_unavailable")
    assert "<<<garbage>>>" in result.stdout_tail and result.stderr_tail.strip() == "warning: x"


def test_launch_without_usage_in_the_result_event(fake, workdir):
    fake.respond(stdout="\n".join(json.dumps(e) for e in (msg(fenced(result_envelope())), result_event(usage=None))))
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.COMPLETED and result.usage is None and result.notes == ("usage_unavailable",)
    assert result.result_envelope == result_envelope()


@pytest.mark.parametrize("stderr,stdout_events,exit_code,status,kind", [
    ("Error: You've hit your rate limit.\n", [], 1, RunStatus.QUOTA_EXHAUSTED, "quota"),
    ("", [{"type": "session.error", "data": {"errorType": "quota", "message": "Quota exceeded for premium requests"}}],
     1, RunStatus.QUOTA_EXHAUSTED, "quota"),
    ("", [{"type": "error", "message": "402 You have no quota remaining"}], 1, RunStatus.QUOTA_EXHAUSTED, "quota"),
    ("Error: Not authenticated. Run `copilot login`.\n", [], 1, RunStatus.FAILED, "authentication"),
    ("Segmentation fault\n", [], 139, RunStatus.FAILED, "other"),
    ("", [], 2, RunStatus.FAILED, "other"),
])
def test_launch_failures_are_classified_and_never_retried(fake, workdir, stderr, stdout_events, exit_code, status, kind):
    fake.respond(stdout=stdout_events, stderr=stderr, exit_code=exit_code)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is status and result.notes == (f"failure:{kind}",)
    assert result.command.exit_code == exit_code and result.result_envelope is None
    assert len(fake.calls()) == 1                       # one process, no retry loop
    assert list(workdir.iterdir()) == []


def test_a_result_event_with_a_failing_exit_code_is_a_failure_even_if_the_process_exited_zero(fake, workdir):
    fake.respond(stdout="\n".join(json.dumps(e) for e in (
        {"type": "session.error", "data": {"message": "rate limit reached"}}, result_event(exit_code=1))), exit_code=0)
    result = fake.adapter().launch(make_request(workdir))
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.session_id == SID


def test_a_run_that_outlives_its_time_limit_is_killed_and_reported(fake, workdir):
    start = {"type": "session.start", "data": {"sessionId": SID}}
    fake.respond(stdout=[start], sleep=60)
    adapter = fake.adapter()
    result = adapter.launch(make_request(workdir, timeout_seconds=3))
    assert result.status is RunStatus.TIMEOUT and result.result_envelope is None and result.usage is None
    assert result.session_id == SID                         # recovered from session.start: it can still be recorded
    assert result.command.exit_code != 0
    (call,) = fake.calls()
    assert call["prompt_file"] == PROMPT_NAME                # the prompt file was there while it ran...
    assert list(workdir.iterdir()) == []                     # ...and is removed after the kill
    assert adapter.build_resume_command(make_request(workdir), SID)   # a session seen in this run may be continued


def test_the_time_limit_and_the_process_rules_reach_the_process_executor(workdir, monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return outcome(0, good_stdout())

    monkeypatch.setattr(process, "run", run)
    adapter = CopilotAdapter("copilot", env_extra={"COPILOT_HOME": "x"}, output_cap_bytes=5000)
    result = adapter.launch(make_request(workdir, timeout_seconds=77))
    assert result.status is RunStatus.COMPLETED
    assert seen["argv"] == ["copilot", *expected_args(workdir)]
    assert seen["cwd"] == workdir and seen["stdin_text"] is None and seen["timeout_seconds"] == 77
    assert seen["env_extra"] == {"COPILOT_HOME": "x"} and seen["output_cap_bytes"] == 5000


def test_a_cancelled_or_truncated_run_maps_to_its_status(workdir, monkeypatch):
    adapter = CopilotAdapter()
    monkeypatch.setattr(process, "run", lambda *a, **k: outcome(1, "", "", cancelled=True))
    assert adapter.launch(make_request(workdir)).status is RunStatus.CANCELLED
    monkeypatch.setattr(process, "run", lambda *a, **k: outcome(1, "", "rate limit", timed_out=True))
    assert adapter.launch(make_request(workdir)).status is RunStatus.TIMEOUT        # a timeout is not a quota failure
    monkeypatch.setattr(process, "run", lambda *a, **k: outcome(0, good_stdout(), truncated=True))
    result = adapter.launch(make_request(workdir))
    assert result.status is RunStatus.COMPLETED and result.notes == ("stdout_truncated",)


def test_the_kept_output_tails_are_bounded(workdir, monkeypatch):
    big = "x" * (OUTPUT_TAIL_CHARS * 3)
    monkeypatch.setattr(process, "run", lambda *a, **k: outcome(1, big + "END", big + "ERR"))
    result = CopilotAdapter().launch(make_request(workdir))
    assert len(result.stdout_tail) == len(result.stderr_tail) == OUTPUT_TAIL_CHARS
    assert result.stdout_tail.endswith("END") and result.stderr_tail.endswith("ERR")


def test_a_capped_capture_still_finds_the_session_and_usage_at_the_end(fake, workdir):
    filler = [{"type": "tool.execution_progress", "data": {"text": "." * 90}} for _ in range(400)]
    fake.respond(stdout=[*filler, msg(fenced(result_envelope())), result_event()])
    result = fake.adapter(output_cap_bytes=4096).launch(make_request(workdir))
    assert result.status is RunStatus.COMPLETED and "stdout_truncated" in result.notes
    assert result.session_id == SID and result.usage == USAGE_NORMALIZED and result.result_envelope == result_envelope()


def test_a_planted_file_with_the_prompt_name_is_replaced_not_followed(fake, workdir, tmp_path):
    fake.respond(stdout=good_stdout())
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")
    planted = workdir / PROMPT_NAME
    try:
        planted.symlink_to(victim)
    except (OSError, NotImplementedError):
        planted.write_text("stale prompt", encoding="utf-8")
    adapter = fake.adapter()
    assert adapter.launch(make_request(workdir)).status is RunStatus.COMPLETED
    assert victim.read_text(encoding="utf-8") == "keep me"
    (call,) = fake.calls()
    assert call["prompt_text"] == adapter.render_prompt(make_request(workdir))
    assert not os.path.lexists(planted)


def test_the_request_session_id_is_ignored_and_said_so(fake, workdir):
    fake.respond(stdout=good_stdout())
    result = fake.adapter().launch(make_request(workdir, session_id=OTHER_SID))
    assert result.session_id == SID and result.notes == ("requested_session_id_ignored",)
    assert OTHER_SID not in " ".join(fake.calls()[0]["argv"])


def test_nothing_is_written_or_started_when_a_rule_is_broken(fake, workdir, tmp_path):
    adapter = fake.adapter()
    with pytest.raises(ProcessLaunchError) as caught:
        adapter.launch(make_request(tmp_path / "missing"))
    assert caught.value.code == process.INVALID_CWD
    with pytest.raises(CopilotAdapterError):
        adapter.launch(make_request(workdir, model="claude-opus"))
    with pytest.raises(EnvelopeError):
        adapter.launch(make_request(workdir, {**make_envelope(), "status": "bogus"}))
    with pytest.raises(ProcessLaunchError) as caught:
        CopilotAdapter("no-such-program-for-copilot-tests").launch(make_request(workdir))
    assert caught.value.code == process.EXECUTABLE_NOT_FOUND
    assert fake.calls() == [] and list(workdir.iterdir()) == []   # the prompt file of the last case was cleaned up


def test_a_program_inside_the_task_folder_is_refused(workdir, tmp_path):
    inside = workdir / "fake-cli.py"
    inside.write_text("print(1)", encoding="utf-8")
    with pytest.raises(ProcessLaunchError) as caught:
        CopilotAdapter([str(inside)]).launch(make_request(workdir))
    assert caught.value.code in {process.EXECUTABLE_IN_WORKDIR, process.EXECUTABLE_NOT_FOUND}
    assert not (workdir / PROMPT_NAME).exists()


# --------------------------------------------------------------------------- resume with the fake program

def test_resume_continues_the_session_the_launch_created(fake, workdir):
    fake.respond(stdout=good_stdout())
    adapter = fake.adapter()
    first = adapter.launch(make_request(workdir))
    assert first.session_id == SID

    fake.respond(stdout=good_stdout(result_envelope(summary="second pass")))
    second = adapter.resume(make_request(workdir, make_envelope(revision=4)), first.session_id)
    assert second.status is RunStatus.COMPLETED and second.session_id == SID
    assert second.result_envelope["summary"] == "second pass"

    launch_call, resume_call = fake.calls()
    assert launch_call["argv"] == expected_args(workdir)
    assert resume_call["argv"] == expected_args(workdir, resume=SID, name="maf-prompt-ORQ-T99-1-resume.md")
    assert resume_call["prompt_file"] == "maf-prompt-ORQ-T99-1-resume.md"
    assert resume_call["prompt_text"] == adapter.render_prompt(make_request(workdir, make_envelope(revision=4)))
    assert list(workdir.iterdir()) == []


def test_resume_refuses_a_session_the_framework_did_not_create(fake, workdir):
    fake.respond(stdout=good_stdout())
    adapter = fake.adapter()
    with pytest.raises(CopilotAdapterError) as caught:
        adapter.resume(make_request(workdir), OTHER_SID)
    assert caught.value.code == "session_not_created_by_framework"
    assert fake.calls() == [] and list(workdir.iterdir()) == []
    stored = fake.adapter(session_guard=lambda sid: sid == OTHER_SID)         # the service vouches for a stored id
    assert stored.resume(make_request(workdir), OTHER_SID).status is RunStatus.COMPLETED
    assert fake.calls()[0]["argv"] == expected_args(workdir, resume=OTHER_SID, name="maf-prompt-ORQ-T99-1-resume.md")


def test_a_quota_failure_on_resume_is_reported_like_one_on_launch(fake, workdir):
    adapter = fake.adapter(session_guard=lambda sid: True)
    fake.respond(stderr="Error: 429 Too Many Requests", exit_code=1)
    result = adapter.resume(make_request(workdir), SID)
    assert result.status is RunStatus.QUOTA_EXHAUSTED and result.notes == ("failure:quota",)


# --------------------------------------------------------------------------- a Windows .cmd shim (npm) in front of the fake

def make_launcher(folder: Path, name: str, script: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        path = folder / f"{name}.cmd"
        path.write_bytes(f'@"{sys.executable}" "{script}" %*\r\n'.encode())
    else:
        path = folder / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_the_argv_runs_through_a_launcher_that_is_a_cmd_shim_on_windows(fake, workdir, tmp_path):
    fake.respond(stdout=good_stdout())
    shim = make_launcher(tmp_path / "bin", "fake-agent-cli", fake.script)
    adapter = CopilotAdapter(str(shim), env_extra=fake.env)
    result = adapter.launch(make_request(workdir, permission=Permission.WRITE))
    assert result.status is RunStatus.COMPLETED and result.session_id == SID
    (call,) = fake.calls()
    assert call["argv"] == expected_args(workdir, write=True)
    assert result.command.argv[0].lower().endswith(".cmd" if sys.platform == "win32" else "fake-agent-cli")
