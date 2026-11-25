# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T06: one entry box, remembered mode and confirmation-only launches (Node, no TestClient)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR


JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=60, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_mode_is_remembered_and_blocked_storage_falls_back_to_orchestrator():
    uri = json.dumps((JS / "shell-state.js").as_uri())
    out = run_node(f"""
      import * as state from {uri};
      const values = new Map();
      globalThis.window = {{localStorage: {{getItem: (key) => values.get(key) || null,
        setItem: (key, value) => values.set(key, value)}}}};
      const first = state.readNewSessionMode();
      state.writeNewSessionMode('single');
      const saved = state.readNewSessionMode();
      const key = [...values.keys()][0];
      window.localStorage = {{getItem: () => {{throw Error('blocked');}}, setItem: () => {{throw Error('blocked');}}}};
      state.writeNewSessionMode('single');
      console.log(JSON.stringify({{first, saved, blocked: state.readNewSessionMode(), key}}));
    """)
    assert out == {"first": "orchestrator", "saved": "single", "blocked": "orchestrator",
                   "key": "multiagent.gui.new-session-mode"}


@needs_node
def test_model_selector_counts_disconnected_without_selecting_them():
    uri = json.dumps((JS / "model-picker-model.js").as_uri())
    out = run_node(f"""
      Object.defineProperty(globalThis, 'navigator', {{value: {{language: 'es-MX'}}, configurable: true}});
      const {{buildModelPicker}} = await import({uri});
      const empty = buildModelPicker({{connections: []}});
      const listed = buildModelPicker({{connections: [
        {{id:'ollama', enabled:true, available:true}},
        {{id:'gemini', enabled:true, available:false, reason:'limit_reached'}},
        {{id:'codex_cli', enabled:false, available:false, missing_step:'enable'}}
      ]}}, 'gemini');
      console.log(JSON.stringify({{empty: empty.models.length, models: listed.models.map(x=>x.id),
        disconnected: listed.disconnected.length, selected: listed.selectedId,
        limit: listed.disconnected[0].reason}}));
    """)
    assert out["empty"] == 0
    assert out["models"] == ["ollama"] and out["disconnected"] == 2 and out["selected"] is None
    assert "Límite alcanzado" in out["limit"]


@needs_node
def test_project_selector_prefills_but_still_asks_and_run_requires_confirmation():
    uri = json.dumps((JS / "new-session-model.js").as_uri())
    out = run_node(f"""
      Object.defineProperty(globalThis, 'navigator', {{value: {{language: 'es-MX'}}, configurable: true}});
      const m = await import({uri});
      const picked = m.reduce(m.createConversation(), {{type:'pick', workflow:{{id:'sample',bundle:'general'}},
        spec:{{inputs:[]}}, project:'Proyecto reciente'}});
      const answered = m.reduce(picked, {{type:'send',text:'Proyecto reciente'}});
      const cleared = m.reduce(answered, {{type:'project', project:''}});
      console.log(JSON.stringify({{phase:picked.phase, question:m.currentQuestion(picked)?.key,
        prefill:picked.answers[m.PROJECT_KEY], after:answered.phase,
        project:m.runBody(answered).project_name, cleared:cleared.phase}}));
    """)
    assert out == {"phase": "ask", "question": "@project", "prefill": "Proyecto reciente",
                   "after": "confirm", "project": "Proyecto reciente", "cleared": "ask"}


@needs_node
def test_one_box_menu_and_limit_banners_stay_visible_in_each_mode():
    view = ui_text((JS / "new-session.js"))
    picker = ui_text((JS / "model-picker.js"))
    css = (STATIC_DIR / "new-session.css").read_text(encoding="utf-8")
    assert view.count('<textarea id="ns-composer-input"') == 1
    assert 'id="single-task"' not in view and 'id="single-executor-task"' not in view
    assert "¿Cómo quieres empezar?" not in view
    assert 'aria-label="Más opciones"' in view and "Usar un ejecutor" in view and "Ver como formulario" in view
    assert "adjunto" not in view.lower() and "attachment" not in view.lower()
    assert 'value="connect"' in picker and "Conectar otro… (" in picker
    assert 'Modo MOCK: respuestas sintéticas, sin modelos.' in view
    assert view.count("<${LimitBanner} notice=${activeLimit}") == 2
    assert view.count("<${LimitBanner} notice=${activeLimitNotice}") == 1      # VOF-T03: the office filters the notice
    assert "ns-box-controls" in css and "ns-plus-menu" in css
    assert 'onClick=${launch}>${busy ? "Lanzando…" : "Aprobar y lanzar"}' in view


def test_escape_closes_the_plus_menu_from_its_button_too():
    # Esc closes the «＋» menu with the focus on the button as well as inside the menu, and gives the focus back to «＋».
    view = ui_text((JS / "new-session.js"))
    wrap = view[view.index('<div class="ns-plus-wrap"'):view.index('class="ns-plus"')]
    assert 'event.key === "Escape" && menuOpen' in wrap and "menuButton.current?.focus()" in wrap
    menu = view[view.index('class="ns-plus-menu"'):view.index('</div>`}', view.index('class="ns-plus-menu"'))]
    assert "onKeyDown" not in menu


def test_box_selects_line_up_by_their_top():
    # «Modelo» can show a line under its select («Sin conectar (N)», a notice): the row lines up by the top, so the three
    # selects stay level, and «＋» and «Enviar» drop by the height of a label to sit level with them.
    css = (STATIC_DIR / "new-session.css").read_text(encoding="utf-8")
    row = re.search(r"\.ns-box-controls \{([^}]*)\}", css).group(1)
    assert "align-items: start" in row and "align-items: end" not in row
    assert "line-height: 1.25rem" in re.search(r"\.ns-box-select > label, \.ns-model-select > label \{([^}]*)\}", css).group(1)
    assert "margin-top: calc(1.25rem + 3px)" in re.search(r"\.ns-plus-wrap \{([^}]*)\}", css).group(1)
    assert "margin-top: calc(1.25rem + 3px)" in re.search(r"\.ns-box-controls \.ns-send \{([^}]*)\}", css).group(1)
