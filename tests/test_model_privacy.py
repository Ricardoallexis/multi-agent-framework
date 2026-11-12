# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""MOD-T04: model catalog privacy fields and the privacy check before a model receives a task.

Everything here is synthetic: temporary catalogs, fake models, fake programs. No network, no real CLI, no credentials.
The negative cases are the point: each one must fail if the guard is changed to fail open.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from multiagent.catalog import CatalogError, ModelSpec
from multiagent.executors import copilot as copilot_module, process
from multiagent.executors.contract import ExecutorLaunchRequest
from multiagent.executors.copilot import CopilotAdapter, CopilotAdapterError
from multiagent.executors.privacy import (
    DATA_CLASS_NOT_ALLOWED, PRIVACY_CLASS_UNKNOWN, PROVIDER_REJECTED, ModelPrivacy, combine, decide_privacy,
    resolve_model_privacy,
)
from multiagent.routing.executor_capabilities import SupportState
from multiagent.services import ServiceError
from multiagent.tasks.envelopes import build_task_envelope

import test_context_handoff as ctx
import test_quota_handoff as handoff_world
import test_quota_hold as quota
from multiagent.routing.executor_capabilities import VerifiedOperatorContext
from test_catalog_levels import load, model
from test_executor_services import (  # noqa: F401  (the fixtures are used by name)
    event_types, make_world, never_a_real_cli, refused, world,
)

SYNTHETIC = "synthetic-cloud-model"


def evidence(*names, model=None):
    """Catalog evidence for the named fields (synthetic): a host or retention counts only with it (contract section 2)."""
    return {name: {"source": "synthetic terms page", "provenance": "provider_docs", "observed_at": "2026-10-27",
                   **({"model": model} if model else {})} for name in names}


# --------------------------------------------------------------------------- the catalog

def test_legacy_catalog_without_privacy_fields_still_loads(tmp_path):
    spec = load(tmp_path, {"plain": model(allowed_data_classes=["non_sensitive"])}).model("plain")
    assert spec.host == "unknown" and spec.retention == {"status": "unknown", "period_days": None}
    assert spec.evidence is None
    assert spec.allowed_data_classes == frozenset({"non_sensitive"})        # the existing classes are untouched


def test_declared_host_retention_and_evidence_are_kept(tmp_path):
    spec = load(tmp_path, {"m": model(
        host="provider_hosted", retention={"status": "limited", "period_days": 30},
        evidence={"retention": {"source": "synthetic terms page", "provenance": "provider_docs",
                                "observed_at": "2026-10-27", "scope": "model", "model": "synthetic-model-1",
                                "note": "synthetic caveat"}})}).model("m")
    assert spec.host == "provider_hosted" and spec.retention == {"status": "limited", "period_days": 30}
    proof = spec.evidence["retention"]
    assert proof.status is SupportState.SUPPORTED and proof.observed_at == "2026-10-27"
    assert proof.model == "synthetic-model-1" and proof.note == "synthetic caveat"


@pytest.mark.parametrize("changes", [
    {"host": "somewhere"},
    {"host": 3},
    {"retention": {"status": "forever"}},
    {"retention": {"status": "limited", "period_days": -1}},
    {"retention": {"status": "limited", "period_days": "30"}},
    {"retention": {"status": "limited", "days": 3}},
    {"retention": "limited"},
    {"evidence": {"host": {"source": "x", "provenance": "provider_docs", "observed_at": "27/10/2026"}}},
    {"evidence": {"host": {"source": "x", "provenance": "hearsay", "observed_at": "2026-10-27"}}},
    {"evidence": {"host": {"provenance": "provider_docs", "observed_at": "2026-10-27"}}},
    {"evidence": {"cost": {"source": "x", "provenance": "provider_docs", "observed_at": "2026-10-27"}}},
    {"evidence": ["host"]},
])
def test_invalid_host_retention_and_evidence_are_rejected(tmp_path, changes):
    with pytest.raises(CatalogError):
        load(tmp_path, {"bad": model(**changes)})


