# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Validation of project template and plan documents (PRY-T04).

Two pure layers. First the structure is checked against ``schema.json``, which
uses a small JSON Schema subset implemented here (any other keyword in that file
raises, so the schema cannot promise a rule that is not enforced). Then come the
rules a schema cannot state: unique codes, phases that are declared, dependencies
that resolve and do not form cycles, real calendar dates, the 200-task limit.
Nothing in a document is executed or evaluated; it is only read.
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..tasks.envelopes import EnvelopeError, validate_reference
from ..tasks.graph import find_cycle

SCHEMA_ID = "maf.project-template/1"
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_TASKS = 200
SCHEMA_FILE = "schema.json"

_SCHEMA_PATH = Path(__file__).resolve().with_name(SCHEMA_FILE)
_DATE_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_REF_PREFIX = "#/$defs/"
_KEYWORDS = frozenset({
    "$schema", "$defs", "$ref", "title", "description", "type", "const", "enum",
    "properties", "required", "additionalProperties",
    "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern",
})


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_template(document: Any) -> list[str]:
    """Every problem found in ``document``; an empty list means it is a valid template or plan.

    Structural errors are reported first; the rules that need a well-formed document run only when there are none.
    """
    schema = _schema()
    errors: list[str] = []
    _check(schema, document, schema, "", errors)
    if errors:
        return errors
    return _rules(document)


# -------- structure: the JSON Schema subset --------
def _check(node: dict[str, Any], value: Any, root: dict[str, Any], path: str, errors: list[str]) -> None:
    if "$ref" in node:
        node = _resolve(node["$ref"], root)
    _reject_unsupported(node, path)
    label = path or "document"
    kind = node.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            errors.append(f"{label}: must be a mapping")
            return
        _check_object(node, value, root, path, errors)
    elif kind == "array":
        if not isinstance(value, list):
            errors.append(f"{label}: must be a list")
            return
        _check_array(node, value, root, path, errors)
    elif kind == "string":
        if not isinstance(value, str):
            errors.append(f"{label}: must be text")
            return
        _check_string(node, value, path, errors)
    if "const" in node and value != node["const"]:
        errors.append(f"{label}: must be {node['const']}")
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{label}: must be one of {', '.join(node['enum'])}")


def _check_object(node: dict[str, Any], value: dict[str, Any], root: dict[str, Any], path: str,
                  errors: list[str]) -> None:
    label = path or "document"
    properties = node.get("properties", {})
    for key in node.get("required", []):
        if key not in value:
            errors.append(f"{label}: missing required key '{key}'")
    for key, child in value.items():
        if key in properties:
            _check(properties[key], child, root, f"{path}.{key}" if path else str(key), errors)
        elif node.get("additionalProperties") is False:
            allowed = ", ".join(sorted(properties))
            errors.append(f"{label}: unknown key '{key}' (allowed: {allowed})")


def _check_array(node: dict[str, Any], value: list[Any], root: dict[str, Any], path: str,
                 errors: list[str]) -> None:
    label = path or "document"
    if len(value) < node.get("minItems", 0):
        errors.append(f"{label}: needs at least {node['minItems']} item(s)")
    if "maxItems" in node and len(value) > node["maxItems"]:
        errors.append(f"{label}: at most {node['maxItems']} items (found {len(value)})")
    if node.get("uniqueItems"):
        if len({json.dumps(item, sort_keys=True, default=str) for item in value}) != len(value):
            errors.append(f"{label}: items must be unique")
    item_schema = node.get("items")
    if item_schema is not None:
        for index, item in enumerate(value):
            _check(item_schema, item, root, f"{path}[{index}]", errors)


def _check_string(node: dict[str, Any], value: str, path: str, errors: list[str]) -> None:
    label = path or "document"
    if len(value) < node.get("minLength", 0):
        errors.append(f"{label}: must have at least {node['minLength']} character(s)")
    if "maxLength" in node and len(value) > node["maxLength"]:
        errors.append(f"{label}: must have at most {node['maxLength']} characters")
    pattern = node.get("pattern")
    # fullmatch, not search: JSON Schema patterns are anchored to the whole string, and "$" must not accept a trailing newline.
    if pattern is not None and not re.fullmatch(pattern, value):
        errors.append(f"{label}: must be {node.get('description', 'in the expected format')}")


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    name = ref[len(_REF_PREFIX):] if ref.startswith(_REF_PREFIX) else ""
    if name not in root.get("$defs", {}):
        raise RuntimeError(f"schema.json has an unresolvable $ref: {ref!r}")
    return root["$defs"][name]


