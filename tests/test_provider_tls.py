# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARR3-T07: the certificates the provider clients trust, and the TLS diagnosis of every provider.

Only fake clients and SDKs: no test creates a real provider client or leaves 127.0.0.1. If creating an SSL context fails
because of ``SSLKEYLOGFILE`` or another variable of the environment, that is the environment, not the code."""
from __future__ import annotations

import json
import ssl
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from multiagent import model_checks
from multiagent.adapters import ollama as ollama_module
from multiagent.adapters.gemini import GeminiAdapter, _classify_gemini_error
from multiagent.adapters.ollama import OllamaAdapter
from multiagent.adapters.openai_cloud import OpenAIAdapter
from multiagent.adapters.tls import effective_tls_trust, is_tls_failure, parse_tls_trust, provider_ssl_context
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.gui.connections_api import add_connection_routes
from multiagent.gui.security import require_json, require_token
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices
from multiagent.services import connections as connections_module
from multiagent.services.connections import ConnectionServices, IDS
from multiagent.services.errors import ServiceError

SECRET = "SYNTHETIC_SECRET"
REAL_HTTPX_CLIENT = httpx.Client


def _settings(tmp_path, **extra) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                    gui_token_file=tmp_path / "gui-token", **extra)


def _write_gui_settings(tmp_path, **values) -> None:
    (tmp_path / "gui-settings.json").write_text(json.dumps({"schema": "maf.gui-settings/1", **values}), encoding="utf-8")


class APIConnectionError(Exception):
    """Stands in for the SDK error: its text says nothing, the ssl error is its cause."""


def _wrapped_certificate_error() -> Exception:
    try:
        try:
            raise ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        except ssl.SSLCertVerificationError as cause:
            raise APIConnectionError("Connection error.") from cause
    except APIConnectionError as error:
        return error


def test_parse_tls_trust(tmp_path):
    assert parse_tls_trust("certifi") == "certifi"
    assert parse_tls_trust("system") == "system"
    assert parse_tls_trust("file:C:/x.pem") == "file:C:/x.pem"
    for bad in ("otra", "", "file:"):
        with pytest.raises(ValueError):
            parse_tls_trust(bad)
    with pytest.raises(ValidationError):                                                # Settings validates it too
        _settings(tmp_path, provider_tls_trust="otra")


def test_precedence(tmp_path):
    assert effective_tls_trust(_settings(tmp_path)) == "certifi"                        # neither: certifi
    assert effective_tls_trust(_settings(tmp_path, provider_tls_trust="system")) == "system"    # no file: the environment
    _write_gui_settings(tmp_path, provider_tls_trust="system")
    assert effective_tls_trust(_settings(tmp_path, provider_tls_trust="certifi")) == "system"   # the file wins
    _write_gui_settings(tmp_path, provider_tls_trust="certifi")
    assert effective_tls_trust(_settings(tmp_path, provider_tls_trust="system")) == "certifi"
    _write_gui_settings(tmp_path, provider_tls_trust="file:C:/x.pem")                   # not a value of the GUI: ignored
    assert effective_tls_trust(_settings(tmp_path, provider_tls_trust="system")) == "system"


class FakeOpenAI:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)


@pytest.fixture
def fake_openai(monkeypatch):
    import openai
    FakeOpenAI.calls = []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return FakeOpenAI


def test_certifi_keeps_clients_unchanged(tmp_path, fake_openai, monkeypatch):
    assert provider_ssl_context("certifi") is None
    adapter = OpenAIAdapter(_settings(tmp_path, openai_api_key=SECRET, openai_enabled=True))
    adapter._client_or_raise()
    assert fake_openai.calls == [{"api_key": SECRET}]                                   # no http_client
    seen = []
    monkeypatch.setattr(ollama_module.httpx, "Client", lambda **kw: seen.append(kw) or REAL_HTTPX_CLIENT(**kw))
    OllamaAdapter(_settings(tmp_path)).client                                           # built on first use (PRR-T02)
    assert "verify" not in seen[0]


def test_system_context_reaches_clients(tmp_path, fake_openai, monkeypatch):
    import openai
    recorded = []

    class Recorder(REAL_HTTPX_CLIENT):
        def __init__(self, **kwargs):
            recorded.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(openai, "DefaultHttpxClient", Recorder)
    settings = _settings(tmp_path, openai_api_key=SECRET, openai_enabled=True, provider_tls_trust="system")
    OpenAIAdapter(settings)._client_or_raise()
    client = fake_openai.calls[0]["http_client"]
    assert isinstance(client, httpx.Client) and isinstance(recorded[0]["verify"], ssl.SSLContext)
    monkeypatch.setattr(ollama_module.httpx, "Client", Recorder)
    ollama = OllamaAdapter(settings)
    assert isinstance(ollama.client, httpx.Client) and isinstance(recorded[1]["verify"], ssl.SSLContext)
    assert recorded[1]["base_url"] == settings.ollama_base_url


def test_gemini_context_or_documented_fallback(tmp_path, monkeypatch, fresh_gemini_state):
    from google import genai
    from google.genai import types
    created = []
    monkeypatch.setattr(genai, "Client", lambda **kw: created.append(kw) or object())
    settings = _settings(tmp_path, gemini_api_key=SECRET, provider_tls_trust="system")
    supported = "client_args" in types.HttpOptions.model_fields      # google-genai 2.13.0: client_args go to httpx.Client
    GeminiAdapter(settings)._client_or_raise()
    if supported:
        assert isinstance(created[0]["http_options"]["client_args"]["verify"], ssl.SSLContext)
    else:                                                          # the documented fallback: Gemini keeps certifi
        assert created == [{"api_key": SECRET}]
        assert _connection_rows(tmp_path, gemini="tls")["gemini"]["tls_fix"] is None


def test_reset_client_drops_only_the_clients_the_adapter_built(tmp_path, fake_openai):
    settings = _settings(tmp_path, openai_api_key=SECRET, openai_enabled=True)
    adapter = OpenAIAdapter(settings)
    first = adapter._client_or_raise()
    adapter.reset_client()
    assert adapter._client_or_raise() is not first
    injected = object()
    kept = OpenAIAdapter(settings, client=injected)
    kept.reset_client()
    assert kept._client_or_raise() is injected
    ollama = OllamaAdapter(settings)
    old = ollama.client
    ollama.reset_client()
    assert ollama.client is not old


def test_tls_failure_found_in_exception_chain():
    assert is_tls_failure(_wrapped_certificate_error()) is True
    assert is_tls_failure(TimeoutError("timed out")) is False
    assert is_tls_failure("[SSL: CERTIFICATE_VERIFY_FAILED] x") is True and is_tls_failure(None) is False
    loop = APIConnectionError("a")
    loop.__cause__ = loop
    assert is_tls_failure(loop) is False                                                # a cycle ends


@pytest.fixture
def fresh_gemini_state(monkeypatch):
    monkeypatch.setattr(model_checks, "_gemini", {"verification": "unverified", "verified_at": None, "resets_at": None})


def test_gemini_tls_is_not_no_access(fresh_gemini_state):
    result = _classify_gemini_error(_wrapped_certificate_error())
    assert model_checks.gemini_verification()["verification"] == "tls_certificate"
    assert SECRET not in str(result)
    _classify_gemini_error(TimeoutError("timed out"))                                   # the other network errors stay as before
    assert model_checks.gemini_verification()["verification"] == "no_access"
    with pytest.raises(ValueError):
        model_checks.record_gemini_verification("other", at=datetime.now(timezone.utc))


class FakeProvider:
    """A provider adapter whose probe is programmed; it counts how often it is asked."""

    def __init__(self, outcome="ok"):
        self.outcome, self.probes, self.resets = outcome, 0, 0

    def probe(self):
        self.probes += 1
        return self.outcome

    def reset_client(self):
        self.resets += 1

    def health(self):
        return {"available": True, "version": "0"}


def _stub_services(tmp_path, adapters, **settings):
    values = dict(gemini_api_key=SECRET, openai_api_key=SECRET, openai_enabled=True, local_dir=tmp_path,
                  provider_tls_trust="certifi", antigravity_notice_accepted="agy-third-party-notice/1")
    values.update(settings)
    executor = SimpleNamespace(status=lambda: {"executors": [
        {"executor_id": i, "enabled": True, "pause_reason": None} for i in IDS if i.endswith("_cli")]})
    usage = SimpleNamespace(plans=lambda: {"apps": []})
    return SimpleNamespace(settings=SimpleNamespace(**values), executors=executor, usage=usage,
                           system=SimpleNamespace(adapters=adapters))


def _detector(**kwargs):
    return {"contexts": {"packaged_process": {"apps": []}}}


def _connection_rows(tmp_path, **kind) -> dict:
    if kind.get("gemini") == "tls":
        model_checks.record_gemini_verification("tls_certificate", at=datetime.now(timezone.utc))
    service = ConnectionServices(_stub_services(tmp_path, {"ollama": FakeProvider()}), detector=_detector)
    return {row["id"]: row for row in service.list()["connections"]}


def _client(tmp_path, connections):
    token = tmp_path / "gui-token"
    token.write_text("synthetic-token-with-enough-length", encoding="utf-8")
    app = FastAPI()
    app.state.gui_token_file = token
    router = APIRouter(prefix="/gui/api", dependencies=[Depends(require_token), Depends(require_json)])
    add_connection_routes(router, SimpleNamespace(connections=connections))
    app.include_router(router)
    headers = {"Authorization": "Bearer synthetic-token-with-enough-length", "Content-Type": "application/json"}
    return TestClient(app), headers


def test_probe_only_on_request(tmp_path, fresh_gemini_state):
    openai, gemini = FakeProvider("ok"), FakeProvider("ok")
    services = _stub_services(tmp_path, {"ollama": FakeProvider(), "openai": openai, "gemini": gemini})
    connections = ConnectionServices(services, detector=_detector)
    client, headers = _client(tmp_path, connections)
    assert len(client.get("/gui/api/connections", headers=headers).json()["connections"]) == 8
    assert client.post("/gui/api/connections/openai/check", headers=headers, json={}).status_code == 200
    assert (openai.probes, gemini.probes) == (0, 0)
    row = client.post("/gui/api/connections/openai/probe", headers=headers, json={}).json()
    assert openai.probes == 1 and gemini.probes == 0 and row["probe"] == "ok" and row["available"] is True
    with pytest.raises(ServiceError) as raised:                                         # an executor has no probe
        connections.probe("claude_cli")
    assert raised.value.status == 404


def test_the_card_offers_the_certificate_button_only_for_a_certificate_failure(tmp_path, monkeypatch, fresh_gemini_state):
    openai = FakeProvider("tls_certificate")
    connections = ConnectionServices(_stub_services(tmp_path, {"ollama": FakeProvider(), "openai": openai}), detector=_detector)
    monkeypatch.setattr(connections_module, "_is_windows", lambda: True)
    row = connections.probe("openai")
    assert row["reason"] == "tls_certificate" and row["available"] is False and row["tls_trust"] == "certifi"
    assert row["tls_fix"] == {"trust": "system", "label": "Usar los certificados de Windows"}
    assert connections.check("openai")["tls_fix"] is not None                         # «Volver a comprobar» keeps it, no network
    assert openai.probes == 1
    monkeypatch.setattr(connections_module, "_is_windows", lambda: False)
    assert connections.check("openai")["tls_fix"]["label"] == "Usar los certificados del sistema"
    assert connections.check("ollama")["tls_fix"] is None and connections.check("gemini")["tls_fix"] is None
    openai.outcome = "ok"
    assert connections.probe("openai")["tls_fix"] is None


def test_the_gemini_row_carries_the_certificate_state(tmp_path, fresh_gemini_state):
    row = _connection_rows(tmp_path, gemini="tls")["gemini"]
    assert row["reason"] == "tls_certificate" and row["tls_fix"]["trust"] == "system" and row["available"] is False


def test_tls_trust_route(tmp_path, fresh_gemini_state):
    services = ApplicationServices.create(_settings(tmp_path, openai_api_key=SECRET, openai_enabled=True), dry_run=True)
    fake = FakeProvider("tls_certificate")
    services.system.adapters["openai"] = fake
    token = ensure_token(services.settings.gui_token_file)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        row = client.post("/gui/api/connections/openai/tls-trust", headers=headers, json={"trust": "system"})
        assert row.status_code == 200, row.text
        written = json.loads((tmp_path / "gui-settings.json").read_text(encoding="utf-8"))
        assert written["provider_tls_trust"] == "system"
        assert fake.resets == 1 and fake.probes == 1                                    # the clients were dropped, then probed
        assert row.json()["tls_trust"] == "system" and row.json()["probe"] == "tls_certificate"
        events = [e for e in services.tasks.store.events() if e["type"] == "provider.tls_trust_changed"]
        assert len(events) == 1 and events[0]["payload"] == {"connection_id": "openai", "from": "certifi", "to": "system"}
        fake.outcome = "ok"
        back = client.post("/gui/api/connections/openai/tls-trust", headers=headers, json={"trust": "certifi"}).json()
        assert back["tls_trust"] == "certifi" and back["probe"] == "ok" and fake.resets == 2
        for bad in ({"trust": "file:x"}, {"trust": "otra"}, {}, {"trust": "system", "extra": 1}):
            assert client.post("/gui/api/connections/openai/tls-trust", headers=headers, json=bad).status_code == 422
        assert client.post("/gui/api/connections/claude_cli/tls-trust", headers=headers,
                           json={"trust": "system"}).status_code == 404
        assert len([e for e in services.tasks.store.events() if e["type"] == "provider.tls_trust_changed"]) == 2
