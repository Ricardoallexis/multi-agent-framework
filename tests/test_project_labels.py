# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared labels, dates and the flat List view of a project (PRY-T05).

One map of labels (multiagent/tasks/labels.json) serves the services, the GUI project view and the Seguimiento tracker; the
List view (``ProjectServices.task_list`` and the «Lista» tab) filters by owner, state and due date and never invents a date.
Synthetic data only, in temporary folders, in Mock mode.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from multiagent.gui import STATIC_DIR
from multiagent.services import ServiceError
from multiagent.tasks import labels
from test_gui_help import NODE
from test_projects_service import AGENT, OWNER, authorize, error_of, make_project, make_services, plan_doc

ROOT = Path(__file__).parents[1]
PROJECT_DIR = STATIC_DIR / "extensions" / "project"
VIEWER = ROOT / "tools" / "seguimiento" / "ver-seguimiento.ps1"
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def services(tmp_path):
    found = make_services(tmp_path / "local")
    yield found
    found.runs.close()


@pytest.fixture
def project(services):
    make_project(services)
    authorize(services, "mkt-INV", "mkt-CON")
    return services


# --- one shared map ---------------------------------------------------------------------------------------------------------

def test_the_service_gives_the_same_map_as_the_core_file(services):
    assert services.projects.labels() == labels.load()


def test_every_label_of_a_view_is_an_entry_of_the_shared_map(project):
    shared = labels.load()
    view = project.projects.get("mkt")
    for stage in view["stages"]:
        assert stage["label"]["text"] == shared["stage"][stage["label"]["key"]]["text"]
        for task in stage["tasks"]:
            entry = shared["task"][task["label"]["key"]]
            assert (task["label"]["text"], task["label"]["tone"]) == (entry["text"], entry["tone"])


def test_the_list_rows_carry_the_shared_label(project):
    shared = labels.load()["task"]
    for row in project.projects.task_list("mkt")["tasks"]:
        assert row["label"]["text"] == shared[row["label"]["key"]]["text"]


def test_the_tracker_reads_its_words_from_the_shared_map():
    script = VIEWER.read_text(encoding="utf-8")
    assert "multiagent/tasks/labels.json" in script
    used = re.findall(r"Get-SharedLabelText '(task|stage)' '(\w+)'", script)
    assert used, "the tracker must take label words from the shared map"
    shared = labels.load()
    assert all(key in shared[scope] for scope, key in used)


def test_the_page_keeps_no_copy_of_the_map():
    shared = labels.load()
    texts = {entry["text"] for scope in ("task", "stage") for entry in shared[scope].values()}
    for name in ("index.js", "parts.js", "model.js"):
        code = (PROJECT_DIR / name).read_text(encoding="utf-8")
        assert not any(f'"{text}"' in code for text in texts), name


# --- the List view ----------------------------------------------------------------------------------------------------------

def test_the_list_is_flat_and_every_row_knows_its_stage_and_planned_dates(project):
    result = project.projects.task_list("mkt")
    assert result["total"] == 4 and [row["id"] for row in result["tasks"]] == [
        "mkt-INV-T01", "mkt-INV-T02", "mkt-CON-T01", "mkt-CON-T02"]
    first, last = result["tasks"][0], result["tasks"][-1]
    assert (first["stage_code"], first["planned_start"], first["planned_end"]) == ("INV", "2026-11-01", "2026-11-15")
    assert (last["stage_code"], last["planned_start"], last["planned_end"]) == ("CON", None, None)
    assert result["owners"] == [AGENT, OWNER] and result["states"] == ["blocked", "ready"]


