# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SIM-T02: simple and advanced mode in the views. The pure models run in Node (skipped without it); the rest is static.

The real ``SECTIONS`` of ``main.js`` and the real ``registerView`` calls of the six extensions are read from the source and
handed to Node as data, so a view that changes its ``mode`` changes these tests. No server, no browser."""
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
SIX = ["approvals", "apps", "continuity", "document_alerts", "project", "task_waiting"]


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def run_node(source: str):
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


def real_sections() -> list[dict]:
    """The built-in sections of main.js, as {id, label, icon, mode}."""
    block = re.search(r"const SECTIONS = \[(.*?)\n\];", read("main.js"), re.S)[1]
    rows = re.findall(r'\{ id: "(\w+)", label: "([^"]+)", icon: "(\w+)", mode: "(\w+)" \}', block)
    assert len(rows) == 9, rows   # every built-in section declares its mode
    return [{"id": a, "label": b, "icon": c, "mode": d} for a, b, c, d in rows]


def real_extension_views() -> list[dict]:
    views = []
    for name in SIX:
        source = (STATIC_DIR / "extensions" / name / "index.js").read_text(encoding="utf-8")
        call = re.search(r"registerView\(\{(.*?)\}\);", source, re.S)[1]
        view = {key: re.search(key + r':\s*"([^"]*)"', call)[1] for key in ("id", "label", "icon", "mode")}
        group = re.search(r'group:\s*"(\w+)"', call)
        if group:
            view["group"] = group[1]
        views.append(view)
    return views


SETUP = """
  import { navigationSections, runItems, sessionItems, isCurrent } from %s;
  import { sectionEntries, itemEntries, helpEntries, searchPalette } from %s;
  import { visibleIn } from %s;
  const sections = %s;
  const views = %s;
  const builtins = sections;
  const extensions = views;
  const ids = (list) => list.map((row) => row.id);
