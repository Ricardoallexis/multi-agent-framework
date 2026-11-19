# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""S6.C1 / ROL-T01: the role of a workflow step, from the approved team bound to the run, in native and mock."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import BackendContext, build_backend
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts import HumanStepSubmission
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.observability.usage import read_usage
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.attempts import build_step_attempts
from multiagent.services.reports import build_run_report
from multiagent.teams.step_roles import ROLE_FIELDS
from multiagent.worker import LocalWorker

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
WORKFLOW = "summarize_document"
ANALYST_PURPOSE = "Extracts key points, findings and checklist evidence from input documents."
ANALYST_RULE = "Ground every point in the provided source text."
WRITER_PURPOSE = "Drafts clear summaries and option comparisons for the target audience."
WRITER_RULE = "Keep recommendations traceable to analyst findings."
FIRST_KEY_POINT = "La reunión semanal pasa de los lunes a los miércoles."   # the bundle's KeyPoints sample
# extract_key_points runs as the analyst, write_summary as the writer; both name their agent.
TWO_ROLES = {"extract_key_points": {"role": "analyst"}, "write_summary": {"role": "writer"}}


def _settings(local_dir: Path) -> Settings:
    return Settings(_env_file=None, local_dir=local_dir, worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False)


def _bundle(tmp_path: Path, steps: dict[str, dict] | None = None, *, team=None, skills=None) -> Path:
    """A copy of the general bundle: ``steps`` merges fields into the steps of summarize_document (no checkpoints),
    ``team`` edits the team document in place, ``skills`` writes ``skills/<id>.md`` files."""
    root = tmp_path / "case"
    shutil.copytree(GENERAL_BUNDLE, root)
    path = root / "workflows" / f"{WORKFLOW}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for step in raw["steps"]:
        step["checkpoint_after"] = False
        step.update((steps or {}).get(step["id"], {}))
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    if team is not None:
        team_path = root / "team.yaml"
        data = yaml.safe_load(team_path.read_text(encoding="utf-8"))
        team(data)
        team_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    for skill_id, text in (skills or {}).items():
        (root / "skills").mkdir(exist_ok=True)
        (root / "skills" / f"{skill_id}.md").write_text(text, encoding="utf-8")
    return root


def _set_steps(root: Path, steps: dict[str, dict]) -> None:
    path = root / "workflows" / f"{WORKFLOW}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for step in raw["steps"]:
        step.update(steps.get(step["id"], {}))
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _request(**overrides) -> RunRequest:
    return RunRequest(**{"project_name": "Roles", "workflow_id": WORKFLOW,
                         "inputs": {"document_text": "La reunión semanal pasa a los miércoles."}, **overrides})


def _approve(services: ApplicationServices) -> dict:
    proposed = services.teams.propose("case", actor="user:ana", channel="python")
    return services.teams.approve(proposed["id"], actor="user:ana", channel="python")


def _binding(services: ApplicationServices) -> dict:
    """The ``team_version_bound`` payload ``services.runs.create`` stores for the approved team of "case"."""
    approved = services.teams.approved("case")
    return {"id": approved["id"], "version_id": approved["id"], "bundle": "case", "team_id": approved["team_id"],
            "version": approved["version"], "sha256": approved["sha256"], "team": approved["team"]}


def _runtime(tmp_path: Path, backend: str, root: Path, *, approve: bool = True) -> SimpleNamespace:
    """The same bundle run by ``backend``: native through ``services.runs``, mock on its own store with the binding
    ``services.runs`` would store (bundle runtimes are always native)."""
    services = ApplicationServices.create(_settings(tmp_path / "local"), bundles={"case": root})
    if approve:
        _approve(services)
    if backend == "native":
        runtime = services.runs._bundle_runtime("case")
        prompts: list[str] = []
        adapter = runtime.service.engine.router.adapters["fake"]
        original = adapter.generate_structured

        def spy(**kwargs):
            prompts.append(kwargs["prompt"])
            return original(**kwargs)

        adapter.generate_structured = spy
        return SimpleNamespace(services=services, store=runtime.store, service=runtime.service, worker=runtime.worker,
                               runtime=runtime, prompts=prompts,
                               create=lambda request: services.runs.create(request, bundle="case"))
    settings = services.runs._bundle_settings("case").model_copy(update={
        "data_dir": tmp_path / "mock", "db_path": tmp_path / "mock" / "multiagent.db",
        "runs_dir": tmp_path / "mock" / "runs", "assets_dir": tmp_path / "mock" / "assets"})
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(root, settings)
    engine = build_backend("mock", BackendContext(
        store=store, settings=settings, workflows=bundle.workflows, prompts=bundle.prompts, catalog=bundle.catalog,
        artifacts=ArtifactWriter(settings), output_schemas=bundle.output_schemas, sample_outputs=bundle.sample_outputs))
    service = RunService(settings, store, engine)
    binding = _binding(services) if approve else None
    return SimpleNamespace(services=services, store=store, service=service, worker=LocalWorker(store, engine),
                           runtime=SimpleNamespace(store=store, bundle="case", service=service), prompts=None,
                           create=lambda request: service.create_run(request, team_binding=binding))


