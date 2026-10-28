"""BDI-T04: the confirmation of "Abrir en Claude" is a gate of effects (contract BDI-T01 section 8).

Opening the app is an ``open_application`` effect. While no document policy is active nothing changes (the launch
is the one GUI-T06 tests cover). With one active, ``launch_claude_desktop`` asks ``launch_safety.require_effect``
before it looks for the program: the route builds the evidence from the confirmation the person gave (the request
that the second step of the screen sends) as an approval of the user limited to that one session, and a call that
brings no evidence is refused with ``effect_not_authorized`` (403) before anything starts or is recorded.

Nothing real is started: ``Popen`` is faked by the fixtures of ``test_gui_open_in_app.py``.
"""
from __future__ import annotations

import pytest

from multiagent.document_actions import Action, ActionKind, Approval, authorize
from multiagent.document_trust import DetectionPolicy, Provenance, SourceKind
from multiagent.gui import open_in_app
from multiagent.services.errors import ServiceError
from test_gui_open_in_app import CLAUDE_ID, SID, launcher, no_real_launch, post, world  # noqa: F401  (fixtures by name)

OPEN = ActionKind.OPEN_APPLICATION


def opened_events(world) -> list:
    return [event for event in world.services.tasks.events() if event["type"] == "session.opened_in_app"]


def summary_of(world) -> dict:
    return world.services.sessions.get_session(SID)


def test_without_a_policy_the_launch_is_as_it_was(world, launcher):
    assert world.services.executors.document_policy is None
    response = post(world)
    assert response.status_code == 200 and response.json()["launched"] is True and len(launcher) == 1
    assert len(opened_events(world)) == 1


@pytest.mark.parametrize("policy", [DetectionPolicy.QUARANTINE_WARN, DetectionPolicy.PAUSE])
def test_with_a_policy_the_confirmation_of_the_person_is_the_evidence(world, launcher, policy):
    world.services.system.engine.document_policy = policy
    assert world.services.executors.document_policy is policy
    response = post(world)
    assert response.status_code == 200 and response.json()["session_id"] == SID
    assert len(launcher) == 1 and len(opened_events(world)) == 1


def test_a_request_that_brings_no_evidence_is_refused_before_anything_starts_or_is_recorded(world, launcher, monkeypatch):
    world.services.system.engine.document_policy = DetectionPolicy.PAUSE
    monkeypatch.setattr(open_in_app, "confirmed_open_approval", lambda summary: ())
    response = post(world)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "effect_not_authorized"
    assert "no evidence" in response.json()["detail"]["message"]
    assert launcher == [] and opened_events(world) == []


def test_the_confirmation_is_an_approval_of_the_user_limited_to_that_session(world):
    summary = summary_of(world)
    (approval,) = open_in_app.confirmed_open_approval(summary)
    assert isinstance(approval, Approval) and approval.provenance.kind == "user" and approval.provenance.trust.value == "trusted"
    assert approval.provenance.ref == f"gui:open-in-app:{SID}" and approval.grants == {OPEN} and approval.targets == {SID}
    assert authorize(Action(OPEN, SID), [approval]).allowed
    assert not authorize(Action(OPEN, f"claude:{'0' * 8}-0000-4000-8000-{'0' * 12}"), [approval]).allowed
    assert not authorize(Action(ActionKind.WRITE, SID), [approval]).allowed
    assert not authorize(Action(ActionKind.LAUNCH_EXECUTOR, SID), [approval]).allowed


@pytest.mark.parametrize("summary", [{}, {"id": None}, {"id": ""}, {"id": "claude: with spaces"}, {"id": 5}])
def test_a_summary_that_cannot_name_a_safe_reference_gives_no_evidence(summary):
    assert open_in_app.confirmed_open_approval(summary) == ()


def test_the_launch_function_refuses_without_evidence_or_with_evidence_for_another_session(world, launcher):
    summary = summary_of(world)
    for evidence in (None, (), [Approval(Provenance(SourceKind.USER.value, ref="chat:message-7"), frozenset({OPEN}),
                                        frozenset({f"claude:{'0' * 8}-0000-4000-8000-{'0' * 12}"}))],
                     [Approval(Provenance("document", ref="doc://notes.md"), frozenset({OPEN}))]):
        with pytest.raises(ServiceError) as caught:
            open_in_app.launch_claude_desktop(summary, allow=True, policy=DetectionPolicy.PAUSE, evidence=evidence)
        assert (caught.value.code, caught.value.status) == ("effect_not_authorized", 403)
    assert launcher == []
    # With the right evidence it launches; with no policy the evidence is not even looked at.
    launched = open_in_app.launch_claude_desktop(summary, allow=True, policy=DetectionPolicy.PAUSE,
                                                 evidence=open_in_app.confirmed_open_approval(summary))
    assert launched["launched"] is True and len(launcher) == 1
    open_in_app._last_launch = None
    assert open_in_app.launch_claude_desktop(summary, allow=True)["launched"] is True and len(launcher) == 2


def test_the_checks_that_were_there_still_come_first(world, launcher, monkeypatch):
    world.services.system.engine.document_policy = DetectionPolicy.PAUSE
    monkeypatch.setattr(open_in_app, "confirmed_open_approval", lambda summary: ())
    summary = summary_of(world)
    with pytest.raises(ServiceError) as caught:                         # the setting that turns launching off
        open_in_app.launch_claude_desktop(summary, allow=False, policy=DetectionPolicy.PAUSE, evidence=None)
    assert caught.value.code == "launch_disabled"
    with pytest.raises(ServiceError) as caught:                         # not a Claude session
        open_in_app.launch_claude_desktop({**summary, "source": "codex"}, allow=True, policy=DetectionPolicy.PAUSE,
                                          evidence=None)
    assert caught.value.code == "unsupported_source" and launcher == []
