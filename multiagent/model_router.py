from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from typing import Any, Callable

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
AttemptCallback = Callable[[dict[str, Any]], None]
_logger = logging.getLogger(__name__)
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

    @staticmethod
    def _notify_observer(callback, payload: dict[str, Any], *, name: str) -> None:
        if callback is None:
            return
        try:
            callback(payload)
        except Exception:
            _logger.warning("ModelRouter %s callback failed; continuing routing", name, exc_info=True)

    @classmethod
    def _notify_attempt(cls, callback: AttemptCallback | None, payload: dict[str, Any]) -> None:
        cls._notify_observer(callback, payload, name="on_attempt")

    @classmethod
    def _notify_failed_attempt(cls, callback, payload: dict[str, Any]) -> None:
        cls._notify_observer(callback, payload, name="on_failed_attempt")

    @staticmethod
    def _observed_model(metadata: dict[str, Any]) -> str | None:
        """Accept a model as observed only when metadata identifies provider provenance."""
        value = metadata.get("provider_observed_model") or metadata.get("observed_model")
        provenance = metadata.get("observed_model_provenance")
        if value and provenance == "provider_response":
            return str(value)
        return None

    @staticmethod
    def _attempt_payload(*, model_id: str, spec: ModelSpec, retry: int, ordinal: int,
                         stage: str, provider_invoked: bool, status: str,
                         response: LLMResponse | None = None, error: Exception | None = None,
                         duration_ms: int | None = None) -> dict[str, Any]:
        metadata = dict(response.metadata) if response is not None else dict(getattr(error, "metadata", {}) or {})
        observed_model = ModelRouter._observed_model(metadata)
        if observed_model is None and error is not None:
            error_metadata = dict(getattr(error, "metadata", {}) or {})
            observed_model = ModelRouter._observed_model(error_metadata)
        model_digest = response.model_digest if response is not None else getattr(error, "model_digest", None)
        tokens_in = response.tokens_in if response is not None else getattr(error, "tokens_in", None)
        tokens_out = response.tokens_out if response is not None else getattr(error, "tokens_out", None)
        num_ctx = metadata.get("num_ctx", getattr(error, "num_ctx", None))
        return {
            "model": model_id,
            "provider": response.provider if response is not None else spec.provider,
            "ok": status == "success",
            "retry": retry,
            "error": str(error) if error is not None else None,
            "error_type": type(error).__name__ if error is not None else None,
            "model_digest": model_digest,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "num_ctx": num_ctx,
            "duration_ms": duration_ms if duration_ms is not None else (response.latency_ms if response is not None else None),
            "metadata": metadata,
            "call_ordinal": ordinal,
            "candidate": model_id,
            "stage": stage,
            "provider_invoked": provider_invoked,
            "status": status,
            "requested_model": model_id,
            "effective_model": spec.model if provider_invoked else None,
            "observed_model": observed_model,
        }

    def execute(self, *, binding: ModelBinding, requirements: TaskRequirements, prompt: str,
                output_schema: type[BaseModel], before_call=None,
                validate_response: ResponseValidator | None = None, on_failed_attempt=None,
                on_attempt: AttemptCallback | None = None):
        if self.dry_run:
            adapter = self.adapters["fake"]
            fake_spec = ModelSpec(id="fixture", provider="fake", model="fixture", tier=requirements.tier.value,
                                  structured_output=True, web=True)
            started = time.perf_counter()
            response: LLMResponse | None = None
            provider_invoked = False
            try:
                if before_call:
                    before_call()
                provider_invoked = True
                response = adapter.generate_structured(spec=fake_spec, prompt=prompt, output_schema=output_schema,
                                                       requires_web=requirements.requires_web)
                if validate_response:
                    try:
                        validate_response(response.parsed, response)
                    except (ValueError, TypeError) as exc:
                        raise StructuredOutputError(f"Semantically invalid output: {exc}") from exc
                response.metadata["catalog_model_id"] = "fixture"
                event = self._attempt_payload(model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                                              stage="provider", provider_invoked=True, status="success",
                                              response=response, duration_ms=int((time.perf_counter() - started) * 1000))
                self._notify_attempt(on_attempt, event)
                return response, [{"model": "fixture", "provider": "fake", "ok": True, "retry": 0}]
            except _ROUTABLE_ERRORS as exc:
                failure = self._attempt_payload(model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                                                stage="provider", provider_invoked=True, status="failed",
                                                response=response, error=exc,
                                                duration_ms=(response.latency_ms if response is not None else int((time.perf_counter() - started) * 1000)))
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                raise
            except Exception as exc:
                failure = self._attempt_payload(
                    model_id="fixture", spec=fake_spec, retry=0, ordinal=1,
                    stage="provider" if provider_invoked else "preflight",
                    provider_invoked=provider_invoked, status="failed", response=response, error=exc,
                    duration_ms=(response.latency_ms if response is not None else int((time.perf_counter() - started) * 1000)),
                )
                if provider_invoked:
                    self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                raise

        candidates = [binding.preferred_model] + ([binding.fallback_model] if binding.fallback_model else [])
        attempts: list[dict] = []
        last_exc: Exception | None = None
        ordinal = 0
        for candidate_index, model_id in enumerate(candidates):
            spec = self.catalog.model(model_id)
            try:
                self._validate_compat(spec, requirements)
            except _ROUTABLE_ERRORS as exc:
                ordinal += 1
                last_exc = exc
                failure = self._attempt_payload(model_id=model_id, spec=spec, retry=0, ordinal=ordinal,
                                                stage="preflight", provider_invoked=False,
                                                status="failed", error=exc)
                attempts.append(failure)
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                continue
            adapter = self.adapters.get(spec.provider)
            if not adapter:
                ordinal += 1
                last_exc = ProviderUnavailable(f"No adapter is configured for {spec.provider}")
                failure = self._attempt_payload(model_id=model_id, spec=spec, retry=0, ordinal=ordinal,
                                                stage="preflight", provider_invoked=False,
                                                status="failed", error=last_exc)
                attempts.append(failure)
                self._notify_failed_attempt(on_failed_attempt, failure)
                self._notify_attempt(on_attempt, failure)
                continue

            retries = binding.max_retries if candidate_index == 0 else 0
            for retry in range(retries + 1):
                ordinal += 1
                response: LLMResponse | None = None
                started = time.perf_counter()
                if before_call:
                    try:
                        before_call()
                    except Exception as exc:
                        failure = self._attempt_payload(model_id=model_id, spec=spec, retry=retry,
                                                        ordinal=ordinal, stage="preflight", provider_invoked=False,
                                                        status="failed", error=exc)
                        attempts.append(failure)
                        self._notify_attempt(on_attempt, failure)
                        raise
                try:
                    response = adapter.generate_structured(spec=spec, prompt=prompt, output_schema=output_schema,
                                                           requires_web=requirements.requires_web)
                    if validate_response:
                        try:
                            validate_response(response.parsed, response)
                        except (ValueError, TypeError) as exc:
                            raise StructuredOutputError(f"Semantically invalid output: {exc}") from exc
                except _ROUTABLE_ERRORS as exc:
                    last_exc = exc
                    duration_ms = (response.latency_ms if response is not None and response.latency_ms > 0
                                   else int((time.perf_counter() - started) * 1000))
                    failure = self._attempt_payload(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                                   stage="provider", provider_invoked=True, status="failed",
                                                   response=response, error=exc, duration_ms=duration_ms)
                    attempts.append(failure)
                    self._notify_failed_attempt(on_failed_attempt, failure)
                    self._notify_attempt(on_attempt, failure)
                    if not isinstance(exc, StructuredOutputError):
                        break
                except Exception as exc:
                    failure = self._attempt_payload(
                        model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                        stage="provider", provider_invoked=True, status="failed", response=response,
                        error=exc, duration_ms=(response.latency_ms if response is not None and response.latency_ms > 0
                                                else int((time.perf_counter() - started) * 1000)),
                    )
                    attempts.append(failure)
                    self._notify_failed_attempt(on_failed_attempt, failure)
                    self._notify_attempt(on_attempt, failure)
                    raise
                else:
                    response.metadata["catalog_model_id"] = model_id
                    event = self._attempt_payload(model_id=model_id, spec=spec, retry=retry, ordinal=ordinal,
                                                  stage="provider", provider_invoked=True, status="success",
                                                  response=response, duration_ms=int((time.perf_counter() - started) * 1000))
                    attempts.append(event)
                    self._notify_attempt(on_attempt, event)
                    return response, attempts
        assert last_exc is not None
        raise last_exc
