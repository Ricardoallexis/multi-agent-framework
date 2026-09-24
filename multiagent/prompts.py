from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class PromptTemplate:
    prompt_id: str
    version: int
    text: str


class PromptManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def load(self, prompt_id: str, version: int) -> PromptTemplate:
        path = self.settings.prompts_dir / f"{prompt_id}.v{version}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")
        return PromptTemplate(prompt_id, version, path.read_text(encoding="utf-8"))

    def render(self, prompt_id: str, version: int, **values) -> PromptTemplate:
        template = self.load(prompt_id, version)
        try:
            rendered = template.text.format_map(_SafeDict(values))
        except Exception as exc:
            raise ValueError(f"Could not render {prompt_id}.v{version}: {exc}") from exc
        return PromptTemplate(prompt_id, version, rendered)

    def skill_text(self, skill_id: str) -> str:
        path = self.settings.skills_dir / f"{skill_id}.md"
        if not path.exists():
            raise FileNotFoundError(f"Skill not found: {skill_id}")
        return path.read_text(encoding="utf-8")


class _SafeDict(dict):
    def __missing__(self, key):
        return ""
