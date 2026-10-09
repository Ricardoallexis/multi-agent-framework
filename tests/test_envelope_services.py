# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Envelope services: context, envelopes, blobs, results and cursors (ORQ-T25)."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from multiagent.config import Settings
from multiagent.services import ApplicationServices, EnvelopeServices, ServiceError
from multiagent.tasks.envelopes import TASK_ENVELOPE_FIELDS, envelope_sha256

PATCH = b"diff --git a/src/a.py b/src/a.py\n+print('a')\n"


def make_services(local: Path) -> ApplicationServices:
    local.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None, local_dir=local, worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True)


@pytest.fixture
def services(tmp_path: Path) -> ApplicationServices:
    services = make_services(tmp_path / "local")
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    services.tasks.create_task("G", "A", "Task A", actor="user", objective="Build A", file_scope=["src/a.py"])
    services.tasks.create_task("G", "B", "Task B", actor="user", depends_on=["A"], file_scope=["src/b.py"])
    return services


def error(call) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value


def start(services, task_id: str = "A", agent: str = "agent:a") -> None:
    for action in ("claim", "start"):
        services.tasks.transition(task_id, action, actor=agent)


def patch_result(services, summary: str = "Built A.") -> dict:
    blob = services.envelopes.put_blob(PATCH, actor="agent:a", media_type="text/x-patch")
    return {"summary": summary, "patch": {"blob": f"sha256:{blob['sha256']}", "files": ["src/a.py"]}}


def event_types(services, after: int = 0) -> list[str]:
    return [event["type"] for event in services.tasks.events(after=after)]


def head(services) -> int:
    return services.envelopes.get_cursor("probe")["head"]


def test_the_service_is_part_of_the_facade(services):
    assert isinstance(services.envelopes, EnvelopeServices)


# -------- task context --------
def test_set_task_context_changes_only_given_fields_bumps_revision_and_emits_an_event(services):
    env = services.envelopes
    before = services.tasks.get_task("B")["revision"]
    task = env.set_task_context("B", actor="user", base_commit="abc1234",
                                context_refs=["doc://README.md", "doc://README.md", "report://r1"],
                                constraints=["no push"])
    assert task["revision"] == before + 1
    assert (task["base_commit"], task["context_refs"], task["constraints"]) == (
        "abc1234", ["doc://README.md", "report://r1"], ["no push"])
    task = env.set_task_context("B", actor="user", constraints=[])
    assert task["base_commit"] == "abc1234" and task["context_refs"] == ["doc://README.md", "report://r1"]
    assert task["constraints"] == []
    last = services.tasks.events(after=0)[-1]
    assert (last["type"], last["task_id"], last["payload"]) == ("task.context_updated", "B", {"fields": ["constraints"]})


@pytest.mark.parametrize(("kwargs", "field"), [
    ({}, "context"),
    ({"base_commit": "HEAD"}, "base_commit"),
    ({"context_refs": "doc://README.md"}, "context_refs"),
    ({"context_refs": ["http://example.com/x"]}, "context_refs"),
    ({"context_refs": ["doc:///etc/passwd"]}, "context_refs"),
    ({"context_refs": [f"doc://d{i}.md" for i in range(21)]}, "context_refs"),
    ({"constraints": ["x" * 201]}, "constraints"),
    ({"constraints": [f"c{i}" for i in range(21)]}, "constraints"),
])
def test_set_task_context_rejects_invalid_input_without_changes(services, kwargs, field):
    seq = head(services)
    caught = error(lambda: services.envelopes.set_task_context("B", actor="user", **kwargs))
    assert (caught.code, caught.details["field"]) == ("invalid_request", field)
    assert head(services) == seq


def test_set_task_context_on_missing_or_closed_tasks(services):
    env = services.envelopes
    assert error(lambda: env.set_task_context("NOPE", actor="user", constraints=[])).code == "not_found"
    services.tasks.transition("B", "cancel", actor="user")
    caught = error(lambda: env.set_task_context("B", actor="user", constraints=[]))
    assert (caught.code, caught.details["status"]) == ("invalid_state", "cancelled")
    start(services)                                   # an open, claimed or running task can change
    assert env.set_task_context("A", actor="user", constraints=["c"])["constraints"] == ["c"]
    services.tasks.transition("A", "submit", actor="agent:a")
    with services.tasks.store.transaction() as conn:  # as run_gate does while its checks run
        services.tasks._apply(conn, "A", "gate_start", actor="user", gate=True, event="gate.started")
    caught = error(lambda: env.set_task_context("A", actor="user", constraints=[]))
    assert (caught.code, caught.details["status"]) == ("invalid_state", "validating")


