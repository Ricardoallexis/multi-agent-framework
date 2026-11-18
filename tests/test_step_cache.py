"""CAP-T13: result cache of a step. Synthetic data, no network; the model is the dry-run Mock (fake) adapter."""
import json

from multiagent.db.envelope_store import EnvelopeStore
from multiagent.document_actions import ActionKind
from multiagent.observability.usage import read_usage
from multiagent.step_cache import (ACTOR_PREFIX, EVENT_ERROR, EVENT_HIT, EVENT_STORED, CachedToolInvoker, StepCache,
                                   StepCachePolicy, model_step_key, tool_cacheable)
from multiagent.tasks.envelopes import envelope_sha256
from multiagent.tools import (EffectDecl, ImpactClass, InvocationKind, SchemaRef, ToolCallContext, ToolInvoker,
                              ToolOrigin, ToolRegistry, ToolRequest, ToolSpec, request_id_for)

from test_generic_runs import request, runtime

SENTINEL = "SENTINEL-cap-t13-content"
ON = StepCachePolicy(enabled=True)


# ---------------------------------------------------------------- model steps
def counting(system, monkeypatch):
    adapter = system.engine.router.adapters["fake"]
    real = adapter.generate_structured
    calls = []

    def counted(**kwargs):
        calls.append(kwargs["prompt"])
        return real(**kwargs)

    monkeypatch.setattr(adapter, "generate_structured", counted)
    return calls


def run_once(system, text="Users can reset a password by email; links expire after 30 minutes."):
    run = system.service.create_run(request(inputs={"spec_text": text}))
    return system.engine.process_run(run["id"])["id"]


def events(system, run_id, kind=None):
    rows = system.store.get_run(run_id)["events"]
    return [row for row in rows if kind is None or row["event_type"] == kind]


def payload(row):
    value = row.get("payload", row.get("payload_json"))
    return json.loads(value) if isinstance(value, str) else value


def test_repeating_a_step_uses_the_cache_without_calling_the_model_and_records_no_usage(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)
    first = run_once(system)
    assert len(calls) == 2                        # extract_requirements, then assess_risks (checkpoint, never cached)
    second = run_once(system)
    assert len(calls) == 3                        # only assess_risks ran again: extract_requirements came from the cache
    hits = [payload(row) for row in events(system, second, EVENT_HIT)]
    assert len(hits) == 1 and hits[0]["step_id"] == "extract_requirements" and len(hits[0]["key"]) == 64
    assert hits[0]["blob_ref"].startswith("blob:sha256:")
    assert hits[0]["usage"] == {"tokens_in": 0, "tokens_out": 0, "estimated_cost_usd": 0.0, "cached": True}
    assert [row["step_id"] for row in read_usage(system.store, second)] == ["assess_risks"]   # no row for the hit
    assert system.store.get_run(second)["llm_calls"] == 1                                     # not counted as a call
    assert [payload(row)["step_id"] for row in events(system, first, EVENT_STORED)] == ["extract_requirements"]
    done = [payload(row) for row in events(system, second, "step_completed")]
    assert done[0]["step_id"] == "extract_requirements" and done[0]["cached"] is True
    assert system.store.latest_completed_step(second, "extract_requirements") == \
        system.store.latest_completed_step(first, "extract_requirements")


def test_other_inputs_miss_the_cache(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)
    run_once(system)
    second = run_once(system, "A different specification text.")
    assert len(calls) == 4 and not events(system, second, EVENT_HIT)


