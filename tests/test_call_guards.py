"""CMP-T02, contract CMP-T01 section 4: the chain of checks before every model call.

The chain is exercised on its own (order, verdicts, observers, skipping, registration), through the router
(what ``before_call`` receives) and through the real engine with scripted providers (how a ``Stop`` or a
``Pause`` from a guard becomes a failed or a waiting run, and that the reservation comes last). The probes
play the guards that USO-T04 and CNT-T04 will add, which must plug in without editing the engine. No network.
"""
from __future__ import annotations

import dataclasses
import functools
import logging
from typing import Any

import pytest
from pydantic import BaseModel

from cmp_harness import (
    Provider, assign_context_profile, events, get, make_system, names, set_window, start, telemetry_prompt_hashes,
)
from multiagent import workflow_engine
from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.call_guards import (
    ORDER_BUDGET, ORDER_CONTEXT, ORDER_LIMITS, PROCEED, CallContext, CallGuardChain, CallPaused, GuardEnvironment, Pause,
    Proceed, RunLimitsGuard, Stop, build_call_guards,
)
from multiagent.catalog import ModelSpec
from multiagent.context.estimate import ContextGuard, ContextSettings
from multiagent.contracts import ModelTier, RunStatus, TaskRequirements, WaitingReason
from multiagent.errors import BudgetExceeded, ProviderUnavailable
from multiagent.model_router import ModelBinding, ModelRouter
from multiagent.schema_utils import estimate_tokens_conservative


def spec(model_id: str = "m", **fields: Any) -> ModelSpec:
    return ModelSpec(id=model_id, provider="lab", model=model_id, tier="local", structured_output=True, web=False, **fields)


def call(**fields: Any) -> CallContext:
    return CallContext(**{"model_id": "m", "spec": spec(), **fields})


class Probe:
    """A guard that records when it ran and answers with a fixed verdict (or raises)."""

    skippable = False

    def __init__(self, name: str, order: int, verdict=PROCEED, *, observer: bool = False, raises: Exception | None = None,
                 trace: list | None = None, skippable: bool = False, on_check=None):
        self.name, self.order, self.observer, self.skippable = name, order, observer, skippable
        self.verdict, self.raises, self.trace, self.on_check = verdict, raises, trace, on_check
        self.calls: list[tuple[CallContext, Any]] = []

    def check(self, call, state):
        self.calls.append((call, state))
        if self.trace is not None:
            self.trace.append(self.name)
        if self.on_check is not None:
            self.on_check(call, state)
        if self.raises is not None:
            raise self.raises
        return self.verdict


STATE = {"llm_calls": 0, "max_llm_calls": 10, "events": []}


# --- the chain on its own --------------------------------------------------------------------------------------


def test_one_check_runs_cancel_then_the_guards_by_order_then_the_reservation():
    trace: list[str] = []
    chain = CallGuardChain(
        [Probe("budget", ORDER_BUDGET, trace=trace), Probe("context", ORDER_CONTEXT, trace=trace),
         Probe("limits", ORDER_LIMITS, trace=trace)],
        cancel=lambda c: trace.append("cancel"), reserve=lambda c: trace.append("reserve"))
    assert chain.names == ("context", "limits", "budget")          # registration order does not matter
    assert chain.check(call(), STATE) is PROCEED
    assert trace == ["cancel", "context", "limits", "budget", "reserve"]


def test_guards_of_the_same_order_keep_their_registration_order():
    trace: list[str] = []
    chain = CallGuardChain([Probe("b", 200, trace=trace), Probe("a", 200, trace=trace), Probe("first", 50, trace=trace)])
    chain.register(Probe("c", 200, trace=trace))
    chain.check(call(), STATE)
    assert trace == ["first", "b", "a", "c"]


def test_a_chain_without_cancel_reserve_or_guards_proceeds():
    assert CallGuardChain().check(call(), STATE) == Proceed()


