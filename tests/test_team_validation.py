# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices
from multiagent.teams import Team, validate_team_workflows
from multiagent.tools.mock import register_mock_tools
from multiagent.tools.registry import ToolRegistry
from multiagent.workflows import WorkflowDefinition, WorkflowStep

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
SPEC_REVIEW_BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


def _team(*, roles=None, assignments=None, members=None) -> Team:
    return Team.model_validate({
        "schema_version": 1,
        "id": "test_team",
        "roles": roles or [{"id": "worker", "purpose": "Does the work."}],
        "members": members or [{"id": "worker_member", "kind": "agent", "agent": "worker_agent"}],
        "assignments": assignments or [{"role": "worker", "member": "worker_member"}],
        "relationships": [],
    })


def _workflow(*, agent="worker_agent", tools=None) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="sample",
        max_iterations=1,
        steps=[WorkflowStep(
            id="step",
            agent=agent,
            prompt_id="sample",
            prompt_version=1,
            skills=[],
            contract="Known",
            preferred_model=None,
            fallback_model=None,
            requires_web=None,
            when=None,
            checkpoint_after=False,
            tools=tools,
        )],
    )


def _validate(team, workflows=(), *, skills_dir=Path("."), registry=None, selection=None):
    return validate_team_workflows(
        team,
        workflows,
        output_schemas={"Known": object},
        skills_dir=skills_dir,
        tool_registry=registry,
        tool_selection=selection,
    )


def test_unknown_role_contract_is_an_error_with_team_location(tmp_path: Path):
    team = _team(roles=[{"id": "worker", "purpose": "Does the work.", "contracts": ["Missing"]}])

    issue, = _validate(team, skills_dir=tmp_path)

    assert (issue.code, issue.location, issue.severity) == (
        "team_unknown_contract", "team.yaml:roles[0].contracts[0]", "error",
    )


def test_unknown_role_skill_is_an_error_with_team_location(tmp_path: Path):
    team = _team(roles=[{"id": "worker", "purpose": "Does the work.", "skills": ["research/missing"]}])

    issue, = _validate(team, skills_dir=tmp_path)

    assert (issue.code, issue.location, issue.severity) == (
        "team_unknown_skill", "team.yaml:roles[0].skills[0]", "error",
    )


def test_missing_tool_impact_permission_is_an_error(tmp_path: Path):
    team = _team(roles=[{"id": "worker", "purpose": "Does the work.", "permissions": ["read"]}])
    registry = ToolRegistry()
    register_mock_tools(registry)

    issues = _validate(team, [_workflow(tools=[{"tool": "mock.note_write"}])],
                       skills_dir=tmp_path, registry=registry)
    issue = next(item for item in issues if item.code == "team_permission_missing")

    assert (issue.location, issue.severity) == ("workflows/sample:steps[0]", "error")
    assert all(value in issue.message for value in ("worker", "mock.note_write", "write"))


def test_unused_role_permission_is_a_warning(tmp_path: Path):
    team = _team(roles=[{"id": "worker", "purpose": "Does the work.", "permissions": ["write"]}])

    issue, = _validate(team, skills_dir=tmp_path)

    assert (issue.code, issue.location, issue.severity) == (
        "team_permission_unused", "team.yaml:roles[0].permissions[0]", "warning",
    )


def test_step_agent_without_a_team_role_is_a_warning(tmp_path: Path):
    issues = _validate(_team(), [_workflow(agent="outside_agent")], skills_dir=tmp_path)

    issue, = issues
    assert (issue.code, issue.location, issue.severity) == (
        "team_agent_unassigned", "workflows/sample:steps[0]", "warning",
    )


def test_unresolved_step_tool_is_a_warning_and_skips_permission_checks(tmp_path: Path):
    team = _team()

    issues = _validate(team, [_workflow(tools=[{"tool": "missing.tool"}])], skills_dir=tmp_path,
                       registry=ToolRegistry())

    assert [(issue.code, issue.location, issue.severity) for issue in issues] == [
        ("team_tool_unresolved", "workflows/sample:steps[0]", "warning"),
    ]


def test_member_with_two_roles_uses_the_union_of_role_permissions(tmp_path: Path):
    team = _team(
        roles=[
            {"id": "reader", "purpose": "Reads.", "permissions": ["read"]},
            {"id": "writer", "purpose": "Writes.", "permissions": ["write"]},
        ],
        assignments=[
            {"role": "reader", "member": "worker_member"},
            {"role": "writer", "member": "worker_member"},
        ],
    )
    registry = ToolRegistry()
    register_mock_tools(registry)

    issues = _validate(team, [_workflow(tools=[{"tool": "mock.note_write"}])],
                       skills_dir=tmp_path, registry=registry)

    assert not any(issue.code == "team_permission_missing" for issue in issues)
    assert [(issue.code, issue.location) for issue in issues] == [
        ("team_permission_unused", "team.yaml:roles[0].permissions[0]"),
    ]