def test_the_bundled_catalog_declares_no_host_or_retention_without_evidence(tmp_path):
    from multiagent.catalog import Catalog
    from multiagent.config import Settings
    catalog = Catalog(Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
                               gemini_api_key="", openai_api_key="", openai_enabled=False))
    assert catalog.models
    for spec in catalog.models.values():
        assert spec.host == "unknown" and spec.retention["status"] == "unknown" and not spec.evidence
        assert spec.allowed_data_classes == frozenset({"non_sensitive"})    # production classes are not widened


# --------------------------------------------------------------------------- the pure decision

HOSTS = ("local", "organization", "provider_hosted", "third_party", "unknown")


def privacy(host="unknown", retention="unknown", allowed=("non_sensitive", "sensitive", "restricted"), profile=None,
            rejected=False):
    return ModelPrivacy(cataloged=True, provider="fake", host=host, retention_status=retention,
                        allowed_data_classes=allowed, profile_data_classes=profile, provider_rejected=rejected)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("data_class", ["non_sensitive", "sensitive"])
def test_ordinary_classes_follow_allowed_data_classes_whatever_the_host(host, data_class):
    assert decide_privacy(data_class, privacy(host, allowed=("non_sensitive", "sensitive"))).allowed
    other = "sensitive" if data_class == "non_sensitive" else "non_sensitive"
    denied = decide_privacy(data_class, privacy(host, allowed=(other,)))
    assert not denied.allowed and denied.reason == DATA_CLASS_NOT_ALLOWED


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("retention", ["none", "zero_retention", "limited", "provider_default", "unknown"])
def test_restricted_is_allowed_only_on_a_local_host_with_known_retention(host, retention):
    decision = decide_privacy("restricted", privacy(host, retention))
    assert decision.allowed is (host == "local" and retention != "unknown")
    if not decision.allowed:
        assert decision.reason == DATA_CLASS_NOT_ALLOWED and decision.stop and decision.level == 0


def test_a_missing_class_is_unknown_when_the_catalog_or_the_profile_restrict_the_model():
    for restricted in (privacy("local", "none", allowed=("non_sensitive",)),
                       privacy("local", "none", allowed=None, profile=("non_sensitive",))):
        decision = decide_privacy(None, restricted)
        assert not decision.allowed and decision.reason == PRIVACY_CLASS_UNKNOWN


@pytest.mark.parametrize("bad", ["secret", "", "Restricted", 7, ["restricted"]])
def test_a_class_outside_the_vocabulary_is_unknown(bad):
    for described in (privacy("local", "none"), ModelPrivacy(), None):
        decision = decide_privacy(bad, described)
        assert not decision.allowed and decision.reason == PRIVACY_CLASS_UNKNOWN


def test_a_missing_class_passes_for_an_undescribed_model_but_a_known_class_needs_a_declared_list():
    legacy = decide_privacy(None, ModelPrivacy())
    assert legacy.allowed and "data_class_unresolved" in legacy.warnings
    # the most unknown model of all still warns about its host and retention
    assert {"model_host_unknown", "model_retention_unknown"} <= set(legacy.warnings)
    for known in ("non_sensitive", "sensitive", "restricted"):
        for described in (None, ModelPrivacy(), ModelPrivacy(cataloged=True, host="local", retention_status="none")):
            denied = decide_privacy(known, described)
            assert not denied.allowed and denied.reason == PRIVACY_CLASS_UNKNOWN, (known, described)
    # a list in the profile alone is enough to authorize an ordinary class
    assert decide_privacy("sensitive", ModelPrivacy(profile_data_classes=("sensitive",))).allowed


def test_unknown_host_or_retention_blocks_restricted_but_not_the_other_classes():
    for host, retention in (("unknown", "none"), ("local", "unknown"), ("unknown", "unknown")):
        described = privacy(host, retention)
        assert not decide_privacy("restricted", described).allowed
        for ordinary in ("non_sensitive", "sensitive"):
            decision = decide_privacy(ordinary, described)
            assert decision.allowed, (host, retention, ordinary)       # no new limit: allowed_data_classes decides
        warned = decide_privacy("sensitive", described).warnings
        assert ("model_host_unknown" in warned) is (host == "unknown")
        assert ("model_retention_unknown" in warned) is (retention == "unknown")


