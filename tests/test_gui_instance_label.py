# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INSTANCE_LABEL in the GUI header and the tab title (PRV-T01)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import INSTANCE_LABEL_MAX, Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def read(name: str) -> str:
    return ui_text((JS / name))


def run_node(source: str):
    result = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True, encoding="utf-8",
                            cwd=STATIC_DIR, timeout=30, check=True)
    return json.loads(result.stdout)


def instance_uri() -> str:
    return json.dumps((JS / "instance.js").as_uri())


def make_settings(tmp_path, **extra):
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token",
                    **extra)


def session_body(tmp_path, **extra) -> dict:
    settings = make_settings(tmp_path, **extra)
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    try:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            response = client.get("/gui/api/session", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        return response.json()
    finally:
        services.runs.close()


def test_default_is_empty(tmp_path, monkeypatch):
    monkeypatch.delenv("INSTANCE_LABEL", raising=False)
    assert session_body(tmp_path)["instance_label"] == ""


def test_label_reaches_session(tmp_path):
    body = session_body(tmp_path, instance_label="Prueba A")
    assert body["instance_label"] == "Prueba A"
    assert set(body) == {"mock", "version", "mode", "instance_label"}


def test_label_reads_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("INSTANCE_LABEL", "Prueba B")
    assert make_settings(tmp_path).instance_label == "Prueba B"


def test_label_validated(tmp_path):
    assert make_settings(tmp_path, instance_label="x" * INSTANCE_LABEL_MAX).instance_label == "x" * 40
    for bad in ("x" * (INSTANCE_LABEL_MAX + 1), "Prueba\nA", "Prueba\tA", "Prueba\x00A", "Prueba\x7fA"):
        with pytest.raises(ValueError):
            make_settings(tmp_path, instance_label=bad)


@needs_node
def test_page_title():
    out = run_node("""
      import { instanceLabel, setInstanceLabel, pageTitle, INSTANCE_LABEL_MAX } from %s;
      const plain = [pageTitle(0), pageTitle(3)];
      setInstanceLabel("Prueba A");
      const labelled = [pageTitle(0), pageTitle(3)];
      setInstanceLabel("x".repeat(41)); const afterLong = instanceLabel();
      setInstanceLabel(7); const afterNumber = instanceLabel();
      setInstanceLabel("Prueba A"); setInstanceLabel("a\\nb"); const afterControl = instanceLabel();
      console.log(JSON.stringify({ plain, labelled, afterLong, afterNumber, afterControl, max: INSTANCE_LABEL_MAX }));
    """ % instance_uri())
    assert out["plain"] == ["Multi-Agent Framework", "(3) Multiagent"]
    assert out["labelled"] == ["Prueba A · Multi-Agent Framework", "(3) Prueba A · Multiagent"]
    assert out["afterLong"] == "" and out["afterNumber"] == "" and out["afterControl"] == ""
    assert out["max"] == INSTANCE_LABEL_MAX == 40


@needs_node
def test_label_listeners_hear_a_real_change_only():
    out = run_node("""
      import { setInstanceLabel, onInstanceLabelChange } from %s;
      const heard = []; const off = onInstanceLabelChange((value) => heard.push(value));
      setInstanceLabel("A"); setInstanceLabel("A"); setInstanceLabel("B"); off(); setInstanceLabel("C");
      console.log(JSON.stringify(heard));
    """ % instance_uri())
    assert out == ["A", "B"]


@needs_node
def test_header_badge_is_text():
    """The label is kept as the literal string and the header draws it as a text child, never as markup."""
    out = run_node("""
      import { instanceLabel, setInstanceLabel, pageTitle } from %s;
      setInstanceLabel("<b>x</b>");
      console.log(JSON.stringify({ label: instanceLabel(), title: pageTitle(0) }));
    """ % instance_uri())
    assert out == {"label": "<b>x</b>", "title": "<b>x</b> · Multi-Agent Framework"}
    shell = read("shell.js")
    badge = re.search(r"\$\{label && html`(.*?)`\}", shell, re.S)
    assert badge, "the header badge of the instance label"
    assert "<span class=\"badge instance\" title=${label}>${label}</span>" in badge.group(1)
    for name in ("shell.js", "instance.js", "pending.js"):
        source = read(name)
        assert "innerHTML" not in source and "dangerouslySetInnerHTML" not in source and "style=" not in source, name


def test_only_page_title_writes_document_title():
    writers = {path.name for path in JS.rglob("*.js") if re.search(r"document\.title\s*=(?!=)", path.read_text(encoding="utf-8"))}
    assert writers == {"pending.js"}
    source = read("pending.js")
    assert len(re.findall(r"document\.title\s*=(?!=)", source)) == 1
    assert "document.title = pageTitle(count);" in source


def test_help_key_exists_and_is_used():
    help_source = read("help-text.js")
    assert '"shell.instance_label": {' in help_source
    shell = read("shell.js")
    assert 'topic="shell.instance_label"' in shell
    # the key sits inside the HELP object, which stays strict JSON
    block = help_source.split("export const HELP = ", 1)[1].split("\n};", 1)[0] + "\n}"
    assert "shell.instance_label" in json.loads(block)


def test_env_example_and_docs_name_the_setting():
    root = Path(__file__).resolve().parents[1]
    assert re.search(r"^INSTANCE_LABEL=$", (root / ".env.example").read_text(encoding="utf-8"), re.M)
    docs = (root / "docs" / "GUI.md").read_text(encoding="utf-8")
    assert "## Instance label" in docs and "`INSTANCE_LABEL`" in docs
