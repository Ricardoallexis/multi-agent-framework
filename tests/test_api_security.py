# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Local API host/origin guards, without model calls or runtime persistence."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from multiagent.api import create_app
from multiagent.config import Settings


REQUEST = {"workflow_id": "example", "project_name": "Security test", "inputs": {}}


def make_client(tmp_path, *, hosts=None, base_url="http://localhost"):
    options = {} if hosts is None else {"api_allowed_hosts": hosts}
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, **options)
    runs = Mock()
    runs.create.return_value = {"id": "example", "status": "queued", "bundle": None}
    definitions = Mock()
    definitions.list_workflows.return_value = [{"id": "example", "bundle": None}]
    services = SimpleNamespace(
        system=SimpleNamespace(settings=settings, worker=Mock()),
        runs=runs, definitions=definitions,
    )
    return TestClient(create_app(services=services), base_url=base_url), runs


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["detail"] == {
        "code": code,
        "message": "Host is not allowed" if code == "invalid_host" else "Request origin is not allowed",
        "details": {},
    }


@pytest.mark.parametrize("host", ["attacker.example", "localhost.attacker.example", "testserver", "localhost@attacker.example", "localhost:bad"])
def test_untrusted_host_cannot_read_or_mutate(tmp_path, host):
    client, runs = make_client(tmp_path)
    with client:
        for method, path in [("GET", "/api/v1/workflows"), ("POST", "/api/v1/runs")]:
            assert_error(client.request(method, path, headers={"host": host}, json=REQUEST), 400, "invalid_host")
    runs.create.assert_not_called()


@pytest.mark.parametrize("origin", ["https://attacker.example", "http://localhost:8001", "https://localhost", "http://127.0.0.1", "null", "", "http://localhost/", "http://user@localhost", "http://localhost#", "http://localhost:"])
def test_wrong_or_malformed_origin_rejected_before_service(tmp_path, origin):
    client, runs = make_client(tmp_path)
    with client:
        assert_error(client.post("/api/v1/runs", json=REQUEST, headers={"origin": origin}), 403, "forbidden_origin")
    runs.create.assert_not_called()


@pytest.mark.parametrize("headers", [{}, {"origin": "http://localhost"}, {"origin": "http://localhost:80", "sec-fetch-site": "same-origin"}])
def test_cli_and_same_origin_creation_preserved(tmp_path, headers):
    client, runs = make_client(tmp_path)
    with client:
        response = client.post("/api/v1/runs", json=REQUEST, headers=headers)
        assert response.status_code == 202
    runs.create.assert_called_once()


@pytest.mark.parametrize("site", ["cross-site", "same-site", "invalid"])
def test_browser_metadata_blocks_originless_cross_site_posts(tmp_path, site):
    client, runs = make_client(tmp_path)
    with client:
        assert_error(client.post("/api/v1/runs", json=REQUEST, headers={"sec-fetch-site": site}), 403, "forbidden_origin")
    runs.create.assert_not_called()


def test_simple_form_action_rejected(tmp_path):
    client, runs = make_client(tmp_path)
    with client:
        assert_error(client.post("/api/v1/runs/example/cancel", data={"x": "y"}, headers={"origin": "https://attacker.example"}), 403, "forbidden_origin")
    runs.cancel.assert_not_called()


def test_get_is_unaffected_by_origin(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        response = client.get("/api/v1/workflows", headers={"origin": "https://attacker.example"})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers


def test_configured_host_and_port(tmp_path):
    client, runs = make_client(tmp_path, hosts=["custom.local"], base_url="http://custom.local:8123")
    with client:
        assert client.post("/api/v1/runs", json=REQUEST, headers={"origin": "http://custom.local:8123"}).status_code == 202
        assert_error(client.post("/api/v1/runs", json=REQUEST, headers={"origin": "http://custom.local"}), 403, "forbidden_origin")
        assert_error(client.get("/api/v1/workflows", headers={"host": "localhost"}), 400, "invalid_host")
    runs.create.assert_called_once()


def test_duplicate_security_headers_rejected(tmp_path):
    client, runs = make_client(tmp_path)
    with client:
        assert_error(client.post("/api/v1/runs", json=REQUEST, headers=[("origin", "http://localhost"), ("origin", "http://localhost")]), 403, "forbidden_origin")
        assert_error(client.get("/api/v1/workflows", headers=[("host", "localhost"), ("host", "localhost")]), 400, "invalid_host")
    runs.create.assert_not_called()


@pytest.mark.parametrize("hosts", [[], ["*"], ["*.example"], ["http://localhost"], ["localhost:8000"], ["localhost "]])
def test_unsafe_host_configuration_rejected(tmp_path, hosts):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_dir=tmp_path, api_allowed_hosts=hosts)


def test_host_configuration_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("API_ALLOWED_HOSTS", '["LOCALHOST", "127.0.0.1"]')
    settings = Settings(_env_file=None, local_dir=tmp_path)
    assert settings.api_allowed_hosts == ["localhost", "127.0.0.1"]
