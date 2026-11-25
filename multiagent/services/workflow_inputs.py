# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Workflow inputs and prompt previews, read from the definition files.

A workflow may declare its inputs (``inputs:``: label, description, example,
type, choices, required; see ``WorkflowInput``). Declaring is optional: every
``{placeholder}`` of the prompt templates the steps use is an input anyway,
exactly as the engine fills them, and a declaration only adds the words for
people. The two are merged:

* a declared input wins: its label, description, example, type, choices and
  ``required`` (when given) replace the derived ones (``source`` is
  ``"declared"``);
* a placeholder nobody declared is still an input (``source`` is
  ``"derived"``) and carries the prompt line where it appears;
* when a workflow has function steps, all declared inputs are offered because
  their code can read any key in ``context["inputs"]``;
* without function steps, a declared name that no prompt uses is not offered
  and ``warnings`` says so.

The derivation rules are:

* ``previous_output``, ``revision_feedback`` and every ``<name>_output`` are
  runtime context (``RunRequest`` reserves them); they are never inputs.
* every other placeholder is an input the operator supplies.
* an input is *required* when at least one step that is not skipped by
  default uses it. Inputs used only by skipped steps (for example a research
  step that needs ``requires_web``) are optional, and names ending in
  ``_context`` are optional too (the legacy social-post path fills them from
  stored context). Function-only inputs are optional unless ``required`` is
  explicitly declared.

