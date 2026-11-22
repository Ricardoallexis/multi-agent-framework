# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""``multiagent doctor`` is the last step of setup_windows.bat, so it must finish on a clean machine.

It must not hang on a command that leaves a process holding its output, must not start Ollama when it is stopped,
and must accept every Python version that pyproject.toml declares."""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from multiagent import doctor
from multiagent.config import Settings

KEEPER = (
    "import subprocess, sys\n"
    "keeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
    " stdout=sys.stdout, stderr=sys.stderr)\n"
    "print(keeper.pid, flush=True)\n"
)


def test_a_command_that_leaves_a_process_holding_its_output_does_not_hang_the_doctor():
    started = time.monotonic()
    code, out = doctor._cmd([sys.executable, "-c", KEEPER], timeout=20)
    elapsed = time.monotonic() - started
    try:
        assert code == 0 and out.isdigit(), out
        assert elapsed < 10                     # it returns when the command ends, not when the keeper does
    finally:
        if out.isdigit():
            os.kill(int(out), signal.SIGTERM)


def test_a_command_that_outlives_its_timeout_is_stopped():
    started = time.monotonic()
    code, out = doctor._cmd([sys.executable, "-c", "import time; time.sleep(60)"], timeout=1)
    assert code == 1 and "timed out" in out
    assert time.monotonic() - started < 10


def test_the_doctor_does_not_run_the_ollama_cli_while_ollama_is_stopped(tmp_path, monkeypatch):
    calls = []

    def record(args, timeout=8):
        calls.append(list(args))
        return 1, ""

    monkeypatch.setattr(doctor, "_cmd", record)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", ollama_base_url="http://127.0.0.1:9",
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    ok, report = doctor.run_doctor(settings)
    assert report["ollama"]["available"] is False
    assert not [args for args in calls if Path(args[0]).stem.lower() == "ollama"]   # on Windows it would start the app
    assert report["python"]["supported"] is True                                   # CI runs every declared version
    assert ok is True


def test_the_supported_python_range_is_the_one_pyproject_declares():
    (low_major, low_minor), (high_major, high_minor) = doctor.SUPPORTED_PYTHON
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'requires-python = ">={low_major}.{low_minor},<{high_major}.{high_minor}"' in pyproject


def test_deep_reports_the_certificate_state_of_every_provider(tmp_path, monkeypatch):
    """ARR3-T07: ``doctor --deep`` says per provider which trust is in force and whether the certificate verified."""
    from types import SimpleNamespace

    class Provider:
        def __init__(self, outcome):
            self.outcome = outcome

        def probe(self):
            return self.outcome

        def health(self):
            return {"available": False, "error": "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"}

        def generate_structured(self, **kwargs):
            raise RuntimeError("no generation in this test")

    real_build = doctor.build_system

    def build(settings):
        system = real_build(settings)
        system.adapters.update(gemini=Provider("ok"), openai=Provider("tls_certificate"), ollama=Provider("ok"))
        return system

    monkeypatch.setattr(doctor, "build_system", build)
    monkeypatch.setattr(doctor, "_cmd", lambda args, timeout=8: (1, ""))
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="SYNTHETIC", openai_api_key="SYNTHETIC",
                        openai_enabled=True, provider_tls_trust="system")
    ok, report = doctor.run_doctor(settings, deep=True)
    assert report["tls"]["openai"] == {"trust": "system", "state": "tls_certificate"}
    assert report["tls"]["gemini"]["state"] == "ok"
    assert report["tls"]["ollama"]["state"] == "tls_certificate"                    # its health error is the certificate
    assert any("TLS certificate" in line for line in report["recommendations"])
    _, shallow = doctor.run_doctor(settings)
    assert "tls" not in shallow
    _, skipped = doctor.run_doctor(Settings(_env_file=None, local_dir=tmp_path / "local2"), deep=True)
    assert skipped["tls"]["openai"]["state"] == "skipped" and skipped["tls"]["openai"]["trust"] == "certifi"
