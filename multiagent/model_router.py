from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from .adapters.base import LLMAdapter, LLMResponse
from .catalog import Catalog, ModelSpec
from .contracts import TaskRequirements
from .errors import (
    AuthenticationError,
    ContextBudgetError,
    ModelUnavailable,
    ProviderRequestError,
    ProviderUnavailable,
    QuotaExceeded,
    RateLimitExceeded,
    StructuredOutputError,
)


@dataclass(frozen=True)
class ModelBinding:
    preferred_model: str
    fallback_model: str | None = None
    max_retries: int = 0


ResponseValidator = Callable[[BaseModel, LLMResponse], None]
_ROUTABLE_ERRORS = (
    ProviderUnavailable,
    ProviderRequestError,
    AuthenticationError,
    ModelUnavailable,
    QuotaExceeded,
    RateLimitExceeded,
    ContextBudgetError,
    StructuredOutputError,
)


class ModelRouter:
    """Route a step through one explicitly bound primary model and at most one fallback."""

    def __init__(self, catalog: Catalog, adapters: dict[str, LLMAdapter], dry_run: bool = False):
        self.catalog = catalog
        self.adapters = adapters
        self.dry_run = dry_run

    def _validate_compat(self, spec: ModelSpec, req: TaskRequirements) -> None:
        if req.requires_web and not spec.web:
            raise ProviderUnavailable(f"{spec.id} does not satisfy requires_web")
        if not spec.structured_output:
            raise ProviderUnavailable(f"{spec.id} does not support structured output")

    def execute(self, *, binding: ModelBinding, requirements: TaskRequirements, prompt: str,
                output_schema: type[BaseModel], before_call=None,
                validate_response: ResponseValidator | None = None, on_failed_attempt=None):
        if self.dry_run:
            if before_call:
                before_call()
            adapter = self.adapters["fake"]
            fake_spec = ModelSpec(id="fixture", provider="fake", model="fixture", tier=requirements.tier.value,
                                  structured_output=True, web=True)
            response = adapter.generate_structured(spec=fake_spec, prompt=prompt, output_schema=output_schema,
                                                   requires_web=requirements.requires_web)
            if validate_response:
                validate_response(response.parsed, response)
            response.metadata["catalog_model_id"] = "fixture"
            return response, [{"model": "fixture", "provider": "fake", "ok": True, "retry": 0}]

        candidates = [binding.preferred_model] + ([binding.fallback_model] if binding.fallback_model else [])
        attempts: list[dict] = []
        last_exc: Exception | None = None
        for candidate_index, model_id in enumerate(candidates):
            spec = self.catalog.model(model_id)
            try:
                self._validate_compat(spec, requirements)
            except _ROUTABLE_ERRORS as exc:
                last_exc = exc
                failure = {"model": model_id, "provider": spec.provider, "ok": False, "retry": 0,
                           "error": str(exc), "error_type": type(exc).__name__}
                attempts.append(failure)
                if on_failed_attempt:
                    on_failed_attempt(failure)
                continue
            adapter = self.adapters.get(spec.provider)
            if not adapter:
                last_exc = ProviderUnavailable(f"No adapter is configured for {spec.provider}")
                failure = {"model": model_id, "provider": spec.provider, "ok": False, "retry": 0,
                           "error": str(last_exc), "error_type": type(last_exc).__name__}
                attempts.append(failure)
                if on_failed_attempt:
                    on_failed_attempt(failure)
                continue

            retries = binding.max_retries if candidate_index == 0 else 0
            for retry in range(retries + 1):
                try:
                    if before_call:
                        before_call()
                    response = adapter.generate_structured(spec=spec, prompt=prompt, output_schema=output_schema,
                                                           requires_web=requirements.requires_web)
                    if validate_response:
                        try:
                            validate_response(response.parsed, response)
                        except (ValueError, TypeError) as exc:
                            raise StructuredOutputError(f"Semantically invalid output: {exc}") from exc
                    response.metadata["catalog_model_id"] = model_id
                    attempts.append({"model": model_id, "provider": spec.provider, "ok": True, "retry": retry})
                    return response, attempts
                except _ROUTABLE_ERRORS as exc:
                    last_exc = exc
                    failure = {"model": model_id, "provider": spec.provider, "ok": False, "retry": retry,
                               "error": str(exc), "error_type": type(exc).__name__}
                    attempts.append(failure)
                    if on_failed_attempt:
                        on_failed_attempt(failure)
                    # Only structured-output validation can benefit from an identical retry.
                    if not isinstance(exc, StructuredOutputError):
                        break
        assert last_exc is not None
        raise last_exc
