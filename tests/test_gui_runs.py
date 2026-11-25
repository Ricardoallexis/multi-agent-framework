# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI runs screen (GUI-T07): inputs, preview, run view, human actions and static checks."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
POST_INPUTS = {"topic": "Gatos que programan", "platform": "Instagram", "audience": "Equipos pequenos",
               "instructions": "Tono cercano"}
CONTENT = {"title": "Gatos y codigo", "hook": "Un gato revisa tu codigo cada manana.",
           "body": "Contenido sintetico de prueba para validar el flujo guiado completo sin ningun modelo.",
           "cta": "Cuentanos mas", "hashtags": ["#gatos", "#codigo"], "source_urls_used": []}


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        max_llm_calls=4, gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token")
    services = ApplicationServices.create(settings, bundles={"team": BUNDLE}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, token=token)
    services.runs.close()


def headers(env):
    return {"Authorization": f"Bearer {env.token}"}


def get(env, path, **kwargs):
    return env.client.get("/gui/api" + path, headers=headers(env), **kwargs)


def post(env, path, body=None):
    return env.client.post("/gui/api" + path, json=body if body is not None else {}, headers=headers(env))


def create_body(**overrides):
    return {"workflow_id": "social_post", "project_name": "Prueba GUI", "inputs": dict(POST_INPUTS),
            "execution_mode": "human_guided", **overrides}


def process(env):
    assert env.services.system.worker.process_once()


def guided_run(env):
    created = post(env, "/runs", create_body())
    assert created.status_code == 202, created.text
    process(env)
    return created.json()["id"]


# --- authentication ---------------------------------------------------------------

READ_ROUTES = ["/bundles", "/workflows", "/workflows/social_post/inputs", "/runs", "/runs/x", "/runs/x/human-next"]
WRITE_ROUTES = ["/runs/preview", "/runs", "/runs/x/approve", "/runs/x/changes", "/runs/x/reject",
                "/runs/x/regenerate", "/runs/x/cancel", "/runs/x/human-submit"]


