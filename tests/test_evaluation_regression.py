# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EVA-T02: saved evaluation runs, baselines and regression comparison (Mock only)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import config
from multiagent.api import create_app
from multiagent.cli import main
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.evaluation import load_eval_set
from multiagent.evaluation.regression import compare_reports
from multiagent.services import ApplicationServices, ServiceError

EXAMPLES = Path(__file__).parent / "evals"
WORKFLOW_SET = EXAMPLES / "workflow.yaml"


@pytest.fixture
def services(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                        gemini_api_key="", openai_api_key="", openai_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    monkeypatch.setattr("multiagent.cli.get_settings", lambda: services.settings)
    yield services
    services.runs.close()


@pytest.fixture
def client(services):
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield client


def cli(capsys, *argv, code=0, stream="out"):
    with pytest.raises(SystemExit) as exit_info:
        main(list(argv))
    assert exit_info.value.code == code
    captured = capsys.readouterr()
    return json.loads(getattr(captured, stream))


def cli_ok(capsys, *argv):
    main(list(argv))
    return json.loads(capsys.readouterr().out)


def broken_workflows(tmp_path, monkeypatch):
    """A temporary copy of the workflows in which social_post ends with a research step, with no checkpoint
    before it: the checks on the last artifact (the post title) now fail. Nothing stored is edited."""
    directory = tmp_path / "workflows"
    shutil.copytree(config._resource_dir("workflows"), directory)
    path = directory / "social_post.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    research, create = document["steps"]
    research.update(when=None, requires_web=False)
    create["checkpoint_after"] = False
    document["steps"] = [create, research]
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    original = config._resource_dir
    monkeypatch.setattr(config, "_resource_dir", lambda name: directory if name == "workflows" else original(name))


def case(case_id, score, passed, checks):
    return {"id": case_id, "passed": passed, "score": score,
            "checks": [{"name": name, "passed": ok, "detail": ""} for name, ok in checks]}


def report(cases, score=1.0, sha="a" * 64, models=None, workflow_sha=None):
    cases = list(cases)
    return {"name": "s", "version": "1", "cases": cases, "score": score, "meta": {"fingerprint": {
        "target": {"kind": "workflow", "sha256_by_case": workflow_sha or {c["id"]: "w1" for c in cases}},
        "set_sha256": sha,
        "provider_models_by_case": models or {c["id"]: [{"provider": "fake", "model": "fixture"}] for c in cases}}}}


def test_pure_comparison_states_and_threshold():
    ok = [("contract_valid", True), ("title", True)]
    half = [("contract_valid", True), ("title", False)]
    base = report([case("same", 1.0, True, ok), case("down", 1.0, True, ok), case("up", 0.5, False, half),
                   case("small", 1.0, True, ok), case("flip", 1.0, True, ok), case("gone", 1.0, True, ok),
                   case("shifted", 1.0, True, ok)])
    candidate = report(
        [case("same", 1.0, True, ok), case("down", 0.5, False, half), case("up", 1.0, True, ok),
         case("small", 0.9, True, ok), case("flip", 0.99, False, ok), case("new", 1.0, True, ok),
         case("shifted", 1.0, True, [("contract_valid", True), ("title", True), ("extra", True)])],
        sha="b" * 64, workflow_sha={"same": "w1", "down": "w2", "up": "w1", "small": "w1", "flip": "w1",
                                    "new": "w1", "shifted": "w1"})
    status = {item["id"]: item["status"] for item in compare_reports(base, candidate)["cases"]}
    assert status == {"same": "unchanged", "down": "regressed", "up": "improved", "small": "regressed",
                      "flip": "regressed", "shifted": "changed"}
    loose = compare_reports(base, candidate, threshold=0.2)
    status = {item["id"]: item["status"] for item in loose["cases"]}
    assert status["small"] == "unchanged"  # a 0.1 drop is inside the threshold
    assert status["flip"] == "regressed"  # a pass that fails always regresses
    assert loose["regression"] is True
    assert loose["only_in_base"] == ["gone"] and loose["only_in_candidate"] == ["new"]
    assert loose["fingerprint"] == {"agent_or_prompt": False, "workflow": True, "models": True, "set_changed": True}

    identical = compare_reports(base, base)
    assert identical["regression"] is False
    assert {item["status"] for item in identical["cases"]} == {"unchanged"}
    assert identical["fingerprint"] == {"agent_or_prompt": False, "workflow": False, "models": False,
                                        "set_changed": False}
    improved_only = compare_reports(candidate, base, threshold=0.0)
    assert improved_only["regression"] is True  # reversing the arguments flips what counts as worse


def test_pure_comparison_agent_prompt_fingerprint():
    def agent(sha):
        value = report([case("c", 1.0, True, [("x", True)])])
        value["meta"]["fingerprint"]["target"] = {"kind": "prompt", "id": "p", "version": 1, "sha256": sha}
        return value
    result = compare_reports(agent("1"), agent("2"))
    assert result["fingerprint"]["agent_or_prompt"] is True and result["fingerprint"]["workflow"] is False
    assert result["cases"][0]["status"] == "changed" and result["regression"] is False


def test_induced_regression_by_cli_and_api(services, client, capsys, tmp_path, monkeypatch):
    good = services.evaluation.run(load_eval_set(WORKFLOW_SET), actor="user:ana")
    good_id = good["meta"]["evaluation_run_id"]
    assert services.evaluation.get_run(good_id)["report"] == good
    assert services.evaluation.get_run(good_id)["actor"] == "user:ana"
    baseline = cli_ok(capsys, "eval", "baseline", "set", good_id, "--actor", "user:ana")
    assert baseline["run_id"] == good_id and baseline["set_name"] == "workflow-example"
    assert cli_ok(capsys, "eval", "baseline", "show", "workflow-example")["run_id"] == good_id

    broken_workflows(tmp_path, monkeypatch)
    bad = cli_ok(capsys, "eval", "run", str(WORKFLOW_SET))
    monkeypatch.undo()
    monkeypatch.setattr("multiagent.cli.get_settings", lambda: services.settings)
    assert bad["score"] < good["score"] and bad["cases"][0]["passed"] is False
    bad_id = bad["meta"]["evaluation_run_id"]

    result = cli(capsys, "eval", "compare", bad_id, code=3)
    assert result["regression"] is True and result["base_id"] == good_id and result["candidate_id"] == bad_id
    assert result["cases"][0]["status"] == "regressed" and result["cases"][0]["changed_checks"]
    assert result["fingerprint"]["workflow"] is True and result["fingerprint"]["set_changed"] is False

    response = client.post("/api/v1/evaluations/compare", json={"candidate_id": bad_id})
    assert response.status_code == 200 and response.json() == result  # a regression is not an HTTP error
    explicit = client.post("/api/v1/evaluations/compare", json={"candidate_id": bad_id, "base_id": good_id,
                                                               "threshold": 0.9})
    assert explicit.status_code == 200 and explicit.json()["regression"] is True  # the pass that fails ignores it

    # The same baseline can be set through the API, and runs are listed by set.
    put = client.put("/api/v1/evaluations/baselines/workflow-example", json={"run_id": good_id, "actor": "user:ana"})
    assert put.status_code == 200 and put.json()["run_id"] == good_id
    listed = client.get("/api/v1/evaluations/runs", params={"set_name": "workflow-example"}).json()
    assert [row["id"] for row in listed["runs"]] == [bad_id, good_id] and "report" not in listed["runs"][0]
    assert client.get(f"/api/v1/evaluations/runs/{bad_id}").json()["report"] == bad
    assert cli_ok(capsys, "eval", "runs", "--set", "workflow-example")["count"] == 2
    assert cli_ok(capsys, "eval", "show", bad_id)["id"] == bad_id


def test_no_regression_exits_zero(services, capsys, client):
    first = services.evaluation.run(load_eval_set(WORKFLOW_SET))["meta"]["evaluation_run_id"]
    second = cli_ok(capsys, "eval", "run", str(WORKFLOW_SET))["meta"]["evaluation_run_id"]
    assert first != second
    cli_ok(capsys, "eval", "baseline", "set", first)
    result = cli(capsys, "eval", "compare", second, code=0)
    assert result["regression"] is False and result["cases"][0]["status"] == "unchanged"
    assert cli(capsys, "eval", "compare", second, "--base", first, "--threshold", "0.3", code=0)["threshold"] == 0.3
    assert client.post("/api/v1/evaluations/compare", json={"candidate_id": second}).json()["regression"] is False


def test_service_errors_are_stable_in_service_cli_and_api(services, client, capsys):
    workflow = services.evaluation.run(load_eval_set(WORKFLOW_SET))["meta"]["evaluation_run_id"]
    agent = services.evaluation.run(load_eval_set(EXAMPLES / "agent.yaml"))["meta"]["evaluation_run_id"]
    unknown = "evr-000000000000"

    with pytest.raises(ServiceError) as error:
        services.evaluation.compare(workflow, agent)
    assert (error.value.code, error.value.status) == ("evaluation_set_mismatch", 409)
    with pytest.raises(ServiceError) as error:
        services.evaluation.set_baseline("agent-example", workflow)
    assert error.value.code == "evaluation_set_mismatch"
    for call in (lambda: services.evaluation.get_run(unknown), lambda: services.evaluation.get_baseline("workflow-example"),
                 lambda: services.evaluation.compare(workflow), lambda: services.evaluation.compare(workflow, unknown),
                 lambda: services.evaluation.set_baseline("workflow-example", unknown)):
        with pytest.raises(ServiceError) as error:
            call()
        assert (error.value.code, error.value.status) == ("not_found", 404)
    for call in (lambda: services.evaluation.get_run("bad"), lambda: services.evaluation.compare(workflow, workflow, 2),
                 lambda: services.evaluation.compare(workflow, workflow, True),
                 lambda: services.evaluation.list_runs(limit=0)):
        with pytest.raises(ServiceError) as error:
            call()
        assert (error.value.code, error.value.status) == ("invalid_request", 422)

    assert client.post("/api/v1/evaluations/compare", json={"candidate_id": workflow, "base_id": agent}).status_code == 409
    assert client.put("/api/v1/evaluations/baselines/agent-example", json={"run_id": workflow}).json()[
        "detail"]["code"] == "evaluation_set_mismatch"
    assert client.get(f"/api/v1/evaluations/runs/{unknown}").status_code == 404
    assert client.get("/api/v1/evaluations/baselines/workflow-example").status_code == 404
    assert client.post("/api/v1/evaluations/compare", json={"candidate_id": workflow, "threshold": 5}).status_code == 422
    assert cli(capsys, "eval", "compare", workflow, "--base", agent, code=1, stream="err")["code"] == "evaluation_set_mismatch"
    assert cli(capsys, "eval", "show", unknown, code=1, stream="err")["code"] == "not_found"
    assert cli(capsys, "eval", "baseline", "set", workflow, "--set", "agent-example", code=1,
               stream="err")["code"] == "evaluation_set_mismatch"


def test_setting_a_baseline_again_replaces_it(services, client):
    first = services.evaluation.run(load_eval_set(WORKFLOW_SET))["meta"]["evaluation_run_id"]
    second = services.evaluation.run(load_eval_set(WORKFLOW_SET))["meta"]["evaluation_run_id"]
    services.evaluation.set_baseline("workflow-example", first, "user:ana")
    assert services.evaluation.get_baseline("workflow-example")["run_id"] == first
    replaced = client.put("/api/v1/evaluations/baselines/workflow-example", json={"run_id": second, "actor": "user:bo"})
    assert replaced.json()["run_id"] == second and replaced.json()["actor"] == "user:bo"
    assert client.get("/api/v1/evaluations/baselines/workflow-example").json()["run_id"] == second
    with services.system.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evaluation_baselines").fetchone()[0] == 1


def test_regression_threshold_setting_is_used_and_bounded(services):
    assert services.settings.eval_regression_threshold == 0.0
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError):
            Settings(_env_file=None, eval_regression_threshold=bad)


def test_migration_017_applies_on_an_existing_database(tmp_path):
    source = Path(__file__).parents[1] / "migrations"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in source.glob("*.sql"):
        if int(path.stem.split("_", 1)[0]) <= 16:
            shutil.copyfile(path, migrations / path.name)
    db = Database(SimpleNamespace(db_path=tmp_path / "data" / "multiagent.db", migrations_dir=migrations))
    db.migrate()
    with db.connect() as conn:
        conn.execute("INSERT INTO projects(id,name,created_at) VALUES('p1','old','2026-01-01T00:00:00Z')")
        conn.commit()
    shutil.copyfile(source / "017_evaluation_runs.sql", migrations / "017_evaluation_runs.sql")
    assert db.migrate() == ["017_evaluation_runs.sql"]
    assert db.migrate() == []
    with db.connect() as conn:
        assert conn.execute("SELECT name FROM projects WHERE id='p1'").fetchone()[0] == "old"
        assert conn.execute("SELECT COUNT(*) FROM evaluation_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evaluation_baselines").fetchone()[0] == 0
