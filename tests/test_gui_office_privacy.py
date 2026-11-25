# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""VOF-T04: office privacy controls and the one-time level question."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.offices import PRIVACY_NOTE
from test_offices_install import ACTOR, make_settings, write_office

EXT = STATIC_DIR / "extensions" / "offices"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
BOOTSTRAP_CODE = "synthetic-privacy-approval-code"


def file_url(path: Path) -> str:
    return json.dumps(path.as_uri())


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


@pytest.fixture
def env(tmp_path):
    settings = make_settings(tmp_path / "local")
    services = ApplicationServices.create(settings, dry_run=True)
    root = write_office(
        tmp_path / "office",
        office_id="demo",
        areas=("reception", "proposal", "follow-up"),
        overrides={"proposal": {"sensitivity": "sensitive"}, "follow-up": {"local_only": True}},
    )
    services.offices.import_folder(root, actor=ACTOR)
    token = ensure_token(settings.gui_token_file)
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = BOOTSTRAP_CODE
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield SimpleNamespace(
            client=client,
            services=services,
            auth={"Authorization": f"Bearer {token}"},
        )
    services.bundle_scans.close()
    services.runs.close()


def get(env, path: str):
    return env.client.get("/gui/api" + path, headers=env.auth)


def post(env, path: str, body: dict):
    return env.client.post("/gui/api" + path, headers=env.auth, json=body)


def approval_decision(env, approval_id: str, decision: str = "approve"):
    session = post(env, "/approvals/session", {"code": BOOTSTRAP_CODE})
    assert session.status_code == 200, session.text
    prefix = f"/approvals/{approval_id}"
    challenge = post(env, prefix + "/challenge", {})
    assert challenge.status_code == 200, challenge.text
    return post(env, prefix + "/decision", {"code": challenge.json()["code"], "decision": decision})


def test_routes_and_privacy_payload(env):
    response = get(env, "/offices/demo/privacy")
    assert response.status_code == 200
    payload = response.json()
    assert payload["office"] == "demo"
    assert payload["cloud"]["allowed"] is False
    assert [(area["id"], area["sensitivity"], area["local_only"], area["data_class"])
            for area in payload["areas"]] == [
        ("reception", None, False, None),
        ("proposal", "sensitive", False, "sensitive"),
        ("follow-up", None, True, "restricted"),
    ]
    assert tuple(payload["note"]) == PRIVACY_NOTE
    assert get(env, "/offices/missing/privacy").status_code == 404
    assert env.client.get("/gui/api/offices/demo/privacy").status_code == 401


@needs_node
def test_rows_and_note(env):
    out = run_node(f"""
      const ui = await import({file_url(STATIC_DIR / "js" / "i18n.js")});
      await import({file_url(EXT / "i18n.js")});
      ui.setLanguage("es");
      const {{ NOTE_TITLE, privacyRows }} = await import({file_url(EXT / "privacy-model.js")});
      const payload = {{
        office: "demo",
        cloud: {{ allowed: true, source: "person" }},
        areas: [
          {{ id: "menu", sensitivity: "normal", local_only: false, data_class: "non_sensitive",
            set_by: "user:gui", set_at: "2026-11-24T12:00:00Z", source: "person",
            cloud: {{ allowed: false, source: "default" }}, cloud_effective: true }},
          {{ id: "proposal", sensitivity: "sensitive", local_only: false, data_class: "sensitive",
            set_by: "user:operator", set_at: null, source: "office",
            cloud: {{ allowed: false, source: "default" }}, cloud_effective: true }},
          {{ id: "reception", sensitivity: null, local_only: false, data_class: null,
            set_by: null, set_at: null, source: "office",
            cloud: {{ allowed: false, source: "default" }}, cloud_effective: false }},
        ],
        note: {json.dumps(list(PRIVACY_NOTE), ensure_ascii=False)},
      }};
      const rows = privacyRows(payload, [
        {{ id: "menu", name: "Menu" }},
        {{ id: "proposal", name: "Proposals" }},
        {{ id: "reception", name: "Reception" }},
      ]);
      console.log(JSON.stringify({{ title: NOTE_TITLE(), rows, note: payload.note }}));
    """)
    assert out["title"] == "Lo que esta opción no libera"
    assert [(row["id"], row["name"], row["level"], row["sourceText"]) for row in out["rows"]] == [
        ("menu", "Menu", "normal", "de esta área"),
        ("proposal", "Proposals", "sensitive", "de la oficina"),
        ("reception", "Reception", None, "de la oficina"),
    ]
    assert out["rows"][0]["cloudSourceText"] == "de la oficina"
    assert out["rows"][1]["cloudEffective"] is True
    assert out["note"] == list(PRIVACY_NOTE)


