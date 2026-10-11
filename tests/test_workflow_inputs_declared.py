# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI-T11: workflows declare their inputs (label, description, example, type, choices) and describe themselves.

Covers the format and its validation, the merge of declared inputs with the ones derived from
prompt placeholders, the GUI endpoint, the bundled workflows and the static client code.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.contracts import OUTPUT_SCHEMAS
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.prompts import PromptManager
from multiagent.services import ApplicationServices
from multiagent.services import workflow_inputs
from multiagent.workflow_validation import (
    WorkflowDefinitionError, parse_workflow_document, validate_workflow_definition,
)
from multiagent.workflows import WorkflowCatalog

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = sorted((ROOT / "workflows").glob("*.yaml"))
SPEC_REVIEW = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"

RESEARCH = {
    "id": "research", "agent": "researcher", "prompt_id": "researcher/research", "prompt_version": 2, "skills": [],
    "contract": "ResearchOutput", "preferred_model": "gemini_grounded", "fallback_model": None,
    "requires_web": True, "when": "requires_web", "checkpoint_after": False,
}
CREATE = {
    "id": "create", "agent": "creator", "prompt_id": "creator/social_post", "prompt_version": 2,
    "skills": ["social_media"], "contract": "ContentOutput", "preferred_model": "local_default",
    "fallback_model": "cloud_fast", "requires_web": False, "when": None, "checkpoint_after": True,
}
VALID = {"id": "sample", "max_iterations": 2, "steps": [RESEARCH, CREATE]}
DECLARED = {
    "description": "Escribe una publicación a partir de un tema.",
    "inputs": [
        {"name": "topic", "label": "Tema", "description": "De qué trata.", "example": "Soporte 24/7"},
        {"name": "instructions", "label": "Instrucciones", "type": "long_text", "example": "Tono cercano"},
        {"name": "platform", "type": "choice", "choices": ["LinkedIn", "Instagram"], "example": "LinkedIn",
         "required": False},
    ],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=tmp_path / "gui-token")


def document(**changes) -> dict:
    raw = copy.deepcopy(VALID)
    raw.update(copy.deepcopy(changes))
    return raw


def parse(raw: dict):
    return parse_workflow_document(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), workflow_id="sample")


def parse_error(raw: dict) -> WorkflowDefinitionError:
    with pytest.raises(WorkflowDefinitionError) as info:
        parse(raw)
    return info.value


def with_input(**changes) -> dict:
    return document(inputs=[{"name": "topic", **changes}])


def codes(error: WorkflowDefinitionError) -> set[tuple[str, str]]:
    return {(issue.code, issue.location) for issue in error.issues}


# ---------- format: old workflows load unchanged, declared ones keep their words ----------

def test_a_workflow_without_the_new_fields_loads_as_before():
    definition = parse(document())
    assert definition.description is None and definition.inputs == []
    assert [step.description for step in definition.steps] == [None, None]


def test_null_inputs_means_nothing_declared():
    assert parse(document(inputs=None)).inputs == []


def test_declared_description_and_inputs_are_read():
    raw = document(**DECLARED)
    raw["steps"][1]["description"] = "Redacta la publicación."
    definition = parse(raw)
    assert definition.description == DECLARED["description"]
    assert definition.steps[1].description == "Redacta la publicación."
    topic, instructions, platform = definition.inputs
    assert (topic.name, topic.label, topic.description, topic.example) == ("topic", "Tema", "De qué trata.", "Soporte 24/7")
    assert (topic.required, topic.type, topic.choices) == (None, "text", [])  # defaults
    assert instructions.type == "long_text" and instructions.required is None
    assert (platform.type, platform.choices, platform.required) == ("choice", ["LinkedIn", "Instagram"], False)


def test_declared_workflow_passes_the_full_validation(settings):
    raw = document(**DECLARED)
    raw["steps"][0]["description"] = "Busca datos."
    validate_workflow_definition(parse(raw), catalog=Catalog(settings), settings=settings, output_schemas=OUTPUT_SCHEMAS)


