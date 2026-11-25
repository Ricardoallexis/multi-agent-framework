# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""MVP-T02: end-to-end office metrics using the shipped templates and temporary data."""
from __future__ import annotations

import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.adapters.fake import FakeAdapter
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from test_check_private_terms import _commit_file, _init_repo, _load_checker, _write_terms
from test_office_plan import CountingAdapter
from test_office_sensitivity import CloudDouble
from test_offices_install import ACTOR

ROOT = Path(__file__).resolve().parents[1]
CAFE = ROOT / "examples" / "offices" / "cafe"
SCHOOL = ROOT / "examples" / "offices" / "school"
GENERAL = ROOT / "examples" / "bundles" / "general"
LOCAL_OUTPUTS = {
    "WeeklyMenu": {
        "title": "Sample weekly menu",
        "menu": "Monday: a hot dish and fruit.",
    },
    "ProgressNote": {"note": "Sample progress note for review."},
    "Notice": {"title": "Sample notice", "body": "Sample text for review."},
    "Reply": {"reply": "Sample reply for review."},
}
class NamedLocalAdapter(FakeAdapter):
    """A fake local model that reports the model it ran, as a real local adapter does (the mock reports «fixture»)."""

    def generate_structured(self, *, spec, **kwargs):
        response = super().generate_structured(spec=spec, **kwargs)
        return replace(response, model=spec.model)


TWO_TASK_PLAN = {
    "tasks": [
        {"id": "plan-1", "title": "Prepare the weekly menu", "depends_on": [],
         "workflow": "weekly_menu", "bundle": "cafe__menu",
         "inputs": {"order": "Monday to Friday menu with a hot dish and fruit."}},
        {"id": "plan-2", "title": "Write the notice", "depends_on": ["plan-1"],
         "workflow": "write_notice", "bundle": "cafe__notices",
         "inputs": {"topic": "This week's menu."}},
    ],
    "rationale": "Synthetic two-task plan.",
}


def approve_cloud_permission(services: ApplicationServices, approval_id: str) -> None:
    """The person's approval, like ``test_cloud_permission.approve_permission`` but without the app's lifespan,
    whose shutdown would close the services this test still needs to run the plan."""
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    client = TestClient(app, base_url="http://localhost")
    assert client.post("/gui/api/approvals/session", headers=headers,
                       json={"code": "synthetic-start-code"}).status_code == 200
    challenge = client.post(f"/gui/api/approvals/{approval_id}/challenge", headers=headers, json={})
    assert challenge.status_code == 200
    decided = client.post(f"/gui/api/approvals/{approval_id}/decision", headers=headers,
                          json={"decision": "approve", "code": challenge.json()["code"]})
    assert decided.status_code == 200, decided.text


def prefer_cloud_in_every_workflow(office: Path, areas: tuple[str, ...]) -> None:
    """Like ``test_office_sensitivity.prefer_cloud``, but for every workflow of each area (the school areas differ)."""
    for area in areas:
        folder = office / "areas" / area
        (folder / "catalog" / "models.yaml").write_text(
            "models:\n"
            "  local_default:\n    provider: ollama\n    model: qwen3:8b\n    tier: local\n    structured_output: true\n"
            "    web: false\n    num_ctx: 4096\n    keep_alive: 15m\n    thinking: false\n"
            "    input_cost_per_million: 0.0\n    output_cost_per_million: 0.0\n    free_tier_eligible: true\n"
            "  cloud_fast:\n    provider: gemini\n    model: synthetic-cloud\n    tier: cloud\n    structured_output: true\n"
            "    web: false\n    input_cost_per_million: 0.0\n    output_cost_per_million: 0.0\n"
            "    free_tier_eligible: true\n    allowed_data_classes: [non_sensitive]\n", encoding="utf-8")
        flows = sorted((folder / "workflows").glob("*.yaml"))
        assert flows
        for flow in flows:
            flow.write_text(
                flow.read_text(encoding="utf-8").replace("preferred_model: local_default", "preferred_model: cloud_fast"),
                encoding="utf-8")


@pytest.fixture
def service_factory(tmp_path):
    opened: list[ApplicationServices] = []

    def build(name: str, *, mock: bool = True, model: str = "local_default",
              adapters: dict | None = None, bundles: dict | None = None) -> ApplicationServices:
        settings = Settings(
            _env_file=None,
            local_dir=tmp_path / name,
            worker_enabled=False,
            mock_mode=mock,
            bundle_scan="off",
            gemini_api_key="",
            openai_api_key="",
            openai_enabled=False,
            gui_token_file=tmp_path / f"{name}-gui-token",
            gui_new_session_model=model,
        )
        services = ApplicationServices.create(
            settings,
            bundles=bundles,
            dry_run=mock,
            adapter_overrides=adapters,
        )
        opened.append(services)
        return services

    yield build
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


