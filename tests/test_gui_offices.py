# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""VOF-T01: the read-only Offices view, its scene model, and GUI routes."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.errors import ServiceError

EXT = STATIC_DIR / "extensions" / "offices"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

OFFICE = {
    "id": "demo",
    "name": "Demo",
    "state": "ok",
    "areas": [{"id": "one"}, {"id": "two"}],
}
DETAIL = {**OFFICE, "version": "1", "orchestrator": {"name": "Coordinator"}}
REQUIREMENTS = {
    "office": "demo",
    "areas": [
        {"id": "one", "text": "Lo puedes cubrir con A o con B"},
        {"id": "two", "text": "Te falta un agente que pueda trabajar con archivos"},
    ],
}
SCENE = {
    "schema": "maf.office-scene/1",
    "office": {"id": "demo", "name": "Demo", "state": "ok"},
    "orchestrator": {"state": "idle", "label": "Libre", "plan": None},
    "areas": [],
    "truncated": False,
}


def run_node(script: str):
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def file_url(path: Path) -> str:
    return json.dumps(path.as_uri())


class OfficeStub:
    def list(self):
        return [OFFICE]

    def get(self, office_id):
        if office_id != "demo":
            raise ServiceError("invalid_state", "not installed", status=409,
                               details={"reason": "office_not_installed"})
        return DETAIL

    def requirements(self, office_id):
        if office_id != "demo":
            raise ServiceError("invalid_state", "not installed", status=409,
                               details={"reason": "office_not_installed"})
        return REQUIREMENTS

    def scene(self, office_id):
        if office_id != "demo":
            raise ServiceError("invalid_state", "not installed", status=409,
                               details={"reason": "office_not_installed"})
        return SCENE


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, dry_run=True)
    services.offices = OfficeStub()
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def headers(env):
    return {"Authorization": f"Bearer {env.token}"}


def get(env, path, **kwargs):
    return env.client.get("/gui/api" + path, headers=headers(env), **kwargs)


@needs_node
def test_registered_in_both_modes():
    out = run_node(f"""
      import {{ loadExtensions, registeredViews, registeredHelp }} from {file_url(JS / "extensions.js")};
      import {{ visibleIn }} from {file_url(JS / "gui-mode.js")};
      const report = await loadExtensions({{
        fetchManifest: async () => ({{ extensions: [{{ id: "offices", script: "/gui/static/extensions/offices/index.js",
          style: "/gui/static/extensions/offices/style.css" }}] }}),
        importModule: () => import({file_url(EXT / "index.js")}),
        addStylesheet: async () => {{}},
      }});
      const view = registeredViews().find((item) => item.id === "offices");
      console.log(JSON.stringify({{
        failed: report.failed,
        view: view && {{ id: view.id, label: view.label, topic: view.topic, order: view.order,
          group: view.group, icon: view.icon, mode: view.mode,
          simple: visibleIn(view, "simple"), advanced: visibleIn(view, "advanced") }},
        help: ["offices.view", "offices.plan", "offices.member_state", "offices.degraded", "offices.declared", "offices.levels"]
          .every((key) => registeredHelp(key)),
      }}));
    """)
    assert out == {
        "failed": [],
        "view": {
            "id": "offices",
            "label": "Oficinas",
            "topic": "offices.view",
            "order": 30,
            "group": "primary",
            "icon": "building",
            "mode": "simple",
            "simple": True,
            "advanced": True,
        },
        "help": True,
    }


@needs_node
def test_office_rows_from_a_scene():
    out = run_node(f"""
      import {{ officeRows, memberLine }} from {file_url(EXT / "model.js")};
      const scene = {{
        orchestrator: {{ state: "idle", label: "Libre" }},
        areas: [
          {{ id: "one", name: "Área uno", state: "idle", label: "Libre", data_class: "non_sensitive",
            members: [{{ id: "agent", kind: "agent", name: "local_default", worker: "—", label: "Libre" }}] }},
          {{ id: "two", name: "Área dos", state: "idle", label: "Libre", data_class: null,
            members: [{{ id: "person", kind: "person", name: "Responsable", label: "Libre" }}] }},
        ],
      }};
      const requirements = {{ areas: [
        {{ id: "one", text: "Lo puedes cubrir con A o con B" }},
        {{ id: "two", text: "Te falta un agente que pueda trabajar con archivos" }},
      ] }};
      const rows = officeRows(scene, requirements);
      console.log(JSON.stringify({{
        rows: rows.map((row) => [row.kind, row.name, row.label, row.line || "", row.requirementText || ""]),
        agent: memberLine({{ kind: "agent", name: "local_default" }}),
        person: memberLine({{ kind: "person", name: "Responsable" }}),
      }}));
    """)
    assert out == {
        "rows": [
            ["orchestrator", "Coordinación", "Libre", "", ""],
            ["area", "Área uno", "Libre", "", "Lo puedes cubrir con A o con B"],
            ["member", "local_default", "Libre", "lo hace: local_default", ""],
            ["area", "Área dos", "Libre", "", "Te falta un agente que pueda trabajar con archivos"],
            ["member", "Responsable", "Libre", "persona: Responsable", ""],
        ],
        "agent": "lo hace: local_default",
        "person": "persona: Responsable",
    }


@needs_node
def test_level_text():
    out = run_node(f"""
      import {{ LEVEL_TEXT, levelText }} from {file_url(EXT / "model.js")};
      console.log(JSON.stringify({{
        restricted: LEVEL_TEXT.restricted,
        none: levelText(null),
        normal: levelText("non_sensitive"),
      }}));
    """)
    assert out == {"restricted": "Solo local", "none": "Sin nivel", "normal": "Normal"}


