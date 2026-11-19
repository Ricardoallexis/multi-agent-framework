# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVA-T06: the availability pieces working together, with fake programs and no network.

One long attempt of a task goes through the whole path of the stage: the caller is answered ``running`` inside the wait
(AVA-T02), the person writes to the inbox of the task through the GUI route and the attempt reads it at its own tick without
approving anything (AVA-T03, AVA-T05), the attempt ends, the next one crosses the context threshold and is handed to a new
session only at that boundary (AVA-T04), and the task ends submitted. The policy is turned on only inside each test's own
temporary services (the ``background`` fixture of ``test_background_runs``); the default stays off and a test checks it.

No real Claude, Codex or Copilot is started, no model is called and the waits are the small limits of that fixture.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.services import availability
from test_background_runs import (  # noqa: F401  (the fixtures are used by name)
    background, events_of, finished_wait, patient, policy,
)
from test_context_handoff import measure
from test_executor_services import (  # noqa: F401  (the fixtures are used by name)
    attempt_events, claude_out, event_types, never_a_real_cli, refused, world,
)
from test_user_inbox import ANA, CONSUMED_EVENT, INBOX_ACTOR, inbox_events, until

LONG_RUN = 2.5      # seconds the first attempt takes; the caller is answered after a fraction of that


def gui(services, tmp_path):
    """The GUI client of these services, with its own token file (the route of AVA-T05 posts as ``user:operator``).

    It is not used as a context manager: leaving the app's lifespan would shut the background attempts down.
    """
    services.settings.gui_token_file = tmp_path / "gui-token"
    token = ensure_token(services.settings.gui_token_file)
    client = TestClient(create_app(services=services), base_url="http://localhost")
    return client, {"Authorization": f"Bearer {token}"}


def test_a_long_run_answers_running_reads_the_person_hands_over_at_the_boundary_and_ends(background, monkeypatch, tmp_path):
    services = background.services
    background.fake.set("claude", sleep=LONG_RUN, stdout=claude_out("no envelope here"))     # ends without a result: paused

    # 1. The caller is not blocked: ``running`` comes inside the wait, long before the program ends.
    started = time.monotonic()
    out = services.executors.run_task("T1", "claude_cli")
    assert time.monotonic() - started < LONG_RUN - 0.4
    assert out["outcome"] == "running" and out["background"] == {"max_wait_seconds": 0.3, "heartbeat_seconds": 0.2}
    attempt = services.executors.background.get("T1")
    until(lambda: attempt._handle is not None, what="the program to start")

    # 2. The person writes a note while it runs (the GUI route, with the actor fixed by the server); nothing is read by the post.
    client, headers = gui(services, tmp_path)
    assert client.get("/gui/api/inbox/policy", headers=headers).json()["enabled"] is True
    posted = client.post("/gui/api/tasks/T1/messages", headers=headers,
                         json={"message_id": "n1", "kind": "note", "text": "FYI: the spec changed"})
    assert posted.status_code == 200 and posted.json()["actor"] == "user:operator" and posted.json()["seq"] == 1

    # 3. The attempt reads it at its own tick: marked as read, the program and the task are left alone.
    (read,) = until(lambda: inbox_events(services, "T1", CONSUMED_EVENT), what="the note to be read")
    assert read["actor"] == INBOX_ACTOR and read["payload"]["action"] == "noted" and read["ref"] == "n1"
    assert services.tasks.get_task("T1")["state"] == "running" and not attempt._handle.done()
    assert client.get("/gui/api/tasks/T1/messages", headers=headers).json()[0]["state"] == "consumed"

    finished_wait(services)
    first = services.tasks.get_task("T1")
    assert first["state"] == "paused" and first["attempt"] == 1
    (failed,) = attempt_events(services, "T1")
    assert failed["outcome"] == "failed"
    assert len(events_of(services, "T1", "executor.heartbeat")) >= 1              # it was alive while it ran
    sequence = event_types(services, "T1")
    assert sequence.index("user.message") < sequence.index("executor.attempt_finished")
    assert services.executors.background.active() == []

    # 4. The next attempt is the boundary: the measure is over the threshold, so it is handed to a new session, once.
    background.fake.set("claude", stdout=claude_out())
    measure(monkeypatch, background, ratio=.71)
    resumed = services.executors.resume_task("T1")
    handoff = resumed["context_handoff"]
    assert resumed["mode"] == "launch" and resumed["outcome"] == "submitted"        # a new session, not a resume
    assert handoff["level"] == "threshold" and handoff["ratio"] == .71
    assert handoff["checkpoint"]["saved"] and handoff["snapshot_ref"] == "snapshot-test"
    assert handoff["inbox_cursor"] == 1                                              # the new session reads after the note
    assert resumed["session_id"] != handoff["retired_session"]
    assert "--resume" not in background.fake.calls("claude")[-1]["argv"]
    assert [e["payload"]["reason"] for e in events_of(services, "T1", "session.retired")] == ["context_threshold"]
    assert len(events_of(services, "T1", "task.checkpointed")) == 1

    # 5. What the person writes from now on is what the new session reads; the task ended submitted, by its own attempt.
    client.post("/gui/api/tasks/T1/messages", headers=headers, json={"message_id": "n2", "kind": "note", "text": "later"})
    assert [m["message_id"] for m in services.inbox.messages("task", "T1", after=handoff["inbox_cursor"])] == ["n2"]
    assert services.tasks.get_task("T1")["state"] == "submitted"
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["failed", "submitted"]
    order = event_types(services, "T1")
    assert order.index("user.message") < order.index("session.retired") < len(order) - 1
    assert order[-2:] == ["executor.attempt_finished", "user.message"]               # the end, then the later note

    # Nothing in the whole path approved, decided or accepted anything for the person.
    assert not [name for name in order if "approv" in name or "accepted" in name or "decision" in name]
    assert services.executors.background.active() == []


