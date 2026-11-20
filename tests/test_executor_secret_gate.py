# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SEC-T03: the secret gate in front of every executor launch and resume.

No real Claude, Codex, Copilot or agy is started: the real adapters run against the generated fake programs of
``test_executor_services`` and ``test_executor_antigravity``, and the hand-off and resume cases use the fake apps of
``test_quota_handoff``. Nobody clicks «Aprobar»: the decision is seeded with ``user_approvals.decide`` or with
TestClient and a session seeded from the test. The secret is synthetic and recognizable, and the tests look for it in
events, approvals, answers and logs.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest

from multiagent import cli
from multiagent.gui.security import APPROVAL_COOKIE, APPROVAL_COOKIE_PATH
from multiagent.services import ServiceError
from test_executor_services import _ClientContext, claude_out
from test_gui_executor_launch import EXECUTOR_IDS, GuiWorld
from test_quota_handoff import at, calls, card_decides, hit_limit, make_world as make_handoff_world

SECRET = "sk-FAKEsynthetic0123456789ABCDEFGH"
OTHER_SECRET = "sk-FAKEsynthetic9876543210ZYXWVUTSR"
CODE = "synthetic-start-code"
TID = "GEM-T99"            # the task id the fake agy puts in its result envelope
MODELS = {"copilot_cli": "gpt-6-luna"}


@pytest.fixture
def gui(tmp_path, monkeypatch):
    world = GuiWorld(tmp_path, monkeypatch)
    world.configure(EXECUTOR_IDS)
    world.services.tasks.create_group("SEC", "Secret gate", actor="user", authorized=True)
    yield world
    world.client.close()
    world.services.runs.close()


@pytest.fixture
def handoff(tmp_path):
    return make_handoff_world(tmp_path, apps=("claude_cli", "codex_cli"))


def make_task(world, task_id, objective):
    world.services.tasks.create_task("SEC", task_id, "Secret gate task", actor="user", objective=objective)


def set_objective(services, task_id, objective):
    """Change the text of a task after it was created (there is no service for it: a test edits the row)."""
    with services.tasks.store.transaction() as conn:
        conn.execute("UPDATE tasks SET objective=? WHERE id=?", (objective, task_id))


def run(world, task_id, executor_id):
    return world.services.executors.run_task(task_id, executor_id, MODELS.get(executor_id), None, actor="user")


def launches(world, executor_id):
    if executor_id == "antigravity_cli":
        return world.fake_agy.runs()
    return world.fake_cli.calls(executor_id.removesuffix("_cli"))


def approvals(services):
    return list(services.user_approvals._all().values())


def everything(services, *answers):
    """The text a leak could be in: every team event, every approval and what the service answered."""
    shown = [{key: value for key, value in answer.items() if key != "task"}
             if isinstance(answer, dict) else answer for answer in answers]       # the task view is the person's own text
    return json.dumps([services.tasks.events(), approvals(services), *shown], default=str, ensure_ascii=False)


def held_approval(services, task_id):
    hold = services.tasks.get_task(task_id)["continuity"]["hold"]
    return hold["condition"]["params"]["approval"], hold


@pytest.mark.parametrize("executor_id", EXECUTOR_IDS)
def test_a_secret_in_the_objective_does_not_launch_and_the_task_is_held(gui, executor_id, caplog):
    caplog.set_level(logging.DEBUG)
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")

    out = run(gui, TID, executor_id)

    assert out["outcome"] == "secret_review_required" and launches(gui, executor_id) == []
    review = out["secret_review"]
    task = services.tasks.get_task(TID)
    approval_id, hold = held_approval(services, TID)
    assert task["state"] == "paused" and hold["reason"] == "approval" and approval_id == review["approval_id"]
    assert hold["condition"]["type"] == "approval" and hold["executor_id"] == executor_id
    (item,) = approvals(services)
    assert item["id"] == approval_id and item["kind"] == "secret_send" and item["state"] == "pending"
    assert item["location"] == {"source": "task", "task_id": TID} and item["secret_types"] == ["secret_key"]
    assert item["sha256"] == review["sha256"] and len(item["sha256"]) == 64
    assert any("secret_review_required" in note and approval_id in note for note in out["notes"])
    (finished,) = [e["payload"] for e in services.tasks.events(task_id=TID) if e["type"] == "executor.attempt_finished"]
    assert finished["outcome"] == "secret_review_required" and "Aprobaciones" in finished["evidence"]
    # Nobody waits it out: the automatic resumptions and the watcher's check leave it as it is.
    assert services.executors.resume_due()["resumed"] == []
    assert services.executors.resume_met(task_id=TID)["resumed"] == []
    assert services.executors.check_waits(task_id=TID)["waits"][0]["state"] != "met"
    assert launches(gui, executor_id) == [] and services.tasks.get_task(TID)["state"] == "paused"
    assert SECRET not in everything(services, out) and SECRET not in caplog.text