# -------- envelope --------
def test_envelope_carries_dependencies_by_reference_and_the_task_context(services):
    env = services.envelopes
    env.set_task_context("B", actor="user", base_commit="abc1234", context_refs=["doc://docs/TASKS.md"],
                         constraints=["no push"])
    services.gates.set_group_gate("G", ["tests"], actor="user")
    envelope = env.get_envelope("B")
    assert tuple(envelope) == TASK_ENVELOPE_FIELDS
    assert envelope["objective"] == "Task B"              # no objective: the title
    assert envelope["status"] == "blocked" and envelope["assignee"] is None and envelope["attempt"] == 0
    assert envelope["depends_on"] == [{"task_id": "A", "requires": "accepted", "status": "ready"}]
    assert envelope["artifact_refs"] == [] and envelope["gate"] == ["command:test"]
    assert (envelope["base_commit"], envelope["context_refs"], envelope["constraints"]) == (
        "abc1234", ["doc://docs/TASKS.md"], ["no push"])
    assert envelope["file_scope"] == ["src/b.py"] and envelope["events_after"] == head(services)

    start(services)
    result = patch_result(services)
    env.submit_result("A", result, actor="agent:a")
    envelope = env.get_envelope("B")
    assert envelope["depends_on"] == [{"task_id": "A", "requires": "accepted", "status": "submitted",
                                       "result_ref": "result://A/1"}]
    assert envelope["artifact_refs"] == [f"blob:{result['patch']['blob']}"]
    assert PATCH.decode() not in str(envelope)             # by reference, never by content


def test_envelope_known_hash_answers_unchanged_until_something_happens(services):
    env = services.envelopes
    envelope = env.get_envelope("A")
    digest = envelope_sha256(envelope)
    assert env.get_envelope("A", known_hash=digest) == {"unchanged": True, "hash": digest,
                                                        "revision": envelope["revision"]}
    assert env.get_envelope("A", known_hash="0" * 64) == envelope
    services.tasks.transition("A", "claim", actor="agent:a")
    changed = env.get_envelope("A", known_hash=digest)
    assert "unchanged" not in changed and changed["status"] == "claimed"


def test_envelope_events_after_is_read_before_the_data(services, monkeypatch):
    env = services.envelopes
    snapshot = services.tasks._snapshot

    def snapshot_after_a_concurrent_change(conn):
        # Another writer claims A between the reads: the envelope may miss the change,
        # but then events_after must be before its event so that a reader still sees it.
        monkeypatch.setattr(services.tasks, "_snapshot", snapshot)
        services.tasks.transition("A", "claim", actor="agent:a")
        return snapshot(conn)

    monkeypatch.setattr(services.tasks, "_snapshot", snapshot_after_a_concurrent_change)
    envelope = env.get_envelope("B")
    claimed = [e for e in services.tasks.events(after=0) if e["type"] == "task.claimed"][0]
    assert envelope["events_after"] < claimed["seq"]


def test_envelope_errors(services):
    env = services.envelopes
    assert error(lambda: env.get_envelope("NOPE")).code == "not_found"
    caught = error(lambda: env.get_envelope(["A"]))
    assert (caught.code, caught.details["field"]) == ("invalid_request", "task_id")
    caught = error(lambda: env.get_envelope("A", known_hash="abc"))
    assert (caught.code, caught.details["field"]) == ("invalid_request", "known_hash")


