# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFI-T02: contracts as data (contracts.yaml), built without executing anything."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts import OUTPUT_SCHEMAS
from multiagent.workflow_validation import WorkflowDefinitionError


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="", openai_api_key="")


def make_bundle(tmp_path: Path, document, *, contracts_py: str | None = None, raw: str | None = None) -> Path:
    root = tmp_path / "office"
    (root / "workflows").mkdir(parents=True, exist_ok=True)
    (root / "contracts.yaml").write_text(
        raw if raw is not None else yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    if contracts_py is not None:
        (root / "contracts.py").write_text(contracts_py, encoding="utf-8")
    return root


def aviso(**fields):
    return {"schema": "maf.contracts/1", "contracts": {"Aviso": {"fields": fields}}}


def load_error(root: Path, settings: Settings):
    with pytest.raises(WorkflowDefinitionError) as info:
        load_bundle(root, settings)
    return info.value.issues[0]


def nested(depth: int) -> dict:
    spec: dict = {"type": "string"}
    for _ in range(depth - 1):
        spec = {"type": "object", "fields": {"x": spec}}
    return spec


def test_data_contract_loads_without_code(tmp_path, settings):
    root = make_bundle(tmp_path, aviso(
        titulo={"type": "string", "max_length": 120}, cuerpo={"type": "string", "document": True}))
    assert not list(root.glob("*.py"))
    model = load_bundle(root, settings).output_schemas["Aviso"]
    assert model.model_validate({"titulo": "x", "cuerpo": "y"}).titulo == "x"
    for bad in ({"titulo": ""}, {"titulo": "x" * 121, "cuerpo": "y"}, {"titulo": "x", "cuerpo": "y", "otro": 1}):
        with pytest.raises(ValidationError):
            model.model_validate(bad)


GOOD_AND_BAD = {
    "string": ({"type": "string"}, "a", 1),
    "integer": ({"type": "integer"}, 3, "3"),
    "number": ({"type": "number"}, 1.5, "x"),
    "boolean": ({"type": "boolean"}, True, "yes"),
    "enum": ({"type": "enum", "values": ["a", "b"]}, "a", "c"),
    "list": ({"type": "list", "items": {"type": "string"}, "min_items": 1, "max_items": 2}, ["a"], []),
    "object": ({"type": "object", "fields": {"dentro": {"type": "integer"}}}, {"dentro": 1}, {"dentro": "z"}),
}


@pytest.mark.parametrize("kind", sorted(GOOD_AND_BAD))
def test_every_field_type(tmp_path, settings, kind):
    spec, good, bad = GOOD_AND_BAD[kind]
    model = load_bundle(make_bundle(tmp_path, aviso(campo=spec)), settings).output_schemas["Aviso"]
    assert model.model_validate({"campo": good}).campo is not None
    with pytest.raises(ValidationError):
        model.model_validate({"campo": bad})


def test_bool_is_not_a_number_and_list_caps_apply(tmp_path, settings):
    model = load_bundle(make_bundle(tmp_path, aviso(
        n={"type": "integer"}, xs={"type": "list", "items": {"type": "string"}, "max_items": 2})),
        settings).output_schemas["Aviso"]
    with pytest.raises(ValidationError):
        model.model_validate({"n": True, "xs": []})
    with pytest.raises(ValidationError):
        model.model_validate({"n": 1, "xs": ["a", "b", "c"]})


def test_enum_without_values_is_rejected(tmp_path, settings):
    issue = load_error(make_bundle(tmp_path, aviso(campo={"type": "enum"})), settings)
    assert issue.code == "bundle_contracts_invalid"
    assert issue.location == "contracts.yaml:Aviso.campo"


@pytest.mark.parametrize("spec", [
    {"type": "date"}, {"type": "string", "pattern": "x"}, {"type": "integer", "max_length": 3},
])
def test_unknown_type_and_key_rejected(tmp_path, settings, spec):
    issue = load_error(make_bundle(tmp_path, aviso(titulo=spec)), settings)
    assert issue.code == "bundle_contracts_invalid"
    assert issue.location == "contracts.yaml:Aviso.titulo"


def test_depth_six_loads(tmp_path, settings):
    assert load_bundle(make_bundle(tmp_path, aviso(a=nested(6))), settings).output_schemas["Aviso"]


@pytest.mark.parametrize("document", [
    aviso(a=nested(7)),
    aviso(Titulo={"type": "string"}),
    {"contracts": {"aviso": {"fields": {"a": {"type": "string"}}}}},
    aviso(model_dump={"type": "string"}),
])
def test_depth_and_names(tmp_path, settings, document):
    assert load_error(make_bundle(tmp_path, document), settings).code == "bundle_contracts_invalid"


def test_document_key(tmp_path, settings):
    model = load_bundle(make_bundle(tmp_path, aviso(
        titulo={"type": "string"}, cuerpo={"type": "string", "document": True})), settings).output_schemas["Aviso"]
    props = model.model_json_schema()["properties"]
    assert props["cuerpo"]["x-maf-document"] is True
    assert "x-maf-document" not in props["titulo"]


def test_two_document_fields_are_rejected(tmp_path, settings):
    issue = load_error(make_bundle(tmp_path, aviso(
        a={"type": "string", "document": True}, b={"type": "string", "document": True})), settings)
    assert issue.code == "bundle_contracts_invalid"
    assert issue.location == "contracts.yaml:Aviso"


def test_required_defaults_to_true(tmp_path, settings):
    model = load_bundle(make_bundle(tmp_path, aviso(
        a={"type": "string"}, b={"type": "string", "required": False})), settings).output_schemas["Aviso"]
    assert model.model_validate({"a": "x"}).b is None
    with pytest.raises(ValidationError):
        model.model_validate({"b": "x"})


def test_samples_are_validated(tmp_path, settings):
    document = aviso(a={"type": "string"})
    document["samples"] = {"Aviso": {"a": "ok"}, "FreeTextAnswer": {"answer": "hola"}}
    bundle = load_bundle(make_bundle(tmp_path, document), settings)
    assert bundle.sample_outputs == {"Aviso": {"a": "ok"}, "FreeTextAnswer": {"answer": "hola"}}


@pytest.mark.parametrize("samples", [{"Aviso": {"a": ""}}, {"FreeTextAnswer": {"answer": ""}}, {"ContentOutput": {}}])
def test_bad_samples_are_rejected(tmp_path, settings, samples):
    document = aviso(a={"type": "string"})
    document["samples"] = samples
    assert load_error(make_bundle(tmp_path, document), settings).code == "sample_output_invalid"


def test_yaml_and_py_clash(tmp_path, settings):
    py = ("from multiagent.contracts import StrictModel\nclass Aviso(StrictModel):\n    a: str\n"
          "OUTPUT_SCHEMAS = {'Aviso': Aviso}\n")
    issue = load_error(make_bundle(tmp_path, aviso(a={"type": "string"}), contracts_py=py), settings)
    assert issue.code == "contract_conflict"


def test_yaml_cannot_redefine_a_core_contract(tmp_path, settings):
    document = {"contracts": {"FreeTextAnswer": {"fields": {"answer": {"type": "string"}}}}}
    assert load_error(make_bundle(tmp_path, document), settings).code == "contract_conflict"


def test_yaml_and_py_contracts_merge(tmp_path, settings):
    py = ("from multiagent.contracts import StrictModel\nclass Otro(StrictModel):\n    a: str\n"
          "OUTPUT_SCHEMAS = {'Otro': Otro}\n")
    bundle = load_bundle(make_bundle(tmp_path, aviso(a={"type": "string"}), contracts_py=py), settings)
    assert {"Aviso", "Otro"} <= set(bundle.output_schemas)


def test_core_alias_is_not_a_clash(tmp_path, settings):
    alias = ("from multiagent.contracts import FreeTextAnswer\n"
             "OUTPUT_SCHEMAS = {'FreeTextAnswer': FreeTextAnswer}\n")
    root = make_bundle(tmp_path, aviso(a={"type": "string"}), contracts_py=alias)
    bundle = load_bundle(root, settings)
    assert bundle.output_schemas["FreeTextAnswer"] is OUTPUT_SCHEMAS["FreeTextAnswer"]
    own = ("from multiagent.contracts import StrictModel\nclass FreeTextAnswer(StrictModel):\n    answer: str\n"
           "OUTPUT_SCHEMAS = {'FreeTextAnswer': FreeTextAnswer}\n")
    (root / "contracts.py").write_text(own, encoding="utf-8")
    assert load_error(root, settings).code == "contract_conflict"


def test_nothing_is_executed(tmp_path, settings):
    marker = tmp_path / "ran.txt"
    raw = ("contracts:\n  Aviso:\n    fields:\n      a: !!python/object/apply:pathlib.Path.write_text "
           f"[{str(marker)!r}, 'x']\n")
    root = make_bundle(tmp_path, None, raw=raw)
    assert load_error(root, settings).code == "bundle_contracts_invalid"
    assert not marker.exists()
