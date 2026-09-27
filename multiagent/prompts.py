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
    """Versioned prompts and skills from explicit directories (the bundled ones by default)."""

    def __init__(self, settings: Settings, *, prompts_dir: Path | None = None, skills_dir: Path | None = None):
        self.settings = settings
        self.prompts_dir = Path(prompts_dir) if prompts_dir is not None else settings.prompts_dir
        self.skills_dir = Path(skills_dir) if skills_dir is not None else settings.skills_dir

    def load(self, prompt_id: str, version: int) -> PromptTemplate:
        path = self.prompts_dir / f"{prompt_id}.v{version}.md"
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
        path = self.skills_dir / f"{skill_id}.md"
        if not path.exists():
            raise FileNotFoundError(f"Skill not found: {skill_id}")
        return path.read_text(encoding="utf-8")


class _SafeDict(dict):
    def __missing__(self, key):
        return ""
