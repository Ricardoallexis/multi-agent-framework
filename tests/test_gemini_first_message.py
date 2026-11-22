# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The first Gemini message checks the key without a separate probe."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from multiagent import model_checks
from multiagent.adapters.gemini import GeminiAdapter
from multiagent.catalog import ModelSpec
from multiagent.errors import AuthenticationError, ProviderUnavailable, QuotaExceeded
from test_connections import fixture as connections


class Answer(BaseModel):
    value: str


class FakeInteractions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(output_text='{"value":"ok"}', steps=[], model="gemini-test", usage=None)


class ProviderFailure(Exception):
    def __init__(self, code, *, details=None, headers=None):
        super().__init__(f"{code}: key=SYNTHETIC_SECRET")
        self.code = code
        self.details = details
        self.headers = headers


@pytest.fixture(autouse=True)
def unverified(monkeypatch):
    monkeypatch.setattr(model_checks, "_gemini", {"verification": "unverified", "verified_at": None, "resets_at": None})


def call(outcome):
    fake = FakeInteractions(outcome)
    adapter = GeminiAdapter(SimpleNamespace(gemini_api_key="SYNTHETIC_SECRET"),
                            client=SimpleNamespace(interactions=fake))
    spec = ModelSpec(id="g", provider="gemini", model="gemini-test", tier="cloud", structured_output=True, web=False)
    return adapter, spec, fake


def test_unverified_is_available_without_a_probe_and_success_verifies():
    service = connections()
    first = service.check("gemini")
    assert first["verification"] == "unverified" and first["available"] is True
    assert first["reason"] is None and first["reachable"] is None
    adapter, spec, fake = call(None)
    assert fake.calls == 0
    adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    row = service.list()["connections"][0]
    assert row["verification"] == "verified" and row["verified_at"]
    assert row["available"] is True and fake.calls == 1
    assert "SYNTHETIC_SECRET" not in str(row)


@pytest.mark.parametrize("failure", [ProviderFailure(401), ProviderFailure(403),
                                      ProviderFailure("PERMISSION_DENIED"), ProviderFailure("API_KEY_INVALID")])
def test_invalid_key_is_ineligible_until_success(failure):
    adapter, spec, _ = call(failure)
    with pytest.raises(AuthenticationError) as raised:
        adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    row = connections().check("gemini")
    assert row["verification"] == row["reason"] == "key_invalid"
    assert row["available"] is False
    assert "SYNTHETIC_SECRET" not in str(row) + str(raised.value)
    adapter, spec, _ = call(None)
    adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    assert connections().check("gemini")["verification"] == "verified"


def test_retry_info_and_retry_after_supply_reset_time():
    for failure in (ProviderFailure(429, details=[{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                    "retryDelay": "37s"}]),
                    ProviderFailure(429, headers={"Retry-After": "37"})):
        adapter, spec, _ = call(failure)
        before = datetime.now(timezone.utc)
        with pytest.raises(QuotaExceeded) as raised:
            adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
        after = datetime.now(timezone.utc)
        assert before + timedelta(seconds=37) <= raised.value.retry_at <= after + timedelta(seconds=37)
        row = connections().check("gemini")
        assert row["verification"] == row["reason"] == "limit_reached"
        assert row["resets_at"] == raised.value.retry_at.isoformat()
        assert row["available"] is False
        assert "SYNTHETIC_SECRET" not in str(row) + str(raised.value)


def test_limit_without_time_and_connection_failure():
    adapter, spec, _ = call(ProviderFailure("RESOURCE_EXHAUSTED"))
    with pytest.raises(QuotaExceeded) as raised:
        adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    row = connections().check("gemini")
    assert raised.value.retry_at is None and row["resets_at"] is None
    assert row["verification"] == "limit_reached" and "unknown" in row["limit_note"]
    adapter, spec, _ = call(ConnectionError("SYNTHETIC_SECRET connection failed"))
    with pytest.raises(ProviderUnavailable) as raised:
        adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    row = connections().check("gemini")
    assert row["verification"] == row["reason"] == "no_access"
    assert row["available"] is False and "SYNTHETIC_SECRET" not in str(row) + str(raised.value)


def test_tls_failure_is_tls_certificate_and_does_not_log_provider_text(caplog):
    adapter, spec, _ = call(ProviderFailure(503))        # a network error stays «no access»
    with pytest.raises(ProviderUnavailable):
        adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    assert connections().check("gemini")["verification"] == "no_access"
    adapter, spec, _ = call(OSError("TLS CERTIFICATE_VERIFY_FAILED SYNTHETIC_SECRET"))   # ARR3-T07: a certificate failure is its own state
    with pytest.raises(ProviderUnavailable):
        adapter.generate_structured(spec=spec, prompt="hello", output_schema=Answer)
    assert connections().check("gemini")["verification"] == "tls_certificate"
    assert "SYNTHETIC_SECRET" not in caplog.text


def test_missing_key_keeps_existing_reason():
    row = connections(key=False).check("gemini")
    assert row["reason"] == "key_missing" and row["missing_step"] == "key"
