from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Settings


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


@dataclass(frozen=True)
class WorkflowDefinition:
    id: str
    max_iterations: int
    steps: list[WorkflowStep]


class WorkflowCatalog:
    def __init__(self, settings: Settings):
        self.settings = settings

    def load(self, workflow_id: str) -> WorkflowDefinition:
        path = self.settings.workflows_dir / f"{workflow_id}.yaml"
        if not path.exists():
            raise KeyError(f"Workflow not found: {workflow_id}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return WorkflowDefinition(
            id=raw["id"],
            max_iterations=int(raw.get("max_iterations", 2)),
            steps=[WorkflowStep(**step) for step in raw["steps"]],
        )


def condition_is_true(condition: str | None, request: dict[str, Any]) -> bool:
    if not condition:
        return True
    if condition == "requires_web":
        return bool(request.get("requires_web"))
    if condition == "not_requires_web":
        return not bool(request.get("requires_web"))
    raise ValueError(f"Unsupported workflow condition: {condition}")
