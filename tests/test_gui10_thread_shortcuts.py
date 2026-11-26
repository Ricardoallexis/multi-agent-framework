# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T14: the row of shortcuts in the header of a conversation (a session and a run thread share one model).

The pure model runs in Node (skipped without it); the components are read as source. No server, no browser."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from tests.gui_text import ui_text

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(body: str):
    imports = "\n".join([
        f"import * as model from {json.dumps((JS / 'thread-model.js').as_uri())};",
        f"import * as ui from {json.dumps((JS / 'i18n.js').as_uri())};",
    ])
    result = subprocess.run([NODE, "--input-type=module", "-e", imports + "\n" + body], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def ids(**args):
    return node(f"console.log(JSON.stringify(model.shortcutsFor({json.dumps(args)}).map((item) => item.id)))")


@needs_node
def test_a_one_question_conversation_offers_only_usage_in_simple_and_usage_and_map_in_advanced():
    assert ids(mode="simple", sessionId="s1", workers=0) == ["usage"]
    assert ids(mode="advanced", sessionId="s1", workers=0) == ["usage", "map"]


@needs_node
def test_the_tree_needs_workers_and_advanced_mode():
    assert ids(mode="advanced", sessionId="s1", workers=2) == ["tree", "usage", "map"]
    assert ids(mode="simple", sessionId="s1", workers=2) == ["usage"]        # the tree is a view of advanced only: no wall
    assert ids(mode="advanced", sessionId="", workers=2) == ["usage", "map"]  # no destination, no link
    # a run does not say how many agents it has: the tree stays, as it was
    assert ids(mode="advanced", sessionId="framework:r1") == ["tree", "usage", "map"]


@needs_node
def test_the_project_link_needs_its_id_and_exists_in_both_modes():
    project = {"id": "p1", "name": "Atlas"}
    assert ids(mode="simple", sessionId="s1", workers=0, project=project) == ["project", "usage"]
    assert ids(mode="advanced", sessionId="s1", workers=3, project=project) == ["tree", "project", "usage", "map"]
    assert ids(mode="advanced", sessionId="s1", workers=3, project={"id": "", "name": "Atlas"}) == ["tree", "usage", "map"]
    assert ids(mode="advanced", sessionId="s1", workers=3, project=None) == ["tree", "usage", "map"]


@needs_node
def test_project_of_reads_only_an_explicit_id():
    result = node("""
      console.log(JSON.stringify([model.projectOf(null), model.projectOf({project_name: "Atlas"}), model.projectOf({project_id: " "}),
        model.projectOf({project_id: "p1", project_name: "atlas", project_title: "Atlas"}), model.projectOf({project_id: "p2"})]));
    """)
    assert result == [None, None, None, {"id": "p1", "name": "Atlas"}, {"id": "p2", "name": ""}]


@needs_node
def test_every_shortcut_is_a_hash_link_with_a_text():
    result = node("""
      ui.setLanguage("en");
      const items = model.shortcutsFor({mode: "advanced", sessionId: "framework:r 1", workers: 1, project: {id: "p/1", name: "Atlas"}});
      console.log(JSON.stringify(items.map((item) => [item.id, item.href, ui.t(item.label, item.data)])));
    """)
    assert result == [["tree", "#tree/framework%3Ar%201", "Agent tree"], ["project", "#project/p%2F1", "Project: Atlas"],
                      ["usage", "#usage", "Usage"], ["map", "#map", "Map"]]


@needs_node
def test_texts_exist_in_both_languages():
    result = node("""
      const out = {};
      for (const lang of ["en", "es"]) {
        ui.setLanguage(lang);
        out[lang] = ["thread.shortcut.project", "thread.shortcut.usage", "thread.shortcut.map", "thread.shortcuts.more",
          "thread.shortcuts.aria", "sessions.agent_tree"].map((key) => ui.t(key));
        out[lang].push(ui.t("thread.shortcut.project_named", {name: "Atlas"}));
      }
      console.log(JSON.stringify(out));
    """)
    assert result["en"] == ["Project", "Usage", "Map", "More", "Related views", "Agent tree", "Project: Atlas"]
    assert result["es"] == ["Proyecto", "Uso", "Mapa", "Más", "Vistas relacionadas", "Árbol de agentes", "Proyecto: Atlas"]


def test_session_and_thread_draw_the_same_component_from_the_same_model():
    session, thread = ui_text(JS / "session-detail.js"), ui_text(JS / "thread.js")
    assert "<${Shortcuts}" in session and "<${Shortcuts}" in thread
    assert re.search(r"import \{ Shortcuts \} from \"\./thread\.js\"", session)
    assert "shortcutsFor(" in thread and "shortcutsFor(" not in session     # one model, one component
    # nothing of the old header went away
    for kept in ('helpFor("session.run_link")', 'helpFor("session.refresh")', "open-in-app-slot", "OpenInApp"):
        assert kept in session, kept
    assert 'helpFor("thread.refresh")' in thread
    # a run has no child count; a session hands over the one it has
    assert "workers=${isRun ? null : summary.child_count}" in session


def test_the_links_are_anchors_with_an_address_and_help():
    source = ui_text(JS / "thread.js")
    block = source[source.index("export function Shortcuts"):source.index("export function RunThread")]
    assert block.count("<a ") == 4 and block.count("href=${") == 4
    for topic in ("agent_tree.open", "session.shortcut_project", "session.shortcut_usage", "session.shortcut_map", "session.shortcuts_more"):
        assert f'helpFor("{topic}")' in block and f'topic="{topic}"' in block, topic
    assert "style=" not in block and "innerHTML" not in block and "onClick=${() => navigate" not in block


def test_the_narrow_menu_is_css_only_without_a_second_copy_of_the_links():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* GUI4-T14"):]
    assert ".shortcuts-toggle { display: none;" in block                      # wide: no «Más»
    narrow = block[block.index("@media (max-width: 599.98px)"):]
    assert re.search(r"\.shortcuts-toggle \{[^}]*flex-wrap: wrap;[^}]*min-width: 0;", block)   # the opened «?» drops to its own row
    assert ".shortcuts-list { display: none;" in narrow and ".shortcuts-list.open { display: flex; }" in narrow
    assert "overflow-x" not in block and "white-space: nowrap" not in block   # nothing scrolls sideways


def test_the_project_also_comes_from_the_launched_entry_in_every_state():
    source = ui_text(JS / "thread.js")
    assert 'projectOf(reviewView) || projectOf(thread.entries.find((entry) => entry.kind === "launched"))' in source


@needs_node
def test_project_of_reads_a_launched_entry():
    result = node("""
      const entries = [{kind: "step"}, {kind: "launched", project_id: "p9", project_name: "Atlas"}];
      console.log(JSON.stringify([model.projectOf(null) || model.projectOf(entries.find((e) => e.kind === "launched")),
        model.projectOf(undefined), model.projectOf([].find((e) => e.kind === "launched"))]));
    """)
    assert result == [{"id": "p9", "name": "Atlas"}, None, None]


def test_each_header_gives_its_list_its_own_id():
    thread, session = ui_text(JS / "thread.js"), ui_text(JS / "session-detail.js")
    assert 'const listId = "shortcuts-list-" + scope' in thread and "aria-controls=${listId}" in thread and "id=${listId}" in thread
    assert 'scope="run"' in thread and 'scope="session"' in session
