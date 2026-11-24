# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CER-T01: after ``close()`` nothing of the framework runs or writes, and a temporary local folder can be removed.

Public fixture only: the example ``general`` package in a temporary ``LOCAL_DIR``, in Mock.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.services.errors import ServiceError


GENERAL = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general"
REQUEST = {"workflow_id": "collect_summary", "project_name": "Close test",
           "inputs": {"document_text": "A short document with one key point.", "focus": "Key points"}}


def framework_threads() -> list[str]:
    return [thread.name for thread in threading.enumerate()
            if thread.is_alive() and thread.name.startswith("multiagent-")]


def make_services(tmp_path: Path) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=True, worker_poll_seconds=0.1,
                        mock_mode=True, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    return ApplicationServices.create(settings, bundles={"general": GENERAL}, dry_run=True)


def complete_one_run(services: ApplicationServices) -> str:
    run_id = services.runs.create(RunRequest(**REQUEST), bundle="general")["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = services.runs.get(run_id)
        if run["status"] in {"completed", "failed", "cancelled"}:
            break
        threading.Event().wait(0.05)
    assert services.runs.get(run_id)["status"] == "completed"
    return run_id


@pytest.fixture
def closed(tmp_path):
    before = set(framework_threads())
    services = make_services(tmp_path)
    complete_one_run(services)
    assert set(framework_threads()) - before, "the bundle worker should be alive before close"
    services.runs.close()
    return services, tmp_path, before


def test_close_leaves_no_framework_thread(closed):
    _services, _tmp, before = closed
    assert [name for name in framework_threads() if name not in before] == []


def test_the_local_folder_can_be_removed_and_stays_removed(closed):
    _services, tmp_path, _before = closed
    local = tmp_path / "local"
    assert (local / "data" / "bundles" / "general" / "multiagent.db").exists()
    shutil.rmtree(local)
    assert not local.exists()
    assert not any(tmp_path.rglob("multiagent.db"))


def test_after_close_a_call_does_not_build_the_runtime_of_a_package(tmp_path):
    services = make_services(tmp_path)
    services.runs.close()
    with pytest.raises(ServiceError):
        services.runs.create(RunRequest(**REQUEST), bundle="general")
    assert framework_threads() == []
    assert not (tmp_path / "local" / "data" / "bundles" / "general" / "multiagent.db").exists()


def test_close_does_not_wait_for_a_run_thread_that_needs_the_service(tmp_path, monkeypatch):
    """The plan hook of a finishing run reads the runtimes; ``close`` must not hold the lock the hook needs."""
    before = set(framework_threads())
    services = make_services(tmp_path)
    in_hook, close_called = threading.Event(), threading.Event()
    original = services.runs.close

    def spy():
        close_called.set()
        original()

    def hook(run_id):
        in_hook.set()
        close_called.wait(10)
        threading.Event().wait(0.3)      # let close() take its lock first: the order that used to deadlock
        services.runs.get(run_id)

    monkeypatch.setattr(services.plan_autoadvance, "run_finished", hook)
    monkeypatch.setattr(services.runs, "close", spy)
    services.runs.create(RunRequest(**REQUEST), bundle="general")
    assert in_hook.wait(30)
    started = time.monotonic()
    services.runs.close()
    assert time.monotonic() - started < 10     # the worker waits up to 30 s for a run thread that never ends
    assert [name for name in framework_threads() if name not in before] == []