def test_restricted_never_reaches_an_external_provider_even_when_profile_and_model_allow_it():
    for host in ("organization", "provider_hosted", "third_party", "unknown"):
        decision = decide_privacy("restricted", privacy(host, "none", allowed=("restricted", "sensitive"),
                                                        profile=("restricted", "sensitive", "non_sensitive")))
        assert not decision.allowed and decision.stop and decision.reason == DATA_CLASS_NOT_ALLOWED
    # and a profile that does not list the class stops even a local model
    assert decide_privacy("restricted", privacy("local", "none", profile=("non_sensitive",))).reason == DATA_CLASS_NOT_ALLOWED


def test_a_rejected_provider_stops_before_any_class_is_looked_at():
    for data_class in (None, "non_sensitive", "sensitive", "restricted", "nonsense"):
        decision = decide_privacy(data_class, privacy("local", "none", rejected=True))
        assert not decision.allowed and decision.reason == PROVIDER_REJECTED
    assert decision.to_dict()["action"] == "stop" and decision.to_dict()["level"] == 0


def test_combining_entries_that_share_a_model_name_takes_the_strictest_reading():
    merged = combine([privacy("local", "none", allowed=("non_sensitive", "sensitive")),
                      privacy("third_party", "none", allowed=("non_sensitive",))])
    assert merged.host == "unknown" and merged.allowed_data_classes == frozenset({"non_sensitive"})
    assert not decide_privacy("sensitive", merged).allowed
    assert combine([]).cataloged is False


def test_resolve_model_privacy_reads_the_catalog_by_id_then_by_model_name(tmp_path):
    catalog = load(tmp_path, {"first": model(model="shared-name", host="local", retention={"status": "none"},
                                             evidence=evidence("host", "retention")),
                              "second": model(model="shared-name", host="third_party", allowed_data_classes=["sensitive"],
                                              evidence=evidence("host"))})
    by_id = resolve_model_privacy(catalog.models, "first")
    assert by_id.cataloged and by_id.host == "local" and by_id.retention_status == "none"
    by_name = resolve_model_privacy(catalog.models, "shared-name")
    assert by_name.host == "unknown" and by_name.allowed_data_classes == frozenset()      # intersection of the two
    assert not resolve_model_privacy(catalog.models, "nobody").cataloged
    assert not resolve_model_privacy(catalog.models, None).cataloged


# --------------------------------------------------------------------------- check 1: the service

def declare(world, *, allowed=("non_sensitive",), host="provider_hosted", retention="provider_default"):
    spec = ModelSpec(id="synthetic_cloud", provider="fake", model=SYNTHETIC, tier="cloud", structured_output=True,
                     web=False, allowed_data_classes=frozenset(allowed), host=host,
                     retention={"status": retention, "period_days": None}, evidence=evidence("host", "retention"))
    world.services.system.catalog.models["synthetic_cloud"] = spec
    return spec


def classify(world, data_class, scope="task:T1"):
    world.services.system.catalog.executor_routing["data_classifications"][scope] = data_class


class Counter:
    """Replaces the program and the pre-flight of the service so a test can see that neither was reached."""

    def __init__(self, world, monkeypatch):
        self.preflight, self.launches = 0, 0
        service = world.services.executors
        original = service._preflight
        adapter = world.adapters["claude"]

        def preflight(*args, **kwargs):
            self.preflight += 1
            return original(*args, **kwargs)

        def launch(request):
            self.launches += 1
            raise AssertionError("the adapter must not be reached")

        monkeypatch.setattr(service, "_preflight", preflight)
        monkeypatch.setattr(adapter, "launch", launch)
        monkeypatch.setattr(adapter, "resume", launch)


def untouched(world, counter, task_id="T1"):
    folder = world.services.settings.executor_workdir_root / f"task-{task_id}"
    assert counter.preflight == 0 and counter.launches == 0 and world.fake.calls() == []
    assert not folder.exists()
    assert world.services.tasks.get_task(task_id)["state"] == "open"
    assert event_types(world.services, task_id) == ["task.created"]


