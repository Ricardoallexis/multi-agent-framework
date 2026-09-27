# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""HTTP API over the application services: new routes and structured errors (G4a-T04)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
GENERIC = {
    "project_name": "Synthetic review", "workflow_id": "spec_review",
    "inputs": {"spec_text": "Users can reset a password by email."},
}


@pytest.fixture
def services(tmp_path: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)


@pytest.fixture
def client(services):
    with TestClient(create_app(services=services)) as client:
        yield client


def process(services: ApplicationServices) -> None:
    assert services.runs._bundle_runtime("team").worker.process_once()


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "details"}
    assert detail["code"] == code
    return detail


def test_health_reports_dry_run(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["dry_run"] is True


def test_definition_routes_match_the_facade(client, services):
    assert client.get("/api/v1/workflows").json() == services.definitions.list_workflows()
    assert client.get("/api/v1/workflows", params={"bundle": "team"}).json() == [{"id": "spec_review", "bundle": "team"}]
    workflow = client.get("/api/v1/workflows/spec_review", params={"bundle": "team"}).json()
    assert workflow == services.definitions.get_workflow("spec_review", bundle="team")
    assert client.get("/api/v1/agents", params={"bundle": "team"}).json() == services.definitions.list_agents(bundle="team")
    assert client.get("/api/v1/bundles").json() == [{"name": "team"}]
    assert client.post("/api/v1/bundles/team/validate").json()["valid"] is True


def test_generic_bundle_run_over_http(client, services):
    response = client.post("/api/v1/runs", params={"bundle": "team"}, json=GENERIC)
    assert response.status_code == 202
    run = response.json()
    assert (run["status"], run["bundle"]) == ("queued", "team")

    assert_error(client.post(f"/api/v1/runs/{run['id']}/approve"), 409, "invalid_state")
    process(services)
    assert client.get(f"/api/v1/runs/{run['id']}").json()["waiting_step"] == "assess_risks"
    assert client.post(f"/api/v1/runs/{run['id']}/approve").json()["status"] == "queued"
    process(services)
    completed = client.post(f"/api/v1/runs/{run['id']}/approve").json()
    assert completed["status"] == "completed"
    assert len(client.get(f"/api/v1/runs/{run['id']}/artifacts").json()) == 3
    assert [item["id"] for item in client.get("/api/v1/runs").json()] == [run["id"]]


def test_social_post_request_still_creates_runs(client):
    response = client.post("/api/v1/runs", json={"project_name": "api-demo", "objective": "Create an educational post", "topic": "KNX"})
    assert response.status_code == 202
    assert response.json()["bundle"] is None


def test_errors_are_structured(client):
    assert_error(client.get("/api/v1/runs/missing"), 404, "not_found")
    assert_error(client.get("/api/v1/runs/missing/human-next"), 404, "not_found")
    assert_error(client.get("/api/v1/workflows/missing"), 404, "not_found")
    assert_error(client.get("/api/v1/workflows", params={"bundle": "other"}), 404, "not_found")
    detail = assert_error(client.get("/api/v1/workflows/bad.id"), 422, "invalid_definition")
    assert detail["details"]["issues"][0]["code"] == "invalid_identifier"
    assert_error(client.post("/api/v1/runs", params={"bundle": "other"}, json=GENERIC), 404, "not_found")


def test_invalid_bodies_use_the_same_error_shape(client):
    detail = assert_error(client.post("/api/v1/runs", json={"workflow_id": "x"}), 422, "invalid_request")
    assert detail["details"]["errors"]
    social = {"project_name": "api-demo", "objective": "Create an educational post", "topic": "KNX"}
    assert_error(client.post("/api/v1/runs", params={"bundle": "team"}, json=social), 422, "invalid_request")


def test_human_step_errors_keep_their_status(client, services):
    run = client.post("/api/v1/runs", params={"bundle": "team"},
                      json={**GENERIC, "execution_mode": "human_guided"}).json()
    assert_error(client.get(f"/api/v1/runs/{run['id']}/human-next"), 409, "human_step_unavailable")
    process(services)
    assert client.get(f"/api/v1/runs/{run['id']}/human-next").json()["step_id"] == "extract_requirements"
    response = client.post(f"/api/v1/runs/{run['id']}/human-submit",
                           json={"raw_response": "not json", "provider": "synthetic_human"})
    assert_error(response, 422, "human_submission_invalid")
