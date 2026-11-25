# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project templates as data (PRY-T04): the schema, the validator, the loader and the shipped files."""
from __future__ import annotations

import copy
import fnmatch
import json
import tomllib
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from multiagent.project_templates import (
    MAX_DOCUMENT_BYTES,
    MAX_TASKS,
    SCHEMA_ID,
    TemplateError,
    TemplateNotFound,
    list_templates,
    load_template,
    parse_template,
    validate_template,
)
from multiagent.project_templates import loader, validation
from multiagent.services.errors import INVALID_REQUEST, NOT_FOUND
from multiagent.tasks.envelopes import (MAX_CONSTRAINT_CHARS, MAX_CONTEXT_REFS, MAX_DEPENDENCIES,
                                        MAX_OBJECTIVE_CHARS)
from multiagent.tasks.graph import REQUIRES

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "multiagent" / "project_templates"
FIXTURE = ROOT / "tests" / "fixtures" / "projects" / "proyecto-sintetico.yaml"
SHIPPED = ["campana-marketing", "desarrollo-software", "vacia"]


# -------- small document builders (every name here is invented) --------
def task(tid: str, **extra: Any) -> dict[str, Any]:
    return {"id": tid, "title": f"Tarea {tid}", **extra}


def stage(code: str, tasks: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"code": code, "title": f"Etapa {code}", "tasks": tasks or [], **extra}


def document(stages: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"schema": SCHEMA_ID, "id": "ejemplo", "title": "Ejemplo", "stages": stages or [], **extra}


def one_stage(tasks: list[Any], **extra: Any) -> dict[str, Any]:
    return document([stage("AAA", tasks, **extra)])


def valid_plan() -> dict[str, Any]:
    """Two stages with phases, every dependency form, dates, a requirement, an actor and a context reference."""
    return document(
        description="Documento de prueba.",
        stages=[
            stage("AAA", [
                task("T01", phase="Fase", due="2026-11-06"),
                task("T02", phase="Fase", depends_on=["T01"], objective="Objetivo corto.", done_when="Hecho",
                     assignee="agent:claude/sonnet-5.5", context_refs=["doc://docs/TASKS.md#references"]),
            ], phases=["Fase"], planned={"start": "2026-11-02", "end": "2026-11-13"}),
            stage("BBB", [
                task("T01", depends_on=["stage:AAA"], requires="submitted", assignee="user:ejemplo"),
                task("T02", depends_on=["AAA-T02", "T01"], assignee="person:persona-prueba"),
            ]),
        ],
    )


# -------- the schema file --------
def _schema() -> dict[str, Any]:
    return json.loads((TEMPLATE_DIR / "schema.json").read_text(encoding="utf-8"))


def _schema_nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("properties", {}).values():
        yield from _schema_nodes(child)
    for child in node.get("$defs", {}).values():
        yield from _schema_nodes(child)
    if "items" in node:
        yield from _schema_nodes(node["items"])


def test_schema_declares_data_fields_only():
    schema = _schema()
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"schema", "id", "title", "description", "visibility", "stages"}
    assert set(schema["$defs"]["stage"]["properties"]) == {"code", "title", "phases", "planned", "tasks"}
    assert set(schema["$defs"]["planned"]["properties"]) == {"start", "end"}
    assert set(schema["$defs"]["task"]["properties"]) == {
        "id", "title", "objective", "done_when", "phase", "assignee", "depends_on", "requires", "due", "context_refs",
    }
    for name, node in schema["$defs"].items():
        if node.get("type") == "object":
            assert node["additionalProperties"] is False, name


def test_schema_uses_only_keywords_the_validator_implements():
    for node in _schema_nodes(_schema()):
        assert set(node) <= validation._KEYWORDS, sorted(set(node) - validation._KEYWORDS)