def _drain(rt: SimpleNamespace) -> None:
    for _ in range(10):
        if not rt.worker.process_once():
            return


def _started(run: dict, step_id: str) -> dict:
    return next(event["payload"] for event in run["events"]
                if event["event_type"] == "step_started" and event["payload"]["step_id"] == step_id)


def _role_fields(mapping: dict) -> dict:
    return {name: mapping[name] for name in ROLE_FIELDS if name in mapping}


def _prompt_hashes(store: Store, run_id: str) -> list[tuple[str, str]]:
    """The sha256 of the prompt of each model call of the run, as its telemetry records it (native and mock)."""
    with store.db.connect() as conn:
        rows = conn.execute("SELECT step_id, prompt_sha256 FROM telemetry WHERE run_id=? ORDER BY id",
                            (run_id,)).fetchall()
    return [(row["step_id"], row["prompt_sha256"]) for row in rows]


def _sample(rt: SimpleNamespace, contract: str) -> dict:
    return load_bundle(rt.services.bundle_path("case"), rt.services.settings).sample_outputs[contract]


@pytest.fixture
def close():
    opened: list[ApplicationServices] = []
    yield opened.append
    for services in opened:
        services.runs.close()


def test_role_purpose_rules_and_skills_reach_the_prompt_of_its_step(tmp_path, close):
    root = _bundle(tmp_path, {"extract_key_points": {"role": "analyst"},
                              "write_summary": {"role": "writer", "skills": ["own_style"]}},
                   team=lambda data: data["roles"][1].update(skills=["analyst_method"]) or
                   data["roles"][2].update(skills=["writer_method"]),
                   skills={"analyst_method": "ANALYST-METHOD-SKILL", "writer_method": "WRITER-METHOD-SKILL",
                           "own_style": "OWN-STYLE-SKILL"})
    rt = _runtime(tmp_path, "native", root)
    close(rt.services)
    run = rt.create(_request())
    _drain(rt)

    assert rt.services.runs.get(run["id"])["status"] == "completed"
    first, second = rt.prompts
    assert f"## Role: analyst\nPurpose: {ANALYST_PURPOSE}\nRules:\n- {ANALYST_RULE}" in first
    # A step that declares no skills loads its role's skills, the way step skills are loaded.
    assert "## Active skill\nANALYST-METHOD-SKILL" in first
    # A step that declares its own skills keeps them; the role does not add its own.
    assert "OWN-STYLE-SKILL" in second and "WRITER-METHOD-SKILL" not in second


@pytest.mark.parametrize("backend", ["native", "mock"])
def test_two_roles_keep_separate_contexts(tmp_path, close, backend):
    rt = _runtime(tmp_path, backend, _bundle(tmp_path, TWO_ROLES))
    close(rt.services)
    run = rt.create(_request(step_modes={"extract_key_points": "human_guided", "write_summary": "human_guided"}))
    _drain(rt)
    first = rt.store.get_run(run["id"])["human_step_request"]["prompt_base"]
    rt.service.human_submit(run["id"], HumanStepSubmission(raw_response=json.dumps(_sample(rt, "KeyPoints"))))
    _drain(rt)
    second = rt.store.get_run(run["id"])["human_step_request"]["prompt_base"]

    assert "## Role: analyst" in first and ANALYST_PURPOSE in first and ANALYST_RULE in first
    assert "## Role: writer" in second and WRITER_PURPOSE in second and WRITER_RULE in second
    for prompt, other in ((first, ("## Role: writer", WRITER_PURPOSE, WRITER_RULE)),
                          (second, ("## Role: analyst", ANALYST_PURPOSE, ANALYST_RULE))):
        assert not any(text in prompt for text in other)
    # The outputs of earlier steps still reach the next step as before.
    assert FIRST_KEY_POINT in second


