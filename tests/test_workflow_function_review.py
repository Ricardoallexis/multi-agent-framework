# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Human review and recovery around deterministic workflow functions."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
import pytest
import yaml

from multiagent import functions
from multiagent.functions import failure_detail, get_function, register_function
from multiagent.gui.threads_api import _input_correction, add_thread_routes, build_thread
from multiagent.run_service import PersonRequired
from multiagent.run_request import RunRequest
from multiagent.errors import InvalidStateTransition
from multiagent.services.errors import ServiceError
from multiagent.workflow_validation import WorkflowDefinitionError

from backends.conformance import advance, backend_system, create_run, events, steps


CALLS: list[dict] = []
DURING: list = []                     # what happens elsewhere while ``_changed_then_fails`` runs
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


def _changed_then_fails(context, arguments):
    CALLS.append(dict(context["steps"]))
    for change in DURING:
        change()
    raise ValueError("bad row")


def _needs_topic(context, arguments):
    CALLS.append(dict(context["inputs"]))
    if context["inputs"]["topic"] != "ready":
        raise ValueError(f"topic needs correction: {context['inputs']['topic']}")
    return {"steps": {}}


for _id, _fn, _text in (
        ("test.function_review_recover", _recover, "Fail once and recover"),
        ("test.function_review_invalid", lambda context, arguments: {}, "Return an invalid output"),
        ("test.function_review_dies_once", _dies_once, "Stop the process on the first call"),
        ("test.function_review_fails_twice", _fails_twice, "Fail on the first two calls"),
        ("test.function_review_missing_file", _missing_file, "Fail with a local path in the message"),
        ("test.function_review_changed_then_fails", _changed_then_fails, "Let the run change, then fail")):
    if get_function(_id) is None:
        register_function(_id, _fn, description=_text)
if get_function("test.function_review_needs_topic") is None:
    register_function("test.function_review_needs_topic", _needs_topic, description="Read a declared run input")


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


@pytest.mark.parametrize("first", [True, False], ids=["first_step", "after_function_without_output"])
def test_function_failure_corrects_declared_run_inputs(system, tmp_path, first):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic", "required": True, "label": "Topic"}]
    if first:
        document["steps"] = document["steps"][1:]
    else:
        document["steps"][0] = {"id": "before", "function": "core.merge_outputs",
                                "arguments": {}, "contract": "CollectedOutputs"}
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Input correction",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    held = advance(system, run["id"])
    assert (held["status"], held["waiting_reason"]) == ("waiting_human", "function_failure")
    entry = _thread(system, held, document)["entries"][-1]
    assert entry["decision"]["correction_target"] == "inputs"
    assert entry["decision"]["actions"] == ["correct_inputs", "reject"]
    assert entry["input_fields"][0]["value"] == "bad"
    for invalid in ({"topic": ""}, {"unknown": "ready"}):
        with pytest.raises(ValueError):
            system.service.correct_function_inputs(run["id"], invalid)
        assert system.store.get_run(run["id"])["status"] == "waiting_human"
    queued = system.service.correct_function_inputs(run["id"], {"topic": "ready"}, by="operator", channel="gui")
    assert queued["request"]["inputs"] == {"topic": "ready"}
    correction = events(queued, "function_inputs_corrected")[-1]["payload"]
    assert correction == {"step_id": "collect", "attempt": 1, "input_names": ["topic"],
                          "by": "operator", "channel": "gui"}
    assert "ready" not in str(correction)
    final = advance(system, run["id"])
    assert final["status"] == "completed", (final["waiting_reason"], final["error"],
                                           [(event["event_type"], event["payload"])
                                            for event in final["events"][-5:]])
    assert len(CALLS) == 2
    assert [item["payload"]["attempt"] for item in events(final, "function_retry_requested")] == [2]


