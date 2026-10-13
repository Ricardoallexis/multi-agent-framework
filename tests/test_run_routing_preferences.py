"""Run-scoped ECO preferences: persistence, policy refusal, preview and no dispatch."""
from copy import deepcopy
import pytest
from fastapi.testclient import TestClient
from multiagent.api import create_app
from multiagent.catalog import ModelSpec
from multiagent.config import Settings
from multiagent.gui.token import ensure_token
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.services.errors import ServiceError


@pytest.fixture
def app(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        mock_mode=True, openai_enabled=False, openai_api_key="", gemini_api_key="",
                        gui_token_file=tmp_path / "token")
    services = ApplicationServices.create(settings)
    catalog = services.system.catalog
    price = {"amount": 0, "denominator": 1000000, "unit": "token", "currency": "USD",
             "source": "synthetic local rate", "provenance": "local_no_charge",
             "valid_from": "2020-01-01T00:00:00Z", "valid_until": "2099-01-01T00:00:00Z"}
    base = catalog.models["local_default"]
    catalog.models["synthetic_t1"] = ModelSpec(**{**vars(base), "id": "synthetic_t1", "level": "T1",
        "classification": "classified", "pricing": {"input": price, "output": price},
        "allowed_data_classes": ["non_sensitive"], "permissions_required": [], "permissions": []})
    catalog.models["synthetic_t2"] = ModelSpec(**{**vars(catalog.models["synthetic_t1"]), "id": "synthetic_t2", "level": "T2"})
    catalog.routing_profiles["synthetic_eco"] = {
        "version": 1, "enabled": True, "mode": "supervised", "levels": {"work": {"min": "T1", "max": "T2"}},
        "candidate_model_ids": ["synthetic_t1", "synthetic_t2"], "economic_selection": True,
        "required_capabilities": ["structured_output"], "required_roles": [], "required_permissions": [],
        "allowed_permissions": [], "allowed_data_classes": ["non_sensitive"], "execution_modes": ["auto", "local"],
        "require_health": False, "max_retries_per_candidate": 0,
    }
    catalog.routing_assignments["social_post"] = {"create": "synthetic_eco"}
    def forbidden(*args, **kwargs):
        raise AssertionError("provider/executor dispatch is forbidden")
    for adapter in services.system.adapters.values():
        if hasattr(adapter, "generate"): monkeypatch.setattr(adapter, "generate", forbidden)
    monkeypatch.setattr(services.executors, "_execute", forbidden)
    client = TestClient(create_app(services=services), base_url="http://localhost")
    auth = {"Authorization": "Bearer " + ensure_token(settings.gui_token_file)}
    yield services, client, auth
    client.close()
    services.runs.close()


def payload(preferences, **extra):
    return {"workflow_id": "social_post", "project_name": "Synthetic project",
            "inputs": {"topic": "Synthetic topic", "tone": "neutral", "channel": "web", "audience": "test", "platform": "web", "instructions": "Synthetic instructions"},
            "routing_preferences": preferences, **extra}


def test_preferences_persist_and_preview_matches_without_dispatch(app):
    services, client, auth = app
    original = deepcopy(services.system.catalog.routing_profiles)
    preferences = {"create": {"profile_id": "synthetic_eco", "mode": "automatic", "level": "T2", "model": "synthetic_t2", "effort": "low"}}
    body = payload(preferences)
    preview = client.post("/gui/api/runs/preview", headers=auth,
                          json={key: value for key, value in body.items() if key != "project_name"})
    assert preview.status_code == 200, preview.text
    route = next(step for step in preview.json()["routing"]["steps"] if step["step_id"] == "create")
    assert (route["mode"], route["level"], route["model"], route["effort"]) == ("automatic", "T2", "synthetic_t2", "low")
    assert client.get("/gui/api/runs", headers=auth).json() == []
    created = client.post("/gui/api/runs", headers=auth, json=body)
    assert created.status_code == 202, created.text
    saved = services.runs.get(created.json()["id"])
    assert saved["request"]["routing_preferences"] == preferences
    assert saved["status"] == "queued"
    assert services.system.catalog.routing_profiles == original


