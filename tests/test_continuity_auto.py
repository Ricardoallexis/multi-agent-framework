# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""USO-T08: the automatic continuity, a switch only the person turns on in Usage.

The pure policy (``handoff.decide`` with ``auto``), the settings and the route that keep the switch, and the service that carries
a hand-off out use the fake executors of ``test_quota_handoff``: no real Claude, Codex or Copilot is ever started.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import test_quota_handoff as q
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices, ServiceError, handoff
from multiagent.services.gui_settings import GUI_SETTINGS_FILE

SINCE = q.z(q.at(-1))                         # the switch was turned on an hour before the fake limits below
AUTO = handoff.Policy("auto", 5, 1, automatic_since=SINCE, base_mode="semi_auto")
AUTO_MANUAL_BASE = handoff.Policy("auto", 5, 1, automatic_since=SINCE, base_mode="manual")
APPS = {"claude_cli": "no_ask", "codex_cli": "no_ask", "copilot_cli": "ask"}


def a_hold(**fields):
    return q.the_hold(**{"since": q.z(q.NOW), **fields})


def unreliable(**fields):
    return a_hold(outcome="wait_fixed_pause", reset_confidence="low", **fields)


def settings_body(mode="semi_auto", hours=5, candidates=None, **extra):
    return {"mode": mode, "wait_threshold_hours": hours, "candidates": dict(candidates or APPS), **extra}


# --------------------------------------------------------------------------- the pure policy

@pytest.mark.parametrize("name, hold, candidates, extra, expected", [
    # no waiting for the threshold: any limit that is not money goes at once to a candidate that may be asked nothing
    ("close-reset", a_hold(reset_at=q.z(q.at(1)), resume_after=q.z(q.at(1, 2))), q.AUTO_OK, {}, ("delegate", "auto_limit_reached")),
    ("far-reset", a_hold(), q.AUTO_OK, {}, ("delegate", "auto_limit_reached")),
    ("no-reliable-time", unreliable(), q.AUTO_OK, {}, ("delegate", "auto_limit_reached")),
    ("limit-unknown", a_hold(reason="limit_unknown", outcome="open_ended", reset_at=None, resume_after=None, open_ended=True),
     q.AUTO_OK, {}, ("delegate", "auto_limit_reached")),
    ("rate-limited-after-retries", unreliable(reason="rate_limited"), q.AUTO_OK, {}, ("delegate", "auto_limit_reached")),
    # still waits: a short retry of a 429 and a person who chose to wait on the card
    ("retry", a_hold(reason="rate_limited", outcome="wait_retry", reset_at=None, reset_confidence=None), q.AUTO_OK, {},
     ("wait", "rate_limited_retry")),
    ("person-waits", a_hold(decision="wait", decided_by="user"), q.AUTO_OK, {}, ("wait", "decided:wait:user")),
    # still asks: money, a blocked hold, what stops any automatic hand-off, and hand-offs used up
    ("credits", a_hold(reason="credits_exhausted", outcome="open_ended", reset_at=None, resume_after=None, open_ended=True),
     q.AUTO_OK, {}, ("ask", "credits_exhausted")),
    ("blocked", a_hold(blocked="failed_resumes"), q.AUTO_OK, {}, ("ask", "blocked:failed_resumes")),
    ("private", a_hold(), q.AUTO_OK, {"private": True}, ("ask", "no_candidate_without_asking")),
    ("no-patch", a_hold(), q.AUTO_OK, {"patch": False}, ("ask", "no_candidate_without_asking")),
    ("budget", a_hold(), q.AUTO_OK, {"budget": False}, ("ask", "no_candidate_without_asking")),
    ("session-open", a_hold(), q.AUTO_OK, {"session_open": True}, ("ask", "no_candidate_without_asking")),
    ("exhausted-reliable", a_hold(), q.AUTO_OK, {"handoffs": 1}, ("ask", "handoffs_exhausted")),
    ("exhausted-unreliable", unreliable(), q.AUTO_OK, {"handoffs": 1}, ("ask", "handoffs_exhausted")),
    # no app qualifies: waits for a reliable reset, asks when there is none, and never picks an app by itself
    ("candidate-asks", a_hold(), [q.candidate("copilot_cli", policy="ask")], {}, ("wait", "no_candidate_without_asking")),
    ("candidate-never", a_hold(), [q.candidate(policy="never")], {}, ("wait", "no_candidate_without_asking")),
    ("paid-api", a_hold(), [q.candidate("paid_api", surface="gemini_api")], {}, ("wait", "no_candidate_without_asking")),
    ("level-above", a_hold(), [q.candidate(within_level=False, level=None, model=None)], {}, ("wait", "no_candidate_without_asking")),
    ("other-folder", a_hold(), [q.candidate(same_folder=False)], {}, ("wait", "no_candidate_without_asking")),
    ("no-candidates", a_hold(), [], {}, ("wait", "no_candidate_without_asking")),
    ("paid-api-no-reset", unreliable(), [q.candidate("paid_api", surface="gemini_api")], {}, ("ask", "no_candidate_without_asking")),
    ("candidate-asks-no-reset", unreliable(), [q.candidate("copilot_cli", policy="ask")], {}, ("ask", "no_candidate_without_asking")),
    ("no-candidates-no-reset", unreliable(), [], {}, ("ask", "no_candidate_without_asking")),
    # something that is not a limit hold is not for this module
    ("condition", a_hold(reason="dependency", condition={"type": "time"}), q.AUTO_OK, {}, ("none", "not_a_limit_hold")),
])
def test_each_row_of_the_automatic_policy(name, hold, candidates, extra, expected):
    decision = q.decided(AUTO, hold, candidates, **extra)
    assert (decision.action, decision.why) == expected, decision
    if decision.action == "delegate":
        assert decision.candidate == "codex_cli" and decision.by == "policy:auto"
    if decision.action in ("wait", "ask") and expected[1] == "no_candidate_without_asking":
        assert decision.action != "delegate" and decision.candidate in (None, "copilot_cli", "paid_api", "codex_cli")