@pytest.mark.parametrize(("data_class", "allowed", "reason"), [
    ("sensitive", ("non_sensitive",), DATA_CLASS_NOT_ALLOWED),
    ("restricted", ("non_sensitive", "sensitive", "restricted"), DATA_CLASS_NOT_ALLOWED),    # provider_hosted host
    (None, ("non_sensitive",), PRIVACY_CLASS_UNKNOWN),
])
def test_run_task_privacy_rejection_precedes_preflight_and_launch(world, monkeypatch, data_class, allowed, reason):
    declare(world, allowed=allowed)
    if data_class is not None:
        classify(world, data_class)
    counter = Counter(world, monkeypatch)

    error = refused(lambda: world.services.executors.run_task("T1", "claude_cli", SYNTHETIC), reason=reason)

    assert error.details["action"] == "stop" and error.details["level"] == 0
    assert str(world.root) not in error.message and "Summarize" not in error.message
    untouched(world, counter)


def test_sources_that_contradict_each_other_give_no_class(world, monkeypatch):
    declare(world, allowed=("non_sensitive", "sensitive"))
    classify(world, "non_sensitive", "task:T1")
    classify(world, "sensitive", "group:ORQ")
    counter = Counter(world, monkeypatch)
    refused(lambda: world.services.executors.run_task("T1", "claude_cli", SYNTHETIC), reason=PRIVACY_CLASS_UNKNOWN)
    untouched(world, counter)


def test_a_model_the_executor_does_not_list_is_a_rejected_provider_and_an_adapter_refusal_keeps_its_own_code(tmp_path):
    from multiagent.executors.registry import ExecutorRegistry
    spec = ModelSpec(id="synthetic_unlisted", provider="fake", model="synthetic-unlisted-1", tier="cloud",
                     structured_output=True, web=False, allowed_data_classes=frozenset({"non_sensitive"}), host="local",
                     retention={"status": "none", "period_days": None})
    config = {"version": 1, "profiles": {}, "requirement_map": {}, "operator_boundary": {}, "executors": {
        "listing": {"surface": "copilot_cli", "adapter_id": "copilot_cli", "enabled": True, "mode": "automatic",
                    "model_support": {"status": "supported", "source": "synthetic", "provenance": "operator_test",
                                      "scope": "listing", "observed_at": "2026-10-10", "values": ["gpt-synthetic"]}}}}
    descriptor = ExecutorRegistry(config, []).get("listing")
    assert descriptor.model_support.status is SupportState.SUPPORTED
    described = resolve_model_privacy({"synthetic_unlisted": spec}, "synthetic_unlisted", descriptor=descriptor)
    assert described.provider_rejected
    assert decide_privacy("non_sensitive", described).reason == PROVIDER_REJECTED
    assert not resolve_model_privacy({"synthetic_unlisted": spec}, "synthetic_unlisted").provider_rejected   # no evidence
    # Copilot's own refusal of a Claude model is the adapter's (claude_model_rejected), not counted as a provider here.
    claude = ModelSpec(id="synthetic_refused", provider="fake", model="claude-synthetic-1", tier="cloud",
                       structured_output=True, web=False)
    assert not resolve_model_privacy({"synthetic_refused": claude}, "claude-synthetic-1").provider_rejected


def test_an_allowed_class_reaches_the_adapter_and_the_request_carries_what_was_resolved(world, monkeypatch):
    declare(world, allowed=("non_sensitive",))
    classify(world, "non_sensitive")
    seen = []
    adapter = world.adapters["claude"]
    original = adapter.launch
    monkeypatch.setattr(adapter, "launch", lambda request: seen.append(request) or original(request))

    out = world.services.executors.run_task("T1", "claude_cli", SYNTHETIC)

    assert out["outcome"] == "submitted"
    (request,) = seen
    assert request.data_class == "non_sensitive" and request.privacy.host == "provider_hosted"
    assert request.privacy.allowed_data_classes == frozenset({"non_sensitive"})


