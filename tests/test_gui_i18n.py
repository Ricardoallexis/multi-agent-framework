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
    plain = JS / "route.js"
    assert ui_text(plain) == plain.read_text(encoding="utf-8")
    assert "Sesiones" in ui_text(JS / "main.js")
    assert "Sessions" in ui_text(JS / "main.js", lang="en")
    assert "Las flechas cambian de resultado" in ui_text(JS / "command-palette.js")


@needs_node
def test_shell_state_loads_first_with_a_document_despite_the_import_cycle():
    source = f"""
      globalThis.document = {{documentElement: {{lang: ''}}}};
      const state = await import({json.dumps((JS / 'shell-state.js').as_uri())});
      console.log(JSON.stringify({{preference: state.readLanguagePreference(), lang: document.documentElement.lang}}));
    """
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30, check=True)
    assert json.loads(result.stdout)["preference"] == "auto"


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


def _dictionary_keys() -> dict[str, dict[str, list[str]]]:
    """Every dictionary, of a view (static/i18n/<lang>/<name>.js) or of an extension (extensions/<name>/i18n.js), with the
    keys it has in each language. The key is the dictionary id: ``i18n/<name>.js`` or ``extensions/<name>/i18n.js``."""
    views = {lang: sorted((STATIC_DIR / "i18n" / lang).glob("*.js")) for lang in ("en", "es")}
    assert [path.name for path in views["en"]] == [path.name for path in views["es"]]
    extensions = sorted((STATIC_DIR / "extensions").glob("*/i18n.js"))
    imports, entries = [], []
    for index, (en, es) in enumerate(zip(views["en"], views["es"])):
        imports.append(f"import view{index}en from {json.dumps(en.as_uri())};")
        imports.append(f"import view{index}es from {json.dumps(es.as_uri())};")
        entries.append(f"{json.dumps('i18n/' + en.name)}: {{en: Object.keys(view{index}en), es: Object.keys(view{index}es)}}")
    for index, path in enumerate(extensions):
        imports.append(f"import ext{index} from {json.dumps(path.as_uri())};")
        entries.append(f"{json.dumps('extensions/' + path.parent.name + '/i18n.js')}: "
                       f"{{en: Object.keys(ext{index}.en), es: Object.keys(ext{index}.es)}}")
    source = "\n".join(imports) + "\nconsole.log(JSON.stringify({" + ", ".join(entries) + "}));"
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


@needs_node
def test_every_dictionary_has_the_same_keys_in_both_languages():
    dictionaries = _dictionary_keys()
    assert dictionaries
    for name, langs in dictionaries.items():
        # IDI phase B: help.<topic> keys hold the Spanish of a help topic whose English base is the help fragment itself,
        # so they may live only in es (phase A put help.palette.* in both); tests/test_gui_help.py checks them
        help_en = {key for key in langs["en"] if key.startswith("help.")}
        help_es = {key for key in langs["es"] if key.startswith("help.")}
        assert help_en <= help_es, (name, sorted(help_en - help_es))
        assert all(re.fullmatch(r"help\.[a-z_]+(?:\.[a-z_]+)+", key) for key in help_es), name
        assert sorted(set(langs["en"]) - help_en) == sorted(set(langs["es"]) - help_es), name
        assert len(set(langs["en"])) == len(langs["en"]), name


@needs_node
def test_no_key_is_in_two_dictionaries_because_the_last_one_would_win_in_silence():
    owner: dict[str, str] = {}
    for name, langs in _dictionary_keys().items():
        for key in langs["en"]:
            assert key not in owner, f"{key} is in {owner.get(key)} and in {name}"
            owner[key] = name


@needs_node
def test_extension_keys_start_with_the_extension_prefix():
    extensions = sorted((STATIC_DIR / "extensions").glob("*/i18n.js"))
    assert {path.parent.name for path in extensions} >= {"apps", "approvals", "continuity", "offices", "project",
                                                         "document_alerts", "task_waiting"}
    for name, langs in _dictionary_keys().items():
        if not name.startswith("extensions/"):
            continue
        prefix = f"ext.{name.split('/')[1]}."
        help_source = (STATIC_DIR / name.removesuffix("/i18n.js") / "help.js").read_text(encoding="utf-8")
        help_topics = set(re.findall(r'^\s*"([^"]+)":\s*\{', help_source, re.M))
        for lang, keys in langs.items():
            invalid = [key for key in keys if not key.startswith(prefix) and not (
                lang == "es" and key.startswith("help.") and key.removeprefix("help.") in help_topics
            )]
            assert not invalid, (name, lang, invalid)


@needs_node
def test_every_extension_entry_loads_its_dictionary_first():
    for path in sorted((STATIC_DIR / "extensions").glob("*/i18n.js")):
        index = (path.parent / "index.js").read_text(encoding="utf-8")
        assert re.search(r'^import "\./i18n\.js";$', index, re.M), path.parent.name
        source = path.read_text(encoding="utf-8")
        for lang in ("en", "es"):
            assert f'registerTranslations("{lang}", {lang});' in source, (path.parent.name, lang)


@needs_node
def test_literal_translation_keys_exist_in_both_languages():
    keys = {key for langs in _dictionary_keys().values() for key in langs["en"]}
    used = set()
    for path in [*JS.rglob("*.js"), *(STATIC_DIR / "extensions").rglob("*.js")]:
        used.update(re.findall(r'\bt\("([a-z][a-z_]*(?:\.[a-z_]+)+)"\)', path.read_text(encoding="utf-8")))
    assert used <= keys, used - keys


def test_ui_text_reads_extension_dictionaries(tmp_path, monkeypatch):
    import tests.gui_text as gui_text
    static = tmp_path / "static"
    (static / "i18n" / "en").mkdir(parents=True)
    (static / "i18n" / "es").mkdir(parents=True)
    (static / "extensions" / "demo").mkdir(parents=True)
    (static / "extensions" / "demo" / "i18n.js").write_text(
        'const en = {\n  "ext.demo.title": "Demo"\n};\nconst es = {\n  "ext.demo.title": "Prueba"\n};\n'
        'export default { en, es };\n', encoding="utf-8")
    source = tmp_path / "view.js"
    source.write_text('const x = t("ext.demo.title");\n', encoding="utf-8")
    monkeypatch.setattr(gui_text, "STATIC_DIR", static)
    assert '"Prueba"' in gui_text.ui_text(source, "es") and "Demo" not in gui_text.ui_text(source, "es")
    assert '"Demo"' in gui_text.ui_text(source, "en")


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
