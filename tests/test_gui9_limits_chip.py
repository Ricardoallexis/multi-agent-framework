# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T10: the limits chip of the sidebar footer. Synthetic connection payloads only; nothing reads a real quota file."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

NOW = "2026-10-11T12:00:00Z"
PAYLOAD = {"connections": [
    {"id": "gemini", "kind": "model", "quota": None, "checked_at": NOW},
    {"id": "ollama", "kind": "model", "quota": None, "checked_at": NOW},
    {"id": "claude_cli", "kind": "executor", "quota": {"status": "ok", "windows": []}, "checked_at": NOW},
    {"id": "codex_cli", "kind": "executor", "checked_at": "2026-10-11T11:56:00Z",
     "quota": {"status": "ok", "windows": [
         {"window": "five_hour", "group": None, "used_percent": 45.0, "resets_at": "2026-10-11T17:00:00Z"},
         {"window": "weekly", "group": None, "used_percent": 10.0, "resets_at": "2026-10-15T00:00:00Z"}]}},
    {"id": "copilot_cli", "kind": "executor", "checked_at": "2026-10-11T11:56:00Z",
     "quota": {"status": "ok", "windows": [
         {"window": "monthly", "group": None, "used_percent": 72.0, "resets_at": "2026-10-11T21:00:00Z"}]}},
    {"id": "antigravity_cli", "kind": "executor", "checked_at": "2026-10-11T09:00:00Z",
     "quota": {"status": "ok", "windows": [
         {"window": "five_hour", "group": "Gemini", "used_percent": 100.0, "resets_at": None}]}},
]}
SCENE = {"areas": [
    {"id": "a", "members": [{"id": "m1", "kind": "agent", "name": "Codex reviewer", "role": "review"},
                            {"id": "m2", "kind": "person", "name": "Ana", "role": "owner"}]},
    {"id": "b", "members": [{"id": "m3", "kind": "agent", "name": "Writer", "role": "draft"}]},
]}


