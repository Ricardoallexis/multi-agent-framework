# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""LIM-T01 round 4: the run panel offers a correction after a cut by the run's own limits, and only then."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.gui import STATIC_DIR
from multiagent.gui.runs import run_view

from gui_text import ui_text

PANEL = STATIC_DIR / "js" / "human-actions.js"


def services():
    return SimpleNamespace(
        connections=SimpleNamespace(list=lambda: {"connections": []}),
        definitions=SimpleNamespace(read_workflow=lambda *_a, **_k: {"steps": []}),
        system=SimpleNamespace(catalog=SimpleNamespace(model=lambda *_: SimpleNamespace(provider="gemini"))))


def review(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                        worker_enabled=False, gemini_api_key="")
    system = build_system(settings, dry_run=True)
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="lim-r4", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    return system, run["id"]


def cut(system, run_id, error, quota=False):
    artifact = system.store.review_artifact(run_id)
    system.store.update_run(run_id, status="waiting_human", error=error, budget_exhausted=1,
                            waiting_reason="budget_exhausted", waiting_step=artifact["step_id"],
                            waiting_attempt=artifact["attempt"])
    if quota:
        system.store.db.log_event(run_id, "model_attempt", {"step_id": artifact["step_id"], "model": "m",
                                                            "provider": "p", "error_type": "QuotaExceeded"})


def test_a_normal_review_allows_corrections(tmp_path):
    system, run_id = review(tmp_path)
    assert run_view(services(), system.run_service.get(run_id))["corrections_allowed"] is True


@pytest.mark.parametrize("error,quota,allowed", [
    ("max_llm_calls reached", False, True), ("max_run_seconds reached", False, True),
    ("max_llm_calls reached", True, False), ("Maximum revision count reached for step=create", False, False)])
def test_the_view_says_whether_a_cut_run_takes_a_correction(tmp_path, error, quota, allowed):
    system, run_id = review(tmp_path)
    cut(system, run_id, error, quota)
    view = run_view(services(), system.run_service.get(run_id))
    assert view["counters"]["budget_exhausted"] is True
    assert view["corrections_allowed"] is allowed


def test_the_panel_uses_the_servers_answer_and_not_its_own_criterion():
    source = PANEL.read_text(encoding="utf-8")
    assert "view.corrections_allowed === false" in source
    assert "const exhausted = waiting.reason" not in source          # the old client-side rule is gone


def test_the_panel_explains_the_cut_and_the_margin_of_a_correction():
    text = ui_text(PANEL)
    assert "Se alcanzó el tope de la ejecución" in text and "cada corrección tiene su propio margen" in text
    assert "Pedir cambios o regenerar ya no es posible" in text      # quota and other cuts keep the old notice
