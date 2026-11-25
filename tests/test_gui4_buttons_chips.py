# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T01: shared pill buttons, status chips, popup surfaces, and their contrast."""
from __future__ import annotations

import re

from multiagent.gui import STATIC_DIR
from test_gui2_components import contrast, themes

CSS = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
NEW_SESSION_CSS = (STATIC_DIR / "new-session.css").read_text(encoding="utf-8")
CSS_WITHOUT_COMMENTS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def selectors(source: str) -> set[str]:
    return {
        part.strip()
        for prelude in re.findall(r"([^{}]+)\{", source)
        for part in prelude.split(",")
    }


def rule(source: str, selector: str) -> str:
    for prelude, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
        if selector in (part.strip() for part in prelude.split(",")):
            return body
    raise AssertionError(f"no rule for {selector}")


def test_buttons_keep_pill_shape_targets_and_visible_keyboard_focus():
    primary = rule(CSS_WITHOUT_COMMENTS, ".btn")
    secondary = rule(CSS_WITHOUT_COMMENTS, ".btn.secondary")
    danger = rule(CSS_WITHOUT_COMMENTS, ".btn.danger")
    small = rule(CSS_WITHOUT_COMMENTS, ".btn.small")
    disabled = rule(CSS_WITHOUT_COMMENTS, ".btn:disabled")
    focus = rule(CSS_WITHOUT_COMMENTS, ".btn:focus-visible")
    primary_hover = rule(CSS_WITHOUT_COMMENTS, ".btn:hover:not(:disabled)")
    secondary_hover = rule(CSS_WITHOUT_COMMENTS, ".btn.secondary:hover:not(:disabled)")

    assert "border-radius: 9px" in primary
    assert "min-height: var(--target)" in primary and "border: 0" in primary
    assert "border-radius:" not in secondary
    assert "min-height: var(--target)" in secondary and "border: 0" in secondary
    assert "background: var(--bad)" in danger
    assert "min-height:" not in small
    assert "opacity: .55" in disabled and "cursor: not-allowed" in disabled
    assert "opacity: .9" in primary_hover
    assert "background: var(--line-strong)" in secondary_hover and "opacity: 1" in secondary_hover
    assert ".btn.danger:hover:not(:disabled)" not in selectors(CSS_WITHOUT_COMMENTS)
    assert "outline: 2px solid var(--accent)" in focus and "outline-offset: 2px" in focus
    assert ".btn.text" not in selectors(CSS_WITHOUT_COMMENTS)


def test_badges_and_popup_surfaces_use_the_shared_visual_system():
    app_selectors = selectors(CSS_WITHOUT_COMMENTS)
    for selector in (
        ".badge", ".badge.mock", ".badge.connected", ".badge.reconnecting", ".badge.offline",
        ".badge.src-claude", ".badge.src-codex", ".badge.src-copilot", ".badge.src-framework",
        ".badge.live-live", ".badge.live-connecting", ".badge.live-failed", ".badge.live-paused",
        ".badge[class*=\"tone-success\"]", ".badge[class*=\"tone-active\"]",
        ".badge[class*=\"tone-warning\"]", ".badge[class*=\"tone-danger\"]",
        ".tone-muted", ".help-more", ".menu-only", ".tool-btn", ".views button",
        ".run-list button", ".side-item",
    ):
        assert selector in app_selectors, selector

    badge = rule(CSS_WITHOUT_COMMENTS, ".badge")
    assert "background: var(--hover)" in badge and "color: var(--muted)" in badge
    assert "border-radius: 999px" in badge and "min-height: 28px" in badge
    assert "display: inline-flex" in badge and "align-items: center" in badge
    assert "color: var(--warn)" in rule(CSS_WITHOUT_COMMENTS, ".badge.mock")
    assert "background: var(--warn-soft)" in rule(CSS_WITHOUT_COMMENTS, ".badge.mock")
    assert "background: var(--accent-soft)" in rule(CSS_WITHOUT_COMMENTS, ".badge.connected")
    assert "background: var(--warn-soft)" in rule(CSS_WITHOUT_COMMENTS, ".badge.reconnecting")
    assert "color: var(--bad)" in rule(CSS_WITHOUT_COMMENTS, ".badge.offline")
    assert "background: var(--hover)" in rule(CSS_WITHOUT_COMMENTS, ".badge.offline")
    assert "background: var(--accent-soft)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-success"]')
    assert "background: var(--accent-soft)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-active"]')
    assert "background: var(--warn-soft)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-warning"]')
    assert "color: var(--bad)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-danger"]')
    assert "background: var(--hover)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-danger"]')
    assert "background: var(--hover)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-muted"]')
    assert "background: var(--hover)" in rule(CSS_WITHOUT_COMMENTS, '.badge[class*="tone-info"]')
    live_failure = rule(CSS_WITHOUT_COMMENTS, ".badge.live-offline")
    assert "color: var(--bad)" in live_failure and "background: var(--hover)" in live_failure
    popup = rule(NEW_SESSION_CSS, ".ns-plus-menu")
    assert "border: 1px solid var(--line-strong)" in popup
    assert "border-radius: 14px" in popup and "box-shadow:" in popup
    assert "min-height: var(--target)" in rule(NEW_SESSION_CSS, ".ns-plus-menu button")
    assert ".ns-plus-menu" not in app_selectors
    assert "border-radius: 0 8px 8px 0" in rule(CSS_WITHOUT_COMMENTS, ".help-more")
    assert "style=" not in CSS and not re.search(r"font-weight\s*:\s*(?:bold|[789]00)\b", CSS, re.I)


def test_button_and_badge_colour_pairs_keep_aa_contrast_in_both_themes():
    pairs = (
        ("on-accent", "accent"), ("text", "bg"), ("text", "accent-soft"), ("muted", "bg"),
        ("text", "hover"), ("text", "line-strong"), ("on-accent", "bad"), ("bad", "warn-soft"),
        ("bad", "hover"), ("warn", "warn-soft"), ("ok", "accent-soft"), ("muted", "hover"),
        ("src-claude", "hover"), ("src-codex", "hover"), ("src-copilot", "hover"),
    )
    for theme_name, colours in themes().items():
        for foreground, background in pairs:
            ratio = contrast(colours[foreground], colours[background])
            assert ratio >= 4.5, f"{theme_name}: --{foreground} on --{background} is {ratio:.2f}:1"

        assert contrast(colours["accent"], colours["accent-soft"]) >= 4.5, theme_name