def test_a_stop_ends_the_check_names_its_guard_and_reserves_nothing():
    trace: list[str] = []
    stop = Stop("budget_cut", "over budget", details={"limit": 5})
    chain = CallGuardChain([Probe("context", 100, trace=trace), Probe("budget", 300, stop, trace=trace),
                            Probe("later", 400, trace=trace)],
                           cancel=lambda c: trace.append("cancel"), reserve=lambda c: trace.append("reserve"))
    verdict = chain.check(call(), STATE)
    assert isinstance(verdict, Stop) and (verdict.code, verdict.message, verdict.guard) == ("budget_cut", "over budget", "budget")
    assert dict(verdict.details) == {"limit": 5}
    assert trace == ["cancel", "context", "budget"]                 # nothing after the stop, and nothing reserved


def test_a_pause_ends_the_check_and_reserves_nothing():
    trace: list[str] = []
    pause = Pause("budget_approval", {"estimated_cost": 1.5}, "needs approval")
    chain = CallGuardChain([Probe("budget", 300, pause, trace=trace), Probe("later", 400, trace=trace)],
                           reserve=lambda c: trace.append("reserve"))
    verdict = chain.check(call(), STATE)
    assert isinstance(verdict, Pause) and verdict.guard == "budget"
    assert (verdict.waiting_reason, dict(verdict.request), verdict.message) == (
        "budget_approval", {"estimated_cost": 1.5}, "needs approval")
    assert trace == ["budget"]


def test_a_guard_that_names_itself_in_its_verdict_keeps_that_name():
    chain = CallGuardChain([Probe("outer", 100, Stop("x", "y", guard="inner"))])
    assert chain.check(call(), STATE).guard == "inner"


def test_a_cancellation_raises_before_any_guard_runs_and_nothing_is_reserved():
    class Cancelled(Exception):
        pass

    def cancel(_call):
        raise Cancelled()

    probe, reserved = Probe("context", 100), []
    chain = CallGuardChain([probe], cancel=cancel, reserve=reserved.append)
    with pytest.raises(Cancelled):
        chain.check(call(), STATE)
    assert probe.calls == [] and reserved == []


def test_guards_receive_the_call_and_the_state_they_were_given():
    probe = Probe("p", 100)
    context = call(run_id="r1", step_id="create", attempt=2)
    CallGuardChain([probe]).check(context, STATE)
    assert probe.calls == [(context, STATE)]


# --- observers and failures ------------------------------------------------------------------------------------


def test_an_observer_that_fails_is_logged_and_the_call_goes_on(caplog):
    trace: list[str] = []
    chain = CallGuardChain([Probe("watcher", 100, observer=True, raises=RuntimeError("boom"), trace=trace),
                            Probe("limits", 200, trace=trace)], reserve=lambda c: trace.append("reserve"))
    with caplog.at_level(logging.WARNING, logger="multiagent.call_guards"):
        assert chain.check(call(), STATE) is PROCEED
    assert trace == ["watcher", "limits", "reserve"]
    assert any("watcher" in record.getMessage() for record in caplog.records)


def test_an_observer_that_answers_nonsense_is_ignored(caplog):
    chain = CallGuardChain([Probe("watcher", 100, verdict="maybe", observer=True)])
    with caplog.at_level(logging.WARNING, logger="multiagent.call_guards"):
        assert chain.check(call(), STATE) is PROCEED


def test_an_observer_can_still_stop_explicitly():
    # The context guard is an observer whose only stop is `unknown_window: block`.
    chain = CallGuardChain([Probe("watcher", 100, Stop("blocked", "no"), observer=True)])
    assert isinstance(chain.check(call(), STATE), Stop)


def test_a_guard_that_is_not_an_observer_fails_closed():
    trace: list[str] = []
    chain = CallGuardChain([Probe("budget", 300, raises=RuntimeError("cannot decide"), trace=trace),
                            Probe("later", 400, trace=trace)], reserve=lambda c: trace.append("reserve"))
    with pytest.raises(RuntimeError, match="cannot decide"):
        chain.check(call(), STATE)
    assert trace == ["budget"]