def test_the_automatic_policy_never_hands_over_without_a_qualifying_app():
    for item in (q.candidate("copilot_cli", policy="ask"), q.candidate(policy="never"),
                 q.candidate("paid_api", surface="gemini_api"), q.candidate(within_level=False, level=None, model=None),
                 q.candidate(within_level=None, level=None, model=None), q.candidate(same_folder=False),
                 q.candidate(enabled=False), q.candidate(paused=True), q.candidate(used=True)):
        for hold in (a_hold(), unreliable(), a_hold(reset_at=q.z(q.at(1)), resume_after=q.z(q.at(1, 2)))):
            assert q.decided(AUTO, hold, [item]).action != "delegate", (item, hold)


def test_it_picks_the_best_app_marked_no_ask():
    items = [q.candidate("copilot_cli", policy="ask", level="T3"), q.candidate("codex_cli", level="T2"),
             q.candidate("claude_cli", level="T3")]
    decision = q.decided(AUTO, a_hold(), items)
    assert (decision.action, decision.candidate) == ("delegate", "claude_cli")


def test_a_limit_from_before_the_switch_keeps_the_saved_mode_and_its_card():
    close = a_hold(since=q.z(q.at(-2)), reset_at=q.z(q.at(1)), resume_after=q.z(q.at(1, 2)))
    far = a_hold(since=q.z(q.at(-2)))
    semi = q.decided(AUTO, close, q.AUTO_OK)
    assert (semi.action, semi.why, semi.by) == ("wait", "reset_within_threshold", "policy:semi_auto")
    assert q.decided(AUTO, far, q.AUTO_OK).why == "reset_beyond_threshold"                   # what semi_auto already did
    manual = q.decided(AUTO_MANUAL_BASE, close, q.AUTO_OK)
    assert (manual.action, manual.why, manual.by) == ("ask", "manual_mode", "policy:manual")
    no_stamp = q.decided(AUTO, {key: value for key, value in close.items() if key != "since"}, q.AUTO_OK)
    assert no_stamp.by == "policy:semi_auto"                                                  # no start time: the old way
    assert q.decided(handoff.Policy("auto", 5, 1), a_hold(), q.AUTO_OK).why == "manual_mode"  # no switch instant: the strictest


