# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError
from multiagent.tasks.release import evaluate


def make_tasks(root: Path):
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True).tasks


@pytest.mark.parametrize("facts", [
    {"delivery_sha256": "a" * 64, "has_patch": False},
    {"delivery_sha256": "a" * 64, "has_patch": True, "integration_commit": "1234567",
     "integration_ref": "report://integration"},
])
def test_unresolved_release_mode_fails_closed_even_with_strong_facts(facts):
    satisfied, reason, _ = evaluate(None, facts)
    assert not satisfied
    assert reason == "release_mode_unresolved"


def test_stale_fact_evaluation_is_fail_closed_even_when_caller_bypasses_writer():
    satisfied, reason, _ = evaluate("probada", {
        "delivery_ref": "report://delivery", "delivery_sha256": "a" * 64,
        "has_patch": True, "patchset_sha256": "b" * 64, "test_verdict": "passed",
        "test_patchset_sha256": "d" * 64, "test_ref": "report://stale-test",
    })
    assert not satisfied
    assert reason == "test_patchset_mismatch"


def test_claim_starts_new_release_round_and_reblocks_dependents(tmp_path):
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.set_release_mode("G", "probada", actor="user")
    tasks.create_task("G", "G-P", "Prerequisite", actor="user")
    tasks.create_task("G", "G-D", "Dependent", actor="user")
    tasks.add_release_dependency("G-D", "G-P", actor="user", source_ref="doc://fixture/release.md")
    tasks.record_delivery_fact("G-P", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    assert tasks.get_task("G-D")["status"] == "ready"

    tasks.transition("G-P", "claim", actor="agent:a")
    assert tasks.get_release_facts("G-P")["delivery_sha256"] == ""
    assert tasks.get_task("G-D")["status"] == "blocked"
    event = [event for event in tasks.events(task_id="G-P") if event["type"] == "task.claimed"][-1]
    assert event["payload"]["release_facts_reset"] is True


@pytest.mark.parametrize("fact_kind", ["delivery_without_patch", "integration"])
def test_unresolved_mode_blocks_delivery_and_integration_facts(tmp_path, fact_kind):
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "G-P", "Prerequisite", actor="user")
    tasks.create_task("G", "G-D", "Dependent", actor="user")
    tasks.add_release_dependency("G-D", "G-P", actor="user", source_ref="doc://fixture/release.md")
    tasks.record_delivery_fact("G-P", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=(fact_kind == "integration"),
                               patchset_sha256=("b" * 64 if fact_kind == "integration" else ""), actor="user")
    if fact_kind == "integration":
        tasks.record_integration_fact("G-P", integration_commit="c" * 40,
                                      integration_ref="report://integration", actor="user")
    assert tasks.get_task("G-D")["status"] == "blocked"


def test_stale_test_verdict_is_rejected_without_releasing_probada_dependency(tmp_path):
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.set_release_mode("G", "probada", actor="user")
    tasks.create_task("G", "G-P", "Prerequisite", actor="user")
    tasks.create_task("G", "G-D", "Dependent", actor="user")
    tasks.add_release_dependency("G-D", "G-P", actor="user", source_ref="doc://fixture/release.md")
    tasks.record_delivery_fact("G-P", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=True, patchset_sha256="b" * 64, actor="user")
    with pytest.raises(ServiceError) as caught:
        tasks.record_test_verdict("G-P", test_verdict="passed", test_patchset_sha256="d" * 64,
                                  test_ref="report://stale-test", actor="user")
    assert caught.value.code == "invalid_state"
    assert tasks.get_task("G-D")["status"] == "blocked"


