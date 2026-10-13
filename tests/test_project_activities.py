# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Activities in the core (PRY-T09, amendment 1 of the PRY contract): lightweight tasks of a project, outside its stages.

Everything runs on synthetic data in temporary folders, in Mock mode (no model, no agent application).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ActivityServices, ApplicationServices, ProjectServices, ServiceError
from multiagent.services.activities import ACTIVITY_LABELS
from multiagent.tasks import labels
from session_fixtures import CLAUDE_ID, make_services as make_session_services, make_stores, write_claude

OWNER = "user:ana"
AGENT = "agent:writer/m1"
EXECUTOR = "executor:codex_cli"
PLAN = {
    "schema": "maf.project-template/1", "id": "synthetic-campaign", "title": "Synthetic campaign",
    "stages": [
        {"code": "INV", "title": "Research", "tasks": [
            {"id": "T01", "title": "Audience study", "assignee": AGENT},
            {"id": "T02", "title": "Competitors", "depends_on": ["T01"]}]},
        {"code": "CON", "title": "Content", "tasks": [
            {"id": "T01", "title": "Calendar", "depends_on": ["stage:INV"]}]},
    ],
}
SECRET_TITLE = "Quarterly layoffs memo"
SECRET_SUMMARY = "Confidential outcome of the memo"


def make_services(root: Path, **options) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True, **options)


@pytest.fixture
def services(tmp_path):
    found = make_services(tmp_path / "local")
    yield found
    found.runs.close()


def make_project(svc: ApplicationServices, key: str = "mkt") -> dict[str, Any]:
    plan = svc.projects.plan(PLAN, key=key)
    return svc.projects.create(PLAN, key=key, plan_sha256=plan["plan_sha256"], actor=OWNER)