@pytest.mark.parametrize("fields,expected", [
    ({"inputs": "topic"}, ("invalid_type", "inputs")),
    ({"inputs": ["topic"]}, ("invalid_type", "inputs[0]")),
    ({"inputs": [{"label": "Tema"}]}, ("missing_field", "inputs[0].name")),
    ({"inputs": [{"name": "my-topic"}]}, ("invalid_identifier", "inputs[0].name")),
    ({"inputs": [{"name": 3}]}, ("invalid_type", "inputs[0].name")),
    ({"inputs": [{"name": "previous_output"}]}, ("invalid_value", "inputs[0].name")),
    ({"inputs": [{"name": "revision_feedback"}]}, ("invalid_value", "inputs[0].name")),
    ({"inputs": [{"name": "research_output"}]}, ("invalid_value", "inputs[0].name")),
    ({"inputs": [{"name": "topic"}, {"name": "audience"}, {"name": "topic"}]}, ("duplicate_input", "inputs[2].name")),
    ({"description": ""}, ("invalid_value", "description")),
    ({"description": 7}, ("invalid_type", "description")),
])
def test_bad_declarations_name_the_field(fields, expected):
    assert expected in codes(parse_error(document(**fields)))


@pytest.mark.parametrize("changes,expected", [
    ({"label": "  "}, ("invalid_value", "inputs[0].label")),
    ({"label": 5}, ("invalid_type", "inputs[0].label")),
    ({"description": ["a"]}, ("invalid_type", "inputs[0].description")),
    ({"example": 12}, ("invalid_type", "inputs[0].example")),
    ({"required": "yes"}, ("invalid_type", "inputs[0].required")),
    ({"type": "number"}, ("invalid_value", "inputs[0].type")),
    ({"type": ["text"]}, ("invalid_type", "inputs[0].type")),
    ({"choices": ["a", "b"]}, ("invalid_value", "inputs[0].choices")),
    ({"type": "long_text", "choices": ["a"]}, ("invalid_value", "inputs[0].choices")),
    ({"type": "choice"}, ("invalid_value", "inputs[0].choices")),
    ({"type": "choice", "choices": []}, ("invalid_value", "inputs[0].choices")),
    ({"type": "choice", "choices": ["a", "a"]}, ("invalid_value", "inputs[0].choices")),
    ({"type": "choice", "choices": ["a", 2]}, ("invalid_value", "inputs[0].choices")),
    ({"type": "choice", "choices": ["a", " "]}, ("invalid_value", "inputs[0].choices")),
    ({"type": "choice", "choices": "ab"}, ("invalid_type", "inputs[0].choices")),
    ({"type": "choice", "choices": ["a", "b"], "example": "c"}, ("invalid_value", "inputs[0].example")),
])
def test_bad_input_values_name_the_field(changes, expected):
    assert expected in codes(parse_error(with_input(**changes)))


def test_unknown_input_keys_are_rejected_with_a_hint():
    error = parse_error(with_input(exmaple="Soporte"))
    assert ("unknown_field", "inputs[0].exmaple") in codes(error)
    assert "did you mean 'example'" in str(error)
    assert "unknown_field" in str(parse_error(with_input(placeholder="x")))


def test_unknown_step_and_root_keys_still_get_rejected():
    raw = document(summary="x")
    raw["steps"][0]["detail"] = "x"
    assert {("unknown_field", "summary"), ("unknown_field", "steps[0].detail")} <= codes(parse_error(raw))


def test_every_problem_is_reported_in_one_pass():
    raw = document(inputs=[{"name": "topic", "type": "nope", "required": "x"}, {"name": "topic", "label": ""}])
    raw["steps"][1]["description"] = ""
    assert {
        ("invalid_value", "inputs[0].type"), ("invalid_type", "inputs[0].required"),
        ("duplicate_input", "inputs[1].name"), ("invalid_value", "inputs[1].label"),
        ("invalid_value", "steps[1].description"),
    } <= codes(parse_error(raw))


# ---------- merge: declared inputs win, undeclared placeholders are derived ----------

def spec_for(raw: dict, settings: Settings) -> dict:
    return workflow_inputs.derive_inputs(parse(raw), PromptManager(settings))


def by_name(spec: dict) -> dict:
    return {item["name"]: item for item in spec["inputs"]}


def test_without_declarations_every_input_is_derived(settings):
    spec = spec_for(document(), settings)
    assert spec["source"] == "prompt_placeholders" and spec["description"] is None and spec["warnings"] == []
    assert {item["source"] for item in spec["inputs"]} == {"derived"}
    topic = by_name(spec)["topic"]
    assert topic["label"] == "Topic" and topic["prompt_line"] == "Topic: {topic}"
    assert (topic["description"], topic["example"], topic["type"], topic["choices"]) == (None, None, "text", [])
    assert topic["required"] is True
    assert "previous_output" not in by_name(spec) and "revision_feedback" not in by_name(spec)