def test_a_cancel_written_before_the_boundary_stops_the_hand_over_and_starts_nothing(background, patient, monkeypatch):
    services = background.services
    services.executors.availability = patient
    background.fake.set("claude", stdout=claude_out("no envelope here"))
    assert services.executors.run_task("T1", "claude_cli")["outcome"] == "failed"      # fast: paused with its session
    measure(monkeypatch, background, ratio=.9)
    services.inbox.post("task", "T1", message_id="c1", kind="cancel", text="", actor=ANA)

    refused(lambda: services.executors.resume_task("T1"), reason="inbox_cancelled")

    assert events_of(services, "T1", "session.retired") == [] and events_of(services, "T1", "task.checkpointed") == []
    assert services.tasks.get_task("T1")["state"] == "paused"                          # nothing was claimed or started
    assert [e["outcome"] for e in attempt_events(services, "T1")] == ["failed"]


def test_with_the_default_settings_the_policy_is_off_and_the_whole_path_stays_as_before(world, tmp_path):
    services = world.services
    assert availability.policy_from_settings(Settings(_env_file=None)) == availability.DISABLED
    assert availability.policy_from_settings(services.settings) == availability.DISABLED
    assert services.executors.availability is None

    out = services.executors.run_task("T1", "claude_cli")                              # the caller waits for the attempt
    assert out["outcome"] == "submitted" and "background" not in out
    assert events_of(services, "T1", "executor.heartbeat") == [] and services.executors.background.active() == []

    client, headers = gui(services, tmp_path)
    assert client.get("/gui/api/inbox/policy", headers=headers).json()["enabled"] is False
    denied = client.post("/gui/api/tasks/T1/messages", headers=headers, json={"message_id": "n1", "kind": "note", "text": "x"})
    assert denied.status_code == 409 and denied.json()["detail"]["details"]["reason"] == "availability_disabled"
    assert services.inbox.messages("task", "T1") == []
