# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Parity checks between CLI commands and the HTTP API (G4a-T10)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
GENERIC_RUN = [
    "run",
    "--project",
    "CLI parity",
    "--workflow",
    "spec_review",
    "--bundle",
    "team",
    "--input",
    "spec_text=Users can reset a password by email.",
]


class _ClientContext:
    """Keep the shared TestClient open across the CLI's per-request context."""

    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


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


def invoke_cli(monkeypatch, capsys, client: TestClient, args: list[str]) -> str:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    try:
        cli.main(args)
    except SystemExit as exc:
        assert exc.code == 1
    captured = capsys.readouterr()
    return captured.out if captured.out else captured.err


def process_bundle_run(services: ApplicationServices) -> None:
    runtime = services.runs._bundle_runtime("team")
    assert runtime is not None and runtime.worker.process_once()


def test_run_report_cli_matches_http(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "local")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        run = client.post("/api/v1/runs", params={"bundle": "team"}, json={
            "project_name": "Report", "workflow_id": "spec_review",
            "inputs": {"spec_text": "Synthetic private fixture"},
        })
        assert run.status_code == 202
        run_id = run.json()["id"]
        expected = client.get(f"/api/v1/runs/{run_id}/report")
        assert expected.status_code == 200
        actual = invoke_cli(monkeypatch, capsys, client, ["run-report", run_id])
        assert json.loads(actual) == expected.json()
        markdown = invoke_cli(monkeypatch, capsys, client, ["run-report", run_id, "--format", "markdown"])
        assert markdown.strip() == client.get(f"/api/v1/runs/{run_id}/report",
                                              params={"format": "markdown"}).text.strip()
    services.runs.close()


def test_cli_workflow_listing_matches_http(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "local")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        expected = client.get("/api/v1/workflows", params={"bundle": "team"})
        assert expected.status_code == 200

        actual = invoke_cli(
            monkeypatch,
            capsys,
            client,
            ["workflows", "--bundle", "team"],
        )

    assert json.loads(actual) == expected.json()
    services.runs.close()


def test_cli_mock_run_matches_http(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "local")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        assert client.get("/api/v1/health").json()["dry_run"] is True

        cli_run = json.loads(invoke_cli(monkeypatch, capsys, client, GENERIC_RUN))
        http_response = client.post(
            "/api/v1/runs",
            params={"bundle": "team"},
            json={
                "project_name": "CLI parity",
                "workflow_id": "spec_review",
                "inputs": {"spec_text": "Users can reset a password by email."},
            },
        )
        assert http_response.status_code == 202
        http_run = http_response.json()

        assert (cli_run["status"], cli_run["workflow_id"], cli_run["bundle"]) == (
            http_run["status"],
            http_run["workflow_id"],
            http_run["bundle"],
        ) == ("queued", "spec_review", "team")

        process_bundle_run(services)
        process_bundle_run(services)
        cli_state = services.runs.get(cli_run["id"])
        http_state = client.get(f"/api/v1/runs/{http_run['id']}").json()
        assert (cli_state["status"], cli_state["waiting_step"]) == (
            http_state["status"],
            http_state["waiting_step"],
        ) == ("waiting_human", "assess_risks")

    services.runs.close()


def test_cli_validation_error_matches_http(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "local")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.get("/api/v1/workflows/bad.id")
        assert response.status_code == 422
        detail = response.json()["detail"]

        cli_error = invoke_cli(monkeypatch, capsys, client, ["workflows", "bad.id"])

    assert f"HTTP {response.status_code}" in cli_error
    assert detail["code"] in cli_error
    assert detail["message"] in cli_error
    assert detail["details"]["issues"][0]["code"] in cli_error
    services.runs.close()


def test_cli_assignment_uses_http_and_anonymous_accept_stays_manual(monkeypatch, capsys, tmp_path):
    services = make_services(tmp_path / "assignment")
    services.tasks.create_group("ORQ", "Authorized", actor="test", authorized=True)
    task = services.tasks.create_task("ORQ", "ORQ-CLI", "CLI assignment", actor="test")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        preview = json.loads(invoke_cli(
            monkeypatch, capsys, client,
            ["task", "assignment", "preview", task["id"], "--profile-id", "unknown-profile"],
        ))
        direct = client.post(f"/api/v1/tasks/{task['id']}/assignment/preview",
                             json={"profile_id": "unknown-profile"})
        assert direct.status_code == 200
        assert preview == direct.json()

        accepted = json.loads(invoke_cli(
            monkeypatch, capsys, client,
            ["task", "assignment", "accept", task["id"], preview["preview_token"], "--acknowledge"],
        ))
        assert accepted["decision"]["status"] == "manual"
        assert "operator_context_unverified" in accepted["decision"]["reason_codes"]
        assert services.tasks.get_task(task["id"])["state"] == "open"
    services.runs.close()


def test_every_cli_help_text_renders():
    """Python 3.14 refuses a help text with a bare % when the parser is built; render every help everywhere."""
    import argparse

    def walk(parser):
        assert parser.format_help()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    walk(sub)

    walk(cli.build_parser())


def test_bundle_group_has_its_four_commands():
    parser = cli.build_parser()
    for argv, command in ((["bundle", "install", "demo", "folder"], "install"), (["bundle", "list"], "list"),
                          (["bundle", "remove", "demo"], "remove"),
                          (["bundle", "reinstall", "demo", "folder"], "reinstall")):
        args = parser.parse_args(argv)
        assert args.func is cli.cmd_bundle and args.bundle_command == command
        if command != "list":
            assert args.actor == "user:local"
    assert parser.parse_args(["bundle-scan", "demo"]).func is cli.cmd_bundle_scan
    assert parser.parse_args(["bundle-validate", "demo"]).func is cli.cmd_bundle_validate
