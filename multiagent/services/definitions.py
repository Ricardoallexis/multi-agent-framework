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
from ..prompts import PromptManager
from ..teams import validate_team_workflows
from ..workflow_validation import WorkflowDefinitionError, require_safe_workflow_id
from ..workflows import WorkflowCatalog, WorkflowDefinition
from . import workflow_inputs
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

    @staticmethod
    def _public_definition(definition: WorkflowDefinition, bundle: str | None) -> dict[str, Any]:
        result = {"bundle": bundle, **dataclasses.asdict(definition)}
        for step in result["steps"]:
            if step.get("function") is not None:
                for key in ("agent", "prompt_id", "prompt_version", "skills", "preferred_model",
                            "fallback_model", "requires_web"):
                    step.pop(key, None)
        return result

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
        "fallback_model", "requires_web", "when", "checkpoint_after", "kind", "effort",
        "description"}], "description", "inputs": [declared inputs]}``
        (``kind`` is ``work`` and ``effort``, ``description`` and ``inputs`` are ``None``/empty
        unless the document sets them).
        Errors: ``not_found``, ``invalid_definition``.
        """
        if bundle is not None:
            self._services.bundle_path(bundle)  # unknown bundle: not_found before any loading
        definition = self._load(workflow_id, bundle)
        return self._public_definition(definition, bundle)

    # --- reading helpers for interfaces that show or fill a workflow (no validation, no model) ---

    def _read_parts(self, workflow_id: str, bundle: str | None) -> tuple[WorkflowDefinition, PromptManager]:
        """Parse a workflow and find its prompts without importing bundle code.

        ``get_workflow`` validates and, for bundles, imports ``contracts.py`` on every call;
        a form that refreshes as the operator types must not. Structural errors still raise.
        """
        if bundle is None:
            engine = self._services.system.engine
            return engine.workflows.load(workflow_id), engine.prompts
        root = self._services.bundle_path(bundle)
        definition = WorkflowCatalog(self._services.settings, workflows_dir=root / "workflows").load(workflow_id)
        prompts = PromptManager(
            self._services.settings,
            prompts_dir=root / "prompts" if (root / "prompts").is_dir() else None,
            skills_dir=root / "skills" if (root / "skills").is_dir() else None,
        )
        return definition, prompts

    @service_errors
    def read_workflow(self, workflow_id: str, *, bundle: str | None = None) -> dict[str, Any]:
        """Structure of one workflow (same shape as ``get_workflow``) without catalog validation.

        Meant for showing the steps of a run whose workflow may have changed since;
        use ``get_workflow`` when the definition must be valid. Errors: ``not_found``,
        ``invalid_definition`` (the document cannot be parsed).
        """
        definition, _ = self._read_parts(workflow_id, bundle)
        return self._public_definition(definition, bundle)

    @service_errors
    def workflow_inputs(self, workflow_id: str, *, bundle: str | None = None) -> dict[str, Any]:
        """Inputs the workflow expects: the ones it declares merged with its prompt placeholders.

        Returns ``{"id", "bundle", "source", "description", "inputs": [{"name", "label",
        "description", "example", "type", "choices", "source", "required", "skipped_only",
        "context", "used_in", "needed_by", "prompt_line"}], "steps": [...], "warnings": [...]}``
        (item ``source`` is ``declared`` or ``derived``; ``description`` and ``example`` are
        ``None`` for derived inputs); see ``workflow_inputs`` for the rules.
        Errors: ``not_found``, ``invalid_definition``.
        """
        definition, prompts = self._read_parts(workflow_id, bundle)
        return {"id": workflow_id, "bundle": bundle, **workflow_inputs.derive_inputs(definition, prompts)}

    @service_errors
    def preview_prompts(self, workflow_id: str, inputs: dict[str, str], *, bundle: str | None = None,
                        execution_mode: str = "auto", sensitive: bool = False) -> dict[str, Any]:
        """Render each step's prompt for these inputs as text; nothing is executed or stored.

        Returns ``{"id", "bundle", "source", "missing_inputs", "empty_optional_inputs",
        "steps": [{"id", "skipped", "skip", "text", "needs_previous_outputs", ...}]}``.
        Errors: ``not_found``, ``invalid_definition``, ``invalid_request`` (a prompt cannot be rendered).
        """
        definition, prompts = self._read_parts(workflow_id, bundle)
        spec = workflow_inputs.derive_inputs(definition, prompts)
        result = workflow_inputs.preview(definition, prompts, spec, inputs,
                                         execution_mode=execution_mode, sensitive=sensitive)
        return {"id": workflow_id, "bundle": bundle, **result}

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
        definitions = []
        for workflow_id in workflow_ids:
            try:
                definitions.append(loaded.load_workflow(workflow_id))
            except WorkflowDefinitionError as exc:
                problems.append(exc.to_payload())
        if loaded.team is None:
            if problems:
                count = sum(len(problem["issues"]) for problem in problems)
                raise ServiceError(
                    INVALID_DEFINITION,
                    f"Invalid bundle '{name}': {count} problem{'' if count == 1 else 's'}",
                    status=422, details={"bundle": name, "workflows": problems},
                )
            return {"bundle": name, "valid": True, "workflows": workflow_ids}

        system = self._services.system
        engine = getattr(system, "engine", None)
        team_issues = validate_team_workflows(
            loaded.team,
            definitions,
            output_schemas=loaded.output_schemas,
            skills_dir=loaded.prompts.skills_dir,
            tool_registry=getattr(system, "tool_registry", None),
            tool_selection=getattr(engine, "tool_selection", None),
        )
        for problem in problems:
            for issue in problem["issues"]:
                issue["severity"] = "error"
        team_payload = [issue.to_dict() for issue in team_issues]
        workflow_error_count = sum(len(problem["issues"]) for problem in problems)
        team_error_count = sum(issue.severity == "error" for issue in team_issues)
        if workflow_error_count or team_error_count:
            count = workflow_error_count + team_error_count
            raise ServiceError(
                INVALID_DEFINITION,
                f"Invalid bundle '{name}': {count} problem{'' if count == 1 else 's'}",
                status=422, details={"bundle": name, "workflows": problems, "team": team_payload},
            )
        result: dict[str, Any] = {"bundle": name, "valid": True, "workflows": workflow_ids}
        warnings = [issue.to_dict() for issue in team_issues if issue.severity == "warning"]
        if warnings:
            result["warnings"] = warnings
        return result
