# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Inputs declared for deterministic workflow steps."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.workflow_inputs import derive_inputs
from multiagent.workflows import WorkflowInput


GENERAL = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general"


@pytest.fixture
def world(tmp_path):
    settings = Settings(
        _env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
        gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, bundles={"general": GENERAL}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    yield services, token
    services.runs.close()


def function_only(services, *, required=None):
    definition, prompts = services.definitions._read_parts("collect_summary", "general")
    definition = replace(
        definition,
        steps=[definition.steps[-1]],
        inputs=[WorkflowInput(name="function_value", required=required)],
    )
    return definition, prompts


@pytest.mark.parametrize(("required", "expected"), [(None, False), (False, False), (True, True)])
def test_function_only_declarations_are_offered_and_keep_required(required, expected, world):
    services, _ = world
    definition, prompts = function_only(services, required=required)

    spec = derive_inputs(definition, prompts)

    assert spec["warnings"] == []
    assert spec["source"] == "declared"
    assert spec["inputs"] == [{
        "name": "function_value", "label": "Function value", "description": None, "example": None,
        "type": "text", "choices": [], "source": "declared", "required": expected, "context": False,
        "skipped_only": False, "used_in": [], "needed_by": [], "prompt_line": None,
    }]


def test_create_endpoint_rejects_missing_required_function_input_before_enqueue(world, monkeypatch):
    services, token = world
    definition, prompts = function_only(services, required=True)
    monkeypatch.setattr(services.definitions, "_read_parts", lambda workflow_id, bundle=None: (definition, prompts))

    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        spec = client.get(
            "/gui/api/workflows/collect_summary/inputs?bundle=general",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = client.post(
            "/gui/api/runs",
            headers={"Authorization": f"Bearer {token}"},
            json={"workflow_id": "collect_summary", "project_name": "Function input test",
                  "bundle": "general", "inputs": {}},
        )

    assert spec.status_code == 200, spec.text
    assert [(item["name"], item["required"], item["used_in"], item["needed_by"], item["prompt_line"])
            for item in spec.json()["inputs"]] == [("function_value", True, [], [], None)]
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert response.json()["detail"]["details"]["missing_inputs"] == ["function_value"]
    assert services.runs.list() == []