@pytest.mark.parametrize("executor_id", EXECUTOR_IDS)
def test_the_click_only_approves_and_the_next_check_launches_once(gui, executor_id):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, executor_id)
    approval_id, _ = held_approval(services, TID)

    decided = services.user_approvals.decide(approval_id, "approve")

    assert decided["state"] == "approved" and launches(gui, executor_id) == []       # the click launched nothing
    out = services.executors.resume_met(task_id=TID)
    (resumed,) = out["resumed"]
    assert resumed["outcome"] == "submitted" and len(launches(gui, executor_id)) == 1
    assert services.user_approvals.peek(approval_id)["state"] == "consumed"
    assert services.tasks.get_task(TID)["state"] == "submitted"
    assert services.executors.resume_met(task_id=TID)["resumed"] == [] and len(launches(gui, executor_id)) == 1
    assert SECRET not in everything(services, out)


@pytest.mark.parametrize("executor_id", ("claude_cli", "antigravity_cli"))
def test_a_text_changed_after_the_approval_asks_again(gui, executor_id):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, executor_id)
    first, _ = held_approval(services, TID)
    services.user_approvals.decide(first, "approve")
    set_objective(services, TID, f"Use the other key {OTHER_SECRET} to call the service.")

    out = services.executors.resume_met(task_id=TID)

    (resumed,) = out["resumed"]
    assert resumed["outcome"] == "secret_review_required" and launches(gui, executor_id) == []
    second, _ = held_approval(services, TID)
    assert second != first and services.user_approvals.peek(first)["state"] == "approved"      # never spent
    assert services.user_approvals.peek(second)["state"] == "pending"
    services.user_approvals.decide(second, "approve")
    assert services.executors.resume_met(task_id=TID)["resumed"][0]["outcome"] == "submitted"
    assert len(launches(gui, executor_id)) == 1 and OTHER_SECRET not in everything(services, out)


def test_the_same_text_run_again_reuses_its_pending_request(gui):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "codex_cli")
    first, _ = held_approval(services, TID)

    run(gui, TID, "codex_cli")

    assert [item["id"] for item in approvals(services)] == [first]


def test_a_rejection_keeps_the_task_held_and_launches_nothing(gui):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(services, TID)

    services.user_approvals.decide(approval_id, "reject")

    out = services.executors.resume_met(task_id=TID)
    assert out["resumed"] == [] and launches(gui, "claude_cli") == []
    assert services.tasks.get_task(TID)["state"] == "paused"
    assert services.user_approvals.peek(approval_id)["state"] == "rejected"


def test_record_approval_refuses_a_secret_and_still_works_for_other_conditions(gui):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(services, TID)

    with pytest.raises(ServiceError) as caught:
        services.executors.record_approval(TID, approval_id, approved=True, by="user")

    assert caught.value.details["reason"] == "user_click_required" and caught.value.status == 409
    assert services.user_approvals.peek(approval_id)["state"] == "pending"
    assert "decisions" not in services.tasks.get_task(TID)["continuity"]["hold"]["condition"]
    assert services.executors.resume_met(task_id=TID)["resumed"] == [] and launches(gui, "claude_cli") == []
    # A plain approval condition is decided as before.
    make_task(gui, "S2", "Summarize the module.")
    gui.fake_cli.set("claude", stdout=claude_out("no envelope here"))
    assert run(gui, "S2", "claude_cli")["outcome"] != "submitted"
    services.executors.hold_on_condition("S2", {"type": "approval", "params": {"approval": "plain-1"}},
                                         executor_id="claude_cli", actor="user")
    done = services.executors.record_approval("S2", "plain-1", approved=True, by="user")
    assert done["status"] == "approved"


