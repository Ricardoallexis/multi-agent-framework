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
