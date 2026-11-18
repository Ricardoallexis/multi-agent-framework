# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Bundles in the new GUI (GUI-T13): ``multiagent gui --bundle`` and the general example bundle.

* Registration: ``multiagent gui`` and ``python -m multiagent.gui`` take ``--bundle NAME=FOLDER`` (repeatable)
  and register it through ``create_app(bundles=...)``.
* The ``examples/bundles/general`` bundle: structure, declared inputs, loading, validation and a full Mock run
  of each of its workflows.

Tests marked ``needs_t11`` read the example workflows through the real loader. Those YAML files declare
``description`` and ``inputs:`` (the schema of GUI-T11), which a loader without GUI-T11 rejects as
``unknown_field``; the tests skip with that reason and un-skip by themselves once GUI-T11 is merged. The
"stripped" variants run the same checks on a copy without those keys, so the bundle is verified either way.
"""
from __future__ import annotations

import shutil
import string
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.gui import __main__ as gui_main
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.workflow_validation import WorkflowDefinitionError, parse_workflow_document

ROOT = Path(__file__).resolve().parents[1]
GENERAL = ROOT / "examples" / "bundles" / "general"
TEAM = ROOT / "examples" / "teams" / "software_development"

WORKFLOWS = {
    "collect_summary": {
        "steps": ["summarize", "collect"],
        "reviewed_after": [],
        "inputs": {"document_text": "The meeting moves to Wednesday at ten.", "focus": "Decisions"},
    },
    "summarize_document": {
        "steps": ["extract_key_points", "write_summary"],
        "reviewed_after": ["write_summary"],
        "inputs": {
            "document_text": "A partir de marzo, la reunion semanal sera los miercoles a las 10:00. Las actas se "
                             "subiran a la carpeta compartida antes de las 18:00.",
            "focus": "Decisiones y plazos", "audience": "Equipo directivo", "summary_length": "media",
        },
    },
    "research_options": {
        "steps": ["gather_findings", "compare_options", "recommend"],
        "reviewed_after": ["recommend"],
        "inputs": {
            "question": "Conviene pasar a reuniones semanales asincronas?",
            "background": "El equipo trabaja en dos zonas horarias.", "constraints": "Sin gasto adicional",
        },
    },
    "review_checklist": {
        "steps": ["check_items", "summarize_review"],
        "reviewed_after": ["summarize_review"],
        "inputs": {
            "document_text": "3.2 El sistema enviara un aviso al cliente. Entrega prevista: 15 de noviembre.",
            "checklist": "Cada requisito tiene un responsable\nLos plazos son concretos",
            "document_kind": "Especificacion de requisitos",
        },
    },
}
INPUT_KEYS = {"name", "label", "description", "example", "required", "type", "choices"}
INPUT_TYPES = {"text", "long_text", "choice"}
RUNTIME_NAMES = {"previous_output", "revision_feedback"}
PRIVATE_MARKERS = [".local", "canal-pareja", "REGLAS", "Flujo", "AGENTES", "Codex", "Copilot", "C:\\", "/home/",
                   "/Users/", "AppData", "api_key", "API_KEY", "password="]


# ---------------------------------------------------------------------------------------------
# GUI-T11 dependency: does the loader accept `description` / `inputs:` in a workflow?
# ---------------------------------------------------------------------------------------------
def _loader_accepts_declared_inputs() -> bool:
    path = GENERAL / "workflows" / "summarize_document.yaml"
    try:
        parse_workflow_document(path.read_bytes(), workflow_id="summarize_document", source=path)
    except WorkflowDefinitionError as exc:
        return not all(issue.code == "unknown_field" for issue in exc.issues)  # other errors must show up as failures
    return True


LOADER_ACCEPTS_DECLARED = _loader_accepts_declared_inputs()
needs_t11 = pytest.mark.skipif(
    not LOADER_ACCEPTS_DECLARED,
    reason="Depends on GUI-T11 (declared `description`/`inputs:` in workflow YAML): the current loader rejects them "
           "as unknown_field. This test un-skips by itself once GUI-T11 is merged.",
)


def _strip_declarations(source: Path, target: Path) -> Path:
    """Copy the bundle without `description` and `inputs:` (the format before GUI-T11)."""
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__"))
    for path in (target / "workflows").glob("*.yaml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document.pop("description", None)
        document.pop("inputs", None)
        for step in document["steps"]:
            step.pop("description", None)
        path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target


@pytest.fixture(params=["declared", "stripped"])
def bundle_dir(request, tmp_path: Path) -> Path:
    """The example as it is in the repository (needs GUI-T11), or a copy without the declarations."""
    if request.param == "declared":
        if not LOADER_ACCEPTS_DECLARED:
            pytest.skip("Depends on GUI-T11 (declared `description`/`inputs:`): the current loader rejects them. "
                        "Un-skips by itself once GUI-T11 is merged.")
        return GENERAL
    return _strip_declarations(GENERAL, tmp_path / "stripped" / "general")


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {"max_llm_calls": 4, "gui_token_file": tmp_path / "gui-token", **overrides}
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False, **values)


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def prompt_placeholders(template: str) -> set[str]:
    return {field.split(".")[0].split("[")[0] for _, field, _, _ in string.Formatter().parse(template) if field}


def is_input_name(name: str) -> bool:
    return name not in RUNTIME_NAMES and not name.endswith("_output")


# ---------------------------------------------------------------------------------------------
# `multiagent gui --bundle` / `python -m multiagent.gui --bundle`
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def launched(monkeypatch):
    """Replace the server start with a recorder of the arguments both entry points would use."""
    calls: list[dict] = []
    monkeypatch.setattr(gui_main, "run", lambda **kwargs: calls.append(kwargs))
    return calls


def run_cli(*argv: str) -> int:
    try:
        cli.main(list(argv))
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def test_the_option_is_defined_for_gui():
    parser = cli.build_parser()
    assert parser.parse_args(["gui"]).bundle is None
    args = parser.parse_args(["gui", "--bundle", "a=uno", "--bundle", "b=dos"])
    assert args.bundle == ["a=uno", "b=dos"]


def test_gui_registers_repeated_bundles(launched):
    argv = ["--bundle", f"general={GENERAL}", "--bundle", f"team={TEAM}", "--no-browser"]
    assert run_cli("gui", *argv) == 0
    gui_main.main(argv)  # `python -m multiagent.gui` takes the same option
    expected = cli._parse_ui_bundles([f"general={GENERAL}", f"team={TEAM}"])
    assert expected == {"general": GENERAL, "team": TEAM}
    assert launched == [{"port": 0, "real": False, "no_browser": True, "bundles": expected}] * 2


def test_without_bundles_the_gui_registers_none(launched):
    assert run_cli("gui") == 0
    gui_main.main([])
    assert [call["bundles"] for call in launched] == [{}, {}]


def test_gui_uses_the_parser_of_ui(launched, monkeypatch):
    seen = []

    def spy(values):
        seen.append(values)
        return {"spy": GENERAL}

    monkeypatch.setattr(cli, "_parse_ui_bundles", spy)
    assert run_cli("gui", "--bundle", "x=y") == 0
    gui_main.main(["--bundle", "z=w"])
    assert seen == [["x=y"], ["z=w"]]
    assert [call["bundles"] for call in launched] == [{"spy": GENERAL}] * 2


def test_the_help_of_both_entry_points_lists_the_option(capsys):
    for entry in (lambda: cli.main(["gui", "--help"]), lambda: gui_main.main(["--help"])):
        with pytest.raises(SystemExit) as caught:
            entry()
        assert caught.value.code == 0
        assert "--bundle NAME=FOLDER" in capsys.readouterr().out


@pytest.mark.parametrize("value, message", [
    ("sin-igual", "--bundle uses the format name=folder"),
    ("=carpeta", "--bundle uses the format name=folder"),
    ("nombre=", "--bundle uses the format name=folder"),
    ("nombre=" + str(ROOT / "no" / "existe"), "Bundle folder not found"),
])
def test_a_bad_bundle_option_is_refused_before_the_server_starts(launched, capsys, value, message):
    assert run_cli("gui", "--bundle", value) == 1
    assert f"ERROR: {message}" in capsys.readouterr().err
    with pytest.raises(SystemExit) as caught:  # the module: the message, no traceback
        gui_main.main(["--bundle", value])
    assert str(caught.value).startswith("ERROR: ") and message in str(caught.value)
    assert launched == []


@pytest.fixture
def server(tmp_path, monkeypatch):
    """Run the real launcher with uvicorn replaced by a recorder; yields the app it would have served."""
    import uvicorn

    started: list = []
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: started.append(self.config.app))
    monkeypatch.setattr(cli, "_check_port_free", lambda host, port: None)
    for name, value in (("APP_HOST", "127.0.0.1"), ("LOCAL_DIR", str(tmp_path / "local")), ("WORKER_ENABLED", "false"),
                        ("GUI_TOKEN_FILE", str(home / ".multiagent" / "gui-token")), ("MOCK_MODE", "true"),
                        ("GEMINI_API_KEY", ""), ("OPENAI_API_KEY", "")):
        monkeypatch.setenv(name, value)
    return SimpleNamespace(started=started, token_file=home / ".multiagent" / "gui-token", root=tmp_path)


def serve(server, **kwargs) -> TestClient:
    gui_main.run(port=8123, no_browser=True, **kwargs)
    assert len(server.started) == 1
    token = server.token_file.read_text(encoding="ascii").strip()
    client = TestClient(server.started[0], base_url="http://localhost", headers={"Authorization": f"Bearer {token}"})
    client.__enter__()
    return client


def test_the_gui_lists_the_registered_bundles_and_their_workflows(server, capsys):
    client = serve(server, bundles={"general": GENERAL, "team": TEAM})
    try:
        assert "Bundles: general, team" in capsys.readouterr().out
        assert client.get("/gui/api/bundles").json() == [{"name": "general"}, {"name": "team"}]
        general = client.get("/gui/api/workflows", params={"bundle": "general"}).json()
        assert sorted(item["id"] for item in general) == sorted(WORKFLOWS)
        team = client.get("/gui/api/workflows", params={"bundle": "team"}).json()
        assert [item["id"] for item in team] == ["software_development"]
        assert client.get("/gui/api/workflows", params={"bundle": "otro"}).status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_the_gui_without_bundles_lists_only_the_built_in_origin(server, capsys):
    client = serve(server)
    try:
        assert "Bundles:" not in capsys.readouterr().out
        assert client.get("/gui/api/bundles").json() == []
        assert {item["id"] for item in client.get("/gui/api/workflows").json()} >= {"social_post"}
    finally:
        client.__exit__(None, None, None)


def test_an_invalid_bundle_name_stops_the_launch(server, capsys):
    with pytest.raises(SystemExit) as caught:
        gui_main.main(["--bundle", f"nombre malo={GENERAL}", "--no-browser"])
    assert str(caught.value).startswith("ERROR: ") and "Invalid bundle name" in str(caught.value)
    assert server.started == []  # the server never started


@needs_t11
def test_the_gui_validates_the_registered_example_bundles(server):
    client = serve(server, bundles={"general": GENERAL, "team": TEAM})
    try:
        for name, workflows in (("general", sorted(WORKFLOWS)), ("team", ["software_development"])):
            reply = client.post(f"/api/v1/bundles/{name}/validate")
            assert reply.status_code == 200, reply.text
            assert reply.json() == {"bundle": name, "valid": True, "workflows": workflows}
    finally:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------------------------
# examples/bundles/general: structure and declared inputs (plain YAML, independent of GUI-T11)
# ---------------------------------------------------------------------------------------------
def test_the_example_has_the_bundle_layout():
    assert sorted(path.stem for path in (GENERAL / "workflows").glob("*.yaml")) == sorted(WORKFLOWS)
    assert (GENERAL / "contracts.py").is_file()
    assert (GENERAL / "catalog" / "agents.yaml").is_file() and (GENERAL / "catalog" / "models.yaml").is_file()
    for workflow_id in WORKFLOWS:
        document = read_yaml(GENERAL / "workflows" / f"{workflow_id}.yaml")
        assert document["id"] == workflow_id
        assert [step["id"] for step in document["steps"]] == WORKFLOWS[workflow_id]["steps"]
        assert [step["id"] for step in document["steps"] if step.get("checkpoint_after")] == WORKFLOWS[workflow_id]["reviewed_after"]
        assert 2 <= len(document["steps"]) <= 4  # fits the default model-call budget of 4
        for step in document["steps"]:
            if step.get("function"):
                assert "agent" not in step and "prompt_id" not in step
            else:
                assert (GENERAL / "prompts" / f"{step['prompt_id']}.v{step['prompt_version']}.md").is_file()


@pytest.mark.parametrize("workflow_id", sorted(WORKFLOWS))
def test_every_workflow_describes_itself_and_declares_its_inputs(workflow_id):
    document = read_yaml(GENERAL / "workflows" / f"{workflow_id}.yaml")
    assert isinstance(document["description"], str) and len(document["description"]) > 30
    assert all(isinstance(step["description"], str) and step["description"].strip() for step in document["steps"])
    inputs = document["inputs"]
    names = [item["name"] for item in inputs]
    assert len(names) == len(set(names))
    assert any(item["required"] for item in inputs), "a workflow needs at least one required input"
    for item in inputs:
        assert set(item) <= INPUT_KEYS and {"name", "label", "description", "example", "required", "type"} <= set(item), item
        assert item["type"] in INPUT_TYPES
        assert isinstance(item["required"], bool)
        for text in ("label", "description", "example"):
            assert isinstance(item[text], str) and item[text].strip(), (item["name"], text)
        if item["type"] == "choice":
            assert item["choices"] and item["example"] in item["choices"]
        else:
            assert "choices" not in item
            assert len(item["example"]) >= 10, f"{item['name']}: the example should be concrete"


@pytest.mark.parametrize("workflow_id", sorted(WORKFLOWS))
def test_declared_inputs_are_exactly_the_placeholders_of_the_prompts(workflow_id):
    document = read_yaml(GENERAL / "workflows" / f"{workflow_id}.yaml")
    used = set()
    for step in document["steps"]:
        if step.get("function"):
            continue
        template = (GENERAL / "prompts" / f"{step['prompt_id']}.v{step['prompt_version']}.md").read_text(encoding="utf-8")
        used |= {name for name in prompt_placeholders(template) if is_input_name(name)}
    assert {item["name"] for item in document["inputs"]} == used


def test_the_example_is_neutral_and_free_of_private_references():
    files = [path for path in GENERAL.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    assert {path.suffix for path in files} <= {".py", ".yaml", ".md"}
    for path in files:
        text = path.read_text(encoding="utf-8")
        found = [marker for marker in PRIVATE_MARKERS if marker in text]
        assert not found, f"{path.relative_to(GENERAL)} mentions private or local details: {found}"
    for path in (ROOT / "multiagent").rglob("*.py"):  # the core knows nothing about the example
        text = path.read_text(encoding="utf-8")
        assert not any(workflow_id in text for workflow_id in WORKFLOWS), path.name


# ---------------------------------------------------------------------------------------------
# examples/bundles/general: loading, validation and Mock runs
# ---------------------------------------------------------------------------------------------
def test_the_bundle_contracts_load_and_their_samples_validate(tmp_path):
    bundle = load_bundle(GENERAL, make_settings(tmp_path))  # contracts, catalog and prompts; workflows are read later
    used = {step["contract"] for workflow_id in WORKFLOWS
            for step in read_yaml(GENERAL / "workflows" / f"{workflow_id}.yaml")["steps"]}
    assert used == set(bundle.sample_outputs)
    for contract, sample in bundle.sample_outputs.items():
        bundle.output_schemas[contract].model_validate(sample)


@needs_t11
def test_the_example_as_shipped_loads_with_the_real_loader(tmp_path):
    bundle = load_bundle(GENERAL, make_settings(tmp_path))
    for workflow_id, expected in WORKFLOWS.items():
        definition = bundle.load_workflow(workflow_id)
        assert [step.id for step in definition.steps] == expected["steps"]


def test_bundle_loads_and_validates(bundle_dir, tmp_path):
    bundle = load_bundle(bundle_dir, make_settings(tmp_path))
    for workflow_id, expected in WORKFLOWS.items():
        definition = bundle.load_workflow(workflow_id)  # agents, prompts, contracts and models all resolve
        assert [step.id for step in definition.steps] == expected["steps"]
        assert {step.agent for step in definition.steps if step.agent is not None} <= set(bundle.catalog.agents)
        assert {step.contract for step in definition.steps} <= set(bundle.sample_outputs)


def test_the_gui_derives_exactly_the_declared_inputs(bundle_dir, tmp_path):
    services = ApplicationServices.create(make_settings(tmp_path), bundles={"general": bundle_dir}, dry_run=True)
    try:
        for workflow_id in WORKFLOWS:
            declared = {item["name"] for item in read_yaml(GENERAL / "workflows" / f"{workflow_id}.yaml")["inputs"]}
            spec = services.definitions.workflow_inputs(workflow_id, bundle="general")
            assert {item["name"] for item in spec["inputs"]} == declared
    finally:
        services.runs.close()


@pytest.mark.parametrize("workflow_id", sorted(WORKFLOWS))
def test_each_workflow_runs_end_to_end_in_mock_mode(bundle_dir, tmp_path, workflow_id):
    expected = WORKFLOWS[workflow_id]
    services = ApplicationServices.create(make_settings(tmp_path), bundles={"general": bundle_dir}, dry_run=True)
    try:
        run = services.runs.create(RunRequest(
            project_name="Ejemplo general", workflow_id=workflow_id, inputs=dict(expected["inputs"]),
        ), bundle="general")
        worker = services.runs._bundle_runtime("general").worker

        assert worker.process_once()
        waiting = services.runs.get(run["id"])
        if expected["reviewed_after"]:
            assert (waiting["status"], waiting["waiting_step"]) == ("waiting_human", expected["reviewed_after"][-1])
            done = services.runs.approve(run["id"])
        else:
            done = waiting

        assert (done["status"], done["bundle"]) == ("completed", "general")
        events = [event["event_type"] for event in services.runs.events(run["id"])]
        assert "budget_exhausted" not in events
        assert events.count("step_completed") == len(expected["steps"])
        assert events.count("human_approved") == len(expected["reviewed_after"])
        assert events[-1] == "workflow_completed"
        artifacts = {artifact["step_id"] for artifact in services.runs.artifacts(run["id"])}
        assert artifacts == set(expected["steps"])
    finally:
        services.runs.close()