def _import(services: ApplicationServices, root: Path) -> dict:
    return services.offices.import_folder(root, actor=ACTOR)


def _approve_teams(services: ApplicationServices, result: dict) -> None:
    for team in result["teams"]:
        services.teams.approve(team["version_id"], actor=ACTOR)


def _scene_member(services: ApplicationServices, area_id: str, member_id: str) -> dict:
    scene = services.offices.scene("cafe")
    area = next(item for item in scene["areas"] if item["id"] == area_id)
    return next(item for item in area["members"] if item["id"] == member_id)


def _menu_run(services: ApplicationServices) -> dict:
    run = services.runs.create(
        RunRequest(
            project_name="Synthetic weekly menu",
            workflow_id="weekly_menu",
            inputs={"order": "Monday to Friday menu with a hot dish and fruit."},
        ),
        bundle="cafe__menu",
    )
    runtime = services.runs._bundle_runtime("cafe__menu")
    assert runtime is not None and runtime.worker is not None
    assert runtime.worker.process_once()
    result = services.runs.get(run["id"])
    assert result["status"] == "waiting_human" and result["waiting_reason"] == "review"
    assert result["artifacts"]
    return result


def test_metric2_same_template_three_ways(service_factory):
    mock = service_factory("metric2-mock")
    mock_office = _import(mock, CAFE)
    mock_menu = next(item for item in mock_office["teams"] if item["bundle"] == "cafe__menu")
    mock.teams.approve(mock_menu["version_id"], actor=ACTOR)
    mock_run = _menu_run(mock)
    mock_member = _scene_member(mock, "menu", "menu_drafter")
    assert mock_member["state"] == "review"

    local = service_factory(
        "metric2-local",
        mock=False,
        adapters={"ollama": NamedLocalAdapter(sample_outputs=LOCAL_OUTPUTS)},
    )
    local_office = _import(local, CAFE)
    local_menu = next(item for item in local_office["teams"] if item["bundle"] == "cafe__menu")
    local.teams.approve(local_menu["version_id"], actor=ACTOR)
    local_run = _menu_run(local)
    local_member = _scene_member(local, "menu", "menu_drafter")
    assert local_member["state"] == "review"
    assert mock_run["id"] != local_run["id"]
    assert mock_member["worker"] != local_member["worker"]


