# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""VOF-T02: office import cards, GUI routes and browser-side export actions."""
from __future__ import annotations

import base64
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
from multiagent.gui.offices_api import BODY_MAX_BYTES
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.installed import ZIP_UPLOAD_MAX_BYTES
from multiagent.services.offices import OFFICE_NOTICE
from test_offices_install import ACTOR, make_settings, make_zip, write_office

EXT = STATIC_DIR / "extensions" / "offices"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
BASE = "/gui/api/offices"
BOOTSTRAP_CODE = "synthetic-office-approval-code"


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


@pytest.fixture
def gui(tmp_path):
    settings = make_settings(tmp_path / "local")
    interpreter = tmp_path / "skillspector" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"synthetic interpreter")
    settings = settings.model_copy(update={"skillspector_python": str(interpreter)})
    services = ApplicationServices.create(settings, dry_run=True)
    services.bundle_scans.verifier = lambda _path: True
    services.bundle_scans.env = {}

    def runner(_argv, **_kwargs):
        report = {"verdict": "SAFE", "skills": [{"path": ".", "coverage_percent": 100}], "findings": []}
        return SimpleNamespace(exit_code=0, stdout_tail=json.dumps(report), stdout_truncated=False, timed_out=False)

    services.bundle_scans.runner = runner
    token = ensure_token(settings.gui_token_file)
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = BOOTSTRAP_CODE
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield SimpleNamespace(client=client, services=services, token=token,
                              auth={"Authorization": f"Bearer {token}"}, session_started=False)
    services.bundle_scans.close()
    services.runs.close()


def post(gui, path: str, body: dict | None = None, **kwargs):
    return gui.client.post(BASE + path, headers=gui.auth, json={} if body is None else body, **kwargs)


def start_approval_session(gui):
    if gui.session_started:
        return
    response = gui.client.post("/gui/api/approvals/session", headers=gui.auth, json={"code": BOOTSTRAP_CODE})
    assert response.status_code == 200, response.text
    gui.session_started = True


def decide(gui, approval_id: str, *, decision: str = "approve", risk_ack: bool = False):
    start_approval_session(gui)
    prefix = f"/gui/api/approvals/{approval_id}"
    challenge = gui.client.post(prefix + "/challenge", headers=gui.auth, json={})
    assert challenge.status_code == 200, challenge.text
    body = {"code": challenge.json()["code"], "decision": decision}
    if risk_ack:
        body["risk_ack"] = True
    return gui.client.post(prefix + "/decision", headers=gui.auth, json=body)


def zip_body(folder: Path, **extra) -> dict:
    return {"filename": "office.zip", "zip_base64": base64.b64encode(make_zip(folder)).decode("ascii"), **extra}


@needs_node
def test_card_shows_everything():
    out = run_node(f"""
      import {{ cardView, CHANGE_TEXT, TEAMS_NOTE }} from {file_url(EXT / "import-model.js")};
      const card = {{
        office: {{ id: "demo", name: "Demo office" }},
        notice: {json.dumps(OFFICE_NOTICE, ensure_ascii=False)},
        level: "data",
        areas: [
          {{ id: "menu", name: "Menu", change: "added", sensitivity: "normal",
            members: [{{ id: "agent", name: "Menu agent", kind: "agent", role: "preparer" }}],
            requires: {{ text: "Lo puedes cubrir con Agente local" }},
            declares: {{ workflows: ["quote"], contracts: ["MenuOutput"], roles: ["cook"] }} }},
          {{ id: "notices", name: "Notices", change: "changed", sensitivity: "sensitive",
            members: [{{ id: "person", name: "Reviewer", kind: "person" }}],
            requires: {{ text: "Te falta un agente que pueda escribir texto" }},
            declares: {{ workflows: ["reply"], contracts: [], roles: [] }} }},
        ],
        code: {{ present: false, files: [], functions: [] }},
        scan: {{ state: "safe", areas: {{ menu: {{ verdict: "safe" }} }} }},
        risk_ack_required: false,
      }};
      const view = cardView(card);
      console.log(JSON.stringify({{ view, CHANGE_TEXT, TEAMS_NOTE }}));
    """)
    view = out["view"]
    assert view["title"] == "Demo office"
    assert view["notice"] == OFFICE_NOTICE
    assert out["TEAMS_NOTE"] == "Al instalar se aprueban los equipos de estas áreas"
    assert [(area["name"], area["change"], area["level"]) for area in view["areas"]] == [
        ("Menu", "Nueva", "Normal"), ("Notices", "Cambió", "Sensible")]
    assert view["areas"][0]["members"] == [{"name": "Menu agent", "role": "preparer", "kind": "agent"}]
    assert view["areas"][1]["members"][0]["name"] == "Reviewer"
    assert view["areas"][0]["requirement"] == "Lo puedes cubrir con Agente local"
    assert view["areas"][1]["requirement"] == "Te falta un agente que pueda escribir texto"
    assert view["areas"][0]["declares"] == {
        "workflows": ["quote"], "contracts": ["MenuOutput"], "roles": ["cook"]}
    assert view["code"]["verdict"] == "Sin alertas conocidas" and view["riskAckRequired"] is False
    assert out["CHANGE_TEXT"] == {"added": "Nueva", "changed": "Cambió", "removed": "Se quita", "unchanged": "Sin cambios"}