def test_schema_limits_are_the_limits_of_the_task_envelope():
    task_node = _schema()["$defs"]["task"]["properties"]
    assert task_node["requires"]["enum"] == list(REQUIRES)
    assert task_node["context_refs"]["maxItems"] == MAX_CONTEXT_REFS
    assert task_node["depends_on"]["maxItems"] == MAX_DEPENDENCIES
    assert task_node["objective"]["maxLength"] == MAX_OBJECTIVE_CHARS
    # done_when is stored as the restriction "Terminado cuando: ...", which must fit one task restriction
    assert task_node["done_when"]["maxLength"] == MAX_CONSTRAINT_CHARS - len("Terminado cuando: ")
    assert MAX_TASKS == 200
    assert SCHEMA_ID == "maf.project-template/1"


# -------- valid documents --------
def test_empty_template_and_full_plan_are_valid():
    assert validate_template(document()) == []
    assert validate_template(valid_plan()) == []


def test_visibility_private_is_a_legal_value_in_a_document():
    assert validate_template(document(visibility="private")) == []


def test_yaml_and_json_texts_are_the_same_document():
    plan = parse_template(FIXTURE.read_bytes())
    assert parse_template(json.dumps(plan)) == plan
    assert parse_template(json.dumps(valid_plan())) == valid_plan()


def test_unquoted_dates_and_short_codes_stay_text():
    text = (
        "schema: maf.project-template/1\nid: ejemplo\ntitle: Ejemplo\nstages:\n"
        "  - code: NO\n    title: Si\n    phases: [2026, On]\n"
        "    planned: {start: 2026-11-02, end: 2026-11-13}\n"
        "    tasks:\n      - {id: T01, title: Uno, phase: 2026, due: 2026-11-06}\n"
    )
    parsed = parse_template(text)
    assert parsed["stages"][0]["code"] == "NO"
    assert parsed["stages"][0]["tasks"][0]["due"] == "2026-11-06"


