# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Activation drift is visible on the next status read, with a fake CLI detector."""
from __future__ import annotations

from test_executor_activations import activate, env  # noqa: F401 - shared fixture and fake detector


def test_status_pauses_changed_executor_and_preserves_fields(env):
    activate(env, "claude_cli")
    assert next(row for row in env.activations.status() if row["id"] == "claude_cli")["state"] == "active"
    env.detector.set("claude", version="3.0.0", account="another@example.test")

    response = env.client.get("/gui/api/executors/activations", headers=env.headers)
    assert response.status_code == 200
    row = next(row for row in response.json()["executors"] if row["id"] == "claude_cli")
    assert row["state"] == "paused" and row["paused"]["reason"] == "changed"
    assert [change["field"] for change in row["changes"]] == ["major", "account"]
    assert row["changes"] == row["paused"]["changes"]
    assert "claude_cli" not in env.activations.active_ids()
    assert next(item for item in env.activations.status() if item["id"] == "claude_cli")["changes"] == row["changes"]
