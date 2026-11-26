# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EJC-T03: system ask_once preview and runs work without installed bundles."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices


class RecordingFake(FakeAdapter):
    def __init__(self):
        super().__init__(sample_outputs={"FreeTextAnswer": {"answer": "Synthetic answer."}})
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        return super().generate_structured(**kwargs)


@pytest.fixture
def app(tmp_path):
    local = RecordingFake()
    cloud = RecordingFake()
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=False,
        gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "token",
    )
    services = ApplicationServices.create(
        settings, bundles={}, dry_run=False, adapter_overrides={"ollama": local, "gemini": cloud},
    )
    auth = {"Authorization": "Bearer " + ensure_token(settings.gui_token_file)}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield services, client, auth, local, cloud
    services.runs.close()


def request(execution_mode="local", *, task="Explain photosynthesis.", confirm_cloud=False):
    body = {
        "workflow_id": "ask_once", "bundle": None, "project_name": "Single session",
        "inputs": {"task": task}, "execution_mode": execution_mode, "sensitive": False,
    }
    if confirm_cloud:
        body["confirm_cloud"] = True
    return body


def test_system_preview_and_local_run_work_without_registered_packages(app):
    services, client, auth, local, _cloud = app
    assert client.get("/gui/api/bundles", headers=auth).json() == []
    preview = client.post("/gui/api/runs/preview", headers=auth, json={
        key: value for key, value in request().items() if key != "project_name"
    })
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["bundle"] is None
    assert result["routing"]["steps"][0]["model"] == "local_default"
    assert result["routing"]["steps"][0]["model_options"] == ["local_default"]
    assert "general" not in services.runs._bundle_runtimes

    created = client.post("/gui/api/runs", headers=auth, json=request())
    assert created.status_code == 202, created.text
    assert created.json()["bundle"] is None
    assert "general" not in services.runs._bundle_runtimes
    assert services.system.worker.process_once()
    run = services.runs.get(created.json()["id"])
    assert run["status"] == "completed" and run["llm_calls"] == 1
    assert run["artifacts"][0]["data"]["answer"] == "Synthetic answer."
    assert len(local.calls) == 1


def test_cloud_requires_confirmation_and_uses_only_cloud_fast(app):
    services, client, auth, local, cloud = app
    denied = client.post("/gui/api/runs", headers=auth, json=request("cloud"))
    assert denied.status_code == 422
    assert denied.json()["detail"]["code"] == "cloud_confirmation_required"
    assert services.runs.list() == []

    created = client.post("/gui/api/runs", headers=auth, json=request("cloud", confirm_cloud=True))
    assert created.status_code == 202, created.text
    stored = services.runs.get(created.json()["id"])["request"]
    assert stored["effective_permissions"] == ["provider:cloud"]
    assert stored["authorized_data_classes"] == ["non_sensitive"]
    assert services.system.worker.process_once()
    run = services.runs.get(created.json()["id"])
    assert run["status"] == "completed", run.get("error")
    assert [call["spec"].id for call in cloud.calls] == ["cloud_fast"]
    assert len(cloud.calls) == 1 and not local.calls
    assert "general" not in services.runs._bundle_runtimes


def test_cloud_secret_waits_for_the_existing_secret_review(app):
    _services, client, auth, local, cloud = app
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    created = client.post("/gui/api/runs", headers=auth, json=request(
        "cloud", task=f"Use this key: {secret}", confirm_cloud=True,
    ))
    assert created.status_code == 202, created.text
    assert _services.system.worker.process_once()
    run = _services.runs.get(created.json()["id"])
    assert run["waiting_reason"] == "secret_review"
    assert not local.calls and not cloud.calls


def test_default_mock_runs_system_ask_once_without_adapter_overrides(tmp_path):
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "mock-local", worker_enabled=False, mock_mode=True,
        gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "mock-token",
    )
    services = ApplicationServices.create(settings, bundles={}, dry_run=True)
    auth = {"Authorization": "Bearer " + ensure_token(settings.gui_token_file)}
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            created = client.post("/gui/api/runs", headers=auth, json=request())
            assert created.status_code == 202, created.text
            assert services.system.worker.process_once()
            run = services.runs.get(created.json()["id"])
            assert run["status"] == "completed", run.get("error")
            assert run["artifacts"][0]["data"]["answer"] == "This is a synthetic answer for testing."
            assert "general" not in services.runs._bundle_runtimes
    finally:
        services.runs.close()
