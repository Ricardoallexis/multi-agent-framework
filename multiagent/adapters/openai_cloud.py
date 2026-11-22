from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from .base import EffortTranslation, LLMAdapter, LLMResponse, cached_tokens, ignored_effort
from ..catalog import ModelSpec
from ..config import Settings
from ..errors import ProviderUnavailable, StructuredOutputError
from ..routing_policy import EFFORT_LEVELS
from .tls import effective_tls_trust, is_tls_failure, provider_ssl_context

# Generic effort -> Responses API ``reasoning.effort``. ``max`` has no level above ``high``.
# ``none`` has no portable value (models differ between "minimal" and "none"), so it is
# ignored and recorded as not applied rather than guessed.
OPENAI_REASONING_EFFORT = {"low": "low", "medium": "medium", "high": "high", "max": "high"}


class OpenAIAdapter(LLMAdapter):
    """Optional OpenAI cloud adapter.

    It is not part of the default routing path. The adapter remains available
    for explicit configuration and uses the Responses API.
    """

    provider = "openai"

    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client
        self._injected = client is not None

    def reset_client(self) -> None:
        """Drop the client this adapter built, so the next call reads the trust setting again (ARR3-T07)."""
        if not self._injected:
            self._client = None

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        if not self.settings.openai_enabled or not self.settings.openai_api_key:
            raise ProviderUnavailable("OpenAI is disabled or OPENAI_API_KEY is missing")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailable("The openai package is not installed") from exc
        context = provider_ssl_context(effective_tls_trust(self.settings))
        if context is None:      # certifi: the SDK keeps its own HTTP client
            self._client = OpenAI(api_key=self.settings.openai_api_key)
        else:
            import httpx
            import openai
            # The SDK's own default client keeps its timeouts and limits; only the certificates change.
            factory = getattr(openai, "DefaultHttpxClient", httpx.Client)
            self._client = OpenAI(api_key=self.settings.openai_api_key, http_client=factory(verify=context))
        return self._client

    def probe(self) -> str:
        """One request that generates nothing (the model list): ``ok``, ``tls_certificate`` or ``error``. Never raises."""
        try:
            for _ in self._client_or_raise().models.list():
                break
        except Exception as exc:  # noqa: BLE001 - the outcome is the answer; provider text stays out
            return "tls_certificate" if is_tls_failure(exc) else "error"
        return "ok"

    @staticmethod
    def _extract_parsed(response: Any) -> BaseModel | None:
        # The Responses SDK exposes the parsed Pydantic object through the output_text block.
        for output in getattr(response, "output", []) or []:
            if getattr(output, "type", "") != "message":
                continue
            for item in getattr(output, "content", []) or []:
                if getattr(item, "type", "") == "output_text" and getattr(item, "parsed", None) is not None:
                    return item.parsed
        return None

    def translate_effort(self, spec: ModelSpec, effort: str | None) -> EffortTranslation:
        """See ``OPENAI_REASONING_EFFORT`` for the mapping."""
        if effort is None:
            return EffortTranslation(None)
        if effort not in EFFORT_LEVELS:
            return ignored_effort(effort, "unknown_effort")
        level = OPENAI_REASONING_EFFORT.get(effort)
        if level is None:
            return ignored_effort(effort, "no_portable_openai_level_for_none")
        return EffortTranslation(effort, True, {"reasoning_effort": level})

    def generate_structured(
        self,
        *,
        spec: ModelSpec,
        prompt: str,
        output_schema: type[BaseModel],
        requires_web: bool = False,
        effort: str | None = None,
    ) -> LLMResponse:
        if requires_web:
            raise ProviderUnavailable("OpenAI is not enabled as a grounded provider in the current routing configuration")

        client = self._client_or_raise()
        translation = self.translate_effort(spec, effort)
        extra: dict[str, Any] = {}
        if translation.applied:
            extra["reasoning"] = {"effort": translation.setting["reasoning_effort"]}
        started = time.perf_counter()
        try:
            response = client.responses.parse(
                model=spec.model,
                input=prompt,
                text_format=output_schema,
                **extra,
            )
            parsed = self._extract_parsed(response)
            if parsed is None:
                raise StructuredOutputError("OpenAI did not return parsed output")
            usage = getattr(response, "usage", None)
        except StructuredOutputError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"OpenAI failed: {exc}") from exc

        metadata = translation.metadata()
        cached = cached_tokens(usage, "input_tokens_details.cached_tokens", "prompt_tokens_details.cached_tokens")
        if cached is not None:
            metadata["cache_read_tokens"] = cached  # prompt tokens served from the provider's prompt cache
        return LLMResponse(
            parsed=parsed,
            raw_text=getattr(response, "output_text", "") or parsed.model_dump_json(),
            provider=self.provider,
            model=getattr(response, "model", None) or spec.model,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            metadata=metadata,
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "available": bool(self.settings.openai_enabled and self.settings.openai_api_key),
            "configured": bool(self.settings.openai_api_key),
            "enabled": self.settings.openai_enabled,
        }