def test_a_task_only_restricts_the_automatic_policy():
    assert handoff.resolve_policy(AUTO, None).mode == "auto"
    assert handoff.resolve_policy(AUTO, {"mode": "auto"}).mode == "auto"
    assert handoff.resolve_policy(AUTO, {"mode": "semi_auto"}).mode == "semi_auto"
    assert handoff.resolve_policy(AUTO, {"mode": "manual"}).mode == "manual"
    # the saved mode behind the switch is a ceiling too: a task cannot lift manual to semi-automatic through ``auto``
    assert handoff.resolve_policy(AUTO_MANUAL_BASE, {"mode": "semi_auto"}).mode == "manual"
    assert handoff.resolve_policy(AUTO_MANUAL_BASE, None).mode == "auto"
    # and it cannot turn the switch on for itself
    assert handoff.resolve_policy(handoff.Policy("semi_auto", 5, 1), {"mode": "auto"}).mode == "semi_auto"
    assert handoff.resolve_policy(handoff.Policy("manual", 5, 1), {"mode": "auto"}).mode == "manual"
    restricted = handoff.resolve_policy(AUTO, {"max_handoffs": 1, "wait_threshold_hours": 24})
    assert restricted.mode == "auto" and restricted.automatic_since == SINCE and restricted.base_mode == "semi_auto"
    assert q.decided(handoff.resolve_policy(AUTO, {"mode": "semi_auto"}), a_hold(), q.AUTO_OK).why == "reset_beyond_threshold"
    assert q.decided(handoff.resolve_policy(AUTO, {"mode": "manual"}), a_hold(), q.AUTO_OK).why == "manual_mode"


def test_the_policy_view_tells_the_switch_only_while_it_is_on():
    assert "automatic" not in handoff.Policy("semi_auto", 5, 1).to_dict()
    assert AUTO.to_dict()["automatic"] is True and AUTO.to_dict()["automatic_since"] == SINCE


# --------------------------------------------------------------------------- the settings and the route

@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False, gui_token_file=tmp_path / "gui-token",
                        continuity_mode="manual")
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(services=services, client=client, auth={"Authorization": f"Bearer {token}"}, settings=settings,
                              file=settings.local_dir / GUI_SETTINGS_FILE, api="/gui/api/settings/continuity")
    services.runs.close()


def saved(env):
    return json.loads(env.file.read_text(encoding="utf-8")).get("continuity")


def test_without_the_key_everything_is_as_before(env):
    first = env.client.get(env.api, headers=env.auth).json()
    assert first["automatic"] is False and first["automatic_since"] is None and first["mode"] == "manual"
    assert first["source"] == "environment" and not env.file.exists()
    saved_now = env.client.put(env.api, json=settings_body("semi_auto"), headers=env.auth).json()
    assert saved_now["automatic"] is False and saved_now["automatic_since"] is None and saved_now["mode"] == "semi_auto"
    assert saved(env) == settings_body("semi_auto")                                          # no switch key was written
    assert env.services.executors.continuity_policy()["mode"] == "semi_auto"
    assert "automatic" not in env.services.executors.continuity_policy()


def test_a_file_from_before_this_version_leaves_the_switch_off(env):
    env.file.parent.mkdir(parents=True, exist_ok=True)
    env.file.write_text(json.dumps({"schema": 1, "continuity": settings_body("manual")}), encoding="utf-8")
    answer = env.client.get(env.api, headers=env.auth).json()
    assert answer["source"] == "gui" and answer["automatic"] is False and answer["mode"] == "manual"


def test_the_switch_is_turned_on_by_the_gui_user_and_the_server_stamps_the_instant(env):
    on = env.client.put(env.api, json=settings_body("manual", automatic=True), headers=env.auth)
    assert on.status_code == 200, on.text
    body = on.json()
    assert body["automatic"] is True and body["mode"] == "auto" and body["saved_mode"] == "manual"
    assert body["source"] == "gui" and body["max_handoffs"] == 1
    since = body["automatic_since"]
    assert since.endswith("Z") and since[:2] == "20"
    assert saved(env) == settings_body("manual", automatic=True, automatic_since=since)
    policy = env.services.executors.continuity_policy()
    assert policy["mode"] == "auto" and policy["automatic"] is True and policy["base_mode"] == "manual"
    # the state survives a new reading (a restart) and it is the same one
    assert env.client.get(env.api, headers=env.auth).json() == body
    # a later save without the key (another field changed) leaves the switch and its instant as they are
    again = env.client.put(env.api, json=settings_body("semi_auto", hours=24), headers=env.auth).json()
    assert again["automatic"] is True and again["automatic_since"] == since and again["saved_mode"] == "semi_auto"
    assert again["wait_threshold_hours"] == 24 and again["mode"] == "auto"
    same = env.client.put(env.api, json=settings_body("semi_auto", hours=24, automatic=True), headers=env.auth).json()
    assert same["automatic_since"] == since                                                  # not stamped again


