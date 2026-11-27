# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVZ-T04: the same progress bar in the run thread, the plan thread, «Contexto» and the project card.

The pure builders (thread-model.js) run in Node and are skipped without it; the views are read as source. No server, no browser."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def node(body: str, lang: str = "en"):
    imports = "\n".join([
        f"import * as model from {json.dumps((JS / 'thread-model.js').as_uri())};",
        f"import * as ui from {json.dumps((JS / 'i18n.js').as_uri())};",
        f'ui.setLanguage("{lang}");',
    ])
    result = subprocess.run([NODE, "--input-type=module", "-e", imports + "\n" + body], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def steps(*states):
    return [{"id": f"s{index + 1}", "state": state} for index, state in enumerate(states)]


def run_bar(listing, entries=(), status="running", lang="en"):
    return node(f"console.log(JSON.stringify(model.stepBar({json.dumps(listing)}, {json.dumps(list(entries))}, {json.dumps(status)})))", lang)


@needs_node
def test_a_run_of_several_steps_draws_one_piece_per_step_with_its_state_in_words():
    bar = run_bar(steps("done", "done", "running", "pending", "pending"))
    assert [piece["state"] for piece in bar["segments"]] == ["done", "done", "running", "pending", "pending"]
    assert [piece["text"] for piece in bar["segments"]][:3] == ["Done", "Done", "In progress"]
    assert bar["line"] == "Step 3 of 5"
    assert bar["summary"] == "2 of 5 steps done, 1 In progress"
    assert (bar["total"], bar["done"]) == (5, 2)


@needs_node
def test_a_failed_or_waiting_step_says_so_in_the_line_and_not_only_in_colour():
    failed = run_bar(steps("done", "failed", "not_run"), status="failed")
    assert failed["line"] == "Step 2 of 3 · Failed"
    assert failed["segments"][1]["tone"] == "danger"
    waiting = run_bar(steps("done", "waiting_review", "pending"), status="waiting_human")
    assert waiting["line"] == "Step 2 of 3 · Waiting for your review"
    assert "1 Waiting for your review" in waiting["summary"]


@needs_node
def test_a_run_of_one_step_or_without_a_list_has_no_bar():
    assert run_bar(steps("running")) is None
    assert run_bar([]) is None
    assert node("console.log(JSON.stringify(model.stepBar(null, [], 'running')))") is None
    assert node("console.log(JSON.stringify(model.stepBar(undefined)))") is None


@needs_node
def test_the_bar_catches_up_with_what_the_thread_heard_after_the_last_read_of_the_steps():
    stale = steps("waiting_review", "pending", "pending")
    heard = [{"kind": "step", "status": "completed", "step_id": "s1"}]
    bar = run_bar(stale, heard, status="running")
    assert [piece["state"] for piece in bar["segments"]] == ["done", "running", "pending"]   # the next step is the one that runs
    assert bar["line"] == "Step 2 of 3"
    still_waiting = run_bar(stale, heard, status="waiting_human")
    assert still_waiting["segments"][0]["state"] == "waiting_review"                          # a fresh wait is not touched
    cancelled = run_bar(steps("done", "running", "pending"), status="cancelled")
    assert [piece["state"] for piece in cancelled["segments"]] == ["done", "not_run", "not_run"]
    finished = run_bar(steps("done", "running"), [{"kind": "step", "status": "completed", "step_id": "s2"}], status="completed")
    assert finished["line"] == "2 of 2 steps done"


@needs_node
def test_the_run_bar_speaks_spanish_too():
    bar = run_bar(steps("done", "running", "pending"), lang="es")
    assert bar["line"] == "Paso 2 de 3"
    assert bar["summary"].startswith("1 de 3 pasos hechos")
    assert bar["segments"][0]["text"] == "Hecho"


def plan_entry(task_id, state):
    return {"seq": 3, "kind": "task_run", "task_id": task_id, "state": state}


@needs_node
def test_a_plan_draws_one_piece_per_task_and_leaves_out_the_cancelled():
    entries = [plan_entry("T1", "accepted"), plan_entry("T2", "running"), plan_entry("T3", "submitted"), plan_entry("T4", "blocked"),
               plan_entry("T5", "cancelled"), {"kind": "plan", "seq": 1}, {"kind": "plan_event", "seq": 2}]
    bar = node(f"console.log(JSON.stringify(model.planBar({json.dumps(entries)})))")
    assert [piece["state"] for piece in bar["segments"]] == ["done", "doing", "review", "blocked"]
    assert bar["line"].startswith("1 of 4 tasks done")
    assert "Submitted" in bar["summary"] and "Blocked" in bar["summary"]
    assert node("console.log(JSON.stringify(model.planBar([{kind: 'plan', seq: 1}])))") is None   # not approved yet: no task runs
    assert node(f"console.log(JSON.stringify(model.planBar([{json.dumps(plan_entry('T1', 'running'))}])))") is None   # one task
    assert node("console.log(JSON.stringify(model.planBar(null)))") is None


@needs_node
def test_the_project_card_draws_from_the_counts_and_never_from_a_percentage():
    bar = node("console.log(JSON.stringify(model.countPieces({done: 3, review: 1, doing: 1, todo: 5, cancelled: 4})))")
    assert [piece["state"] for piece in bar["segments"]] == ["done"] * 3 + ["doing", "review"] + ["todo"] * 5
    assert bar["line"] == "3 of 10 tasks done"          # the cancelled ones do not count
    assert bar["summary"] == "3 of 10 tasks done, 1 Waiting for your review"
    assert node("console.log(JSON.stringify(model.countPieces({done: 1})))") is None
    assert node("console.log(JSON.stringify(model.countPieces(undefined)))") is None
    assert node("console.log(JSON.stringify(model.countPieces({percent: 90})))") is None
    many = node("console.log(JSON.stringify(model.countPieces({done: 500, todo: 500, failed: 1})))")
    assert len(many["segments"]) <= 62 and many["total"] == 1001 and many["segments"][-1]["state"] == "todo"
    assert any(piece["state"] == "failed" for piece in many["segments"])


def block(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start))]


