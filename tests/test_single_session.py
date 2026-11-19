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


def body(model="local_default"):
    return {"workflow_id": "ask_once", "bundle": "general", "project_name": "Sesión única",
            "inputs": {"task": "Explica la fotosíntesis."}, "execution_mode": "auto",
            "routing_preferences": {"answer": {"mode": "single", "model": model}}}


def preview_body(model="local_default"):
    result = body(model)
    result.pop("project_name")
    return result


def test_preview_and_run_use_chosen_catalog_model_with_mock_usage(app):
    services, client, auth = app
    assert services.runs._bundle_runtime("general", create=False) is None
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
                               json=preview_body("cloud_fast") if endpoint.endswith("preview") else body("cloud_fast"))
        assert response.status_code == 422, response.text
        assert "outside" in response.text.lower()
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
        {{id: 'gemini', enabled: true, available: true}},
        {{id: 'openai', enabled: false, available: false}}
      ]}};
      const routing = {{steps: [{{step_id: 'answer', model_options: ['local_default']}}],
        catalog_models: [{{id: 'local_default', provider: 'ollama'}}]}};
      const choices = singleSessionModels(connections, routing);
      if (choices[0].models[0].id !== 'local_default') throw Error('local model missing');
      if (choices[1].models.length || !choices[1].reason.includes('catálogo')) throw Error('uncatalogued provider offered');
      if (choices[2].models.length) throw Error('disconnected provider offered');
      const body = singleSessionBody('  hola  ', 'local_default');
      if (body.inputs.task !== 'hola' || body.routing_preferences.answer.mode !== 'single') throw Error('wrong body');
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
