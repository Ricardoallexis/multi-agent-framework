# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Definition services of the facade: workflows, agents and bundles (G4a-T02)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError

SPEC_REVIEW = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture
def bundle_copy(tmp_path: Path) -> Path:
    target = tmp_path / "bundles" / "spec_review"
    shutil.copytree(SPEC_REVIEW, target)
    return target


@pytest.fixture
def services(tmp_path: Path, bundle_copy: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, bundles={"spec_review": bundle_copy}, dry_run=True)


def service_error(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def test_lists_builtin_and_bundle_workflows(services):
    builtin = services.definitions.list_workflows()
    assert {"id": "social_post", "bundle": None} in builtin
    assert [item["id"] for item in builtin] == sorted(item["id"] for item in builtin)
    assert services.definitions.list_workflows(bundle="spec_review") == [{"id": "spec_review", "bundle": "spec_review"}]


def test_get_workflow_returns_validated_json_ready_steps(services):
    workflow = services.definitions.get_workflow("spec_review", bundle="spec_review")
    assert workflow["id"] == "spec_review" and workflow["bundle"] == "spec_review"
    assert [step["id"] for step in workflow["steps"]] == ["extract_requirements", "assess_risks", "draft_test_plan"]
    assert workflow["steps"][0]["agent"] == "requirements_analyst"
    assert json.loads(json.dumps(workflow)) == workflow

    builtin = services.definitions.get_workflow("social_post")
    assert builtin["bundle"] is None and builtin["steps"]


def test_unknown_workflow_and_bundle_are_not_found(services):
    assert service_error(services.definitions.get_workflow, "missing").code == "not_found"
    assert service_error(services.definitions.get_workflow, "spec_review").code == "not_found"
    assert service_error(services.definitions.list_workflows, bundle="other").code == "not_found"
    assert service_error(services.definitions.list_agents, bundle="other").code == "not_found"
    assert service_error(services.definitions.validate_bundle, "other").code == "not_found"


def test_unsafe_workflow_id_is_an_invalid_definition(services):
    error = service_error(services.definitions.get_workflow, "../escape")
    assert (error.code, error.status) == ("invalid_definition", 422)
    assert error.details["issues"][0]["code"] == "invalid_identifier"


def test_lists_agents_of_builtin_catalog_and_bundle(services):
    bundle_agents = services.definitions.list_agents(bundle="spec_review")
    assert [agent["id"] for agent in bundle_agents] == ["requirements_analyst", "risk_reviewer", "test_planner"]
    builtin_ids = {agent["id"] for agent in services.definitions.list_agents()}
    assert builtin_ids and not builtin_ids & {agent["id"] for agent in bundle_agents}


def test_bundles_are_listed_by_name_without_paths(services, bundle_copy):
    bundles = services.definitions.list_bundles()
    assert bundles == [{"name": "spec_review"}]
    assert str(bundle_copy) not in json.dumps(bundles)


def test_valid_bundle(services):
    assert services.definitions.validate_bundle("spec_review") == {
        "bundle": "spec_review", "valid": True, "workflows": ["spec_review"],
    }


def test_invalid_bundle_reports_issues_per_workflow_without_paths(services, bundle_copy):
    path = bundle_copy / "workflows" / "spec_review.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["steps"][0]["agent"] = "missing_agent"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    error = service_error(services.definitions.validate_bundle, "spec_review")
    assert (error.code, error.status) == ("invalid_definition", 422)
    assert error.details["bundle"] == "spec_review"
    [problem] = error.details["workflows"]
    assert problem["workflow_id"] == "spec_review"
    assert problem["issues"]
    assert str(bundle_copy) not in json.dumps(error.to_payload())
