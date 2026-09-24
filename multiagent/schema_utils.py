from __future__ import annotations

from copy import deepcopy
from typing import Any


def flatten_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Dereference local $defs/$ref entries for constrained-output engines.

    This does not attempt to implement all of JSON Schema. It covers the $ref
    patterns emitted by Pydantic for the framework's intentionally simple
    contracts.
    """
    root = deepcopy(schema)
    defs = root.get("$defs", {})

    def resolve(node):
        if isinstance(node, list):
            return [resolve(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and node["$ref"].startswith("#/$defs/"):
            key = node["$ref"].split("/")[-1]
            if key not in defs:
                raise ValueError(f"Unknown $ref: {node['$ref']}")
            merged = deepcopy(defs[key])
            extras = {k: v for k, v in node.items() if k != "$ref"}
            merged.update(extras)
            return resolve(merged)
        return {k: resolve(v) for k, v in node.items() if k != "$defs"}

    root = resolve(root)
    root.pop("$defs", None)
    return root


def estimate_tokens_conservative(text: str) -> int:
    """Conservative local preflight estimate, not an exact tokenizer count.

    It is used to fail before Ollama truncates context. The actual token count
    is recorded later from prompt_eval_count.
    """
    if not text:
        return 0
    return max(1, int(len(text) / 3.2))


def ollama_structural_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce JSON Schema to the structural subset sent to Ollama.

    Pydantic may emit constraints and metadata (``minLength``, ``maxLength``,
    ``maxItems``, ``default``, ``title``, and similar fields). They are useful
    for final validation but are not required to guide JSON shape, and
    constrained-decoding support varies across Ollama versions and models.

    The framework keeps the complete Pydantic schema as the source of truth and
    validates the response again after inference. Ollama receives only
    structural information: types, properties, required fields, arrays, simple
    enums/unions, and ``additionalProperties`` when relevant.
    """
    flat = flatten_json_schema(schema)
    structural_keys = {
        "type", "properties", "required", "items", "enum",
        "anyOf", "oneOf", "allOf", "additionalProperties",
    }

    def clean(node):
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key not in structural_keys:
                continue
            if key == "properties" and isinstance(value, dict):
                result[key] = {name: clean(child) for name, child in value.items()}
            elif key == "additionalProperties" and isinstance(value, dict):
                result[key] = clean(value)
            else:
                result[key] = clean(value)
        return result

    return clean(flat)