# -------- rejections: each case names the problem --------
REJECTIONS: list[tuple[str, Callable[[], Any], str]] = [
    ("unknown top-level key", lambda: {**document(), "command": "rm -rf x"}, "unknown key 'command'"),
    ("unknown task key (a gate)", lambda: one_stage([task("T01", gate="tests")]), "unknown key 'gate'"),
    ("unknown stage key (a workflow)", lambda: document([stage("AAA", workflow="x")]), "unknown key 'workflow'"),
    ("unknown merge key", lambda: {**document(), "<<": {"id": "x"}}, "unknown key '<<'"),
    ("wrong schema version", lambda: {**document(), "schema": "maf.project-template/2"},
     "must be maf.project-template/1"),
    ("template id with spaces", lambda: {**document(), "id": "Mi plantilla"}, "id: must be"),
    ("missing title", lambda: {k: v for k, v in document().items() if k != "title"},
     "missing required key 'title'"),
    ("stages is not a list", lambda: {**document(), "stages": {"code": "AAA"}}, "stages: must be a list"),
    ("stage code in lowercase", lambda: document([stage("inv")]), "code: must be"),
    ("task id without zeros", lambda: one_stage([task("T1")]), "id: must be"),
    ("task is not a mapping", lambda: one_stage(["T01"]), "must be a mapping"),
    ("depends_on a missing task", lambda: one_stage([task("T01", depends_on=["T09"])]),
     "no task T09 in stage AAA"),
    ("depends_on a missing stage", lambda: one_stage([task("T01", depends_on=["ZZZ-T01"])]),
     "unknown stage ZZZ"),
    ("depends_on a missing stage by reference", lambda: one_stage([task("T01", depends_on=["stage:ZZZ"])]),
     "unknown stage ZZZ"),
    ("depends_on an empty stage", lambda: document([stage("AAA", [task("T01", depends_on=["stage:BBB"])]),
                                                    stage("BBB")]),
     "stage BBB has no tasks"),
    ("depends_on in a bad form", lambda: one_stage([task("T01", depends_on=["uno"])]), "depends_on[0]: must be"),
    ("depends_on repeats an entry", lambda: one_stage([task("T01"), task("T02", depends_on=["T01", "T01"])]),
     "items must be unique"),
    ("a task depends on itself", lambda: one_stage([task("T01", depends_on=["T01"])]),
     "dependency cycle AAA-T01 -> AAA-T01"),
    ("cycle inside a stage", lambda: one_stage([task("T01", depends_on=["T02"]), task("T02", depends_on=["T01"])]),
     "dependency cycle"),
    ("cycle across stages", lambda: document([
        stage("AAA", [task("T01", depends_on=["stage:BBB"])]),
        stage("BBB", [task("T01", depends_on=["AAA-T01"])]),
    ]), "dependency cycle"),
    ("phase not declared by the stage", lambda: one_stage([task("T01", phase="Otra")], phases=["Fase"]),
     "Otra is not one of the phases of stage AAA"),
    ("phase set but the stage declares none", lambda: one_stage([task("T01", phase="Fase")]),
     "Fase is not one of the phases of stage AAA"),
    ("repeated phase name", lambda: one_stage([task("T01")], phases=["Fase", "Fase"]), "items must be unique"),
    ("too many phases", lambda: one_stage([task("T01")], phases=[f"F{i}" for i in range(13)]),
     "at most 12 items"),
    ("repeated stage code", lambda: document([stage("AAA"), stage("AAA")]), "stage code AAA is already used"),
    ("repeated task id in a stage", lambda: one_stage([task("T01"), task("T01")]),
     "task id T01 is already used in stage AAA"),
    ("more than 30 stages", lambda: document([stage(f"S{i:02d}") for i in range(31)]), "at most 30 items"),
    ("more than 200 tasks in total", lambda: document([
        stage("AAA", [task(f"T{i:03d}") for i in range(1, 151)]),
        stage("BBB", [task(f"T{i:03d}") for i in range(1, 52)]),
    ]), "at most 200 tasks in total (found 201)"),
    ("done_when of 183 characters", lambda: one_stage([task("T01", done_when="x" * 183)]),
     "at most 182 characters"),
    ("objective too long", lambda: one_stage([task("T01", objective="x" * (MAX_OBJECTIVE_CHARS + 1))]),
     "at most 4000 characters"),
    ("assignee without an actor prefix", lambda: one_stage([task("T01", assignee="claude")]),
     "assignee: must be"),
    ("assignee with an empty name", lambda: one_stage([task("T01", assignee="agent:")]), "assignee: must be"),
    ("assignee longer than 100 characters", lambda: one_stage([task("T01", assignee="agent:" + "x" * 95)]),
     "at most 100 characters"),
    ("due that is not a date", lambda: one_stage([task("T01", due="noviembre")]),
     "due: must be a calendar date"),
    ("due that does not exist", lambda: one_stage([task("T01", due="2026-02-30")]),
     "due: must be a real calendar date"),
    ("planned start after end", lambda: document([stage("AAA", planned={"start": "2026-11-20", "end": "2026-11-02"})]),
     "planned: start is after end"),
    ("planned without an end", lambda: document([stage("AAA", planned={"start": "2026-11-02"})]),
     "missing required key 'end'"),
    ("requires without depends_on", lambda: one_stage([task("T01", requires="accepted")]),
     "requires: needs depends_on"),
    ("requires with another value", lambda: one_stage([task("T01", requires="done")]),
     "requires: must be one of accepted, submitted"),
    ("context reference with an unknown scheme", lambda: one_stage([task("T01", context_refs=["http://x"])]),
     "context_refs[0]"),
    ("too many context references",
     lambda: one_stage([task("T01", context_refs=[f"doc://a{i}.md" for i in range(21)])]),
     "at most 20 items"),
    ("visibility with another value", lambda: {**document(), "visibility": "public"},
     "must be one of normal, private"),
    ("the document is a list", lambda: [document()], "document: must be a mapping"),
]


