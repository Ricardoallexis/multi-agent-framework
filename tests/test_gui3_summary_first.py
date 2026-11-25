# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T08: summary-first run detail tabs and collapsible rows in Apps y agentes."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from test_gui_rwd import PHONE_QUERY, all_media, read_sheet
from test_gui2_layout import rule

JS = STATIC_DIR / "js"
EXT_APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

TOUCHED_JS = [
    JS / "runs-layout.js",
    JS / "run-detail.js",
    JS / "help-text.js",
    EXT_APPS / "index.js",
    EXT_APPS / "model.js",
    EXT_APPS / "help.js",
]


def _run_node(body: str) -> dict:
    done = subprocess.run([NODE, "--input-type=module", "-e", body], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
@pytest.mark.parametrize("path", TOUCHED_JS, ids=lambda p: p.relative_to(STATIC_DIR).as_posix())
def test_touched_scripts_pass_node_check(path):
    res = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, (path.name, res.stderr)


@needs_node
def test_run_detail_tabs_layout_and_sections():
    script = (
        f'import {{ DEFAULT_RUN_TAB, RUN_DETAIL_TABS, RUN_TAB_SECTIONS, resolveRunTab, computeRunsLayout }} from {json.dumps((JS / "runs-layout.js").as_uri())};\n'
        f'import {{ RunTabContent }} from {json.dumps((JS / "run-detail.js").as_uri())};\n'
        + """
      const collectNames = (vnode) => {
        if (vnode == null || typeof vnode === "boolean" || typeof vnode === "string" || typeof vnode === "number") return [];
        if (Array.isArray(vnode)) return vnode.flatMap(collectNames);
        if (typeof vnode.type === "function") return [vnode.type.name];
        return collectNames(vnode.props?.children);
      };

      const view = {
        id: "r1",
        workflow_id: "wf",
        project_name: "Demo",
        status: "completed",
        terminal: true,
        steps: [],
        counters: { model_calls: 0, max_model_calls: 4, human_steps: 0, steps_done: 0, steps_total: 0, steps_skipped: 0, active_seconds: 0, max_seconds: 60 },
        history: [],
        artifacts: [],
        inputs: { topic: "hola" },
        events: [],
        events_total: 0,
      };

      console.log(JSON.stringify({
        defaultTab: DEFAULT_RUN_TAB,
        tabs: RUN_DETAIL_TABS,
        sections: RUN_TAB_SECTIONS,
        resolved: [resolveRunTab(undefined), resolveRunTab("bad"), resolveRunTab("results"), resolveRunTab("details")],
        tabSteps: collectNames(RunTabContent({ tab: "steps", view, token: "t" })),
        tabResults: collectNames(RunTabContent({ tab: "results", view, token: "t" })),
        tabDetails: collectNames(RunTabContent({ tab: "details", view, token: "t" })),
        waitingDesktop: computeRunsLayout({ width: 1200, hasSelection: true, waiting: true }),
        doneDesktop: computeRunsLayout({ width: 1200, hasSelection: true, waiting: false }),
        waitingPhone: computeRunsLayout({ width: 390, hasSelection: true, waiting: true }),
      }));
    """
    )
    out = _run_node(script)

    assert out["defaultTab"] == "steps"
    assert out["tabs"] == [
        {"id": "steps", "label": "Pasos"},
        {"id": "results", "label": "Resultados"},
        {"id": "details", "label": "Detalles"},
    ]
    assert out["sections"] == {
        "steps": ["steps", "counters"],
        "results": ["results"],
        "details": ["history", "inputs", "technical"],
    }
    assert out["resolved"] == ["steps", "steps", "results", "details"]
    assert out["tabSteps"] == ["Steps", "Counters"]
    assert out["tabResults"] == ["Results"]
    assert out["tabDetails"] == ["History", "RunInputs", "Technical"]
    assert out["waitingDesktop"]["actionFirst"] is True and out["waitingDesktop"]["actionBar"] is False
    assert out["doneDesktop"]["actionFirst"] is False and out["doneDesktop"]["actionBar"] is False
    assert out["waitingPhone"]["actionFirst"] is True and out["waitingPhone"]["actionBar"] is True


def test_run_detail_uses_tabs_without_new_routes_or_conversation_tab():
    detail = ui_text((JS / "run-detail.js"))
    route = ui_text((JS / "route.js"))

    assert 'import { Tabs } from "./ui/tabs.js";' in detail
    assert '<${Tabs} id="rundetail"' in detail
    assert 'helpFor("run.tabs")' in detail
    assert 'topic="run.tabs"' in detail
    assert "DEFAULT_RUN_TAB" in detail
    assert "Conversación" not in detail
    assert 'threadHref(view.id)' in detail and "Abrir en el chat" in detail
    # Limit notice and HumanActions when waiting sit above Tabs; non-waiting HumanActions sit below Tabs.
    assert detail.index("Límite alcanzado") < detail.index("<${Tabs}")
    assert detail.index("${layout.actionFirst && actionsCard}") < detail.index("<${Tabs}")
    assert detail.index("<${Tabs}") < detail.index("${!layout.actionFirst && actionsCard}")
    # Tab selection is local state, never written to location.hash or route.js.
    assert "location.hash" not in detail
    assert "rundetail" not in route


@needs_node
def test_apps_rows_open_only_for_pending_step_and_collapsed_rows_keep_status_and_missing_line():
    out = _run_node(f"""
      import {{ buildAppsView, isPendingStep }} from {json.dumps((EXT_APPS / "model.js").as_uri())};

      const view = buildAppsView({{
        connections: [
          {{ id: "claude_cli", kind: "executor", installed: false, enabled: false, available: false, reason: "not_installed", missing_step: "install" }},
          {{ id: "codex_cli", kind: "executor", installed: true, session: "signed_out", enabled: true, available: false, reason: "session_missing", missing_step: "session" }},
          {{ id: "copilot_cli", kind: "executor", installed: true, session: "unknown", enabled: true, available: false, reason: "session_unverifiable" }},
          {{ id: "antigravity_cli", kind: "executor", installed: true, session: "signed_in", enabled: true, notice_accepted: true, available: true }},
          {{ id: "gemini", kind: "model", key_configured: true, enabled: true, available: true, verification: "unverified" }},
          {{ id: "openai", kind: "model", key_configured: false, enabled: false, available: false, reason: "key_missing", missing_step: "key" }},
          {{ id: "ollama", kind: "model", reachable: true, enabled: true, available: true }},
        ],
      }}, {{
        allow_launch: true,
        console_supported: true,
        permissions: {{}},
        plans: {{
          claude_cli: {{ install: {{ command: ["winget", "install", "Anthropic.ClaudeCode"] }} }},
          codex_cli: {{ login: {{ command: ["codex", "login"] }} }},
        }},
      }});

      const byId = Object.fromEntries([...view.executors, ...view.models].map((row) => [row.id, {{
        defaultOpen: row.defaultOpen,
        chip: row.chip.label,
        missingLine: row.missingLine,
        pending: isPendingStep({{ available: row.available, missing_step: row.defaultOpen ? "x" : null }}),
      }}]));
      console.log(JSON.stringify(byId));
    """)

    assert out["claude_cli"] == {"defaultOpen": True, "chip": "Falta un paso", "missingLine": "Falta instalarlo", "pending": True}
    assert out["codex_cli"] == {"defaultOpen": True, "chip": "Falta un paso", "missingLine": "Falta iniciar sesión", "pending": True}
    assert out["openai"] == {"defaultOpen": True, "chip": "Falta un paso", "missingLine": "Falta configurar la clave", "pending": True}
    assert out["copilot_cli"] == {"defaultOpen": False, "chip": "Sin verificar", "missingLine": "Sesión sin comprobar", "pending": False}
    assert out["antigravity_cli"] == {"defaultOpen": False, "chip": "Listo", "missingLine": None, "pending": False}
    assert out["gemini"] == {"defaultOpen": False, "chip": "Sin comprobar", "missingLine": None, "pending": False}
    assert out["ollama"] == {"defaultOpen": False, "chip": "Listo", "missingLine": None, "pending": False}


def test_apps_collapsible_card_markup_targets_and_copy_console_untouched():
    apps_js = ui_text((EXT_APPS / "index.js"))
    apps_css = read_sheet(EXT_APPS / "style.css")

    assert '<details class="apps-card-details"' in apps_js
    assert '<summary class="apps-card-summary">' in apps_js
    assert "item.missingLine" in apps_js
    assert "useState(Boolean(item.defaultOpen))" in apps_js
    assert "navigator.clipboard.writeText(text)" in apps_js
    assert 'apiGet("/gui/api/setup/console", token)' in apps_js
    assert 'apiPost("/gui/api/setup/console", token' in apps_js
    # The registration keeps its route, label, help and order; GUI3-T03 added `group` and `icon` at its end, SIM-T02 `mode`.
    assert ('registerView({ id: "apps", label: "Apps y agentes", topic: "apps.view", component: AppsView, order: 70, '
            'owner: "apps", group: "primary", icon: "apps", mode: "simple" });') in apps_js

    summary_rule = rule(apps_css, ".apps-card-summary")
    assert "min-height: var(--target)" in summary_rule
    phone_css = all_media(apps_css, PHONE_QUERY)
    assert "padding: 10px 12px" in rule(phone_css, ".apps-card-summary")
    assert "url(" not in apps_css and "@import" not in apps_css and "style=" not in apps_js
