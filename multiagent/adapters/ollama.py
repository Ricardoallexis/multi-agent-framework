from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from .base import EffortTranslation, LLMAdapter, LLMResponse, ignored_effort
from ..catalog import ModelSpec
from ..config import Settings
from ..errors import ContextBudgetError, ProviderRequestError, ProviderUnavailable, StructuredOutputError
from ..routing_policy import EFFORT_LEVELS
from ..schema_utils import estimate_tokens_conservative, flatten_json_schema
from .tls import effective_tls_trust, provider_ssl_context


class OllamaAdapter(LLMAdapter):
    provider = "ollama"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self._injected = client is not None
        self._client = client
        self._digest_cache: dict[str, str] = {}

    @property
    def client(self) -> httpx.Client:
        client = self._client
        if client is None:
            client = self._build_client()
            self._client = client
        return client

    def _build_client(self) -> httpx.Client:
        context = provider_ssl_context(effective_tls_trust(self.settings))
        extra = {} if context is None else {"verify": context}      # certifi: httpx keeps its own configuration
        return httpx.Client(base_url=self.settings.ollama_base_url, timeout=self.settings.ollama_timeout_seconds, **extra)

    def reset_client(self) -> None:
        """Replace the client this adapter built, so the next call reads the trust setting again (ARR3-T07)."""
        if self._injected:
            return
        old = self._client
        if old is None:
            return
        self._client = self._build_client()
        try:
            old.close()
        except Exception:  # noqa: BLE001 - closing a stale client is best effort
            pass

    def translate_effort(self, spec: ModelSpec, effort: str | None) -> EffortTranslation:
        """Ollama only has an on/off switch: ``none`` -> ``think: false``; any other level -> ``think: true``."""
        if effort is None:
            return EffortTranslation(None)
        if effort not in EFFORT_LEVELS:
            return ignored_effort(effort, "unknown_effort")
        return EffortTranslation(effort, True, {"think": effort != "none"})

    def generate_structured(self, *, spec: ModelSpec, prompt: str, output_schema: type[BaseModel], requires_web: bool = False,
                            effort: str | None = None) -> LLMResponse:
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
        translation = self.translate_effort(spec, effort)
        # Without an effort the model's catalog ``thinking`` flag decides, as before.
        think = translation.setting["think"] if translation.applied else bool(spec.thinking is True)
        body = {
            "model": spec.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": think,
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
                "thinking": think,
                **translation.metadata(),
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

    def installed_model_details(self) -> list[dict[str, Any]]:
        """Only capability evidence from /api/tags; no prompt or generation request.

        A network error, a bad JSON body or an unexpected shape gives an empty list, as ``health`` does not raise.
        """
        try:
            payload = self.client.get("/api/tags").json()
        except Exception:
            return []
        tags = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(tags, list):
            return []
        result = []
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            details = tag.get("details")
            if not isinstance(details, dict):
                details = {}
            result.append({"model": tag.get("name") or tag.get("model"),
                           "parameter_size": details.get("parameter_size"),
                           "quantization_level": details.get("quantization_level"),
                           "family": details.get("family")})
        return result