""" % (uri("sidebar-model.js"), uri("command-palette-model.js"), uri("gui-mode.js"),
       json.dumps(real_sections()) if NODE else "[]", json.dumps(real_extension_views()) if NODE else "[]")


@needs_node
def test_register_view_mode_defaults_to_advanced():
    out = run_node("""
      import { registerView, registeredViews } from %s;
      const base = { label: "X", component: () => null };
      registerView({ ...base, id: "plain" });
      registerView({ ...base, id: "calm", mode: "simple" });
      let error = "";
      try { registerView({ ...base, id: "odd", mode: "expert" }); } catch (e) { error = String(e.message); }
      console.log(JSON.stringify({ views: registeredViews().map((v) => [v.id, v.mode]), error }));
    """ % uri("extensions.js"))
    assert out["views"] == [["calm", "simple"], ["plain", "advanced"]]   # "odd" was refused
    assert "mode must be" in out["error"]


@needs_node
def test_simple_navigation_is_the_closed_list():
    out = run_node(SETUP + """
      const nav = navigationSections(builtins, extensions, [], "simple");
      console.log(JSON.stringify({
        primary: nav.primary, more: nav.more, pending: ids(nav.pending), sessionsRoute: ids(nav.more).includes("sessions"),
      }));
    """)
    rows = out["primary"] + out["more"]
    assert all(row["mode"] == "simple" for row in rows)
    primary_ids = [row["id"] for row in out["primary"]]
    more_ids = [row["id"] for row in out["more"]]
    assert "project" in primary_ids and "apps" in primary_ids
    assert {"usage", "customize", "help"} <= set(more_ids)
    assert not {"runs", "evaluation", "teams", "plans", "map", "tree"} & set(primary_ids + more_ids)
    assert sorted(out["pending"]) == sorted(["approvals", "continuity", "document_alerts", "task_waiting"])   # the four views of today
    help_row = next(row for row in out["more"] if row["id"] == "help")
    assert help_row["action"] == "help"   # a row that turns help mode on, not a view


@needs_node
def test_advanced_navigation_unchanged():
    out = run_node(SETUP + """
      const plain = navigationSections(builtins, extensions);
      const explicit = navigationSections(builtins, extensions, [], "advanced");
      console.log(JSON.stringify({
        same: JSON.stringify(plain) === JSON.stringify(explicit),
        all: ids([...plain.primary, ...plain.more, ...plain.pending]),
      }));
    """)
    assert out["same"] is True
    expected = {section["id"] for section in real_sections()} | set(SIX)
    assert set(out["all"]) == expected and len(out["all"]) == len(expected)   # nothing hidden, nothing twice, no «Ayuda» row


@needs_node
def test_pinned_advanced_section_hidden_in_simple():
    out = run_node(SETUP + """
      const simple = navigationSections(builtins, extensions, ["runs"], "simple");
      const advanced = navigationSections(builtins, extensions, ["runs"], "advanced");
      console.log(JSON.stringify({ simple: ids([...simple.primary, ...simple.more]), advanced: ids(advanced.primary) }));
    """)
    assert "runs" not in out["simple"]
    assert "runs" in out["advanced"]


@needs_node
def test_palette_searches_only_visible():
    out = run_node(SETUP + """
      const items = sessionItems([{ id: "claude:1", source: "claude", cwd: "C:/work/Tienda", status: "active", title: "Arreglar el carrito" }])
        .concat(runItems([{ id: "r1", workflow_id: "review_flow", status: "running" }], "simple"));
      const tabs = [{ id: "all", label: "Todo", route: ["pending", null] }];
      const build = (mode) => {
        const nav = navigationSections(builtins, extensions, [], mode);
        return [...sectionEntries(nav, tabs, mode), ...itemEntries(items, mode), ...helpEntries({})];
      };
      const labels = (mode, query) => searchPalette(build(mode), query).map((entry) => entry.label);
      console.log(JSON.stringify({
        simpleRuns: labels("simple", "Ejecuciones"), simpleTeams: labels("simple", "Equipos"), simpleSession: labels("simple", "carrito"),
        simpleHelpRow: labels("simple", "Ayuda"), advancedRuns: labels("advanced", "Ejecuciones"), advancedTeams: labels("advanced", "Equipos"),
        simpleUsage: labels("simple", "Uso"),
      }));
    """)
    assert out["simpleRuns"] == [] and out["simpleTeams"] == []
    assert out["simpleSession"] == ["Arreglar el carrito"]
    assert out["simpleHelpRow"] == []   # the «Ayuda» row is not a screen: there is no route to open
    assert out["simpleUsage"] == ["Uso"]
    assert "Ejecuciones" in out["advancedRuns"] and "Equipos" in out["advancedTeams"]


@needs_node
def test_run_item_opens_thread_in_simple():
    out = run_node(SETUP + """
      const simple = runItems([{ id: "r1" }], "simple")[0];
      const advanced = runItems([{ id: "r1" }])[0];
      console.log(JSON.stringify({
        simple: simple.route, advanced: advanced.route,
        simpleCurrent: isCurrent(simple, { section: "new", id: "r1" }),
        simpleOtherId: isCurrent(simple, { section: "new", id: "r2" }),
        simpleTeamThread: isCurrent(simple, { section: "new", id: "r1", kind: "team" }),
        simpleOnRuns: isCurrent(simple, { section: "runs", id: "r1" }),
        advancedCurrent: isCurrent(advanced, { section: "runs", id: "r1" }),
      }));
    """)
    assert out["simple"] == ["new/run", "r1"] and out["advanced"] == ["runs", "r1"]
    assert out["simpleCurrent"] is True and out["advancedCurrent"] is True
    assert out["simpleOtherId"] is False and out["simpleTeamThread"] is False and out["simpleOnRuns"] is False


@needs_node
def test_visible_in_and_the_mode_holder():
    out = run_node("""
      import { MODES, DEFAULT_MODE, ADVANCED_ONLY_TEXT, currentMode, setCurrentMode, onModeChange, visibleIn, saveMode } from %s;
      const heard = [];
      const off = onModeChange((mode) => heard.push(mode));
      const start = currentMode();
      setCurrentMode("nonsense");
      setCurrentMode("advanced");
      setCurrentMode("advanced");
      off();
      setCurrentMode("simple");
      const calls = [];
      const post = async (path, token, body) => { calls.push([path, token, body]); return { mode: body.mode }; };
      const saved = await saveMode("tok", "advanced", post);
      let refused = "";
      try { await saveMode("tok", "expert", post); } catch (e) { refused = e.message; }
      console.log(JSON.stringify({
        MODES, DEFAULT_MODE, ADVANCED_ONLY_TEXT, start, heard, after: currentMode(), calls, saved,
        simpleShows: [visibleIn({ mode: "simple" }, "simple"), visibleIn({ mode: "advanced" }, "simple"), visibleIn({}, "simple"), visibleIn(null, "simple")],
        advancedShows: [visibleIn({ mode: "advanced" }, "advanced"), visibleIn({}, "advanced")],
        refused,
      }));
    """ % uri("gui-mode.js"))
    assert out["MODES"] == ["simple", "advanced"] and out["DEFAULT_MODE"] == "simple" and out["start"] == "simple"
    assert out["heard"] == ["advanced"]   # an invalid value and a repeat are not changes; a removed listener hears nothing
    assert out["calls"] == [["/gui/api/settings/mode", "tok", {"mode": "advanced"}]]
    assert out["saved"] == {"mode": "advanced"} and out["after"] == "advanced"   # saveMode followed the answer
    assert out["simpleShows"] == [True, False, False, False]   # a view with no mode is advanced (L4)
    assert out["advancedShows"] == [True, True]
    assert "mode must be" in out["refused"]


@needs_node
def test_pin_list_offers_only_visible_sections_in_simple_and_keeps_the_pins():
    out = run_node(SETUP + """
      import { pinnableSections, togglePinned } from %s;
      const all = [...builtins, ...extensions];
      const simple = pinnableSections(all, ["runs"], "simple");
      const advanced = pinnableSections(all, ["runs"], "advanced");
      console.log(JSON.stringify({
        simple: ids(simple), advanced: ids(advanced), advancedPinned: advanced.find((r) => r.id === "runs").pinned,
        kept: togglePinned(["runs"], "usage"),
      }));
    """ % uri("customize-model.js"))
    assert "runs" not in out["simple"] and "usage" in out["simple"]
    assert "runs" in out["advanced"] and out["advancedPinned"] is True
    assert out["kept"] == ["runs", "usage"]   # pinning in simple mode does not release the advanced pin


def test_advanced_only_text_and_the_line_with_its_button():
    assert 'ADVANCED_ONLY_TEXT = "Esta vista es del modo avanzado"' in read("gui-mode.js")
    main = read("main.js")
    assert "<p class=\"advanced-only-text\">${ADVANCED_ONLY_TEXT}</p>" in main
    assert "Activar modo avanzado" in main and 'saveMode(token, "advanced")' in main
    assert 'helpFor("shell.advanced_only")' in main and 'topic="shell.advanced_only"' in main
    # the line replaces the view; the route is not touched
    assert "const blocked = modeKnown && !creating && !inPending && !visibleIn(tree ? { mode: \"advanced\" } : current, mode)" in main
    for view in ('<${Runs}', '<${Evaluation}', '<${Teams}', '<${Plans}', '<${WorkMap}', '<${AgentTree}', '<${view.component}'):
        line = next(row for row in main.splitlines() if view in row)
        assert "!blocked" in line, view
    assert "navigate(" not in main[main.index("function AdvancedOnly"):main.index("function NoToken")]


def test_gui_mode_has_no_local_storage():
    source = read("gui-mode.js")
    assert "localStorage" not in source and "sessionStorage" not in source and "indexedDB" not in source


def test_the_six_extensions_declare_simple_and_the_built_ins_say_their_mode():
    for name in SIX:
        source = (STATIC_DIR / "extensions" / name / "index.js").read_text(encoding="utf-8")
        call = re.search(r"registerView\(\{(.*?)\}\);", source, re.S)[1]
        assert re.search(r'\bmode:\s*"simple"', call), name
    modes = {section["id"]: section["mode"] for section in real_sections()}
    assert {key for key, value in modes.items() if value == "simple"} == {"sessions", "usage", "customize", "packages"}
    assert {key for key, value in modes.items() if value == "advanced"} == {"runs", "evaluation", "teams", "plans", "map"}
    assert 'mode: view.mode' in read("main.js")   # the registered views carry their mode into the sections


def test_the_wiring_reads_the_mode_from_the_server_and_follows_it():
    main = read("main.js")
    assert "setCurrentMode(s.mode)" in main and "onModeChange(setMode)" in main
    assert "mode=${mode}" in main
    sidebar = read("sidebar.js")
    assert "writeHelpMode(true)" in sidebar and 'helpFor("shell.help_mode", { scope: "more" })' in sidebar
    assert "runItems(data.runs, mode)" in sidebar and "navigationSections(" in sidebar and "pinned, mode)" in sidebar
    customize = read("customize.js")
    assert 'helpFor("customize.advanced_mode")' in customize and 'topic="customize.advanced_mode"' in customize
    assert 'saveMode(token, event.target.checked ? "advanced" : "simple")' in customize


def test_the_help_texts_exist_and_the_css_block_is_marked():
    text = read("help-text.js")
    for key in ("customize.advanced_mode", "shell.advanced_only"):
        assert f'"{key}": {{' in text
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* SIM-T02 */"):]
    assert ".advanced-only" in block and "style=" not in block and "url(" not in block