@pytest.mark.parametrize("choice", [
    {"bogus": {"effort": "low"}}, {"create": {"mode": "bogus"}}, {"create": {"effort": "ultra"}},
    {"create": {"model": "missing"}}, {"create": {"level": "T4"}},
    {"create": {"profile_id": "missing"}}, {"create": {"permission": "allow"}},
    {"create": {"mode": "single"}},
])
def test_bad_choices_are_rejected_before_any_run_is_created(app, choice):
    services, client, auth = app
    result = client.post("/gui/api/runs", headers=auth, json=payload(choice))
    assert result.status_code == 422, result.text
    assert services.runs.list() == []


def test_disabled_and_unsupported_budget_profiles_are_not_bypassed(app):
    services, client, auth = app
    profile = services.system.catalog.routing_profiles["synthetic_eco"]
    for setting in ({"enabled": False}, {"enabled": True, "strict_budgets": ["cost"]}):
        profile.update(setting)
        result = client.post("/gui/api/runs", headers=auth, json=payload({"create": {"effort": "low"}}))
        assert result.status_code == 422, result.text
        assert services.runs.list() == []


def test_core_direct_create_validates_and_idempotency_includes_preferences(app):
    services, _, _ = app
    body = payload({"create": {"model": "synthetic_t1"}}, idempotency_key="synthetic-key")
    first = services.runs.create(RunRequest(**body))
    assert services.runs.create(RunRequest(**body))["id"] == first["id"]
    body["routing_preferences"] = {"create": {"model": "synthetic_t2"}}
    with pytest.raises(ServiceError): services.runs.create(RunRequest(**body))
    with pytest.raises(ServiceError): services.runs.create(RunRequest(**payload({"create": {"model": "unknown"}})))
    assert len(services.runs.list()) == 1


def test_preferences_serialize_only_explicit_fields():
    request = RunRequest(**payload({"create": {"effort": "low"}}))
    assert request.model_dump(mode="json")["routing_preferences"] == {"create": {"effort": "low"}}


def test_single_mode_and_null_effort_do_not_mutate_policy(app):
    services, client, auth = app
    original = deepcopy(services.system.catalog.routing_profiles)
    preferences = {"create": {"mode": "single", "model": "synthetic_t1", "effort": None}}
    result = client.post("/gui/api/runs", headers=auth, json=payload(preferences))
    assert result.status_code == 202, result.text
    assert services.runs.get(result.json()["id"])["request"]["routing_preferences"] == preferences
    assert services.system.catalog.routing_profiles == original


def test_sensitive_data_and_single_mode_cannot_escape_profile_limits(app):
    services, client, auth = app
    choice = {"create": {"model": "synthetic_t1"}}
    result = client.post("/gui/api/runs", headers=auth, json=payload(choice, sensitive=True))
    assert result.status_code == 422, result.text
    profile = services.system.catalog.routing_profiles["synthetic_eco"]
    profile["levels"]["work"] = {"max": "T1"}
    result = client.post("/gui/api/runs", headers=auth,
                         json=payload({"create": {"mode": "single", "model": "synthetic_t2"}}))
    assert result.status_code == 422, result.text
    assert services.runs.list() == []


def test_runtime_route_uses_saved_preferences_with_no_provider_call(app):
    services, _, _ = app
    engine = services.system.engine
    preference = {"mode": "automatic", "model": "synthetic_t2", "effort": "low", "level": "T2"}
    request = RunRequest(**payload({"create": preference}))
    run = services.runs.create(request)
    step = next(item for item in engine.preflight("social_post").steps if item.id == "create")
    from multiagent.routing_preferences import resolve_profile
    from multiagent.contracts import ExecutionMode
    profile_id, profile = resolve_profile(engine.catalog, "social_post", step.id, step.kind, preference)
    binding, requirements, reason = engine._routing_for_step(step=step, execution_mode=ExecutionMode.AUTO,
                                                            requires_web=False, sensitive=False)
    route = engine._route_with_profile(run_id=run["id"], run=run, state=run, step=step, attempt=1,
        profile_id=profile_id, profile=profile, request=run["request"], execution_mode=ExecutionMode.AUTO,
        requires_web=False, estimated_input=1, binding=binding, requirements=requirements, routing_reason=reason)
    assert route[0].selected_model == "synthetic_t2"
    assert route[0].profile_mode == "automatic"
    assert route[4] == "low"


def test_empty_preferences_keep_the_historical_request_shape():
    request = RunRequest(workflow_id="social_post", project_name="Synthetic project")
    assert "routing_preferences" not in request.model_dump(mode="json")
    assert "routing_preferences" not in request.model_dump_json()