def test_turning_it_off_brings_back_the_saved_mode(env):
    env.client.put(env.api, json=settings_body("semi_auto", automatic=True), headers=env.auth)
    assert env.services.executors.continuity_policy()["mode"] == "auto"
    off = env.client.put(env.api, json=settings_body("semi_auto", automatic=False), headers=env.auth).json()
    assert off["automatic"] is False and off["automatic_since"] is None and off["mode"] == "semi_auto"
    assert saved(env) == settings_body("semi_auto")                                          # the keys are gone, not false
    assert env.services.executors.continuity_policy()["mode"] == "semi_auto"
    on_again = env.client.put(env.api, json=settings_body("semi_auto", automatic=True), headers=env.auth).json()
    assert on_again["automatic"] is True and on_again["automatic_since"] >= "2026"            # a new instant


@pytest.mark.parametrize("extra", [
    {"automatic": "yes"}, {"automatic": 1}, {"automatic": 0}, {"automatic": None, "automatic_since": "2026-01-01T00:00:00Z"},
    {"automatic_since": "2026-01-01T00:00:00Z"}, {"mode": "auto"}, {"max_handoffs": 3}, {"overage": True},
    {"allow_paid_api": True},
])
def test_the_route_rejects_anything_else(env, extra):
    body = {**settings_body("manual"), **extra}
    assert env.client.put(env.api, json=body, headers=env.auth).status_code == 422
    assert not env.file.exists()


def test_the_route_needs_the_token(env):
    assert env.client.put(env.api, json=settings_body(automatic=True)).status_code == 401
    assert not env.file.exists()


def test_only_the_gui_user_can_turn_it_on(env):
    gui = env.services.gui_settings
    body = settings_body("semi_auto", automatic=True, automatic_since=SINCE)
    for actor in ("agent:planner", "executor:claude_cli", "user", "gui", "policy:auto", ""):
        with pytest.raises(ServiceError) as caught:
            gui.set_usage("continuity", body, actor=actor)
        assert caught.value.status == 422 and caught.value.details["field"] == "actor"
    assert not env.file.exists()
    assert gui.set_usage("continuity", body, actor="user:gui")["automatic"] is True
    # nothing else writes it: the settings object has no other way in, and the saved block is checked on every read
    assert "automatic" not in {key for key in Settings.model_fields if key.startswith("continuity")}
    assert not hasattr(gui, "set_continuity")


def test_the_environment_cannot_turn_it_on(tmp_path, monkeypatch):
    kwargs = dict(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                  gemini_api_key="", openai_api_key="", openai_enabled=False)
    monkeypatch.setenv("CONTINUITY_MODE", "auto")                  # the environment cannot ask for ``auto``
    with pytest.raises(ValueError):
        Settings(**kwargs)
    with pytest.raises(ValueError):
        Settings(_env_file=None, continuity_mode="auto")
    monkeypatch.delenv("CONTINUITY_MODE")
    for name in ("CONTINUITY_AUTOMATIC", "MULTIAGENT_CONTINUITY_AUTOMATIC", "AUTOMATIC"):
        monkeypatch.setenv(name, "true")                           # and a variable of that name is not read at all
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    resolved = ApplicationServices.create(settings, dry_run=True).gui_settings.resolve("continuity")
    assert resolved["automatic"] is False and resolved["mode"] in ("manual", "semi_auto")


