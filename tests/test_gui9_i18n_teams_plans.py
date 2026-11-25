# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""IDI-T06: paired translations for teams, plans, the work map and the agent tree."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from tests.gui_text import ui_text

JS = STATIC_DIR / "js"
I18N = STATIC_DIR / "i18n"
NODE = shutil.which("node")


def _dictionary(path):
    source = path.read_text(encoding="utf-8")
    match = re.search(r"^export default (\{.*?^});$", source, re.S | re.M)
    assert match, path
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", match[1]))


def _help_topics():
    source = (JS / "help-text-teams.js").read_text(encoding="utf-8")
    match = re.search(r"^export const HELP = (\{.*?^});$", source, re.S | re.M)
    assert match
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", match[1]))


def test_t06_dictionary_keys_and_help_topics_are_owned_and_paired():
    for name, prefix in (
        ("teams", "teams."),
        ("plans", "plans."),
        ("work-map", "work_map."),
        ("agent-tree", "agent_tree."),
    ):
        english = _dictionary(I18N / "en" / f"{name}.js")
        spanish = _dictionary(I18N / "es" / f"{name}.js")
        english_keys = {key for key in english if not key.startswith("help.")}
        spanish_keys = {key for key in spanish if not key.startswith("help.")}
        assert english_keys == spanish_keys, name
        assert all(key.startswith(prefix) for key in english_keys), name
        assert not any(key.startswith("help.") for key in english), name

    spanish = _dictionary(I18N / "es" / "teams.js")
    help_entries = {key.removeprefix("help."): value for key, value in spanish.items() if key.startswith("help.")}
    topics = _help_topics()
    assert set(help_entries) == set(topics)
    for topic, entry in help_entries.items():
        assert set(entry) == {"title", "hint", "more"}, topic
        assert len(entry["more"]) == len(topics[topic]["more"]), topic


def test_view_text_is_reconstructed_from_the_selected_catalog():
    assert "Equipos y organigramas" in ui_text(JS / "teams.js", "es")
    assert "Teams and org charts" in ui_text(JS / "teams.js", "en")
    assert "Planes propuestos" in ui_text(JS / "plans.js", "es")
    assert "Proposed plans" in ui_text(JS / "plans.js", "en")
    assert "Mapa de trabajo" in ui_text(JS / "work-map.js", "es")
    assert "Work map" in ui_text(JS / "work-map.js", "en")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_known_team_and_plan_texts_switch_language_and_keep_unknown_values():
    i18n_url = json.dumps((JS / "i18n.js").as_uri())
    team_url = json.dumps((JS / "team-card-model.js").as_uri())
    plan_url = json.dumps((JS / "plan-card-model.js").as_uri())
    script = f"""
      globalThis.window = {{ localStorage: {{ getItem: () => null, setItem: () => {{}} }} }};
      globalThis.document = {{ documentElement: {{ lang: "" }} }};
      const ui = await import({i18n_url});
      const teams = await import({team_url});
      const plans = await import({plan_url});
      const decision = {{ status: 409, details: {{ reason: "team_not_approved",
        blockers: [{{ bundle: "general" }}] }} }};
      ui.setLanguage("en");
      const english = {{
        mode: teams.modeText("supervised"),
        task: plans.taskRunLine({{ task_id: "task-17", title: "Review", state: "running" }}),
        decision: plans.decisionFailure(decision).text,
      }};
      ui.setLanguage("es");
      const spanish = {{
        mode: teams.modeText("supervised"),
        task: plans.taskRunLine({{ task_id: "task-17", title: "Review", state: "running" }}),
        decision: plans.decisionFailure(decision).text,
      }};
      console.log(JSON.stringify({{ english, spanish,
        unknown: [teams.modeText("future_mode"),
          plans.taskRunLine({{ task_id: "task-18", state: "future_state" }}).text] }}));
    """
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["english"]["mode"] == "Supervised"
    assert output["spanish"]["mode"] == "Supervisado"
    assert "task-17" in output["english"]["task"]["text"] and "In progress" in output["english"]["task"]["text"]
    assert "task-17" in output["spanish"]["task"]["text"] and "En marcha" in output["spanish"]["task"]["text"]
    assert output["english"]["decision"] == "Approve the team for general first."
    assert output["spanish"]["decision"] == "Primero hay que aprobar el equipo de «general»."
    assert output["unknown"] == ["future_mode", "Tarea task-18: future_state."]
