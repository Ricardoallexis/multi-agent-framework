# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Errors of the project template loader (PRY-T04).

``code`` and the HTTP-like status use the stable codes of ``services/errors.py``,
so the projects service can translate them without importing this package's internals.
"""
from __future__ import annotations


class TemplateError(ValueError):
    """A template document or template file is invalid; ``errors`` lists every problem found."""

    code = "invalid_request"  # services/errors.py INVALID_REQUEST, 422
    status = 422

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "Invalid template")


class TemplateNotFound(LookupError):
    """No shipped template has that id."""

    code = "not_found"  # services/errors.py NOT_FOUND, 404
    status = 404

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Template not found: {name}")