def test_note_has_no_destinations():
    source = ui_text((EXT / "privacy.js"))
    assert "Permitir enviar" not in source
    assert "destination" not in source.lower()
    assert 'class="offices-privacy-note"' in source and "payload.note" in source


def test_raise_without_code(env):
    raised = post(env, "/offices/demo/privacy/raise", {
        "area": "reception", "sensitivity": "sensitive", "local_only": False,
    })
    assert raised.status_code == 200, raised.text
    area = next(item for item in raised.json()["areas"] if item["id"] == "reception")
    assert (area["sensitivity"], area["data_class"], area["source"], area["set_by"]) == (
        "sensitive", "sensitive", "person", "user:gui",
    )
    assert env.services.user_approvals.pending() == []


def test_lowering_needs_the_one_use_code(env):
    requested = post(env, "/offices/demo/privacy/lower-request", {
        "area": "proposal", "sensitivity": "normal", "local_only": False,
    })
    assert requested.status_code == 200
    approval = requested.json()
    item = env.services.user_approvals.peek(approval["approval_id"])
    assert item["kind"] == "office_privacy_change" and item["state"] == "pending"
    assert len(approval["sha256"]) == 64

    before_code = post(env, "/offices/demo/privacy/lower", {
        "area": "proposal", "approval_id": approval["approval_id"],
    })
    assert before_code.status_code == 409
    assert before_code.json()["detail"]["details"]["reason"] == "office_privacy_change_not_approved"

    decided = approval_decision(env, approval["approval_id"])
    assert decided.status_code == 200, decided.text
    lowered = post(env, "/offices/demo/privacy/lower", {
        "area": "proposal", "approval_id": approval["approval_id"],
    })
    assert lowered.status_code == 200, lowered.text
    area = next(item for item in lowered.json()["areas"] if item["id"] == "proposal")
    assert (area["sensitivity"], area["data_class"], area["source"]) == ("normal", "non_sensitive", "person")
    events = [event for event in env.services.tasks.events() if event["type"] == "office.privacy_changed"]
    assert len(events) == 1
    assert events[0]["payload"]["direction"] == "lower"

    spent = post(env, "/offices/demo/privacy/lower", {
        "area": "proposal", "approval_id": approval["approval_id"],
    })
    assert spent.status_code == 409
    assert spent.json()["detail"]["details"]["reason"] == "office_privacy_change_not_approved"


def test_cancel_lowering_keeps_the_level(env):
    requested = post(env, "/offices/demo/privacy/lower-request", {
        "area": "proposal", "sensitivity": "normal", "local_only": False,
    })
    assert requested.status_code == 200
    approval_id = requested.json()["approval_id"]
    assert approval_decision(env, approval_id, "reject").status_code == 200
    rejected = post(env, "/offices/demo/privacy/lower", {
        "area": "proposal", "approval_id": approval_id,
    })
    assert rejected.status_code == 409
    current = get(env, "/offices/demo/privacy").json()
    area = next(item for item in current["areas"] if item["id"] == "proposal")
    assert area["sensitivity"] == "sensitive"
    assert [event for event in env.services.tasks.events() if event["type"] == "office.privacy_changed"] == []


