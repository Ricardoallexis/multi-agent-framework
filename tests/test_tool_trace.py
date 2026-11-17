"""Synthetic tool traces stay linked while excluding private tool payloads."""
from __future__ import annotations

import hashlib
import json

from multiagent.services.reports import build_run_report, render_run_report_markdown
from multiagent.tools.trace import (adapt_log_event_sink, make_tool_invoker_sinks,
                                    project_tool_events)


MARKER = "SYNTHETIC_PRIVATE_TOOL_CONTENT"


def _event(event_type: str, request_id: str, **fields):
    payload = {
        "schema": "multiagent.tool_event/1",
        "request_id": request_id,
        "tool_id": "mock.echo",
        "tool_version": 1,
        "spec_sha256": "a" * 64,
        "scope": "run_step",
        "run_id": "run-fixture",
        "task_id": None,
        "step_id": "step-fixture",
        "attempt": 2,
        "ordinal": 3,
        "model_call_ordinal": 4,
        "actor": {"kind": "tool", "id": "mock.echo", "version": 1},
        "origin": {"kind": "model_output", "ref": "call-fixture"},
        **fields,
    }
    return {"seq": 1, "ts": "2026-01-01T00:00:00Z", "event_type": event_type, "payload": payload}


def test_tool_trace_and_report_filter_raw_values_and_ignore_unknown_schema():
    success = "request_ok"
    denied = "request_no"
    failed = "request_err"
    digest = hashlib.sha256(b"synthetic").hexdigest()
    events = [
        _event("tool_call_requested", success, arguments_sha256=digest, arguments_bytes=20,
               arguments=MARKER),
        _event("tool_call_completed", success, output_sha256=digest, output_bytes=18,
               duration_ms=7, output=MARKER, blob_refs={"output": f"blob:sha256:{digest}"}),
        _event("tool_call_requested", denied, arguments_sha256=digest, arguments_bytes=20,
               arguments=MARKER),
        _event("tool_permission_requested", denied, permission_request={
            "permission_request_id": "permission-fixture", "summary": "Approve fixture target",
            "actions": [{"target": "fixture-target"}],
        }),
        _event("tool_call_denied", denied, code="denied", reason=MARKER),
        _event("tool_call_requested", failed, arguments_sha256=digest, arguments_bytes=20,
               arguments=MARKER),
        _event("tool_call_failed", failed, code="execution_failed", retryable=True, duration_ms=9,
               tool_message_sha256=digest, output=MARKER),
        _event("tool_call_completed", "request_bad", schema="other.tool_event/9",
               output_sha256=digest, output=MARKER),
    ]

    traces = project_tool_events(events)
    assert len(traces) == 3
    assert {row["status"] for row in traces} == {"completed", "denied", "failed"}
    for row in traces:
        assert (row["run_id"], row["step_id"], row["attempt"]) == ("run-fixture", "step-fixture", 2)
        assert row["request_id"] in {success, denied, failed}
    assert traces[0]["blob_refs"]["output"] == f"blob:sha256:{digest}"

    run = {"id": "run-fixture", "status": "completed", "events": events}
    safe = build_run_report(run, {"records": []})
    raw = build_run_report(run, {"records": []}, include_raw=True)
    for rendered in (json.dumps(traces), json.dumps(safe), json.dumps(raw),
                     render_run_report_markdown(safe), render_run_report_markdown(raw)):
        assert MARKER not in rendered
    assert len(safe["tools"]) == 7
    assert safe["tools"][1]["output_sha256"] == digest
    assert safe["tools"][1]["blob_refs"]["output"] == f"blob:sha256:{digest}"
    approval_row = next(row for row in raw["tools"] if row["event_type"] == "tool_permission_requested")
    assert approval_row["summary"] == "Approve fixture target"
    assert approval_row["destinations"] == ["fixture-target"]
    assert "output" not in approval_row


def test_host_sink_adapters_bind_run_and_forward_optional_blob_sink():
    logged = []
    blob = lambda data, media_type: "blob:sha256:" + hashlib.sha256(data).hexdigest()
    event_sink, forwarded_blob = make_tool_invoker_sinks(
        lambda run_id, event_type, payload: logged.append((run_id, event_type, payload)),
        "run-fixture", blob,
    )
    payload = {"schema": "multiagent.tool_event/1"}
    event_sink("tool_call_requested", payload)
    assert logged == [("run-fixture", "tool_call_requested", payload)]
    assert forwarded_blob is blob
    adapt_log_event_sink(lambda *args: logged.append(args), "another-run")("tool_call_failed", payload)
    assert logged[-1] == ("another-run", "tool_call_failed", payload)


def test_request_without_outcome_stays_requested_and_mismatched_identity_is_ignored():
    digest = hashlib.sha256(b"synthetic").hexdigest()
    events = [
        _event("tool_call_requested", "request_open", arguments_sha256=digest, arguments_bytes=20),
        _event("tool_call_completed", "request_open", run_id="run-other", output_sha256=digest),
    ]
    (row,) = project_tool_events(events)
    assert row["status"] == "requested"
    assert row["run_id"] == "run-fixture"
    assert "output_sha256" not in row
