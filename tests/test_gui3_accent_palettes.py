# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T09: the accent palettes of «Personalizar». Static checks of the stylesheet and the scripts, plus Node for the stored
choice; no TestClient."""
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
PALETTES = ("azul", "verde", "violeta")
TOKENS = ("accent", "on-accent", "accent-soft")
BACKGROUNDS = ("bg", "panel", "chrome", "hover", "accent-soft", "warn-soft")
CSS = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
BLOCK = CSS[CSS.index("/* GUI3-T09 */"):]
DARK_AT = BLOCK.index("@media (prefers-color-scheme: dark)")
LIGHT, DARK = BLOCK[:DARK_AT], BLOCK[DARK_AT:]
RULE = re.compile(r'\.app\[data-accent="([a-z]+)"\][^{}]*\{([^{}]*)\}')


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def colours(body: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})\b", body))


def root_themes() -> dict[str, dict[str, str]]:
    head = CSS[:CSS.index("/* GUI3-T09 */")]
    dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{([^{}]*)\}", head)[1]
    light = colours(re.search(r":root \{([^{}]*)\}", head)[1])
    return {"light": light, "dark": {**light, **colours(dark)}}


def palettes() -> dict[str, dict[str, dict[str, str]]]:
    found: dict[str, dict[str, dict[str, str]]] = {}
    for theme, part in (("light", LIGHT), ("dark", DARK)):
        for name, body in RULE.findall(part):
            found.setdefault(name, {})[theme] = colours(body)
    return found


def luminance(colour: str) -> float:
    channels = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_each_palette_has_its_three_tokens_in_light_and_in_dark_and_nothing_else():
    found = palettes()
    assert set(found) == set(PALETTES)                  # «Neutro» has no block: it is :root
    for name, by_theme in found.items():
        assert set(by_theme) == {"light", "dark"}, name
        for theme, values in by_theme.items():
            assert set(values) == set(TOKENS), (name, theme)    # no palette changes a background, the text or a tone


def test_the_accent_and_its_text_pass_4_5_to_1_on_every_surface_in_both_themes():
    base = root_themes()
    for name, by_theme in palettes().items():
        for theme, values in by_theme.items():
            surface = {**base[theme], **values}
            for background in BACKGROUNDS:
                ratio = contrast(surface["accent"], surface[background])
                assert ratio >= 4.5, f"{name}/{theme}: --accent on --{background} is {ratio:.2f}:1"
            assert contrast(surface["on-accent"], surface["accent"]) >= 4.5, f"{name}/{theme}: --on-accent on --accent"
    for theme, surface in base.items():    # «Neutro» too
        assert min(contrast(surface["accent"], surface[b]) for b in BACKGROUNDS) >= 4.5, theme
        assert contrast(surface["on-accent"], surface["accent"]) >= 4.5, theme


def test_the_block_follows_the_others_has_no_inline_style_or_url_and_uses_no_new_breakpoint():
    assert CSS.index("/* GUI3-T07 */") < CSS.index("/* GUI3-T09 */") and CSS.count("/* GUI3-T09 */") == 1
    for forbidden in ("url(", "style=", "@import", "http://", "https://"):
        assert forbidden not in BLOCK, forbidden
    assert BLOCK.count("@media") == 1                           # only the dark scheme; no width cut is needed
    assert BLOCK.count("--accent:") == 6                        # three palettes, two themes


def test_the_swatch_takes_its_colour_from_the_stylesheet_not_from_the_script():
    source = read("customize.js")
    assert "data-accent=${option.id}" in source and "style=" not in source and "style:" not in source
    assert not re.search(r"#[0-9a-fA-F]{6}\b", source + read("customize-model.js") + read("shell.js"))
    assert '.accent-swatch[data-accent="azul"]' in BLOCK


def test_the_screen_has_its_fieldset_with_a_radio_per_palette_and_help():
    source = read("customize.js")
    assert 'aria-label="Colores"' in source and "<legend>Paleta</legend>" in source and 'type="radio" name="accent"' in source
    assert "ACCENTS.map" in source and 'helpFor("customize.accent")' in source and 'topic="customize.accent"' in source
    assert "writeAccent(option.id)" in source and "change.accent" in source
    assert '"customize.accent": {' in read("help-text.js")
    assert "localStorage" not in source


def test_the_shell_puts_the_palette_on_the_app_and_follows_the_choice():
    shell = read("shell.js")
    assert "data-accent=${accent}" in shell and "useState(readAccent)" in shell and "change.accent" in shell
    assert "localStorage" not in shell and "localStorage" not in read("customize-model.js")


def run_node(source: str) -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


STORAGE = """
  const data = new Map();
  const blocked = %s;
  globalThis.window = { localStorage: {
    getItem(key) { if (blocked) throw new Error("denied"); return data.has(key) ? data.get(key) : null; },
    setItem(key, value) { if (blocked) throw new Error("denied"); data.set(key, String(value)); },
  } };
"""


@needs_node
def test_the_accent_is_stored_announced_and_an_unknown_value_is_neutral():
    uri = json.dumps((JS / "shell-state.js").as_uri())
    out = run_node(STORAGE % "false" + f"""
      const state = await import({uri});
      const heard = [];
      state.onChoiceChange((change) => heard.push(change));
      const first = state.readAccent();
      state.writeAccent("verde");
      const stored = [state.readAccent(), data.get("multiagent.gui.accent")];
      data.set("multiagent.gui.accent", "rosa");
      const unknown = state.readAccent();
      state.writeAccent("violeta");
      state.writeAccent("nope");
      state.writeAccent("violeta");
      state.resetChoices();
      console.log(JSON.stringify({{ first, stored, unknown, heard: heard.filter((c) => c.accent).map((c) => c.accent), after: state.readAccent() }}));
    """)
    assert out["first"] == "neutral" and out["stored"] == ["verde", "verde"] and out["unknown"] == "neutral"
    assert out["heard"] == ["verde", "violeta", "neutral", "violeta", "neutral"] and out["after"] == "neutral"


@needs_node
def test_without_storage_the_accent_applies_to_the_open_page_but_comes_back_neutral():
    uri = json.dumps((JS / "shell-state.js").as_uri())
    out = run_node(STORAGE % "true" + f"""
      const state = await import({uri});
      const heard = [];
      state.onChoiceChange((change) => heard.push(change));
      state.writeAccent("azul");
      console.log(JSON.stringify({{ heard, now: state.readAccent(), stored: data.size }}));
    """)
    assert out == {"heard": [{"accent": "azul"}], "now": "neutral", "stored": 0}
