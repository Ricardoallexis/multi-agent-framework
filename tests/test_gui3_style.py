# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T01: static checks for the calm neutral base stylesheet, type scale, borderless buttons and link underlines."""
from __future__ import annotations

import re

from multiagent.gui import STATIC_DIR
from test_gui2_components import contrast, rule, themes

CORE_SHEETS = [
    STATIC_DIR / "app.css",
    STATIC_DIR / "new-session.css",
    STATIC_DIR / "panels.css",
    STATIC_DIR / "agent-tree.css",
    STATIC_DIR / "subagents-panel.css",
]
# Navigation rows and buttons, not prose. «Pendientes» in the sidebar is a link row since GUI3-T05,
# marked by aria-current and the accent bar like the other rows.
ALLOWED_NO_UNDERLINE = {".skip-link", ".side-item", ".session-row", "a.btn", ".ns-card", ".sub-item",
                        ".side .views a.side-link"}


def all_sheets():
    return [*CORE_SHEETS, *sorted((STATIC_DIR / "extensions").glob("*/style.css"))]


def strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def test_new_neutral_tokens_exist_in_both_themes_and_on_accent_passes_contrast():
    palette = themes()
    for theme in ("light", "dark"):
        colours = palette[theme]
        for name in ("hover", "accent-soft", "warn-soft"):
            assert name in colours, f"{theme}: missing --{name}"
        ratio = contrast(colours["on-accent"], colours["accent"])
        assert ratio >= 4.5, f"{theme}: --on-accent on --accent is {ratio:.2f}:1"


def test_no_font_weight_above_600_or_bold_in_any_gui_stylesheet():
    for path in all_sheets():
        css = strip_comments(path.read_text(encoding="utf-8"))
        assert not re.search(r"font-weight\s*:\s*(?:bold|[789]00)\b", css, re.I), path.name
        assert not re.search(r"font\s*:\s*[^;]*\b(?:bold|[789]00)\b", css, re.I), path.name


def test_secondary_button_has_no_visible_border_and_keeps_target_height():
    css = strip_comments((STATIC_DIR / "app.css").read_text(encoding="utf-8"))
    btn = rule(css, ".btn")
    secondary = rule(css, ".btn.secondary")
    assert "min-height: var(--target)" in btn
    assert "border: 0" in btn
    assert "background: var(--hover)" in secondary
    assert "border: 0" in secondary
    assert "min-height: var(--target)" in secondary
    active_nav = rule(css, '.views button[aria-current="page"]')
    assert "background: var(--hover)" in active_nav and "font-weight: 600" in active_nav


def test_prose_links_keep_their_browser_underline_and_no_network_resources():
    for path in all_sheets():
        css = strip_comments(path.read_text(encoding="utf-8"))
        for forbidden in ("@import", "@font-face", "url(", "style=", "http://", "https://"):
            assert forbidden not in css, (path.name, forbidden)
        for prelude, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(r"text-decoration\s*:\s*none\b", body):
                for part in (p.strip() for p in prelude.split(",")):
                    assert part in ALLOWED_NO_UNDERLINE, f"{path.name}: {part} removes link underline"
