# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Run operating modes fixed at creation, with native and mock handoffs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.contracts import HumanStepSubmission
from multiagent.document_actions import ActionKind
from multiagent.external_output import canonical_json, sha256
from multiagent.run_request import RunRequest
from multiagent.services import ServiceError
from multiagent.step_cache import StepCachePolicy
from multiagent.teams.schema import TeamDefinitionError, load_team, parse_team_document
from multiagent.tools.contracts import EffectDecl, ImpactClass, InvocationKind, SchemaRef, ToolSpec
from multiagent.tasks.envelopes import envelope_sha256

from test_step_role import _bundle, _drain, _request, _runtime, _sample, TWO_ROLES
from test_team_approval import _copy_general, _read_team_yaml, _services, _write_team_yaml


@pytest.fixture
def close():
    opened = []
    yield opened.append
    for services in opened:
        services.runs.close()


def _events(run, kind):
    return [event["payload"] for event in run["events"] if event["event_type"] == kind]


def _effect_tool(rt, impact):
    input_schema = {"type": "object", "properties": {"target": {"type": "string"}},
                    "required": ["target"], "additionalProperties": False}
    output_schema = {"type": "object", "properties": {"text": {"type": "string"}},
                     "required": ["text"], "additionalProperties": False}
    def ref(name, schema):
        return SchemaRef(schema_id=name, json_schema=schema, sha256=envelope_sha256(schema))
    kind = ActionKind.DESTRUCTIVE if impact == ImpactClass.DESTRUCTIVE else ActionKind.NETWORK
    rt.services.system.tool_registry.register(
        ToolSpec(tool_id="mock.effect", version=1, title="Effect", invocation=InvocationKind.LOCAL,
                 impacts=frozenset({impact}), effects=(EffectDecl(kind=kind, target_from="/target"),),
                 allowed_data_classes=frozenset({"non_sensitive"}) if impact == ImpactClass.EXTERNAL else None,
                 input_schema=ref("mock.effect.input/1", input_schema),
                 output_schema=ref("mock.effect.output/1", output_schema), declared_by="builtin"),
        lambda arguments, context, *, should_cancel: {"text": "done"})


def test_team_mode_validation_and_legacy_hash():
    path = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general" / "team.yaml"
    team = load_team(path)
    canonical = team.model_dump(mode="json")
    assert "mode" not in canonical
    assert sha256(canonical_json(canonical)) == "d1b63e35b67b715344a385bbb736bae10645a9f9c0218ab062377ef46277f64c"
    with pytest.raises(TeamDefinitionError) as error:
        parse_team_document(path.read_text(encoding="utf-8") + "\nmode: unknown\n")
    assert [(issue.code, issue.location) for issue in error.value.issues] == [("invalid_mode", "mode")]


