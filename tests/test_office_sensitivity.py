# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""SEN-T01: the privacy level of each office area reaches its runs and its plans (invented offices, doubles only).

``normal`` is ``non_sensitive``, ``sensitive`` is ``sensitive`` and «Solo local» is ``restricted``; an area without a level
is ``None`` and asks before the cloud. Raising a level works from the API and the CLI; lowering one needs the one-use
approval of «Privacidad» and has no route in ``/api/v1``. The cloud double counts its calls; nothing leaves 127.0.0.1.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.contracts import ExecutionMode
from multiagent.gui.runs import _routing_preview
from multiagent.gui.security import APPROVAL_COOKIE
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.offices import DATA_CLASS_OF, LEVELS, OFFICES_SCHEMA, PRIVACY_NOTE
from test_offices_install import ACTOR, invoke_cli, make_area, make_settings, write_office

INPUTS = {"spec_text": "A synthetic specification."}
DEMO = {"propuesta": {"sensitivity": "sensitive"}, "seguimiento": {"local_only": True}, "avisos": {"sensitivity": "normal"}}
DEMO_AREAS = ("recepcion", "propuesta", "seguimiento", "avisos")


def request(**fields) -> RunRequest:
    return RunRequest(project_name="Sensitivity", workflow_id="spec_review", inputs=INPUTS, **fields)


def failure(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


def reason(error: ServiceError) -> str:
    return error.details["reason"]


def make_demo(tmp: Path, **kwargs) -> Path:
    return write_office(tmp / "demo", office_id="demo", areas=DEMO_AREAS, overrides=DEMO, **kwargs)


def declare(root: Path, **top) -> None:
    """Add keys at the top of an office.yaml (``local_only`` of the whole office, say)."""
    path = root / "office.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.update(top)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


class CloudDouble(LLMAdapter):
    """A provider that answers the contract of the step and counts every call it receives."""

    def __init__(self, provider: str):
        self.provider, self.calls = provider, []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.calls.append(spec.id)
        parsed = output_schema.model_validate({"objective": "A synthetic objective", "key_findings": ["A synthetic finding"]})
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model, tokens_in=10, tokens_out=5)

    def health(self):
        return {"provider": self.provider, "available": True}