@needs_node
def test_is_lowering():
    out = run_node(f"""
      import {{ isLowering }} from {file_url(EXT / "privacy-model.js")};
      console.log(JSON.stringify([
        isLowering("sensitive", "normal"),
        isLowering(null, "normal"),
        isLowering("normal", "local_only"),
        isLowering("local_only", "sensitive"),
      ]));
    """)
    assert out == [True, False, False, True]


@needs_node
def test_question_sets_the_level_and_retries_once():
    out = run_node(f"""
      import {{ askLevelOnce }} from {file_url(JS / "office-level-question.js")};
      const posts = [];
      let requests = 0;
      let prompts = 0;
      let retries = 0;
      const request = async () => {{
        requests += 1;
        if (requests === 1) throw {{ status: 409, details: {{ reason: "office_level_required" }} }};
        return {{ status: 200, body: "retried" }};
      }};
      let result;
      try {{ await request(); }} catch (failure) {{
        if (failure.details.reason === "office_level_required") {{
          result = await askLevelOnce({{
            token: "synthetic-token",
            officeId: "demo",
            area: "reception",
            post: async (...args) => {{ posts.push(args); return {{ changed: true }}; }},
            retry: async (question) => {{
              if (question) {{ prompts += 1; return "normal"; }}
              retries += 1;
              return request();
            }},
          }});
        }}
      }}
      let failedCalls = 0;
      let repeatedError = "";
      try {{
        await askLevelOnce({{
          token: "synthetic-token",
          officeId: "demo",
          area: "reception",
          post: async () => ({{}}),
          retry: async (question) => {{
            failedCalls += 1;
            if (question) return "sensitive";
            throw new Error("repeat failed");
          }},
        }});
      }} catch (error) {{ repeatedError = error.message; }}
      let stale = false;
      let stalePosts = 0;
      let staleRetries = 0;
      const staleResult = await askLevelOnce({{
        token: "synthetic-token",
        officeId: "demo",
        area: "reception",
        isStale: () => stale,
        post: async () => {{ stalePosts += 1; stale = true; }},
        retry: async (question) => {{
          if (question) return "sensitive";
          staleRetries += 1;
          return {{ status: 200 }};
        }},
      }});
      console.log(JSON.stringify({{
        posts, prompts, retries, requests, result, failedCalls, repeatedError,
        staleResult, stalePosts, staleRetries,
      }}));
    """)
    assert out["posts"] == [[
        "/gui/api/offices/demo/privacy/raise", "synthetic-token",
        {"area": "reception", "sensitivity": "normal", "local_only": False},
    ]]
    assert (out["prompts"], out["retries"], out["requests"], out["result"]) == (
        1, 1, 2, {"status": 200, "body": "retried"},
    )
    assert (out["failedCalls"], out["repeatedError"]) == (2, "repeat failed")
    assert (out["staleResult"], out["stalePosts"], out["staleRetries"]) == (None, 1, 0)


@needs_node
def test_question_lines():
    out = run_node(f"""
      import {{ QUESTION_LINES }} from {file_url(JS / "office-level-question.js")};
      console.log(JSON.stringify(QUESTION_LINES));
    """)
    assert out == {
        "normal": "Normal: puede usar modelos de la nube si lo permites en «Privacidad»",
        "sensitive": "Sensible: solo agentes de tu equipo",
        "local_only": "Solo local: nunca sale de tu equipo",
    }


def test_no_question_in_advanced_mode():
    source = ui_text((JS / "new-session.js"))
    assert 'currentMode() === "simple" && officeId' in source
    assert 'routeHref("offices", officeLevelNotice.officeId)' in source
    assert "await askLevelOnce({" in source
    assert "if (stale()) return;" in source


def test_question_blocks_new_submission():
    source = ui_text((JS / "new-session.js"))
    assert "pendingSecret || officeLevelQuestion || (!asking && !trimmed)" in source
    assert "disabled=${busy || pendingCloud || pendingSecret || officeLevelQuestion" in source


