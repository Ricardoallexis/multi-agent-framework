# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Parity checks between the Python application facade and HTTP API (G4a-T07)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.api import create_app

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
GENERIC = {
    "project_name": "Synthetic review",
    "workflow_id": "spec_review",
    "inputs": {"spec_text": "Users can reset a password by email."},
}
RUN_FIELDS = (
    "status",
    "workflow_id",
    "bundle",
    "waiting_step",
    "waiting_reason",
    "current_step",
    "llm_calls",
)


def make_services(root: Path) -> ApplicationServices:
    settings = Settings(
        _env_file=None,
        local_dir=root,
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    return ApplicationServices.create(settings, bundles={"team": BUNDLE})


def process_bundle_run(services: ApplicationServices) -> None:
    runtime = services.runs._bundle_runtime("team")
    assert runtime is not None and runtime.worker.process_once()


def run_snapshot(run: dict) -> dict:
    return {field: run.get(field) for field in RUN_FIELDS}


def test_workflow_lists_match_between_facade_and_http(tmp_path):
    services = make_services(tmp_path / "services")
    with TestClient(create_app(services=services)) as client:
        assert client.get("/api/v1/workflows").json() == services.definitions.list_workflows()
        assert client.get("/api/v1/workflows", params={"bundle": "team"}).json() == (
            services.definitions.list_workflows(bundle="team")
        )


def test_mock_run_creation_and_approval_match_between_facade_and_http(tmp_path):
    facade_services = make_services(tmp_path / "facade")
    http_services = make_services(tmp_path / "http")
    assert facade_services.system.engine.router.dry_run
    assert http_services.system.engine.router.dry_run

    facade_run = facade_services.runs.create(RunRequest(**GENERIC), bundle="team")
    with TestClient(create_app(services=http_services)) as client:
        response = client.post("/api/v1/runs", params={"bundle": "team"}, json=GENERIC)
        assert response.status_code == 202
        http_run = response.json()
        assert run_snapshot(http_run) == run_snapshot(facade_run)

        process_bundle_run(facade_services)
        process_bundle_run(http_services)
        facade_waiting = facade_services.runs.get(facade_run["id"])
        http_waiting = client.get(f"/api/v1/runs/{http_run['id']}").json()
        assert run_snapshot(http_waiting) == run_snapshot(facade_waiting)
        assert facade_waiting["status"] == "waiting_human"

        facade_queued = facade_services.runs.approve(facade_run["id"])
        http_queued = client.post(f"/api/v1/runs/{http_run['id']}/approve").json()
        assert run_snapshot(http_queued) == run_snapshot(facade_queued)

        process_bundle_run(facade_services)
        process_bundle_run(http_services)
        facade_waiting = facade_services.runs.get(facade_run["id"])
        http_waiting = client.get(f"/api/v1/runs/{http_run['id']}").json()
        assert run_snapshot(http_waiting) == run_snapshot(facade_waiting)
        assert facade_waiting["status"] == "waiting_human"

        facade_completed = facade_services.runs.approve(facade_run["id"])
        http_completed = client.post(f"/api/v1/runs/{http_run['id']}/approve").json()
        assert run_snapshot(http_completed) == run_snapshot(facade_completed)
        assert http_completed["status"] == "completed"


def test_invalid_workflow_error_matches_between_facade_and_http(tmp_path):
    services = make_services(tmp_path / "services")
    with TestClient(create_app(services=services)) as client:
        with pytest.raises(ServiceError) as error:
            services.definitions.get_workflow("bad.id")

        response = client.get("/api/v1/workflows/bad.id")
        assert response.status_code == error.value.status == 422
        assert response.json()["detail"] == error.value.to_payload()
