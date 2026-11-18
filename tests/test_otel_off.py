# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CAP-T21: with OpenTelemetry off or not installed, nothing changes.

These checks need no OpenTelemetry package, so they live apart from tests/test_otel_traces.py, which is
skipped as a whole where opentelemetry-api is missing (an install without requirements-dev.txt). They also
pass with the packages installed, as in CI: the setting off still imports no OpenTelemetry module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.observability import otel
from multiagent.observability.otel import build_telemetry
from multiagent.observability.usage import read_usage


def settings_for(tmp_path: Path, **extra) -> Settings:
    return Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                    runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                    worker_enabled=False, gemini_api_key="", **extra)


def run_once(system):
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="otel", objective="Create an educational post", topic="KNX"))
    assert system.worker.process_once() is True
    return run["id"], system.run_service.get(run["id"])


def test_off_by_default_builds_nothing(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("OpenTelemetry must not be touched when the setting is off")

    monkeypatch.setattr(otel, "_sdk_providers", forbidden)
    assert build_telemetry(settings_for(tmp_path)) is None
    system = build_system(settings_for(tmp_path), dry_run=True)
    assert system.engine.telemetry is None
    assert system.store.db.event_listeners == [] and system.engine.usage_recorder.observers == []
    run_id, state = run_once(system)
    assert state["status"] == RunStatus.WAITING_HUMAN.value and len(read_usage(system.store, run_id)) == 1


def test_missing_package_with_the_setting_on_changes_nothing(tmp_path, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    with caplog.at_level("WARNING"):
        assert build_telemetry(settings_for(tmp_path, telemetry_otel_enabled=True)) is None
    assert "opentelemetry-api is not installed" in caplog.text
    system = build_system(settings_for(tmp_path, telemetry_otel_enabled=True), dry_run=True)
    assert system.engine.telemetry is None
    assert run_once(system)[1]["status"] == RunStatus.WAITING_HUMAN.value


def test_run_with_the_setting_off_imports_no_opentelemetry(tmp_path):
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from multiagent.bootstrap import build_system\n"
        "from multiagent.config import Settings\n"
        "from multiagent.contracts import SocialPostRequest\n"
        f"root = Path({str(tmp_path / 'sub')!r})\n"
        "s = Settings(data_dir=root / 'd', db_path=root / 'd' / 'm.db', runs_dir=root / 'd' / 'r',\n"
        "             assets_dir=root / 'd' / 'a', worker_enabled=False, gemini_api_key='')\n"
        "system = build_system(s, dry_run=True)\n"
        "system.run_service.create_social_post(SocialPostRequest(project_name='xx', objective='Create a post', topic='KNX'))\n"
        "system.worker.process_once()\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'opentelemetry'))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120,
                          cwd=str(Path(__file__).resolve().parents[1]),
                          env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("[]"), done.stdout
