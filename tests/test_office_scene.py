# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ESC-T01: the office scene is a bounded, read-only view of persisted work."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.offices.plan import office_group_id
from multiagent.offices.scene import (
    IDLE,
    IDLE_LABEL,
    SCENE_SCHEMA,
    STATE_PRIORITY,
    build_scene,
    run_state,
    winning_state,
)
from multiagent.services import ApplicationServices
from test_offices_install import make_settings, write_office

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
TEAM = {
    "roles": [{"id": "redaccion"}, {"id": "revision"}, {"id": "otra"}],
    "members": [
        {"id": "writer", "kind": "agent", "agent": "writer_agent", "name": "Writer"},
        {"id": "reviewer", "kind": "person", "name": "Reviewer"},
        {"id": "other", "kind": "person", "name": "Other"},
    ],
    "assignments": [
        {"role": "redaccion", "member": "writer"},
        {"role": "revision", "member": "reviewer"},
        {"role": "otra", "member": "other"},
    ],
    "relationships": [],
}


def _office(**fields):
    return {"id": "demo", "name": "Demo office", "state": "ok",
            "orchestrator": {"enabled": True, "name": "Coordinator"}, "relations": [], **fields}


def _area(*, runs=None, team=None, **fields):
    return {
        "id": "recepcion", "name": "Reception", "bundle": "demo__recepcion",
        "package_state": "ok", "data_class": "non_sensitive", "degraded": [],
        "team": team or TEAM, "runs": runs or [], **fields,
    }


def _run(status, *, member="writer", role="redaccion", updated=None, worker="local_default", revised=False):
    return {
        "id": f"run-{status}", "workflow_id": "spec_review", "status": status, "current_step": 0,
        "waiting_step": "review" if status == "waiting_human" else "",
        "waiting_reason": "review" if status == "waiting_human" else "",
        "revised": revised, "step_role": role, "step_member": member, "step_model_id": worker,
        "updated_at": (updated or NOW).isoformat(),
    }


def _scene(areas=None, proposals=None, office=None, *, now=NOW):
    return build_scene(office or _office(), areas if areas is not None else [_area()],
                       proposals or [], now=now)


def _member(scene, member_id="writer", area_id="recepcion"):
    area = next(item for item in scene["areas"] if item["id"] == area_id)
    return next(item for item in area["members"] if item["id"] == member_id)


def test_review_wins_over_failed_and_doing():
    area = _area(runs=[_run("waiting_human"), _run("running"), _run("failed")])
    scene = _scene([area])
    assert (_member(scene)["state"], _member(scene)["label"]) == ("review", "Espera tu revisión")


def test_state_priority_is_l17():
    assert STATE_PRIORITY == ("review", "changes", "failed", "doing", "blocked", "ready", "todo", "done", "cancelled")
    assert winning_state(["done", "ready"]) == "ready"


def test_idle_without_sources():
    scene = _scene([_area(runs=[])])
    assert scene["areas"][0]["state"] == IDLE
    assert scene["areas"][0]["label"] == IDLE_LABEL
    for member in scene["areas"][0]["members"]:
        assert (member["state"], member["label"], member["since"]) == (IDLE, IDLE_LABEL, None)


def test_run_without_role_counts_for_area():
    scene = _scene([_area(runs=[_run("running", member=None, role=None)])])
    assert scene["areas"][0]["state"] == "doing"
    assert all(member["state"] == IDLE for member in scene["areas"][0]["members"])


def test_area_shows_its_busiest_member():
    team = {
        "roles": [{"id": "redaccion"}, {"id": "revision"}, {"id": "otra"}],
        "members": [
            {"id": "writer", "kind": "agent", "name": "Writer"},
            {"id": "reviewer", "kind": "person", "name": "Reviewer"},
            {"id": "other", "kind": "agent", "name": "Other"},
        ],
        "assignments": [
            {"role": "redaccion", "member": "writer"},
            {"role": "revision", "member": "reviewer"},
            {"role": "otra", "member": "other"},
        ],
        "relationships": [],
    }
    review_since = NOW - timedelta(hours=2)
    doing_since = NOW - timedelta(hours=1)
    scene = _scene([_area(team=team, runs=[
        _run("waiting_human", member="writer", updated=review_since),
        _run("running", member="other", role="otra", updated=doing_since),
    ])])
    area = scene["areas"][0]
    assert (area["state"], area["label"], area["since"]) == (
        "review", "Espera tu revisión", review_since.isoformat())
    assert _member(scene, "reviewer")["state"] == "review"
    assert _member(scene, "other")["state"] == "doing"