def test_a_guard_that_is_not_an_observer_must_return_a_verdict():
    chain = CallGuardChain([Probe("budget", 300, verdict=None)])
    with pytest.raises(TypeError, match="budget"):
        chain.check(call(), STATE)


# --- registration and skipping ---------------------------------------------------------------------------------


@pytest.mark.parametrize("guard, message", [
    (Probe("", 100), "name"),
    (Probe("  ", 100), "name"),
    (Probe("x", "100"), "order"),
    (Probe("x", True), "order"),
    (Probe("x", 1.5), "order"),
    (type("NoCheck", (), {"name": "x", "order": 1, "observer": False})(), "check"),
])
def test_a_guard_without_a_usable_name_order_or_check_is_refused(guard, message):
    with pytest.raises(ValueError, match=message):
        CallGuardChain().register(guard)


def test_names_are_unique_and_a_guard_can_be_unregistered():
    chain = CallGuardChain([Probe("limits", 200)])
    with pytest.raises(ValueError, match="already registered"):
        chain.register(Probe("limits", 999))
    assert chain.unregister("limits") is True and chain.unregister("limits") is False
    chain.register(Probe("limits", 999))
    assert chain.names == ("limits",)


def test_a_skippable_guard_can_be_left_out_and_the_rest_still_runs():
    trace: list[str] = []
    chain = CallGuardChain([Probe("context", 100, Stop("x", "y"), skippable=True, trace=trace),
                            Probe("limits", 200, trace=trace)], reserve=lambda c: trace.append("reserve"))
    assert chain.check(call(), STATE, skip=("context",)) is PROCEED
    assert trace == ["limits", "reserve"]
    trace.clear()
    assert chain.check(call(), STATE, skip="context") is PROCEED             # a single name is accepted
    assert chain.check(call(), STATE, skip=("nobody",)).guard == "context"   # an unknown name is ignored


def test_a_guard_that_is_not_skippable_cannot_be_left_out():
    chain = CallGuardChain([Probe("limits", 200), Probe("context", 100, skippable=True)])
    with pytest.raises(ValueError, match="cannot be skipped"):
        chain.check(call(), STATE, skip=("limits",))
    chain.check(call(), STATE, skip=("context",))


def test_the_summarizer_skips_the_context_guard_but_not_the_limits():
    # CMP-T03's author flagged the recursion: measuring a summary call must not start another compaction.
    emitted: list[tuple[str, dict]] = []
    settings = ContextSettings(enabled=True)
    big = call(spec=spec(context_window={"tokens": 1000, "source": "s", "checked_at": "2026-10-11",
                                         "provenance": "provider_docs"}), estimated_input_tokens=900)
    chain = CallGuardChain([ContextGuard(settings, emit=lambda *a: emitted.append(a)), RunLimitsGuard()])
    chain.check(big, STATE, skip=("context",))
    assert emitted == []
    chain.check(big, STATE)
    assert [item[0] for item in emitted] == ["context_warning"]
    exhausted = {**STATE, "llm_calls": 10}
    assert isinstance(chain.check(big, exhausted, skip=("context",)), Stop)   # the limits still apply


# --- value types -----------------------------------------------------------------------------------------------


def test_verdicts_validate_and_normalize():
    assert Pause(WaitingReason.REVIEW).waiting_reason == "review"
    for bad in ("", "  "):
        with pytest.raises(ValueError):
            Pause(bad)
        with pytest.raises(ValueError):
            Stop(bad, "message")
    with pytest.raises(dataclasses.FrozenInstanceError):
        Stop("a", "b").code = "c"


def test_a_call_context_is_frozen_and_copied_with_fields():
    original = call(retry=1, ordinal=2, estimated_input_tokens=7)
    copy = original.with_fields(run_id="r1", step_id="create", attempt=3)
    assert (copy.run_id, copy.step_id, copy.attempt, copy.retry, copy.estimated_input_tokens) == ("r1", "create", 3, 1, 7)
    assert original.run_id is None and original.attempt == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        original.attempt = 5
    with pytest.raises(TypeError):
        CallContext("m", spec())            # keyword only


