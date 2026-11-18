# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Team definition schema, structured validation and optional bundle team.yaml loading (EQP-T01)."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError
from multiagent.teams import (
    MemberKind,
    RelationshipType,
    Team,
    TeamDefinitionError,
    load_team,
)
from multiagent.tools.contracts import ImpactClass
from multiagent.workflow_validation import WorkflowDefinitionError

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
SPEC_REVIEW_BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


def valid_team_dict() -> dict:
    return {
        "schema_version": 1,
        "id": "delivery_team",
        "description": "Cross-functional team for delivery workflows.",
        "roles": [
            {
                "id": "lead",
                "purpose": "Coordinates work and approves outputs.",
                "rules": ["Review before release."],
                "skills": ["planning/review"],
                "contracts": ["SummaryOutput"],
                "capabilities": ["doc.review"],
                "permissions": ["read"],
            },
            {
                "id": "researcher",
                "purpose": "Gathers verified source material.",
                "rules": ["Cite sources."],
                "skills": [],
                "contracts": ["ResearchOutput"],
                "capabilities": ["web.search", "file.read"],
                "permissions": ["read", "external"],
            },
            {
                "id": "writer",
                "purpose": "Produces structured drafts.",
                "rules": [],
                "skills": ["writing/draft"],
                "contracts": ["DraftOutput"],
                "capabilities": ["file.write"],
                "permissions": ["read", "write"],
            },
        ],
        "members": [
            {"id": "m_lead", "kind": "person", "name": "Alice"},
            {"id": "m_researcher", "kind": "agent", "agent": "research_agent"},
            {"id": "m_writer", "kind": "agent", "agent": "writer_agent"},
        ],
        "assignments": [
            {"role": "lead", "member": "m_lead"},
            {"role": "researcher", "member": "m_researcher"},
            {"role": "writer", "member": "m_writer"},
        ],
        "relationships": [
            {"type": "reports_to", "from": "researcher", "to": "lead"},
            {"type": "reports_to", "from": "writer", "to": "lead"},
            {"type": "reviews", "from": "lead", "to": "writer"},
            {"type": "delegates_to", "from": "lead", "to": "researcher"},
        ],
    }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )


def load_team_error(tmp_path: Path, data: dict, *, agents: dict | list | None = None, ext: str = ".yaml") -> TeamDefinitionError:
    path = tmp_path / f"team{ext}"
    if ext == ".json":
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(TeamDefinitionError) as caught:
        load_team(path, agents=agents)
    return caught.value


def test_loads_valid_team_from_yaml_yml_json_and_model_validate(tmp_path: Path):
    data = valid_team_dict()

    yaml_path = tmp_path / "team.yaml"
    yml_path = tmp_path / "team.yml"
    json_path = tmp_path / "team.json"
    yaml_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    yml_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    json_path.write_text(json.dumps(data), encoding="utf-8")

    from_yaml = load_team(yaml_path)
    from_yml = load_team(yml_path)
    from_json = load_team(json_path)
    from_dict = Team.model_validate(data)

    assert from_yaml == from_yml == from_json == from_dict
    assert from_yaml.schema_version == 1
    assert from_yaml.id == "delivery_team"
    assert from_yaml.roles[1].capabilities == frozenset({"web.search", "file.read"})
    assert from_yaml.roles[1].permissions == frozenset({ImpactClass.READ, ImpactClass.EXTERNAL})
    assert from_yaml.members[0].kind == MemberKind.PERSON
    assert from_yaml.members[0].agent is None
    assert from_yaml.members[1].kind == MemberKind.AGENT
    assert from_yaml.members[1].agent == "research_agent"
    assert from_yaml.relationships[0].type == RelationshipType.REPORTS_TO
    assert from_yaml.relationships[0].from_ == "researcher"

    round_trip = Team.model_validate(from_yaml.model_dump(mode="json"))
    assert round_trip == from_yaml


def test_structured_error_duplicate_role_id(tmp_path: Path):
    data = valid_team_dict()
    data["roles"].append({"id": "writer", "purpose": "Duplicate writer"})
    err = load_team_error(tmp_path, data)
    assert [(i.code, i.location) for i in err.issues] == [("duplicate_role_id", "roles[3].id")]


def test_structured_error_duplicate_member_id(tmp_path: Path):
    data = valid_team_dict()
    data["members"].append({"id": "m_lead", "kind": "person"})
    err = load_team_error(tmp_path, data)
    assert [(i.code, i.location) for i in err.issues] == [("duplicate_member_id", "members[3].id")]


