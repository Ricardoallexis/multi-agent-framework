"""Definition bundles: one folder holding a workflow family and everything it references.

Layout (every part except ``workflows/`` is optional and falls back to the bundled resources)::

    <bundle>/
        workflows/<workflow_id>.yaml
        catalog/agents.yaml, catalog/models.yaml
        prompts/<role>/<name>.v<N>.md
        skills/<skill>.md
        contracts.py      OUTPUT_SCHEMAS = {name: pydantic model}; optional SAMPLE_OUTPUTS = {name: dict}

Bundle contracts are added to the bundled ``OUTPUT_SCHEMAS``; they may not redefine one.
``contracts.py`` is imported, which runs its code: load only bundles you trust, such as the
repository fixtures or your own private definitions.

Besides the workflow issue codes, ``load_bundle`` raises ``WorkflowDefinitionError`` with
``bundle_invalid`` (no ``workflows/``), ``bundle_contracts_invalid`` (``contracts.py`` fails to
import or has a malformed ``OUTPUT_SCHEMAS``/``SAMPLE_OUTPUTS``), ``contract_conflict`` (a bundle
contract reuses a bundled name) and ``sample_output_invalid``.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .catalog import Catalog
from .config import Settings
from .contracts import OUTPUT_SCHEMAS
from .prompts import PromptManager
from .workflow_validation import WorkflowDefinitionError, WorkflowIssue, validate_workflow_definition
from .workflows import WorkflowCatalog, WorkflowDefinition


@dataclass(frozen=True)
class DefinitionBundle:
    root: Path
    settings: Settings
    workflows: WorkflowCatalog
    catalog: Catalog
    prompts: PromptManager
    output_schemas: Mapping[str, type[BaseModel]]
    sample_outputs: Mapping[str, dict[str, Any]] = field(default_factory=dict)

    def load_workflow(self, workflow_id: str) -> WorkflowDefinition:
        """Load a workflow of this bundle and validate it against the bundle's own resources."""
        definition = self.workflows.load(workflow_id)
        validate_workflow_definition(
            definition, catalog=self.catalog, settings=self.settings,
            output_schemas=self.output_schemas, prompts=self.prompts,
        )
        return definition


def load_bundle(root: Path | str, settings: Settings) -> DefinitionBundle:
    """Build a ``DefinitionBundle`` from ``root``; raise ``WorkflowDefinitionError`` if it is unusable."""
    root = Path(root).resolve()
    name = root.name
    if not (root / "workflows").is_dir():
        raise WorkflowDefinitionError(name, [WorkflowIssue(
            "bundle_invalid", "workflows", f"Bundle {name!r} has no workflows/ directory",
        )])

    def part(sub: str) -> Path | None:
        return root / sub if (root / sub).is_dir() else None

    contracts, samples = _load_contracts(root, name)
    return DefinitionBundle(
        root=root,
        settings=settings,
        workflows=WorkflowCatalog(settings, workflows_dir=root / "workflows"),
        catalog=Catalog(settings, catalog_dir=part("catalog")),
        prompts=PromptManager(settings, prompts_dir=part("prompts"), skills_dir=part("skills")),
        output_schemas={**OUTPUT_SCHEMAS, **contracts},
        sample_outputs=samples,
    )


def _load_contracts(root: Path, name: str) -> tuple[dict[str, type[BaseModel]], dict[str, dict[str, Any]]]:
    path = root / "contracts.py"
    if not path.is_file():
        return {}, {}

    def fail(code: str, message: str):
        raise WorkflowDefinitionError(name, [WorkflowIssue(code, "contracts.py", message)])

    module_name = f"_multiagent_bundle_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered like a normal import so pydantic can resolve the module's annotations.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any import failure makes the bundle unusable
        del sys.modules[module_name]
        fail("bundle_contracts_invalid", f"contracts.py could not be imported: {type(exc).__name__}: {exc}")

    contracts = getattr(module, "OUTPUT_SCHEMAS", None)
    if not isinstance(contracts, dict) or not all(
        isinstance(key, str) and isinstance(value, type) and issubclass(value, BaseModel)
        for key, value in contracts.items()
    ):
        fail("bundle_contracts_invalid", "OUTPUT_SCHEMAS must be a dict of contract name -> pydantic model class")
    clashes = sorted(set(contracts) & set(OUTPUT_SCHEMAS))
    if clashes:
        fail("contract_conflict", f"Bundle contracts may not redefine bundled ones: {', '.join(clashes)}")

    samples = getattr(module, "SAMPLE_OUTPUTS", {})
    if not isinstance(samples, dict):
        fail("bundle_contracts_invalid", "SAMPLE_OUTPUTS must be a dict of contract name -> sample output")
    for contract, sample in samples.items():
        if contract not in contracts:
            fail("sample_output_invalid", f"SAMPLE_OUTPUTS names {contract!r}, which is not a contract of this bundle")
        try:
            contracts[contract].model_validate(sample)
        except ValidationError as exc:
            fail("sample_output_invalid", f"Sample for {contract!r} does not satisfy the contract: {exc.error_count()} error(s)")
    return dict(contracts), dict(samples)