def test_second_failure_after_input_correction_is_terminal(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_fails_twice")
    document["inputs"] = [{"name": "topic", "required": True}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="One input retry",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    assert advance(system, run["id"])["waiting_reason"] == "function_failure"
    system.service.correct_function_inputs(run["id"], {"topic": "ready"})
    final = advance(system, run["id"])
    assert final["status"] == "failed"
    assert "retry after input correction failed" in final["error"]
    assert len(CALLS) == 2
    assert len(events(final, "function_step_failed")) == 1
    assert len(events(final, "workflow_failed")) == 1


@pytest.mark.parametrize("data_class", ["sensitive", "restricted"])
def test_data_class_hides_function_failure_detail(system, tmp_path, monkeypatch, data_class):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic"}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Classified input",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    get_run = system.store.get_run
    monkeypatch.setattr(system.store, "get_run", lambda run_id: {**get_run(run_id), "data_class": data_class})
    held = advance(system, run["id"])
    assert held["waiting_reason"] == "function_failure"
    assert "bad" not in str(events(held, "function_step_failed"))
    assert "bad" not in held["error"]
    entry = _thread(system, held, document)["entries"][-1]
    assert entry["detail"] == ""
    assert entry["input_fields"][0]["value"] is None
    assert entry["input_fields"][0]["sensitive"] is True


def test_input_correction_rejects_changed_function(system, tmp_path):
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic"}]
    document["steps"] = document["steps"][1:]
    path = tmp_path / "workflows" / "function_steps.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Changed function",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    held = advance(system, run["id"])
    document["steps"][0]["when"] = "requires_web"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(InvalidStateTransition, match="no longer matches"):
        system.service.correct_function_inputs(run["id"], {"topic": "ready"})
    assert _thread(system, held, document)["entries"][-1]["decision"]["actions"] == ["correct_inputs", "reject"]
    assert system.store.get_run(run["id"])["status"] == "waiting_human"


@pytest.mark.parametrize("change", ["model", "negative_index", "past_end"])
def test_input_correction_rejects_step_mismatch(system, tmp_path, change):
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    model_step = document["steps"][0]
    document["inputs"] = [{"name": "topic"}]
    document["steps"] = document["steps"][1:]
    path = tmp_path / "workflows" / "function_steps.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Step mismatch",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    advance(system, run["id"])
    if change == "model":
        document["steps"][0] = {**model_step, "id": "collect"}
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
    else:
        system.store.update_run(run["id"], current_step=-1 if change == "negative_index" else 1)
    with pytest.raises(InvalidStateTransition, match="no longer matches"):
        system.service.correct_function_inputs(run["id"], {"topic": "ready"})


@pytest.mark.parametrize("error", [KeyError("missing"), WorkflowDefinitionError("function_steps", [])])
def test_input_correction_translates_changed_definition(system, tmp_path, monkeypatch, error):
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic"}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Missing definition",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    advance(system, run["id"])

    def broken_preflight(*args):
        raise error

    monkeypatch.setattr(system.service.engine, "preflight", broken_preflight)
    with pytest.raises(InvalidStateTransition, match="no longer matches"):
        system.service.correct_function_inputs(run["id"], {"topic": "ready"})


@pytest.mark.parametrize("index,step", [(-1, {"id": "collect", "function": "test.function_review_needs_topic"}),
                                            (1, {"id": "collect", "function": "test.function_review_needs_topic"}),
                                            (0, {"id": "other", "function": "test.function_review_needs_topic"}),
                                            (0, {"id": "collect", "function": None})])
def test_thread_does_not_offer_inputs_for_a_changed_step(index, step):
    run = {"current_step": index, "waiting_step": "collect"}
    view = {"steps": [step], "artifacts": []}
    assert not _input_correction(run, view, [{"name": "topic"}])


def test_input_correction_ignores_undeclared_derived_required(system, tmp_path, monkeypatch):
    from multiagent.services import workflow_inputs

    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic", "required": True}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Derived input",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    advance(system, run["id"])
    original = workflow_inputs.derive_inputs

    def with_derived(*args):
        spec = original(*args)
        return {**spec, "inputs": [*spec["inputs"], {"name": "undeclared", "required": True}]}

    monkeypatch.setattr(workflow_inputs, "derive_inputs", with_derived)
    assert system.service.correct_function_inputs(run["id"], {"topic": "ready"})["status"] == "queued"


def test_sensitive_function_input_failure_wait_and_supervised_retry(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic", "required": True}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Sensitive input",
                                               inputs={"topic": "bad"}, sensitive=True,
                                               operating_mode="supervised"))
    review = advance(system, run["id"])
    assert review["waiting_reason"] == "function_review"
    system.service.approve(run["id"], person=True)
    held = advance(system, run["id"])
    entry = _thread(system, held, document)["entries"][-1]
    assert entry["input_fields"][0]["sensitive"] is True
    assert entry["input_fields"][0]["value"] is None
    assert "bad" not in entry["text"] and "bad" not in entry["detail"]
    assert "bad" not in str(events(held, "function_step_failed"))
    assert "bad" not in held["error"]
    assert "bad" not in str(_thread(system, held, document))
    system.service.correct_function_inputs(run["id"], {"topic": "ready"})
    review = advance(system, run["id"])
    assert (review["status"], review["waiting_reason"]) == ("waiting_human", "function_review")
    assert len(CALLS) == 1
    system.service.cancel(run["id"])
    cancelled = system.store.get_run(run["id"])
    assert cancelled["status"] == "cancelled"
    assert not system.worker.process_once()
    assert len(CALLS) == 1


def test_function_input_correction_route(system, tmp_path):
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic", "required": True, "type": "choice",
                           "choices": ["bad", "ready"]}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Route fixture",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    advance(system, run["id"])
    runtime = SimpleNamespace(service=system.service)
    runs = SimpleNamespace(_find=lambda run_id: runtime,
                           _tag=lambda runtime, item: {**item, "bundle": None},
                           get=system.store.get_run)
    services = SimpleNamespace(runs=runs, definitions=SimpleNamespace(read_workflow=lambda *a, **kw: document))
    router = APIRouter(prefix="/gui/api")
    add_thread_routes(router, services)
    app = FastAPI()
    app.add_exception_handler(ServiceError, lambda request, exc: JSONResponse(
        status_code=exc.status, content=exc.to_payload()))
    app.include_router(router)
    client = TestClient(app)
    invalid = client.post(f"/gui/api/threads/runs/{run['id']}/function-inputs", json={"inputs": {"topic": ""}})
    assert invalid.status_code == 422
    invalid_choice = client.post(f"/gui/api/threads/runs/{run['id']}/function-inputs",
                                 json={"inputs": {"topic": "unknown"}})
    assert invalid_choice.status_code == 422
    assert system.store.get_run(run["id"])["status"] == "waiting_human"
    corrected = client.post(f"/gui/api/threads/runs/{run['id']}/function-inputs",
                            json={"inputs": {"topic": "ready"}})
    assert corrected.status_code == 200
    assert system.store.get_run(run["id"])["request"]["inputs"] == {"topic": "ready"}
    assert events(system.store.get_run(run["id"]), "function_inputs_corrected")[-1]["payload"]["by"] == "user:operator"
    stale = client.post(f"/gui/api/threads/runs/{run['id']}/function-inputs",
                        json={"inputs": {"topic": "ready"}})
    assert stale.status_code == 409