# -------- blobs and artifacts --------
def test_put_blob_stores_content_once_in_a_file(services):
    env = services.envelopes
    first = env.put_blob(PATCH, actor="agent:a", media_type="text/x-patch")
    sha = hashlib.sha256(PATCH).hexdigest()
    assert first == {"ref": f"blob:sha256:{sha}", "sha256": sha, "size": len(PATCH), "media_type": "text/x-patch",
                     "created": True}
    again = env.put_blob(bytearray(PATCH), actor="agent:b", expected_sha256=f"sha256:{sha}")
    assert again["created"] is False and again["media_type"] == "text/x-patch"
    assert env.store.blob_path(sha).read_bytes() == PATCH


@pytest.mark.parametrize(("content", "kwargs", "field"), [
    ("text", {}, "content"),
    (b"x", {"media_type": "Text/Plain"}, "media_type"),
    (b"x", {"media_type": "plain"}, "media_type"),
    (b"x", {"expected_sha256": "0" * 64}, "expected_sha256"),
])
def test_put_blob_rejects_invalid_input(services, content, kwargs, field):
    caught = error(lambda: services.envelopes.put_blob(content, actor="agent:a", **kwargs))
    assert (caught.code, caught.details["field"]) == ("invalid_request", field)


def test_put_blob_size_limit_is_configurable(services):
    env = services.envelopes
    env.blob_max_bytes = 4
    assert env.put_blob(b"1234", actor="agent:a")["size"] == 4
    caught = error(lambda: env.put_blob(b"12345", actor="agent:a"))
    assert (caught.code, caught.details["field"], caught.details["limit"]) == ("invalid_request", "content", 4)


def test_get_artifact_reads_text_by_ranges(services):
    env = services.envelopes
    text = "añb€c".encode()          # 1 + 2 + 1 + 3 + 1 bytes
    ref = env.put_blob(text, actor="agent:a", media_type="text/plain")["ref"]
    whole = env.get_artifact(ref)
    assert (whole["encoding"], whole["content"], whole["length"], whole["eof"]) == ("utf-8", "añb€c", 8, True)
    cut = env.get_artifact(ref, offset=0, length=5)   # would cut the euro sign
    assert (cut["content"], cut["length"], cut["eof"]) == ("añb", 4, False)
    rest = env.get_artifact(ref, offset=cut["length"])
    assert rest["content"] == "€c" and rest["eof"] is True
    assert env.get_artifact(ref, offset=8)["content"] == ""


def test_get_artifact_returns_binary_and_non_utf8_text_as_base64(services):
    env = services.envelopes
    data = bytes(range(256))
    ref = env.put_blob(data, actor="agent:a")["ref"]
    part = env.get_artifact(ref, offset=10, length=20)
    assert part["encoding"] == "base64" and base64.b64decode(part["content"]) == data[10:30]
    latin = env.put_blob("ñ".encode("latin-1"), actor="agent:a", media_type="text/plain")["ref"]
    assert env.get_artifact(latin)["encoding"] == "base64"


def test_get_artifact_errors(services):
    env = services.envelopes
    ref = env.put_blob(b"abc", actor="agent:a")["ref"]
    cases = [
        (lambda: env.get_artifact("doc://README.md"), "invalid_request"),
        (lambda: env.get_artifact("result://A/1"), "invalid_request"),
        (lambda: env.get_artifact("not a ref"), "invalid_request"),
        (lambda: env.get_artifact(ref, offset=4), "invalid_request"),
        (lambda: env.get_artifact(ref, length=0), "invalid_request"),
        (lambda: env.get_artifact(ref, length=1024 * 1024 + 1), "invalid_request"),
        (lambda: env.get_artifact("blob:sha256:" + "0" * 64), "not_found"),
    ]
    for call, code in cases:
        assert error(call).code == code


# -------- results --------
def test_submit_result_stores_it_and_submits_the_task_in_one_step(services):
    env = services.envelopes
    start(services)
    result = patch_result(services)
    seq = head(services)
    answer = env.submit_result("A", result, actor="agent:a")
    assert answer["duplicate"] is False and answer["task"]["state"] == "submitted"
    stored = answer["result"]
    assert (stored["ref"], stored["task_id"], stored["attempt"], stored["agent"]) == ("result://A/1", "A", 1, "agent:a")
    assert stored["summary"] == "Built A." and stored["decisions"] == []
    events = services.tasks.events(after=seq)
    assert [event["type"] for event in events] == ["task.submitted", "result.submitted"]
    submitted = events[-1]
    assert submitted["type"] == "result.submitted" and submitted["ref"] == "result://A/1"
    assert submitted["payload"] == {"attempt": 1, "result_ref": "result://A/1", "files_count": 1,
                                    "patch_blob": f"blob:{result['patch']['blob']}", "patch_size": len(PATCH)}
    assert "Built A." not in str(submitted["payload"])     # the event does not carry the summary


