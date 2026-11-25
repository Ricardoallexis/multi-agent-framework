# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""CNT-T06: continuity in the GUI (decision card, node of the agent tree, section «Continuidad» of Uso).

Server tests run the real services (the task lifecycle, the executor service with fake adapters and a fake clock, the event
log) through ``/gui/api``: no real Claude, Codex or Copilot is started and nothing leaves the machine. The client is the
extension folder ``static/extensions/continuity/``; its words and rules are pure functions (``model.js``) that Node runs, its
drawing is checked as text and as a tree of nodes. The browser check is done by hand and reported with the task (light, dark, phone).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import test_gui_extensions as gui_extensions
import test_quota_hold as hold_tests
from multiagent.api import create_app
from multiagent.executors.registry import ExecutorRegistry
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR, continuity_api
from multiagent.gui.extensions import discover_extensions
from multiagent.gui.security import CSP
from multiagent.gui.sessions_api import add_session_routes
from multiagent.gui.token import ensure_token
from multiagent.routing.executor_capabilities import descriptor_from_declaration, validate_executor_descriptor
from multiagent.services import hold as hold_module
from multiagent.services.assignment import AssignmentServices
from test_gui_help import NODE, check_extensions, extension_help

JS = STATIC_DIR / "js"
EXT = STATIC_DIR / "extensions" / "continuity"
DECISION_ID = "11111111-aaaa-4bbb-8ccc-222222222222"
BODY = {"decision_id": DECISION_ID, "decision": "wait", "expected_revision": 0}


# --- the world: the real services with two fake executors, and the GUI over them ------------------------------------------------

class SecondExecutor(hold_tests.FakeExecutor):
    """A second application (a Codex-like surface), so a held task has someone to be delegated to."""
    adapter_id = "fake_hold_two"

    def __init__(self):
        super().__init__("other_cli")

    def describe(self, config):
        base = descriptor_from_declaration(self.executor_id, {"surface": "codex_cli", **dict(config or {})})
        return validate_executor_descriptor(replace(base, mode="automatic", adapter_id=self.adapter_id, adapter_version=self.version))


@pytest.fixture
def world(tmp_path):
    world = hold_tests.make_world(tmp_path)
    second = SecondExecutor()
    config = world.config
    config["executors"]["other_cli"] = {**hold_tests.declaration(), "adapter_id": second.adapter_id, "surface": "codex_cli"}
    services = world.services
    services.system.catalog.executor_routing = config
    services.assignment = AssignmentServices(services, registry=ExecutorRegistry(config, [world.fake, second]))
    services.executors.clock = world.clock
    services.settings.gui_token_file = tmp_path / "gui-token"
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        world.client, world.auth, world.second = client, {"Authorization": f"Bearer {token}"}, second
        world.levels = lambda: setattr(services.system.catalog, "models", {
            "fake-small": SimpleNamespace(level="T1", model="fake-small"), "fake-big": SimpleNamespace(level="T3", model="fake-big")})
        yield world


def held(world, task="T1", **options):
    """Run ``task`` until it ends at a plan limit that resets at 16:12:32 UTC; returns the run."""
    world.fake.script.append({"limit": hold_tests.limit(reset_at=options.pop("reset", hold_tests.at(4, 12, 32)), **options)})
    return world.run(task, model="fake-big", effort="high")


def held_on_second(world, task="T2", reset=None):
    """Run ``task`` on the other application until it ends at a plan limit."""
    world.second.script.append({"limit": hold_tests.limit(reset_at=reset or hold_tests.at(4, 12, 32))})
    return world.services.executors.run_task(task, "other_cli")


def cards(world):
    response = world.client.get("/gui/api/continuity/decisions", headers=world.auth)
    assert response.status_code == 200
    return response.json()["decisions"]


def decide(world, card, decision="wait", executor_id=None, decision_id=DECISION_ID, **changes):
    body = {"decision_id": decision_id, "decision": decision, "expected_revision": card["revision"], **changes}
    if executor_id:
        body["executor_id"] = executor_id
    return world.client.post(f"/gui/api/tasks/{card['task_id']}/continuity/decision", headers=world.auth, json=body)


def reason_of(response):
    return response.json()["detail"]["details"]["reason"]


def decided_events(world, task="T1"):
    return [event for event in world.services.tasks.events(task_id=task) if event["type"] == continuity_api.DECIDED_EVENT]


# --- 1. the routes need the token and describe the policy -----------------------------------------------------------------------

