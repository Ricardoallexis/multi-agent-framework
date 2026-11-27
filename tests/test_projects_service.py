# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project services (PRY-T02): migration 011, plans, views, labels, stages, events and facade/HTTP/CLI parity.

Everything runs on synthetic data in temporary folders, in Mock mode (no model, no agent application).
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db import Database
from multiagent.db.store import _canonical_request_fingerprint
from multiagent.project_templates import (TemplateError, TemplateNotFound, list_templates, load_template,
                                          parse_template, validate_template)
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ProjectServices, ServiceError
from multiagent.services import projects as projects_module
from multiagent.services.errors import service_errors, translate
from multiagent.tasks import labels
from session_fixtures import (CLAUDE_ID, make_services as make_session_services, make_stores,
                              write_claude)

MIGRATIONS = Path(__file__).parents[1] / "migrations"
BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
SYNTHETIC_PROJECT = Path(__file__).parent / "fixtures" / "projects" / "proyecto-sintetico.yaml"
OWNER = "user:ana"
AGENT = "agent:writer/m1"
PLAN = {
    "schema": "maf.project-template/1", "id": "synthetic-campaign", "title": "Synthetic campaign",
    "description": "A made-up plan for tests.",
    "stages": [
        {"code": "INV", "title": "Research", "phases": ["Sources", "Analysis"],
         "planned": {"start": "2026-11-01", "end": "2026-11-15"},
         "tasks": [
             {"id": "T01", "title": "Audience study", "objective": "Find three segments.", "phase": "Sources",
              "assignee": AGENT, "done_when": "A report with 3 segments exists", "context_refs": ["doc://brief.md"]},
             {"id": "T02", "title": "Competitors", "phase": "Analysis", "depends_on": ["T01"], "due": "2026-11-10"}]},
        {"code": "CON", "title": "Content", "tasks": [
            {"id": "T01", "title": "Calendar", "depends_on": ["stage:INV"], "assignee": "user:ana"},
            {"id": "T02", "title": "Posts", "depends_on": ["T01", "INV-T02"], "requires": "submitted"}]},
    ],
}


def make_services(root: Path, **options) -> ApplicationServices:
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    return ApplicationServices.create(settings, dry_run=True, **options)


@pytest.fixture
def services(tmp_path):
    found = make_services(tmp_path / "local")
    yield found
    found.runs.close()


def plan_doc(**changes: Any) -> dict[str, Any]:
    doc = copy.deepcopy(PLAN)
    doc.update(changes)
    return doc


def make_project(svc: ApplicationServices, key: str = "mkt", doc: dict | None = None, *, actor: str = OWNER,
                 **options: Any) -> dict[str, Any]:
    doc = doc if doc is not None else plan_doc()
    plan = svc.projects.plan(doc, key=key, **options)
    return svc.projects.create(doc, key=key, plan_sha256=plan["plan_sha256"], actor=actor, **options)