@pytest.mark.parametrize("block", [
    {"automatic": True}, {"automatic": True, "automatic_since": "not a time"}, {"automatic": True, "automatic_since": 5},
    {"automatic": True, "automatic_since": "2026-01-01T00:00:00"}, {"automatic": False, "automatic_since": SINCE},
    {"automatic": "true", "automatic_since": SINCE},
])
def test_a_file_with_a_malformed_switch_is_ignored_and_leaves_it_off(env, block):
    env.file.parent.mkdir(parents=True, exist_ok=True)
    env.file.write_text(json.dumps({"schema": 1, "continuity": settings_body("manual", **block)}), encoding="utf-8")
    answer = env.client.get(env.api, headers=env.auth).json()
    assert answer["automatic"] is False and answer["source"] == "environment" and answer["mode"] == "manual"


# --------------------------------------------------------------------------- the service

def turn_on(world, *, mode="semi_auto", since=None, candidates=None):
    body = settings_body(mode, candidates=candidates, automatic=True, automatic_since=q.z(since or q.at(-1)))
    world.services.gui_settings.set_usage("continuity", body)


def turn_off(world, *, mode="semi_auto", candidates=None):
    world.services.gui_settings.set_usage("continuity", settings_body(mode, candidates=candidates))


def test_with_it_on_the_task_goes_at_once_to_the_app_marked_no_ask(world):
    services = world.services
    turn_on(world)
    first = q.hit_limit(world, reset=q.at(1))                  # a close reset: semi_auto would wait for it
    out = services.executors.decide_holds()
    (done,) = out["delegated"]
    assert done["to_executor"] == "codex_cli" and done["by"] == "policy:auto" and done["why"] == "auto_limit_reached"
    assert done["executed"] is True and out["waiting"] == [] and out["asked"] == []
    task = services.tasks.get_task("T1")
    assert task["assignee"] == "executor:codex_cli" and task["continuity"]["handoffs"] == 1
    assert [call.kind for call in q.calls(world, "codex_cli")] == ["launch"] and len(q.calls(world, "claude_cli")) == 1
    # the notice: the limit, the origin, the destination and who decided
    (handed,) = q.events(services, "continuity.handoff", "T1")
    payload = handed["payload"]
    assert handed["ref"] == "codex_cli" and handed["actor"] == "policy:auto" and payload["by"] == "policy:auto"
    assert payload["from"] == "claude_cli" and payload["from_model"] == "claude-big"
    assert payload["reason"] == "plan_limit" and payload["window"] == "five_hour" and payload["reset_at"] == q.z(q.at(1))
    assert payload["model"] == "codex-big" and payload["to_surface"] == "codex_cli"
    assert payload["session_retired"] == first["session_id"] and payload["handoff_no"] == 1
    (decided,) = q.events(services, "continuity.decided", "T1")
    assert decided["actor"] == "policy:auto" and decided["payload"]["by"] == "policy:auto"


def test_with_it_on_it_also_goes_without_a_reliable_time_or_a_known_limit(world):
    services = world.services
    turn_on(world)
    world.fakes["claude_cli"].script.append({"limit": q.limit("plan_limit", reset_at=None)})
    out = services.executors.run_task("T1", "claude_cli", model="claude-big", effort="high")
    assert out["outcome"] == "quota_exhausted"
    q.give_patch(world, "T1")
    assert world.hold()["outcome"] != "wait_until_reset"
    (done,) = services.executors.decide_holds()["delegated"]
    assert done["to_executor"] == "codex_cli" and done["why"] == "auto_limit_reached" and done["executed"] is True
    # a limit the app did not name, with no time at all
    services.executors.lift_pause("claude_cli", reason="the test needs the executor again")
    world.fakes["claude_cli"].script.append({"limit": q.limit("limit_unknown", reset_at=None)})
    out = services.executors.run_task("T2", "claude_cli", model="claude-big", effort="high")
    assert out["outcome"] == "quota_exhausted"
    q.give_patch(world, "T2")
    assert world.hold("T2")["reason"] == "limit_unknown"
    (done,) = services.executors.decide_holds(task_id="T2")["delegated"]
    assert done["to_executor"] == "codex_cli" and done["why"] == "auto_limit_reached" and done["executed"] is True