def test_declared_inputs_win_and_the_rest_stay_derived(settings):
    spec = spec_for(document(**DECLARED), settings)
    assert spec["source"] == "declared" and spec["description"] == DECLARED["description"]
    items = by_name(spec)
    # declared first, in the declared order; then the undeclared placeholders in order of first use
    assert [item["source"] for item in spec["inputs"]][:3] == ["declared"] * 3
    assert [item["name"] for item in spec["inputs"]][:3] == ["topic", "instructions", "platform"]
    assert {name for name, item in items.items() if item["source"] == "derived"} >= {"audience", "objective"}

    topic = items["topic"]
    assert (topic["label"], topic["description"], topic["example"], topic["type"]) == (
        "Tema", "De qué trata.", "Soporte 24/7", "text")
    assert topic["prompt_line"] == "Topic: {topic}" and topic["used_in"] == ["research", "create"]
    assert items["instructions"]["type"] == "long_text"
    platform = items["platform"]
    assert (platform["type"], platform["choices"], platform["example"]) == ("choice", ["LinkedIn", "Instagram"], "LinkedIn")
    audience = items["audience"]
    assert audience["label"] == "Audience" and audience["description"] is None and audience["example"] is None
    assert audience["prompt_line"] and audience["source"] == "derived"


def test_a_missing_label_falls_back_to_the_readable_name(settings):
    raw = document(inputs=[{"name": "audience", "description": "Quién lo lee."}])
    audience = by_name(spec_for(raw, settings))["audience"]
    assert audience["source"] == "declared" and audience["label"] == "Audience"


def test_required_follows_todays_rule_unless_declared(settings):
    raw = document(inputs=[
        {"name": "topic"}, {"name": "objective"},  # objective is only used by the skipped research step
        {"name": "research_context"},               # context names are optional
        {"name": "audience", "required": False},    # declared optional although a step needs it
        {"name": "platform", "required": True},
        {"name": "strategy_context", "required": True},
    ])
    items = by_name(spec_for(raw, settings))
    assert {name: items[name]["required"] for name in items} == {
        "topic": True, "objective": False, "research_context": False, "audience": False, "platform": True,
        "strategy_context": True, "instructions": True, "brand_context": False}
    assert items["objective"]["skipped_only"] is True and items["topic"]["skipped_only"] is False


def test_missing_required_uses_the_declared_rule(settings):
    spec = spec_for(document(inputs=[{"name": "platform", "required": False}, {"name": "objective", "required": True}]), settings)
    values = {"topic": "x", "audience": "x", "instructions": "x"}
    assert workflow_inputs.missing_required(spec, values) == ["objective"]
    assert workflow_inputs.missing_required(spec, {**values, "objective": "  "}) == ["objective"]
    assert workflow_inputs.missing_required(spec, {**values, "objective": "y"}) == []


def test_a_declared_name_no_prompt_uses_is_a_warning_and_is_not_offered(settings):
    raw = document(inputs=[{"name": "topic"}, {"name": "topik", "label": "Tema"}])
    spec = spec_for(raw, settings)
    assert "topik" not in by_name(spec)
    assert [(w["code"], w["location"]) for w in spec["warnings"]] == [("unused_input", "inputs[1].name")]
    assert "topik" in spec["warnings"][0]["message"]
    assert by_name(spec)["topic"]["source"] == "declared"


def test_step_descriptions_travel_with_the_steps(settings):
    raw = document()
    raw["steps"][1]["description"] = "Redacta."
    spec = spec_for(raw, settings)
    assert [(s["id"], s["description"], s["skipped_by_default"]) for s in spec["steps"]] == [
        ("research", None, True), ("create", "Redacta.", False)]


def test_preview_still_renders_with_declared_inputs(settings):
    definition = parse(document(**DECLARED))
    prompts = PromptManager(settings)
    spec = workflow_inputs.derive_inputs(definition, prompts)
    inputs = {name: f"valor {name}" for name in by_name(spec)}
    result = workflow_inputs.preview(definition, prompts, spec, inputs)
    assert result["source"] == "declared" and result["missing_inputs"] == []
    assert "Topic: valor topic" in result["steps"][1]["text"]


# ---------- bundled workflows declare everything they use ----------

