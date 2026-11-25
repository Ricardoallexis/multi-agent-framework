# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for workflow ``result_step`` selection and deterministic Markdown rendering."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import Field

from examples.bundles.general.contracts import SAMPLE_OUTPUTS, DocumentSummary
from multiagent.config import Settings
from multiagent.contracts import StrictModel
from multiagent.documents import (
    DOCUMENT_KEY,
    MAX_DEPTH,
    TITLE_MAX,
    document_title,
    escape_text,
    render_markdown,
    result_step_id,
)
from multiagent.workflow_validation import WorkflowDefinitionError
from multiagent.workflows import WorkflowCatalog, WorkflowDefinition, WorkflowStep


def _step(step_id: str) -> WorkflowStep:
    return WorkflowStep(
        id=step_id,
        agent="creator",
        prompt_id="creator/social_post",
        prompt_version=2,
        skills=[],
        contract="ContentOutput",
        preferred_model="local_default",
        fallback_model=None,
        requires_web=False,
        when=None,
        checkpoint_after=False,
    )


def _definition(*step_ids: str, description: str | None = None, result_step: str | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        id="sample_flow",
        max_iterations=2,
        steps=[_step(step_id) for step_id in step_ids],
        description=description,
        result_step=result_step,
    )


VALID_WORKFLOW = {
    "id": "sample",
    "max_iterations": 2,
    "steps": [
        {
            "id": "draft",
            "agent": "researcher",
            "prompt_id": "researcher/research",
            "prompt_version": 2,
            "skills": [],
            "contract": "ResearchOutput",
            "preferred_model": "gemini_grounded",
            "fallback_model": None,
            "requires_web": True,
            "when": "requires_web",
            "checkpoint_after": False,
        },
        {
            "id": "summarize",
            "agent": "creator",
            "prompt_id": "creator/social_post",
            "prompt_version": 2,
            "skills": ["social_media"],
            "contract": "ContentOutput",
            "preferred_model": "local_default",
            "fallback_model": "cloud_fast",
            "requires_web": False,
            "when": None,
            "checkpoint_after": True,
        },
    ],
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local", gemini_api_key="", openai_api_key="")


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    path = tmp_path / "workflows"
    path.mkdir()
    return path


def test_result_step_declared():
    definition = _definition("draft", "summarize", "polish", result_step="summarize")
    assert result_step_id(definition, {"draft", "summarize"}) == "summarize"
    assert result_step_id(definition, {"draft", "polish"}) is None


def test_result_step_defaults_to_last_completed():
    definition = _definition("a", "b", "c")
    assert result_step_id(definition, {"a", "b"}) == "b"
    assert result_step_id(definition, {"c", "a"}) == "c"
    assert result_step_id(definition, set()) is None


def test_unknown_result_step_is_a_validation_issue(settings, workflows_dir):
    raw = copy.deepcopy(VALID_WORKFLOW)
    raw["result_step"] = "nada"
    (workflows_dir / "sample.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    with pytest.raises(WorkflowDefinitionError) as info:
        WorkflowCatalog(settings, workflows_dir=workflows_dir).load("sample")

    assert [(issue.code, issue.location) for issue in info.value.issues] == [
        ("unknown_result_step", "result_step"),
    ]


def test_valid_result_step_and_invalid_type(settings, workflows_dir):
    raw = copy.deepcopy(VALID_WORKFLOW)
    raw["result_step"] = "summarize"
    (workflows_dir / "sample.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    loaded = WorkflowCatalog(settings, workflows_dir=workflows_dir).load("sample")
    assert loaded.result_step == "summarize"

    bad = copy.deepcopy(VALID_WORKFLOW)
    bad["result_step"] = 42
    (workflows_dir / "sample.yaml").write_text(yaml.safe_dump(bad, sort_keys=False), encoding="utf-8")
    with pytest.raises(WorkflowDefinitionError) as info:
        WorkflowCatalog(settings, workflows_dir=workflows_dir).load("sample")
    assert {(issue.code, issue.location) for issue in info.value.issues} == {
        ("invalid_type", "result_step"),
    }


def test_title_from_description_or_id():
    long_first = "A" * (TITLE_MAX + 25)
    two_lines = f"{long_first}\nSecond line ignored"
    with_desc = _definition("a", description=two_lines)
    title = document_title(with_desc)
    assert len(title) == TITLE_MAX
    assert title == "A" * TITLE_MAX

    assert document_title(_definition("a", description="Primera línea\nSegunda línea")) == "Primera línea"
    assert document_title(_definition("a", description=None)) == "sample_flow"
    assert document_title(_definition("a", description="   \n  ")) == "sample_flow"


def test_render_with_contract_descriptions():
    md = render_markdown("Resumen semanal", SAMPLE_OUTPUTS["DocumentSummary"], DocumentSummary.model_json_schema())
    assert md.startswith("# Resumen semanal\n\n")
    assert "## Title\n\nChange of the weekly meeting" in md
    assert "## Summary\n\nThe weekly meeting moves to Wednesdays" in md
    assert "## Key Points\n\n- New day and time: Wednesday at 10:00.\n- The minutes are published before 18:00" in md
    assert "## Open Questions\n\n- What to do if the Wednesday is a holiday." in md

    class DescribedSummary(StrictModel):
        headline: str = Field(description="Titular principal")
        points: list[str] = Field(description="Puntos clave")

    rendered = render_markdown(
        "Acta",
        {"headline": "Aprobado", "points": ["Uno", "Dos"]},
        DescribedSummary.model_json_schema(),
    )
    assert "## Titular principal\n\nAprobado" in rendered
    assert "## Puntos clave\n\n- Uno\n- Dos" in rendered


def test_render_nested_and_refs():
    class Leaf(StrictModel):
        note: str = Field(description="Nota final")
        score: int = Field(description="Puntuación")
        active: bool = Field(description="Activo")

    class Branch(StrictModel):
        label: str = Field(description="Etiqueta")
        leaf: Leaf = Field(description="Detalle")

    class Root(StrictModel):
        branch: Branch = Field(description="Rama principal")
        items: list[Leaf] = Field(description="Elementos")

    data = {
        "branch": {
            "label": "Principal",
            "leaf": {"note": "Todo claro", "score": 0, "active": False},
        },
        "items": [
            {"note": "Primero", "score": 10, "active": True},
        ],
    }
    md = render_markdown("Informe", data, Root.model_json_schema())
    assert "## Rama principal\n" in md
    assert "### Etiqueta\n\nPrincipal" in md
    assert "### Detalle\n" in md
    assert "#### Nota final\n\nTodo claro" in md
    assert "#### Puntuación\n\n0" in md
    assert "#### Activo\n\nfalse" in md
    assert "## Elementos\n" in md
    assert "### Leaf 1\n" in md
    assert "#### Nota final\n\nPrimero" in md

    broken_schema = {
        "type": "object",
        "properties": {
            "broken": {"$ref": "#/$defs/DoesNotExist"},
        },
    }
    broken_md = render_markdown("Roto", {"broken": {"raw_key": "raw value"}}, broken_schema)
    assert "## broken\n" in broken_md
    assert "### raw_key\n\nraw value" in broken_md

    cyclic_schema = {
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre"},
                    "child": {"$ref": "#/$defs/Node", "description": "Hijo"},
                },
            }
        },
        "$ref": "#/$defs/Node",
    }
    deep_data: dict[str, object] = {"name": "nivel-8"}
    for level in range(7, 0, -1):
        deep_data = {"name": f"nivel-{level}", "child": deep_data}

    cyclic_md = render_markdown("Ciclo", deep_data, cyclic_schema)
    assert f"nivel-{MAX_DEPTH}" in cyclic_md
    assert f"nivel-{MAX_DEPTH + 1}" not in cyclic_md
    assert "#######" not in cyclic_md


