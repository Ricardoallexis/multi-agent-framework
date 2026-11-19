# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SES-T02: pure connection picker, agy composer capability and supervised continuity."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from multiagent.executors.antigravity import AntigravityAdapter, NOTICE_ID
from multiagent.executors.registry import ExecutorRegistry
from multiagent.gui import STATIC_DIR, continuity_api
from multiagent.routing.executor_capabilities import validate_executor_config
from multiagent.services.executors import ExecutorServices
from multiagent.services.handoff import Candidate, SUBSCRIPTION_SURFACES

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str, data: dict) -> dict:
    prelude = (
        f'import * as m from {json.dumps((JS / "model-picker-model.js").as_uri())};\n'
        "globalThis.fetch = () => { throw new Error('pure model must not use network'); };\n"
        "const DATA = JSON.parse(process.env.DATA);\n"
    )
    result = subprocess.run([NODE, "--input-type=module", "-e", prelude + script], capture_output=True, text=True,
                            encoding="utf-8", timeout=60, env={**os.environ, "DATA": json.dumps(data)})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_only_enabled_connected_choices_can_be_selected():
    data = {"connections": [
        {"id": "ollama", "enabled": True, "available": True, "checked_at": "2026-11-19T01:00:00Z"},
        {"id": "gemini", "enabled": True, "available": False, "reason": "reachability_unknown"},
        {"id": "codex_cli", "enabled": False, "available": True, "missing_step": "enable"},
        {"id": "claude_cli", "enabled": True, "available": True, "quota": {
            "status": "ok", "windows": [{"window": "weekly", "used_percent": 40, "resets_at": "2026-11-25"}]}},
    ]}
    result = run_node("console.log(JSON.stringify({picker:m.buildModelPicker(DATA,'codex_cli'), "
                      "ollama:m.buildModelPicker(DATA,'ollama').selectedId}))", data)
    picker = result["picker"]
    assert [item["id"] for item in picker["models"]] == ["ollama"]
    assert [item["id"] for item in picker["executors"]] == ["claude_cli"]
    assert [item["id"] for item in picker["disconnected"]] == ["gemini", "codex_cli"]
    assert picker["selectedId"] is None and result["ollama"] == "ollama"
    assert picker["models"][0]["checkedAt"] == "2026-11-19T01:00:00Z"
    assert "40 %" in picker["executors"][0]["quota"][0]
    assert picker["disconnected"][1]["reason"] == "Falta habilitarlo."


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_agy_quota_floor_disables_choice_with_notice_and_empty_state():
    data = {"connections": [{"id": "antigravity_cli", "enabled": True, "available": False,
                             "reason": "quota_below_launch_floor", "checked_at": "2026-11-19T01:00:00Z",
                             "quota": {"windows": [{"window": "weekly", "used_percent": 90}]}}]}
    picker = run_node("console.log(JSON.stringify(m.buildModelPicker(DATA,'antigravity_cli')))", data)
    assert picker["empty"] and picker["selectedId"] is None
    assert picker["disconnected"][0]["selectable"] is False
    assert "15 %" in picker["disconnected"][0]["reason"]
    assert "Cada lanzamiento pide aprobación" in picker["disconnected"][0]["agyNotice"]
    assert "90 %" in picker["disconnected"][0]["quota"][0]
    assert run_node("console.log(JSON.stringify(m.buildModelPicker(DATA)))", {"connections": []})["empty"]
    data["connections"][0]["available"] = True
    data["connections"][0]["reason"] = None
    ready = run_node("console.log(JSON.stringify(m.buildModelPicker(DATA,'antigravity_cli')))", data)
    assert ready["selectedId"] == "antigravity_cli"
    assert "Cada lanzamiento pide aprobación" in ready["executors"][0]["agyNotice"]


def test_picker_is_wired_to_read_only_connections_and_has_phone_rules():
    view = (JS / "new-session.js").read_text(encoding="utf-8")
    component = (JS / "model-picker.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "new-session.css").read_text(encoding="utf-8")
    assert 'apiGet("/gui/api/connections", token)' in view
    assert "<${ModelPicker}" in view
    assert "apiPost(" not in component
    assert "No conectados (" in component and 'type="radio"' in component
    assert 'helpFor("chat.model_choice")' in component
    assert 'helpFor("chat.model_disconnected")' in component
    assert 'helpFor("chat.model_retry")' in component
    assert 'group("Modelos", picker.models)' in component and 'group("Ejecutores", picker.executors)' in component
    assert "/* SES-T02:" in css and "@media (max-width: 599.98px)" in css
    # app.css gives every input full width and a touch-target height; the picker radio keeps its own size.
    radio = re.search(r"\.ns-picker-choice input\s*\{([^}]*)\}", css)
    assert radio and "width: auto" in radio.group(1) and "min-height: 0" in radio.group(1)
    assert "innerHTML" not in view + component


def test_composer_accepts_agy_model_without_separate_effort():
    catalog = yaml.safe_load((ROOT / "catalog" / "models.yaml").read_text(encoding="utf-8"))
    declaration = validate_executor_config(catalog["executor_routing"])["executors"]["antigravity_cli"]
    adapter = AntigravityAdapter(notice_accepted=NOTICE_ID)
    config = {"version": 1, "executors": {"antigravity_cli": {**declaration, "enabled": True}}}
    registry = ExecutorRegistry(validate_executor_config(config), [adapter])
    service = ExecutorServices(SimpleNamespace(), registry=registry)
    service.status = lambda: {"executors": [{"executor_id": "antigravity_cli", "available": True}]}
    [option] = service._composer_options()["executors"]
    assert option["executor_id"] == "antigravity_cli" and option["available"] is True
    assert "gemini-4-argon-high" in option["models"] and option["efforts"] == []


def test_agy_is_named_in_continuity_but_still_requires_approval():
    assert continuity_api.APPS["antigravity_cli"] == ("Antigravity", "antigravity")
    assert continuity_api._app_of("antigravity_cli", "antigravity_cli") == ("Antigravity", "antigravity")
    assert "antigravity_cli" not in SUBSCRIPTION_SURFACES
    candidate = Candidate("antigravity_cli", surface="antigravity_cli", policy="no_ask",
                          within_level=True, same_folder=True)
    assert candidate.subscription is False
    assert candidate.ask_reasons() == ["paid_api"]
