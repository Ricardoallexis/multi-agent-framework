# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""RWD-T02: master-detail in Runs, pending action first, fixed phone action bar, and chat shortcut."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from multiagent.gui import STATIC_DIR
from test_gui_rwd import PHONE_QUERY, all_media, read_sheet
from test_gui2_layout import rule

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

TOUCHED_SCRIPTS = ("runs-layout.js", "runs.js", "run-detail.js", "human-actions.js", "help-text.js")
FORBIDDEN = [
    (r"\binnerHTML\b", "innerHTML"),
    (r"\bouterHTML\b", "outerHTML"),
    (r"insertAdjacentHTML", "insertAdjacentHTML"),
    (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML"),
    (r"__html", "__html"),
    (r"\beval\s*\(", "eval"),
    (r"\bnew\s+Function\b", "Function"),
    (r"document\.write", "document.write"),
    (r"style=", "inline style"),
]


def _run_node(script: str) -> dict:
    uri = json.dumps((JS / "runs-layout.js").as_uri())
    done = subprocess.run(
        [NODE, "--input-type=module", "-e", f"import * as m from {uri};\n{script}"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_runs_layout_module_is_pure_and_free_of_dom_or_network():
    src = (JS / "runs-layout.js").read_text(encoding="utf-8")
    assert not re.search(r"^import\b", src, re.M)
    for forbidden in ("fetch(", "apiGet", "apiPost", "document.", "window.", "localStorage"):
        assert forbidden not in src, forbidden


@needs_node
def test_runs_layout_master_detail_and_action_first_rules():
    out = _run_node("""
      console.log(JSON.stringify({
        phoneNoSel: m.computeRunsLayout({ width: 390, hasSelection: false, waiting: false }),
        phone360NoSel: m.computeRunsLayout({ width: 360, hasSelection: false, waiting: false }),
        phoneWithSel: m.computeRunsLayout({ width: 390, hasSelection: true, waiting: false }),
        phoneCreating: m.computeRunsLayout({ width: 390, hasSelection: false, creating: true }),
        phoneWaiting: m.computeRunsLayout({ width: 390, hasSelection: true, waiting: true }),
        phoneTerminalWaiting: m.computeRunsLayout({ width: 390, hasSelection: true, waiting: true, terminal: true }),
        tabletWithSel: m.computeRunsLayout({ width: 768, hasSelection: true, waiting: true }),
        desktopNoSel: m.computeRunsLayout({ width: 1200, hasSelection: false, waiting: false }),
        desktopWaiting: m.computeRunsLayout({ width: 1200, hasSelection: true, waiting: true }),
        reasons: {
          review: m.waitingReasonLabel({ reason: "review", step_id: "create" }),
          tool: m.waitingReasonLabel({ reason: "tool_approval", step_id: "write" }),
          human: m.waitingReasonLabel({ reason: "execute_step", step_id: "draft" }),
          research: m.waitingReasonLabel({ reason: "research", step_id: "" }),
          empty: m.waitingReasonLabel(null),
        },
      }));
    """)

    assert out["phoneNoSel"]["showList"] is True
    assert out["phoneNoSel"]["showDetail"] is False
    assert out["phoneNoSel"]["showBack"] is False
    assert out["phone360NoSel"]["showList"] is True and out["phone360NoSel"]["showDetail"] is False

    assert out["phoneWithSel"]["showList"] is False
    assert out["phoneWithSel"]["showDetail"] is True
    assert out["phoneWithSel"]["showBack"] is True
    assert out["phoneWithSel"]["actionFirst"] is False
    assert out["phoneWithSel"]["actionBar"] is False

    assert out["phoneCreating"]["showList"] is False and out["phoneCreating"]["showDetail"] is True

    assert out["phoneWaiting"]["showList"] is False
    assert out["phoneWaiting"]["showDetail"] is True
    assert out["phoneWaiting"]["actionFirst"] is True
    assert out["phoneWaiting"]["actionBar"] is True
    assert out["phoneWaiting"]["actionPosition"] == "bottom-bar"

    assert out["phoneTerminalWaiting"]["actionFirst"] is False
    assert out["phoneTerminalWaiting"]["actionBar"] is False

    assert out["tabletWithSel"]["showList"] is False
    assert out["tabletWithSel"]["showDetail"] is True
    assert out["tabletWithSel"]["actionFirst"] is True
    assert out["tabletWithSel"]["actionBar"] is False

    assert out["desktopNoSel"]["showList"] is True and out["desktopNoSel"]["showDetail"] is True
    assert out["desktopNoSel"]["showBack"] is False
    assert out["desktopWaiting"]["showList"] is True and out["desktopWaiting"]["showDetail"] is True
    assert out["desktopWaiting"]["actionFirst"] is True
    assert out["desktopWaiting"]["actionBar"] is False
    assert out["desktopWaiting"]["actionPosition"] == "top"

    assert out["reasons"]["review"] == "Esperando tu revisión · paso «create»"
    assert out["reasons"]["tool"] == "Esperando aprobación de herramienta · paso «write»"
    assert out["reasons"]["human"] == "Paso manual pendiente · paso «draft»"
    assert out["reasons"]["research"] == "Investigación externa pendiente"
    assert out["reasons"]["empty"] == ""


def test_master_detail_action_order_fixed_bar_and_chat_shortcut():
    runs = (JS / "runs.js").read_text(encoding="utf-8")
    detail = (JS / "run-detail.js").read_text(encoding="utf-8")
    actions = (JS / "human-actions.js").read_text(encoding="utf-8")
    help_text = (JS / "help-text.js").read_text(encoding="utf-8")

    assert "computeRunsLayout(" in runs
    assert "hidden=${!layout.showList}" in runs and "hidden=${!layout.showDetail}" in runs
    assert "onBack=${layout.showBack ? goBack : null}" in runs

    assert "Volver a la lista" in detail and 'helpFor("runs.back")' in detail
    assert "threadHref" in detail and "Abrir en el chat" in detail and 'helpFor("run.open_chat")' in detail
    assert "has-action-bar" in detail
    # Waiting runs put HumanActions before Steps and Results; non-waiting runs put it after Results.
    assert detail.index("${layout.actionFirst && actionsCard}") < detail.index("<${Steps}")
    assert detail.index("<${Results}") < detail.index("${!layout.actionFirst && actionsCard}")

    assert "waitingReasonLabel(waiting)" in actions
    assert 'class="human-action-bar"' in actions
    assert 'helpFor("human.bar_focus")' in actions

    for key in ('"runs.back":', '"run.open_chat":', '"human.bar_focus":'):
        assert key in help_text


def test_fixed_bar_reserves_bottom_padding_and_scripts_are_safe():
    raw_css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "RWD-T02" in raw_css
    app = read_sheet(STATIC_DIR / "app.css")
    phone = all_media(app, PHONE_QUERY)
    detail_pad = rule(phone, ".run-detail.has-action-bar")
    assert "padding-bottom:" in detail_pad
    bar_rule = rule(phone, ".human-action-bar")
    assert "position: fixed" in bar_rule and "bottom: 0" in bar_rule

    for name in TOUCHED_SCRIPTS:
        src = (JS / name).read_text(encoding="utf-8")
        for pattern, label in FORBIDDEN:
            assert not re.search(pattern, src), f"{name}: {label}"


@needs_node
@pytest.mark.parametrize("name", TOUCHED_SCRIPTS)
def test_touched_scripts_pass_node_check(name):
    result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (name, result.stderr)
