"""Pre-execution validation of workflow definitions.

Validation happens in two layers so that a definition fails before any run work:

* ``WorkflowCatalog.load`` parses the YAML document through
  :func:`parse_workflow_document`, which rejects malformed documents and
  values the current engine cannot interpret (structural checks).
* :func:`validate_workflow_definition` re-applies the structural checks, then
  resolves every external reference against the catalog, the bundled prompts
  and skills, and the output contracts.

Both raise :class:`WorkflowDefinitionError`, which carries every problem found
as a :class:`WorkflowIssue` with a stable ``code``:

========================  =====================================================
``yaml_invalid``          The document is not UTF-8 YAML or repeats a key.
``root_not_mapping``      The document root is not a mapping.
``missing_field``         A required root or step field is absent.
``unknown_field``         A root, step or declared-input field is not part of the format.
``invalid_steps``         ``steps`` is not a non-empty list.
``invalid_type``          A field has the wrong type.
``invalid_value``         A field has the right type but an unusable value (includes an
                          unknown step ``kind`` or ``effort``, and an input ``type`` that is not
                          ``text``, ``long_text`` or ``choice``, or ``choices`` that do not fit it).
``invalid_identifier``    A workflow or step id is not ``[A-Za-z0-9_][A-Za-z0-9_-]*``.
``workflow_id_mismatch``  The declared ``id`` differs from the requested one.
``duplicate_step_id``     Two steps share an id.
``duplicate_input``       Two declared ``inputs`` share a name.
``unsupported_condition`` ``when`` is not null, ``requires_web`` or ``not_requires_web``.
``invalid_reference``     A prompt or skill reference is not a relative ``a/b`` path.
``unknown_agent``         The agent is not in the catalog.
``inactive_agent``        The agent is marked ``active: false`` in the catalog.
``unknown_contract``      The output contract is not a known schema.
``unknown_model``         A preferred or fallback model is not in the catalog.
``model_incompatible``    A model lacks structured output, or web access for ``requires_web: true``.
``prompt_not_found``      No ``<prompt_id>.v<prompt_version>.md`` in the prompts directory.
``skill_not_found``       No ``<skill>.md`` in the skills directory.
========================  =====================================================

:func:`validate_resume_point` checks that a run paused on a step can still
resume on the current definition, and raises the same error with:

============================  =================================================
``resume_cursor_out_of_range``  The stored cursor is not a step of the workflow.
``resume_step_mismatch``        Another step (or none) now sits at the cursor.
``resume_contract_mismatch``    The paused step now declares another contract.
============================  =================================================
"""
from __future__ import annotations

import difflib
import math
import os
import re
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import MISSING, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .context.project_memory import setting_issue
from .capabilities import is_valid_capability_id
from .functions import get_function
from .routing_policy import EFFORT_LEVELS, STEP_KINDS
from .workflows import INPUT_TYPES, SUPPORTED_CONDITIONS, WorkflowDefinition, WorkflowInput, WorkflowStep

if TYPE_CHECKING:
    from .catalog import Catalog
    from .config import Settings
    from .prompts import PromptManager

IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
# Prompt and skill references are relative paths built only from identifier segments,
# so they can never be absolute, contain "..", or depend on the OS path separator.
RESOURCE_REFERENCE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*(?:/[A-Za-z0-9_][A-Za-z0-9_-]*)*")

DEFAULT_MAX_ITERATIONS = 2
ROOT_FIELDS = ("id", "description", "max_iterations", "inputs", "steps", "project_memory")
REQUIRED_ROOT_FIELDS = ("id", "steps")
STEP_FIELDS = tuple(field.name for field in fields(WorkflowStep))
# Fields with a default (``kind``, ``effort``, ``description``) may be omitted from a document.
REQUIRED_STEP_FIELDS = ("id", "contract")
MODEL_REQUIRED_STEP_FIELDS = ("id", "agent", "prompt_id", "prompt_version", "skills", "contract",
                              "preferred_model", "fallback_model", "requires_web", "when", "checkpoint_after")