@pytest.mark.parametrize(("case", "build", "expected"), REJECTIONS, ids=[case for case, _, _ in REJECTIONS])
def test_invalid_documents_are_rejected_with_the_problem_named(case, build, expected):
    errors = validate_template(build())
    assert errors, f"{case}: accepted"
    assert any(expected in error for error in errors), (case, errors)


def test_every_structural_problem_is_reported_at_once():
    errors = validate_template(document([stage("AAA", [task("T01", gate="x")])], command="rm"))
    assert any("unknown key 'command'" in error for error in errors)
    assert any("unknown key 'gate'" in error for error in errors)


def test_rules_wait_until_the_structure_is_valid():
    # An unknown key and a cycle together: only the structural error is reported, the rules do not run on bad data.
    doc = one_stage([task("T01", depends_on=["T02"], gate="x"), task("T02", depends_on=["T01"])])
    errors = validate_template(doc)
    assert errors and all("cycle" not in error for error in errors)


def test_validation_never_changes_the_document():
    doc = valid_plan()
    before = copy.deepcopy(doc)
    validate_template(doc)
    assert doc == before


# -------- parsing: plain data only --------
def test_anchors_and_aliases_are_refused():
    text = "schema: maf.project-template/1\nid: ejemplo\ntitle: &t Ejemplo\nstages: []\nalias: *t\n"
    with pytest.raises(TemplateError) as info:
        parse_template(text)
    assert "anchors and aliases are not allowed" in info.value.errors[0]


def test_duplicate_keys_are_refused():
    text = "schema: maf.project-template/1\nid: ejemplo\nid: otro\ntitle: Ejemplo\nstages: []\n"
    with pytest.raises(TemplateError) as info:
        parse_template(text)
    assert "duplicate key 'id'" in info.value.errors[0]


def test_python_tags_are_refused_and_never_run(capsys):
    text = ("schema: maf.project-template/1\nid: ejemplo\n"
            "title: !!python/object/apply:builtins.print [ejecutado]\nstages: []\n")
    with pytest.raises(TemplateError):
        parse_template(text)
    assert "ejecutado" not in capsys.readouterr().out


def test_json_with_a_duplicate_key_is_refused():
    text = '{"schema": "maf.project-template/1", "id": "ejemplo", "id": "otro", "title": "x", "stages": []}'
    with pytest.raises(TemplateError) as info:
        parse_template(text)
    assert "duplicate key" in info.value.errors[0]


def test_syntax_errors_name_the_line():
    with pytest.raises(TemplateError) as info:
        parse_template("schema: maf.project-template/1\ntitle: [sin cerrar\n")
    assert info.value.errors[0].startswith("invalid YAML at line ")


@pytest.mark.parametrize(("raw", "expected"), [
    (b"", "document is empty"),
    (b"- uno\n- dos\n", "document: must be a mapping"),
    (b"\xff\xfe\x00", "document must be UTF-8 text"),
    (b"[" * 5000 + b"]" * 5000, "document nests too deeply"),
    (b"x" * (MAX_DOCUMENT_BYTES + 1), f"document is larger than {MAX_DOCUMENT_BYTES // 1024} KB"),
], ids=["empty", "not-a-mapping", "not-utf8", "too-deep", "too-large"])
def test_unreadable_or_oversized_documents_are_refused(raw, expected):
    with pytest.raises(TemplateError) as info:
        parse_template(raw)
    assert any(expected in error for error in info.value.errors), info.value.errors


def test_text_that_cannot_be_encoded_is_refused_as_a_template_error():
    with pytest.raises(TemplateError) as info:
        parse_template("schema: maf.project-template/1\ntitle: \ud800\n")
    assert info.value.errors == ["document must be UTF-8 text"]


def test_parse_accepts_text_and_bytes_alike():
    text = FIXTURE.read_text(encoding="utf-8")
    assert parse_template(text) == parse_template(text.encode("utf-8"))