def test_structured_error_unknown_role_and_member_references(tmp_path: Path):
    data = valid_team_dict()
    data["assignments"][0]["role"] = "ghost_role"
    data["assignments"][1]["member"] = "ghost_member"
    data["relationships"][0]["from"] = "ghost_from"
    data["relationships"][1]["to"] = "ghost_to"
    err = load_team_error(tmp_path, data)
    assert {(i.code, i.location) for i in err.issues} == {
        ("unknown_role", "assignments[0].role"),
        ("unknown_member", "assignments[1].member"),
        ("unknown_role", "relationships[0].from"),
        ("unknown_role", "relationships[1].to"),
    }


@pytest.mark.parametrize("rel_type", ["reports_to", "reviews", "delegates_to"])
def test_structured_error_self_relationship(tmp_path: Path, rel_type: str):
    data = valid_team_dict()
    data["relationships"] = [{"type": rel_type, "from": "lead", "to": "lead"}]
    err = load_team_error(tmp_path, data)
    assert [(i.code, i.location) for i in err.issues] == [("self_relationship", "relationships[0]")]


def test_structured_error_reports_to_cycle_and_allows_non_reports_to_mutual_links(tmp_path: Path):
    data = valid_team_dict()
    data["relationships"] = [
        {"type": "reports_to", "from": "researcher", "to": "writer"},
        {"type": "reports_to", "from": "writer", "to": "lead"},
        {"type": "reports_to", "from": "lead", "to": "researcher"},
    ]
    err = load_team_error(tmp_path, data)
    assert [(i.code, i.location) for i in err.issues] == [("reports_to_cycle", "relationships[1]")]

    # Mutual reviews and a diamond reports_to DAG are not cycles in reports_to.
    dag = valid_team_dict()
    dag["relationships"] = [
        {"type": "reports_to", "from": "researcher", "to": "writer"},
        {"type": "reports_to", "from": "researcher", "to": "lead"},
        {"type": "reports_to", "from": "writer", "to": "lead"},
        {"type": "reviews", "from": "lead", "to": "writer"},
        {"type": "reviews", "from": "writer", "to": "lead"},
    ]
    assert Team.model_validate(dag).id == "delivery_team"


def test_structured_error_invalid_capability_and_permission(tmp_path: Path):
    data = valid_team_dict()
    data["roles"][0]["capabilities"] = ["doc.review", "InvalidCap"]
    data["roles"][1]["permissions"] = ["read", "admin"]
    err = load_team_error(tmp_path, data)
    assert {(i.code, i.location) for i in err.issues} == {
        ("invalid_capability_id", "roles[0].capabilities[1]"),
        ("invalid_permission", "roles[1].permissions[1]"),
    }

    bare = valid_team_dict()
    bare["roles"][0]["capabilities"] = "doc.review"
    bare["roles"][1]["permissions"] = "read"
    bare_err = load_team_error(tmp_path, bare)
    assert {(i.code, i.location) for i in bare_err.issues} == {
        ("invalid_capability_id", "roles[0].capabilities"),
        ("invalid_permission", "roles[1].permissions"),
    }


def test_structured_error_member_agent_rules_and_unknown_bundle_agent(tmp_path: Path):
    missing_agent = valid_team_dict()
    del missing_agent["members"][1]["agent"]
    err_missing = load_team_error(tmp_path, missing_agent)
    assert [(i.code, i.location) for i in err_missing.issues] == [("missing_field", "members[1].agent")]

    person_with_agent = valid_team_dict()
    person_with_agent["members"][0]["agent"] = "research_agent"
    err_person = load_team_error(tmp_path, person_with_agent)
    assert [(i.code, i.location) for i in err_person.issues] == [("invalid_value", "members[0].agent")]

    err_unknown = load_team_error(tmp_path, valid_team_dict(), agents={"research_agent": {}})
    assert [(i.code, i.location) for i in err_unknown.issues] == [("unknown_agent", "members[2].agent")]

    err_empty_catalog = load_team_error(tmp_path, valid_team_dict(), agents={})
    assert {(i.code, i.location) for i in err_empty_catalog.issues} == {
        ("unknown_agent", "members[1].agent"),
        ("unknown_agent", "members[2].agent"),
    }


