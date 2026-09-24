from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from .base import LLMAdapter, LLMResponse
from ..catalog import ModelSpec
from ..config import Settings
from ..errors import ProviderUnavailable, StructuredOutputError


class OpenAIAdapter(LLMAdapter):
    """Optional OpenAI cloud adapter.

    It is not part of the default routing path. The adapter remains available
    for explicit configuration and uses the Responses API.
    """

    provider = "openai"

    def __init__(self, settings: Settings, client: Any | None = None):
        self.settings = settings
        self._client = client

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        if not self.settings.openai_enabled or not self.settings.openai_api_key:
            raise ProviderUnavailable("OpenAI is disabled or OPENAI_API_KEY is missing")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderUnavailable("The openai package is not installed") from exc
        self._client = OpenAI(api_key=self.settings.openai_api_key)
        return self._client

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

    def generate_structured(
        self,
        *,
        spec: ModelSpec,
        prompt: str,
        output_schema: type[BaseModel],
        requires_web: bool = False,
    ) -> LLMResponse:
        if requires_web:
            raise ProviderUnavailable("OpenAI is not enabled as a grounded provider in the current routing configuration")

        client = self._client_or_raise()
        started = time.perf_counter()
        try:
            response = client.responses.parse(
                model=spec.model,
                input=prompt,
                text_format=output_schema,
            )
            parsed = self._extract_parsed(response)
            if parsed is None:
                raise StructuredOutputError("OpenAI did not return parsed output")
            usage = getattr(response, "usage", None)
        except StructuredOutputError:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"OpenAI failed: {exc}") from exc

        return LLMResponse(
            parsed=parsed,
            raw_text=getattr(response, "output_text", "") or parsed.model_dump_json(),
            provider=self.provider,
            model=getattr(response, "model", None) or spec.model,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0) if usage else 0,
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0) if usage else 0,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "available": bool(self.settings.openai_enabled and self.settings.openai_api_key),
            "configured": bool(self.settings.openai_api_key),
            "enabled": self.settings.openai_enabled,
        }