@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_require_the_token(env, path):
    assert env.client.get("/gui/api" + path).status_code == 401
    assert env.client.get("/gui/api" + path, headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize("path", WRITE_ROUTES)
def test_write_routes_require_token_and_json(env, path):
    assert env.client.post("/gui/api" + path, json={}).status_code == 401
    plain = env.client.post("/gui/api" + path, content="{}", headers={**headers(env), "Content-Type": "text/plain"})
    assert plain.status_code == 415
    form = env.client.post("/gui/api" + path, data={"a": "b"}, headers=headers(env))
    assert form.status_code == 415


def test_a_decision_without_the_token_changes_nothing(env):
    run_id = guided_run(env)
    assert env.client.post(f"/gui/api/runs/{run_id}/cancel", json={}).status_code == 401
    assert env.services.runs.get(run_id)["status"] == "waiting_human"


# --- inputs and form ---------------------------------------------------------------

def test_workflow_inputs_come_from_the_workflow_and_its_prompt_placeholders(env):
    spec = get(env, "/workflows/social_post/inputs").json()
    assert spec["source"] == "declared"  # the workflow declares them; GUI-T11 tests cover the details
    by_name = {item["name"]: item for item in spec["inputs"]}
    assert {name for name, item in by_name.items() if item["required"]} == {
        "topic", "platform", "audience", "instructions"}
    objective = by_name["objective"]  # only the research step uses it, and research is skipped by default
    assert objective["required"] is False and objective["skipped_only"] is True and objective["used_in"] == ["research"]
    assert by_name["brand_context"]["context"] is True and by_name["brand_context"]["required"] is False
    assert by_name["topic"]["prompt_line"] == "Topic: {topic}"
    assert "previous_output" not in by_name and "revision_feedback" not in by_name
    assert [(s["id"], s["skipped_by_default"]) for s in spec["steps"]] == [("research", True), ("create", False)]


def test_full_pipeline_makes_objective_required(env):
    spec = get(env, "/workflows/social_post_full/inputs").json()
    assert {item["name"]: item["required"] for item in spec["inputs"]}["objective"] is True


def test_bundle_workflow_inputs_exclude_runtime_context(env):
    spec = get(env, "/workflows/spec_review/inputs", params={"bundle": "team"}).json()
    assert [(i["name"], i["required"]) for i in spec["inputs"]] == [("spec_text", True)]
    assert spec["bundle"] == "team" and spec["source"] == "prompt_placeholders"
    assert [i["source"] for i in spec["inputs"]] == ["derived"]


def test_unknown_workflow_or_bundle_is_not_found(env):
    assert get(env, "/workflows/missing/inputs").status_code == 404
    assert get(env, "/workflows/spec_review/inputs", params={"bundle": "nope"}).status_code == 404
    assert get(env, "/workflows/..%2Fx/inputs").status_code in {404, 422}


def test_catalog_routes(env):
    assert {w["id"] for w in get(env, "/workflows").json()} == {"social_post", "social_post_full"}
    assert get(env, "/bundles").json() == [{"name": "team"}]
    assert [w["id"] for w in get(env, "/workflows", params={"bundle": "team"}).json()] == ["spec_review"]


# --- required inputs are enforced by the server -------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_creation_rejects_empty_required_inputs(env, blank):
    body = create_body(inputs={**POST_INPUTS, "topic": blank, "audience": blank})
    response = post(env, "/runs", body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["details"]["missing_inputs"] == ["topic", "audience"]
    assert get(env, "/runs").json() == []  # nothing was created


def test_creation_rejects_absent_inputs_and_allows_missing_optional_ones(env):
    assert post(env, "/runs", create_body(inputs={})).json()["detail"]["details"]["missing_inputs"] == [
        "topic", "platform", "audience", "instructions"]  # in the order the workflow declares them
    ok = post(env, "/runs", create_body())  # objective and *_context were not given
    assert ok.status_code == 202


def test_creation_validates_the_rest_of_the_request(env):
    assert post(env, "/runs", create_body(project_name="x")).status_code == 422
    assert post(env, "/runs", create_body(execution_mode="warp")).status_code == 422
    assert post(env, "/runs", create_body(workflow_id="missing")).status_code == 404
    assert post(env, "/runs", {**create_body(), "surprise": 1}).status_code == 422
    assert get(env, "/runs").json() == []


def test_creation_returns_the_run_view_with_planned_skips(env):
    run = post(env, "/runs", create_body()).json()
    assert (run["status"], run["workflow_id"], run["project_name"], run["bundle"]) == (
        "queued", "social_post", "Prueba GUI", None)
    assert run["inputs"]["topic"] == POST_INPUTS["topic"]
    assert [(s["id"], s["state"], s["skip"]) for s in run["steps"]] == [
        ("research", "will_skip", {"condition": "requires_web"}), ("create", "pending", None)]
    assert run["counters"]["model_calls"] == 0 and run["counters"]["max_model_calls"] == 4


def test_creation_is_idempotent_with_a_key(env):
    first = post(env, "/runs", create_body(idempotency_key="k-1")).json()
    again = post(env, "/runs", create_body(idempotency_key="k-1")).json()
    assert first["id"] == again["id"] and len(get(env, "/runs").json()) == 1


def test_bundle_run_view(env):
    body = {"workflow_id": "spec_review", "bundle": "team", "project_name": "Revision", "inputs": {"spec_text": "Reset by email."}}
    assert post(env, "/runs", {**body, "inputs": {"spec_text": " "}}).status_code == 422
    run = post(env, "/runs", body).json()
    assert run["bundle"] == "team" and [s["id"] for s in run["steps"]] == [
        "extract_requirements", "assess_risks", "draft_test_plan"]
    assert get(env, f"/runs/{run['id']}").json()["bundle"] == "team"
    assert [r["id"] for r in get(env, "/runs").json()] == [run["id"]]


# --- preview -------------------------------------------------------------------------

def test_preview_renders_the_prompt_as_text_without_creating_anything(env):
    response = post(env, "/runs/preview", {"workflow_id": "social_post", "inputs": POST_INPUTS})
    assert response.status_code == 200
    preview = response.json()
    assert preview["missing_inputs"] == [] and preview["source"] == "declared"
    research, create = preview["steps"]
    assert research["skipped"] is True and research["skip"] == {"condition": "requires_web"} and research["text"] is None
    assert create["skipped"] is False and create["contract"] == "ContentOutput"
    assert "Topic: Gatos que programan" in create["text"] and "Audience: Equipos pequenos" in create["text"]
    assert "## Active skill" in create["text"] and "{topic}" not in create["text"]
    assert "objective" not in preview["missing_inputs"]
    assert get(env, "/runs").json() == []
    assert env.services.system.store.list_runs() == []


def test_preview_reports_missing_inputs_and_blank_holes(env):
    preview = post(env, "/runs/preview", {"workflow_id": "social_post", "inputs": {"topic": "Solo tema"}}).json()
    assert preview["missing_inputs"] == ["platform", "audience", "instructions"]
    assert "brand_context" in preview["empty_optional_inputs"]
    assert "Platform: \n" in preview["steps"][1]["text"]


def test_preview_text_is_data_not_code(env):
    hostile = '<img src=x onerror=alert(1)>{__class__} {0}'
    preview = post(env, "/runs/preview", {"workflow_id": "social_post",
                                           "inputs": {**POST_INPUTS, "topic": hostile}}).json()
    assert hostile in preview["steps"][1]["text"]  # inserted verbatim, never evaluated


def test_preview_marks_steps_that_need_earlier_outputs(env):
    steps = post(env, "/runs/preview", {"workflow_id": "spec_review", "bundle": "team",
                                         "inputs": {"spec_text": "A"}}).json()["steps"]
    assert [s["needs_previous_outputs"] for s in steps] == [False, True, True]
    assert all(s["text"] for s in steps)


def test_preview_adds_the_privacy_warning_only_when_guided_and_sensitive(env):
    text = lambda **extra: post(env, "/runs/preview", {"workflow_id": "social_post", "inputs": POST_INPUTS,
                                                        **extra}).json()["steps"][1]["text"]
    assert text(execution_mode="human_guided", sensitive=True).startswith("# PRIVACY WARNING")
    assert not text(execution_mode="auto", sensitive=True).startswith("# PRIVACY WARNING")
    assert not text(execution_mode="human_guided").startswith("# PRIVACY WARNING")


def test_preview_errors(env):
    assert post(env, "/runs/preview", {"workflow_id": "missing"}).status_code == 404
    assert post(env, "/runs/preview", {"workflow_id": "social_post", "inputs": {"version": "x"}}).status_code == 422
    assert post(env, "/runs/preview", {"workflow_id": "social_post", "inputs": {"a": 1}}).status_code == 422


# --- list and detail -------------------------------------------------------------------

def test_run_list_and_detail(env):
    first = post(env, "/runs", create_body(project_name="Primera")).json()
    second = post(env, "/runs", create_body(project_name="Segunda")).json()
    listed = get(env, "/runs").json()
    assert {item["id"] for item in listed} == {first["id"], second["id"]}
    assert {"id", "workflow_id", "project_name", "status", "bundle", "llm_calls", "max_llm_calls"} <= set(listed[0])
    assert get(env, "/runs", params={"status": "completed"}).json() == []
    assert get(env, "/runs", params={"limit": 0}).status_code == 422
    detail = get(env, f"/runs/{first['id']}").json()
    assert detail["id"] == first["id"] and detail["project_name"] == "Primera"
    assert get(env, "/runs/missing").status_code == 404


def test_detail_shows_skipped_steps_counters_and_finished_cursor(env):
    run_id = guided_run(env)
    waiting = get(env, f"/runs/{run_id}").json()
    assert [(s["id"], s["state"]) for s in waiting["steps"]] == [("research", "skipped"), ("create", "waiting_human")]
    assert waiting["steps"][0]["skip"] == {"condition": "requires_web"}
    assert waiting["waiting"]["step_id"] == "create" and waiting["waiting"]["reason"] == "execute_step"
    assert waiting["pending_human"]["expected_contract"] == "ContentOutput"
    assert waiting["cursor"] == {"index": 1, "total": 2, "finished": False}
    assert waiting["counters"]["model_calls"] == 0 and waiting["counters"]["max_model_calls"] == 4

    pending = get(env, f"/runs/{run_id}/human-next").json()
    assert "Topic: Gatos que programan" in pending["prompt"]
    post(env, f"/runs/{run_id}/human-submit", {"raw_response": json.dumps(CONTENT)})
    done = post(env, f"/runs/{run_id}/approve").json()
    assert done["status"] == "completed" and done["terminal"] is True
    assert done["cursor"] == {"index": 2, "total": 2, "finished": True}
    assert [(s["id"], s["state"]) for s in done["steps"]] == [("research", "skipped"), ("create", "done")]
    assert done["counters"]["model_calls"] == 0 and done["counters"]["human_steps"] == 1
    assert done["counters"]["steps_done"] == 1 and done["counters"]["steps_skipped"] == 1
    assert done["steps"][1]["executor"] == {"kind": "human", "provider": "human", "model": ""}
    assert done["artifacts"][0]["kind"] == "ContentOutput" and done["artifacts"][0]["approved"] is True


def test_detail_survives_a_removed_workflow_definition(env, monkeypatch):
    run_id = guided_run(env)
    from multiagent.services.errors import ServiceError
    def gone(*args, **kwargs):
        raise ServiceError("not_found", "gone", status=404)
    monkeypatch.setattr(env.services.definitions, "read_workflow", gone)
    detail = get(env, f"/runs/{run_id}").json()
    assert detail["definition_available"] is False
    assert [s["id"] for s in detail["steps"]] == ["research", "create"]


def test_event_payloads_drop_local_paths(env):
    run_id = guided_run(env)
    events = get(env, f"/runs/{run_id}").json()["events"]
    assert any(e["type"] == "human_step_requested" for e in events)
    assert "prompt_path" not in json.dumps(events) and "raw_path" not in json.dumps(events)
    # the raw framework event does carry the path, so the cleaning is doing the work
    raw = env.services.runs.get(run_id)["events"]
    assert any("prompt_path" in e["payload"] for e in raw)


# --- human actions and history -------------------------------------------------------------

def test_guided_flow_records_a_history_of_human_actions(env):
    run_id = guided_run(env)
    bad = post(env, f"/runs/{run_id}/human-submit", {"raw_response": "no es json", "provider": "chat"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "human_submission_invalid"
    good = post(env, f"/runs/{run_id}/human-submit",
                {"raw_response": json.dumps(CONTENT), "provider": "gemini-web", "model": "gemini 3"})
    assert good.status_code == 200
    view = good.json()
    assert (view["status"], view["waiting"]["reason"]) == ("waiting_human", "review")
    assert view["steps"][1]["state"] == "waiting_review"
    changes = post(env, f"/runs/{run_id}/changes", {"feedback": "Mas corto"}).json()
    assert changes["status"] == "queued"
    process(env)
    post(env, f"/runs/{run_id}/human-submit", {"raw_response": json.dumps(CONTENT), "provider": "gemini-web"})
    final = post(env, f"/runs/{run_id}/approve").json()
    history = final["history"]
    assert [h["kind"] for h in history] == [
        "prompt_delivered", "response_rejected", "response_submitted", "changes_requested",
        "prompt_delivered", "response_submitted", "approved"]
    assert [h["actor"] for h in history] == ["system", "operator", "declared", "user:operator", "system", "declared", "user:operator"]
    submitted = history[2]
    assert (submitted["provider"], submitted["model"], submitted["step_id"], submitted["attempt"]) == (
        "gemini-web", "gemini 3", "create", 1)
    assert history[3]["feedback"] == "Mas corto" and history[1]["error"]
    assert all(h["ts"] and h["seq"] for h in history)
    assert [h["seq"] for h in history] == sorted(h["seq"] for h in history)
    assert final["counters"]["human_steps"] == 2 and final["counters"]["model_calls"] == 0


def test_review_decisions_go_through_the_run_services(env):
    run_id = guided_run(env)
    post(env, f"/runs/{run_id}/human-submit", {"raw_response": json.dumps(CONTENT)})
    regenerated = post(env, f"/runs/{run_id}/regenerate").json()
    assert regenerated["status"] == "queued" and regenerated["history"][-1]["kind"] == "regenerated"
    process(env)
    rejected = post(env, f"/runs/{run_id}/reject", {"feedback": "Fuera de alcance"}).json()
    assert rejected["status"] == "rejected" and rejected["terminal"] is True
    assert rejected["history"][-1] == {**rejected["history"][-1], "kind": "rejected", "feedback": "Fuera de alcance",
                                       "actor": "operator"}


def test_changes_need_feedback_and_state_errors_are_409(env):
    run_id = guided_run(env)
    blank = post(env, f"/runs/{run_id}/changes", {"feedback": "   "})
    assert blank.status_code == 422 and blank.json()["detail"]["details"] == {"field": "feedback"}
    assert post(env, f"/runs/{run_id}/approve").status_code == 409  # waiting for the human step, not a review
    assert post(env, "/runs/missing/approve").status_code == 404
    assert post(env, f"/runs/{run_id}/human-submit", {"raw_response": ""}).status_code == 422
    assert get(env, "/runs/missing/human-next").status_code == 404


def test_cancel_records_who_did_it(env):
    run_id = guided_run(env)
    cancelled = post(env, f"/runs/{run_id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["history"][-1]["kind"] == "cancelled" and cancelled["history"][-1]["actor"] == "operator"
    assert [s["state"] for s in cancelled["steps"]] == ["skipped", "not_run"]
    assert get(env, f"/runs/{run_id}/human-next").status_code == 409


def test_soft_cancel_of_a_running_run_is_recorded_as_a_request(env):
    run_id = post(env, "/runs", create_body(execution_mode="auto")).json()["id"]
    env.services.system.store.update_run(run_id, status="running")
    pending = post(env, f"/runs/{run_id}/cancel").json()
    assert pending["status"] == "running" and pending["cancel_requested"] is True
    assert pending["history"][-1]["kind"] == "cancel_requested"
    forced = post(env, f"/runs/{run_id}/cancel", {"force": True}).json()
    assert forced["status"] == "cancelled" and forced["history"][-1]["forced"] is True


def test_automatic_run_counts_model_calls(env):
    run_id = post(env, "/runs", create_body(execution_mode="auto")).json()["id"]
    process(env)
    view = get(env, f"/runs/{run_id}").json()
    assert view["status"] == "waiting_human" and view["waiting"]["reason"] == "review"
    assert view["counters"]["model_calls"] == 1 and view["counters"]["human_steps"] == 0
    assert view["steps"][1]["executor"]["kind"] == "model"
    assert view["history"] == []  # a model step is not a human action
    assert post(env, f"/runs/{run_id}/approve").json()["status"] == "completed"


# --- the stream and the static client ------------------------------------------------------

def test_stream_carries_the_events_the_run_view_refreshes_on(env):
    run_id = guided_run(env)
    from multiagent.gui import events
    seen = events.read_after(env.services.system.store, 0)
    assert any(e["run_id"] == run_id and e["type"] == "human_step_requested" for e in seen)


JS_FILES = sorted((STATIC_DIR / "js").rglob("*.js"))
FORBIDDEN = [
    (r"\binnerHTML\b", "innerHTML"), (r"\bouterHTML\b", "outerHTML"), (r"insertAdjacentHTML", "insertAdjacentHTML"),
    (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML"), (r"\beval\s*\(", "eval"), (r"\bnew\s+Function\b", "Function"),
    (r"document\.write", "document.write"), (r"javascript:", "javascript: URL"),
    (r"""\bon[a-z]+\s*=\s*["']""", "inline handler attribute"),
    (r"""setAttribute\(\s*["']on""", "setAttribute on*"),
    (r"""\bset(?:Timeout|Interval)\(\s*["'`]""", "string timer"),
    (r"\bcreateContextualFragment\b", "createContextualFragment"), (r"DOMParser", "DOMParser"),
    (r"\.srcdoc\b", "srcdoc"), (r"\bimport\s*\(\s*[^)'\"`]", "dynamic import of a variable"),
]


def test_new_client_files_exist():
    names = {path.name for path in JS_FILES}
    assert {"runs.js", "create-run.js", "run-detail.js", "human-actions.js", "api.js", "bus.js", "text.js"} <= names


@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.name)
def test_client_code_has_no_html_injection_eval_or_inline_handlers(path):
    text = path.read_text(encoding="utf-8")
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{path.name}: {label}"


def test_client_loads_only_same_origin_code_and_no_polling_every_two_seconds():
    for path in JS_FILES:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""https?://""", text), path.name
        assert not re.search(r"setInterval\([^)]*,\s*(?:[0-9]{1,3}|1[0-9]{3}|2000)\s*\)", text), path.name
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "style=" not in html


def test_client_texts_are_spanish_and_name_the_counters_clearly():
    text = ui_text((STATIC_DIR / "js" / "text.js"))
    run_detail = ui_text((STATIC_DIR / "js" / "run-detail.js"))
    assert "Llamadas al modelo" in run_detail and "máximo" in run_detail
    assert "no matching step" not in text + run_detail and "Model calls" not in text + run_detail
    assert "Todos los pasos terminaron" in text + run_detail