def test_every_route_needs_the_token(world):
    client = world.client
    for path in ("/gui/api/continuity/policy", "/gui/api/continuity/decisions", "/gui/api/continuity/session/claude:abc"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401, path
    assert client.post("/gui/api/tasks/T1/continuity/decision", json=BODY).status_code == 401
    assert client.post("/gui/api/tasks/T1/continuity/decision", headers={"Authorization": "Bearer wrong"}, json=BODY).status_code == 401
    response = client.get("/gui/api/continuity/policy", headers=world.auth)
    assert response.status_code == 200 and response.headers["content-security-policy"] == CSP


def test_the_policy_shows_the_contract_defaults_and_the_real_executors(world):
    policy = world.client.get("/gui/api/continuity/policy", headers=world.auth).json()
    assert policy["mode"] == hold_module.DEFAULT_MODE == "semi_auto" and policy["modes"] == ["manual", "semi_auto", "auto"]
    assert policy["wait_threshold_hours"] == 5 and policy["max_handoffs"] == 1 and policy["max_failed_resumes"] == 3
    assert policy["never_during_integration"] is True and policy["auto_scope"] == "project" and policy["source"] == "default"
    rows = {row["executor_id"]: row for row in policy["candidates"]}
    assert set(rows) == {"fake_cli", "other_cli"}
    assert rows["fake_cli"] == {"executor_id": "fake_cli", "app": "Claude", "enabled": True, "available": True, "paused_until": None,
                                "paused_open_ended": False, "policy": "no_ask"}
    assert rows["other_cli"]["app"] == "Codex" and rows["other_cli"]["policy"] == "no_ask"


def test_a_paused_executor_shows_its_pause_in_the_policy(world):
    held(world)
    rows = {row["executor_id"]: row for row in world.client.get("/gui/api/continuity/policy", headers=world.auth).json()["candidates"]}
    assert rows["fake_cli"]["available"] is False and rows["fake_cli"]["paused_until"] == hold_tests.at(4, 14, 32).isoformat()
    assert rows["other_cli"]["available"] is True


# --- 2. the decision card -------------------------------------------------------------------------------------------------------

def test_without_a_held_task_there_is_no_card(world):
    assert cards(world) == []
    session = "claude:11111111-2222-4333-8444-555555555555"
    assert world.client.get("/gui/api/continuity/session/" + session, headers=world.auth).json() == {
        "held": False, "session_id": session, "card": None}


def test_the_card_says_who_reached_which_limit_when_it_resets_and_what_was_saved(world):
    out = held(world)
    world.levels()
    (card,) = cards(world)
    assert (card["task_id"], card["group_id"], card["title"]) == ("T1", "CNT", "Fix the parser")
    assert (card["app"], card["executor_id"], card["model"], card["level"]) == ("Claude", "fake_cli", "fake-big", "T3")
    assert (card["reason"], card["window"], card["decision"], card["blocked"]) == ("plan_limit", "five_hour", "pending", None)
    assert card["reset"] == {"at": "2026-10-11T16:12:32Z", "source": "app_epoch", "confidence": "high", "zone": "UTC",
                             "resume_after": "2026-10-11T16:14:32Z", "open_ended": False, "reported_at": None}
    assert card["handoffs"] == {"used": 0, "max": 1} and card["handoffs_exhausted"] is False and card["can_wait"] is True
    assert card["session_id"] == out["session_id"] and card["session_ref"] == "claude:" + out["session_id"]
    assert card["revision"] == world.services.tasks.get_task("T1")["revision"]
    assert card["checkpoint"]["saved"] is True and card["checkpoint"]["patch"] is False        # no git in the fake work folder
    # the only candidate is the other application: the one that hit the limit is in the chain
    (candidate,) = card["candidates"]
    assert candidate == {"executor_id": "other_cli", "app": "Codex", "model": "fake-big", "level": "T3", "mode": "automatic",
                         "policy": "no_ask", "available": True, "paused_until": None, "sends": "el sobre de la tarea (sin parche)"}
    # no text of the agent, no path of the machine and no evidence text travel in the card
    assert str(world.workdir) not in json.dumps(card) and "session limit" not in json.dumps(card)


def test_a_cheaper_original_never_gets_a_dearer_suggestion(world):
    world.fake.script.append({"limit": hold_tests.limit(reset_at=hold_tests.at(4, 12, 32))})
    world.run("T1", model="fake-small", effort="low")
    world.levels()
    (card,) = cards(world)
    assert (card["level"], card["candidates"][0]["model"], card["candidates"][0]["level"]) == ("T1", "fake-small", "T1")


def test_without_a_level_in_the_catalog_the_model_is_left_for_later(world):
    held(world)
    (card,) = cards(world)
    assert card["level"] is None and card["candidates"][0]["model"] is None and card["candidates"][0]["level"] is None


def test_the_session_route_finds_the_card_of_the_session(world):
    out = held(world)
    found = world.client.get("/gui/api/continuity/session/claude:" + out["session_id"], headers=world.auth).json()
    assert found["held"] is True and found["card"]["task_id"] == "T1"
    other = world.client.get("/gui/api/continuity/session/claude:00000000-0000-4000-8000-000000000000", headers=world.auth).json()
    assert other["held"] is False
    assert world.client.get("/gui/api/continuity/session/" + "x" * 200, headers=world.auth).status_code == 422


def test_a_credit_limit_has_no_time_to_wait_for(world):
    world.fake.script.append({"limit": hold_tests.limit("credits_exhausted", window="spend", reset_at=None,
                                                        evidence="credit balance is too low")})
    world.run("T1")
    (card,) = cards(world)
    assert card["reason"] == "credits_exhausted" and card["reset"]["open_ended"] is True and card["reset"]["resume_after"] is None
    assert card["can_wait"] is False
    refused = decide(world, card)
    assert refused.status_code == 409 and reason_of(refused) == "no_reset_time"


def test_a_wait_by_condition_is_not_a_usage_limit_card(world):
    held(world)
    assert len(cards(world)) == 1
    hold = hold_module.hold_of(world.services.tasks.store.get_task("T1"))
    world.services.tasks._edit_continuity("T1", {"hold": {**hold, "reason": "dependency"}}, actor="test")
    assert cards(world) == []


# --- 3. the decision -----------------------------------------------------------------------------------------------------------

def test_waiting_is_recorded_once_and_changes_nothing_else(world):
    held(world)
    (card,) = cards(world)
    before = world.services.tasks.get_task("T1")
    answer = decide(world, card)
    assert answer.status_code == 200
    body = answer.json()
    assert (body["ok"], body["executed"], body["repeated"], body["decision"]) == (True, False, False, "wait")
    assert body["card"]["decision"] == "wait" and body["card"]["revision"] == card["revision"] + 1
    after = world.services.tasks.get_task("T1")
    hold = after["continuity"]["hold"]
    assert (hold["decision"], hold["decided_by"], hold["decided_at"]) == ("wait", "user", "2026-10-11T12:00:00Z")
    # the rest of the hold, the state and the owner are as they were: the task still resumes by itself at the reset
    assert {key: value for key, value in hold.items() if key not in ("decision", "decided_by", "decided_at")} == \
        {key: value for key, value in before["continuity"]["hold"].items() if key != "decision"}
    assert (after["state"], after["assignee"]) == (before["state"], before["assignee"]) and "blocked" not in hold
    (event,) = decided_events(world)
    assert event["ref"] == DECISION_ID and event["actor"] == "user:gui"
    assert event["payload"]["decision"] == "wait" and event["payload"]["by"] == "user" and event["payload"]["executor_id"] == "fake_cli"
    # the same decision again returns the first result and writes nothing
    again = decide(world, card)
    assert again.status_code == 200 and again.json()["repeated"] is True and len(decided_events(world)) == 1
    assert world.services.tasks.get_task("T1")["revision"] == after["revision"]
    assert len(world.fake.calls) == 1 and world.second.calls == []          # the decision launched nothing


def test_a_stale_card_is_refused_and_a_decision_id_is_not_shared_between_tasks(world):
    held(world)
    held_on_second(world)
    first, second = cards(world)
    assert [first["task_id"], second["task_id"]] == ["T1", "T2"]
    stale = decide(world, {**first, "revision": first["revision"] - 1})
    assert stale.status_code == 409 and reason_of(stale) == "stale_revision"
    assert decide(world, first).status_code == 200
    reused = decide(world, second)       # the same decision_id, another task
    assert reused.status_code == 409 and reason_of(reused) == "decision_id_reused"
    assert decided_events(world, "T2") == []


def test_bad_requests_are_refused_before_anything_is_written(world):
    held(world)
    (card,) = cards(world)
    url = "/gui/api/tasks/T1/continuity/decision"
    base = {"decision_id": DECISION_ID, "decision": "wait", "expected_revision": card["revision"]}
    post = lambda **body: world.client.post(url, headers=world.auth, json={**base, **body})
    assert post(decision="cancel").status_code == 422
    assert post(decision_id="x").status_code == 422
    assert post(expected_revision=-1).status_code == 422
    assert post(unknown=1).status_code == 422
    assert post(executor_id="BAD ID").status_code == 422
    assert world.client.post("/gui/api/tasks/NOPE/continuity/decision", headers=world.auth, json=base).status_code == 404
    assert reason_of(world.client.post("/gui/api/tasks/T3/continuity/decision", headers=world.auth, json=base)) == "not_held"
    assert decided_events(world) == [] and world.services.tasks.get_task("T1")["continuity"]["hold"]["decision"] == "pending"


def test_delegating_records_the_choice_and_stops_the_automatic_resume(world):
    held(world)
    (card,) = cards(world)
    assert decide(world, card, "delegate").status_code == 422                  # a candidate is needed
    unknown = decide(world, card, "delegate", "fake_cli")                       # the executor that hit the limit is not a candidate
    assert unknown.status_code == 409 and reason_of(unknown) == "candidate_not_available"
    answer = decide(world, card, "delegate", "other_cli")
    assert answer.status_code == 200 and answer.json()["executed"] is False
    task = world.services.tasks.get_task("T1")
    hold = task["continuity"]["hold"]
    assert (hold["decision"], hold["candidate"], hold["blocked"], hold["decided_by"]) == ("delegate", "other_cli", "delegation_requested", "user")
    # nothing was started and the owner did not change: only the person's choice was saved
    assert (task["state"], task["assignee"]) == ("paused", "executor:fake_cli") and len(world.fake.calls) == 1
    assert world.second.calls == [] and task["continuity"]["handoffs"] == 0
    (event,) = decided_events(world)
    assert event["payload"]["decision"] == "delegate" and event["payload"]["candidate"] == "other_cli"
    assert event["payload"]["sends"] == "el sobre de la tarea (sin parche)"
    # past the reset the framework does not resume it by itself: a person decided
    world.clock.to(hold_tests.at(5))
    due = world.services.executors.resume_due(dry_run=True)
    assert due["due"] == [] and [item["why"] for item in due["skipped"]] == ["blocked:delegation_requested"]
    # the card shows the choice; «Esperar» is still offered, and taking it back lets the task resume at the reset
    (shown,) = cards(world)
    assert (shown["decision"], shown["candidate"], shown["blocked"], shown["can_wait"]) == ("delegate", "other_cli", "delegation_requested", True)
    back = decide(world, shown, "wait", decision_id="33333333-aaaa-4bbb-8ccc-444444444444")
    assert back.status_code == 200 and back.json()["card"]["decision"] == "wait" and back.json()["card"]["blocked"] is None
    hold = world.services.tasks.get_task("T1")["continuity"]["hold"]
    assert "blocked" not in hold and "candidate" not in hold
    assert [item["task_id"] for item in world.services.executors.resume_due(dry_run=True)["due"]] == ["T1"]


def test_a_paused_candidate_is_shown_but_cannot_be_chosen(world):
    held(world)
    held_on_second(world, reset=hold_tests.at(6))
    (card,) = [item for item in cards(world) if item["task_id"] == "T1"]
    (candidate,) = card["candidates"]
    assert candidate["available"] is False and candidate["paused_until"] is not None
    refused = decide(world, card, "delegate", "other_cli")
    assert refused.status_code == 409 and reason_of(refused) == "candidate_not_available"


def test_when_the_handoffs_are_used_up_no_candidate_is_offered(world):
    held(world)
    world.services.tasks._edit_continuity("T1", {"handoffs": 1}, actor="test")
    (card,) = cards(world)
    assert card["handoffs"] == {"used": 1, "max": 1} and card["handoffs_exhausted"] is True and card["candidates"] == []
    assert card["can_wait"] is True
    refused = decide(world, card, "delegate", "other_cli")
    assert refused.status_code == 409 and reason_of(refused) == "handoffs_exhausted"


# --- 4. the node of the agent tree ---------------------------------------------------------------------------------------------

def test_a_held_task_is_a_waiting_node_of_its_session(world):
    out = held(world)
    session = "claude:" + out["session_id"]
    (node,) = continuity_api.held_children(world.services, session)
    assert node["id"] == "task:T1" and (node["source"], node["kind"], node["status"], node["note"]) == ("claude", "task", "idle", "waiting_limit")
    assert node["reset_at"] == "2026-10-11T16:12:32Z" and node["model"] == "fake-big" and node["tokens"] is None and node["link_id"] is None
    assert continuity_api.held_children(world.services, "claude:00000000-0000-4000-8000-000000000000") == []
    assert continuity_api.held_children(world.services, "framework:run") == [] and continuity_api.held_children(world.services, "nonsense") == []
    # a store that cannot answer must not break the children list
    assert continuity_api.held_children(SimpleNamespace(), session) == []


def test_the_children_route_and_its_version_carry_the_waiting_node():
    base = {"session_id": "claude:s", "children": [{"id": "a", "status": "running"}], "counts": {"active": 1, "done": 0, "total": 1},
            "version": "abc", "warnings": []}
    node = {"id": "task:T1", "status": "idle", "note": "waiting_limit", "reset_at": "2026-10-11T16:12:32Z"}

    class Sessions:
        def list_children(self, session_id, status=None):
            assert session_id == "claude:s"
            return dict(base)

    api, plain = APIRouter(prefix="/gui/api"), APIRouter(prefix="/plain")
    add_session_routes(api, Sessions(), extra_children=lambda session_id: [node])
    add_session_routes(plain, Sessions())
    app = FastAPI()
    app.include_router(api)
    app.include_router(plain)
    client = TestClient(app)
    both = client.get("/gui/api/sessions/claude:s/children").json()
    assert [child["id"] for child in both["children"]] == ["a", "task:T1"] and both["counts"] == {"active": 2, "done": 0, "total": 2}
    assert both["version"] != "abc" and len(both["version"]) == 12
    done = client.get("/gui/api/sessions/claude:s/children?status=done").json()
    assert done["children"] == [{"id": "a", "status": "running"}] and done["counts"]["total"] == 2
    assert client.get("/plain/sessions/claude:s/children").json() == base                       # no extra nodes: the answer is untouched
    assert continuity_api.with_extra_children(base, []) is base


# --- 5. the extension folder ---------------------------------------------------------------------------------------------------

def test_the_extension_is_found_by_convention_and_follows_every_rule():
    names = {path.name for path in EXT.iterdir()}
    assert {"index.js", "style.css", "help.js", "model.js", "card.js", "view.js", "slot.js", "policy.js", "poll.js"} <= names
    found = next(item for item in discover_extensions(STATIC_DIR) if item["id"] == "continuity")
    assert found == {"id": "continuity", "script": "/gui/static/extensions/continuity/index.js",
                     "style": "/gui/static/extensions/continuity/style.css"}
    assert gui_extensions.check_extension_code(STATIC_DIR) >= 8 and gui_extensions.check_extension_styles(STATIC_DIR) >= 1
    assert {key for key in extension_help(STATIC_DIR) if key.startswith("continuity.")} == {
        "continuity.view", "continuity.slot", "continuity.usage", "continuity.wait", "continuity.delegate", "continuity.later",
        "continuity.refresh"}
    seen = check_extensions(STATIC_DIR)
    assert seen["extensions"] >= 1 and seen["controls"] >= 8 and seen["sections"] >= 2


def test_the_stylesheet_is_light_dark_and_phone_safe():
    text = re.sub(r"/\*.*?\*/", "", (EXT / "style.css").read_text(encoding="utf-8"), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", text)          # colours are the variables of app.css (dark follows)
    assert "var(--panel)" in text and "var(--warn)" in text and "var(--line" in text
    assert "min-height: 44px" in text and "@media (max-width: 599.98px)" in text
    assert "overflow-wrap: anywhere" in text and "overflow-x" not in text           # long words wrap; nothing scrolls sideways
    assert "style=" not in "".join(path.read_text(encoding="utf-8") for path in EXT.glob("*.js"))


def test_the_extension_registers_a_screen_a_panel_section_and_a_tab_of_uso():
    index = re.sub(r"^\s*//.*$", "", ui_text((EXT / "index.js")), flags=re.M)
    assert 'registerView({ id: "continuity", owner: "continuity"' in index and 'registerSlot("continuity", ContinuitySlot' in index
    assert 'registerUsageSection({ id: "continuity", owner: "continuity"' in index and 'registerHelp(HELP, "continuity")' in index
    usage = ui_text((JS / "usage.js"))
    assert "registeredUsageSections()" in usage and "extraSection.component" in usage and "extensions/" not in usage
    for shared in JS.rglob("*.js"):
        imports = "".join(re.findall(r"""\bfrom\s+["']([^"']+)["']""", shared.read_text(encoding="utf-8")))
        assert "continuity" not in imports, shared.name


def test_the_card_is_a_labelled_group_with_pressed_buttons_a_confirmation_and_no_cancel():
    card = ui_text((EXT / "card.js"))
    assert 'role="group" aria-labelledby=${titleId}' in card and "aria-pressed=" in card and 'role="img" aria-label="Reloj' in card
    assert 'role=${message.kind === "error" ? "alert" : "status"}' in card and 'role="status">${view.decision}' in card
    assert 'data-help-exempt="confirm"' in card and "Escape" in card and "Volver" in card         # Esc goes back; the focus starts on «Volver»
    assert "Cancelar" not in card                                                                    # the contract has no «Cancelar»
    assert "apiPost(decisionPath(card.task_id)" in card and card.count("apiPost(") == 1             # one place writes, after the confirmation
    for text in ("Decidir después", "Decidir ahora"):
        assert text in card


# --- 6. the words and rules of the client, run in Node --------------------------------------------------------------------------

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         env={**os.environ, "TZ": "UTC"})
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def uri(path: Path) -> str:
    return json.dumps(path.as_uri())


CARD = {
    "task_id": "CNT-T06", "group_id": "CNT", "title": "Fix the parser", "revision": 7, "attempt": 1, "executor_id": "claude_cli",
    "app": "Claude", "model": "claude-sonnet-5-5", "level": "T2", "session_id": "s-1", "session_ref": "claude:s-1",
    "reason": "plan_limit", "window": "five_hour", "mode": "semi_auto", "outcome": "wait_until_reset",
    "reset": {"at": "2026-10-11T16:10:00Z", "source": "app_epoch", "confidence": "high", "zone": "UTC",
              "resume_after": "2026-10-11T16:12:00Z", "open_ended": False, "reported_at": None},
    "retry": None, "since": "2026-10-11T12:00:00Z",
    "checkpoint": {"saved": True, "patch": True, "patch_size": 900, "files_total": 3, "reason": None},
    "handoffs": {"used": 0, "max": 1}, "decision": "pending", "candidate": None, "decided_at": None, "blocked": None,
    "can_wait": True, "handoffs_exhausted": False,
    "candidates": [{"executor_id": "codex_cli", "app": "Codex", "model": "gpt-6.1", "level": "T3", "mode": "automatic", "policy": "no_ask",
                    "available": True, "paused_until": None, "sends": "el sobre de la tarea y el parche de 3 archivos"}],
}


@needs_node
def test_the_card_words_follow_the_contract():
    out = run_node(f"""
      import * as m from {uri(EXT / "model.js")};
      const card = {json.dumps(CARD)};
      const now = new Date("2026-10-11T12:30:00Z");
      const view = m.describeCard(card, now);
      const other = (changes) => m.describeCard({{ ...card, ...changes }}, now);
      console.log(JSON.stringify({{
        view, tree: m.treeText(card, now),
        creditless: other({{ reason: "credits_exhausted", reset: {{ ...card.reset, at: null, resume_after: null, open_ended: true }}, can_wait: false }}),
        rate: other({{ reason: "rate_limited", reset: {{ ...card.reset, at: null, resume_after: "2026-10-11T12:31:00Z" }} }}),
        unknown: other({{ reason: "limit_unknown", window: "unknown", reset: {{ ...card.reset, source: "configured", confidence: "low" }} }}),
        tomorrow: m.clockText("2026-10-12T03:40:00Z", now), later: m.clockText("2026-10-15T03:40:00Z", now),
        bad: [m.clockText(null, now), m.clockText("no es una fecha", now), m.whenText("", now)],
        nopatch: other({{ checkpoint: {{ saved: true, patch: false, files_total: null, reason: "not_git" }} }}).checkpoint,
        nosave: other({{ checkpoint: {{ saved: false, patch: false }} }}).checkpoint,
        delegated: other({{ decision: "delegate", candidate: "codex_cli", decided_at: "2026-10-11T12:20:00Z" }}).decision,
        waited: other({{ decision: "wait" }}).decision,
        exhausted: other({{ candidates: [], handoffs: {{ used: 1, max: 1 }}, handoffs_exhausted: true }}),
        body: m.decisionBody(card, "delegate", "codex_cli", "id-1"), waitBody: m.decisionBody(card, "wait", "ignored", "id-2"),
        path: m.decisionPath("A B/1"), confirmWait: m.confirmText(card, {{ kind: "wait" }}, now),
        confirmDelegate: m.confirmText(card, {{ kind: "delegate", executor_id: "codex_cli" }}, now),
      }}));
    """)
    view = out["view"]
    assert view["title"] == "Claude · claude-sonnet-5-5 alcanzó su límite de sesión (5 horas)."
    assert view["reset"] == "Se restablece a las 16:10 (hora local · fuente: aviso de la app, hora exacta)."
    assert view["checkpoint"] == "Avance guardado: parche de 3 archivos." and view["handoffs"] == "Traspasos 0/1"
    assert view["task"] == "Tarea CNT-T06 · Fix the parser" and view["wait"] == "Esperar hasta 16:10" and view["decision"] is None
    assert view["candidates"] == [{"id": "codex_cli", "label": "Delegar a Codex · gpt-6.1 · T3", "available": True,
                                   "note": "Se envía el sobre de la tarea y el parche de 3 archivos."}]
    assert out["tree"] == "En espera · hasta 16:10"
    assert out["creditless"]["title"] == "Claude · claude-sonnet-5-5 se quedó sin créditos."
    assert out["creditless"]["reset"] == "Restablecimiento no disponible: solo tú puedes reanudar la tarea." and out["creditless"]["wait"] is None
    assert out["rate"]["title"].endswith("demasiadas solicitudes seguidas.") and out["rate"]["reset"] == "Se reintenta solo a las 12:31 (hora local)."
    assert out["unknown"]["title"].endswith("alcanzó un límite de uso.") and "hora estimada" in out["unknown"]["reset"]
    assert "pausa configurada" in out["unknown"]["reset"]
    assert out["tomorrow"] == "mañana 03:40" and re.fullmatch(r"[a-zéáíóú]{3,4}\.? \d{1,2} [a-z]{3,5}\.? 03:40", out["later"])
    assert out["bad"] == [None, None, None]
    assert out["nopatch"] == "Avance guardado sin parche (la carpeta de trabajo no es un repositorio git)."
    assert out["nosave"] == "Sin avance guardado."
    assert out["delegated"].startswith("Pediste delegar a Codex (12:20).") and "no se reanudará sola" in out["delegated"]
    assert out["waited"] == "Decidiste esperar hasta 16:10. La tarea se reanuda sola poco después de esa hora."
    assert out["exhausted"]["candidates"] == [] and out["exhausted"]["exhausted"] is True
    assert out["body"] == {"decision_id": "id-1", "decision": "delegate", "expected_revision": 7, "executor_id": "codex_cli"}
    assert out["waitBody"] == {"decision_id": "id-2", "decision": "wait", "expected_revision": 7}
    assert out["path"] == "/gui/api/tasks/A%20B%2F1/continuity/decision"
    assert out["confirmWait"]["yes"] == "Sí, esperar" and "No se envía nada" in " ".join(out["confirmWait"]["lines"])
    assert out["confirmDelegate"]["title"] == "¿Delegar a Codex · gpt-6.1 · T3?"
    assert "El historial de la conversación no viaja" in " ".join(out["confirmDelegate"]["lines"])


@needs_node
def test_the_policy_rows_and_the_error_texts():
    out = run_node(f"""
      import * as m from {uri(EXT / "model.js")};
      const now = new Date("2026-10-11T12:30:00Z");
      const policy = {{ mode: "semi_auto", wait_threshold_hours: 5, max_handoffs: 1, auto_scope: "project", source: "default", candidates: [
        {{ executor_id: "a", app: "Claude", enabled: true, available: true, paused_until: null, policy: "no_ask" }},
        {{ executor_id: "b", app: "Copilot", enabled: true, available: false, paused_until: "2026-10-11T18:00:00Z", policy: "ask" }},
        {{ executor_id: "c", app: "Codex", enabled: false, available: false, paused_until: null, policy: "never" }},
        {{ executor_id: "d", app: "X", enabled: true, available: false, paused_until: null, paused_open_ended: true, policy: "mystery" }}] }};
      const error = (status, code, reason) => m.decisionErrorText({{ status, code, details: reason ? {{ reason }} : {{}}, message: "boom" }});
      console.log(JSON.stringify({{
        rows: m.policyRows(policy), candidates: m.policyCandidates(policy, now), manual: m.policyRows({{ ...policy, mode: "manual", wait_threshold_hours: 1 }})[0],
        errors: [error(409, "invalid_state", "stale_revision"), error(409, "invalid_state", "not_held"), error(404, "not_found"),
                 error(0, "network_error"), error(401, "unauthorized"), error(500, "http_500"), m.decisionErrorText(null)],
        stale: [m.isStale({{ status: 409 }}), m.isStale({{ status: 404 }}), m.isStale({{ status: 500 }}), m.isStale(null)],
      }}));
    """)
    assert out["rows"][0] == ["Modo", "Semiautomático. Espera solo si el límite se restablece pronto; si no, te propone otro agente y te pregunta."]
    assert out["rows"][1] == ["Umbral de espera", "5 horas: si el límite tarda más en restablecerse, se propone otro agente."]
    assert out["rows"][2] == ["Traspasos máximos", "1 por tarea"] and out["rows"][3][1] == "Nunca se traspasa una tarea mientras se integra."
    assert out["rows"][4][1].startswith("Solo se activa por proyecto")
    assert out["manual"][1].startswith("Manual. Siempre te pregunta")
    assert [(c["app"], c["state"], c["policy"]) for c in out["candidates"]] == [
        ("Claude", "Disponible", "Sin preguntar"), ("Copilot", "En pausa hasta 18:00", "Pregunta antes"),
        ("Codex", "Desactivado", "Nunca"), ("X", "En pausa sin hora de fin", "Sin dato")]
    assert out["errors"][0].startswith("La tarea cambió desde que se dibujó la tarjeta") and out["errors"][1].startswith("La tarea ya no espera")
    assert out["errors"][2].startswith("La tarea ya no existe") and out["errors"][3].startswith("No se pudo contactar")
    assert out["errors"][4].startswith("El token de acceso") and out["errors"][5] == "No se pudo guardar la decisión: boom"
    assert out["errors"][6] == "No se pudo guardar la decisión." and out["stale"] == [True, True, False, False]


@needs_node
def test_the_card_draws_the_facts_the_buttons_and_the_confirmation():
    out = run_node(f"""
      import {{ CardView }} from {uri(EXT / "card.js")};
      const text = (v) => v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("") : typeof v === "object" ? text(v.props?.children) : String(v);
      const all = (v) => v == null ? [] : Array.isArray(v) ? v.flatMap(all) : typeof v === "object" ? [v, ...all(v.props?.children)] : [];
      const card = {json.dumps(CARD)};
      const now = new Date("2026-10-11T12:30:00Z");
      const draw = (changes = {{}}, ui = {{}}, extra = {{}}) => CardView({{ card: {{ ...card, ...changes }}, ui, now, ...extra }});
      const buttons = (tree) => all(tree).filter((n) => n.type === "button").map((n) => ({{ text: text(n), pressed: n.props["aria-pressed"], disabled: Boolean(n.props.disabled), described: n.props["aria-describedby"] || "" }}));
      const pending = draw();
      const waiting = draw({{ decision: "wait" }});
      const delegated = draw({{ decision: "delegate", candidate: "codex_cli", blocked: "delegation_requested" }});
      const confirming = draw({{}}, {{ confirming: {{ kind: "wait", decisionId: "d1" }} }});
      const later = draw({{}}, {{ later: true }});
      const nothing = draw({{ can_wait: false, candidates: [], reset: {{ ...card.reset, at: null, resume_after: null, open_ended: true }} }});
      const paused = draw({{ candidates: [{{ ...card.candidates[0], available: false, paused_until: "2026-10-11T18:00:00Z" }}] }});
      const failed = draw({{}}, {{}}, {{ message: {{ kind: "error", text: "No se pudo" }} }});
      console.log(JSON.stringify({{
        pendingText: text(pending), pendingButtons: buttons(pending), waitingButtons: buttons(waiting), delegatedButtons: buttons(delegated),
        waitingText: text(waiting), delegatedText: text(delegated),
        group: [pending.props.role, pending.props["aria-labelledby"], pending.props.tabindex, pending.props["data-decision"]],
        title: all(pending).find((n) => n.type === "h4").props.id, confirmType: all(confirming).some((n) => n.type?.name === "ConfirmBox"),
        confirmButtons: buttons(confirming).map((b) => b.text), laterButtons: buttons(later).map((b) => b.text), laterText: text(later),
        nothingText: text(nothing), nothingButtons: buttons(nothing).map((b) => b.text), pausedButtons: buttons(paused), pausedText: text(paused),
        link: all(pending).find((n) => n.type === "a").props.href, failed: all(failed).find((n) => n.props?.role === "alert")?.props.class,
        helps: all(pending).filter((n) => n.type?.name === "Help").map((n) => [n.props.topic, n.props.scope, n.props.compact]),
        clock: all(pending).find((n) => n.props?.role === "img").props["aria-label"],
      }}));
    """)
    t = out["pendingText"]
    assert "Claude · claude-sonnet-5-5 alcanzó su límite de sesión (5 horas)." in t
    assert "Se restablece a las 16:10 (hora local · fuente: aviso de la app, hora exacta)." in t
    assert "Avance guardado: parche de 3 archivos." in t and "Traspasos 0/1" in t
    assert "El historial de la conversación no viaja a otra aplicación." in t
    assert [b["text"] for b in out["pendingButtons"]] == ["Esperar hasta 16:10", "Delegar a Codex · gpt-6.1 · T3", "Decidir después"]
    assert all(b.get("pressed") in (None, "false") and b["described"].startswith("help-continuity-") for b in out["pendingButtons"])
    assert [b.get("pressed") for b in out["waitingButtons"]] == ["true", "false", None] and "Decidiste esperar hasta 16:10." in out["waitingText"]
    assert [b.get("pressed") for b in out["delegatedButtons"]] == ["false", "true", None]
    assert "Pediste delegar a Codex" in out["delegatedText"]
    assert out["group"] == ["group", out["title"], "-1", "pending"] and out["title"] == "continuity-card-CNT-T06-title"
    assert out["confirmType"] is True and out["confirmButtons"] == []                    # the choices are replaced by the confirmation box
    assert out["laterButtons"] == ["Decidir ahora"] and "Sin decidir: la tarea sigue en espera" in out["laterText"]
    assert out["nothingButtons"] == ["Decidir después"] and "No hay otra opción" in out["nothingText"]
    assert "Restablecimiento no disponible" in out["nothingText"]
    assert out["pausedButtons"][1]["disabled"] is True and "En pausa hasta 18:00." in out["pausedText"]
    assert out["link"] == "#sessions/claude%3As-1" and out["failed"] == "message error"
    assert ["continuity.wait", "continuity.delegate", "continuity.later"] == [h[0] for h in out["helps"]] and all(h[2] for h in out["helps"])
    assert out["clock"].startswith("Reloj")


@needs_node
def test_the_extension_loads_through_the_page_loader_and_registers_everything():
    manifest = [item for item in discover_extensions(STATIC_DIR) if item["id"] == "continuity"]
    out = run_node(f"""
      import {{ pathToFileURL }} from "node:url";
      import * as registry from {uri(JS / "extensions.js")};
      import {{ parseRoute }} from {uri(JS / "route.js")};
      const base = {json.dumps(str(STATIC_DIR))};
      const report = await registry.loadExtensions({{
        fetchManifest: async () => ({{ extensions: {json.dumps(manifest)} }}),
        importModule: (target) => import(pathToFileURL(base + target.replace("/gui/static", "")).href),
        addStylesheet: async () => {{}},
      }});
      const view = registry.registeredViews()[0];
      const slot = registry.registeredSlots()[0];
      const usage = registry.registeredUsageSections()[0];
      const refused = [];
      for (const bad of [{{ id: "plans", label: "x", component() {{}} }}, {{ id: "continuity", label: "x", component() {{}} }},
                         {{ id: "Bad", label: "x", component() {{}} }}, {{ id: "ok", label: "", component() {{}} }}, {{ id: "ok", label: "x" }}]) {{
        try {{ registry.registerUsageSection(bad); }} catch (error) {{ refused.push(String(error.message).split(":")[0]); }}
      }}
      console.log(JSON.stringify({{
        report, view: [view.id, view.label], slot: [slot.name, slot.title, slot.topic], usage: [usage.id, usage.label],
        route: parseRoute("#continuity"), help: registry.registeredHelp("continuity.wait").title, refused,
        reserved: registry.RESERVED_USAGE_SECTIONS,
      }}));
    """)
    assert out["report"] == {"loaded": ["continuity"], "failed": []}
    assert out["view"] == ["continuity", "Decisiones"] and out["slot"] == ["continuity", "Continuidad", "continuity.slot"]
    assert out["usage"] == ["continuity", "Continuidad"] and out["route"] == {"section": "continuity", "id": None}
    assert out["help"] == "Esperar hasta la hora indicada" and out["reserved"] == ["plans", "global", "costs"]
    assert out["refused"] == ["registerUsageSection"] * 5          # reserved, duplicate, bad id, empty label, no component


@needs_node
def test_uso_orders_the_tabs_that_extensions_register():
    out = run_node(f"""
      import * as registry from {uri(JS / "extensions.js")};
      const before = registry.registeredUsageSections().length;
      registry.registerUsageSection({{ id: "b_tab", label: "B", component() {{}}, order: 20 }});
      registry.registerUsageSection({{ id: "a_tab", label: "A", component() {{}}, order: 20 }});
      registry.registerUsageSection({{ id: "z_tab", label: "Z", component() {{}}, order: 5 }});
      console.log(JSON.stringify({{ before, order: registry.registeredUsageSections().map((s) => s.id) }}));
    """)
    assert out == {"before": 0, "order": ["z_tab", "a_tab", "b_tab"]}


# --- 7. the documentation -------------------------------------------------------------------------------------------------------

def test_the_gui_document_describes_the_screen_the_routes_and_the_usage_tab():
    docs = (Path(__file__).resolve().parents[1] / "docs" / "GUI.md").read_text(encoding="utf-8")
    assert "registerUsageSection" in docs and "## Continuity in the GUI" in docs
    for text in ("/gui/api/continuity/decisions", "/gui/api/tasks/{task_id}/continuity/decision", "continuity.decided",
                 "delegation_requested", "never acts for the person"):
        assert text in docs, text