# -------- the loader: list, load and error codes --------
def test_shipped_files_are_the_three_templates_and_the_schema():
    names = sorted(path.name for path in TEMPLATE_DIR.iterdir() if path.is_file() and path.suffix in (".yaml", ".json"))
    assert names == ["campana-marketing.yaml", "desarrollo-software.yaml", "schema.json", "vacia.yaml"]


def test_list_templates_returns_each_shipped_template_sorted_by_id():
    summaries = list_templates()
    assert [item["id"] for item in summaries] == SHIPPED
    assert {item["id"]: item["title"] for item in summaries} == {
        "campana-marketing": "Marketing campaign",
        "desarrollo-software": "Software development",
        "vacia": "Empty project",
    }
    assert all(item["description"] for item in summaries)


@pytest.mark.parametrize("name", SHIPPED)
def test_load_template_returns_a_validated_document_each_time(name):
    first = load_template(name)
    assert first["id"] == name and first["schema"] == SCHEMA_ID
    assert validate_template(first) == []
    first["title"] = "cambiado"
    first["stages"].append(stage("ZZZ"))
    assert load_template(name) == parse_template((TEMPLATE_DIR / f"{name}.yaml").read_bytes())


@pytest.mark.parametrize("name", ["no-existe", "../vacia", "Vacia", "a", "", "vacia.yaml", "schema"])
def test_load_template_refuses_unknown_or_unsafe_names(name):
    with pytest.raises(TemplateNotFound) as info:
        load_template(name)
    assert info.value.code == NOT_FOUND == "not_found" and info.value.status == 404


def test_template_errors_carry_the_service_error_codes():
    assert TemplateError([]).code == INVALID_REQUEST == "invalid_request"
    assert TemplateError([]).status == 422


def test_loader_reads_json_templates_and_skips_other_files(tmp_path):
    (tmp_path / "plan-json.json").write_text(json.dumps(document(id="plan-json")), encoding="utf-8")
    (tmp_path / "notas.txt").write_text("no es una plantilla", encoding="utf-8")
    (tmp_path / "subcarpeta").mkdir()
    (tmp_path / validation.SCHEMA_FILE).write_text("{}", encoding="utf-8")
    assert [item["id"] for item in loader._list(tmp_path)] == ["plan-json"]
    assert loader._load(tmp_path, "plan-json")["id"] == "plan-json"


def test_a_template_defined_twice_is_refused(tmp_path):
    (tmp_path / "doble.yaml").write_text("schema: maf.project-template/1\nid: doble\ntitle: x\nstages: []\n",
                                         encoding="utf-8")
    (tmp_path / "doble.json").write_text(json.dumps(document(id="doble")), encoding="utf-8")
    with pytest.raises(TemplateError) as info:
        loader._list(tmp_path)
    assert "defined twice" in info.value.errors[0]


def test_a_file_name_that_is_not_an_id_is_refused(tmp_path):
    (tmp_path / "Mi_Plantilla.yaml").write_text("schema: maf.project-template/1\n", encoding="utf-8")
    with pytest.raises(TemplateError) as info:
        loader._list(tmp_path)
    assert "not a valid template id" in info.value.errors[0]


def test_a_file_that_declares_another_id_is_refused(tmp_path):
    (tmp_path / "uno.yaml").write_text("schema: maf.project-template/1\nid: otro\ntitle: x\nstages: []\n",
                                       encoding="utf-8")
    with pytest.raises(TemplateError) as info:
        loader._load(tmp_path, "uno")
    assert info.value.errors == ["uno: the file declares the id otro"]


def test_an_invalid_template_file_names_the_template_in_its_errors(tmp_path):
    (tmp_path / "roto.yaml").write_text(
        "schema: maf.project-template/1\nid: roto\ntitle: x\nstages: []\ncommand: rm\n", encoding="utf-8")
    with pytest.raises(TemplateError) as info:
        loader._load(tmp_path, "roto")
    assert info.value.errors == [
        "roto: document: unknown key 'command' (allowed: description, id, schema, stages, title, visibility)",
    ]


