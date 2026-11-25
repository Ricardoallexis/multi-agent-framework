# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ESC-T02: office plans and session interpretation stay inside their office."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import bootstrap, cli
from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.offices.plan import default_plan_template, is_office_group, office_group_id, office_privacy
from multiagent.offices.schema import load_office
from multiagent.planning import _GROUP
from multiagent.planning import model as planning_model
from multiagent.services import ApplicationServices, ServiceError
from test_offices_install import write_office

ROOT = Path(__file__).resolve().parents[1]
GENERAL = ROOT / "examples" / "bundles" / "general"
AREA_IDS = ("reception", "proposal", "follow-up")
DEFAULT_PLAN = [
    {"area": "reception", "workflow": "spec_review"},
    {"area": "proposal", "workflow": "spec_review", "after": ["reception"]},
]
NORMAL = {"reception": {"sensitivity": "normal"}, "proposal": {"sensitivity": "normal"},
          "follow-up": {"local_only": True}}
OUTPUT = {"PlanningOutput": {"tasks": [
    {"id": "plan-1", "title": "Review the specification", "depends_on": [],
     "workflow": "spec_review", "bundle": "demo__reception", "inputs": {"spec_text": "A synthetic request."}},
], "rationale": "Synthetic plan"}}


class CountingAdapter(FakeAdapter):
    def __init__(self, provider="gemini", sample_outputs=None):
        super().__init__(sample_outputs=sample_outputs or OUTPUT)
        self.provider = provider
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.calls.append(spec.id)
        self.prompts.append(prompt)
        return super().generate_structured(spec=spec, prompt=prompt, output_schema=output_schema,
                                           requires_web=requires_web, effort=effort)


def demo_folder(tmp_path: Path, *, areas: tuple[str, ...] = AREA_IDS, plan=DEFAULT_PLAN,
                overrides: dict | None = None) -> Path:
    privacy = {area: dict(NORMAL.get(area, {"sensitivity": "normal"})) for area in areas}
    privacy.update(overrides or {})
    return write_office(tmp_path / "demo-source", office_id="demo", areas=areas, plan=plan, overrides=privacy)


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(name="local", *, model="local_default", adapter=None, mock=True, bundles=None):
        settings = Settings(
            _env_file=None, local_dir=tmp_path / name, worker_enabled=False, mock_mode=mock,
            gemini_api_key="", openai_api_key="", openai_enabled=False,
            gui_token_file=tmp_path / f"{name}-gui-token", gui_new_session_model=model,
            egress_default="off",
        )
        overrides = None
        if adapter is not None:
            overrides = {"fake" if mock else ("gemini" if model.startswith("cloud") else "ollama"): adapter}
        services = ApplicationServices.create(settings, bundles=bundles, dry_run=mock,
                                              adapter_overrides=overrides)
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def install_demo(services: ApplicationServices, tmp_path: Path, **options) -> Path:
    folder = demo_folder(tmp_path, **options)
    services.offices.import_folder(folder, actor="user:test")
    return folder


def allow_sensitive_local_model(monkeypatch, tmp_path: Path) -> None:
    catalog_dir = tmp_path / "catalog"
    shutil.copytree(ROOT / "catalog", catalog_dir)
    model_path = catalog_dir / "models.yaml"
    document = yaml.safe_load(model_path.read_text(encoding="utf-8"))
    document["models"]["local_default"]["allowed_data_classes"] = ["non_sensitive", "sensitive"]
    model_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "Catalog", lambda settings: Catalog(settings, catalog_dir=catalog_dir))


def test_planner_never_sends_a_local_only_office_request_to_the_cloud(world):
    start, tmp = world
    adapter = CountingAdapter("gemini")
    services = start("cloud-protected", model="cloud_fast", adapter=adapter, mock=False)
    install_demo(services, tmp)
    draft = services.new_session.draft_plan("Summarize the specification and then review it",
                                            confirm_cloud=True, office="demo")
    assert adapter.calls == []
    assert draft["planner"] == "deterministic" and draft["fallback_reason"] == "office_not_normal"
    assert [task["bundle"] for task in draft["tasks"]] == ["demo__reception", "demo__proposal"]


def test_interpret_never_sends_a_local_only_office_request_to_the_cloud(world):
    start, tmp = world
    adapter = CountingAdapter("gemini")
    services = start("cloud-interpret", model="cloud_fast", adapter=adapter, mock=False)
    install_demo(services, tmp)
    result = services.new_session.interpret("Summarize the specification", confirm_cloud=True, office="demo")
    assert adapter.calls == []
    assert result["source"] == "keywords" and result["reason"] == "office_not_normal"


