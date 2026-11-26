# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T03: the pure sidebar navigation and its remembered choices (Node, no server)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")


def run_node(source: str) -> dict:
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
        cwd=STATIC_DIR, timeout=20, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_section_has_one_route_with_extensions_and_pins():
    model = json.dumps((JS / "sidebar-model.js").as_uri())
    extensions = json.dumps((JS / "extensions.js").as_uri())
    route = json.dumps((JS / "route.js").as_uri())
    out = run_node(f"""
      import {{ navigationSections }} from {model};
      import {{ registerView }} from {extensions};
      import {{ parseRoute }} from {route};
      const builtins = ["sessions", "runs", "usage", "evaluation", "teams", "plans", "map"]
        .map((id) => ({{ id, label: id }}));
      const ext = [
        {{ id: "project", label: "Proyectos", group: "primary", icon: "folder" }},
        {{ id: "apps", label: "Apps y agentes", group: "primary", icon: "apps" }},
        {{ id: "approvals", label: "Aprobaciones", group: "pending", icon: "shield" }},
        {{ id: "extra", label: "Extra" }},
      ];
      for (const view of ext) registerView({{ ...view, component: () => null, owner: view.id }});
      const first = navigationSections(builtins, ext);
      const pinned = navigationSections(builtins, ext, ["runs", "approvals", "missing"]);
      const ids = (nav) => [...nav.primary, ...nav.more, ...nav.pending].map((item) => item.id);
      console.log(JSON.stringify({{
        primary: first.primary.map((item) => item.id),
        more: first.more.map((item) => item.id),
        tabs: first.pending.map((item) => item.id),
        pinnedTabs: pinned.pending.map((item) => item.id),
        pinnedPrimary: pinned.primary.map((item) => item.id),
        pinnedMore: pinned.more.map((item) => item.id),
        unique: new Set(ids(first)).size === ids(first).length && new Set(ids(pinned)).size === ids(pinned).length,
        routes: ids(first).map((id) => parseRoute("#" + id).section),
      }}));
    """)
    assert out["primary"] == ["project", "apps"]
    # GUI3-T05: a view of «Pendientes» is a tab of #pending, not a row of «Más»; every section is still listed once
    assert out["more"] == ["sessions", "runs", "usage", "evaluation", "teams", "plans", "map", "extra"]
    assert out["tabs"] == ["approvals"] and out["pinnedTabs"] == []
    assert out["pinnedPrimary"] == ["runs", "project", "apps", "approvals"]
    assert out["pinnedMore"] == ["sessions", "usage", "evaluation", "teams", "plans", "map", "extra"]
    assert out["unique"] and out["routes"] == out["primary"] + out["more"] + out["tabs"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_pins_and_more_notice_work_with_blocked_storage():
    state = json.dumps((JS / "shell-state.js").as_uri())
    out = run_node(f"""
      import {{ readPinnedSections, readMoreNoticeSeen, writeMoreNoticeSeen }} from {state};
      const values = new Map();
      globalThis.window = {{ localStorage: {{
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
      }} }};
      const initial = [readPinnedSections(), readMoreNoticeSeen()];
      values.set("multiagent.gui.pinned-sections", JSON.stringify(["runs", 3, "apps"]));
      writeMoreNoticeSeen();
      const saved = [readPinnedSections(), readMoreNoticeSeen()];
      globalThis.window = {{ get localStorage() {{ throw Error("blocked"); }} }};
      const blocked = [readPinnedSections(), readMoreNoticeSeen()];
      writeMoreNoticeSeen();
      console.log(JSON.stringify({{ initial, saved, blocked }}));
    """)
    assert out == {
        "initial": [[], False],
        "saved": [["runs", "apps"], True],
        "blocked": [[], False],
    }


def test_more_disclosure_and_notice_remain_accessible():
    sidebar = ui_text((JS / "sidebar.js"))
    assert 'aria-controls="side-more"' in sidebar and 'aria-expanded=${moreOpen ? "true" : "false"}' in sidebar
    assert 'hidden=${!moreOpen}' in sidebar
    assert 'readMoreNoticeSeen' in sidebar and 'writeMoreNoticeSeen()' in sidebar
    assert 'onClick=${dismissNotice}>Entendido' in sidebar
    assert 'if (!noticeSeen) dismissNotice()' in sidebar
    assert 'route.section !== "sessions" && navigation.more.some((section) => section.id === route.section)' in sidebar
    assert 'aria-current=${s.id === route.section ? "page" : null}' in sidebar
    assert sidebar.count('data-help-exempt="navigation"') == 9   # GUI4-T03: the 3 of before + 6 (section toggle and «+», «Ver más» twice, office and area «+»)


def test_sidebar_footer_and_the_pending_row_stay_reachable_on_desktop():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    # The connection state and «Ayuda» stay at the bottom of the sidebar, not after the whole session list.
    footer = re.search(r"\n\.side-footer \{([^}]*)\}", css).group(1)
    assert "position: sticky" in footer and "bottom: 0" in footer and "background: var(--chrome)" in footer
    # «Pendientes» is a link to its own screen (GUI3-T05): no floating list is left, and the sidebar still paints
    # above the right panel so its "?" texts are not covered.
    panel_z = int(re.search(r"\n\.rightpanel \{[^}]*z-index: (\d+)", css).group(1))
    t03 = css[css.index("/* GUI3-T03"):css.index("/* GUI3-T02 help mode")]
    assert int(re.search(r"\n\.app \.sidebar \{ z-index: (\d+); \}", t03).group(1)) > panel_z
    assert ".pending-popover" not in css
    # Narrow screens: the drawer keeps its z-index.
    narrow = re.search(r"@media \(max-width: 899\.98px\) \{(.*?)\n\}", t03, re.S).group(1)
    assert "z-index: 40;" in re.search(r"\.app \.sidebar \{[^}]*\}", narrow).group(0)
    phone = re.search(r"@media \(max-width: 599\.98px\) \{(.*?)\n\}", t03, re.S).group(1)
    # The thin bar over the content pulls into the 24 px padding of wider screens; below 600 px the padding is 12 px,
    # so it keeps its sides and «Contexto» does not touch the edge of the screen.
    assert re.search(r"\n\.content-toolbar \{[^}]*margin: -12px -12px 12px;", t03)
    assert ".content-toolbar { margin: -4px 0 8px; }" in phone


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_icons_are_decorative_svg_and_unknown_names_fall_back_to_a_dot():
    icon = json.dumps((JS / "ui" / "icon.js").as_uri())
    out = run_node(f"""
      import {{ Icon, ICON_NAMES }} from {icon};
      const folder = Icon({{ name: "folder" }});
      const unknown = Icon({{ name: "not_registered" }});
      console.log(JSON.stringify({{
        count: ICON_NAMES.length,
        root: folder.type,
        viewBox: folder.props.viewBox,
        hidden: folder.props["aria-hidden"],
        focusable: folder.props.focusable,
        shape: folder.props.children.type,
        fallback: unknown.props.children.type,
      }}));
    """)
    assert out == {
        "count": 25, "root": "svg", "viewBox": "0 0 24 24", "hidden": "true",
        "focusable": "false", "shape": "path", "fallback": "circle",
    }
