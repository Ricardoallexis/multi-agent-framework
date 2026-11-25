"""WFL-T03: today's date reaches the prompt of a workflow step, from the clock of this machine.

The date is a context section (``build_context_parts``), not a template variable, an input or part of the stable
prefix. The clock is replaced in every test: nothing here depends on the day it runs, and no provider is called
(the throwaway bundle and the scripted adapter of ``test_context_compaction``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import test_context_compaction as base
from multiagent.context import workflow as cw
from multiagent.prompts import PromptManager
from multiagent.services.workflow_inputs import derive_inputs

CST = timezone(timedelta(hours=-6))
WEDNESDAY = datetime(2026, 11, 25, 23, 59, tzinfo=CST)
THURSDAY = datetime(2026, 11, 26, 0, 1, tzinfo=CST)
TODAY = "Today's date: 2026-11-25 (Wednesday), local time UTC-06:00."
TOMORROW = "Today's date: 2026-11-26 (Thursday), local time UTC-06:00."


@pytest.fixture
def clock(monkeypatch):
    """The prompt clock: ``clock.now`` is what the machine says it is."""
    state = type("Clock", (), {"now": WEDNESDAY})()
    monkeypatch.setattr(cw, "local_now", lambda: state.now)
    return state


def test_the_text_has_the_iso_date_the_weekday_and_the_offset_from_utc():
    assert cw.current_date_text(WEDNESDAY) == TODAY
    india = datetime(2026, 1, 4, 8, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert cw.current_date_text(india) == "Today's date: 2026-01-04 (Sunday), local time UTC+05:30."
    assert cw.current_date_text(datetime(2026, 7, 1, tzinfo=timezone.utc)) == (
        "Today's date: 2026-07-01 (Wednesday), local time UTC+00:00.")


def test_without_a_clock_the_text_is_the_local_time_of_the_machine():
    text = cw.current_date_text()
    assert text.startswith("Today's date: ") and text.endswith(".") and "local time UTC" in text
    assert cw.local_now().utcoffset() is not None
    # A naive moment is read as local time, so the text always carries an offset.
    assert cw.current_date_text(datetime(2026, 11, 25, 12, 0)).startswith("Today's date: 2026-11-25 (Wednesday)")


def test_the_date_is_a_section_not_a_variable_and_the_clock_is_read_each_time(clock):
    def parts():
        return cw.build_context_parts(inputs={"topic": "T"}, prior_outputs=[], previous_output=None,
                                      revision_feedback="")

    context = cw.StepContext(parts())
    assert context.sections() == [TODAY]
    assert "current_date" not in context.variables()
    part = context.part("current_date")
    assert (part.kind, part.placement, part.summarized, part.ref, part.sha256) == (
        cw.KIND_CURRENT_DATE, cw.PLACEMENT_SECTION, False, None, None)
    clock.now = THURSDAY                                     # a change of day: no provider, no restart
    assert cw.StepContext(parts()).sections() == [TOMORROW]


def test_an_explicit_moment_wins_over_the_clock(clock):
    parts = cw.build_context_parts(inputs={}, prior_outputs=[], previous_output=None, revision_feedback="",
                                   now=THURSDAY)
    assert cw.StepContext(parts).sections() == [TOMORROW]


def test_a_compaction_never_summarizes_the_date_nor_counts_it_as_an_output(clock):
    a, b = base.rec("a"), base.rec("b")
    state = base.make_state(a)
    context = cw.StepContext(cw.build_context_parts(
        inputs={"topic": "T"}, prior_outputs=[("a", a), ("b", b)], previous_output=None, revision_feedback="",
        state=state))
    date_section, summary = context.sections()
    assert date_section == TODAY and summary.startswith("## Context summary")
    assert TODAY not in summary and not context.part("current_date").summarized
    assert state.step_ids == ("a",)                          # the date is not among the replaced outputs
    # The pinned check only reads inputs and outputs, so the date cannot make a compaction fail.
    plan = cw.plan_compaction([a, b], inputs={"topic": "T"}, keep_recent=1, state=None)
    assert cw.check_pinned_in_context(plan, context) == []


def test_render_and_the_stable_prefix_do_not_carry_the_date(clock):
    prefix = PromptManager.stable_prefix("skill text", "## REQUIRED OUTPUT CONTRACT\n{}")
    assert "Today's date" not in prefix
    assert "Today's date" not in PromptManager.assemble(prefix, "rendered", [], "tail").split("rendered")[0]


def test_the_date_is_not_an_input_of_the_workflow(tmp_path, clock):
    system = base.runtime(tmp_path)
    definition = system.bundle.workflows.load("chain")
    spec = derive_inputs(definition, system.engine.prompts)
    assert [item["name"] for item in spec["inputs"]] == ["topic"]
    assert "current_date" not in {item["name"] for item in spec["inputs"]}


def test_a_workflow_step_prompt_has_the_date_after_the_stable_prefix_and_it_changes_with_the_day(tmp_path, clock):
    first = base.runtime(tmp_path / "wednesday")
    _, run = base.run_all(first)
    assert run["status"] == "completed"
    clock.now = THURSDAY
    second = base.runtime(tmp_path / "thursday")
    _, run = base.run_all(second)
    assert run["status"] == "completed"
    wednesday, thursday = base.prompts_of(first), base.prompts_of(second)
    assert len(wednesday) == len(thursday) == 6
    for before, after in zip(wednesday, thursday):
        assert before.count(TODAY) == 1 and after.count(TOMORROW) == 1
        assert before.index(TODAY) > before.index("# Role")                      # in the variable part, not the prefix
        assert before.replace(TODAY, "") == after.replace(TOMORROW, "")           # nothing else depends on the day
        assert "Today's date" not in before.split("# Role")[0]                    # the stable prefix is date free


def test_the_preview_shows_the_same_date_section_the_prompt_gets(tmp_path, clock):
    from multiagent.services import workflow_inputs
    system = base.runtime(tmp_path)
    definition = system.bundle.workflows.load("chain")
    prompts = system.engine.prompts
    spec = derive_inputs(definition, prompts)
    result = workflow_inputs.preview(definition, prompts, spec, {"topic": "T"})
    section = cw.StepContext(cw.build_context_parts(
        inputs={"topic": "T"}, prior_outputs=[], previous_output=None, revision_feedback="")).sections()[0]
    assert section == TODAY
    for step in result["steps"]:
        assert step["text"].count(TODAY) == 1
    clock.now = THURSDAY
    again = workflow_inputs.preview(definition, prompts, spec, {"topic": "T"})
    assert again["steps"][0]["text"].count(TOMORROW) == 1
