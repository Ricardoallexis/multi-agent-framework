# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Human review and recovery around deterministic workflow functions."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from multiagent import functions
from multiagent.functions import failure_detail, get_function, register_function
from multiagent.gui.threads_api import build_thread
from multiagent.run_service import PersonRequired

from backends.conformance import advance, backend_system, create_run, events, steps


CALLS: list[dict] = []
PRIVATE_FILE = "C:\\Users\\someone\\private\\data.csv"


class _ProcessDied(BaseException):
    """Stands for the process stopping while the function runs: nothing after the call is recorded."""


def _recover(context, arguments):
    CALLS.append(dict(context["steps"]))
    if len(CALLS) == 1:
        token = "sk" + "-" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
        raise ValueError(f"missing date column; token {token}")
    return {"steps": dict(context["steps"])}


def _dies_once(context, arguments):
    CALLS.append(dict(context["steps"]))
    if len(CALLS) == 1:
        raise _ProcessDied()
    return {"steps": dict(context["steps"])}


def _fails_twice(context, arguments):
    CALLS.append(dict(context["steps"]))
    if len(CALLS) <= 2:
        raise ValueError(f"bad row {len(CALLS)}")
    return {"steps": dict(context["steps"])}


def _missing_file(context, arguments):
    raise FileNotFoundError(2, "No such file or directory", PRIVATE_FILE)


for _id, _fn, _text in (
        ("test.function_review_recover", _recover, "Fail once and recover"),
        ("test.function_review_invalid", lambda context, arguments: {}, "Return an invalid output"),
        ("test.function_review_dies_once", _dies_once, "Stop the process on the first call"),
        ("test.function_review_fails_twice", _fails_twice, "Fail on the first two calls"),
        ("test.function_review_missing_file", _missing_file, "Fail with a local path in the message")):
    if get_function(_id) is None:
        register_function(_id, _fn, description=_text)


@pytest.fixture(params=["native", "mock"])
def system(request):
    return request.getfixturevalue("backend_system")