@needs_node
def test_office_header_follows_its_areas():
    out = run_node(f"""
      import {{ officeWorks }} from {file_url(EXT / "model.js")};
      const waiting = {{ areas: [{{ id: "menu", degraded: [{{ code: "team_not_approved" }}] }}, {{ id: "avisos", degraded: [] }}] }};
      const fine = {{ areas: [{{ id: "menu", degraded: [] }}] }};
      console.log(JSON.stringify({{
        waiting: officeWorks({{ state: "ok" }}, waiting),
        fine: officeWorks({{ state: "ok" }}, fine),
        broken: officeWorks({{ state: "degraded" }}, fine),
        no_scene_yet: officeWorks({{ state: "ok" }}, null),
      }}));
    """)
    assert out == {"waiting": False, "fine": True, "broken": False, "no_scene_yet": True}


@needs_node
def test_degraded_actions():
    out = run_node(f"""
      import {{ degradedAction }} from {file_url(EXT / "model.js")};
      const entries = [
        {{ code: "team_not_approved", fix: {{ label: "Aprobar equipo", href: "#new/team/v-1", action: null }} }},
        {{ code: "requirement_missing", fix: {{ label: "Conectar un agente", href: "#apps", action: null }} }},
        {{ code: "installed_bundle_changed", fix: {{ label: "Reimportar la oficina", href: null, action: "reimport" }} }},
      ];
      console.log(JSON.stringify(entries.map(degradedAction)));
    """)
    assert out == [
        {"label": "Aprobar equipo", "href": "#new/team/v-1", "action": None},
        {"label": "Conectar un agente", "href": "#apps", "action": None},
        {"label": "Reimportar la oficina", "href": None, "action": "reimport"},
    ]


@needs_node
def test_permissions_say_declared():
    out = run_node(f"""
      import {{ declaredPermissionText }} from {file_url(EXT / "model.js")};
      console.log(JSON.stringify(declaredPermissionText({{ roles: [{{ permissions: ["read", "write"] }}] }})));
    """)
    assert "declarado" in out
    assert "garantizado" not in out


@needs_node
def test_empty_state():
    out = run_node(f"""
      import {{ EMPTY_TEXT, sceneTargets }} from {file_url(EXT / "model.js")};
      console.log(JSON.stringify({{ text: EMPTY_TEXT, targets: sceneTargets([]), noList: sceneTargets(null) }}));
    """)
    assert out == {"text": "Todavía no tienes oficinas", "targets": [], "noList": []}


@needs_node
def test_polls_every_4_seconds_and_keeps_the_last_scene_on_failure():
    out = run_node(f"""
      import {{ createScenePoller, OFFLINE_TEXT, POLL_MS }} from {file_url(EXT / "model.js")};
      let callback;
      let interval;
      let cleared = 0;
      let calls = 0;
      let last = null;
      let notice = "";
      const answers = [{{ name: "first" }}, new Error("offline"), {{ name: "after-stop" }}];
      const poller = createScenePoller({{
        fetchScene: async () => {{
          calls += 1;
          const value = answers.shift();
          if (value instanceof Error) throw value;
          return value;
        }},
        onScene: (scene) => {{ last = scene; notice = ""; }},
        onFailure: () => {{ notice = OFFLINE_TEXT; }},
        setIntervalFn: (fn, ms) => {{ callback = fn; interval = ms; return 7; }},
        clearIntervalFn: (id) => {{ if (id === 7) cleared += 1; }},
      }});
      await poller.start();
      await callback();
      const afterFailure = {{ calls, last: last.name, notice }};
      poller.stop();
      await callback();
      console.log(JSON.stringify({{
        interval, pollMs: POLL_MS, afterFailure, callsAfterStop: calls, cleared,
      }}));
    """)
    assert out == {
        "interval": 4000,
        "pollMs": 4000,
        "afterFailure": {
            "calls": 2,
            "last": "first",
            "notice": "Sin conexión; reintento en 4 s",
        },
        "callsAfterStop": 2,
        "cleared": 1,
    }


def test_routes(env):
    assert env.client.get("/gui/api/offices").status_code == 401
    assert env.client.get("/gui/api/offices", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert env.client.get("/gui/api/offices/demo").status_code == 401
    assert env.client.get("/gui/api/offices/demo", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert env.client.get("/gui/api/offices/demo/scene").status_code == 401
    assert env.client.get("/gui/api/offices/demo/scene", headers={"Authorization": "Bearer wrong"}).status_code == 401

    listing = get(env, "/offices")
    assert listing.status_code == 200
    assert listing.json() == {"offices": [{"id": "demo", "name": "Demo", "state": "ok", "areas": 2}]}

    detail = get(env, "/offices/demo")
    assert detail.status_code == 200
    assert detail.json() == {**DETAIL, "requirements": REQUIREMENTS}
    scene = get(env, "/offices/demo/scene")
    assert scene.status_code == 200
    assert scene.json() == SCENE
    assert get(env, "/offices/missing").status_code == 404
    assert get(env, "/offices/missing/scene").status_code == 404


def test_no_offices_changes_nothing(tmp_path):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, dry_run=True)
    registry = services.offices._registry_path
    assert not registry.exists()
    token = ensure_token(settings.gui_token_file)
    try:
        with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
            response = client.get("/gui/api/offices", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"offices": []}
        assert not registry.exists()
    finally:
        services.runs.close()
