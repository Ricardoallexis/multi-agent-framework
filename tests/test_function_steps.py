# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic function steps across validation and both execution backends."""
from __future__ import annotations

from pathlib import Path

import pytest

from multiagent.bundles import load_bundle
from multiagent.functions import function_owner, register_function
from multiagent.workflow_validation import WorkflowDefinitionError, parse_workflow_document

from backends.conformance import FIXTURES, advance, backend_system, create_run, events, usage


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bundles" / "general"


def _bad_output(context, arguments):
    return {}


def _raises(context, arguments):
    raise RuntimeError("private exception detail")


def _inspect(context, arguments):
    try:
        context["inputs"]["topic"] = "changed"
    except TypeError:
        readonly = True
    else:
        readonly = False
    return {"steps": {"seen": {
        "topic": context["inputs"]["topic"], "label": arguments["label"], "readonly": readonly,
    }}}


register_function("test.bad_output", _bad_output, description="Return an invalid output")
register_function("test.raises", _raises, description="Raise an exception")
register_function("test.inspect", _inspect, description="Inspect function context and arguments")


@pytest.fixture(params=["native", "mock"])
def system(request):
    return request.getfixturevalue("backend_system")


def _document(function="core.merge_outputs", extra=""):
    return f"""id: function_steps
steps:
  - id: collect
    function: {function}
    contract: CollectedOutputs
{extra}"""


def test_unknown_function_in_bundle(system):
    bundle = load_bundle(FIXTURES, system.settings)
    definition = parse_workflow_document(_document("other.missing"), workflow_id="function_steps")
    bundle.workflows.load = lambda workflow_id: definition
    with pytest.raises(WorkflowDefinitionError) as caught:
        bundle.load_workflow("function_steps")
    assert [issue.code for issue in caught.value.issues] == ["unknown_function"]


def test_builtin_function_has_the_core_owner():
    assert function_owner("core.merge_outputs") == "core"
    assert function_owner("nothing.here") is None


def test_example_bundle_loads(system):
    definition = load_bundle(EXAMPLE, system.settings).load_workflow("collect_summary")
    assert definition.steps[-1].function == "core.merge_outputs"


@pytest.mark.parametrize("function", ["no_dot", "Core.Merge", "a..b"])
def test_function_id_uses_capability_pattern(function):
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse_workflow_document(_document(function), workflow_id="function_steps")
    assert any(issue.code == "invalid_value" and issue.location.endswith(".function")
               for issue in caught.value.issues)


def test_arguments_are_literal():
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse_workflow_document(_document(extra="    arguments: {day: 2026-11-18}\n"),
                                workflow_id="function_steps")
    assert any(issue.location.endswith(".arguments") for issue in caught.value.issues)


@pytest.mark.parametrize("extra", ["    agent: fixture_writer\n", "    tools: []\n",
                                    "    requires_web: false\n", "    prompt_id: fixture/write\n"])
def test_function_rejects_model_and_tool_fields(extra):
    with pytest.raises(WorkflowDefinitionError) as caught:
        parse_workflow_document(_document(extra=extra), workflow_id="function_steps")
    assert "invalid_value" in [issue.code for issue in caught.value.issues]


def test_function_runs_with_validated_prior_output_and_zero_usage(system):
    run = create_run(system, "function_steps")
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert final["llm_calls"] == 1
    assert len(usage(system, run["id"])) == 1
    assert final["artifacts"][1]["data"] == {"steps": {"first": final["artifacts"][0]["data"]}}
    expected = system.adapter.generate_structured(
        spec=None, prompt="", output_schema=system.backend.output_schemas["ContentOutput"],
    ).parsed.model_dump(mode="json")
    assert final["artifacts"][1]["data"] == {"steps": {"first": expected}}
    for kind in ("step_started", "step_completed"):
        function_event = [event for event in events(final, kind) if event["payload"]["step_id"] == "collect"]
        assert function_event[0]["payload"]["function"] == "core.merge_outputs"


def test_function_ignores_call_and_active_time_budgets(system, tmp_path):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "function_steps.yaml").write_text(_document(), encoding="utf-8")
    system.backend.workflows.workflows_dir = workflow_dir
    run = create_run(system, "function_steps")
    with system.store.db.connect() as conn:
        conn.execute("UPDATE runs SET max_llm_calls=0 WHERE id=?", (run["id"],))
        conn.commit()
    system.store.update_run(run["id"], active_seconds=run["max_run_seconds"] + 1)
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert final["llm_calls"] == 0
    assert not usage(system, run["id"])


def test_function_receives_readonly_inputs_and_literal_arguments(system, tmp_path):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    document = _document("test.inspect", extra='    arguments: {label: "${topic}"}\n')
    (workflow_dir / "function_steps.yaml").write_text(document, encoding="utf-8")
    system.backend.workflows.workflows_dir = workflow_dir
    run = create_run(system, "function_steps")
    final = advance(system, run["id"])
    assert final["status"] == "completed"
    assert final["artifacts"][0]["data"] == {"steps": {"seen": {
        "topic": "durable execution", "label": "${topic}", "readonly": True,
    }}}


@pytest.mark.parametrize("function,code", [("test.bad_output", "function_output_invalid"),
                                             ("test.raises", "function_failed")])
def test_function_failure_has_stable_code_and_no_trace(system, tmp_path, function, code):
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "function_steps.yaml").write_text(_document(function), encoding="utf-8")
    system.backend.workflows.workflows_dir = workflow_dir
    run = create_run(system, "function_steps")
    final = advance(system, run["id"])
    assert final["status"] == "failed"
    assert final["llm_calls"] == 0
    assert not usage(system, run["id"])
    failure = events(final, "workflow_failed")[0]["payload"]
    assert failure["issues"][0]["code"] == code
    if code == "function_failed":
        assert "private exception detail" in failure["detail"]
    else:
        assert "detail" not in failure
        assert "private exception detail" not in str(failure)
    assert "Traceback" not in str(failure)
    assert 'File "' not in str(failure)
