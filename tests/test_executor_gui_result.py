# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Only the latest submitted result summary enters GUI executor status."""
from __future__ import annotations

import pytest

from multiagent.services import ServiceError
from test_gui_executor_launch import GUI_LAUNCH, GuiWorld


@pytest.fixture
def world(tmp_path, monkeypatch):
    instance = GuiWorld(tmp_path, monkeypatch)
    yield instance
    instance.client.close()
    instance.services.runs.close()


def test_status_exposes_only_the_latest_submitted_summary(world):
    services = world.services
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    services.tasks.create_task("G", "T1", "Task", actor="user")
    url = GUI_LAUNCH + "/T1"
    before = world.client.get(url, headers=world.auth)
    assert before.status_code == 200 and before.json()["latest_result"] is None

    services.tasks.transition("T1", "claim", actor="agent:a")
    services.tasks.transition("T1", "start", actor="agent:a")
    services.envelopes.submit_result("T1", {"summary": "First.", "decisions": ["private"]}, actor="agent:a")
    services.tasks.transition("T1", "request_changes", actor="user")
    services.tasks.transition("T1", "start", actor="agent:a")
    services.envelopes.submit_result("T1", {"summary": "Second <script>x()</script>",
                                            "decisions": ["private"]}, actor="agent:a")
    assert [row["attempt"] for row in services.envelopes.list_results("T1", latest_only=True)] == [2]
    assert [row["attempt"] for row in services.envelopes.list_results("T1")] == [2, 1]

    response = world.client.get(url, headers=world.auth)
    assert response.status_code == 200
    assert response.json()["latest_result"] == {
        "summary": "Second <script>x()</script>", "attempt": 2, "ref": "result://T1/2"}
    assert "private" not in response.text
    assert "patch" not in response.text


def test_missing_task_and_result_keep_explicit_errors(world):
    response = world.client.get(GUI_LAUNCH + "/missing", headers=world.auth)
    assert response.status_code == 404 and response.json()["detail"]["code"] == "not_found"
    services = world.services
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    services.tasks.create_task("G", "T1", "Task", actor="user")
    assert world.client.get(GUI_LAUNCH + "/T1", headers=world.auth).json()["latest_result"] is None
    with pytest.raises(ServiceError) as caught:
        services.envelopes.get_result("T1")
    assert caught.value.code == "not_found"
