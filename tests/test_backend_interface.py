# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The execution backend interface and its registry (BKD-T01, roadmap S4.A1)."""
from __future__ import annotations

import inspect
import subprocess
import sys

import pytest

from multiagent.backends import (
    BUILTIN_BACKENDS,
    KNOWN_CAPABILITIES,
    REQUIRED_CAPABILITIES,
    BackendContext,
    ExecutionBackend,
    InvalidBackend,
    UnknownBackend,
    available_backends,
    backend_problems,
    build_backend,
    register_backend,
    unregister_backend,
)
from multiagent.workflow_engine import WorkflowEngine

PROTOCOL_METHODS = ("preflight", "process_run", "submit_human_step", "review_point", "check_approval_deadlines")


class _Backend:
    """The smallest object that meets the contract."""

    def __init__(self, name="toy", capabilities=REQUIRED_CAPABILITIES):
        self.name = name
        self.capabilities = frozenset(capabilities)
        self.usage_recorder = object()

    def preflight(self, workflow_id):
        raise KeyError(workflow_id)

    def process_run(self, run_id):
        return {"id": run_id}

    def submit_human_step(self, run_id, submission):
        return {"id": run_id}

    def review_point(self, run):
        return None, {}

    def check_approval_deadlines(self, *, run_id=None):
        return 0


def _context() -> BackendContext:
    return BackendContext(store=None, settings=None, workflows=None, prompts=None, catalog=None, artifacts=None)


@pytest.fixture
def registered():
    names: list[str] = []

    def register(name, factory, **kwargs):
        register_backend(name, factory, **kwargs)
        names.append(name)

    yield register
    for name in names:
        unregister_backend(name)


def test_the_native_engine_already_has_the_shape_of_the_interface():
    # BKD-T02 puts the engine behind the interface "without behavior changes": its public methods must already take
    # the arguments the protocol names, with the same kinds (positional or keyword-only).
    for method in PROTOCOL_METHODS:
        protocol = inspect.signature(getattr(ExecutionBackend, method))
        engine = inspect.signature(getattr(WorkflowEngine, method))
        expected = [(p.name, p.kind) for p in protocol.parameters.values()]
        actual = [(p.name, p.kind) for p in engine.parameters.values() if p.name in protocol.parameters]
        assert actual == expected, method


def test_a_minimal_backend_meets_the_contract_and_the_runtime_protocol():
    backend = _Backend()
    assert backend_problems(backend, name="toy") == []
    assert isinstance(backend, ExecutionBackend)


@pytest.mark.parametrize(("change", "problem"), [
    (lambda b: delattr(b, "usage_recorder"), "missing attribute 'usage_recorder'"),
    (lambda b: setattr(b, "process_run", None), "missing method process_run()"),
    (lambda b: setattr(b, "capabilities", {"usage", "budgets", "cancellation"}), "capabilities must be a frozenset"),
    (lambda b: setattr(b, "capabilities", REQUIRED_CAPABILITIES | {"teleport"}), "unknown capabilities: teleport"),
    (lambda b: setattr(b, "capabilities", frozenset({"usage"})), "missing required capabilities: budgets, cancellation"),
    (lambda b: setattr(b, "capabilities", REQUIRED_CAPABILITIES | {"inbox"}),
     "declares 'inbox' without a 'availability_source' attribute"),
    (lambda b: setattr(b, "name", "other"), "registered as 'toy' but its name is 'other'"),
])
def test_backend_problems_names_each_broken_part(change, problem):
    backend = _Backend()
    change(backend)
    assert problem in backend_problems(backend, name="toy")


def test_required_capabilities_are_known_and_every_capability_is_described():
    assert REQUIRED_CAPABILITIES <= KNOWN_CAPABILITIES
    assert {"human_steps", "review", "recovery", "routing", "tools"} <= KNOWN_CAPABILITIES


def test_build_backend_builds_a_registered_backend_with_its_context(registered):
    seen = []

    def factory(context):
        seen.append(context)
        return _Backend()

    registered("toy", factory)
    context = _context()
    assert build_backend("toy", context).name == "toy"
    assert seen == [context]
    assert "toy" in available_backends()


def test_build_backend_refuses_a_factory_that_breaks_the_contract(registered):
    registered("toy", lambda context: _Backend(capabilities={"usage"}))
    with pytest.raises(InvalidBackend, match="missing required capabilities"):
        build_backend("toy", _context())


def test_an_unknown_name_lists_the_available_backends(registered):
    registered("toy", lambda context: _Backend())
    with pytest.raises(UnknownBackend, match=r"Unknown execution backend 'nope'; available: .*toy"):
        build_backend("nope", _context())


def test_names_are_lowercase_and_cannot_be_taken_twice(registered):
    with pytest.raises(ValueError, match="lowercase"):
        register_backend("Toy", lambda context: _Backend())
    registered("toy", lambda context: _Backend())
    with pytest.raises(ValueError, match="already exists"):
        register_backend("toy", lambda context: _Backend())
    with pytest.raises(ValueError, match="already exists"):
        register_backend("native", lambda context: _Backend(name="native"))
    registered("toy", lambda context: _Backend(), replace=True)


def test_the_built_in_backends_are_declared_by_module_and_function():
    assert set(BUILTIN_BACKENDS) == {"native", "mock"}
    for target in BUILTIN_BACKENDS.values():
        module, function = target.split(":")
        assert module.startswith("multiagent.backends.") and function.startswith("build_")


def test_importing_the_interface_does_not_import_the_engine_or_an_adapter():
    code = ("import sys, multiagent.backends; "
            "loaded = [m for m in sys.modules if m == 'multiagent.workflow_engine' or m.startswith('multiagent.adapters')]; "
            "print(','.join(loaded))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ""