def test_with_it_off_a_close_reset_is_still_waited_for_and_a_far_one_goes_by_semi_auto(world):
    services = world.services
    q.hit_limit(world, reset=q.at(1))
    out = services.executors.decide_holds()
    assert [item["why"] for item in out["waiting"]] == ["reset_within_threshold"] and out["delegated"] == []
    q.hit_limit(world, "T2", reset=q.at(9), executor="claude_cli", model="claude-big")
    (done,) = services.executors.decide_holds()["delegated"]
    assert done["by"] == "policy:semi_auto" and done["why"] == "reset_beyond_threshold"


def test_turning_it_off_brings_back_the_saved_mode_in_the_service(world):
    services = world.services
    turn_on(world, mode="manual")
    assert services.executors.continuity_policy()["mode"] == "auto"
    turn_off(world, mode="manual")
    assert services.executors.continuity_policy()["mode"] == "manual"
    q.hit_limit(world)
    out = services.executors.decide_holds()
    assert [item["why"] for item in out["asked"]] == ["manual_mode"] and out["delegated"] == []


def test_a_limit_from_before_the_switch_keeps_its_card(world):
    services = world.services
    q.hit_limit(world, reset=q.at(9))                         # held at NOW (12:00)
    turn_on(world, since=q.at(1), mode="manual")              # the switch comes later
    out = services.executors.decide_holds()
    assert [item["why"] for item in out["asked"]] == ["manual_mode"] and out["delegated"] == []
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli"
    # a limit held after the switch is new: it goes
    world.clock.to(q.at(2))
    q.hit_limit(world, "T2", reset=q.at(11), executor="claude_cli", model="claude-big")
    (done,) = services.executors.decide_holds(task_id="T2")["delegated"]
    assert done["by"] == "policy:auto" and done["to_executor"] == "codex_cli"


def test_it_does_not_go_to_an_app_marked_ask_first_or_never(world):
    services = world.services
    turn_on(world, candidates={"claude_cli": "no_ask", "codex_cli": "ask", "copilot_cli": "ask"})
    q.hit_limit(world)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and [item["why"] for item in out["waiting"]] == ["no_candidate_without_asking"]
    assert services.tasks.get_task("T1")["assignee"] == "executor:claude_cli" and q.calls(world, "codex_cli") == []
    turn_on(world, candidates={"claude_cli": "no_ask", "codex_cli": "never", "copilot_cli": "ask"})
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and q.calls(world, "codex_cli") == []


def test_it_asks_with_no_patch_or_a_private_project_or_credits_or_an_open_session(world, monkeypatch):
    services = world.services
    turn_on(world)
    q.hit_limit(world, patch=False)
    out = services.executors.decide_holds()
    assert out["delegated"] == [] and out["asked"][0]["ask"] == ["no_patch"] and out["asked"][0]["candidate"] == "codex_cli"
    q.give_patch(world)
    monkeypatch.setattr(services.executors, "_private_project", lambda task, conn=None: True)
    assert services.executors.decide_holds()["asked"][0]["ask"] == ["private_project"]
    monkeypatch.undo()
    monkeypatch.setattr(services.usage, "budgets", lambda **kwargs: {"status": [
        {"budget_id": "month", "state": "limit_reached", "mode": "warn"}]})
    assert services.executors.decide_holds()["asked"][0]["ask"] == ["budget"]
    monkeypatch.undo()
    q.hit_limit(world, "T2", kind="credits_exhausted")
    out = services.executors.decide_holds(task_id="T2")
    assert out["delegated"] == [] and out["asked"][0]["why"] == "credits_exhausted"
    assert q.calls(world, "codex_cli") == []
    # a session the person has open in the app is not retired behind their back
    q.hit_limit(world, "T3", executor="claude_cli", model="claude-big")
    services.executors.record_app_open(world.hold("T3")["session_id"], source="claude")
    out = services.executors.decide_holds(task_id="T3")
    assert out["delegated"] == [] and "session_open_in_app" in out["asked"][0]["ask"]
    assert q.calls(world, "codex_cli") == []


