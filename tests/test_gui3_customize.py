# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T07: «Personalizar». The pure model and the stored choices run in Node (with and without storage); no TestClient."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from gui_text import ui_text

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(name: str) -> str:
    return ui_text(JS / name)


def run_node(source: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


STORAGE = """
  const data = new Map();
  const blocked = %s;
  globalThis.window = { localStorage: {
    getItem(key) { if (blocked) throw new Error("denied"); return data.has(key) ? data.get(key) : null; },
    setItem(key, value) { if (blocked) throw new Error("denied"); data.set(key, String(value)); },
  } };
"""


@needs_node
def test_pin_and_release_keep_order_and_offer_only_the_sections_that_are_not_fixed():
    out = run_node(f"""
      import {{ pinnableSections, togglePinned }} from {uri("customize-model.js")};
      const sections = [
        {{ id: "sessions", label: "Sesiones" }}, {{ id: "usage", label: "Uso" }}, {{ id: "customize", label: "Personalizar" }},
        {{ id: "apps", label: "Apps", group: "primary" }}, {{ id: "continuity", label: "Decisiones", group: "pending" }}, {{ id: "usage", label: "Otra vez" }},
      ];
      console.log(JSON.stringify({{
        rows: pinnableSections(sections, ["usage"]).map((row) => [row.id, row.pinned]),
        add: togglePinned(["a"], "b"), drop: togglePinned(["a", "b", "c"], "b"), junk: togglePinned(["a", 3], 7), bad: togglePinned(null, "x"),
      }}));
    """)
    assert out["rows"] == [["sessions", False], ["usage", True], ["continuity", False]]   # not «Personalizar», not the fixed «Apps»
    assert out["add"] == ["a", "b"] and out["drop"] == ["a", "c"] and out["junk"] == ["a"] and out["bad"] == ["x"]


@needs_node
def test_card_modes_use_the_registered_default_and_ignore_invalid_ones():
    out = run_node(f"""
      import {{ cardRows, withCardMode }} from {uri("customize-model.js")};
      const extra = [{{ name: "project", title: "Proyecto", defaultMode: "always" }}, {{ name: "alerts", title: "Alertas" }}];
      const modes = withCardMode({{ tokens: "never" }}, "changes", "always");
      console.log(JSON.stringify({{
        rows: cardRows(extra, modes).map((row) => [row.name, row.mode]),
        invalid: withCardMode(modes, "sources", "sometimes"), keep: withCardMode(modes, "sources", "auto"),
      }}));
    """)
    assert out["rows"] == [["changes", "always"], ["subagents", "auto"], ["tokens", "never"], ["sources", "auto"],
                           ["project", "always"], ["alerts", "auto"]]
    assert out["invalid"] == {"tokens": "never", "changes": "always"} and out["keep"]["sources"] == "auto"


@needs_node
def test_choices_are_stored_read_back_and_reset():
    out = run_node(STORAGE % "false" + f"""
      const state = await import({uri("shell-state.js")});
      const heard = [];
      const off = state.onChoiceChange((key) => heard.push(key));
      state.writePinnedSections(["usage", "usage", 4, "map"]);
      state.writeCardModes({{ tokens: "never", changes: "always", bad: "x" }});
      state.writeHelpMode(true);
      const after = {{ pinned: state.readPinnedSections(), modes: state.readCardModes(), help: state.readHelpMode(), remembers: state.storageRemembers() }};
      state.resetChoices();
      off();
      state.writeHelpMode(true);
      console.log(JSON.stringify({{ after, reset: {{ pinned: state.readPinnedSections(), modes: state.readCardModes() }}, heard: heard.length,
        stored: [...data.keys()].sort() }}));
    """)
    assert out["after"] == {"pinned": ["usage", "map"], "modes": {"tokens": "never", "changes": "always"}, "help": True, "remembers": True}
    assert out["reset"] == {"pinned": [], "modes": {}} and out["heard"] == 7      # three writes and four resets (the accent too, GUI3-T09); none after off()
    assert "multiagent.gui.pinned-sections" in out["stored"] and "multiagent.gui.context-cards" in out["stored"]


@needs_node
def test_without_storage_the_change_still_reaches_the_page_but_is_not_remembered():
    out = run_node(STORAGE % "true" + f"""
      const state = await import({uri("shell-state.js")});
      const heard = [];
      state.onChoiceChange((change) => heard.push(change));
      const before = {{ help: state.readHelpMode(), pinned: state.readPinnedSections(), remembers: state.storageRemembers() }};
      state.writePinnedSections(["usage"]);
      state.writeCardModes({{ tokens: "never" }});
      state.writeHelpMode(true);
      console.log(JSON.stringify({{ before, heard, now: {{ pinned: state.readPinnedSections(), modes: state.readCardModes(), help: state.readHelpMode() }}, stored: data.size }}));
    """)
    assert out["before"] == {"help": False, "pinned": [], "remembers": False}
    assert out["heard"] == [{"pinned": ["usage"]}, {"modes": {"tokens": "never"}}, {"help": True}]    # the value travels with the message
    assert out["now"] == {"pinned": [], "modes": {}, "help": False} and out["stored"] == 0             # after a reload: the defaults of T02, T03, T04


def test_the_screen_has_its_three_blocks_and_every_control_is_labelled():
    source = read("customize.js")
    assert 'aria-pressed=${row.pinned ? "true" : "false"}' in source
    # The row shows the name once and a short «Fijar» beside it; the button's name still says which section it pins.
    assert 't("customize.pin_label", { name: row.label })' in source and ">Fijar</button>" in source and "Fijar ${row.label}" not in source
    assert "<fieldset" in source and "<legend>" in source and 'type="radio"' in source and "CARD_MODES.map" in source
    assert "Mostrar siempre los «?»" in source and "writeHelpMode(event.target.checked)" in source     # the state of T02, not a copy
    assert "Restablecer" in source and "resetChoices" in source and "no guarda las preferencias" in source
    assert "localStorage" not in source and "innerHTML" not in source
    assert source.count("helpFor(") >= 4


def test_the_colours_block_is_a_labelled_fieldset_of_radios_and_reset_returns_to_neutral():
    source = read("customize.js")        # GUI3-T09: the palettes themselves are checked in test_gui3_accent_palettes.py
    block = source[source.index('aria-label="Colores"'):source.index('aria-label="Ayuda"')]
    assert "<fieldset" in block and "<legend>Paleta</legend>" in block and 'type="radio"' in block and "writeAccent(" in block
    assert "writeAccent(DEFAULT_ACCENT)" in read("shell-state.js")


def test_the_route_is_reserved_reachable_and_drawn():
    assert '"customize"' in re.search(r"const SECTION_IDS = \[(.*?)\]", read("route.js"))[1]
    assert '"customize"' in re.search(r"RESERVED_SECTIONS = \[(.*?)\]", read("extensions.js"))[1]
    main = read("main.js")
    assert '{ id: "customize", label: "Personalizar", icon: "settings", mode: "simple" }' in main   # SIM-T02: the mode is explicit
    assert "<${Customize} token=${token} sections=${allSections()} mode=${mode} />" in main
    shell = read("shell.js")
    assert 'navigate("customize")' in shell and 'helpFor("shell.customize")' in shell     # the gear of the sidebar footer
    sidebar = read("sidebar.js")
    assert "onChoiceChange((change) => { if (change.pinned) setPinned(change.pinned); })" in sidebar               # pinning shows without reloading
    assert "writePanelOpen" in shell and "onChoiceChange" in shell


def test_the_stylesheet_has_the_block_with_44px_targets_and_no_new_accent():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* GUI3-T07 */"):css.index("/* GUI3-T09 */")]   # T09 has its own block after this one
    assert css.index("/* GUI3-T05 */") < css.index("/* GUI3-T07 */")
    assert "--accent:" not in block and "url(" not in block and "style=" not in block
    for selector in (".side-search-btn", ".palette-option", ".customize-row"):
        assert re.search(re.escape(selector) + r"[^{]*\{[^}]*min-height: var\(--target\)", block), selector
    assert "@media (max-width: 599.98px)" in block and block.count("@media") == 1


def test_a_pin_row_keeps_the_name_and_its_button_on_one_line_on_a_phone():
    # Below 600 px the name and «Fijar» stay side by side: a full-width button under each name doubled the list.
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* GUI3-T07 */"):css.index("/* GUI3-T09 */")]   # T09 has its own block after this one
    phone = block[block.index("@media (max-width: 599.98px)"):]
    assert ".customize-row .btn" not in phone