@needs_node
def test_card_uses_plain_levels_and_declaration_names():
    out = run_node(f"""
      import {{ cardView, declarationName }} from {file_url(EXT / "import-model.js")};
      const card = cardView({{
        office: {{ id: "private-office-id" }},
        areas: [
          {{ id: "restricted-id", name: "Local", data_class: "restricted", members: [{{ id: "private-member-id" }}] }},
          {{ id: "sensitive-id", name: "Sensible", data_class: "sensitive" }},
          {{ id: "normal-id", name: "Normal", data_class: "non_sensitive" }},
        ],
        scan: {{ state: "safe", areas: {{ "restricted-id": {{ verdict: "safe" }} }} }},
      }});
      console.log(JSON.stringify({{
        title: card.title,
        areas: card.areas.map((area) => ({{ name: area.name, level: area.level, member: area.members[0]?.name ?? null }})),
        scan: card.code.verdict,
        scanArea: card.code.areas[0].name,
        names: ["cotizar", "extract_requirements", "MenuOutput"].map(declarationName),
      }}));
    """)
    assert out == {
        "title": "Oficina",
        "areas": [
            {"name": "Local", "level": "Solo local", "member": "Miembro"},
            {"name": "Sensible", "level": "Sensible", "member": None},
            {"name": "Normal", "level": "Normal", "member": None},
        ],
        "scan": "Sin alertas conocidas",
        "scanArea": "Local",
        "names": ["Cotizar", "Extract requirements", "Menu Output"],
    }


def test_install_in_one_click_with_two_decisions_at_most(gui, tmp_path):
    folder = write_office(tmp_path / "three", areas=("one", "two", "three"))
    staged = post(gui, "/stage-zip", zip_body(folder))
    assert staged.status_code == 200, staged.text
    card = staged.json()
    approvals = list(gui.services.user_approvals._all().values())
    assert sorted(item["kind"] for item in approvals) == ["office_install", "osv_query"]

    scan_id = card["scan"]["approval_id"]
    assert decide(gui, scan_id).status_code == 200
    running = gui.client.get(BASE + "/stage/" + card["stage_id"], headers=gui.auth)
    assert running.status_code == 200
    gui.services.bundle_scans.close()
    ready = gui.client.get(BASE + "/stage/" + card["stage_id"], headers=gui.auth)
    assert ready.status_code == 200 and ready.json()["scan"]["state"] == "safe"

    # staged while the scan was still pending: the service asks for «Asumo los riesgos» (the same as
    # tests/test_office_install_card.py), and that box belongs to the install decision, not a third one
    assert ready.json()["risk_ack_required"] is True
    assert decide(gui, card["approval_id"], risk_ack=True).status_code == 200
    installed = post(gui, f"/stage/{card['stage_id']}/install", {"approval_id": card["approval_id"]})
    assert installed.status_code == 200, installed.text
    assert len([item for item in gui.services.user_approvals._all().values()
                if item["kind"] in {"osv_query", "office_install"}]) == 2
    for area in ("one", "two", "three"):
        team = gui.services.teams.approved(f"demo__{area}")
        assert team is not None and team["status"] == "approved"
        assert team["note"].startswith("approved when installing office demo ")


def test_install_without_decision_is_refused(gui, tmp_path):
    staged = post(gui, "/stage-zip", zip_body(write_office(tmp_path / "demo")))
    assert staged.status_code == 200, staged.text
    card = staged.json()
    assert decide(gui, card["scan"]["approval_id"]).status_code == 200
    pending = gui.client.get(BASE + "/stage/" + card["stage_id"], headers=gui.auth)
    assert pending.status_code == 200
    gui.services.bundle_scans.close()
    ready = gui.client.get(BASE + "/stage/" + card["stage_id"], headers=gui.auth)
    assert ready.status_code == 200 and ready.json()["scan"]["state"] == "safe"
    response = post(gui, f"/stage/{card['stage_id']}/install", {"approval_id": card["approval_id"]})
    assert response.status_code == 409
    assert response.json()["detail"]["details"]["reason"] == "office_install_not_approved"


