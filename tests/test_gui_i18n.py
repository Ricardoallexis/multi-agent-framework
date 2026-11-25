# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Language selection, dictionary coverage and runtime fallback."""
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
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def test_ui_text_tracks_converted_and_unconverted_views():
    plain = JS / "sessions.js"
    assert ui_text(plain) == plain.read_text(encoding="utf-8")
    assert "Sesiones" in ui_text(JS / "main.js")
    assert "Sessions" in ui_text(JS / "main.js", lang="en")
    assert "Las flechas cambian de resultado" in ui_text(JS / "command-palette.js")


@needs_node
def test_language_selection_persists_updates_lang_and_repaints():
    source = f"""
      const saved = new Map();
      globalThis.window = {{localStorage: {{getItem: (key) => saved.get(key) || null,
        setItem: (key, value) => saved.set(key, value)}}}};
      globalThis.document = {{documentElement: {{lang: ''}}}};
      Object.defineProperty(globalThis, 'navigator', {{value: {{language: 'es-MX'}}, configurable: true}});
      const ui = await import({json.dumps((JS / 'i18n.js').as_uri())});
      const start = [ui.currentLanguage(), document.documentElement.lang, ui.languageChoice()];
      const seen = []; ui.onLanguageChange((lang) => seen.push(lang));
      ui.setLanguage('en');
      const chosen = [ui.currentLanguage(), document.documentElement.lang, saved.get('multiagent.gui.language')];
      ui.setLanguage('auto');
      console.log(JSON.stringify({{start, chosen, automatic: ui.currentLanguage(), seen,
        translated: ui.t('customize.pin_label', {{name: 'Usage'}}), fallback: ui.t('unknown.key')}}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30, check=True)
    data = json.loads(result.stdout)
    assert data == {
        "start": ["es", "es", "auto"], "chosen": ["en", "en", "en"], "automatic": "es",
        "seen": ["en", "es"], "translated": "Fijar Usage", "fallback": "unknown.key",
    }


@needs_node
def test_literal_translation_keys_exist_in_both_languages():
    paths = {lang: sorted((STATIC_DIR / "i18n" / lang).glob("*.js")) for lang in ("en", "es")}
    assert [path.name for path in paths["en"]] == [path.name for path in paths["es"]]
    imports = "\n".join(
        f"import {lang}{index} from {json.dumps(path.as_uri())};"
        for lang, files in paths.items() for index, path in enumerate(files)
    )
    merged = {lang: ", ".join(f"...{lang}{index}" for index in range(len(files)))
              for lang, files in paths.items()}
    source = imports + f"\nconsole.log(JSON.stringify({{en: Object.keys({{{merged['en']}}}), es: Object.keys({{{merged['es']}}})}}));"
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30, check=True)
    keys = json.loads(result.stdout)
    assert set(keys["en"]) == set(keys["es"])
    used = set()
    for path in [*JS.rglob("*.js"), *(STATIC_DIR / "extensions").rglob("*.js")]:
        used.update(re.findall(r'\bt\("([a-z][a-z_]*(?:\.[a-z_]+)+)"\)', path.read_text(encoding="utf-8")))
    assert used <= set(keys["en"]), used - set(keys["en"])


def test_translated_views_keep_visible_spanish_in_dictionaries():
    for name in ("main.js", "customize.js"):
        source = (JS / name).read_text(encoding="utf-8")
        assert "No se pudo cambiar el modo. Inténtalo de nuevo." not in source
        assert "Este navegador no guarda las preferencias" not in source
    palette = (JS / "command-palette.js").read_text(encoding="utf-8")
    model = (JS / "command-palette-model.js").read_text(encoding="utf-8")
    for phrase in ("Sección, sesión, ejecución o ayuda…", "Nada coincide con «", " resultados.", "Explicación"):
        assert phrase not in palette
    assert 'label: "Pendientes › "' not in model


@needs_node
def test_command_palette_switches_labels_and_help_with_language():
    source = f"""
      const ui = await import({json.dumps((JS / 'i18n.js').as_uri())});
      const palette = await import({json.dumps((JS / 'command-palette-model.js').as_uri())});
      const helpText = await import({json.dumps((JS / 'help-text.js').as_uri())});
      const help = {{'palette.search': {{title: 'Buscar en todo', hint: 'Spanish', more: []}}}};
      ui.setLanguage('es');
      const spanish = [palette.KINDS.run, palette.helpEntries(help)[0].label, helpText.localizedHelp('palette.close').title];
      ui.setLanguage('en');
      const english = [palette.KINDS.run, palette.helpEntries(help)[0].label, helpText.localizedHelp('palette.close').title];
      console.log(JSON.stringify({{spanish, english, noMatch: ui.t('palette.no_match', {{query: 'x'}})}}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30, check=True)
    assert json.loads(result.stdout) == {
        "spanish": ["Ejecución", "Buscar en todo", "Cerrar la búsqueda"],
        "english": ["Run", "Search everywhere", "Close search"],
        "noMatch": "No matches for “x”.",
    }