def _reject_unsupported(node: dict[str, Any], path: str) -> None:
    unknown = sorted(set(node) - _KEYWORDS)
    if "additionalProperties" in node and node["additionalProperties"] is not False:
        unknown.append("additionalProperties (only false is implemented)")
    if unknown:
        raise RuntimeError(f"schema.json at {path or 'the root'} uses keywords this validator does not implement: {unknown}")


# -------- rules a schema cannot state --------
def _rules(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    stages = document.get("stages", [])

    first_index: dict[str, int] = {}
    for index, stage in enumerate(stages):
        code = stage["code"]
        if code in first_index:
            errors.append(f"stages[{index}].code: stage code {code} is already used by stages[{first_index[code]}]")
        else:
            first_index[code] = index

    total = sum(len(stage.get("tasks", [])) for stage in stages)
    if total > MAX_TASKS:
        errors.append(f"stages: at most {MAX_TASKS} tasks in total (found {total})")

    stage_tasks: dict[str, list[str]] = {}
    for code, index in first_index.items():
        stage_tasks[code] = [task["id"] for task in stages[index].get("tasks", [])]

    for index, stage in enumerate(stages):
        _stage_rules(stage, f"stages[{index}]", errors)

    edges: list[dict[str, str]] = []
    for index, stage in enumerate(stages):
        code = stage["code"]
        if first_index[code] != index:
            continue  # a repeated code is already reported; its dependencies cannot be addressed
        for t_index, task in enumerate(stage.get("tasks", [])):
            node = f"{code}-{task['id']}"
            for d_index, ref in enumerate(task.get("depends_on", [])):
                targets, problem = _targets(ref, code, stage_tasks)
                if problem:
                    errors.append(f"stages[{index}].tasks[{t_index}].depends_on[{d_index}]: {problem}")
                edges.extend({"task_id": node, "depends_on_id": target} for target in targets)

    cycle = find_cycle(edges)
    if cycle:
        errors.append("depends_on: dependency cycle " + " -> ".join(cycle))
    return errors


def _stage_rules(stage: dict[str, Any], path: str, errors: list[str]) -> None:
    code = stage["code"]
    phases = stage.get("phases", [])
    planned = stage.get("planned")
    if planned:
        for key in ("start", "end"):
            if not _is_date(planned[key]):
                errors.append(f"{path}.planned.{key}: must be a real calendar date")
        if _is_date(planned["start"]) and _is_date(planned["end"]) and planned["start"] > planned["end"]:
            errors.append(f"{path}.planned: start is after end")

    seen: set[str] = set()
    for t_index, task in enumerate(stage.get("tasks", [])):
        tpath = f"{path}.tasks[{t_index}]"
        if task["id"] in seen:
            errors.append(f"{tpath}.id: task id {task['id']} is already used in stage {code}")
        seen.add(task["id"])
        phase = task.get("phase", "")
        if phase and phase not in phases:
            errors.append(f"{tpath}.phase: {phase} is not one of the phases of stage {code}")
        if "due" in task and not _is_date(task["due"]):
            errors.append(f"{tpath}.due: must be a real calendar date")
        if "requires" in task and not task.get("depends_on"):
            errors.append(f"{tpath}.requires: needs depends_on")
        for r_index, ref in enumerate(task.get("context_refs", [])):
            try:
                validate_reference(ref, field="context_refs")
            except EnvelopeError as exc:
                errors.append(f"{tpath}.context_refs[{r_index}]: {exc}")


def _targets(ref: str, own_code: str, stage_tasks: dict[str, list[str]]) -> tuple[list[str], str | None]:
    """The task nodes a ``depends_on`` entry points to, or the reason it points nowhere."""
    if ref.startswith("stage:"):
        code = ref[len("stage:"):]
        if code not in stage_tasks:
            return [], f"unknown stage {code}"
        if not stage_tasks[code]:
            return [], f"stage {code} has no tasks"
        return [f"{code}-{task_id}" for task_id in stage_tasks[code]], None
    code, task_id = tuple(ref.split("-", 1)) if "-" in ref else (own_code, ref)
    if code not in stage_tasks:
        return [], f"unknown stage {code}"
    if task_id not in stage_tasks[code]:
        return [], f"no task {task_id} in stage {code}"
    return [f"{code}-{task_id}"], None


def _is_date(text: Any) -> bool:
    if not isinstance(text, str) or not _DATE_SHAPE.fullmatch(text):
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True
