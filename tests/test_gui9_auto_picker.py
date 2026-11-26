# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T05: grouped auto picker and read-only composer choices."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import composer_options_api
from multiagent.gui.composer_options_api import composer_options
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "multiagent" / "gui" / "static" / "js"
VENDOR = ROOT / "multiagent" / "gui" / "static" / "vendor"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_groups_keep_blocked_reason_and_search_models():
    data = {"connections": [
        {"id": "codex_cli", "enabled": True, "available": True},
        {"id": "ollama", "enabled": True, "available": True},
        {"id": "gemini", "enabled": True, "available": False, "reason": "no_access"},
    ]}
    script = (
        "Object.defineProperty(globalThis, 'navigator', {value:{language:'es-MX'}, configurable:true});"
        "globalThis.fetch = () => { throw new Error('pickerGroups must not fetch'); };"
        f"const m = await import({json.dumps((JS / 'model-picker-model.js').as_uri())});"
        "const data = JSON.parse(process.env.DATA);"
        "const legacy = m.pickerGroups(data,{executors:[{executor_id:'codex_cli',models:['gpt-6'],efforts:['high']}]},'');"
        "const options = {options:[{id:'codex_cli',kind:'app',status:'available',selectable:true,models:['gpt-6'],"
        "efforts:['high'],context_window:128000},{id:'gemini',kind:'cloud',status:'blocked',selectable:false,reason:'local_only'}]};"
        "const modern = m.pickerGroups(data,options,'');"
        "const empty = m.pickerGroups(data,options,'no matching model');"
        "console.log(JSON.stringify({legacy,modern,empty,"
        "found:m.pickerGroups(data,{executors:[{executor_id:'codex_cli',models:['gpt-6']}]},'gpt-6'),"
        "efforts:m.AUTO_EFFORTS, variants:m.groupAgyModels(['gemini-4-hydrogen-low','gemini-4-hydrogen-high'])}));"
    )
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                            capture_output=True, text=True, encoding="utf-8", timeout=60,
                            env={**os.environ, "DATA": json.dumps(data)})
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert [item["id"] for item in out["legacy"]["apps"]] == ["codex_cli"]
    assert [item["id"] for item in out["legacy"]["local"]] == ["ollama"]
    assert out["legacy"]["cloud"][0]["reason"] == "Sin acceso."
    assert [item["id"] for item in out["found"]["apps"]] == ["codex_cli"]
    assert out["modern"]["apps"][0]["models"] == ["gpt-6"]
    assert out["modern"]["apps"][0]["efforts"] == ["high"]
    assert out["modern"]["apps"][0]["contextWindow"] == 128000
    assert out["modern"]["cloud"][0]["reason"] == "No disponible en un área solo local."
    assert all(not group for group in out["empty"].values())
    assert out["efforts"] == ["medium", "high", "max"]
    assert out["variants"] == [{"base": "gemini-4-hydrogen", "efforts": ["low", "high"]}]


def test_composer_options_joins_snapshots_without_checks_or_writes():
    calls = []

    class Connections:
        def list(self):
            calls.append("list")
            return {"connections": [
                {"id": "codex_cli", "kind": "executor", "enabled": True, "available": True,
                 "quota": {"windows": [{"used_percent": 45}]}},
                {"id": "gemini", "kind": "model", "enabled": True, "available": True},
            ]}

    class Executors:
        def _composer_options(self):
            calls.append("options")
            return {"executors": [{"executor_id": "codex_cli", "available": True,
                                    "models": ["gpt-6"], "efforts": ["high"]}]}

    out = composer_options(SimpleNamespace(connections=Connections(), executors=Executors()), local_only=True)
    assert calls == ["list", "options"]
    assert out["options"][0]["selectable"] is True
    assert out["options"][0]["quota"]["windows"][0]["used_percent"] == 45
    assert out["options"][0]["models"] == ["gpt-6"]
    assert out["options"][1]["selectable"] is False
    assert out["options"][1]["reason"] == "local_only"