def test_window_is_24_hours():
    old = NOW - timedelta(hours=25)
    active_old = NOW - timedelta(days=3)
    completed = _area(id="completed", bundle="demo__completed", runs=[_run("completed", updated=old)])
    running = _area(id="running", bundle="demo__running", runs=[_run("running", updated=active_old)])
    scene = _scene([completed, running])
    by_id = {area["id"]: area for area in scene["areas"]}
    assert by_id["completed"]["state"] == IDLE
    assert by_id["running"]["state"] == "doing"


def test_run_state_table():
    assert run_state({"status": "paused"}) == "doing"
    assert run_state({"status": "queued"}) == "ready"
    assert run_state({"status": "queued", "revised": True}) == "changes"
    assert run_state({"status": "interrupted"}) == "failed"
    assert run_state({"status": "rejected"}) == "cancelled"


def test_person_reviews_by_relationship():
    team = {**TEAM, "relationships": [{"type": "reviews", "from": "revision", "to": "redaccion"}]}
    scene = _scene([_area(team=team, runs=[_run("waiting_human")])])
    assert _member(scene, "writer")["state"] == "review"
    assert _member(scene, "reviewer")["state"] == "review"
    assert _member(scene, "other")["state"] == IDLE

    scene = _scene([_area(team=TEAM, runs=[_run("waiting_human")])])
    assert _member(scene, "reviewer")["state"] == "review"
    assert _member(scene, "other")["state"] == "review"

    no_people = {"roles": [{"id": "redaccion"}],
                 "members": [{"id": "writer", "kind": "agent", "agent": "writer_agent"}],
                 "assignments": [{"role": "redaccion", "member": "writer"}], "relationships": []}
    scene = _scene([_area(team=no_people, runs=[_run("waiting_human")])])
    assert scene["areas"][0]["state"] == "review"
    assert _member(scene)["state"] == "review"


def test_worker_is_the_last_attempt():
    scene = _scene([_area(runs=[_run("running", worker="model-last-attempt")])])
    assert _member(scene)["worker"] == "model-last-attempt"
    scene = _scene([_area(runs=[_run("running", worker="")])])
    assert _member(scene)["worker"] == "—"


def test_orchestrator_from_office_proposals():
    group_id = office_group_id("demo")
    proposed = {
        "id": "proposal-1", "group_id": group_id, "status": "proposed", "created_at": NOW.isoformat(),
        "proposal": {"tasks": [{"id": "task-1", "bundle": "demo__recepcion"}]}, "scene_tasks": {},
    }
    other = {**proposed, "id": "proposal-other", "group_id": office_group_id("other")}
    scene = _scene(proposals=[other, proposed])
    assert scene["orchestrator"]["state"] == "review"
    assert scene["orchestrator"]["plan"]["href"] == "#new/plan/proposal-1"

    approved = {
        **proposed, "id": "proposal-2", "status": "approved",
        "proposal": {"tasks": [{"id": "task-2", "bundle": "demo__recepcion"}]},
        "scene_tasks": {"task-2": {"id": "task-2", "state": "running", "status": "ready",
                                   "updated_at": NOW.isoformat()}},
    }
    scene = _scene(proposals=[approved])
    assert scene["orchestrator"]["state"] == "doing"
    assert scene["areas"][0]["state"] == "doing"


def test_unknown_area_relation_has_a_warning():
    office = _office(relations=[{"from": "recepcion", "to": "removed", "kind": "hands_off"}])
    scene = _scene(office=office)
    assert scene["relations"] == [{
        "from": "recepcion", "to": "removed", "kind": "hands_off",
        "label": "entrega", "warning": "office_relation_unknown_area",
    }]


@pytest.fixture
def world(tmp_path):
    services = ApplicationServices.create(
        make_settings(tmp_path / "local"), dry_run=True,
    )
    yield services, tmp_path
    services.bundle_scans.close()
    services.runs.close()


