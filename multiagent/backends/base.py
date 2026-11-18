# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The execution backend interface (roadmap S4.A1).

A backend is whatever turns a queued run into executed steps. The run service and the worker talk to it only
through ``ExecutionBackend``, and the ``Store`` is the one durable record every backend reads and writes: a backend
keeps no run state of its own between calls. ``docs/BACKENDS.md`` has the long form of this contract.

execute
    ``process_run(run_id)`` takes a ``queued`` run (the worker has already stamped it with its id), executes steps
    from ``current_step`` and returns the run as the ``Store`` holds it once it is ``completed``, ``failed``,
    ``cancelled``, ``rejected`` or ``waiting_human``. A run that is not queued comes back as it is, after its approval
    deadlines are checked. Each attempt is recorded with ``Store.record_step`` and each output with
    ``Store.add_artifact``. A failure of the run is stored, not raised; what ``process_run`` raises is a fault of the
    backend itself, and the worker fails the run with it.
events
    Only through the ``Store``: ``Store.db.log_event``, or a ``Store`` operation that writes its own event in the same
    transaction (``fail_active_run``, ``finish_review``). A backend has no event table of its own, so every reader of
    the run sees every backend the same way. ``REQUIRED_EVENTS`` names the events each backend must emit.
usage
    Only through the ``UsageRecorder`` the backend exposes as ``usage_recorder``: one record per model call (a backend
    without real calls records zero tokens), so budgets and observers read every backend the same way.
pause for a human
    A step that needs a person sets ``status=waiting_human`` with a ``WaitingReason`` and returns. A backend never
    blocks a thread waiting for a person.
resume
    The person's decision goes through ``RunService`` (approve, reject and the held-call decisions of the ``Store``)
    or through ``submit_human_step``. A run that goes back to ``queued`` is picked up by ``process_run`` again and
    continues from ``current_step``.
cancel
    ``RunService.cancel`` sets ``cancel_requested``. The backend checks it before each step and before each model
    call, and ends the run in ``cancelled`` with a ``workflow_cancelled`` event.

Nothing in this module imports a backend, a provider adapter or the workflow engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

if TYPE_CHECKING:  # imported for annotations only: this module must stay free of engine and adapter imports
    from pydantic import BaseModel

    from ..artifacts import ArtifactWriter
    from ..catalog import Catalog
    from ..contracts import HumanStepSubmission
    from ..db.store import Store
    from ..model_router import ModelRouter
    from ..observability.usage import UsageRecorder
    from ..prompts import PromptManager
    from ..tools.registry import ToolRegistry
    from ..workflows import WorkflowCatalog, WorkflowDefinition

# What a backend can declare in ``capabilities``. A backend declares only what it really does; the conformance suite
# (S4.B1) runs the checks of a capability only against the backends that declare it.
CAPABILITY_DESCRIPTIONS: Mapping[str, str] = {
    "usage": "one usage record per model call through usage_recorder",
    "budgets": "stops at max_llm_calls and max_run_seconds and records budget_exhausted",
    "cancellation": "honours cancel_requested before each step and each model call",
    "human_steps": "pauses human_guided steps until submit_human_step validates a submission",
    "review": "pauses at checkpoints for approve or reject, and reruns a rejected step with the revision feedback",
    "recovery": "continues a run the worker recovered after an interruption, from current_step",
    "approval_deadlines": "expires checkpoint approvals after approval_timeout_seconds",
    "routing": "selects models with the router, routing profiles and bounded escalation",
    "tools": "runs the tools a step declares, with per-call approval",
    "step_cache": "reuses cached step results under a step cache policy",
    "context_compaction": "compacts step context under a compaction policy",
    "document_barrier": "screens and quotes untrusted context under a document policy",
    "budget_guard": "asks budgets_source before each paid call (budget cut or warning)",
    "inbox": "reads availability_source for messages of the person at step boundaries",
}
KNOWN_CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_DESCRIPTIONS)

# Every backend has these: without them the run service could not bound, stop or account for a run.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({"usage", "budgets", "cancellation"})

