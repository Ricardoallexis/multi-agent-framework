"""Shared helpers of the CMP-T02 tests: a real system (engine, store, router) with scripted providers.

No network and no paid call: the bundled ``social_post`` workflow runs on the catalog models ``local_default``
(provider ``ollama``, the preferred model of its ``create`` step) and ``cloud_fast`` (``gemini``, its fallback),
both replaced by scripted providers.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.bootstrap import build_system
from multiagent.catalog import validate_context_profile
from multiagent.config import Settings
from multiagent.contracts import ContentOutput, SocialPostRequest
from multiagent.schema_utils import estimate_tokens_conservative

WORKFLOW = "social_post"
PREFERRED = "local_default"   # provider ollama
FALLBACK = "cloud_fast"       # provider gemini


def good() -> ContentOutput:
    return ContentOutput(title="T", hook="A hook long enough for contract validation",
                         body="A body long enough to validate the structured contract correctly.",
                         cta="CTA", hashtags=["#ok"], source_urls_used=[])


class Provider(LLMAdapter):
    """A scripted provider. Each call takes the next action of ``script`` (``"ok"`` once it is empty): ``"ok"``,
    an exception instance to raise, or a function ``(spec, prompt)`` returning one of those (it may also have
    side effects)."""

    def __init__(self, provider: str, script: list[Any] | None = None):
        self.provider = provider
        self.script = list(script or [])
        self.prompts: list[str] = []
        self.models: list[str] = []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        self.prompts.append(prompt)
        self.models.append(spec.id)
        action = self.script.pop(0) if self.script else "ok"
        if callable(action):
            action = action(spec, prompt)
        if isinstance(action, Exception):
            raise action
        parsed = good()
        return LLMResponse(parsed, parsed.model_dump_json(), self.provider, spec.model, tokens_in=100, tokens_out=50)

    def health(self):
        return {"provider": self.provider, "available": True}


def window(tokens: int, model: str) -> dict[str, Any]:
    """Window evidence in the shape the catalog produces for a cloud model."""
    return {"tokens": tokens, "source": "unit test documentation", "checked_at": "2026-10-11",
            "provenance": "provider_docs", "model": model}


def set_window(system, model_id: str, tokens: int | None) -> None:
    """Give a catalog model a window of ``tokens`` (``None`` makes it unknown)."""
    spec = system.catalog.models[model_id]
    system.catalog.models[model_id] = dataclasses.replace(
        spec, context_window=window(tokens, spec.model) if tokens is not None else None)


def make_system(tmp_path: Path, *, ollama: Provider | None = None, gemini: Provider | None = None,
                max_llm_calls: int | None = None):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                        worker_enabled=False, gemini_api_key="", **({"max_llm_calls": max_llm_calls} if max_llm_calls else {}))
    ollama, gemini = ollama or Provider("ollama"), gemini or Provider("gemini")
    system = build_system(settings, dry_run=False, adapter_overrides={"ollama": ollama, "gemini": gemini})
    system.ollama, system.gemini = ollama, gemini
    return system


def assign_context_profile(system, *, profile_id: str = "standard", workflow: str = WORKFLOW, **fields) -> dict[str, Any]:
    """Declare a context profile (enabled unless ``enabled=False``) and assign it to ``workflow``."""
    profile = validate_context_profile(profile_id, {"enabled": True, **fields})
    system.catalog.context_profiles[profile_id] = profile
    system.catalog.context_assignments[workflow] = profile_id
    return profile


def start(system, **request: Any) -> str:
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="cmp-t02", objective="Create an educational post", topic="KNX", **request))
    system.worker.process_once()
    return run["id"]


def get(system, run_id: str) -> dict[str, Any]:
    return system.run_service.get(run_id)


def events(run: dict[str, Any], name: str) -> list[dict[str, Any]]:
    return [event["payload"] for event in run["events"] if event["event_type"] == name]


def names(run: dict[str, Any]) -> list[str]:
    return [event["event_type"] for event in run["events"]]


def telemetry_prompt_hashes(system, run_id: str) -> list[str]:
    with system.db.connect() as conn:
        return [row["prompt_sha256"] for row in conn.execute(
            "SELECT prompt_sha256 FROM telemetry WHERE run_id=? ORDER BY id", (run_id,))]


def prompt_tokens(tmp_path: Path) -> int:
    """The token estimate of the ``create`` prompt of the test request (it does not depend on the models)."""
    system = make_system(tmp_path)
    start(system)
    return estimate_tokens_conservative(system.ollama.prompts[0])
