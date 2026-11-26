# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T09: the right-panel cards live inside the four context groups."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def uri(path) -> str:
    return json.dumps(path.as_uri())


def run_node(source: str):
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_context_groups_are_ordered_and_keep_the_existing_slots():
    panel = (JS / "right-panel.js").read_text(encoding="utf-8")
    groups = ["where", "conversation", "outputs", "in_progress"]
    starts = [panel.index(f'<${{ContextGroup}} title=${{t("panel.{name}_heading")}}') for name in groups]
    assert starts == sorted(starts)
    ends = starts[1:] + [panel.index("${extra.map")]
    blocks = [panel[start:end] for start, end in zip(starts, ends)]
    assert "where.map" in blocks[0]
    assert '<${TokensSlot} ...${slot} />' in blocks[1]
    assert '<${ChangesSlot} ...${slot} />' in blocks[2] and '<${SourcesSlot} ...${slot} />' in blocks[2]
    assert '<${SubagentsSlot} ...${slot} onContent=${reporter("subagents")} />' in blocks[3]
    assert '<div class="rp-progress-slot" aria-hidden="true"></div>' in blocks[3]

    slots = (JS / "panel-slots.js").read_text(encoding="utf-8")
    for name in ("changes", "subagents", "tokens", "sources"):
        assert f'data-slot="{name}"' in slots
        assert f'id="panel-{name}-title"' in panel
    assert 'id="subagents-slot" class="slot" data-slot="subagents"' in slots
    assert '<${SubagentsSlot} ...${slot} onContent=${reporter("subagents")} />' in panel
    assert '<${ChangesSlot} ...${slot} />' in panel and '<${SourcesSlot} ...${slot} />' in panel
    assert '<${TokensSlot} ...${slot} />' in panel
    assert 'shown=${groupShown(shown, ["changes", "sources"])}' in panel
    assert 'shown=${groupShown(shown, ["subagents"])}' in panel


@needs_node
def test_a_group_is_hidden_when_all_its_cards_are_hidden():
    out = run_node(f"""
      import {{ groupShown }} from {uri(JS / "context-model.js")};
      console.log(JSON.stringify([
        groupShown({{ changes: false, sources: false }}, ["changes", "sources"]),
        groupShown({{ changes: false, sources: true }}, ["changes", "sources"]),
        groupShown(null, ["changes"]),
      ]));
    """)
    assert out == [False, True, False]


@needs_node
def test_where_uses_only_the_current_subject_app_and_existing_folder():
    out = run_node(f"""
      Object.defineProperty(globalThis, "navigator", {{ value: {{ language: "en-US" }}, configurable: true }});
      const {{ contextWhere }} = await import({uri(JS / "context-where.js")});
      const session = {{ kind: "session", sessionId: "claude:private-session", key: "session:claude:private-session" }};
      const antigravity = {{ kind: "session", sessionId: "antigravity:private-session", key: "session:antigravity:private-session" }};
      const run = {{ kind: "run", runId: "run-1", sessionId: "framework:run-1", key: "run:run-1" }};
      const english = {{
        session: contextWhere(session, {{ summary: {{ cwd: "D:\\\\work\\\\repo" }} }}),
        antigravity: contextWhere(antigravity, {{ summary: {{ cwd: "" }} }}),
        noFolder: contextWhere(session, {{ summary: {{ cwd: "" }} }}),
        run: contextWhere(run, {{ run: {{ workflow_id: "private-workflow" }} }}),
        none: contextWhere(null, {{}}),
      }};
      Object.defineProperty(globalThis, "navigator", {{ value: {{ language: "es-MX" }}, configurable: true }});
      console.log(JSON.stringify({{ ...english, spanish: contextWhere(session, {{ summary: {{ cwd: "" }} }}) }}));
    """)
    assert out["session"] == [{"label": "App", "value": "Claude"}, {"label": "Work folder", "value": "D:\\work\\repo"}]
    assert out["antigravity"] == [{"label": "App", "value": "Antigravity"}]
    assert out["noFolder"] == [{"label": "App", "value": "Claude"}]
    assert out["run"] == [{"label": "App", "value": "Workflows"}]
    assert out["none"] == []
    assert out["spanish"] == [{"label": "Aplicación", "value": "Claude"}]


def test_simple_tokens_collapse_to_one_line_without_removing_advanced_details():
    panel = (JS / "right-panel.js").read_text(encoding="utf-8")
    tokens = (JS / "panel-tokens.js").read_text(encoding="utf-8")
    sheet = (STATIC_DIR / "panels.css").read_text(encoding="utf-8")
    assert 'data-mode=${currentMode()}' in panel and 't("panel.simple_tokens_line"' in tokens
    assert 'total: totalText' in tokens
    assert 'model.byModel.length > 0' in tokens and 'cost.evidence.length > 0' in tokens
    assert ".ctx-summary-simple { display: none; }" in sheet
    for selector in (".ctx-summary-advanced", ".ctx-tokens > .ctx-list", ".ctx-tokens > .ctx-cost",
                     ".ctx-tokens > .ctx-note", ".ctx-tokens > .ctx-details-row"):
        assert f'.rp[data-mode="simple"] {selector}' in sheet