def test_a_legacy_run_with_no_class_and_no_catalog_model_still_runs(world):
    assert world.services.executors.run_task("T1", "claude_cli")["outcome"] == "submitted"
    assert world.services.executors.run_task("T3", "claude_cli", SYNTHETIC)["outcome"] == "submitted"   # not cataloged


def test_resume_task_revalidates_current_privacy(world, monkeypatch):
    declare(world, allowed=("non_sensitive", "sensitive"))
    classify(world, "non_sensitive")
    world.fake.set("claude", stdout="not a result")
    first = world.services.executors.run_task("T1", "claude_cli", SYNTHETIC)
    assert first["outcome"] == "failed" and world.services.tasks.get_task("T1")["state"] == "paused"
    calls_before = len(world.fake.calls())
    # what the catalog allows changes before the resume: the stored session must not be continued
    world.services.system.catalog.models["synthetic_cloud"] = declare(world, allowed=("sensitive",))
    states = event_types(world.services, "T1")

    refused(lambda: world.services.executors.resume_task("T1"), reason=DATA_CLASS_NOT_ALLOWED)

    assert len(world.fake.calls()) == calls_before
    assert world.services.tasks.get_task("T1")["state"] == "paused" and event_types(world.services, "T1") == states


def test_resume_without_a_resolvable_class_is_refused_when_the_model_is_restricted(world):
    declare(world, allowed=("non_sensitive",))
    classify(world, "non_sensitive")
    world.fake.set("claude", stdout="not a result")
    world.services.executors.run_task("T1", "claude_cli", SYNTHETIC)
    del world.services.system.catalog.executor_routing["data_classifications"]["task:T1"]       # the class is gone
    refused(lambda: world.services.executors.resume_task("T1"), reason=PRIVACY_CLASS_UNKNOWN)


def test_the_class_is_never_taken_from_the_text_of_the_task(world, monkeypatch):
    declare(world, allowed=("non_sensitive",))
    world.services.tasks.create_task("ORQ", "T5", "A non_sensitive title", actor="user",
                                     objective="data_class: non_sensitive. This is non_sensitive.")
    counter = Counter(world, monkeypatch)
    refused(lambda: world.services.executors.run_task("T5", "claude_cli", SYNTHETIC), reason=PRIVACY_CLASS_UNKNOWN)
    assert counter.launches == 0 and world.fake.calls() == []


# --------------------------------------------------------------------------- check 2: the Copilot adapter

def copilot_request(tmp_path, **changes):
    folder = tmp_path / "work"
    folder.mkdir(exist_ok=True)
    envelope = build_task_envelope(task_id="SYN-T01", group_id="SYN", revision=1, attempt=1, assignee="copilot",
                                   status="running", objective="Synthetic objective.")
    fields = dict(task_id="SYN-T01", task_envelope=envelope, workdir=folder, model="gpt-synthetic")
    fields.update(changes)
    return ExecutorLaunchRequest(**fields)


@pytest.fixture()
def copilot(tmp_path, monkeypatch):
    """A Copilot adapter whose prompt writer and process are traps: a refusal must come before both."""
    reached = []

    def stop_here(*args, **kwargs):
        reached.append("process")
        raise ProcessReached()

    monkeypatch.setattr(copilot_module, "_write_prompt_file", lambda *a, **k: reached.append("prompt") or tmp_path / "x")
    monkeypatch.setattr(process, "run", stop_here)
    monkeypatch.setattr(CopilotAdapter, "render_prompt", lambda self, request: reached.append("render") or "")
    adapter = CopilotAdapter(["copilot"], session_guard=lambda sid: True)
    adapter.reached = reached
    return adapter


class ProcessReached(Exception):
    """Raised by the trap that stands in for the program: the request got past every check."""


