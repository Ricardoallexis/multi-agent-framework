# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Workflow inputs and prompt previews, read from the definition files.

Workflow YAML files do not declare their inputs (their format has no field for
them), so the expected inputs are derived from the ``{placeholders}`` of the
prompt templates the steps use, exactly as the engine fills them:

* ``previous_output``, ``revision_feedback`` and every ``<name>_output`` are
  runtime context (``RunRequest`` reserves them); they are never inputs.
* every other placeholder is an input the operator supplies.
* an input is *required* when at least one step that is not skipped by
  default uses it. Inputs used only by skipped steps (for example a research
  step that needs ``requires_web``) are optional, and names ending in
  ``_context`` are optional too (the legacy social-post path fills them from
  stored context).

Nothing here executes a step or calls a model: it reads templates, renders
text and reports it. A future explicit ``inputs:`` section in the workflow
format can replace the derivation without changing these return shapes.
"""
from __future__ import annotations

import re
import string
from typing import Any, Mapping

from ..prompts import PromptManager
from ..workflows import WorkflowDefinition, condition_is_true
from .errors import INVALID_DEFINITION, INVALID_REQUEST, ServiceError

SOURCE = "prompt_placeholders"
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


def derive_inputs(definition: WorkflowDefinition, prompts: PromptManager) -> dict[str, Any]:
    """``{"source", "inputs": [...], "steps": [...]}`` for one workflow definition."""
    request = _request_for(definition)
    skipped = {step.id: not condition_is_true(step.when, request) for step in definition.steps}
    found: dict[str, dict[str, Any]] = {}
    for step in definition.steps:
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
    inputs = []
    for item in found.values():
        item["context"] = item["name"].endswith(OPTIONAL_SUFFIX)
        item["required"] = bool(item["needed_by"]) and not item["context"]
        item["skipped_only"] = not item["needed_by"]
        inputs.append(item)
    return {
        "source": SOURCE,
        "inputs": inputs,
        "steps": [{"id": step.id, "agent": step.agent, "when": step.when, "skipped_by_default": skipped[step.id]}
                  for step in definition.steps],
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
    steps = []
    for index, step in enumerate(definition.steps):
        skipped = not condition_is_true(step.when, request)
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
