# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Per-run event sequence numbers and incremental reads (UI0-T13)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
GENERIC = {
    "project_name": "Event sequence", "workflow_id": "spec_review",
    "inputs": {"spec_text": "Users can reset a password by email."},
}
RUN_VIEW = Path(__file__).parents[1] / "multiagent" / "ui" / "static" / "js" / "run-view.js"


@pytest.fixture
def services(tmp_path: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    yield services
    services.runs.close()


@pytest.fixture
def client(services):
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield client


def process(services: ApplicationServices) -> None:
    assert services.runs._bundle_runtime("team").worker.process_once()


def waiting_run(client, services) -> dict:
    response = client.post("/api/v1/runs", params={"bundle": "team"}, json=GENERIC)
    assert response.status_code == 202
    run = response.json()
    process(services)
    return client.get(f"/api/v1/runs/{run['id']}").json()


def seqs(events: list[dict]) -> list[int]:
    return [event["seq"] for event in events]


def test_events_are_numbered_per_run_without_gaps(client, services):
    first = waiting_run(client, services)
    second = waiting_run(client, services)
    for run in (first, second):
        assert run["events"], "a processed run has events"
        assert seqs(run["events"]) == list(range(1, len(run["events"]) + 1))


def test_sequence_grows_across_actions_and_stays_stable(client, services):
    run = waiting_run(client, services)
    before = run["events"]
    assert client.post(f"/api/v1/runs/{run['id']}/approve").status_code == 200
    process(services)
    after = client.get(f"/api/v1/runs/{run['id']}").json()["events"]
    assert seqs(after) == list(range(1, len(after) + 1))
    assert len(after) > len(before)
    # Already-seen events keep their numbers and content.
    assert after[:len(before)] == before


def test_events_after_returns_only_newer_events(client, services):
    run = waiting_run(client, services)
    events = run["events"]
    last = events[-1]["seq"]
    assert client.get(f"/api/v1/runs/{run['id']}/events").json() == events
    assert client.get(f"/api/v1/runs/{run['id']}/events", params={"after": 0}).json() == events
    assert client.get(f"/api/v1/runs/{run['id']}/events", params={"after": 1}).json() == events[1:]
    assert client.get(f"/api/v1/runs/{run['id']}/events", params={"after": last}).json() == []
    assert client.get(f"/api/v1/runs/{run['id']}/events", params={"after": last + 10}).json() == []

    client.post(f"/api/v1/runs/{run['id']}/approve")
    process(services)
    newer = client.get(f"/api/v1/runs/{run['id']}/events", params={"after": last}).json()
    assert newer and newer[0]["seq"] == last + 1
    assert seqs(newer) == list(range(last + 1, last + 1 + len(newer)))


def test_events_facade_matches_http(client, services):
    run = waiting_run(client, services)
    for after in (0, 1, 2):
        assert services.runs.events(run["id"], after=after) == client.get(
            f"/api/v1/runs/{run['id']}/events", params={"after": after}).json()


@pytest.mark.parametrize("after", [-1, 1.5, "2", True])
def test_facade_rejects_invalid_after(client, services, after):
    run = waiting_run(client, services)
    with pytest.raises(ServiceError) as error:
        services.runs.events(run["id"], after=after)
    assert (error.value.code, error.value.status) == ("invalid_request", 422)


def test_http_errors_are_structured(client, services):
    run = waiting_run(client, services)
    for value in ("-1", "abc"):
        response = client.get(f"/api/v1/runs/{run['id']}/events", params={"after": value})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_request"
    missing = client.get("/api/v1/runs/does-not-exist/events")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "not_found"


class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def test_cli_events_matches_http(monkeypatch, capsys, client, services):
    run = waiting_run(client, services)
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    cli.main(["events", run["id"], "--after", "1"])
    output = capsys.readouterr().out
    assert json.loads(output) == client.get(f"/api/v1/runs/{run['id']}/events", params={"after": 1}).json()


def test_run_view_checks_the_sequence_and_reloads():
    # The UI has no JavaScript test runner; check that the view keeps the contract it documents.
    source = RUN_VIEW.read_text(encoding="utf-8")
    assert "export function checkEventSequence(events)" in source
    for problem in ("has no sequence number", "is repeated or out of order", "are missing"):
        assert problem in source
    assert "if (!acceptSequence(run)) return;" in source
    assert "reloading the run from the API" in source
