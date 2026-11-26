# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T03: the widths of the sidebar and the panel (B1), hiding at the edge, the keys, the footer slot and «Ver plano».

Pure Node checks on shell-state.js and sidebar-model.js (skipped if Node is absent) plus static checks of the sources."""
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


def run_node(source: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def state_uri() -> str:
    return json.dumps((JS / "shell-state.js").as_uri())


@needs_node
def test_widths_are_kept_inside_their_range_and_a_drag_to_the_edge_hides_the_pane():
    out = run_node(f"""
      import {{ HIDE_BELOW, PANES, resolvePaneWidth }} from {state_uri()};
      console.log(JSON.stringify({{
        below: HIDE_BELOW, side: PANES.side, panel: PANES.panel,
        edge: resolvePaneWidth("side", 17), justIn: resolvePaneWidth("side", 18), tiny: resolvePaneWidth("panel", 0),
        low: resolvePaneWidth("side", 100), high: resolvePaneWidth("side", 900), mid: resolvePaneWidth("side", 301.4),
        panelHigh: resolvePaneWidth("panel", 900), panelLow: resolvePaneWidth("panel", 30), nan: resolvePaneWidth("side", "x"),
      }}));
    """)
    assert out["below"] == 18
    assert (out["side"]["min"], out["side"]["def"], out["side"]["max"], out["side"]["steps"]) == (220, 264, 340, [220, 264, 340])
    assert (out["panel"]["min"], out["panel"]["max"]) == (240, 400)   # the user's answer: 400 px at most
    assert out["edge"]["hidden"] is True and out["tiny"]["hidden"] is True
    assert out["justIn"] == {"hidden": False, "width": 220}           # 18 px is not the edge: it goes to the minimum
    assert out["low"]["width"] == 220 and out["high"]["width"] == 340 and out["mid"]["width"] == 301
    assert out["panelHigh"]["width"] == 400 and out["panelLow"]["width"] == 240
    assert out["nan"] == {"hidden": False, "width": 264}


@needs_node
def test_arrow_keys_home_and_end_move_the_edge_of_each_pane():
    out = run_node(f"""
      import {{ keyWidth }} from {state_uri()};
      console.log(JSON.stringify({{
        sideGrow: keyWidth("side", 264, "ArrowRight", "right"), sideShrink: keyWidth("side", 264, "ArrowLeft", "right"),
        sideFloor: keyWidth("side", 224, "ArrowLeft", "right"), sideCeil: keyWidth("side", 336, "ArrowRight", "right"),
        panelGrow: keyWidth("panel", 300, "ArrowLeft", "left"), panelShrink: keyWidth("panel", 300, "ArrowRight", "left"),
        home: keyWidth("side", 300, "Home", "right"), end: keyWidth("panel", 300, "End", "left"), other: keyWidth("side", 264, "a", "right"),
      }}));
    """)
    assert out == {"sideGrow": 280, "sideShrink": 248, "sideFloor": 220, "sideCeil": 340, "panelGrow": 316, "panelShrink": 284,
                   "home": 220, "end": 400, "other": 264}


@needs_node
def test_widths_hidden_state_and_folded_sections_survive_blocked_storage():
    out = run_node(f"""
      import {{ readPaneWidth, writePaneWidth, readSideHidden, writeSideHidden, readSectionOpen, writeSectionOpen, storageRemembers }} from {state_uri()};
      globalThis.window = {{ localStorage: {{
        getItem() {{ throw new Error("blocked"); }}, setItem() {{ throw new Error("blocked"); }},
      }} }};
      writePaneWidth("side", 300); writeSideHidden(true); writeSectionOpen("offices", false);
      console.log(JSON.stringify({{
        side: readPaneWidth("side"), panel: readPaneWidth("panel"), hidden: readSideHidden(), open: readSectionOpen("offices"),
        remembers: storageRemembers(),
      }}));
    """)
    assert out == {"side": None, "panel": None, "hidden": False, "open": True, "remembers": False}


@needs_node
def test_widths_hidden_state_and_folded_sections_are_remembered_and_junk_is_ignored():
    out = run_node(f"""
      import {{ readPaneWidth, writePaneWidth, readSideHidden, writeSideHidden, readSectionOpen, writeSectionOpen }} from {state_uri()};
      const store = new Map();
      globalThis.window = {{ localStorage: {{ getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }} }};
      writePaneWidth("side", 300); writePaneWidth("panel", 9000); writeSideHidden(true); writeSectionOpen("offices", false);
      const first = {{ side: readPaneWidth("side"), panel: readPaneWidth("panel"), hidden: readSideHidden(),
        offices: readSectionOpen("offices"), projects: readSectionOpen("projects") }};
      writePaneWidth("side", null); writeSideHidden(false); writeSectionOpen("offices", true);
      store.set("multiagent.gui.panel-width", "12"); store.set("multiagent.gui.side-sections", "[1,2]");
      console.log(JSON.stringify({{ first, reset: readPaneWidth("side"), hidden: readSideHidden(), offices: readSectionOpen("offices"),
        junkPanel: readPaneWidth("panel"), junkSections: readSectionOpen("anything") }}));
    """)
    assert out["first"] == {"side": 300, "panel": 400, "hidden": True, "offices": False, "projects": True}
    assert out["reset"] is None and out["hidden"] is False and out["offices"] is True
    assert out["junkPanel"] is None and out["junkSections"] is True


@needs_node
def test_ctrl_b_is_the_only_sidebar_key_and_is_not_taken_with_other_modifiers():
    out = run_node(f"""
      import {{ isSidebarKey }} from {state_uri()};
      console.log(JSON.stringify([
        isSidebarKey({{ key: "b", ctrlKey: true }}), isSidebarKey({{ key: "B", metaKey: true }}), isSidebarKey({{ key: "b" }}),
        isSidebarKey({{ key: "b", ctrlKey: true, shiftKey: true }}), isSidebarKey({{ key: "n", ctrlKey: true }}),
        isSidebarKey({{ key: "b", ctrlKey: true, isComposing: true }}), isSidebarKey(null),
      ]));
    """)
    assert out == [True, True, False, False, False, False, False]


@needs_node
def test_the_lists_show_five_rows_and_always_the_ones_that_wait():
    model = json.dumps((JS / "sidebar-model.js").as_uri())
    out = run_node(f"""
      import {{ LIST_LIMIT, limitRows, latestFirst, officeRows, projectRows }} from {model};
      const rows = Array.from({{ length: 8 }}, (_, i) => ({{ key: "r" + i, attention: i === 6, updatedAt: "2026-01-0" + (i + 1) + "T00:00:00Z" }}));
      const short = limitRows(rows.slice(0, 5)); const long = limitRows(rows); const open = limitRows(rows, true);
      console.log(JSON.stringify({{
        limit: LIST_LIMIT, shortShown: short.shown.length, shortHidden: short.hidden,
        longKeys: long.shown.map((r) => r.key), longHidden: long.hidden, openShown: open.shown.length, openHidden: open.hidden,
        newest: latestFirst(rows).map((r) => r.key).slice(0, 2),
        offices: officeRows([{{ id: "a", name: "A", state: "ok" }}, {{ id: "b", state: "degraded" }}, {{ name: "no id" }}, null]),
        projects: projectRows([{{ id: 3, title: "T" }}, {{ id: "p", name: "N" }}, {{ title: "none" }}]),
        none: limitRows(null),
      }}));
    """)
    assert out["limit"] == 5 and out["shortShown"] == 5 and out["shortHidden"] == 0
    assert out["longKeys"] == ["r0", "r1", "r2", "r3", "r4", "r6"] and out["longHidden"] == 2   # the waiting row (r6) is never hidden
    assert out["openShown"] == 8 and out["openHidden"] == 0 and out["newest"] == ["r7", "r6"]
    assert out["offices"] == [{"id": "a", "name": "A", "attention": False}, {"id": "b", "name": "b", "attention": True}]
    assert out["projects"] == [{"id": "3", "name": "T", "attention": False}, {"id": "p", "name": "N", "attention": False}]
    assert out["none"] == {"shown": [], "hidden": 0}


def test_the_width_reaches_the_grid_through_the_cssom_and_never_through_a_style_attribute():
    shell = ui_text(JS / "shell.js")
    assert "style.setProperty" in shell and "style.removeProperty" in shell
    for forbidden in ("style=", "cssText", 'setAttribute("style"'):
        assert forbidden not in shell, forbidden
    handle_js = ui_text(JS / "ui" / "resize-handle.js")   # the separator is a shared component: no js/*.js draws aria-valuenow by hand
    assert 'role="separator"' in handle_js and "aria-valuenow" in handle_js and "aria-valuemin" in handle_js and "aria-valuemax" in handle_js
    assert "onDblClick" in handle_js and "setPointerCapture" in handle_js and "keyWidth(" in handle_js
    assert "ResizeHandle" in shell and "keyWidth(" in shell and "aria-valuenow" not in shell
    assert "style=" not in handle_js and "style." not in handle_js
    assert 'aria-keyshortcuts="Control+B"' in shell and "isSidebarKey" in shell
    sheet = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "style=" not in sheet
    handle = re.search(r"\.resize-handle \{([^}]*)\}", sheet)[1]
    assert "width: 8px" in handle and "cursor: col-resize" in handle and "touch-action: none" in handle
    assert re.search(r"@media \(pointer: coarse\) \{ \.resize-handle \{ display: none; \} \}", sheet)   # no drag handle on touch


def test_the_footer_keeps_a_slot_for_the_limits_chip_and_the_blocks_of_the_next_cards_are_marked():
    shell = ui_text(JS / "shell.js")
    assert 'data-slot="limits"' in shell and "/* GUI4-T10 */" in shell
    assert shell.index("limitsSlot") < shell.index("side-footer-tools")           # above «Personalizar» and «Ayuda»
    assert shell.count("helpFor(\"shell.help_mode\"") == 1   # one «Ayuda» button
    sidebar = ui_text(JS / "sidebar.js")
    assert "/* GUI4-T08 */" in sidebar and sidebar.index("/* GUI4-T08 */") < sidebar.index("const paletteEntries")
    assert "HELP_ROW" in ui_text(JS / "sidebar-model.js")                            # not touched here (GUI4-T06)


def test_view_plan_is_offered_only_while_the_floor_plan_view_is_registered():
    sidebar = ui_text(JS / "sidebar.js")
    assert 'isRegisteredView("floor_plan")' in sidebar
    assert "plan && html" in sidebar and 'routeHref("floor_plan", office.id)' in sidebar
    # the «+» of an office is a button beside the summary, not inside it
    office = sidebar[sidebar.index("function OfficeRow"):sidebar.index("export function Sidebar")]
    summary = office[office.index("<summary"):office.index("</summary>")]
    assert "<button" not in summary
    assert office.index("</details>") < office.index('class="side-mini office-plus"')


def test_new_texts_are_in_english_and_spanish_and_the_old_spanish_is_untouched():
    en = (JS.parent / "i18n" / "en" / "shell.js").read_text(encoding="utf-8")
    es = (JS.parent / "i18n" / "es" / "shell.js").read_text(encoding="utf-8")
    keys = ["hide_sidebar", "show_sidebar", "show_panel", "resize_sidebar", "resize_panel", "offices_heading", "projects_heading",
            "recent_heading", "add_office", "new_in", "show_more", "show_fewer", "view_plan", "office_areas", "needs_attention",
            "no_offices", "no_projects"]
    for key in keys:
        assert f'"shell.{key}"' in en and f'"shell.{key}"' in es, key
    # the Spanish that already existed in the bar, letter by letter
    for key, text in {"new_session": "Nueva sesión", "search": "Buscar", "more": "Más", "understood": "Entendido",
                      "sessions_heading": "Sesiones", "view_all": "Ver todas", "customize": "Personalizar", "help": "Ayuda"}.items():
        assert f'"shell.{key}": "{text}"' in es, key


# --- Round 2 (review findings 1 to 4 and the waiting_human mark) ---

@needs_node
def test_a_run_that_waits_for_a_person_is_marked_as_needing_attention():
    model = json.dumps((JS / "sidebar-model.js").as_uri())
    out = run_node(f"""
      import {{ runItems }} from {model};
      const items = runItems([{{ id: "a", status: "waiting_human" }}, {{ id: "b", status: "running" }}, {{ id: "c", status: "failed" }}]);
      console.log(JSON.stringify(items.map((item) => item.attention)));
    """)
    assert out == [True, False, False]


def test_escape_closes_the_overlay_panel_and_the_focus_and_inert_follow_it():
    shell = ui_text(JS / "shell.js")
    assert "const overlay = panelShown && narrow" in shell
    # Escape (capture phase, so session-detail.js does not see it): the drawer first, then the panel; help and dialogs keep their own
    handler = shell[shell.index("const onKey = (event) => {"):shell.index('document.addEventListener("keydown", onKey, true)')]
    assert "state.current.overlay" in handler and "closePanelOverlay(true)" in handler and "closeDrawer(true)" in handler
    assert ".help-block.open, dialog[open]" in handler and handler.index("closeDrawer(true)") < handler.index("closePanelOverlay(true)")
    assert "panelBox.current.focus()" in shell and "panelButton.current.focus()" in shell     # in on open, back to the top-bar button
    assert 'onClick=${() => closePanelOverlay(true)}' in shell                                # the scrim gives the focus back too
    assert "inert=${drawerOpen || overlay}" in shell                                          # <main> is inert under the overlay
    assert 'tabindex="-1" ref=${panelBox}' in shell


def test_hiding_and_showing_the_sidebar_moves_the_focus_and_makes_it_inert():
    shell = ui_text(JS / "shell.js")
    assert "inert=${sideGone}" in shell and "topbar.inert = sideGone" in shell   # #sidebar and .topbar
    effect = shell[shell.index("Hiding the sidebar moves the focus"):shell.index("// Any change of address")]
    assert "edgeSideButton.current" in effect and "sideHideButton.current" in effect and "[sideGone]" in effect
    assert "state.current.sideFocus = true" in shell     # every route (Ctrl+B, the button, the edge tab, a drag) goes through toggleSide


def test_the_context_button_and_its_help_hide_together_on_desktop():
    shell = ui_text(JS / "shell.js")
    top = shell[shell.index('<div class="panel-tool panel-only">'):shell.index("</header>")]
    assert 'topic="shell.panel" scope="top"' in top and top.index("<${Help}") > top.index("</button>") and "</div>" in top
    sheet = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\n\.panel-only \{ display: none; \}", sheet)


def test_recent_has_one_show_more_button_with_aria_expanded():
    sidebar = ui_text(JS / "sidebar.js")
    block = sidebar[sidebar.index('id="recent"'):sidebar.index("</${SideSection}>", sidebar.index('id="recent"'))]
    assert block.count('class="show-list-more"') == 1 and "aria-expanded=${recentExpanded" in block
    assert "setRecentExpanded((value) => !value)" in block


def test_edge_tab_touch_area_and_the_office_marker_and_the_notice_hover():
    sheet = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.edge-side::before \{ left: 0; right: -16px; \}", sheet)      # 28 visible + 16 = 44 px
    assert re.search(r"\.edge-panel::before \{ left: -16px; right: 0; \}", sheet)
    assert r'.office-folder::before { content: "\25B8"' in sheet and r'details[open] > .office-folder::before { content: "\25BE"' in sheet
    assert ".side-more-notice .btn.secondary:hover:not(:disabled) { background: var(--line-strong); }" in sheet
