# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""English and Spanish run-thread messages and the owned help catalog."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
I18N = STATIC_DIR / "i18n"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_known_thread_events_are_translated_but_arbitrary_text_is_preserved():
    source = f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      const cards = await import({json.dumps((JS / "thread-cards.js").as_uri())});
      const event = (kind, fields) => cards.threadEntryText({{kind, ...fields}});
      const cases = {{
        en: {{
          started: "Started workflow-7.",
          completed: "Step draft is complete.",
          waiting: "Waiting for your review.",
          message: "Message read.",
          finals: ["The run is complete.", "The run failed.", "The run was cancelled.", "The run was rejected.", "The run was interrupted."],
          waits: [
            "Waiting for your review.", "Waiting for tool approval.", "Waiting for a manual step.",
            "Waiting for external research.", "Waiting for approval to escalate.",
            "Waiting because the model-call budget is exhausted.", "Waiting for content review."
          ]
        }},
        es: {{
          started: "Se inició workflow-7.",
          completed: "El paso «draft» terminó.",
          waiting: "Esperando tu revisión.",
          message: "Mensaje leído.",
          finals: ["La ejecución terminó.", "La ejecución falló.", "La ejecución se canceló.", "La ejecución se rechazó.", "La ejecución se interrumpió."],
          waits: [
            "Esperando tu revisión.", "Esperando aprobación de herramienta.", "Esperando un paso manual.",
            "Esperando investigación externa.", "Esperando aprobación para escalar.",
            "Esperando porque se agotó el presupuesto de llamadas al modelo.", "Esperando revisión del contenido."
          ]
        }}
      }};
      const statuses = ["completed", "failed", "cancelled", "rejected", "interrupted"];
      const reasons = ["review", "tool_approval", "execute_step", "research", "escalation_approval", "budget_exhausted", "content_review"];
      const result = {{}};
      for (const lang of ["en", "es"]) {{
        ui.setLanguage(lang);
        result[lang] = {{
          started: event("launched", {{workflow_id: "workflow-7", text: "Started workflow-7"}}),
          completed: event("step", {{step_id: "draft", text: "draft completed"}}),
          waiting: event("waiting", {{reason: "review", text: "Waiting: review"}}),
          message: event("message_consumed", {{text: "Message read"}}),
          finals: statuses.map(status => event("final", {{status, text: "Run " + status}})),
          waits: reasons.map(reason => event("waiting", {{reason, text: "Waiting: " + reason}})),
          arbitrary: event("user_message", {{text: "Waiting: review is part of the user's message"}}),
          custom: event("waiting", {{reason: "review", text: "Waiting: review (custom payload)"}}),
          unknown: event("waiting", {{reason: "new_reason", text: "Waiting: new_reason"}})
        }};
      }}
      console.log(JSON.stringify({{result, cases}}));
    """
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        capture_output=True, text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60, check=True,
    )
    data = json.loads(completed.stdout)
    for lang in ("en", "es"):
        expected = data["cases"][lang]
        actual = data["result"][lang]
        for key in ("started", "completed", "waiting", "message", "finals", "waits"):
            assert actual[key] == expected[key], (lang, key, actual[key])
        assert actual["arbitrary"] == "Waiting: review is part of the user's message"
        assert actual["custom"] == "Waiting: review (custom payload)"
        assert actual["unknown"] == "Waiting: new_reason"


@needs_node
def test_run_help_uses_spanish_dictionary_and_english_fallback_without_english_help_keys():
    source = f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      const helpText = await import({json.dumps((JS / "help-text.js").as_uri())});
      const sourceHelp = await import({json.dumps((JS / "help-text-runs.js").as_uri())});
      const en = await import({json.dumps((I18N / "en" / "runs.js").as_uri())});
      const es = await import({json.dumps((I18N / "es" / "runs.js").as_uri())});
      const topics = Object.keys(sourceHelp.HELP);
      const spanishKeys = Object.keys(es.default).filter(key => key.startsWith("help."));
      ui.setLanguage("en");
      const english = helpText.localizedHelp("runs.section");
      ui.setLanguage("es");
      const spanish = helpText.localizedHelp("runs.section");
      console.log(JSON.stringify({{
        topics, spanishKeys, englishHelpKeys: Object.keys(en.default).filter(key => key.startsWith("help.")),
        english, spanish
      }}));
    """
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        capture_output=True, text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60, check=True,
    )
    data = json.loads(completed.stdout)
    assert data["englishHelpKeys"] == []
    assert sorted(data["spanishKeys"]) == sorted("help." + topic for topic in data["topics"])
    assert data["english"]["title"] == "Runs"
    assert data["spanish"]["title"] == "Ejecuciones"
    assert set(data["spanish"]) == {"title", "hint", "more"}
