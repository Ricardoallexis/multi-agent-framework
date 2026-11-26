# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Weighted progress by stored state, ``remaining`` and ``last_change`` of a project view (AVZ-T01).

The weights are data in ``multiagent/tasks/labels.json``; ``progress`` and its ``percent`` do not change.
Synthetic data only, in temporary folders, in Mock mode.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from multiagent.tasks import labels
from test_projects_service import AGENT, OWNER, authorize, deliver, make_project, make_services, plan_doc

STEPS = {"open": 0, "claimed": 10, "running": 30, "paused": 30, "validating": 60, "needs_changes": 60,
         "submitted": 80, "accepted": 100}


def doc(tasks: list[dict]) -> dict:
    return plan_doc(stages=[{"code": "ST", "title": "Only stage", "tasks": tasks}])


@pytest.fixture
def services(tmp_path):
    found = make_services(tmp_path / "local")
    yield found
    found.runs.close()


# --- the pure weights -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("state,percent", list(STEPS.items()))
def test_every_step_has_its_percent(state, percent):
    assert labels.progress_weighted([state]) == {"percent": percent, "counted": 1}


def test_a_released_submitted_task_weighs_as_accepted():
    assert labels.progress_weighted([("submitted", True)])["percent"] == 100
    assert labels.progress_weighted([("submitted", False)])["percent"] == 80
    assert labels.weight_of("submitted", released=True) == 1


def test_cancelled_is_out_of_the_count_and_failed_is_in_with_zero():
    assert labels.progress_weighted(["accepted", "cancelled"]) == {"percent": 100, "counted": 1}
    assert labels.progress_weighted(["accepted", "failed"]) == {"percent": 50, "counted": 2}


def test_a_mix_rounds_half_up_like_progress():
    assert labels.progress_weighted(["accepted", "submitted", "running", "open"]) == {"percent": 53, "counted": 4}
    assert labels.progress_weighted(["claimed", "open", "open"])["percent"] == 3  # 3.33
    assert labels.progress_weighted(["claimed", "open"])["percent"] == 5  # 5.0
    assert labels.progress_weighted(["running"] + ["open"] * 7)["percent"] == 4  # 3.75


def test_nothing_counted_gives_none():
    assert labels.progress_weighted([]) == {"percent": None, "counted": 0}
    assert labels.progress_weighted(["cancelled", "cancelled"]) == {"percent": None, "counted": 0}


def test_an_unknown_state_is_an_error_not_a_zero():
    with pytest.raises(ValueError, match="No weight"):
        labels.progress_weighted(["accepted", "dreaming"])
    with pytest.raises(ValueError, match="No weight"):
        labels.weight_of("dreaming")


def test_the_percent_of_progress_is_the_one_of_before():
    assert labels.progress(["done", "review", "doing", "todo"])["percent"] == 25
    assert labels.progress(["done", "cancelled"]) == {"done": 1, "cancelled": 1, "total": 2, "percent": 100}
    assert labels.progress([])["percent"] is None


# --- the file is validated --------------------------------------------------------------------------------------------------

def load_with(monkeypatch, change):
    data = json.loads((Path(labels.__file__).with_name("labels.json")).read_text(encoding="utf-8"))
    change(data)

    class FakePath:
        def __init__(self, *_):
            pass

        def with_name(self, _):
            return self

        def read_text(self, **_):
            return json.dumps(data)

    monkeypatch.setattr(labels, "Path", FakePath)
    return labels._load.__wrapped__()


def test_the_shipped_file_has_every_state_and_accepted_is_one():
    weights = labels.load()["weights"]
    assert set(weights) == set(labels.STORED_STATES) and weights["accepted"] == 1


@pytest.mark.parametrize("change,message", [
    (lambda d: d.pop("weights"), "needs a weights map"),
    (lambda d: d["weights"].pop("running"), r"missing: \['running'\]"),
    (lambda d: d["weights"].update(dreaming=0.5), r"unknown: \['dreaming'\]"),
    (lambda d: d["weights"].update(running=1.5), "weights.running must be a number between 0 and 1"),
    (lambda d: d["weights"].update(running=-0.1), "weights.running must be a number between 0 and 1"),
    (lambda d: d["weights"].update(running="0.3"), "weights.running must be a number between 0 and 1"),
    (lambda d: d["weights"].update(running=0.333), "at most two decimals"),
    (lambda d: d["weights"].update(accepted=0.9), "weights.accepted must be 1"),
])
def test_a_bad_weights_map_is_refused_with_a_clear_message(monkeypatch, change, message):
    with pytest.raises(ValueError, match=message):
        load_with(monkeypatch, change)