def test_the_run_thread_draws_the_bar_from_the_steps_it_already_read_and_adds_no_call():
    thread = (JS / "thread.js").read_text(encoding="utf-8")
    assert "setRunSteps(view.steps)" in thread
    assert "stepBar(runSteps, thread.entries, thread.status, decisions)" in thread
    assert 'className="thread-segbar"' in thread and "${bar.line}" in thread
    run = block(thread, "export function RunThread", "// AVA-T05")
    assert run.count("apiGet(") == 2 and run.count("setInterval(") == 1   # the thread page and the run view, as before; one timer
    assert "usePanelData" not in run


def test_the_plan_thread_draws_the_bar_from_its_own_read_with_the_timer_it_had():
    plan = (JS / "plan-thread.js").read_text(encoding="utf-8")
    assert "const bar = planBar(state.entries);" in plan and 'className="thread-segbar"' in plan
    assert plan.count("setInterval(") == 1 and plan.count("apiGet(") == 1


def test_the_context_slot_fills_only_with_a_run_that_has_steps_and_keeps_the_empty_one():
    panel = (JS / "right-panel.js").read_text(encoding="utf-8")
    assert 'subject && subject.kind === "run" && data && data.run ? stepBar(data.run.steps, [], data.run.status) : null' in panel
    group = block(panel, 'title=${t("panel.in_progress_heading")}', "${extra.map")
    assert '<div class="rp-progress-slot" aria-hidden="true"></div>' in group      # no subject with steps: nothing drawn, still hidden
    assert 'html`<div class="rp-progress-slot">' in group and 'className="rp-segbar"' in group
    assert 'groupShown(shown, ["subagents"]) || Boolean(bar)' in group
    assert "const dot = Boolean(subject) && anyContent(cards, modes, contents);" in panel   # the dot does not change
    assert "usePanelData(" in panel and panel.count("usePanelData(") == 1


def test_the_project_card_uses_the_same_component_and_keeps_its_progress():
    index = (STATIC_DIR / "extensions" / "project" / "index.js").read_text(encoding="utf-8")
    slot = block(index, "export function ProjectSlot", "registerView(")
    assert "countPieces(chosen.counts)" in slot and 'className="project-segbar project-segbar-small"' in slot
    assert "<${Progress}" in slot and "apiGet(" in slot and slot.count("apiGet(") == 1


