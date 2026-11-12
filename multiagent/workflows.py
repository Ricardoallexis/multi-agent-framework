from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .routing_policy import DEFAULT_STEP_KIND


# Values of WorkflowInput.type; the GUI shows a one-line field, a text area or a select.
INPUT_TYPES = ("text", "long_text", "choice")
DEFAULT_INPUT_TYPE = "text"


@dataclass(frozen=True)
class WorkflowInput:
    """One input a workflow declares for the person who starts a run.

    Declaring inputs is optional: a workflow that omits them gets its inputs
    derived from the prompt placeholders. ``required`` None means "decide as
    the derived rule does" (needed by a step that is not skipped by default).
    """

    name: str
    label: str | None = None
    description: str | None = None
    example: str | None = None
    required: bool | None = None
    type: str = DEFAULT_INPUT_TYPE
    # Only for type "choice": the values the person may pick.
    choices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowStep:
    id: str
    agent: str
    prompt_id: str
    prompt_version: int
    skills: list[str]
    contract: str
    preferred_model: str
    fallback_model: str | None
    requires_web: bool | str
    when: str | None
    checkpoint_after: bool
    # Optional fields: a document that omits them behaves exactly as before.
    # ``kind`` is one of routing_policy.STEP_KINDS; ``effort`` is one of
    # routing_policy.EFFORT_LEVELS, or None to leave the provider default.
    kind: str = DEFAULT_STEP_KIND
    effort: str | None = None
    # Short sentence for people: what the step does.
    description: str | None = None
    egress: dict[str, Any] | None = None


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    max_iterations: int
    steps: list[WorkflowStep]
    # Optional, for people: what the workflow produces and when to use it.
    description: str | None = None
    # Optional declared inputs; see WorkflowInput.
    inputs: list[WorkflowInput] = field(default_factory=list)


class WorkflowCatalog:
    """Loads workflow definitions by id from a single directory.

    ``workflows_dir`` defaults to the bundled resources; an explicit directory
    lets callers keep their own definitions outside the package.
    """

    def __init__(self, settings: Settings, *, workflows_dir: Path | None = None):
        self.settings = settings
        self.workflows_dir = Path(workflows_dir) if workflows_dir is not None else settings.workflows_dir

    def load(self, workflow_id: str) -> WorkflowDefinition:
        """Return a structurally valid definition.

        Raises ``KeyError`` when no such workflow exists and
        ``WorkflowDefinitionError`` when the id or the document is invalid.
        Catalog, prompt, skill and contract references are checked separately
        by ``validate_workflow_definition``.
        """
        # Imported here because the validation module builds these dataclasses.
        from .workflow_validation import parse_workflow_document, require_safe_workflow_id

        require_safe_workflow_id(workflow_id)
        path = self.workflows_dir / f"{workflow_id}.yaml"
        if not path.is_file():
            raise KeyError(f"Workflow not found: {workflow_id}")
        return parse_workflow_document(path.read_bytes(), workflow_id=workflow_id, source=path)


# Values of WorkflowStep.when that condition_is_true understands; null means "always".
SUPPORTED_CONDITIONS = ("requires_web", "not_requires_web")


def condition_is_true(condition: str | None, request: dict[str, Any]) -> bool:
    if not condition:
        return True
    if condition == "requires_web":
        return bool(request.get("requires_web"))
    if condition == "not_requires_web":
        return not bool(request.get("requires_web"))
    raise ValueError(f"Unsupported workflow condition: {condition}")