def test_metric3_scene_follows_a_two_task_plan(service_factory, monkeypatch):
    adapter = FakeAdapter(sample_outputs={**LOCAL_OUTPUTS, "PlanningOutput": TWO_TASK_PLAN})
    services = service_factory("metric3", mock=False, adapters={"ollama": adapter})
    imported = _import(services, CAFE)
    _approve_teams(services, imported)

    draft = services.offices.plan_draft("cafe", "Prepare the weekly menu and then write the notice.")
    menu_task = next(task for task in draft["tasks"] if task["bundle"] == "cafe__menu")
    notice_task = next(task for task in draft["tasks"] if task["bundle"] == "cafe__notices")
    services.tasks.create_group(
        draft["group_id"], draft["goal"], actor=ACTOR, authorized=True, default_requires="submitted"
    )
    proposal = services.plan_proposals.save(draft["proposal"], actor=ACTOR, channel="test")
    proposal_id = proposal["id"]
    assert services.offices.scene("cafe")["orchestrator"]["state"] == "review"

    approved = services.plan_proposals.approve(
        proposal_id, actor=ACTOR, channel="test", expected_revision=1
    )
    first = next(task for task in approved["plan_status"]["tasks"] if task["id"] == menu_task["id"])
    first_run_id = first["run"]["id"]
    runtime = services.runs._bundle_runtime("cafe__menu")
    assert runtime is not None and runtime.worker is not None

    entered = threading.Event()
    resume = threading.Event()
    original_generate = adapter.generate_structured

    def paused_generate(**kwargs):
        entered.set()
        if not resume.wait(timeout=10):
            raise TimeoutError("The synthetic model was not released.")
        return original_generate(**kwargs)

    monkeypatch.setattr(adapter, "generate_structured", paused_generate)
    process_result: list[bool] = []
    process_errors: list[BaseException] = []

    def process_first_run():
        try:
            process_result.append(runtime.worker.process_once())
        except BaseException as exc:
            process_errors.append(exc)

    worker = threading.Thread(target=process_first_run)
    worker.start()
    try:
        assert entered.wait(timeout=10)
        running_scene = services.offices.scene("cafe")
        assert running_scene["orchestrator"]["state"] == "doing"
        menu_agent = next(
            member for member in next(area for area in running_scene["areas"] if area["id"] == "menu")["members"]
            if member["id"] == "menu_drafter"
        )
        assert menu_agent["state"] == "doing"
    finally:
        resume.set()
        worker.join(timeout=10)
    assert not worker.is_alive() and not process_errors and process_result == [True]

    first_review = services.runs.get(first_run_id)
    assert first_review["status"] == "waiting_human" and first_review["artifacts"]
    assert _scene_member(services, "menu", "menu_drafter")["state"] == "review"
    completed = services.runs.approve(first_run_id, by=ACTOR, channel="test")
    assert completed["status"] == "completed"

    approved_again = services.plan_proposals.get(proposal_id)
    second_task = next(task for task in approved_again["proposal"]["tasks"] if task["id"] == notice_task["id"])
    status = services.plans.status(approved_again["proposal"]["plan"])
    second = next(task for task in status["tasks"] if task["id"] == second_task["id"])
    assert second["run"] is not None
    advanced = [
        event for event in services.tasks.events(group_id=draft["group_id"])
        if event["type"] == "plan.advanced"
    ]
    assert any(
        any(item["task_id"] == notice_task["id"] for item in event["payload"]["advanced"])
        for event in advanced
    )
    assert _scene_member(services, "menu", "menu_drafter")["state"] == "done"

    second_runtime = services.runs._bundle_runtime("cafe__notices")
    assert second_runtime is not None and second_runtime.worker is not None
    assert second_runtime.worker.process_once()
    notice_review = services.runs.get(second["run"]["id"])
    assert notice_review["status"] == "waiting_human" and notice_review["artifacts"]
    assert services.runs.approve(notice_review["id"], by=ACTOR, channel="test")["status"] == "completed"
    # A task is «done» for the scene only when the person accepts it (the run's end only submits it).
    assert services.offices.scene("cafe")["orchestrator"]["state"] == "doing"
    for task_id in (menu_task["id"], notice_task["id"]):
        services.tasks.transition(task_id, "accept", actor=ACTOR)
    final_scene = services.offices.scene("cafe")
    assert final_scene["orchestrator"]["state"] == "done"
    assert next(
        member for member in next(area for area in final_scene["areas"] if area["id"] == "menu")["members"]
        if member["id"] == "menu_drafter"
    )["state"] == "done"


def test_metric4_shared_area_keeps_data_apart(service_factory):
    services = service_factory("metric4")
    _import(services, CAFE)
    _import(services, SCHOOL)
    school_data = services.settings.data_dir / "bundles" / "school__notices"
    school_data.mkdir(parents=True)

    removed = services.offices.remove_area("school", "notices", actor=ACTOR)
    assert removed["kept_data"] is True and school_data.is_dir()
    exported = services.offices.export("cafe", area="notices")
    imported = services.offices.import_zip(exported["data"], actor=ACTOR, into="school")
    assert "notices" in [area["id"] for area in imported["office"]["areas"]]

    def run_notice(bundle: str, project: str, topic: str) -> dict:
        run = services.runs.create(
            RunRequest(project_name=project, workflow_id="write_notice", inputs={"topic": topic}),
            bundle=bundle,
        )
        runtime = services.runs._bundle_runtime(bundle)
        assert runtime is not None and runtime.worker is not None
        assert runtime.worker.process_once()
        finished = services.runs.get(run["id"])
        assert finished["status"] == "waiting_human" and finished["artifacts"]
        return finished

    cafe_run = run_notice("cafe__notices", "Cafe synthetic data", "Sample opening hours")
    school_run = run_notice("school__notices", "School synthetic data", "Sample meeting")
    cafe_data = services.settings.data_dir / "bundles" / "cafe__notices"
    assert cafe_data.is_dir() and school_data.is_dir() and cafe_data != school_data
    since = "1970-01-01T00:00:00+00:00"
    cafe_ids = {item["id"] for item in services.runs.recent_runs("cafe__notices", since=since)}
    school_ids = {item["id"] for item in services.runs.recent_runs("school__notices", since=since)}
    assert cafe_run["id"] in cafe_ids and school_run["id"] not in cafe_ids
    assert school_run["id"] in school_ids and cafe_run["id"] not in school_ids