def test_the_sheets_draw_the_bar_for_every_state_and_for_forced_colours():
    app = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    panels = (STATIC_DIR / "panels.css").read_text(encoding="utf-8")
    for sheet, name in ((app, "thread-segbar"), (panels, "rp-segbar")):
        tail = sheet[sheet.index("/* AVZ-T04"):]
        assert f".{name}" in tail and "flex: 1 1 0" in tail and "forced-colors: active" in tail
        assert re.search(rf'\.{name} > \[data-state="todo"\]', tail) and f'.{name} > [data-state="pending"]' in tail
        assert "style=" not in tail
    assert app.index("/* AVZ-T04") > app.index("ACP") and panels.index("/* AVZ-T04") > panels.index("GUI4-T09")
    assert ".project-segbar" not in app and ".project-segbar" not in panels            # the project's sheet is not copied or moved


def test_the_new_texts_exist_in_both_languages_and_the_state_texts_are_the_old_ones():
    keys = ["thread.progress.step", "thread.progress.done", "plans.thread.progress.done",
            *[f"panel.progress.task.{name}" for name in ("done", "doing", "review", "changes", "failed", "blocked", "ready", "todo")]]
    for name, keys_of in (("thread", ["thread.progress.step", "thread.progress.done"]), ("plans", ["plans.thread.progress.done"]),
                          ("panel", [key for key in keys if key.startswith("panel.")])):
        for lang in ("en", "es"):
            text = (STATIC_DIR / "i18n" / lang / f"{name}.js").read_text(encoding="utf-8")
            for key in keys_of:
                assert f'"{key}":' in text, (lang, key)


# --- round 2: a step that may be running again is never called done ---------------------------------------------------------

def review_trail(*extra):
    """What the thread heard for step s1: it completed (seq 3), then waited for review (seq 4)."""
    return [{"kind": "step", "status": "completed", "step_id": "s1", "seq": 3}, {"kind": "waiting", "step_id": "s1", "seq": 4}, *extra]


def decided_bar(decisions, entries, status="running", listing=None):
    listing = listing or steps("waiting_review", "pending", "pending")
    return node(f"console.log(JSON.stringify(model.stepBar({json.dumps(listing)}, {json.dumps(entries)}, {json.dumps(status)}, {json.dumps(decisions)})))")


@needs_node
def test_asking_for_changes_keeps_the_step_running_and_the_count_does_not_rise():
    bar = decided_bar({"s1": {"wait_seq": 4, "choice": "changes"}}, review_trail())
    assert [piece["state"] for piece in bar["segments"]] == ["running", "pending", "pending"]
    assert bar["done"] == 0 and bar["line"] == "Step 1 of 3"
    trail = [{"kind": "step", "status": "completed", "step_id": "s2", "seq": 6}, {"kind": "waiting", "step_id": "s2", "seq": 7}]
    last = decided_bar({"s2": {"wait_seq": 7, "choice": "changes"}}, trail, listing=steps("done", "waiting_review"))
    assert last["segments"][1]["state"] == "running" and last["line"] == "Step 2 of 2" and last["done"] == 1


@needs_node
def test_approving_in_this_thread_makes_the_step_done():
    bar = decided_bar({"s1": {"wait_seq": 4, "choice": "approve"}}, review_trail())
    assert [piece["state"] for piece in bar["segments"]] == ["done", "running", "pending"]
    assert bar["done"] == 1


@needs_node
def test_a_decision_nobody_here_knows_draws_no_bar():
    assert decided_bar({}, review_trail()) is None                       # decided in another tab, in the CLI, or the page was reloaded
    assert decided_bar({"s2": {"wait_seq": 4, "choice": "approve"}}, review_trail()) is None        # a decision about another step says nothing about this one
    assert decided_bar({}, review_trail(), status="waiting_human") is not None   # still waiting: the list is fresh


@needs_node
def test_a_completed_message_after_the_wait_proves_the_step_is_done():
    later = review_trail({"kind": "step", "status": "completed", "step_id": "s1", "seq": 9})
    bar = decided_bar({}, later)
    assert [piece["state"] for piece in bar["segments"]] == ["done", "running", "pending"]
    sameSeq = [{"kind": "step", "status": "completed", "step_id": "s1", "seq": 4}, {"kind": "waiting", "step_id": "s1", "seq": 4}]
    assert decided_bar({}, sameSeq) is None                              # not later than the wait: no proof


