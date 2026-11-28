# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Task services (ORQ-T05): the contract of ORQ-T02 over the task store and the graph."""
from __future__ import annotations

from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError


def settings_for(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                    gemini_api_key="", openai_api_key="", openai_enabled=False)


@pytest.fixture
def services(tmp_path: Path) -> ApplicationServices:
    return ApplicationServices.create(settings_for(tmp_path), dry_run=True)


@pytest.fixture
def tasks(services: ApplicationServices):
    return services.tasks


def error(call) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value


def diamond(tasks, *, authorized: bool = True) -> None:
    """A -> B, A -> C, B + C -> D, D -> E."""
    tasks.create_group("G", "Group", actor="user", authorized=authorized)
    tasks.create_task("G", "A", "A", actor="user", file_scope=["src/a.py"])
    tasks.create_task("G", "B", "B", actor="user", depends_on=["A"], file_scope=["src/b"])
    tasks.create_task("G", "C", "C", actor="user", depends_on=["A"])
    tasks.create_task("G", "D", "D", actor="user", depends_on=["B", {"task_id": "C", "requires": "accepted"}])
    tasks.create_task("G", "E", "E", actor="user", depends_on=["D"])


def run_to_accepted(tasks, task_id: str, actor: str = "agent:a") -> dict:
    tasks.transition(task_id, "claim", actor=actor)
    tasks.transition(task_id, "start", actor=actor)
    tasks.transition(task_id, "submit", actor=actor)
    return tasks.transition(task_id, "accept", actor="user")


def test_facade_exposes_tasks_without_touching_the_database_first(tmp_path):
    ApplicationServices(object()).tasks  # no system.db needed until the first call


def test_groups(tasks):
    group = tasks.create_group("ORQ", "Orchestration", actor="user")
    assert group["authorized"] is False and group["counts"] == {}
    assert error(lambda: tasks.create_group("ORQ", "Again", actor="user")).code == "invalid_state"
    assert error(lambda: tasks.create_group("9bad", "x", actor="user")).code == "invalid_request"
    assert error(lambda: tasks.create_group("X", "x", actor="user", default_requires="done")).code == "invalid_request"
    assert tasks.update_group("ORQ", actor="user", authorized=True)["authorized"] is True
    assert error(lambda: tasks.update_group("NOPE", actor="user", title="x")).code == "not_found"
    assert [g["id"] for g in tasks.list_groups()] == ["ORQ"]
    assert error(lambda: tasks.get_group("NOPE")).status == 404


def test_create_task_computes_blocked_and_ready(tasks):
    diamond(tasks)
    assert {t["id"]: t["status"] for t in tasks.list_tasks()} == {
        "A": "ready", "B": "blocked", "C": "blocked", "D": "blocked", "E": "blocked"}
    d = tasks.get_task("D")
    assert d["state"] == "open" and d["dependents"] == ["E"]
    assert [b["task_id"] for b in d["blockers"]] == ["B", "C"]
    assert d["depends_on"] == [{"task_id": "B", "requires": "accepted", "satisfied": False},
                               {"task_id": "C", "requires": "accepted", "satisfied": False}]
    assert tasks.get_group("G")["counts"] == {"ready": 1, "blocked": 4}


def test_create_task_rejects_bad_input(tasks):
    diamond(tasks)
    assert error(lambda: tasks.create_task("G", "A", "dup", actor="user")).code == "invalid_state"
    assert error(lambda: tasks.create_task("NOPE", "Z", "z", actor="user")).code == "not_found"
    assert error(lambda: tasks.create_task("G", "Z", "z", actor="user", depends_on=["GHOST"])).code == "not_found"
    assert error(lambda: tasks.create_task("G", "Z", "z", actor="user", depends_on=["Z"])).code == "invalid_request"
    assert error(lambda: tasks.create_task("G", "Z", "z", actor="user", depends_on=["A", "A"])).code == "invalid_request"
    for scope in (["../x"], ["/abs"], ["C:/x"], ["a\\b"], "src"):
        assert error(lambda: tasks.create_task("G", "Z", "z", actor="user", file_scope=scope)).code == "invalid_request"
    assert error(lambda: tasks.create_task("G", "Z", "", actor="user")).code == "invalid_request"
    assert error(lambda: tasks.get_task("Z")).code == "not_found"


def test_claim_requires_ready_and_an_authorized_group(tasks):
    diamond(tasks, authorized=False)
    assert error(lambda: tasks.transition("A", "claim", actor="agent:a")).details["group_id"] == "G"
    tasks.update_group("G", actor="user", authorized=True)
    blocked = error(lambda: tasks.transition("B", "claim", actor="agent:a"))
    assert blocked.code == "invalid_state" and blocked.details["status"] == "blocked"
    claimed = tasks.transition("A", "claim", actor="agent:a")
    assert (claimed["state"], claimed["assignee"], claimed["revision"]) == ("claimed", "agent:a", 2)


