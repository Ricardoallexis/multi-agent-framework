# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SES-T04: explicitly approved GUI executor launches and status tracking."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.executors import process
from multiagent.executors.antigravity import NOTICE_ID, RUN_IN_PROGRESS
from multiagent.executors.claude import ClaudeAdapter
from multiagent.executors.codex import CodexExecutorAdapter
from multiagent.executors.copilot import CopilotAdapter
from multiagent.executors.registry import ExecutorRegistry
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.assignment import AssignmentServices
from test_executor_antigravity import FakeAgy, good_run, make_adapter, usage_text
from test_executor_services import Fake

ROOT = Path(__file__).resolve().parents[1]
GUI_LAUNCH = "/gui/api/executors/launch"
EXECUTOR_IDS = ("claude_cli", "codex_cli", "copilot_cli", "antigravity_cli")
NODE = shutil.which("node")


class GuiWorld:
    def __init__(self, tmp_path: Path, monkeypatch):
        real_find = process.find_executable

        def guard(name, **kwargs):
            if name in ("claude", "codex", "copilot", "agy"):
                raise AssertionError(f"real executor lookup attempted: {name}")
            return real_find(name, **kwargs)

        monkeypatch.setattr(process, "find_executable", guard)
        settings = Settings(
            _env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
            gemini_api_key="", openai_api_key="", openai_enabled=False,
            gui_token_file=tmp_path / "gui-token", antigravity_notice_accepted=NOTICE_ID,
            antigravity_kill_switch=str(tmp_path / "antigravity-off"),
            antigravity_lock_file=str(tmp_path / "antigravity-run.lock"),
        )
        self.services = ApplicationServices.create(settings)
        self.fake_cli = Fake(tmp_path / "fake-cli")
        self.fake_agy = FakeAgy(tmp_path / "fake-agy", monkeypatch)
        self.adapters = [
            ClaudeAdapter(command=self.fake_cli.command("claude")),
            CodexExecutorAdapter(command=self.fake_cli.command("codex")),
            CopilotAdapter(self.fake_cli.command("copilot"), session_guard=self.services.executors.session_guard),
            make_adapter(self.fake_agy, tmp_path, notice_accepted=NOTICE_ID,
                         kill_switch=tmp_path / "antigravity-off", lock_path=tmp_path / "antigravity-run.lock"),
        ]
        self.config = copy.deepcopy(self.services.system.catalog.executor_routing)
        self.token = ensure_token(settings.gui_token_file)
        self.auth = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(create_app(services=self.services), base_url="http://localhost")
        self.configure(())

    def configure(self, enabled):
        config = copy.deepcopy(self.config)
        for executor_id in EXECUTOR_IDS:
            config["executors"][executor_id]["enabled"] = executor_id in enabled
        registry = ExecutorRegistry(config, self.adapters)
        self.services.system.catalog.executor_routing = config
        self.services.system.catalog.executor_routing_valid = True
        self.services.assignment = AssignmentServices(self.services, registry=registry)


@pytest.fixture
def world(tmp_path, monkeypatch):
    instance = GuiWorld(tmp_path, monkeypatch)
    yield instance
    instance.client.close()
    instance.services.runs.close()


def launch_body(executor_id, **values):
    return {"task": "Summarize the current project.", "executor_id": executor_id, "confirmed": True, **values}


def test_gui_launch_requires_token(world):
    response = world.client.post(GUI_LAUNCH, json=launch_body("claude_cli"))
    assert response.status_code == 401
    assert world.fake_cli.calls() == [] and world.fake_agy.calls() == []