def prefer_cloud(office: Path, areas: tuple[str, ...]) -> None:
    """The steps of these areas prefer a cloud model that the area declares, so only the privacy keeps them local."""
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
        flow = folder / "workflows" / "spec_review.yaml"
        flow.write_text(flow.read_text(encoding="utf-8").replace("preferred_model: local_default", "preferred_model: cloud_fast"),
                        encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(name: str = "local", *, doubles: dict | None = None) -> ApplicationServices:
        root = tmp_path / name
        settings = make_settings(root)
        if doubles is not None:
            settings = settings.model_copy(update={"mock_mode": False})
        services = ApplicationServices.create(settings, dry_run=doubles is None,
                                              **({"adapter_overrides": doubles} if doubles is not None else {}))
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()


@pytest.fixture
def demo(world):
    start, tmp = world
    services = start()
    root = make_demo(tmp)
    services.offices.import_folder(root, actor=ACTOR)
    return services, root, tmp


def process(services: ApplicationServices, run: dict) -> dict:
    services.runs._bundle_runtime(run["bundle"]).worker.process_once()
    return services.runs.get(run["id"])


def privacy_of(services, office="demo") -> dict:
    return {area["id"]: area for area in services.offices.privacy(office)["areas"]}


def test_level_to_data_class(demo, world):
    services, root, tmp = demo
    offices = services.offices
    assert DATA_CLASS_OF == {"normal": "non_sensitive", "sensitive": "sensitive"} and LEVELS == ("normal", "sensitive")
    assert offices.area_class("demo__avisos") == "non_sensitive"
    assert offices.area_class("demo__propuesta") == "sensitive"
    assert offices.area_class("demo__seguimiento") == "restricted"
    assert offices.area_class("demo__recepcion") is None            # no level (Decision 15)
    assert offices.area_class("demo__nothing") is None and offices.area_class("plain") is None
    wide = write_office(tmp / "wide", office_id="wide", areas=("uno", "dos"), overrides={"uno": {"sensitivity": "normal"}})
    declare(wide, local_only=True)
    offices.import_folder(wide, actor=ACTOR)
    assert [offices.area_class(f"wide__{area}") for area in ("uno", "dos")] == ["restricted", "restricted"]
    assert {area["data_class"] for area in offices.privacy("wide")["areas"]} == {"restricted"}


def test_run_of_a_sensitive_area_is_sensitive(demo):
    services, _, _ = demo
    created = services.runs.create(request(sensitive=False), bundle="demo__propuesta")
    assert created["request"]["sensitive"] is True and created["data_class"] == "sensitive"
    again = services.runs.get(created["id"])
    assert again["request"]["sensitive"] is True and again["data_class"] == "sensitive"
    assert {item["id"]: item.get("data_class") for item in services.runs.list(limit=10)}[created["id"]] == "sensitive"
    normal = services.runs.create(request(), bundle="demo__avisos")
    assert normal["request"]["sensitive"] is False and normal["data_class"] == "non_sensitive"
    local = services.runs.create(request(), bundle="demo__seguimiento")
    assert local["request"]["sensitive"] is True and local["data_class"] == "restricted"


def test_local_only_refuses_cloud_and_human_guided(demo):
    services, _, _ = demo
    runs = services.runs
    for fields in ({"confirm_cloud": True}, {"execution_mode": "human_guided"},
                   {"step_modes": {"extract_requirements": "human_guided"}}):
        error = failure(runs.create, request(**fields), bundle="demo__seguimiento")
        assert (error.code, error.status, reason(error)) == ("invalid_request", 422, "area_local_only")
        assert error.details["office"] == "demo" and error.details["area"] == "seguimiento"
    assert [item for item in runs.list(limit=20) if item["bundle"] == "demo__seguimiento"] == []
    # the same requests in a sensitive area are not refused for being «Solo local»: the run is sensitive instead
    guided = runs.create(request(execution_mode="human_guided"), bundle="demo__propuesta")
    assert guided["request"]["sensitive"] is True


def test_local_only_never_reaches_a_cloud_model(world):
    start, tmp = world
    cloud, local = CloudDouble("gemini"), CloudDouble("ollama")
    services = start("cloud", doubles={"gemini": cloud, "ollama": local})
    root = make_demo(tmp)
    prefer_cloud(root, DEMO_AREAS)
    services.offices.import_folder(root, actor=ACTOR)
    # the control: a «normal» area with the person's confirmation does reach the double
    done = process(services, services.runs.create(request(confirm_cloud=True), bundle="demo__avisos"))
    assert done["status"] == "completed" and cloud.calls == ["cloud_fast"]
    cloud.calls.clear()
    # a «sensitive» area cannot confirm the cloud: the confirmation does nothing
    sensitive = process(services, services.runs.create(request(confirm_cloud=True), bundle="demo__propuesta"))
    assert sensitive["request"]["sensitive"] is True and cloud.calls == []
    # «Solo local»: the confirmation and the guided mode are refused, and a plain run never calls the cloud either
    for fields in ({"confirm_cloud": True}, {"execution_mode": "human_guided"}):
        assert reason(failure(services.runs.create, request(**fields), bundle="demo__seguimiento")) == "area_local_only"
    process(services, services.runs.create(request(), bundle="demo__seguimiento"))
    assert cloud.calls == []


def test_plan_task_carries_its_area_class(world):
    start, tmp = world
    cloud, local = CloudDouble("gemini"), CloudDouble("ollama")
    services = start("cloud", doubles={"gemini": cloud, "ollama": local})
    root = make_demo(tmp)
    prefer_cloud(root, DEMO_AREAS)
    services.offices.import_folder(root, actor=ACTOR)
    assert services.runs._bundle_runtime("demo__seguimiento") is not None
    assert services.runs._bundle_runtime("demo__avisos") is not None
    permission = services.offices.request_cloud_permission("demo", "avisos")
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    with TestClient(app, base_url="http://localhost") as client:
        decide_with_code(client, headers, permission["approval_id"])
    services.offices.grant_cloud_permission("demo", "avisos", approval_id=permission["approval_id"], actor=ACTOR)
    tasks = services.tasks
    tasks.create_group("plan", "Invented plan", actor="user", authorized=True, default_requires="accepted")
    tasks.create_task("plan", "keep", "Follow up", actor="user")
    tasks.create_task("plan", "notice", "Notices", actor="user")
    document = {"schema": "multiagent.plan/1", "group_id": "plan", "tasks": {
        "keep": {"workflow": "spec_review", "inputs": INPUTS, "bundle": "demo__seguimiento"},
        "notice": {"workflow": "spec_review", "inputs": INPUTS, "bundle": "demo__avisos"}}}
    status = services.plans.advance(document, actor="planner")
    runs = {task["id"]: task["run"]["id"] for task in status["tasks"] if task["run"]}
    assert set(runs) == {"keep", "notice"}
    kept = services.runs.get(runs["keep"])
    noticed = services.runs.get(runs["notice"])
    assert kept["request"]["sensitive"] is True and kept["data_class"] == "restricted"
    assert noticed["request"]["sensitive"] is False and noticed["data_class"] == "non_sensitive"
    services.runs._bundle_runtime("demo__seguimiento").worker.process_once()
    assert cloud.calls == [] and services.runs.get(runs["keep"])["status"] == "completed"   # «Solo local»: 0 cloud calls
    services.runs._bundle_runtime("demo__avisos").worker.process_once()
    assert cloud.calls == ["cloud_fast"]           # the control: the «normal» task of the same plan does use its cloud model


def test_reimport_never_lowers_a_level(demo):
    services, root, tmp = demo
    offices = services.offices
    offices.raise_level("demo", "recepcion", sensitivity="sensitive", actor="user:ana", channel="api")
    declare(root, local_only=True)
    offices.reimport_folder("demo", root, actor=ACTOR)           # the office now says «Solo local» for everything
    declare(root, local_only=False)
    write_office(tmp / "again", office_id="demo", areas=DEMO_AREAS,
                 overrides={"recepcion": {"sensitivity": "normal"}, "propuesta": {"sensitivity": "normal"},
                            "seguimiento": {}, "avisos": {"sensitivity": "normal"}})
    offices.reimport_folder("demo", tmp / "again", actor=ACTOR)  # declares everything lower
    levels = privacy_of(services)
    assert (levels["recepcion"]["sensitivity"], levels["recepcion"]["source"], levels["recepcion"]["set_by"]) == (
        "sensitive", "person", "user:ana")
    assert levels["recepcion"]["data_class"] == "restricted"      # the office-wide «Solo local» did not go away either
    assert levels["propuesta"]["sensitivity"] == "sensitive" and levels["seguimiento"]["local_only"] is True
    assert offices.privacy("demo")["local_only"] is True
    assert all(item["data_class"] == "restricted" for item in levels.values())
    assert offices.get("demo")["areas"][0]["sensitivity"] == "sensitive"      # `office show` reads the same levels
    # a higher declaration wins over what was there
    write_office(tmp / "higher", office_id="demo", areas=DEMO_AREAS, overrides={"avisos": {"sensitivity": "sensitive"}})
    offices.reimport_folder("demo", tmp / "higher", actor=ACTOR)
    assert privacy_of(services)["avisos"]["sensitivity"] == "sensitive"


def test_reimport_keeps_the_levels_when_the_office_is_unchanged(demo):
    services, root, _ = demo
    services.offices.raise_level("demo", "avisos", sensitivity="sensitive", actor=ACTOR)
    before = json.dumps(services.offices.privacy("demo"), sort_keys=True)
    result = services.offices.reimport_folder("demo", root, actor=ACTOR)
    assert result["changes"]["changed"] == [] and result["changes"]["added"] == []
    assert json.dumps(services.offices.privacy("demo"), sort_keys=True) == before


def test_unset_level_asks_before_cloud(demo):
    services, _, _ = demo
    error = failure(services.runs.create, request(confirm_cloud=True), bundle="demo__recepcion")
    assert (error.code, error.status, reason(error)) == ("invalid_state", 409, "office_level_required")
    assert (error.details["office"], error.details["area"]) == ("demo", "recepcion")
    assert [item for item in services.runs.list(limit=20) if item["bundle"] == "demo__recepcion"] == []
    created = services.runs.create(request(), bundle="demo__recepcion")      # a local run does not ask
    assert created["request"]["sensitive"] is False and "data_class" not in created
    services.offices.raise_level("demo", "recepcion", sensitivity="normal", actor=ACTOR)
    assert services.runs.create(request(confirm_cloud=True), bundle="demo__recepcion")["data_class"] == "non_sensitive"


def api(services: ApplicationServices):
    return TestClient(create_app(services=services), base_url="http://localhost")


def comparable(answer: dict) -> dict:
    return {**answer, "areas": [{key: value for key, value in area.items() if key != "set_at"} for area in answer["areas"]]}


def test_raise_by_api_and_cli(world, monkeypatch, capsys):
    start, tmp = world
    via_api, via_cli = start("api"), start("cli")
    for services in (via_api, via_cli):
        services.offices.import_folder(make_demo(tmp), actor=ACTOR)
    with api(via_api) as http, api(via_cli) as cli_client:
        shown = http.get("/api/v1/offices/demo/privacy")
        assert shown.status_code == 200 and shown.json()["note"] == list(PRIVACY_NOTE)
        raised = http.post("/api/v1/offices/demo/privacy/raise",
                           json={"area": "recepcion", "sensitivity": "sensitive", "actor": ACTOR})
        assert raised.status_code == 200 and raised.json()["changed"] is True
        by_area = {area["id"]: area for area in raised.json()["areas"]}
        assert (by_area["recepcion"]["sensitivity"], by_area["recepcion"]["data_class"], by_area["recepcion"]["source"]) == (
            "sensitive", "sensitive", "person")
        lowering = http.post("/api/v1/offices/demo/privacy/raise",
                             json={"area": "recepcion", "sensitivity": "normal", "actor": ACTOR})
        assert lowering.status_code == 409 and lowering.json()["detail"]["details"]["reason"] == "office_level_lowering_needs_gui"
        unlocking = http.post("/api/v1/offices/demo/privacy/raise",
                              json={"area": "seguimiento", "local_only": False, "actor": ACTOR})
        assert unlocking.status_code == 409 and unlocking.json()["detail"]["details"]["reason"] == "office_level_lowering_needs_gui"
        assert http.post("/api/v1/offices/demo/privacy/raise",
                         json={"area": "recepcion", "sensitivity": "sensitive", "actor": "agent:x"}).status_code == 409
        assert http.post("/api/v1/offices/demo/privacy/raise", json={"actor": ACTOR}).status_code == 422
        assert http.post("/api/v1/offices/demo/privacy/raise",
                         json={"actor": ACTOR, "area": "avisos", "sensitivity": "sensitive", "approval_id": "x"}).status_code == 422
        assert http.get("/api/v1/offices/nothing/privacy").status_code == 409
        printed = invoke_cli(monkeypatch, capsys, cli_client,
                             ["office", "privacy", "demo", "--area", "recepcion", "--sensitive"])
        assert comparable(json.loads(printed)) == comparable(raised.json())
        assert json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "privacy", "demo"])) == \
            cli_client.get("/api/v1/offices/demo/privacy").json()
        locked = json.loads(invoke_cli(monkeypatch, capsys, cli_client, ["office", "privacy", "demo", "--local-only"]))
        assert locked["local_only"] is True and {area["data_class"] for area in locked["areas"]} == {"restricted"}
        routes = {route.path for route in http.app.routes}
        assert "/api/v1/offices/{office_id}/privacy/raise" in routes
        assert not [path for path in routes if "lower" in path or path.endswith("/privacy/lower")]
    assert via_cli.offices.area_class("demo__recepcion") == "restricted"