def test_degraded_states_carry_their_fix(world):
    services, tmp_path = world
    services.offices.import_folder(write_office(tmp_path / "demo", areas=("recepcion",)), actor="user:test")
    scene = services.offices.scene("demo", now=NOW)
    area = scene["areas"][0]
    team_fix = next(item for item in area["degraded"] if item["code"] == "team_not_approved")
    version_id = services.offices.get("demo")["areas"][0]["team"]["version_id"]
    assert team_fix == {
        "code": "team_not_approved",
        "fix": {"label": "Aprobar equipo", "href": f"#new/team/{version_id}", "action": None},
    }

    services.installed._states["demo__recepcion"] = "changed"
    changed = services.offices.scene("demo", now=NOW)["areas"][0]
    fix = next(item for item in changed["degraded"] if item["code"] == "installed_bundle_changed")
    assert fix["fix"]["label"] == "Reimportar la oficina" and fix["fix"]["action"] == "reimport"


def test_scene_never_loads_a_bundle(world, monkeypatch):
    services, tmp_path = world
    services.offices.import_folder(write_office(tmp_path / "demo", areas=("recepcion",)), actor="user:test")
    approvals_before = [event for event in services.tasks.store.events(limit=500)
                        if event["type"].startswith("user_approval.")]

    def forbidden(_bundle):
        pytest.fail("scene must not call require_load")

    monkeypatch.setattr(services.bundle_scans, "require_load", forbidden)
    for _ in range(3):
        services.offices.scene("demo", now=NOW)
    services.installed._states["demo__recepcion"] = "changed"
    services.offices.scene("demo", now=NOW)
    approvals_after = [event for event in services.tasks.store.events(limit=500)
                       if event["type"].startswith("user_approval.")]
    assert approvals_after == approvals_before


def test_scene_never_checks_connections(world, monkeypatch):
    services, tmp_path = world
    services.offices.import_folder(
        write_office(tmp_path / "demo", areas=("recepcion",),
                     overrides={"recepcion": {"requires": ["model.text"]}}),
        actor="user:test",
    )
    monkeypatch.setattr(services.settings, "mock_mode", False)

    def forbidden(*_args, **_kwargs):
        pytest.fail("scene must not list or check connections")

    monkeypatch.setattr(services.connections, "list", forbidden)
    monkeypatch.setattr(services.connections, "check", forbidden)
    scene = services.offices.scene("demo", now=NOW)
    assert not any(item["code"] == "requirement_missing" for item in scene["areas"][0]["degraded"])

    services.connections._last["openai"] = {
        "id": "openai", "kind": "model", "available": False,
    }
    scene = services.offices.scene("demo", now=NOW)
    assert any(item["code"] == "requirement_missing" for item in scene["areas"][0]["degraded"])