# --- the limits guard (moved out of the engine) -----------------------------------------------------------------


def test_the_limits_guard_stops_at_max_llm_calls_with_the_message_the_engine_always_had():
    verdict = RunLimitsGuard().check(call(), {"llm_calls": 3, "max_llm_calls": 3, "events": []})
    assert verdict == Stop("max_llm_calls", "max_llm_calls reached")
    assert RunLimitsGuard().check(call(), {"llm_calls": 2, "max_llm_calls": 3, "events": []}) is PROCEED


def _reserved(profile_id: str, count: int) -> list[dict]:
    return [{"event_type": "routing_dispatch_reserved", "payload": {"profile_id": profile_id}}] * count


def test_the_limits_guard_counts_the_dispatches_of_its_own_profile_only():
    guard = RunLimitsGuard("eco", {"max_provider_invocations": 2})
    other = _reserved("other", 5)
    assert guard.check(call(), {"llm_calls": 0, "max_llm_calls": 9, "events": other + _reserved("eco", 1)}) is PROCEED
    verdict = guard.check(call(), {"llm_calls": 0, "max_llm_calls": 9, "events": other + _reserved("eco", 2)})
    assert verdict == Stop("max_provider_invocations", "routing profile max_provider_invocations reached")


def test_the_limits_guard_checks_the_profile_before_max_llm_calls_and_ignores_a_profile_without_limit():
    guard = RunLimitsGuard("eco", {"max_provider_invocations": 1})
    both = {"llm_calls": 9, "max_llm_calls": 9, "events": _reserved("eco", 1)}
    assert guard.check(call(), both).code == "max_provider_invocations"
    assert RunLimitsGuard("eco", {}).check(call(), {**both, "llm_calls": 0}) is PROCEED
    assert RunLimitsGuard(None, {"max_provider_invocations": 0}).check(call(), {**both, "llm_calls": 0}) is PROCEED


# --- building the chain ----------------------------------------------------------------------------------------


def test_the_default_chain_has_only_the_limits_and_nothing_changes_without_a_context_profile():
    assert build_call_guards(GuardEnvironment()).names == ("limits",)
    assert build_call_guards(GuardEnvironment(context_settings=ContextSettings(enabled=False))).names == ("limits",)


def test_an_enabled_context_profile_adds_the_context_guard_before_the_limits_and_it_emits_to_the_host():
    emitted: list[tuple[str, dict]] = []
    chain = build_call_guards(GuardEnvironment(context_settings=ContextSettings(enabled=True),
                                               emit=lambda event_type, payload: emitted.append((event_type, payload))))
    assert chain.names == ("context", "limits")
    chain.check(call(), STATE)                   # an unknown window: one warning for the host
    assert [item[0] for item in emitted] == ["context_warning"]


def test_a_later_stage_plugs_in_with_one_registration_and_runs_where_its_order_says():
    chain = build_call_guards(GuardEnvironment(context_settings=ContextSettings(enabled=True)))
    chain.register(Probe("budget", ORDER_BUDGET))
    assert chain.names == ("context", "limits", "budget")


# --- the router hands the candidate to before_call ---------------------------------------------------------------


class Payload(BaseModel):
    value: str


class Scripted(LLMAdapter):
    def __init__(self, provider: str, results: list[Any]):
        self.provider, self.results, self.calls = provider, list(results), 0

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return LLMResponse(Payload(value=result), "{}", self.provider, spec.model, tokens_in=1, tokens_out=1)

    def health(self):
        return {"provider": self.provider, "available": True}