def error_of(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def authorize(svc: ApplicationServices, *group_ids: str) -> None:
    for group_id in group_ids:
        svc.tasks.update_group(group_id, actor=OWNER, authorized=True)


def deliver(svc: ApplicationServices, task_id: str, *, agent: str = AGENT, result: dict | None = None,
            accept: bool = True) -> None:
    """The task goes claim -> start -> result (which submits it) -> accept by the user."""
    svc.tasks.transition(task_id, "claim", actor=agent)
    svc.tasks.transition(task_id, "start", actor=agent)
    svc.envelopes.submit_result(task_id, result or {"summary": f"Done {task_id}."}, actor=agent)
    if accept:
        svc.tasks.transition(task_id, "accept", actor=OWNER, cause="reviewed")


def stage_of(view: dict, group_id: str) -> dict:
    return next(stage for stage in view["stages"] if stage["group_id"] == group_id)


def columns(db: Database, table: str) -> dict[str, sqlite3.Row]:
    with db.connect() as conn:
        return {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def event_types(svc: ApplicationServices, **filters: Any) -> list[str]:
    return [event["type"] for event in svc.tasks.events(**filters)]


# ---------------------------------------------------------------------------------------------------------------
# Migration 011
# ---------------------------------------------------------------------------------------------------------------

def legacy_database(tmp_path: Path, through: int = 10) -> tuple[Settings, Database]:
    """A database at the schema of migrations 001..``through`` in the place the services will open."""
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    settings.ensure_directories()
    old = tmp_path / "old-migrations"
    old.mkdir()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if int(path.stem.split("_", 1)[0]) <= through:
            shutil.copyfile(path, old / path.name)
    db = Database(SimpleNamespace(data_dir=settings.data_dir, db_path=settings.db_path, migrations_dir=old))
    db.migrate()
    return settings, db


def test_migration_011_on_an_empty_database_adds_the_columns_tables_and_indexes(services):
    db = services.system.db
    assert db.schema_version() == max(int(p.stem.split("_", 1)[0]) for p in MIGRATIONS.glob("*.sql")) >= 11
    assert {"title", "visibility", "archived_at", "created_by", "revision"} <= set(columns(db, "projects"))
    assert {"project_id", "position", "phases_json", "planned_start", "planned_end"} <= set(columns(db, "task_groups"))
    assert {"phase", "due_at", "kind"} <= set(columns(db, "tasks"))
    assert "kind" in columns(db, "task_groups")
    assert "task_id" in columns(db, "runs") and "task_id" in columns(db, "telemetry")
    assert set(columns(db, "task_session_links")) == {"task_id", "session_id", "linked_by", "created_at"}
    with db.connect() as conn:
        indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_task_groups_project_position", "idx_tasks_due", "idx_runs_task", "idx_telemetry_task",
            "idx_task_session_links_session"} <= indexes


def test_migration_011_keeps_a_populated_010_database_valid(tmp_path):
    settings, old = legacy_database(tmp_path)
    assert old.schema_version() == 10
    with old.connect() as conn:
        conn.executescript("""
            INSERT INTO projects(id,name,created_at) VALUES('p-uuid-1','Spring launch','2026-01-01T00:00:00+00:00');
            INSERT INTO projects(id,name,created_at) VALUES('p-uuid-2','Docs','2026-01-02T00:00:00+00:00');
            INSERT INTO runs(id,project_id,workflow_id,status,request_json,max_llm_calls,max_run_seconds,created_at,updated_at)
                VALUES('r1','p-uuid-1','social_post','completed','{}',4,60,'2026-01-03T00:00:00+00:00','2026-01-03T00:00:00+00:00');
            INSERT INTO telemetry(run_id,step_id,provider,model,prompt_id,prompt_version,success,created_at)
                VALUES('r1','s0','fake','m','p',1,1,'2026-01-03T00:00:00+00:00');
            INSERT INTO task_groups(id,title,authorized,created_by,created_at,updated_at)
                VALUES('ORQ','Orchestration',1,'user','t','t');
            INSERT INTO tasks(id,group_id,title,created_by,created_at,updated_at) VALUES('ORQ-T01','ORQ','Old','user','t','t');
            INSERT INTO tasks(id,group_id,title,state,created_by,created_at,updated_at)
                VALUES('ORQ-T02','ORQ','Older','accepted','user','t','t');
            INSERT INTO task_deps(task_id,depends_on_id,requires,created_at) VALUES('ORQ-T01','ORQ-T02','accepted','t');
            INSERT INTO team_events(type,group_id,task_id,actor,created_at) VALUES('task.created','ORQ','ORQ-T01','user','t');
        """)
        conn.commit()
        before = {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                  for table in ("projects", "runs", "telemetry", "task_groups", "tasks", "task_deps", "team_events")}
    svc = ApplicationServices.create(settings, dry_run=True)   # the system applies migration 011 on start
    try:
        assert svc.system.db.schema_version() >= 11
        with svc.system.db.connect() as conn:
            for table, rows in before.items():
                # the old columns of every old row are untouched (new columns come after them)
                kept = [tuple(row)[:len(rows[0])] for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                assert kept == rows, table
            projects = [dict(row) for row in conn.execute("SELECT * FROM projects ORDER BY rowid")]
            group = dict(conn.execute("SELECT * FROM task_groups WHERE id='ORQ'").fetchone())
            task = dict(conn.execute("SELECT * FROM tasks WHERE id='ORQ-T01'").fetchone())
            run = dict(conn.execute("SELECT * FROM runs WHERE id='r1'").fetchone())
            telemetry = dict(conn.execute("SELECT * FROM telemetry").fetchone())
        # the only backfill is title = name; everything else is "no data"
        assert [(p["id"], p["title"], p["visibility"], p["archived_at"], p["created_by"], p["revision"])
                for p in projects] == [("p-uuid-1", "Spring launch", "normal", None, "", 1),
                                       ("p-uuid-2", "Docs", "normal", None, "", 1)]
        assert (group["project_id"], group["position"], group["phases_json"], group["planned_start"],
                group["planned_end"]) == (None, None, "[]", None, None)
        assert (task["phase"], task["due_at"]) == ("", None)
        # amendment 1 (P-518): every existing row is a plain task or a stage
        assert (task["kind"], group["kind"]) == ("task", "stage")
        with svc.system.db.connect() as conn:
            assert {row["kind"] for row in conn.execute("SELECT kind FROM tasks")} == {"task"}
            assert {row["kind"] for row in conn.execute("SELECT kind FROM task_groups")} == {"stage"}
        assert run["task_id"] is None and telemetry["task_id"] is None
        # the old data still reads through the services, and the legacy projects are listed without stages
        assert [t["id"] for t in svc.tasks.list_tasks(group_id="ORQ")] == ["ORQ-T01", "ORQ-T02"]
        assert svc.tasks.get_group("ORQ")["project_id"] is None
        assert [(p["id"], p["title"], p["stages"]) for p in svc.projects.list()] == [
            ("p-uuid-1", "Spring launch", 0), ("p-uuid-2", "Docs", 0)]
        legacy = svc.projects.get("p-uuid-1")
        assert legacy["stages"] == [] and legacy["progress"]["percent"] is None
        # and the new projects can live next to them
        assert make_project(svc, "spring")["id"] == "spring"
    finally:
        svc.runs.close()


def test_migration_011_constraints_protect_the_new_columns(services):
    make_project(services)
    with services.system.db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):   # a position is unique inside a project
            conn.execute("UPDATE task_groups SET position=1 WHERE id='mkt-CON'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE task_groups SET planned_start='11/01/2026' WHERE id='mkt-CON'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET due_at='tomorrow' WHERE id='mkt-CON-T01'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE projects SET visibility='public' WHERE id='mkt'")
        with pytest.raises(sqlite3.IntegrityError):   # amendment 1: the only kinds there are
            conn.execute("UPDATE tasks SET kind='chore' WHERE id='mkt-CON-T01'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE task_groups SET kind='activity' WHERE id='mkt-CON'")
        conn.execute("UPDATE tasks SET kind='activity' WHERE id='mkt-CON-T01'")    # the legal values are accepted
        conn.execute("UPDATE task_groups SET kind='activities' WHERE id='mkt-CON'")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE task_groups SET position=0 WHERE id='mkt-CON'")
        with pytest.raises(sqlite3.IntegrityError):   # a stage cannot point to a project that does not exist
            conn.execute("UPDATE task_groups SET project_id='ghost' WHERE id='mkt-CON'")


def test_every_new_row_is_a_task_in_a_stage_and_the_views_say_so(services):
    view = make_project(services)
    assert {stage["kind"] for stage in view["stages"]} == {"stage"}
    assert {task["kind"] for stage in view["stages"] for task in stage["tasks"]} == {"task"}
    assert services.tasks.get_task("mkt-INV-T01")["kind"] == "task"
    assert services.tasks.get_group("mkt-INV")["kind"] == "stage"
    services.tasks.create_group("OLD", "Created by hand", actor="user")
    services.tasks.create_task("OLD", "OLD-T01", "By hand", actor="user")
    assert (services.tasks.get_group("OLD")["kind"], services.tasks.get_task("OLD-T01")["kind"]) == ("stage", "task")
    assert {g["kind"] for g in services.tasks.list_groups()} == {"stage"}
    assert {t["kind"] for t in services.tasks.list_tasks()} == {"task"}
    # the columns are stored data only: nothing about stages, progress or closing changes with them yet
    with services.system.db.connect() as conn:
        assert {r["kind"] for r in conn.execute("SELECT kind FROM tasks")} == {"task"}


# ---------------------------------------------------------------------------------------------------------------
# The shared map of labels
# ---------------------------------------------------------------------------------------------------------------

def test_the_label_map_has_the_eleven_labels_and_every_rule_has_its_text():
    data = labels.load()
    assert data["schema"] == "maf.task-labels/1"
    assert list(data["task"]) == list(labels.TASK_KEYS) and list(data["stage"]) == list(labels.STAGE_KEYS)
    texts = {entry["text"] for scope in ("task", "stage") for entry in data[scope].values()}
    assert len(texts) == 11 and {"Falló", "Cancelada", "Cerrada", "Espera tu verificación"} <= texts
    assert all(set(entry) == {"text", "tone", "explanation"} and entry["explanation"]
               for scope in ("task", "stage") for entry in data[scope].values())
    data["task"]["done"]["text"] = "changed"
    assert labels.load()["task"]["done"]["text"] == "Terminada"   # a caller cannot change the shared map


TASK_STATES = ("open", "claimed", "running", "paused", "submitted", "validating", "needs_changes", "accepted",
               "failed", "cancelled")


@pytest.mark.parametrize("authorized", [True, False])
@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("state", TASK_STATES)
def test_task_label_matrix_ten_states_by_authorization_by_inherited_release(state, released, authorized):
    expected = {"accepted": "done", "cancelled": "cancelled", "failed": "failed", "submitted": "review",
                "needs_changes": "changes", "claimed": "doing", "running": "doing", "paused": "doing",
                "validating": "doing"}.get(state)
    if state == "submitted" and released:
        expected = "done"
    if state == "open":
        expected = "ready" if authorized else "todo"
    assert labels.task_label_key(state, state, authorized=authorized, released=released) == expected
    if state == "open":   # an open task that waits for a dependency is "blocked" once its stage is authorized
        assert labels.task_label_key("open", "blocked", authorized=authorized, released=released) == (
            "blocked" if authorized else "todo")


def test_label_details_and_views():
    assert labels.task_label("paused", "paused", authorized=True) == {
        "key": "doing", "text": "En curso", "tone": "active", "detail": "en pausa"}
    assert labels.task_label("validating", "validating", authorized=True)["detail"] == "comprobación automática"
    assert "detail" not in labels.task_label("running", "running", authorized=True)
    assert labels.label("stage", "verify") == {"key": "verify", "text": "Espera tu verificación", "tone": "attention"}


def test_stage_label_rules():
    key = labels.stage_label_key
    assert key("closed", True, ["done"]) == "closed" and key("closed", False, []) == "closed"
    assert key("open", False, ["done", "cancelled"]) == "verify"   # verification does not need the stage to be authorized
    assert key("open", True, ["cancelled", "cancelled"]) == "doing"   # nothing done: nothing to verify
    assert key("open", True, ["done", "blocked"]) == "doing"
    assert key("open", False, ["ready"]) == "todo" and key("open", True, []) == "todo"
    assert key("open", True, ["ready", "review"]) == "doing"


def test_progress_ignores_cancelled_tasks_and_never_invents_a_percent():
    assert labels.progress(["done", "cancelled", "ready", "ready"]) == {"done": 1, "cancelled": 1, "total": 4, "percent": 33}
    assert labels.progress(["done", "done", "ready"])["percent"] == 67
    assert labels.progress(["cancelled"])["percent"] is None and labels.progress([])["percent"] is None
    assert labels.counts(["ready", "done", "ready"]) == {"done": 1, "ready": 2}


def test_task_and_group_views_carry_the_label_with_the_inherited_release_rule(services):
    tasks = services.tasks
    tasks.create_group("OLD", "Imported", actor="user", authorized=True)
    tasks.create_task("OLD", "OLD-T01", "Delivered", actor="user")
    tasks.create_task("OLD", "OLD-T02", "Waiting", actor="user", depends_on=["OLD-T01"])
    assert tasks.get_task("OLD-T01")["label"]["key"] == "ready"
    assert tasks.get_task("OLD-T02")["label"]["key"] == "blocked"
    tasks.update_group("OLD", actor="user", authorized=False)
    assert tasks.get_task("OLD-T01")["label"] == {"key": "todo", "text": "Pendiente", "tone": "neutral"}
    tasks.update_group("OLD", actor="user", authorized=True)
    for action in ("claim", "start", "submit"):
        tasks.transition("OLD-T01", action, actor=AGENT)
    assert tasks.get_task("OLD-T01")["label"]["key"] == "review"
    tasks.set_release_mode("OLD", "terminada", actor="user")
    tasks.record_delivery_fact("OLD-T01", delivery_ref="report://delivered", delivery_sha256="a" * 64,
                               has_patch=False, patchset_sha256="", actor="user")
    assert tasks.get_task("OLD-T01")["label"]["key"] == "done"   # submitted and released by its evidence
    group = tasks.get_group("OLD")
    assert group["label"]["key"] == "doing" and group["counts"] == {"submitted": 1, "blocked": 1}
    assert [g["label"]["key"] for g in tasks.list_groups()] == ["doing"]


# ---------------------------------------------------------------------------------------------------------------
# Plans and creation
# ---------------------------------------------------------------------------------------------------------------

def test_plan_says_what_would_be_created_and_writes_nothing(services):
    seq = services.tasks.events()
    plan = services.projects.plan(PLAN, key="mkt")
    assert plan["status"] == "planned" and len(plan["plan_sha256"]) == 64
    assert plan["project"] == {"id": "mkt", "title": "Synthetic campaign", "visibility": "private",
                               "template": "synthetic-campaign"}
    assert [(s["group_id"], s["position"], s["task_count"]) for s in plan["stages"]] == [("mkt-INV", 1, 2), ("mkt-CON", 2, 2)]
    assert [t["id"] for t in plan["tasks"]] == ["mkt-INV-T01", "mkt-INV-T02", "mkt-CON-T01", "mkt-CON-T02"]
    assert plan["counts"] == {"stages": 2, "tasks": 4, "dependencies": 5} and plan["warnings"] == []
    assert services.tasks.events() == seq and services.projects.list() == [] and services.tasks.list_groups() == []
    # the same plan, key and options give the same hash; another key or title gives another one
    assert services.projects.plan(PLAN, key="mkt")["plan_sha256"] == plan["plan_sha256"]
    assert services.projects.plan(PLAN, key="other")["plan_sha256"] != plan["plan_sha256"]
    assert services.projects.plan(PLAN, key="mkt", title="Another title")["plan_sha256"] != plan["plan_sha256"]
    assert services.projects.plan(json.dumps(PLAN), key="mkt")["plan_sha256"] == plan["plan_sha256"]   # JSON text too


def test_create_writes_the_whole_project_in_one_step(services):
    view = make_project(services)
    assert (view["id"], view["name"], view["title"], view["visibility"], view["created_by"], view["revision"]) == (
        "mkt", "mkt", "Synthetic campaign", "private", OWNER, 1)   # decision 5: private by default
    assert [(s["group_id"], s["code"], s["position"], s["label"]["key"], s["authorized"]) for s in view["stages"]] == [
        ("mkt-INV", "INV", 1, "todo", False), ("mkt-CON", "CON", 2, "todo", False)]
    inv = stage_of(view, "mkt-INV")
    assert inv["phases"] == ["Sources", "Analysis"] and (inv["planned_start"], inv["planned_end"]) == ("2026-11-01", "2026-11-15")
    assert [(t["id"], t["phase"], t["due_at"], t["depends_on"]) for t in inv["tasks"]] == [
        ("mkt-INV-T01", "Sources", None, []), ("mkt-INV-T02", "Analysis", "2026-11-10", ["mkt-INV-T01"])]
    first = services.tasks.store.get_task("mkt-INV-T01")
    assert first["preferred"] == AGENT and first["assignee"] == "" and first["state"] == "open"   # assignee -> preferred
    assert first["constraints_json"] == '["Terminado cuando: A report with 3 segments exists"]'
    assert first["context_refs_json"] == '["doc://brief.md"]' and first["objective"] == "Find three segments."
    # stage:INV becomes a dependency on every task INV has; a task-level "requires" applies to its edges
    assert sorted(d["task_id"] for d in services.tasks.get_task("mkt-CON-T01")["depends_on"]) == [
        "mkt-INV-T01", "mkt-INV-T02"]
    posts = services.tasks.get_task("mkt-CON-T02")["depends_on"]
    assert sorted((d["task_id"], d["requires"]) for d in posts) == [
        ("mkt-CON-T01", "submitted"), ("mkt-INV-T02", "submitted")]
    assert services.tasks.get_task("mkt-INV-T02")["depends_on"][0]["requires"] == "accepted"
    assert view["counts"] == {"todo": 4} and view["progress"] == {"done": 0, "cancelled": 0, "total": 4, "percent": 0}
    assert services.projects.get("mkt") == view


def test_create_accepts_an_explicit_visibility_and_title(services):
    view = make_project(services, "open1", title="Open project", visibility="normal")
    assert (view["title"], view["visibility"]) == ("Open project", "normal")
    assert make_project(services, "dflt", plan_doc(visibility="normal"))["visibility"] == "normal"   # the document's
    assert make_project(services, "mine", plan_doc(visibility="normal"), visibility="private")["visibility"] == "private"


def test_the_empty_template_makes_a_project_without_stages(services):
    view = make_project(services, "blank", {"schema": "maf.project-template/1", "id": "empty", "title": "Empty project",
                                            "stages": []})
    assert view["stages"] == [] and view["progress"]["percent"] is None and view["visibility"] == "private"
    assert event_types(services, project_id="blank") == ["project.created"]


def test_two_projects_can_use_the_same_stage_and_task_codes(services):
    make_project(services, "one")
    make_project(services, "two")
    assert services.tasks.get_task("one-INV-T01")["id"] != services.tasks.get_task("two-INV-T01")["id"]
    assert {p["id"] for p in services.projects.list()} == {"one", "two"}
    assert [g["id"] for g in services.tasks.store.list_groups(project_id="two")] == ["two-INV", "two-CON"]


def test_create_is_atomic(services, monkeypatch):
    seq = services.tasks.events()
    real = services.tasks.store.append_event
    calls = {"n": 0}

    def failing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:
            raise ValueError("boom while writing events")
        return real(*args, **kwargs)

    monkeypatch.setattr(services.tasks.store, "append_event", failing)
    plan = services.projects.plan(PLAN, key="mkt")
    assert error_of(services.projects.create, PLAN, key="mkt", plan_sha256=plan["plan_sha256"], actor=OWNER).code == "invalid_operation"
    monkeypatch.undo()
    assert calls["n"] == 6
    with services.system.db.connect() as conn:
        for table in ("projects", "task_groups", "tasks", "task_deps"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert services.tasks.events() == seq
    assert make_project(services)["id"] == "mkt"   # and it can be created afterwards


def test_create_needs_the_confirmed_plan_and_a_user(services):
    plan = services.projects.plan(PLAN, key="mkt")
    digest = plan["plan_sha256"]
    changed = plan_doc()
    changed["stages"][0]["tasks"][0]["title"] = "Audience study, edited after confirming"
    stale = error_of(services.projects.create, changed, key="mkt", plan_sha256=digest, actor=OWNER)
    assert (stale.status, stale.code, stale.details["reason"]) == (409, "invalid_state", "plan_changed")
    assert error_of(services.projects.create, PLAN, key="mkt", plan_sha256="0" * 64, actor=OWNER).status == 409
    assert error_of(services.projects.create, PLAN, key="mkt", title="Other", plan_sha256=digest, actor=OWNER).status == 409
    for actor in (AGENT, "person:lucia", "executor:codex_cli"):
        refused = error_of(services.projects.create, PLAN, key="mkt", plan_sha256=digest, actor=actor)
        assert (refused.status, refused.code, refused.details["reason"]) == (409, "invalid_state", "confirmation_required")
    for actor in ("bob", "", "agent:", "user:" + "x" * 200):
        assert error_of(services.projects.create, PLAN, key="mkt", plan_sha256=digest, actor=actor).code == "invalid_request"
    assert error_of(services.projects.create, PLAN, key="mkt", plan_sha256="abc", actor=OWNER).code == "invalid_request"
    assert services.projects.list() == [] and services.tasks.events() == []
    for legacy in ("user", "human", "user:"):   # the older spellings of the user still count as the user
        make_project(services, "k" + legacy.replace(":", "x"), actor=legacy)


def test_existing_ids_are_refused_by_plan_and_create(services):
    make_project(services)
    assert error_of(services.projects.plan, PLAN, key="mkt").status == 409
    # a run creates its project by name; the same key cannot be taken afterwards
    services.system.store.get_or_create_project("taken")
    assert error_of(services.projects.plan, PLAN, key="taken").details == {"project_id": "taken"}
    services.tasks.create_group("zed-INV", "Somebody's group", actor="user")
    refused = error_of(services.projects.plan, PLAN, key="zed")
    assert refused.status == 409 and refused.details["id"] == "zed-INV"
    services.tasks.create_group("stray", "Group", actor="user")
    services.tasks.create_task("stray", "yak-INV-T02", "Stray task", actor="user")
    assert error_of(services.projects.plan, PLAN, key="yak").details["id"] == "yak-INV-T02"


def bad(path: list, value: Any, *, drop: bool = False) -> dict[str, Any]:
    doc = plan_doc()
    node: Any = doc
    for step in path[:-1]:
        node = node[step]
    if drop:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return doc


STAGE = ["stages", 0]
TASK = ["stages", 0, "tasks", 0]
INVALID_PLANS = [
    ("unknown top-level key", bad(["code"], "x"), "doc"),
    ("wrong schema", bad(["schema"], "maf.project-template/2"), "schema"),
    ("missing stages", bad(["stages"], None, drop=True), "doc"),
    ("template id", bad(["id"], "Not Valid"), "id"),
    ("title too long", bad(["title"], "x" * 201), "title"),
    ("description too long", bad(["description"], "x" * 2001), "description"),
    ("bad visibility", bad(["visibility"], "public"), "visibility"),
    ("stage code", bad(STAGE + ["code"], "inv"), "stages[0].code"),
    ("repeated stage code", bad(["stages", 1, "code"], "INV"), "stages[1].code"),
    ("unknown stage key", bad(STAGE + ["command"], "rm -rf"), "stages[0]"),
    ("repeated phase", bad(STAGE + ["phases"], ["A", "A"]), "stages[0].phases"),
    ("too many phases", bad(STAGE + ["phases"], [f"P{i}" for i in range(13)]), "stages[0].phases"),
    ("planned end before start", bad(STAGE + ["planned"], {"start": "2026-12-01", "end": "2026-11-01"}), "stages[0].planned"),
    ("not a date", bad(STAGE + ["planned"], {"start": "2026-02-30"}), "stages[0].planned.start"),
    ("task id", bad(TASK + ["id"], "T1"), "stages[0].tasks[0].id"),
    ("repeated task id", bad(["stages", 0, "tasks", 1, "id"], "T01"), "stages[0].tasks[1].id"),
    ("unknown task key", bad(TASK + ["gate"], ["tests"]), "stages[0].tasks[0]"),
    ("code-like key", bad(TASK + ["command"], "make test"), "stages[0].tasks[0]"),
    ("phase not declared", bad(TASK + ["phase"], "Nowhere"), "stages[0].tasks[0].phase"),
    ("done_when too long for the 200 character restriction", bad(TASK + ["done_when"], "x" * 183), "stages[0].tasks[0].done_when"),
    ("bad assignee", bad(TASK + ["assignee"], "somebody"), "stages[0].tasks[0].assignee"),
    ("bad requires", bad(TASK + ["requires"], "tested"), "stages[0].tasks[0].requires"),
    ("bad due date", bad(TASK + ["due"], "next week"), "stages[0].tasks[0].due"),
    ("bad context reference", bad(TASK + ["context_refs"], ["file:///etc/passwd"]), "stages[0].tasks[0].context_refs"),
    ("dependency without target", bad(TASK + ["depends_on"], ["T09"]), "depends_on"),
    ("dependency on a missing stage", bad(TASK + ["depends_on"], ["stage:NOPE"]), "stages[0].tasks[0].depends_on"),
    ("dependency on the own stage", bad(TASK + ["depends_on"], ["stage:INV"]), "stages[0].tasks[0].depends_on"),
    ("malformed dependency", bad(TASK + ["depends_on"], ["../T01"]), "stages[0].tasks[0].depends_on"),
    ("repeated dependency", bad(TASK + ["depends_on"], ["T02", "T02"]), "stages[0].tasks[0].depends_on"),
    ("cycle", bad(TASK + ["depends_on"], ["T02"]), "depends_on"),
    ("cycle across stages", bad(TASK + ["depends_on"], ["CON-T01"]), "depends_on"),
    ("too many stages", bad(["stages"], [{"code": f"S{i:02d}", "title": "S", "tasks": []} for i in range(31)]), "stages"),
]


@pytest.mark.parametrize(("label", "doc", "field"), INVALID_PLANS, ids=[item[0] for item in INVALID_PLANS])
def test_invalid_plans_are_rejected_with_the_field_that_fails(services, label, doc, field):
    refused = error_of(services.projects.plan, doc, key="mkt")
    assert (refused.status, refused.code) == (422, "invalid_request"), label
    assert refused.details["field"] == field, (label, refused.message)
    digest = "0" * 64
    assert error_of(services.projects.create, doc, key="mkt", plan_sha256=digest, actor=OWNER).code == "invalid_request"
    assert services.projects.list() == []


def test_document_limits_and_shapes(services):
    too_many = plan_doc(stages=[{"code": f"S{i:02d}", "title": "S", "tasks": [
        {"id": f"T{j:02d}", "title": "t"} for j in range(1, 11)]} for i in range(21)])
    assert error_of(services.projects.plan, too_many, key="mkt").message.startswith("The plan has more than 200 tasks")
    big = plan_doc(description="d" * 2000)
    big["stages"][0]["tasks"][0]["objective"] = "o" * 4000   # the longest allowed text still fits
    padded = plan_doc(stages=[{"code": f"S{i:02d}", "title": "S", "tasks": [
        {"id": f"T{j:02d}", "title": "t", "objective": "o" * 4000} for j in range(1, 7)]} for i in range(30)])
    assert error_of(services.projects.plan, padded, key="mkt").message.startswith("The document is larger than")
    assert error_of(services.projects.plan, "x" * (257 * 1024), key="mkt").details["field"] == "doc"
    assert error_of(services.projects.plan, "{not json", key="mkt").details["field"] == "doc"
    assert error_of(services.projects.plan, ["a list"], key="mkt").details["field"] == "doc"
    assert error_of(services.projects.plan, {"a": float("nan")}, key="mkt").details["field"] == "doc"
    nested: dict = {}
    for _ in range(3000):
        nested = {"a": nested}
    assert error_of(services.projects.plan, nested, key="mkt").details["field"] == "doc"
    assert error_of(services.projects.plan, "[" * 3000, key="mkt").details["field"] == "doc"
    assert error_of(services.projects.plan, {1: "x", "a": "y"}, key="mkt").details["field"] == "doc"
    for key in ("", "A1", "1ab", "a", "x" * 17, "mkt-1", None):
        assert error_of(services.projects.plan, PLAN, key=key).details["field"] == "key", key
    assert error_of(services.projects.plan, PLAN, key="mkt", visibility="public").details["field"] == "visibility"
    assert services.projects.plan(big, key="mkt")["counts"]["tasks"] == 4


SHIPPED_TEMPLATES = ["campana-marketing", "desarrollo-software", "vacia"]


def expected_edges(doc: dict) -> int:
    """Dependencies a template should produce: the entries of depends_on, with stage:X as one edge per task of X."""
    tasks_of = {stage["code"]: [t["id"] for t in stage.get("tasks", [])] for stage in doc["stages"]}
    edges = set()
    for stage in doc["stages"]:
        for task in stage.get("tasks", []):
            for token in task.get("depends_on", []):
                if token.startswith("stage:"):
                    targets = [(token[6:], tid) for tid in tasks_of[token[6:]]]
                elif "-" in token:
                    targets = [tuple(token.split("-", 1))]
                else:
                    targets = [(stage["code"], token)]
                edges.update(((stage["code"], task["id"]), target) for target in targets)
    return len(edges)


@pytest.mark.parametrize("name", SHIPPED_TEMPLATES)
def test_the_shipped_templates_go_through_plan_and_create_end_to_end(services, name):
    doc = load_template(name)
    assert validate_template(doc) == []
    plan = services.projects.plan(load_template(name), key="mkt")   # the dict the loader gives, as it comes
    tasks = sum(len(stage.get("tasks", [])) for stage in doc["stages"])
    assert plan["counts"] == {"stages": len(doc["stages"]), "tasks": tasks, "dependencies": expected_edges(doc)}
    assert plan["project"] == {"id": "mkt", "title": doc["title"], "visibility": "private", "template": name}
    view = services.projects.create(load_template(name), key="mkt", plan_sha256=plan["plan_sha256"], actor=OWNER)
    assert [s["code"] for s in view["stages"]] == [s["code"] for s in doc["stages"]]
    assert view["progress"]["total"] == tasks and view["visibility"] == "private"
    assert [(s["phases"], len(s["tasks"])) for s in view["stages"]] == [
        (s.get("phases", []), len(s.get("tasks", []))) for s in doc["stages"]]


def test_the_marketing_template_keeps_its_assignees_and_restrictions(services):
    doc = load_template("campana-marketing")
    assert services.projects.plan(doc, key="mkt")["counts"]["stages"] == 6
    view = make_project(services, "mkt", doc)
    first = services.tasks.store.get_task("mkt-INV-T01")
    assert first["preferred"] == "agent:claude" and first["assignee"] == ""
    assert first["constraints_json"] == '["Terminado cuando: Report with 3 segments"]'
    assert services.tasks.get_task("mkt-EST-T01")["blockers"] and view["counts"] == {"todo": view["progress"]["total"]}
    assert make_project(services, "mkt2", doc)["id"] == "mkt2"   # a second project of the same template does not collide


def test_the_synthetic_sample_project_is_a_valid_plan_with_dates(services):
    doc = parse_template(SYNTHETIC_PROJECT.read_bytes())
    view = make_project(services, "sample", doc)
    alfa = stage_of(view, "sample-ALFA")
    assert (alfa["planned_start"], alfa["planned_end"]) == ("2026-11-02", "2026-11-13")
    assert [t["due_at"] for t in alfa["tasks"]] == ["2026-11-06", "2026-11-13"]
    assert [t["due_at"] for t in stage_of(view, "sample-BETA")["tasks"]] == ["2026-11-20", None]
    assert stage_of(view, "sample-BETA")["tasks"][0]["assignee_info"]["kind"] == "agent"


def test_the_template_files_and_the_service_agree_on_what_is_invalid(services):
    for label, doc, field in INVALID_PLANS:
        assert validate_template(doc) != [], f"the template validator accepts {label}"
        assert error_of(services.projects.plan, doc, key="mkt").code == "invalid_request"
    for name in SHIPPED_TEMPLATES:   # and the other way: what the loader ships, the service accepts
        assert services.projects.plan(load_template(name), key="chk" + name[:3])["status"] == "planned"
    assert [t["id"] for t in list_templates()] == sorted(SHIPPED_TEMPLATES)


def test_template_errors_leave_the_service_layer_as_service_errors():
    @service_errors
    def load(name):
        return load_template(name)

    @service_errors
    def parse(text):
        return parse_template(text)

    missing = error_of(load, "no-such-template")
    assert (missing.status, missing.code, missing.details) == (404, "not_found", {"template": "no-such-template"})
    invalid = error_of(parse, "schema: maf.project-template/1\nid: x\n")
    assert (invalid.status, invalid.code) == (422, "invalid_request") and invalid.details["errors"]
    assert isinstance(TemplateError(["a"]), ValueError) and not isinstance(TemplateNotFound("a"), ValueError)
    assert translate(TemplateError(["a", "b"])).details == {"errors": ["a", "b"]}   # not the generic 409 of ValueError
    assert translate(TemplateNotFound("zz")).status == 404


def test_a_stage_dependency_covers_the_tasks_present_at_creation_only(services):
    make_project(services)
    authorize(services, "mkt-INV", "mkt-CON")
    services.tasks.create_task("mkt-INV", "mkt-INV-T03", "Added later", actor=OWNER)
    deps = {d["task_id"] for d in services.tasks.get_task("mkt-CON-T01")["depends_on"]}
    assert deps == {"mkt-INV-T01", "mkt-INV-T02"}   # the late task does not hold the calendar back
    plan = services.projects.plan(plan_doc(stages=[{"code": "AAA", "title": "Empty", "tasks": []},
                                                   {"code": "BBB", "title": "B", "tasks": [
                                                       {"id": "T01", "title": "t", "depends_on": ["stage:AAA"]}]}]), key="zz")
    assert plan["dependencies"] == [] and plan["warnings"] and "stage:AAA" in plan["warnings"][0]


# ---------------------------------------------------------------------------------------------------------------
# Views: get, list, review
# ---------------------------------------------------------------------------------------------------------------

def test_get_aggregates_progress_attention_and_who_is_working(services):
    make_project(services)
    authorize(services, "mkt-INV", "mkt-CON")
    view = services.projects.get("mkt")
    assert view["counts"] == {"ready": 1, "blocked": 3} and view["working_now"] == []
    assert [t["id"] for t in view["attention"]["blocked"]] == ["mkt-INV-T02", "mkt-CON-T01", "mkt-CON-T02"]
    waiting = view["attention"]["blocked"][0]
    assert waiting["blockers"] == [{"task_id": "mkt-INV-T01", "state": "open", "requires": "accepted"}]
    services.tasks.transition("mkt-INV-T01", "claim", actor=AGENT)
    services.tasks.transition("mkt-INV-T01", "start", actor=AGENT)
    view = services.projects.get("mkt")
    assert [(t["id"], t["assignee"], t["label"]["key"]) for t in view["working_now"]] == [("mkt-INV-T01", AGENT, "doing")]
    assert view["working_now"][0]["assignee_info"] == {"raw": AGENT, "kind": "agent", "app": "writer", "model": "m1", "level": None}
    services.tasks.transition("mkt-INV-T01", "submit", actor=AGENT)
    view = services.projects.get("mkt")
    assert [t["id"] for t in view["attention"]["reviews"]] == ["mkt-INV-T01"]
    assert view["counts"]["review"] == 1 and view["progress"]["done"] == 0


def test_review_with_a_user_actor_moves_the_progress_and_changes_come_back(services):
    make_project(services)
    authorize(services, "mkt-INV", "mkt-CON")
    deliver(services, "mkt-INV-T01", accept=False)
    services.tasks.transition("mkt-INV-T01", "request_changes", actor=OWNER, cause="needs a third segment")
    view = services.projects.get("mkt")
    assert view["counts"]["changes"] == 1 and view["attention"]["reviews"] == []
    services.tasks.transition("mkt-INV-T01", "start", actor=AGENT)
    services.envelopes.submit_result("mkt-INV-T01", {"summary": "Third segment added."}, actor=AGENT)
    services.tasks.transition("mkt-INV-T01", "accept", actor=OWNER)
    view = services.projects.get("mkt")
    assert view["progress"] == {"done": 1, "cancelled": 0, "total": 4, "percent": 25}
    assert [t["id"] for t in view["working_now"]] == [] and view["counts"] == {"done": 1, "ready": 1, "blocked": 2}
    assert [t["label"]["key"] for t in stage_of(view, "mkt-INV")["tasks"]] == ["done", "ready"]


def test_your_own_tasks_are_the_actionable_ones_assigned_to_a_user(services):
    make_project(services)
    authorize(services, "mkt-INV", "mkt-CON")
    assert services.projects.get("mkt")["attention"]["mine"] == []
    deliver(services, "mkt-INV-T01")
    deliver(services, "mkt-INV-T02")
    mine = services.projects.get("mkt")["attention"]["mine"]
    assert [(t["id"], t["label"]["key"], t["assignee_info"]["kind"]) for t in mine] == [("mkt-CON-T01", "ready", "user")]


def test_the_level_of_an_agent_comes_from_the_catalog_and_is_never_stored(services, monkeypatch):
    make_project(services)
    monkeypatch.setattr(services.sessions, "_level_of", lambda: (lambda model: "T2" if model == "m1" else None))
    first = stage_of(services.projects.get("mkt"), "mkt-INV")["tasks"][0]
    assert first["assignee_info"]["level"] == "T2" and first["preferred"] == AGENT
    assert services.tasks.store.get_task("mkt-INV-T01")["preferred"] == AGENT   # the level is not in the actor
    assert projects_module.actor_view("agent:writer")["model"] is None
    assert projects_module.actor_view("free text") == {"raw": "free text", "kind": "other", "name": "free text"}
    assert projects_module.actor_view("") is None
    assert projects_module.actor_view("user:ana")["name"] == "ana" and projects_module.actor_view("human")["kind"] == "user"


def test_list_shows_active_or_archived_projects_with_what_needs_you(services):
    make_project(services, "one")
    make_project(services, "two")
    services.system.store.get_or_create_project("from-a-run")   # a run creates its project by name, with a generated id
    rows = {p["name"]: p for p in services.projects.list()}
    assert set(rows) == {"one", "two", "from-a-run"}
    assert rows["from-a-run"]["stages"] == 0 and rows["from-a-run"]["title"] == "from-a-run"
    assert rows["from-a-run"]["id"] != "from-a-run" and rows["one"]["id"] == "one"
    assert rows["one"]["stages"] == 2 and rows["one"]["needs_you"] == {"reviews": 0, "verifications": 0, "mine": 0, "blocked": 0}
    authorize(services, "one-INV")
    deliver(services, "one-INV-T01", accept=False)
    assert {p["name"]: p["needs_you"]["reviews"] for p in services.projects.list()}["one"] == 1
    view = services.projects.get("two")
    services.projects.archive("two", actor=OWNER, expected_revision=view["revision"])
    assert {p["name"] for p in services.projects.list()} == {"one", "from-a-run"}
    assert [p["id"] for p in services.projects.list(archived=True)] == ["two"]
    assert error_of(services.projects.get, "ghost").status == 404


# ---------------------------------------------------------------------------------------------------------------
# Project writes: update, archive, attach, reorder
# ---------------------------------------------------------------------------------------------------------------

def test_update_changes_title_and_visibility_with_the_revision(services):
    view = make_project(services)
    updated = services.projects.update("mkt", actor=OWNER, expected_revision=1, title="Renamed", visibility="normal")
    assert (updated["title"], updated["visibility"], updated["revision"]) == ("Renamed", "normal", 2)
    assert error_of(services.projects.update, "mkt", actor=OWNER, expected_revision=1, title="Late").status == 409
    same = services.projects.update("mkt", actor=OWNER, expected_revision=2, title="Renamed")
    assert same["revision"] == 2   # nothing changed, so nothing is written
    assert error_of(services.projects.update, "mkt", actor=AGENT, expected_revision=2, title="X").details["reason"] == "confirmation_required"
    assert error_of(services.projects.update, "mkt", actor=OWNER, expected_revision=2).code == "invalid_request"
    assert error_of(services.projects.update, "mkt", actor=OWNER, expected_revision=2, visibility="secret").code == "invalid_request"
    assert error_of(services.projects.update, "mkt", actor=OWNER, expected_revision=0, title="X").code == "invalid_request"
    assert error_of(services.projects.update, "ghost", actor=OWNER, expected_revision=1, title="X").status == 404
    assert event_types(services, project_id="mkt").count("project.updated") == 1
    assert view["revision"] == 1


def test_archiving_keeps_everything_and_blocks_new_work(services):
    make_project(services)
    authorize(services, "mkt-INV")
    services.tasks.transition("mkt-INV-T01", "claim", actor=AGENT)
    busy = error_of(services.projects.archive, "mkt", actor=OWNER, expected_revision=1)
    assert (busy.status, busy.details["reason"], busy.details["task_ids"]) == (409, "work_in_progress", ["mkt-INV-T01"])
    services.tasks.transition("mkt-INV-T01", "unclaim", actor=AGENT)
    assert error_of(services.projects.archive, "mkt", actor=OWNER, expected_revision=7).status == 409
    assert error_of(services.projects.archive, "mkt", actor=AGENT, expected_revision=1).details["reason"] == "confirmation_required"
    archived = services.projects.archive("mkt", actor=OWNER, expected_revision=1)
    assert archived["archived"] is True and archived["archived_at"] and archived["revision"] == 2
    assert len(archived["stages"]) == 2 and archived["progress"]["total"] == 4   # nothing was deleted
    again = services.projects.archive("mkt", actor=OWNER, expected_revision=2)
    assert again["revision"] == 2 and event_types(services, project_id="mkt").count("project.archived") == 1
    # no claim, no new task, no dependency change in an archived project
    assert "archived" in error_of(services.tasks.transition, "mkt-INV-T01", "claim", actor=AGENT).message
    assert error_of(services.tasks.create_task, "mkt-INV", "mkt-INV-T09", "Late", actor=OWNER).status == 409
    assert error_of(services.tasks.add_dependency, "mkt-INV-T01", "mkt-CON-T02", actor=OWNER).status == 409
    assert error_of(services.tasks.remove_dependency, "mkt-INV-T02", "mkt-INV-T01", actor=OWNER).status == 409
    assert error_of(services.projects.reorder, "mkt", ["mkt-CON", "mkt-INV"], actor=OWNER, expected_revision=2).status == 409
    services.tasks.create_group("OLD", "Imported", actor="user")
    assert error_of(services.projects.attach_stage, "mkt", "OLD", actor=OWNER, expected_revision=2).status == 409
    restored = services.projects.unarchive("mkt", actor=OWNER, expected_revision=2)
    assert restored["archived"] is False and restored["archived_at"] is None and restored["revision"] == 3
    services.tasks.transition("mkt-INV-T01", "claim", actor=AGENT)   # works again
    assert event_types(services, project_id="mkt").count("project.unarchived") == 1


def test_a_legacy_group_is_attached_without_being_renamed(services):
    make_project(services)
    services.tasks.create_group("ORQ", "Imported from ORQ", actor="user", authorized=True)
    services.tasks.create_task("ORQ", "ORQ-T01", "Old task", actor="user")
    revision = services.tasks.get_group("ORQ")["revision"]
    view = services.projects.attach_stage("mkt", "ORQ", actor=OWNER, expected_revision=1)
    stage = stage_of(view, "ORQ")
    assert (stage["code"], stage["position"], stage["title"], view["revision"]) == ("ORQ", 3, "Imported from ORQ", 2)
    assert services.tasks.get_group("ORQ")["revision"] == revision   # the group itself did not change
    assert services.tasks.get_group("ORQ")["project_id"] == "mkt" and view["progress"]["total"] == 5
    assert services.projects.attach_stage("mkt", "ORQ", actor=OWNER, expected_revision=2)["revision"] == 2   # idempotent
    make_project(services, "other")
    taken = error_of(services.projects.attach_stage, "other", "ORQ", actor=OWNER, expected_revision=1)
    assert taken.status == 409 and taken.details["project_id"] == "mkt"
    assert error_of(services.projects.attach_stage, "mkt", "GHOST", actor=OWNER, expected_revision=2).status == 404
    assert error_of(services.projects.attach_stage, "mkt", "ORQ", actor=OWNER, expected_revision=1).status == 409
    assert error_of(services.projects.attach_stage, "mkt", "bad id", actor=OWNER, expected_revision=2).code == "invalid_request"
    assert event_types(services, project_id="mkt").count("project.stage_attached") == 1


def test_reorder_gives_positions_one_to_n_in_a_single_step(services):
    make_project(services)
    services.tasks.create_group("ORQ", "Imported", actor="user")
    services.projects.attach_stage("mkt", "ORQ", actor=OWNER, expected_revision=1)
    view = services.projects.reorder("mkt", ["ORQ", "mkt-CON", "mkt-INV"], actor=OWNER, expected_revision=2)
    assert [(s["group_id"], s["position"]) for s in view["stages"]] == [("ORQ", 1), ("mkt-CON", 2), ("mkt-INV", 3)]
    assert view["revision"] == 3
    with services.system.db.connect() as conn:
        positions = [r[0] for r in conn.execute("SELECT position FROM task_groups WHERE project_id='mkt' ORDER BY position")]
    assert positions == [1, 2, 3]
    assert services.projects.reorder("mkt", ["ORQ", "mkt-CON", "mkt-INV"], actor=OWNER, expected_revision=3)["revision"] == 3
    for wrong in (["ORQ", "mkt-CON"], ["ORQ", "mkt-CON", "mkt-CON"], ["ORQ", "mkt-CON", "mkt-INV", "x"], ["ORQ", "mkt-CON", "other"], []):
        assert error_of(services.projects.reorder, "mkt", wrong, actor=OWNER, expected_revision=3).code == "invalid_request", wrong
    assert error_of(services.projects.reorder, "mkt", "ORQ", actor=OWNER, expected_revision=3).code == "invalid_request"
    assert error_of(services.projects.reorder, "mkt", ["mkt-INV", "mkt-CON", "ORQ"], actor=OWNER, expected_revision=1).status == 409
    assert error_of(services.projects.reorder, "mkt", ["mkt-INV", "mkt-CON", "ORQ"], actor=AGENT, expected_revision=3).status == 409
    assert [s["group_id"] for s in services.projects.get("mkt")["stages"]] == ["ORQ", "mkt-CON", "mkt-INV"]


# ---------------------------------------------------------------------------------------------------------------
# Closing a stage
# ---------------------------------------------------------------------------------------------------------------

def finish_research(svc: ApplicationServices) -> None:
    authorize(svc, "mkt-INV", "mkt-CON")
    deliver(svc, "mkt-INV-T01")
    deliver(svc, "mkt-INV-T02")


def test_closing_a_stage_without_a_patch_derives_the_evidence_from_the_last_result(services):
    make_project(services)
    finish_research(services)
    view = services.projects.get("mkt")
    inv = stage_of(view, "mkt-INV")
    assert inv["label"]["key"] == "verify" and inv["closed_at"] is None
    assert [s["group_id"] for s in view["attention"]["verifications"]] == ["mkt-INV"]
    assert "tasks" not in view["attention"]["verifications"][0]
    seq = services.tasks.events()[-1]["seq"]
    closed = services.projects.close_stage("mkt", "mkt-INV", actor=OWNER, expected_revision=inv["revision"])
    assert closed["closed_stage"] == {"group_id": "mkt-INV", "evidence_recorded": ["mkt-INV-T01", "mkt-INV-T02"]}
    inv = stage_of(closed, "mkt-INV")
    assert (inv["label"]["key"], inv["lifecycle_state"]) == ("closed", "closed") and inv["closed_at"]
    assert closed["milestones"][0] == {"group_id": "mkt-INV", "code": "INV", "title": "Research", "position": 1,
                                       "planned": "2026-11-15", "actual": inv["closed_at"]}
    assert closed["milestones"][1]["actual"] is None
    fact = services.tasks.get_release_facts("mkt-INV-T01")
    assert (fact["delivery_ref"], fact["has_patch"]) == ("result://mkt-INV-T01/1", False)
    stored = services.envelopes.get_result("mkt-INV-T01", attempt=1)
    assert fact["delivery_sha256"] == stored["result_sha256"]
    events = services.tasks.events(after=seq)
    assert [e["type"] for e in events if e["type"].startswith(("release.", "group."))] == [
        "release.delivery_recorded", "release.delivery_recorded", "group.lifecycle_changed"]
    assert all(e["cause"].startswith("project:mkt") for e in events if e["type"].startswith(("release.", "group.")))
    # the stage that follows is released by the accepted tasks
    assert services.tasks.get_task("mkt-CON-T01")["label"]["key"] == "ready"
    again = services.projects.close_stage("mkt", "mkt-INV", actor=OWNER, expected_revision=inv["revision"])
    assert again["closed_stage"]["evidence_recorded"] == [] and services.tasks.events(after=events[-1]["seq"]) == []


def test_close_stage_refuses_what_cannot_be_verified_and_changes_nothing(services):
    make_project(services)
    authorize(services, "mkt-INV", "mkt-CON")
    revision = services.tasks.get_group("mkt-INV")["revision"]
    seq = services.tasks.events()[-1]["seq"]
    not_yet = error_of(services.projects.close_stage, "mkt", "mkt-INV", actor=OWNER, expected_revision=revision)
    assert (not_yet.status, not_yet.details["reason"]) == (409, "not_ready_to_verify")
    assert {t["task_id"]: t["label"] for t in not_yet.details["tasks"]} == {"mkt-INV-T01": "ready", "mkt-INV-T02": "blocked"}
    deliver(services, "mkt-INV-T01")
    # an accepted task whose result is missing (it was submitted without storing one) cannot give its evidence
    for action in ("claim", "start", "submit", "accept"):
        services.tasks.transition("mkt-INV-T02", action, actor=AGENT if action != "accept" else OWNER)
    seq = services.tasks.events()[-1]["seq"]
    revision = services.tasks.get_group("mkt-INV")["revision"]
    missing = error_of(services.projects.close_stage, "mkt", "mkt-INV", actor=OWNER, expected_revision=revision)
    assert (missing.status, missing.details["reason"], missing.details["task_ids"]) == (409, "missing_results", ["mkt-INV-T02"])
    assert services.tasks.events(after=seq) == [] and services.tasks.get_release_facts("mkt-INV-T01")["delivery_ref"] == ""
    assert services.tasks.get_group("mkt-INV")["lifecycle_state"] == "open"
    assert error_of(services.projects.close_stage, "mkt", "mkt-INV", actor=OWNER, expected_revision=revision + 1).status == 409
    assert error_of(services.projects.close_stage, "mkt", "mkt-INV", actor=AGENT, expected_revision=revision).details["reason"] == "confirmation_required"
    assert error_of(services.projects.close_stage, "ghost", "mkt-INV", actor=OWNER, expected_revision=revision).status == 404
    services.tasks.create_group("OTHER", "Not in the project", actor="user")
    assert error_of(services.projects.close_stage, "mkt", "OTHER", actor=OWNER, expected_revision=1).status == 404
    assert error_of(services.projects.close_stage, "mkt", "GHOST", actor=OWNER, expected_revision=1).status == 404


def test_closing_ignores_cancelled_tasks_but_not_failed_ones(services):
    make_project(services)
    authorize(services, "mkt-INV")
    deliver(services, "mkt-INV-T01")
    services.tasks.transition("mkt-INV-T02", "claim", actor=AGENT)
    services.tasks.transition("mkt-INV-T02", "start", actor=AGENT)
    services.tasks.transition("mkt-INV-T02", "fail", actor=AGENT, cause="source unavailable")
    revision = services.tasks.get_group("mkt-INV")["revision"]
    assert error_of(services.projects.close_stage, "mkt", "mkt-INV", actor=OWNER, expected_revision=revision).details["reason"] == "not_ready_to_verify"
    services.tasks.transition("mkt-INV-T02", "reopen", actor=OWNER, cause="drop it", expected_revision=services.tasks.get_task("mkt-INV-T02")["revision"])
    services.tasks.transition("mkt-INV-T02", "cancel", actor=OWNER)
    view = services.projects.get("mkt")
    assert stage_of(view, "mkt-INV")["label"]["key"] == "verify"
    assert view["progress"]["cancelled"] == 1
    closed = services.projects.close_stage("mkt", "mkt-INV", actor=OWNER, expected_revision=revision)
    assert closed["closed_stage"]["evidence_recorded"] == ["mkt-INV-T01"]
    assert stage_of(closed, "mkt-INV")["label"]["key"] == "closed"


def test_an_imported_stage_released_by_its_evidence_closes_and_only_derives_what_is_missing(services):
    make_project(services)
    services.tasks.create_group("ORQ", "Imported", actor="user", authorized=True)
    services.tasks.create_task("ORQ", "ORQ-T01", "Delivered with evidence", actor="user")
    services.tasks.create_task("ORQ", "ORQ-T02", "Accepted with a result", actor="user")
    deliver(services, "ORQ-T01", accept=False)   # stays submitted: the legacy evidence releases it
    services.tasks.set_release_mode("ORQ", "terminada", actor="user")
    services.tasks.record_delivery_fact("ORQ-T01", delivery_ref="report://t01", delivery_sha256="a" * 64,
                                        has_patch=False, patchset_sha256="", actor="user")
    deliver(services, "ORQ-T02")
    view = services.projects.attach_stage("mkt", "ORQ", actor=OWNER, expected_revision=1)
    stage = stage_of(view, "ORQ")
    assert [t["label"]["key"] for t in stage["tasks"]] == ["done", "done"] and stage["label"]["key"] == "verify"
    closed = services.projects.close_stage("mkt", "ORQ", actor=OWNER, expected_revision=stage["revision"])
    assert closed["closed_stage"]["evidence_recorded"] == ["ORQ-T02"]
    assert services.tasks.get_release_facts("ORQ-T01")["delivery_ref"] == "report://t01"   # the real evidence is kept


def test_a_patch_needs_its_integration_before_the_stage_can_close(services):
    make_project(services)
    authorize(services, "mkt-INV")
    blob = services.envelopes.put_blob(b"diff --git a/a b/a\n+x\n", actor=AGENT, media_type="text/x-patch")
    deliver(services, "mkt-INV-T01", result={"summary": "With a patch.", "patch": {"blob": f"sha256:{blob['sha256']}", "files": ["a"]}})
    deliver(services, "mkt-INV-T02")
    revision = services.tasks.get_group("mkt-INV")["revision"]
    refused = error_of(services.projects.close_stage, "mkt", "mkt-INV", actor=OWNER, expected_revision=revision)
    assert (refused.details["reason"], refused.details["task_ids"]) == ("integration_required", ["mkt-INV-T01"])
    assert services.tasks.get_release_facts("mkt-INV-T02")["delivery_ref"] == ""   # nothing was recorded


def test_set_group_state_is_unchanged_for_other_callers(services):
    make_project(services)
    authorize(services, "mkt-INV")
    deliver(services, "mkt-INV-T01")
    services.tasks.transition("mkt-INV-T02", "cancel", actor=OWNER)
    revision = services.tasks.get_group("mkt-INV")["revision"]
    refused = error_of(services.tasks.set_group_state, "mkt-INV", "closed", actor=OWNER, cause="x", expected_revision=revision)
    assert refused.status == 409 and "submitted or accepted" in refused.message   # ORQ-4 policy stays as it was


# ---------------------------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------------------------

def test_events_are_written_with_the_change_and_the_project_filter_finds_them(services):
    make_project(services, "other")
    services.tasks.create_group("ORQ", "Imported", actor="user")
    seq = services.tasks.events()[-1]["seq"]
    make_project(services)
    services.projects.update("mkt", actor=OWNER, expected_revision=1, title="Renamed")
    services.projects.attach_stage("mkt", "ORQ", actor=OWNER, expected_revision=2)
    services.projects.reorder("mkt", ["ORQ", "mkt-INV", "mkt-CON"], actor=OWNER, expected_revision=3)
    services.projects.archive("mkt", actor=OWNER, expected_revision=4)
    services.projects.unarchive("mkt", actor=OWNER, expected_revision=5)
    mine = services.tasks.events(after=seq, project_id="mkt")
    assert [e["type"] for e in mine] == (
        ["project.created", "group.created", "task.created", "task.created", "dependency.added", "group.created",
         "task.created", "dependency.added", "dependency.added", "task.created", "dependency.added", "dependency.added",
         "project.updated", "project.stage_attached", "project.stages_reordered", "project.archived", "project.unarchived"])
    created = mine[0]
    assert (created["ref"], created["actor"], created["cause"]) == ("project:mkt", OWNER, "project:mkt")
    assert created["payload"]["stages"] == 2 and created["payload"]["tasks"] == 4 and len(created["payload"]["plan_sha256"]) == 64
    assert all(e["cause"] == "project:mkt" for e in mine[1:12])   # group.created, task.created, dependency.added
    assert {e["ref"] for e in mine if e["type"].startswith("project.")} == {"project:mkt"}
    assert all(len(json.dumps(e["payload"]).encode()) <= 2048 for e in mine)
    assert services.tasks.events(after=seq, project_id="other") == []   # another project's events stay out
    assert all(e["group_id"].startswith("other-") or e["ref"] == "project:other"
               for e in services.tasks.events(project_id="other"))


def test_every_event_type_of_the_contract_is_registered():
    from multiagent.services.tasks import EVENT_TYPES
    assert {"project.created", "project.updated", "project.archived", "project.unarchived", "project.stage_attached",
            "project.stages_reordered", "project.remote_model_acknowledged", "group.plan_updated", "task.plan_updated",
            "task.session_linked", "task.session_unlinked"} <= set(EVENT_TYPES)


# ---------------------------------------------------------------------------------------------------------------
# Links between tasks, runs and chats
# ---------------------------------------------------------------------------------------------------------------

def test_a_chat_of_another_app_is_linked_to_a_task_once(services):
    make_project(services)
    session = f"claude:{CLAUDE_ID}"
    seq = services.tasks.events()[-1]["seq"]
    first = services.projects.link_session("mkt-INV-T01", session, actor=OWNER)
    assert [(s["session_id"], s["linked_by"]) for s in first["sessions"]] == [(session, OWNER)] and first["task_id"] == "mkt-INV-T01"
    assert services.projects.link_session("mkt-INV-T01", session, actor=AGENT)["sessions"][0]["linked_by"] == OWNER
    assert event_types(services, after=seq) == ["task.session_linked"]
    event = services.tasks.events(after=seq)[0]
    assert (event["task_id"], event["group_id"], event["ref"], event["payload"]) == (
        "mkt-INV-T01", "mkt-INV", session, {"session_id": session})
    assert services.projects.unlink_session("mkt-INV-T01", session, actor=OWNER)["sessions"] == []
    assert services.projects.unlink_session("mkt-INV-T01", session, actor=OWNER)["sessions"] == []
    assert event_types(services, after=seq) == ["task.session_linked", "task.session_unlinked"]
    assert error_of(services.projects.link_session, "mkt-INV-T01", "framework:" + "a" * 32, actor=OWNER).code == "invalid_request"
    assert error_of(services.projects.link_session, "mkt-INV-T01", "claude:not-a-session", actor=OWNER).code == "invalid_session_id"
    assert error_of(services.projects.link_session, "GHOST", session, actor=OWNER).status == 404
    assert error_of(services.projects.link_session, "mkt-INV-T01", session, actor="bob").code == "invalid_request"


def test_session_summaries_list_the_tasks_they_work_on(tmp_path):
    stores = make_stores(tmp_path)
    svc = make_session_services(stores)
    try:
        write_claude(stores)
        svc.tasks.create_group("G", "Group", actor="user", authorized=True)
        svc.tasks.create_task("G", "G-T01", "One", actor="user")
        svc.tasks.create_task("G", "G-T02", "Two", actor="user")
        session = f"claude:{CLAUDE_ID}"
        assert svc.sessions.get_session(session)["task_ids"] == []
        svc.projects.link_session("G-T02", session, actor=OWNER)
        svc.projects.link_session("G-T01", session, actor=OWNER)
        assert svc.sessions.get_session(session)["task_ids"] == ["G-T01", "G-T02"]
        listed = {s["id"]: s for s in svc.sessions.list_sessions()["sessions"]}
        assert listed[session]["task_ids"] == ["G-T01", "G-T02"]
        # a framework run takes the task it was created for
        with svc.system.db.connect() as conn:
            conn.execute("INSERT INTO projects(id,name,created_at,title) VALUES('rp','runproject','t','runproject')")
            conn.execute("INSERT INTO runs(id,project_id,workflow_id,status,request_json,max_llm_calls,max_run_seconds,"
                         "created_at,updated_at,task_id) VALUES('run1','rp','social_post','queued','{}',4,60,"
                         "'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','G-T02')")
            conn.commit()
        assert svc.sessions.get_session("framework:run1")["task_ids"] == ["G-T02"]
        assert {s["id"]: s["task_ids"] for s in svc.sessions.list_sessions()["sessions"]}["framework:run1"] == ["G-T02"]
    finally:
        svc.runs.close()


RUN = {"project_name": "Synthetic review", "workflow_id": "spec_review",
       "inputs": {"spec_text": "Users can reset a password by email."}}


def test_a_run_can_carry_the_task_it_works_for(tmp_path):
    svc = make_services(tmp_path / "local", bundles={"team": BUNDLE})
    try:
        make_project(svc)
        run = svc.runs.create(RunRequest(**RUN, task_id="mkt-INV-T01"), bundle="team")
        assert run["task_id"] == "mkt-INV-T01" and run["request"]["task_id"] == "mkt-INV-T01"
        plain = svc.runs.create(RunRequest(**RUN, idempotency_key="no-task"), bundle="team")
        assert plain["task_id"] is None
        # the task must exist in the system database, and the link is part of the request: it cannot change
        missing = error_of(svc.runs.create, RunRequest(**RUN, task_id="mkt-INV-T99"), bundle="team")
        assert (missing.status, missing.code) == (404, "not_found")
        first = svc.runs.create(RunRequest(**RUN, idempotency_key="linked", task_id="mkt-INV-T01"), bundle="team")
        assert svc.runs.create(RunRequest(**RUN, idempotency_key="linked", task_id="mkt-INV-T01"), bundle="team")["id"] == first["id"]
        assert error_of(svc.runs.create, RunRequest(**RUN, idempotency_key="linked", task_id="mkt-INV-T02"),
                        bundle="team").code == "idempotency_conflict"
        assert error_of(svc.runs.create, RunRequest(**RUN, idempotency_key="linked"), bundle="team").code == "idempotency_conflict"
        # telemetry copies the task when it is written
        store = svc.runs._bundle_runtime("team").store
        for run_id in (run["id"], plain["id"]):
            store.record_telemetry(run_id=run_id, step_id="s0", agent_id="a", attempt=1, provider="fake", model="m",
                                   model_digest="", prompt_id="p", prompt_version=1, prompt_sha256="", brand_version=None,
                                   tokens_in=3, tokens_out=2, num_ctx=None, latency_ms=1, duration_ms=1,
                                   estimated_cost_usd=None, success=1, error_type="")
        with store.db.connect() as conn:
            rows = {r["run_id"]: r["task_id"] for r in conn.execute("SELECT run_id,task_id FROM telemetry")}
        assert rows == {run["id"]: "mkt-INV-T01", plain["id"]: None}
    finally:
        svc.runs.close()


def test_a_run_without_a_task_keeps_the_fingerprint_it_had_before_the_field_existed():
    request = RunRequest(**RUN, idempotency_key="k")
    payload = request.model_dump(mode="json")
    # fields that did not exist then: task_id, and CUO-T03's confirm_cloud and server-derived grants
    for name in ("idempotency_key", "task_id", "confirm_cloud", "effective_permissions", "authorized_data_classes"):
        payload.pop(name)
    old = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert _canonical_request_fingerprint(request) == old
    assert _canonical_request_fingerprint(RunRequest(**RUN, task_id="T-1")) != old
    with pytest.raises(ValueError):
        RunRequest(**RUN, task_id="bad id")


# ---------------------------------------------------------------------------------------------------------------
# Facade, HTTP and CLI give the same results
# ---------------------------------------------------------------------------------------------------------------

class _ClientContext:
    """Keep the shared TestClient open across the CLI's per-request context."""

    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc_info) -> bool:
        return False


PARITY_SESSION = f"claude:{CLAUDE_ID}"


BAD_DOC = {**PLAN, "stages": [{"code": "bad", "title": "x", "tasks": []}]}
SCRIPT: list[tuple[str, dict[str, Any]]] = [
    ("labels", {}),
    ("plan", {"doc": PLAN, "key": "par"}),
    ("plan", {"doc": BAD_DOC, "key": "par"}),
    ("create", {"doc": PLAN, "key": "par", "plan_sha256": "{digest}", "actor": AGENT}),
    ("create", {"doc": PLAN, "key": "par", "plan_sha256": "0" * 64, "actor": OWNER}),
    ("create", {"doc": PLAN, "key": "par", "plan_sha256": "{digest}", "actor": OWNER}),
    ("create", {"doc": PLAN, "key": "par", "plan_sha256": "{digest}", "actor": OWNER}),
    ("list", {}),
    ("list", {"archived": True}),
    ("get", {"project_id": "par"}),
    ("get", {"project_id": "ghost"}),
    ("task_list", {"project_id": "par"}),
    ("task_list", {"project_id": "par", "owner": AGENT}),
    ("task_list", {"project_id": "par", "state": "ready"}),
    ("task_list", {"project_id": "par", "due_from": "2026-11-01", "due_to": "2026-11-30"}),
    ("task_list", {"project_id": "par", "state": "almost"}),
    ("task_list", {"project_id": "ghost"}),
    # "private" is what the project already is: HTTP and the CLI do not make a private project "normal" (CNT-T05, see
    # tests/test_quota_handoff.py); the facade does, being in-process.
    ("update", {"project_id": "par", "actor": OWNER, "expected_revision": 1, "title": "Renamed", "visibility": "private"}),
    ("update", {"project_id": "par", "actor": OWNER, "expected_revision": 1, "title": "Stale"}),
    ("archive", {"project_id": "par", "actor": OWNER, "expected_revision": 2}),
    ("list", {"archived": True}),
    ("unarchive", {"project_id": "par", "actor": OWNER, "expected_revision": 3}),
    ("attach_stage", {"project_id": "par", "group_id": "OLD", "actor": OWNER, "expected_revision": 4}),
    ("attach_stage", {"project_id": "par", "group_id": "NOPE", "actor": OWNER, "expected_revision": 5}),
    ("reorder", {"project_id": "par", "group_ids": ["OLD", "par-INV", "par-CON"], "actor": OWNER, "expected_revision": 5}),
    ("reorder", {"project_id": "par", "group_ids": ["OLD"], "actor": OWNER, "expected_revision": 6}),
    ("close_stage", {"project_id": "par", "group_id": "par-INV", "actor": OWNER, "expected_revision": 1}),
    ("link_session", {"task_id": "par-INV-T01", "session_id": PARITY_SESSION, "actor": OWNER}),
    ("link_session", {"task_id": "par-INV-T01", "session_id": "framework:" + "a" * 32, "actor": OWNER}),
    ("link_session", {"task_id": "par-INV-T01", "session_id": "claude:nope", "actor": OWNER}),
    ("unlink_session", {"task_id": "par-INV-T01", "session_id": PARITY_SESSION, "actor": OWNER}),
    ("events", {"project_id": "par"}),
]


def prepare(svc: ApplicationServices) -> None:
    svc.tasks.create_group("OLD", "Imported", actor="user")


def with_digest(args: dict[str, Any], digest: str) -> dict[str, Any]:
    return {key: (digest if value == "{digest}" else value) for key, value in args.items()}


def run_facade(svc: ApplicationServices, op: str, args: dict[str, Any]) -> Any:
    args = dict(args)
    projects = svc.projects
    if op == "labels":
        return projects.labels()
    if op == "plan":
        return projects.plan(args.pop("doc"), **args)
    if op == "create":
        return projects.create(args.pop("doc"), **args)
    if op in ("get", "update", "archive", "unarchive"):
        return getattr(projects, op)(args.pop("project_id"), **args)
    if op == "list":
        return projects.list(**args)
    if op == "task_list":
        return projects.task_list(args.pop("project_id"), **args)
    if op == "reorder":
        return projects.reorder(args.pop("project_id"), args.pop("group_ids"), **args)
    if op in ("attach_stage", "close_stage"):
        return getattr(projects, op)(args.pop("project_id"), args.pop("group_id"), **args)
    if op in ("link_session", "unlink_session"):
        return getattr(projects, op)(args.pop("task_id"), args.pop("session_id"), **args)
    if op == "events":
        return svc.tasks.events(project_id=args["project_id"])
    raise AssertionError(op)  # pragma: no cover


def run_http(client: TestClient, op: str, args: dict[str, Any]) -> tuple[int, Any]:
    a = dict(args)
    seg = lambda value: quote(value, safe="")  # noqa: E731
    if op == "labels":
        response = client.get("/api/v1/task-labels")
    elif op == "plan":
        response = client.post("/api/v1/projects/plan", json=a)
    elif op == "create":
        response = client.post("/api/v1/projects", json=a)
    elif op == "list":
        response = client.get("/api/v1/projects", params={"archived": str(a.get("archived", False)).lower()})
    elif op == "get":
        response = client.get(f"/api/v1/projects/{seg(a['project_id'])}")
    elif op == "task_list":
        response = client.get(f"/api/v1/projects/{seg(a.pop('project_id'))}/tasks", params=a or None)
    elif op == "update":
        response = client.patch(f"/api/v1/projects/{seg(a.pop('project_id'))}", json=a)
    elif op in ("archive", "unarchive"):
        response = client.post(f"/api/v1/projects/{seg(a.pop('project_id'))}/{op}", json=a)
    elif op == "attach_stage":
        response = client.post(f"/api/v1/projects/{seg(a.pop('project_id'))}/stages", json=a)
    elif op == "reorder":
        response = client.put(f"/api/v1/projects/{seg(a.pop('project_id'))}/stages", json=a)
    elif op == "close_stage":
        response = client.post(f"/api/v1/projects/{seg(a.pop('project_id'))}/stages/{seg(a.pop('group_id'))}/close", json=a)
    elif op == "link_session":
        response = client.post(f"/api/v1/tasks/{seg(a.pop('task_id'))}/sessions", json=a)
    elif op == "unlink_session":
        response = client.delete(f"/api/v1/tasks/{seg(a.pop('task_id'))}/sessions", params=a)
    elif op == "events":
        response = client.get("/api/v1/team-events", params={"project": a["project_id"]})
    else:  # pragma: no cover
        raise AssertionError(op)
    return response.status_code, response.json()


def cli_args(op: str, args: dict[str, Any], doc_file: Path) -> list[str]:
    a = dict(args)
    if op == "labels":
        return ["task", "labels"]
    if op in ("plan", "create"):
        doc_file.write_text(json.dumps(a["doc"]), encoding="utf-8")
        argv = ["project", op, str(doc_file), "--key", a["key"]]
        if op == "create":
            argv += ["--actor", a["actor"], "--plan-sha256", a["plan_sha256"]]
        return argv
    if op == "list":
        return ["projects"] + (["--archived"] if a.get("archived") else [])
    if op == "get":
        return ["project", "show", a["project_id"]]
    if op == "task_list":
        argv = ["project", "tasks", a.pop("project_id")]
        for name, value in a.items():
            argv += ["--" + name.replace("_", "-"), value]
        return argv
    if op == "update":
        argv = ["project", "update", a["project_id"], "--actor", a["actor"], "--revision", str(a["expected_revision"])]
        argv += ["--title", a["title"]] if "title" in a else []
        return argv + (["--visibility", a["visibility"]] if "visibility" in a else [])
    if op in ("archive", "unarchive"):
        return ["project", op, a["project_id"], "--actor", a["actor"], "--revision", str(a["expected_revision"])]
    if op == "attach_stage":
        return ["project", "stage", "attach", a["project_id"], a["group_id"], "--actor", a["actor"],
                "--revision", str(a["expected_revision"])]
    if op == "reorder":
        return ["project", "stage", "order", a["project_id"], *a["group_ids"], "--actor", a["actor"],
                "--revision", str(a["expected_revision"])]
    if op == "close_stage":
        return ["project", "stage", "close", a["project_id"], a["group_id"], "--actor", a["actor"],
                "--revision", str(a["expected_revision"])]
    if op in ("link_session", "unlink_session"):
        return ["task", "session", "link" if op == "link_session" else "unlink", a["task_id"], a["session_id"],
                "--actor", a["actor"]]
    if op == "events":
        return ["team-events", "--project", a["project_id"]]
    raise AssertionError(op)  # pragma: no cover


def run_cli(monkeypatch, capsys, client: TestClient, op: str, args: dict[str, Any], tmp_path: Path) -> tuple[int, Any]:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    code = 0
    try:
        cli.main(cli_args(op, args, tmp_path / "plan.json"))
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if code == 0 else captured.err)


