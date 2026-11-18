# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EVA-T03: the read-only evaluation view of the GUI (``/gui/api/evaluations`` and ``static/js/evaluation.js``).

The router is checked over real services in Mock: an empty list, a set with a baseline that a run induced to fail
regresses against, a set without a baseline, ``not_found``, and that every route asks for the GUI token. The view is
checked as static text and, when Node is installed, rendered with data of the router's shape.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.evaluation import load_eval_set
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from test_evaluation_regression import WORKFLOW_SET, broken_workflows

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def get(env, path, **params):
    return env.client.get("/gui/api/evaluations" + path, params=params, headers={"Authorization": f"Bearer {env.token}"})


def save_good_then_regressed(env, tmp_path, monkeypatch):
    good = env.services.evaluation.run(load_eval_set(WORKFLOW_SET), actor="user:ana")
    good_id = good["meta"]["evaluation_run_id"]
    broken_workflows(tmp_path, monkeypatch)
    bad = env.services.evaluation.run(load_eval_set(WORKFLOW_SET), actor="user:ana")
    return good["name"], good_id, bad["meta"]["evaluation_run_id"]


# --- the router --------------------------------------------------------------------------------------------------

def test_every_route_asks_for_the_gui_token(env):
    for path in ("", "/runs", "/runs/evr-000000000000", "/comparison?set_name=x"):
        assert env.client.get("/gui/api/evaluations" + path).status_code == 401, path
        assert env.client.get("/gui/api/evaluations" + path, headers={"Authorization": "Bearer wrong"}).status_code == 401, path


def test_an_empty_system_lists_no_sets(env):
    answer = get(env, "")
    assert answer.status_code == 200 and answer.json() == {"sets": [], "count": 0}
    assert get(env, "/runs").json() == {"runs": [], "count": 0}


def test_a_set_without_a_baseline_says_so_and_has_nothing_to_compare(env):
    report = env.services.evaluation.run(load_eval_set(WORKFLOW_SET))
    entry = get(env, "").json()["sets"][0]
    assert entry["set_name"] == report["name"] and entry["status"] == "no_baseline" and entry["baseline"] is None
    assert entry["latest_run"]["id"] == report["meta"]["evaluation_run_id"] and entry["latest_run"]["live"] is False
    answer = get(env, "/comparison", set_name=report["name"])
    assert answer.status_code == 404 and answer.json()["detail"]["code"] == "not_found"


def test_a_baseline_and_an_induced_regression(env, tmp_path, monkeypatch):
    name, good_id, bad_id = save_good_then_regressed(env, tmp_path, monkeypatch)
    # the latest run is the one that was just saved, and with no baseline the set is not judged
    assert get(env, "").json()["sets"][0]["status"] == "no_baseline"
    env.services.evaluation.set_baseline(name, good_id, "user:ana")

    entry = get(env, "").json()["sets"][0]
    assert entry["status"] == "regression"
    assert entry["latest_run"]["id"] == bad_id and entry["baseline"]["run_id"] == good_id
    assert entry["baseline"]["run"]["id"] == good_id and "report" not in entry["baseline"]["run"]

    comparison = get(env, "/comparison", set_name=name)
    assert comparison.status_code == 200   # a regression is a result, not an error
    body = comparison.json()
    assert body["regression"] is True and body["base_id"] == good_id and body["candidate_id"] == bad_id
    assert body["candidate"]["id"] == bad_id and body["base"]["id"] == good_id
    assert body["summary"]["regressed"] >= 1 and {"id", "status", "delta"} <= set(body["cases"][0])
    assert set(body["fingerprint"]) == {"agent_or_prompt", "workflow", "models", "set_changed"}

    runs = get(env, "/runs", set_name=name).json()
    assert [run["id"] for run in runs["runs"]] == [bad_id, good_id]
    one = get(env, f"/runs/{good_id}").json()
    assert one["id"] == good_id and "report" in one and "fingerprint" in one


def test_the_latest_run_equal_to_the_baseline_is_up_to_date(env):
    report = env.services.evaluation.run(load_eval_set(WORKFLOW_SET))
    run_id = report["meta"]["evaluation_run_id"]
    env.services.evaluation.set_baseline(report["name"], run_id)
    assert get(env, "").json()["sets"][0]["status"] == "up_to_date"
    body = get(env, "/comparison", set_name=report["name"]).json()
    assert body["regression"] is False and body["summary"]["regressed"] == 0


def test_not_found_and_invalid_requests_use_the_service_errors(env):
    assert get(env, "/runs/evr-000000000000").status_code == 404
    assert get(env, "/runs/not-an-id").status_code == 422
    assert get(env, "/comparison", set_name="unknown").status_code == 404
    assert get(env, "/runs", limit=0).status_code == 422
    assert get(env, "/comparison").status_code == 422   # set_name is required


def test_the_routes_only_read(env):
    env.services.evaluation.run(load_eval_set(WORKFLOW_SET))
    for method in ("post", "put", "delete", "patch"):
        for path in ("", "/runs", "/baselines/x", "/comparison"):
            answer = env.client.request(method.upper(), "/gui/api/evaluations" + path, json={},
                                        headers={"Authorization": f"Bearer {env.token}"})
            assert answer.status_code in (404, 405), (method, path)
    assert get(env, "/runs").json()["count"] == 1


# --- the view, as text -------------------------------------------------------------------------------------------

