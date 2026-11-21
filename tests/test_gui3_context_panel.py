# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T04: the «Contexto» panel is closed by default, offered only with a session or run, and draws the cards by mode,
content and urgency (Node, no server)."""
from __future__ import annotations

import json
import re
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


@needs_node
def test_cards_are_drawn_by_mode_content_and_urgency():
    out = run_node(f"""
      import {{ cardShown, shownCards, anyContent, modeOf, contentOf }} from {uri(JS / "context-model.js")};
      const table = {{}};
      for (const mode of ["auto", "always", "never"]) {{
        table[mode] = [cardShown(mode, {{ has: true }}), cardShown(mode, {{ has: false }}),
                       cardShown(mode, {{ has: false, urgent: true }}), cardShown(mode, undefined)];
      }}
      const cards = [{{ name: "changes" }}, {{ name: "task_waiting", defaultMode: "never" }}, {{ name: "ext" }}];
      const contents = {{ changes: {{ has: false }}, task_waiting: {{ has: true }} }};      // ext never reports
      const plain = shownCards(cards, {{}}, contents);
      const chosen = shownCards(cards, {{ changes: "always", task_waiting: "auto", ext: "never" }}, contents);
      const urgent = shownCards(cards, {{}}, {{ ...contents, task_waiting: {{ has: true, urgent: true }} }});
      console.log(JSON.stringify({{ table, plain, chosen, urgent,
        dot: [anyContent(cards, {{}}, contents), anyContent([cards[0]], {{ changes: "always" }}, contents),
              anyContent([cards[1]], {{}}, contents), anyContent([cards[1]], {{}}, {{ task_waiting: {{ has: true, urgent: true }} }})],
        modes: [modeOf(cards[1], {{}}), modeOf(cards[1], {{ task_waiting: "always" }}), modeOf(cards[1], {{ task_waiting: "x" }}),
                modeOf(cards[0], null)],
        completed: [contentOf(undefined), contentOf({{ urgent: true, has: false }})] }}));
    """)
    # auto: only with content; always: also empty; never: only what is urgent; a card that does not report has content
    assert out["table"]["auto"] == [True, False, True, True]
    assert out["table"]["always"] == [True, True, True, True]
    assert out["table"]["never"] == [False, False, True, False]
    assert out["plain"] == {"changes": False, "task_waiting": False, "ext": True}
    assert out["chosen"] == {"changes": True, "task_waiting": True, "ext": False}
    assert out["urgent"]["task_waiting"] is True                              # urgent shows in «Nunca» too
    assert out["dot"] == [True, False, False, True]                           # an empty card drawn by «Siempre» does not light the dot
    assert out["modes"] == ["never", "always", "never", "auto"]               # an invalid stored mode falls back to the registered one
    assert out["completed"] == [{"has": True, "urgent": False}, {"has": True, "urgent": True}]


@needs_node
def test_the_own_cards_calculate_their_content_from_their_data():
    out = run_node(f"""
      import {{ ownContents }} from {uri(JS / "context-model.js")};
      const session = {{ kind: "session", sessionId: "claude:1", key: "session:claude:1" }};
      const run = {{ kind: "run", runId: "r1", sessionId: "framework:r1", key: "run:r1" }};
      const view = (extra) => ({{ edits: [], reads: [], urls: [], searches: 0, attachments: 0, more: false, ...extra }});
      const ready = (extra) => ({{ status: "ready", summary: {{}}, children: null, view: view({{}}), ...extra }});
      const edit = {{ path: "a.py", edits: 1, additions: null, deletions: null, tools: [] }};
      console.log(JSON.stringify({{
        none: ownContents(null, ready({{}})),
        loading: ownContents(session, {{ status: "loading" }}),
        empty: ownContents(session, ready({{}})),
        edits: ownContents(session, ready({{ view: view({{ edits: [edit] }}) }})),
        reads: ownContents(session, ready({{ view: view({{ searches: 1 }}) }})),
        tokens: ownContents(session, ready({{ summary: {{ tokens: {{ input: 5, output: 2 }} }} }})),
        emptyRun: ownContents(run, {{ status: "ready", run: {{ artifacts: [], steps: [], events: [], inputs: {{}} }}, usage: null }}),
        run: ownContents(run, {{ status: "ready", run: {{ workflow_id: "w", artifacts: [], steps: [], events: [], inputs: {{}} }}, usage: null }}),
      }}));
    """)
    off = {"has": False}
    assert out["none"] == out["loading"] == {"changes": off, "tokens": off, "sources": off}
    assert out["empty"] == {"changes": off, "tokens": off, "sources": off}      # nothing to show: nothing drawn in «Automática»
    assert out["edits"]["changes"] == {"has": True} and out["edits"]["sources"] == off
    assert out["reads"]["sources"] == {"has": True}
    assert out["tokens"]["tokens"] == {"has": True}
    assert out["emptyRun"] == {"changes": off, "tokens": off, "sources": off}
    assert out["run"]["sources"] == {"has": True}                               # the workflow of a run is a source


@needs_node
def test_card_modes_are_remembered_and_fall_back_without_storage():
    out = run_node(f"""
      const store = new Map();
      let mode = "ok";
      globalThis.window = {{
        get localStorage() {{ if (mode === "getter") throw new Error("denied"); return {{
          getItem(key) {{ if (mode === "throw") throw new Error("blocked"); return store.has(key) ? store.get(key) : null; }},
          setItem(key, value) {{ if (mode === "throw") throw new Error("blocked"); store.set(key, String(value)); }},
        }}; }},
      }};
      const state = await import({uri(JS / "shell-state.js")});
      const out = {{}};
      out.fresh = state.readCardModes();
      state.writeCardModes({{ task_waiting: "always", changes: "never", bad: "sometimes", Bad_Name: "auto" }});
      out.saved = state.readCardModes();
      store.set("multiagent.gui.context-cards", "not json"); out.garbage = state.readCardModes();
      store.set("multiagent.gui.context-cards", "[1,2]"); out.array = state.readCardModes();
      mode = "throw"; out.thrown = state.readCardModes(); state.writeCardModes({{ a: "auto" }});
      mode = "getter"; out.getter = state.readCardModes(); state.writeCardModes({{ a: "auto" }});
      console.log(JSON.stringify(out));
    """)
    assert out["fresh"] == {}
    assert out["saved"] == {"task_waiting": "always", "changes": "never"}      # only valid names and modes are kept
    assert out["garbage"] == {} and out["array"] == {} and out["thrown"] == {} and out["getter"] == {}


@needs_node
def test_a_closed_panel_without_subject_has_no_toggle_and_the_aside_stays():
    shell = (JS / "shell.js").read_text(encoding="utf-8")
    assert "hasSubject = false" in shell and "panelOpen && hasSubject" in shell
    toolbar = shell[shell.index("${hasSubject && html`<div class=\"content-toolbar\">"):shell.index("${children}")]
    assert 'aria-expanded=${panelOpen ? "true" : "false"}' in toolbar and 'aria-controls="right-panel"' in toolbar
    assert "Contexto" in toolbar and "context-dot" in toolbar and "contextDot" in toolbar
    assert "!panelOpen && contextDot" in toolbar                                # the dot is for a closed panel
    # the aside is outside the condition: it is always in the DOM, so aria-controls points at something
    aside = re.search(r'<aside id="right-panel"[^>]*>', shell)[0]
    assert "hidden=${!panelShown}" in aside and shell.index(aside) > shell.index("${children}")
    main = (JS / "main.js").read_text(encoding="utf-8")
    assert "subjectOf(context" in main and "hasSubject=${hasSubject}" in main and "onDot=${setContextDot}" in main
    out = run_node(f"""
      import {{ subjectOf }} from {uri(JS / "panel-model.js")};
      console.log(JSON.stringify([subjectOf({{ section: "usage", id: null }}, null), subjectOf({{ section: "sessions", id: null }}, null),
        subjectOf({{ section: "sessions", id: "claude:abc" }}, "claude:abc"), subjectOf({{ section: "runs", id: "r1" }}, null)].map((s) => s && s.kind)));
    """)
    assert out == [None, None, "session", "run"]


@needs_node
def test_slots_that_do_not_report_are_drawn_and_the_panel_honours_the_modes():
    panel = (JS / "right-panel.js").read_text(encoding="utf-8")
    assert "readCardModes()" in panel and "shownCards(cards, modes, contents)" in panel
    assert "defaultMode: entry.defaultMode" in panel                            # the mode a card registered
    assert panel.count("hidden=${!shown.") == 4 and "hidden=${!shown}" in panel   # not drawn means hidden, never unmounted
    assert "onContent=${reporter(entry.name)}" in panel and "onDot" in panel
    out = run_node(f"""
      import {{ registerSlot, registeredSlots }} from {uri(JS / "extensions.js")};
      const View = () => null;
      registerSlot("silent", View, {{ title: "Silenciosa" }});
      registerSlot("never_by_default", View, {{ title: "Nunca", defaultMode: "never" }});
      const {{ shownCards }} = await import({uri(JS / "context-model.js")});
      const cards = registeredSlots().map((e) => ({{ name: e.name, defaultMode: e.defaultMode }}));
      console.log(JSON.stringify({{ cards, shown: shownCards(cards, {{}}, {{}}) }}));
    """)
    assert sorted(out["cards"], key=lambda c: c["name"]) == [{"name": "never_by_default", "defaultMode": "never"}, {"name": "silent", "defaultMode": "auto"}]
    assert out["shown"] == {"silent": True, "never_by_default": False}


def test_the_extension_slots_report_their_content_and_task_waiting_starts_in_never():
    static = STATIC_DIR / "extensions"
    for path, call in (("project/index.js", "useContentReport(onContent, projects !== null"),
                       ("continuity/slot.js", "useContentReport(onContent, held, held)"),
                       ("document_alerts/index.js", "useContentReport(onContent, data.alerts.length > 0, pendingCount(data.alerts) > 0)"),
                       ("task_waiting/index.js", "useContentReport(onContent, waitingForUser(data.tasks).length > 0)")):
        assert call in (static / path).read_text(encoding="utf-8"), path
    # with no run open, «Alertas de documentos» says it has nothing, through a component that draws nothing
    alerts = (static / "document_alerts/index.js").read_text(encoding="utf-8")
    assert "<${ContentReport} onContent=${onContent} has=${false} />" in alerts
    report = (JS / "context-report.js").read_text(encoding="utf-8")
    assert re.search(r"export function ContentReport\(\{ onContent, has = false, urgent = false \}\) \{\s*useContentReport\(onContent, has, urgent\);\s*return null;", report)
    waiting =(static / "task_waiting/index.js").read_text(encoding="utf-8")
    assert re.search(r'registerSlot\("task_waiting".*?defaultMode: "never"', waiting, re.S)
    # the same list stays reachable: its screen is still registered
    assert 'registerView({ id: "task_waiting"' in waiting
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* GUI3-T04"):css.index("/* GUI3-T09 */")]   # the palettes of T09 come after and set --accent
    assert ".context-dot" in block and "--accent:" not in block and "z-index: " not in block.replace("z-index 20 against 30", "")


def test_the_subagents_panel_reports_whether_it_has_subagents():
    panel = (JS / "subagents-panel.js").read_text(encoding="utf-8")
    mount = (JS / "subagents-mount.js").read_text(encoding="utf-8")
    assert "onContent({ has: data.loaded && data.children.length > 0 })" in panel
    assert "onContent({ has: false })" in mount and "onContent=${onContent}" in mount
