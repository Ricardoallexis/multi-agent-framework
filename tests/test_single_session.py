# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SES-T03: one-step general bundle and connected catalog choices, with Mock only."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL = ROOT / "examples" / "bundles" / "general"
MODEL = ROOT / "multiagent" / "gui" / "static" / "js" / "new-session-model.js"


@pytest.fixture
def app(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "token")
    services = ApplicationServices.create(settings, bundles={"general": GENERAL}, dry_run=True)
    auth = {"Authorization": "Bearer " + ensure_token(settings.gui_token_file)}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield services, client, auth
    services.runs.close()


def body(model="local_default", *, execution_mode="auto", sensitive=False):
    return {"workflow_id": "ask_once", "bundle": "general", "project_name": "Sesión única",
            "inputs": {"task": "Explica la fotosíntesis."}, "execution_mode": execution_mode, "sensitive": sensitive,
            "routing_preferences": {"answer": {"mode": "single", "model": model}}}


def preview_body(model="local_default", *, execution_mode="auto", sensitive=False):
    result = body(model, execution_mode=execution_mode, sensitive=sensitive)
    result.pop("project_name")
    return result


def test_preview_and_run_use_chosen_catalog_model_with_mock_usage(app):
    services, client, auth = app
    assert services.runs._bundle_runtime("general", create=False) is None
    default_request = preview_body()
    default_request.pop("routing_preferences")
    default_preview = client.post("/gui/api/runs/preview", headers=auth, json=default_request)
    assert default_preview.status_code == 200, default_preview.text
    default_route = default_preview.json()["routing"]["steps"][0]
    # Without a preference the preview offers both catalog candidates and picks none; the run decides (see below).
    assert default_route["model"] is None and default_route["mode"] == "automatic"
    assert default_route["model_options"] == ["local_default", "cloud_fast"]
    preview = client.post("/gui/api/runs/preview", headers=auth, json=preview_body())
    assert preview.status_code == 200, preview.text
    route = preview.json()["routing"]["steps"][0]
    assert route["model"] == "local_default" and route["mode"] == "single"
    assert services.runs._bundle_runtime("general", create=False) is None
    created = client.post("/gui/api/runs", headers=auth, json=body())
    assert created.status_code == 202, created.text
    runtime = services.runs._bundle_runtime("general", create=False)
    assert runtime.worker.process_once()
    run = services.runs.get(created.json()["id"])
    assert run["status"] == "completed", run.get("error")
    assert run["llm_calls"] == 1
    assert run["artifacts"][0]["data"]["answer"]
    assert run["request"]["routing_preferences"] == body()["routing_preferences"]


def test_model_outside_bundle_catalog_is_rejected_before_run(app):
    services, client, auth = app
    for endpoint in ("/gui/api/runs/preview", "/gui/api/runs"):
        response = client.post(endpoint, headers=auth,
                               json=preview_body("cloud_advanced") if endpoint.endswith("preview") else body("cloud_advanced"))
        assert response.status_code == 422, response.text
        assert "outside" in response.text.lower()
    assert services.runs.list() == []


def test_run_without_preference_calls_only_local_default(app):
    services, client, auth = app
    request = body()
    request.pop("routing_preferences")
    created = client.post("/gui/api/runs", headers=auth, json=request)
    assert created.status_code == 202, created.text
    assert services.runs._bundle_runtime("general", create=False).worker.process_once()
    run = services.runs.get(created.json()["id"])
    assert run["status"] == "completed", run.get("error")
    assert run["llm_calls"] == 1
    decisions = [e["payload"] for e in run["events"] if e["event_type"] == "routing_decision"]
    assert [d["selected_model"] for d in decisions] == ["local_default"]
    assert [c["model_id"] for c in decisions[0]["candidates"] if c["eligible"]] == ["local_default"]
    providers = {row["provider"] for row in services.runs.usage(run["id"])["records"]}
    assert "gemini" not in providers


@pytest.mark.parametrize("execution_mode", ["auto", "cloud"])
def test_cloud_model_without_a_cloud_grant_is_rejected_before_run(app, execution_mode):
    # No public route carries a cloud grant yet (RunRequest is strict and has none), so the server refuses
    # cloud_fast whatever the client says; it never reaches Gemini and creates no run.
    services, client, auth = app
    response = client.post("/gui/api/runs", headers=auth, json=body("cloud_fast", execution_mode=execution_mode))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert services.runs.list() == []


def test_sensitive_data_cannot_select_cloud_fast(app):
    services, client, auth = app
    response = client.post("/gui/api/runs", headers=auth,
                           json=body("cloud_fast", sensitive=True))
    assert response.status_code == 422, response.text
    assert services.runs.list() == []


def test_budget_still_limits_single_session(app):
    services, client, auth = app
    runtime = services.runs._bundle_runtime("general")
    runtime.service.settings = runtime.service.settings.model_copy(update={"max_llm_calls": 0})
    created = client.post("/gui/api/runs", headers=auth, json=body())
    assert created.status_code == 202, created.text
    assert runtime.worker.process_once()
    run = services.runs.get(created.json()["id"])
    assert run["llm_calls"] == 0
    assert run["status"] != "completed"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_pure_model_only_offers_connected_catalog_candidates():
    script = f"""
      import {{ singleSessionModels, singleSessionBody }} from {json.dumps(MODEL.as_uri())};
      const connections = {{connections: [
        {{id: 'ollama', enabled: true, available: true}},
        {{id: 'gemini', enabled: true, available: true, key_configured: true}},
        {{id: 'openai', enabled: false, available: false}}
      ]}};
      const routing = {{steps: [{{step_id: 'answer', model_options: ['local_default', 'cloud_fast']}}],
        catalog_models: [{{id: 'local_default', provider: 'ollama'}}, {{id: 'cloud_fast', provider: 'gemini'}}]}};
      const choices = singleSessionModels(connections, routing);
      if (choices[0].models[0].id !== 'local_default') throw Error('local model missing');
      if (choices[1].models[0].id !== 'cloud_fast') throw Error('connected cloud model missing');
      if (choices[2].models.length) throw Error('disconnected provider offered');
      const noGeminiKey = singleSessionModels({{connections: connections.connections.map((row) =>
        row.id === 'gemini' ? {{...row, available: false, key_configured: false}} : row)}}, routing);
      if (noGeminiKey[1].models.length) throw Error('cloud model offered without a configured connection');
      const body = singleSessionBody('  hola  ', 'local_default');
      if (body.inputs.task !== 'hola' || body.routing_preferences.answer.mode !== 'single') throw Error('wrong body');
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