@needs_node
def test_a_step_that_was_to_be_skipped_ends_as_the_run_does():
    listing = steps("done", "running", "will_skip")
    assert [p["state"] for p in run_bar(listing, status="cancelled")["segments"]] == ["done", "not_run", "not_run"]
    assert [p["state"] for p in run_bar(listing, status="rejected")["segments"]] == ["done", "not_run", "not_run"]
    assert [p["state"] for p in run_bar(listing, status="completed")["segments"]] == ["done", "running", "skipped"]
    assert [p["state"] for p in run_bar(steps("done", "running", "will_skip"), status="running")["segments"]][2] == "will_skip"


@needs_node
def test_a_project_of_many_tasks_never_draws_more_than_sixty_pieces():
    counts = {"done": 900, "doing": 3, "review": 2, "changes": 2, "failed": 1, "blocked": 4, "ready": 5, "todo": 6}
    bar = node(f"console.log(JSON.stringify(model.countPieces({json.dumps(counts)})))")
    assert len(bar["segments"]) == 60 and bar["total"] == 923
    for state in counts:
        assert any(piece["state"] == state for piece in bar["segments"])   # every group keeps at least one piece
    assert bar["segments"][0]["state"] == "done"


@needs_node
def test_a_paused_task_is_named_in_the_line_and_in_the_sentence():
    entries = [plan_entry("T1", "accepted"), plan_entry("T2", "paused"), plan_entry("T3", "running")]
    bar = node(f"console.log(JSON.stringify(model.planBar({json.dumps(entries)})))")
    assert "Paused" in bar["line"] and "Paused" in bar["summary"]
    assert [piece["state"] for piece in bar["segments"]] == ["done", "paused", "doing"]


@needs_node
def test_the_plan_and_the_project_bars_speak_spanish_too():
    entries = [plan_entry("T1", "accepted"), plan_entry("T2", "paused"), plan_entry("T3", "submitted")]
    plan = node(f"console.log(JSON.stringify(model.planBar({json.dumps(entries)})))", "es")
    assert plan["line"].startswith("1 de 3 tareas hechas") and "En pausa" in plan["summary"] and "Entregada" in plan["summary"]
    project = node("console.log(JSON.stringify(model.countPieces({done: 2, review: 1, todo: 2})))", "es")
    assert project["summary"] == "2 de 5 tareas hechas, 1 Espera tu revisión"
    assert project["segments"][-1]["text"] == "Pendiente"


def test_the_acp_threads_draw_no_bar():
    thread = (JS / "thread.js").read_text(encoding="utf-8")
    for name in ("export function AcpThread", "export function AcpStart"):
        body = thread[thread.index(name):]
        body = body[:body.index("\nexport function", 10)] if "\nexport function" in body[10:] else body
        assert "stepBar" not in body and "SegmentBar" not in body and "segbar" not in body
    assert thread.count("<${SegmentBar}") == 1                      # only the run thread draws it here


def test_the_step_texts_the_bar_uses_exist_in_both_languages():
    states = ("done", "skipped", "will_skip", "waiting_review", "waiting_human", "running", "queued", "pending", "failed", "not_run")
    for lang in ("en", "es"):
        text = (STATIC_DIR / "i18n" / lang / "common.js").read_text(encoding="utf-8")
        for state in states:
            assert f'"common.status.step.{state}.label":' in text, (lang, state)
    for lang in ("en", "es"):
        text = (STATIC_DIR / "i18n" / lang / "plans.js").read_text(encoding="utf-8")
        assert '"plans.taskState.paused":' in text


def test_the_run_thread_passes_what_the_person_decided_and_adds_no_call():
    thread = (JS / "thread.js").read_text(encoding="utf-8")
    assert "stepBar(runSteps, thread.entries, thread.status, decisions)" in thread
    assert "[stepId]: { wait_seq: waitSeq, choice: action }" in thread
    run = block(thread, "export function RunThread", "// AVA-T05")
    assert run.count("apiGet(") == 2 and run.count("apiPost(") == 1 and run.count("setInterval(") == 1


# --- round 3: only a review of what the step produced settles it; a decision belongs to its wait ------------------------------

def decision(wait_seq, choice):
    return {"s1": {"wait_seq": wait_seq, "choice": choice}}


