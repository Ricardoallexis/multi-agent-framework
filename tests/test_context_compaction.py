"""CMP-T04: compacting the context of workflow steps (contract CMP-T01 sections 5, 8 and 10).

End to end with a throwaway bundle (six steps, ``a`` to ``f``, each reading every earlier output), a real
engine, store and router, and scripted providers: no network, no paid call, nothing outside the temp folder.
The window of the step model is a few thousand tokens, so a step crosses the 70 % threshold after three outputs
have piled up; the summary models answer from queues.

Also the pure parts of ``multiagent.context.workflow``: the context parts, the state read from the events, the
plan of what to summarize and the events.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.artifacts import ArtifactWriter
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.context import SUMMARY_SCHEMA, content_sha256
from multiagent.context import workflow as cw
from multiagent.context.summary import content_text
from multiagent.db.database import Database
from multiagent.db.envelope_store import EnvelopeStore
from multiagent.db.store import Store
from multiagent.errors import ProviderUnavailable, StructuredOutputError
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.schema_utils import estimate_tokens_conservative
from multiagent.workflow_engine import WorkflowEngine

CONTRACTS = '''\
from pydantic import Field

from multiagent.contracts import StrictModel


class Note(StrictModel):
    text: str = Field(min_length=1)


OUTPUT_SCHEMAS = {"Note": Note}
SAMPLE_OUTPUTS = {"Note": {"text": "sample"}}
'''

PROMPT = """\
# Role
You write the next note of a chain.

# Topic
{topic}

# Earlier notes
A: {a_output}
B: {b_output}
C: {c_output}
D: {d_output}
E: {e_output}
F: {f_output}

