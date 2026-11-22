from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
    StructuredOutputError,
)
from ..model_checks import record_gemini_verification
from ..routing_policy import EFFORT_LEVELS
from .tls import effective_tls_trust, is_tls_failure, provider_ssl_context

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


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _retry_delay(value: Any) -> timedelta | None:
    if isinstance(value, timedelta):
        return value if value.total_seconds() >= 0 else None
    if isinstance(value, str):
        match = re.fullmatch(r"(\d+)(?:\.(\d{1,9}))?s", value.strip())
        if match:
            return timedelta(seconds=int(match[1]) + float("0." + (match[2] or "0")))
    seconds = _field(value, "seconds")
    nanos = _field(value, "nanos") or 0
    if isinstance(seconds, (int, float, str)) and str(seconds).isdigit() and isinstance(nanos, int):
        return timedelta(seconds=int(seconds), microseconds=nanos / 1000)
    return None


def _retry_at(exc: Exception, now: datetime) -> datetime | None:
    details = _field(exc, "details") or _field(_field(exc, "response"), "details") or []
    if isinstance(details, dict):
        details = details.get("details", [details])
    if not isinstance(details, (list, tuple)):
        details = [details]
    for detail in details:
        delay = _retry_delay(_field(detail, "retryDelay") or _field(detail, "retry_delay"))
        if delay is not None:
            return now + delay
    headers = _field(exc, "headers") or _field(_field(exc, "response"), "headers") or {}
    try:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        if retry_after is not None:
            if str(retry_after).strip().isdigit():
                return now + timedelta(seconds=int(str(retry_after).strip()))
            parsed = parsedate_to_datetime(str(retry_after))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _classify_gemini_error(exc: Exception) -> Exception:
    # Provider text can contain a credential. Keep it only for classification, never in our exception.
    if is_tls_failure(exc):    # the SDK wraps the ssl error: look at its causes, and do not count it as «no access»
        record_gemini_verification("tls_certificate", at=datetime.now(timezone.utc))
        return ProviderUnavailable("Gemini certificate could not be verified (TLS)")
    low = str(exc).lower()
    code = str(_field(exc, "code") or _field(exc, "status_code") or
               _field(_field(exc, "response"), "status_code") or "").lower()
    now = datetime.now(timezone.utc)
    if code in {"401", "403", "permission_denied", "unauthenticated"} or any(
            term in low for term in ("api_key_invalid", "api key not valid", "permission_denied", "unauthenticated")) or re.search(r"\b(401|403)\b", low):
        record_gemini_verification("key_invalid", at=now)
        return AuthenticationError("Gemini key is invalid")
    if code in {"429", "resource_exhausted"} or any(term in low for term in ("429", "resource_exhausted", "quota")):
        retry_at = _retry_at(exc, now)
        record_gemini_verification("limit_reached", at=now, retry_at=retry_at)
        return QuotaExceeded("Gemini limit reached", retry_at=retry_at)
    if code == "404" or "no longer available" in low or "not_found" in low or "model" in low and "404" in low:
        return ModelUnavailable("Gemini model unavailable")
    if code == "400" or "invalid_argument" in low:
        return ProviderRequestError("Gemini rejected the request")
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) or any(
            term in low for term in ("timeout", "connection", "tls", "certificate", "503", "502")):
        record_gemini_verification("no_access", at=now)
        return ProviderUnavailable("Gemini has no access")
    return ProviderUnavailable("Gemini request failed")


class GeminiAdapter(LLMAdapter):
    provider = "gemini"

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
        if not self.settings.gemini_api_key:
            raise AuthenticationError("GEMINI_API_KEY is not configured")
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderUnavailable("The google-genai package is not installed") from exc
        context = provider_ssl_context(effective_tls_trust(self.settings))
        if context is None:      # certifi: the client keeps its own configuration
            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        else:                    # google-genai 2.13.0: ``HttpOptions.client_args`` goes to the ``httpx.Client``
            self._client = genai.Client(api_key=self.settings.gemini_api_key,
                                        http_options={"client_args": {"verify": context}})
        return self._client

    def probe(self) -> str:
        """One request that generates nothing (the model list): ``ok``, ``tls_certificate`` or ``error``. Never raises."""
        try:
            for _ in self._client_or_raise().models.list():
                break
        except Exception as exc:  # noqa: BLE001 - the outcome is the answer; provider text stays out
            _classify_gemini_error(exc)       # records key_invalid, limit_reached, no_access or tls_certificate
            return "tls_certificate" if is_tls_failure(exc) else "error"
        record_gemini_verification("verified", at=datetime.now(timezone.utc))
        return "ok"

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
            raise _classify_gemini_error(exc) from None
        record_gemini_verification("verified", at=datetime.now(timezone.utc))
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
