# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR2-T02: pure Spanish quota text module, shared across the three GUI models, and Ollama Spanish diagnosis."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
EXT_APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str, data: dict | None = None) -> dict:
    prelude = (
        "Object.defineProperty(globalThis, 'navigator', {value: {language: 'es-MX'}, configurable: true});\n"
        f'const qt = await import({json.dumps((JS / "quota-text.js").as_uri())});\n'
        f'const picker = await import({json.dumps((JS / "model-picker-model.js").as_uri())});\n'
        f'const session = await import({json.dumps((JS / "new-session-model.js").as_uri())});\n'
        f'const apps = await import({json.dumps((EXT_APPS / "model.js").as_uri())});\n'
        "globalThis.fetch = () => { throw new Error('pure model must not use network'); };\n"
        "const DATA = JSON.parse(process.env.DATA || 'null');\n"
    )
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", prelude + script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={**os.environ, "DATA": json.dumps(data), "TZ": "UTC"},
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_quota_text_module_is_pure_and_has_no_dom_or_network():
    src = ui_text((JS / "quota-text.js"))
    assert 'import { currentLanguage, t } from "./i18n.js";' in src
    src = src.replace('import { currentLanguage, t } from "./i18n.js";', "")
    for forbidden in ("import ", "fetch(", "apiPost", "apiGet", "document.", "window.", "localStorage", "innerHTML"):
        assert forbidden not in src, forbidden


@needs_node
def test_window_labels_cover_known_and_unknown_windows():
    out = run_node("""
      const keys = ["five_hour", "weekly", "model_weekly", "monthly", "spend", "custom_limit", "", null, undefined, "toString"];
      console.log(JSON.stringify(Object.fromEntries(keys.map((k) => [String(k), qt.windowLabel(k)]))));
    """)
    assert out == {
        "five_hour": "Cinco horas",
        "weekly": "Semana",
        "model_weekly": "Semana por modelo",
        "monthly": "Mes",
        "spend": "Gasto",
        "custom_limit": "Uso",
        "": "Uso",
        "null": "Uso",
        "undefined": "Uso",
        "toString": "Uso",
    }


@needs_node
def test_reset_text_formats_local_time_or_returns_empty_when_missing():
    out = run_node("""
      console.log(JSON.stringify({
        valid: qt.resetText("2026-12-01T01:00:00Z"),
        empty: qt.resetText(""),
        none: qt.resetText(null),
        undef: qt.resetText(undefined),
        invalid: qt.resetText("not-a-date"),
      }));
    """)
    assert out["valid"] == "reinicia el 1 dic, 01:00"
    assert out["empty"] == ""
    assert out["none"] == ""
    assert out["undef"] == ""
    assert out["invalid"] == ""


@needs_node
def test_all_three_models_use_same_spanish_name_for_monthly_and_formatted_reset():
    out = run_node("""
      const quota = { windows: [{ group: "Copilot", window: "monthly", used_percent: 25, resets_at: "2026-12-01T01:00:00Z" }] };
      const plainMonthly = { windows: [{ window: "monthly", used_percent: 25 }] };
      console.log(JSON.stringify({
        picker: picker.quotaLines(quota),
        apps: apps.quotaLines(quota),
        session: session.quotaRemainingLines(quota),
        plain: [picker.quotaLines(plainMonthly)[0], apps.quotaLines(plainMonthly)[0], session.quotaRemainingLines(plainMonthly)[0]],
      }));
    """)
    assert out["picker"] == ["Copilot · Mes: 25 % usado · reinicia el 1 dic, 01:00"]
    assert out["apps"] == ["Copilot · Mes: 25 % usado · reinicia el 1 dic, 01:00"]
    assert out["session"] == ["Copilot · Mes: 75 % restante · reinicia el 1 dic, 01:00"]
    assert [line.split(":")[0] for line in out["plain"]] == ["Mes", "Mes", "Mes"]
    for line in out["picker"] + out["apps"] + out["session"]:
        assert "monthly" not in line
        assert "2026-12-01T01:00:00Z" not in line


@needs_node
def test_ollama_reasons_are_spanish_and_never_leak_english_diagnosis():
    data = {
        "connections": [
            {
                "id": "ollama",
                "kind": "model",
                "reachable": False,
                "available": False,
                "reason": "unreachable",
                "diagnosis": "Ollama did not answer its health probe.",
            },
        ],
    }
    unreachable = run_node("console.log(JSON.stringify(apps.buildAppsView(DATA, null)));", data)["models"][0]
    assert "Ollama no respondió a su comprobación" in unreachable["action"]["detail"]
    assert "Ollama did not answer its health probe." not in json.dumps(unreachable)

    data["connections"][0]["reason"] = "tls_certificate"
    data["connections"][0]["diagnosis"] = "The TLS certificate could not be verified; check the local trust chain."
    tls = run_node("console.log(JSON.stringify(apps.buildAppsView(DATA, null)));", data)["models"][0]
    assert "No se pudo verificar el certificado TLS" in tls["action"]["detail"]
    assert "TLS certificate" not in json.dumps(tls)
