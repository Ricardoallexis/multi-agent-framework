# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Contracts as data: read a bundle's ``contracts.yaml`` and build pydantic models from it.

The file is read with ``yaml.safe_load`` and the models are built with ``pydantic.create_model`` on
``StrictModel``. Nothing in it is executed or imported.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field, create_model

from .contracts import StrictModel
from .documents import DOCUMENT_KEY, MAX_DEPTH

CONTRACTS_SCHEMA = "maf.contracts/1"
FIELD_TYPES = ("string", "integer", "number", "boolean", "enum", "list", "object")
CONTRACT_NAME_PATTERN = r"^[A-Z][A-Za-z0-9]{0,63}$"
FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_ENUM_VALUES = 64

_FILE_KEYS = {"schema", "contracts", "samples"}
_CONTRACT_KEYS = {"description", "fields"}
_COMMON_KEYS = {"type", "required", "description", "document"}
_TYPE_KEYS = {
    "string": {"max_length"},
    "list": {"min_items", "max_items", "items"},
    "enum": {"values"},
    "object": {"fields"},
}


class ContractsFileError(Exception):
    """``contracts.yaml`` is unusable. ``location`` reads ``contracts.yaml[:<contract>[.<field>]]``."""

    def __init__(self, code: str, message: str, location: str = "contracts.yaml"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.location = location


def _fail(location: str, message: str) -> ContractsFileError:
    return ContractsFileError("bundle_contracts_invalid", message, location)


def load_contracts_file(path: Path) -> tuple[dict[str, type[StrictModel]], dict[str, dict]]:
    """Return ``(contracts, samples)`` from ``path``; raise ``ContractsFileError`` when it is invalid."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as exc:
        raise _fail("contracts.yaml", f"contracts.yaml could not be read: {type(exc).__name__}") from exc
    if not isinstance(raw, dict):
        raise _fail("contracts.yaml", "contracts.yaml must be a mapping")
    unknown = sorted(str(key) for key in set(raw) - _FILE_KEYS)
    if unknown:
        raise _fail("contracts.yaml", f"Unknown top-level key(s): {', '.join(unknown)}")
    if "schema" in raw and raw["schema"] != CONTRACTS_SCHEMA:
        raise _fail("contracts.yaml", f"schema must be {CONTRACTS_SCHEMA!r}")
    declared = raw.get("contracts")
    if not isinstance(declared, dict) or not declared:
        raise _fail("contracts.yaml", "contracts must be a non-empty mapping of contract name -> definition")

    contracts: dict[str, type[StrictModel]] = {}
    for name, definition in declared.items():
        location = f"contracts.yaml:{name}"
        if not isinstance(name, str) or not re.fullmatch(CONTRACT_NAME_PATTERN, name):
            raise _fail(location, f"Contract name {name!r} must match {CONTRACT_NAME_PATTERN}")
        contracts[name] = _build_contract(name, definition, location)

    samples = raw.get("samples") or {}
    if not isinstance(samples, dict):
        raise _fail("contracts.yaml", "samples must be a mapping of contract name -> sample output")
    for key, sample in samples.items():
        if not isinstance(key, str) or not isinstance(sample, dict):
            raise _fail("contracts.yaml", "samples must map a contract name to a mapping")
    return contracts, dict(samples)


def _build_contract(name: str, definition: Any, location: str) -> type[StrictModel]:
    if not isinstance(definition, dict):
        raise _fail(location, f"Contract {name!r} must be a mapping")
    unknown = sorted(str(key) for key in set(definition) - _CONTRACT_KEYS)
    if unknown:
        raise _fail(location, f"Unknown key(s) in contract {name!r}: {', '.join(unknown)}")
    description = definition.get("description")
    if description is not None and not isinstance(description, str):
        raise _fail(location, "description must be text")
    return _build_object(name, definition.get("fields"), location, [name], depth=1, top_level=True,
                         doc=description)


def _build_object(model_name: str, fields: Any, location: str, path: list[str], *, depth: int,
                  top_level: bool, doc: str | None = None) -> type[StrictModel]:
    if not isinstance(fields, dict) or not fields:
        raise _fail(location, "fields must be a non-empty mapping of field name -> definition")
    built: dict[str, Any] = {}
    documents = 0
    for field_name, spec in fields.items():
        field_location = f"{location}.{field_name}"
        if not isinstance(field_name, str) or not re.fullmatch(FIELD_NAME_PATTERN, field_name):
            raise _fail(field_location, f"Field name {field_name!r} must match {FIELD_NAME_PATTERN}")
        if field_name.startswith("model_") or hasattr(StrictModel, field_name):
            raise _fail(field_location, f"Field name {field_name!r} is reserved")
        annotation, info, is_document = _build_field(
            spec, field_location, path + [field_name], depth=depth, top_level=top_level)
        documents += is_document
        built[field_name] = (annotation, info)
    if documents > 1:
        raise _fail(location, "Only one field per contract may set document: true")
    extra = {"__doc__": doc} if doc else {}
    return create_model("_".join(path), __base__=StrictModel, **extra, **built)


def _build_field(spec: Any, location: str, path: list[str], *, depth: int, top_level: bool,
                 is_item: bool = False) -> tuple[Any, Any, bool]:
    if depth > MAX_DEPTH:
        raise _fail(location, f"Contracts nest at most {MAX_DEPTH} levels")
    if not isinstance(spec, dict):
        raise _fail(location, "A field must be a mapping")
    kind = spec.get("type")
    if kind not in FIELD_TYPES:
        raise _fail(location, f"type must be one of: {', '.join(FIELD_TYPES)}")
    allowed = _COMMON_KEYS | _TYPE_KEYS.get(kind, set())
    unknown = sorted(str(key) for key in set(spec) - allowed)
    if unknown:
        raise _fail(location, f"Unknown or misplaced key(s) for type {kind}: {', '.join(unknown)}")
    if is_item and ({"required", "document"} & set(spec)):
        raise _fail(location, "A list item cannot set required or document")

    required = spec.get("required", True)
    if not isinstance(required, bool):
        raise _fail(location, "required must be true or false")
    description = spec.get("description")
    if description is not None and not isinstance(description, str):
        raise _fail(location, "description must be text")
    document = spec.get("document", False)
    if not isinstance(document, bool):
        raise _fail(location, "document must be true or false")
    if document and (kind != "string" or not top_level):
        raise _fail(location, "document: true is only for a top-level string field")

    constraints: dict[str, Any] = {}
    if kind == "string":
        annotation: Any = str
        constraints["strict"] = True
        max_length = _int_key(spec, "max_length", location, minimum=1)
        if max_length is not None:
            constraints["max_length"] = max_length
        if required:
            constraints["min_length"] = 1
    elif kind == "integer":
        annotation = int
        constraints["strict"] = True
    elif kind == "number":
        annotation = float
        constraints["strict"] = True
    elif kind == "boolean":
        annotation = bool
        constraints["strict"] = True
    elif kind == "enum":
        values = spec.get("values")
        if (not isinstance(values, list) or not values or len(values) > MAX_ENUM_VALUES
                or not all(isinstance(v, str) and v for v in values) or len(set(values)) != len(values)):
            raise _fail(location, "enum needs values: a list of distinct, non-empty texts")
        annotation = Literal[tuple(values)]  # type: ignore[valid-type]
    elif kind == "list":
        item_spec = spec.get("items")
        if item_spec is None:
            raise _fail(location, "list needs items: the definition of one element")
        item_annotation, item_info, _ = _build_field(
            item_spec, f"{location}[]", path + ["item"], depth=depth + 1, top_level=False, is_item=True)
        annotation = list[Annotated[item_annotation, item_info]]  # type: ignore[valid-type]
        min_items = _int_key(spec, "min_items", location, minimum=0)
        max_items = _int_key(spec, "max_items", location, minimum=0)
        if min_items is not None:
            constraints["min_length"] = min_items
        if max_items is not None:
            constraints["max_length"] = max_items
        if min_items is not None and max_items is not None and min_items > max_items:
            raise _fail(location, "min_items cannot exceed max_items")
    else:  # object
        annotation = _build_object("_".join(path), spec.get("fields"), location, path,
                                   depth=depth + 1, top_level=False)

    if description:
        constraints["description"] = description
    if document:
        constraints["json_schema_extra"] = {DOCUMENT_KEY: True}
    if required:
        return annotation, Field(**constraints), document
    return annotation | None, Field(default=None, **constraints), document


def _int_key(spec: dict, key: str, location: str, *, minimum: int) -> int | None:
    if key not in spec:
        return None
    value = spec[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(location, f"{key} must be a whole number of at least {minimum}")
    return value