def test_full_life_cycle_releases_dependents(tasks, services):
    diamond(tasks)
    started = tasks.transition("A", "claim", actor="agent:a") and tasks.transition("A", "start", actor="agent:a")
    assert started["attempt"] == 1 and started["status"] == "running"
    submitted = tasks.transition("A", "submit", actor="agent:a")
    assert submitted["status"] == "submitted"
    assert tasks.get_task("B")["status"] == "blocked"  # submitted is not accepted
    accepted = tasks.transition("A", "accept", actor="user", cause="reviewed")
    assert accepted["status"] == "accepted"
    assert [t["id"] for t in tasks.ready_tasks()] == ["B", "C"]
    ready_events = [e for e in tasks.events() if e["type"] == "task.ready"]
    assert [(e["task_id"], e["cause"]) for e in ready_events] == [("B", "dependency A accepted"), ("C", "dependency A accepted")]
    run_to_accepted(tasks, "B")
    assert tasks.get_task("D")["status"] == "blocked"
    run_to_accepted(tasks, "C")
    assert tasks.get_task("D")["status"] == "ready"


def test_only_the_assignee_starts_pauses_resumes_and_submits(tasks):
    diamond(tasks)
    tasks.transition("A", "claim", actor="agent:a")
    wrong = error(lambda: tasks.transition("A", "start", actor="agent:b"))
    assert wrong.code == "invalid_state" and wrong.details["assignee"] == "agent:a"
    tasks.transition("A", "start", actor="agent:a")
    assert tasks.transition("A", "pause", actor="agent:a")["status"] == "paused"
    assert tasks.transition("A", "resume", actor="agent:a")["status"] == "running"


def test_invalid_pairs_and_unknown_actions(tasks):
    diamond(tasks)
    bad = error(lambda: tasks.transition("A", "submit", actor="agent:a"))
    assert bad.details["allowed"] == ["running"] and bad.details["action"] == "submit"
    assert error(lambda: tasks.transition("A", "finish", actor="agent:a")).code == "invalid_request"
    assert error(lambda: tasks.transition("NOPE", "claim", actor="agent:a")).code == "not_found"
    run_to_accepted(tasks, "A")
    assert error(lambda: tasks.transition("A", "cancel", actor="user")).code == "invalid_state"


def test_request_changes_returns_to_the_same_assignee(tasks):
    diamond(tasks)
    tasks.transition("A", "claim", actor="agent:a")
    tasks.transition("A", "start", actor="agent:a")
    tasks.transition("A", "submit", actor="agent:a")
    changes = tasks.transition("A", "request_changes", actor="user", cause="missing tests")
    assert (changes["status"], changes["assignee"]) == ("needs_changes", "agent:a")
    again = tasks.transition("A", "start", actor="agent:a")
    assert again["attempt"] == 2


def test_unclaim_and_cancel(tasks):
    diamond(tasks)
    tasks.transition("A", "claim", actor="agent:a")
    released = tasks.transition("A", "unclaim", actor="agent:a")
    assert (released["status"], released["assignee"]) == ("ready", "")
    assert tasks.transition("B", "cancel", actor="user")["status"] == "cancelled"
    d = tasks.get_task("D")
    assert {"task_id": "B", "state": "cancelled", "requires": "accepted"} in d["blockers"]


def test_claim_is_refused_when_files_overlap_another_assignee(tasks):
    tasks.create_group("G", "Group", actor="user", authorized=True)
    tasks.create_task("G", "T1", "one", actor="user", file_scope=["src/pkg"])
    tasks.create_task("G", "T2", "two", actor="user", file_scope=["src/pkg/mod.py"])
    tasks.create_task("G", "T3", "three", actor="user", file_scope=["docs/x.md"])
    tasks.transition("T1", "claim", actor="agent:a")
    tasks.transition("T1", "start", actor="agent:a")
    tasks.transition("T1", "pause", actor="agent:a")  # a paused task still holds its files
    locked = error(lambda: tasks.transition("T2", "claim", actor="agent:b"))
    assert locked.details["conflicts"] == [{"task_id": "T1", "assignee": "agent:a", "status": "paused",
                                            "paths": ["src/pkg"]}]
    assert tasks.transition("T3", "claim", actor="agent:b")["assignee"] == "agent:b"
    # The same assignee may hold overlapping tasks.
    assert tasks.transition("T2", "claim", actor="agent:a")["assignee"] == "agent:a"


def test_expected_revision_detects_a_stale_client(tasks):
    diamond(tasks)
    task = tasks.get_task("A")
    tasks.transition("A", "claim", actor="agent:a", expected_revision=task["revision"])
    stale = error(lambda: tasks.transition("A", "unclaim", actor="agent:a", expected_revision=task["revision"]))
    assert stale.code == "invalid_state" and stale.details == {"task_id": "A", "expected_revision": 1, "revision": 2}