FUNCTION_STEP_FIELDS = frozenset(("id", "function", "arguments", "contract", "description", "when", "checkpoint_after"))
INPUT_FIELDS = tuple(field.name for field in fields(WorkflowInput))
REQUIRED_INPUT_FIELDS = tuple(
    field.name for field in fields(WorkflowInput)
    if field.default is MISSING and field.default_factory is MISSING
)
# An input name is a prompt placeholder; the engine fills these runtime names itself.
INPUT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
RESERVED_INPUT_NAMES = ("previous_output", "revision_feedback")
RESERVED_INPUT_SUFFIX = "_output"

DOCUMENT = "<document>"
ROOT = "<root>"
REQUEST = "<request>"


@dataclass(frozen=True)
class WorkflowIssue:
    code: str
    location: str
    message: str
    step_id: str | None = None

    def __str__(self) -> str:
        step = f" (step '{self.step_id}')" if self.step_id else ""
        return f"[{self.code}] {self.location}{step}: {self.message}"

    def to_dict(self) -> dict[str, str | None]:
        """Public form shared by run events, API and CLI; ``step_id`` is None when not applicable."""
        return {"code": self.code, "location": self.location, "step_id": self.step_id, "message": self.message}


class WorkflowDefinitionError(ValueError):
    """A workflow definition cannot be executed by the current engine.

    ``issues`` holds every problem found in one pass, in document order;
    ``str()`` renders one problem per line.
    """

    def __init__(self, workflow_id: str, issues: Iterable[WorkflowIssue], *, source: Path | None = None):
        self.workflow_id = workflow_id
        self.issues = tuple(issues)
        self.source = source
        where = f" ({source})" if source else ""
        super().__init__("\n".join([self._summary(where), *(f"  - {issue}" for issue in self.issues)]))

    def to_payload(self) -> dict[str, Any]:
        """Public, JSON-ready form of the error.

        It leaves out ``source``, which is a local file path, so it is safe to
        store in run events or return from the API and CLI.
        """
        return {
            "error": self._summary(),
            "workflow_id": self.workflow_id,
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def _summary(self, where: str = "") -> str:
        count = len(self.issues)
        return f"Invalid workflow definition '{self.workflow_id}'{where}: {count} problem{'' if count == 1 else 's'}"


def validate_workflow_definition(
    definition: WorkflowDefinition,
    *,
    catalog: Catalog,
    settings: Settings,
    output_schemas: Mapping[str, type],
    prompts: PromptManager | None = None,
) -> None:
    """Raise ``WorkflowDefinitionError`` unless every step can be executed as defined.

    Prompts and skills are looked up where ``prompts`` reads them, or in the
    bundled directories of ``settings`` when it is omitted.
    """
    issues = structural_issues(definition)
    if not issues:
        issues = _reference_issues(
            definition, catalog=catalog, output_schemas=output_schemas,
            prompts_dir=prompts.prompts_dir if prompts is not None else settings.prompts_dir,
            skills_dir=prompts.skills_dir if prompts is not None else settings.skills_dir,
        )
    if issues:
        raise WorkflowDefinitionError(str(definition.id), issues)


def validate_resume_point(
    definition: WorkflowDefinition,
    *,
    current_step: int,
    step_id: str,
    expected_contract: str | None = None,
) -> None:
    """Raise ``WorkflowDefinitionError`` unless a run paused on ``step_id`` can resume here.

    ``current_step``, ``step_id`` and ``expected_contract`` are what the run stored
    when it paused. The definition may have been edited while the run waited, so
    this compares them with the step now at the cursor. Call it after
    ``validate_workflow_definition``; it reads nothing from disk.
    """
    steps = definition.steps
    issues: list[WorkflowIssue] = []
    if not _is_int(current_step) or not 0 <= current_step < len(steps):
        issues.append(WorkflowIssue(
            "resume_cursor_out_of_range", "current_step",
            f"The run paused at cursor {current_step!r}, but the workflow has {len(steps)} steps",
            step_id,
        ))
    else:
        step = steps[current_step]
        location = f"steps[{current_step}]"
        if step.id != step_id:
            moved_to = next((index for index, other in enumerate(steps) if other.id == step_id), None)
            where = (f"; that step is now steps[{moved_to}]" if moved_to is not None
                     else "; the workflow no longer has that step")
            issues.append(WorkflowIssue(
                "resume_step_mismatch", f"{location}.id",
                f"The run paused at step {step_id!r}, but {location} is now {step.id!r}{where}",
                step_id,
            ))
        elif expected_contract is not None and step.contract != expected_contract:
            issues.append(WorkflowIssue(
                "resume_contract_mismatch", f"{location}.contract",
                f"The run paused expecting contract {expected_contract!r}, but the step now declares {step.contract!r}",
                step_id,
            ))
    if issues:
        raise WorkflowDefinitionError(str(definition.id), issues)


def require_safe_workflow_id(workflow_id: Any) -> None:
    """Reject requested ids that could address a file outside the workflows directory."""
    if not isinstance(workflow_id, str) or not IDENTIFIER.fullmatch(workflow_id):
        raise WorkflowDefinitionError(str(workflow_id), [WorkflowIssue(
            "invalid_identifier", REQUEST,
            f"Workflow id {workflow_id!r} must match {IDENTIFIER.pattern}",
        )])


def parse_workflow_document(document: bytes | str, *, workflow_id: str, source: Path | None = None) -> WorkflowDefinition:
    """Build a structurally valid ``WorkflowDefinition`` from a YAML document."""

    def fail(issues: list[WorkflowIssue]):
        raise WorkflowDefinitionError(workflow_id, issues, source=source)

    try:
        text = document.decode("utf-8") if isinstance(document, bytes) else document
        raw = yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except UnicodeDecodeError as exc:
        fail([WorkflowIssue("yaml_invalid", DOCUMENT, f"The file is not valid UTF-8: {exc.reason} at byte {exc.start}")])
    except yaml.YAMLError as exc:
        fail([WorkflowIssue("yaml_invalid", DOCUMENT, f"Invalid YAML: {_describe_yaml_error(exc)}")])

    if not isinstance(raw, dict):
        fail([WorkflowIssue(
            "root_not_mapping", ROOT,
            f"Expected a mapping with fields {', '.join(ROOT_FIELDS)}; got {_kind(raw)}",
        )])

    issues = _field_set_issues(raw, required=REQUIRED_ROOT_FIELDS, allowed=ROOT_FIELDS, prefix="")
    steps = raw.get("steps")
    if "steps" in raw:
        if not isinstance(steps, list) or not steps:
            issues.append(WorkflowIssue("invalid_steps", "steps", f"Expected a non-empty list of steps; got {_kind(steps)}"))
        else:
            for index, step in enumerate(steps):
                location = f"steps[{index}]"
                if not isinstance(step, dict):
                    issues.append(WorkflowIssue("invalid_type", location, f"Expected a step mapping; got {_kind(step)}"))
                    continue
                issues += _field_set_issues(
                    step, required=REQUIRED_STEP_FIELDS if "function" in step else MODEL_REQUIRED_STEP_FIELDS,
                    allowed=STEP_FIELDS, prefix=f"{location}.",
                    step_id=_step_label(step.get("id")),
                )
                if "function" in step:
                    if step["function"] is None:
                        issues.append(WorkflowIssue("invalid_value", f"{location}.function",
                                                    "Function id must not be null", _step_label(step.get("id"))))
                    for field in step.keys() - FUNCTION_STEP_FIELDS:
                        if field in STEP_FIELDS:
                            issues.append(WorkflowIssue("invalid_value", f"{location}.{field}",
                                                        "Function steps cannot declare model or tool fields",
                                                        _step_label(step.get("id"))))
                elif "arguments" in step:
                    issues.append(WorkflowIssue("invalid_value", f"{location}.arguments",
                                                "Arguments require a function step", _step_label(step.get("id"))))
    # Unknown fields do not prevent building the definition, so value problems are
    # still reported alongside them; missing fields and malformed steps do.
    raw_inputs, input_issues = _raw_inputs(raw.get("inputs"))
    issues += input_issues
    constructible = "id" in raw and isinstance(steps, list) and bool(steps) and all(
        isinstance(step, dict) and all(name in step for name in
                                       (REQUIRED_STEP_FIELDS if "function" in step else MODEL_REQUIRED_STEP_FIELDS))
        for step in steps
    ) and raw_inputs is not None
    if not constructible:
        fail(issues)

    definition = WorkflowDefinition(
        id=raw["id"],
        max_iterations=raw.get("max_iterations", DEFAULT_MAX_ITERATIONS),
        steps=[WorkflowStep(**{
            **({name: None for name in MODEL_REQUIRED_STEP_FIELDS if name not in ("id", "contract")}
               if "function" in step else {}),
            **{name: step[name] for name in STEP_FIELDS if name in step},
            **({"checkpoint_after": step.get("checkpoint_after", False)} if "function" in step else {}),
        }) for step in steps],
        description=raw.get("description"),
        project_memory=raw.get("project_memory"),
        inputs=[
            WorkflowInput(**{
                name: ([] if name == "choices" and entry[name] is None else entry[name])
                for name in INPUT_FIELDS if name in entry
            })
            for entry in raw_inputs
        ],
    )
    if isinstance(definition.id, str) and definition.id != workflow_id:
        issues.append(WorkflowIssue(
            "workflow_id_mismatch", "id",
            f"The document declares id {definition.id!r} but was requested as {workflow_id!r}",
        ))
    issues += structural_issues(definition)
    if issues:
        fail(issues)
    return definition


def _raw_inputs(value: Any) -> tuple[list[dict] | None, list[WorkflowIssue]]:
    """Declared inputs of a parsed document: ``(entries, issues)``; entries is None when they cannot be built.

    An absent or null ``inputs`` means "nothing declared". Value checks happen in
    :func:`structural_issues`, on the built definition.
    """
    if value is None:
        return [], []
    if not isinstance(value, list):
        return None, [WorkflowIssue("invalid_type", "inputs", f"Expected a list of input mappings; got {_kind(value)}")]
    issues: list[WorkflowIssue] = []
    entries: list[dict] | None = []
    for index, entry in enumerate(value):
        location = f"inputs[{index}]"
        if not isinstance(entry, dict):
            issues.append(WorkflowIssue("invalid_type", location, f"Expected an input mapping; got {_kind(entry)}"))
            entries = None
            continue
        found = _field_set_issues(entry, required=REQUIRED_INPUT_FIELDS, allowed=INPUT_FIELDS, prefix=f"{location}.")
        issues += found
        if any(issue.code == "missing_field" for issue in found):
            entries = None
        elif entries is not None:
            entries.append(entry)
    return entries, issues


def structural_issues(definition: WorkflowDefinition) -> list[WorkflowIssue]:
    """Problems that make a definition uninterpretable regardless of the catalog."""
    issues: list[WorkflowIssue] = []
    if not isinstance(definition.id, str):
        issues.append(WorkflowIssue("invalid_type", "id", f"Expected a string; got {_kind(definition.id)}"))
    elif not IDENTIFIER.fullmatch(definition.id):
        issues.append(WorkflowIssue("invalid_identifier", "id", f"Workflow id {definition.id!r} must match {IDENTIFIER.pattern}"))

    max_iterations = definition.max_iterations
    if not _is_int(max_iterations):
        issues.append(WorkflowIssue("invalid_type", "max_iterations", f"Expected an integer; got {_kind(max_iterations)}"))
    elif max_iterations < 0:
        issues.append(WorkflowIssue("invalid_value", "max_iterations", f"Expected 0 or more revisions; got {max_iterations}"))

    _check_text(lambda code, field, message: issues.append(WorkflowIssue(code, field, message)),
                "description", definition.description)
    issues += _input_issues(definition.inputs)
    memory_issue = setting_issue(getattr(definition, "project_memory", None))
    if memory_issue:
        issues.append(WorkflowIssue("invalid_value", "project_memory", memory_issue))

    steps = definition.steps
    if not isinstance(steps, list) or not steps:
        issues.append(WorkflowIssue("invalid_steps", "steps", f"Expected a non-empty list of steps; got {_kind(steps)}"))
        return issues

    seen: dict[str, int] = {}
    for index, step in enumerate(steps):
        location = f"steps[{index}]"
        if not isinstance(step, WorkflowStep):
            issues.append(WorkflowIssue("invalid_type", location, f"Expected a WorkflowStep; got {_kind(step)}"))
            continue
        label = _step_label(step.id)

        def add(code: str, field: str, message: str) -> None:
            issues.append(WorkflowIssue(code, f"{location}.{field}", message, label))

        if not isinstance(step.id, str):
            add("invalid_type", "id", f"Expected a string; got {_kind(step.id)}")
        elif not IDENTIFIER.fullmatch(step.id):
            add("invalid_identifier", "id", f"Step id {step.id!r} must match {IDENTIFIER.pattern}")
        elif step.id in seen:
            add("duplicate_step_id", "id", f"Step id {step.id!r} is already used by steps[{seen[step.id]}]")
        else:
            seen[step.id] = index

        if step.function is not None:
            _check_name(add, "contract", step.contract)
            if not is_valid_capability_id(step.function):
                add("invalid_value", "function", f"Function id {step.function!r} must match the capability id pattern")
            if step.arguments is not None and not _literal_arguments(step.arguments):
                add("invalid_value", "arguments", "Expected a mapping of JSON-like literal arguments")
            for field in ("agent", "prompt_id", "prompt_version", "skills", "preferred_model", "fallback_model",
                          "requires_web", "tools", "egress", "effort", "project_memory", "role"):
                if getattr(step, field) is not None:
                    add("invalid_value", field, "Function steps cannot declare model or tool fields")
            if step.when is not None and step.when not in SUPPORTED_CONDITIONS:
                add("unsupported_condition", "when", f"Unsupported condition {step.when!r}")
            if not isinstance(step.checkpoint_after, bool):
                add("invalid_type", "checkpoint_after", f"Expected true or false; got {_kind(step.checkpoint_after)}")
            _check_text(add, "description", step.description)
            continue
        if step.arguments is not None:
            add("invalid_value", "arguments", "Arguments require a function step")

        _check_text(add, "description", step.description)
        if step.role is not None:
            _check_name(add, "role", step.role)
        # A step with a role may leave ``agent`` null: the run gives it the role's only agent member.
        for field in ("agent", "contract", "preferred_model"):
            if field == "agent" and step.agent is None and step.role is not None:
                continue
            _check_name(add, field, getattr(step, field))
        if step.fallback_model is not None:
            _check_name(add, "fallback_model", step.fallback_model)

        if not isinstance(step.prompt_id, str):
            add("invalid_type", "prompt_id", f"Expected a string; got {_kind(step.prompt_id)}")
        elif not RESOURCE_REFERENCE.fullmatch(step.prompt_id):
            add("invalid_reference", "prompt_id", f"Prompt id {step.prompt_id!r} must be a relative path like 'role/name'")
        if not _is_int(step.prompt_version):
            add("invalid_type", "prompt_version", f"Expected an integer; got {_kind(step.prompt_version)}")
        elif step.prompt_version < 1:
            add("invalid_value", "prompt_version", f"Prompt versions start at 1; got {step.prompt_version}")

        if not isinstance(step.skills, list):
            add("invalid_type", "skills", f"Expected a list of skill ids; got {_kind(step.skills)}")
        else:
            for skill in step.skills:
                if not isinstance(skill, str) or not RESOURCE_REFERENCE.fullmatch(skill):
                    add("invalid_reference", "skills", f"Skill id {skill!r} must be a relative path like 'group/name'")

        if not (isinstance(step.requires_web, bool) or step.requires_web == "from_request"):
            add("invalid_value", "requires_web", f"Expected true, false or 'from_request'; got {step.requires_web!r}")
        if step.when is not None and not (isinstance(step.when, str) and step.when in SUPPORTED_CONDITIONS):
            add("unsupported_condition", "when",
                f"Unsupported condition {step.when!r}; use null, {', '.join(repr(c) for c in SUPPORTED_CONDITIONS)}")

        # Any step may request a review: approving an intermediate one resumes at the next step.
        if not isinstance(step.checkpoint_after, bool):
            add("invalid_type", "checkpoint_after", f"Expected true or false; got {_kind(step.checkpoint_after)}")

        if not isinstance(step.kind, str):
            add("invalid_type", "kind", f"Expected one of {', '.join(STEP_KINDS)}; got {_kind(step.kind)}")
        elif step.kind not in STEP_KINDS:
            add("invalid_value", "kind", f"Unknown step kind {step.kind!r}; use one of {', '.join(STEP_KINDS)}")
        if step.egress is not None:
            policy = step.egress
            if not isinstance(policy, dict) or set(policy) - {"action", "include_local"} or "action" not in policy:
                add("invalid_value", "egress", "Expected {action: off|redact|block|escalate, include_local: false}")
            else:
                if policy["action"] is False:  # YAML 1.1 parses unquoted `off` as false.
                    policy["action"] = "off"
                if policy["action"] not in ("off", "redact", "block", "escalate"):
                    add("invalid_value", "egress.action", "Unknown egress action")
                if not isinstance(policy.get("include_local", False), bool):
                    add("invalid_type", "egress.include_local", "Expected true or false")
        memory_issue = setting_issue(getattr(step, "project_memory", None))
        if memory_issue:
            add("invalid_value", "project_memory", memory_issue)
        if step.tools is not None:
            if not isinstance(step.tools, list):
                add("invalid_type", "tools", f"Expected a list of tool calls; got {_kind(step.tools)}")
            else:
                for position, entry in enumerate(step.tools):
                    where = f"tools[{position}]"
                    if not isinstance(entry, dict) or set(entry) - {"tool", "capability", "version", "arguments"}:
                        add("invalid_value", where, "Expected {tool | capability, version?, arguments?}")
                        continue
                    if ("tool" in entry) == ("capability" in entry):
                        add("invalid_value", where, "Name exactly one of 'tool' or 'capability'")
                    elif not isinstance(entry.get("tool", entry.get("capability")), str):
                        add("invalid_type", where, "The tool or capability id must be a string")
                    version = entry.get("version")
                    if version is not None and (not _is_int(version) or version < 1 or "tool" not in entry):
                        add("invalid_value", f"{where}.version", "A version is a positive integer next to 'tool'")
                    if not isinstance(entry.get("arguments", {}), dict):
                        add("invalid_type", f"{where}.arguments", "Expected a mapping of arguments")
        # ``effort`` null (or omitted) leaves the provider default. In YAML, ``none`` is the
        # string "none" (no reasoning); ``off``/``no`` parse as booleans and are rejected.
        if step.effort is not None:
            if not isinstance(step.effort, str):
                add("invalid_type", "effort",
                    f"Expected one of {', '.join(EFFORT_LEVELS)} (or null for the provider default); got {_kind(step.effort)}")
            elif step.effort not in EFFORT_LEVELS:
                add("invalid_value", "effort",
                    f"Unknown effort {step.effort!r}; use one of {', '.join(EFFORT_LEVELS)}")
    return issues


def _reference_issues(definition: WorkflowDefinition, *, catalog: Catalog, prompts_dir: Path, skills_dir: Path,
                      output_schemas: Mapping[str, type]) -> list[WorkflowIssue]:
    issues: list[WorkflowIssue] = []
    for index, step in enumerate(definition.steps):
        location = f"steps[{index}]"

        def add(code: str, field: str, message: str) -> None:
            issues.append(WorkflowIssue(code, f"{location}.{field}", message, step.id))

        if step.contract not in output_schemas:
            add("unknown_contract", "contract",
                f"Unknown output contract {step.contract!r}; known contracts: {_names(output_schemas)}")

        if step.function is not None:
            if get_function(step.function) is None:
                add("unknown_function", "function", f"Unknown registered function {step.function!r}")
            continue

        if step.agent is None and step.role is not None:
            pass  # the agent comes from the role when the run starts (``teams.step_roles``)
        elif step.agent not in catalog.agents:
            add("unknown_agent", "agent", f"Unknown agent {step.agent!r}; configured agents: {_names(catalog.agents)}")
        elif isinstance(catalog.agents[step.agent], dict) and catalog.agents[step.agent].get("active") is False:
            add("inactive_agent", "agent", f"Agent {step.agent!r} is marked inactive in the catalog")

        for field in ("preferred_model", "fallback_model"):
            model_id = getattr(step, field)
            if model_id is None:
                continue
            spec = catalog.models.get(model_id)
            if spec is None:
                add("unknown_model", field,
                    f"Model {model_id!r} is not configured in the catalog; configured models: {_names(catalog.models)}")
            elif not spec.structured_output:
                add("model_incompatible", field, f"Model {model_id!r} does not support structured output")
            elif step.requires_web is True and not spec.web:
                add("model_incompatible", field,
                    f"Model {model_id!r} has no web access, but the step sets requires_web: true")

        prompt_file = f"{step.prompt_id}.v{step.prompt_version}.md"
        if not _resource_file_exists(prompts_dir, prompt_file):
            add("prompt_not_found", "prompt_id",
                f"Prompt {step.prompt_id!r} version {step.prompt_version} not found; "
                f"expected {prompt_file} in the prompts directory")
        for skill in step.skills:
            if not _resource_file_exists(skills_dir, f"{skill}.md"):
                add("skill_not_found", "skills", f"Skill {skill!r} not found; expected {skill}.md in the skills directory")
    return issues


def _resource_file_exists(root: Path, relative: str) -> bool:
    """Match each segment by exact name so results do not depend on file-system case rules."""
    current = root
    for part in relative.split("/"):
        try:
            with os.scandir(current) as entries:
                names = {entry.name for entry in entries}
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return current.is_file() and current.resolve().is_relative_to(root.resolve())


def _field_set_issues(mapping: dict, *, required: Iterable[str], allowed: tuple[str, ...], prefix: str,
                      step_id: str | None = None) -> list[WorkflowIssue]:
    issues = [
        WorkflowIssue("missing_field", f"{prefix}{name}", "Required field is missing", step_id)
        for name in required if name not in mapping
    ]
    for key in mapping:
        if key in allowed:
            continue
        hint = difflib.get_close_matches(str(key), allowed, n=1)
        suggestion = f"; did you mean {hint[0]!r}?" if hint else f"; expected one of {', '.join(allowed)}"
        issues.append(WorkflowIssue("unknown_field", f"{prefix}{key}", f"Unknown field {key!r}{suggestion}", step_id))
    return issues


def _check_name(add, field: str, value: Any) -> None:
    if not isinstance(value, str):
        add("invalid_type", field, f"Expected a string; got {_kind(value)}")
    elif not value.strip():
        add("invalid_value", field, "Expected a non-empty name")


def _check_text(add, field: str, value: Any) -> None:
    """An optional human-readable text: absent (None) or a non-blank string."""
    if value is None:
        return
    if not isinstance(value, str):
        add("invalid_type", field, f"Expected a string; got {_kind(value)}")
    elif not value.strip():
        add("invalid_value", field, "Expected text, or leave the field out")


def _input_issues(inputs: Any) -> list[WorkflowIssue]:
    """Value checks of the declared inputs (their shape was already checked while parsing)."""
    if not isinstance(inputs, list):
        return [WorkflowIssue("invalid_type", "inputs", f"Expected a list of inputs; got {_kind(inputs)}")]
    issues: list[WorkflowIssue] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(inputs):
        location = f"inputs[{index}]"
        if not isinstance(item, WorkflowInput):
            issues.append(WorkflowIssue("invalid_type", location, f"Expected a WorkflowInput; got {_kind(item)}"))
            continue

        def add(code: str, field: str, message: str, _location: str = location) -> None:
            issues.append(WorkflowIssue(code, f"{_location}.{field}", message))

        name = item.name
        if not isinstance(name, str):
            add("invalid_type", "name", f"Expected a string; got {_kind(name)}")
        elif not INPUT_NAME.fullmatch(name):
            add("invalid_identifier", "name", f"Input name {name!r} must match {INPUT_NAME.pattern}")
        elif name in RESERVED_INPUT_NAMES or name.endswith(RESERVED_INPUT_SUFFIX):
            add("invalid_value", "name",
                f"Input name {name!r} is filled by the engine (runtime context), so it cannot be declared as an input")
        elif name in seen:
            add("duplicate_input", "name", f"Input {name!r} is already declared by inputs[{seen[name]}]")
        else:
            seen[name] = index

        for field in ("label", "description", "example"):
            _check_text(add, field, getattr(item, field))
        if item.required is not None and not isinstance(item.required, bool):
            add("invalid_type", "required", f"Expected true or false; got {_kind(item.required)}")

        choices = item.choices
        if not isinstance(item.type, str):
            add("invalid_type", "type", f"Expected one of {', '.join(INPUT_TYPES)}; got {_kind(item.type)}")
        elif item.type not in INPUT_TYPES:
            add("invalid_value", "type", f"Unknown input type {item.type!r}; use one of {', '.join(INPUT_TYPES)}")
        if not isinstance(choices, list):
            add("invalid_type", "choices", f"Expected a list of strings; got {_kind(choices)}")
        elif item.type != "choice":
            if choices and item.type in INPUT_TYPES:
                add("invalid_value", "choices", "choices is only allowed when type is 'choice'")
        else:
            bad = [choice for choice in choices if not isinstance(choice, str) or not choice.strip()]
            if bad:
                add("invalid_value", "choices", f"Every choice must be a non-blank string; got {bad[0]!r}")
            elif not choices:
                add("invalid_value", "choices", "An input of type 'choice' needs at least one choice")
            elif len(set(choices)) != len(choices):
                add("invalid_value", "choices", "Choices must not repeat")
            elif isinstance(item.example, str) and item.example not in choices:
                add("invalid_value", "example", f"The example {item.example!r} is not one of the choices")
    return issues


def _step_label(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _literal_arguments(value: Any) -> bool:
    if not isinstance(value, dict):
        return False

    def literal(item: Any) -> bool:
        if item is None or isinstance(item, (str, bool, int)):
            return True
        if isinstance(item, float):
            return math.isfinite(item)
        if isinstance(item, list):
            return all(literal(child) for child in item)
        if isinstance(item, dict):
            return all(isinstance(key, str) and literal(child) for key, child in item.items())
        return False

    return literal(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return f"boolean {value!r}"
    if isinstance(value, (str, int, float)):
        return f"{type(value).__name__} {value!r}"
    return type(value).__name__


def _names(mapping: Mapping[str, Any]) -> str:
    return ", ".join(sorted(mapping)) or "(none)"


def _describe_yaml_error(exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError):
        parts = [part for part in (exc.context, exc.problem) if part]
        mark = exc.problem_mark or exc.context_mark
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        return ", ".join(parts) + where
    return str(exc)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects repeated mapping keys instead of keeping the last value."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if isinstance(key, Hashable) and key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark,
            )
        if isinstance(key, Hashable):
            seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)
