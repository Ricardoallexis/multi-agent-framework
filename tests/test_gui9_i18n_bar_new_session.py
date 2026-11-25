# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""IDI-T04: localized shell, new-session, create-run, and model-picker text."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
I18N = STATIC_DIR / "i18n"
T04_HELP_EXCLUSIONS = {"palette.search", "palette.close", "customize.language"}


NODE = shutil.which("node")
OWNED = ("shell", "new-session", "create-run", "model-picker")


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def dictionary_keys(path: Path) -> list[str]:
    return re.findall(r'^  "([^"]+)"\s*:', path.read_text(encoding="utf-8"), re.M)


def test_owned_dictionaries_have_paired_keys_and_no_english_help_duplicates():
    for name in ("shell", "new-session", "create-run", "model-picker"):
        english = dictionary_keys(I18N / "en" / f"{name}.js")
        spanish = dictionary_keys(I18N / "es" / f"{name}.js")
        english_non_help = {key for key in english if not key.startswith("help.")}
        spanish_non_help = {key for key in spanish if not key.startswith("help.")}
        assert len(english) == len(set(english)), name
        assert len(spanish) == len(set(spanish)), name
        assert english_non_help == spanish_non_help, name

    help_source = (JS / "help-text-shell.js").read_text(encoding="utf-8")
    help_block = re.search(r"^export const HELP = (\{.*?^\});$", help_source, re.S | re.M)
    assert help_block
    topics = set(json.loads(help_block[1])) - T04_HELP_EXCLUSIONS
    english_help = {key.removeprefix("help.") for key in dictionary_keys(I18N / "en" / "shell.js")
                    if key.startswith("help.")}
    spanish_help = {key.removeprefix("help.") for key in dictionary_keys(I18N / "es" / "shell.js")
                    if key.startswith("help.")}
    assert english_help == set()
    assert spanish_help == topics


def test_translation_keys_cover_the_user_visible_t04_components():
    shell = ui_text(JS / "shell.js")
    new_session = ui_text(JS / "new-session.js")
    create_run = ui_text(JS / "create-run.js")
    model_picker = ui_text(JS / "model-picker.js")

    assert "Menú" in shell and "Sin asignar" in shell
    assert "¿Qué quieres hacer?" in new_session and "Trabajar en un workflow" in new_session
    assert "Revisar y lanzar" in new_session and "Límite alcanzado" in new_session
    assert "Crear ejecución" in create_run and "Este campo es obligatorio" in create_run
    assert "Conecta un modelo" in model_picker and "Reintentar" in model_picker


def test_known_executor_notes_and_identifiers_are_labeled_without_changing_data():
    source = (JS / "new-session.js").read_text(encoding="utf-8")
    english = (I18N / "en" / "new-session.js").read_text(encoding="utf-8")
    spanish = (I18N / "es" / "new-session.js").read_text(encoding="utf-8")

    assert 'id === "answer" ? t("new_session.answer_step") : String(id)' in source
    assert 'id === "local_default" ? t("new_session.local_default_model") : String(id)' in source
    assert "routingOverrides[step.step_id]" in source
    assert "const failure = /^failure:\\s*([a-z_]+)(.*)$/.exec(raw);" in source
    assert "return key ? t(key) + failure[2] : raw;" in source
    assert 'raw.startsWith(stderrPrefix)' in source and 'raw.slice(stderrPrefix.length)' in source
    for key in ("answer_step", "local_default_model", "executor_failure_quota",
                "executor_failure_blocked_open_session", "executor_failure_authentication",
                "executor_failure_permission_denied", "executor_failure_other", "executor_stderr"):
        assert f'"new_session.{key}"' in english
        assert f'"new_session.{key}"' in spanish
    assert '"new_session.executor_stderr": "Error output: {stderr}"' in english
    assert '"new_session.executor_stderr": "Salida de error: {stderr}"' in spanish


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_free_text_placeholder_follows_the_language():
    source = (JS / "new-session.js").read_text(encoding="utf-8")
    assert 't("new_session.free_placeholder")' in source and "FREE_PLACEHOLDER" not in source
    out = run_node(f"""
      import {{ t, setLanguage }} from {json.dumps((JS / "i18n.js").as_uri())};
      const result = {{}};
      setLanguage("es"); result.es = t("new_session.free_placeholder");
      setLanguage("en"); result.en = t("new_session.free_placeholder");
      console.log(JSON.stringify(result));
    """)
    assert out == {"es": "Ej.: quiero resumir un documento", "en": "e.g., I want to summarize a document"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_owned_dictionaries_answer_live_after_a_language_change():
    keys = {name: [key for key in dictionary_keys(I18N / "en" / f"{name}.js") if not key.startswith("help.")]
            for name in OWNED}
    sample = {name: [items[0], items[len(items) // 2], items[-1]] for name, items in keys.items()}
    out = run_node(f"""
      import {{ t, setLanguage }} from {json.dumps((JS / "i18n.js").as_uri())};
      const sample = {json.dumps(sample)};
      const result = {{}};
      for (const language of ["en", "es", "en", "es"]) {{
        setLanguage(language);
        result[language] = Object.fromEntries(Object.entries(sample).map(([name, keys]) => [name, keys.map((key) => t(key))]));
      }}
      result.keys = sample;
      console.log(JSON.stringify(result));
    """)
    for name, items in sample.items():
        for index, key in enumerate(items):
            assert out["en"][name][index] != key and out["es"][name][index] != key, key
        assert out["en"][name] != out["es"][name] or name == "model-picker", name
    spanish = {name: (I18N / "es" / f"{name}.js").read_text(encoding="utf-8") for name in OWNED}
    for name, items in sample.items():
        for index, key in enumerate(items):
            assert json.dumps(out["es"][name][index], ensure_ascii=False) in spanish[name], key


def test_keys_used_through_a_constant_exist_in_both_languages():
    quoted = r'"((?:shell|new_session|create_run|model_picker)\.[a-z_0-9]+)"'
    constants = r'(?:const|let)\s+[A-Za-z_]+\s*=\s*"((?:shell|new_session|create_run|model_picker)\.[a-z_]+)"\s*;'
    known = {language: {key for name in OWNED for key in dictionary_keys(I18N / language / f"{name}.js")}
             for language in ("en", "es")}
    through_constants, in_either = set(), set()
    for path in JS.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        through_constants.update(re.findall(constants, source))
        in_either.update(key for key in re.findall(quoted, source) if key in known["en"] or key in known["es"])
    assert "new_session.free_placeholder" in in_either
    for language in ("en", "es"):
        assert not through_constants - known[language], (language, through_constants - known[language])
        assert not in_either - known[language], (language, in_either - known[language])
