# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SUP-T02: supervised and autonomous operating modes in the GUI and help texts.

Checks creating runs through ``/gui/api/runs`` with and without ``operating_mode`` (and against approved team modes),
that omitting a mode writes no ``operating_mode_set`` event, static escaping/help/payload checks on ``create-run.js``,
``run-detail.js``, and ``help-text.js``, ``node --check`` on every touched script, and pure Node rendering with both
real router payloads and hostile HTML-like strings.
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
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

TOUCHED_SCRIPTS = ("create-run.js", "run-detail.js", "help-text.js")


def _make_bundle(tmp_path: Path, name: str, *, mode: str | None) -> Path:
    dest = tmp_path / name
    shutil.copytree(GENERAL_BUNDLE, dest)
    team_path = dest / "team.yaml"
    team = yaml.safe_load(team_path.read_text(encoding="utf-8"))
    if mode is None:
        team.pop("mode", None)
    else:
        team["mode"] = mode
    team_path.write_text(yaml.safe_dump(team, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return dest


@pytest.fixture
def env(tmp_path):
    plain = _make_bundle(tmp_path, "plain_team", mode=None)
    supervised = _make_bundle(tmp_path, "supervised_team", mode="supervised")
    autonomous = _make_bundle(tmp_path, "autonomous_team", mode="autonomous")
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
    services = ApplicationServices.create(
        settings,
        bundles={"plain": plain, "supervised_team": supervised, "autonomous_team": autonomous},
    )
    for bundle_name in ("plain", "supervised_team", "autonomous_team"):
        proposed = services.teams.propose(bundle_name, actor="user:ana", channel="python")
        services.teams.approve(proposed["id"], actor="user:ana", channel="python")
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def _create_run(env, bundle: str, *, operating_mode: str | None = None):
    body = {
        "workflow_id": "summarize_document",
        "bundle": bundle,
        "project_name": "Modes GUI",
        "inputs": {"document_text": "Nota de prueba para verificar el modo de operación."},
    }
    if operating_mode is not None:
        body["operating_mode"] = operating_mode
    return env.client.post(
        "/gui/api/runs",
        headers={"Authorization": f"Bearer {env.token}"},
        json=body,
    )


def _mode_events(view: dict) -> list[dict]:
    return [event["payload"] for event in view.get("events", []) if event.get("type") == "operating_mode_set"]


# --- GUI router ---------------------------------------------------------------------------------------------------

def test_gui_create_run_with_and_without_operating_mode_and_detail(env):
    unauth = env.client.post("/gui/api/runs", json={"workflow_id": "summarize_document", "project_name": "No auth"})
    assert unauth.status_code == 401

    headers = {"Authorization": f"Bearer {env.token}"}

    # Without mode on a team without mode: no operating_mode_set event anywhere.
    plain_resp = _create_run(env, "plain")
    assert plain_resp.status_code == 202, plain_resp.text
    plain_view = plain_resp.json()
    assert _mode_events(plain_view) == []
    plain_detail = env.client.get(f"/gui/api/runs/{plain_view['id']}", headers=headers).json()
    assert _mode_events(plain_detail) == []
    assert env.services.runs.get(plain_view["id"])["operating_mode"] is None

    # Explicit run mode on a plain team records source="run".
    for chosen in ("supervised", "autonomous"):
        resp = _create_run(env, "plain", operating_mode=chosen)
        assert resp.status_code == 202, resp.text
        created = resp.json()
        assert _mode_events(created) == [{"mode": chosen, "source": "run"}]
        detail = env.client.get(f"/gui/api/runs/{created['id']}", headers=headers).json()
        assert _mode_events(detail) == [{"mode": chosen, "source": "run"}]
        assert env.services.runs.get(created["id"])["operating_mode"] == chosen

    # Without run mode on a team with mode: inherits team mode with source="team".
    team_resp = _create_run(env, "supervised_team")
    assert team_resp.status_code == 202, team_resp.text
    team_detail = env.client.get(f"/gui/api/runs/{team_resp.json()['id']}", headers=headers).json()
    assert _mode_events(team_detail) == [{"mode": "supervised", "source": "team"}]

    # Run mode overrides team mode and records source="run".
    override_resp = _create_run(env, "supervised_team", operating_mode="autonomous")
    assert override_resp.status_code == 202, override_resp.text
    override_detail = env.client.get(f"/gui/api/runs/{override_resp.json()['id']}", headers=headers).json()
    assert _mode_events(override_detail) == [{"mode": "autonomous", "source": "run"}]

    # Unknown operating_mode is rejected.
    bad = _create_run(env, "plain", operating_mode="turbo")
    assert bad.status_code == 422


# --- static checks ------------------------------------------------------------------------------------------------

def test_scripts_follow_escaping_help_and_conditional_send_rules():
    create_src = (JS / "create-run.js").read_text(encoding="utf-8")
    detail_src = (JS / "run-detail.js").read_text(encoding="utf-8")
    help_src = (JS / "help-text.js").read_text(encoding="utf-8")

    for name, src in (("create-run.js", create_src), ("run-detail.js", detail_src), ("help-text.js", help_src)):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "dangerouslySetInnerHTML", "__html", "eval(",
                    "style=", "document.write", "fetch("):
            assert bad not in src, (name, bad)

    assert "OperatingModeSelect" in create_src
    assert "buildCreateRunBody" in create_src
    assert 'if (operatingMode) body.operating_mode = operatingMode;' in create_src
    assert 'value: "", label: "Sin modo"' in create_src
    assert 'value: "supervised", label: "Supervisado"' in create_src
    assert 'value: "autonomous", label: "Autónomo"' in create_src
    assert 'helpFor("create.operating_mode")' in create_src
    assert 'topic="create.operating_mode"' in create_src

    assert "operatingModeInfo" in detail_src
    assert "RunOperatingMode" in detail_src
    assert "if (!modeInfo) return null;" in detail_src
    assert 'topic="run.operating_mode"' in detail_src

    match = re.search(r"^export const HELP = (\{.*?^\});$", help_src, re.S | re.M)
    assert match
    entries = json.loads(match[1])
    for key in ("create.operating_mode", "run.operating_mode"):
        assert key in entries
        assert set(entries[key]) == {"title", "hint", "more"}


@needs_node
def test_touched_scripts_pass_node_check():
    for name in TOUCHED_SCRIPTS:
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)


