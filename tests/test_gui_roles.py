# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ROL-T02: step roles in the GUI run detail, step attempts, contextual help, and Markdown report.

Checks the GUI routes used by the run detail and attempts view under the GUI token, static safety and help wiring
of ``run-detail.js`` and ``help-text.js``, Node execution of ``Steps`` and ``AttemptDetail`` with both real router
payloads and hostile HTML-like strings, and Markdown report rendering with and without roles.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from test_gui_help import spanish_help
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services.reports import build_run_report, render_run_report_markdown
from multiagent.teams.step_roles import ROLE_FIELDS

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _make_bundle(tmp_path: Path, name: str, *, with_roles: bool) -> Path:
    dest = tmp_path / name
    shutil.copytree(GENERAL_BUNDLE, dest)
    wf_path = dest / "workflows" / "summarize_document.yaml"
    wf = yaml.safe_load(wf_path.read_text(encoding="utf-8"))
    roles = {"extract_key_points": "analyst", "write_summary": "writer"}
    for step in wf["steps"]:
        step["checkpoint_after"] = False
        if with_roles and step["id"] in roles:
            step["role"] = roles[step["id"]]
    wf_path.write_text(yaml.safe_dump(wf, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest


@pytest.fixture
def env(tmp_path):
    with_roles = _make_bundle(tmp_path, "with_roles", with_roles=True)
    without_roles = _make_bundle(tmp_path, "without_roles", with_roles=False)
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
    services = ApplicationServices.create(settings, bundles={"with_roles": with_roles, "without_roles": without_roles})
    proposed = services.teams.propose("with_roles", actor="user:ana", channel="python")
    approved = services.teams.approve(proposed["id"], actor="user:ana", channel="python")
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token, approved=approved)
    services.runs.close()


def _create_and_drain(env, bundle: str) -> str:
    resp = env.client.post(
        "/gui/api/runs",
        headers={"Authorization": f"Bearer {env.token}"},
        json={
            "workflow_id": "summarize_document",
            "bundle": bundle,
            "project_name": "Roles GUI",
            "inputs": {"document_text": "La reunión semanal pasa de los lunes a los miércoles."},
        },
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["id"]
    worker = env.services.runs._bundle_runtime(bundle).worker
    for _ in range(10):
        if not worker.process_once():
            break
    return run_id


# --- GUI router and services --------------------------------------------------------------------------------------

def test_gui_run_detail_and_attempts_routes_require_token_and_expose_roles_only_when_present(env):
    role_run = _create_and_drain(env, "with_roles")
    plain_run = _create_and_drain(env, "without_roles")

    for path in (f"/gui/api/runs/{role_run}", f"/gui/api/runs/{role_run}/steps/write_summary/attempts"):
        assert env.client.get(path).status_code == 401, path
        assert env.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401, path

    headers = {"Authorization": f"Bearer {env.token}"}
    detail = env.client.get(f"/gui/api/runs/{role_run}", headers=headers).json()
    started = [e["payload"] for e in detail["events"] if e["type"] == "step_started"]
    assert [(p["step_id"], p["role"], p["member"], p["team_id"], p["team_version"]) for p in started] == [
        ("extract_key_points", "analyst", "doc_analyst_agent", "general_team", 1),
        ("write_summary", "writer", "writer_agent", "general_team", 1),
    ]
    attempts = env.client.get(f"/gui/api/runs/{role_run}/steps/write_summary/attempts", headers=headers).json()["attempts"]
    assert len(attempts) == 1
    assert attempts[0]["role"] == "writer" and attempts[0]["member"] == "writer_agent"
    assert attempts[0]["team_id"] == "general_team" and attempts[0]["team_version"] == 1
    assert attempts[0]["team_version_id"] == env.approved["id"]

    plain_detail = env.client.get(f"/gui/api/runs/{plain_run}", headers=headers).json()
    plain_started = [e["payload"] for e in plain_detail["events"] if e["type"] == "step_started"]
    assert plain_started and not any(set(ROLE_FIELDS) & set(p) for p in plain_started)
    plain_attempts = env.client.get(f"/gui/api/runs/{plain_run}/steps/write_summary/attempts", headers=headers).json()["attempts"]
    assert plain_attempts and not (set(ROLE_FIELDS) & set(plain_attempts[0]))


# --- static checks ------------------------------------------------------------------------------------------------

def test_run_detail_and_help_follow_escaping_conditional_rendering_and_help_rules():
    detail = ui_text((JS / "run-detail.js"))
    help_text = ui_text((JS / "help-text.js"))

    for bad in ("innerHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style=", "document.write", "fetch("):
        assert bad not in detail, bad
    assert "stepRolesByStep" in detail and "AttemptDetail" in detail
    assert 'typeof payload.role !== "string"' in detail
    assert 'typeof detail.role === "string"' in detail
    assert "${hasRoles &&" in detail and "${roleInfo &&" in detail and "${hasRole &&" in detail
    assert 'topic="run.role"' in detail

    match = re.search(r"^export const HELP = (\{.*?^\});$", help_text, re.S | re.M)
    assert match
    entries = json.loads(match[1])
    assert "run.role" in entries
    entry = entries["run.role"]
    assert set(entry) == {"title", "hint", "more"}
    # the fragment holds the English base; the Spanish of the topic is in the es dictionary of the runs view
    entry = spanish_help()["run.role"]
    assert "equipo aprobado" in entry["hint"].lower()
    assert 1 <= len(entry["more"]) <= 2 or len(entry["more"]) == 2
    assert "aprobada" in " ".join(entry["more"]).lower()


@needs_node
def test_touched_scripts_pass_node_check():
    for name in ("run-detail.js", "help-text.js", "route.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)


# --- Node rendering checks ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("roles-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def _run_node(root: Path, body: str) -> dict:
    script = root / "roles-view.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


WALKER = r"""
const kids = (v) => (v == null || typeof v === "boolean" ? [] : Array.isArray(v) ? v.flatMap(kids) : typeof v === "object" ? [v] : []);
const expand = (v) => {
  if (v == null || typeof v === "boolean" || typeof v === "string" || typeof v === "number") return v;
  if (Array.isArray(v)) return v.map(expand);
  if (v.type?.name === "Help") return { type: "Help", props: { topic: v.props?.topic, compact: Boolean(v.props?.compact), scope: v.props?.scope || "" }, children: null };
  if (typeof v.type === "function") return expand(v.type(v.props || {}));
  return { type: v.type, props: { ...v.props, children: expand(v.props?.children) } };
};
const all = (v) => kids(v).flatMap((n) => [n, ...all(n.props?.children)]);
const text = (v) => v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("")
  : typeof v === "object" ? text(v.props?.children) : String(v);
"""


@needs_node
def test_steps_and_attempts_render_roles_safely_and_omit_them_without_role(node_root, env):
    role_run = _create_and_drain(env, "with_roles")
    plain_run = _create_and_drain(env, "without_roles")
    headers = {"Authorization": f"Bearer {env.token}"}

    role_view = env.client.get(f"/gui/api/runs/{role_run}", headers=headers).json()
    role_attempt = env.client.get(f"/gui/api/runs/{role_run}/steps/write_summary/attempts", headers=headers).json()["attempts"][0]
    plain_view = env.client.get(f"/gui/api/runs/{plain_run}", headers=headers).json()
    plain_attempt = env.client.get(f"/gui/api/runs/{plain_run}/steps/write_summary/attempts", headers=headers).json()["attempts"][0]

    payload = json.dumps({
        "roleView": role_view,
        "roleAttempt": role_attempt,
        "plainView": plain_view,
        "plainAttempt": plain_attempt,
        "hostileRole": '<img src=x onerror=alert(1)>',
        "hostileMember": '<script>alert("xss")</script>',
    })

    out = _run_node(node_root, WALKER + f"""
      import {{ Steps, AttemptDetail }} from "./js/run-detail.js";
      import {{ parseRoute }} from "./js/route.js";
      const data = {payload};
      const artifact = {{ step_id: "write_summary", attempt: 1, approved: true, kind: "Summary", data: {{ ok: true }} }};

      const withRoleSteps = expand(Steps({{ view: data.roleView }}));
      const withoutRoleSteps = expand(Steps({{ view: data.plainView }}));
      const withRoleAttempt = expand(AttemptDetail({{ artifact, detail: data.roleAttempt, isOpen: true, scopePrefix: "r1" }}));
      const withoutRoleAttempt = expand(AttemptDetail({{ artifact, detail: data.plainAttempt, isOpen: true, scopePrefix: "r2" }}));

      const hostileView = {{
        ...data.plainView,
        events: [{{ seq: 1, ts: "2026-11-18", type: "step_started", payload: {{
          step_id: "extract_key_points", role: data.hostileRole, member: data.hostileMember,
          team_id: "<b>team</b>", team_version: 3, team_version_id: "tmv-111111111111"
        }} }}],
      }};
      const hostileSteps = expand(Steps({{ view: hostileView }}));
      const hostileAttempt = expand(AttemptDetail({{
        artifact,
        detail: {{ ...data.plainAttempt, role: data.hostileRole, member: data.hostileMember, team_id: "<b>team</b>", team_version: 3 }},
        isOpen: true,
      }}));

      console.log(JSON.stringify({{
        route: parseRoute("#runs/" + data.roleView.id),
        withRoleStepsText: text(withRoleSteps),
        withRoleStepsHelps: all(withRoleSteps).filter((n) => n.type === "Help").map((n) => n.props.topic),
        withRoleStepsTitles: all(withRoleSteps).map((n) => n.props?.title).filter(Boolean),
        withoutRoleStepsText: text(withoutRoleSteps),
        withoutRoleStepsHelps: all(withoutRoleSteps).filter((n) => n.type === "Help").map((n) => n.props.topic),
        withRoleAttemptText: text(withRoleAttempt),
        withRoleAttemptHelps: all(withRoleAttempt).filter((n) => n.type === "Help").map((n) => n.props),
        withRoleAttemptTitles: all(withRoleAttempt).map((n) => n.props?.title).filter(Boolean),
        withoutRoleAttemptText: text(withoutRoleAttempt),
        withoutRoleAttemptHelps: all(withoutRoleAttempt).filter((n) => n.type === "Help").map((n) => n.props.topic),
        hostileStepsTags: all(hostileSteps).map((n) => n.type),
        hostileStepsText: text(hostileSteps),
        hostileAttemptTags: all(hostileAttempt).map((n) => n.type),
        hostileAttemptText: text(hostileAttempt),
      }}));
    """)

    assert out["route"] == {"section": "runs", "id": role_run}

    assert "Rol: analyst · doc_analyst_agent (general_team v1)" in out["withRoleStepsText"]
    assert "Rol: writer · writer_agent (general_team v1)" in out["withRoleStepsText"]
    assert "run.role" in out["withRoleStepsHelps"]
    assert any("general_team" in title and "versión 1" in title for title in out["withRoleStepsTitles"])

    assert "Rol:" not in out["withoutRoleStepsText"]
    assert "run.role" not in out["withoutRoleStepsHelps"]

    assert "rol: writer (writer_agent)" in out["withRoleAttemptText"]
    assert "writer · writer_agent (general_team v1)" in out["withRoleAttemptText"]
    assert any(h["topic"] == "run.role" and h["compact"] is True for h in out["withRoleAttemptHelps"])
    assert any("general_team" in title and "versión 1" in title for title in out["withRoleAttemptTitles"])

    assert "rol:" not in out["withoutRoleAttemptText"] and "Rol" not in out["withoutRoleAttemptText"]
    assert "run.role" not in out["withoutRoleAttemptHelps"]

    # Hostile strings stay plain text in vnodes, never parsed into <img>, <script>, or <b> elements.
    for tag in ("img", "script", "b"):
        assert tag not in out["hostileStepsTags"], tag
        assert tag not in out["hostileAttemptTags"], tag
    assert '<img src=x onerror=alert(1)>' in out["hostileStepsText"]
    assert '<script>alert("xss")</script>' in out["hostileAttemptText"]


# --- Markdown report rendering ------------------------------------------------------------------------------------

def test_markdown_report_renders_step_roles_readably_and_escapes_values(env):
    role_run = _create_and_drain(env, "with_roles")
    plain_run = _create_and_drain(env, "without_roles")

    role_json = env.services.runs.report(role_run)
    role_md = env.services.runs.report(role_run, format="markdown")
    assert "| analyst | doc_analyst_agent |" in role_md
    assert "| writer | writer_agent |" in role_md
    assert "### Step roles" in role_md
    assert "- extract_key_points (attempt 1): role analyst, member doc_analyst_agent — team general_team v1" in role_md
    assert "- write_summary (attempt 1): role writer, member writer_agent — team general_team v1" in role_md
    assert set(role_json["steps"][0]) >= set(ROLE_FIELDS)

    plain_json = env.services.runs.report(plain_run)
    plain_md = env.services.runs.report(plain_run, format="markdown")
    assert "### Step roles" not in plain_md
    assert not any("role" in row for row in plain_json["steps"])

    hostile_run = {
        "id": "run-xss",
        "status": "completed",
        "workflow_id": "demo",
        "events": [
            {
                "seq": 1,
                "ts": "2026-11-18T00:00:00Z",
                "event_type": "step_started",
                "payload": {
                    "step_id": "step_a",
                    "attempt": 1,
                    "agent_id": "a",
                    "execution_mode": "auto",
                    "role": "<script>role|x\ny</script>",
                    "member": "<img src=x>|m",
                    "team_id": "team<1>",
                    "team_version_id": "tmv-111111111111",
                    "team_version": 2,
                },
            }
        ],
    }
    report = build_run_report(hostile_run, {"records": []})
    md = render_run_report_markdown(report)
    assert "<script>" not in md and "<img" not in md and "team<1>" not in md
    assert "&lt;script&gt;role\\|x y&lt;/script&gt;" in md
    assert "&lt;img src=x&gt;\\|m" in md
    assert "team&lt;1&gt; v2" in md