def before_run_trail(*extra):
    """A wait that comes before the step runs (tool approval, function review...): no completed message yet."""
    return [{"kind": "waiting", "step_id": "s1", "seq": 2}, *extra]


@needs_node
def test_approving_a_wait_before_the_step_runs_leaves_it_running_and_the_count_does_not_rise():
    listing = steps("waiting_human", "pending", "pending")
    bar = decided_bar(decision(2, "approve"), before_run_trail(), listing=listing)
    assert [piece["state"] for piece in bar["segments"]] == ["running", "pending", "pending"]
    assert bar["done"] == 0 and bar["line"] == "Step 1 of 3"
    queued = decided_bar(decision(2, "approve"), before_run_trail(), status="queued", listing=listing)
    assert queued["segments"][0]["state"] == "queued"
    only = decided_bar(decision(2, "approve"), before_run_trail(), listing=steps("done", "waiting_human"))
    assert only is None                                                   # the decision is about step s1, not s2: unknown
    last = decided_bar({"s2": {"wait_seq": 5, "choice": "approve"}}, [{"kind": "waiting", "step_id": "s2", "seq": 5}],
                       listing=steps("done", "waiting_human"))
    assert last["segments"][1]["state"] == "running" and last["done"] == 1   # never «2 of 2 steps done»


@needs_node
def test_the_completed_message_after_that_approval_makes_it_done():
    listing = steps("waiting_human", "pending")
    bar = decided_bar(decision(2, "approve"), before_run_trail({"kind": "step", "status": "completed", "step_id": "s1", "seq": 6}), listing=listing)
    assert [piece["state"] for piece in bar["segments"]] == ["done", "running"]


@needs_node
def test_a_decision_about_an_older_wait_does_not_answer_for_a_later_one():
    # approved the first wait (seq 2), the step ran and reached a review (seq 7); someone else decided it elsewhere
    trail = [{"kind": "waiting", "step_id": "s1", "seq": 2}, {"kind": "step", "status": "completed", "step_id": "s1", "seq": 6},
             {"kind": "waiting", "step_id": "s1", "seq": 7}]
    assert decided_bar(decision(2, "approve"), trail) is None
    assert decided_bar(decision(7, "approve"), trail)["segments"][0]["state"] == "done"


@needs_node
def test_changes_in_attempt_one_do_not_hide_that_attempt_two_was_decided_elsewhere():
    # changes asked at the wait of seq 4; attempt 2 completed (seq 8) and waited again (seq 9); somebody approved it elsewhere
    trail = [{"kind": "step", "status": "completed", "step_id": "s1", "seq": 3}, {"kind": "waiting", "step_id": "s1", "seq": 4},
             {"kind": "step", "status": "completed", "step_id": "s1", "seq": 8}, {"kind": "waiting", "step_id": "s1", "seq": 9}]
    assert decided_bar(decision(4, "changes"), trail) is None


@needs_node
def test_a_wait_without_a_step_id_does_not_let_the_older_completed_message_through():
    trail = [{"kind": "step", "status": "completed", "step_id": "s1", "seq": 3}, {"kind": "waiting", "step_id": "", "seq": 5, "reason": "budget_exhausted"}]
    assert decided_bar({}, trail) is None                                # the step is the one the list had waiting: not proven, unknown
    two = decided_bar({}, trail, listing=steps("waiting_review", "waiting_human", "pending"))
    assert two is None                                                   # two candidates: whose wait is it? nobody knows
    seen = decided_bar({}, trail + [{"kind": "step", "status": "completed", "step_id": "s1", "seq": 9}])
    assert seen["segments"][0]["state"] == "done"                        # a later completed message settles it
    older = [{"kind": "waiting", "step_id": "", "seq": 1}, {"kind": "step", "status": "completed", "step_id": "s1", "seq": 3}]
    assert decided_bar({}, older)["segments"][0]["state"] == "done"      # a wait that came before the completed message does not matter


@needs_node
def test_the_wait_a_decision_is_about_is_the_last_one_heard_for_the_step():
    entries = [{"kind": "waiting", "step_id": "s1", "seq": 2}, {"kind": "waiting", "step_id": "s2", "seq": 9}, {"kind": "waiting", "step_id": "s1", "seq": 5}]
    seqs = node(f"console.log(JSON.stringify([model.waitSeqOf({json.dumps(entries)}, 's1'), model.waitSeqOf({json.dumps(entries)}, 's3'), model.waitSeqOf(null, 's1')]))")
    assert seqs == [5, None, None]


