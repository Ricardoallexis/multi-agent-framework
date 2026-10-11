from __future__ import annotations

from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError


@pytest.fixture
def tasks(tmp_path: Path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True).tasks


def test_reopen_preserves_attempt_and_requires_cause_revision(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user", file_scope=["src/a"])
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    submitted = tasks.get_task("T")
    with pytest.raises(ServiceError):
        tasks.transition("T", "reopen", actor="user", expected_revision=submitted["revision"])
    reopened = tasks.transition("T", "reopen", actor="user", cause="superseded",
                                expected_revision=submitted["revision"])
    assert reopened["state"] == "open" and reopened["attempt"] == 1
    assert reopened["assignee"] == "" and reopened["lock_policy"] == {"shared_scope": [], "workspace_scope": []}
    assert tasks.events(task_id="T")[-1]["type"] == "task.reopened"
    with pytest.raises(ServiceError):
        tasks.transition("T", "reopen", actor="user", cause="stale", expected_revision=submitted["revision"])


def test_scope_stale_noop_and_partition_validation(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user", file_scope=["src/a"])
    task = tasks.get_task("T")
    same = tasks.update_scope("T", file_scope=["src/a"], actor="user", expected_revision=task["revision"])
    assert same["revision"] == task["revision"]
    with pytest.raises(ServiceError):
        tasks.update_scope("T", file_scope=["src/b"], actor="user", expected_revision=task["revision"] - 1)
    tasks.transition("T", "claim", actor="agent:a")
    claimed = tasks.get_task("T")
    changed = tasks.update_scope("T", file_scope=["src/b"], actor="agent:a",
                                 expected_revision=claimed["revision"])
    assert changed["file_scope"] == ["src/b"]
    tasks.transition("T", "start", actor="agent:a")
    with pytest.raises(ServiceError):
        tasks.update_scope("T", file_scope=["src/c"], actor="agent:a",
                           expected_revision=tasks.get_task("T")["revision"])


def test_group_close_requires_delivery_and_group_revision(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    group = tasks.get_group("G")
    assert group["lifecycle_state"] == "open" and group["revision"] == 1
    tasks.create_task("G", "T", "Task", actor="user")
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    current = tasks.get_group("G")
    with pytest.raises(ServiceError):
        tasks.set_group_state("G", "closed", actor="user", cause="complete",
                              expected_revision=current["revision"])
    tasks.record_delivery_fact("T", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    closed = tasks.set_group_state("G", "closed", actor="user", cause="complete",
                                   expected_revision=tasks.get_group("G")["revision"])
    assert closed["lifecycle_state"] == "closed" and closed["revision"] == 2
    with pytest.raises(ServiceError):
        tasks.create_task("G", "T2", "Task 2", actor="user")
    reopened = tasks.create_task("G", "T2", "Task 2", actor="user", reopen_group=True,
                                expected_group_revision=closed["revision"], cause="follow-up")
    assert reopened["id"] == "T2" and tasks.get_group("G")["lifecycle_state"] == "open"
    assert tasks.get_group("G")["revision"] == 3


@pytest.mark.parametrize("terminal", ["accepted", "failed", "cancelled"])
def test_reopen_supported_terminal_states_and_keeps_attempt_and_results(tasks, terminal):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user")
    if terminal == "accepted":
        tasks.transition("T", "claim", actor="agent:a")
        tasks.transition("T", "start", actor="agent:a")
        tasks.transition("T", "submit", actor="agent:a")
        tasks.transition("T", "accept", actor="user")
    elif terminal == "failed":
        tasks.transition("T", "claim", actor="agent:a")
        tasks.transition("T", "fail", actor="agent:a")
    else:
        tasks.transition("T", "cancel", actor="user")
    before = tasks.get_task("T")
    reopened = tasks.transition("T", "reopen", actor="user", cause="retry",
                                expected_revision=before["revision"])
    assert reopened["state"] == "open" and reopened["attempt"] == before["attempt"]


def test_reopen_rejects_validating_until_gate_abort(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user")
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    tasks._services.gates.set_task_gate("T", ["review"], actor="user")
    with tasks.store.transaction() as conn:
        tasks._apply(conn, "T", "gate_start", actor="user", cause="test", gate=True, event="gate.started")
    before = tasks.get_task("T")
    with pytest.raises(ServiceError):
        tasks.transition("T", "reopen", actor="user", cause="redo",
                         expected_revision=before["revision"])
    assert tasks.get_task("T")["revision"] == before["revision"]
    tasks._services.gates.abort_gate("T", actor="user", cause="aborted")
    submitted = tasks.get_task("T")
    assert tasks.transition("T", "reopen", actor="user", cause="redo",
                             expected_revision=submitted["revision"])["state"] == "open"


@pytest.mark.parametrize("kind", ["native", "legacy"])
def test_start_resume_revalidate_invalidated_dependency_without_writes(tasks, kind):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "P", "Prerequisite", actor="user")
    tasks.transition("P", "claim", actor="agent:p")
    tasks.transition("P", "start", actor="agent:p")
    tasks.transition("P", "submit", actor="agent:p")
    if kind == "native":
        tasks.transition("P", "accept", actor="user")
    else:
        tasks.set_release_mode("G", "terminada", actor="user")
        tasks.record_delivery_fact("P", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                                   has_patch=False, patchset_sha256="", actor="user")
    tasks.create_task("G", "D", "Dependent", actor="user", depends_on=(
        ["P"] if kind == "native" else []), release_dependencies=(
        [{"depends_on_id": "P", "source_ref": "report://source"}] if kind == "legacy" else []),
        file_scope=["src/d"])
    tasks.transition("D", "claim", actor="agent:d")
    tasks.transition("D", "start", actor="agent:d")
    tasks.transition("D", "pause", actor="agent:d")
    if kind == "native":
        tasks.transition("P", "reopen", actor="user", cause="invalidate",
                         expected_revision=tasks.get_task("P")["revision"])
    else:
        tasks.reset_release_facts("P", actor="user", reason="invalidate")
    before = tasks.get_task("D")
    events = tasks.events(task_id="D")
    with pytest.raises(ServiceError):
        tasks.transition("D", "resume", actor="agent:d", expected_revision=before["revision"])
    after = tasks.get_task("D")
    assert (after["state"], after["attempt"], after["revision"]) == (
        before["state"], before["attempt"], before["revision"])
    assert tasks.events(task_id="D") == events


def test_paused_workspace_scope_conflicts_with_other_paused_shared_scope(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    for task_id in ("W", "S"):
        tasks.create_task("G", task_id, task_id, actor="user", file_scope=["src/x"])
    tasks.transition("W", "claim", actor="agent:w", actor_domain="workspace:w")
    tasks.transition("W", "start", actor="agent:w")
    tasks.transition("W", "pause", actor="agent:w")
    paused = tasks.get_task("W")
    tasks.update_scope("W", file_scope=["src/x"], actor="agent:w", expected_revision=paused["revision"],
                       lock_policy={"shared_scope": [], "workspace_scope": ["src/x"]},
                       workspace_domain="workspace:w")
    tasks.transition("S", "claim", actor="agent:s", actor_domain="workspace:s")
    tasks.transition("S", "start", actor="agent:s")
    tasks.transition("S", "pause", actor="agent:s")
    w = tasks.get_task("W")
    with pytest.raises(ServiceError):
        tasks.update_scope("W", file_scope=["src/x"], actor="agent:w", expected_revision=w["revision"],
                           lock_policy={"shared_scope": [], "workspace_scope": ["src/x"]},
                           workspace_domain="workspace:w")


def test_closed_group_invalid_task_creation_rolls_back_reopen(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user")
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    tasks.record_delivery_fact("T", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    closed = tasks.set_group_state("G", "closed", actor="user", cause="finish",
                                   expected_revision=tasks.get_group("G")["revision"])
    events = tasks.events(group_id="G")
    with pytest.raises(ServiceError):
        tasks.create_task("G", "bad", "Invalid", actor="user", reopen_group=True,
                          expected_group_revision=closed["revision"], cause="follow-up",
                          depends_on=["missing"])
    assert tasks.get_group("G")["lifecycle_state"] == "closed"
    assert tasks.get_group("G")["revision"] == closed["revision"]
    assert tasks.events(group_id="G") == events


@pytest.mark.parametrize("domain", [None, "workspace:one"])
def test_paused_workspace_unknown_or_same_domain_conflicts_with_claim(tasks, domain):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "W", "Workspace", actor="user", file_scope=["src/x"])
    tasks.create_task("G", "C", "Claim", actor="user", file_scope=["src/x"])
    tasks.transition("W", "claim", actor="agent:w", actor_domain="workspace:one")
    tasks.transition("W", "start", actor="agent:w")
    tasks.transition("W", "pause", actor="agent:w")
    paused = tasks.get_task("W")
    tasks.update_scope("W", file_scope=["src/x"], actor="agent:w", expected_revision=paused["revision"],
                       lock_policy={"shared_scope": [], "workspace_scope": ["src/x"]},
                       workspace_domain=domain or "")
    with pytest.raises(ServiceError):
        tasks.transition("C", "claim", actor="agent:c", actor_domain="workspace:one")


def test_group_mutators_advance_revision_only_on_change(tasks):
    group = tasks.create_group("G", "Group", actor="user")
    same = tasks.update_group("G", actor="user", title="Group")
    assert same["revision"] == group["revision"]
    tasks._services.gates.set_group_gate("G", ["review"], actor="user")
    assert tasks.get_group("G")["revision"] == 2
    tasks._services.gates.set_group_gate("G", ["review"], actor="user")
    assert tasks.get_group("G")["revision"] == 2


def test_scope_claimed_rejects_non_assignee_without_writes(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user", file_scope=["src/a"])
    tasks.transition("T", "claim", actor="agent:a")
    before = tasks.get_task("T")
    events = tasks.events(task_id="T")
    with pytest.raises(ServiceError):
        tasks.update_scope("T", file_scope=["src/b"], actor="agent:other",
                           expected_revision=before["revision"])
    after = tasks.get_task("T")
    assert (after["state"], after["revision"], after["file_scope"], after["lock_policy"]) == (
        before["state"], before["revision"], before["file_scope"], before["lock_policy"])
    assert tasks.events(task_id="T") == events


def test_needs_changes_scope_stays_global_and_owner_only(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user", file_scope=["src/a"])
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    task = tasks.transition("T", "request_changes", actor="user")
    assert task["state"] == "needs_changes"
    with pytest.raises(ServiceError):
        tasks.update_scope("T", file_scope=["src/b"], actor="user", expected_revision=task["revision"])
    updated = tasks.update_scope("T", file_scope=["src/b"], actor="agent:a",
                                 expected_revision=task["revision"])
    assert updated["lock_policy"] == {"shared_scope": ["src/b"], "workspace_scope": []}


def test_native_needs_changes_start_rechecks_global_lock(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    for task_id in ("T", "O"):
        tasks.create_task("G", task_id, task_id, actor="user", file_scope=["src/a"])
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    tasks.transition("T", "request_changes", actor="user")
    # Simulate another active lock on a previously unscoped imported row.
    with tasks.store.transaction() as conn:
        conn.execute("UPDATE tasks SET state='running',assignee='agent:o',lock_policy_json='{}' WHERE id='O'")
    before = tasks.get_task("T")
    events = tasks.events(task_id="T")
    with pytest.raises(ServiceError):
        tasks.transition("T", "start", actor="agent:a", expected_revision=before["revision"])
    after = tasks.get_task("T")
    assert (after["state"], after["attempt"], after["revision"]) == (
        before["state"], before["attempt"], before["revision"])
    assert tasks.events(task_id="T") == events


def test_create_context_refs_are_validated_persisted_once_and_returned(tasks):
    tasks.create_group("G", "Group", actor="user")
    created = tasks.create_task("G", "T", "Task", actor="user",
                                context_refs=["doc://docs/spec.md", "report://release", "doc://docs/spec.md"])
    assert created["revision"] == 1
    events = tasks.events(task_id="T")
    assert [event["type"] for event in events] == ["task.created"]
    envelope = tasks._services.envelopes.get_envelope("T")
    assert envelope["context_refs"] == ["doc://docs/spec.md", "report://release"]


def test_invalid_create_context_ref_does_not_reopen_group_or_write(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T", "Task", actor="user")
    tasks.transition("T", "claim", actor="agent:a")
    tasks.transition("T", "start", actor="agent:a")
    tasks.transition("T", "submit", actor="agent:a")
    tasks.record_delivery_fact("T", delivery_ref="report://delivery", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    closed = tasks.set_group_state("G", "closed", actor="user", cause="done",
                                   expected_revision=tasks.get_group("G")["revision"])
    events = tasks.events(group_id="G")
    for refs in (["bad://reference"], [f"doc://docs/{index}.md" for index in range(21)]):
        with pytest.raises(ServiceError):
            tasks.create_task("G", "NEW", "New task", actor="user", reopen_group=True,
                              expected_group_revision=closed["revision"], cause="follow-up",
                              context_refs=refs)
        assert tasks.get_group("G")["lifecycle_state"] == "closed"
        assert tasks.get_group("G")["revision"] == closed["revision"]
        assert [task["id"] for task in tasks.list_tasks(group_id="G")] == ["T"]
        assert tasks.events(group_id="G") == events
