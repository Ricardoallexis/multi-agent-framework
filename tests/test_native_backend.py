# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The native engine and alternate backends in the application runtime."""
from __future__ import annotations

from dataclasses import replace

import pytest

from multiagent.backends import (
    BackendContext, KNOWN_CAPABILITIES, REQUIRED_CAPABILITIES, UnknownBackend,
    available_backends, backend_problems, register_backend, unregister_backend,
)
from multiagent.backends.native import NativeBackend, build_native_backend
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import SocialPostRequest
from multiagent.doctor import run_doctor
from multiagent.services import ApplicationServices
from multiagent.step_cache import StepCachePolicy
from multiagent.workflow_engine import WorkflowEngine


def settings_for(tmp_path, **overrides):
    return Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False,
                    ollama_base_url="http://127.0.0.1:9", **overrides)


def context_for(system):
    engine = system.engine
    return BackendContext(
        store=system.store, settings=system.settings, workflows=engine.workflows,
        prompts=engine.prompts, catalog=engine.catalog, artifacts=engine.artifacts,
        router=engine.router, tool_registry=engine.tool_registry,
        approval_timeout_seconds=engine.approval_timeout_seconds,
    )


def test_default_system_uses_the_native_backend(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    assert system.settings.execution_backend == "native"
    assert system.backend is system.engine
    assert isinstance(system.backend, NativeBackend)
    assert system.backend.capabilities == KNOWN_CAPABILITIES
    assert backend_problems(system.backend, name="native") == []
    assert system.run_service.engine is system.worker.engine is system.backend


def test_execution_backend_comes_from_the_unprefixed_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECUTION_BACKEND", "missing_backend")
    settings = settings_for(tmp_path)
    assert settings.execution_backend == "missing_backend"
    with pytest.raises(UnknownBackend, match="available: .*native"):
        build_system(settings, dry_run=True)


def test_native_factory_requires_a_router_and_rejects_unknown_options(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    context = context_for(system)
    with pytest.raises(ValueError, match="requires a router"):
        build_native_backend(replace(context, router=None))
    with pytest.raises(ValueError, match="unknown_key"):
        build_native_backend(replace(context, options={"unknown_key": True}))
    cache_policy = StepCachePolicy(enabled=True)
    backend = build_native_backend(replace(context, options={
        "document_policy": None, "step_cache": cache_policy, "tool_selection": {"version": 1},
    }))
    assert backend.document_policy is None
    assert backend.step_cache_policy is cache_policy
    assert backend.tool_selection.choices == {}


def test_registered_backend_reaches_run_service_worker_and_inbox(tmp_path):
    class TestBackend:
        name = "test_backend"
        capabilities = REQUIRED_CAPABILITIES | {"inbox"}

        def __init__(self, context):
            self.native = build_native_backend(context)
            self.usage_recorder = self.native.usage_recorder
            self.availability_source = None

        def __getattr__(self, name):
            return getattr(self.native, name)

    register_backend("test_backend", TestBackend)
    try:
        system = build_system(settings_for(tmp_path, execution_backend="test_backend"), dry_run=True)
        assert isinstance(system.backend, TestBackend)
        assert system.run_service.engine is system.worker.engine is system.backend
        assert isinstance(system.engine, WorkflowEngine)
        assert system.engine is not system.backend
        services = ApplicationServices(system)
        assert system.backend.availability_source == services.inbox.policy
    finally:
        unregister_backend("test_backend")


def test_dry_run_still_finishes_through_run_service_and_local_worker(tmp_path):
    system = build_system(settings_for(tmp_path), dry_run=True)
    run = system.run_service.create_social_post(SocialPostRequest(
        project_name="native-backend", objective="Create a short educational post", topic="KNX",
    ))
    assert system.worker.process_once() is True
    assert system.run_service.get(run["id"])["status"] == "waiting_human"
    assert system.run_service.approve(run["id"])["status"] == "completed"


def test_doctor_reports_active_and_available_backends(tmp_path, monkeypatch):
    monkeypatch.setattr("multiagent.doctor._gpu_report", lambda: {"available": False})
    ok, report = run_doctor(settings_for(tmp_path))
    assert ok
    assert report["backend"] == {"active": "native", "available": available_backends()}