def test_model_validate_enforces_schema_version_strictness_and_semantics():
    bad_version = valid_team_dict()
    bad_version["schema_version"] = 2
    with pytest.raises(ValidationError):
        Team.model_validate(bad_version)

    extra_field = valid_team_dict()
    extra_field["unexpected"] = True
    with pytest.raises(ValidationError):
        Team.model_validate(extra_field)

    cycle = valid_team_dict()
    cycle["relationships"] = [
        {"type": "reports_to", "from": "writer", "to": "lead"},
        {"type": "reports_to", "from": "lead", "to": "writer"},
    ]
    with pytest.raises(ValidationError):
        Team.model_validate(cycle)


def test_parse_errors_reject_duplicate_keys_invalid_docs_and_bad_extensions(tmp_path: Path):
    dup_yaml = tmp_path / "dup.yaml"
    dup_yaml.write_text("schema_version: 1\nid: t1\nid: t2\n", encoding="utf-8")
    with pytest.raises(TeamDefinitionError) as yaml_err:
        load_team(dup_yaml)
    assert yaml_err.value.issues[0].code == "yaml_invalid"

    dup_json = tmp_path / "dup.json"
    dup_json.write_text('{"schema_version": 1, "id": "t1", "id": "t2"}', encoding="utf-8")
    with pytest.raises(TeamDefinitionError) as json_err:
        load_team(dup_json)
    assert json_err.value.issues[0].code == "json_invalid"

    not_mapping = tmp_path / "list.yaml"
    not_mapping.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(TeamDefinitionError) as root_err:
        load_team(not_mapping)
    assert [(i.code, i.location) for i in root_err.value.issues] == [("root_not_mapping", "<root>")]

    bad_ext = tmp_path / "team.txt"
    bad_ext.write_text("id: t1\n", encoding="utf-8")
    with pytest.raises(TeamDefinitionError) as ext_err:
        load_team(bad_ext)
    assert ext_err.value.issues[0].code == "unsupported_format"


def _definition_services(settings: Settings, bundles: dict[str, Path]) -> ApplicationServices:
    stub_system = SimpleNamespace(settings=settings, engine=None, backend=None)
    return ApplicationServices(stub_system, bundles=bundles)


def test_example_bundle_with_team_validates(settings: Settings):
    bundle = load_bundle(GENERAL_BUNDLE, settings)
    assert bundle.team is not None
    assert bundle.team.id == "general_team"
    assert any(member.kind == MemberKind.PERSON for member in bundle.team.members)
    assert all(
        member.agent in bundle.catalog.agents
        for member in bundle.team.members
        if member.kind == MemberKind.AGENT
    )

    services = _definition_services(settings, {"general": GENERAL_BUNDLE})
    result = services.definitions.validate_bundle("general")
    assert result["bundle"] == "general"
    assert result["valid"] is True
    assert sorted(result["workflows"]) == ["research_options", "review_checklist", "summarize_document"]


def test_bundle_without_team_is_unchanged(settings: Settings):
    bundle = load_bundle(SPEC_REVIEW_BUNDLE, settings)
    assert bundle.team is None
    definition = bundle.load_workflow("spec_review")
    assert [step.id for step in definition.steps] == ["extract_requirements", "assess_risks", "draft_test_plan"]

    services = _definition_services(settings, {"spec_review": SPEC_REVIEW_BUNDLE})
    assert services.definitions.validate_bundle("spec_review") == {
        "bundle": "spec_review",
        "valid": True,
        "workflows": ["spec_review"],
    }


def test_invalid_bundle_team_surfaces_through_bundle_validation_without_leaking_paths(
    tmp_path: Path, settings: Settings,
):
    bundle_copy = tmp_path / "spec_review"
    shutil.copytree(SPEC_REVIEW_BUNDLE, bundle_copy)
    team_data = copy.deepcopy(valid_team_dict())
    team_data["members"][1]["agent"] = "requirements_analyst"
    team_data["members"][2]["agent"] = "missing_bundle_agent"
    (bundle_copy / "team.yaml").write_text(yaml.safe_dump(team_data, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowDefinitionError) as caught:
        load_bundle(bundle_copy, settings)
    assert [(i.code, i.location) for i in caught.value.issues] == [
        ("unknown_agent", "team.yaml:members[2].agent"),
    ]

    services = _definition_services(settings, {"spec_review": bundle_copy})
    with pytest.raises(ServiceError) as svc_err:
        services.definitions.validate_bundle("spec_review")
    err = svc_err.value
    assert (err.code, err.status) == ("invalid_definition", 422)
    assert err.details["issues"][0]["code"] == "unknown_agent"
    assert err.details["issues"][0]["location"] == "team.yaml:members[2].agent"
    assert str(bundle_copy) not in json.dumps(err.to_payload())