def test_the_decision_card_and_the_thread_tie_the_decision_to_its_wait():
    cards = (JS / "thread-cards.js").read_text(encoding="utf-8")
    assert "onAction(pending, body)" in cards                         # the card is as it was; the thread says which wait it decides
    thread = (JS / "thread.js").read_text(encoding="utf-8")
    assert "async function act(action, body, where = null)" in thread
    assert thread.count("onAction=${actFor(") == 2 and "waitSeq: Number(entry.seq)" in thread
    assert "waitSeqOf(thread.entries, stepId)" in thread and "reviewView?.waiting?.step_id" in thread


# --- AVZ-T05: the thread page brings the steps; the project card shows the weighted figure ----------------------------------

def test_the_run_thread_takes_the_steps_of_its_page_and_adds_no_call():
    thread = (JS / "thread.js").read_text(encoding="utf-8")
    run = block(thread, "export function RunThread", "// AVA-T05")
    assert "if (Array.isArray(page.steps)) setRunSteps(page.steps);" in run
    assert run.index("setRunSteps(view.steps)") < run.index("setRunSteps(page.steps)")   # the page's own list wins
    assert run.count("apiGet(") == 2 and run.count("apiPost(") == 1 and run.count("setInterval(") == 1


@needs_node
def test_a_run_without_waits_has_a_bar_from_the_page_steps():
    bar = run_bar(steps("done", "running", "pending", "pending", "pending"))
    assert len(bar["segments"]) == 5 and bar["line"] == "Step 2 of 5"


def test_the_project_card_shows_the_weighted_figure_only_with_data():
    index = (STATIC_DIR / "extensions" / "project" / "index.js").read_text(encoding="utf-8")
    slot = block(index, "export function ProjectSlot", "registerView(")
    assert "overviewFigures(chosen).percent" in slot
    assert '${weighted !== null && html`' in slot
    assert 't("ext.project.figures.weighted")' in slot and 't("ext.project.figures.percent", { percent: weighted })' in slot
    assert "<${Progress}" in slot and "countPieces(chosen.counts)" in slot and slot.count("apiGet(") == 1


@needs_node
def test_the_weighted_figure_of_the_card_is_null_without_data():
    uri = (STATIC_DIR / "extensions" / "project" / "model.js").as_uri()
    code = (f"import * as m from {json.dumps(uri)};"
            "console.log(JSON.stringify([m.overviewFigures({progress_weighted:{percent:40}}).percent,"
            "m.overviewFigures({}).percent, m.overviewFigures({progress_weighted:{percent:null}}).percent]));")
    result = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [40, None, None]


# --- AVZ-T05 round 2: the fresh list of every page, as `_steps` shapes it, keeps the rule of a step that had a wait ----------------

def review_wait(step="s1", seq=4):
    return [{"kind": "step", "status": "completed", "step_id": step, "seq": seq - 1}, {"kind": "waiting", "step_id": step, "seq": seq}]


@needs_node
def test_after_asking_for_changes_here_a_fresh_done_is_not_shown_as_done_and_the_next_step_is_not_running():
    # `_steps` keeps saying done for s1 (its first attempt completed) while it runs again after the changes
    bar = decided_bar({"s1": {"wait_seq": 4, "choice": "changes"}}, review_wait(), status="running", listing=steps("done", "pending", "pending"))
    assert [piece["state"] for piece in bar["segments"]] == ["running", "pending", "pending"] and bar["done"] == 0
    assert bar["line"] == "Step 1 of 3"
    later = review_wait() + [{"kind": "step", "status": "completed", "step_id": "s1", "seq": 6}]
    bar = decided_bar({"s1": {"wait_seq": 4, "choice": "changes"}}, later, status="running", listing=steps("done", "running", "pending"))
    assert [piece["state"] for piece in bar["segments"]] == ["done", "running", "pending"]   # the completed message after the wait proves it