def run_node(script: str, lang: str = "es-MX") -> dict:
    prelude = (
        f"Object.defineProperty(globalThis, 'navigator', {{value: {{language: {json.dumps(lang)}}}, configurable: true}});\n"
        f'const m = await import({json.dumps((JS / "limits-chip-model.js").as_uri())});\n'
        "globalThis.fetch = () => { throw new Error('pure model must not use network'); };\n"
        f"const PAYLOAD = {json.dumps(PAYLOAD)}; const SCENE = {json.dumps(SCENE)}; const NOW = Date.parse({json.dumps(NOW)});\n"
    )
    done = subprocess.run([NODE, "--input-type=module", "-e", prelude + script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env={**os.environ, "TZ": "UTC"})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_rows_come_from_apps_that_report_a_percentage_and_the_chip_shows_the_closest_to_its_limit():
    out = run_node("""
      const rows = m.appLimitRows(PAYLOAD);
      console.log(JSON.stringify({ ids: rows.map((r) => r.id), used: rows.map((r) => r.used), chip: m.chipRow(rows)?.id,
        none: m.chipRow(m.appLimitRows({ connections: [{ id: "claude_cli", quota: null }] })),
        bad: m.appLimitRows(null).length, codexRemaining: rows[0].remaining }));
    """)
    # claude_cli has no window, gemini and ollama are not apps: no bar for them; the worst window of codex is the five hours.
    assert out["ids"] == ["codex_cli", "copilot_cli", "antigravity_cli"]
    assert out["used"] == [45, 72, 100]
    assert out["chip"] == "antigravity_cli"
    assert out["none"] is None and out["bad"] == 0 and out["codexRemaining"] == 55


@needs_node
def test_detail_text_in_spanish_keeps_used_left_reset_and_age_and_says_limit_reached():
    out = run_node("""
      const rows = Object.fromEntries(m.appLimitRows(PAYLOAD).map((r) => [r.id, r]));
      console.log(JSON.stringify({ copilot: m.rowDetail(rows.copilot_cli, NOW), agy: m.rowDetail(rows.antigravity_cli, NOW),
        aria: m.rowAria(rows.copilot_cli), now: m.ageText("2026-10-11T12:00:00Z", NOW), hours: m.ageText("2026-10-11T09:00:00Z", NOW),
        days: m.ageText("2026-10-08T12:00:00Z", NOW), unknown: m.ageText("not a date", NOW) }));
    """)
    assert out["copilot"] == "72 % usado · queda 28 % · reinicia el 11 oct, 21:00 · comprobado hace 4 min"
    assert out["agy"] == "Límite alcanzado · comprobado hace 3 h"
    assert out["aria"] == "Copilot CLI: 72 % usado"
    assert out["now"] == "comprobado hace un momento"
    assert out["hours"] == "comprobado hace 3 h" and out["days"] == "comprobado hace 3 d" and out["unknown"] == ""


@needs_node
def test_detail_text_follows_the_active_language():
    out = run_node("""
      const rows = Object.fromEntries(m.appLimitRows(PAYLOAD).map((r) => [r.id, r]));
      console.log(JSON.stringify({ copilot: m.rowDetail(rows.copilot_cli, NOW), agy: m.rowDetail(rows.antigravity_cli, NOW) }));
    """, lang="en-US")
    assert out["copilot"] == "72 % used · 28 % left · resets Oct 11, 21:00 · checked 4 min ago"
    assert out["agy"] == "Limit reached · checked 3 h ago"


@needs_node
def test_rows_carry_the_app_id_and_the_name_is_resolved_when_painting():
    out = run_node("""
      const row = m.appLimitRows(PAYLOAD)[1];
      console.log(JSON.stringify({ keys: Object.keys(row).sort(), name: m.appName(row.id), unknown: m.appName("zzz"),
        officeApi: Object.keys(m).filter((key) => /office|filter/i.test(key)) }));
    """)
    assert "name" not in out["keys"] and out["name"] == "Copilot CLI" and out["unknown"] == "zzz"
    assert out["officeApi"] == []      # no filter by office until the scene names the real app of each seat


def test_the_office_filter_is_hidden_and_the_scene_is_not_read():
    chip = (JS / "limits-chip.js").read_text(encoding="utf-8")
    assert "filter" not in chip and "/scene" not in chip and "useRoute" not in chip and "limits.no_office_apps" not in chip


def test_the_token_is_read_like_pending_js_and_the_newest_reading_wins():
    chip = (JS / "limits-chip.js").read_text(encoding="utf-8")
    assert "useState(loadToken)" in chip and "loadToken" in (JS / "pending.js").read_text(encoding="utf-8")
    assert "++request.current" in chip and chip.count("mine === request.current") == 2


def test_esc_closes_only_the_popover_and_not_the_drawer():
    chip = (JS / "limits-chip.js").read_text(encoding="utf-8")
    assert 'event.key !== "Escape" || event.defaultPrevented' in chip and "event.preventDefault()" in chip
    shell = (JS / "shell.js").read_text(encoding="utf-8")
    handler = shell[shell.index("Esc closes the drawer"):shell.index("useEffect(() => { if (!narrow)")]
    assert ".limits:has(> .limits-popover:not([hidden]))" in handler and "GUI4-T10" in handler
    assert handler.index(".limits:has(") < handler.index("closeDrawer(true)")


def test_every_inner_button_has_help_or_says_why_it_has_none():
    chip = (JS / "limits-chip.js").read_text(encoding="utf-8")
    for button in re.findall(r"<button.*?>", chip, re.S):
        assert "helpFor(" in button or 'data-help-exempt="navigation"' in button, button


def test_slot_mounts_the_chip_and_the_chip_only_reads():
    shell = (JS / "shell.js").read_text(encoding="utf-8")
    slot = re.search(r"/\* GUI4-T10 \*/\s*const limitsSlot = html`(.*?)`;", shell, re.S)
    assert slot and 'data-slot="limits"' in slot[1] and "<${LimitsChip} />" in slot[1]
    for name in ("limits-chip.js", "limits-chip-model.js"):
        source = (JS / name).read_text(encoding="utf-8")
        assert "apiPost" not in source and "/probe" not in source and "/check" not in source and "setInterval" not in source, name
        assert "style=" not in source and "innerHTML" not in source, name
        assert "SPDX-License-Identifier: Apache-2.0" in source
    chip = (JS / "limits-chip.js").read_text(encoding="utf-8")
    assert chip.count('apiGet("/gui/api/connections"') == 1 and 'navigate("usage")' in chip
    model_imports = re.findall(r'from "(\./[^"]+)"', (JS / "limits-chip-model.js").read_text(encoding="utf-8"))
    assert sorted(model_imports) == ["./i18n.js", "./quota-text.js"]


def test_the_chip_bars_are_progress_elements_with_a_spoken_percentage():
    chip = (JS / "limits-chip.js").read_text(encoding="utf-8")
    assert chip.count("<progress") == 2 and chip.count("aria-label=${rowAria(") == 2
    css = (STATIC_DIR / "panels.css").read_text(encoding="utf-8")
    block = css[css.index("/* GUI4-T10"):]
    assert ".limits-popover" in block and "100dvh" in block and "CanvasText" in block and "var(--panel)" in block and "min-height: var(--target)" in block and "style=" not in block
    assert "left: calc(100%" not in block      # the bar scrolls: a popover to its side would be clipped


def test_dictionaries_have_the_same_keys_and_are_loaded():
    keys = {}
    for lang in ("en", "es"):
        text = (STATIC_DIR / "i18n" / lang / "limits-chip.js").read_text(encoding="utf-8")
        keys[lang] = re.findall(r'^\s+"(limits\.[\w.]+)":', text, re.M)
    assert keys["en"] == keys["es"] and len(keys["en"]) > 20
    i18n = (JS / "i18n.js").read_text(encoding="utf-8")
    assert "limitsChipEn" in i18n and "limitsChipEs" in i18n
    used = set(re.findall(r't\("(limits\.[\w.]+)"', "".join((JS / n).read_text(encoding="utf-8")
                                                          for n in ("limits-chip.js", "limits-chip-model.js"))))
    used |= {f"limits.app_{name}" for name in ("claude", "codex", "copilot", "agy")}
    assert used <= set(keys["en"])