def test_default_plan_keeps_parallel_items(world):
    start, tmp = world
    parallel_services = start("parallel")
    install_demo(parallel_services, tmp, areas=("reception", "proposal"),
                 plan=[{"area": "reception", "workflow": "spec_review"},
                       {"area": "proposal", "workflow": "spec_review"}])
    parallel = parallel_services.new_session.draft_plan("Request", office="demo")
    assert [task["depends_on"] for task in parallel["tasks"]] == [[], []]

    ordered_services = start("ordered")
    install_demo(ordered_services, tmp, areas=("reception", "proposal"),
                 plan=[{"area": "reception", "workflow": "spec_review"},
                       {"area": "proposal", "workflow": "spec_review", "after": ["reception"]}])
    ordered = ordered_services.new_session.draft_plan("Request", office="demo")
    assert ordered["tasks"][1]["depends_on"] == ["demo-reception"]


def test_request_goes_to_the_first_required_text_input(tmp_path):
    root = demo_folder(tmp_path, areas=("reception", "proposal"), plan=DEFAULT_PLAN)
    definition = load_office(root)
    template = default_plan_template(
        definition, "A synthetic request",
        lambda _workflow, bundle: "spec_text" if bundle == "demo__reception" else None,
    )
    assert template is not None
    assert template["stages"][0]["inputs"] == {"spec_text": "A synthetic request"}
    assert template["stages"][1]["inputs"] == {}


def test_office_group_id():
    group_id = office_group_id("demo")
    assert re.fullmatch(r"office-demo-[0-9a-f]{8}", group_id)
    assert _GROUP.fullmatch(group_id)
    longest = office_group_id("a" * 40)
    assert len(longest) <= 56 and _GROUP.fullmatch(longest)
    assert is_office_group(group_id, "demo")
    assert not is_office_group(group_id, "demo-x")


def test_office_privacy():
    assert office_privacy(["non_sensitive", "non_sensitive"]) == "normal"
    assert office_privacy(["non_sensitive", None]) == "unset"
    assert office_privacy([None, "sensitive"]) == "protected"
    assert office_privacy(["restricted"]) == "protected"


def test_catalog_is_limited_to_the_office(world):
    start, tmp = world
    adapter = CountingAdapter("gemini")
    services = start("catalog", model="cloud_fast", adapter=adapter, mock=False, bundles={"outside": GENERAL})
    install_demo(services, tmp, areas=("reception", "proposal"))
    services.new_session.draft_plan("Summarize the specification", confirm_cloud=True, office="demo")
    catalog_line = next(line for line in adapter.prompts[0].splitlines() if line.startswith("CATALOG_JSON: "))
    catalog = json.loads(catalog_line.removeprefix("CATALOG_JSON: "))
    assert catalog and all(item["bundle"].startswith("demo__") for item in catalog)
    assert all(item["bundle"] != "outside" for item in catalog)


def test_model_plan_outside_the_office_is_rejected(world):
    start, tmp = world
    output = {"PlanningOutput": {"tasks": [
        {"id": "plan-1", "title": "Outside task", "depends_on": [],
         "workflow": "summarize_document", "bundle": "outside", "inputs": {}},
    ]}}
    services = start("outside-plan", model="cloud_fast",
                     adapter=CountingAdapter("gemini", output), mock=False, bundles={"outside": GENERAL})
    install_demo(services, tmp, areas=("reception", "proposal"))
    with pytest.raises(ServiceError) as caught:
        services.new_session.draft_plan("Summarize the specification", confirm_cloud=True, office="demo")
    assert caught.value.status == 422
    assert caught.value.details["reason"] == "office_plan_outside_office"
    assert caught.value.details["task"]["bundle"] == "outside"


def test_local_model_plans_with_the_office_class(world, monkeypatch):
    start, tmp = world
    allow_sensitive_local_model(monkeypatch, tmp)
    adapter = CountingAdapter("ollama")
    services = start("local-sensitive", model="local_default", adapter=adapter, mock=False)
    install_demo(services, tmp, areas=("reception", "proposal"),
                 overrides={"proposal": {"sensitivity": "sensitive"}})

    observed: list[str | None] = []
    build_guards = planning_model.build_call_guards

    class RecordingGuards:
        def __init__(self, chain):
            self.chain = chain

        def check(self, call, run_state):
            observed.append(call.data_class)
            return self.chain.check(call, run_state)

    monkeypatch.setattr(planning_model, "build_call_guards",
                        lambda *args, **kwargs: RecordingGuards(build_guards(*args, **kwargs)))
    draft = services.new_session.draft_plan("Summarize the specification", office="demo")
    assert len(adapter.calls) == 1 and observed == ["sensitive"]
    assert draft["planner"] == "model" and draft["fallback_reason"] is None