def _workflow(system, tmp_path, *, function="core.merge_outputs", checkpoint=False, max_iterations=None):
    source = (system.backend.workflows.workflows_dir / "function_steps.yaml").read_text(encoding="utf-8")
    document = yaml.safe_load(source)
    document["steps"][0]["checkpoint_after"] = checkpoint
    document["steps"][1]["function"] = function
    if max_iterations is not None:
        document["max_iterations"] = max_iterations
    folder = tmp_path / "workflows"
    folder.mkdir()
    (folder / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    system.backend.workflows.workflows_dir = folder
    return document


def _thread(system, run, document):
    services = SimpleNamespace(definitions=SimpleNamespace(read_workflow=lambda *args, **kwargs: document))
    return build_thread(services, run)


def test_supervised_reviews_inputs_and_outputs_before_one_call(system, tmp_path):
    document = _workflow(system, tmp_path)
    run = create_run(system, "function_steps", operating_mode="supervised")
    waiting = advance(system, run["id"])
    assert waiting["status"] == "waiting_human"
    assert waiting["waiting_reason"] == "function_review"
    assert waiting["waiting_step"] == "collect"
    assert len(waiting["artifacts"]) == 1
    assert not events(waiting, "step_started")[-1]["payload"].get("function")

    thread = _thread(system, waiting, document)
    entry = [entry for entry in thread["entries"] if entry.get("reason") == "function_review"][-1]
    assert entry["inputs"]["topic"] == "durable execution"
    assert entry["prior_outputs"]["first"] == waiting["artifacts"][0]["data"]
    assert entry["decision"]["actions"] == ["approve", "reject"]
    with pytest.raises(PersonRequired):
        system.service.approve(run["id"], recorded_only=True)
    assert system.service.approve(run["id"], person=True)["status"] == "queued"
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert len(final["artifacts"]) == 2
    assert len([event for event in events(final, "step_started")
                if event["payload"].get("function") == "core.merge_outputs"]) == 1


def test_autonomous_runs_function_without_extra_wait(system, tmp_path):
    _workflow(system, tmp_path)
    run = create_run(system, "function_steps", operating_mode="autonomous")
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert not events(final, "function_review_requested")


def test_invalid_function_output_has_recoverable_stable_code(system, tmp_path):
    _workflow(system, tmp_path, function="test.function_review_invalid")
    run = create_run(system, "function_steps", operating_mode="autonomous")
    waiting = advance(system, run["id"])
    assert waiting["status"] == "waiting_human"
    assert waiting["waiting_reason"] == "function_failure"
    assert len(waiting["artifacts"]) == 1
    assert events(waiting, "function_step_failed")[0]["payload"]["code"] == "function_output_invalid"


def test_failed_function_waits_for_explicit_model_correction(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_recover")
    run = create_run(system, "function_steps", operating_mode="supervised")
    first_wait = advance(system, run["id"])
    system.service.approve(run["id"], person=True)
    failed = advance(system, run["id"])
    assert failed["status"] == "waiting_human"
    assert failed["waiting_reason"] == "function_failure"
    assert failed["waiting_attempt"] == 1
    assert len(failed["artifacts"]) == 1
    assert len(CALLS) == 1
    with system.store.db.connect() as conn:
        rows = conn.execute("SELECT attempt,status FROM run_steps WHERE run_id=? AND step_id='collect'",
                            (run["id"],)).fetchall()
    assert [(row["attempt"], row["status"]) for row in rows] == [(1, "failed")]
    detail = events(failed, "function_step_failed")[0]["payload"]
    assert detail["code"] == "function_failed"
    assert "[REDACTED:" in detail["detail"]
    assert "Traceback" not in str(failed)
    assert "sk-AbCd" not in str(failed)
    assert _thread(system, failed, document)["entries"][-1]["decision"]["actions"] == ["changes", "reject"]
    with pytest.raises(ValueError):
        system.service.changes(run["id"], "")
    assert system.store.get_run(run["id"])["status"] == "waiting_human"
    system.service.changes(run["id"], "Correct the date column in the previous output")
    revised = advance(system, run["id"])
    assert revised["status"] == "waiting_human"
    assert revised["waiting_reason"] == "function_review"
    assert revised["waiting_attempt"] == 2
    assert len(revised["artifacts"]) == 2
    assert len(CALLS) == 1
    system.service.approve(run["id"], person=True)
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert len(CALLS) == 2
    assert [(item["step_id"], item["attempt"]) for item in final["artifacts"]] == [
        ("first", 1), ("first", 2), ("collect", 2),
    ]
    assert len(events(final, "function_step_failed")) == 1
    with system.store.db.connect() as conn:
        rows = conn.execute("SELECT attempt,status FROM run_steps WHERE run_id=? AND step_id='collect' ORDER BY attempt",
                            (run["id"],)).fetchall()
    assert [(row["attempt"], row["status"]) for row in rows] == [(1, "failed"), (2, "completed")]


def test_prior_checkpoint_still_works_in_autonomous_mode(system, tmp_path):
    _workflow(system, tmp_path, checkpoint=True)
    run = create_run(system, "function_steps", operating_mode="autonomous")
    waiting = advance(system, run["id"])
    assert waiting["waiting_reason"] == "review"
    assert waiting["waiting_step"] == "first"
    system.service.approve(run["id"])
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert not events(final, "function_review_requested")


def _function_starts(run):
    return [event for event in events(run, "step_started") if event["payload"].get("function")]


def _review_entry(system, run, document):
    return [item for item in _thread(system, run, document)["entries"] if item.get("reason") == "function_review"][-1]


def test_only_a_person_approves_the_function_review(system, tmp_path):
    _workflow(system, tmp_path)
    run = create_run(system, "function_steps", operating_mode="supervised")
    advance(system, run["id"])
    for flags in ({}, {"recorded_only": True}, {"recorded_only": True, "person": True}):
        with pytest.raises(PersonRequired):
            system.service.approve(run["id"], **flags)
        held = system.store.get_run(run["id"])
        assert (held["status"], held["waiting_reason"]) == ("waiting_human", "function_review")
        assert not events(held, "function_review_approved")
    assert not system.worker.process_once()
    assert not _function_starts(system.store.get_run(run["id"]))
    assert system.service.approve(run["id"], person=True)["status"] == "queued"
    assert advance(system, run["id"])["status"] == "completed"

    other = create_run(system, "function_steps", project_name="Second fixture", operating_mode="supervised")
    advance(system, other["id"])
    rejected = system.service.reject(other["id"], "Wrong inputs")      # rejecting needs no person
    assert rejected["status"] == "rejected"
    assert not _function_starts(rejected)


def test_restart_after_an_approved_start_asks_again(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_dies_once")
    run = create_run(system, "function_steps", operating_mode="supervised")
    advance(system, run["id"])
    system.service.approve(run["id"], person=True)
    with pytest.raises(_ProcessDied):
        system.worker.process_once()
    assert len(CALLS) == 1
    died = system.store.get_run(run["id"])
    assert died["status"] == "running"
    assert [step for step in steps(system, run["id"]) if step["step_id"] == "collect"] == []
    system.store.update_run(run["id"], heartbeat_at="")
    assert system.store.recover_running_runs("replacement-worker") == [run["id"]]

    again = advance(system, run["id"])
    assert (again["status"], again["waiting_reason"]) == ("waiting_human", "function_review")
    assert (again["waiting_step"], again["waiting_attempt"]) == ("collect", 1)
    assert len(CALLS) == 1                                # the spent approval did not call it a second time
    request = events(again, "function_review_requested")[-1]["payload"]
    assert request["interrupted"] is True
    assert "stopped before it recorded a result" in request["message"]
    entry = _review_entry(system, again, document)
    assert entry["text"] == request["message"]
    assert entry["decision"]["actions"] == ["approve", "reject"]
    assert entry["prior_outputs"]["first"] == again["artifacts"][0]["data"]
    assert not system.worker.process_once()
    assert len(CALLS) == 1

    system.service.approve(run["id"], person=True)
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert len(CALLS) == 2
    assert [(item["step_id"], item["attempt"]) for item in final["artifacts"]] == [("first", 1), ("collect", 1)]
    assert len(_function_starts(final)) == 2


def test_requeued_failure_waits_instead_of_retrying(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_recover")
    run = create_run(system, "function_steps", operating_mode="autonomous")
    failed = advance(system, run["id"])
    assert (failed["waiting_reason"], failed["waiting_attempt"]) == ("function_failure", 1)
    # Something queues the run again without a correction (an older state, a restart): no retry without a decision.
    system.store.update_run(run["id"], status="queued", waiting_reason="", waiting_step="", waiting_attempt=None)
    held = advance(system, run["id"])
    assert (held["status"], held["waiting_reason"]) == ("waiting_human", "function_failure")
    assert (held["waiting_step"], held["waiting_attempt"], held["current_step"]) == ("collect", 1, 1)
    assert len(CALLS) == 1
    assert not events(held, "function_retry_requested")
    restored = events(held, "function_failure_restored")[-1]["payload"]
    assert restored["code"] == "function_failed"
    assert restored["message"].startswith("Function step 'collect' failed (function_failed)")
    entry = _thread(system, held, document)["entries"][-1]
    assert (entry["reason"], entry["code"]) == ("function_failure", "function_failed")
    assert entry["text"] == restored["message"]
    assert entry["decision"]["actions"] == ["changes", "reject"]
    assert [(row["attempt"], row["status"]) for row in steps(system, run["id"]) if row["step_id"] == "collect"] == [
        (1, "failed")]

    system.service.changes(run["id"], "Add the date column")
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert len(CALLS) == 2


def test_second_failure_after_a_correction_needs_another_decision(system, tmp_path):
    CALLS.clear()
    _workflow(system, tmp_path, function="test.function_review_fails_twice")
    run = create_run(system, "function_steps", operating_mode="autonomous")
    assert advance(system, run["id"])["waiting_attempt"] == 1
    system.service.changes(run["id"], "Fix the first row")
    second = advance(system, run["id"])
    assert (second["status"], second["waiting_reason"], second["waiting_attempt"]) == (
        "waiting_human", "function_failure", 2)
    assert len(CALLS) == 2
    assert [event["payload"]["attempt"] for event in events(second, "function_step_failed")] == [1, 2]
    assert not system.worker.process_once()               # nothing retries the function on its own
    assert len(CALLS) == 2
    system.service.changes(run["id"], "Fix the second row")
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert len(CALLS) == 3
    assert [(item["step_id"], item["attempt"]) for item in final["artifacts"]] == [
        ("first", 1), ("first", 2), ("first", 3), ("collect", 3),
    ]
    assert [(row["attempt"], row["status"]) for row in steps(system, run["id"]) if row["step_id"] == "collect"] == [
        (1, "failed"), (2, "failed"), (3, "completed")]
    assert [event["payload"]["attempt"] for event in events(final, "function_retry_requested")] == [2, 3]


def test_a_requested_function_retry_is_not_cut_by_max_iterations(system, tmp_path):
    CALLS.clear()
    _workflow(system, tmp_path, function="test.function_review_fails_twice", max_iterations=1)
    run = create_run(system, "function_steps", operating_mode="autonomous")
    assert advance(system, run["id"])["waiting_attempt"] == 1
    system.service.changes(run["id"], "Fix the first row")
    assert advance(system, run["id"])["waiting_attempt"] == 2
    system.service.changes(run["id"], "Fix the second row")
    final = advance(system, run["id"])          # attempt 3 of the function step is above max_iterations + 1
    assert final["status"] == "completed", final.get("error")
    assert len(CALLS) == 3
    assert [event["payload"]["attempt"] for event in events(final, "function_retry_requested")] == [2, 3]
    assert not any("Maximum revision count" in str(event) for event in final.get("events", []))


def test_run_without_a_mode_calls_the_function_as_before(system, tmp_path):
    _workflow(system, tmp_path)
    run = create_run(system, "function_steps")
    assert run.get("operating_mode") is None
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert not events(final, "function_review_requested")
    assert len(_function_starts(final)) == 1


def test_function_waits_carry_an_english_message(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_recover")
    run = create_run(system, "function_steps", operating_mode="supervised")
    review = advance(system, run["id"])
    payload = events(review, "function_review_requested")[-1]["payload"]
    assert payload["message"] == ("Review the inputs and earlier outputs before function step 'collect' runs "
                                  "(attempt 1).")
    assert "interrupted" not in payload
    entry = _review_entry(system, review, document)
    assert entry["text"] == payload["message"]
    system.service.approve(run["id"], person=True)
    failed = advance(system, run["id"])
    payload = events(failed, "function_step_failed")[-1]["payload"]
    assert payload["message"] == ("Function step 'collect' failed (function_failed). Ask for a correction of the "
                                  "previous step, or reject the run.")
    entry = _thread(system, failed, document)["entries"][-1]
    assert entry["text"] == payload["message"]
    assert not entry["text"].startswith("Waiting:")


def test_absolute_paths_stay_out_of_the_failure_detail(system, tmp_path):
    document = _workflow(system, tmp_path, function="test.function_review_missing_file")
    run = create_run(system, "function_steps", operating_mode="autonomous")
    failed = advance(system, run["id"])
    assert failed["waiting_reason"] == "function_failure"
    detail = events(failed, "function_step_failed")[0]["payload"]["detail"]
    assert detail == "FileNotFoundError: [Errno 2] No such file or directory: '<path>'"
    entry = _thread(system, failed, document)["entries"][-1]
    collect = [row for row in steps(system, run["id"]) if row["step_id"] == "collect"]
    for text in (str(failed), str(entry), str(collect)):
        assert "someone" not in text and "private" not in text


@pytest.mark.parametrize(("message", "expected"), [
    ("cannot open /home/someone/private/data.csv for reading", "cannot open <path> for reading"),
    ("read C:\\Users\\Some One\\data.csv failed", "read <path> failed"),
    ("read C:/Users/someone/data.csv failed", "read <path> failed"),
    ("share \\\\fileserver\\team\\private\\data.csv is offline", "share <path> is offline"),
    ("open 'C:\\\\Users\\\\some one\\\\data.csv' failed", "open '<path>' failed"),
    ("open \"/home/some one/it's.csv\" failed", "open \"<path>\" failed"),
    ("see https://example.com/a/b for help", "see https://example.com/a/b for help"),
    ("missing data/rows.csv and 10/20 rows", "missing data/rows.csv and 10/20 rows"),
])
def test_failure_detail_replaces_absolute_paths(message, expected):
    assert failure_detail(ValueError(message)) == f"ValueError: {expected}"


def test_function_waits_are_one_transaction(system, tmp_path, monkeypatch):
    CALLS.clear()
    _workflow(system, tmp_path, function="test.function_review_recover")

    def broken(conn, run_id, items):
        raise RuntimeError("the event could not be written")

    monkeypatch.setattr(functions, "insert_run_events", broken)
    supervised = create_run(system, "function_steps", operating_mode="supervised")
    review = advance(system, supervised["id"])
    assert (review["status"], review["waiting_reason"]) == ("failed", "")
    assert not events(review, "function_review_requested")
    assert not CALLS
    autonomous = create_run(system, "function_steps", project_name="Second fixture", operating_mode="autonomous")
    failure = advance(system, autonomous["id"])
    assert (failure["status"], failure["waiting_reason"]) == ("failed", "")
    assert not events(failure, "function_step_failed")
    assert [row for row in steps(system, autonomous["id"]) if row["step_id"] == "collect"] == []
    assert len(CALLS) == 1


def test_function_events_reach_listeners_after_commit(system, tmp_path):
    CALLS.clear()
    _workflow(system, tmp_path, function="test.function_review_recover")
    heard: dict[str, str | None] = {}

    def listener(run_id, event_type, payload, event_id):
        with system.store.db.connect() as conn:   # another connection sees only committed rows
            row = conn.execute("SELECT event_type FROM run_events WHERE id=?", (event_id,)).fetchone()
        heard.setdefault(event_type, row["event_type"] if row else None)

    system.store.db.event_listeners.append(listener)
    run = create_run(system, "function_steps", operating_mode="supervised")
    advance(system, run["id"])
    system.service.approve(run["id"], person=True)
    advance(system, run["id"])
    system.service.changes(run["id"], "Fix the date column")
    wanted = ("function_review_requested", "function_review_approved", "function_step_failed",
              "human_revision", "function_retry_requested")
    assert {kind: heard.get(kind) for kind in wanted} == {kind: kind for kind in wanted}
