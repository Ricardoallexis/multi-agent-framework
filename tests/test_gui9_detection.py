"""Apps cards reflect measured version and activation evidence."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "multiagent" / "gui" / "static" / "extensions" / "apps" / "model.js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_apps_view_shows_version_context_and_activation_conflicts():
    payload = {
        "connections": [
            {
                "id": "claude_cli",
                "installed": True,
                "version": None,
                "version_result": "timed_out",
                "detection_context": "packaged_process",
                "windows_package_path": True,
                "session": "signed_in",
                "enabled": False,
                "available": False,
                "reason": "version_check_failed",
                "checked_at": "2026-11-25T17:00:00+00:00",
            },
            {
                "id": "codex_cli",
                "installed": False,
                "version": None,
                "version_result": "probe_failed",
                "session": "signed_in",
                "enabled": False,
                "available": False,
                "reason": "not_installed",
                "missing_step": "install",
                "checked_at": "2026-11-25T17:00:00+00:00",
            },
            {
                "id": "copilot_cli",
                "installed": True,
                "version": None,
                "version_result": "uninterpretable",
                "session": "unknown",
                "enabled": False,
                "available": False,
                "reason": "version_check_failed",
                "checked_at": "2026-11-25T17:00:00+00:00",
            },
        ]
    }
    module_url = json.dumps(MODEL.as_uri())
    payload_json = json.dumps(payload)
    script = f"""
      import {{ buildAppsView }} from {module_url};
      const payload = {payload_json};
      const activations = {{ executors: [
        {{ id: "claude_cli", state: "ready" }},
        {{ id: "codex_cli", state: "ready" }},
        {{ id: "copilot_cli", state: "ready" }},
      ] }};
      console.log(JSON.stringify(buildAppsView(payload, null, "advanced", activations).executors));
    """
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    entries = json.loads(completed.stdout)
    claude, codex, copilot = entries

    assert claude["version"] == "Versión no comprobada · no respondió a tiempo"
    assert claude["installed"] == "Encontrado en el contexto de esta app; no se verificó su disponibilidad fuera de ella"
    assert claude["checkedAt"] == "2026-11-25T17:00:00+00:00"
    assert claude["chip"]["status"] == "conflict"
    assert claude["activation"]["state"] == "conflict" and claude["activation"]["actions"] == []
    assert codex["version"] == "Versión no comprobada · no se pudo ejecutar"
    assert codex["installed"] == "No"
    assert codex["chip"]["status"] == "conflict"
    assert codex["activation"]["state"] == "conflict" and codex["activation"]["actions"] == []
    assert copilot["version"] == "Versión no comprobada · respuesta no reconocida"
    displayed = json.dumps(entries, ensure_ascii=False)
    assert all(code not in displayed for code in ("probe_failed", "timed_out", "uninterpretable"))
