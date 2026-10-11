# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project templates as data (PRY-T04).

Templates and plans are JSON or YAML documents checked against ``schema.json``.
No key runs anything: there is no code, command, gate or workflow in them.
``load_template(name)`` returns a validated dict; ``validate_template(doc)``
returns the list of problems (empty when valid).
"""
from __future__ import annotations

from .errors import TemplateError, TemplateNotFound
from .loader import list_templates, load_template, parse_template
from .validation import MAX_DOCUMENT_BYTES, MAX_TASKS, SCHEMA_ID, validate_template

__all__ = [
    "MAX_DOCUMENT_BYTES",
    "MAX_TASKS",
    "SCHEMA_ID",
    "TemplateError",
    "TemplateNotFound",
    "list_templates",
    "load_template",
    "parse_template",
    "validate_template",
]