def test_launch_requires_confirmation_and_does_not_create_a_task(world):
    before = world.services.tasks.list_groups()
    response = world.client.post(GUI_LAUNCH, headers=world.auth,
                                 json={"task": "Summarize this.", "executor_id": "claude_cli"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "confirmation_required"
    assert world.services.tasks.list_groups() == before
    assert world.fake_cli.calls() == []


def test_copilot_without_a_model_is_rejected_before_task_creation(world):
    before = world.services.tasks.list_groups()
    response = world.client.post(GUI_LAUNCH, headers=world.auth, json=launch_body("copilot_cli"))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "model_required"
    assert world.services.tasks.list_groups() == before
    assert world.fake_cli.calls() == []


def test_disabled_agy_is_rejected_with_its_reason(world):
    response = world.client.post(GUI_LAUNCH, headers=world.auth, json=launch_body("antigravity_cli"))
    assert response.status_code == 409
    detail = response.json()["detail"]
    # The service reports a refused launch as invalid_state with the reason in details (services/executors.py).
    assert detail["code"] == "invalid_state"
    assert detail["details"]["reason"] == "executor_disabled"
    assert "disabled" in detail["message"]
    assert world.fake_agy.calls() == []
    # A refused launch leaves no open task behind: the task the route created is cancelled.
    launched = [task for task in world.services.tasks.list_tasks() if task["id"].startswith("gui-executor-")]
    assert len(launched) == 1 and launched[0]["status"] == "cancelled"


def test_agy_below_launch_floor_is_rejected_with_the_quota_reason(world):
    world.configure({"antigravity_cli"})
    world.fake_agy.set(
        usage={"stdout": usage_text(gemini_week=12, gemini_5h=12, other_week=12, other_5h=12, argon=12)},
        run={"stdout": []},
    )
    response = world.client.post(GUI_LAUNCH, headers=world.auth, json=launch_body("antigravity_cli"))
    assert response.status_code == 200
    assert "quota_below_launch_floor" in response.text
    assert world.fake_agy.runs() == []


def test_a_second_agy_launch_is_rejected_while_the_first_is_running(world):
    world.configure({"antigravity_cli"})
    world.fake_agy.set(run={"stdout": good_run(), "sleep": 1.5})
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(world.client.post, GUI_LAUNCH, headers=world.auth,
                            json=launch_body("antigravity_cli"))
        deadline = time.monotonic() + 8
        while not world.fake_agy.runs() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert world.fake_agy.runs(), "the first launch did not reach the fake agy binary"
        second = world.client.post(GUI_LAUNCH, headers=world.auth, json=launch_body("antigravity_cli"))
        assert second.status_code == 409
        assert second.json()["detail"]["details"]["code"] == RUN_IN_PROGRESS
        assert first.result(timeout=30).status_code == 200
    assert len(world.fake_agy.runs()) == 1


def test_confirmed_launch_returns_and_serves_executor_task_status(world):
    world.configure({"claude_cli"})
    response = world.client.post(GUI_LAUNCH, headers=world.auth, json=launch_body("claude_cli"))
    assert response.status_code == 200, response.text
    body = response.json()
    task_id = body["task_id"]
    assert body["launch"]["executor_id"] == "claude_cli"
    assert body["status"]["task"]["task_id"] == task_id
    assert body["status"]["task"]["sessions"][0]["executor_id"] == "claude_cli"
    assert world.fake_cli.calls("claude")
    status = world.client.get(GUI_LAUNCH + "/" + task_id, headers=world.auth)
    assert status.status_code == 200
    assert status.json()["task"]["task_id"] == task_id


def run_node(script: str) -> Any:
    if not NODE:
        pytest.skip("Node is not installed")
    module = (ROOT / "multiagent" / "gui" / "static" / "js" / "new-session-model.js").as_uri()
    source = f"""
      const m = await import({json.dumps(module)});
      {script}
    """
    completed = subprocess.run([NODE, "--input-type=module", "-e", source], capture_output=True, text=True,
                               encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_node_executor_approval_body_carries_only_the_confirmed_selection():
    body = run_node("""
      console.log(JSON.stringify(m.executorLaunchBody("  Review this code  ", "codex_cli", "gpt-5", "high")));
    """)
    assert body == {"task": "Review this code", "executor_id": "codex_cli", "model": "gpt-5",
                    "effort": "high", "confirmed": True}


def test_node_quota_summary_reports_remaining_and_unknown_values():
    lines = run_node("""
      console.log(JSON.stringify(m.quotaRemainingLines({ windows: [
        { group: "Gemini", window: "weekly", used_percent: 72.5, resets_at: "2026-11-20" },
        { group: "Claude", window: "5h", used_percent: null }
      ] })));
    """)
    assert lines == ["Gemini · weekly: 27,5 % restante · reinicia 2026-11-20",
                     "Claude · 5h: desconocido restante"]
