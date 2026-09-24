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


class Catalog:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.agents = self._load_yaml(settings.catalog_dir / "agents.yaml").get("agents", {})
        raw_models = self._load_yaml(settings.catalog_dir / "models.yaml").get("models", {})
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

    def model(self, model_id: str) -> ModelSpec:
        try:
            return self.models[model_id]
        except KeyError as exc:
            raise KeyError(f"Model is not configured: {model_id}") from exc
