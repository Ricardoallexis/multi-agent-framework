# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Function steps through the public API and GUI in Mock mode."""
from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent import cli
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.run_request import FunctionStepRequestError, RunRequest
from multiagent.routing_preferences import preference_request
from multiagent.services import ApplicationServices
from multiagent.services.errors import translate
from multiagent.services.workflow_inputs import derive_inputs
from multiagent.workflows import WorkflowInput
from multiagent.contracts import ExecutionMode


GENERAL = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general"
BODY = {"workflow_id": "collect_summary", "project_name": "Function gate test",
        "inputs": {"document_text": "A short document with one key point.", "focus": "Key points"}}


@pytest.fixture
def gate(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"general": GENERAL}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield client, services, {"Authorization": f"Bearer {token}"}


def test_example_through_api_and_gui(gate):
    client, services, headers = gate
    for prefix in ("/api/v1", "/gui/api"):
        response = client.get(f"{prefix}/workflows/collect_summary/inputs", params={"bundle": "general"},
                              headers=headers)
        assert response.status_code == 200, response.text
        spec = response.json()
        assert [item["name"] for item in spec["inputs"]] == ["document_text", "focus"]
        assert spec["steps"][1]["function"] == "core.merge_outputs"
        assert "agent" not in spec["steps"][1]

    definition = client.get("/api/v1/workflows/collect_summary", params={"bundle": "general"}).json()
    assert definition["steps"][1]["function"] == "core.merge_outputs"
    assert "agent" not in definition["steps"][1]

    preview = client.post("/gui/api/runs/preview", json={"workflow_id": BODY["workflow_id"],
                                                        "inputs": BODY["inputs"], "bundle": "general"},
                          headers=headers)
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["steps"][1]["function"] == "core.merge_outputs"
    assert data["steps"][1]["text"] is None
    route = data["routing"]["steps"][1]
    assert route["status"] == "function" and route["function"] == "core.merge_outputs"
    assert all(route[key] is None for key in ("profile_id", "model", "effort"))

    created = client.post("/api/v1/runs", params={"bundle": "general"}, json=BODY)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    runtime = services.runs._bundle_runtime("general", create=False)
    assert runtime.worker.process_once()
    finished = client.get(f"/api/v1/runs/{run_id}").json()
    assert finished["status"] == "completed", (finished["error"], finished["events"][-3:])
    assert finished["llm_calls"] == 1
    assert finished["artifacts"][1]["data"]["steps"]["summarize"] == finished["artifacts"][0]["data"]
    report = client.get(f"/api/v1/runs/{run_id}/report")
    assert report.status_code == 200, report.text
    assert report.json()["status"] == "completed"
    assert [item["function"] for item in report.json()["steps"] if item.get("function")] == [
        "core.merge_outputs", "core.merge_outputs"]
    assert "agent_id" not in next(item for item in report.json()["steps"] if item.get("function"))
    attempts = client.get(f"/api/v1/runs/{run_id}/steps/collect/attempts")
    assert attempts.status_code == 200, attempts.text
    item = attempts.json()["attempts"][0]
    assert item["producer"] == {"kind": "function", "function": "core.merge_outputs"}
    assert item["tokens"] is None
    gui_run = client.get(f"/gui/api/runs/{run_id}", headers=headers)
    assert gui_run.status_code == 200, gui_run.text
    assert gui_run.json()["steps"][1]["function"] == "core.merge_outputs"
    assert "agent" not in gui_run.json()["steps"][1]
    assert gui_run.json()["steps"][1]["executor"]["kind"] == "function"


@pytest.mark.parametrize("option", ["step_modes", "routing_preferences"])
def test_function_step_run_options_have_one_stable_error(gate, option):
    client, services, headers = gate
    value = {"collect": "cloud"} if option == "step_modes" else {"collect": {"effort": "high"}}
    response = client.post("/api/v1/runs", params={"bundle": "general"}, json={**BODY, option: value})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == FunctionStepRequestError.code
    assert services.runs.list() == []

    gui_create = client.post("/gui/api/runs", json={**BODY, "bundle": "general", option: value}, headers=headers)
    assert gui_create.status_code == 422, gui_create.text
    assert gui_create.json()["detail"]["code"] == FunctionStepRequestError.code

    if option == "routing_preferences":
        gui = client.post("/gui/api/runs/preview", json={"workflow_id": BODY["workflow_id"],
                                                         "inputs": BODY["inputs"], "bundle": "general", option: value},
                          headers=headers)
        assert gui.status_code == 422, gui.text
        assert gui.json()["detail"]["code"] == FunctionStepRequestError.code, gui.json()


def test_python_request_error_uses_the_public_code():
    assert translate(FunctionStepRequestError("collect")).code == FunctionStepRequestError.code
    assert RunRequest(**BODY, step_modes={"collect": "cloud"}).step_modes["collect"].value == "cloud"


def test_function_only_inputs_have_no_prompt_markers(gate):
    _, services, _ = gate
    definition, prompts = services.definitions._read_parts("collect_summary", "general")
    function_only = replace(definition, steps=[definition.steps[1]], inputs=[WorkflowInput(name="unused")])
    spec = derive_inputs(function_only, prompts)
    assert spec["inputs"] == []
    assert spec["warnings"][0]["code"] == "unused_input"
    assert spec["steps"] == [{"id": "collect", "function": "core.merge_outputs", "description": definition.steps[1].description,
                               "when": None, "skipped_by_default": False}]


def test_preference_request_refuses_function_even_without_model(gate):
    _, services, _ = gate
    runtime = services.runs._bundle_runtime("general")
    step = runtime.service.engine.preflight("collect_summary").steps[1]
    with pytest.raises(FunctionStepRequestError) as caught:
        preference_request(runtime.service.engine, "collect_summary", step, BODY["inputs"],
                           ExecutionMode.AUTO, False, {"effort": "high"})
    assert caught.value.code == FunctionStepRequestError.code


def test_cli_reports_same_step_mode_error(gate, monkeypatch, capsys):
    client, _, _ = gate
    monkeypatch.setattr(cli, "api_client", lambda: nullcontext(client))
    with pytest.raises(SystemExit) as caught:
        cli.main(["run", "--project", BODY["project_name"], "--workflow", "collect_summary",
                  "--bundle", "general", "--input", "document_text=Text", "--input", "focus=Key points",
                  "--step-mode", "collect=cloud"])
    assert caught.value.code == 1
    assert FunctionStepRequestError.code in capsys.readouterr().err
