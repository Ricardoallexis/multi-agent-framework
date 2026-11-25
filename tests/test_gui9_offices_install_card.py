# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T01: install-card UI distinguishes declarations, scan status and local requirements."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from multiagent.gui import STATIC_DIR

EXT = STATIC_DIR / "extensions" / "offices"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str):
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def file_url(path: Path) -> str:
    return json.dumps(path.as_uri())


@needs_node
def test_card_view_keeps_declarations_separate_and_marks_unavailable_scan():
    out = run_node(f"""
      import {{ cardView }} from {file_url(EXT / "import-model.js")};
      const view = cardView({{
        file_tools_configured: false,
        areas: [{{
          id: "menu",
          declares: {{ workflows: ["weekly_menu"], contracts: ["MenuOutput"], roles: ["cook"] }},
          workflow_declarations: [
            {{ id: "weekly_menu", roles: [], file_tools: ["file.read"] }},
            {{ id: "prepare_order", roles: ["cook"], file_tools: ["file.write"] }},
          ],
        }}],
        code: {{
          present: true, files: ["areas/menu/functions.py"], functions: ["cafe.menu.register"],
          functions_unverified: true, python_contracts: ["PrivateOutput"], python_contracts_unverified: true,
        }},
        scan: {{
          state: "not_installed",
          areas: {{ menu: {{ state: "not_installed" }} }},
        }},
      }});
      console.log(JSON.stringify(view));
    """)
    area = out["areas"][0]
    assert area["declares"] == {
        "workflows": ["weekly_menu"], "contracts": ["MenuOutput"], "roles": ["cook"],
    }
    assert area["workflowDeclarations"] == [
        {"id": "weekly_menu", "roles": [], "fileTools": ["file.read"]},
        {"id": "prepare_order", "roles": ["cook"], "fileTools": ["file.write"]},
    ]
    assert out["fileToolsConfigured"] is False
    assert out["code"]["functions"] == ["cafe.menu.register"]
    assert out["code"]["pythonContracts"] == ["PrivateOutput"]
    assert out["code"]["functionsUnverified"] is True
    assert out["code"]["pythonContractsUnverified"] is True
    assert out["code"]["scanUnavailable"] is True
    assert out["code"]["verdict"] == ""
    assert out["code"]["areas"] == []


def test_card_and_chat_present_one_scan_warning_and_local_requirements():
    importer = (EXT / "import.js").read_text(encoding="utf-8")
    model = (EXT / "import-model.js").read_text(encoding="utf-8")
    help_text = (EXT / "help.js").read_text(encoding="utf-8")
    new_session = (STATIC_DIR / "js" / "new-session.js").read_text(encoding="utf-8")

    assert importer.count("No hay escáner de oficinas disponible: no se hizo la revisión.") == 1
    assert "view.code.scanUnavailable" in importer
    assert "Este workflow no requiere un puesto del equipo; puede ejecutarse sin un equipo aprobado." in importer
    assert "Este workflow necesita herramientas de archivos locales. Configura FILE_TOOLS_ROOT" in importer
    assert "advanced && html`<p>Herramientas requeridas:" in importer
    assert "El navegador no ofrece un selector seguro de carpetas." in importer
    assert "showIncludeCode && html`<label class=\"offices-import-field\">Ruta local de la carpeta" in importer
    assert "not_installed" not in model.split("const SCAN_TEXT", 1)[1].split("});", 1)[0]
    assert "el navegador no ofrece un selector seguro" in help_text
    assert "file_tools_configured" in new_session
    assert "if (fileToolsMissing) blockers.push" in new_session
    assert "Este workflow necesita herramientas de archivos locales. Configura FILE_TOOLS_ROOT" in new_session
    assert '${fileToolsNotice && fileToolsMissing && html`<p class="message error small" role="alert">' in new_session
    assert '${fileToolsNotice && !fileToolsMissing && html`<p class="hint" role="status">' in new_session
