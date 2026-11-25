# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T02: simple-mode update notices and safe file-diff rendering."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from multiagent.gui import STATIC_DIR
from tests.gui_text import ui_text
from test_gui_office_import import EXT, file_url

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
STATIC_JS = STATIC_DIR / "js"


def run_node(script: str):
    script = f"""
      const mafUi = await import({file_url(STATIC_DIR / "js" / "i18n.js")});
      await import({file_url(EXT / "i18n.js")});
      mafUi.setLanguage("es");
      {script}
    """
    script = re.sub(
        r'(?m)^(\s*)import\s+(\{[^}]*\})\s+from\s+("[^"]*/extensions/offices/(?:import-)?model\.js");',
        r"\1const \2 = await import(\3);",
        script,
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_file_diff_model_keeps_only_safe_relative_paths():
    out = run_node(f"""
      import {{ cardView }} from {file_url(EXT / "import-model.js")};
      const card = cardView({{
        file_changes: [
          {{ area: "menu", path: "prompts/notice.md", change: "changed" }},
          {{ area: "menu", path: "../outside.txt", change: "removed" }},
          {{ area: "menu", path: "C:\\\\private\\\\office.yaml", change: "added" }},
          {{ area: "menu", path: "/private/office.yaml", change: "added" }},
        ],
      }});
      console.log(JSON.stringify(card.fileChanges));
    """)
    assert out == [{"area": "menu", "path": "prompts/notice.md", "change": "Cambiado"}]


def test_chat_offers_reviewed_updates_and_office_controls_in_both_modes():
    chat = ui_text(STATIC_JS / "new-session.js")
    importer = ui_text(EXT / "import.js")
    offices = ui_text(EXT / "index.js")

    assert 'import("../extensions/offices/import.js")' in chat
    assert not re.search(r'import\s+[^;]*?\s+from\s+["\']\.\./extensions/', chat, re.S)
    assert "Hay una actualización disponible para esta oficina." in chat
    assert "La carpeta de origen no está disponible. No se instaló ningún cambio." in chat
    assert '<div class="ns-msg ns-bot notice" role="status">\n        <p>La carpeta de origen no está disponible. No se instaló ningún cambio.</p>' in chat
    assert "Abre «Oficinas» para revisar la actualización." in chat
    assert "currentOfficeDetail.source_update.changed" in chat
    assert "stage-source-update" in importer
    assert "Revisar cambios" in importer
    assert re.search(r'onClick=\$\{install\}>\$\{busy \? "Instalando…" : reimport\s*\? "Actualizar oficina"', importer)
    assert '${!card && html`<div class="offices-import-form">' in importer
    assert "Archivo .zip" in importer
    assert '${showIncludeCode && html`<label class="offices-import-field">Ruta local de la carpeta' in importer
    assert 'onClick=${() => doExport(row.id)}>Exportar área</button>' in offices
    assert 'onClick=${() => doExport()}>Exportar oficina</button>' in offices
    assert 'onClick=${() => onImport({ reimport: office.id })}>Reimportar</button>' in offices
    assert 'onClick=${() => onImport({ into: office.id })}>Añadir un área</button>' in offices
    assert 'topic="offices.export" compact=${true} scope=${officeScope + "-" + row.id}' in offices
    assert 'topic="offices.import_office" compact=${true} scope=${officeScope + "-reimport"}' in offices
    assert 'topic="offices.import_office" compact=${true} scope=${officeScope + "-add-area"}' in offices
    assert '${!simple && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("offices.export"' not in offices
    assert '${!simple && html`<div class="offices-actions offices-import-entry">' not in offices
    assert "source_update.path" not in chat