def test_filter_by_owner_state_and_date(project):
    lst = project.projects.task_list
    assert [r["id"] for r in lst("mkt", owner=AGENT)["tasks"]] == ["mkt-INV-T01"]
    assert [r["id"] for r in lst("mkt", owner=OWNER)["tasks"]] == ["mkt-CON-T01"]
    assert [r["id"] for r in lst("mkt", state="ready")["tasks"]] == ["mkt-INV-T01"]
    assert [r["id"] for r in lst("mkt", state="blocked")["tasks"]] == ["mkt-INV-T02", "mkt-CON-T01", "mkt-CON-T02"]
    assert [r["id"] for r in lst("mkt", due_from="2026-11-10", due_to="2026-11-10")["tasks"]] == ["mkt-INV-T02"]
    assert lst("mkt", due_from="2026-11-11")["tasks"] == []
    assert [r["id"] for r in lst("mkt", state="blocked", owner=OWNER)["tasks"]] == ["mkt-CON-T01"]
    assert lst("mkt", state="blocked", owner=AGENT)["tasks"] == []
    assert lst("mkt", owner=AGENT)["total"] == 4          # the total and the choices ignore the filters


def test_a_task_without_a_due_date_is_never_given_one(project):
    rows = {row["id"]: row for row in project.projects.task_list("mkt")["tasks"]}
    assert rows["mkt-INV-T02"]["due_at"] == "2026-11-10"
    assert all(rows[key]["due_at"] is None for key in ("mkt-INV-T01", "mkt-CON-T01", "mkt-CON-T02"))
    # a date filter leaves them out instead of treating them as "any date"
    assert "mkt-INV-T01" not in {r["id"] for r in project.projects.task_list("mkt", due_from="2000-01-01")["tasks"]}


def test_a_project_without_any_planned_date_shows_none(services):
    doc = plan_doc()
    for stage in doc["stages"]:
        stage.pop("planned", None)
        for task in stage["tasks"]:
            task.pop("due", None)
    make_project(services, doc=doc)
    rows = services.projects.task_list("mkt")["tasks"]
    assert rows and all(r["due_at"] is None and r["planned_start"] is None and r["planned_end"] is None for r in rows)


def test_bad_filters_and_unknown_projects_are_refused(project):
    lst = project.projects.task_list
    assert error_of(lst, "mkt", state="almost").details["field"] == "state"
    assert error_of(lst, "mkt", due_from="tomorrow").details["field"] == "due_from"
    assert error_of(lst, "mkt", due_to="2026-02-30").details["field"] == "due_to"
    missing = error_of(lst, "nope")
    assert isinstance(missing, ServiceError) and missing.status == 404


def test_the_list_tab_is_wired_to_the_page():
    index = (PROJECT_DIR / "index.js").read_text(encoding="utf-8")
    parts = (PROJECT_DIR / "parts.js").read_text(encoding="utf-8")
    assert '{ id: "list", label: "Lista plana" }' in index and "ListPanel" in index and "export function ListPanel" in parts


@needs_node
def test_the_page_rules_for_the_list_match_the_service(project):
    view = project.projects.get("mkt")
    lst = project.projects.task_list
    expected = {"owner": [r["id"] for r in lst("mkt", owner=AGENT)["tasks"]],
                "state": [r["id"] for r in lst("mkt", state="blocked")["tasks"]],
                "dates": [r["id"] for r in lst("mkt", due_from="2026-11-01", due_to="2026-11-30")["tasks"]]}
    script = f"""
      import {{ listRows, listOwners, matchesList }} from "{(PROJECT_DIR / 'model.js').as_uri()}";
      const rows = listRows({json.dumps(view)});
      const ids = (f) => rows.filter((r) => matchesList(r, f)).map((r) => r.id);
      console.log(JSON.stringify({{
        all: rows.map((r) => r.id), owner: ids({{ owner: {json.dumps(AGENT)} }}), state: ids({{ label: "blocked" }}),
        dates: ids({{ from: "2026-11-01", to: "2026-11-30" }}), owners: listOwners(rows).map((o) => o.value),
        first: [rows[0].planned_start, rows[3].planned_start]
      }}));"""
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["all"] == [r["id"] for r in lst("mkt")["tasks"]]
    assert (out["owner"], out["state"], out["dates"]) == (expected["owner"], expected["state"], expected["dates"])
    assert sorted(out["owners"]) == [AGENT, OWNER] and out["first"] == ["2026-11-01", None]