def _seed_run(services, bundle: str, run_id: str, *, updated_at: datetime, request_text: str = "safe"):
    settings = services.runs._bundle_settings(bundle)
    db = Database(settings)
    if not settings.db_path.is_file():
        db.migrate()
    stamp = updated_at.isoformat()
    with db.tx() as conn:
        conn.execute("INSERT OR IGNORE INTO projects(id,name,created_at) VALUES(?,?,?)",
                     ("scene-project", "Scene project", stamp))
        conn.execute(
            """INSERT INTO runs(id,project_id,workflow_id,status,request_json,max_llm_calls,max_run_seconds,
                                created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, "scene-project", "spec_review", "running", json.dumps({"sentinel": request_text}),
             10, 300, stamp, stamp),
        )
        conn.execute(
            """INSERT INTO run_steps(run_id,step_id,attempt,status,input_json,output_json,model_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (run_id, "extract_requirements", 2, "running", "{}", "{}", "model-last", stamp, stamp),
        )
        conn.execute(
            "INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
            (run_id, stamp, "step_started",
             json.dumps({"step_id": "extract_requirements", "attempt": 2, "role": "analyst",
                         "member": "analyst_agent"})),
        )


def test_scene_has_no_paths_or_payloads(world):
    services, tmp_path = world
    services.offices.import_folder(write_office(tmp_path / "demo", areas=("recepcion",)), actor="user:test")
    _seed_run(services, "demo__recepcion", "private-run", updated_at=NOW, request_text="private-input-sentinel")
    scene = services.offices.scene("demo", now=NOW)
    encoded = json.dumps(scene, ensure_ascii=False)
    assert "private-input-sentinel" not in encoded
    assert str(tmp_path).lower() not in encoded.lower()
    assert "data_dir" not in encoded and "input_json" not in encoded and "output_json" not in encoded


def test_one_query_per_area(world, monkeypatch):
    services, tmp_path = world
    area_ids = tuple(f"area-{index}" for index in range(10))
    services.offices.import_folder(write_office(tmp_path / "demo", areas=area_ids), actor="user:test")
    for area_id in area_ids:
        for index in range(20):
            _seed_run(services, f"demo__{area_id}", f"{area_id}-run-{index}",
                      updated_at=NOW - timedelta(minutes=index))

    queries = []
    original = Store.scene_runs

    def counted(store, **kwargs):
        queries.append(kwargs)
        return original(store, **kwargs)

    monkeypatch.setattr(Store, "scene_runs", counted)
    started = time.perf_counter()
    scene = services.offices.scene("demo", now=NOW)
    elapsed = time.perf_counter() - started
    assert len(queries) <= 10
    assert elapsed < 1.0
    assert scene["truncated"] is False


class _ClientContext:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_scene_route_and_cli_parity(world, monkeypatch, capsys):
    services, _ = world
    root = Path(__file__).resolve().parents[1]
    services.offices.import_folder(root / "examples" / "offices" / "cafeteria", actor="user:test")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.get("/api/v1/offices/cafeteria/scene")
        assert response.status_code == 200
        monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
        cli.main(["office", "scene", "cafeteria", "--json"])
        actual = json.loads(capsys.readouterr().out)
        expected = response.json()
        assert actual["schema"] == expected["schema"] == SCENE_SCHEMA
        actual.pop("generated_at")
        expected.pop("generated_at")
        assert actual == expected
        assert client.get("/api/v1/offices/missing/scene").status_code == 404


def _roleless(status, **fields):
    return {**_run(status, member=None, role=None, **fields), "step_id": "redactar"}


STEP_AGENTS = {"spec_review": {"redactar": "writer_agent"}}


def test_step_without_role_goes_to_the_agent_that_matches():
    area = _area(runs=[_roleless("running")], step_agents=STEP_AGENTS)
    scene = _scene([area])
    assert _member(scene)["state"] == "doing"
    assert _member(scene, "reviewer")["state"] == IDLE


def test_step_without_role_with_two_matching_agents_stays_with_the_area():
    team = {**TEAM, "members": [*TEAM["members"],
                                {"id": "twin", "kind": "agent", "agent": "writer_agent", "name": "Twin"}]}
    scene = _scene([_area(team=team, runs=[_roleless("running")], step_agents=STEP_AGENTS)])
    assert scene["areas"][0]["state"] == "doing"
    assert _member(scene)["state"] == IDLE and _member(scene, "twin")["state"] == IDLE


def test_step_without_role_with_no_matching_agent_stays_with_the_area():
    for step_agents in ({"spec_review": {"redactar": "unknown_agent"}}, {}, None):
        scene = _scene([_area(runs=[_roleless("running")], step_agents=step_agents)])
        assert scene["areas"][0]["state"] == "doing"
        assert _member(scene)["state"] == IDLE


def test_waiting_human_without_role_uses_the_role_of_the_agent():
    team = {**TEAM, "relationships": [{"type": "reviews", "from": "revision", "to": "redaccion"}]}
    scene = _scene([_area(team=team, runs=[_roleless("waiting_human")], step_agents=STEP_AGENTS)])
    assert _member(scene)["state"] == "review"
    assert _member(scene, "reviewer")["state"] == "review"
    assert _member(scene, "other")["state"] == IDLE


def test_scene_survives_broken_workflow_files_for_step_agents(world):
    services, tmp_path = world
    services.offices.import_folder(write_office(tmp_path / "demo", areas=("recepcion",)), actor="user:test")
    folder = services.bundle_path("demo__recepcion")
    flows = sorted((folder / "workflows").glob("*.yaml"))
    assert flows
    flows[0].write_text("steps: [unclosed\n  - : :\n", encoding="utf-8")
    (folder / "workflows" / "odd.yaml").write_text("steps:\n  - 3\n  - {id: 5, agent: [x]}\n", encoding="utf-8")
    (folder / "workflows" / "scalar.yaml").write_text("just text\n", encoding="utf-8")
    scene = services.offices.scene("demo", now=NOW)
    assert scene["areas"][0]["state"] == "idle" and "step_agents" not in scene["areas"][0]