def test_whole_office_local_only_by_service(demo):
    services, _, _ = demo
    assert reason(failure(services.offices.raise_level, "demo", None, sensitivity="sensitive", actor=ACTOR)) == \
        "office_privacy_area_required"
    answer = services.offices.raise_level("demo", None, local_only=True, actor=ACTOR)
    assert answer["local_only"] is True and answer["changed"] is True
    assert services.offices.area_class("demo__avisos") == "restricted"
    assert reason(failure(services.offices.raise_level, "demo", None, local_only=False, actor=ACTOR)) == \
        "office_level_lowering_needs_gui"
    assert services.offices.raise_level("demo", None, local_only=True, actor=ACTOR)["changed"] is False
    assert reason(failure(services.offices.raise_level, "demo", "nothing", sensitivity="sensitive", actor=ACTOR)) == \
        "office_area_not_installed"


def decide_with_code(client: TestClient, headers: dict, approval_id: str) -> None:
    """The double of the person: the browser session and the 120 s code of the approvals, as the GUI would use them."""
    if client.cookies.get(APPROVAL_COOKIE) is None:
        assert client.post("/gui/api/approvals/session", headers=headers, json={"code": "synthetic-start-code"}).status_code == 200
    code = client.post(f"/gui/api/approvals/{approval_id}/challenge", headers=headers, json={}).json()["code"]
    done = client.post(f"/gui/api/approvals/{approval_id}/decision", headers=headers,
                       json={"decision": "approve", "code": code})
    assert done.status_code == 200, done.text