def test_role_and_member_are_recorded_in_events_attempts_and_report(tmp_path, close, monkeypatch, capsys):
    rt = _runtime(tmp_path, "native", _bundle(tmp_path, TWO_ROLES))
    close(rt.services)
    approved = rt.services.teams.approved("case")
    run = rt.create(_request())
    _drain(rt)
    run_id = run["id"]
    analyst = {"role": "analyst", "member": "doc_analyst_agent", "team_id": "general_team",
               "team_version_id": approved["id"], "team_version": 1}
    writer = {**analyst, "role": "writer", "member": "writer_agent"}

    detail = rt.services.runs.get(run_id)
    assert detail["status"] == "completed"
    assert _role_fields(_started(detail, "extract_key_points")) == analyst
    assert _role_fields(_started(detail, "write_summary")) == writer
    # A role never changes the agent a step names.
    assert [(r["step_id"], r["agent_id"]) for r in read_usage(rt.store, run_id)] == [
        ("extract_key_points", "document_analyst"), ("write_summary", "summary_writer")]

    attempts = rt.services.runs.step_attempts(run_id, "write_summary")["attempts"]
    assert _role_fields(attempts[0]) == writer
    report = rt.services.runs.report(run_id)
    started = [row for row in report["steps"] if row["event_type"] == "step_started"]
    assert [_role_fields(row) for row in started] == [analyst, writer]
    markdown = rt.services.runs.report(run_id, format="markdown")
    assert "| analyst | doc_analyst_agent |" in markdown and "| writer | writer_agent |" in markdown

    with TestClient(create_app(services=rt.services), base_url="http://localhost") as client:
        assert _role_fields(client.get(f"/api/v1/runs/{run_id}/steps/write_summary/attempts")
                            .json()["attempts"][0]) == writer
        http_steps = client.get(f"/api/v1/runs/{run_id}/report").json()["steps"]
        assert [_role_fields(row) for row in http_steps if row["event_type"] == "step_started"] == [analyst, writer]

        class _Client:
            def __enter__(self):
                return client

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(cli, "api_client", lambda: _Client())
        cli.main(["status", run_id])
        shown = json.loads(capsys.readouterr().out)
        assert _role_fields(_started(shown, "extract_key_points")) == analyst
        cli.main(["run-report", run_id])
        cli_steps = json.loads(capsys.readouterr().out)["steps"]
        assert [_role_fields(row) for row in cli_steps if row["event_type"] == "step_started"] == [analyst, writer]


def test_native_and_mock_give_the_same_result(tmp_path, close):
    results = {}
    for backend in ("native", "mock"):
        rt = _runtime(tmp_path / backend, backend, _bundle(tmp_path / backend, TWO_ROLES))
        close(rt.services)
        run = rt.create(_request())
        _drain(rt)
        run = rt.store.get_run(run["id"])
        assert run["status"] == "completed"
        report = build_run_report(run, {"records": read_usage(rt.store, run["id"])})
        results[backend] = {
            "events": [(e["payload"]["step_id"], _role_fields(e["payload"]))
                       for e in run["events"] if e["event_type"] == "step_started"],
            "attempts": [_role_fields(item) for step in ("extract_key_points", "write_summary")
                         for item in build_step_attempts(rt.runtime, rt.services.definitions, run["id"], step)["attempts"]],
            "report": [_role_fields(row) for row in report["steps"] if row["event_type"] == "step_started"],
            "agents": [(r["step_id"], r["agent_id"]) for r in read_usage(rt.store, run["id"])],
        }
    native, mock = results["native"], results["mock"]
    assert native["events"][0][1]["role"] == "analyst" and native["events"][1][1]["member"] == "writer_agent"
    # Each runtime approved its own version 1 of the same team: only the version id differs.
    for result in (native, mock):
        for _, fields in result["events"]:
            fields.pop("team_version_id")
        for fields in (*result["attempts"], *result["report"]):
            fields.pop("team_version_id")
    assert native == mock


def test_step_without_agent_runs_the_only_agent_member_of_its_role(tmp_path, close):
    # The reviewer role has one agent member (reviewer_agent, agent quality_reviewer).
    root = _bundle(tmp_path, {"write_summary": {"role": "reviewer", "agent": None}})
    for backend in ("native", "mock"):
        rt = _runtime(tmp_path / backend, backend, root)
        close(rt.services)
        run = rt.create(_request())
        _drain(rt)
        run = rt.store.get_run(run["id"])
        assert run["status"] == "completed"
        assert _started(run, "write_summary")["member"] == "reviewer_agent"
        assert dict((r["step_id"], r["agent_id"]) for r in read_usage(rt.store, run["id"]))["write_summary"] == \
            "quality_reviewer"


def test_routing_preferences_are_checked_with_the_agent_of_the_role(tmp_path, close, monkeypatch):
    import multiagent.run_service as run_service

    rt = _runtime(tmp_path, "native", _bundle(tmp_path, {"write_summary": {"role": "reviewer", "agent": None}}))
    close(rt.services)
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(run_service, "validate_run_preferences", lambda engine, request, definition: seen.update(
        (step.id, step.agent) for step in definition.steps))
    rt.create(_request(routing_preferences={"write_summary": {"effort": "low"}}))
    # The preferences of a step without an agent are checked with the agent its role gives it at run time.
    assert seen == {"extract_key_points": "document_analyst", "write_summary": "quality_reviewer"}