def test_risk_ack_only_when_asked(gui, tmp_path):
    data_only = post(gui, "/stage-zip", zip_body(write_office(tmp_path / "plain")))
    assert data_only.status_code == 200
    # the service asks for the box while the scan of a data office is pending; the GUI only follows it
    assert data_only.json()["risk_ack_required"] is True
    assert decide(gui, data_only.json()["approval_id"]).json()["detail"]["code"] == "risk_ack_required"

    gui.services.gui_settings.set_mode("advanced")
    code_folder = write_office(tmp_path / "code")
    (code_folder / "areas" / "reception" / "functions.py").write_text(
        "def synthetic(context, arguments):\n    return {}\n", encoding="utf-8")
    staged = post(gui, "/stage-folder", {"folder": str(code_folder)})
    assert staged.status_code == 200, staged.text
    card = staged.json()
    assert card["risk_ack_required"] is True
    response = decide(gui, card["approval_id"])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "risk_ack_required"
    view = run_node(f"""
      import {{ cardView }} from {file_url(EXT / "import-model.js")};
      console.log(JSON.stringify([false, true].map((required) =>
        cardView({{ risk_ack_required: required }}).riskAckRequired)));
    """)
    source = ui_text((EXT / "import.js"))
    assert view == [False, True]
    assert 'view.riskAckRequired && html`<label class="check-inline offices-risk-ack">' in source


def test_reimport_lists_only_changes(gui, tmp_path):
    original = write_office(tmp_path / "original")
    gui.services.offices.import_folder(original, actor=ACTOR)
    changed = write_office(tmp_path / "changed", markers={"reception": "updated"})
    staged = post(gui, "/stage-zip", zip_body(changed, reimport="demo"))
    assert staged.status_code == 200, staged.text
    assert [(area["id"], area["change"]) for area in staged.json()["areas"]] == [("reception", "changed")]


def test_cancel_discards_and_rejects_the_card(gui, tmp_path):
    staged = post(gui, "/stage-zip", zip_body(write_office(tmp_path / "demo")))
    assert staged.status_code == 200
    card = staged.json()
    response = post(gui, f"/stage/{card['stage_id']}/discard")
    assert response.status_code == 200 and response.json() == {"discarded": True}
    assert gui.services.user_approvals.status(card["approval_id"])["state"] == "rejected"
    assert not gui.services.offices._stage_folder(card["stage_id"]).exists()
    assert gui.services.offices.list() == []


def test_export_routes_and_remove_keep_data(gui, tmp_path):
    gui.services.offices.import_folder(write_office(tmp_path / "installed"), actor=ACTOR)
    exported = post(gui, "/demo/export")
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload["filename"] == "demo.zip"
    assert base64.b64decode(payload["zip_base64"], validate=True)
    assert isinstance(payload["manifest"], dict) and isinstance(payload["warnings"], list)

    area_export = post(gui, "/demo/export", {"area": "reception"})
    assert area_export.status_code == 200 and area_export.json()["filename"] == "demo-reception.zip"
    area_removed = post(gui, "/demo/areas/proposal/remove")
    assert area_removed.status_code == 200 and area_removed.json()["kept_data"] is True
    assert (gui.services.settings.data_dir / "installed" / ".offices" / "demo" / "v1" / "office.yaml").is_file()

    removed = post(gui, "/demo/remove")
    assert removed.status_code == 200 and removed.json()["kept_data"] is True
    assert (gui.services.settings.data_dir / "installed" / ".offices" / "demo" / "v1" / "office.yaml").is_file()


@needs_node
def test_remove_confirmation_shows_data_retention_notice():
    out = run_node(f"""
      import {{ REMOVE_NOTICE }} from {file_url(EXT / "import-model.js")};
      console.log(JSON.stringify(REMOVE_NOTICE));
    """)
    source = ui_text((EXT / "index.js"))
    assert out == "Se quita de la lista. Se conservan sus datos"
    assert '<p class="small">${REMOVE_NOTICE}</p>' in source
    assert 'result?.kept_data !== true' in source


