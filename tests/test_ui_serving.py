# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Stage 0 UI serving and Mock-mode acceptance checks (UI0-T09)."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.services import ApplicationServices

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
GENERIC_RUN = {
    "project_name": "Stage 0 acceptance",
    "workflow_id": "spec_review",
    "inputs": {"spec_text": "Users can reset a password by email."},
}


@pytest.fixture
def services(tmp_path: Path) -> ApplicationServices:
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        max_llm_calls=5,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    return ApplicationServices.create(
        settings,
        bundles={"team": BUNDLE},
        dry_run=True,
    )


@pytest.fixture
def client(services: ApplicationServices):
    with TestClient(create_app(services=services), base_url="http://localhost") as test_client:
        yield test_client


def process_bundle_run(services: ApplicationServices) -> None:
    runtime = services.runs._bundle_runtime("team", create=False)
    assert runtime is not None and runtime.worker is not None
    assert runtime.worker.process_once()


def assert_api_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "details"}
    assert detail["code"] == code
    return detail


def create_bundle_run(client: TestClient, **overrides) -> dict:
    response = client.post(
        "/api/v1/runs",
        params={"bundle": "team"},
        json={**GENERIC_RUN, **overrides},
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_ui_redirect_and_static_modules_are_served(client: TestClient):
    redirect = client.get("/ui", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/ui/"

    page = client.get("/ui/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert 'id="catalog"' in page.text
    assert 'id="run-view"' in page.text
    assert 'id="human-actions"' in page.text

    for path, media_type in [
        ("/ui/static/app.css", "text/css"),
        ("/ui/static/js/api.js", ("text/javascript", "application/javascript")),
        ("/ui/static/js/create-run.js", ("text/javascript", "application/javascript")),
        ("/ui/static/js/run-view.js", ("text/javascript", "application/javascript")),
        ("/ui/static/js/human-actions.js", ("text/javascript", "application/javascript")),
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
        content_type = response.headers["content-type"]
        expected_types = (media_type,) if isinstance(media_type, str) else media_type
        assert any(content_type.startswith(expected) for expected in expected_types), path


def test_ui_command_defaults_to_mock_and_registers_trusted_bundles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
):
    import uvicorn

    from multiagent import api

    monkeypatch.delenv("MOCK_MODE", raising=False)
    real_settings = Settings
    observed = {}
    fake_app = object()

    def settings_factory() -> Settings:
        return real_settings(
            _env_file=None,
            local_dir=tmp_path / "local",
            worker_enabled=False,
        )

    def build_system(settings: Settings):
        observed["mock_mode"] = settings.mock_mode
        return object()

    def create_app(system, *, bundles):
        observed["bundles"] = bundles
        return fake_app

    monkeypatch.setattr(cli, "Settings", settings_factory)
    monkeypatch.setattr(cli, "build_system", build_system)
    monkeypatch.setattr(api, "create_app", create_app)
    monkeypatch.setattr(cli, "_check_port_free", lambda host, port: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: observed.update(app=app, server=kwargs))

    args = cli.build_parser().parse_args([
        "ui",
        "--no-browser",
        "--port",
        "8123",
        "--bundle",
        f"team={BUNDLE}",
    ])
    cli.cmd_ui(args)

    assert observed["mock_mode"] is True
    assert observed["bundles"] == {"team": BUNDLE}
    assert observed["app"] is fake_app
    assert observed["server"] == {"host": "127.0.0.1", "port": 8123, "log_level": "warning"}
    output = capsys.readouterr().out
    assert "Mode: Mock (synthetic results, no API keys)" in output
    assert "Bundles: team" in output


def test_ui_command_reports_an_occupied_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        with pytest.raises(RuntimeError, match="choose another port with --port"):
            cli._check_port_free("127.0.0.1", port)


def test_mock_run_supports_review_actions_status_events_and_artifacts(
    client: TestClient, services: ApplicationServices, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        services.system.adapters["ollama"],
        "health",
        lambda: {"provider": "ollama", "available": False},
    )
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["dry_run"] is True

    run_response = client.post("/api/v1/runs", params={"bundle": "team"}, json=GENERIC_RUN)
    assert run_response.status_code == 202
    run = run_response.json()
    assert (run["status"], run["bundle"]) == ("queued", "team")
    assert_api_error(client.post(f"/api/v1/runs/{run['id']}/approve"), 409, "invalid_state")

    process_bundle_run(services)
    waiting = client.get(f"/api/v1/runs/{run['id']}").json()
    assert (waiting["status"], waiting["waiting_reason"]) == ("waiting_human", "review")
    assert waiting["events"]
    assert waiting["artifacts"]
    assert client.get(f"/api/v1/runs/{run['id']}/artifacts").json() == waiting["artifacts"]

    changed = client.post(
        f"/api/v1/runs/{run['id']}/changes",
        json={"feedback": "Clarify the main risk."},
    )
    assert changed.status_code == 200 and changed.json()["status"] == "queued"
    process_bundle_run(services)
    assert client.get(f"/api/v1/runs/{run['id']}").json()["status"] == "waiting_human"

    regenerated = client.post(f"/api/v1/runs/{run['id']}/regenerate")
    assert regenerated.status_code == 200 and regenerated.json()["status"] == "queued"
    process_bundle_run(services)
    assert client.get(f"/api/v1/runs/{run['id']}").json()["status"] == "waiting_human"

    approved = client.post(f"/api/v1/runs/{run['id']}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "queued"
    process_bundle_run(services)
    next_review = client.get(f"/api/v1/runs/{run['id']}").json()
    assert (next_review["status"], next_review["waiting_reason"]) == ("waiting_human", "review")
    completed = client.post(f"/api/v1/runs/{run['id']}/approve")
    assert completed.status_code == 200 and completed.json()["status"] == "completed"
    final_run = completed.json()
    assert (final_run["llm_calls"], final_run["max_llm_calls"]) == (5, 5)
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    assert {
        (artifact["step_id"], artifact["attempt"])
        for artifact in artifacts
    } == {
        ("extract_requirements", 1),
        ("assess_risks", 1),
        ("assess_risks", 2),
        ("assess_risks", 3),
        ("draft_test_plan", 1),
    }


def test_mock_run_supports_rejection_and_cancellation(
    client: TestClient, services: ApplicationServices
):
    rejected = create_bundle_run(client)
    process_bundle_run(services)
    response = client.post(
        f"/api/v1/runs/{rejected['id']}/reject",
        json={"feedback": "Out of scope."},
    )
    assert response.status_code == 200 and response.json()["status"] == "rejected"

    cancelled = create_bundle_run(client)
    response = client.post(f"/api/v1/runs/{cancelled['id']}/cancel")
    assert response.status_code == 200 and response.json()["status"] == "cancelled"


def test_human_bridge_validates_responses_and_completes_the_run(
    client: TestClient, services: ApplicationServices
):
    samples = load_bundle(BUNDLE, services.settings).sample_outputs
    run = create_bundle_run(client, execution_mode="human_guided")
    assert_api_error(client.get(f"/api/v1/runs/{run['id']}/human-next"), 409, "human_step_unavailable")

    invalid_response_checked = False
    for _ in range(3):
        process_bundle_run(services)
        pending = client.get(f"/api/v1/runs/{run['id']}/human-next")
        assert pending.status_code == 200
        step = pending.json()
        assert step["prompt"] and step["expected_contract"] in samples

        if not invalid_response_checked:
            invalid = client.post(
                f"/api/v1/runs/{run['id']}/human-submit",
                json={"raw_response": "not JSON", "provider": "synthetic_human"},
            )
            assert_api_error(invalid, 422, "human_submission_invalid")
            assert client.get(f"/api/v1/runs/{run['id']}").json()["status"] == "waiting_human"
            invalid_response_checked = True

        submitted = client.post(
            f"/api/v1/runs/{run['id']}/human-submit",
            json={
                "raw_response": json.dumps(samples[step["expected_contract"]]),
                "provider": "synthetic_human",
            },
        )
        assert submitted.status_code == 200
        state = submitted.json()
        if state.get("waiting_reason") == "review":
            state = client.post(f"/api/v1/runs/{run['id']}/approve").json()

    assert invalid_response_checked
    final = client.get(f"/api/v1/runs/{run['id']}").json()
    assert final["status"] == "completed"
    assert len(client.get(f"/api/v1/runs/{run['id']}/artifacts").json()) == 3


def test_stage0_api_errors_remain_structured(client: TestClient):
    assert_api_error(client.get("/api/v1/runs/missing"), 404, "not_found")
    assert_api_error(client.get("/api/v1/workflows", params={"bundle": "missing"}), 404, "not_found")
    invalid = assert_api_error(client.post("/api/v1/runs", json={"workflow_id": "spec_review"}), 422, "invalid_request")
    assert invalid["details"]["errors"]