def test_document_field_used_as_is():
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Resumen"},
            "body": {"type": "string", DOCUMENT_KEY: True},
            "notes": {"type": "string", "description": "Notas"},
        },
    }
    data = {
        "summary": "No debe salir",
        "body": "## Introducción\n\nTexto del documento.\n\n- Punto uno",
        "notes": "Tampoco sale",
    }
    md = render_markdown("Mi Documento", data, schema)
    assert md == "# Mi Documento\n\n## Introducción\n\nTexto del documento.\n\n- Punto uno\n"
    assert "No debe salir" not in md
    assert "Tampoco sale" not in md


def test_html_escaped():
    assert escape_text("a & b < c > d") == "a &amp; b &lt; c &gt; d"

    schema = {
        "type": "object",
        "properties": {
            "field": {"type": "string", "description": "Campo <peligroso> & más"},
        },
    }
    md = render_markdown(
        "Título <x> & y",
        {"field": "<script>alert(1)</script> & texto"},
        schema,
    )
    assert "# Título &lt;x&gt; &amp; y" in md
    assert "## Campo &lt;peligroso&gt; &amp; más" in md
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; texto" in md
    assert "<script>" not in md

    doc_schema = {
        "type": "object",
        "properties": {
            "doc": {"type": "string", DOCUMENT_KEY: True},
        },
    }
    doc_md = render_markdown("Doc", {"doc": "Hola <script>alert(1)</script> & adiós"}, doc_schema)
    assert doc_md == "# Doc\n\nHola &lt;script&gt;alert(1)&lt;/script&gt; &amp; adiós\n"


def test_render_is_deterministic_and_skips_empty_fields():
    schema = {
        "type": "object",
        "properties": {
            "first": {"type": "string", "description": "Primero"},
            "empty_text": {"type": "string", "description": "Vacío"},
            "null_field": {"type": "string", "description": "Nulo"},
            "empty_list": {"type": "array", "items": {"type": "string"}, "description": "Lista vacía"},
            "empty_obj": {"type": "object", "description": "Objeto vacío", "properties": {"x": {"type": "string"}}},
            "second": {"type": "number", "description": "Segundo"},
        },
    }
    data = {
        "second": 3.5,
        "empty_text": "   ",
        "null_field": None,
        "empty_list": [],
        "empty_obj": {"x": None},
        "first": "Valor uno",
    }
    first_call = render_markdown("Determinista", data, schema)
    second_call = render_markdown("Determinista", data, schema)
    assert first_call == second_call
    assert first_call.endswith("\n")
    assert not first_call.endswith("\n\n")
    assert first_call.index("## Primero") < first_call.index("## Segundo")
    for omitted in ("Vacío", "Nulo", "Lista vacía", "Objeto vacío"):
        assert omitted not in first_call