# Events every backend emits through ``Store.db.log_event`` (the conformance suite checks their order).
REQUIRED_EVENTS: tuple[str, ...] = (
    "run_started",         # process_run starts or resumes a queued run
    "step_started",        # an attempt of a step begins
    "step_completed",      # an attempt produced an output that passed its contract
    "workflow_completed",  # the last step completed
    "workflow_failed",     # the run ended in failed
    "workflow_cancelled",  # the run ended in cancelled
)
# Emitted only by a backend that declares the capability on the left.
CAPABILITY_EVENTS: Mapping[str, tuple[str, ...]] = {
    "human_steps": ("human_step_requested", "human_step_submitted"),
    "review": ("checkpoint",),
    "budgets": ("budget_exhausted",),
}


@dataclass(frozen=True)
class BackendContext:
    """What a backend factory gets to build a backend. Every backend shares the same store, settings and definitions;
    the router, tools and policies are there for the backends that use them, and a backend may ignore them."""

    store: Store
    settings: Any
    workflows: WorkflowCatalog
    prompts: PromptManager
    catalog: Catalog
    artifacts: ArtifactWriter
    router: ModelRouter | None = None
    output_schemas: Mapping[str, type[BaseModel]] | None = None
    tool_registry: ToolRegistry | None = None
    approval_timeout_seconds: float | None = None
    # Example outputs per contract (a bundle's ``sample_outputs``); the mock backend answers with them.
    sample_outputs: Mapping[str, Any] = field(default_factory=dict)
    # Keyword arguments only one backend understands (for the native one, ``document_policy`` or ``step_cache``).
    options: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class ExecutionBackend(Protocol):
    """What the run service and the worker need from whatever executes runs (see the module docstring).

    ``name`` is the name it is registered under. ``capabilities`` is a subset of ``KNOWN_CAPABILITIES`` that contains
    ``REQUIRED_CAPABILITIES``. A backend that declares ``budget_guard`` also has a writable ``budgets_source``
    attribute, and one that declares ``inbox`` a writable ``availability_source`` (the application services set them).
    """

    name: str
    capabilities: frozenset[str]
    usage_recorder: UsageRecorder

    def preflight(self, workflow_id: str) -> WorkflowDefinition:
        """Load and validate a workflow before a run is created; raise ``KeyError`` or ``WorkflowDefinitionError``."""
        ...

    def process_run(self, run_id: str) -> dict[str, Any]:
        """Execute a queued run until it waits, completes, fails or is cancelled; return the stored run."""
        ...

    def submit_human_step(self, run_id: str, submission: HumanStepSubmission) -> dict[str, Any]:
        """Validate and store the person's output for the step the run waits on; return the stored run."""
        ...

    def review_point(self, run: dict[str, Any]) -> tuple[WorkflowDefinition, dict[str, Any]]:
        """The definition and the artifact under review for a run waiting at a checkpoint."""
        ...

    def check_approval_deadlines(self, *, run_id: str | None = None) -> int:
        """Expire the approvals whose deadline passed (one run or all); return how many changed."""
        ...


_PROTOCOL_METHODS = ("preflight", "process_run", "submit_human_step", "review_point", "check_approval_deadlines")
_PROTOCOL_ATTRIBUTES = ("name", "capabilities", "usage_recorder")


def backend_problems(backend: object, *, name: str | None = None) -> list[str]:
    """What keeps ``backend`` from meeting the contract; empty when it meets it. ``name`` is the registered name."""
    problems: list[str] = []
    for attribute in _PROTOCOL_ATTRIBUTES:
        if not hasattr(backend, attribute):
            problems.append(f"missing attribute {attribute!r}")
    for method in _PROTOCOL_METHODS:
        if not callable(getattr(backend, method, None)):
            problems.append(f"missing method {method}()")
    declared_name = getattr(backend, "name", None)
    if name is not None and declared_name is not None and declared_name != name:
        problems.append(f"registered as {name!r} but its name is {declared_name!r}")
    capabilities = getattr(backend, "capabilities", None)
    if capabilities is not None:
        if not isinstance(capabilities, frozenset):
            problems.append("capabilities must be a frozenset")
        else:
            unknown = sorted(capabilities - KNOWN_CAPABILITIES)
            missing = sorted(REQUIRED_CAPABILITIES - capabilities)
            if unknown:
                problems.append(f"unknown capabilities: {', '.join(unknown)}")
            if missing:
                problems.append(f"missing required capabilities: {', '.join(missing)}")
            for capability, attribute in (("budget_guard", "budgets_source"), ("inbox", "availability_source")):
                if capability in capabilities and not hasattr(backend, attribute):
                    problems.append(f"declares {capability!r} without a {attribute!r} attribute")
    return problems