def test_submit_result_is_idempotent_and_rejects_other_content(services):
    env = services.envelopes
    start(services)
    result = patch_result(services)
    first = env.submit_result("A", result, actor="agent:a")
    seq = head(services)
    replay = env.submit_result("A", dict(result), actor="agent:a")
    assert replay["duplicate"] is True and replay["result"] == first["result"]
    assert head(services) == seq                          # nothing written
    caught = error(lambda: env.submit_result("A", {**result, "summary": "Other."}, actor="agent:a"))
    assert caught.code == "invalid_state" and caught.details["ref"] == "result://A/1"
    assert error(lambda: env.submit_result("A", {"summary": "No patch."}, actor="agent:a")).code == "invalid_state"
    assert len(env.list_results("A")) == 1


def test_submit_result_follows_the_submit_rules_and_writes_nothing_when_refused(services):
    env = services.envelopes
    result = {"summary": "Analysis only."}
    assert error(lambda: env.submit_result("NOPE", result, actor="agent:a")).code == "not_found"
    assert error(lambda: env.submit_result("A", result, actor="agent:a")).code == "invalid_state"   # never started
    start(services)
    caught = error(lambda: env.submit_result("A", result, actor="agent:z"))
    assert caught.code == "invalid_state" and caught.details["assignee"] == "agent:a"
    services.tasks.transition("A", "pause", actor="agent:a")
    assert error(lambda: env.submit_result("A", result, actor="agent:a")).code == "invalid_state"
    assert env.list_results("A") == [] and services.tasks.get_task("A")["state"] == "paused"


@pytest.mark.parametrize(("result", "field"), [
    ({"summary": ""}, "summary"),
    ({"summary": "x", "attempt": 2}, "attempt"),
    ({"summary": "x", "agent": "agent:b"}, "agent"),
    ({"summary": "x", "patch": {"blob": "sha256:" + "0" * 64, "files": ["src/a.py"]}}, "patch.blob"),
    ({"summary": "x", "patch": {"blob": "sha256:abc", "files": []}}, "patch.blob"),
    ({"summary": "x", "requests": [{"to": "user"}]}, "requests.what"),
])
def test_submit_result_validates_the_result(services, result, field):
    start(services)
    caught = error(lambda: services.envelopes.submit_result("A", result, actor="agent:a"))
    assert (caught.code, caught.details["field"]) == ("invalid_request", field)
    assert services.tasks.get_task("A")["state"] == "running"


def test_a_new_attempt_takes_a_new_result_and_plain_submit_still_works(services):
    env, tasks = services.envelopes, services.tasks
    start(services)
    env.submit_result("A", {"summary": "First try."}, actor="agent:a")
    tasks.transition("A", "request_changes", actor="user")
    tasks.transition("A", "start", actor="agent:a")
    env.submit_result("A", {"summary": "Second try."}, actor="agent:a")
    assert [item["ref"] for item in env.list_results("A")] == ["result://A/2", "result://A/1"]
    assert env.get_result("A")["summary"] == "Second try."
    assert env.get_result("A", attempt=1)["summary"] == "First try."
    assert env.get_result("result://A/1")["summary"] == "First try."
    tasks.create_task("G", "C", "Task C", actor="user")
    start(services, "C", "agent:c")
    assert tasks.transition("C", "submit", actor="agent:c")["state"] == "submitted"   # no result needed