def error_of(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def types(svc: ApplicationServices, **filters: Any) -> list[str]:
    return [event["type"] for event in svc.tasks.events(**filters)]


def deliver(svc: ApplicationServices, task_id: str) -> None:
    svc.tasks.transition(task_id, "claim", actor=AGENT)
    svc.tasks.transition(task_id, "start", actor=AGENT)
    svc.envelopes.submit_result(task_id, {"summary": f"Done {task_id}."}, actor=AGENT)
    svc.tasks.transition(task_id, "accept", actor=OWNER, cause="reviewed")


def stage_of(view: dict, group_id: str) -> dict:
    return next(stage for stage in view["stages"] if stage["group_id"] == group_id)


# ---------------------------------------------------------------------------------------------------------------
# The kind of tasks and groups, and the activities group
# ---------------------------------------------------------------------------------------------------------------

def test_tasks_and_groups_carry_their_kind_and_the_activities_group_appears_on_first_use(services):
    make_project(services)
    assert services.tasks.get_task("mkt-INV-T01")["kind"] == "task"
    assert services.tasks.get_group("mkt-INV")["kind"] == "stage"
    assert services.projects.get("mkt")["activities"] == {
        "group_id": None, "total": 0, "doing": 0, "done": 0, "counts": {},
        "progress": {"done": 0, "cancelled": 0, "total": 0, "percent": None}}
    assert services.activities.list("mkt") == []
    assert services.tasks.store.list_groups(project_id="mkt", kind="activities") == []

    seq = services.tasks.events()[-1]["seq"]
    first = services.activities.start("mkt", "Review the brief", actor=OWNER)
    group = services.tasks.get_group("mkt-ACT")
    assert (group["kind"], group["project_id"], group["position"], group["authorized"]) == ("activities", "mkt", None, True)
    assert (group["lifecycle_state"], group["created_by"]) == ("open", OWNER)
    assert (first["id"], first["group_id"], first["project_id"], first["kind"]) == ("mkt-ACT-T01", "mkt-ACT", "mkt", "activity")
    assert services.tasks.get_task("mkt-ACT-T01")["kind"] == "activity"
    assert services.activities.start("mkt", "Second chat", actor=OWNER)["id"] == "mkt-ACT-T02"
    assert types(services, after=seq).count("group.created") == 1   # created once, on first use
    created = [e for e in services.tasks.events(after=seq) if e["type"] == "group.created"][0]
    assert (created["group_id"], created["cause"], created["payload"]["kind"]) == ("mkt-ACT", "project:mkt", "activities")

    # it sits outside the stages: positions, the list of stages and the project filter of the event log
    view = services.projects.get("mkt")
    assert [s["group_id"] for s in view["stages"]] == ["mkt-INV", "mkt-CON"]
    assert [s["position"] for s in view["stages"]] == [1, 2] and {s["kind"] for s in view["stages"]} == {"stage"}
    assert view["activities"]["group_id"] == "mkt-ACT" and view["activities"]["total"] == 2
    assert {g["id"] for g in services.tasks.store.list_groups(project_id="mkt")} == {"mkt-INV", "mkt-CON", "mkt-ACT"}
    assert [g["id"] for g in services.tasks.store.list_groups(project_id="mkt", kind="stage")] == ["mkt-INV", "mkt-CON"]
    assert "group.created" in types(services, after=seq, project_id="mkt")


def test_each_project_has_its_own_activities_group_and_numbering(services):
    make_project(services)
    make_project(services, "other")
    assert services.activities.start("mkt", "One", actor=OWNER)["id"] == "mkt-ACT-T01"
    assert services.activities.start("other", "One", actor=OWNER)["id"] == "other-ACT-T01"
    assert services.activities.start("mkt", "Two", actor=OWNER)["id"] == "mkt-ACT-T02"
    assert [a["id"] for a in services.activities.list("mkt")] == ["mkt-ACT-T01", "mkt-ACT-T02"]
    assert [a["id"] for a in services.activities.list("other")] == ["other-ACT-T01"]


def test_the_stage_code_act_is_reserved_and_a_foreign_group_with_that_id_blocks_the_first_use(services):
    reserved = {**PLAN, "stages": [{"code": "ACT", "title": "Not allowed", "tasks": []}]}
    failure = error_of(services.projects.plan, reserved, key="mkt")
    assert (failure.code, failure.details["field"]) == ("invalid_request", "stages[0].code")
    assert "reserved" in failure.message
    make_project(services)
    services.tasks.create_group("mkt-ACT", "Made by hand", actor="user")   # an ordinary stage group with that id
    clash = error_of(services.activities.start, "mkt", "Review", actor=OWNER)
    assert clash.status == 409 and clash.details["group_id"] == "mkt-ACT"
    assert services.tasks.get_group("mkt-ACT")["kind"] == "stage"


# ---------------------------------------------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------------------------------------------

def test_start_takes_the_activity_as_the_actor_and_writes_the_usual_events(services):
    make_project(services)
    seq = services.tasks.events()[-1]["seq"]
    started = services.activities.start("mkt", "Review the brief", actor=AGENT, objective="Read it and note doubts.")
    assert started["created"] is True
    assert (started["state"], started["status"], started["assignee"], started["attempt"]) == ("running", "running", AGENT, 1)
    assert started["label"]["key"] == "doing" and started["closed_at"] is None and started["summary"] is None
    assert (started["title"], started["objective"], started["created_by"]) == ("Review the brief", "Read it and note doubts.", AGENT)
    assert started["assignee_info"]["kind"] == "agent" and started["sessions"] == [] and started["executor"] is None
    events = services.tasks.events(after=seq)
    assert [e["type"] for e in events] == ["group.created", "task.created", "task.claimed", "task.started"]
    created = events[1]
    assert (created["task_id"], created["actor"], created["cause"], created["payload"]) == (
        "mkt-ACT-T01", AGENT, "project:mkt", {"status": "ready", "kind": "activity"})
    assert all(e["actor"] == AGENT and e["group_id"] == "mkt-ACT" for e in events)
    task = services.tasks.get_task("mkt-ACT-T01")
    assert (task["file_scope"], task["depends_on"], task["dependents"], task["blockers"]) == ([], [], [], [])


def test_start_with_an_executor_only_prepares_the_activity_for_it(services):
    make_project(services)
    prepared = services.activities.start("mkt", "Summarize the call", actor=OWNER, executor="codex_cli")
    assert (prepared["state"], prepared["status"], prepared["assignee"], prepared["attempt"]) == ("open", "ready", "", 0)
    assert (prepared["preferred"], prepared["executor"], prepared["label"]["key"]) == (EXECUTOR, "codex_cli", "ready")
    assert types(services, project_id="mkt")[-2:] == ["group.created", "task.created"]
    # it is not taken from the queue of planned work, but a run can claim and start it by id as for any task
    assert services.tasks.ready_tasks() == []
    assert [t["id"] for t in services.tasks.list_tasks(status="ready")][-1] == "mkt-ACT-T01"
    for action in ("claim", "start"):
        services.tasks.transition("mkt-ACT-T01", action, actor=EXECUTOR)
    done = services.envelopes.submit_result("mkt-ACT-T01", {"summary": "Call summarized."}, actor=EXECUTOR)
    assert done["task"]["state"] == "accepted" and done["result"]["summary"] == "Call summarized."
    assert services.activities.list("mkt")[0]["summary"] == "Call summarized."


@pytest.mark.parametrize("kwargs, field", [
    ({"title": ""}, "title"), ({"title": "x" * 201}, "title"), ({"title": 5}, "title"),
    ({"actor": "bob"}, "actor"), ({"actor": ""}, "actor"), ({"executor": "not an id"}, "executor"),
    ({"executor": ""}, "executor"), ({"idempotency_key": " "}, "idempotency_key"),
    ({"idempotency_key": "k" * 201}, "idempotency_key"), ({"objective": "o" * 4001}, "objective"),
])
def test_start_rejects_malformed_input_and_writes_nothing(services, kwargs, field):
    make_project(services)
    seq = services.tasks.events()[-1]["seq"]
    call = {"title": "Review", "actor": OWNER, **kwargs}
    failure = error_of(services.activities.start, "mkt", call.pop("title"), **call)
    assert (failure.code, failure.status, failure.details["field"]) == ("invalid_request", 422, field)
    assert services.tasks.events(after=seq) == []   # nothing was created
    assert services.tasks.store.list_groups(project_id="mkt", kind="activities") == []


def test_start_needs_an_existing_active_project(services):
    assert error_of(services.activities.start, "ghost", "Review", actor=OWNER).status == 404
    make_project(services)
    services.projects.archive("mkt", actor=OWNER, expected_revision=1)
    archived = error_of(services.activities.start, "mkt", "Review", actor=OWNER)
    assert (archived.status, archived.code) == (409, "invalid_state")
    assert services.activities.list("mkt") == []   # listing an archived project is allowed


def test_a_repeated_idempotency_key_gives_back_the_same_activity_and_writes_nothing(services):
    make_project(services)
    make_project(services, "other")
    key = f"claude:{CLAUDE_ID}"
    first = services.activities.start("mkt", "First title", actor=OWNER, idempotency_key=key)
    seq = services.tasks.events()[-1]["seq"]
    again = services.activities.start("mkt", "Another title", actor=AGENT, idempotency_key=key)
    assert again["created"] is False and again["id"] == first["id"] and again["title"] == "First title"
    assert services.tasks.events(after=seq) == []
    other_key = services.activities.start("mkt", "Second", actor=OWNER, idempotency_key="other-key")
    assert other_key["created"] is True and other_key["id"] == "mkt-ACT-T02"
    no_key = services.activities.start("mkt", "No key", actor=OWNER)
    assert no_key["created"] is True and no_key["id"] == "mkt-ACT-T03"
    elsewhere = services.activities.start("other", "Same key, other project", actor=OWNER, idempotency_key=key)
    assert elsewhere["created"] is True and elsewhere["id"] == "other-ACT-T01"   # the key is scoped to the project
    # the key is kept inside the task, never shown by its views
    assert "idempotency_key" not in json.dumps(services.tasks.get_task(first["id"]))
    assert key not in json.dumps(services.activities.list("mkt"))


# ---------------------------------------------------------------------------------------------------------------
# close and submit
# ---------------------------------------------------------------------------------------------------------------

def test_close_stores_the_short_result_and_accepts_the_activity_in_one_transaction(services):
    make_project(services)
    services.activities.start("mkt", "Review the brief", actor=OWNER)
    seq = services.tasks.events()[-1]["seq"]
    closed = services.activities.close("mkt-ACT-T01", "Brief reviewed, two doubts noted.", actor=OWNER)
    assert (closed["state"], closed["label"]["key"], closed["summary"]) == ("accepted", "done", "Brief reviewed, two doubts noted.")
    assert closed["closed_at"] and closed["assignee"] == OWNER and "created" not in closed
    events = services.tasks.events(after=seq)
    assert [e["type"] for e in events] == ["task.submitted", "result.submitted", "task.accepted"]
    assert events[2]["cause"] == "activity_closed" and events[2]["actor"] == OWNER
    assert (events[2]["payload"]["from"], events[2]["payload"]["to"]) == ("submitted", "accepted")
    result = services.envelopes.get_result("mkt-ACT-T01")
    assert (result["summary"], result["agent"], result["attempt"], result["patch"]) == (
        "Brief reviewed, two doubts noted.", OWNER, 1, None)
    assert result["ref"] == "result://mkt-ACT-T01/1"
    # a second close finds nothing running; nobody but the assignee closes it
    assert error_of(services.activities.close, "mkt-ACT-T01", "Again", actor=OWNER).status == 409
    services.activities.start("mkt", "Second", actor=OWNER)
    stranger = error_of(services.activities.close, "mkt-ACT-T02", "Hijacked", actor=AGENT)
    assert (stranger.status, stranger.code) == (409, "invalid_state") and stranger.details["assignee"] == OWNER
    assert services.tasks.get_task("mkt-ACT-T02")["state"] == "running"


def test_close_checks_what_it_is_closing(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    services.activities.start("mkt", "Prepared", actor=OWNER, executor="codex_cli")
    assert error_of(services.activities.close, "mkt-ACT-T99", "Done", actor=OWNER).status == 404
    notask = error_of(services.activities.close, "mkt-INV-T01", "Done", actor=OWNER)
    assert (notask.code, notask.details["field"]) == ("invalid_request", "activity_id")
    assert error_of(services.activities.close, "bad id", "Done", actor=OWNER).code == "invalid_request"
    assert error_of(services.activities.close, "mkt-ACT-T01", "", actor=OWNER).details["field"] == "summary"
    assert error_of(services.activities.close, "mkt-ACT-T01", "x" * 1501, actor=OWNER).details["field"] == "summary"
    assert error_of(services.activities.close, "mkt-ACT-T01", "Done", actor="bob").details["field"] == "actor"
    never_started = error_of(services.activities.close, "mkt-ACT-T02", "Done", actor=OWNER)
    assert never_started.status == 409
    assert services.tasks.get_task("mkt-ACT-T01")["state"] == "running" and services.tasks.get_task("mkt-ACT-T02")["state"] == "open"
    assert services.tasks.get_task("mkt-INV-T01")["state"] == "open"


def test_submit_accepts_an_activity_but_still_reviews_ordinary_tasks(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    seq = services.tasks.events()[-1]["seq"]
    view = services.tasks.transition("mkt-ACT-T01", "submit", actor=OWNER)
    assert view["state"] == "accepted" and view["label"]["key"] == "done"
    assert types(services, after=seq) == ["task.submitted", "task.accepted"]
    # an ordinary task keeps waiting for its review
    services.tasks.update_group("mkt-INV", actor=OWNER, authorized=True)
    for action in ("claim", "start", "submit"):
        done = services.tasks.transition("mkt-INV-T01", action, actor=AGENT)
    assert done["state"] == "submitted" and done["label"]["key"] == "review"


def test_a_reopened_activity_can_be_run_and_closed_again(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    services.activities.close("mkt-ACT-T01", "First pass.", actor=OWNER)
    revision = services.tasks.get_task("mkt-ACT-T01")["revision"]
    reopened = services.tasks.transition("mkt-ACT-T01", "reopen", actor=OWNER, cause="more to say", expected_revision=revision)
    assert reopened["state"] == "open" and reopened["status"] == "ready"
    services.tasks.transition("mkt-ACT-T01", "claim", actor=AGENT)
    assert services.tasks.transition("mkt-ACT-T01", "start", actor=AGENT)["attempt"] == 2
    again = services.activities.close("mkt-ACT-T01", "Second pass.", actor=AGENT)
    assert (again["state"], again["summary"], again["attempt"]) == ("accepted", "Second pass.", 2)
    assert [r["attempt"] for r in services.envelopes.list_results("mkt-ACT-T01")] == [2, 1]


# ---------------------------------------------------------------------------------------------------------------
# Rules of the core
# ---------------------------------------------------------------------------------------------------------------

def test_nothing_depends_on_an_activity_or_on_the_other_end(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    services.activities.start("mkt", "Other", actor=OWNER)
    ref = "doc://notes.md"
    attempts = [
        (services.tasks.create_task, ("mkt-INV", "mkt-INV-T09", "Late"), {"actor": OWNER, "depends_on": ["mkt-ACT-T01"]}),
        (services.tasks.create_task, ("mkt-INV", "mkt-INV-T09", "Late"),
         {"actor": OWNER, "depends_on": [{"task_id": "mkt-ACT-T01", "requires": "submitted"}]}),
        (services.tasks.create_task, ("mkt-INV", "mkt-INV-T09", "Late"),
         {"actor": OWNER, "release_dependencies": [{"depends_on_id": "mkt-ACT-T01", "source_ref": ref}]}),
        (services.tasks.add_dependency, ("mkt-INV-T01", "mkt-ACT-T01"), {"actor": OWNER}),
        (services.tasks.add_dependency, ("mkt-ACT-T01", "mkt-INV-T01"), {"actor": OWNER}),
        (services.tasks.add_dependency, ("mkt-ACT-T01", "mkt-ACT-T02"), {"actor": OWNER}),
        (services.tasks.add_release_dependency, ("mkt-INV-T01", "mkt-ACT-T01"), {"actor": OWNER, "source_ref": ref}),
        (services.tasks.add_release_dependency, ("mkt-ACT-T01", "mkt-INV-T01"), {"actor": OWNER, "source_ref": ref}),
    ]
    for call, args, kwargs in attempts:
        failure = error_of(call, *args, **kwargs)
        assert (failure.code, failure.status) == ("invalid_request", 422), (call.__name__, args)
        assert failure.details["kind"] == "activity"
    assert services.tasks.get_task("mkt-INV-T01")["depends_on"] == []
    assert services.tasks.store.dependencies(depends_on_id="mkt-ACT-T01") == []
    # a plan cannot name an activity either: they are not part of any plan
    plan = {**PLAN, "stages": [{"code": "NEW", "title": "New", "tasks": [
        {"id": "T01", "title": "Late", "depends_on": ["ACT-T01"]}]}]}
    assert error_of(services.projects.plan, plan, key="fresh").code == "invalid_request"
    assert "mkt-INV-T09" not in {t["id"] for t in services.tasks.list_tasks()}


def test_an_activity_is_read_only_and_ordinary_tasks_cannot_be_made_in_its_group(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    scope = error_of(services.tasks.update_scope, "mkt-ACT-T01", file_scope=["src/app.py"], actor=OWNER,
                     expected_revision=services.tasks.get_task("mkt-ACT-T01")["revision"])
    assert (scope.code, scope.details["field"]) == ("invalid_request", "file_scope")
    assert services.tasks.get_task("mkt-ACT-T01")["file_scope"] == []
    made = error_of(services.tasks.create_task, "mkt-ACT", "mkt-ACT-T50", "By hand", actor=OWNER)
    assert (made.code, made.details["group_id"]) == ("invalid_request", "mkt-ACT")
    assert "mkt-ACT-T50" not in {t["id"] for t in services.tasks.list_tasks()}


def test_an_activity_has_no_gate(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    assert error_of(services.gates.set_task_gate, "mkt-ACT-T01", ["review"], actor=OWNER).code == "invalid_request"
    assert error_of(services.gates.set_group_gate, "mkt-ACT", ["review"], actor=OWNER).code == "invalid_request"
    assert services.gates.set_task_gate("mkt-ACT-T01", [], actor=OWNER)["checks"] == []   # clearing one is harmless
    assert services.gates.set_group_gate("mkt-ACT", [], actor=OWNER)["default_gate"] == []
    assert services.gates.get_gate("mkt-ACT-T01")["checks"] == []
    # stage tasks and stage groups still take gates
    assert services.gates.set_task_gate("mkt-INV-T01", ["review"], actor=OWNER)["checks"] == ["review"]
    assert services.gates.set_group_gate("mkt-INV", ["review"], actor=OWNER)["default_gate"] == ["review"]


def test_the_activities_group_is_labelled_by_its_unfinished_work_and_never_waits_for_a_verification(services):
    make_project(services)
    services.activities.start("mkt", "Running", actor=OWNER)
    assert services.tasks.get_group("mkt-ACT")["label"]["key"] == "doing"
    services.activities.close("mkt-ACT-T01", "Done.", actor=OWNER)
    group = services.tasks.get_group("mkt-ACT")
    assert group["counts"] == {"accepted": 1} and group["label"]["key"] == "todo"   # not «Espera tu verificación»
    services.activities.start("mkt", "Next", actor=OWNER, executor="codex_cli")
    assert services.tasks.get_group("mkt-ACT")["label"]["key"] == "doing"
    assert services.tasks.get_group("mkt-INV")["kind"] == "stage" and services.tasks.get_group("mkt-INV")["label"]["key"] == "todo"


def test_the_activities_group_never_closes(services):
    make_project(services)
    services.activities.start("mkt", "Review", actor=OWNER)
    services.activities.close("mkt-ACT-T01", "Done.", actor=OWNER)
    group = services.tasks.get_group("mkt-ACT")
    refused = error_of(services.tasks.set_group_state, "mkt-ACT", "closed", actor=OWNER, cause="all done",
                       expected_revision=group["revision"])
    assert (refused.status, refused.details["kind"]) == (409, "activities")
    assert services.tasks.get_group("mkt-ACT")["lifecycle_state"] == "open"
    assert services.activities.start("mkt", "Next", actor=OWNER)["id"] == "mkt-ACT-T02"   # still takes work


# ---------------------------------------------------------------------------------------------------------------
# Outside the stages: progress, attention, closing, archiving
# ---------------------------------------------------------------------------------------------------------------

def test_activities_do_not_count_in_the_stages_the_progress_or_what_you_have_to_do(services):
    make_project(services)
    before = services.projects.get("mkt")
    services.activities.start("mkt", "Review", actor=OWNER)            # assigned to the user and in progress
    services.activities.start("mkt", "Prepared", actor=OWNER, executor="codex_cli")
    services.activities.close("mkt-ACT-T01", "Done.", actor=OWNER)
    services.activities.start("mkt", "Running", actor=OWNER)
    view = services.projects.get("mkt")
    for key in ("progress", "counts", "stages", "milestones"):
        assert view[key] == before[key], key
    assert view["attention"] == before["attention"]   # no review, no own task, no blockage from an activity
    assert view["activities"] == {
        "group_id": "mkt-ACT", "total": 3, "doing": 1, "done": 1, "counts": {"done": 1, "doing": 1, "ready": 1},
        "progress": {"done": 1, "cancelled": 0, "total": 3, "percent": 33}}
    # the ones in progress are shown among «working now», with their kind
    assert [(item["id"], item["kind"]) for item in view["working_now"]] == [("mkt-ACT-T03", "activity")]
    listed = next(p for p in services.projects.list() if p["id"] == "mkt")
    assert listed["stages"] == 2 and listed["activities"] == {"total": 3, "doing": 1, "done": 1}
    assert listed["needs_you"] == {"reviews": 0, "verifications": 0, "mine": 0, "blocked": 0}
    assert listed["progress"] == before["progress"] and listed["counts"] == before["counts"]
    # an ordinary task in progress keeps coming first
    services.tasks.update_group("mkt-INV", actor=OWNER, authorized=True)
    services.tasks.transition("mkt-INV-T01", "claim", actor=AGENT)
    services.tasks.transition("mkt-INV-T01", "start", actor=AGENT)
    assert [(i["id"], i["kind"]) for i in services.projects.get("mkt")["working_now"]] == [
        ("mkt-INV-T01", "task"), ("mkt-ACT-T03", "activity")]


def test_a_stage_closes_while_activities_run_and_the_activities_group_is_not_a_stage(services):
    make_project(services)
    services.activities.start("mkt", "Running", actor=OWNER)
    services.tasks.update_group("mkt-INV", actor=OWNER, authorized=True)
    services.tasks.update_group("mkt-CON", actor=OWNER, authorized=True)
    deliver(services, "mkt-INV-T01")
    deliver(services, "mkt-INV-T02")
    view = services.projects.get("mkt")
    inv = stage_of(view, "mkt-INV")
    assert inv["label"]["key"] == "verify"
    closed = services.projects.close_stage("mkt", "mkt-INV", actor=OWNER, expected_revision=inv["revision"])
    assert closed["closed_stage"]["evidence_recorded"] == ["mkt-INV-T01", "mkt-INV-T02"]
    # the activities group cannot be closed as a stage, attached, or put in the order
    act_revision = services.tasks.get_group("mkt-ACT")["revision"]
    assert error_of(services.projects.close_stage, "mkt", "mkt-ACT", actor=OWNER, expected_revision=act_revision).status == 404
    revision = services.projects.get("mkt")["revision"]
    attach = error_of(services.projects.attach_stage, "mkt", "mkt-ACT", actor=OWNER, expected_revision=revision)
    assert (attach.code, attach.details["field"]) == ("invalid_request", "group_id")
    make_project(services, "other")
    services.activities.start("other", "Elsewhere", actor=OWNER)
    foreign = error_of(services.projects.attach_stage, "mkt", "other-ACT", actor=OWNER, expected_revision=revision)
    assert foreign.code == "invalid_request"
    assert error_of(services.projects.reorder, "mkt", ["mkt-CON", "mkt-INV", "mkt-ACT"], actor=OWNER,
                    expected_revision=revision).code == "invalid_request"
    reordered = services.projects.reorder("mkt", ["mkt-CON", "mkt-INV"], actor=OWNER, expected_revision=revision)
    assert [(s["group_id"], s["position"]) for s in reordered["stages"]] == [("mkt-CON", 1), ("mkt-INV", 2)]
    assert services.tasks.get_group("mkt-ACT")["position"] is None
    # a new stage attached afterwards takes the next position, not one counted from the activities group
    services.tasks.create_group("ORQ", "Imported", actor="user")
    attached = services.projects.attach_stage("mkt", "ORQ", actor=OWNER, expected_revision=reordered["revision"])
    assert stage_of(attached, "ORQ")["position"] == 3


def test_a_running_activity_stops_the_project_from_being_archived_until_it_is_closed(services):
    make_project(services)
    services.activities.start("mkt", "Running", actor=OWNER)
    busy = error_of(services.projects.archive, "mkt", actor=OWNER, expected_revision=1)
    assert (busy.status, busy.details["reason"], busy.details["task_ids"]) == (409, "work_in_progress", ["mkt-ACT-T01"])
    services.activities.close("mkt-ACT-T01", "Done.", actor=OWNER)
    archived = services.projects.archive("mkt", actor=OWNER, expected_revision=1)
    assert archived["archived"] is True and archived["activities"]["done"] == 1   # kept with the project
    assert [a["id"] for a in services.activities.list("mkt")] == ["mkt-ACT-T01"]
    services.projects.unarchive("mkt", actor=OWNER, expected_revision=2)
    assert services.activities.start("mkt", "Again", actor=OWNER)["id"] == "mkt-ACT-T02"


# ---------------------------------------------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------------------------------------------

def test_list_filters_by_label_and_by_date_and_keeps_creation_order(services):
    make_project(services)
    services.activities.start("mkt", "One", actor=OWNER)
    services.activities.start("mkt", "Two", actor=OWNER)
    services.activities.start("mkt", "Three", actor=OWNER, executor="codex_cli")
    services.activities.close("mkt-ACT-T01", "Done.", actor=OWNER)
    services.tasks.transition("mkt-ACT-T02", "fail", actor=OWNER, cause="gave up")
    everything = services.activities.list("mkt")
    assert [(a["id"], a["label"]["key"]) for a in everything] == [
        ("mkt-ACT-T01", "done"), ("mkt-ACT-T02", "failed"), ("mkt-ACT-T03", "ready")]
    assert {a["kind"] for a in everything} == {"activity"}
    assert [a["id"] for a in services.activities.list("mkt", status="done")] == ["mkt-ACT-T01"]
    assert [a["id"] for a in services.activities.list("mkt", status="failed")] == ["mkt-ACT-T02"]
    assert services.activities.list("mkt", status="doing") == []
    assert services.activities.list("mkt", status="cancelled") == []
    tomorrow = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    assert services.activities.list("mkt", since=tomorrow) == []
    assert len(services.activities.list("mkt", since="2020-01-01")) == 3
    assert len(services.activities.list("mkt", since="2020-01-01T00:00:00+02:00")) == 3
    assert len(services.activities.list("mkt", since="2020-01-01T00:00:00")) == 3
    cut = everything[1]["updated_at"]
    assert [a["id"] for a in services.activities.list("mkt", since=cut)][:1] == ["mkt-ACT-T02"]
    assert [a["id"] for a in services.activities.list("mkt", status="done", since=cut)] == []
    for wrong in ({"status": "review"}, {"status": "blocked"}, {"status": "Done"}, {"status": ""}):
        assert error_of(services.activities.list, "mkt", **wrong).details["field"] == "status"
    for wrong in ({"since": "yesterday"}, {"since": ""}, {"since": 5}):
        assert error_of(services.activities.list, "mkt", **wrong).details["field"] == "since"
    assert error_of(services.activities.list, "ghost").status == 404
    assert set(ACTIVITY_LABELS) <= set(labels.TASK_KEYS)
    assert {a["label"]["key"] for a in everything} <= set(ACTIVITY_LABELS)


def test_the_chat_of_another_app_links_to_an_activity_and_shows_in_its_sessions(tmp_path):
    stores = make_stores(tmp_path)
    svc = make_session_services(stores)
    try:
        write_claude(stores)
        make_project(svc)
        started = svc.activities.start("mkt", "Chat about the brief", actor=OWNER)
        session = f"claude:{CLAUDE_ID}"
        links = svc.projects.link_session(started["id"], session, actor=OWNER)
        assert [s["session_id"] for s in links["sessions"]] == [session]
        assert [s["session_id"] for s in svc.activities.list("mkt")[0]["sessions"]] == [session]
        assert svc.sessions.get_session(session)["task_ids"] == [started["id"]]
    finally:
        svc.runs.close()


def test_what_uso_needs_to_count_activities_apart_is_in_the_views(services):
    make_project(services)
    started = services.activities.start("mkt", "Review", actor=OWNER)
    # every view of a task says its kind, and the activities of a project come with their project and group
    assert services.tasks.get_task(started["id"])["kind"] == "activity"
    assert {t["id"]: t["kind"] for t in services.tasks.list_tasks()}[started["id"]] == "activity"
    assert {t["id"] for t in services.tasks.list_tasks() if t["kind"] == "activity"} == {started["id"]}
    assert (started["project_id"], started["group_id"]) == ("mkt", "mkt-ACT")
    assert services.projects.get("mkt")["activities"]["group_id"] == "mkt-ACT"
    # the group of a project can be told apart, so the stages are exactly the groups of kind "stage"
    assert {g["id"]: g["kind"] for g in services.tasks.list_groups() if g.get("project_id") == "mkt"} == {
        "mkt-INV": "stage", "mkt-CON": "stage", "mkt-ACT": "activities"}


# ---------------------------------------------------------------------------------------------------------------
# Privacy: events carry ids and states, never what the user typed
# ---------------------------------------------------------------------------------------------------------------

def test_events_never_carry_the_title_the_objective_the_summary_or_the_key(services):
    make_project(services)
    key = f"claude:{CLAUDE_ID}"
    services.activities.start("mkt", SECRET_TITLE, actor=OWNER, objective="Secret objective", idempotency_key=key)
    services.activities.close("mkt-ACT-T01", SECRET_SUMMARY, actor=OWNER)
    services.activities.start("mkt", "Another one", actor=OWNER, executor="codex_cli")
    events = services.tasks.events(project_id="mkt")
    text = json.dumps(events)
    for secret in (SECRET_TITLE, "Secret objective", SECRET_SUMMARY, key):
        assert secret not in text, secret
    assert all(len(json.dumps(e["payload"]).encode()) <= 2048 for e in events)
    assert services.activities.list("mkt")[0]["title"] == SECRET_TITLE   # the views do carry them


def test_no_new_event_types_were_needed():
    from multiagent.services.tasks import EVENT_TYPES
    assert {"task.created", "task.claimed", "task.started", "task.submitted", "task.accepted"} <= set(EVENT_TYPES)
    assert not [name for name in EVENT_TYPES if name.startswith("activity.")]


# ---------------------------------------------------------------------------------------------------------------
# Facade, HTTP and CLI give the same results
# ---------------------------------------------------------------------------------------------------------------

class _ClientContext:
    """Keep the shared TestClient open across the CLI's per-request context."""

    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc_info) -> bool:
        return False


SCRIPT: list[tuple[str, dict[str, Any]]] = [
    ("list", {"project_id": "par"}),
    ("start", {"project_id": "par", "title": "Review the brief", "actor": OWNER, "idempotency_key": "chat-1",
               "objective": "Read it."}),
    ("start", {"project_id": "par", "title": "Replay", "actor": OWNER, "idempotency_key": "chat-1"}),
    ("start", {"project_id": "par", "title": "Prepared", "actor": OWNER, "executor": "codex_cli"}),
    ("start", {"project_id": "par", "title": "Bad actor", "actor": "bob"}),
    ("start", {"project_id": "par", "title": "", "actor": OWNER}),
    ("start", {"project_id": "ghost", "title": "Nowhere", "actor": OWNER}),
    ("list", {"project_id": "par"}),
    ("list", {"project_id": "par", "status": "doing"}),
    ("list", {"project_id": "par", "status": "ready", "since": "2020-01-01"}),
    ("list", {"project_id": "par", "status": "nonsense"}),
    ("list", {"project_id": "ghost"}),
    ("close", {"activity_id": "par-ACT-T01", "summary": "Reviewed.", "actor": AGENT}),
    ("close", {"activity_id": "par-ACT-T01", "summary": "Reviewed.", "actor": OWNER}),
    ("close", {"activity_id": "par-ACT-T01", "summary": "Again.", "actor": OWNER}),
    ("close", {"activity_id": "par-INV-T01", "summary": "Not an activity.", "actor": OWNER}),
    ("close", {"activity_id": "par-ACT-T77", "summary": "Missing.", "actor": OWNER}),
    ("list", {"project_id": "par", "status": "done"}),
    ("get", {"project_id": "par"}),
]


def run_facade(svc: ApplicationServices, op: str, args: dict[str, Any]) -> Any:
    args = dict(args)
    if op == "start":
        return svc.activities.start(args.pop("project_id"), args.pop("title"), **args)
    if op == "close":
        return svc.activities.close(args.pop("activity_id"), args.pop("summary"), **args)
    if op == "list":
        return svc.activities.list(args.pop("project_id"), **args)
    if op == "get":
        return svc.projects.get(args["project_id"])
    raise AssertionError(op)  # pragma: no cover


def run_http(client: TestClient, op: str, args: dict[str, Any]) -> tuple[int, Any]:
    a = dict(args)
    seg = lambda value: quote(value, safe="")  # noqa: E731
    if op == "start":
        response = client.post(f"/api/v1/projects/{seg(a.pop('project_id'))}/activities", json=a)
    elif op == "close":
        response = client.post(f"/api/v1/activities/{seg(a.pop('activity_id'))}/close", json=a)
    elif op == "list":
        response = client.get(f"/api/v1/projects/{seg(a.pop('project_id'))}/activities", params=a)
    elif op == "get":
        response = client.get(f"/api/v1/projects/{seg(a['project_id'])}")
    else:  # pragma: no cover
        raise AssertionError(op)
    return response.status_code, response.json()


def cli_args(op: str, args: dict[str, Any]) -> list[str]:
    a = dict(args)
    if op == "start":
        argv = ["activity", "start", a["project_id"], "--title", a["title"], "--actor", a["actor"]]
        for name, flag in (("executor", "--executor"), ("idempotency_key", "--idempotency-key"), ("objective", "--objective")):
            if name in a:
                argv += [flag, a[name]]
        return argv
    if op == "close":
        return ["activity", "close", a["activity_id"], "--summary", a["summary"], "--actor", a["actor"]]
    if op == "list":
        argv = ["activity", "list", a["project_id"]]
        for name in ("status", "since"):
            if name in a:
                argv += [f"--{name}", a[name]]
        return argv
    if op == "get":
        return ["project", "show", a["project_id"]]
    raise AssertionError(op)  # pragma: no cover


def run_cli(monkeypatch, capsys, client: TestClient, op: str, args: dict[str, Any]) -> tuple[int, Any]:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    code = 0
    try:
        cli.main(cli_args(op, args))
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else captured.err)


def normalize(value: Any) -> Any:
    """Drop the timestamps: everything else must be identical across interfaces."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()
                if k not in ("created_at", "updated_at", "archived_at", "closed_at", "actual")}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def test_facade_http_and_cli_give_the_same_results(monkeypatch, capsys, tmp_path):
    facade = make_services(tmp_path / "facade")
    http = make_services(tmp_path / "http")
    through_cli = make_services(tmp_path / "cli")
    for svc in (facade, http, through_cli):
        make_project(svc, "par")
    try:
        with TestClient(create_app(services=http), base_url="http://127.0.0.1") as http_client, \
                TestClient(create_app(services=through_cli), base_url="http://127.0.0.1") as cli_client:
            for step, (op, args) in enumerate(SCRIPT):
                try:
                    expected: tuple[str, Any] = ("ok", normalize(run_facade(facade, op, args)))
                except ServiceError as error:
                    expected = ("error", (error.status, error.code))
                status, body = run_http(http_client, op, args)
                if expected[0] == "ok":
                    assert status in (200, 201), (step, op, body)
                    assert normalize(body) == expected[1], (step, op)
                else:
                    assert (status, body["detail"]["code"]) == expected[1], (step, op, body)
                code, output = run_cli(monkeypatch, capsys, cli_client, op, args)
                if expected[0] == "ok":
                    assert code == 0 and normalize(output) == expected[1], (step, op, output)
                else:
                    assert code == 1 and f"HTTP {expected[1][0]}: {expected[1][1]}:" in output, (step, op, output)
    finally:
        for svc in (facade, http, through_cli):
            svc.runs.close()


def test_the_parity_script_covers_every_public_operation():
    public = {name for name, member in vars(ActivityServices).items() if callable(member) and not name.startswith("_")}
    assert {op for op, _ in SCRIPT} - {"get"} == public == {"start", "close", "list"}
    # the project service keeps its own operations: activities are not added to it
    assert not {"start", "close", "start_activity"} & {n for n, m in vars(ProjectServices).items() if callable(m)}


def test_http_answers_201_for_a_new_activity_and_200_for_a_replayed_one(tmp_path):
    svc = make_services(tmp_path / "local")
    try:
        make_project(svc)
        with TestClient(create_app(services=svc), base_url="http://127.0.0.1") as client:
            body = {"title": "Review", "actor": OWNER, "idempotency_key": "chat-1"}
            first = client.post("/api/v1/projects/mkt/activities", json=body)
            again = client.post("/api/v1/projects/mkt/activities", json=body)
            assert (first.status_code, first.json()["created"]) == (201, True)
            assert (again.status_code, again.json()["created"], again.json()["id"]) == (200, False, "mkt-ACT-T01")
            extra = client.post("/api/v1/projects/mkt/activities", json={**body, "unexpected": 1})
            assert extra.status_code == 422
            missing = client.post("/api/v1/projects/mkt/activities", json={"title": "No actor"})
            assert missing.status_code == 422
            assert client.get("/api/v1/projects/mkt/activities").json()[0]["id"] == "mkt-ACT-T01"
            closed = client.post("/api/v1/activities/mkt-ACT-T01/close", json={"summary": "Done.", "actor": OWNER})
            assert closed.status_code == 200 and closed.json()["state"] == "accepted"
            assert client.get("/api/v1/projects/mkt").json()["activities"]["done"] == 1
    finally:
        svc.runs.close()