def test_upload_limit_is_413_before_body_read(gui):
    headers = {**gui.auth, "Content-Type": "application/json", "Content-Length": str(BODY_MAX_BYTES + 1)}
    response = gui.client.post(BASE + "/stage-zip", content=b"{}", headers=headers)
    assert response.status_code == 413
    assert response.json()["detail"]["details"]["reason"] == "zip_too_large"
    oversized_zip = base64.b64encode(b"\0" * (ZIP_UPLOAD_MAX_BYTES + 1)).decode("ascii")
    decoded = post(gui, "/stage-zip", {"filename": "large.zip", "zip_base64": oversized_zip})
    assert decoded.status_code == 413
    assert decoded.json()["detail"]["details"]["reason"] == "zip_too_large"
    assert ZIP_UPLOAD_MAX_BYTES == 25 * 2**20


def test_stage_folder_needs_advanced_mode(gui, tmp_path):
    folder = write_office(tmp_path / "folder")
    simple = post(gui, "/stage-folder", {"folder": str(folder)})
    assert simple.status_code == 409
    assert simple.json()["detail"]["details"]["reason"] == "advanced_mode_required"
    gui.services.gui_settings.set_mode("advanced")
    advanced = post(gui, "/stage-folder", {"folder": str(folder)})
    assert advanced.status_code == 200, advanced.text


@needs_node
def test_export_downloads_without_network():
    out = run_node(f"""
      import assert from "node:assert/strict";
      import {{ exportOffice }} from {file_url(EXT / "import.js")};
      const calls = [];
      let clicked = false;
      let removed = false;
      let downloaded = null;
      globalThis.document = {{
        body: {{ appendChild(link) {{ downloaded = link; }} }},
        createElement(tag) {{
          assert.equal(tag, "a");
          return {{ click() {{ clicked = true; }}, remove() {{ removed = true; }} }};
        }},
      }};
      globalThis.URL.createObjectURL = (blob) => {{
        assert.equal(blob.type, "application/zip");
        return "blob:local-office";
      }};
      globalThis.URL.revokeObjectURL = (url) => assert.equal(url, "blob:local-office");
      const payload = await exportOffice({{
        token: "local", officeId: "demo", area: "menu", includeCode: true,
        post: async (...args) => {{
          calls.push(args);
          return {{ filename: "demo-menu.zip", zip_base64: "UEsDBA==", warnings: [] }};
        }},
      }});
      console.log(JSON.stringify({{
        calls, filename: downloaded.download, href: downloaded.href, clicked, removed,
        result: payload.filename, body: calls[0][2],
      }}));
    """)
    assert len(out["calls"]) == 1
    assert out["filename"] == out["result"] == "demo-menu.zip"
    assert out["href"] == "blob:local-office" and out["clicked"] and out["removed"]
    assert out["body"] == {"area": "menu", "include_code": True}


@needs_node
def test_include_code_only_in_advanced_mode():
    out = run_node(f"""
      import {{ includeCodeVisible }} from {file_url(EXT / "import-model.js")};
      console.log(JSON.stringify(["simple", "advanced"].map(includeCodeVisible)));
    """)
    assert out == [False, True]
    source = ui_text((EXT / "index.js"))
    assert 'showIncludeCode && html`<label class="check-inline offices-include-code">' in source
    assert "Incluir código" in source


@needs_node
def test_secret_found_never_shows_the_value():
    out = run_node(f"""
      import {{ errorText }} from {file_url(EXT / "import-model.js")};
      const secret = "SYNTHETIC_SECRET_MUST_NOT_APPEAR";
      const text = errorText("export_secret_found", {{
        secret, found: [{{ path: "areas/menu/settings.py", line: 7, value: secret }}],
      }});
      console.log(JSON.stringify({{ text, leaks: text.includes(secret) }}));
    """)
    assert out == {
        "text": "Hay algo que parece una clave en areas/menu/settings.py, línea 7. Quítalo y vuelve a exportar.",
        "leaks": False,
    }


@needs_node
def test_error_texts():
    out = run_node(f"""
      import {{ errorText }} from {file_url(EXT / "import-model.js")};
      const reasons = ["office_has_code", "office_stage_expired", "office_changed_since_card",
        "zip_too_large", "bundle_busy", "office_area_exists"];
      console.log(JSON.stringify({{
        texts: reasons.map((reason) => errorText(reason)),
        code: errorText("advanced_mode_required", {{ action: "install_code" }}),
      }}));
    """)
    assert out == {
        "texts": [
            "Esta oficina trae código: impórtala en modo avanzado.",
            "La ficha caducó: vuelve a elegir el archivo.",
            "La oficina cambió desde que la viste: vuelve a elegir el archivo.",
            "El archivo pasa de 25 MB.",
            "Espera a que terminen sus trabajos.",
            "Esta oficina ya tiene un área con ese nombre.",
        ],
        "code": "Esta oficina trae código: impórtala en modo avanzado.",
    }