def test_dependencies_can_be_added_and_removed(tasks):
    diamond(tasks)
    cycle = error(lambda: tasks.add_dependency("A", "E", actor="user"))
    assert cycle.code == "invalid_request" and cycle.details["cycle"][0] == cycle.details["cycle"][-1]
    assert error(lambda: tasks.add_dependency("B", "A", actor="user")).code == "invalid_state"
    assert error(lambda: tasks.add_dependency("B", "GHOST", actor="user")).code == "not_found"
    tasks.create_task("G", "F", "F", actor="user")
    blocked = tasks.add_dependency("F", "A", actor="user", requires="submitted")
    assert blocked["status"] == "blocked" and blocked["revision"] == 2
    ready = tasks.remove_dependency("F", "A", actor="user")
    assert ready["status"] == "ready"
    assert tasks.events()[-1]["type"] == "task.ready"
    assert error(lambda: tasks.remove_dependency("F", "A", actor="user")).code == "not_found"
    run_to_accepted(tasks, "A")
    assert error(lambda: tasks.add_dependency("A", "F", actor="user")).code == "invalid_state"


def test_group_default_requires_applies_to_new_dependencies(tasks):
    tasks.create_group("S", "Submitted is enough", actor="user", authorized=True, default_requires="submitted")
    tasks.create_task("S", "A", "A", actor="user")
    tasks.create_task("S", "B", "B", actor="user", depends_on=["A"])
    assert tasks.get_task("B")["depends_on"][0]["requires"] == "submitted"
    tasks.transition("A", "claim", actor="agent:a")
    tasks.transition("A", "start", actor="agent:a")
    tasks.transition("A", "submit", actor="agent:a")
    assert tasks.get_task("B")["status"] == "ready"


def test_list_filters_and_status_validation(tasks):
    diamond(tasks)
    tasks.transition("A", "claim", actor="agent:a")
    assert [t["id"] for t in tasks.list_tasks(assignee="agent:a")] == ["A"]
    assert [t["id"] for t in tasks.list_tasks(status="claimed")] == ["A"]
    assert tasks.list_tasks(group_id="OTHER") == []
    assert error(lambda: tasks.list_tasks(status="done")).code == "invalid_request"


def test_every_change_is_one_event_with_actor_and_cause(tasks):
    diamond(tasks)
    tasks.transition("A", "claim", actor="agent:a", cause="first")
    events = tasks.events()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "group.created"
    claimed = events[-1]
    assert (claimed["type"], claimed["actor"], claimed["cause"], claimed["payload"]) == (
        "task.claimed", "agent:a", "first", {"from": "ready", "to": "claimed"})
    assert [e["type"] for e in tasks.events(task_id="A")] == ["task.created", "task.claimed"]
    assert tasks.events(after=claimed["seq"]) == []
    assert len(tasks.events(limit=2)) == 2
    for bad in ({"after": -1}, {"after": True}, {"limit": 0}, {"limit": 1001}):
        assert error(lambda: tasks.events(**bad)).code == "invalid_request"


def test_a_refused_transition_changes_nothing(tasks):
    diamond(tasks)
    before = len(tasks.events())
    error(lambda: tasks.transition("B", "claim", actor="agent:a"))
    assert len(tasks.events()) == before
    assert tasks.get_task("B")["revision"] == 1


def test_tasks_survive_a_restart(tmp_path):
    settings = settings_for(tmp_path)
    first = ApplicationServices.create(settings, dry_run=True).tasks
    diamond(first)
    first.transition("A", "claim", actor="agent:a")
    second = ApplicationServices.create(settings, dry_run=True).tasks
    assert second.get_task("A")["status"] == "claimed"
    assert second.events()[-1]["type"] == "task.claimed"


def test_create_task_refuses_a_bad_dependency_with_the_same_codes_and_messages(tasks):
    diamond(tasks)
    missing = error(lambda: tasks.create_task("G", "N1", "N", actor="user", depends_on=["NOPE"]))
    assert (missing.code, str(missing)) == ("not_found", "Task not found: NOPE")
    itself = error(lambda: tasks.create_task("G", "N2", "N", actor="user", depends_on=["N2"]))
    assert (itself.code, str(itself)) == ("invalid_request", "A task cannot depend on itself")
    assert itself.details["cycle"] == ["N2", "N2"]
    both = error(lambda: tasks.create_task("G", "N3", "N", actor="user", depends_on=["A"],
                                           release_dependencies=[{"depends_on_id": "A", "source_ref": "doc://x"}]))
    assert (both.code, str(both)) == ("invalid_request", "Dependency pair has multiple kinds: N3 -> A")
    twice = error(lambda: tasks.create_task("G", "N4", "N", actor="user", depends_on=["A", "A"]))
    assert (twice.code, str(twice)) == ("invalid_request", "Repeated dependency: A")
    assert [t["id"] for t in tasks.list_tasks()] == list("ABCDE")


def test_create_task_still_closes_no_cycle_and_add_dependency_still_finds_one(tasks):
    diamond(tasks)
    created = tasks.create_task("G", "F", "F", actor="user", depends_on=["E", {"task_id": "A", "requires": "submitted"}])
    assert created["status"] == "blocked"
    assert [d["task_id"] for d in created["depends_on"]] == ["E", "A"]
    assert tasks.get_task("A")["dependents"] == ["B", "C", "F"]
    cycle = error(lambda: tasks.add_dependency("A", "F", actor="user"))
    assert cycle.code == "invalid_request" and cycle.details["cycle"][0] == cycle.details["cycle"][-1]
