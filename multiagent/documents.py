# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic Markdown rendering and result-step selection for workflow runs."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping
from typing import Any

from .workflows import WorkflowDefinition

DOCUMENT_KEY = "x-maf-document"
TITLE_MAX = 120
MAX_DEPTH = 6


def escape_text(value: str) -> str:
    """Escape ``&``, ``<`` and ``>`` so Markdown viewers cannot execute model HTML."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def document_title(definition: WorkflowDefinition) -> str:
    """Return the first line of ``definition.description`` (max 120 chars), or ``definition.id``."""
    desc = (definition.description or "").strip()
    if not desc:
        return definition.id
    first_line = desc.splitlines()[0].strip()
    if not first_line:
        return definition.id
    return first_line[:TITLE_MAX]


def document_filename(title: str, run_id: str) -> str:
    """File name of a run's document: the title as a slug (60 chars max), the first 8 chars of the run id, ``.md``."""
    folded = unicodedata.normalize("NFKD", str(title)).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:60].strip("-")
    short = re.sub(r"[^a-z0-9]", "", str(run_id).lower())[:8]
    return "-".join(part for part in (slug, short) if part) + ".md" if (slug or short) else "documento.md"


def result_step_id(definition: WorkflowDefinition, completed: Collection[str]) -> str | None:
    """Return the step id providing the run's result document, or ``None``.

    When ``definition.result_step`` is set, only that step counts (returning
    ``None`` if it did not complete, e.g. because ``when`` skipped it). When
    omitted, the result defaults to the last completed step in workflow order.
    """
    completed_set = set(completed)
    declared = getattr(definition, "result_step", None)
    if declared is not None:
        return declared if declared in completed_set else None
    for step in reversed(definition.steps):
        if step.id in completed_set:
            return step.id
    return None


def render_markdown(title: str, data: Mapping[str, Any], schema: Mapping[str, Any]) -> str:
    """Render structured step output ``data`` to Markdown using its JSON ``schema``."""
    clean_title = " ".join(str(title).splitlines()).strip()
    header = f"# {escape_text(clean_title)}" if clean_title else "#"

    defs = schema.get("$defs") if isinstance(schema, Mapping) and isinstance(schema.get("$defs"), Mapping) else {}
    root_schema = _resolve_schema(schema, defs)

    document_body = _extract_document_field(data, root_schema, defs)
    if document_body is not None:
        if document_body:
            return f"{header}\n\n{escape_text(document_body)}\n"
        return f"{header}\n"

    body_lines = _render_object(data, root_schema, defs, heading_level=2, depth=0)
    if not body_lines:
        return f"{header}\n"
    return "\n".join([header, "", *body_lines]) + "\n"