class StaticCatalog:
    routing_profiles: dict = {}

    def __init__(self):
        self.models = {
            "primary": spec("primary", context_window={"tokens": 100, "source": "s", "provenance": "provider_docs",
                                                       "checked_at": "2026-10-11"}),
            "fallback": ModelSpec("fallback", "two", "fb", "cloud", True, False),
        }

    def model(self, model_id):
        return self.models[model_id]


def routed(results_one, results_two=(), **kwargs):
    adapters = {"lab": Scripted("lab", results_one), "two": Scripted("two", results_two)}
    router = ModelRouter(StaticCatalog(), adapters, **kwargs)
    requirements = TaskRequirements(requires_web=False, output_schema="Payload", tier=ModelTier.LOCAL)
    return router, adapters, requirements


def test_before_call_receives_each_candidate_retry_ordinal_and_the_estimate_of_the_exact_prompt():
    router, adapters, requirements = routed([ProviderUnavailable("down")], ["ok"])
    seen: list[CallContext] = []
    prompt = "x" * 640
    router.execute(binding=ModelBinding("primary", "fallback"), requirements=requirements, prompt=prompt,
                   output_schema=Payload, before_call=seen.append)
    assert [(c.model_id, c.retry, c.ordinal) for c in seen] == [("primary", 0, 1), ("fallback", 0, 2)]
    assert [c.spec.id for c in seen] == ["primary", "fallback"]
    assert {c.estimated_input_tokens for c in seen} == {estimate_tokens_conservative(prompt)} == {200}
    # What the router does not know is left for the host to fill in.
    assert seen[0].scope == "run_step" and seen[0].run_id is None and seen[0].step_id is None and seen[0].attempt == 1


def test_a_retry_of_the_same_candidate_is_its_own_call():
    from multiagent.errors import StructuredOutputError
    router, adapters, requirements = routed([StructuredOutputError("bad json"), "ok"])
    seen: list[CallContext] = []
    router.execute(binding=ModelBinding("primary", max_retries=1), requirements=requirements, prompt="p",
                   output_schema=Payload, before_call=seen.append)
    assert [(c.model_id, c.retry, c.ordinal) for c in seen] == [("primary", 0, 1), ("primary", 1, 2)]


def test_a_before_call_without_parameters_still_works():
    router, adapters, requirements = routed(["ok"])
    calls: list[str] = []
    router.execute(binding=ModelBinding("primary"), requirements=requirements, prompt="p", output_schema=Payload,
                   before_call=lambda: calls.append("called"))
    assert calls == ["called"] and adapters["lab"].calls == 1


def test_a_before_call_with_variadic_or_bound_arguments_receives_the_context():
    router, adapters, requirements = routed(["ok", "ok"])
    got: list[Any] = []

    def variadic(*args):
        got.append(args)

    def with_skip(context, *, skip=()):
        got.append((context.model_id, skip))

    router.execute(binding=ModelBinding("primary"), requirements=requirements, prompt="p", output_schema=Payload,
                   before_call=variadic)
    router.execute(binding=ModelBinding("primary"), requirements=requirements, prompt="p", output_schema=Payload,
                   before_call=functools.partial(with_skip, skip=("context",)))  # how the summarizer's calls are made
    assert isinstance(got[0][0], CallContext) and got[1] == ("primary", ("context",))


def test_a_before_call_that_raises_stops_the_candidate_before_any_provider_call():
    router, adapters, requirements = routed(["ok"], ["ok"])
    attempts: list[dict] = []

    def refuse(_context):
        raise BudgetExceeded("no more calls")

    with pytest.raises(BudgetExceeded):
        router.execute(binding=ModelBinding("primary", "fallback"), requirements=requirements, prompt="p",
                       output_schema=Payload, before_call=refuse, on_attempt=attempts.append)
    assert adapters["lab"].calls == 0 and adapters["two"].calls == 0
    assert [(a["stage"], a["provider_invoked"], a["status"]) for a in attempts] == [("preflight", False, "failed")]


