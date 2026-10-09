from __future__ import annotations

from pydantic import BaseModel
import pytest

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.catalog import ModelSpec
from multiagent.contracts import ModelTier, TaskRequirements
from multiagent.errors import ProviderUnavailable, StructuredOutputError
from multiagent.model_router import ModelBinding, ModelRouter


class Payload(BaseModel):
    value: str


class StaticCatalog:
    def __init__(self):
        self.models = {
            "primary": ModelSpec("primary", "one", "provider-one", "local", True, False),
            "fallback": ModelSpec("fallback", "two", "provider-two", "cloud", True, True),
            "no_web": ModelSpec("no_web", "one", "provider-one", "local", True, False),
        }

    def model(self, model_id):
        return self.models[model_id]


class SequenceAdapter(LLMAdapter):
    def __init__(self, provider, responses):
        self.provider = provider
        self.responses = list(responses)
        self.calls = 0

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def health(self):
        return {"provider": self.provider, "available": True}


def response(value, *, model, tokens_in=13, tokens_out=5, metadata=None):
    parsed = Payload(value=value)
    return LLMResponse(parsed, parsed.model_dump_json(), model.split("-")[0], model,
                       tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=12,
                       metadata=dict(metadata or {}))


def requirements(requires_web=False):
    return TaskRequirements(requires_web=requires_web, output_schema="Payload", tier=ModelTier.LOCAL)


def reject_marked(payload, _response):
    if payload.value == "reject":
        raise ValueError("semantic rejection")


def test_semantic_rejection_preserves_response_usage_and_fallback_event():
    primary = SequenceAdapter("one", [response("reject", model="provider-one-v2", metadata={"num_ctx": 4096, "vendor": "x"})])
    fallback = SequenceAdapter("two", [response("accepted", model="provider-two-v3")])
    events, failed = [], []
    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})

    result, attempts = router.execute(
        binding=ModelBinding("primary", "fallback"), requirements=requirements(), prompt="x",
        output_schema=Payload, validate_response=reject_marked,
        on_attempt=events.append, on_failed_attempt=failed.append,
    )

    assert result.parsed.value == "accepted"
    assert [event["call_ordinal"] for event in events] == [1, 2]
    assert [event["requested_model"] for event in events] == ["primary", "fallback"]
    rejected = events[0]
    assert rejected["status"] == "failed" and rejected["provider_invoked"] is True
    assert rejected["observed_model"] is None
    assert rejected["effective_model"] == "provider-one"
    assert rejected["tokens_in"] == 13 and rejected["tokens_out"] == 5
    assert rejected["num_ctx"] == 4096 and rejected["metadata"]["vendor"] == "x"
    assert rejected["error_type"] == "StructuredOutputError"
    assert len(failed) == 1 and failed[0]["tokens_in"] == 13
    assert len(attempts) == 2


def test_retry_and_final_failure_report_each_invoked_attempt():
    adapter = SequenceAdapter("one", [response("reject", model="provider-one-v2"), response("reject", model="provider-one-v2")])
    events, failed = [], []
    router = ModelRouter(StaticCatalog(), {"one": adapter})

    with pytest.raises(StructuredOutputError):
        router.execute(binding=ModelBinding("primary", max_retries=1), requirements=requirements(),
                       prompt="x", output_schema=Payload, validate_response=reject_marked,
                       on_attempt=events.append, on_failed_attempt=failed.append)

    assert [event["call_ordinal"] for event in events] == [1, 2]
    assert [event["retry"] for event in events] == [0, 1]
    assert all(event["provider_invoked"] for event in events)
    assert len(failed) == 2


def test_preflight_rejection_emits_event_without_usage_callback():
    primary = SequenceAdapter("one", [])
    fallback = SequenceAdapter("two", [response("accepted", model="provider-two-v3")])
    events, failed = [], []
    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})

    router.execute(binding=ModelBinding("no_web", "fallback"), requirements=requirements(requires_web=True),
                   prompt="x", output_schema=Payload, on_attempt=events.append,
                   on_failed_attempt=failed.append)

    assert events[0]["stage"] == "preflight"
    assert events[0]["provider_invoked"] is False
    assert events[0]["error_type"] == "ProviderUnavailable"
    assert primary.calls == 0 and fallback.calls == 1
    assert len(failed) == 1
    assert failed[0]["stage"] == "preflight" and failed[0]["provider_invoked"] is False


def test_observer_callback_errors_do_not_change_fallback_or_success(caplog):
    primary = SequenceAdapter("one", [ProviderUnavailable("offline")])
    fallback = SequenceAdapter("two", [response("accepted", model="provider-two-v3")])
    events, failures = [], []

    def broken_attempt(event):
        events.append(event)
        raise RuntimeError("event sink unavailable")

    def broken_failure(event):
        failures.append(event)
        raise RuntimeError("legacy observer unavailable")

    router = ModelRouter(StaticCatalog(), {"one": primary, "two": fallback})
    result, _ = router.execute(
        binding=ModelBinding("primary", "fallback"), requirements=requirements(), prompt="x",
        output_schema=Payload, on_attempt=broken_attempt, on_failed_attempt=broken_failure,
    )

    assert result.parsed.value == "accepted"
    assert primary.calls == fallback.calls == 1
    assert len(events) == 2 and len(failures) == 1
    assert "on_attempt callback failed" in caplog.text
    assert "on_failed_attempt callback failed" in caplog.text


def test_success_observer_error_does_not_discard_provider_response():
    adapter = SequenceAdapter("one", [response("accepted", model="provider-one-v2")])
    router = ModelRouter(StaticCatalog(), {"one": adapter})

    def broken_attempt(_event):
        raise RuntimeError("sink down")

    result, attempts = router.execute(
        binding=ModelBinding("primary"), requirements=requirements(), prompt="x",
        output_schema=Payload, on_attempt=broken_attempt,
    )

    assert result.parsed.value == "accepted"
    assert attempts[0]["status"] == "success"