@needs_node
def test_after_approving_here_a_fresh_done_counts_as_done():
    bar = decided_bar({"s1": {"wait_seq": 4, "choice": "approve"}}, review_wait(), status="running", listing=steps("done", "running", "pending"))
    assert [piece["state"] for piece in bar["segments"]] == ["done", "running", "pending"] and bar["done"] == 1


@needs_node
def test_a_decision_taken_elsewhere_hides_the_bar_for_a_fresh_done_running_or_queued_step():
    for first in ("done", "running", "queued"):
        listing = steps(first, "pending", "pending")
        assert node(f"console.log(JSON.stringify(model.stepBar({json.dumps(listing)}, {json.dumps(review_wait())}, 'running', {{}})))") is None
    stale = {"s1": {"wait_seq": 2, "choice": "approve"}}   # about an older wait: says nothing about this one
    assert decided_bar(stale, review_wait(), status="running", listing=steps("done", "running", "pending")) is None


@needs_node
def test_a_step_that_never_had_a_wait_in_the_thread_uses_the_fresh_list_as_it_is():
    bar = decided_bar({}, [{"kind": "launched", "seq": 1}, {"kind": "step", "status": "completed", "step_id": "s1", "seq": 2}],
                      status="running", listing=steps("done", "running", "pending", "pending", "pending"))
    assert [piece["state"] for piece in bar["segments"]][:2] == ["done", "running"] and bar["total"] == 5


# --- AVZ-T05 round 3: an earlier wait holds only until something later goes past it -----------------------------------------------

@needs_node
def test_a_review_approved_elsewhere_hides_the_bar_while_the_next_step_runs_and_it_comes_back_at_the_next_stop():
    trail = review_wait()                                            # s1 completed (3), then waited (4); approved outside, no decisions
    assert decided_bar({}, trail, status="running", listing=steps("done", "running", "pending")) is None
    for waiting in ("waiting_review", "waiting_human"):
        bar = decided_bar({}, trail + [{"kind": "waiting", "step_id": "s2", "seq": 6}], status="waiting_human",
                          listing=steps("done", waiting, "pending"))
        assert bar is not None and bar["segments"][0]["state"] == "done" and bar["segments"][1]["state"] == waiting
    heard = decided_bar({}, trail + [{"kind": "step", "status": "completed", "step_id": "s2", "seq": 6}], status="running",
                        listing=steps("done", "running", "pending"))
    assert heard is not None and [piece["state"] for piece in heard["segments"]] == ["done", "done", "running"]   # the completed message of s2 proves it


@needs_node
def test_the_bar_goes_on_when_the_next_review_is_approved_in_the_thread_and_the_step_after_it_starts():
    trail = review_wait() + [{"kind": "step", "status": "completed", "step_id": "s2", "seq": 6}, {"kind": "waiting", "step_id": "s2", "seq": 7}]
    bar = decided_bar({"s2": {"wait_seq": 7, "choice": "approve"}}, trail, status="running", listing=steps("done", "done", "running"))
    assert [piece["state"] for piece in bar["segments"]] == ["done", "done", "running"] and bar["done"] == 2


@needs_node
def test_a_review_approved_elsewhere_and_then_a_failure_brings_the_bar_back():
    bar = decided_bar({}, review_wait(), status="failed", listing=steps("done", "failed", "not_run"))
    assert bar is not None and bar["segments"][1]["state"] == "failed" and bar["line"] == "Step 2 of 3 · Failed"


@needs_node
def test_approving_a_wait_before_the_step_runs_in_a_retry_does_not_make_it_done():
    trail = review_wait() + [{"kind": "waiting", "step_id": "s1", "seq": 6, "reason": "tool_approval"}]   # attempt 1 done, changes, attempt 2 asks
    decided = {"s1": {"wait_seq": 6, "choice": "approve"}}
    running = decided_bar(decided, trail, status="running", listing=steps("done", "pending", "pending"))
    queued = decided_bar(decided, trail, status="queued", listing=steps("done", "pending", "pending"))
    assert running["segments"][0]["state"] == "running" and queued["segments"][0]["state"] == "queued" and running["done"] == 0
    after_review = review_wait() + [{"kind": "waiting", "step_id": "s1", "seq": 6, "reason": "review"}]
    assert decided_bar(decided, after_review, status="running", listing=steps("done", "running", "pending"))["segments"][0]["state"] == "done"