def test_team_version_diff_tracks_mode_without_changing_legacy_team(tmp_path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        v1 = services.teams.propose("case", actor="user:ana", channel="python")
        assert v1["sha256"] == "d1b63e35b67b715344a385bbb736bae10645a9f9c0218ab062377ef46277f64c"
        assert v1["diff"]["team_fields"] == {}
        assert len(v1["diff"]["roles"]["added"]) == 4
        services.teams.approve(v1["id"], actor="user:ana", channel="python")

        data = _read_team_yaml(bundle_dir)
        data["mode"] = "supervised"
        _write_team_yaml(bundle_dir, data)
        v2 = services.teams.propose("case", actor="user:ana", channel="python")
        assert v2["diff"]["team_fields"] == {"mode": {"before": None, "after": "supervised"}}
        assert v2["diff"]["has_changes"] is True
        assert all(not any(section.values()) for section in
                   (v2["diff"][name] for name in ("roles", "members", "assignments", "relationships")))
        services.teams.approve(v2["id"], actor="user:ana", channel="python")

        data["mode"] = "autonomous"
        _write_team_yaml(bundle_dir, data)
        v3 = services.teams.propose("case", actor="user:ana", channel="python")
        assert v3["diff"]["team_fields"] == {
            "mode": {"before": "supervised", "after": "autonomous"}}
        assert v3["diff"]["has_changes"] is True
    finally:
        services.runs.close()


@pytest.mark.parametrize("backend", ["native", "mock"])
@pytest.mark.parametrize("team_mode,run_mode,expected,source", [
    ("supervised", None, "supervised", "team"),
    ("supervised", "autonomous", "autonomous", "run"),
    (None, "supervised", "supervised", "run"),
    (None, None, None, None),
])
def test_mode_is_bound_and_handoff_behaves_by_mode(tmp_path, close, backend, team_mode, run_mode, expected, source):
    def set_mode(data):
        if team_mode:
            data["mode"] = team_mode
    rt = _runtime(tmp_path, backend, _bundle(tmp_path, TWO_ROLES, team=set_mode))
    close(rt.services)
    run = rt.create(_request(**({"operating_mode": run_mode} if run_mode else {})))
    fixed = _events(run, "operating_mode_set")
    assert fixed == ([{"mode": expected, "source": source}] if expected else [])
    _drain(rt)
    state = rt.store.get_run(run["id"])
    if expected == "supervised":
        assert (state["status"], state["waiting_reason"], state["waiting_step"]) == (
            "waiting_human", "review", "extract_key_points")
        assert len(_events(state, "checkpoint")) == 1
        rt.service.approve(run["id"])
        _drain(rt)
        assert rt.store.get_run(run["id"])["status"] == "completed"
    else:
        assert state["status"] == "completed"
        assert not _events(state, "checkpoint")
    if backend == "native":
        assert rt.services.runs.get(run["id"])["operating_mode"] == expected


@pytest.mark.parametrize("mode,expected_wait", [("supervised", True), ("autonomous", False)])
def test_write_approval_depends_on_mode(tmp_path, close, mode, expected_wait):
    def team(data):
        data["mode"] = mode
        data["roles"][1]["permissions"] = ["write"]
    root = _bundle(tmp_path, {"extract_key_points": {
        "role": "analyst", "tools": [{"tool": "mock.note_write",
                               "arguments": {"target": "notes/run.txt", "text": "hello"}}]}}, team=team)
    rt = _runtime(tmp_path, "native", root)
    close(rt.services)
    run = rt.create(_request())
    _drain(rt)
    state = rt.store.get_run(run["id"])
    if expected_wait:
        assert (state["status"], state["waiting_reason"]) == ("waiting_human", "tool_approval")
        pending = rt.services.runs.get(run["id"])["tool_approval"]
        rt.service.approve(run["id"], permission_request_id=pending["permission_request_id"], person=True)
        _drain(rt)
        state = rt.store.get_run(run["id"])
    else:
        assert state["status"] == "completed"
        assert _events(state, "tool_call_completed")[0]["approval_cause"] == "autonomous"
    assert len(_events(state, "tool_call_completed")) == 1


def test_autonomous_write_outside_role_permissions_is_rejected_at_team_validation(tmp_path):
    root = _bundle(tmp_path, {"extract_key_points": {
        "role": "analyst", "tools": [{"tool": "mock.note_write",
                               "arguments": {"target": "notes/run.txt", "text": "hello"}}]}},
        team=lambda data: data.update(mode="autonomous"))
    with pytest.raises(ServiceError) as error:
        _runtime(tmp_path, "native", root)
    assert error.value.code == "invalid_definition"


@pytest.mark.parametrize("backend", ["native", "mock"])
def test_supervised_human_step_handoff_waits_for_review(tmp_path, close, backend):
    rt = _runtime(tmp_path, backend, _bundle(tmp_path, TWO_ROLES,
        team=lambda data: data.update(mode="supervised")))
    close(rt.services)
    run = rt.create(_request(step_modes={"extract_key_points": "human_guided"}))
    _drain(rt)
    assert rt.store.get_run(run["id"])["waiting_reason"] == "execute_step"
    rt.service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps(_sample(rt, "KeyPoints"))))
    state = rt.store.get_run(run["id"])
    assert (state["waiting_reason"], state["waiting_step"]) == ("review", "extract_key_points")
    rt.service.approve(run["id"])
    _drain(rt)
    assert rt.store.get_run(run["id"])["status"] == "completed"