@pytest.mark.parametrize("path", BUNDLED, ids=lambda p: p.stem)
def test_bundled_workflows_declare_all_their_inputs(path, settings):
    catalog = WorkflowCatalog(settings)
    definition = catalog.load(path.stem)
    spec = workflow_inputs.derive_inputs(definition, PromptManager(settings))
    assert spec["warnings"] == []  # no declared name is unused
    undeclared = [item["name"] for item in spec["inputs"] if item["source"] != "declared"]
    assert undeclared == [], f"{path.stem} uses placeholders it does not declare: {undeclared}"
    assert spec["description"] and spec["description"].strip()
    assert all(step["description"] for step in spec["steps"])
    for item in spec["inputs"]:
        assert item["label"] and item["description"] and item["example"], item["name"]
        assert item["type"] in {"text", "long_text", "choice"}
        if item["type"] == "choice":
            assert item["example"] in item["choices"]
    # the declared order is what the form shows
    assert [item["name"] for item in spec["inputs"]] == [item.name for item in definition.inputs]
    validate_workflow_definition(definition, catalog=Catalog(settings), settings=settings, output_schemas=OUTPUT_SCHEMAS)


def test_bundled_required_inputs_are_the_ones_the_runnable_steps_need(settings):
    def required(workflow_id: str) -> set[str]:
        spec = workflow_inputs.derive_inputs(WorkflowCatalog(settings).load(workflow_id), PromptManager(settings))
        return {item["name"] for item in spec["inputs"] if item["required"]}

    assert required("social_post") == {"topic", "platform", "audience", "instructions"}
    assert required("social_post_full") == {"topic", "objective", "platform", "audience", "instructions"}


# ---------- GUI endpoint ----------

@pytest.fixture
def env(tmp_path, settings):
    bundle = tmp_path / "bundle"
    shutil.copytree(SPEC_REVIEW, bundle)
    workflow = bundle / "workflows" / "spec_review.yaml"
    text = workflow.read_text(encoding="utf-8")
    text = text.replace("max_iterations: 2\n", """description: Revisa una especificación y propone casos de prueba.
max_iterations: 2
inputs:
  - name: spec_text
    label: Especificación
    description: El texto que se va a revisar.
    example: El usuario puede restablecer su contraseña por correo.
    type: long_text
  - name: focus
    label: Enfoque
    description: Qué aspecto revisar primero.
    example: seguridad
    type: choice
    choices: [seguridad, rendimiento]
    required: false
  - name: ghost
    label: Fantasma
""", 1)
    workflow.write_text(text, encoding="utf-8")
    prompt = bundle / "prompts" / "spec" / "extract_requirements.v1.md"
    prompt.write_text(prompt.read_text(encoding="utf-8").replace(
        "# Specification\n", "# Focus\n{focus}\n\n# Reader\nRead for: {reader}\n\n# Specification\n"), encoding="utf-8")
    services = ApplicationServices.create(settings, bundles={"team": bundle}, dry_run=True)
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, token=token)
    services.runs.close()


def get(env, path, **kwargs):
    return env.client.get("/gui/api" + path, headers={"Authorization": f"Bearer {env.token}"}, **kwargs)


ITEM_KEYS = {"name", "label", "description", "example", "type", "choices", "source", "required", "context",
             "skipped_only", "used_in", "needed_by", "prompt_line"}


def test_endpoint_returns_declared_and_derived_inputs(env):
    response = get(env, "/workflows/spec_review/inputs", params={"bundle": "team"})
    assert response.status_code == 200
    spec = response.json()
    assert spec["description"] == "Revisa una especificación y propone casos de prueba."
    assert spec["source"] == "declared" and spec["bundle"] == "team" and spec["id"] == "spec_review"
    assert all(set(item) == ITEM_KEYS for item in spec["inputs"])
    items = {item["name"]: item for item in spec["inputs"]}
    assert [item["name"] for item in spec["inputs"]] == ["spec_text", "focus", "reader"]  # ghost is not offered
    assert (items["spec_text"]["label"], items["spec_text"]["type"], items["spec_text"]["source"]) == (
        "Especificación", "long_text", "declared")
    assert items["spec_text"]["example"] == "El usuario puede restablecer su contraseña por correo."
    assert items["spec_text"]["required"] is True
    assert (items["focus"]["type"], items["focus"]["choices"], items["focus"]["required"]) == (
        "choice", ["seguridad", "rendimiento"], False)
    assert items["reader"]["source"] == "derived" and items["reader"]["prompt_line"] == "Read for: {reader}"
    assert items["reader"]["description"] is None and items["reader"]["example"] is None
    assert [warning["code"] for warning in spec["warnings"]] == ["unused_input"]
    assert "ghost" in spec["warnings"][0]["message"]
    assert [step["id"] for step in spec["steps"]] == ["extract_requirements", "assess_risks", "draft_test_plan"]
    assert all("description" in step for step in spec["steps"])