def test_it_hands_over_once_and_then_asks(world):
    services = world.services
    turn_on(world)
    q.hit_limit(world)
    world.fakes["codex_cli"].script.append({"limit": q.limit(reset_at=q.at(9))})
    (done,) = services.executors.decide_holds()["delegated"]
    assert done["to_executor"] == "codex_cli"
    out = services.executors.decide_holds()                    # the delegate ran out too: the one hand-off is used
    assert out["delegated"] == [] and [item["why"] for item in out["asked"]] == ["handoffs_exhausted"]
    assert len(q.events(services, "continuity.handoff", "T1")) == 1
    assert services.executors.continuity_policy()["max_handoffs"] == 1


def test_it_never_hands_over_during_an_integration_and_never_cuts_a_running_step(world):
    services = world.services
    turn_on(world)
    q.hit_limit(world)
    services.executors.integration_active = lambda: True
    out = services.executors.decide_holds()
    assert out["integration"] is True and out["delegated"] == [] and q.calls(world, "codex_cli") == []
    q.refused(lambda: services.executors.handoff_task("T1", "codex_cli"), "integration_in_progress")
    services.executors.integration_active = lambda: False
    # a step that is running is not a held task: only a paused one with a limit hold is ever looked at
    running = services.tasks.get_task("T2")
    out = services.executors.decide_holds()
    assert out["held"] == 1 and [item["task_id"] for item in out["delegated"]] == ["T1"]
    after = services.tasks.get_task("T2")
    assert (after["state"], after["assignee"], after["revision"]) == (running["state"], running["assignee"], running["revision"])


def test_it_never_goes_to_a_paid_api(tmp_path):
    world = q.make_world(tmp_path, apps=("claude_cli", "paid_api"))
    turn_on(world, candidates={"claude_cli": "no_ask", "codex_cli": "no_ask", "copilot_cli": "ask"})
    q.hit_limit(world)
    out = world.services.executors.decide_holds()
    assert out["delegated"] == [] and q.calls(world, "paid_api") == []
    assert world.services.tasks.get_task("T1")["assignee"] == "executor:claude_cli"


def test_the_session_card_shows_the_last_hand_off(tmp_path):
    world = q.make_world(tmp_path, gui_token_file=tmp_path / "gui-token")
    services = world.services
    token = ensure_token(tmp_path / "gui-token")
    auth = {"Authorization": f"Bearer {token}"}
    turn_on(world)
    first = q.hit_limit(world, reset=q.at(1))
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        route = "/gui/api/continuity/session/claude:" + first["session_id"]
        before = client.get(route, headers=auth).json()
        assert before["held"] is True and "last_handoff" not in before
        services.executors.decide_holds()
        after = client.get(route, headers=auth).json()
        assert after["held"] is False and after["card"] is None
        notice = after["last_handoff"]
        assert notice["automatic"] is True and notice["by"] == "policy:auto" and notice["task_id"] == "T1"
        assert notice["limit"] == {"reason": "plan_limit", "window": "five_hour", "executor_id": "claude_cli", "app": "Claude",
                                   "model": "claude-big", "reset_at": q.z(q.at(1))}
        assert notice["to"] == {"executor_id": "codex_cli", "app": "Codex", "model": "codex-big"} and notice["at"]
        other = client.get("/gui/api/continuity/session/claude:00000000-0000-4000-8000-000000000000", headers=auth).json()
        assert other == {"held": False, "session_id": "claude:00000000-0000-4000-8000-000000000000", "card": None}
        policy = client.get("/gui/api/continuity/policy", headers=auth).json()
        assert policy["automatic"] is True and policy["mode"] == "auto" and policy["automatic_since"] == SINCE
        assert "auto" in policy["modes"]


def test_a_hand_off_a_person_decided_is_not_called_automatic(tmp_path):
    world = q.make_world(tmp_path, gui_token_file=tmp_path / "gui-token")
    services = world.services
    token = ensure_token(tmp_path / "gui-token")
    first = q.hit_limit(world)
    q.card_decides(world, "codex_cli")
    services.executors.decide_holds()
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        notice = client.get("/gui/api/continuity/session/claude:" + first["session_id"],
                            headers={"Authorization": f"Bearer {token}"}).json()["last_handoff"]
    assert notice["automatic"] is False and notice["by"] == "user"


@pytest.fixture
def world(tmp_path):
    return q.make_world(tmp_path)