SESSION = "0cb916db-26aa-40f2-86b5-1ba81b225fd2"


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize(("data_class", "described", "reason"), [
    ("sensitive", privacy("provider_hosted", "none", allowed=("non_sensitive",)), DATA_CLASS_NOT_ALLOWED),
    ("restricted", privacy("provider_hosted", "none"), DATA_CLASS_NOT_ALLOWED),
    ("restricted", privacy("unknown", "unknown"), DATA_CLASS_NOT_ALLOWED),
    (None, privacy("local", "none", allowed=("non_sensitive",)), PRIVACY_CLASS_UNKNOWN),
    ("non_sensitive", privacy("local", "none", rejected=True), PROVIDER_REJECTED),
])
def test_copilot_rejects_provider_or_class_before_prompt_or_process(tmp_path, copilot, resume, data_class, described, reason):
    request = copilot_request(tmp_path, data_class=data_class, privacy=described)
    call = (lambda: copilot.resume(request, SESSION)) if resume else (lambda: copilot.launch(request))
    with pytest.raises(CopilotAdapterError) as caught:
        call()
    assert caught.value.code == reason
    assert copilot.reached == []                                         # no prompt rendered, written or process started
    assert "Synthetic objective" not in str(caught.value) and str(tmp_path) not in str(caught.value)
    assert not list((tmp_path / "work").iterdir())


def test_copilot_keeps_the_claude_rejection_ahead_of_the_privacy_check(tmp_path, copilot):
    request = copilot_request(tmp_path, model="claude-sonnet-5.5", data_class="non_sensitive",
                              privacy=privacy("local", "none"))
    with pytest.raises(CopilotAdapterError) as caught:
        copilot.launch(request)
    assert caught.value.code == "claude_model_rejected" and copilot.reached == []
    legacy = copilot_request(tmp_path, model="claude-sonnet-5.5")            # a legacy request: the old guard still stands
    with pytest.raises(CopilotAdapterError) as caught:
        copilot.launch(legacy)
    assert caught.value.code == "claude_model_rejected" and copilot.reached == []


def test_copilot_lets_an_allowed_request_through_to_the_prompt_stage(tmp_path, copilot):
    request = copilot_request(tmp_path, data_class="non_sensitive", privacy=privacy("local", "none"))
    with pytest.raises(ProcessReached):
        copilot.launch(request)
    assert copilot.reached == ["render", "prompt", "process"]


def test_a_legacy_request_without_privacy_fields_is_not_checked_by_the_adapter(tmp_path, copilot):
    with pytest.raises(ProcessReached):
        copilot.launch(copilot_request(tmp_path))
    assert copilot.reached == ["render", "prompt", "process"]


# --------------------------------------------------------------------------- B1: a known class needs a declared list

@pytest.mark.parametrize("executor", ["claude_cli", "copilot_cli"])
@pytest.mark.parametrize("model_name", [None, "gpt-6-luna"])
def test_a_sensitive_task_does_not_reach_a_model_the_catalog_does_not_declare(world, monkeypatch, executor, model_name):
    classify(world, "sensitive")                       # the operator classified it; nobody authorized that class for a model
    launched = []
    for adapter in world.adapters.values():
        monkeypatch.setattr(adapter, "launch", lambda request: launched.append(request))
    error = refused(lambda: world.services.executors.run_task("T1", executor, model_name), reason=PRIVACY_CLASS_UNKNOWN)
    assert error.details["action"] == "stop"
    assert launched == [] and world.fake.calls() == [] and event_types(world.services, "T1") == ["task.created"]


def test_the_same_run_without_a_class_still_goes_through_and_says_so_at_warning_level(world, caplog):
    with caplog.at_level("WARNING", logger="multiagent.services.executors"):
        out = world.services.executors.run_task("T1", "claude_cli")
    assert out["outcome"] == "submitted"
    text = " ".join(record.getMessage() for record in caplog.records if record.levelname == "WARNING")
    for code in ("data_class_unresolved", "model_host_unknown", "model_retention_unknown"):
        assert code in text                              # M1: visible, also for a model the catalog does not describe


def test_the_decision_rides_on_the_launch_request_for_a_later_evaluation(world, monkeypatch):
    seen = []
    adapter = world.adapters["claude"]
    original = adapter.launch
    monkeypatch.setattr(adapter, "launch", lambda request: seen.append(request) or original(request))
    world.services.executors.run_task("T1", "claude_cli")
    (request,) = seen
    assert request.privacy_decision["allowed"] is True and request.privacy_decision["action"] == "continue"
    assert "data_class_unresolved" in request.privacy_decision["warnings"]