def privacy_events(services) -> list[dict]:
    return [event for event in services.tasks.events() if event["type"] == "office.privacy_changed"]


def test_lowering_needs_a_one_use_approval(demo):
    services, _, _ = demo
    offices = services.offices
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    asked = offices.request_lowering("demo", "propuesta", sensitivity="normal")
    assert asked["kind"] == "office_privacy_change" and asked["state"] == "pending" and len(asked["sha256"]) == 64
    assert offices.request_lowering("demo", "propuesta", sensitivity="normal")["id"] == asked["id"]
    assert services.user_approvals.pending() == []                # hidden: not in «Aprobaciones» or «Pendientes»
    assert reason(failure(offices.lower_level, "demo", "propuesta", approval_id=asked["id"])) == \
        "office_privacy_change_not_approved"
    assert offices.area_class("demo__propuesta") == "sensitive"
    with TestClient(app, base_url="http://localhost") as client:
        decide_with_code(client, headers, asked["id"])
    error = failure(offices.lower_level, "demo", "avisos", approval_id=asked["id"])       # another area: not this approval
    assert (error.status, reason(error)) == (409, "office_privacy_change_not_approved")
    lowered = offices.lower_level("demo", "propuesta", approval_id=asked["id"], actor="user:ana")
    assert (lowered["from"], lowered["to"], lowered["changed"]) == ("sensitive", "normal", True)
    assert offices.area_class("demo__propuesta") == "non_sensitive"
    now = privacy_of(services)["propuesta"]
    assert (now["sensitivity"], now["source"], now["set_by"]) == ("normal", "person", "user:ana")
    (event,) = privacy_events(services)
    assert (event["actor"], event["ref"]) == ("user:ana", "demo")
    assert {key: event["payload"][key] for key in ("office", "area", "from", "to")} == {
        "office": "demo", "area": "propuesta", "from": "sensitive", "to": "normal"}
    assert reason(failure(offices.lower_level, "demo", "propuesta", approval_id=asked["id"])) == \
        "office_privacy_change_not_approved"                      # spent: it works once
    assert services.user_approvals.pending() == []
    assert [event for event in privacy_events(services)] == [event]