def test_result_reads(services):
    env = services.envelopes
    start(services)
    result = patch_result(services)
    env.submit_result("A", result, actor="agent:a")
    full = env.get_result("result://A/1")
    assert full["patch"] == result["patch"] and len(full["result_sha256"]) == 64
    summary = env.list_results("A")[0]
    assert summary == {"ref": "result://A/1", "task_id": "A", "attempt": 1, "agent": "agent:a",
                       "summary": "Built A.", "patch_blob": f"blob:{result['patch']['blob']}", "files_count": 1,
                       "result_sha256": full["result_sha256"], "created_at": full["created_at"]}
    assert env.list_results("B") == []
    for call, code in [
        (lambda: env.get_result("B"), "not_found"),
        (lambda: env.get_result("result://A/2"), "not_found"),
        (lambda: env.get_result("NOPE"), "not_found"),
        (lambda: env.list_results("NOPE"), "not_found"),
        (lambda: env.get_result("doc://README.md"), "invalid_request"),
        (lambda: env.get_result("result://A/1", attempt=1), "invalid_request"),
        (lambda: env.get_result("A", attempt=0), "invalid_request"),
    ]:
        assert error(call).code == code


# -------- cursors --------
def test_cursor_reads_do_not_move_it_and_ack_does_never_backwards(services):
    env = services.envelopes
    top = head(services)
    assert env.get_cursor("agent:x") == {"consumer": "agent:x", "scope": "", "seq": 0, "head": top}
    first = env.read_events("agent:x", limit=2)
    assert [e["type"] for e in first["events"]] == ["group.created", "task.created"]
    assert (first["cursor"], first["head"], first["has_more"]) == (0, top, True)
    assert env.read_events("agent:x", limit=2) == first        # reading again: same events
    assert env.ack_events("agent:x", first["events"][-1]["seq"])["seq"] == 2
    rest = env.read_events("agent:x")
    assert rest["cursor"] == 2 and rest["has_more"] is False
    assert [e["seq"] for e in rest["events"]] == list(range(3, top + 1))
    assert env.ack_events("agent:x", 1)["seq"] == 2            # never backwards
    caught = error(lambda: env.ack_events("agent:x", top + 1))
    assert (caught.code, caught.details["field"]) == ("invalid_request", "seq")


def test_cursors_are_per_consumer_and_scope(services):
    env = services.envelopes
    services.tasks.create_group("H", "Other", actor="user")
    env.ack_events("agent:x", head(services))
    assert env.get_cursor("agent:y")["seq"] == 0
    assert env.get_cursor("agent:x", scope="group:G")["seq"] == 0
    in_group = env.read_events("agent:x", scope="group:G")["events"]
    assert in_group and all(e["group_id"] == "G" for e in in_group)
    in_task = env.read_events("agent:x", scope="task:B")["events"]
    assert in_task and all(e["task_id"] == "B" for e in in_task)


def test_read_events_filters_by_type_and_by_assignee(services):
    env = services.envelopes
    start(services)
    env.submit_result("A", {"summary": "Done."}, actor="agent:a")
    only = env.read_events("agent:x", types=["result.submitted"], limit=1)
    assert [e["type"] for e in only["events"]] == ["result.submitted"] and only["has_more"] is False
    mine = env.read_events("agent:a", assigned_to_me=True)["events"]
    assert mine and {e["task_id"] for e in mine} == {"A"}
    assert env.read_events("agent:nobody", assigned_to_me=True)["events"] == []


@pytest.mark.parametrize(("call", "field"), [
    (lambda env: env.get_cursor(""), "consumer"),
    (lambda env: env.get_cursor("x" * 101), "consumer"),
    (lambda env: env.get_cursor("agent:x", scope="team"), "scope"),
    (lambda env: env.get_cursor("agent:x", scope="task:bad id"), "scope"),
    (lambda env: env.read_events("agent:x", limit=0), "limit"),
    (lambda env: env.read_events("agent:x", limit=1001), "limit"),
    (lambda env: env.read_events("agent:x", types="task.created"), "types"),
    (lambda env: env.read_events("agent:x", types=3), "types"),
    (lambda env: env.read_events("agent:x", assigned_to_me="yes"), "assigned_to_me"),
    (lambda env: env.ack_events("agent:x", -1), "seq"),
    (lambda env: env.ack_events("agent:x", True), "seq"),
])
def test_cursor_errors(services, call, field):
    caught = error(lambda: call(services.envelopes))
    assert (caught.code, caught.details["field"]) == ("invalid_request", field)