def normalize(value: Any) -> Any:
    """Drop the timestamps: everything else must be identical across interfaces."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items() if k not in ("created_at", "archived_at", "closed_at", "actual")}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def test_facade_http_and_cli_give_the_same_results(monkeypatch, capsys, tmp_path):
    facade = make_services(tmp_path / "facade")
    http = make_services(tmp_path / "http")
    through_cli = make_services(tmp_path / "cli")
    for svc in (facade, http, through_cli):
        prepare(svc)
    digest = facade.projects.plan(PLAN, key="par")["plan_sha256"]
    try:
        with TestClient(create_app(services=http), base_url="http://127.0.0.1") as http_client, \
                TestClient(create_app(services=through_cli), base_url="http://127.0.0.1") as cli_client:
            for step, (op, raw) in enumerate(SCRIPT):
                args = with_digest(raw, digest)
                try:
                    expected: tuple[str, Any] = ("ok", normalize(run_facade(facade, op, args)))
                except ServiceError as error:
                    expected = ("error", (error.status, error.code))
                status, body = run_http(http_client, op, args)
                if expected[0] == "ok":
                    assert status in (200, 201), (step, op, body)
                    assert normalize(body) == expected[1], (step, op)
                else:
                    assert (status, body["detail"]["code"]) == expected[1], (step, op, body)
                code, output = run_cli(monkeypatch, capsys, cli_client, op, args, tmp_path)
                if expected[0] == "ok":
                    assert code == 0 and normalize(output) == expected[1], (step, op, output)
                else:
                    assert code == 1 and f"HTTP {expected[1][0]}: {expected[1][1]}:" in output, (step, op, output)
    finally:
        for svc in (facade, http, through_cli):
            svc.runs.close()


def test_the_parity_script_covers_every_public_operation():
    operations = {op for op, _ in SCRIPT}
    public = {name for name, member in vars(ProjectServices).items() if callable(member) and not name.startswith("_")}
    assert operations - {"events"} == public   # "events" is the team event log read with the project filter


def test_the_cli_reads_json_or_yaml_plan_files_and_creates_with_its_own_hash(monkeypatch, capsys, tmp_path):
    svc = make_services(tmp_path / "local")
    yaml_file = tmp_path / "plan.yaml"
    yaml_file.write_text(
        "schema: maf.project-template/1\nid: from-yaml\ntitle: From YAML\nstages:\n"
        "  - code: ONE\n    title: First\n    planned: {start: 2026-12-01, end: 2026-12-31}\n"
        "    tasks:\n      - {id: T01, title: Do it, due: 2026-12-15}\n", encoding="utf-8")
    try:
        with TestClient(create_app(services=svc), base_url="http://127.0.0.1") as client:
            monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
            cli.main(["project", "create", str(yaml_file), "--key", "yml", "--actor", OWNER])
            created = json.loads(capsys.readouterr().out)
            assert created["stages"][0]["planned_start"] == "2026-12-01"
            assert created["stages"][0]["tasks"][0]["due_at"] == "2026-12-15" and created["visibility"] == "private"
            for content, message in (("- a\n- b\n", "must contain an object"), ("a: [unclosed", "not valid JSON or YAML")):
                bad_file = tmp_path / "bad.yaml"
                bad_file.write_text(content, encoding="utf-8")
                with pytest.raises(SystemExit):
                    cli.main(["project", "plan", str(bad_file), "--key", "yml"])
                assert message in capsys.readouterr().err
            with pytest.raises(SystemExit):
                cli.main(["project", "plan", str(tmp_path / "missing.json"), "--key", "yml"])
            assert "Could not read plan file" in capsys.readouterr().err
            big = tmp_path / "big.json"
            big.write_bytes(b" " * (256 * 1024 + 1))
            with pytest.raises(SystemExit):
                cli.main(["project", "plan", str(big), "--key", "yml"])
            assert "larger than" in capsys.readouterr().err
    finally:
        svc.runs.close()


def test_the_http_labels_route_serves_the_same_map(tmp_path):
    svc = make_services(tmp_path / "local")
    try:
        with TestClient(create_app(services=svc), base_url="http://127.0.0.1") as client:
            assert client.get("/api/v1/task-labels").json() == labels.load()
            assert client.get("/api/v1/projects").json() == []
            bad_body = client.post("/api/v1/projects/plan", json={"doc": PLAN})
            assert bad_body.status_code == 422 and bad_body.json()["detail"]["code"] == "invalid_request"
    finally:
        svc.runs.close()


def test_list_items_carry_the_same_weighted_progress_as_get(services):
    make_project(services, "one")
    make_project(services, "two")
    services.system.store.get_or_create_project("from-a-run")
    authorize(services, "one-INV")
    deliver(services, "one-INV-T01")
    rows = services.projects.list()
    assert len(rows) == 3
    for row in rows:
        assert "progress_weighted" in row and row["progress_weighted"] == services.projects.get(row["id"])["progress_weighted"]
