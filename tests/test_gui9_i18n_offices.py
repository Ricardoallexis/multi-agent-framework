# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""IDI-T10 translations for Offices, Projects, document alerts, and task waits."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str):
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def file_url(path):
    return json.dumps(path.as_uri())


@needs_node
def test_extension_help_is_spanish_only_and_matches_each_help_catalog():
    extensions = ("offices", "project", "document_alerts", "task_waiting")
    imports = "\n".join(
        f'import {{ HELP as help{index} }} from {file_url(STATIC_DIR / "extensions" / name / "help.js")};'
        f'\nimport dict{index} from {file_url(STATIC_DIR / "extensions" / name / "i18n.js")};'
        for index, name in enumerate(extensions)
    )
    output = run_node(f"""
      {imports}
      const entries = [{", ".join(f"{{help: help{i}, dict: dict{i}, name: {json.dumps(name)}}}" for i, name in enumerate(extensions))}];
      console.log(JSON.stringify(entries.map(({{help, dict, name}}) => {{
        const esHelp = Object.keys(dict.es).filter((key) => key.startsWith("help.")).map((key) => key.slice(5)).sort();
        const hasHelpShape = (entry) => entry && Object.hasOwn(entry, "title") && entry.title.length > 0
          && Object.hasOwn(entry, "hint") && entry.hint.length > 0 && Object.hasOwn(entry, "more")
          && Array.isArray(entry.more) && entry.more.every((sentence) => typeof sentence === "string");
        const helpShape = Object.keys(dict.es).filter((key) => key.startsWith("help."))
          .every((key) => hasHelpShape(dict.es[key]));
        return {{
          name,
          topics: Object.keys(help).sort(),
          esHelp,
          helpShape,
          enHelp: Object.keys(dict.en).filter((key) => key.startsWith("help.")),
          cardSentences: name === "offices" ? dict.es["help.offices.card"].more.length : null,
          standardKeysMatch: JSON.stringify(Object.keys(dict.en).sort()) === JSON.stringify(
            Object.keys(dict.es).filter((key) => !key.startsWith("help.")).sort()),
        }};
      }})));
    """)
    for extension in output:
        assert extension["topics"] == extension["esHelp"], extension["name"]
        assert extension["helpShape"], extension["name"]
        assert extension["enHelp"] == [], extension["name"]
        assert extension["standardKeysMatch"], extension["name"]
    offices = next(item for item in output if item["name"] == "offices")
    assert offices["cardSentences"] == 4


@needs_node
def test_key_texts_follow_language_and_keep_model_values_as_data():
    out = run_node(f"""
      const ui = await import({file_url(STATIC_DIR / "js" / "i18n.js")});
      await import({file_url(STATIC_DIR / "extensions" / "offices" / "i18n.js")});
      await import({file_url(STATIC_DIR / "extensions" / "project" / "i18n.js")});
      await import({file_url(STATIC_DIR / "extensions" / "document_alerts" / "i18n.js")});
      await import({file_url(STATIC_DIR / "i18n" / "en" / "office-level-question.js")});
      await import({file_url(STATIC_DIR / "i18n" / "es" / "office-level-question.js")});
      const offices = await import({file_url(STATIC_DIR / "extensions" / "offices" / "model.js")});
      const officeText = await import({file_url(STATIC_DIR / "extensions" / "offices" / "import-model.js")});
      const privacy = await import({file_url(STATIC_DIR / "extensions" / "offices" / "privacy-model.js")});
      const question = await import({file_url(STATIC_DIR / "js" / "office-level-question.js")});
      const project = await import({file_url(STATIC_DIR / "extensions" / "project" / "model.js")});
      const alerts = await import({file_url(STATIC_DIR / "extensions" / "document_alerts" / "alert-item.js")});
      const render = () => {{
        const privacyRow = privacy.privacyRows({{
          cloud: {{ source: "office" }},
          areas: [{{ id: "sensitive", source: "person", sensitivity: "sensitive", cloud_effective: false,
            cloud: {{ source: "default" }} }}],
        }}, [{{ id: "sensitive", name: "Synthetic Area" }}])[0];
        return {{
          worker: offices.memberLine({{kind: "agent", worker: "step_model_id"}}),
          team: offices.degradedMessage("team_not_approved"),
          sourceError: officeText.errorText("office_source_unavailable"),
          question: question.QUESTION_LINES.normal,
          level: question.LEVEL_OPTIONS.find((item) => item.value === "local_only").label,
          privacySource: privacyRow.sourceText,
          privacyCloudSource: privacyRow.cloudSourceText,
          date: project.dayText("2026-11-15"),
          alertKind: alerts.kindText("document"),
          channelEntry: alerts.kindText("channel_entry"),
          workerReport: alerts.kindText("worker_report"),
          executorResult: alerts.kindText("executor_result"),
          cloudAllowedFrom: ui.t("ext.offices.privacy.cloud_allowed_from", {{
            source: ui.t("office_level.source.area"),
          }}),
          dueUntil: ui.t("ext.project.filter.due_until"),
          cardTitle: officeText.cardView({{office: {{name: "Synthetic Office"}}}}).title,
        }};
      }};
      ui.setLanguage("en");
      const en = render();
      ui.setLanguage("es");
      const es = render();
      console.log(JSON.stringify({{en, es}}));
    """)
    assert out["en"] == {
        "worker": "Model: step_model_id",
        "team": "A team has not been approved.",
        "sourceError": "The source folder is unavailable. No changes were installed.",
        "question": "Normal: cloud models may be used if you allow them in Privacy",
        "level": "Local only",
        "privacySource": "set for this area",
        "privacyCloudSource": "set for this office",
        "date": "Nov 15, 2026",
        "alertKind": "Document",
        "channelEntry": "Channel message",
        "workerReport": "Report from a worker agent",
        "executorResult": "Result from an app",
        "cloudAllowedFrom": "Cloud models are allowed (set for this area).",
        "dueUntil": "Due on or before",
        "cardTitle": "Synthetic Office",
    }
    assert out["es"] == {
        "worker": "Modelo: step_model_id",
        "team": "Hay un equipo sin aprobar.",
        "sourceError": "La carpeta de origen no está disponible. No se instaló ningún cambio.",
        "question": "Normal: puede usar modelos de la nube si lo permites en «Privacidad»",
        "level": "Solo local",
        "privacySource": "de esta área",
        "privacyCloudSource": "de la oficina",
        "date": "15 nov 2026",
        "alertKind": "Documento",
        "channelEntry": "Entrada del canal",
        "workerReport": "Informe de un trabajador",
        "executorResult": "Resultado de un ejecutor",
        "cloudAllowedFrom": "Modelos de la nube permitidos de esta área.",
        "dueUntil": "Vence hasta",
        "cardTitle": "Synthetic Office",
    }


