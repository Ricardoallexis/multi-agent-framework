# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The token-protected open-activities feed and its sidebar projection."""
from __future__ import annotations

import copy
import json
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from test_gui_help import NODE
from test_gui_project import auth, env

JS = STATIC_DIR / "js"
PLAN = {
    "schema": "maf.project-template/1",
    "id": "synthetic-activities",
    "title": "Proyecto sintético",
    "description": "Proyecto de prueba de actividades.",
    "stages": [{
        "code": "INV",
        "title": "Investigación",
        "phases": [],
        "tasks": [{
            "id": "T01",
            "title": "Tarea sintética",
            "objective": "Validar el proyecto.",
            "assignee": "agent:writer/m1",
        }],
    }],
}


def create_project(env, key):
    doc = copy.deepcopy(PLAN)
    plan = env.services.projects.plan(doc, key=key)
    return env.services.projects.create(doc, key=key, plan_sha256=plan["plan_sha256"], actor="user:operator")


def test_activities_route_returns_only_open_activities_grouped_by_project(env):
    project = create_project(env, "snt")
    open_activity = env.services.activities.start("snt", "Preparar fuentes", actor="user:operator")
    closed_activity = env.services.activities.start("snt", "Revisar borrador", actor="user:operator")
    env.services.activities.close(closed_activity["id"], "Revisión terminada", actor="user:operator")

    empty_project = create_project(env, "oth")
    completed = env.services.activities.start("oth", "Actividad cerrada", actor="user:operator")
    env.services.activities.close(completed["id"], "Terminada", actor="user:operator")

    response = env.client.get("/gui/api/activities", headers=auth(env))
    assert response.status_code == 200, response.text
    result = response.json()["projects"]
    assert len(result) == 1
    assert result[0]["project_id"] == project["id"] == "snt"
    assert result[0]["name"] == project["name"]
    assert [activity["id"] for activity in result[0]["activities"]] == [open_activity["id"]]
    assert result[0]["activities"][0] == env.services.activities.list("snt")[0]
    assert empty_project["id"] not in [item["project_id"] for item in result]


def test_activities_route_requires_the_gui_token(env):
    assert env.client.get("/gui/api/activities").status_code == 401
    assert env.client.get("/gui/api/activities", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_sidebar_activities_filter_uses_token_route_and_accessible_toggle():
    sidebar = (JS / "sidebar.js").read_text(encoding="utf-8")
    assert 'apiGet("/gui/api/activities", token)' in sidebar
    assert 'aria-pressed=${activitiesMode ? "true" : "false"}' in sidebar
    assert ">Actividades</button>" in sidebar
    assert "activityItems(activityData.projects)" in sidebar


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_activity_model_keeps_open_items_and_groups_and_filters_them():
    script = """
      import { activityItems, buildGroups } from %s;
      const activities = [
        { id: "p-ACT-T01", title: "Preparar fuentes", updated_at: "2026-11-01T10:00:00Z",
          label: { key: "doing", text: "En curso", tone: "active" }, assignee_info: { name: "Equipo" } },
        { id: "p-ACT-T02", title: "Cerrada", label: { key: "done", text: "Hecha", tone: "success" } },
        { id: "p-ACT-T03", title: "Fallida", label: { key: "failed", text: "Falló", tone: "danger" } },
        { id: "p-ACT-T04", title: "Cancelada", label: { key: "cancelled", text: "Cancelada", tone: "muted" } },
      ];
      const items = activityItems([{ project_id: "p", name: "Proyecto", activities }]);
      console.log(JSON.stringify({
        items: items.map(({ key, project, title, dot, dotLabels, who, route }) =>
          ({ key, project, title, dot, label: dotLabels[dot].text, who, route })),
        searched: buildGroups(items, "equipo").flatMap((group) => group.items.map((item) => item.key)),
      }));
    """ % json.dumps((JS / "sidebar-model.js").as_uri())
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["items"] == [{
        "key": "activity:p-ACT-T01", "project": "Proyecto", "title": "Preparar fuentes",
        "dot": "doing", "label": "En curso", "who": "Equipo", "route": ["project", "p"],
    }]
    assert result["searched"] == ["activity:p-ACT-T01"]