def _settings(root: Path) -> Settings:
    return Settings(
        _env_file=None,
        local_dir=root,
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )


def _services(root: Path, bundle: Path) -> ApplicationServices:
    return ApplicationServices.create(_settings(root), bundles={"case": bundle})


def _copy_general_bundle(root: Path) -> Path:
    destination = root / "general"
    shutil.copytree(GENERAL_BUNDLE, destination)
    return destination


def test_bundle_without_team_keeps_existing_validation_shape(tmp_path: Path):
    services = _services(tmp_path / "local", SPEC_REVIEW_BUNDLE)
    try:
        assert services.definitions.validate_bundle("case") == {
            "bundle": "case",
            "valid": True,
            "workflows": ["spec_review"],
        }
    finally:
        services.runs.close()


def test_bundle_validation_api_returns_team_errors_in_422_details(tmp_path: Path):
    bundle = _copy_general_bundle(tmp_path)
    team_file = bundle / "team.yaml"
    team = yaml.safe_load(team_file.read_text(encoding="utf-8"))
    team["roles"][0]["contracts"].append("MissingOutput")
    team_file.write_text(yaml.safe_dump(team, sort_keys=False), encoding="utf-8")
    workflow_file = bundle / "workflows" / "summarize_document.yaml"
    workflow = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    workflow["steps"][0]["contract"] = "MissingWorkflowOutput"
    workflow_file.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    services = _services(tmp_path / "local", bundle)
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            response = client.post("/api/v1/bundles/case/validate")

        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["details"]["team"][0]["code"] == "team_unknown_contract"
        assert detail["details"]["team"][0]["location"] == "team.yaml:roles[0].contracts[3]"
        assert detail["details"]["team"][0]["severity"] == "error"
        workflow_issues = [
            issue
            for workflow in detail["details"]["workflows"]
            for issue in workflow["issues"]
        ]
        assert workflow_issues
        assert all(
            issue["severity"] == "error"
            for issue in workflow_issues
        )
    finally:
        services.runs.close()


def test_bundle_validation_api_returns_warnings_for_valid_team(tmp_path: Path):
    bundle = _copy_general_bundle(tmp_path)
    team_file = bundle / "team.yaml"
    team = yaml.safe_load(team_file.read_text(encoding="utf-8"))
    team["roles"][0]["permissions"] = ["destructive"]
    team_file.write_text(yaml.safe_dump(team, sort_keys=False), encoding="utf-8")
    services = _services(tmp_path / "local", bundle)
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            response = client.post("/api/v1/bundles/case/validate")

        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["warnings"] == [{
            "code": "team_permission_unused",
            "location": "team.yaml:roles[0].permissions[0]",
            "message": "Role 'lead' does not need permission 'destructive' for any assigned member step",
            "severity": "warning",
        }]
    finally:
        services.runs.close()


def test_bundle_validate_cli_prints_422_findings_and_exits_one(monkeypatch, capsys):
    response = type("Response", (), {
        "status_code": 422,
        "json": lambda self: {"detail": {"details": {
            "workflows": [{"workflow_id": "sample", "issues": [{
                "code": "unknown_contract",
                "location": "steps[0].contract",
                "message": "missing contract",
                "severity": "error",
            }]}],
            "team": [{
                "code": "team_permission_missing",
                "location": "workflows/sample:steps[0]",
                "message": "missing write for mock.note_write",
                "severity": "error",
            }],
        }}},
    })()
    monkeypatch.setattr(cli, "_api_response", lambda *args, **kwargs: response)

    with pytest.raises(SystemExit) as caught:
        cli.main(["bundle-validate", "case"])

    output = capsys.readouterr().err
    assert caught.value.code == 1
    assert "[error] workflows/sample:steps[0].contract" in output
    assert "[error] workflows/sample:steps[0]" in output


def test_bundle_validate_cli_prints_warnings_and_exits_zero(monkeypatch, capsys):
    response = type("Response", (), {
        "status_code": 200,
        "json": lambda self: {"bundle": "case", "valid": True, "workflows": [],
                              "warnings": [{"code": "team_permission_unused", "severity": "warning"}]},
    })()
    monkeypatch.setattr(cli, "_api_response", lambda *args, **kwargs: response)

    assert cli.main(["bundle-validate", "case"]) is None

    assert json.loads(capsys.readouterr().out)["warnings"][0]["severity"] == "warning"