def test_cross_group_edge_uses_dependent_group_mode(tmp_path):
    tasks = make_tasks(tmp_path)
    tasks.create_group("PREREQ", "Prerequisite group", actor="user", authorized=True)
    tasks.create_group("DEPEND", "Dependent group", actor="user", authorized=True)
    tasks.set_release_mode("DEPEND", "terminada", actor="user")
    tasks.create_task("PREREQ", "P", "Prerequisite", actor="user")
    tasks.create_task("DEPEND", "D", "Dependent", actor="user")
    tasks.add_release_dependency("D", "P", actor="user", source_ref="doc://fixture/cross-group.md")
    tasks.record_delivery_fact("P", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=True, patchset_sha256="b" * 64, actor="user")
    assert tasks.get_task("D")["status"] == "ready"
    tasks.set_release_mode("PREREQ", "integrada", actor="user")
    assert tasks.get_task("D")["status"] == "ready"
    tasks.set_release_mode("DEPEND", "integrada", actor="user")
    assert tasks.get_task("D")["status"] == "blocked"


def test_native_and_legacy_edges_share_duplicate_and_cycle_checks(tmp_path):
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.set_release_mode("G", "probada", actor="user")
    tasks.create_task("G", "G-A", "A", actor="user")
    tasks.create_task("G", "G-B", "B", actor="user")
    tasks.add_release_dependency("G-B", "G-A", actor="user", source_ref="doc://fixture/a.md")
    with pytest.raises(ServiceError):
        tasks.add_dependency("G-A", "G-B", actor="user")
    with pytest.raises(ServiceError):
        tasks.add_dependency("G-B", "G-A", actor="user")
    assert tasks.get_task("G-A")["depends_on"] == []
    assert len(tasks.get_task("G-B")["release_dependencies"]) == 1


def test_context_v2_reports_prerequisite_status_from_native_and_legacy_union(tmp_path):
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.set_release_mode("G", "probada", actor="user")
    for task_id in ("G-A", "G-B", "G-C"):
        tasks.create_task("G", task_id, task_id, actor="user")
    tasks.add_release_dependency("G-B", "G-A", actor="user", source_ref="doc://fixture/a.md")
    tasks.add_release_dependency("G-C", "G-B", actor="user", source_ref="doc://fixture/b.md")

    body = tasks._services.envelopes.get_context_v2("G-C")
    edge = next(edge for edge in body["release_context"]["dependencies"]
                if edge.get("dependent_task_id") == "G-C")
    assert edge["task_id"] == "G-B"
    assert edge["prerequisite"]["status"] == "blocked"
    assert body["release_context"]["readiness"] == "blocked"


def test_create_task_atomic_release_dependency_missing_prerequisite(tmp_path):
    # The consumer/API regression also checks rollback of a valid native edge;
    # this service-level check verifies the 404 contract before any insert/event.
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user")
    tasks.create_task("G", "G-P", "Prerequisite", actor="user")
    before = tasks.events()
    with pytest.raises(ServiceError) as caught:
        tasks.create_task("G", "G-D", "Dependent", actor="user", depends_on=["G-P"],
                          release_dependencies=[{"depends_on_id": "MISSING", "source_ref": "doc://missing"}])
    assert caught.value.code == "not_found"
    assert caught.value.status == 404
    assert tasks.events() == before
    with pytest.raises(ServiceError):
        tasks.get_task("G-D")


def test_context_v2_pins_event_head_and_release_context_to_one_read_snapshot(tmp_path, monkeypatch):
    tasks = make_tasks(tmp_path)
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "G-P", "Prerequisite", actor="user")
    tasks.create_task("G", "G-D", "Dependent", actor="user")
    tasks.add_release_dependency("G-D", "G-P", actor="user", source_ref="doc://fixture/release.md")
    envelopes = tasks._services.envelopes
    original = envelopes.store.event_head
    changed = False

    def race_after_head(*, conn=None):
        nonlocal changed
        value = original(conn=conn)
        if not changed:
            changed = True
            # Commit through another connection after the reader has established its WAL snapshot.
            tasks.set_release_mode("G", "probada", actor="agent:concurrent")
        return value

    monkeypatch.setattr(envelopes.store, "event_head", race_after_head)
    body = envelopes.get_context_v2("G-D")
    assert changed
    assert body["task_envelope"]["events_after"] < envelopes.store.event_head()
    assert body["release_context"]["legacy_release_mode"] is None
    assert body["release_context"]["group_policy"]["legacy_release_mode"] is None