def test_loader_modules_stay_free_of_template_content():
    for path in TEMPLATE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in ("marketing", "campaña", "campana", "software", "desarrollo"):
            assert word not in text, f"{path.name} mentions the template {word!r}"


# -------- the shipped templates --------
def test_vacia_has_no_stages():
    template = load_template("vacia")
    assert template["title"] == "Empty project"
    assert template["stages"] == []


def test_campana_marketing_has_its_six_stages_in_order():
    template = load_template("campana-marketing")
    assert [(item["code"], item["title"]) for item in template["stages"]] == [
        ("INV", "Research"), ("EST", "Strategy"), ("CON", "Content"),
        ("PRO", "Production"), ("PUB", "Publishing"), ("MED", "Measurement"),
    ]
    for item in template["stages"]:
        assert item["phases"] and item["tasks"], item["code"]


def test_desarrollo_software_has_design_to_release():
    template = load_template("desarrollo-software")
    assert [item["code"] for item in template["stages"]] == ["DIS", "DES", "PRU", "REV", "LAN"]
    review = template["stages"][3]["tasks"][0]
    assert review["requires"] == "submitted" and review["depends_on"] == ["DES-T03"]


def _waits_for(template: dict[str, Any]) -> dict[str, set[str]]:
    """What creating the project links: each task label -> the labels it waits for, ``stage:X`` expanded."""
    tasks_of = {item["code"]: [f"{item['code']}-{t['id']}" for t in item["tasks"]] for item in template["stages"]}
    waits: dict[str, set[str]] = {}
    for item in template["stages"]:
        for t in item["tasks"]:
            targets: set[str] = set()
            for ref in t.get("depends_on", []):
                if ref.startswith("stage:"):
                    targets.update(tasks_of[ref[len("stage:"):]])
                elif "-" in ref:
                    targets.add(ref)
                else:
                    targets.add(f"{item['code']}-{ref}")
            waits[f"{item['code']}-{t['id']}"] = targets
    return waits


def test_marketing_stage_references_expand_to_every_task_of_the_stage():
    waits = _waits_for(load_template("campana-marketing"))
    assert waits["EST-T01"] == {"INV-T01", "INV-T02", "INV-T03"}
    assert waits["CON-T01"] == {"EST-T01", "EST-T02", "EST-T03"}
    assert waits["MED-T01"] == {"PUB-T01", "PUB-T02", "PUB-T03"}


def test_software_release_waits_for_testing_and_review():
    waits = _waits_for(load_template("desarrollo-software"))
    assert waits["LAN-T01"] == {"PRU-T01", "PRU-T02", "REV-T01", "REV-T02"}
    assert waits["DES-T02"] == {"DIS-T03"}


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_templates_carry_no_dates_and_no_private_visibility(name):
    text = (TEMPLATE_DIR / f"{name}.yaml").read_text(encoding="utf-8")
    assert "visibility" not in load_template(name)
    assert "planned" not in text and "due:" not in text
    assert "visibility: private" not in text


# -------- the synthetic sample project --------
def test_synthetic_fixture_is_a_valid_plan_with_dates():
    plan = parse_template(FIXTURE.read_bytes())
    assert plan["id"] == "proyecto-sintetico"
    assert "inventados" in plan["description"]
    alpha, beta = plan["stages"]
    assert alpha["planned"] == {"start": "2026-11-02", "end": "2026-11-13"}
    assert alpha["tasks"][0]["due"] == "2026-11-06"
    assert "due" not in beta["tasks"][1]  # a task without a date, shown as "sin fecha"


# -------- shipping with the package --------
def test_pyproject_ships_the_template_files():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]
    assert "multiagent.project_templates" in config["packages"]
    patterns = config["package-data"]["multiagent.project_templates"]
    shipped = [path.name for path in TEMPLATE_DIR.iterdir() if path.is_file() and path.suffix in (".yaml", ".json")]
    assert len(shipped) == 4
    for name in shipped:
        assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), name