def test_an_approval_does_not_outlive_the_level_it_was_asked_for(demo):
    services, _, _ = demo
    offices = services.offices
    app = create_app(services=services)
    app.state.approval_sessions.bootstrap_code = "synthetic-start-code"
    headers = {"Authorization": f"Bearer {ensure_token(services.settings.gui_token_file)}"}
    asked = offices.request_lowering("demo", "propuesta", sensitivity="normal")
    with TestClient(app, base_url="http://localhost") as client:
        decide_with_code(client, headers, asked["id"])
    offices.raise_level("demo", "propuesta", local_only=True, actor=ACTOR)       # the level changed after the person saw it
    assert reason(failure(offices.lower_level, "demo", "propuesta", approval_id=asked["id"])) == \
        "office_privacy_change_not_approved"
    assert offices.area_class("demo__propuesta") == "restricted" and privacy_events(services)[-1]["payload"]["direction"] == "raise"
    assert reason(failure(offices.request_lowering, "demo", "propuesta", sensitivity="sensitive")) == "office_privacy_not_lower"
    assert reason(failure(offices.request_lowering, "demo", "propuesta")) == "office_privacy_nothing_to_change"
    assert failure(offices.lower_level, "demo", "propuesta", approval_id="").status == 409
    # a kind that is not this one cannot be passed off as it
    other = services.user_approvals.request(kind="osv_query", destination={"office": "demo", "area": "propuesta"},
                                            location={"source": "t"}, sha256="0" * 64)
    services.user_approvals.decide(other["id"], "approve")
    assert reason(failure(offices.lower_level, "demo", "propuesta", approval_id=other["id"])) == \
        "office_privacy_change_not_approved"


