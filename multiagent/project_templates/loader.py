# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Reading project templates and plans from text or from the shipped files (PRY-T04).

Templates are data. YAML is read by a loader that accepts only plain data: no
tags (so no object constructors), no anchors or aliases (so nothing expands),
no merge keys, no duplicate keys, and every plain scalar is text, so an unquoted
``due: 2026-11-06`` stays a string and ``code: NO`` is not a boolean. JSON is a
subset of YAML and goes through the same path.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .errors import TemplateError, TemplateNotFound
from .validation import MAX_DOCUMENT_BYTES, SCHEMA_FILE, validate_template

TEMPLATE_DIR = Path(__file__).resolve().parent
TEMPLATE_SUFFIXES = (".yaml", ".json")
_TEMPLATE_ID = re.compile(r"[a-z0-9-]{2,40}")
_NULL_TAG = "tag:yaml.org,2002:null"


class _PlainLoader(yaml.SafeLoader):
    """``SafeLoader`` restricted to plain data (see the module docstring)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise yaml.composer.ComposerError(None, None, "anchors and aliases are not allowed", event.start_mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> Any:
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, _value in node.value:
                if isinstance(key_node, yaml.ScalarNode):
                    if key_node.value in seen:
                        raise yaml.constructor.ConstructorError(
                            None, None, f"duplicate key {key_node.value!r}", key_node.start_mark)
                    seen.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


# Only null keeps its implicit resolver: every other plain scalar is a string (no dates, numbers, booleans or merge keys).
_PlainLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag == _NULL_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_template(source: bytes | str) -> dict[str, Any]:
    """Parse and validate one template or plan document (YAML or JSON text).

    Raises ``TemplateError`` with every problem found; never executes content and never names a file path.
    """
    try:
        raw = source.encode("utf-8") if isinstance(source, str) else source
        text = raw.decode("utf-8")
    except UnicodeError:
        raise TemplateError(["document must be UTF-8 text"]) from None
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise TemplateError([f"document is larger than {MAX_DOCUMENT_BYTES // 1024} KB"])
    try:
        data = yaml.load(text, Loader=_PlainLoader)
    except yaml.YAMLError as exc:
        raise TemplateError([_yaml_problem(exc)]) from None
    except RecursionError:
        raise TemplateError(["document nests too deeply"]) from None
    if data is None:
        raise TemplateError(["document is empty"])
    errors = validate_template(data)
    if errors:
        raise TemplateError(errors)
    return data


def _yaml_problem(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or "not valid YAML"
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is None:
        return f"invalid YAML: {problem}"
    return f"invalid YAML at line {mark.line + 1}, column {mark.column + 1}: {problem}"


def list_templates() -> list[dict[str, str]]:
    """The shipped templates as ``{id, title, description}``, sorted by id."""
    return _list(TEMPLATE_DIR)


def load_template(name: str) -> dict[str, Any]:
    """A shipped template by id, validated; each call returns a new dict."""
    return _load(TEMPLATE_DIR, name)


def _list(directory: Path) -> list[dict[str, str]]:
    summaries = []
    for name, path in _template_files(directory).items():
        document = _read(name, path)
        summaries.append({
            "id": document["id"],
            "title": document["title"],
            "description": document.get("description", ""),
        })
    return summaries


def _load(directory: Path, name: str) -> dict[str, Any]:
    if not isinstance(name, str) or not _TEMPLATE_ID.fullmatch(name):
        raise TemplateNotFound(str(name))
    path = _template_files(directory).get(name)
    if path is None:
        raise TemplateNotFound(name)
    document = _read(name, path)
    if document["id"] != name:
        raise TemplateError([f"{name}: the file declares the id {document['id']}"])
    return document


def _template_files(directory: Path) -> dict[str, Path]:
    """Template id -> file for ``directory``; the schema file is not a template."""
    found: dict[str, Path] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name == SCHEMA_FILE or path.suffix not in TEMPLATE_SUFFIXES:
            continue
        if not _TEMPLATE_ID.fullmatch(path.stem):
            raise TemplateError([f"{path.name}: the file name is not a valid template id"])
        if path.stem in found:
            raise TemplateError([f"{path.stem}: defined twice ({found[path.stem].name} and {path.name})"])
        found[path.stem] = path
    return found


def _read(name: str, path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise TemplateError([f"document is larger than {MAX_DOCUMENT_BYTES // 1024} KB"])
        raw = path.read_bytes()
    except OSError:
        raise TemplateError([f"{name}: the template file cannot be read"]) from None
    try:
        return parse_template(raw)
    except TemplateError as exc:
        raise TemplateError([f"{name}: {problem}" for problem in exc.errors]) from None
