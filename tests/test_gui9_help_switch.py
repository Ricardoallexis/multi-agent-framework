# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T06: the help switch of «Personalizar» and the «Guía rápida y atajos» dialog.

Pure Node checks on quick-guide-model.js (skipped if Node is absent) plus static checks of the sources; no TestClient."""
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


def raw(path) -> str:
    return path.read_text(encoding="utf-8")


def dictionary(lang: str, name: str) -> dict:
    """The keys of one i18n dictionary, read by Node so no parsing rule is repeated here."""
    uri = json.dumps((STATIC_DIR / "i18n" / lang / name).as_uri())
    result = subprocess.run([NODE, "--input-type=module", "-e", f"const m = await import({uri}); console.log(JSON.stringify(m.default));"],
                            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    return json.loads(result.stdout)


def run_node(source: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


SPANISH = """
  globalThis.window = { localStorage: { getItem: () => null, setItem: () => {} } };
  globalThis.document = { documentElement: { lang: "" } };
  Object.defineProperty(globalThis, "navigator", { value: { language: "es-MX" }, configurable: true });
"""


@needs_node
def test_the_guide_lists_only_shortcuts_the_application_really_handles():
    uri = lambda name: json.dumps((JS / name).as_uri())   # noqa: E731
    out = run_node(SPANISH + f"""
      const guide = await import({uri("quick-guide-model.js")});
      const state = await import({uri("shell-state.js")});
      const palette = await import({uri("command-palette-model.js")});
      const rows = guide.shortcutRows();
      const handled = {{
        help: state.isHelpKey({{ key: "F1" }}),
        sidebar: state.isSidebarKey({{ key: "b", ctrlKey: true }}),
        search: palette.isPaletteKey({{ key: "k", ctrlKey: true }}),
      }};
      console.log(JSON.stringify({{ rows, handled }}));
    """)
    assert [(row["id"], row["keys"]) for row in out["rows"]] == [
        ("help", ["F1"]), ("sidebar", ["Ctrl", "B"]), ("search", ["Ctrl", "K"]), ("close", ["Esc"])]
    assert out["handled"] == {"help": True, "sidebar": True, "search": True}   # each listed key is a key the code answers to
    assert out["rows"][0]["text"] == "Activa o desactiva la ayuda."


@needs_node
def test_the_texts_follow_the_language_and_are_computed_when_asked():
    uri = json.dumps((JS / "quick-guide-model.js").as_uri())
    i18n = json.dumps((JS / "i18n.js").as_uri())
    out = run_node(SPANISH + f"""
      const guide = await import({uri});
      const ui = await import({i18n});
      const es = guide.guideBlocks();
      ui.setLanguage("en");
      const en = guide.guideBlocks();
      console.log(JSON.stringify({{ es, en, enKey: guide.shortcutRows()[1].text }}));
    """)
    assert out["es"]["again"].startswith("Pulsa F1") and "Personalizar" in out["es"]["again"]
    assert out["en"]["again"].startswith("Press F1") and "Customize" in out["en"]["again"]
    assert len(out["en"]["intro"]) == 2 and out["enKey"] == "Hide or show the sidebar."


@needs_node
def test_the_request_reaches_the_listener_and_stops_after_it_is_removed():
    uri = json.dumps((JS / "quick-guide-model.js").as_uri())
    out = run_node(SPANISH + f"""
      const guide = await import({uri});
      const target = new EventTarget();
      let heard = 0;
      const stop = guide.onQuickGuide(() => {{ heard += 1; }}, target);
      const first = guide.requestQuickGuide(target);
      stop();
      guide.requestQuickGuide(target);
      console.log(JSON.stringify({{ first, heard, none: guide.requestQuickGuide(null), noop: typeof guide.onQuickGuide(() => {{}}, null) }}));
    """)
    assert out == {"first": True, "heard": 1, "none": False, "noop": "function"}


def test_the_help_row_of_customize_is_a_switch_with_a_visible_state():
    source = raw(JS / "customize.js")
    block = source[source.index('aria-label=${t("customize.help")}'):source.index('<div class="customize-actions">')]
    assert 'class="switch"' in block and 'role="switch"' in block and 'aria-checked=${helpOn ? "true" : "false"}' in block
    assert 'type="checkbox"' not in block                                   # the checkbox became the switch
    assert "writeHelpMode(!helpOn)" in block and "readHelpMode" in source   # the same state as F1 and the footer button
    assert 'helpFor("customize.help")' in block and 'topic="customize.help"' in block
    assert 't("customize.help_on")' in block and 't("customize.help_off")' in block
    assert 'aria-labelledby="customize-help-label"' in block and 'id="customize-help-label"' in block
    assert "style=" not in source


def test_the_footer_and_more_rows_open_the_guide_and_the_help_toggle_is_kept():
    shell, sidebar = raw(JS / "shell.js"), raw(JS / "sidebar.js")
    assert "requestQuickGuide()" in shell and "<${QuickGuide} />" in shell and shell.count('helpFor("shell.quick_guide")') == 1
    assert 'aria-pressed=${helpOn ? "true" : "false"}' in shell and "onClick=${toggleHelp}" in shell   # «Ayuda» still toggles
    assert "requestQuickGuide()" in sidebar and 'helpFor("shell.quick_guide", { scope: "more" })' in sidebar
    assert "writeHelpMode(true)" in sidebar                                                           # the «Más» help row still turns it on
    assert 'topic="shell.quick_guide"' in shell and 'topic="shell.quick_guide" scope="more"' in sidebar


def test_the_dialog_is_native_traps_the_focus_and_gives_it_back():
    source = raw(JS / "quick-guide.js")
    assert "<dialog" in source and "showModal()" in source and 'aria-labelledby="quick-guide-title"' in source
    assert 'event.key === "Escape"' in source and "event.preventDefault()" in source          # Esc closes only the guide
    assert "document.activeElement" in source and "back.focus()" in source                      # the focus returns to the opener
    assert "closeButton.current.focus()" in source                                              # and starts inside the dialog
    assert "innerHTML" not in source and "style=" not in source and "localStorage" not in source
    model = raw(JS / "quick-guide-model.js")
    assert "document" not in model.replace("no DOM", "") and "localStorage" not in model


def test_no_shortcut_the_browser_keeps_is_listed():
    texts = dictionary("en", "quick-guide.js")
    listed = " ".join(texts.values())
    assert "Ctrl+N" not in listed and "Ctrl+T" not in listed and "Ctrl+W" not in listed
    model = raw(JS / "quick-guide-model.js")
    assert re.findall(r'keys: \[([^\]]*)\]', model) == ['"F1"', '"Ctrl", "B"', '"Ctrl", "K"', '"Esc"']


@needs_node
def test_both_languages_have_the_same_new_texts():
    english, spanish = dictionary("en", "quick-guide.js"), dictionary("es", "quick-guide.js")
    assert set(english) == set(spanish) and english["quick_guide.title"] == "Quick guide and shortcuts"
    assert spanish["quick_guide.title"] == "Guía rápida y atajos" and spanish["quick_guide.open"] == "Guía rápida"
    on_off = {lang: (dictionary(lang, "customize.js")["customize.help_on"], dictionary(lang, "customize.js")["customize.help_off"])
              for lang in ("en", "es")}
    assert on_off == {"en": ("On", "Off"), "es": ("Activada", "Desactivada")}
    assert dictionary("es", "customize.js")["customize.show_help"] == "Mostrar siempre los «?»"   # the Spanish that existed is unchanged
    assert dictionary("es", "shell.js")["help.shell.quick_guide"]["title"] == "Guía rápida y atajos"


def test_the_dictionaries_are_joined_once_and_the_css_block_has_no_inline_style():
    i18n = raw(JS / "i18n.js")
    assert i18n.count("quickGuideEn") == 2 and i18n.count("quickGuideEs") == 2
    sheet = raw(STATIC_DIR / "app.css")
    block = sheet[sheet.index("/* GUI4-T06 */"):]
    assert ".quick-guide {" in block and ".switch-state" in block and "var(--target)" in block and "box-shadow: var(--shadow)" in block
    assert "style=" not in block and "@import" not in block and "url(" not in block
    assert sheet.rstrip().count("/* GUI4-T06 */") == 1


def test_round_2_the_dialog_lives_outside_the_sidebar_and_the_more_row_keeps_the_drawer():
    shell, sidebar = raw(JS / "shell.js"), raw(JS / "sidebar.js")
    nav_end = shell.index("</nav>")
    assert shell.count("<${QuickGuide} />") == 1 and shell.index("<${QuickGuide} />") > nav_end      # not inside <nav id="sidebar">
    assert shell.index("<${QuickGuide} />") > shell.index("<aside id=\"right-panel\"")               # a direct child of the app container
    assert shell.count("GUI4-T06") >= 3                                                              # the import and both blocks are marked
    row = sidebar[sidebar.index('<button type="button" class="side-help-row" aria-haspopup="dialog"'):]
    assert "data-nav" not in row[:row.index("</button>")]                                            # it does not close the drawer
    assert "requestQuickGuide()" in row[:row.index("</button>")]


def test_round_2_customize_opens_the_same_guide_with_its_own_help():
    source = raw(JS / "customize.js")
    assert 'import { requestQuickGuide } from "./quick-guide-model.js"' in source
    assert 'helpFor("shell.quick_guide", { scope: "customize" })' in source and "onClick=${() => requestQuickGuide()}" in source
    assert 'topic="shell.quick_guide" scope="customize"' in source and 't("quick_guide.title")' in source


def test_round_2_ctrl_b_ignores_an_open_dialog_and_the_focus_has_a_fallback():
    shell = raw(JS / "shell.js")
    handler = shell[shell.index("const onSideKey"):shell.index('removeEventListener("keydown", onSideKey)')]
    assert 'document.querySelector("dialog[open]")' in handler and handler.index("dialog[open]") < handler.index("toggleSide(")
    guide = raw(JS / "quick-guide.js")
    assert 'closest("[inert]")' in guide and 'visibility === "hidden"' in guide and "getClientRects().length === 0" in guide
    assert 'document.querySelector(".edge-side") || document.getElementById("main-content")' in guide
    assert "!hidden(back)" in guide and "fallbackFocus()" in guide
