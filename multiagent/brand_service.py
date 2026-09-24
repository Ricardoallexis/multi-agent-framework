from __future__ import annotations

from .catalog import Catalog
from .contracts import BrandProfilePayload, ModelTier, TaskRequirements
from .db.store import Store
from .model_router import ModelBinding, ModelRouter
from .prompts import PromptManager


class BrandService:
    def __init__(self, store: Store, catalog: Catalog, prompts: PromptManager, router: ModelRouter):
        self.store = store
        self.catalog = catalog
        self.prompts = prompts
        self.router = router

    def import_profile(self, payload: BrandProfilePayload, *, created_by: str = "human", activate: bool = False):
        return self.store.save_brand_profile(payload, created_by=created_by, activate=activate)

    def generate_from_answers(self, answers: str, *, model_id: str = "cloud_fast"):
        if len(answers.strip()) < 50:
            raise ValueError("Branding answers are too short")
        template = self.prompts.load("branding/brand_profile", 2)
        prompt = template.text + "\n\n# Human answers\n" + answers.strip()
        spec = self.catalog.model(model_id)
        requirements = TaskRequirements(requires_web=False, output_schema="BrandProfilePayload", tier=ModelTier(spec.tier))
        response, attempts = self.router.execute(
            binding=ModelBinding(preferred_model=model_id, fallback_model=None),
            requirements=requirements,
            prompt=prompt,
            output_schema=BrandProfilePayload,
        )
        saved = self.store.save_brand_profile(response.parsed, created_by=f"branding_agent:{response.model}", activate=False)
        return {**saved, "attempts": attempts, "profile": response.parsed.model_dump(mode="json")}

    def activate(self, profile_id: str):
        self.store.activate_brand_profile(profile_id)
        return {"id": profile_id, "active": True}

    def activate_version(self, version: int):
        profile_id = self.store.activate_brand_version(version)
        return {"id": profile_id, "version": version, "active": True}