def test_unset_level_asks_before_a_cloud_plan(world):
    start, tmp = world
    adapter = CountingAdapter("gemini")
    services = start("cloud-unset", model="cloud_fast", adapter=adapter, mock=False)
    install_demo(services, tmp, areas=("reception", "proposal"),
                 overrides={"proposal": {"sensitivity": None}})
    held = services.new_session.draft_plan("Summarize the specification", office="demo")
    assert held["reason"] == "cloud_confirmation_required"
    with pytest.raises(ServiceError) as caught:
        services.new_session.draft_plan("Summarize the specification", confirm_cloud=True, office="demo")
    assert caught.value.status == 409
    assert caught.value.details == {"reason": "office_level_required", "office": "demo", "area": "proposal"}
    assert adapter.calls == []


def test_normal_office_with_cloud_confirmation(world):
    start, tmp = world
    adapter = CountingAdapter("gemini")
    services = start("cloud-normal", model="cloud_fast", adapter=adapter, mock=False)
    install_demo(services, tmp, areas=("reception", "proposal"))
    draft = services.new_session.draft_plan("Summarize the specification", confirm_cloud=True, office="demo")
    assert len(adapter.calls) == 1
    assert draft["planner"] == "model" and draft["fallback_reason"] is None


def test_no_default_plan_and_nothing_matches(world):
    start, tmp = world
    services = start("no-default")
    install_demo(services, tmp, areas=("reception", "proposal"), plan=None)
    with pytest.raises(ServiceError) as caught:
        services.new_session.draft_plan("filler xyzzy", office="demo")
    assert caught.value.status == 409
    assert caught.value.details["reason"] == "office_plan_unavailable"
    assert caught.value.details["fix"] == "This office has no default plan: ask for one thing at a time"


def test_draft_saves_nothing_and_save_needs_confirmation(world):
    start, tmp = world
    services = start("save")
    install_demo(services, tmp)
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    before = services.plan_proposals.list()
    draft = services.offices.plan_draft("demo", "Summarize the specification")
    assert services.plan_proposals.list() == before == []
    assert draft["office"] == "demo" and draft["fallback_reason"] == "mock"
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        held = client.post("/gui/api/new-session/plan/save", headers=headers, json={"draft": draft})
        assert held.status_code == 409 and services.plan_proposals.list() == []
        saved = client.post("/gui/api/new-session/plan/save", headers=headers,
                            json={"draft": draft, "confirmed": True})
    assert saved.status_code == 200
    proposal = services.plan_proposals.get(saved.json()["proposal_id"])
    assert proposal["status"] == "proposed" and is_office_group(proposal["group_id"], "demo")


def test_without_office_nothing_changes(world):
    start, _tmp = world
    services = start("without-office", bundles={"general": GENERAL})
    draft = services.new_session.draft_plan("Summarize the document in plain language")
    interpreted = services.new_session.interpret("Summarize the document in plain language")
    assert draft["planner"] == "deterministic" and draft["group_id"].startswith("chat-")
    assert "office" not in draft and "fallback_reason" not in draft
    assert interpreted["source"] == "model" and interpreted["reason"] == ""


def test_new_session_office_routes_return_not_found_for_missing_offices(world):
    start, tmp = world
    services = start("gui-office", model="cloud_fast")
    install_demo(services, tmp)
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        interpret = client.post("/gui/api/new-session/interpret", headers=headers,
                                json={"text": "Summarize the specification", "office": "demo"})
        missing_interpret = client.post("/gui/api/new-session/interpret", headers=headers,
                                        json={"text": "Summarize the specification", "office": "missing"})
        plan = client.post("/gui/api/new-session/plan", headers=headers,
                           json={"text": "Summarize the specification", "office": "missing"})
        assert interpret.status_code == 200 and interpret.json()["reason"] == "office_not_normal"
        assert missing_interpret.status_code == 404 and plan.status_code == 404


def test_plan_draft_route_and_cli_parity(world, monkeypatch, capsys):
    start, tmp = world
    services = start("parity")
    install_demo(services, tmp)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        expected = client.post("/api/v1/offices/demo/plan-draft",
                               json={"text": "Summarize the specification"}).json()
        assert client.post("/api/v1/offices/demo/plan-draft",
                           json={"text": "Summarize the specification", "unexpected": True}).status_code == 422

        class ClientContext:
            def __enter__(self):
                return client

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(cli, "api_client", ClientContext)
        cli.main(["office", "plan-draft", "demo", "Summarize the specification", "--json"])
        actual = json.loads(capsys.readouterr().out)
    assert actual == expected
