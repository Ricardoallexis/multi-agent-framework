from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .base import LLMAdapter, LLMResponse
from ..catalog import ModelSpec
from ..config import Settings
from ..errors import ContextBudgetError, ProviderRequestError, ProviderUnavailable, StructuredOutputError
from ..schema_utils import estimate_tokens_conservative, flatten_json_schema


class OllamaAdapter(LLMAdapter):
    provider = "ollama"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(base_url=settings.ollama_base_url, timeout=settings.ollama_timeout_seconds)
        self._digest_cache: dict[str, str] = {}

    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False) -> LLMResponse:
        if requires_web:
            raise ProviderUnavailable("The local runtime does not provide web grounding for this step")
        num_ctx = int(spec.num_ctx or self.settings.ollama_num_ctx)
        estimate = estimate_tokens_conservative(prompt)
        soft_limit = int(num_ctx * self.settings.ollama_context_soft_limit)
        if estimate > soft_limit:
            raise ContextBudgetError(
                f"Estimated prompt size {estimate} tokens > soft limit {soft_limit} for num_ctx={num_ctx}"
            )
        schema = flatten_json_schema(output_schema.model_json_schema())
        body = {
            "model": spec.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": bool(spec.thinking is True),
            "format": schema,
            "options": {"num_ctx": num_ctx, "temperature": 0.1},
            "keep_alive": spec.keep_alive or self.settings.ollama_keep_alive,
        }
        started = time.perf_counter()
        try:
            response = self.client.post("/api/chat", json=body)
        except (httpx.RequestError, OSError) as exc:
            raise ProviderUnavailable(f"Ollama unavailable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            if response.status_code >= 500:
                raise ProviderUnavailable(f"Ollama returned HTTP {response.status_code}: {detail}")
            raise ProviderRequestError(f"Ollama rejected the request with HTTP {response.status_code}: {detail}")
        latency_ms = int((time.perf_counter() - started) * 1000)
        data = response.json()
        text = (data.get("message") or {}).get("content", "")
        try:
            parsed = output_schema.model_validate_json(text)
        except ValidationError as exc:
            raise StructuredOutputError(f"Ollama returned invalid JSON for {output_schema.__name__}: {exc}") from exc
        return LLMResponse(
            parsed=parsed,
            raw_text=text,
            provider=self.provider,
            model=data.get("model", spec.model),
            model_digest=self._model_digest(spec.model),
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            latency_ms=latency_ms,
            metadata={
                "num_ctx": num_ctx,
                "estimated_input_tokens": estimate,
                "context_soft_limit": soft_limit,
                "thinking": bool(spec.thinking is True),
                "load_duration": data.get("load_duration"),
                "prompt_eval_duration": data.get("prompt_eval_duration"),
                "eval_duration": data.get("eval_duration"),
            },
        )

    def _model_digest(self, model_name: str) -> str:
        if model_name in self._digest_cache:
            return self._digest_cache[model_name]
        try:
            models = self.client.get("/api/tags").json().get("models", [])
            for item in models:
                name = item.get("name") or item.get("model")
                if name == model_name:
                    digest = item.get("digest", "")
                    self._digest_cache[model_name] = digest
                    return digest
        except Exception:
            pass
        return ""

    def health(self) -> dict[str, Any]:
        try:
            version = self.client.get("/api/version").json().get("version", "")
            tags = self.client.get("/api/tags").json().get("models", [])
            ps = self.client.get("/api/ps").json().get("models", [])
            return {
                "provider": self.provider,
                "available": True,
                "version": version,
                "installed_models": [x.get("name") or x.get("model") for x in tags],
                "loaded_models": [x.get("name") or x.get("model") for x in ps],
            }
        except Exception as exc:
            return {"provider": self.provider, "available": False, "error": str(exc)}