def test_composer_options_http_auth_local_only_and_executor_unavailable(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings)
    created_connections = []

    class Connections:
        calls = 0

        def list(self):
            self.calls += 1
            return {"connections": [
                {"id": "codex_cli", "kind": "executor", "enabled": True, "available": True},
                {"id": "codex_acp", "kind": "executor", "enabled": True, "available": True},
                {"id": "gemini", "kind": "model", "enabled": True, "available": True,
                 "checked_at": "2026-11-25T00:00:00Z", "verification": "verified",
                 "context_window": {"tokens": 128000}},
                {"id": "ollama", "kind": "model", "enabled": True, "available": True},
            ]}

    connections = Connections()

    def connection_service(actual_services):
        created_connections.append(actual_services)
        return connections

    class Executors:
        calls = 0

        def _composer_options(self):
            self.calls += 1
            return {"executors": [
                {"executor_id": "codex_cli", "available": False, "models": ["gpt-6"], "efforts": ["high"]},
                {"executor_id": "codex_acp", "available": True, "models": ["gpt-6"], "efforts": ["high"]},
            ]}

    services.connections = None
    services.executors = Executors()
    monkeypatch.setattr(composer_options_api, "ConnectionServices", connection_service)
    token = ensure_token(settings.gui_token_file)
    auth = {"Authorization": f"Bearer {token}"}
    client = TestClient(create_app(services=services), base_url="http://localhost")
    try:
        assert client.get("/gui/api/composer/options").status_code == 401
        response = client.get("/gui/api/composer/options", headers=auth)
        assert response.status_code == 200
        options = {row["id"]: row for row in response.json()["options"]}
        assert options["codex_cli"]["reason"] == "executor_unavailable"
        assert options["codex_cli"]["selectable"] is False
        assert "codex_acp" not in options
        assert options["gemini"]["context_window"] == {"tokens": 128000}

        local = client.get("/gui/api/composer/options?local_only=true", headers=auth)
        assert local.status_code == 200
        local_options = {row["id"]: row for row in local.json()["options"]}
        assert local_options["gemini"]["selectable"] is False
        assert local_options["gemini"]["reason"] == "local_only"
        assert local_options["ollama"]["selectable"] is True
        assert created_connections == [services]
        assert connections.calls == services.executors.calls == 2
    finally:
        client.close()
        services.runs.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_model_picker_renders_legacy_select_without_parent_callbacks_and_new_controls_with_them():
    component = JS / "model-picker.js"
    model = JS / "model-picker-model.js"
    vendor = VENDOR / "htm-preact-standalone.module.js"
    help_module = JS / "help.js"
    text_module = JS / "text.js"
    i18n_module = JS / "i18n.js"
    script = f"""
      import fs from "node:fs";
      import {{ html }} from {json.dumps(vendor.as_uri())};
      import * as pickerModel from {json.dumps(model.as_uri())};
      import {{ helpFor }} from {json.dumps(help_module.as_uri())};
      import {{ errorText, fmtDate }} from {json.dumps(text_module.as_uri())};
      import {{ t }} from {json.dumps(i18n_module.as_uri())};
      Object.defineProperty(globalThis, "navigator", {{value:{{language:"es-MX"}}, configurable:true}});
      const source = fs.readFileSync({json.dumps(str(component))}, "utf8")
        .replace(/^import .*;\\r?\\n/gm, "")
        .replace("export function ModelPicker(", "function ModelPicker(");
      let states = [], stateIndex = 0, writes = [];
      const useState = (initial) => {{
        const index = stateIndex++;
        if (states[index] === undefined) states[index] = initial;
        const value = states[index];
        return [value, (next) => writes.push([index, next])];
      }};
      const useRef = () => ({{current:null}});
      const useEffect = () => {{}};
      const render = new Function("html","useState","useRef","useEffect","helpFor","AUTO_EFFORTS",
        "buildModelPicker","pickerGroups","errorText","fmtDate","t", source + "\\nreturn ModelPicker;")
        (html,useState,useRef,useEffect,helpFor,pickerModel.AUTO_EFFORTS,pickerModel.buildModelPicker,
          pickerModel.pickerGroups,errorText,fmtDate,t);
      const draw = (props) => {{ stateIndex = 0; return flatten(render(props)); }};
      const data = {{connections:[
        {{id:"codex_cli",kind:"executor",enabled:true,available:true}},
        {{id:"gemini",kind:"model",enabled:true,available:true}},
      ]}};
      const flatten = (value, output=[]) => {{
        if (value == null || typeof value === "boolean") return output;
        if (Array.isArray(value)) {{ value.forEach((item) => flatten(item, output)); return output; }}
        if (typeof value !== "object") return output;
        output.push(value);
        flatten(value.props?.children, output);
        return output;
      }};
      const text = (value) => value == null || typeof value === "boolean" ? ""
        : Array.isArray(value) ? value.map(text).join("")
        : typeof value === "object" ? text(value.props?.children) : String(value);
      const props = {{connections:data,selectedId:"gemini",onSelect(){{}},onConnect(){{}}}};
      const legacy = draw(props);
      states = [true, true];
      const autoChanged = [], effortChanged = [];
      const automatic = draw({{...props,onAutoChange(value){{autoChanged.push(value);}},
        onEffortChange(value){{effortChanged.push(value);}}}});
      automatic.find((node) => node.props.role === "switch").props.onClick();
      automatic.find((node) => node.type === "button" && text(node) === "Alto").props.onClick();
      states = [true, false, "medium", ""];
      const withoutExecutor = draw({{...props,onAutoChange(){{}},onEffortChange(){{}},
        composerOptions:{{options:[{{id:"codex_cli",kind:"app",status:"available",selectable:true}}]}}}});
      states = [true, false, "medium", ""];
      let selected = null, executor = null;
      writes = [];
      const manual = draw({{...props,onAutoChange(){{}},onEffortChange(){{}},
        onSelect(value){{selected=value;}},onSelectExecutor(value){{executor=value;}},
        composerOptions:{{options:[{{id:"codex_cli",kind:"app",status:"available",selectable:true,
          models:["gpt-6"],efforts:["high"]}}]}}}});
      const find = (tree, predicate) => tree.find(predicate);
      const select = find(manual, (node) => node.type === "select" && node.props.id === "ns-model-select");
      const app = find(manual, (node) => node.type === "button" && node.props.class === "ns-auto-option");
      const trigger = find(manual, (node) => node.props.class === "ns-auto-trigger");
      const dialog = find(manual, (node) => node.props.role === "dialog");
      let focusReturned = 0, preventCalls = 0;
      trigger.ref.current = {{focus(){{focusReturned += 1;}}}};
      dialog.props.onKeyDown({{key:"Escape",defaultPrevented:true,preventDefault(){{preventCalls += 1;}}}});
      const closeWritesAfterPrevented = writes.length;
      dialog.props.onKeyDown({{key:"Escape",defaultPrevented:false,preventDefault(){{preventCalls += 1;}}}});
      const closeWritesAfterEscape = writes.length;
      select.props.onChange({{target:{{value:"gemini"}}}});
      const closeWritesAfterSelect = writes.length;
      app.props.onClick();
      const output = {{
        legacySelect: Boolean(find(legacy, (node) => node.type === "select" && node.props.id === "ns-model-select")),
        legacyLabelFor: find(legacy, (node) => node.type === "label")?.props.for,
        legacyTrigger: Boolean(find(legacy, (node) => node.props.class === "ns-auto-trigger")),
        legacyEfforts: legacy.some((node) => node.props.role === "group" && node.props["aria-label"] === "Esfuerzo"),
        appOptionWithoutHandler: Boolean(find(withoutExecutor, (node) => node.type === "button"
          && node.props.class === "ns-auto-option" && text(node).includes("Codex"))),
        appNavigationWithoutHandler: Boolean(find(withoutExecutor, (node) => node.props.class === "ns-auto-connect")),
        automaticEfforts: automatic.filter((node) => node.type === "button").map((node) => node.props.children)
          .flat(2).filter((value) => ["Estándar","Alto","Máximo"].includes(value)),
        automaticTrigger: text(find(automatic, (node) => node.props.class === "ns-auto-trigger")),
        autoChanged, effortChanged,
        manualTrigger: text(trigger),
        dialog: dialog.props["aria-label"],
        focusReturned, preventCalls, closeWritesAfterPrevented, closeWritesAfterEscape, closeWritesAfterSelect,
        appDisabled: app.props.disabled,
        selected, executor, writes,
      }};
      console.log(JSON.stringify(output));
    """
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["legacySelect"] is True and out["legacyTrigger"] is False and out["legacyEfforts"] is False
    assert out["legacyLabelFor"] == "ns-model-select"
    assert out["appOptionWithoutHandler"] is False and out["appNavigationWithoutHandler"] is True
    assert out["automaticEfforts"] == ["Estándar", "Alto", "Máximo"]
    assert out["automaticTrigger"] == "Automático" and out["manualTrigger"] == "Gemini"
    assert out["autoChanged"] == [False] and out["effortChanged"] == ["high"]
    assert out["dialog"] == "Modelo"
    assert out["focusReturned"] == 1 and out["preventCalls"] == 1 and out["closeWritesAfterPrevented"] == 0
    assert out["closeWritesAfterEscape"] == 1 and out["closeWritesAfterSelect"] == 2
    assert out["appDisabled"] is False
    assert out["selected"] == "gemini" and out["executor"] == "codex_cli"
    assert [write for write in out["writes"] if write[0] == 0] == [[0, False], [0, False], [0, False]]
    picker = component.read_text(encoding="utf-8")
    for name in ("connections", "selectedId", "onSelect", "loading", "error", "onRetry", "onConnect", "help"):
        assert name in picker
    assert 'helpFor("chat.model_picker")' in picker
    assert "event.defaultPrevented" in picker and "event.preventDefault()" in picker
    assert "triggerRef.current?.focus()" in picker and "popover?.querySelector" in picker
    assert "!popover?.contains(event.target)" in picker and "pointerdown" in picker
    assert "innerHTML" not in picker and "apiPost(" not in picker
