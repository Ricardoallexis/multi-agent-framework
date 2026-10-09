from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Settings


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    model: str
    tier: str
    structured_output: bool
    web: bool
    num_ctx: int | None = None
    keep_alive: str | None = None
    thinking: bool | str | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    free_tier_eligible: bool = False
    capabilities: frozenset[str] | None = None
    roles: frozenset[str] | None = None
    permissions: frozenset[str] | None = None
    permissions_required: frozenset[str] | None = None
    allowed_data_classes: frozenset[str] | None = None
    pricing: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("capabilities", "roles", "permissions", "permissions_required", "allowed_data_classes"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, frozenset):
                object.__setattr__(self, name, frozenset([value] if isinstance(value, str) else value))


class Catalog:
    """Agents and models from ``catalog_dir`` (the bundled catalog by default)."""

    def __init__(self, settings: Settings, *, catalog_dir: Path | None = None):
        self.settings = settings
        self.catalog_dir = Path(catalog_dir) if catalog_dir is not None else settings.catalog_dir
        self.agents = self._load_yaml(self.catalog_dir / "agents.yaml").get("agents", {})
        models_document = self._load_yaml(self.catalog_dir / "models.yaml")
        raw_models = models_document.get("models", {})
        routing = models_document.get("routing_profiles", {}) or {}
        self.routing_profiles = dict(routing.get("profiles", {}) or {})
        self.routing_assignments = dict(routing.get("assignments", {}) or {})
        self.models: dict[str, ModelSpec] = {}
        for model_id, raw in raw_models.items():
            raw = dict(raw)
            if model_id in {"local_default", "local_reasoning"}:
                raw["model"] = settings.ollama_model
                raw["num_ctx"] = settings.ollama_num_ctx
                raw["keep_alive"] = settings.ollama_keep_alive
            elif model_id in {"gemini_fast", "cloud_fast"}:
                raw["model"] = settings.gemini_model
            elif model_id == "cloud_advanced":
                raw["model"] = settings.gemini_advanced_model
            elif model_id == "gemini_grounded":
                raw["model"] = settings.gemini_grounded_model
            elif model_id == "openai_optional":
                raw["model"] = settings.openai_model
            self.models[model_id] = ModelSpec(id=model_id, **raw)

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def routing_profile_for(self, workflow_id: str, step_id: str) -> tuple[str | None, dict[str, Any] | None]:
        """Return only an explicitly configured profile assignment for this workflow step."""
        workflow = self.routing_assignments.get(workflow_id, {})
        profile_id = workflow.get(step_id) if isinstance(workflow, dict) else None
        if profile_id is None:
            return None, None
        profile_id = str(profile_id)
        profile = self.routing_profiles.get(profile_id)
        return profile_id, dict(profile) if isinstance(profile, dict) else None

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Model is not configured: {model_id}") from exc