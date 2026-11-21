# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T07: Ctrl+K. The pure model runs in Node; the dialog is checked statically. No server, no TestClient."""
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


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def run_node(source: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


SETUP = """
  import { sectionEntries, itemEntries, helpEntries, searchPalette, moveIndex, keyAction, openEntry, isPaletteKey } from %s;
  import { sessionItems, runItems } from %s;
  const navigation = {
    primary: [{ id: "project", label: "Proyectos" }, { id: "apps", label: "Apps y agentes" }],
    more: [{ id: "usage", label: "Uso" }, { id: "evaluation", label: "Evaluación" }, { id: "customize", label: "Personalizar" }],
  };
  const tabs = [{ id: "all", label: "Todo", route: ["pending", null] }, { id: "continuity", label: "Decisiones", route: ["continuity", null] }];
  const sessions = sessionItems([{ id: "claude:1", source: "claude", cwd: "C:/work/Tienda", status: "active", title: "Arreglar el carrito" }]);
  const runs = runItems([{ id: "r1", workflow_id: "review_flow", status: "running", project_name: "Tienda" }]);
  const help = { "shell.more": { title: "Más secciones", hint: "Aquí siguen las demás pantallas.", more: ["Abrir solo cambia la vista."] } };
  const entries = [...sectionEntries(navigation, tabs), ...itemEntries(sessions.concat(runs)), ...helpEntries(help)];
  const labels = (list) => list.map((entry) => entry.label);
""" % (uri("command-palette-model.js"), uri("sidebar-model.js"))


@needs_node
def test_finds_sections_pending_tabs_sessions_runs_and_help_without_case_or_accents():
    out = run_node(SETUP + """
      console.log(JSON.stringify({
        sections: labels(searchPalette(entries, "evaluacion")),
        extension: labels(searchPalette(entries, "apps")),
        custom: searchPalette(entries, "personalizar").map((entry) => entry.route),
        tab: searchPalette(entries, "decisiones").map((entry) => [entry.kind, entry.route]),
        session: labels(searchPalette(entries, "CARRITO")),
        run: labels(searchPalette(entries, "review tienda")),
        help: searchPalette(entries, "demas pantallas").map((entry) => [entry.kind, entry.label]),
        none: searchPalette(entries, "zzzz").length,
        both: labels(searchPalette(entries, "tienda")),
      }));
    """)
    assert out["sections"] == ["Evaluación"] and out["extension"] == ["Apps y agentes"]
    assert out["custom"] == [["customize", None]]
    assert out["tab"] == [["tab", ["continuity", None]]]
    assert out["session"] == ["Arreglar el carrito"] and out["run"] == ["review_flow"]
    assert out["help"] == [["help", "Más secciones"]] and out["none"] == 0
    assert out["both"] == ["Arreglar el carrito", "review_flow"]            # same matching as the old sidebar search


@needs_node
def test_an_empty_query_lists_the_screens_and_a_few_recent_items_and_never_help():
    out = run_node(SETUP + """
      console.log(JSON.stringify({ rows: searchPalette(entries, "").map((entry) => [entry.kind, entry.label]) }));
    """)
    kinds = [kind for kind, _ in out["rows"]]
    assert kinds[:7] == ["section"] * 5 + ["tab"] * 2 and "help" not in kinds and {"session", "run"} <= set(kinds)


@needs_node
def test_arrows_stop_at_the_ends_enter_opens_and_other_keys_do_nothing():
    out = run_node(SETUP + """
      console.log(JSON.stringify({
        down: [moveIndex(0, "ArrowDown", 3), moveIndex(2, "ArrowDown", 3)],
        up: [moveIndex(1, "ArrowUp", 3), moveIndex(0, "ArrowUp", 3)],
        empty: moveIndex(0, "ArrowDown", 0),
        actions: [keyAction({ key: "ArrowDown" }), keyAction({ key: "Enter" }), keyAction({ key: "Enter", isComposing: true }), keyAction({ key: "Escape" }), keyAction({ key: "a" })],
        ctrlK: [isPaletteKey({ key: "k", ctrlKey: true }), isPaletteKey({ key: "K", metaKey: true }), isPaletteKey({ key: "k" }),
                isPaletteKey({ key: "k", ctrlKey: true, shiftKey: true }), isPaletteKey({ key: "k", ctrlKey: true, altKey: true })],
        sectionOpen: openEntry(entries[0]), helpOpen: openEntry(entries[entries.length - 1]), nothing: openEntry(undefined),
      }));
    """)
    assert out["down"] == [1, 2] and out["up"] == [0, 0] and out["empty"] == -1
    assert out["actions"] == ["move", "open", None, None, None]               # Esc belongs to the native dialog
    assert out["ctrlK"] == [True, True, False, False, False]
    assert out["sectionOpen"] == {"route": ["project", None]}
    assert out["helpOpen"]["show"]["title"] == "Más secciones" and "route" not in out["helpOpen"]
    assert out["nothing"] is None


def test_the_dialog_is_native_closes_with_esc_and_gives_the_focus_back():
    palette = read("command-palette.js")
    assert "<dialog" in palette and "showModal()" in palette and 'addEventListener("close"' in palette
    assert "before.current = document.activeElement" in palette and "back.focus()" in palette
    assert 'role="combobox"' in palette and 'role="listbox"' in palette and 'role="option"' in palette
    # Esc: the dialog's own cancel, plus one handler in the field that only closes it (a search field spends the first Esc
    # clearing itself; see test_one_escape_closes_the_dialog_even_with_text_in_the_field)
    assert palette.count("Escape") == 1 and palette.count("dialog.current.close()") >= 2
    sidebar = read("sidebar.js")
    assert "<${CommandPalette}" in sidebar and "isPaletteKey(event)" in sidebar and 'class="side-search-btn"' in sidebar
    # the shell's Esc for the drawer leaves an open dialog alone, and the help block in front of it still gets its own Esc
    shell = read("shell.js")
    assert "dialog[open]" in shell and ".help-block.open" in shell
    # no other new key: Ctrl+K and the arrows and Intro of the open dialog; F1 stays the help key of shell.js
    assert 'event.key === "F1"' in read("shell-state.js") and "isPaletteKey" not in shell


def test_the_palette_only_navigates_and_explains():
    for name in ("command-palette.js", "command-palette-model.js"):
        text = read(name)
        for forbidden in ("apiPost", "apiGet", "fetch(", "/challenge", "/decision", "extensions/approvals", "Aprobar", "Permitir la consulta",
                          "Cargar este bundle", "innerHTML", "localStorage"):
            assert forbidden not in text, (name, forbidden)
        assert not re.search(r"^import .*extensions/", text, re.M)


def test_the_old_fixed_field_is_gone_and_the_search_row_shows_the_shortcut():
    sidebar = read("sidebar.js")
    assert 'id="side-search"' not in sidebar and "setQuery" not in sidebar
    assert 'aria-keyshortcuts="Control+K"' in sidebar and 'helpFor("shell.search")' in sidebar
    help_text = read("help-text.js")
    assert '"palette.search": {' in help_text and '"palette.close": {' in help_text


def test_dialog_opens_before_paint_so_the_first_keys_reach_the_field():
    # The dialog opens and takes the focus in a layout effect: keys typed right after Ctrl+K or the «Buscar» row land in
    # the field instead of the page (a plain effect waits for the next frame, and a hidden tab may delay it).
    palette = (JS / "command-palette.js").read_text(encoding="utf-8")
    opening = palette[:palette.index("box.showModal()")]
    assert opening.rstrip().rfind("useLayoutEffect(") > opening.rstrip().rfind("useEffect(")


def test_one_escape_closes_the_dialog_even_with_text_in_the_field():
    # A search field spends the first Esc clearing itself: the field's own handler closes the dialog on the first one
    # (unless an open help text took it first), and the focus goes back through the dialog's "close" event.
    palette = (JS / "command-palette.js").read_text(encoding="utf-8")
    handler = palette[palette.index("const onKeyDown"):palette.index("const optionId")]
    assert 'event.key === "Escape"' in handler and "!event.defaultPrevented" in handler
    assert "event.preventDefault()" in handler and "dialog.current.close()" in handler


def test_a_route_chosen_in_the_dialog_closes_the_drawer_on_a_phone():
    # Below 900 px the sidebar is a drawer: a click on a link closes it, and a route opened with Intro in the Ctrl+K dialog
    # (no click) closes it too, because any change of address while it is open does.
    shell = read("shell.js")
    listener = shell[shell.index('window.addEventListener("hashchange"') - 200:shell.index('window.addEventListener("hashchange"')]
    assert "state.current.toMain = true; closeDrawer(false);" in listener
    assert 'window.removeEventListener("hashchange"' in shell
    # the focus then goes to the new view, not to a control of the closed drawer
    effect = shell[shell.index("// Opening moves the focus into the drawer"):shell.index('window.addEventListener("hashchange"')]
    assert "state.current.toMain && mainBox.current" in effect and "mainBox.current.focus()" in effect