def test_supervised_handoff_is_not_skipped_by_step_cache(tmp_path, close):
    rt = _runtime(tmp_path, "native", _bundle(tmp_path, TWO_ROLES,
        team=lambda data: data.update(mode="supervised")))
    close(rt.services)
    rt.service.engine.step_cache_policy = StepCachePolicy(enabled=True)
    for _ in range(2):
        run = rt.create(_request())
        _drain(rt)
        state = rt.store.get_run(run["id"])
        assert (state["waiting_reason"], state["waiting_step"]) == ("review", "extract_key_points")
        assert not _events(state, "step_cache_hit")
        rt.service.approve(run["id"])
        _drain(rt)


@pytest.mark.parametrize("impact", [ImpactClass.DESTRUCTIVE, ImpactClass.EXTERNAL])
def test_autonomous_high_impact_still_waits(tmp_path, close, impact):
    root = _bundle(tmp_path, {"extract_key_points": {
        "tools": [{"tool": "mock.effect", "arguments": {"target": "notes/run.txt"}}]}},
        team=lambda data: data.update(mode="autonomous"))
    rt = _runtime(tmp_path, "native", root)
    close(rt.services)
    _effect_tool(rt, impact)
    run = rt.create(_request())
    _drain(rt)
    state = rt.store.get_run(run["id"])
    assert (state["status"], state["waiting_reason"]) == ("waiting_human", "tool_approval")
    assert not _events(state, "tool_call_completed")


def test_plan_still_waits_for_user(tmp_path):
    from multiagent.config import Settings
    from multiagent.services import ApplicationServices
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        execution_backend="mock", mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings)
    try:
        plan = services.planning.propose("manual", {"group_id": "launch", "goal": "Launch",
            "tasks": [{"id": "technical", "title": "Technical review", "workflow": "social_post",
                       "depends_on": [], "objective": "Review", "inputs": {"topic": "Launch"}}]})
        saved = services.plan_proposals.save(plan, actor="agent:planner")
        assert not services.tasks.list_tasks(group_id="launch")
        with pytest.raises(ServiceError):
            services.plan_proposals.approve(saved["id"], actor="agent:planner", channel="python",
                                            expected_revision=1)
        assert not services.tasks.list_tasks(group_id="launch")
        services.plan_proposals.approve(saved["id"], actor="user:ana", channel="python", expected_revision=1)
        assert services.tasks.list_tasks(group_id="launch")
    finally:
        services.runs.close()


def test_api_and_cli_pass_run_mode(tmp_path, monkeypatch, capsys):
    from multiagent.config import Settings
    from multiagent.services import ApplicationServices
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings)
    class ClientContext:
        def __init__(self, client):
            self.client = client
        def __enter__(self):
            return self.client
        def __exit__(self, *args):
            return False
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            body = {"project_name": "Mode parity", "workflow_id": "social_post",
                    "operating_mode": "supervised"}
            response = client.post("/api/v1/runs", json=body)
            assert response.status_code == 202, response.text
            api_run = client.get(f"/api/v1/runs/{response.json()['id']}").json()
            assert api_run["operating_mode"] == "supervised"
            monkeypatch.setattr(cli, "api_client", lambda: ClientContext(client))
            cli.main(["run", "--project", "Mode parity", "--workflow", "social_post",
                      "--mode", "human_guided", "--operating-mode", "autonomous"])
            cli_run = json.loads(capsys.readouterr().out)
            assert client.get(f"/api/v1/runs/{cli_run['id']}").json()["operating_mode"] == "autonomous"
            assert _events(cli_run, "operating_mode_set") == [{"mode": "autonomous", "source": "run"}]
            assert cli_run["request"]["execution_mode"] == "human_guided"
    finally:
        services.runs.close()