def test_endpoint_describes_the_bundled_workflows(env):
    spec = get(env, "/workflows/social_post/inputs").json()
    assert spec["source"] == "declared" and "publicación" in spec["description"]
    items = {item["name"]: item for item in spec["inputs"]}
    assert [item["name"] for item in spec["inputs"]][:4] == ["topic", "platform", "audience", "instructions"]
    assert items["topic"]["label"] == "Tema" and items["topic"]["example"] and items["topic"]["description"]
    assert items["instructions"]["type"] == "long_text"
    assert all(item["source"] == "declared" for item in spec["inputs"])
    assert {name for name, item in items.items() if item["required"]} == {"topic", "platform", "audience", "instructions"}
    assert [(step["id"], bool(step["description"])) for step in spec["steps"]] == [("research", True), ("create", True)]


def test_preview_accepts_the_declared_inputs(env):
    body = {"workflow_id": "spec_review", "bundle": "team",
            "inputs": {"spec_text": "Reset by email.", "focus": "seguridad", "reader": "QA"}}
    response = env.client.post("/gui/api/runs/preview", json=body, headers={"Authorization": f"Bearer {env.token}"})
    assert response.status_code == 200
    step = response.json()["steps"][0]
    assert "seguridad" in step["text"] and "Read for: QA" in step["text"] and response.json()["missing_inputs"] == []


# ---------- static client code ----------

CLIENT_FILES = ["create-run.js", "text.js"]
FORBIDDEN = [r"\binnerHTML\b", r"\bouterHTML\b", r"insertAdjacentHTML", r"dangerouslySetInnerHTML", r"\beval\s*\(",
             r"\bnew\s+Function\b", r"document\.write", r"""\bon[a-z]+\s*=\s*["']"""]


@pytest.mark.parametrize("name", CLIENT_FILES)
def test_client_code_for_inputs_injects_no_html(name):
    text = (STATIC_DIR / "js" / name).read_text(encoding="utf-8")
    for pattern in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {pattern}"


def test_create_run_shows_description_example_and_the_right_control():
    text = (STATIC_DIR / "js" / "create-run.js").read_text(encoding="utf-8")
    assert "spec.description" in text and "item.description" in text           # workflow and field descriptions
    assert "placeholder=" in text and "exampleText(item)" in text              # example as placeholder text
    assert re.search(r'item\.type === "choice"', text) and "<select" in text   # choice -> select
    assert '"long_text"' in text and "<textarea" in text                        # long text -> text area
    assert "step.description" in text                                          # step descriptions


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_text_helpers_for_inputs():
    script = """
      import { exampleText, inputHelp, inputsNote } from %s;
      const declared = { source: "declared", name: "a", example: "Soporte", type: "text", used_in: ["x"], prompt_line: "A: {a}" };
      const derived = { source: "derived", name: "b", example: null, type: "text", used_in: ["x", "y"], prompt_line: "B: {b}" };
      const choice = { source: "declared", name: "c", example: "uno", type: "choice", used_in: [] };
      console.log(JSON.stringify({
        example: exampleText(declared), noExample: exampleText(derived), choiceExample: exampleText(choice),
        declaredHelp: inputHelp(declared), derivedHelp: inputHelp(derived),
        none: inputsNote({ inputs: [] }), all: inputsNote({ inputs: [declared] }),
        mixed: inputsNote({ inputs: [declared, derived] }), derivedOnly: inputsNote({ inputs: [derived] }),
      }));
    """ % json.dumps((STATIC_DIR / "js" / "text.js").as_uri())
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["example"] == "Ej.: Soporte" and out["noExample"] is None and out["choiceExample"] is None
    assert "En el prompt" not in out["declaredHelp"] and "Se usa en: x." in out["declaredHelp"]
    assert "En el prompt: «B: {b}»" in out["derivedHelp"]
    assert out["none"] == "Este workflow no pide entradas."
    assert out["all"] == "Las entradas las declara el propio workflow."
    assert "línea del prompt" in out["mixed"] and "no declara sus entradas" in out["derivedOnly"]