def test_metric5_without_offices_nothing_changes(service_factory, monkeypatch):
    services = service_factory("metric5", bundles={"general": GENERAL})
    office_file = services.settings.data_dir / "installed" / "offices.json"
    scene_calls: list[str] = []
    original_scene = services.offices.scene

    def record_scene(office_id: str, **kwargs):
        scene_calls.append(office_id)
        return original_scene(office_id, **kwargs)

    monkeypatch.setattr(services.offices, "scene", record_scene)
    run = services.runs.create(
        RunRequest(project_name="Existing package", workflow_id="ask_once", inputs={"task": "Say hello."}),
        bundle="general",
    )
    assert services.runs._bundle_runtime("general").worker.process_once()
    assert services.runs.get(run["id"])["status"] == "completed"
    # The app's shutdown closes the services it was given, so the HTTP check goes last.
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/offices").json() == {"offices": []}
    assert not office_file.exists() and scene_calls == []


def test_metric6_local_only_never_leaves_even_in_a_plan(service_factory, tmp_path):
    school_cloud = tmp_path / "school-cloud-template"
    shutil.copytree(SCHOOL, school_cloud)
    prefer_cloud_in_every_workflow(school_cloud, ("follow-up", "notices"))
    local = FakeAdapter(sample_outputs=LOCAL_OUTPUTS)
    cloud = CloudDouble("gemini")
    services = service_factory(
        "metric6-plan",
        mock=False,
        adapters={"ollama": local, "gemini": cloud},
    )
    _import(services, school_cloud)

    permission = services.offices.request_cloud_permission("school", "notices")
    approve_cloud_permission(services, permission["approval_id"])
    services.offices.grant_cloud_permission(
        "school", "notices", approval_id=permission["approval_id"], actor=ACTOR
    )
    group_id = "school-privacy-plan"
    services.tasks.create_group(group_id, "Synthetic school plan", actor=ACTOR, authorized=True,
                                default_requires="submitted")
    for task_id, title in (("follow", "Restricted follow-up"), ("notice", "Normal notice")):
        services.tasks.create_task(group_id, task_id, title, actor=ACTOR)
    plan = {
        "schema": "multiagent.plan/1",
        "group_id": group_id,
        "tasks": {
            "follow": {
                "workflow": "progress_note",
                "bundle": "school__follow-up",
                "inputs": {"observations": "Sample observation.", "student": "Student A"},
            },
            "notice": {
                "workflow": "write_notice",
                "bundle": "school__notices",
                "inputs": {"topic": "Sample notice."},
            },
        },
    }
    status = services.plans.advance(plan, actor=ACTOR)
    runs = {task["id"]: task["run"] for task in status["tasks"]}
    follow = services.runs.get(runs["follow"]["id"])
    notice = services.runs.get(runs["notice"]["id"])
    assert follow["data_class"] == "restricted" and follow["request"]["sensitive"] is True
    assert notice["data_class"] == "non_sensitive" and notice["request"]["sensitive"] is False

    runtime = services.runs._bundle_runtime("school__follow-up")
    assert runtime is not None and runtime.worker is not None and runtime.worker.process_once()
    assert services.runs.get(follow["id"])["status"] == "waiting_human"
    assert cloud.calls == []


def test_metric6_planner_keeps_the_school_local(service_factory):
    cloud = CountingAdapter("gemini")
    services = service_factory(
        "metric6-planner",
        mock=False,
        model="cloud_fast",
        adapters={"gemini": cloud},
    )
    _import(services, SCHOOL)

    draft = services.offices.plan_draft(
        "school", "Prepare an activity and a progress note.", confirm_cloud=True
    )
    assert draft["fallback_reason"] == "office_not_normal"
    assert draft["planner"] == "deterministic" and cloud.calls == []


def test_metric6_private_terms_check_fails_with_a_temp_list(tmp_path, monkeypatch):
    checker = _load_checker()
    repo = _init_repo(tmp_path, "metric6-repo")
    term = "termino-de-prueba-xyz"
    terms = _write_terms(tmp_path, [term], "metric6-terms.txt")
    first = _commit_file(repo, "README.md", "Synthetic public text.\n", "base")
    second = _commit_file(repo, "README.md", f"Synthetic public text.\n{term}\n", "add synthetic term")
    monkeypatch.chdir(repo)

    args = ["--terms", str(terms), "--range", f"{first}..{second}"]
    assert checker.main(args) == 1
    terms.write_text("palabra-sin-coincidencias-abc\n", encoding="utf-8")
    assert checker.main(args) == 0


def test_offices_doc_has_every_section():
    doc = ROOT / "docs" / "OFFICES.md"
    sections = [line.removeprefix("## ") for line in doc.read_text(encoding="utf-8").splitlines()
                if line.startswith("## ")]
    assert sections == [
        "Format",
        "Contracts as data",
        "Install, remove and reimport",
        "Requirements",
        "Sensitivity",
        "The install card",
        "Export and import",
        "Scene",
        "Orchestrator",
        "Templates",
    ]