def test_the_gui_route_seeds_the_decision_and_the_list_hides_the_secret(gui):
    services, client = gui.services, gui.client
    client.app.state.approval_sessions.bootstrap_code = CODE
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(services, TID)
    base = "/gui/api/approvals"
    assert client.post(base + "/session", headers=gui.auth, json={"code": CODE}).status_code == 200
    listed = client.get(base, headers=gui.auth).json()["items"]
    assert [item["id"] for item in listed] == [approval_id] and SECRET not in json.dumps(listed)
    code = client.post(f"{base}/{approval_id}/challenge", headers=gui.auth, json={}).json()["code"]
    decided = client.post(f"{base}/{approval_id}/decision", headers=gui.auth, json={"decision": "approve", "code": code})
    assert decided.status_code == 200 and decided.json()["state"] == "approved"
    assert client.cookies.get(APPROVAL_COOKIE) is not None and APPROVAL_COOKIE_PATH


def test_a_task_without_a_secret_launches_as_before_and_asks_nobody(gui):
    make_task(gui, TID, "Summarize the current project.")

    out = run(gui, TID, "codex_cli")

    assert out["outcome"] == "submitted" and "secret_review" not in out
    assert approvals(gui.services) == [] and len(launches(gui, "codex_cli")) == 1


def test_the_api_the_gui_and_the_cli_reach_the_gate(gui, monkeypatch, capsys):
    client = gui.client
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    make_task(gui, "S2", f"Use the key {SECRET} to call the service.")
    api = client.post("/api/v1/executors/run", headers=gui.auth, json={"task_id": TID, "executor_id": "claude_cli"})
    assert api.status_code == 200 and api.json()["outcome"] == "secret_review_required"
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    cli.main(["executors", "run", "S2", "--executor", "codex_cli"])
    shown = json.loads(capsys.readouterr().out)
    assert shown["outcome"] == "secret_review_required"
    gui_launch = client.post("/gui/api/executors/launch", headers=gui.auth, json={
        "task": f"Call it with {SECRET}.", "executor_id": "claude_cli", "confirmed": True})
    assert gui_launch.status_code == 200 and gui_launch.json()["launch"]["outcome"] == "secret_review_required"
    assert gui.fake_cli.calls() == []
    assert SECRET not in json.dumps([api.json().get("secret_review"), shown.get("secret_review"),
                                     gui_launch.json()["launch"].get("secret_review"), approvals(gui.services)])
    assert SECRET not in capsys.readouterr().err and len(approvals(gui.services)) == 3


def test_a_resume_with_a_secret_in_the_prompt_waits_and_then_resumes_the_same_session(handoff):
    services = handoff.services
    first = hit_limit(handoff, patch=False, reset=at(2))
    services.executors.lift_pause("claude_cli", reason="the test needs the executor")
    set_objective(services, "T1", f"Use the key {SECRET} to call the service.")

    out = services.executors.resume_task("T1")

    assert out["outcome"] == "secret_review_required" and len(calls(handoff, "claude_cli")) == 1
    approval_id, hold = held_approval(services, "T1")
    assert hold["session_id"] == first["session_id"] and services.tasks.get_task("T1")["state"] == "paused"
    services.user_approvals.decide(approval_id, "approve")
    resumed = services.executors.resume_met(task_id="T1")["resumed"]
    assert [item["outcome"] for item in resumed] == ["submitted"]
    assert [call.kind for call in calls(handoff, "claude_cli")] == ["launch", "resume"]
    assert calls(handoff, "claude_cli")[1].session_id == first["session_id"]
    assert SECRET not in everything(services, out)


def test_a_hand_off_with_a_secret_in_the_prompt_waits_for_the_click(handoff):
    services = handoff.services
    hit_limit(handoff, reset=at(2))
    set_objective(services, "T1", f"Use the key {SECRET} to call the service.")
    card_decides(handoff, "codex_cli")

    out = services.executors.decide_holds()

    (done,) = out["delegated"]
    assert done["run_outcome"] == "secret_review_required" and calls(handoff, "codex_cli") == []
    task = services.tasks.get_task("T1")
    approval_id, hold = held_approval(services, "T1")
    assert task["state"] == "paused" and task["assignee"] == "executor:codex_cli" and hold["executor_id"] == "codex_cli"
    handoff.clock.now += timedelta(hours=3)
    assert services.executors.resume_due()["resumed"] == [] and calls(handoff, "codex_cli") == []
    services.user_approvals.decide(approval_id, "approve")
    resumed = services.executors.resume_met(task_id="T1")["resumed"]
    assert [item["outcome"] for item in resumed] == ["submitted"] and len(calls(handoff, "codex_cli")) == 1
    assert SECRET not in everything(services, out)