def test_off_by_default_changes_nothing(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    assert system.engine.step_cache_policy == StepCachePolicy() and not system.engine.step_cache_policy.enabled
    calls = counting(system, monkeypatch)
    run_once(system)
    second = run_once(system)
    assert len(calls) == 4
    kinds = {row["event_type"] for row in events(system, second)}
    assert not kinds & {EVENT_HIT, EVENT_STORED, EVENT_ERROR}
    system.engine.step_cache_policy = StepCachePolicy(enabled=False)          # an explicit off is also off
    run_once(system)
    assert len(calls) == 6
    assert not StepCachePolicy(enabled=True, step_ids=frozenset({"other"})).allows("extract_requirements")


def test_a_step_that_waits_for_approval_is_never_cached(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    run_once(system)
    stored = {payload(row)["step_id"] for row in events(system, run_once(system), EVENT_STORED)}
    assert stored == set()                                                    # second run: hit, nothing new stored
    first = run_once(system, "Another text.")
    assert {payload(row)["step_id"] for row in events(system, first, EVENT_STORED)} == {"extract_requirements"}


def test_cache_failures_do_not_break_the_run(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)
    monkeypatch.setattr(EnvelopeStore, "put_blob", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    first = run_once(system)
    assert system.store.get_run(first)["status"] == "waiting_human" and len(calls) == 2
    assert [payload(row)["stage"] for row in events(system, first, EVENT_ERROR)] == ["write"]
    monkeypatch.undo()
    counting(system, monkeypatch)
    run_once(system)                                                          # stores normally this time
    monkeypatch.setattr(EnvelopeStore, "read_blob", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    third = run_once(system)
    assert system.store.get_run(third)["status"] == "waiting_human"
    assert [payload(row)["stage"] for row in events(system, third, EVENT_ERROR)] == ["read"]
    assert not events(system, third, EVENT_HIT)


def test_a_corrupt_or_foreign_entry_is_ignored(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)
    run_once(system)
    blobs = EnvelopeStore(system.store.db)
    with system.store.db.connect() as conn:
        shas = [row[0] for row in conn.execute("SELECT sha256 FROM blobs WHERE created_by LIKE ?", (ACTOR_PREFIX + "%",))]
    assert len(shas) == 1
    blobs.blob_path(shas[0]).write_bytes(b"{not json")                        # corrupt
    second = run_once(system)
    assert len(calls) == 4 and not events(system, second, EVENT_HIT)
    # An entry of another schema version under the same key is ignored too.
    key = payload(events(system, second, EVENT_STORED)[0])["key"]
    cache = StepCache(blobs, system.store.db)
    stale = json.dumps({"schema": "multiagent.step_cache_entry/0", "key": key, "kind": "model_step",
                        "result": {"data": {}}}).encode()
    with system.store.db.tx() as conn:
        for sha in shas:
            conn.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
        conn.execute("DELETE FROM blobs WHERE created_by=?", (ACTOR_PREFIX + key,))
        blobs.put_blob(conn, stale, actor=ACTOR_PREFIX + key)
    assert cache.lookup(key, kind="model_step") is None


def test_the_hit_event_carries_the_key_and_no_content(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    sentinel_text = f"Users can reset a password; {SENTINEL}."
    run_once(system, sentinel_text)
    second = run_once(system, sentinel_text)
    hit_rows = events(system, second, EVENT_HIT)
    assert hit_rows
    for row in hit_rows + events(system, second, EVENT_STORED):
        assert SENTINEL not in json.dumps(payload(row))
    output = json.dumps(system.store.latest_completed_step(second, "extract_requirements")[1])
    for row in hit_rows:                                                      # nothing of the cached output either
        text = json.dumps(payload(row))
        assert all(str(value) not in text for value in json.loads(output).get("requirements", [{}])[0].values()
                   if isinstance(value, str) and len(value) > 8)


def test_the_model_key_changes_with_anything_that_changes_the_output():
    base = {"step": {"id": "s", "prompt_version": 1}, "model": {"preferred": "m1", "fallback": None},
            "params": {"effort": None, "requires_web": False}, "prompt_sha256": "a" * 64, "inputs_sha256": "b" * 64}
    key = model_step_key(base)
    assert key == model_step_key(json.loads(json.dumps(base))) and len(key) == 64
    for change in ({"step": {"id": "s", "prompt_version": 2}}, {"model": {"preferred": "m2", "fallback": None}},
                   {"params": {"effort": "high", "requires_web": False}}, {"prompt_sha256": "c" * 64},
                   {"inputs_sha256": "d" * 64}):
        assert model_step_key({**base, **change}) != key


# ---------------------------------------------------------------- tools
def _ref(name, schema):
    return SchemaRef(schema_id=name, json_schema=schema, sha256=envelope_sha256(schema))


TEXT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}


def spec(tool_id="mock.lookup", version=1, *, deterministic=True, impact=ImpactClass.READ, description="", **extra):
    return ToolSpec(tool_id=tool_id, version=version, title="Lookup", description=description,
                    invocation=InvocationKind.LOCAL, impacts=frozenset({impact}),
                    input_schema=_ref(f"{tool_id}.input/1", TEXT), output_schema=_ref(f"{tool_id}.output/1", TEXT),
                    declared_by="builtin", deterministic=deterministic, **extra)


def tool_world(tmp_path, monkeypatch, *specs):
    system = runtime(tmp_path)
    calls = []
    registry = ToolRegistry()
    for item in specs:
        def executor(arguments, context, *, should_cancel, name=item.tool_id, version=item.version):
            calls.append((name, version, arguments["text"]))
            return {"text": f"{SENTINEL}:{arguments['text']}"}
        registry.register(item, executor)
    sink = []
    cache = StepCache(EnvelopeStore(system.store.db), system.store.db)
    wrapped = CachedToolInvoker(ToolInvoker(registry), cache, ON, event_sink=lambda kind, body: sink.append((kind, body)))
    return wrapped, calls, sink, registry, cache


def call(tool="mock.lookup", text="hello", version=None, index=0):
    context = ToolCallContext(scope="run_step", run_id="r1", step_id="s1")
    return ToolRequest(request_id=request_id_for(context, index), tool_id=tool, tool_version=version,
                       arguments={"text": text}, context=context, origin=ToolOrigin(kind="model_output", ref="m:1"))


GRANT = {"tool:mock.lookup", "tool:mock.lookup_v"}


def test_a_deterministic_read_tool_is_answered_from_the_cache_without_calling_it(tmp_path, monkeypatch):
    wrapped, calls, sink, _, _ = tool_world(tmp_path, monkeypatch, spec())
    first = wrapped.invoke(call(), grants=GRANT)
    again = wrapped.invoke(call(index=1), grants=GRANT)
    assert first.ok and again.ok and len(calls) == 1
    assert again.output == first.output and again.output_sha256 == first.output_sha256 and again.duration_ms == 0
    assert again.request_id != first.request_id and again.provenance_ref == f"tool:{again.request_id}"
    hits = [body for kind, body in sink if kind == EVENT_HIT]
    assert len(hits) == 1 and len(hits[0]["key"]) == 64 and hits[0]["usage"]["cached"] is True
    assert hits[0]["blob_ref"].startswith("blob:sha256:")
    assert all(SENTINEL not in json.dumps(body) for _, body in sink)          # keys and refs, never the content


def test_version_spec_or_arguments_invalidate_the_tool_cache(tmp_path, monkeypatch):
    v1, v2 = spec(), spec(version=2)
    changed_spec = spec(version=3, description="declared differently")
    wrapped, calls, _, _, _ = tool_world(tmp_path, monkeypatch, v1, v2, changed_spec)
    wrapped.invoke(call(version=1), grants=GRANT)
    wrapped.invoke(call(version=1, index=1), grants=GRANT)
    assert len(calls) == 1
    wrapped.invoke(call(version=2, index=2), grants=GRANT)                     # another version
    wrapped.invoke(call(version=3, index=3), grants=GRANT)                     # another spec_sha256
    wrapped.invoke(call(version=1, text="other", index=4), grants=GRANT)       # other arguments
    assert [item[:2] for item in calls] == [("mock.lookup", 1), ("mock.lookup", 2), ("mock.lookup", 3), ("mock.lookup", 1)]
    assert v1.version != v2.version and envelope_sha256(v1.model_dump(mode="json")) != \
        envelope_sha256(changed_spec.model_dump(mode="json"))


def test_tools_with_effects_or_not_deterministic_are_never_cached(tmp_path, monkeypatch):
    nondet = spec("mock.nondet", deterministic=False)
    writer = spec("mock.writer", deterministic=True, impact=ImpactClass.WRITE,
                  effects=(EffectDecl(kind=ActionKind.WRITE, target_from="/text"),))
    assert not tool_cacheable(nondet) and not tool_cacheable(writer) and tool_cacheable(spec())
    wrapped, calls, sink, _, cache = tool_world(tmp_path, monkeypatch, nondet, writer)
    wrapped.invoke(call("mock.nondet"), grants={"tool:mock.nondet"})
    wrapped.invoke(call("mock.nondet", index=1), grants={"tool:mock.nondet"})
    assert len(calls) == 2
    denied = wrapped.invoke(call("mock.writer"), grants={"tool:mock.writer"})   # approval required: delegated, refused
    assert not denied.ok and denied.error.code.value == "approval_required"
    with cache.db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM blobs WHERE created_by LIKE ?", (ACTOR_PREFIX + "%",)).fetchone()[0] == 0
    assert not [kind for kind, _ in sink if kind in (EVENT_HIT, EVENT_STORED)]


def test_a_cached_result_never_bypasses_permissions_and_the_tool_cache_is_off_by_default(tmp_path, monkeypatch):
    wrapped, calls, sink, registry, cache = tool_world(tmp_path, monkeypatch, spec())
    wrapped.invoke(call(), grants=GRANT)
    refused = wrapped.invoke(call(index=1), grants=set())                       # no grant: not served from the cache
    assert not refused.ok and refused.error.code.value == "not_granted" and len(calls) == 1
    off = CachedToolInvoker(ToolInvoker(registry), cache, StepCachePolicy())
    off.invoke(call(index=2), grants=GRANT)
    off.invoke(call(index=3), grants=GRANT)
    assert len(calls) == 3                                                      # default policy: every call runs


def test_a_cache_failure_does_not_break_a_tool_call(tmp_path, monkeypatch):
    wrapped, calls, sink, _, _ = tool_world(tmp_path, monkeypatch, spec())
    monkeypatch.setattr(EnvelopeStore, "put_blob", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert wrapped.invoke(call(), grants=GRANT).ok and wrapped.invoke(call(index=1), grants=GRANT).ok
    assert len(calls) == 2 and [kind for kind, _ in sink if kind == EVENT_ERROR] == [EVENT_ERROR, EVENT_ERROR]


# ---------------------------------------------------------------- review corrections (CAP-T13 round 2)
import dataclasses
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

import multiagent.step_cache as step_cache_module
import multiagent.workflow_engine as engine_module
from multiagent.call_guards import ORDER_EGRESS, PROCEED, Stop
from multiagent.model_router import ModelBinding
from multiagent.step_cache import KIND_MODEL, KIND_TOOL, tool_key


class OtherOut(BaseModel):
    value: str


def key_world(tmp_path):
    system = runtime(tmp_path)
    step = system.engine.preflight("spec_review").steps[0]
    contract = system.engine.output_schemas[step.contract]
    base = dict(step=step, binding=ModelBinding("local_default"), profile_id=None, routing_decision=None, effort=None,
                requires_web=False, execution_mode=engine_module.ExecutionMode.LOCAL, output_schema=contract,
                prompt_sha256="a" * 64, inputs={"spec_text": "x"}, sensitive=False,
                run={"project_id": "p1", "workflow_id": "spec_review"}, egress={"action": "off"})
    return system, base, lambda **change: system.engine._step_cache_key(**{**base, **change})


def test_every_field_of_the_engine_key_changes_the_key(tmp_path, monkeypatch):
    system, base, key = key_world(tmp_path)
    reference = key()
    assert reference == key() and len(reference) == 64
    step = base["step"]
    variations = {
        "prompt": key(prompt_sha256="b" * 64),
        "inputs": key(inputs={"spec_text": "y"}),
        "preferred model": key(binding=ModelBinding("local_other")),
        "fallback model": key(binding=ModelBinding("local_default", fallback_model="local_other")),
        "sensitive": key(sensitive=True),
        "output schema": key(output_schema=OtherOut),
        "prompt version": key(step=dataclasses.replace(step, prompt_version=step.prompt_version + 1)),
        "effort": key(effort="high"),
        "requires_web": key(requires_web=True),
        "project": key(run={"project_id": "p2", "workflow_id": "spec_review"}),
        "workflow": key(run={"project_id": "p1", "workflow_id": "other"}),
        "egress": key(egress={"action": "block"}),
        "data class": key(data_class="restricted"),
    }
    system.engine.router.dry_run = not system.engine.router.dry_run
    variations["dry_run"] = key()
    system.engine.router.dry_run = not system.engine.router.dry_run
    old = system.engine.document_policy
    system.engine.document_policy = engine_module.resolve_policy("pause")
    variations["document policy"] = key()
    system.engine.document_policy = old
    spec_of_model = system.engine.catalog.models["local_default"]
    monkeypatch.setitem(system.engine.catalog.models, "local_default",
                        dataclasses.replace(spec_of_model, model="other-model"))
    variations["catalog spec"] = key()
    monkeypatch.undo()
    monkeypatch.setattr(engine_module, "STEP_CACHE_CODE_VERSION", "other/9")
    variations["code version"] = key()
    monkeypatch.undo()
    monkeypatch.setattr(step_cache_module, "KEY_SCHEMA", "multiagent.step_cache_key/99")
    variations["key schema"] = key()
    monkeypatch.undo()
    assert all(value != reference for value in variations.values()), \
        [name for name, value in variations.items() if value == reference]
    assert len(set(variations.values())) == len(variations)


def test_the_tool_key_uses_the_key_schema_too(monkeypatch):
    item = spec()
    before = tool_key(item, "s" * 64, {"text": "a"})
    monkeypatch.setattr(step_cache_module, "KEY_SCHEMA", "multiagent.step_cache_key/99")
    assert tool_key(item, "s" * 64, {"text": "a"}) != before


def test_projects_do_not_share_entries(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)

    def run(project):
        created = system.service.create_run(request(project_name=project))
        return system.engine.process_run(created["id"])["id"]
    run("Project A")
    second = run("Project B")
    assert not events(system, second, EVENT_HIT) and len(calls) == 4
    third = run("Project A")
    assert [payload(row)["step_id"] for row in events(system, third, EVENT_HIT)] == ["extract_requirements"]


def test_a_step_with_requires_web_is_never_cached(tmp_path, monkeypatch):
    from test_generic_runs import BUNDLE
    web = tmp_path / "bundle_web"
    shutil.copytree(BUNDLE, web)
    flow = web / "workflows" / "spec_review.yaml"
    text = flow.read_text(encoding="utf-8")
    flow.write_text(text.replace("requires_web: false", "requires_web: true", 1), encoding="utf-8")
    models = web / "catalog" / "models.yaml"
    models.write_text(models.read_text(encoding="utf-8").replace("web: false", "web: true"), encoding="utf-8")
    system = runtime(tmp_path / "w", bundle_root=web)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)
    for _ in range(2):
        run_id = run_once(system)
        assert not events(system, run_id, EVENT_HIT) and not events(system, run_id, EVENT_STORED)
    assert len(calls) == 4


def test_a_hit_emits_model_selected_marked_as_cached(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    run_once(system)
    second = run_once(system)
    selected = [payload(row) for row in events(system, second, "model_selected")]
    cached = [item for item in selected if item["step_id"] == "extract_requirements"]
    assert len(cached) == 1 and cached[0]["cached"] is True and cached[0]["model"] and cached[0]["attempts"] == []


class FlagGuard:
    """A call guard that stops the call while the flag is set (stands in for inbox, review, budget)."""
    name = "flag"
    order = ORDER_EGRESS - 1
    observer = False

    def __init__(self, flag):
        self.flag = flag

    def check(self, call, state):
        return Stop("flag_stop", "stopped by the test guard") if self.flag["stop"] else PROCEED


def test_a_hit_never_skips_a_guard_and_the_run_behaves_as_without_cache(tmp_path, monkeypatch):
    flag = {"stop": False}
    real = engine_module.build_call_guards

    def with_flag(env, **kwargs):
        chain = real(env, **kwargs)
        chain.register(FlagGuard(flag))
        return chain
    monkeypatch.setattr(engine_module, "build_call_guards", with_flag)
    outcomes = {}
    for cache in (False, True):
        system = runtime(tmp_path / str(cache))
        system.engine.step_cache_policy = ON if cache else StepCachePolicy()
        flag["stop"] = False
        run_once(system)
        flag["stop"] = True
        blocked = run_once(system)
        run = system.store.get_run(blocked)
        outcomes[cache] = (run["status"], run["error"], run["llm_calls"],
                           [row["step_id"] for row in read_usage(system.store, blocked)],
                           bool(events(system, blocked, EVENT_HIT)))
    assert outcomes[True] == outcomes[False] and outcomes[True][0] == "failed" and outcomes[True][4] is False


def test_egress_block_fails_with_the_cache_exactly_as_without_it(tmp_path):
    from test_generic_runs import BUNDLE
    cloud = tmp_path / "bundle_cloud"
    shutil.copytree(BUNDLE, cloud)
    models = cloud / "catalog" / "models.yaml"
    models.write_text(models.read_text(encoding="utf-8").replace("tier: local", "tier: cloud"), encoding="utf-8")
    text = "Users can reset a password; contact admin.person@example.com for access."
    results = {}
    for cache in (False, True):
        system = runtime(tmp_path / f"w{cache}", bundle_root=cloud)
        system.engine.settings.egress_default = "block"
        system.engine.step_cache_policy = ON if cache else StepCachePolicy()
        created = system.service.create_run(request(inputs={"spec_text": text}, execution_mode="cloud"))
        run_id = system.engine.process_run(created["id"])["id"]
        kinds = [row["event_type"] for row in events(system, run_id)]
        results[cache] = (system.store.get_run(run_id)["status"], "egress_blocked" in kinds,
                          EVENT_HIT in kinds, EVENT_STORED in kinds)
    assert results[False][:2] == results[True][:2] == ("failed", True)
    assert results[True][2:] == (False, False)


def test_redacting_egress_emits_once_per_step_on_a_cache_miss(tmp_path, monkeypatch):
    from test_generic_runs import BUNDLE
    cloud = tmp_path / "bundle_cloud"
    shutil.copytree(BUNDLE, cloud)
    models = cloud / "catalog" / "models.yaml"
    models.write_text(models.read_text(encoding="utf-8").replace("tier: local", "tier: cloud"),
                      encoding="utf-8")
    system = runtime(tmp_path / "runs", bundle_root=cloud)
    system.engine.step_cache_policy = ON
    system.engine.settings.egress_default = "redact"
    calls = counting(system, monkeypatch)
    text = "Contact admin.person@example.com about account access."

    def run():
        created = system.service.create_run(request(inputs={"spec_text": text}, execution_mode="cloud"))
        return system.engine.process_run(created["id"])["id"]

    first = run()
    assert events(system, first, EVENT_STORED) == []
    second = run()
    assert not events(system, second, EVENT_HIT) and len(calls) == 4
    redactions = [payload(row)["step_id"] for row in events(system, second, "egress_redacted")]
    assert redactions.count("extract_requirements") == 1
    assert redactions.count("assess_risks") <= 1


# -- integrity of the entries (each check of the read and of the hit)
def make_cache(tmp_path):
    system = runtime(tmp_path)
    return system, StepCache(EnvelopeStore(system.store.db), system.store.db)


def test_lookup_ignores_an_entry_for_another_key_or_with_a_wrong_hash(tmp_path):
    system, cache = make_cache(tmp_path)
    key, other = "k" * 64, "o" * 64
    ref = cache.store(other, kind=KIND_MODEL, result={"data": {"a": 1}})
    sha = ref.rsplit(":", 1)[1]
    with system.store.db.tx() as conn:                           # the blob of ``other`` served under ``key``
        conn.execute("UPDATE blobs SET created_by=? WHERE sha256=?", (ACTOR_PREFIX + key, sha))
    assert cache.lookup(key, kind=KIND_MODEL) is None
    good = cache.store("g" * 64, kind=KIND_MODEL, result={"data": {"a": 1}})
    assert cache.lookup("g" * 64, kind=KIND_MODEL) is not None
    path = cache.blobs.blob_path(good.rsplit(":", 1)[1])
    path.write_bytes(path.read_bytes().replace(b'"a":1', b'"a":2'))   # still valid JSON, no longer its own sha256
    assert cache.lookup("g" * 64, kind=KIND_MODEL) is None
    cache.store("t" * 64, kind=KIND_TOOL, result={"data": {"a": 1}})
    assert cache.lookup("t" * 64, kind=KIND_MODEL) is None             # another kind


def test_a_saved_output_that_no_longer_validates_is_not_used(tmp_path, monkeypatch):
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    calls = counting(system, monkeypatch)
    first = run_once(system)
    key = payload(events(system, first, EVENT_STORED)[0])["key"]
    with system.store.db.tx() as conn:
        conn.execute("DELETE FROM blobs WHERE created_by=?", (ACTOR_PREFIX + key,))
    StepCache(EnvelopeStore(system.store.db), system.store.db).store(
        key, kind=KIND_MODEL, result={"data": {"not": "the contract"}, "model": "m"})
    second = run_once(system)
    assert len(calls) == 4 and not events(system, second, EVENT_HIT)


def test_a_tampered_tool_entry_is_not_served_and_failures_and_cancellation_are_not_cached(tmp_path, monkeypatch):
    item = spec()
    wrapped, calls, sink, registry, cache = tool_world(tmp_path, monkeypatch, item)
    registered = registry.lookup("mock.lookup")
    key = tool_key(item, registered.spec_sha256, {"text": "hello"})
    cache.store(key, kind=KIND_TOOL, result={"output": {"text": "forged"}, "output_sha256": "0" * 64,
                                              "output_bytes": 5, "tool_version": 1,
                                              "spec_sha256": registered.spec_sha256})
    result = wrapped.invoke(call(), grants=GRANT)
    assert result.ok and calls == [("mock.lookup", 1, "hello")] and "forged" not in json.dumps(result.output)
    with pytest.raises(RuntimeError):                              # a cancelled call is not answered from the cache
        wrapped.invoke(call(index=1), grants=GRANT, should_cancel=lambda: True)
    assert len(calls) == 1
    failing = spec("mock.fails")
    registry.register(failing, lambda arguments, context, *, should_cancel: (_ for _ in ()).throw(ValueError("x")))
    assert not wrapped.invoke(call("mock.fails"), grants={"tool:mock.fails"}).ok
    failed_key = tool_key(failing, registry.lookup("mock.fails").spec_sha256, {"text": "hello"})
    assert cache.lookup(failed_key, kind=KIND_TOOL) is None


def test_a_tool_hit_goes_through_the_invoker_guards_and_leaves_a_trace_without_a_sink(tmp_path, monkeypatch, caplog):
    wrapped, calls, _, registry, cache = tool_world(tmp_path, monkeypatch, spec())
    wrapped.event_sink = None
    wrapped.invoke(call(), grants=GRANT)
    with caplog.at_level(logging.INFO, logger="multiagent.step_cache"):
        assert wrapped.invoke(call(index=1), grants=GRANT).ok
    assert any(EVENT_HIT in record.getMessage() for record in caplog.records) and len(calls) == 1
    assert all(SENTINEL not in record.getMessage() for record in caplog.records)

    class StopGuard:
        name, order, observer = "stop", 1, False

        def check(self, tool_spec, request, state):
            return Stop("blocked", "no")
    stopped = CachedToolInvoker(ToolInvoker(registry, guards=[StopGuard()]), cache, ON)
    result = stopped.invoke(call(index=2), grants=GRANT)
    assert not result.ok and result.error.code.value == "guard_stopped" and len(calls) == 1


# ---------------------------------------------------------------- second review pass
def test_the_real_catalog_and_set_valued_specs_build_a_key(tmp_path):
    from pathlib import Path
    from multiagent.catalog import Catalog
    system, base, key = key_world(tmp_path)
    spec_of_model = system.engine.catalog.models["local_default"]
    sets = dataclasses.replace(spec_of_model, allowed_data_classes=frozenset({"restricted", "non_sensitive"}),
                               capabilities=frozenset({"b", "a"}))
    system.engine.catalog.models["local_default"] = sets
    first = key()
    assert first is not None
    system.engine.catalog.models["local_default"] = dataclasses.replace(
        sets, allowed_data_classes=frozenset({"non_sensitive", "restricted"}), capabilities=frozenset({"a", "b"}))
    assert key() == first                                        # sets hash the same whatever their order
    system.engine.catalog.models["local_default"] = dataclasses.replace(
        sets, allowed_data_classes=frozenset({"non_sensitive"}))
    assert key() != first
    repo_catalog = Path(engine_module.__file__).resolve().parent.parent / "catalog"
    real = Catalog(system.engine.settings, catalog_dir=repo_catalog)
    system.engine.catalog = real
    assert real.models
    for model_id in real.models:
        assert key(binding=ModelBinding(model_id)) is not None, model_id
    assert key(data_class="restricted") != key() and key(data_class="non_sensitive") == key()


def test_set_valued_catalog_spec_hash_is_stable_across_processes():
    root = str(Path(engine_module.__file__).resolve().parent.parent)
    script = """
import dataclasses
from multiagent.catalog import ModelSpec
from multiagent.tasks.envelopes import envelope_sha256
from multiagent.workflow_engine import _json_safe

spec = ModelSpec(id="sample", provider="fake", model="sample", tier="local",
                 structured_output=True, web=False,
                 capabilities=frozenset({"alpha", "beta", "gamma", "delta", "epsilon", "zeta"}),
                 allowed_data_classes=frozenset({"restricted", "non_sensitive", "sensitive"}))
print(envelope_sha256(_json_safe(dataclasses.asdict(spec))))
"""
    digests = []
    for seed in ("0", "1", "42", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed,
               "PYTHONPATH": os.pathsep.join(filter(None, (root, os.environ.get("PYTHONPATH", ""))))}
        digests.append(subprocess.check_output([sys.executable, "-c", script], env=env, text=True).strip())
    assert len(set(digests)) == 1 and len(digests[0]) == 64


def test_escalation_stores_under_the_final_binding_and_effort_key(tmp_path, monkeypatch):
    from test_bounded_escalation import events as escalation_events
    from test_bounded_escalation import make, profile, start, state

    system = make(tmp_path, scripts={"t1": ["bad"], "t2": ["ok"]},
                  eco=profile(max_retries_per_candidate=0), checkpoint=False)
    system.engine.step_cache_policy = ON
    original = system.engine._step_cache_key
    keys = []

    def captured(**kwargs):
        key = original(**kwargs)
        keys.append((key, kwargs["binding"].preferred_model, kwargs["effort"]))
        return key

    monkeypatch.setattr(system.engine, "_step_cache_key", captured)
    run = state(system, start(system))
    assert system.lab.calls == ["t1", "t2"]
    assert escalation_events(run, "model_escalated")
    assert len(keys) == 2 and keys[0][0] is not None and keys[1][0] is not None
    assert keys[0][1] == "t1" and keys[1][1] == "t2" and keys[0][0] != keys[1][0]
    stored = escalation_events(run, EVENT_STORED)
    assert len(stored) == 1 and stored[0]["key"] == keys[1][0]
    assert not escalation_events(run, EVENT_HIT)


def test_an_observer_guard_that_stops_or_pauses_cancels_the_tool_hit(tmp_path, monkeypatch):
    from multiagent.call_guards import Pause

    class Observer:
        name, order, observer = "watch", 1, True

        def __init__(self, verdict):
            self.verdict = verdict

        def check(self, tool_spec, request, state):
            return self.verdict
    for verdict in (Stop("blocked", "no"), Pause("review", {}, "wait")):
        wrapped, calls, _, registry, cache = tool_world(tmp_path / type(verdict).__name__, monkeypatch, spec())
        wrapped.invoke(call(), grants=GRANT)
        guarded = CachedToolInvoker(ToolInvoker(registry, guards=[Observer(verdict)]), cache, ON)
        try:
            result = guarded.invoke(call(index=1), grants=GRANT)
        except Exception as exc:                                   # a Pause is raised by the real invoker
            assert type(exc).__name__ == "ToolCallPaused"
        else:
            assert not result.ok and result.error.code.value == "guard_stopped"
        assert len(calls) == 1                                     # never answered from the cache


def test_the_saved_output_is_validated_before_the_guards_run(tmp_path, monkeypatch):
    seen = []

    class Counting:
        name, order, observer = "counting", ORDER_EGRESS - 1, False

        def check(self, call, state):
            seen.append(call.step_id)
            return PROCEED
    real = engine_module.build_call_guards

    def with_counter(env, **kwargs):
        chain = real(env, **kwargs)
        chain.register(Counting())
        return chain
    monkeypatch.setattr(engine_module, "build_call_guards", with_counter)
    system = runtime(tmp_path)
    system.engine.step_cache_policy = ON
    first = run_once(system)
    key = payload(events(system, first, EVENT_STORED)[0])["key"]
    with system.store.db.tx() as conn:
        conn.execute("DELETE FROM blobs WHERE created_by=?", (ACTOR_PREFIX + key,))
    StepCache(EnvelopeStore(system.store.db), system.store.db).store(
        key, kind=KIND_MODEL, result={"data": {"not": "the contract"}, "model": "m"})
    seen.clear()
    run_once(system)
    assert seen.count("extract_requirements") == 1                 # the real call only: no guard ran for the bad entry
