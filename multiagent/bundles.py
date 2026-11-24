"""Definition bundles: one folder holding a workflow family and everything it references.

Layout (every part except ``workflows/`` is optional and falls back to the bundled resources)::

    <bundle>/
        workflows/<workflow_id>.yaml
        catalog/agents.yaml, catalog/models.yaml
        prompts/<role>/<name>.v<N>.md
        skills/<skill>.md
        contracts.py      OUTPUT_SCHEMAS = {name: pydantic model}; optional SAMPLE_OUTPUTS = {name: dict}
        contracts.yaml    contracts as data (``maf.contracts/1``, see docs/OFFICES.md); optional ``samples:``
        team.yaml         optional team definition (S6.A1)

Bundle contracts are added to the bundled ``OUTPUT_SCHEMAS``; they may not redefine one. Re-exporting one of the
two neutral core contracts (``NEUTRAL_CONTRACTS``) as the same class is an alias, not a clash. ``contracts.py`` and ``contracts.yaml`` may
both be present, but a name may appear in only one of them.
A ``catalog/`` folder with only one of ``agents.yaml`` and ``models.yaml`` reads the other from the app catalog.
Function steps may name only functions already registered by the core or trusted host; unknown ids fail workflow validation.
``contracts.py`` is imported, which runs its code (``contracts.yaml`` never runs anything): load only bundles you trust, such as the
repository fixtures or your own private definitions.

Besides the workflow issue codes, ``load_bundle`` raises ``WorkflowDefinitionError`` with
``bundle_invalid`` (no ``workflows/``), ``bundle_contracts_invalid`` (``contracts.py`` fails to
import, ``contracts.yaml`` is not valid, or ``contracts.py`` has a malformed ``OUTPUT_SCHEMAS``/``SAMPLE_OUTPUTS``), ``contract_conflict`` (a bundle
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
from .contracts import NEUTRAL_CONTRACTS, OUTPUT_SCHEMAS
from .contracts_data import ContractsFileError, load_contracts_file
from .prompts import PromptManager
from .teams import Team, TeamDefinitionError, load_team
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
    team: Team | None = None

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
    catalog = Catalog(settings, catalog_dir=part("catalog"))
    team = _load_team(root, name, catalog)
    return DefinitionBundle(
        root=root,
        settings=settings,
        workflows=WorkflowCatalog(settings, workflows_dir=root / "workflows"),
        catalog=catalog,
        prompts=PromptManager(settings, prompts_dir=part("prompts"), skills_dir=part("skills")),
        output_schemas={**OUTPUT_SCHEMAS, **contracts},
        sample_outputs=samples,
        team=team,
    )


def _load_team(root: Path, name: str, catalog: Catalog) -> Team | None:
    path = root / "team.yaml"
    if not path.is_file():
        return None
    try:
        return load_team(path, agents=catalog.agents)
    except TeamDefinitionError as exc:
        raise WorkflowDefinitionError(
            name,
            [WorkflowIssue(issue.code, _team_issue_location(issue.location), issue.message) for issue in exc.issues],
        ) from exc


def _team_issue_location(location: str) -> str:
    if location in ("<document>", "<root>"):
        return "team.yaml"
    return f"team.yaml:{location}"


def _load_contracts(root: Path, name: str) -> tuple[dict[str, type[BaseModel]], dict[str, dict[str, Any]]]:
    py_path, yaml_path = root / "contracts.py", root / "contracts.yaml"
    contracts: dict[str, type[BaseModel]] = {}
    samples: dict[str, dict[str, Any]] = {}
    origin: dict[str, str] = {}
    if py_path.is_file():
        contracts, samples = _load_contracts_py(py_path, name)
        origin = {key: "contracts.py" for key in samples}
    if yaml_path.is_file():
        try:
            data_contracts, data_samples = load_contracts_file(yaml_path)
        except ContractsFileError as exc:
            raise WorkflowDefinitionError(name, [WorkflowIssue(exc.code, exc.location, exc.message)]) from exc
        twice = sorted(set(contracts) & set(data_contracts))
        if twice:
            _fail(name, "contract_conflict", "contracts.yaml",
                  f"contracts.py and contracts.yaml both define: {', '.join(twice)}")
        clashes = sorted(set(data_contracts) & set(OUTPUT_SCHEMAS))
        if clashes:
            _fail(name, "contract_conflict", "contracts.yaml",
                  f"Bundle contracts may not redefine bundled ones: {', '.join(clashes)}")
        twice = sorted(set(samples) & set(data_samples))
        if twice:
            _fail(name, "sample_output_invalid", "contracts.yaml",
                  f"Samples for {', '.join(twice)} are declared in contracts.py and in contracts.yaml")
        contracts = {**contracts, **data_contracts}
        samples = {**samples, **data_samples}
        origin.update({key: "contracts.yaml" for key in data_samples})
    # A sample may name a contract of this bundle or one of the two neutral core contracts.
    known = {**{key: OUTPUT_SCHEMAS[key] for key in NEUTRAL_CONTRACTS}, **contracts}
    for contract, sample in samples.items():
        where = origin[contract]
        if contract not in known:
            _fail(name, "sample_output_invalid", where,
                  f"Samples name {contract!r}, which is not a contract of this bundle")
        try:
            known[contract].model_validate(sample)
        except ValidationError as exc:
            _fail(name, "sample_output_invalid", where,
                  f"Sample for {contract!r} does not satisfy the contract: {exc.error_count()} error(s)")
    return contracts, samples


def _fail(name: str, code: str, location: str, message: str):
    raise WorkflowDefinitionError(name, [WorkflowIssue(code, location, message)])


def _load_contracts_py(path: Path, name: str) -> tuple[dict[str, type[BaseModel]], dict[str, dict[str, Any]]]:
    def fail(code: str, message: str):
        _fail(name, code, "contracts.py", message)

    module_name = f"_multiagent_bundle_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered like a normal import so pydantic can resolve the module's annotations.
    sys.modules[module_name] = module
    try:
        # SKL-T02: run the source the scan read, never a __pycache__ file. An unchecked .pyc would
        # run code the scan never saw, and writing one would change the bundle's fingerprint.
        exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    except Exception as exc:  # noqa: BLE001 - any import failure makes the bundle unusable
        del sys.modules[module_name]
        fail("bundle_contracts_invalid", f"contracts.py could not be imported: {type(exc).__name__}: {exc}")

    contracts = getattr(module, "OUTPUT_SCHEMAS", None)
    if not isinstance(contracts, dict) or not all(
        isinstance(key, str) and isinstance(value, type) and issubclass(value, BaseModel)
        for key, value in contracts.items()
    ):
        fail("bundle_contracts_invalid", "OUTPUT_SCHEMAS must be a dict of contract name -> pydantic model class")
    # L15: a neutral core contract re-exported as itself (the same class) is an alias, not a clash.
    # Any other bundled name, even as the same class, stays a clash.
    clashes = sorted(
        key for key in set(contracts) & set(OUTPUT_SCHEMAS)
        if key not in NEUTRAL_CONTRACTS or contracts[key] is not OUTPUT_SCHEMAS[key]
    )
    if clashes:
        fail("contract_conflict", f"Bundle contracts may not redefine bundled ones: {', '.join(clashes)}")

    samples = getattr(module, "SAMPLE_OUTPUTS", {})
    if not isinstance(samples, dict):
        fail("bundle_contracts_invalid", "SAMPLE_OUTPUTS must be a dict of contract name -> sample output")
    return dict(contracts), dict(samples)
