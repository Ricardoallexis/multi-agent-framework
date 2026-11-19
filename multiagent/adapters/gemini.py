from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import EffortTranslation, LLMAdapter, LLMResponse, cached_tokens, ignored_effort
from ..catalog import ModelSpec
from ..config import Settings
from ..errors import (
    AuthenticationError,
    ModelUnavailable,
    ProviderRequestError,
    ProviderUnavailable,
    QuotaExceeded,
    RateLimitExceeded,
    StructuredOutputError,
)
from ..routing_policy import EFFORT_LEVELS

# Generic effort -> ``generation_config.thinking_level`` of the Interactions API, whose levels
# are minimal, low, medium and high. Gemini 3 models cannot switch thinking off, so ``none``
# maps to the lowest level (``minimal``, the same value the bundled catalog uses for its
# fast models), and ``max`` has no level above ``high``. The catalog's per-model ``thinking``
# value is only recorded in the response metadata today; an explicit effort overrides it
# for that call and nothing is sent when no effort is requested.
GEMINI_THINKING_LEVEL = {"none": "minimal", "low": "low", "medium": "medium", "high": "high", "max": "high"}


def _first_count(usage: Any, *names: str) -> int:
    """First positive integer among the usage fields ``names``; 0 when none is reported (read as unknown)."""
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, dict):
            value = None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return 0


def _classify_gemini_error(exc: Exception) -> Exception:
    text = str(exc)
    low = text.lower()
    if "api_key_invalid" in low or "api key not valid" in low or "unauthenticated" in low:
        return AuthenticationError(f"Gemini authentication failed: {text}")
    if "no longer available" in low or "not_found" in low or "model" in low and "404" in low:
        return ModelUnavailable(f"Gemini model unavailable: {text}")
    if "quota" in low or "resource_exhausted" in low:
        return QuotaExceeded(f"Gemini quota exhausted: {text}")
    if "rate" in low and "limit" in low or "429" in low:
        return RateLimitExceeded(f"Rate limit Gemini: {text}")
    if "400" in low or "invalid_argument" in low:
        return ProviderRequestError(f"Gemini rejected the request: {text}")
    if "timeout" in low or "connection" in low or "503" in low or "502" in low:
        return ProviderUnavailable(f"Gemini unavailable: {text}")
    return ProviderUnavailable(f"Gemini failed: {text}")


class GeminiAdapter(LLMAdapter):
    provider = "gemini"

    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        if not self.settings.gemini_api_key:
            raise AuthenticationError("GEMINI_API_KEY is not configured")
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderUnavailable("The google-genai package is not installed") from exc
        self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    def translate_effort(self, spec: ModelSpec, effort: str | None) -> EffortTranslation:
        """See ``GEMINI_THINKING_LEVEL`` for the mapping."""
        if effort is None:
            return EffortTranslation(None)
        if effort not in EFFORT_LEVELS:
            return ignored_effort(effort, "unknown_effort")
        return EffortTranslation(effort, True, {"thinking_level": GEMINI_THINKING_LEVEL[effort]})

    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False,
                            effort: str | None = None) -> LLMResponse:
        client = self._client_or_raise()
        translation = self.translate_effort(spec, effort)
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "input": prompt,
            "response_format": [{"type": "text", "mime_type": "application/json", "schema": output_schema.model_json_schema()}],
        }
        if requires_web:
            kwargs["tools"] = [{"type": "google_search"}]
        if translation.applied:
            kwargs["generation_config"] = dict(translation.setting)
        started = time.perf_counter()
        try:
            interaction = client.interactions.create(**kwargs)
        except Exception as exc:
            raise _classify_gemini_error(exc) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = getattr(interaction, "output_text", "") or ""
        try:
            parsed = output_schema.model_validate_json(text)
        except ValidationError as exc:
            raise StructuredOutputError(f"Gemini returned invalid output: {exc}") from exc
        sources: list[str] = []
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", "") != "model_output":
                continue
            for block in getattr(step, "content", []) or []:
                for ann in getattr(block, "annotations", []) or []:
                    if getattr(ann, "type", "") == "url_citation" and getattr(ann, "url", None):
                        if ann.url not in sources:
                            sources.append(ann.url)
        usage = getattr(interaction, "usage", None)
        # The Interactions API names its usage ``total_input_tokens``, ``total_output_tokens``, ``total_thought_tokens``
        # and ``total_cached_tokens``; the older names stay as a fallback. 0 means "not reported": the engine stores it as None.
        tokens_in = _first_count(usage, "total_input_tokens", "input_tokens", "prompt_tokens") if usage else 0
        tokens_out = _first_count(usage, "total_output_tokens", "output_tokens", "completion_tokens") if usage else 0
        thought = _first_count(usage, "total_thought_tokens") if usage else 0
        tokens_out += thought  # thinking tokens are billed as output
        metadata = {"grounded": requires_web, "thinking": spec.thinking, **translation.metadata()}
        if thought:
            metadata["thought_tokens"] = thought
        cached = cached_tokens(usage, "total_cached_tokens", "cached_content_token_count", "cached_tokens")
        if cached is not None:
            metadata["cache_read_tokens"] = cached  # prompt tokens Gemini reports as served from its cache
        return LLMResponse(
            parsed=parsed,
            raw_text=text,
            provider=self.provider,
            model=getattr(interaction, "model", None) or spec.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            sources=sources,
            metadata=metadata,
        )

    def health(self) -> dict[str, Any]:
        if not self.settings.gemini_api_key:
            return {"provider": self.provider, "available": False, "reason": "api_key_missing"}
        try:
            self._client_or_raise()
            return {"provider": self.provider, "available": True, "configured": True}
        except Exception as exc:
            return {"provider": self.provider, "available": False, "error": str(exc)}
