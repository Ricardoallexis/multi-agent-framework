# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Ollama builds its HTTP client only when the adapter is used."""
from __future__ import annotations

import pytest

from multiagent.adapters import ollama as ollama_module
from multiagent.adapters.ollama import OllamaAdapter
from multiagent.bootstrap import build_system
from multiagent.config import Settings


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class ClientDouble:
    instances: list[ClientDouble] = []
    responses = {
        "/api/version": {"version": "0.0.0"},
        "/api/tags": {"models": [{"name": "local-model"}]},
        "/api/ps": {"models": [{"name": "local-model"}]},
    }

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.get_calls: list[str] = []
        self.closed = False
        type(self).instances.append(self)

    def get(self, path: str) -> _Response:
        self.get_calls.append(path)
        return _Response(self.responses[path])

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def client_double(monkeypatch):
    ClientDouble.instances.clear()
    monkeypatch.setattr(ollama_module.httpx, "Client", ClientDouble)
    return ClientDouble


def _settings(tmp_path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True)


def test_build_system_in_mock_creates_no_ollama_client(tmp_path, client_double):
    system = build_system(_settings(tmp_path), dry_run=True)

    assert isinstance(system.adapters["ollama"], OllamaAdapter)
    assert client_double.instances == []


def test_first_call_builds_the_client_once(tmp_path, client_double):
    adapter = OllamaAdapter(_settings(tmp_path))

    first = adapter.health()
    second = adapter.health()

    assert first == {
        "provider": "ollama",
        "available": True,
        "version": "0.0.0",
        "installed_models": ["local-model"],
        "loaded_models": ["local-model"],
    }
    assert second == first
    assert len(client_double.instances) == 1
    assert client_double.instances[0].get_calls == [
        "/api/version", "/api/tags", "/api/ps",
        "/api/version", "/api/tags", "/api/ps",
    ]


def test_reset_before_use_builds_nothing(tmp_path, client_double):
    adapter = OllamaAdapter(_settings(tmp_path))

    adapter.reset_client()

    assert client_double.instances == []
    assert adapter.health()["available"] is True
    assert len(client_double.instances) == 1


def test_reset_after_use_replaces_and_closes(tmp_path, client_double):
    adapter = OllamaAdapter(_settings(tmp_path))
    assert adapter.health()["available"] is True
    old = client_double.instances[0]

    adapter.reset_client()

    assert len(client_double.instances) == 2
    assert old.closed is True
    assert adapter.client is client_double.instances[1]
    assert adapter.health()["available"] is True
    assert len(client_double.instances) == 2


def test_injected_client_is_kept(tmp_path, client_double):
    injected = ClientDouble(base_url="http://injected", timeout=1)
    adapter = OllamaAdapter(_settings(tmp_path), client=injected)

    assert adapter.client is injected
    adapter.reset_client()

    assert adapter.client is injected
    assert len(client_double.instances) == 1
    assert injected.closed is False
