from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import LLMAdapter, LLMResponse
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

    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False) -> LLMResponse:
        client = self._client_or_raise()
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "input": prompt,
            "response_format": [{"type": "text", "mime_type": "application/json", "schema": output_schema.model_json_schema()}],
        }
        if requires_web:
            kwargs["tools"] = [{"type": "google_search"}]
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
        tokens_in = int(getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        return LLMResponse(
            parsed=parsed,
            raw_text=text,
            provider=self.provider,
            model=getattr(interaction, "model", None) or spec.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            sources=sources,
            metadata={"grounded": requires_web, "thinking": spec.thinking},
        )

    def health(self) -> dict[str, Any]:
        if not self.settings.gemini_api_key:
            return {"provider": self.provider, "available": False, "reason": "api_key_missing"}
        try:
            self._client_or_raise()
            return {"provider": self.provider, "available": True, "configured": True}
        except Exception as exc:
            return {"provider": self.provider, "available": False, "error": str(exc)}
