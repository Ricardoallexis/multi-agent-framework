# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""IDI-T07: list translations, help shape, and data that must stay unchanged."""
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
DICTIONARIES = ("start", "sessions", "packages", "pending", "task-waiting", "usage", "evaluation", "panel", "subagents")


def run_node(source: str) -> dict:
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
        encoding="utf-8", cwd=STATIC_DIR, timeout=30, check=True,
    )
    return json.loads(result.stdout)


def uri(path) -> str:
    return json.dumps(path.as_uri())


@needs_node
def test_dictionaries_have_matching_keys_and_help_has_spanish_shape_only():
    imports = []
    pairs = []
    for index, name in enumerate(DICTIONARIES):
        for lang in ("en", "es"):
            imports.append(f"import {lang}{index} from {uri(STATIC_DIR / 'i18n' / lang / (name + '.js'))};")
        pairs.append(f"[{json.dumps(name)}, en{index}, es{index}]")
    source = "\n".join(imports) + f"\nimport {{ HELP }} from {uri(JS / 'help-text-lists.js')};\n"
    source += """
      const pairs = [%s];
      const errors = [];
      for (const [name, en, es] of pairs) {
        if (JSON.stringify(Object.keys(en).filter(k => !k.startsWith('help.')).sort()) !==
            JSON.stringify(Object.keys(es).filter(k => !k.startsWith('help.')).sort())) errors.push(name + ': keys');
        if (Object.keys(en).some(k => k.startsWith('help.'))) errors.push(name + ': English help duplicated');
      }
      const spanish = Object.assign({}, ...pairs.map(([, , es]) => es));
      for (const [topic, entry] of Object.entries(HELP)) {
        const translated = spanish['help.' + topic];
        if (!translated || !['title', 'hint', 'more'].every(k => k in translated) ||
            !Array.isArray(translated.more) || translated.more.length !== entry.more.length) errors.push(topic);
      }
      console.log(JSON.stringify({topics: Object.keys(HELP).length, errors}));
    """ % ", ".join(pairs)
    assert run_node(source) == {"topics": 78, "errors": []}
    for name in DICTIONARIES:
        for lang in ("en", "es"):
            text = (STATIC_DIR / "i18n" / lang / f"{name}.js").read_text(encoding="utf-8")
            keys = re.findall(r'^\s*"([a-z][a-z_]*(?:\.[a-z_]+)+)"\s*:', text, re.M)
            assert len(keys) == len(set(keys)), (name, lang)


@needs_node
def test_known_labels_switch_language_while_ids_and_arbitrary_data_stay_raw():
    source = f"""
      const storage = new Map();
      globalThis.window = {{localStorage: {{getItem: k => storage.get(k) || null, setItem: (k,v) => storage.set(k,v)}}}};
      const ui = await import({uri(JS / 'i18n.js')});
      const start = await import({uri(JS / 'start-model.js')});
      const session = await import({uri(JS / 'session-text.js')});
      const packages = await import({uri(JS / 'packages-model.js')});
      const pending = await import({uri(JS / 'pending-model.js')});
      const waiting = await import({uri(JS / 'task-waiting.js')});
      const work = await import({uri(JS / 'work-fold.js')});
      const subagents = await import({uri(JS / 'subagents-text.js')});
      const sample = () => ({{
        checking: start.CHECKING_TEXT, removeNotice: packages.REMOVE_NOTICE,
        noConversation: subagents.NO_CONVERSATION,
        tryMock: start.startRows({{steps: [{{id:'try_mock', title:'Raw server title', done:false}}],
          pending:['try_mock'], example_installed:true}})[0],
        surface: session.surfaceLabel('desktop'), unknownSurface: session.surfaceLabel('custom-surface'),
        title: session.sessionTitle({{title:'A person title', id:'codex:abc'}}),
        warning: session.warningText('no_local_history'), unknownWarning: session.warningText('new_code: details'),
        package: packages.packageRows({{packages:[{{name:'general', level:'data', state:'ok', source_label:'my-file.zip'}}]}}, 'simple')[0],
        reason: ui.t('pending.review'), credits: ui.t('pending.credits_exhausted'),
        allTab: pending.pendingTabs([], [])[0], waiting: waiting.waitBadge({{who:'user'}}),
        work: work.workLabel({{tools:2, seconds:null}}),
      }});
      ui.setLanguage('es'); const es = sample();
      ui.setLanguage('en'); const en = sample();
      console.log(JSON.stringify({{es,en}}));
    """
    result = run_node(source)
    es, en = result["es"], result["en"]
    assert es["tryMock"]["actions"][0]["href"] == en["tryMock"]["actions"][0]["href"] == "#new/office/cafe/menu"
    assert es["tryMock"]["title"] == en["tryMock"]["title"] == "Raw server title"
    assert es["tryMock"]["actions"][0]["label"] == "Probar en Mock"
    assert en["tryMock"]["actions"][0]["label"] == "Try in Mock"
    assert (es["checking"], en["checking"]) == ("Comprobando…", "Checking…")
    assert es["removeNotice"].startswith("Se quita") and en["removeNotice"].startswith("It will be removed")
    assert es["noConversation"].startswith("Sin conversación") and en["noConversation"].startswith("No conversation")
    assert (es["surface"], en["surface"]) == ("Escritorio", "Desktop")
    assert es["unknownSurface"] == en["unknownSurface"] == "custom-surface"
    assert es["title"] == en["title"] == "A person title"
    assert es["unknownWarning"] == en["unknownWarning"] == "new_code: details"
    assert es["package"]["name"] == en["package"]["name"] == "general"
    assert es["package"]["origin"] == en["package"]["origin"] == "my-file.zip"
    assert (es["reason"], en["reason"]) == ("Revisión pendiente", "Review pending")
    assert (es["credits"], en["credits"]) == ("Se agotaron los créditos", "Credits used up")
    assert (es["allTab"]["label"], en["allTab"]["label"]) == ("Todo", "All")
    assert (es["waiting"], en["waiting"]) == ("Espera tu decisión", "Waiting for your decision")
    assert es["work"].startswith("Trabajó") and en["work"].startswith("Worked")


def test_known_reason_codes_are_only_presented_without_changing_the_code():
    source = (JS / "pending.js").read_text(encoding="utf-8")
    assert 'reason === "review"' in source and 'reason === "credits_exhausted"' in source
    assert re.search(r"reasonText\(item\.reason\)", source)