def test_the_task_and_stage_entries_keep_their_shape():
    data = labels.load()
    assert list(data["task"]) == list(labels.TASK_KEYS) and list(data["stage"]) == list(labels.STAGE_KEYS)
    assert all(set(entry) == {"text", "tone", "explanation"}
               for scope in ("task", "stage") for entry in data[scope].values())


# --- the project view -------------------------------------------------------------------------------------------------------

FOUR = [{"id": f"T0{n}", "title": f"Task {n}", "assignee": AGENT} for n in range(1, 5)]


def four_tasks(services) -> dict:
    make_project(services, doc=doc(FOUR))
    authorize(services, "mkt-ST")
    deliver(services, "mkt-ST-T01")
    deliver(services, "mkt-ST-T02", accept=False)
    services.tasks.transition("mkt-ST-T03", "claim", actor=AGENT)
    services.tasks.transition("mkt-ST-T03", "start", actor=AGENT)
    return services.projects.get("mkt")


def test_four_tasks_give_25_and_53_then_50_and_58(services):
    view = four_tasks(services)
    assert view["progress"]["percent"] == 25
    assert view["progress_weighted"] == {"percent": 53, "counted": 4}
    assert view["stages"][0]["progress_weighted"]["percent"] == 53
    services.tasks.transition("mkt-ST-T02", "accept", actor=OWNER, cause="reviewed")
    view = services.projects.get("mkt")
    assert view["progress"]["percent"] == 50
    assert view["progress_weighted"]["percent"] == 58


def test_the_bar_goes_down_when_review_asks_for_changes(services):
    four_tasks(services)
    services.tasks.transition("mkt-ST-T02", "request_changes", actor=OWNER, cause="more detail")
    assert services.projects.get("mkt")["progress_weighted"]["percent"] == 48  # 100 + 60 + 30 + 0 = 190 of 400


def test_a_project_with_every_task_cancelled_has_no_percent(services):
    make_project(services, doc=doc(FOUR[:1]))
    authorize(services, "mkt-ST")
    services.tasks.transition("mkt-ST-T01", "cancel", actor=OWNER, cause="not needed")
    view = services.projects.get("mkt")
    assert view["progress_weighted"] == {"percent": None, "counted": 0}
    assert view["remaining"] == {"tasks": 0, "layers": 0}


def test_remaining_counts_unfinished_tasks_and_parallel_layers(services):
    tasks = [{"id": "T01", "title": "First", "assignee": AGENT},
             {"id": "T02", "title": "Second", "assignee": AGENT},
             {"id": "T03", "title": "Third", "assignee": AGENT, "depends_on": ["T01", "T02"]}]
    make_project(services, doc=doc(tasks))
    authorize(services, "mkt-ST")
    view = services.projects.get("mkt")
    assert view["remaining"] == {"tasks": 3, "layers": 2}
    assert view["stages"][0]["remaining"] == {"tasks": 3, "layers": 2}
    deliver(services, "mkt-ST-T01")
    deliver(services, "mkt-ST-T02")
    assert services.projects.get("mkt")["remaining"] == {"tasks": 1, "layers": 1}
    deliver(services, "mkt-ST-T03")
    assert services.projects.get("mkt")["remaining"] == {"tasks": 0, "layers": 0}


def test_last_change_is_the_last_team_event_of_the_project(services):
    make_project(services, doc=doc(FOUR[:1]))
    first = services.projects.get("mkt")
    assert first["last_change"] is not None and first["last_change"]["seq"] >= 1
    assert first["last_change"]["created_at"]
    authorize(services, "mkt-ST")
    deliver(services, "mkt-ST-T01")
    view = services.projects.get("mkt")
    assert view["last_change"]["seq"] > first["last_change"]["seq"]
    assert view["stages"][0]["last_change"]["seq"] == view["last_change"]["seq"]
    last = services.tasks.events(project_id="mkt")[-1]
    assert view["last_change"]["seq"] == last["seq"]


def test_last_change_is_null_without_events(services):
    make_project(services, doc=doc(FOUR[:1]))
    with services.projects.store.db.connect() as conn:
        conn.execute("DELETE FROM team_events")
        view = services.projects._project_view_in(conn, "mkt")
    assert view["last_change"] is None and view["stages"][0]["last_change"] is None