Nothing here executes a step or calls a model: it reads templates, renders
text and reports it.
"""
from __future__ import annotations

import re
import string
from typing import Any, Mapping

from ..context.workflow import StepContext, build_context_parts
from ..prompts import PromptManager
from ..workflow_validation import WorkflowIssue
from ..workflows import DEFAULT_INPUT_TYPE, WorkflowDefinition, WorkflowInput, condition_is_true
from .errors import INVALID_DEFINITION, INVALID_REQUEST, ServiceError

SOURCE = "prompt_placeholders"
SOURCE_DECLARED = "declared"
ITEM_DECLARED = "declared"
ITEM_DERIVED = "derived"
RUNTIME_NAMES = frozenset({"previous_output", "revision_feedback"})
OPTIONAL_SUFFIX = "_context"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LINE_LIMIT = 160


def placeholders(template: str) -> list[str]:
    """Names used by ``str.format_map`` in the template, in order of first use."""
    names: list[str] = []
    try:
        for _, field, _, _ in string.Formatter().parse(template):
            match = _IDENTIFIER.match(field) if field else None
            if match and match.group(0) not in names:
                names.append(match.group(0))
    except ValueError:
        pass  # malformed braces: rendering reports them; keep what was read
    return names


def is_runtime_name(name: str) -> bool:
    return name in RUNTIME_NAMES or name.endswith("_output")


def _line_with(template: str, name: str) -> str | None:
    marker = "{" + name
    for line in template.splitlines():
        if marker in line:
            text = line.strip()
            return text if len(text) <= _LINE_LIMIT else text[:_LINE_LIMIT - 1] + "..."
    return None


def _label(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _request_for(definition: WorkflowDefinition, inputs: Mapping[str, str] | None = None) -> dict[str, Any]:
    """What a generic run stores as ``request``: conditions read nothing else from it."""
    return {"workflow_id": definition.id, "inputs": dict(inputs or {})}


def _template(prompts: PromptManager, prompt_id: str, version: int, step_id: str) -> str:
    try:
        return prompts.load(prompt_id, version).text
    except FileNotFoundError:
        raise ServiceError(INVALID_DEFINITION, f"Prompt not found for step '{step_id}': {prompt_id} v{version}",
                           status=422, details={"step_id": step_id, "prompt_id": prompt_id}) from None


def unused_input_warnings(definition: WorkflowDefinition, used: set[str] | frozenset[str]) -> list[WorkflowIssue]:
    """Warn for declarations not offered by prompts when no function can read them."""
    if any(step.function is not None for step in definition.steps):
        return []
    return [
        WorkflowIssue("unused_input", f"inputs[{index}].name",
                      f"Declared input {item.name!r} is not used by any step prompt, so it is not offered; "
                      "check the name against the prompt placeholders")
        for index, item in enumerate(definition.inputs) if item.name not in used
    ]


def _merged(usage: dict[str, Any], declared: WorkflowInput | None) -> dict[str, Any]:
    """One input as the interfaces see it: usage facts plus the declared words, when there are any."""
    item = dict(usage)
    context = item["name"].endswith(OPTIONAL_SUFFIX)
    default_required = bool(item["needed_by"]) and not context
    item.update({
        "context": context,
        "skipped_only": bool(item["used_in"]) and not item["needed_by"],
        "source": ITEM_DERIVED, "description": None, "example": None, "type": DEFAULT_INPUT_TYPE, "choices": [],
        "required": default_required,
    })
    if declared is not None:
        item.update({
            "source": ITEM_DECLARED,
            "label": declared.label or item["label"],
            "description": declared.description,
            "example": declared.example,
            "type": declared.type,
            "choices": list(declared.choices),
            "required": default_required if declared.required is None else declared.required,
        })
    return item


def derive_inputs(definition: WorkflowDefinition, prompts: PromptManager) -> dict[str, Any]:
    """``{"source", "description", "inputs": [...], "steps": [...], "warnings": [...]}`` for one workflow.

    Declared inputs come first, in the order the workflow declares them, then the
    placeholders nobody declared, in order of first use.
    """
    request = _request_for(definition)
    skipped = {step.id: not condition_is_true(step.when, request) for step in definition.steps}
    has_function_steps = any(step.function is not None for step in definition.steps)
    found: dict[str, dict[str, Any]] = {}
    for step in definition.steps:
        if step.function is not None:
            continue
        template = _template(prompts, step.prompt_id, step.prompt_version, step.id)
        for name in placeholders(template):
            if is_runtime_name(name):
                continue
            item = found.setdefault(name, {
                "name": name, "label": _label(name), "used_in": [], "needed_by": [],
                "prompt_line": _line_with(template, name),
            })
            if step.id not in item["used_in"]:
                item["used_in"].append(step.id)
            if not skipped[step.id] and step.id not in item["needed_by"]:
                item["needed_by"].append(step.id)
    declared = {item.name: item for item in definition.inputs}
    inputs = []
    for name, item in declared.items():
        usage = found.get(name)
        if usage is None:
            if not has_function_steps:
                continue
            usage = {"name": name, "label": _label(name), "used_in": [], "needed_by": [], "prompt_line": None}
        inputs.append(_merged(usage, item))
    inputs += [_merged(usage, None) for name, usage in found.items() if name not in declared]
    return {
        "source": SOURCE_DECLARED if any(item["source"] == ITEM_DECLARED for item in inputs) else SOURCE,
        "description": definition.description,
        "inputs": inputs,
        "steps": [{"id": step.id, **({"function": step.function} if step.function is not None
                                  else {"agent": step.agent}), "description": step.description, "when": step.when,
                   "skipped_by_default": skipped[step.id]} for step in definition.steps],
        "warnings": [issue.to_dict() for issue in unused_input_warnings(definition, set(found))],
    }


def missing_required(spec: Mapping[str, Any], values: Mapping[str, Any]) -> list[str]:
    """Names of required inputs that are absent or only whitespace."""
    return [item["name"] for item in spec["inputs"]
            if item["required"] and not str(values.get(item["name"], "") or "").strip()]


def preview(definition: WorkflowDefinition, prompts: PromptManager, spec: Mapping[str, Any],
            inputs: Mapping[str, str], *, execution_mode: str = "auto", sensitive: bool = False) -> dict[str, Any]:
    """Render every step's prompt with the given inputs, as plain text.

    Outputs of earlier steps do not exist yet, so they render empty
    (``needs_previous_outputs`` says which steps depend on them). A skipped step
    has no text and says why. The output contract the engine appends is only
    named (``contract``), not rendered.
    """
    request = _request_for(definition, inputs)
    # The context sections the engine appends to every step prompt (today's date), from the same function.
    sections = StepContext(build_context_parts(
        inputs=inputs, prior_outputs=[], previous_output=None, revision_feedback="")).sections()
    steps = []
    for index, step in enumerate(definition.steps):
        skipped = not condition_is_true(step.when, request)
        if step.function is not None:
            steps.append({"id": step.id, "function": step.function, "contract": step.contract,
                          "skipped": skipped, "skip": {"condition": step.when} if skipped else None,
                          "text": None, "needs_previous_outputs": False})
            continue
        entry: dict[str, Any] = {
            "id": step.id, "agent": step.agent, "prompt_id": step.prompt_id,
            "prompt_version": step.prompt_version, "contract": step.contract,
            "skipped": skipped, "skip": {"condition": step.when} if skipped else None,
            "text": None, "needs_previous_outputs": False,
        }
        if not skipped:
            template = _template(prompts, step.prompt_id, step.prompt_version, step.id)
            prior = [f"{earlier.id}_output" for earlier in definition.steps[:index]]
            entry["needs_previous_outputs"] = any(name in prior for name in placeholders(template))
            context = {**inputs, **{name: "" for name in prior}, "previous_output": "", "revision_feedback": ""}
            try:
                text = prompts.render(step.prompt_id, step.prompt_version, **context).text
                skills = "\n\n".join(prompts.skill_text(skill) for skill in step.skills)
            except FileNotFoundError as exc:
                raise ServiceError(INVALID_DEFINITION, f"Skill not found for step '{step.id}'", status=422,
                                   details={"step_id": step.id}) from exc
            except (TypeError, ValueError) as exc:
                raise ServiceError(INVALID_REQUEST, f"The prompt of step '{step.id}' could not be rendered: {exc}",
                                   status=422, details={"step_id": step.id}) from exc
            text += "".join("\n\n" + section for section in sections)
            text += ("\n\n## Active skill\n" + skills) if skills else ""
            if sensitive and execution_mode == "human_guided":
                text = ("# PRIVACY WARNING\nThis run is marked SENSITIVE. Review your data-handling policy "
                        "before copying this prompt to an external service.\n\n" + text)
            entry["text"] = text
        steps.append(entry)
    runnable = {step["id"] for step in steps if not step["skipped"]}
    empty_optional = [item["name"] for item in spec["inputs"]
                      if not item["required"] and runnable.intersection(item["used_in"])
                      and not str(inputs.get(item["name"], "") or "").strip()]
    return {
        "source": spec["source"],
        "missing_inputs": missing_required(spec, inputs),
        "empty_optional_inputs": empty_optional,
        "steps": steps,
    }