@pytest.mark.parametrize("backend", ["native", "mock"])
def test_steps_without_a_role_run_as_before(tmp_path, close, backend):
    runs = {}
    for bound in (False, True):
        rt = _runtime(tmp_path / str(bound), backend, _bundle(tmp_path / str(bound)), approve=bound)
        close(rt.services)
        run = rt.create(_request())
        _drain(rt)
        run = rt.store.get_run(run["id"])
        assert run["status"] == "completed"
        assert any(e["event_type"] == "team_version_bound" for e in run["events"]) is bound
        runs[bound] = SimpleNamespace(
            prompts=rt.prompts,
            started=[sorted(e["payload"]) for e in run["events"] if e["event_type"] == "step_started"],
            hashes=_prompt_hashes(rt.store, run["id"]),
            attempts=build_step_attempts(rt.runtime, rt.services.definitions, run["id"], "write_summary")["attempts"],
            report=build_run_report(run, {"records": []})["steps"],
        )
    unbound, bound = runs[False], runs[True]
    # A bound team changes nothing for steps that name no role: same prompts, same fields everywhere.
    assert unbound.prompts == bound.prompts and unbound.hashes == bound.hashes
    assert unbound.started == bound.started and not any(set(ROLE_FIELDS) & set(keys) for keys in bound.started)
    assert sorted(unbound.attempts[0]) == sorted(bound.attempts[0]) and "role" not in bound.attempts[0]
    assert [sorted(row) for row in unbound.report] == [sorted(row) for row in bound.report]
    assert not any("role" in row for row in bound.report)


@pytest.mark.parametrize("backend", ["native", "mock"])
@pytest.mark.parametrize("steps, approve, code", [
    ({"extract_key_points": {"role": "analyst"}}, False, "role_without_team"),
    ({"extract_key_points": {"role": "auditor"}}, True, "unknown_role"),
    # analyst has two agent members, and the step does not say which agent runs it.
    ({"extract_key_points": {"role": "analyst", "agent": None}}, True, "role_member_ambiguous"),
    # No member of the reviewer role runs document_analyst.
    ({"extract_key_points": {"role": "reviewer"}}, True, "role_member_not_found"),
])
def test_role_problems_reject_the_run_before_any_model_call(tmp_path, close, backend, steps, approve, code):
    rt = _runtime(tmp_path, backend, _bundle(tmp_path, steps), approve=approve)
    close(rt.services)
    with pytest.raises(Exception) as caught:
        rt.create(_request())
    error = caught.value
    payload = error.details if isinstance(error, ServiceError) else error.to_payload()
    if isinstance(error, ServiceError):
        assert (error.code, error.status) == ("invalid_definition", 422)
    assert [(issue["code"], issue["location"], issue["step_id"]) for issue in payload["issues"]] == [
        (code, "steps[0].role", "extract_key_points")]
    assert rt.store.list_runs() == []


@pytest.mark.parametrize("backend", ["native", "mock"])
def test_a_role_problem_found_when_the_run_starts_fails_it_before_any_model_call(tmp_path, close, backend):
    root = _bundle(tmp_path, TWO_ROLES)
    rt = _runtime(tmp_path, backend, root)
    close(rt.services)
    run = rt.create(_request())
    _set_steps(root, {"write_summary": {"role": "auditor"}})   # the definition changes while the run is queued
    _drain(rt)

    failed = rt.store.get_run(run["id"])
    assert failed["status"] == "failed" and failed["llm_calls"] == 0
    assert not any(e["event_type"] == "step_started" for e in failed["events"])
    issues = next(e["payload"]["issues"] for e in failed["events"] if e["event_type"] == "workflow_failed")
    assert [(issue["code"], issue["step_id"]) for issue in issues] == [("unknown_role", "write_summary")]
    assert rt.prompts in (None, [])


def test_the_team_version_is_bound_before_a_worker_can_take_the_run(tmp_path, close):
    rt = _runtime(tmp_path, "native", _bundle(tmp_path, TWO_ROLES))
    close(rt.services)
    taken: list[str] = []

    def fast_worker(run_id, event_type, payload, event_id):
        # A worker that takes the run the moment it can see it.
        if event_type == "workflow_started" and not taken:
            taken.append(run_id)
            rt.worker.process_once()

    rt.store.db.event_listeners.append(fast_worker)
    run = rt.create(_request())

    assert taken == [run["id"]]
    done = rt.store.get_run(run["id"])
    assert done["status"] == "completed"
    kinds = [event["event_type"] for event in done["events"]]
    assert kinds[:3] == ["workflow_started", "team_version_bound", "run_started"]
    assert _started(done, "extract_key_points")["role"] == "analyst"
