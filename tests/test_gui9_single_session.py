# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EJC-T03: single-session requests use system routes and explicit local/cloud modes."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from test_gui9_launch import DOM_PRELUDE

JS = STATIC_DIR / "js"
MODEL = JS / "new-session-model.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SPANISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'es-MX'},configurable:true})",
]


def run_dom_node(script: str) -> dict:
    result = subprocess.run(
        [NODE, *SPANISH_NAVIGATOR, "--input-type=module", "-e", DOM_PRELUDE + script],
        capture_output=True, text=True, encoding="utf-8", timeout=60, cwd=STATIC_DIR,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_body_and_model_choices_use_system_preview_options_only():
    script = f"""
      import {{ singleSessionModels, singleSessionBody }} from {json.dumps(MODEL.as_uri())};
      const routing = {{
        steps: [{{ step_id: "answer", model_options: ["local_default", "cloud_fast"] }}],
        catalog_models: [
          {{ id: "local_default", provider: "ollama" }},
          {{ id: "cloud_fast", provider: "gemini" }},
          {{ id: "cloud_advanced", provider: "gemini" }},
        ],
      }};
      const choices = singleSessionModels({{ connections: [
        {{ id: "ollama", enabled: true, available: true }},
        {{ id: "gemini", enabled: true, available: true }},
        {{ id: "openai", enabled: true, available: true }},
      ] }}, routing);
      const local = singleSessionBody("  local task  ", "local_default", false, "Local project");
      const cloud = singleSessionBody("cloud task", "cloud_fast");
      const confirmed = singleSessionBody("cloud task", "cloud_fast", true);
      console.log(JSON.stringify({{ choices, local, cloud, confirmed }}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert [item["id"] for choice in out["choices"] for item in choice["models"]] == [
        "local_default", "cloud_fast",
    ]
    assert out["local"] == {
        "workflow_id": "ask_once", "bundle": None, "project_name": "Local project",
        "inputs": {"task": "local task"}, "execution_mode": "local", "sensitive": False,
    }
    assert out["cloud"]["execution_mode"] == "cloud" and "confirm_cloud" not in out["cloud"]
    assert out["confirmed"]["confirm_cloud"] is True
    assert "routing_preferences" not in out["cloud"] and "model" not in out["cloud"]


def test_single_session_uses_system_previews_and_one_visible_submit_action():
    raw_source = (JS / "new-session.js").read_text(encoding="utf-8")
    component = raw_source[raw_source.index("function SingleSession("):raw_source.index(
        "// SES-T04:", raw_source.index("function SingleSession("))]
    assert 'workflow_id: "ask_once", bundle: null' in component
    assert 'Promise.all(["local", "cloud"]' in component
    assert "routing_preferences" not in component
    assert 't("new_session.single_session_system_lead")' in component
    assert 't("new_session.loading_system_models")' in component
    assert "showSubmit=${false}" in component
    assert 't("new_session.launch_ready")' in component
    assert 't("new_session.launch")' in component
    assert "showSubmit = true" in raw_source
    executor = raw_source[raw_source.index("// SES-T04:"):raw_source.index("export function NewSession(")]
    assert "showSubmit" not in executor
    new_session = raw_source[raw_source.index("export function NewSession("):]
    assert "showSubmit" not in new_session


@needs_node
def test_single_session_dom_has_one_submit_action_in_each_state():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    component = json.dumps((JS / "new-session.js").as_uri())
    out = run_dom_node(f"""
      const {{ render, html }} = await import({preact});
      const {{ SingleSession }} = await import({component});
      const calls = [];
      const reply = (payload) => ({{ ok: true, status: 200, json: async () => payload }});
      globalThis.fetch = async (path, options = {{}}) => {{
        const body = options.body ? JSON.parse(options.body) : null;
        calls.push({{ path: String(path), body }});
        if (path === "/gui/api/runs/preview") {{
          const models = body.execution_mode === "cloud" ? ["cloud_fast"] : ["local_default"];
          if (body.inputs.task === "Vista previa") return reply({{
            routing: {{ steps: [{{ step_id: "answer", model_options: models }}],
              catalog_models: [
                {{ id: "local_default", provider: "ollama" }},
                {{ id: "cloud_fast", provider: "gemini" }},
              ] }},
          }});
          return reply({{ steps: [], missing_inputs: [], empty_optional_inputs: [] }});
        }}
        throw new Error("Unexpected fake API request: " + path);
      }};
      const root = document.createElement("div");
      const connections = {{ connections: [
        {{ id: "ollama", enabled: true, available: true }},
        {{ id: "gemini", enabled: true, available: true }},
      ] }};
      render(html`<${{SingleSession}} token="fake" mock=${{true}}
        connections=${{connections}}
        task="Synthetic task" onTaskChange=${{() => {{}}}} project="" projects=${{[]}}
        onProject=${{() => {{}}}} onMode=${{() => {{}}}} onForm=${{() => {{}}}}
        initialProvider="gemini" />`, root);
      const actions = () => allElements(root).filter((node) =>
        node.localName === "button" && ["Enviar", "Revisar y lanzar", "Lanzar"].includes(node.textContent.trim()))
        .map((node) => node.textContent.trim());
      await waitUntil(() => calls.filter((call) => call.path === "/gui/api/runs/preview").length === 2,
        5000, "system preview requests");
      await waitUntil(() => allElements(root).find((node) => node.id === "ns-single-model")?.value === "cloud_fast",
        5000, "default cloud model selection");
      await waitUntil(() => actions().length === 1, 5000, "single review action");
      const before = actions();
      const review = allElements(root).find((node) =>
        node.localName === "button" && node.textContent.trim() === "Revisar y lanzar");
      review.emit("click", {{ target: review }});
      await waitUntil(() => root.textContent.includes("Todo listo: nada se ejecuta hasta que pulses «Lanzar»."),
        5000, "launch-ready notice");
      const launch = allElements(root).find((node) =>
        node.localName === "button" && node.textContent.trim() === "Lanzar");
      const cloudConfirm = allElements(root).find((node) => node.localName === "input" && node.type === "checkbox");
      const disabledUntilConfirm = launch.disabled;
      cloudConfirm.checked = true;
      cloudConfirm.emit("change", {{ target: cloudConfirm, currentTarget: cloudConfirm }});
      await waitUntil(() => !allElements(root).find((node) =>
        node.localName === "button" && node.textContent.trim() === "Lanzar").disabled,
        5000, "cloud confirmation enables launch");
      console.log(JSON.stringify({{ before, after: actions(), notice:
        root.textContent.includes("Todo listo: nada se ejecuta hasta que pulses «Lanzar»."),
        cloudConfirmVisible: root.textContent.includes("Confirmo enviar esta tarea al modelo en la nube."),
        disabledUntilConfirm,
        wrappers: allElements(root).filter((node) => hasClass(node, "ns")).length }}));
    """)
    assert out == {
        "before": ["Revisar y lanzar"], "after": ["Lanzar"], "notice": True,
        "cloudConfirmVisible": True, "disabledUntilConfirm": True, "wrappers": 0,
    }


@needs_node
def test_single_session_form_uses_system_workflow_routes_without_bundle():
    preact = json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())
    component = json.dumps((JS / "new-session.js").as_uri())
    shell_state = json.dumps((JS / "shell-state.js").as_uri())
    gui_mode = json.dumps((JS / "gui-mode.js").as_uri())
    out = run_dom_node(f"""
      const saved = new Map();
      storage.getItem = (key) => saved.get(key) || null;
      storage.setItem = (key, value) => saved.set(key, value);
      const shell = await import({shell_state});
      shell.writeNewSessionMode("single");
      const {{ setCurrentMode }} = await import({gui_mode});
      setCurrentMode("advanced");
      const {{ render, html }} = await import({preact});
      const {{ NewSession }} = await import({component});
      const calls = [];
      const reply = (payload) => ({{ ok: true, status: 200, json: async () => payload }});
      globalThis.fetch = async (path, options = {{}}) => {{
        const method = options.method || "GET";
        const body = options.body ? JSON.parse(options.body) : null;
        calls.push({{ method, path: String(path), body }});
        if (method === "POST" && path === "/gui/api/runs/preview") return reply({{
          routing: {{ steps: [], catalog_models: [] }},
          steps: [], missing_inputs: [], empty_optional_inputs: [],
        }});
        if (path === "/gui/api/offices") return reply({{ offices: [] }});
        if (path === "/gui/api/bundles") return reply([]);
        if (path === "/gui/api/workflows") return reply([{{ id: "ask_once", bundle: null }}]);
        if (path === "/gui/api/workflows/ask_once/inputs") return reply({{
          id: "ask_once", bundle: null, inputs: [
            {{ name: "task", label: "Task", description: "The task to answer.", example: "Explain photosynthesis.",
              required: true, type: "long_text", choices: [], source: "declared", context: false,
              used_in: ["answer"], needed_by: ["answer"], skipped_only: false, prompt_line: "Task: {{task}}" }},
          ], steps: [],
        }});
        if (path === "/gui/api/connections") return reply({{ connections: [
          {{ id: "ollama", enabled: true, available: true }},
        ] }});
        if (path === "/gui/api/runs?limit=50") return reply([]);
        throw new Error("Unexpected fake API request: " + method + " " + path);
      }};
      location._hash = "#new";
      const root = document.createElement("div");
      render(html`<${{NewSession}} token="fake" mock=${{true}} />`, root);
      await waitUntil(() => Boolean(allElements(root).find((node) => hasClass(node, "ns-plus"))),
        5000, "single-session plus menu");
      const wrapperCount = allElements(root).filter((node) => hasClass(node, "ns")).length;
      const plus = allElements(root).find((node) => hasClass(node, "ns-plus"));
      plus.emit("click", {{ target: plus }});
      await waitUntil(() => allElements(root).some((node) =>
        node.localName === "button" && node.textContent.includes("Ver como formulario")),
        5000, "form option");
      const form = allElements(root).find((node) =>
        node.localName === "button" && node.textContent.includes("Ver como formulario"));
      form.emit("click", {{ target: form }});
      await waitUntil(() => calls.some((call) => call.path === "/gui/api/workflows/ask_once/inputs"),
        5000, "system workflow inputs request");
      console.log(JSON.stringify({{
        workflowList: calls.some((call) => call.path === "/gui/api/workflows"),
        workflowInputs: calls.some((call) => call.path === "/gui/api/workflows/ask_once/inputs"),
        bundleQuery: calls.some((call) => call.path.includes("bundle=")), wrapperCount,
      }}));
    """)
    assert out == {"workflowList": True, "workflowInputs": True, "bundleQuery": False, "wrapperCount": 1}