# --------------------------------------------------------------------------- M5: host and retention need evidence

def test_a_host_or_retention_without_evidence_reads_as_unknown_and_never_opens_restricted(tmp_path):
    def privacy_of(**changes):
        spec = load(tmp_path, {"m": model(host="local", retention={"status": "none"}, allowed_data_classes=["restricted"],
                                          **changes)}).model("m")
        return ModelPrivacy.from_spec(spec)
    bare = privacy_of()
    assert bare.host == "unknown" and bare.retention_status == "unknown"
    assert decide_privacy("restricted", bare).reason == DATA_CLASS_NOT_ALLOWED
    half = privacy_of(evidence=evidence("host"))
    assert half.host == "local" and half.retention_status == "unknown"
    assert not decide_privacy("restricted", half).allowed
    stale = privacy_of(evidence=evidence("host", "retention", model="another-model-name"))
    assert stale.host == "unknown" and stale.retention_status == "unknown"          # evidence for another model
    proven = privacy_of(evidence=evidence("host", "retention", model="m"))
    assert proven.host == "local" and proven.retention_status == "none"
    assert decide_privacy("restricted", proven).allowed


# --------------------------------------------------------------------------- M4: profile, operator context, conflicts

def test_the_gate_reads_the_classes_of_the_profile_of_the_plan(world):
    spec = declare(world, allowed=("non_sensitive", "sensitive"))
    classify(world, "sensitive")
    world.services.system.catalog.executor_routing["profiles"]["auto"]["allowed_data_classes"] = ["non_sensitive"]
    descriptor = world.services.assignment.registry.get("claude_cli")
    task = world.services.tasks.get_task("T1")
    service = world.services.executors
    plan = SimpleNamespace(executor_id="claude_cli", descriptor=descriptor, model=spec.model, selection=None,
                           decision={"profile_id": "auto"})
    with pytest.raises(ServiceError) as caught:
        service._privacy_gate(task, plan, None)
    assert caught.value.details["reason"] == DATA_CLASS_NOT_ALLOWED
    assert service._privacy_gate(task, SimpleNamespace(**{**vars(plan), "decision": None}), None)[0] == "sensitive"


def test_a_class_given_by_a_verified_operator_context_reaches_the_gate(world):
    declare(world, allowed=("non_sensitive", "sensitive"))
    context = VerifiedOperatorContext("ops", "agent:verified", grants=frozenset(), provenance="test", verified=True,
                                      trusted_data_class="sensitive", data_class_scope="task:T1")
    out = world.services.executors.run_task("T1", "claude_cli", SYNTHETIC, operator_context=context)
    assert out["outcome"] == "submitted"
    # without the context there is no class, and the model restricts classes
    refused(lambda: world.services.executors.run_task("T3", "claude_cli", SYNTHETIC), reason=PRIVACY_CLASS_UNKNOWN)
    # an unverified or wrongly scoped context gives nothing
    for task_id, bad in (("T2", VerifiedOperatorContext("ops", "agent:verified", verified=False,
                                                        trusted_data_class="sensitive", data_class_scope="task:T2")),
                         ("T4", VerifiedOperatorContext("ops", "agent:verified", verified=True,
                                                        trusted_data_class="sensitive", data_class_scope="task:OTHER"))):
        refused(lambda: world.services.executors.run_task(task_id, "claude_cli", SYNTHETIC, operator_context=bad),
                reason=PRIVACY_CLASS_UNKNOWN)


def test_contradicting_sources_stop_a_model_the_catalog_does_not_describe(world, monkeypatch):
    classify(world, "non_sensitive", "task:T1")
    classify(world, "sensitive", "group:ORQ")
    counter = Counter(world, monkeypatch)
    refused(lambda: world.services.executors.run_task("T1", "claude_cli"), reason=PRIVACY_CLASS_UNKNOWN)
    untouched(world, counter)