def _resolve_schema(node: Any, defs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(node, Mapping):
        return {}
    current = dict(node)
    seen: set[str] = set()
    while "$ref" in current:
        ref = current.get("$ref")
        extras = {k: v for k, v in current.items() if k != "$ref"}
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            return {"type": "object", **extras}
        key = ref[len("#/$defs/"):]
        target = defs.get(key)
        if key in seen or not isinstance(target, Mapping):
            return {"type": "object", **extras}
        seen.add(key)
        merged = dict(target)
        merged.update(extras)
        current = merged
    for union_key in ("anyOf", "oneOf"):
        variants = current.get(union_key)
        if isinstance(variants, list):
            non_null = [v for v in variants if not (isinstance(v, Mapping) and v.get("type") == "null")]
            if non_null:
                picked = _resolve_schema(non_null[0], defs)
                outer = {k: v for k, v in current.items() if k != union_key}
                picked.update(outer)
                return picked
    return current


def _extract_document_field(
    data: Mapping[str, Any],
    root_schema: Mapping[str, Any],
    defs: Mapping[str, Any],
) -> str | None:
    """Return the raw document string (or ``""`` when empty/null) if a property sets ``DOCUMENT_KEY``."""
    props = root_schema.get("properties")
    if not isinstance(props, Mapping):
        return None
    for key, raw_prop in props.items():
        resolved = _resolve_schema(raw_prop, defs)
        is_doc = (
            (isinstance(raw_prop, Mapping) and raw_prop.get(DOCUMENT_KEY) is True)
            or resolved.get(DOCUMENT_KEY) is True
        )
        if not is_doc:
            continue
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip("\r\n")
        return ""
    return None


def _heading_line(level: int, text: str) -> str:
    hashes = "#" * min(max(level, 1), MAX_DEPTH)
    clean = " ".join(str(text).splitlines()).strip()
    return f"{hashes} {escape_text(clean)}"


def _field_heading(name: str, prop_schema: Mapping[str, Any]) -> str:
    for key in ("description", "title"):
        candidate = prop_schema.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return name


def _item_label(item_schema: Mapping[str, Any]) -> str:
    for key in ("title", "description"):
        candidate = item_schema.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Item"


def _format_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return escape_text(str(value))
    if isinstance(value, str):
        stripped = value.strip()
        return escape_text(stripped) if stripped else None
    text = str(value).strip()
    return escape_text(text) if text else None


def _join_blocks(blocks: list[list[str]]) -> list[str]:
    lines: list[str] = []
    for index, block in enumerate(blocks):
        if index > 0:
            lines.append("")
        lines.extend(block)
    return lines


def _render_object(
    data: Mapping[str, Any],
    obj_schema: Mapping[str, Any],
    defs: Mapping[str, Any],
    *,
    heading_level: int,
    depth: int,
) -> list[str]:
    if depth >= MAX_DEPTH or not isinstance(data, Mapping):
        return []
    props = obj_schema.get("properties") if isinstance(obj_schema.get("properties"), Mapping) else {}
    ordered_keys = [k for k in props if k in data] + [k for k in data if k not in props]

    blocks: list[list[str]] = []
    for key in ordered_keys:
        value = data[key]
        if value is None:
            continue
        raw_prop = props.get(key)
        prop_schema = _resolve_schema(raw_prop, defs) if isinstance(raw_prop, Mapping) else {}
        body = _render_value(value, prop_schema, defs, heading_level=heading_level, depth=depth)
        if not body:
            continue
        heading = _field_heading(str(key), prop_schema)
        blocks.append([_heading_line(heading_level, heading), "", *body])
    return _join_blocks(blocks)


def _render_value(
    value: Any,
    prop_schema: Mapping[str, Any],
    defs: Mapping[str, Any],
    *,
    heading_level: int,
    depth: int,
) -> list[str]:
    if isinstance(value, Mapping):
        return _render_object(value, prop_schema, defs, heading_level=heading_level + 1, depth=depth + 1)

    if isinstance(value, (list, tuple)):
        if not value:
            return []
        item_schema = _resolve_schema(prop_schema.get("items"), defs)
        non_none = [item for item in value if item is not None]
        if not non_none:
            return []
        if all(isinstance(item, Mapping) for item in non_none):
            if depth + 1 >= MAX_DEPTH:
                return []
            label = _item_label(item_schema)
            item_blocks: list[list[str]] = []
            for index, item in enumerate(non_none, start=1):
                item_lines = _render_object(
                    item, item_schema, defs, heading_level=heading_level + 2, depth=depth + 1,
                )
                if not item_lines:
                    continue
                item_blocks.append([_heading_line(heading_level + 1, f"{label} {index}"), "", *item_lines])
            return _join_blocks(item_blocks)

        bullets: list[str] = []
        for item in non_none:
            if isinstance(item, Mapping):
                continue
            formatted = _format_scalar(item)
            if formatted is not None:
                single_line = " ".join(formatted.splitlines()).strip()
                if single_line:
                    bullets.append(f"- {single_line}")
        return bullets

    formatted = _format_scalar(value)
    return [formatted] if formatted is not None else []