def test_cloud_permission_needs_the_code(env):
    requested = post(env, "/offices/demo/privacy/cloud-request", {"area": "reception", "allowed": True})
    assert requested.status_code == 200
    approval = requested.json()
    item = env.services.user_approvals.peek(approval["approval_id"])
    assert item["kind"] == "office_privacy_change" and item["state"] == "pending"

    before_code = post(env, "/offices/demo/privacy/cloud", {
        "area": "reception", "approval_id": approval["approval_id"],
    })
    assert before_code.status_code == 409
    assert before_code.json()["detail"]["details"]["reason"] == "office_privacy_change_not_approved"

    assert approval_decision(env, approval["approval_id"]).status_code == 200
    granted = post(env, "/offices/demo/privacy/cloud", {
        "area": "reception", "approval_id": approval["approval_id"],
    })
    assert granted.status_code == 200, granted.text
    area = next(item for item in granted.json()["areas"] if item["id"] == "reception")
    assert area["cloud_effective"] is True and area["cloud"]["allowed"] is True
    spent = post(env, "/offices/demo/privacy/cloud", {
        "area": "reception", "approval_id": approval["approval_id"],
    })
    assert spent.status_code == 409
    assert spent.json()["detail"]["details"]["reason"] == "office_privacy_change_not_approved"


def test_cloud_cancel_does_not_change_permission(env):
    requested = post(env, "/offices/demo/privacy/cloud-request", {"area": "reception", "allowed": True})
    assert requested.status_code == 200
    approval_id = requested.json()["approval_id"]
    assert approval_decision(env, approval_id, "reject").status_code == 200
    grant = post(env, "/offices/demo/privacy/cloud", {"area": "reception", "approval_id": approval_id})
    assert grant.status_code == 409
    current = get(env, "/offices/demo/privacy").json()
    area = next(item for item in current["areas"] if item["id"] == "reception")
    assert area["cloud_effective"] is False


def test_cloud_off_is_immediate(env):
    requested = post(env, "/offices/demo/privacy/cloud-request", {"area": None, "allowed": True})
    assert requested.status_code == 200
    approval_id = requested.json()["approval_id"]
    assert approval_decision(env, approval_id).status_code == 200
    granted = post(env, "/offices/demo/privacy/cloud", {"area": None, "approval_id": approval_id})
    assert granted.status_code == 200
    revoked = post(env, "/offices/demo/privacy/raise", {"area": None, "cloud_allowed": False})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["cloud"]["allowed"] is False


@needs_node
def test_sensitive_area_says_never_cloud():
    out = run_node(f"""
      import {{ privacyRows }} from {file_url(EXT / "privacy-model.js")};
      const [row] = privacyRows({{
        cloud: {{ allowed: true }},
        areas: [{{ id: "proposal", sensitivity: "sensitive", data_class: "sensitive",
          cloud: {{ source: "default" }}, cloud_effective: true }}],
      }});
      console.log(JSON.stringify(row));
    """)
    source = ui_text((EXT / "privacy.js"))
    assert out["dataClass"] == "sensitive" and out["cloudEffective"] is True
    assert "Esta área nunca usa la nube, aunque se permita." in source
    assert 'row.dataClass === "sensitive" || row.dataClass === "restricted"' in source


def test_no_lowering_route_in_api_v1(env):
    paths = {route.path for route in env.client.app.routes}
    assert "/api/v1/offices/{office_id}/privacy/lower" not in paths


def test_privacy_panel_is_shrinkable():
    source = (EXT / "style.css").read_text(encoding="utf-8")
    panel = source.split(".offices-privacy {", 1)[1].split("}", 1)[0]
    actions = source.split(".offices-privacy-confirm {", 1)[1].split("}", 1)[0]
    select = source.split(".offices-privacy-field select {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in panel
    assert "flex-wrap: wrap" in actions
    assert "width: 100%" in select and "min-width: 0" in select