# --------------------------------------------------------------------------- M3: control 2 fails closed

def test_copilot_refuses_a_class_without_policy_and_a_policy_without_class_for_a_restricted_model(tmp_path, copilot):
    for data_class in ("non_sensitive", "sensitive", "restricted"):
        with pytest.raises(CopilotAdapterError) as caught:
            copilot.launch(copilot_request(tmp_path, data_class=data_class))              # a class, no policy at all
        assert caught.value.code == PRIVACY_CLASS_UNKNOWN
    with pytest.raises(CopilotAdapterError) as caught:
        copilot.launch(copilot_request(tmp_path, privacy=privacy("local", "none", allowed=("non_sensitive",))))
    assert caught.value.code == PRIVACY_CLASS_UNKNOWN and copilot.reached == []           # a policy, no class resolved


# --------------------------------------------------------------------------- M2: no irreversible change before a refusal

def events_of(services, kind, task_id="T1"):
    return [e for e in services.tasks.events(task_id=task_id) if e["type"] == kind]


def test_an_automatic_relaunch_refused_by_privacy_does_not_retire_the_held_session(tmp_path):
    world = quota.make_world(tmp_path, resume=False)             # no resume: the due hold is relaunched
    first = quota.hold_at_the_reset(world, model="fake-big")
    checkpoints = len(events_of(world.services, "task.checkpointed"))          # the limit itself saved one
    world.services.system.catalog.models["synthetic_hold"] = ModelSpec(
        id="synthetic_hold", provider="fake", model="fake-big", tier="cloud", structured_output=True, web=False,
        allowed_data_classes=frozenset({"non_sensitive"}))
    world.config.setdefault("data_classifications", {})["task:T1"] = "restricted"
    world.clock.to(quota.at(4, 14, 32))
    services = world.services

    done = services.executors.resume_due()

    assert done["resumed"] == [] and [item["why"] for item in done["skipped"]] == [DATA_CLASS_NOT_ALLOWED]
    assert events_of(services, "session.retired") == []
    assert len(events_of(services, "task.checkpointed")) == checkpoints
    assert services.executors.session_guard(first["session_id"]) is True
    assert world.hold() is not None and services.tasks.get_task("T1")["state"] == "paused"
    assert [call.kind for call in world.fake.calls] == ["launch"]


def test_a_hand_off_to_a_delegate_that_cannot_receive_the_task_commits_nothing(tmp_path):
    world = handoff_world.make_world(tmp_path)
    first = handoff_world.hit_limit(world)
    handoff_world.card_decides(world, "codex_cli")
    world.config["data_classifications"]["task:T1"] = "restricted"
    services = world.services
    before = services.tasks.get_task("T1")

    error = refused(lambda: services.executors.handoff_task("T1", "codex_cli", actor="user"), reason=PRIVACY_CLASS_UNKNOWN)

    assert error.details["action"] == "stop"
    after = services.tasks.get_task("T1")
    assert events_of(services, "session.retired") == [] and events_of(services, "continuity.handoff") == []
    assert services.executors.session_guard(first["session_id"]) is True
    assert after["assignee"] == before["assignee"] and after["state"] == "paused"
    assert after["continuity"].get("handoffs", 0) == 0 and after["continuity"]["hold"] is not None
    assert handoff_world.calls(world, "codex_cli") == []


def test_a_context_hand_off_refused_by_privacy_does_not_retire_the_session(world, monkeypatch):
    first = ctx.paused(world)
    ctx.measure(monkeypatch, world, ratio=.9)
    checkpoints = len(ctx.events(world, "task.checkpointed"))
    classify(world, "restricted")                               # the class changed while the task waited
    refused(lambda: world.services.executors.resume_task("T1"), reason=PRIVACY_CLASS_UNKNOWN)
    assert ctx.events(world, "session.retired") == [] and len(ctx.events(world, "task.checkpointed")) == checkpoints
    assert world.services.executors.session_guard(first["session_id"]) is True
    assert world.services.tasks.get_task("T1")["state"] == "paused"
