# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""INI-T02: the first-run path of metric 1, automatic. Fake CLIs and a double of «Activar»; nobody presses anything.

Two starts of the same installation: the first in Mock (install, try, connect, activate), the second with real models
(a fake adapter stands in for the provider) and its own ``FirstRunServices``.
"""
from __future__ import annotations

from datetime import datetime

from multiagent.adapters.fake import FakeAdapter
from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.services.first_run import FIRST_RUN_FILE, STEPS

ACTOR = "user:test"


def settings_for(tmp_path, *, mock):
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=mock,
                    gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token")


def run_to_completion(services):
    run_id = services.runs.create(RunRequest(workflow_id="ask_once", project_name="Primer recorrido",
                                             inputs={"task": "Explica la idea."}), bundle="general")["id"]
    assert services.runs._bundle_runtime("general").worker.process_once()
    assert services.runs.get(run_id)["status"] == "completed"
    return run_id


def test_first_run_path_all_five_steps(tmp_path):
    opened = []
    try:
        # Start 1: Mock.
        mock = ApplicationServices.create(settings_for(tmp_path, mock=True), dry_run=True,
                                          adapter_overrides={"ollama": FakeAdapter()})
        opened.append(mock)
        mock.connections.list = lambda: {"connections": []}      # the real list() reads the home of the machine
        assert mock.first_run.status()["pending"] == list(STEPS)

        mock.installed.install_shipped("general", actor=ACTOR)                              # 1. install the example
        assert mock.first_run.status()["pending"] == list(STEPS[1:])
        run_to_completion(mock)                                                             # 2. a Mock run, completed
        status = mock.first_run.status()
        assert status["pending"] == list(STEPS[2:]) and status["offer_real"] is True
        mock.connections.list = lambda: {"connections": [{"id": "claude_cli", "kind": "executor",   # 3. a fake CLI
                                                          "available": True, "reason": None}]}
        assert mock.first_run.status()["pending"] == list(STEPS[3:])
        mock.executor_activations.active_ids = lambda: ["claude_cli"]                       # 4. the double of «Activar»
        status = mock.first_run.status()
        assert status["pending"] == ["real_run"] and status["all_done"] is False

        answer = mock.first_run.set_models("real")                                          # the button of «Inicio»
        assert answer["models"] == "real" and answer["restart_required"] is True

        # Start 2: the person opens Iniciar again, now with real models (a fake adapter instead of the provider).
        real = ApplicationServices.create(settings_for(tmp_path, mock=False), dry_run=False,
                                          adapter_overrides={"ollama": FakeAdapter(
                                              sample_outputs={"FreeTextAnswer": {"answer": "Una respuesta sencilla."}},
                                              output_schemas=None)})
        opened.append(real)
        real.connections.list = lambda: {"connections": []}
        status = real.first_run.status()
        assert status["mock"] is False and status["offer_real"] is False
        assert "real_run" in status["pending"] and "try_mock" not in status["pending"]   # the saved steps stay done

        run_to_completion(real)                                                             # 5. a run with real models
        status = real.first_run.status()
        assert status["all_done"] is True and status["pending"] == []
        hours = [datetime.fromisoformat(item["done_at"]) for item in status["times"]]
        assert [item["id"] for item in status["times"]] == list(STEPS)
        assert len(hours) == 5 and hours == sorted(hours)
        assert (tmp_path / "local" / FIRST_RUN_FILE).is_file()
        assert "Correr con tus datos y guardar el documento" in real.first_run.times_text()
    finally:
        for services in opened:
            services.runs.close()