def test_levels_set_by_a_person_survive_removing_another_area(demo):
    services, _, _ = demo
    services.offices.raise_level("demo", "recepcion", sensitivity="sensitive", actor=ACTOR)
    services.offices.raise_level("demo", "avisos", sensitivity="sensitive", actor=ACTOR)
    services.offices.remove_area("demo", "avisos", actor=ACTOR)
    levels = privacy_of(services)
    assert levels["recepcion"]["sensitivity"] == "sensitive" and levels["recepcion"]["source"] == "person"
    assert "avisos" not in levels
    entry = json.loads((services.settings.data_dir / "installed" / "offices.json").read_text(encoding="utf-8"))
    assert entry["schema"] == OFFICES_SCHEMA and list(entry["offices"]["demo"]["privacy"]["areas"]) == ["recepcion"]


def test_an_office_without_levels_set_by_a_person_reads_as_before(demo):
    services, _, _ = demo
    stored = json.loads((services.settings.data_dir / "installed" / "offices.json").read_text(encoding="utf-8"))
    assert "privacy" not in stored["offices"]["demo"] and stored["schema"] == OFFICES_SCHEMA
    shown = services.offices.privacy("demo")["areas"]
    assert [(area["id"], area["sensitivity"], area["local_only"], area["source"]) for area in shown] == [
        ("recepcion", None, False, "office"), ("propuesta", "sensitive", False, "office"),
        ("seguimiento", None, True, "office"), ("avisos", "normal", False, "office")]
    assert services.offices.get("demo")["areas"][1]["sensitivity"] == "sensitive"


def test_bundles_outside_offices_are_unchanged(world):
    start, tmp = world
    services = start()
    services.offices.import_folder(make_demo(tmp), actor=ACTOR)     # an office exists; the plain package is not part of it
    services.installed.apply_batch([{"op": "install", "name": "plain", "folder": str(make_area(tmp / "plain"))}], actor=ACTOR)
    created = services.runs.create(request(sensitive=False, confirm_cloud=True, execution_mode="human_guided"), bundle="plain")
    assert created["request"]["sensitive"] is False and "data_class" not in created
    assert "data_class" not in services.runs.get(created["id"])
    assert services.offices.area_class("plain") is None


def test_gui_preview_treats_the_area_as_sensitive(world):
    start, tmp = world
    services = start()
    root = make_demo(tmp)
    prefer_cloud(root, DEMO_AREAS)
    services.offices.import_folder(root, actor=ACTOR)

    def chosen(bundle: str, sensitive: bool = False) -> str:
        steps = _routing_preview(services, "spec_review", bundle, INPUTS, ExecutionMode.AUTO, sensitive)["steps"]
        return steps[0]["model"]

    assert chosen("demo__avisos") == "cloud_fast"                  # the control: a «normal» area offers its cloud model
    assert chosen("demo__propuesta") == "local_default"
    assert chosen("demo__seguimiento") == "local_default"
    assert chosen("demo__recepcion") == "cloud_fast"               # no level: nothing is forced before the person chooses
    assert chosen("demo__avisos", sensitive=True) == "local_default"


def test_privacy_note():
    note = PRIVACY_NOTE
    assert note == ("Contraseñas, claves y tokens: siempre te pregunto, uno por uno.",
                    "Las áreas marcadas «Solo local».",
                    "Con un ejecutor (Claude, Codex, Copilot, agy), el agente puede leer toda la carpeta de trabajo de la "
                    "oficina, no solo la tarea.")


def test_privacy_note_is_the_one_the_service_returns(demo):
    services, _, _ = demo
    note = services.offices.privacy("demo")["note"]
    assert note == PRIVACY_NOTE
    text = " ".join(note).casefold()
    assert "destino" not in text and "autoriz" not in text and "nube" not in text
    assert [area["id"] for area in services.offices.privacy("demo")["areas"]] == list(DEMO_AREAS)
    assert set(services.offices.privacy("demo")["areas"][0]) == {
        "id", "sensitivity", "local_only", "data_class", "set_by", "set_at", "source", "cloud", "cloud_effective"}
    assert reason(failure(services.offices.privacy, "nothing")) == "office_not_installed"