# Revision
Previous attempt: {previous_output}
Feedback: {revision_feedback}
"""

STEPS = "abcdef"
TOPIC = "Quarterly release of the importer"
WINDOW = 2200   # tokens of the step model: 70 % is 1 540, 62 % is 1 364
SUMMARY_WINDOW = 8000


# --- the throwaway bundle -------------------------------------------------------------------------------

def price(amount, *, provenance="local_no_charge", source="local, no charge"):
    side = {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD", "source": source,
            "provenance": provenance, "valid_from": "2025-01-01T00:00:00Z", "valid_until": "2035-01-01T00:00:00Z"}
    return {"input": dict(side), "output": {**side, "amount": amount * 2}}


def local_model(name, *, window, level=None, sensitive=False, tier="local"):
    model = {"provider": "ollama" if tier == "local" else "gemini", "model": name, "tier": tier,
             "structured_output": True, "web": False, "num_ctx": window, "keep_alive": "5m", "thinking": False,
             "capabilities": ["text_generation"], "roles": ["assistant"],
             "permissions": ["data:sensitive"] if sensitive else [], "permissions_required": [],
             "allowed_data_classes": ["non_sensitive", "sensitive"] if sensitive else ["non_sensitive"]}
    if tier != "local":
        del model["num_ctx"], model["keep_alive"], model["thinking"]
        model["context_window"] = {"tokens": window, "source": "unit test documentation", "checked_at": "2026-10-11",
                                   "provenance": "provider_docs", "model": name}
        model["permissions"], model["permissions_required"] = ["provider:cloud"], ["provider:cloud"]
    if level is not None:
        model.update(level=level, classification="classified", pricing=price(0))
    return model


def workflow_yaml(steps: str, checkpoints, fallback: bool) -> str:
    document = {"id": "chain", "max_iterations": 2, "inputs": [{"name": "topic", "label": "Topic", "type": "text"}],
                "steps": [{"id": step, "agent": "worker", "prompt_id": "chain/step", "prompt_version": 1,
                           "skills": [], "contract": "Note", "preferred_model": "local_default",
                           "fallback_model": "step_fallback" if fallback else None, "requires_web": False,
                           "when": None, "checkpoint_after": step in checkpoints} for step in steps]}
    return yaml.safe_dump(document, sort_keys=False)


def write_bundle(root: Path, *, steps=STEPS, checkpoints=(), fallback=False, window=WINDOW, summary_models=True,
                 sensitive_summarizer=False, profile: dict[str, Any] | None = None, assign=True) -> Path:
    """A bundle whose catalog has the step model, an optional fallback, two summary models and a context profile."""
    (root / "workflows").mkdir(parents=True)
    (root / "prompts" / "chain").mkdir(parents=True)
    (root / "catalog").mkdir()
    (root / "contracts.py").write_text(CONTRACTS, encoding="utf-8")
    (root / "prompts" / "chain" / "step.v1.md").write_text(PROMPT, encoding="utf-8")
    (root / "workflows" / "chain.yaml").write_text(workflow_yaml(steps, checkpoints, fallback), encoding="utf-8")
    (root / "catalog" / "agents.yaml").write_text(yaml.safe_dump({"agents": {"worker": {
        "role": "Writes a note", "active": True, "capabilities": {"writing": "PRIMARY"}}}}), encoding="utf-8")
    models = {"local_default": local_model("step-main", window=window)}
    if fallback:
        models["step_fallback"] = local_model("step-fallback", window=window)
    if summary_models:
        models["sum_t1"] = local_model("sum-t1", window=SUMMARY_WINDOW, level="T1", sensitive=sensitive_summarizer)
        models["sum_t2"] = local_model("sum-t2", window=SUMMARY_WINDOW, level="T2", sensitive=sensitive_summarizer)
    document = {
        "models": models, "routing_profiles": {"profiles": {}, "assignments": {}},
        "context_profiles": {"profiles": {"standard": {"version": 1, "enabled": True, **(profile or {})}},
                             "assignments": {"chain": "standard"} if assign else {}},
    }
    (root / "catalog" / "models.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return root


# --- scripted providers ---------------------------------------------------------------------------------

def filler(label: str, size: int = 2600) -> str:
    """Distinct, deterministic text of about ``size`` characters."""
    unit = f"{label}-detail "
    return (unit * (size // len(unit) + 1))[:size]


def good_summary(**changes):
    summary = {"objective": "Write the chain of notes for the quarterly release.", "decisions": [], "pending": [],
               "files_and_artifacts": [], "constraints": ["Keep each note factual"], "errors_seen": []}
    summary.update(changes)
    return summary


@dataclass
class Call:
    kind: str          # "note" | "summary" | "check"
    model: str         # catalog id of the model called
    prompt: str
    tokens: int


class Scripted(LLMAdapter):
    """Answers every call from queues. ``notes`` feeds the steps, in order, with the text of the note or an
    exception to raise; ``summaries`` and ``checks`` feed the summary calls (a default valid answer when empty)."""

    def __init__(self, provider="ollama", *, notes=(), summaries=(), checks=()):
        self.provider = provider
        self.notes, self.summaries, self.checks = list(notes), list(summaries), list(checks)
        self.calls: list[Call] = []

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        kind = {"ContextSummaryContent": "summary", "SummaryCheckOutput": "check"}.get(output_schema.__name__, "note")
        self.calls.append(Call(kind, spec.id, prompt, estimate_tokens_conservative(prompt)))
        queue = {"note": self.notes, "summary": self.summaries, "check": self.checks}[kind]
        if queue:
            item = queue.pop(0)
        elif kind == "check":
            item = {"verdict": "ok", "items": []}
        elif kind == "summary":
            item = good_summary()
        else:
            item = filler(f"auto{len(self.calls)}")
        if callable(item):
            item = item()
        if isinstance(item, Exception):
            raise item
        if kind == "note":
            item = {"text": item}
        try:
            parsed = output_schema.model_validate(copy.deepcopy(item))
        except ValidationError as exc:
            raise StructuredOutputError(f"invalid {output_schema.__name__}: {exc}") from exc
        return LLMResponse(parsed=parsed, raw_text=parsed.model_dump_json(), provider=self.provider, model=spec.model,
                           tokens_in=100, tokens_out=40, latency_ms=3, metadata={})

    def health(self):
        return {"provider": self.provider, "available": True}

    def of(self, kind):
        return [call for call in self.calls if call.kind == kind]


def runtime(tmp_path, *, notes=None, summaries=(), checks=(), max_llm_calls=40, window=WINDOW, **bundle_options):
    """A real engine over a temp store with the throwaway bundle. ``notes`` defaults to one distinct text per step.

    ``window`` is the window of ``local_default``: the catalog takes it from ``OLLAMA_NUM_CTX``, not from its entry.
    """
    root = write_bundle(tmp_path / "bundle", window=window, **bundle_options)
    settings = Settings(_env_file=None, local_dir=tmp_path / "local", worker_enabled=False, gemini_api_key="",
                        openai_api_key="", openai_enabled=False, max_llm_calls=max_llm_calls, ollama_num_ctx=window)
    settings.ensure_directories()
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(root, settings)
    adapter = Scripted(notes=[filler(step) for step in STEPS * 3] if notes is None else notes,
                       summaries=summaries, checks=checks)
    cloud = Scripted("gemini")
    engine = WorkflowEngine.from_bundle(bundle, store=store, settings=settings, artifacts=ArtifactWriter(settings),
                                        adapters={"ollama": adapter, "gemini": cloud}, dry_run=False)
    return SimpleNamespace(store=store, engine=engine, bundle=bundle, adapter=adapter, cloud=cloud, settings=settings,
                           service=RunService(settings, store, engine=engine), root=root)


def start(system, topic=TOPIC, **request):
    return system.service.create_run(RunRequest(
        workflow_id="chain", project_name="Synthetic chain", inputs={"topic": topic}, **request))["id"]


def run_all(system, **request):
    """Create a run and process it until it stops (completed, or waiting for a person)."""
    run_id = start(system, **request)
    return run_id, system.engine.process_run(run_id)


def events(run, name):
    return [event["payload"] for event in run["events"] if event["event_type"] == name]


def names(run):
    return [event["event_type"] for event in run["events"]]


def prompts_of(system):
    """The prompts of the step calls, in order."""
    return [call.prompt for call in system.adapter.of("note")]


def step_prompt(prompt: str) -> str:
    """The part of a prompt before the output contract (which is the same JSON schema in every step)."""
    return prompt.split("## REQUIRED OUTPUT CONTRACT")[0]


def telemetry(system, run_id):
    with system.store.db.connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM telemetry WHERE run_id=? ORDER BY id", (run_id,))]


def checkpoints_of(system, run_id):
    return [event["payload"] for event in system.store.get_run(run_id)["events"] if event["event_type"] == "checkpoint"]




def baseline(tmp_path, **options):
    """The same run with no context profile assigned: what the prompts were before this feature."""
    system = runtime(tmp_path / "baseline", assign=False, **options)
    run_id, run = run_all(system)
    return SimpleNamespace(system=system, run_id=run_id, run=run, prompts=prompts_of(system))


# =============================================================================================================
# The pure parts: context parts, state from the events, plan, pinned check
# =============================================================================================================

def rec(step_id, attempt=1, size=200):
    return cw.OutputRecord(step_id, attempt, {"text": filler(step_id, size)})


def make_state(*records, summary=None):
    artifact = {"schema": SUMMARY_SCHEMA, **(summary or good_summary())}
    sha = content_sha256(artifact)
    return cw.CompactionState(
        cw.blob_ref(sha), sha, artifact, tuple(cw.ReplacedOutput(r.step_id, r.attempt, r.sha256) for r in records))


def compacted_payload(state, **changes):
    payload = {"scope": "run_step", "summary_ref": state.summary_ref, "summary_sha256": state.summary_sha256,
               "replaced": [item.to_dict() for item in state.replaced]}
    payload.update(changes)
    return payload


def log(*payloads, event_type="context_compacted"):
    return [{"event_type": event_type, "payload": payload} for payload in payloads]


def test_the_mark_that_replaces_an_output_names_the_blob_that_keeps_it():
    assert cw.summarized_mark("a" * 64) == "[resumido · blob:sha256:" + "a" * 64 + "]"
    assert cw.step_ref("research", 2) == "step:research:2"
    record = rec("a")
    assert record.sha256 == content_sha256({"text": filler("a", 200)}) and record.ref == "step:a:1"
    assert record.text == json.dumps(record.output, ensure_ascii=False, indent=2)


def test_without_a_state_the_context_is_exactly_the_plain_one():
    a, c = rec("a"), rec("c")
    parts = cw.build_context_parts(
        inputs={"topic": "T", "count": 3}, prior_outputs=[("a", a), ("b", None), ("c", c)],
        previous_output={"x": 1}, revision_feedback="fb")
    context = cw.StepContext(parts)
    assert context.variables() == {
        "topic": "T", "count": 3, "a_output": a.text, "b_output": "", "c_output": c.text,
        "previous_output": json.dumps({"x": 1}, ensure_ascii=False, indent=2), "revision_feedback": "fb"}
    assert list(context.variables()) == ["topic", "count", "a_output", "b_output", "c_output", "previous_output",
                                         "revision_feedback"]
    assert context.sections() == [] and context.summary_ref is None
    assert [part.kind for part in parts] == [
        cw.KIND_INPUT, cw.KIND_INPUT, cw.KIND_STEP_OUTPUT, cw.KIND_STEP_OUTPUT, cw.KIND_STEP_OUTPUT,
        cw.KIND_PREVIOUS_OUTPUT, cw.KIND_REVISION_FEEDBACK]
    part = context.part("a_output")
    assert (part.ref, part.sha256, part.summarized) == ("step:a:1", a.sha256, False)
    # A step with no earlier output and no previous attempt gets empty texts, as it always did.
    empty = cw.StepContext(cw.build_context_parts(inputs={}, prior_outputs=[], previous_output=None,
                                                  revision_feedback=""))
    assert empty.variables() == {"previous_output": "", "revision_feedback": ""}


def test_a_state_replaces_older_outputs_with_marks_and_adds_the_summary_section():
    a, b = rec("a"), rec("b")
    state = make_state(a)
    context = cw.StepContext(cw.build_context_parts(
        inputs={"topic": "T"}, prior_outputs=[("a", a), ("b", b)], previous_output=None, revision_feedback="",
        state=state))
    variables = context.variables()
    assert variables["a_output"] == cw.summarized_mark(a.sha256) and variables["b_output"] == b.text
    assert variables["topic"] == "T"
    (section,) = context.sections()
    assert section.startswith("## Context summary") and f"Summary reference: {state.summary_ref}" in section
    assert state.summary["objective"] in section
    assert context.summary_ref == state.summary_ref
    part = context.part("a_output")
    assert part.summarized and part.ref == "step:a:1" and part.sha256 == a.sha256
    assert not context.part("b_output").summarized
    assert context.part("context_summary").placement == cw.PLACEMENT_SECTION
    assert context.part("context_summary").kind == cw.KIND_CONTEXT_SUMMARY


@pytest.mark.parametrize("prior", [
    [("a", rec("a", attempt=2)), ("b", rec("b"))],                                   # the output was redone
    [("a", cw.OutputRecord("a", 1, {"text": "changed"})), ("b", rec("b"))],          # the output changed
    [("a", None), ("b", rec("b"))],                                                  # the output is gone
    [("b", rec("b"))],                                                               # the replaced step is not an earlier step
])
def test_a_state_that_does_not_fit_the_outputs_is_ignored(prior):
    state = make_state(rec("a"))
    kwargs = dict(inputs={"topic": "T"}, prior_outputs=prior, previous_output=None, revision_feedback="")
    assert cw.build_context_parts(**kwargs, state=state) == cw.build_context_parts(**kwargs)


def test_the_plan_keeps_the_last_outputs_and_the_run_inputs_and_summarizes_what_is_older():
    outputs = [rec("a", size=800), rec("b", size=800), rec("c", size=800)]
    plan = cw.plan_compaction(outputs, inputs={"topic": "T"}, keep_recent=1, state=None)
    assert [source.ref for source in plan.sources] == ["step:a:1", "step:b:1"]
    assert [source.sha256 for source in plan.sources] == [outputs[0].sha256, outputs[1].sha256]
    assert [item.name for item in plan.pinned] == ["request.inputs", "output:c"]
    assert plan.pinned[1].content == outputs[2].text and plan.pinned[0].content == {"topic": "T"}
    assert plan.previous is None and plan.kept == (outputs[2],) and plan.detail == ""
    assert [item.ref for item in plan.replaced] == ["step:a:1", "step:b:1"] and plan.new == plan.replaced
    assert plan.tokens_before == sum(source.tokens for source in plan.sources) > 0
    more = cw.plan_compaction(outputs, inputs={"topic": "T"}, keep_recent=2, state=None)
    assert [source.ref for source in more.sources] == ["step:a:1"]
    assert [item.name for item in more.pinned] == ["request.inputs", "output:b", "output:c"]
    assert cw.plan_compaction(outputs, inputs={}, keep_recent=0, state=None).kept == (outputs[2],)   # at least one


def test_the_plan_extends_the_summary_in_force_with_what_is_newly_old():
    outputs = [rec("a", size=800), rec("b", size=800), rec("c", size=800), rec("d", size=800)]
    state = make_state(outputs[0], outputs[1])
    plan = cw.plan_compaction(outputs, inputs={"topic": "T"}, keep_recent=1, state=state)
    assert [source.ref for source in plan.sources] == ["step:c:1"]            # a and b are already in the summary
    assert plan.previous is not None and plan.previous.ref == state.summary_ref
    assert [item.step_id for item in plan.replaced] == ["a", "b", "c"] and [item.step_id for item in plan.new] == ["c"]
    assert plan.tokens_before == plan.sources[0].tokens + plan.previous.tokens


def test_the_plan_starts_again_from_the_originals_when_a_pinned_output_was_already_replaced():
    outputs = [rec("a", size=800), rec("b", size=800), rec("c", size=800)]
    state = make_state(outputs[0], outputs[1])
    plan = cw.plan_compaction(outputs, inputs={}, keep_recent=2, state=state)   # b must now stay literal
    assert plan.previous is None
    assert [source.ref for source in plan.sources] == ["step:a:1"]
    assert [item.step_id for item in plan.replaced] == ["a"]


def test_an_output_equal_to_a_pinned_one_stays_literal_and_nothing_to_summarize_says_why():
    same = {"text": "the same output"}
    outputs = [cw.OutputRecord("a", 1, same), cw.OutputRecord("b", 1, {"text": "other"}), cw.OutputRecord("c", 1, same)]
    plan = cw.plan_compaction(outputs, inputs={}, keep_recent=1, state=None)     # c is pinned; a has the same hash
    assert [source.ref for source in plan.sources] == ["step:b:1"]
    nothing = cw.plan_compaction(outputs[:1], inputs={}, keep_recent=1, state=None)
    assert nothing.sources == () and "no output older than the last 1" in nothing.detail and nothing.tokens_before == 0
    assert cw.plan_compaction([], inputs={}, keep_recent=1, state=None).sources == ()
    assert 0 < cw.minimum_summary_tokens() < 100


def test_the_pinned_check_reads_the_context_the_next_steps_will_get():
    outputs = [rec("a", size=800), rec("b", size=800)]
    prior = [("a", outputs[0]), ("b", outputs[1])]
    inputs = {"topic": "T", "n": 3}
    plan = cw.plan_compaction(outputs, inputs=inputs, keep_recent=1, state=None)

    def context(state, run_inputs=inputs):
        return cw.StepContext(cw.build_context_parts(inputs=run_inputs, prior_outputs=prior, previous_output=None,
                                                     revision_feedback="", state=state))

    assert cw.check_pinned_in_context(plan, context(make_state(outputs[0]))) == []
    # A state that would also replace the pinned output, or inputs that changed, are caught.
    both = cw.check_pinned_in_context(plan, context(make_state(outputs[0], outputs[1])))
    assert {issue.ref for issue in both} == {"output:b"}
    changed = cw.check_pinned_in_context(plan, context(make_state(outputs[0]), {"topic": "other", "n": 3}))
    assert {issue.ref for issue in changed} == {"request.inputs"}


def test_the_state_is_the_latest_compaction_event_if_everything_it_replaced_is_still_there():
    a, b = rec("a"), rec("b")
    state = make_state(a, b)
    current = {"a": a, "b": b}.get
    reads = []

    def read(sha):
        reads.append(sha)
        return dict(state.summary)

    resolved = cw.resolve_compaction_state(log(compacted_payload(state)), current=current, read_summary=read)
    assert resolved == state and reads == [state.summary_sha256]
    older = make_state(a, summary=good_summary(objective="An older objective."))
    two = log(compacted_payload(older), compacted_payload(state))
    assert cw.resolve_compaction_state(two, current=current, read_summary=read) == state    # the later one wins
    assert cw.latest_compaction_event(two)["summary_sha256"] == state.summary_sha256
    assert cw.resolve_compaction_state([], current=current, read_summary=read) is None


@pytest.mark.parametrize("change", ["output_changed", "attempt_changed", "output_gone", "summary_missing",
                                    "summary_altered", "summary_not_valid", "ref_mismatch", "no_replaced",
                                    "malformed_entry", "bad_hash", "other_scope"])
def test_anything_doubtful_means_no_state(change):
    a = rec("a")
    state = make_state(a)
    payload = compacted_payload(state)
    current = {"a": a}.get
    stored: Any = dict(state.summary)
    if change == "output_changed":
        current = {"a": cw.OutputRecord("a", 1, {"text": "changed"})}.get
    elif change == "attempt_changed":
        current = {"a": rec("a", attempt=2)}.get
    elif change == "output_gone":
        current = {}.get
    elif change == "summary_missing":
        stored = None
    elif change == "summary_altered":
        stored = {**state.summary, "objective": "Somebody changed this."}
    elif change == "summary_not_valid":
        stored = {"schema": SUMMARY_SCHEMA, "objective": "x"}
    elif change == "ref_mismatch":
        payload["summary_ref"] = cw.blob_ref("b" * 64)
    elif change == "no_replaced":
        payload["replaced"] = []
    elif change == "malformed_entry":
        payload["replaced"] = [{"step_id": "a"}]
    elif change == "bad_hash":
        payload["summary_sha256"] = "not-a-hash"
    elif change == "other_scope":
        payload["scope"] = "task"
    assert cw.resolve_compaction_state(log(payload), current=current, read_summary=lambda sha: stored) is None


def test_an_unreadable_summary_store_is_no_state_and_never_an_error():
    a = rec("a")
    state = make_state(a)

    def broken(sha):
        raise FileNotFoundError(sha)

    assert cw.resolve_compaction_state(log(compacted_payload(state)), current={"a": a}.get, read_summary=broken) is None


# =============================================================================================================
# End to end
# =============================================================================================================

def test_with_no_profile_a_disabled_one_or_nothing_pending_the_prompts_are_what_they_were(tmp_path):
    base = baseline(tmp_path)
    assert base.run["status"] == "completed" and len(base.prompts) == 6
    hashes = [row["prompt_sha256"] for row in telemetry(base.system, base.run_id)]
    for label, options in {"disabled": dict(profile={"enabled": False}), "roomy_window": dict(window=100_000)}.items():
        system = runtime(tmp_path / label, **options)
        run_id, run = run_all(system)
        assert run["status"] == "completed"
        assert prompts_of(system) == base.prompts
        assert [row["prompt_sha256"] for row in telemetry(system, run_id)] == hashes
        assert names(run) == names(base.run)                                   # not even a context event
        assert not system.adapter.of("summary") and not system.adapter.of("check")


def test_a_step_over_the_threshold_is_compacted_before_the_next_step(tmp_path):
    base = baseline(tmp_path)
    system = runtime(tmp_path, summaries=[good_summary(), good_summary(objective="The chain now covers four notes.")])
    run_id, run = run_all(system)
    assert run["status"] == "completed"
    a, b, c, d, e = ({"text": filler(step)} for step in "abcde")

    # The order of the events: measured, finished, compacted, and only then the next step starts.
    order = [(event["event_type"], event["payload"].get("step_id")) for event in run["events"]]
    assert order.index(("context_warning", "c")) < order.index(("step_completed", "c")) \
        < order.index(("context_compacted", "c")) < order.index(("step_started", "d"))

    first, second = events(run, "context_compacted")
    assert (first["scope"], first["run_id"], first["step_id"], first["after_step_id"], first["attempt"]) == (
        "run_step", run_id, "c", "c", 1)
    assert first["cursor"] == {"kind": "step_index", "value": 2}
    # Tokens before and after, the model and the estimated cost.
    assert first["tokens_before"] == estimate_tokens_conservative(content_text(a)) + estimate_tokens_conservative(content_text(b))
    assert 0 < first["tokens_after"] < first["tokens_before"] / 2
    assert first["tokens_saved"] == first["tokens_before"] - first["tokens_after"]
    assert (first["model_id"], first["level"], first["escalated"], first["checker_model_id"]) == (
        "sum_t1", "T1", False, "sum_t1")
    assert first["summary_calls"] == 1 and (first["tokens_in"], first["tokens_out"]) == (200, 80)
    assert first["estimated_cost"] == 0.0 and first["cost_currency"] == "USD" and len(first["cost_basis"]) == 2
    assert isinstance(first["duration_ms"], int) and first["covered_count"] == 2
    # What it stands for, by hash, and what stays pinned.
    assert [(item["ref"], item["step_id"], item["attempt"]) for item in first["replaced"]] == [
        ("step:a:1", "a", 1), ("step:b:1", "b", 1)]
    assert [item["sha256"] for item in first["replaced"]] == [content_sha256(a), content_sha256(b)]
    assert first["summary_ref"] == "blob:sha256:" + first["summary_sha256"] and first["previous_summary"] is None
    assert first["validation"]["ok"] is True and set(first["validation"]["checks"].values()) == {True}
    assert set(first["validation"]["checks"]) == {"schema", "references", "coverage", "pinned", "size_reduction",
                                                  "model_check"}
    pinned = {item["name"]: item["sha256"] for item in first["pinned"]}
    assert pinned == {"request.inputs": content_sha256({"topic": TOPIC}),
                      "output:c": hashlib.sha256(cw.output_text(c).encode("utf-8")).hexdigest()}

    # The prompts: identical until the compaction, then marks, the summary and the pinned text, and smaller.
    prompts = prompts_of(system)
    assert prompts[:3] == base.prompts[:3]
    d_prompt = prompts[3]
    assert d_prompt.count("[resumido · blob:sha256:") == 2
    assert cw.summarized_mark(content_sha256(a)) in d_prompt and cw.summarized_mark(content_sha256(b)) in d_prompt
    assert "a-detail" not in d_prompt and "b-detail" not in d_prompt
    assert cw.output_text(c) in d_prompt and TOPIC in d_prompt                     # the pinned text, untouched
    assert d_prompt.count("## Context summary") == 1 and first["summary_ref"] in d_prompt
    assert d_prompt.index("Feedback:") < d_prompt.index("## Context summary") < d_prompt.index("## REQUIRED OUTPUT CONTRACT")
    assert system.adapter.of("note")[3].tokens < base.system.adapter.of("note")[3].tokens * 0.6
    assert not any(payload["step_id"] == "d" for payload in events(run, "context_warning"))   # back under the warning
    # The summary calls are not measured for compaction themselves: only the step model ever warns.
    assert {payload["model_id"] for payload in events(run, "context_warning")} == {"local_default"}

    # A second compaction extends the first one with what became old since.
    assert (second["step_id"], second["cursor"]["value"], second["covered_count"]) == ("e", 4, 2)
    assert second["previous_summary"]["ref"] == first["summary_ref"]
    assert [item["step_id"] for item in second["replaced"]] == ["a", "b", "c", "d"]
    assert second["tokens_before"] == first["tokens_after"] + sum(
        estimate_tokens_conservative(content_text(item)) for item in (c, d))
    f_prompt = prompts[5]
    assert all(cw.summarized_mark(content_sha256(item)) in f_prompt for item in (a, b, c, d))
    assert cw.output_text(e) in f_prompt and second["summary_ref"] in f_prompt and first["summary_ref"] not in f_prompt
    started = {payload["step_id"]: payload for payload in events(run, "step_started")}
    assert "context_summary" not in started["a"] and "context_summary" not in started["c"]
    assert started["d"]["context_summary"] == {"ref": first["summary_ref"], "replaced": ["a", "b"]}
    assert started["f"]["context_summary"]["replaced"] == ["a", "b", "c", "d"]

    # Nothing was deleted: the originals are in the run and as blobs, the summary too.
    assert system.store.latest_step_output(run_id, "a") == a
    blobs = EnvelopeStore(system.store.db)
    assert blobs.get_blob(content_sha256(a))["media_type"] == "application/json"
    assert json.loads(blobs.read_blob(content_sha256(a))) == a and json.loads(blobs.read_blob(content_sha256(d))) == d
    stored = blobs.read_blob(first["summary_sha256"])
    assert hashlib.sha256(stored).hexdigest() == first["summary_sha256"] and json.loads(stored)["schema"] == SUMMARY_SCHEMA
    assert blobs.get_blob(first["summary_sha256"])["media_type"] == "application/vnd.multiagent.context-summary+json"

    # The summary calls are telemetry of the agent "context" and count as calls of the run.
    context_rows = [row for row in telemetry(system, run_id) if row["agent_id"] == "context"]
    assert len(context_rows) == 4 and {row["prompt_id"] for row in context_rows} == {"context_summary"}
    assert {row["step_id"] for row in context_rows} == {"c", "e"} and all(row["success"] == 1 for row in context_rows)
    assert (run["llm_calls"], run["successful_llm_calls"]) == (6 + 4, 6)
    assert [call.kind for call in system.adapter.calls].count("summary") == 2


def test_a_retry_or_the_fallback_sees_the_same_prompt_and_the_summary_never_runs_in_the_middle_of_a_step(tmp_path):
    notes = [filler("a"), filler("b"), ProviderUnavailable("the main model is down"), filler("c"),
             filler("d"), filler("e"), filler("f")]
    system = runtime(tmp_path, notes=notes, fallback=True)
    run_id, run = run_all(system)
    assert run["status"] == "completed"
    calls = system.adapter.calls
    assert [(call.kind, call.model) for call in calls[:4]] == [
        ("note", "local_default"), ("note", "local_default"), ("note", "local_default"), ("note", "step_fallback")]
    assert calls[2].prompt == calls[3].prompt                      # the failed call and the fallback: one prompt
    assert [call.kind for call in calls[4:7]] == ["summary", "check", "note"]    # the summary comes after the step
    order = [(event["event_type"], event["payload"].get("step_id")) for event in run["events"]]
    start_c, done_c = order.index(("step_started", "c")), order.index(("step_completed", "c"))
    assert not [name for name, step in order[start_c:done_c] if name in ("context_compacted", "context_compaction_skipped")]
    assert order.index(("context_compacted", "c")) > done_c
    # Both candidates were measured against their own window; the step finished with the fallback.
    assert {payload["model_id"] for payload in events(run, "context_warning") if payload["step_id"] == "c"} == {
        "local_default", "step_fallback"}
    (first, _) = events(run, "context_compacted")
    assert first["step_id"] == "c" and first["tokens_saved"] > 0


def test_a_compaction_before_a_review_survives_the_review_a_revision_and_a_new_worker(tmp_path):
    notes = [filler("a"), filler("b"), filler("c"), filler("c2"), filler("d"), filler("e"), filler("f")]
    system = runtime(tmp_path, notes=notes, checkpoints=("c",))
    run_id = start(system)
    run = system.engine.process_run(run_id)
    assert (run["status"], run["waiting_reason"], run["waiting_step"]) == ("waiting_human", "review", "c")
    assert names(run).index("context_compacted") < names(run).index("checkpoint")     # before the review is announced
    (first,) = events(run, "context_compacted")

    system.service.changes(run_id, "Make it shorter")
    run = system.engine.process_run(run_id)                          # a later process_run: the state comes from the events
    assert run["waiting_attempt"] == 2
    revision = prompts_of(system)[3]
    assert revision.count("[resumido · blob:sha256:") == 2 and first["summary_ref"] in revision
    assert cw.output_text({"text": filler("c")}) in revision          # previous_output stays literal
    assert "Feedback: Make it shorter" in revision                    # so does the pending instruction
    assert len(events(run, "context_compacted")) == 1

    system.service.approve(run_id)
    run = system.engine.process_run(run_id)
    assert run["status"] == "completed"
    d_prompt = prompts_of(system)[4]
    assert cw.output_text({"text": filler("c2")}) in d_prompt and "c-detail" not in d_prompt   # the approved attempt
    assert d_prompt.count("[resumido · blob:sha256:") == 2 and first["summary_ref"] in d_prompt


def test_a_summary_that_does_not_validate_leaves_the_run_with_its_originals(tmp_path):
    base = baseline(tmp_path)
    invented = good_summary(decisions=[{"text": "A decision nobody took", "source_ref": "step:nobody:9"}])
    system = runtime(tmp_path, summaries=[invented] * 6)
    run_id, run = run_all(system)
    assert run["status"] == "completed" and not events(run, "context_compacted")
    skipped = events(run, "context_compaction_skipped")
    assert [payload["step_id"] for payload in skipped] == ["c", "d", "e"]     # each pending step tried, none applied
    first = skipped[0]
    assert (first["reason"], first["escalated"], first["summary_calls"]) == ("validation_failed", True, 2)   # T1, then T2
    assert first["scope"] == "run_step" and first["cursor"] == {"kind": "step_index", "value": 2}
    assert any(issue["code"] == "invented_reference" for issue in first["issues"])
    assert first["tokens_before"] > 0 and (first["tokens_in"], first["tokens_out"]) == (200, 80)
    assert prompts_of(system) == base.prompts                       # not one prompt changed
    assert not system.adapter.of("check")                           # a summary that fails the mechanical checks is not asked about


def test_without_an_eligible_model_nothing_is_compacted_and_nothing_fails(tmp_path):
    base = baseline(tmp_path)
    system = runtime(tmp_path, summary_models=False)
    run_id, run = run_all(system)
    assert run["status"] == "completed" and prompts_of(system) == base.prompts
    assert {payload["reason"] for payload in events(run, "context_compaction_skipped")} == {"no_eligible_model"}
    assert not system.adapter.of("summary") and not events(run, "context_compacted")


@pytest.mark.parametrize("sensitive_summarizer, authorized, expect_compaction", [
    (False, True, False),    # the summary models may not see sensitive data
    (True, False, False),    # they may, but the run does not authorize the class
    (True, True, True),
])
def test_sensitive_data_is_only_summarized_by_a_model_that_may_see_it(
        tmp_path, sensitive_summarizer, authorized, expect_compaction):
    system = runtime(tmp_path, sensitive_summarizer=sensitive_summarizer)
    run_id = start(system, sensitive=True)
    if authorized:
        with system.store.db.connect() as conn:
            request = json.loads(conn.execute("SELECT request_json FROM runs WHERE id=?", (run_id,)).fetchone()[0])
            request["authorized_data_classes"] = ["non_sensitive", "sensitive"]
            request["effective_permissions"] = ["data:sensitive"]
            conn.execute("UPDATE runs SET request_json=? WHERE id=?", (json.dumps(request), run_id))
            conn.commit()
    run = system.engine.process_run(run_id)
    assert run["status"] == "completed"
    assert not system.cloud.calls                                   # nothing went to a cloud provider
    if expect_compaction:
        assert events(run, "context_compacted") and {call.model for call in system.adapter.of("summary")} == {"sum_t1"}
    else:
        assert not events(run, "context_compacted") and not system.adapter.of("summary")
        assert {payload["reason"] for payload in events(run, "context_compaction_skipped")} == {"no_eligible_model"}


def test_the_summary_calls_count_in_max_llm_calls_and_a_budget_that_cannot_hold_them_skips_the_compaction(tmp_path):
    system = runtime(tmp_path, max_llm_calls=3)                     # three steps use all of it
    run_id, run = run_all(system)
    (skipped,) = events(run, "context_compaction_skipped")
    assert (skipped["reason"], skipped["step_id"], skipped["summary_calls"]) == ("budget", "c", 0)
    assert "max_llm_calls" in skipped["detail"] and not system.adapter.of("summary")
    assert run["llm_calls"] == 3 and not events(run, "context_compacted")
    # With room for one more call the summary is made, counted, and the check that does not fit skips it.
    tight = runtime(tmp_path / "tight", max_llm_calls=4)
    _, run = run_all(tight)
    (skipped,) = events(run, "context_compaction_skipped")
    assert skipped["reason"] == "budget" and skipped["summary_calls"] == 1
    assert len(tight.adapter.of("summary")) == 1 and not tight.adapter.of("check") and run["llm_calls"] == 4


def test_the_summary_calls_go_through_the_routing_profile_limits_of_the_step(tmp_path, monkeypatch):
    # A step under a routing profile books its dispatches in the profile; the summary calls are booked there too.
    from multiagent.routing_policy import RoutingDecision

    system = runtime(tmp_path)
    system.bundle.catalog.routing_profiles["capped"] = {
        "enabled": True, "candidate_model_ids": ["local_default"], "max_candidates": 1,
        "max_provider_invocations": 3, "max_retries_per_candidate": 0}
    system.bundle.catalog.routing_assignments["chain"] = {step: "capped" for step in STEPS}
    monkeypatch.setattr(system.engine.router, "select", lambda request, evidence: RoutingDecision(
        status="selected", requested_model="local_default", selected_model="local_default", profile_id="capped",
        budget_scope={"run_id": evidence.scope, "profile_id": "capped"}))
    _, run = run_all(system)
    assert [payload["step_id"] for payload in events(run, "routing_dispatch_reserved")] == ["a", "b", "c"]
    skipped = events(run, "context_compaction_skipped")        # the cap of 3 dispatches was used up by the steps
    assert skipped and skipped[0]["reason"] == "budget" and "max_provider_invocations" in skipped[0]["detail"]
    assert not system.adapter.of("summary") and not events(run, "context_compacted")
    # With room for the summary calls they are booked in the profile as well, named after the model that ran.
    roomy = runtime(tmp_path / "roomy")
    roomy.bundle.catalog.routing_profiles["capped"] = {**roomy.bundle.catalog.routing_profiles.get("capped", {}),
        "enabled": True, "candidate_model_ids": ["local_default"], "max_candidates": 1,
        "max_provider_invocations": 20, "max_retries_per_candidate": 0}
    roomy.bundle.catalog.routing_assignments["chain"] = {step: "capped" for step in STEPS}
    monkeypatch.setattr(roomy.engine.router, "select", lambda request, evidence: RoutingDecision(
        status="selected", requested_model="local_default", selected_model="local_default", profile_id="capped",
        budget_scope={"run_id": evidence.scope, "profile_id": "capped"}))
    _, run = run_all(roomy)
    reserved = events(run, "routing_dispatch_reserved")
    context_calls = [payload for payload in reserved if payload.get("purpose") == "context_summary"]
    assert len(reserved) == 6 + 4 and len(context_calls) == 4
    assert {payload["attempted_model"] for payload in context_calls} == {"sum_t1"}
    assert {payload["profile_id"] for payload in reserved} == {"capped"}


def test_a_provider_error_in_the_summary_skips_that_compaction_only(tmp_path):
    system = runtime(tmp_path, summaries=[ProviderUnavailable("the summary model is down")])
    run_id, run = run_all(system)
    assert run["status"] == "completed"
    (skipped,) = events(run, "context_compaction_skipped")
    assert (skipped["reason"], skipped["step_id"]) == ("provider_error", "c")
    (compacted,) = events(run, "context_compacted")                  # the next pending step tried again and it worked
    assert compacted["step_id"] == "d" and [item["step_id"] for item in compacted["replaced"]] == ["a", "b", "c"]


def test_an_unexpected_failure_in_the_compaction_never_fails_the_step(tmp_path, monkeypatch):
    def explode(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("multiagent.workflow_engine.summarize_context", explode)
    system = runtime(tmp_path)
    run_id, run = run_all(system)
    assert run["status"] == "completed" and not events(run, "context_compacted")
    skipped = events(run, "context_compaction_skipped")
    assert skipped and {payload["reason"] for payload in skipped} == {"internal_error"}
    assert skipped[0]["detail"] == "RuntimeError: boom" and skipped[0]["summary_calls"] == 0


def test_a_cancellation_during_the_summary_cancels_the_run(tmp_path):
    holder: dict[str, str] = {}

    def cancel_then_answer():
        system.store.update_run(holder["run"], cancel_requested=1)
        return good_summary()

    system = runtime(tmp_path, summaries=[cancel_then_answer])
    holder["run"] = run_id = start(system)
    run = system.engine.process_run(run_id)
    assert run["status"] == "cancelled" and "workflow_cancelled" in names(run)
    assert not events(run, "context_compacted") and not events(run, "context_compaction_skipped")
    assert len(system.adapter.of("note")) == 3                      # the steps after c never ran


def test_the_last_step_is_compacted_only_when_a_review_may_ask_for_a_revision(tmp_path):
    plain = runtime(tmp_path / "plain", steps="abc")
    _, run = run_all(plain)
    last = events(run, "context_warning")[-1]
    assert (last["step_id"], last["compaction_pending"]) == ("c", True)               # it did cross the threshold
    assert run["status"] == "completed" and not plain.adapter.of("summary")
    assert not events(run, "context_compacted") and not events(run, "context_compaction_skipped")
    reviewed = runtime(tmp_path / "reviewed", steps="abc", checkpoints=("c",))
    _, run = run_all(reviewed)
    assert run["status"] == "waiting_human"
    (compacted,) = events(run, "context_compacted")
    assert compacted["step_id"] == "c"


def test_keep_recent_keeps_that_many_outputs_literal(tmp_path):
    system = runtime(tmp_path, profile={"keep_recent": 2})
    run_id, run = run_all(system)
    (first, *_) = events(run, "context_compacted")
    assert [item["step_id"] for item in first["replaced"]] == ["a"]
    assert {item["name"] for item in first["pinned"]} == {"request.inputs", "output:b", "output:c"}
    d_prompt = prompts_of(system)[3]
    assert d_prompt.count("[resumido · blob:sha256:") == 1
    assert cw.output_text({"text": filler("b")}) in d_prompt and cw.output_text({"text": filler("c")}) in d_prompt


def test_outputs_too_small_to_be_worth_a_summary_cost_no_model_call(tmp_path):
    notes = [f"note {step}" for step in STEPS]
    system = runtime(tmp_path, notes=notes, window=1024)
    run_id = start(system, topic=filler("topic", 4000))               # a long input keeps every step over the window
    run = system.engine.process_run(run_id)
    assert run["status"] == "completed" and not system.adapter.of("summary")
    skipped = events(run, "context_compaction_skipped")
    assert {payload["reason"] for payload in skipped} == {"no_reduction"} and not events(run, "context_compacted")
    assert "no output older than the last 1" in skipped[0]["detail"]                  # after the first step
    assert all("smallest valid summary" in payload["detail"] for payload in skipped[1:])
    assert all(payload["summary_calls"] == 0 for payload in skipped)


@pytest.mark.parametrize("damage", ["summary_blob", "output_changed", "profile_disabled"])
def test_a_compaction_that_cannot_be_trusted_any_more_is_ignored(tmp_path, damage):
    system = runtime(tmp_path, checkpoints=("c",))
    run_id = start(system)
    run = system.engine.process_run(run_id)
    (first,) = events(run, "context_compacted")
    if damage == "summary_blob":
        blobs = EnvelopeStore(system.store.db)
        blobs.blob_path(first["summary_sha256"]).write_bytes(b'{"schema": "tampered"}')
    elif damage == "output_changed":
        with system.store.db.connect() as conn:
            conn.execute("UPDATE run_steps SET output_json=? WHERE run_id=? AND step_id='a'",
                         (json.dumps({"text": "A changed output"}), run_id))
            conn.commit()
    else:
        system.bundle.catalog.context_profiles["standard"]["enabled"] = False
    system.service.approve(run_id)
    run = system.engine.process_run(run_id)
    assert run["status"] == "completed"
    d_prompt = prompts_of(system)[3]
    assert "[resumido" not in d_prompt and "## Context summary" not in d_prompt     # the originals, in full
    assert cw.output_text({"text": filler("c")}) in d_prompt
    if damage == "output_changed":
        assert "A changed output" in d_prompt
    if damage == "profile_disabled":
        assert len(events(run, "context_compacted")) == 1 and not events(run, "context_compaction_skipped")


def test_the_context_of_a_step_is_assembled_in_one_place_from_ordered_parts(tmp_path):
    system = runtime(tmp_path, checkpoints=("c",))
    run_id = start(system)
    run = system.engine.process_run(run_id)
    state = system.engine._active_compaction(run_id, run["events"], cw_settings(system))
    assert state is not None and state.step_ids == ("a", "b")
    context = system.engine._context_for_step(
        run_id, "d", run["request"], "", brand_version=None, prior_step_ids=["a", "b", "c"], compaction=state)
    assert isinstance(context, cw.StepContext)
    assert [(part.name, part.kind, part.summarized) for part in context.parts] == [
        ("topic", "input", False), ("a_output", "step_output", True), ("b_output", "step_output", True),
        ("c_output", "step_output", False), ("previous_output", "previous_output", False),
        ("revision_feedback", "revision_feedback", False), ("context_summary", "context_summary", False)]
    assert context.part("c_output").ref == "step:c:1" and context.part("a_output").sha256 == content_sha256({"text": filler("a")})
    assert context.summary_ref == state.summary_ref and len(context.sections()) == 1
    plain = system.engine._context_for_step(
        run_id, "d", run["request"], "", brand_version=None, prior_step_ids=["a", "b", "c"])
    assert plain.sections() == [] and plain.variables()["a_output"] == cw.output_text({"text": filler("a")})


def cw_settings(system):
    from multiagent.context.estimate import context_settings_for
    return context_settings_for(system.bundle.catalog, "chain")


def test_the_legacy_social_post_path_is_measured_but_never_compacted(tmp_path):
    import cmp_harness as legacy

    tokens = legacy.prompt_tokens(tmp_path / "base")
    system = legacy.make_system(tmp_path / "run")
    legacy.set_window(system, "local_default", tokens)              # the prompt fills the whole window
    legacy.assign_context_profile(system)
    run = legacy.get(system, legacy.start(system))
    (warning,) = legacy.events(run, "context_warning")
    assert warning["level"] == "threshold" and warning["compaction_pending"] is True
    assert run["status"] == "waiting_human" and len(system.ollama.prompts) == 1
    assert not [name for name in legacy.names(run) if name in ("context_compacted", "context_compaction_skipped")]
    assert "## Context summary" not in system.ollama.prompts[0]