def test_a_dry_run_hands_over_the_fixture_candidate():
    class FakeProvider(Scripted):
        pass

    adapters = {"fake": FakeProvider("fake", ["ok"])}
    router = ModelRouter(StaticCatalog(), adapters, dry_run=True)
    seen: list[CallContext] = []
    router.execute(binding=ModelBinding("primary"),
                   requirements=TaskRequirements(requires_web=False, output_schema="Payload", tier=ModelTier.LOCAL),
                   prompt="p", output_schema=Payload, before_call=seen.append)
    assert [(c.model_id, c.spec.id, c.retry, c.ordinal) for c in seen] == [("fixture", "fixture", 0, 1)]


# --- the engine ------------------------------------------------------------------------------------------------


def plug(monkeypatch, *guards):
    """Register extra guards in every chain the engine builds, as USO-T04 and CNT-T04 will in build_call_guards()."""
    original = workflow_engine.build_call_guards

    def build(env, **kwargs):
        chain = original(env, **kwargs)
        for guard in guards:
            if chain.names.count(guard.name) == 0:
                chain.register(guard)
        return chain

    monkeypatch.setattr(workflow_engine, "build_call_guards", build)


def test_the_engine_runs_the_chain_in_order_and_reserves_the_call_last(tmp_path, monkeypatch):
    system = make_system(tmp_path)
    assign_context_profile(system)
    set_window(system, "local_default", 100_000)
    seen_calls_at_budget: list[int] = []
    trace: list[str] = []
    plug(monkeypatch,
         Probe("early", 50, trace=trace),
         Probe("middle", 150, trace=trace),
         Probe("between", 250, trace=trace),
         Probe("budget", ORDER_BUDGET, trace=trace, on_check=lambda c, state: seen_calls_at_budget.append(state["llm_calls"])))
    run = get(system, start(system))
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert trace == ["early", "middle", "between", "budget"]
    assert seen_calls_at_budget == [0] and run["llm_calls"] == 1     # booked only after every guard let the call through
    assert system.ollama.models == ["local_default"]


def test_the_engine_completes_the_call_context_the_router_started(tmp_path, monkeypatch):
    system = make_system(tmp_path)
    probe = Probe("budget", ORDER_BUDGET)
    plug(monkeypatch, probe)
    run_id = start(system)
    (context, state), = probe.calls
    assert (context.scope, context.run_id, context.task_id, context.step_id, context.step_kind) == (
        "run_step", run_id, None, "create", "work")
    assert (context.attempt, context.retry, context.ordinal, context.model_id, context.profile_id) == (
        1, 0, 1, "local_default", None)
    assert context.spec is system.catalog.models["local_default"] and context.data_class == "non_sensitive"
    assert context.estimated_input_tokens == estimate_tokens_conservative(system.ollama.prompts[0])
    assert state["id"] == run_id and "events" in state


def test_a_sensitive_request_is_labelled_in_the_call_context(tmp_path, monkeypatch):
    system = make_system(tmp_path)
    probe = Probe("budget", ORDER_BUDGET)
    plug(monkeypatch, probe)
    start(system, sensitive=True)
    assert probe.calls and probe.calls[0][0].data_class == "sensitive"


