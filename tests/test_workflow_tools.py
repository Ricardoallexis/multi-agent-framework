"""Workflow steps that declare tools invoke them through ToolInvoker in the real engine (CAP-T02/T19 wiring)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import PipelineMode, RunStatus, SocialPostRequest
from multiagent.services.runs import pending_tool_approval
from multiagent.tools.contracts import ImpactClass, InvocationKind, SchemaRef, ToolSpec
from multiagent.tasks.envelopes import envelope_sha256
from multiagent.workflow_validation import WorkflowDefinitionError
from multiagent.workflows import WorkflowCatalog


def _settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", data_dir=tmp_path / "data",
                    db_path=tmp_path / "data" / "multiagent.db", runs_dir=tmp_path / "data" / "runs",
                    assets_dir=tmp_path / "data" / "assets", worker_enabled=False, gemini_api_key="",
                    openai_api_key="", openai_enabled=False)


def _system(tmp_path: Path, tools_by_step: dict[str, list[dict]]):
    settings = _settings(tmp_path)
    system = build_system(settings, dry_run=True)
    folder = tmp_path / "workflows"
    folder.mkdir()
    source = Path(settings.workflows_dir) / "social_post_full.yaml"
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    for step in document["steps"]:
        if step["id"] in tools_by_step:
            step["tools"] = tools_by_step[step["id"]]
    (folder / "social_post_full.yaml").write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    system.engine.workflows = WorkflowCatalog(settings, workflows_dir=folder)
    return system


def _start(system):
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="tools-wiring", objective="Create an educational post", topic="KNX",
        pipeline_mode=PipelineMode.FULL))
    system.worker.process_once()
    return system.run_service.get(run["id"])


def _kinds(state, prefix="tool_"):
    return [event["event_type"] for event in state["events"] if event["event_type"].startswith(prefix)]


def test_a_read_tool_runs_in_the_step_and_its_result_reaches_the_prompt(tmp_path):
    system = _system(tmp_path, {"strategy": [{"tool": "mock.echo", "arguments": {"text": "${topic}"}}]})
    state = _start(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value and state["waiting_reason"] == "review"
    assert _kinds(state) == ["tool_call_requested", "tool_call_completed"]
    completed = next(e for e in state["events"] if e["event_type"] == "tool_call_completed")
    assert completed["payload"]["tool_id"] == "mock.echo" and completed["payload"]["step_id"] == "strategy"


def test_a_write_tool_pauses_for_tool_approval_and_runs_once_after_the_person_approves(tmp_path):
    system = _system(tmp_path, {"create": [{"tool": "mock.note_write",
                                            "arguments": {"target": "notes/post.txt", "text": "${topic}"}}]})
    state = _start(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value and state["waiting_reason"] == "tool_approval"
    pending = pending_tool_approval(state)
    assert pending["tool_id"] == "mock.note_write"
    assert "tool_call_completed" not in _kinds(state)
    approved = system.run_service.approve(state["id"], permission_request_id=pending["permission_request_id"],
                                          person=True)
    assert approved["status"] == RunStatus.QUEUED.value
    system.worker.process_once()
    state = system.run_service.get(state["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value and state["waiting_reason"] == "review"
    kinds = _kinds(state)
    assert kinds.count("tool_call_completed") == 1 and "tool_approval_released" in kinds
    assert system.run_service.approve(state["id"])["status"] == RunStatus.COMPLETED.value


def test_rejecting_the_tool_call_fails_the_run_without_running_it(tmp_path):
    system = _system(tmp_path, {"create": [{"tool": "mock.note_write",
                                            "arguments": {"target": "notes/post.txt", "text": "x"}}]})
    state = _start(system)
    system.run_service.reject(state["id"], feedback="no")
    state = system.run_service.get(state["id"])
    assert state["status"] == RunStatus.FAILED.value and "tool_call_completed" not in _kinds(state)


def test_a_capability_is_resolved_by_select_tool(tmp_path):
    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
              "additionalProperties": False}
    ref = lambda name: SchemaRef(schema_id=name, json_schema=schema, sha256=envelope_sha256(schema))
    system = _system(tmp_path, {"strategy": [{"capability": "text.upper", "arguments": {"text": "knx"}}]})
    system.tool_registry.register(
        ToolSpec(tool_id="demo.upper", version=1, title="Upper", invocation=InvocationKind.LOCAL,
                 impacts=frozenset({ImpactClass.READ}), capabilities=frozenset({"text.upper"}),
                 input_schema=ref("demo.upper.input/1"), output_schema=ref("demo.upper.output/1"),
                 declared_by="builtin"),
        lambda arguments, context, *, should_cancel: {"text": arguments["text"].upper()})
    state = _start(system)
    completed = [e for e in state["events"] if e["event_type"] == "tool_call_completed"]
    assert [e["payload"]["tool_id"] for e in completed] == ["demo.upper"]


def test_an_unknown_tool_fails_the_run(tmp_path):
    system = _system(tmp_path, {"strategy": [{"tool": "mock.missing", "arguments": {}}]})
    state = _start(system)
    assert state["status"] == RunStatus.FAILED.value and "unknown_tool" in state["error"]


@pytest.mark.parametrize("entry", [{"arguments": {}}, {"tool": "mock.echo", "capability": "x.y"},
                                   {"tool": "mock.echo", "arguments": []}, {"tool": "mock.echo", "extra": 1}])
def test_malformed_tool_declarations_are_rejected_at_preflight(tmp_path, entry):
    system = _system(tmp_path, {"strategy": [entry]})
    with pytest.raises(WorkflowDefinitionError):
        system.engine.preflight("social_post_full")