def test_function_input_required_none_is_boolean(system, tmp_path):
    document = _workflow(system, tmp_path, function="test.function_review_needs_topic")
    document["inputs"] = [{"name": "topic"}]
    document["steps"] = document["steps"][1:]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Optional input",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    held = advance(system, run["id"])
    assert _thread(system, held, document)["entries"][-1]["input_fields"][0]["required"] is False


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
    # Every part of a path goes, the text between two paths stays, URLs and relative paths are left alone.
    ("C:\\a\\b.csv and D:\\c.csv", "<path> and <path>"),
    ("/home/u/My Documents/f.csv not found", "<path> not found"),
    ("C:\\Program Files\\app\\x.dll failed", "<path> failed"),
    ("\\\\server\\share\\x.txt is locked", "<path> is locked"),
    ("see https://example.com/a/b", "see https://example.com/a/b"),
    ("data/input.csv missing", "data/input.csv missing"),
    ("C:\\Program Files (x86)\\Common Files\\x.dll failed", "<path> failed"),
    ("C:\\Users\\u\\OneDrive - Some Name\\Documents\\x.csv is open", "<path> is open"),
    ("/My Documents/f.csv not found", "<path> not found"),
    ("copy /home/u/a.csv to /home/u/b.csv failed", "copy <path> to <path> failed"),
    ("C:\\data\\in.csv not found in C:\\data\\backup", "<path> not found in <path>"),
    ("C:\\a\\b.csv, D:\\c.csv.", "<path>, <path>."),
    ("C:\\data\\in.csv: see data/rules.md", "<path>: see data/rules.md"),
    ("/home/u/in.csv has 10/20 bad rows", "<path> has 10/20 bad rows"),
    ("\\\\server\\share\\x.txt and https://example.com/a", "<path> and https://example.com/a"),
    ("C:\\a\\b.csv and ./c.csv", "<path> and ./c.csv"),
    ("use /help or see docs/x.md", "use /help or see docs/x.md"),
])
def test_failure_detail_replaces_absolute_paths(message, expected):
    assert failure_detail(ValueError(message)) == f"ValueError: {expected}"