def test_a_stop_from_a_guard_fails_the_run_before_any_provider_call(tmp_path, monkeypatch):
    system = make_system(tmp_path)
    plug(monkeypatch, Probe("budget", ORDER_BUDGET, Stop("budget_cut", "this call would exceed the budget")))
    run = get(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and run["error"] == "this call would exceed the budget"
    assert system.ollama.prompts == [] and system.gemini.prompts == []
    assert run["llm_calls"] == 0                                        # nothing was reserved
    (attempt,) = events(run, "model_attempt")
    assert (attempt["stage"], attempt["provider_invoked"], attempt["status"], attempt["error_type"]) == (
        "preflight", False, "failed", "BudgetExceeded")


def test_a_pause_from_a_guard_waits_for_a_human_with_its_waiting_reason(tmp_path, monkeypatch):
    system = make_system(tmp_path)
    request = {"estimated_cost": 4.2, "currency": "USD"}
    plug(monkeypatch, Probe("budget", ORDER_BUDGET, Pause("budget_approval", request, "approve a 4.2 USD call")))
    run = get(system, start(system))
    assert run["status"] == RunStatus.WAITING_HUMAN.value
    assert (run["waiting_reason"], run["waiting_step"], run["waiting_attempt"], run["current_step"]) == (
        "budget_approval", "create", 1, 1)
    (paused,) = events(run, "call_paused")
    assert paused == {"step_id": "create", "attempt": 1, "guard": "budget", "waiting_reason": "budget_approval",
                      "model_id": "local_default", "ordinal": 1, "message": "approve a 4.2 USD call", "request": request}
    assert system.ollama.prompts == [] and run["llm_calls"] == 0
    assert not {"workflow_failed", "budget_exhausted", "step_completed"} & set(names(run)) and not run["error"]


def test_the_engine_counts_a_call_only_when_it_is_reserved(tmp_path, monkeypatch):
    # A Stop on the second call (the fallback) leaves the first one booked and the second not.
    system = make_system(tmp_path, ollama=Provider("ollama", [ProviderUnavailable("down")]))

    probe = Probe("budget", ORDER_BUDGET)
    probe.check = lambda c, state: Stop("budget_cut", "stop") if c.ordinal == 2 else PROCEED
    plug(monkeypatch, probe)
    run = get(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and run["llm_calls"] == 1
    assert system.ollama.models == ["local_default"] and system.gemini.prompts == []


def test_a_cancellation_during_a_step_is_seen_by_the_chain_before_the_next_guard_runs(tmp_path, monkeypatch):
    holder: dict[str, Any] = {}

    def cancel_then_fail(_spec, _prompt):
        holder["system"].store.update_run(holder["run_id"], cancel_requested=1)
        return ProviderUnavailable("down")

    system = make_system(tmp_path, ollama=Provider("ollama", [cancel_then_fail]))
    holder["system"] = system
    probe = Probe("budget", ORDER_BUDGET)
    plug(monkeypatch, probe)
    original_create = system.run_service.create_social_post

    def create(request):
        run = original_create(request)
        holder["run_id"] = run["id"]
        return run

    system.run_service.create_social_post = create
    run = get(system, start(system))
    assert run["status"] == RunStatus.CANCELLED.value
    assert len(probe.calls) == 1                                       # the fallback call never reached the guards
    assert system.gemini.prompts == []


def test_the_limits_guard_keeps_max_llm_calls_behaviour_in_the_engine(tmp_path):
    system = make_system(tmp_path, ollama=Provider("ollama", [ProviderUnavailable("down")]), max_llm_calls=1)
    run = get(system, start(system))
    assert run["status"] == RunStatus.FAILED.value and run["error"] == "max_llm_calls reached"
    assert run["llm_calls"] == 1 and system.gemini.prompts == []


def test_a_failing_observer_does_not_cut_a_real_call(tmp_path, monkeypatch):
    system = make_system(tmp_path)
    plug(monkeypatch, Probe("watcher", 120, observer=True, raises=RuntimeError("boom")))
    run = get(system, start(system))
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert len(system.ollama.prompts) == 1


def test_a_run_without_extra_guards_is_what_it_was(tmp_path):
    system = make_system(tmp_path)
    run_id = start(system)
    run = get(system, run_id)
    assert names(run) == ["workflow_started", "run_started", "step_skipped", "routing_decision", "step_started",
                          "model_attempt", "model_selected", "step_completed", "checkpoint"]
    assert run["llm_calls"] == 1 and len(telemetry_prompt_hashes(system, run_id)) == 1


def test_a_call_paused_error_carries_the_verdict_and_the_call():
    verdict, context = Pause("budget_approval", message=""), call()
    error = CallPaused(verdict, context)
    assert error.verdict is verdict and error.call is context and "budget_approval" in str(error)
