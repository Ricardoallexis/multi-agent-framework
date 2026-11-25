# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T01: «Nueva sesión» prepares a plan when the request needs more than one workflow.

Service and route tests, all in Mock with fake adapters: a plan is drafted (nothing saved), saved only after a
confirmation as a proposal nobody approved, and the teams it lacks are proposed, never approved."""
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.planning.chat import PLAN_SEPARATORS, split_request
from multiagent.services import ApplicationServices

ROOT = Path(__file__).resolve().parents[1]
GENERAL = ROOT / "examples" / "bundles" / "general"
TWO_PARTS = "Summarize the document in plain language and then review the specification against the criteria"


def make(tmp_path, bundle=GENERAL, adapter=None, **options):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                        gemini_api_key="", openai_api_key="", openai_enabled=False,
                        gui_token_file=tmp_path / "gui-token", **options)
    overrides = {"fake": adapter} if adapter is not None else None
    return ApplicationServices.create(settings, bundles={"general": bundle}, dry_run=True, adapter_overrides=overrides)


@pytest.fixture
def services(tmp_path):
    made = make(tmp_path)
    yield made
    made.runs.close()


@pytest.fixture
def env(tmp_path):
    made = make(tmp_path)
    token = ensure_token(made.settings.gui_token_file)
    with TestClient(create_app(services=made), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, auth={"Authorization": f"Bearer {token}"}, services=made)
    made.runs.close()


def role_bundle(tmp_path):
    folder = tmp_path / "role-general"
    shutil.copytree(GENERAL, folder)
    path = folder / "workflows" / "summarize_document.yaml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    workflow["steps"][0]["role"] = "analyst"
    workflow["steps"][1]["role"] = "writer"
    path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")
    return folder


# --- the parts of a request ------------------------------------------------------------------------------------------------

def test_split_request():
    assert split_request("Resume el informe y luego redacta un correo") == ["Resume el informe", "redacta un correo"]
    assert split_request("Resume el informe") == ["Resume el informe"]
    assert split_request("Haz esto, Y DESPUÉS, haz aquello; luego lo otro") == ["Haz esto", "haz aquello", "lo otro"]
    assert split_request("luego   ") == [] and split_request("") == []
    assert split_request("Summarize the report and then draft an email") == ["Summarize the report", "draft an email"]
    assert split_request("Do this, THEN do that; after that the rest") == ["Do this", "do that", "the rest"]
    assert split_request("Strengthen the authentication") == ["Strengthen the authentication"]   # whole words only
    assert PLAN_SEPARATORS == ("y luego", "y después", "después", "luego", "and then", "after that", "then")


# --- interpret -------------------------------------------------------------------------------------------------------------

def test_single_request_unchanged(services):
    answer = services.new_session.interpret("Summarize the document in plain language please")
    assert answer["source"] == "model" and answer["reason"] == "" and "plan_draft" not in answer
    assert answer["proposal"]["workflow_id"] == "summarize_document"


def test_two_workflows_suggest_a_plan(services):
    answer = services.new_session.interpret(TWO_PARTS)
    assert answer["source"] == "model" and answer["reason"] == "plan_suggested"
    draft = answer["plan_draft"]
    assert draft["schema"] == "maf.new-session-plan/1" and draft["planner"] == "deterministic"
    assert draft["group_id"].startswith("chat-") and len(draft["group_id"]) == len("chat-") + 12
    assert [task["workflow"] for task in draft["tasks"]] == ["summarize_document", "review_checklist"]
    first, second = draft["tasks"]
    assert first["depends_on"] == [] and second["depends_on"] == [first["id"]]
    assert first["inputs"] == {"document_text": "Summarize the document in plain language"}
    assert draft["proposal"]["schema"] == "multiagent.plan_proposal/1" and draft["teams"] == []
    assert answer["proposal"]["workflow_id"]      # the single-workflow proposal stays available


def test_parts_without_a_workflow_stay_a_single_request(services):
    answer = services.new_session.interpret("Summarize the document in plain language and then zzz qqq")
    assert answer["reason"] == "" and "plan_draft" not in answer


# --- the draft and the save -----------------------------------------------------------------------------------------------

def test_draft_saves_nothing(services):
    before = services.plan_proposals.list()
    services.new_session.draft_plan(TWO_PARTS)
    services.new_session.interpret(TWO_PARTS)
    assert services.plan_proposals.list() == before == []


def test_save_needs_confirmation(env):
    draft = env.client.post("/gui/api/new-session/plan", headers=env.auth, json={"text": TWO_PARTS}).json()
    url = "/gui/api/new-session/plan/save"
    for body in ({"draft": draft}, {"draft": draft, "confirmed": False}):
        answer = env.client.post(url, headers=env.auth, json=body)
        assert answer.status_code == 409
        assert answer.json()["detail"]["code"] == "invalid_state"
        assert answer.json()["detail"]["details"]["reason"] == "confirmation_required"
    assert env.services.plan_proposals.list() == []


def test_save_creates_proposal_not_approved(env):
    runs_before = env.client.get("/gui/api/runs?limit=50", headers=env.auth).json()
    draft = env.client.post("/gui/api/new-session/plan", headers=env.auth, json={"text": TWO_PARTS}).json()
    saved = env.client.post("/gui/api/new-session/plan/save", headers=env.auth, json={"draft": draft, "confirmed": True})
    assert saved.status_code == 200
    body = saved.json()
    assert body["href"] == "#new/plan/" + body["proposal_id"] and body["revision"] == 1 and body["team_versions"] == []
    stored = env.services.plan_proposals.get(body["proposal_id"])
    assert stored["status"] == "proposed" and stored["created_by"] == "user:operator" and stored["created_channel"] == "gui"
    assert [task["workflow"] for task in stored["proposal"]["tasks"]] == ["summarize_document", "review_checklist"]
    assert env.client.get("/gui/api/runs?limit=50", headers=env.auth).json() == runs_before
    assert env.services.tasks.list_tasks(group_id=draft["group_id"]) == []


def test_role_bundle_without_team_proposes_team(tmp_path):
    made = make(tmp_path, role_bundle(tmp_path))
    try:
        draft = made.new_session.draft_plan("Summarize the document in plain language")
        assert draft["teams"] == [{"bundle": "general", "roles": ["analyst", "writer"], "action": "propose"}]
        assert made.teams.approved("general") is None
        saved = made.new_session.save_plan(draft)
        assert len(saved["team_versions"]) == 1 and saved["team_errors"] == []
        version = made.teams.get(saved["team_versions"][0])
        assert version["status"] == "proposed" and version["bundle"] == "general"
        assert made.teams.approved("general") is None
        proposal = made.plan_proposals.get(saved["proposal_id"])
        assert proposal["status"] == "proposed" and proposal["blockers"]
        again = made.new_session.save_plan(draft)       # a second save reuses the version already waiting
        assert again["team_versions"] == saved["team_versions"]
    finally:
        made.runs.close()


def test_tampered_draft_rejected(env):
    draft = env.client.post("/gui/api/new-session/plan", headers=env.auth, json={"text": TWO_PARTS}).json()
    draft["proposal"]["tasks"][0]["workflow"] = "no_such_workflow"
    answer = env.client.post("/gui/api/new-session/plan/save", headers=env.auth, json={"draft": draft, "confirmed": True})
    assert answer.status_code == 422
    forged = {"schema": "maf.new-session-plan/1", "proposal": {"schema": "x"}}
    answer = env.client.post("/gui/api/new-session/plan/save", headers=env.auth, json={"draft": forged, "confirmed": True})
    assert answer.status_code == 422
    assert env.services.plan_proposals.list() == []


def test_tampered_teams_in_the_draft_are_ignored(tmp_path):
    made = make(tmp_path, role_bundle(tmp_path))
    try:
        draft = made.new_session.draft_plan("Summarize the document in plain language")
        draft["teams"] = []
        assert len(made.new_session.save_plan(draft)["team_versions"]) == 1
    finally:
        made.runs.close()


class CountingAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def generate_structured(self, **kwargs):
        self.calls += 1
        return super().generate_structured(**kwargs)


def test_cloud_planner_needs_confirmation(tmp_path):
    adapter = CountingAdapter()
    made = make(tmp_path, adapter=adapter, gui_new_session_model="cloud_fast", egress_default="off")
    try:
        held = made.new_session.draft_plan(TWO_PARTS)
        assert held["reason"] == "cloud_confirmation_required" and held["tier"] == "cloud" and "proposal" not in held
        interpreted = made.new_session.interpret(TWO_PARTS)
        assert interpreted["reason"] == "cloud_confirmation_required"
        assert adapter.calls == 0
        assert made.plan_proposals.list() == []
        confirmed = made.new_session.draft_plan(TWO_PARTS, confirm_cloud=True)
        assert confirmed["schema"] == "maf.new-session-plan/1"
    finally:
        made.runs.close()


def test_plan_routes_reject_extra_fields_and_need_a_token(env):
    assert env.client.post("/gui/api/new-session/plan", headers=env.auth, json={"text": "x", "extra": 1}).status_code == 422
    assert env.client.post("/gui/api/new-session/plan", headers=env.auth, json={"text": ""}).status_code == 422
    assert env.client.post("/gui/api/new-session/plan", json={"text": "x"}).status_code in (401, 403)
    assert env.client.post("/gui/api/new-session/plan/save", json={"draft": {}, "confirmed": True}).status_code in (401, 403)