@needs_node
def test_office_phrases_and_alert_step_render_in_spanish():
    out = run_node(f"""
      const ui = await import({file_url(STATIC_DIR / "js" / "i18n.js")});
      await import({file_url(STATIC_DIR / "extensions" / "offices" / "i18n.js")});
      await import({file_url(STATIC_DIR / "extensions" / "document_alerts" / "i18n.js")});
      ui.setLanguage("es");
      const offices = await import({file_url(STATIC_DIR / "extensions" / "offices" / "import-model.js")});
      const officeModel = await import({file_url(STATIC_DIR / "extensions" / "offices" / "model.js")});
      const question = await import({file_url(STATIC_DIR / "js" / "office-level-question.js")});
      const {{ AlertItem }} = await import({file_url(STATIC_DIR / "extensions" / "document_alerts" / "alert-item.js")});
      const item = AlertItem({{ alert: {{
        id: "synthetic-alert", state: "open", ts: "2026-01-02T00:00:00Z",
        step_id: "synthetic_step", attempt: 3, part: "summary",
        source: {{ kind: "document" }}, policy: "pause", max_severity: "low",
      }} }});
      const text = (node) => {{
        if (Array.isArray(node)) return node.map(text).join("");
        if (node == null || typeof node === "boolean") return "";
        if (typeof node === "string" || typeof node === "number") return String(node);
        if (typeof node.type === "function") return text(node.type(node.props));
        return text(node.props?.children);
      }};
      const findWhere = (node) => {{
        if (Array.isArray(node)) return node.map(findWhere).find(Boolean);
        if (!node || typeof node !== "object") return null;
        if (node.type === "p" && node.props.class === "document-alerts-where") return node;
        if (typeof node.type === "function") return findWhere(node.type(node.props));
        return findWhere(node.props?.children);
      }};
      const findStrong = (node) => {{
        if (Array.isArray(node)) return node.map(findStrong).find(Boolean);
        if (!node || typeof node !== "object") return null;
        if (node.type === "strong") return text(node.props.children);
        if (typeof node.type === "function") return findStrong(node.type(node.props));
        return findStrong(node.props?.children);
      }};
      const where = findWhere(item);
      const visible = {{
        declared: offices.declaredFunctionsText("x", false),
        reimport: offices.importTitle({{ reimport: true }}),
        downloaded: offices.exportDownloadedText("oficina.zip"),
        step: text(where),
        boldStep: findStrong(where),
      }};
      const esDynamic = {{
        empty: officeModel.EMPTY_TEXT(),
        offline: officeModel.OFFLINE_TEXT(),
        teams: offices.TEAMS_NOTE(),
        remove: offices.REMOVE_NOTICE(),
        note: question.NOTE_TITLE(),
      }};
      ui.setLanguage("en");
      const enDynamic = {{
        empty: officeModel.EMPTY_TEXT(),
        offline: officeModel.OFFLINE_TEXT(),
        teams: offices.TEAMS_NOTE(),
        remove: offices.REMOVE_NOTICE(),
        note: question.NOTE_TITLE(),
      }};
      const enVisible = {{
        declared: offices.declaredFunctionsText("x", false),
        reimport: offices.importTitle({{ reimport: true }}),
        downloaded: offices.exportDownloadedText("oficina.zip"),
      }};
      console.log(JSON.stringify({{visible, esDynamic, enDynamic, enVisible}}));
    """)
    assert out["visible"] == {
        "declared": "Funciones declaradas: x",
        "reimport": "Reimportar oficina",
        "downloaded": "oficina.zip se descargó.",
        "step": "Paso synthetic_step, intento 3, parte summary",
        "boldStep": "synthetic_step",
    }
    assert out["esDynamic"] == {
        "empty": "Todavía no tienes oficinas",
        "offline": "Sin conexión; reintento en 4 s",
        "teams": "Al instalar se aprueban los equipos de estas áreas",
        "remove": "Se quita de la lista. Se conservan sus datos",
        "note": "Lo que esta opción no libera",
    }
    assert out["enDynamic"] == {
        "empty": "You do not have any offices yet",
        "offline": "Offline; trying again in 4 sec",
        "teams": "Installing also approves the teams in these areas",
        "remove": "This only removes it from the office list. Its data is kept.",
        "note": "What this setting does not allow",
    }
    assert out["enVisible"] == {
        "declared": "Declared functions: x",
        "reimport": "Import the office again",
        "downloaded": "oficina.zip was downloaded.",
    }
