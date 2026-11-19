# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Plan execution uses task transitions and ordinary mock-backend runs."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError


INPUTS = {"topic": "A product launch", "platform": "LinkedIn", "audience": "Customers",
          "instructions": "Keep it concise", "objective": "Announce the launch"}
BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        execution_backend="mock", mock_mode=True, gemini_api_key="",
                        openai_api_key="", openai_enabled=False)
    instance = ApplicationServices.create(settings, bundles={"review": BUNDLE})
    yield instance
    instance.runs.close()


def make_plan(services, *, submitted=False):
    tasks = services.tasks
    tasks.create_group("launch", "Product launch", actor="user", authorized=True,
                       default_requires="submitted" if submitted else "accepted")
    tasks.create_task("launch", "plan", "Write plan", actor="user")
    tasks.create_task("launch", "backend", "Build service", actor="user", depends_on=["plan"])
    tasks.create_task("launch", "docs", "Write docs", actor="user", depends_on=["plan"])
    tasks.create_task("launch", "release", "Release", actor="user", depends_on=["backend", "docs"])
    return {"schema": "multiagent.plan/1", "group_id": "launch",
            "tasks": {task: {"workflow": "social_post", "inputs": INPUTS} for task in
                      ("plan", "backend", "docs", "release")}}


def by_id(result):
    return {task["id"]: task for task in result["tasks"]}


def complete_run(services, run_id):
    for _ in range(5):
        state = services.runs.get(run_id)["status"]
        if state == "completed":
            return
        if state == "waiting_human":
            services.runs.approve(run_id)
        else:
            assert services.system.worker.process_once()
    assert services.runs.get(run_id)["status"] == "completed"


def test_dependency_order_parallel_runs_and_plan_states(services):
    document = make_plan(services)
    plans = services.plans
    assert plans.status(document)["status"] == "waiting"
    first = plans.advance(document, actor="planner")
    assert first["status"] == "running"
    assert [task["id"] for task in first["tasks"] if task["run"]] == ["plan"]
    assert plans.advance(document, actor="planner") == first
    complete_run(services, by_id(first)["plan"]["run"]["id"])
    waiting = plans.advance(document, actor="planner")
    assert waiting["status"] == "waiting"
    assert by_id(waiting)["plan"]["state"] == "submitted"
    assert by_id(waiting)["backend"]["status"] == "blocked"
    services.tasks.transition("plan", "accept", actor="user")
    parallel = plans.advance(document, actor="planner")
    assert parallel["status"] == "running"
    assert all(by_id(parallel)[task]["run"] for task in ("backend", "docs"))
    assert by_id(parallel)["release"]["run"] is None
    for task in ("backend", "docs"):
        complete_run(services, by_id(parallel)[task]["run"]["id"])
    assert plans.advance(document, actor="planner")["status"] == "waiting"
    for task in ("backend", "docs"):
        services.tasks.transition(task, "accept", actor="user")
    release = plans.advance(document, actor="planner")
    assert by_id(release)["release"]["run"] is not None
    complete_run(services, by_id(release)["release"]["run"]["id"])
    assert plans.advance(document, actor="planner")["status"] == "completed"


def test_submitted_dependency_and_failed_run(services):
    document = make_plan(services, submitted=True)
    first = services.plans.advance(document, actor="planner")
    complete_run(services, by_id(first)["plan"]["run"]["id"])
    parallel = services.plans.advance(document, actor="planner")
    assert all(by_id(parallel)[task]["run"] for task in ("backend", "docs"))
    services.runs.cancel(by_id(parallel)["backend"]["run"]["id"])
    failed = services.plans.advance(document, actor="planner")
    assert failed["status"] == "failed"
    assert by_id(failed)["backend"]["state"] == "failed"
    assert by_id(failed)["release"]["status"] == "blocked"
    assert by_id(failed)["release"]["run"] is None
    assert any(blocker["task_id"] == "backend" for blocker in by_id(failed)["release"]["blockers"])


def test_run_creation_failure_fails_the_task_instead_of_leaving_it_running(services, monkeypatch):
    document = make_plan(services)

    def broken_create(request, *, bundle=None):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(services.runs, "create", broken_create)
    with pytest.raises(RuntimeError):
        services.plans.advance(document, actor="planner")
    result = services.plans.status(document)
    assert by_id(result)["plan"]["state"] == "failed"
    assert by_id(result)["plan"]["run"] is None
    assert result["status"] == "failed"
    assert by_id(result)["backend"]["status"] == "blocked"


def test_invalid_document_and_manual_task(services):
    document = make_plan(services)
    document["tasks"].pop("docs")
    assert by_id(services.plans.advance(document, actor="planner"))["docs"]["run"] is None
    with pytest.raises(ServiceError) as caught:
        services.plans.status({"schema": "wrong", "group_id": "launch", "tasks": {}})
    assert caught.value.code == "invalid_request"
    with pytest.raises(ServiceError) as caught:
        services.plans.status({"schema": "multiagent.plan/1", "group_id": "launch",
                               "tasks": {"missing": {"workflow": "social_post"}}})
    assert caught.value.code == "not_found"


def test_reopened_task_gets_a_new_run(services):
    document = make_plan(services)
    first = services.plans.advance(document, actor="planner")
    original = by_id(first)["plan"]["run"]["id"]
    complete_run(services, original)
    services.plans.advance(document, actor="another")
    task = services.tasks.get_task("plan")
    assert task["state"] == "submitted"
    services.tasks.transition("plan", "reopen", actor="user", cause="revise plan",
                              expected_revision=task["revision"])
    second = services.plans.advance(document, actor="planner")
    assert by_id(second)["plan"]["run"]["id"] != original


def test_registered_bundle_run(services):
    document = make_plan(services)
    document["tasks"] = {"plan": {"workflow": "spec_review", "bundle": "review",
                                    "inputs": {"spec_text": "Users can reset a password by email."}}}
    started = services.plans.advance(document, actor="planner")
    run = by_id(started)["plan"]["run"]
    assert services.runs.get(run["id"])["bundle"] == "review"
    assert services.runs._bundle_runtime("review").worker.process_once()
    assert services.plans.status(document)["status"] == "running"


def test_api_and_cli_parity(services, tmp_path, monkeypatch, capsys):
    document = make_plan(services)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        class ClientContext:
            def __enter__(self):
                return client

            def __exit__(self, *args):
                return False

        monkeypatch.setattr(cli, "api_client", ClientContext)
        expected = client.post("/api/v1/plans/status", json={"document": document}).json()
        cli.main(["plan", "status", str(path)])
        assert json.loads(capsys.readouterr().out) == expected
        yaml_path = tmp_path / "plan.yaml"
        yaml_path.write_text(yaml.safe_dump(document), encoding="utf-8")
        cli.main(["plan", "status", str(yaml_path)])
        assert json.loads(capsys.readouterr().out) == expected
        cli.main(["plan", "advance", str(path), "--actor", "planner"])
        actual = json.loads(capsys.readouterr().out)
        assert actual == client.post("/api/v1/plans/status", json={"document": document}).json()
        assert client.post("/api/v1/plans/advance", json={"document": document, "actor": "planner"}).json() == actual


def test_api_reports_service_errors(services):
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        invalid = client.post("/api/v1/plans/status", json={"document": {"schema": "wrong"}})
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "invalid_request"
        missing = client.post("/api/v1/plans/status", json={"document": {
            "schema": "multiagent.plan/1", "group_id": "absent", "tasks": {}}})
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "not_found"