# --- Node rendering checks ----------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("modes-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def _run_node(root: Path, body: str) -> dict:
    script = root / "modes-view.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


WALKER = r"""
const kids = (v) => (v == null || typeof v === "boolean" ? [] : Array.isArray(v) ? v.flatMap(kids) : typeof v === "object" ? [v] : []);
const expand = (v) => {
  if (v == null || typeof v === "boolean" || typeof v === "string" || typeof v === "number") return v;
  if (Array.isArray(v)) return v.map(expand);
  if (v.type?.name === "Help") return { type: "Help", props: { topic: v.props?.topic, compact: Boolean(v.props?.compact) }, children: null };
  if (typeof v.type === "function") return expand(v.type(v.props || {}));
  return { type: v.type, props: { ...v.props, children: expand(v.props?.children) } };
};
const all = (v) => kids(v).flatMap((n) => [n, ...all(n.props?.children)]);
const text = (v) => v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("")
  : typeof v === "object" ? text(v.props?.children) : String(v);
"""


@needs_node
def test_create_and_detail_components_handle_modes_and_escape_hostile_strings(node_root, env):
    headers = {"Authorization": f"Bearer {env.token}"}
    plain_id = _create_run(env, "plain").json()["id"]
    run_mode_id = _create_run(env, "plain", operating_mode="autonomous").json()["id"]
    team_mode_id = _create_run(env, "supervised_team").json()["id"]

    plain_view = env.client.get(f"/gui/api/runs/{plain_id}", headers=headers).json()
    run_mode_view = env.client.get(f"/gui/api/runs/{run_mode_id}", headers=headers).json()
    team_mode_view = env.client.get(f"/gui/api/runs/{team_mode_id}", headers=headers).json()

    payload = json.dumps({
        "plainView": plain_view,
        "runModeView": run_mode_view,
        "teamModeView": team_mode_view,
        "hostileMode": '<img src=x onerror=alert(1)>',
        "hostileSource": '<script>alert("xss")</script>',
    })

    out = _run_node(node_root, WALKER + f"""
      import {{ OperatingModeSelect, OPERATING_MODES, buildCreateRunBody }} from "./js/create-run.js";
      import {{ RunOperatingMode }} from "./js/run-detail.js";
      const data = {payload};

      const baseArgs = {{
        workflowId: "summarize_document", bundle: "plain", project: " Demo ",
        inputs: {{ document_text: "Hola" }}, mode: "auto", sensitive: false,
      }};
      const bodyNone = buildCreateRunBody({{ ...baseArgs, operatingMode: "" }});
      const bodySup = buildCreateRunBody({{ ...baseArgs, operatingMode: "supervised" }});
      const bodyAuto = buildCreateRunBody({{ ...baseArgs, operatingMode: "autonomous" }});

      const selectTree = expand(OperatingModeSelect({{ value: "", disabled: false, onChange: () => {{}} }}));
      const selectNode = all(selectTree).find((n) => n.type === "select");
      const optionNodes = all(selectTree).filter((n) => n.type === "option");

      const plainBadge = expand(RunOperatingMode({{ view: data.plainView }}));
      const runModeBadge = expand(RunOperatingMode({{ view: data.runModeView }}));
      const teamModeBadge = expand(RunOperatingMode({{ view: data.teamModeView }}));

      const hostileView = {{
        ...data.plainView,
        events: [{{ seq: 1, ts: "2026-11-18", type: "operating_mode_set", payload: {{
          mode: data.hostileMode, source: data.hostileSource,
        }} }}],
      }};
      const hostileBadge = expand(RunOperatingMode({{ view: hostileView }}));

      console.log(JSON.stringify({{
        operatingModes: OPERATING_MODES,
        bodyNoneHasKey: Object.prototype.hasOwnProperty.call(bodyNone, "operating_mode"),
        bodyNone,
        bodySup,
        bodyAuto,
        selectId: selectNode?.props?.id,
        selectDescribedBy: selectNode?.props?.["aria-describedby"],
        options: optionNodes.map((n) => ({{ value: n.props?.value, text: text(n) }})),
        selectHelps: all(selectTree).filter((n) => n.type === "Help").map((n) => n.props.topic),
        plainBadge,
        runModeText: text(runModeBadge),
        runModeHelps: all(runModeBadge).filter((n) => n.type === "Help").map((n) => n.props),
        teamModeText: text(teamModeBadge),
        teamModeHelps: all(teamModeBadge).filter((n) => n.type === "Help").map((n) => n.props),
        hostileTags: all(hostileBadge).map((n) => n.type),
        hostileText: text(hostileBadge),
      }}));
    """)

    assert out["operatingModes"] == [
        {"value": "", "label": "Sin modo"},
        {"value": "supervised", "label": "Supervisado"},
        {"value": "autonomous", "label": "Autónomo"},
    ]
    assert out["bodyNoneHasKey"] is False
    assert out["bodySup"]["operating_mode"] == "supervised"
    assert out["bodyAuto"]["operating_mode"] == "autonomous"

    assert out["selectId"] == "cr-operating-mode"
    assert "help-create-operating-mode-hint" in out["selectDescribedBy"]
    assert out["options"] == [
        {"value": "", "text": "Sin modo"},
        {"value": "supervised", "text": "Supervisado"},
        {"value": "autonomous", "text": "Autónomo"},
    ]
    assert out["selectHelps"] == ["create.operating_mode"]

    assert out["plainBadge"] is None
    assert "Autónomo" in out["runModeText"] and "de la ejecución" in out["runModeText"]
    assert out["runModeHelps"] == [{"topic": "run.operating_mode", "compact": True}]
    assert "Supervisado" in out["teamModeText"] and "del equipo" in out["teamModeText"]
    assert out["teamModeHelps"] == [{"topic": "run.operating_mode", "compact": True}]

    for tag in ("img", "script"):
        assert tag not in out["hostileTags"], tag
    assert '<img src=x onerror=alert(1)>' in out["hostileText"]
    assert '<script>alert("xss")</script>' in out["hostileText"]
