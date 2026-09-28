# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Definition services: workflows, agents and bundles (read and validate only).

Contract fixed by G4a-T01; bodies are implemented by G4a-T02. Every method
returns JSON-ready data and raises only ``ServiceError`` for known failures.
``bundle=None`` means the built-in definitions; a name selects a bundle
registered in ``ApplicationServices``.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..bundles import DefinitionBundle, load_bundle
from ..workflow_validation import WorkflowDefinitionError, require_safe_workflow_id
from ..workflows import WorkflowDefinition
from .errors import INVALID_DEFINITION, ServiceError, service_errors

if TYPE_CHECKING:
    from . import ApplicationServices


class DefinitionServices:
    def __init__(self, services: ApplicationServices):
        self._services = services

    # Built-in definitions come from the running system; a bundle is loaded from
    # its registered folder on each call, so edits to it are picked up.

    def _workflows_dir(self, bundle: str | None) -> Path:
        if bundle is None:
            return Path(self._services.system.engine.workflows.workflows_dir)
        return self._services.bundle_path(bundle) / "workflows"

    def _bundle(self, name: str) -> DefinitionBundle:
        return load_bundle(self._services.bundle_path(name), self._services.settings)

    def _workflow_ids(self, bundle: str | None) -> list[str]:
        directory = self._workflows_dir(bundle)
        if not directory.is_dir():
            return []
        ids = []
        for path in directory.glob("*.yaml"):
            try:
                require_safe_workflow_id(path.stem)
            except WorkflowDefinitionError:
                continue  # not loadable by id, so not offered
            ids.append(path.stem)
        return sorted(ids)

    def _load(self, workflow_id: str, bundle: str | None) -> WorkflowDefinition:
        if bundle is not None:
            return self._bundle(bundle).load_workflow(workflow_id)
        return self._services.system.engine.preflight(workflow_id)

    @service_errors
    def list_workflows(self, *, bundle: str | None = None) -> list[dict[str, Any]]:
        """Workflow ids available for runs, sorted by id.

        Item: ``{"id": str, "bundle": str | None}``. Listing does not validate;
        use ``get_workflow`` for a validated definition.
        """
        return [{"id": workflow_id, "bundle": bundle} for workflow_id in self._workflow_ids(bundle)]

    @service_errors
    def get_workflow(self, workflow_id: str, *, bundle: str | None = None) -> dict[str, Any]:
        """One workflow, validated against its catalog, prompts and contracts.

        Returns ``{"id", "bundle", "max_iterations", "steps": [{"id", "agent",
        "prompt_id", "prompt_version", "skills", "contract", "preferred_model",
        "fallback_model", "requires_web", "when", "checkpoint_after"}]}``.
        Errors: ``not_found``, ``invalid_definition``.
        """
        if bundle is not None:
            self._services.bundle_path(bundle)  # unknown bundle: not_found before any loading
        definition = self._load(workflow_id, bundle)
        return {"bundle": bundle, **dataclasses.asdict(definition)}

    @service_errors
    def list_agents(self, *, bundle: str | None = None) -> list[dict[str, Any]]:
        """Agents of the catalog, sorted by id: ``{"id": str, **catalog fields}``."""
        catalog = self._services.system.catalog if bundle is None else self._bundle(bundle).catalog
        return [{"id": agent_id, **dict(fields or {})} for agent_id, fields in sorted(catalog.agents.items())]

    @service_errors
    def list_bundles(self) -> list[dict[str, Any]]:
        """Registered bundles, sorted by name: ``{"name": str}``. Never exposes paths."""
        return [{"name": name} for name in self._services.bundle_names()]

    @service_errors
    def validate_bundle(self, name: str) -> dict[str, Any]:
        """Load a registered bundle and validate all its workflows.

        Returns ``{"bundle": name, "valid": True, "workflows": [ids]}``.
        Errors: ``not_found``, ``invalid_definition`` (details carry the
        validator's issues).
        """
        loaded = self._bundle(name)
        workflow_ids = self._workflow_ids(name)
        problems = []
        for workflow_id in workflow_ids:
            try:
                loaded.load_workflow(workflow_id)
            except WorkflowDefinitionError as exc:
                problems.append(exc.to_payload())
        if problems:
            count = sum(len(problem["issues"]) for problem in problems)
            raise ServiceError(
                INVALID_DEFINITION,
                f"Invalid bundle '{name}': {count} problem{'' if count == 1 else 's'}",
                status=422, details={"bundle": name, "workflows": problems},
            )
        return {"bundle": name, "valid": True, "workflows": workflow_ids}
