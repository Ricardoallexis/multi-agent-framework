"""CMP-T02, contract CMP-T01 sections 3, 10 and 11: estimating the prompt against the model window, the warning
before the call, and the context profiles of the catalog.

Pure estimation first, then the profile and its validation in the catalog, then the guard on its own, then
the real engine with scripted providers (no network): the warning, the threshold that never cuts a call, an
unknown window, one measure per candidate, and prompts identical with the feature off.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from cmp_harness import (
    Provider, assign_context_profile, events, get, make_system, names, prompt_tokens, set_window, start,
    telemetry_prompt_hashes,
)
from multiagent.bootstrap import build_system
from multiagent.call_guards import PROCEED, CallContext, Stop
from multiagent.catalog import (
    DEFAULT_CONTEXT_SUMMARY, Catalog, CatalogError, ModelSpec, validate_context_profile,
)
from multiagent.config import Settings
from multiagent.context.estimate import (
    CODE_CONTEXT_WINDOW_UNKNOWN, ESTIMATOR, ContextGuard, ContextSettings, compaction_pending, context_settings_for,
    context_settings_for_tasks, estimate_call, estimate_context, estimate_prompt_tokens,
)
from multiagent.contracts import RunStatus, SocialPostRequest
from multiagent.errors import ProviderUnavailable

ROOT = Path(__file__).resolve().parents[1]


def evidence(tokens: int = 1000, **overrides: Any) -> dict[str, Any]:
    return {"tokens": tokens, "source": "unit test documentation", "checked_at": "2026-10-11",
            "provenance": "provider_docs", "model": "m", **overrides}


def spec(window: dict | None = None) -> ModelSpec:
    return ModelSpec(id="m", provider="lab", model="m", tier="local", structured_output=True, web=False,
                     context_window=window)


def call(tokens: int, *, window: dict | None = None, **fields: Any) -> CallContext:
    return CallContext(**{"model_id": "m", "spec": spec(window), "estimated_input_tokens": tokens,
                          "run_id": "r1", "step_id": "create", "attempt": 1, "ordinal": 1, **fields})


SETTINGS = ContextSettings(enabled=True)


# --- the estimate -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tokens, level, ratio", [
    (0, "ok", 0.0),
    (619, "ok", 0.619),
    (620, "warning", 0.62),            # the warning starts AT 62 %
    (699, "warning", 0.699),
    (700, "threshold", 0.7),           # and the threshold AT 70 %
    (950, "threshold", 0.95),
    (5000, "threshold", 5.0),          # bigger than the window: still only a level, never a refusal
])
def test_the_level_follows_the_ratio_of_the_estimate_to_the_window(tokens, level, ratio):
    estimate = estimate_context(model_id="m", estimated_tokens=tokens, window=evidence(1000), settings=SETTINGS)
    assert estimate.level == level and estimate.ratio == pytest.approx(ratio)
    assert estimate.compaction_pending is (level == "threshold")
    assert (estimate.warning_at, estimate.threshold) == (0.62, 0.70)


def test_the_thresholds_come_from_the_profile():
    settings = ContextSettings(enabled=True, warning_at=0.5, threshold=0.9)
    levels = [estimate_context(model_id="m", estimated_tokens=n, window=evidence(1000), settings=settings).level
              for n in (499, 500, 899, 900)]
    assert levels == ["ok", "warning", "warning", "threshold"]


@pytest.mark.parametrize("window", [
    None, {}, evidence(0), evidence(-5), evidence(True), evidence(1000.5), evidence(source=""), evidence(provenance="guess"),
    evidence(provenance="provider_docs", checked_at=None), "1000",
])
def test_a_window_without_usable_evidence_is_unknown_and_never_replaced_by_a_number(window):
    estimate = estimate_context(model_id="m", estimated_tokens=10**9, window=window, settings=SETTINGS)
    assert (estimate.level, estimate.ratio, estimate.window, estimate.compaction_pending) == ("window_unknown", None, None, False)
    assert estimate.window_fields() is None


def test_a_local_window_from_num_ctx_counts():
    window = {"tokens": 4096, "source": "num_ctx sent to Ollama", "checked_at": None, "provenance": "runtime_setting"}
    estimate = estimate_context(model_id="m", estimated_tokens=3000, window=window, settings=SETTINGS)
    assert estimate.level == "threshold" and estimate.window_fields() == {
        "tokens": 4096, "source": "num_ctx sent to Ollama", "provenance": "runtime_setting"}


def test_a_call_is_measured_against_the_window_of_its_own_candidate():
    assert estimate_call(call(650, window=evidence(1000)), SETTINGS).level == "warning"
    assert estimate_call(call(650, window=evidence(10_000)), SETTINGS).level == "ok"
    assert estimate_call(call(650), SETTINGS).level == "window_unknown"


def test_the_estimator_is_the_conservative_character_count():
    assert estimate_prompt_tokens("x" * 320) == 100 and estimate_prompt_tokens("") == 0
    assert ESTIMATOR == "estimate_tokens_conservative"


# --- the settings of a profile ----------------------------------------------------------------------------------


def test_a_profile_is_off_and_has_the_contract_defaults_unless_it_says_otherwise():
    settings = ContextSettings()
    assert (settings.enabled, settings.warning_at, settings.threshold, settings.unknown_window, settings.keep_recent,
            settings.tasks_window_tokens) == (False, 0.62, 0.70, "skip", 1, None)
    assert dict(settings.summary) == {"first_level": "T1", "retry_level": "T2", "effort": "low", "model_check": True,
                                      "max_calls": 4, "max_ratio": 0.5}
    assert dict(settings.summary) == DEFAULT_CONTEXT_SUMMARY


def test_settings_from_a_profile_fill_the_defaults_and_expose_the_summary_block():
    settings = ContextSettings.from_profile("lean", {"enabled": True, "threshold": 0.8, "summary": {"max_calls": 2},
                                                     "tasks": {"window_tokens": 8000}})
    assert (settings.profile_id, settings.enabled, settings.warning_at, settings.threshold, settings.tasks_window_tokens) == (
        "lean", True, 0.62, 0.8, 8000)
    assert dict(settings.summary) == {**DEFAULT_CONTEXT_SUMMARY, "max_calls": 2}   # what SummaryPolicy.from_profile takes
    with pytest.raises(TypeError):
        settings.summary["max_calls"] = 9                                          # a read-only view


@pytest.mark.parametrize("fields", [
    {"warning_at": 0.7, "threshold": 0.7}, {"warning_at": 0.8, "threshold": 0.7}, {"warning_at": 0.0},
    {"threshold": 1.0}, {"unknown_window": "maybe"}, {"keep_recent": 0}, {"enabled": "yes"},
    {"summary": {"first_level": "T3"}}, {"tasks_window_tokens": 0},
])
def test_settings_that_the_catalog_would_refuse_cannot_be_built(fields):
    with pytest.raises(CatalogError):
        ContextSettings(**fields)


# --- context_profiles in catalog/models.yaml --------------------------------------------------------------------


def settings_for(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, local_dir=tmp_path / "local")


def load(tmp_path: Path, context_profiles: Any = ..., *, models: dict | None = None) -> Catalog:
    folder = tmp_path / "catalog"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    document: dict[str, Any] = {"models": models or {}}
    if context_profiles is not ...:
        document["context_profiles"] = context_profiles
    (folder / "models.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    return Catalog(settings_for(tmp_path), catalog_dir=folder)


def test_the_bundled_catalog_declares_a_standard_profile_that_is_off_and_assigned_to_nothing(tmp_path):
    catalog = Catalog(settings_for(tmp_path))
    assert set(catalog.context_profiles) == {"standard"} and catalog.context_assignments == {}
    standard = catalog.context_profiles["standard"]
    assert standard == {
        "version": 1, "enabled": False, "warning_at": 0.62, "threshold": 0.7, "unknown_window": "skip", "keep_recent": 1,
        "summary": {"first_level": "T1", "retry_level": "T2", "effort": "low", "model_check": True, "max_calls": 4,
                    "max_ratio": 0.5},
        "tasks": {"window_tokens": None},
    }
    assert catalog.context_profile_for("social_post") == (None, None) and catalog.context_profile_for_tasks() == (None, None)
    assert context_settings_for(catalog, "social_post") is None and context_settings_for_tasks(catalog) is None
    # Separate from routing: declaring it changed nothing there.
    assert "standard" not in catalog.routing_profiles and catalog.routing_assignments == {}


@pytest.mark.parametrize("catalog_dir", [
    ROOT / "examples" / "teams" / "software_development" / "catalog", ROOT / "examples" / "bundles" / "general" / "catalog"])
def test_catalogs_without_the_section_load_with_no_context_profiles(tmp_path, catalog_dir):
    catalog = Catalog(settings_for(tmp_path), catalog_dir=catalog_dir)
    assert catalog.context_profiles == {} and catalog.context_assignments == {}


def test_a_catalog_with_no_context_profiles_section_has_none(tmp_path):
    catalog = load(tmp_path)
    assert (catalog.context_profiles, catalog.context_assignments) == ({}, {})
    assert catalog.context_profile_for("w") == (None, None)


def test_profiles_are_normalized_and_assigned_by_workflow_or_to_tasks(tmp_path):
    catalog = load(tmp_path, {
        "profiles": {"lean": {"enabled": True, "threshold": 0.8, "summary": {"retry_level": None, "effort": "medium"}},
                     "off": {}},
        "assignments": {"social_post": "lean", "tasks": "off"},
    })
    profile_id, profile = catalog.context_profile_for("social_post")
    assert profile_id == "lean" and profile["enabled"] is True and profile["threshold"] == 0.8
    assert profile["warning_at"] == 0.62 and profile["summary"]["retry_level"] is None and profile["summary"]["effort"] == "medium"
    assert profile["summary"]["max_calls"] == 4                       # defaults filled in for what was left out
    assert catalog.context_profile_for("other_workflow") == (None, None)
    assert catalog.context_profile_for("tasks") == (None, None)       # `tasks` is reserved for task envelopes
    task_id, task_profile = catalog.context_profile_for_tasks()
    assert task_id == "off" and task_profile["enabled"] is False
    settings = context_settings_for(catalog, "social_post")
    assert settings.enabled and settings.profile_id == "lean" and context_settings_for_tasks(catalog).enabled is False


def test_the_catalog_returns_copies_of_its_profiles(tmp_path):
    catalog = load(tmp_path, {"profiles": {"p": {}}, "assignments": {"w": "p"}})
    _, profile = catalog.context_profile_for("w")
    profile["summary"]["max_calls"] = 99
    profile["enabled"] = True
    assert catalog.context_profile_for("w")[1]["summary"]["max_calls"] == 4 and catalog.context_profiles["p"]["enabled"] is False


def test_an_assignment_to_a_profile_that_does_not_exist_is_refused_so_a_typo_cannot_leave_it_off(tmp_path):
    with pytest.raises(CatalogError, match="assignments.w"):
        load(tmp_path, {"profiles": {"p": {}}, "assignments": {"w": "typo"}})
    # Mutating the assignments afterwards (tests do) degrades to "no profile", as routing does.
    catalog = load(tmp_path / "again", {"profiles": {"p": {}}})
    catalog.context_assignments["w"] = "gone"
    assert catalog.context_profile_for("w") == ("gone", None) and context_settings_for(catalog, "w") is None


@pytest.mark.parametrize("section, message", [
    ("nope", "mapping"),
    ({"profiles": {}, "extra": 1}, "unknown keys"),
    ({"profiles": []}, "profiles must be a mapping"),
    ({"profiles": {"p": "on"}}, "must be a mapping"),
    ({"profiles": {"p": {"colour": "red"}}}, "unknown keys"),
    ({"profiles": {"p": {"version": 2}}}, "version"),
    ({"profiles": {"p": {"enabled": "true"}}}, "enabled"),
    ({"profiles": {"p": {"warning_at": 0.8, "threshold": 0.7}}}, "warning_at < threshold"),
    ({"profiles": {"p": {"warning_at": 0}}}, "warning_at < threshold"),
    ({"profiles": {"p": {"threshold": 1}}}, "warning_at < threshold"),
    ({"profiles": {"p": {"threshold": "high"}}}, "threshold"),
    ({"profiles": {"p": {"threshold": True}}}, "threshold"),
    ({"profiles": {"p": {"unknown_window": "ignore"}}}, "unknown_window"),
    ({"profiles": {"p": {"keep_recent": 0}}}, "keep_recent"),
    ({"profiles": {"p": {"keep_recent": 1.5}}}, "keep_recent"),
    ({"profiles": {"p": {"summary": "short"}}}, "summary"),
    ({"profiles": {"p": {"summary": {"colour": 1}}}}, "summary has unknown keys"),
    ({"profiles": {"p": {"summary": {"first_level": "T3"}}}}, "first_level"),
    ({"profiles": {"p": {"summary": {"retry_level": "T3"}}}}, "retry_level"),
    ({"profiles": {"p": {"summary": {"first_level": "T2", "retry_level": "T1"}}}}, "above first_level"),
    ({"profiles": {"p": {"summary": {"effort": "extreme"}}}}, "effort"),
    ({"profiles": {"p": {"summary": {"model_check": "yes"}}}}, "model_check"),
    ({"profiles": {"p": {"summary": {"max_calls": 0}}}}, "max_calls"),
    ({"profiles": {"p": {"summary": {"max_ratio": 1}}}}, "max_ratio"),
    ({"profiles": {"p": {"tasks": {"window_tokens": 0}}}}, "tasks.window_tokens"),
    ({"profiles": {"p": {"tasks": {"other": 1}}}}, "tasks has unknown keys"),
    ({"profiles": {"p": {}}, "assignments": "all"}, "assignments"),
])
def test_a_malformed_context_profile_is_reported_when_the_catalog_loads(tmp_path, section, message):
    with pytest.raises(CatalogError, match=message):
        load(tmp_path, section)


def test_validation_is_idempotent_and_does_not_change_its_input():
    raw = {"enabled": True, "summary": {"max_calls": 2}}
    once = validate_context_profile("p", raw)
    assert raw == {"enabled": True, "summary": {"max_calls": 2}}
    assert validate_context_profile("p", once) == once


# --- the guard on its own ---------------------------------------------------------------------------------------


class Sink:
    def __init__(self):
        self.items: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, payload: dict) -> None:
        self.items.append((event_type, payload))

    @property
    def payloads(self) -> list[dict]:
        return [payload for _, payload in self.items]


def test_the_guard_is_an_observer_of_order_100_that_the_summarizer_may_skip():
    guard = ContextGuard(SETTINGS)
    assert (guard.name, guard.order, guard.observer, guard.skippable) == ("context", 100, True, True)


def test_below_the_warning_the_guard_says_nothing():
    sink = Sink()
    assert ContextGuard(SETTINGS, emit=sink).check(call(100, window=evidence(1000)), {}) is PROCEED
    assert sink.items == []


def test_the_warning_event_carries_what_the_contract_lists():
    sink = Sink()
    guard = ContextGuard(SETTINGS, emit=sink)
    assert guard.check(call(650, window=evidence(1000)), {}) is PROCEED
    ((event_type, payload),) = sink.items
    assert event_type == "context_warning"
    assert payload == {
        "scope": "run_step", "run_id": "r1", "step_id": "create", "attempt": 1, "ordinal": 1, "model_id": "m",
        "context_window": {"tokens": 1000, "source": "unit test documentation", "provenance": "provider_docs"},
        "estimated_tokens": 650, "ratio": 0.65, "warning_at": 0.62, "threshold": 0.7, "level": "warning",
        "compaction_pending": False, "estimator": "estimate_tokens_conservative",
    }


def test_crossing_the_threshold_marks_compaction_pending_and_still_lets_the_call_go():
    sink = Sink()
    verdict = ContextGuard(SETTINGS, emit=sink).check(call(900, window=evidence(1000)), {})
    assert verdict is PROCEED                                         # the step may finish; compaction comes after
    ((_, payload),) = sink.items
    assert (payload["level"], payload["compaction_pending"], payload["ratio"]) == ("threshold", True, 0.9)


def test_a_level_is_reported_once_per_step_attempt_and_model():
    sink = Sink()
    guard = ContextGuard(SETTINGS, emit=sink)
    window = evidence(1000)
    for ordinal in (1, 2, 3):                                         # retries and repeated checks of the same candidate
        guard.check(call(650, window=window, ordinal=ordinal, retry=ordinal - 1), {})
    assert len(sink.items) == 1
    guard.check(call(650, window=window, attempt=2), {})              # a new attempt of the step (a revision)
    guard.check(call(650, window=window, step_id="review"), {})       # another step
    guard.check(call(650, window=window, model_id="other"), {})       # another candidate, with its own window
    assert [(p["step_id"], p["attempt"], p["model_id"]) for p in sink.payloads] == [
        ("create", 1, "m"), ("create", 2, "m"), ("review", 1, "m"), ("create", 1, "other")]


def test_reaching_the_threshold_after_the_warning_is_reported_too():
    sink = Sink()
    guard = ContextGuard(SETTINGS, emit=sink)
    guard.check(call(650, window=evidence(1000)), {})
    guard.check(call(750, window=evidence(1000)), {})
    assert [p["level"] for p in sink.payloads] == ["warning", "threshold"]


def test_a_rebuilt_guard_does_not_repeat_what_the_events_of_the_run_already_say():
    first, second = Sink(), Sink()
    ContextGuard(SETTINGS, emit=first).check(call(650, window=evidence(1000)), {})
    state = {"events": [{"event_type": "context_warning", "payload": first.payloads[0]},
                        {"event_type": "step_started", "payload": {}}]}
    ContextGuard(SETTINGS, emit=second).check(call(650, window=evidence(1000)), state)
    assert second.items == []
    ContextGuard(SETTINGS, emit=second).check(call(650, window=evidence(1000), attempt=2), state)   # not seen yet
    assert len(second.items) == 1


def test_an_unknown_window_lets_the_call_go_with_one_warning_per_run_and_model():
    sink = Sink()
    guard = ContextGuard(SETTINGS, emit=sink)
    for step in ("create", "review", "create"):
        assert guard.check(call(10**6, step_id=step, attempt=3), {}) is PROCEED
    ((_, payload),) = sink.items
    assert payload["level"] == "window_unknown" and payload["context_window"] is None and payload["ratio"] is None
    assert payload["compaction_pending"] is False and payload["estimated_tokens"] == 10**6
    guard.check(call(10, model_id="other"), {})
    assert [p["model_id"] for p in sink.payloads] == ["m", "other"]
    state = {"events": [{"event_type": "context_warning", "payload": payload}]}
    again = Sink()
    ContextGuard(SETTINGS, emit=again).check(call(10), state)
    assert again.items == []


def test_unknown_window_block_stops_the_call_and_names_the_code():
    sink = Sink()
    guard = ContextGuard(ContextSettings(enabled=True, unknown_window="block", profile_id="strict"), emit=sink)
    for _ in range(2):
        verdict = guard.check(call(10), {})
        assert isinstance(verdict, Stop) and verdict.code == CODE_CONTEXT_WINDOW_UNKNOWN == "context_window_unknown"
        assert "'m'" in verdict.message and "strict" in verdict.message
    assert len(sink.items) == 1                                       # the stop is repeated, the warning is not
    assert guard.check(call(10, window=evidence(1000)), {}) is PROCEED


def test_the_task_scope_names_the_task_instead_of_the_run():
    sink = Sink()
    ContextGuard(SETTINGS, emit=sink).check(
        call(650, window=evidence(1000), scope="task", run_id=None, task_id="T-1", step_id=None), {})
    payload = sink.payloads[0]
    assert payload["scope"] == "task" and payload["task_id"] == "T-1" and "run_id" not in payload


def test_an_event_that_cannot_be_written_never_cuts_the_call_or_hides_a_block(caplog):
    def broken(event_type, payload):
        raise OSError("disk full")

    with caplog.at_level(logging.WARNING, logger="multiagent.context.estimate"):
        assert ContextGuard(SETTINGS, emit=broken).check(call(900, window=evidence(1000)), {}) is PROCEED
        blocking = ContextSettings(enabled=True, unknown_window="block")
        assert isinstance(ContextGuard(blocking, emit=broken).check(call(10), {}), Stop)
    assert any("context_warning" in record.getMessage() for record in caplog.records)


def test_without_an_event_sink_the_guard_still_decides():
    assert ContextGuard(SETTINGS).check(call(900, window=evidence(1000)), {}) is PROCEED
    assert isinstance(ContextGuard(ContextSettings(enabled=True, unknown_window="block")).check(call(1), {}), Stop)


def test_compaction_pending_is_read_from_the_events_of_the_step():
    sink = Sink()
    guard = ContextGuard(SETTINGS, emit=sink)
    guard.check(call(650, window=evidence(1000)), {})
    guard.check(call(900, window=evidence(1000), step_id="review", attempt=2), {})
    log = [{"event_type": "context_warning", "payload": p} for p in sink.payloads] + [
        {"event_type": "model_attempt", "payload": {"step_id": "create"}}]
    assert compaction_pending(log, step_id="create") is False            # only a warning
    assert compaction_pending(log, step_id="review") is True
    assert compaction_pending(log, step_id="review", attempt=2) is True
    assert compaction_pending(log, step_id="review", attempt=1) is False
    assert compaction_pending(log, step_id="nobody") is False and compaction_pending([], step_id="review") is False


# --- the engine -------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tokens(tmp_path_factory) -> int:
    """The estimated tokens of the ``create`` prompt of the test request."""
    return prompt_tokens(tmp_path_factory.mktemp("baseline"))


def window_for(tokens: int, ratio: float) -> int:
    return int(tokens / ratio)


def test_the_prompt_estimate_is_stable_so_the_bands_below_are_meaningful(tokens):
    assert tokens > 100


def test_without_an_assigned_profile_nothing_is_measured_even_over_the_window(tmp_path, tokens):
    system = make_system(tmp_path)
    set_window(system, "local_default", tokens // 2)                  # the prompt is twice the window
    run = get(system, start(system))
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert "context_warning" not in names(run)


def test_a_disabled_profile_measures_nothing_and_the_prompt_is_the_same(tmp_path, tokens):
    base, off = make_system(tmp_path / "base"), make_system(tmp_path / "off")
    set_window(off, "local_default", tokens // 2)
    assign_context_profile(off, enabled=False)
    base_id, off_id = start(base), start(off)
    assert "context_warning" not in names(get(off, off_id))
    assert off.ollama.prompts == base.ollama.prompts
    assert telemetry_prompt_hashes(off, off_id) == telemetry_prompt_hashes(base, base_id)


def test_a_profile_assigned_to_another_workflow_does_not_apply(tmp_path, tokens):
    system = make_system(tmp_path)
    set_window(system, "local_default", tokens // 2)
    assign_context_profile(system, workflow="some_other_workflow")
    assert "context_warning" not in names(get(system, start(system)))


def test_far_from_the_window_the_run_is_silent_and_identical(tmp_path, tokens):
    base, on = make_system(tmp_path / "base"), make_system(tmp_path / "on")
    set_window(on, "local_default", tokens * 10)
    assign_context_profile(on)
    base_id, on_id = start(base), start(on)
    run = get(on, on_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and "context_warning" not in names(run)
    assert names(run) == names(get(base, base_id))
    assert on.ollama.prompts == base.ollama.prompts and telemetry_prompt_hashes(on, on_id) == telemetry_prompt_hashes(base, base_id)


def test_the_warning_event_is_recorded_once_and_the_call_is_made(tmp_path, tokens):
    base, system = make_system(tmp_path / "base"), make_system(tmp_path / "run")
    set_window(system, "local_default", window_for(tokens, 0.655))
    assign_context_profile(system)
    base_id, run_id = start(base), start(system)
    run = get(system, run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review" and run["llm_calls"] == 1
    (warning,) = events(run, "context_warning")
    assert (warning["level"], warning["compaction_pending"], warning["model_id"]) == ("warning", False, "local_default")
    assert (warning["scope"], warning["run_id"], warning["step_id"], warning["attempt"], warning["ordinal"]) == (
        "run_step", run_id, "create", 1, 1)
    assert warning["estimated_tokens"] == tokens and warning["context_window"]["tokens"] == window_for(tokens, 0.655)
    assert warning["context_window"]["provenance"] == "provider_docs" and 0.62 <= warning["ratio"] < 0.70
    assert (warning["warning_at"], warning["threshold"], warning["estimator"]) == (0.62, 0.7, "estimate_tokens_conservative")
    assert compaction_pending(run["events"], step_id="create") is False
    # The event comes before the call, and the prompt sent is exactly what it was without the feature.
    order = names(run)
    assert order.index("context_warning") < order.index("model_attempt")
    assert system.ollama.prompts == base.ollama.prompts and telemetry_prompt_hashes(system, run_id) == telemetry_prompt_hashes(base, base_id)


def test_crossing_the_threshold_marks_compaction_pending_without_cutting_the_step(tmp_path, tokens):
    system = make_system(tmp_path)
    set_window(system, "local_default", window_for(tokens, 0.75))
    assign_context_profile(system)
    run = get(system, start(system))
    (warning,) = events(run, "context_warning")
    assert (warning["level"], warning["compaction_pending"]) == ("threshold", True)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    assert run["llm_calls"] == 1 and len(system.ollama.prompts) == 1 and "step_completed" in names(run)
    assert compaction_pending(run["events"], step_id="create") is True


def test_a_prompt_bigger_than_the_window_is_still_only_a_threshold_event(tmp_path, tokens):
    system = make_system(tmp_path)
    set_window(system, "local_default", tokens // 2)
    assign_context_profile(system)
    run = get(system, start(system))
    (warning,) = events(run, "context_warning")
    assert warning["level"] == "threshold" and warning["ratio"] > 1
    assert run["status"] == RunStatus.WAITING_HUMAN.value and len(system.ollama.prompts) == 1


def test_a_new_attempt_of_the_step_is_measured_and_reported_again(tmp_path, tokens):
    system = make_system(tmp_path)
    set_window(system, "local_default", window_for(tokens, 0.655))
    assign_context_profile(system)
    run_id = start(system)
    system.run_service.changes(run_id, "Make it shorter")
    system.worker.process_once()
    run = get(system, run_id)
    first, second = events(run, "context_warning")
    # The revision feedback makes the second prompt a little bigger, so it may reach a higher level.
    assert (first["attempt"], first["level"], second["attempt"]) == (1, "warning", 2)
    assert second["estimated_tokens"] > first["estimated_tokens"] and second["level"] in ("warning", "threshold")


def test_each_candidate_is_measured_against_its_own_window(tmp_path, tokens):
    system = make_system(tmp_path, ollama=Provider("ollama", [ProviderUnavailable("down")]))
    set_window(system, "local_default", window_for(tokens, 0.655))     # the preferred model: warning
    set_window(system, "cloud_fast", window_for(tokens, 0.75))         # the fallback: threshold
    assign_context_profile(system)
    run = get(system, start(system))
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["llm_calls"] == 2
    assert [(w["model_id"], w["level"], w["ordinal"], w["compaction_pending"]) for w in events(run, "context_warning")] == [
        ("local_default", "warning", 1, False), ("cloud_fast", "threshold", 2, True)]
    assert compaction_pending(run["events"], step_id="create") is True


def test_a_roomy_fallback_is_not_reported_because_of_the_preferred_model(tmp_path, tokens):
    system = make_system(tmp_path, ollama=Provider("ollama", [ProviderUnavailable("down")]))
    set_window(system, "local_default", window_for(tokens, 0.655))
    set_window(system, "cloud_fast", tokens * 20)
    assign_context_profile(system)
    run = get(system, start(system))
    assert [w["model_id"] for w in events(run, "context_warning")] == ["local_default"]


def test_an_unknown_window_skips_with_one_warning_per_model_and_the_step_goes_on(tmp_path, tokens):
    system = make_system(tmp_path, ollama=Provider("ollama", [ProviderUnavailable("down")]))
    set_window(system, "local_default", None)
    set_window(system, "cloud_fast", None)
    assign_context_profile(system)
    run_id = start(system)
    system.run_service.changes(run_id, "Make it shorter")                  # a second attempt must not repeat them
    system.worker.process_once()
    run = get(system, run_id)
    assert run["status"] == RunStatus.WAITING_HUMAN.value and run["waiting_reason"] == "review"
    unknown = events(run, "context_warning")
    assert [(w["model_id"], w["level"], w["context_window"], w["ratio"], w["compaction_pending"]) for w in unknown] == [
        ("local_default", "window_unknown", None, None, False), ("cloud_fast", "window_unknown", None, None, False)]
    assert not compaction_pending(run["events"], step_id="create")


def test_unknown_window_block_stops_the_run_before_the_provider(tmp_path):
    system = make_system(tmp_path)
    set_window(system, "local_default", None)
    assign_context_profile(system, unknown_window="block")
    run = get(system, start(system))
    assert run["status"] == RunStatus.FAILED.value
    assert "'local_default'" in run["error"] and "unknown_window: block" in run["error"]
    assert system.ollama.prompts == [] and system.gemini.prompts == [] and run["llm_calls"] == 0
    assert [w["level"] for w in events(run, "context_warning")] == ["window_unknown"]


def test_the_bundled_local_model_is_measured_against_its_num_ctx(tmp_path, tokens):
    # No window is set by the test: local_default's window is the 8192-token num_ctx the catalog derives.
    system = make_system(tmp_path)
    assert system.catalog.context_window("local_default")["tokens"] == 8192
    assign_context_profile(system, warning_at=0.08, threshold=0.1)     # the prompt is ~13 % of 8192
    run = get(system, start(system))
    (warning,) = events(run, "context_warning")
    assert warning["level"] == "threshold" and warning["context_window"]["provenance"] == "runtime_setting"


def test_a_dry_run_is_never_measured(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", db_path=tmp_path / "data" / "multiagent.db",
                        runs_dir=tmp_path / "data" / "runs", assets_dir=tmp_path / "data" / "assets",
                        worker_enabled=False, gemini_api_key="")
    system = build_system(settings, dry_run=True)
    assign_context_profile(system, unknown_window="block")
    run = system.run_service.create_social_post(
        SocialPostRequest(project_name="cmp-t02", objective="Create an educational post", topic="KNX"))
    system.worker.process_once()
    state = system.run_service.get(run["id"])
    assert state["status"] == RunStatus.WAITING_HUMAN.value and "context_warning" not in names(state)