def test_the_long_random_ids_of_the_framework_are_not_taken_for_a_secret(gui):
    launched = gui.client.post("/gui/api/executors/launch", headers=gui.auth, json={
        "task": "Summarize the current project.", "executor_id": "codex_cli", "confirmed": True})

    assert launched.status_code == 200 and launched.json()["launch"]["outcome"] == "submitted"
    assert launched.json()["task_id"].startswith("gui-executor-") and len(launched.json()["task_id"]) > 40
    assert approvals(gui.services) == [] and len(launches(gui, "codex_cli")) == 1


def click(gui, approval_id, decision):
    """The decision through the GUI route, with the session and the code seeded from the test."""
    client, base = gui.client, "/gui/api/approvals"
    client.app.state.approval_sessions.bootstrap_code = CODE
    if client.cookies.get(APPROVAL_COOKIE) is None:
        assert client.post(base + "/session", headers=gui.auth, json={"code": CODE}).status_code == 200
    code = client.post(f"{base}/{approval_id}/challenge", headers=gui.auth, json={}).json()["code"]
    return client.post(f"{base}/{approval_id}/decision", headers=gui.auth, json={"decision": decision, "code": code})


def test_the_gui_approval_checks_the_hold_itself_and_launches_once(gui):
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(gui.services, TID)

    response = click(gui, approval_id, "approve")

    assert response.status_code == 200 and response.json()["state"] == "approved"
    assert response.json()["resume"]["state"] in ("checked", "running")
    for _ in range(100):
        if launches(gui, "claude_cli"):
            break
        __import__("time").sleep(0.1)
    assert len(launches(gui, "claude_cli")) == 1 and SECRET not in response.text
    assert gui.services.executors.resume_met(task_id=TID)["resumed"] == [] and len(launches(gui, "claude_cli")) == 1


def test_the_gui_rejection_never_launches(gui):
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(gui.services, TID)

    response = click(gui, approval_id, "reject")

    assert response.status_code == 200 and response.json()["state"] == "rejected"
    assert launches(gui, "claude_cli") == [] and gui.services.tasks.get_task(TID)["state"] == "paused"


def test_a_failing_check_after_the_click_keeps_the_approval_for_the_next_one(gui, monkeypatch):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(services, TID)
    real = services.executors.resume_met

    def broken(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(services.executors, "resume_met", broken)
    response = click(gui, approval_id, "approve")
    monkeypatch.setattr(services.executors, "resume_met", real)

    assert response.status_code == 200 and response.json()["resume"] == {"state": "error", "code": "RuntimeError"}
    assert services.user_approvals.peek(approval_id)["state"] == "approved" and launches(gui, "claude_cli") == []
    assert [item["outcome"] for item in real(task_id=TID)["resumed"]] == ["submitted"]
    assert len(launches(gui, "claude_cli")) == 1


def test_a_different_secret_in_the_prompt_with_the_same_envelope_asks_again(handoff):
    services = handoff.services
    set_objective(services, "T1", f"Use the key {SECRET} to call the service.")
    handoff.fakes["claude_cli"].script.append({})
    out = services.executors.run_task("T1", "claude_cli", model="claude-big", effort="high")
    assert out["outcome"] == "secret_review_required"
    first, _ = held_approval(services, "T1")
    services.user_approvals.decide(first, "approve")
    handoff.fakes["claude_cli"].render_prompt = lambda request: f"prompt {OTHER_SECRET}"

    resumed = services.executors.resume_met(task_id="T1")["resumed"]

    assert [item["outcome"] for item in resumed] == ["secret_review_required"] and calls(handoff, "claude_cli") == []
    second, _ = held_approval(services, "T1")
    assert second != first and services.user_approvals.peek(first)["state"] == "approved"
    assert OTHER_SECRET not in everything(services, resumed)


def test_the_tray_path_fails_closed_when_the_approvals_cannot_be_read(gui, monkeypatch):
    services = gui.services
    make_task(gui, TID, f"Use the key {SECRET} to call the service.")
    run(gui, TID, "claude_cli")
    approval_id, _ = held_approval(services, TID)

    def broken(_id):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(services.user_approvals, "peek", broken)
    with pytest.raises(ServiceError) as caught:
        services.executors.record_approval(TID, approval_id, approved=True, by="user")
    assert caught.value.details["reason"] == "approval_lookup_failed"
    waits = services.executors.check_waits(task_id=TID)["waits"]
    assert waits[0]["state"] != "met" and launches(gui, "claude_cli") == []