def test_path_redaction_stays_linear_on_long_texts():
    for text in ("C:\\a " * 50_000, "/a/b " * 50_000, "C:\\" + "a b " * 50_000, "\\\\x" + ". " * 50_000):
        assert len(failure_detail(ValueError(text))) <= functions.FAILURE_DETAIL_LIMIT


def test_a_cancel_while_the_function_runs_ends_the_run_cancelled(system, tmp_path):
    CALLS.clear()
    _workflow(system, tmp_path, function="test.function_review_changed_then_fails")
    run = create_run(system, "function_steps", operating_mode="autonomous")
    DURING[:] = [lambda: system.service.cancel(run["id"])]
    try:
        final = advance(system, run["id"])
    finally:
        DURING.clear()
    assert len(CALLS) == 1
    assert (final["status"], final["waiting_reason"], final["waiting_step"]) == ("cancelled", "", "")
    assert len(events(final, "cancel_requested")) == 1
    assert len(events(final, "workflow_cancelled")) == 1
    assert not events(final, "function_step_failed")
    assert not events(final, "workflow_failed")
    assert [row for row in steps(system, run["id"]) if row["step_id"] == "collect"] == []
    assert not system.worker.process_once()
    assert len(CALLS) == 1


def test_a_cancel_while_a_first_function_fails_ends_the_run_cancelled(system, tmp_path):
    # A cancel during a recoverable first function failure still wins over the new input-correction wait.
    CALLS.clear()
    document = _workflow(system, tmp_path, function="test.function_review_changed_then_fails")
    document["steps"] = document["steps"][1:]
    document["inputs"] = [{"name": "topic", "required": True}]
    (tmp_path / "workflows" / "function_steps.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    run = system.service.create_run(RunRequest(workflow_id="function_steps", project_name="Concurrent cancel",
                                               inputs={"topic": "bad"}, operating_mode="autonomous"))
    DURING[:] = [lambda: system.service.cancel(run["id"])]
    try:
        final = advance(system, run["id"])
    finally:
        DURING.clear()
    assert len(CALLS) == 1
    assert (final["status"], final["waiting_reason"]) == ("cancelled", "")
    assert len(events(final, "workflow_cancelled")) == 1
    assert not events(final, "workflow_failed")
    assert not events(final, "function_step_failed")


@pytest.mark.parametrize(("change", "status", "cancelled"), [
    ("queued", "failed", 0),            # queued again without a cancel: it fails, as before
    ("paused", "paused", 0),            # no longer running, no cancel: nothing overwrites it
    ("forced", "cancelled", 1),         # already cancelled: no second cancel event
])
def test_only_a_cancel_turns_a_refused_failure_wait_into_cancelled(system, tmp_path, change, status, cancelled):
    CALLS.clear()
    _workflow(system, tmp_path, function="test.function_review_changed_then_fails")
    run = create_run(system, "function_steps", operating_mode="autonomous")
    changes = {
        "queued": lambda: system.store.update_run(run["id"], status="queued"),
        "paused": lambda: system.store.update_run(run["id"], status="paused"),
        "forced": lambda: system.service.cancel(run["id"], force=True),
    }
    DURING[:] = [changes[change]]
    try:
        system.worker.process_once()
    finally:
        DURING.clear()
    final = system.store.get_run(run["id"])
    assert len(CALLS) == 1
    assert final["status"] == status
    assert len(events(final, "workflow_cancelled")) == cancelled
    assert not events(final, "function_step_failed")


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