def test_the_view_is_wired_into_the_routes_the_menu_and_the_help():
    view = (JS / "evaluation.js").read_text(encoding="utf-8")
    main = (JS / "main.js").read_text(encoding="utf-8")
    assert 'import { Evaluation } from "./evaluation.js"' in main and '{ id: "evaluation", label: "Evaluación" }' in main
    assert re.search(r"<\$\{Evaluation\}[^>]*route=\$\{route\}", main)
    assert '"evaluation"' in (JS / "route.js").read_text(encoding="utf-8")
    for path in ("/gui/api/evaluations", "/comparison?", "/runs?"):
        assert path in view, path
    # read only: it never sends anything
    assert "apiPost" not in view and "method:" not in view and "fetch(" not in view
    for bad in ("innerHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style="):
        assert bad not in view, bad
    for topic in re.findall(r'topic="(evaluation\.\w+)"', view) + re.findall(r'helpFor\("(evaluation\.\w+)"', view):
        assert topic in (JS / "help-text.js").read_text(encoding="utf-8"), topic


# --- the view, rendered (needs Node) -----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("evaluation-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "evaluation-view.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


WALKER = r"""
const expand = (v) => {
  if (v == null || typeof v === "boolean" || typeof v === "string" || typeof v === "number") return v;
  if (Array.isArray(v)) return v.map(expand);
  if (v.type?.name === "Help") return { type: "help-placeholder", props: { topic: v.props?.topic } };
  if (typeof v.type === "function") return expand(v.type(v.props || {}));
  return { type: v.type, props: { ...v.props, children: expand(v.props?.children) } };
};
const text = (v) => v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("")
  : typeof v === "object" ? text(v.props?.children) : String(v);
"""


@needs_node
def test_the_list_shows_score_mode_and_state_of_each_set(node_root):
    out = run_node(node_root, WALKER + r"""
      import { SetList } from "./js/evaluation.js";
      const run = (score, live) => ({ id: "evr-000000000001", score, live, created_at: "2026-11-18T10:00:00+00:00" });
      const data = { sets: [
        { set_name: "uno", status: "regression", latest_run: run(0.5, false), baseline: { run: run(1, false) } },
        { set_name: "dos", status: "no_baseline", latest_run: run(1, true), baseline: null },
        { set_name: "tres", status: "up_to_date", latest_run: run(1, false), baseline: { run: run(1, false) } },
      ] };
      console.log(JSON.stringify({ text: text(expand(SetList({ data }))), empty: text(expand(SetList({ data: { sets: [] } }))) }));
    """)
    for word in ("uno", "50 %", "Regresión", "dos", "En vivo", "Sin línea base", "tres", "Al día", "Mock"):
        assert word in out["text"], word
    assert "Todavía no hay ejecuciones" in out["empty"]


@needs_node
def test_the_comparison_lists_cases_by_state_the_unmatched_ones_and_the_fingerprint(node_root):
    out = run_node(node_root, WALKER + r"""
      import { ComparisonView, orderCases, fingerprintChanges } from "./js/evaluation.js";
      const item = (id, status) => ({ id, status, base_score: 1, candidate_score: 0.5, base_passed: true, candidate_passed: false, changed_checks: ["title"] });
      const comparison = { regression: true, threshold: 0, score: { base: 1, candidate: 0.5, delta: -0.5 },
        summary: { regressed: 1, improved: 1, changed: 1, unchanged: 1 },
        cases: [item("c-same", "unchanged"), item("c-changed", "changed"), item("c-up", "improved"), item("c-down", "regressed")],
        only_in_base: ["solo-base"], only_in_candidate: ["solo-nueva"],
        fingerprint: { agent_or_prompt: false, workflow: true, models: true, set_changed: false } };
      const quiet = { ...comparison, regression: false, only_in_base: [], only_in_candidate: [],
        fingerprint: { agent_or_prompt: false, workflow: false, models: false, set_changed: false } };
      console.log(JSON.stringify({
        text: text(expand(ComparisonView({ comparison }))), quiet: text(expand(ComparisonView({ comparison: quiet }))),
        order: orderCases(comparison.cases).map((c) => c.id), changes: fingerprintChanges(comparison.fingerprint) }));
    """)
    assert out["order"] == ["c-down", "c-up", "c-changed", "c-same"]
    assert out["changes"] == ["Flujo de trabajo", "Modelos y proveedores"]
    for word in ("Regresión", "Mejora", "Cambió", "Sin cambios", "Solo en la línea base", "solo-base", "solo-nueva",
                 "Flujo de trabajo", "Modelos y proveedores", "-50 puntos"):
        assert word in out["text"], word
    assert "Solo en la línea base" not in out["quiet"] and "Nada:" in out["quiet"]


@needs_node
def test_evaluation_is_a_supported_hash_route(node_root):
    out = run_node(node_root, r"""
      import { parseRoute, routeHref } from "./js/route.js";
      console.log(JSON.stringify({ list: parseRoute("#evaluation"), one: parseRoute("#evaluation/mi%20conjunto"),
        href: routeHref("evaluation", "mi conjunto") }));
    """)
    assert out == {"list": {"section": "evaluation", "id": None}, "one": {"section": "evaluation", "id": "mi conjunto"},
                   "href": "#evaluation/mi%20conjunto"}


@needs_node
def test_every_changed_script_parses():
    for name in ("evaluation.js", "route.js", "main.js", "extensions.js", "help-text.js"):
        result = subprocess.run([NODE, "--check", str(JS / name)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr)
