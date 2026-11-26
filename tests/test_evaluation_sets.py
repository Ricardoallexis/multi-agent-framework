from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.cli import main
from multiagent.config import Settings
from multiagent.evaluation import load_eval_set
from multiagent.evaluation.runner import run_eval_set
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices, ServiceError

EXAMPLES = Path(__file__).parent / "evals"


@pytest.fixture
def services(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    yield services
    services.runs.close()


@pytest.mark.parametrize("name", ["workflow", "agent", "prompt"])
def test_examples_pass_in_mock(services, name):
    report = services.evaluation.run(load_eval_set(EXAMPLES / f"{name}.yaml"))
    assert report["score"] == 1.0
    assert report["meta"]["fingerprint"]["target"]["kind"] == name
    assert len(report["meta"]["fingerprint"]["set_sha256"]) == 64


def _agent_set(inputs):
    return load_eval_set_from({"name": "pick", "version": "1", "target": {"kind": "agent", "id": "creator"},
                               "cases": [{"id": "c1", "inputs": inputs,
                                          "checks": [{"type": "contract_valid"}]}]})


def load_eval_set_from(raw):
    from multiagent.evaluation.cases import EvalSet
    return EvalSet.model_validate(raw)


def test_agent_target_picks_the_workflow_whose_inputs_the_cases_give(services):
    from multiagent.evaluation.runner import _matching_step
    social = _matching_step(services, _agent_set({"topic": "t", "platform": "x", "audience": "a"}))
    assert social.prompt_id.startswith("creator/social_post")
    once = _matching_step(services, _agent_set({"task": "Explain photosynthesis."}))
    assert once.prompt_id == "creator/ask_once"


def test_unknown_target_and_live_validation(services):
    raw = load_eval_set(EXAMPLES / "agent.yaml").model_dump(mode="json")
    raw["target"]["kind"] = "unknown"
    with pytest.raises(ServiceError) as error:
        services.evaluation.run(raw)
    assert error.value.code == "invalid_request"
    assert error.value.details["errors"][0]["loc"] == ["target", "kind"]

    raw["target"]["kind"] = "agent"
    with pytest.raises(ServiceError) as error:
        services.evaluation.run(raw, live=True)
    assert error.value.code == "eval_live_target_unsupported"
    assert error.value.details["errors"][0]["loc"] == ["live"]

    raw["target"]["id"] = "no_such_agent"
    with pytest.raises(ServiceError) as error:
        services.evaluation.run(raw)
    assert error.value.code == "invalid_request"
    assert error.value.details["errors"][0]["loc"] == ["target", "id"]


def test_legacy_set_remains_valid(services):
    report = services.evaluation.run(load_eval_set(EXAMPLES / "example.yaml"))
    assert report["name"] == "synthetic-social-post"
    assert report["score"] < 1
    assert report["meta"]["fingerprint"]["target"]["kind"] == "workflow"


def test_python_cli_http_share_report(services, monkeypatch, capsys):
    eval_set = load_eval_set(EXAMPLES / "agent.yaml")
    python = services.evaluation.run(eval_set)
    monkeypatch.setattr("multiagent.cli.get_settings", lambda: services.settings)
    main(["eval", "run", str(EXAMPLES / "agent.yaml")])
    cli = json.loads(capsys.readouterr().out)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/evaluations/run", json=eval_set.model_dump(mode="json"))
    assert response.status_code == 200
    http = response.json()
    ids = {report["meta"].pop("evaluation_run_id") for report in (python, cli, http)}
    assert len(ids) == 3  # each execution is saved under its own id
    assert python == cli == http


def test_invalid_set_has_same_error_in_python_cli_http(services, monkeypatch, capsys, tmp_path):
    raw = load_eval_set(EXAMPLES / "agent.yaml").model_dump(mode="json")
    raw["target"]["kind"] = "unknown"
    with pytest.raises(ServiceError) as error:
        services.evaluation.run(raw)
    expected = error.value.to_payload()

    invalid = tmp_path / "invalid.yaml"
    import yaml
    invalid.write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr("multiagent.cli.get_settings", lambda: services.settings)
    with pytest.raises(SystemExit) as exit_info:
        main(["eval", "run", str(invalid)])
    assert exit_info.value.code == 1
    cli_error = json.loads(capsys.readouterr().err)

    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/evaluations/run", json=raw)
    assert response.status_code == 422
    assert expected == cli_error == response.json()["detail"]


def test_live_agent_is_rejected_by_http(services):
    raw = load_eval_set(EXAMPLES / "agent.yaml").model_dump(mode="json")
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/evaluations/run", json={**raw, "live": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "eval_live_target_unsupported"
    assert response.json()["detail"]["details"]["errors"][0]["loc"] == ["live"]


@pytest.mark.parametrize("interface", ["python", "cli", "http"])
def test_mock_does_not_touch_system_queue(services, monkeypatch, capsys, interface):
    queued = services.runs.create(RunRequest(workflow_id="social_post", project_name="user-queued"))
    before = services.runs.get(queued["id"])
    count = len(services.runs.list())
    eval_set = load_eval_set(EXAMPLES / "workflow.yaml")
    if interface == "python":
        report = services.evaluation.run(eval_set)
    elif interface == "cli":
        monkeypatch.setattr("multiagent.cli.get_settings", lambda: services.settings)
        main(["eval", "run", str(EXAMPLES / "workflow.yaml")])
        report = json.loads(capsys.readouterr().out)
    else:
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            response = client.post("/api/v1/evaluations/run", json=eval_set.model_dump(mode="json"))
        assert response.status_code == 200
        report = response.json()
    after = services.runs.get(queued["id"])
    assert after["status"] == "queued"
    assert after["events"] == before["events"]
    assert len(services.runs.list()) == count
    assert "run_ids" not in report["meta"]
    assert report["meta"]["statuses"]


def test_live_without_worker_creates_no_run(services):
    eval_set = load_eval_set(EXAMPLES / "workflow.yaml")
    before = len(services.runs.list())
    with pytest.raises(ServiceError) as error:
        services.evaluation.run(eval_set, live=True)
    assert error.value.code == "eval_live_worker_unavailable"
    assert error.value.status == 409
    assert len(services.runs.list()) == before

    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        response = client.post("/api/v1/evaluations/run",
                               json={**eval_set.model_dump(mode="json"), "live": True})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "eval_live_worker_unavailable"
    assert len(services.runs.list()) == before


def test_mock_paths_are_temporary_and_definitions_are_shared(services, monkeypatch):
    original = ApplicationServices.create
    observed = []

    def capture(cls, settings=None, **options):
        observed.append(settings)
        return original(settings, **options)

    monkeypatch.setattr(ApplicationServices, "create", classmethod(capture))
    services.evaluation.run(load_eval_set(EXAMPLES / "workflow.yaml"))
    mock = observed[0]
    assert mock.data_dir != services.settings.data_dir
    assert mock.db_path.parent == mock.data_dir
    assert mock.runs_dir.parent == mock.data_dir
    assert mock.assets_dir.parent == mock.data_dir
    assert mock.worker_enabled is False
    for name in ("catalog_dir", "workflows_dir", "prompts_dir", "skills_dir"):
        assert getattr(mock, name) == getattr(services.settings, name)
    assert not mock.data_dir.exists()


def test_live_timeout_leaves_run_queued(services, monkeypatch):
    class AliveThread:
        def is_alive(self):
            return True

    services.system.worker._thread = AliveThread()
    services.settings.worker_enabled = True
    services.system.engine.router.dry_run = False
    services.settings.eval_live_timeout_seconds = 2
    ticks = iter([0, 0, 2])
    original = run_eval_set
    monkeypatch.setattr("multiagent.services.evaluation.run_eval_set",
                        lambda svc, value, live: original(svc, value, live=live,
                                                          clock=lambda: next(ticks), wait=lambda _: None))
    report = services.evaluation.run(load_eval_set(EXAMPLES / "workflow.yaml"), live=True)
    assert report["meta"]["statuses"] == {"post": "timeout"}
    assert report["score"] == 0
    assert report["cases"][0]["passed"] is False
    run_id = report["meta"]["run_ids"]["post"]
    assert services.runs.get(run_id)["status"] == "queued"


def test_cli_live_calls_server(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("multiagent.cli._api_json", lambda *args, **kwargs: calls.append((args, kwargs)) or
                        {"name": "remote", "version": "1", "score": 1, "cases": [], "meta": {}})
    monkeypatch.setattr("multiagent.cli.get_settings", lambda: pytest.fail("CLI opened a local runtime"))
    main(["eval", "run", str(EXAMPLES / "workflow.yaml"), "--live"])
    assert json.loads(capsys.readouterr().out)["name"] == "remote"
    assert calls[0][0] == ("POST", "/api/v1/evaluations/run")
    assert calls[0][1]["json_body"]["live"] is True
    assert calls[0][1]["timeout"] is None
