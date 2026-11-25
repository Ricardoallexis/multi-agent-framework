# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Localized model text for the apps, approvals, and continuity extensions."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
EXT = STATIC_DIR / "extensions"


@needs_node
def test_extension_models_follow_language_and_keep_dynamic_values():
    source = f"""
      const ui = await import({json.dumps((STATIC_DIR / "js" / "i18n.js").as_uri())});
      const activation = await import({json.dumps((EXT / "apps" / "activation-model.js").as_uri())});
      const approvals = await import({json.dumps((EXT / "approvals" / "model.js").as_uri())});
      const continuity = await import({json.dumps((EXT / "continuity" / "model.js").as_uri())});
      const row = {{ id: "codex_cli" }};
      const approval = {{
        id: "approval-1", kind: "secret_send", destination: {{ executor: "Codex", model: "gpt-6.1" }},
        location: {{ run: "run-42", step: "draft" }}, secret_types: ["private_key"], masked_sample: "****abcd",
      }};
      const card = {{ app: "Claude", model: "claude-sonnet-5-5", reason: "rate_limited", window: "five_hour" }};
      const read = () => ({{
        activation: activation.activationView(row, {{ state: "ready" }}).label,
        executorFallback: activation.executorName(""),
        approval: approvals.describeApproval(approval),
        limit: continuity.limitText(card),
        stale: continuity.decisionErrorText({{ status: 409, code: "invalid_state", details: {{ reason: "stale_revision" }} }}),
      }});
      ui.setLanguage("es");
      const es = read();
      ui.setLanguage("en");
      const en = read();
      console.log(JSON.stringify({{ es, en }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=60,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["es"]["activation"] == "Listo para activar"
    assert output["en"]["activation"] == "Ready to activate"
    assert output["es"]["executorFallback"] == "el ejecutor"
    assert output["en"]["executorFallback"] == "the executor"
    assert output["es"]["approval"]["title"] == "Envío con forma de secreto"
    assert output["en"]["approval"]["title"] == "Send containing secret-like content"
    assert output["es"]["approval"]["destination"] == "Codex · gpt-6.1"
    assert output["en"]["approval"]["destination"] == "Codex · gpt-6.1"
    assert output["es"]["approval"]["location"] == "Ejecución run-42 · paso draft"
    assert output["en"]["approval"]["location"] == "Run run-42 · step draft"
    assert output["es"]["limit"] == "Claude · claude-sonnet-5-5 recibió demasiadas solicitudes seguidas."
    assert output["en"]["limit"] == "Claude · claude-sonnet-5-5 sent too many requests."
    assert output["es"]["stale"].startswith("La tarea cambió desde que se dibujó")
    assert output["en"]["stale"].startswith("The task changed after this card was shown")


@needs_node
def test_owned_dictionaries_match_and_keep_help_in_spanish_only():
    source = f"""
      import apps from {json.dumps((EXT / "apps" / "i18n.js").as_uri())};
      import approvals from {json.dumps((EXT / "approvals" / "i18n.js").as_uri())};
      import continuity from {json.dumps((EXT / "continuity" / "i18n.js").as_uri())};
      import openEn from {json.dumps((STATIC_DIR / "i18n" / "en" / "open-in-app.js").as_uri())};
      import openEs from {json.dumps((STATIC_DIR / "i18n" / "es" / "open-in-app.js").as_uri())};
      import {{ HELP as appsHelp }} from {json.dumps((EXT / "apps" / "help.js").as_uri())};
      import {{ HELP as approvalsHelp }} from {json.dumps((EXT / "approvals" / "help.js").as_uri())};
      import {{ HELP as continuityHelp }} from {json.dumps((EXT / "continuity" / "help.js").as_uri())};
      import {{ HELP as openHelp }} from {json.dumps((STATIC_DIR / "js" / "help-text-apps.js").as_uri())};
      const pair = (en, es, help) => ({{
        en: Object.keys(en), es: Object.keys(es), topics: Object.keys(help).map((topic) => "help." + topic),
      }});
      console.log(JSON.stringify({{
        apps: pair(apps.en, apps.es, appsHelp),
        approvals: pair(approvals.en, approvals.es, approvalsHelp),
        continuity: pair(continuity.en, continuity.es, continuityHelp),
        open: pair(openEn, openEs, openHelp),
      }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=60,
        check=True,
    )
    dictionaries = json.loads(result.stdout)
    prefixes = {"apps": "ext.apps.", "approvals": "ext.approvals.", "continuity": "ext.continuity.", "open": "open."}
    for name, prefix in prefixes.items():
        en = set(dictionaries[name]["en"])
        es = set(dictionaries[name]["es"])
        help_keys = {key for key in es if key.startswith("help.")}
        assert not any(key.startswith("help.") for key in en), name
        assert es - help_keys == en, name
        assert all(key.startswith(prefix) for key in en), name
        assert help_keys == set(dictionaries[name]["topics"]), name
