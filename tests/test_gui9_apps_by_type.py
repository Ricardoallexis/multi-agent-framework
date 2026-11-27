# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T11: Apps and agents tabs, option grouping, and stale activation refresh."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SPANISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'es-MX'},configurable:true})",
]


def run_node(source: str):
    result = subprocess.run(
        [NODE, *SPANISH_NAVIGATOR, "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def component_module(tmp_path):
    source = (APPS / "index.js").read_text(encoding="utf-8")
    source = re.sub(r"^import .*?;[^\r\n]*\r?\n", "", source, flags=re.M)
    source = source.replace("export function AppsView(", "function AppsView(")
    prefix = f"""
import {{ html }} from {json.dumps((STATIC_DIR / "vendor" / "htm-preact-standalone.module.js").as_uri())};
import {{ t }} from {json.dumps((STATIC_DIR / "js" / "i18n.js").as_uri())};
import {{ buildAppsView, canShowAllReady, groupModelsByType }} from {json.dumps((APPS / "model.js").as_uri())};
const useState = (initial) => {{
  const value = globalThis.__states[globalThis.__hook++];
  return [value === undefined ? initial : value, () => {{}}];
}};
const useEffect = () => {{}};
const useRef = (initial) => ({{ current: initial }});
class ApiError extends Error {{}}
const apiGet = async () => ({{}});
const apiPost = async () => ({{}});
const registerHelp = () => {{}};
const registerView = () => {{}};
const currentMode = () => globalThis.__mode || "simple";
const Help = () => null;
const helpFor = () => ({{}});
const errorText = (error) => String(error);
const fmtDate = (value) => value;
const StatusChip = () => null;
const Tabs = () => null;
const HELP = {{}};
const activationNote = () => ({{}});
const stopAllConfirmText = () => "";
const AgentsSection = () => null;
const activateExecutor = async () => globalThis.__activationOutcome;
const deactivateExecutor = async () => ({{}});
const requestActivation = async () => globalThis.__requestActivationOutcome || ({{}});
const stopAllExecutors = async () => ({{}});
"""
    path = tmp_path / "apps-component.mjs"
    path.write_text(prefix + source + "\nexport { AppsView, ActivationPanel, MissingStep, StopAll, ToolCard };\n", encoding="utf-8")
    return path


@needs_node
def test_composer_options_group_models_and_paused_snapshot_is_not_ready():
    result = run_node(f"""
      const model = await import({json.dumps((APPS / "model.js").as_uri())});
      const connections = {{connections: [
        {{id: "codex_cli", kind: "executor", installed: true, session: "signed_in", enabled: true, available: true}},
        {{id: "gemini", kind: "model", enabled: true, available: true}},
        {{id: "openai", kind: "model", enabled: true, available: true}},
        {{id: "ollama", kind: "model", enabled: true, available: true}},
      ]}};
      const options = {{options: [
        {{id: "codex_cli", kind: "app"}},
        {{id: "gemini", kind: "cloud"}},
        {{id: "openai", kind: "cloud"}},
        {{id: "ollama", kind: "local"}},
      ]}};
      const paused = model.buildAppsView(connections, null, "simple",
        {{executors: [{{id: "codex_cli", state: "paused"}}]}});
      const active = model.buildAppsView(connections, null, "simple",
        {{executors: [{{id: "codex_cli", state: "active"}}]}});
      const groups = model.groupModelsByType(paused.models, options);
      console.log(JSON.stringify({{
        local: groups.localModels.map((item) => item.id),
        cloud: groups.cloudModels.map((item) => item.id),
        pausedSaysReady: model.canShowAllReady(paused.executors[0]),
        activeSaysReady: model.canShowAllReady(active.executors[0]),
      }}));
    """)
    assert result == {
        "local": ["ollama"],
        "cloud": ["gemini", "openai"],
        "pausedSaysReady": False,
        "activeSaysReady": True,
    }


@needs_node
def test_tabs_open_pending_cards_fold_others_and_refresh_activation_conflicts(tmp_path):
    module = component_module(tmp_path)
    result = run_node(f"""
      const mod = await import({json.dumps(module.as_uri())});
      const data = {{connections: [
        {{id: "codex_cli", kind: "executor", installed: true, session: "signed_in", enabled: true, available: true}},
        {{id: "claude_cli", kind: "executor", installed: false, enabled: false, available: false,
          reason: "not_installed", missing_step: "install"}},
        {{id: "gemini", kind: "model", enabled: true, available: false, missing_step: "key"}},
        {{id: "openai", kind: "model", enabled: true, available: true}},
        {{id: "ollama", kind: "model", enabled: true, available: true}},
      ]}};
      const activations = {{executors: [{{id: "codex_cli", state: "active"}}]}};
      const options = {{options: [
        {{id: "codex_cli", kind: "app"}}, {{id: "gemini", kind: "cloud"}},
        {{id: "openai", kind: "cloud"}}, {{id: "ollama", kind: "local"}},
      ]}};
      const nodes = (root) => {{
        const found = [];
        const walk = (node) => {{
          if (Array.isArray(node)) return node.forEach(walk);
          if (!node || typeof node !== "object") return;
          found.push(node);
          walk(node.props && node.props.children);
        }};
        walk(root);
        return found;
      }};
      const rendered = [];
      for (const [mode, tab, loading] of [
        ["simple", "apps", false], ["simple", "apps", true], ["simple", "local", false],
        ["simple", "cloud", false], ["advanced", "apps", false], ["advanced", "local", false],
        ["advanced", "cloud", false],
      ]) {{
        globalThis.__mode = mode;
        globalThis.__states = [data, {{}}, loading, null, null, false, activations, options, null, tab];
        globalThis.__hook = 0;
        const tree = mod.AppsView({{token: "test"}});
        const found = nodes(tree);
        const tabs = found.find((node) => node.type && node.type.name === "Tabs");
        rendered.push({{
          mode, tab, loading,
          tabLabels: tabs.props.tabs.map((item) => item.label),
          cards: found.filter((node) => node.type === mod.ToolCard).map((node) => node.props.item),
          stopBeforeTabs: (() => {{
            const stop = found.findIndex((node) => node.type === mod.StopAll);
            return stop >= 0 && stop < found.indexOf(tabs);
          }})(),
        }});
      }}
      globalThis.__states = ["note", {{title: "Confirm", lines: ["Line"], confirmLabel: "Confirm"}},
        {{approval_id: "approval"}}, null];
      globalThis.__activationOutcome = {{ok: false, restart: true}};
      globalThis.__hook = 0;
      const changed = [];
      const panel = mod.ActivationPanel({{
        item: {{id: "codex_cli", activation: {{state: "ready", actions: ["activate"], detail: ""}}}},
        token: "test", onChanged: (id) => changed.push(id),
      }});
      const accept = nodes(panel).find((node) => node.type === "button").props.onClick;
      await accept();
      globalThis.__states = ["idle", null, null, null];
      globalThis.__requestActivationOutcome = {{ok: false, refresh: true, message: "changed"}};
      globalThis.__hook = 0;
      const requestChanged = [];
      const requestPanel = mod.ActivationPanel({{
        item: {{id: "codex_cli", activation: {{state: "ready", actions: ["activate"], detail: ""}}}},
        token: "test", onChanged: (id) => requestChanged.push(id),
      }});
      const request = nodes(requestPanel).find((node) => node.type === "button").props.onClick;
      await request();
      console.log(JSON.stringify({{rendered: rendered.map((item) => ({{
        mode: item.mode, tab: item.tab, loading: item.loading, tabLabels: item.tabLabels,
        cards: item.cards.map((card) => [card.id, card.defaultOpen]),
        stopBeforeTabs: item.stopBeforeTabs,
      }})), changed, requestChanged}}));
    """)
    assert [entry["tabLabels"] for entry in result["rendered"]] == [
        ["Apps en tu PC", "En tu PC (local)", "Servicios en la nube"],
        ["Apps en tu PC", "En tu PC (local)", "Servicios en la nube"],
        ["Apps en tu PC", "En tu PC (local)", "Servicios en la nube"],
        ["Apps en tu PC", "En tu PC (local)", "Servicios en la nube"],
        ["Apps (CLI)", "Locales", "API"],
        ["Apps (CLI)", "Locales", "API"],
        ["Apps (CLI)", "Locales", "API"],
    ]
    assert [entry["cards"] for entry in result["rendered"]] == [
        [["claude_cli", True], ["codex_cli", False]],
        [["claude_cli", True], ["codex_cli", False]],
        [["ollama", False]],
        [["gemini", True], ["openai", False]],
        [["claude_cli", True], ["codex_cli", False]],
        [["ollama", False]],
        [["gemini", True], ["openai", False]],
    ]
    assert all(entry["stopBeforeTabs"] for entry in result["rendered"])
    assert result["rendered"][1]["loading"] is True
    assert result["changed"] == ["codex_cli"]
    assert result["requestChanged"] == ["codex_cli"]


@needs_node
def test_missing_step_only_says_all_ready_for_models_or_active_executors(tmp_path):
    module = component_module(tmp_path)
    result = run_node(f"""
      const mod = await import({json.dumps(module.as_uri())});
      const collect = (root) => {{
        const text = [];
        const walk = (node) => {{
          if (Array.isArray(node)) return node.forEach(walk);
          if (typeof node === "string") return text.push(node);
          if (!node || typeof node !== "object") return;
          walk(node.props && node.props.children);
        }};
        walk(root);
        return text.join("");
      }};
      const item = (activation) => ({{available: true, action: null, activation}});
      const draw = (value) => mod.MissingStep({{item: value}});
      console.log(JSON.stringify({{
        paused: collect(draw(item({{state: "paused"}}))),
        model: collect(draw(item(null))),
        active: collect(draw(item({{state: "active"}}))),
      }}));
    """)
    assert "Todo listo para usar." not in result["paused"]
    assert "Todo listo para usar." in result["model"]
    assert "Todo listo para usar." in result["active"]


def test_apps_view_uses_read_only_options_route_and_help_for_tabs():
    source = (APPS / "index.js").read_text(encoding="utf-8")
    assert 'apiGet("/gui/api/composer/options", token)' in source
    assert 'className="apps-tabs"' in source
    assert 'helpFor("apps.executors")' in source and 'helpFor("apps.models")' in source
    assert 'key=${tab === "apps" ? "executors" : "models"}' in source
