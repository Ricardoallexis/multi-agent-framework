# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Named planners share validation and return documents consumed by plan execution."""
from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.observability.usage import read_usage
from multiagent.planning import register_planner
from multiagent.services import ApplicationServices, ServiceError


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        execution_backend="mock", mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False)
    instance = ApplicationServices.create(settings)
    yield instance
    instance.runs.close()


def manual():
    return {"group_id": "launch", "goal": "Launch a product", "tasks": [
        {"id": "technical", "title": "Technical review", "depends_on": [], "workflow": "social_post",
         "inputs": {"topic": "Launch"}},
        {"id": "commercial", "title": "Commercial review", "depends_on": ["technical"],
         "workflow": "social_post"}]}


def deterministic():
    return {"group_id": "launch", "goal": "Launch a product", "template": {
        "prefix": "launch", "stages": [
            {"id": "technical", "title": "Technical review", "workflow": "social_post"},
            {"id": "commercial", "title": "Commercial review", "workflow": "social_post"}]}}


@pytest.mark.parametrize("planner,planning_request", [
    ("manual", manual()), ("deterministic", deterministic()),
    ("model", {"group_id": "launch", "goal": "Launch a product"}),
])
def test_each_planner_returns_same_shape_and_executable_document(services, planner, planning_request):
    proposal = services.planning.propose(planner, planning_request)
    assert proposal["schema"] == "multiagent.plan_proposal/1"
    assert proposal["planner"] == planner and proposal["group_id"] == "launch"
    assert proposal["goal"] == planning_request["goal"]
    assert proposal["plan"] == {"schema": "multiagent.plan/1", "group_id": "launch",
                                "tasks": {task["id"]: {key: task[key] for key in ("workflow", "bundle", "inputs")
                                                      if key in task} for task in proposal["tasks"]}}
    services.tasks.create_group("launch", "Product launch", actor="user", authorized=True)
    for task in proposal["tasks"]:
        services.tasks.create_task("launch", task["id"], task["title"], actor="user",
                                   depends_on=task["depends_on"])
    assert services.plans.status(proposal["plan"])["group_id"] == "launch"


def test_deterministic_ids_and_dependencies_are_reproducible(services):
    first = services.planning.propose("deterministic", deterministic())
    assert services.planning.propose("deterministic", deterministic()) == first
    assert [task["id"] for task in first["tasks"]] == ["launch-technical", "launch-commercial"]
    assert first["tasks"][1]["depends_on"] == ["launch-technical"]


def test_model_mock_is_reproducible_and_invalid_output_is_rejected(services):
    request = {"group_id": "launch", "goal": "Launch a product"}
    first = services.planning.propose("model", request)
    assert services.planning.propose("model", request) == first
    fake = services.system.engine.router.adapters["fake"]
    fake.sample_outputs["PlanningOutput"] = {"tasks": [
        {"id": "bad", "title": "Bad workflow", "depends_on": [], "workflow": "missing"}]}
    with pytest.raises(ServiceError) as caught:
        services.planning.propose("model", request)
    assert caught.value.code == "not_found"


def test_model_requires_confirmation_for_cloud_catalog_entry(services):
    services.settings.gui_new_session_model = "cloud_fast"
    with pytest.raises(ServiceError, match="confirm_cloud"):
        services.planning.propose("model", {"group_id": "launch", "goal": "Launch a product"})


def test_model_records_usage_and_honors_egress_policy(tmp_path):
    class ObservedFake(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
            self.prompts.append(prompt)
            return super().generate_structured(spec=spec, prompt=prompt, output_schema=output_schema,
                                               requires_web=requires_web, effort=effort)

    adapter = ObservedFake()
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gui_new_session_model="cloud_fast", egress_default="redact",
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True, adapter_overrides={"fake": adapter})
    try:
        services.planning.propose("model", {"group_id": "launch", "goal": "Contact ana@example.com"},
                                  confirm_cloud=True)
        assert adapter.prompts and "ana@example.com" not in adapter.prompts[0]
        assert "[REDACTED:email:1]" in adapter.prompts[0]
        assert read_usage(services.system.engine.store, "planning:propose")
    finally:
        services.runs.close()


@pytest.mark.parametrize("change,match", [
    ({"tasks": [{"id": "a", "title": "A", "depends_on": ["b"], "workflow": "social_post"},
                {"id": "b", "title": "B", "depends_on": ["a"], "workflow": "social_post"}]}, "cycle"),
    ({"tasks": [{"id": "a", "title": "A", "depends_on": ["missing"], "workflow": "social_post"}]}, "Unknown task"),
    ({"tasks": [{"id": "a", "title": "A", "depends_on": [], "workflow": "missing"}]}, "missing"),
])
def test_invalid_proposals_fail_wholly(services, change, match):
    with pytest.raises(ServiceError, match=match):
        services.planning.propose("manual", {**manual(), **change})


def test_unknown_and_repeated_planner_names(services):
    with pytest.raises(ServiceError) as caught:
        services.planning.propose("absent", manual())
    assert caught.value.code == "not_found"
    with pytest.raises(ServiceError) as caught:
        register_planner("manual", lambda *_: {}, description="Duplicate")
    assert caught.value.code == "invalid_request"


def test_trusted_planner_registration(services):
    register_planner("test_custom", lambda _services, request, _confirm: {"tasks": request["tasks"]},
                     description="Test planner")
    assert services.planning.propose("test_custom", manual())["planner"] == "test_custom"
    assert any(item["name"] == "test_custom" for item in services.planning.planners())


def test_api_cli_parity(services, monkeypatch, capsys, tmp_path):
    request = deterministic()
    path = tmp_path / "request.yaml"
    path.write_text(yaml.safe_dump(request), encoding="utf-8")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        class ClientContext:
            def __enter__(self):
                return client

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(cli, "api_client", ClientContext)
        expected_list = client.get("/api/v1/planners").json()
        cli.main(["plan", "planners"])
        assert json.loads(capsys.readouterr().out) == expected_list
        expected = client.post("/api/v1/plans/propose", json={"planner": "deterministic",
                                                               "request": request}).json()
        cli.main(["plan", "propose", "--planner", "deterministic", str(path)])
        assert json.loads(capsys.readouterr().out) == expected
