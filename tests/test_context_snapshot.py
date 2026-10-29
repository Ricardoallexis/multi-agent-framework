"""CMP-T05: the context snapshot of tasks (contract CMP-T01 section 9).

The pure rules of ``multiagent.context.snapshot`` (what is summarized and what stays literal, the stored document, the
package a reader gets, the measure and the events) and, end to end, the envelope services on a throwaway database with
scripted local models: warning and threshold, the validated summary stored as a blob, the link in ``context_refs``
(replacing the previous snapshot, the limit of 20, frozen tasks left alone), reading the envelope with the snapshot and
the events after its cursor, and the way every failure ends (a skipped event, the originals in use). No network, no
paid call, nothing outside the temp folder.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from multiagent.adapters.base import LLMAdapter, LLMResponse
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.context import (
    CONTEXT_ACTOR,
    CONTEXT_EVENT_TYPES,
    PACKAGE_SCHEMA,
    SNAPSHOT_MEDIA_TYPE,
    SNAPSHOT_SCHEMA,
    SUMMARY_SCHEMA,
    ScopeTask,
    SnapshotError,
    build_package,
    check_pinned_in_packages,
    contract_of,
    measure_context,
    parse_snapshot,
    plan_snapshot,
    snapshot_bytes,
    split_scope,
)
from multiagent.context import snapshot as cs
from multiagent.context.estimate import ContextSettings, estimate_context
from multiagent.context.summary import content_sha256
from multiagent.errors import ProviderUnavailable, StructuredOutputError
from multiagent.model_router import ModelRouter
from multiagent.schema_utils import estimate_tokens_conservative
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services import envelopes as envelope_services
from multiagent.services import tasks as task_services
from multiagent.tasks.envelopes import TASK_ENVELOPE_FIELDS, blob_ref

PROFILE = "standard"
HUGE = 10_000_000          # a window no test reaches
SHA = "c" * 64


# ----------------------------------------------------------------------------------------------------------------
# Pure rules
# ----------------------------------------------------------------------------------------------------------------

def sha_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def envelope(task_id="A", **changes) -> dict[str, Any]:
    data = {
        "schema": "multiagent.task_envelope/1", "task_id": task_id, "group_id": "G", "revision": 3, "attempt": 1,
        "assignee": "agent:a", "status": "running", "objective": f"Build {task_id}",
        "depends_on": [{"task_id": "Z", "requires": "accepted", "status": "accepted", "result_ref": "result://Z/1"}],
        "artifact_refs": [], "context_refs": ["doc://README.md"], "file_scope": [f"src/{task_id.lower()}.py"],
        "constraints": ["no push"], "gate": [{"name": "tests", "command": "test"}], "base_commit": "abc1234",
        "events_after": 9,
    }
    data.update(changes)
    return data


def result_view(task_id="A", attempt=1, **changes) -> dict[str, Any]:
    text = f"{task_id}/{attempt}"
    view = {
        "schema": "multiagent.task_result/1", "task_id": task_id, "attempt": attempt, "agent": "agent:a",
        "base_commit": "", "patch": None, "summary": f"Summary of {text}", "decisions": [f"Decision of {text}"],
        "contract_changes": "", "tests_not_run": [], "requests": [{"to": "reviewer", "what": f"Review {text}"}],
        "ref": f"result://{task_id}/{attempt}", "result_sha256": sha_of(text), "created_at": "2026-10-29T00:00:00Z",
    }
    view.update(changes)
    return view


def event(seq, type_="task.started", *, task_id="A", actor="agent:a", cause="", ref="", payload=None) -> dict[str, Any]:
    return {"seq": seq, "type": type_, "group_id": "G", "task_id": task_id, "actor": actor, "cause": cause, "ref": ref,
            "payload": payload or {}, "created_at": "2026-10-29T00:00:00Z"}


def summary_document(*, scope="task:A", cursor=7, covered_results=(), **changes) -> dict[str, Any]:
    summary = {"schema": SUMMARY_SCHEMA, "objective": "Build A", "decisions": [], "pending": [],
               "files_and_artifacts": [], "constraints": [], "errors_seen": []}
    document = {
        "schema": SNAPSHOT_SCHEMA, "scope": scope, "cursor": {"kind": "event_seq", "value": cursor},
        "summary_sha256": content_sha256(summary), "summary": summary, "tokens": {"before": 100, "after": 20, "saved": 80},
        "previous_summary": None, "producer": {"model_id": "t1", "level": "T1"}, "validation": {"ok": True},
        "pinned": [], "covered_results": [dict(item) for item in covered_results], "covered": [],
    }
    document.update(changes)
    return document


def stored_snapshot(**kwargs):
    return parse_snapshot(snapshot_bytes(summary_document(**kwargs)))


def test_the_mirrored_constants_follow_the_services():
    assert cs.TERMINAL_STATES == task_services.TERMINAL_STATES
    assert cs.FROZEN_STATES == envelope_services._FROZEN_STATES
    assert cs.EVENT_PAYLOAD_LIMIT == task_services.MAX_EVENT_PAYLOAD_BYTES
    assert cs.EVENT_CHANGES_REQUESTED == task_services._EVENT_OF_ACTION["request_changes"]
    assert cs.STATE_NEEDS_CHANGES == task_services.TRANSITIONS["request_changes"][1]
    assert set(CONTEXT_EVENT_TYPES) == {"context_warning", "context_compacted", "context_compaction_skipped"}


@pytest.mark.parametrize("scope", ["task:A", "group:G"])
def test_split_scope_accepts_task_and_group(scope):
    kind, identifier = split_scope(scope)
    assert f"{kind}:{identifier}" == scope


@pytest.mark.parametrize("scope", ["", "A", "task:", "team:A", None, 3])
def test_split_scope_rejects_everything_else(scope):
    with pytest.raises(ValueError):
        split_scope(scope)


def test_the_contract_is_taken_whole_and_leaves_out_what_changes_with_time():
    contract = contract_of(envelope())
    assert contract == {
        "objective": "Build A", "constraints": ["no push"], "gate": [{"name": "tests", "command": "test"}],
        "file_scope": ["src/a.py"], "depends_on": [{"task_id": "Z", "requires": "accepted"}], "base_commit": "abc1234"}
    assert contract_of(envelope(status="blocked", revision=9, events_after=99)) == contract


def test_plan_summarizes_old_events_and_earlier_results_and_keeps_the_last_result_literal():
    task = ScopeTask("A", "submitted", envelope(), (result_view(attempt=1), result_view(attempt=2)), "G")
    events = [event(1, "task.created"), event(2, "task.started"), event(3, "result.submitted", ref="result://A/2")]
    plan = plan_snapshot(scope="task:A", tasks=[task], events=events, previous=None, keep_recent=1)
    assert [source.ref for source in plan.sources] == ["result://A/1", "event:1", "event:2", "event:3"]
    assert plan.cursor == 3 and plan.previous is None and plan.detail == ""
    assert [item.name for item in plan.pinned] == ["contract:A", "result://A/2"]
    assert plan.pinned_names == {"A": ("contract:A", "result://A/2")}
    assert [item["ref"] for item in plan.covered_results] == ["result://A/1"]
    assert plan.tokens_before == sum(source.tokens for source in plan.sources) > 0
    # the covered result is hashed as the task_results table stores it
    assert plan.sources[0].sha256 == sha_of("A/1")


def test_keep_recent_keeps_more_results_literal():
    task = ScopeTask("A", "submitted", envelope(), tuple(result_view(attempt=n) for n in (1, 2, 3)), "G")
    plan = plan_snapshot(scope="task:A", tasks=[task], events=[], previous=None, keep_recent=2)
    assert [source.ref for source in plan.sources] == ["result://A/1"]
    assert [item.name for item in plan.pinned][1:] == ["result://A/2", "result://A/3"]


def test_rules_and_the_pending_change_request_are_pinned_and_hold_the_cursor_back():
    rules = f"rules@sha256:{SHA}"
    task = ScopeTask("A", "needs_changes", envelope(context_refs=["doc://README.md", rules]), (result_view(),), "G")
    events = [event(1, "task.created"), event(2, "task.submitted"),
              event(3, "task.changes_requested", actor="user", cause="Rename the parser"), event(4, "task.started")]
    plan = plan_snapshot(scope="task:A", tasks=[task], events=events, previous=None, keep_recent=1)
    assert plan.cursor == 2                                              # stops before the unanswered instruction
    assert [source.ref for source in plan.sources] == ["event:1", "event:2"]
    names = [item.name for item in plan.pinned]
    assert names == ["contract:A", f"rules:A", "result://A/1", "instruction:A"]
    instruction = next(item for item in plan.pinned if item.name == "instruction:A")
    assert instruction.content["cause"] == "Rename the parser" and instruction.content["seq"] == 3
    # once the task runs again the instruction is answered and no longer pinned
    running = ScopeTask("A", "running", task.envelope, task.results, "G")
    later = plan_snapshot(scope="task:A", tasks=[running], events=events, previous=None, keep_recent=1)
    assert later.cursor == 4 and "instruction:A" not in [item.name for item in later.pinned]


def test_events_of_the_context_machinery_are_not_summarized_but_the_cursor_passes_them():
    task = ScopeTask("A", "running", envelope(), (), "G")
    events = [event(1, "task.created"), event(2, "task.context_updated", actor=CONTEXT_ACTOR),
              event(3, "context_warning", actor=CONTEXT_ACTOR), event(4, "task.started")]
    plan = plan_snapshot(scope="task:A", tasks=[task], events=events, previous=None, keep_recent=1)
    assert [source.ref for source in plan.sources] == ["event:1", "event:4"] and plan.cursor == 4
    only_context = plan_snapshot(scope="task:A", tasks=[task], events=events[1:3], previous=None, keep_recent=1)
    assert only_context.sources == () and only_context.cursor == 3
    assert "no event or earlier result" in only_context.detail


def test_a_new_snapshot_extends_the_previous_one_and_covers_only_what_is_new():
    previous = stored_snapshot(cursor=2, covered_results=[{"ref": "result://A/1", "sha256": sha_of("A/1")}])
    task = ScopeTask("A", "submitted", envelope(), tuple(result_view(attempt=n) for n in (1, 2, 3)), "G")
    events = [event(1), event(2), event(3, "task.submitted"), event(4, "result.submitted", ref="result://A/3")]
    plan = plan_snapshot(scope="task:A", tasks=[task], events=events, previous=previous, keep_recent=1)
    assert [source.ref for source in plan.sources] == ["result://A/2", "event:3", "event:4"]
    assert plan.previous is not None and plan.previous.ref == previous.ref and plan.cursor == 4
    assert [item["ref"] for item in plan.covered_results] == ["result://A/1", "result://A/2"]
    assert plan.tokens_before == sum(s.tokens for s in plan.sources) + plan.previous.tokens


def test_nothing_new_means_nothing_to_summarize():
    previous = stored_snapshot(cursor=4)
    task = ScopeTask("A", "submitted", envelope(), (), "G")
    plan = plan_snapshot(scope="task:A", tasks=[task], events=[event(1), event(4)], previous=previous, keep_recent=1)
    assert plan.sources == () and plan.cursor == 4 and plan.tokens_before == 0
    assert "nothing new" in plan.detail


def test_a_terminal_task_without_envelope_keeps_nothing_literal():
    live = ScopeTask("A", "running", envelope(), (), "G")
    done = ScopeTask("B", "accepted", None, (result_view("B", 1),), "G")
    plan = plan_snapshot(scope="group:G", tasks=[live, done], events=[event(1, task_id="B")], previous=None,
                         keep_recent=1)
    assert [source.ref for source in plan.sources] == ["result://B/1", "event:1"]
    assert plan.pinned_names == {"A": ("contract:A",)}


def test_a_stored_snapshot_is_checked_when_it_is_read():
    raw = snapshot_bytes(summary_document(cursor=7))
    parsed = parse_snapshot(raw, ref_sha256=hashlib.sha256(raw).hexdigest())
    assert (parsed.scope, parsed.cursor, parsed.ref) == ("task:A", 7, blob_ref(hashlib.sha256(raw).hexdigest()))
    assert parsed.package_view()["cursor"] == {"kind": "event_seq", "value": 7}
    bad = summary_document()
    bad["summary"] = {**bad["summary"], "objective": "Something else"}
    for broken, why in [
        (summary_document(schema="multiagent.context_snapshot/2"), "schema"),
        (summary_document(scope="team:A"), "scope"),
        (summary_document(cursor=-1), "cursor"),
        (bad, "summary_sha256"),
        ({**summary_document(), "covered_results": "no"}, "covered_results"),
    ]:
        with pytest.raises(SnapshotError, match=why):
            parse_snapshot(snapshot_bytes(broken))
    with pytest.raises(SnapshotError, match="hash"):
        parse_snapshot(raw, ref_sha256="0" * 64)
    with pytest.raises(SnapshotError, match="JSON"):
        parse_snapshot(b"not json")


def test_the_package_has_the_whole_envelope_the_last_results_and_the_events_after_the_cursor():
    task = ScopeTask("A", "needs_changes", envelope(), (result_view(attempt=1), result_view(attempt=2)), "G")
    snapshot = stored_snapshot(cursor=4)
    instruction = event(6, "task.changes_requested", actor="user", cause="Rename it")
    after = [event(5, "task.submitted"), instruction, event(7, "context_warning", actor=CONTEXT_ACTOR)]
    package = build_package(task=task, scope="task:A", snapshot=snapshot, events=after, pending=instruction,
                            keep_recent=1, head=12)
    assert package["schema"] == PACKAGE_SCHEMA
    assert package["envelope"] == envelope()                              # complete, not summarized
    assert [item["ref"] for item in package["last_results"]] == ["result://A/2"]
    assert package["pending_instruction"]["cause"] == "Rename it"
    assert package["snapshot"]["cursor"] == {"kind": "event_seq", "value": 4}
    assert [item["seq"] for item in package["events"]] == [5, 6]          # the machinery's own event is hidden
    assert (package["events_after"], package["head"], package["has_more"]) == (4, 12, False)
    limited = build_package(task=task, scope="task:A", snapshot=None, events=after, pending=None, keep_recent=1,
                            head=12, limit=1)
    assert [item["seq"] for item in limited["events"]] == [5] and limited["has_more"] and limited["snapshot"] is None
    assert limited["events_after"] == 0


def test_pinned_items_are_checked_in_the_package_the_readers_get():
    rules = f"rules@sha256:{SHA}"
    task = ScopeTask("A", "needs_changes", envelope(context_refs=[rules]), (result_view(),), "G")
    instruction = event(3, "task.changes_requested", actor="user", cause="Rename it")
    events = [event(1), instruction]
    plan = plan_snapshot(scope="task:A", tasks=[task], events=events, previous=None, keep_recent=1)
    good = build_package(task=task, scope="task:A", snapshot=None, events=[], pending=instruction, keep_recent=1, head=3)
    assert check_pinned_in_packages(plan, {"A": good}) == []
    changed = copy.deepcopy(good)
    changed["envelope"]["objective"] = "Build something else"
    changed["last_results"][0]["summary"] = "edited"
    changed["pending_instruction"] = None
    codes = {(issue.code, issue.ref) for issue in check_pinned_in_packages(plan, {"A": changed})}
    assert codes == {("pinned_modified", "contract:A"), ("pinned_modified", "result://A/1"),
                     ("pinned_missing", "instruction:A")}
    assert [issue.code for issue in check_pinned_in_packages(plan, {})] == ["pinned_missing"]


def test_the_measure_counts_the_parts_and_ignores_the_context_events():
    snapshot = stored_snapshot()
    events = [event(1, payload={"note": "x" * 320}), event(2, "context_warning", actor=CONTEXT_ACTOR,
                                                           payload={"note": "y" * 3200})]
    measure = measure_context(envelope=envelope(), references=[result_view()], snapshot=snapshot, events=events)
    assert measure.envelope == estimate_tokens_conservative(json.dumps(
        envelope(), sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    assert measure.snapshot == snapshot.tokens > 0 and measure.references > 0
    assert 100 <= measure.events < 200                                    # one event of about 320 characters
    assert measure.total == measure.envelope + measure.references + measure.snapshot + measure.events
    assert measure.to_dict()["total"] == measure.total
    assert measure_context(envelope=envelope()).total == measure.envelope


def test_event_payloads_always_fit_a_team_events_row():
    payload = {"scope": "task", "cost_basis": [{"note": "n" * 400}] * 5, "issues": [
        {"check": "coverage", "code": "x", "message": "m" * 500, "ref": None}] * 12,
        "pinned": [{"name": f"contract:T{i}", "sha256": SHA} for i in range(40)], "linked": ["A"] * 5,
        "linked_count": 5, "detail": "d" * 900}
    fitted = cs.fit_event_payload(payload)
    assert len(json.dumps(fitted, separators=(",", ":")).encode("utf-8")) <= cs.EVENT_PAYLOAD_LIMIT
    assert fitted["linked_count"] == 5 and len(fitted["detail"]) <= 300 and "pinned" not in fitted
    assert len(fitted.get("issues", [])) <= 5
    with pytest.raises(ValueError):                                               # even cut, this is too much
        cs.fit_event_payload({f"field{n}": "x" * 300 for n in range(20)})
    assert len(cs.fit_event_payload({"scope": "x" * 3000})["scope"]) == 200       # one long text is just cut


def test_a_warning_with_an_oversized_model_id_or_window_still_fits_a_row():
    window = {"tokens": 1000, "source": "s" * 3000, "checked_at": "2026-10-11", "provenance": "provider_docs"}
    estimate = estimate_context(model_id="m" * 3000, estimated_tokens=800, window=window,
                                settings=ContextSettings())
    payload = cs.warning_payload(cs.event_ids(snapshot_scope="task:A", task_id="A", attempt=1), estimate,
                                 measure_context(envelope=envelope()))
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= cs.EVENT_PAYLOAD_LIMIT
    assert payload["level"] == "threshold" and payload["compaction_pending"] is True and payload["ratio"] == 0.8
    assert payload["context_window"]["tokens"] == 1000 and payload["measured"]["total"] > 0


# ----------------------------------------------------------------------------------------------------------------
# Services: a throwaway database, local scripted models, a tasks context profile
# ----------------------------------------------------------------------------------------------------------------

def price(amount, local=True):
    side = {"amount": amount, "denominator": 1_000_000, "unit": "token", "currency": "USD",
            "source": "local, no charge" if local else "unit-test rate card",
            "provenance": "local_no_charge" if local else "catalog_declared", "valid_from": "2025-01-01T00:00:00Z",
            "valid_until": "2035-01-01T00:00:00Z"}
    return {"input": dict(side), "output": {**side, "amount": amount * 2}}


def model(name, *, window, level=None, tier="local"):
    spec = {"provider": "ollama" if tier == "local" else "gemini", "model": name, "tier": tier,
            "structured_output": True, "web": False, "num_ctx": window, "keep_alive": "5m", "thinking": False,
            "capabilities": ["text_generation"], "roles": ["assistant"], "permissions": [], "permissions_required": [],
            "allowed_data_classes": ["non_sensitive"]}
    if tier != "local":
        del spec["num_ctx"], spec["keep_alive"], spec["thinking"]
        spec["context_window"] = {"tokens": window, "source": "unit test documentation", "checked_at": "2026-10-11",
                                  "provenance": "provider_docs", "model": name}
        spec["permissions"], spec["permissions_required"] = ["provider:cloud"], ["provider:cloud"]
    if level is not None:
        spec.update(level=level, classification="classified", pricing=price(0) if tier == "local" else price(0.3, False))
    return spec


def filler(label: str, size: int = 1400) -> str:
    unit = f"{label}-detail "
    return (unit * (size // len(unit) + 1))[:size]


@dataclass
class Call:
    kind: str
    prompt: str


class Summarizer(LLMAdapter):
    """A local model that answers the summary calls the way a good model would: it copies what MUST APPEAR and the
    previous summary it extends. ``summaries`` and ``checks`` queue answers (or exceptions) that win over that."""

    provider = "ollama"

    def __init__(self, summaries=(), checks=()):
        self.summaries, self.checks = list(summaries), list(checks)
        self.calls: list[Call] = []

    def derive(self, prompt: str) -> dict[str, Any]:
        must = re.search(r"MUST APPEAR \(copy the strings exactly\):\n(\{.*\})", prompt)
        wanted = json.loads(must.group(1)) if must else {}
        previous = re.search(r"PREVIOUS SUMMARY \([^)]*\):\n(\{.*\})", prompt)
        carried = json.loads(previous.group(1))["content"] if previous else {}
        out: dict[str, Any] = {"objective": "Keep the task going.", "decisions": [], "pending": [],
                               "files_and_artifacts": [], "constraints": [], "errors_seen": []}
        for key in out:
            if key != "objective":
                out[key] = list(carried.get(key, []))

        def add(section, item, key):
            if all(known[key] != item[key] for known in out[section]):
                out[section].append(item)

        for ref in wanted.get("files_and_artifacts.ref", []):
            add("files_and_artifacts", {"ref": ref, "note": ""}, "ref")
        for ref in wanted.get("decisions.source_ref", []):
            add("decisions", {"text": f"Decided in {ref}", "source_ref": ref}, "source_ref")
        for ref in wanted.get("pending.source_ref", []):
            add("pending", {"text": f"Asked in {ref}", "source_ref": ref}, "source_ref")
        for kind in wanted.get("errors_seen.type", []):
            add("errors_seen", {"type": kind, "note": "", "resolved": False}, "type")
        return out

    def generate_structured(self, *, spec, prompt, output_schema, requires_web=False, effort=None):
        kind = "check" if output_schema.__name__ == "SummaryCheckOutput" else "summary"
        self.calls.append(Call(kind, prompt))
        queue = self.checks if kind == "check" else self.summaries
        item = queue.pop(0) if queue else ({"verdict": "ok", "items": []} if kind == "check" else self.derive(prompt))
        if isinstance(item, Exception):
            raise item
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


@dataclass
class World:
    services: ApplicationServices
    catalog: Catalog
    adapter: Summarizer

    @property
    def env(self):
        return self.services.envelopes

    def window(self, tokens: int | None) -> None:
        """Change ``tasks.window_tokens`` of the profile in force (the catalog hands out copies of its profiles)."""
        self.catalog.context_profiles[PROFILE]["tasks"]["window_tokens"] = tokens

    def window_for(self, ratio: float, task_id: str = "A", **kwargs) -> None:
        """Set the window so the current context of ``task_id`` measures ``ratio`` of it."""
        total = self.env.read_task_context(task_id, **kwargs)["measure"]["total"]
        self.window(int(total / ratio))

    def events(self, task_id: str = "A", types=None) -> list[dict[str, Any]]:
        found = self.services.tasks.events(task_id=task_id, limit=1000)
        return [item for item in found if types is None or item["type"] in types]

    def context_events(self, task_id: str = "A", kind: str | None = None) -> list[dict[str, Any]]:
        return [item for item in self.events(task_id) if item["type"] in CONTEXT_EVENT_TYPES
                and (kind is None or item["type"] == kind)]


def build(tmp_path: Path, *, enabled=True, assign=True, window: int | None = HUGE, summaries=(), checks=(),
          models=None, profile=None, group_tasks=("A",)) -> World:
    root = tmp_path / "local"
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None, local_dir=root, worker_enabled=False, gemini_api_key="", openai_api_key="",
                        openai_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    folder = tmp_path / "catalog"
    folder.mkdir()
    (folder / "agents.yaml").write_text("agents: {}\n", encoding="utf-8")
    models = models if models is not None else {
        "sum_t1": model("sum-t1", window=8000, level="T1"), "sum_t2": model("sum-t2", window=8000, level="T2"),
        "step_main": model("step-main", window=3000)}
    document = {
        "models": models, "routing_profiles": {"profiles": {}, "assignments": {}},
        "context_profiles": {"profiles": {PROFILE: {"version": 1, "enabled": enabled, **(profile or {}),
                                                    "tasks": {"window_tokens": window}}},
                             "assignments": {"tasks": PROFILE} if assign else {}}}
    (folder / "models.yaml").write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    catalog = Catalog(settings, catalog_dir=folder)
    adapter = Summarizer(summaries, checks)
    services.system.catalog = catalog
    services.system.engine.router = ModelRouter(catalog, {"ollama": adapter})
    services.tasks.create_group("G", "Group", actor="user", authorized=True)
    for number, task_id in enumerate(group_tasks, start=1):
        services.tasks.create_task("G", task_id, f"Task {task_id}", actor="user", objective=f"Build {task_id} for the vendor",
                                   file_scope=[f"src/{task_id.lower()}{number}.py"])
        services.envelopes.set_task_context(task_id, actor="user", constraints=["no push", "keep the public API"])
    return World(services, catalog, adapter)


def attempt(world: World, task_id: str = "A", number: int = 1, *, agent="agent:a", feedback: str | None = None):
    """One attempt: claim and start (or restart), submit a result with decisions, a request and a patch."""
    tasks, env = world.services.tasks, world.env
    if number == 1:
        tasks.transition(task_id, "claim", actor=agent)
    elif tasks.get_task(task_id)["status"] == "submitted":
        tasks.transition(task_id, "request_changes", actor="user", cause="Another round")
    tasks.transition(task_id, "start", actor=agent)
    blob = env.put_blob(f"diff {task_id} {number}\n".encode(), actor=agent, media_type="text/x-patch")
    submitted = env.submit_result(task_id, {
        "summary": filler(f"{task_id}{number}"), "decisions": [f"Use approach {number} for {task_id}"],
        "requests": [{"to": "reviewer", "what": f"Review attempt {number}"}],
        "patch": {"blob": f"sha256:{blob['sha256']}", "files": [f"src/{task_id.lower()}_{number}.py"]}}, actor=agent)
    if feedback is not None:
        tasks.transition(task_id, "request_changes", actor="user", cause=feedback)
    return submitted


@pytest.fixture
def world(tmp_path):
    return build(tmp_path)


def snapshot_refs(world: World, task_id: str = "A") -> list[str]:
    refs = world.services.envelopes.store.task_context(task_id)["context_refs"]
    return [ref for ref in refs if world.env._is_snapshot_of(ref, f"task:{task_id}")]


# -------- off by default --------
def test_without_a_tasks_profile_nothing_is_measured_summarized_or_hidden(tmp_path):
    world = build(tmp_path, assign=False)
    attempt(world, "A", 1, feedback="Again")
    attempt(world, "A", 2)
    assert world.env.refresh_task_context("A") == {"scope": "task:A", "enabled": False, "action": "none",
                                                   "reason": "disabled"}
    assert world.context_events() == [] and world.adapter.calls == []
    package = world.env.read_task_context("A")
    assert package["snapshot"] is None and package["events_after"] == 0
    assert [item["type"] for item in package["events"]] == [item["type"] for item in world.events()]
    assert error(lambda: world.env.refresh_task_context("NOPE")).code == "not_found"


def test_a_disabled_profile_is_the_same_as_none(tmp_path):
    world = build(tmp_path, enabled=False)
    attempt(world, "A", 1)
    world.window(10)
    assert world.env.refresh_task_context("A")["enabled"] is False
    assert world.context_events() == [] and world.adapter.calls == []


def error(call) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call()
    return caught.value


# -------- measuring and warning --------
def test_below_the_warning_level_nothing_is_recorded(world):
    attempt(world, "A", 1)
    report = world.env.refresh_task_context("A")
    assert (report["enabled"], report["action"], report["level"]) == (True, "none", "ok")
    assert report["ratio"] < 0.01 and set(report["measure"]) == {"envelope", "references", "snapshot", "events", "total"}
    assert report["measure"]["references"] > 0                                    # the last result counts
    assert world.context_events() == []


def test_the_warning_is_recorded_once_per_attempt_and_the_snapshot_waits_for_the_threshold(world):
    attempt(world, "A", 1, feedback="Try again")
    world.window_for(0.66)
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["level"]) == ("warned", "warning")
    warnings = world.context_events(kind="context_warning")
    assert len(warnings) == 1
    payload = warnings[0]["payload"]
    assert warnings[0]["actor"] == CONTEXT_ACTOR and warnings[0]["task_id"] == "A"
    assert (payload["scope"], payload["snapshot_scope"], payload["task_id"], payload["attempt"]) == ("task", "task:A", "A", 1)
    assert (payload["level"], payload["compaction_pending"], payload["warning_at"], payload["threshold"]) == (
        "warning", False, 0.62, 0.7)
    assert payload["context_window"]["provenance"] == "runtime_setting" and payload["estimator"]
    assert 0.62 <= payload["ratio"] < 0.7 and payload["measured"]["total"] == payload["estimated_tokens"]
    again = world.env.refresh_task_context("A")
    assert again["action"] == "none" and len(world.context_events(kind="context_warning")) == 1
    assert world.adapter.calls == [] and snapshot_refs(world) == []
    # a new attempt warns again
    world.window(HUGE)
    attempt(world, "A", 2, feedback="Once more")
    world.window_for(0.66)
    assert world.env.refresh_task_context("A")["action"] == "warned"
    assert [event["payload"]["attempt"] for event in world.context_events(kind="context_warning")] == [1, 2]


def test_an_unknown_window_is_never_invented(tmp_path):
    world = build(tmp_path, window=None)
    attempt(world, "A", 1)                       # ending the attempt already measured it and warned
    payload = world.context_events(kind="context_warning")[0]["payload"]
    assert payload["level"] == "window_unknown"
    assert payload["context_window"] is None and payload["ratio"] is None and payload["compaction_pending"] is False
    report = world.env.refresh_task_context("A")
    assert (report["level"], report["ratio"], report["action"]) == ("window_unknown", None, "none")   # once
    assert len(world.context_events(kind="context_warning")) == 1 and world.adapter.calls == []


def test_without_tasks_window_tokens_the_window_of_the_requested_model_is_used(tmp_path):
    world = build(tmp_path, window=None)
    attempt(world, "A", 1)
    report = world.env.refresh_task_context("A", model_id="step_main")            # 3 000 tokens
    assert report["level"] == "ok" and 0 < report["ratio"] < 1
    total = report["measure"]["total"]
    assert report["ratio"] == pytest.approx(total / 3000, abs=1e-3)
    assert world.env.refresh_task_context("A", model_id="no_such_model")["level"] == "window_unknown"


# -------- the snapshot --------
def over_the_threshold(tmp_path, **kwargs) -> World:
    """Three attempts (the last one sent back for changes would pin it, so no feedback), then a window of 80 %."""
    world = build(tmp_path, **kwargs)
    attempt(world, "A", 1, feedback="Fix the naming")
    attempt(world, "A", 2, feedback="Add a test")
    attempt(world, "A", 3)
    world.window_for(0.8)
    return world


def test_at_the_threshold_the_old_context_becomes_a_validated_snapshot_linked_to_the_task(tmp_path):
    world = over_the_threshold(tmp_path)
    before_context = world.env.store.task_context("A")
    revision = world.services.tasks.get_task("A")["revision"]
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["level"]) == ("compacted", "threshold")
    ref = report["snapshot_ref"]
    assert report["linked"] == ["A"] and report["unlinked"] == [] and report["tokens_after"] < report["tokens_before"]

    # the blob: content addressed, of the media type of the contract, readable as text
    blob = world.env.store.get_blob(ref.removeprefix("blob:sha256:"))
    assert blob["media_type"] == SNAPSHOT_MEDIA_TYPE == "application/vnd.multiagent.context-summary+json"
    stored = world.env.get_artifact(ref)
    assert stored["encoding"] == "utf-8" and stored["eof"] is True
    document = json.loads(stored["content"])
    assert document["schema"] == SNAPSHOT_SCHEMA and document["scope"] == "task:A"
    assert hashlib.sha256(stored["content"].encode("utf-8")).hexdigest() == blob["sha256"]
    assert document["summary"]["schema"] == SUMMARY_SCHEMA and document["summary_sha256"] == content_sha256(document["summary"])
    covered = {item["ref"] for item in document["covered"]}
    assert {"result://A/1", "result://A/2"} <= covered and "result://A/3" not in covered   # the last one stays literal
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in document["covered"])
    assert {item["ref"] for item in document["covered_results"]} == {"result://A/1", "result://A/2"}
    assert document["cursor"] == {"kind": "event_seq", "value": report["cursor"]}
    assert document["previous_summary"] is None and document["producer"]["level"] == "T1"
    assert document["validation"]["ok"] is True and document["tokens"]["after"] < document["tokens"]["before"]
    assert {item["name"] for item in document["pinned"]} == {"contract:A", "result://A/3"}
    # the summary lists what the originals state (coverage), as the model was asked to
    refs = {item["ref"] for item in document["summary"]["files_and_artifacts"]}
    assert {"result://A/1", "result://A/2", "src/a_1.py", "src/a_2.py"} <= refs
    assert {item["source_ref"] for item in document["summary"]["decisions"]} >= {"result://A/1", "result://A/2"}

    # linked: one reference more, the others untouched, the envelope still a valid v1
    context = world.env.store.task_context("A")
    assert context["context_refs"] == [*before_context["context_refs"], ref] and context["constraints"] == before_context["constraints"]
    assert world.services.tasks.get_task("A")["revision"] == revision + 1
    envelope = world.env.get_envelope("A")
    assert tuple(envelope) == TASK_ENVELOPE_FIELDS and ref in envelope["context_refs"]

    # the events: warning with compaction pending, then compacted, both by the context actor and within a row
    kinds = [event["type"] for event in world.context_events()]
    assert kinds == ["context_warning", "context_compacted"]
    warning, compacted = world.context_events()
    assert warning["payload"]["level"] == "threshold" and warning["payload"]["compaction_pending"] is True
    payload = compacted["payload"]
    assert compacted["actor"] == CONTEXT_ACTOR and compacted["ref"] == ref and payload["summary_ref"] == ref
    assert (payload["scope"], payload["snapshot_scope"], payload["cursor"]) == ("task", "task:A", document["cursor"])
    assert payload["tokens_before"] > payload["tokens_after"] and payload["tokens_saved"] > 0
    assert payload["model_id"] == "sum_t1" and payload["level"] == "T1" and payload["escalated"] is False
    assert payload["summary_calls"] == 1 and payload["tokens_in"] == 200 and payload["linked"] == ["A"]
    assert payload["estimated_cost"] == 0 and payload["covered_count"] == len(document["covered"])
    assert len(world.adapter.of("summary")) == 1 and len(world.adapter.of("check")) == 1
    # the originals are all still there
    assert [item["attempt"] for item in world.env.list_results("A")] == [3, 2, 1]
    assert world.events("A")[0]["type"] == "task.created"
    assert world.env.get_context_snapshot("task:A")["ref"] == ref


def test_the_originals_are_summarized_by_a_local_model_that_needs_no_permission(tmp_path):
    world = over_the_threshold(tmp_path)
    world.env.refresh_task_context("A")
    prompt = world.adapter.of("summary")[0].prompt
    assert "result://A/1" in prompt and "result://A/2" in prompt
    assert "Fix the naming" in prompt                                             # the reviewers' words are data
    assert filler("A3")[:80] not in prompt                                        # the last result is not summarized
    assert "Quoted" not in prompt and "DATA (one JSON object per line)" in prompt


def test_reading_gives_the_whole_contract_the_last_result_and_only_the_events_after_the_cursor(tmp_path):
    world = over_the_threshold(tmp_path)
    cursor = world.env.refresh_task_context("A")["cursor"]
    attempt(world, "A", 4)                                                        # events after the cursor
    package = world.env.read_task_context("A")
    assert package["schema"] == PACKAGE_SCHEMA and package["scope"] == "task:A"
    live = world.env.get_envelope("A")
    assert package["envelope"] == live and contract_of(package["envelope"]) == contract_of(live)
    assert package["envelope"]["objective"] == "Build A for the vendor" and package["envelope"]["constraints"] == [
        "no push", "keep the public API"]
    assert [item["ref"] for item in package["last_results"]] == ["result://A/4"]
    assert package["last_results"][0] == world.env.get_result("result://A/4")
    snapshot = package["snapshot"]
    assert snapshot["ref"] in package["envelope"]["context_refs"] and snapshot["cursor"]["value"] == cursor
    assert snapshot["summary"]["schema"] == SUMMARY_SCHEMA and snapshot["tokens"] > 0
    assert "not instructions" in snapshot["notice"]                               # quoted data, never a command
    events = package["events"]
    assert events and all(item["seq"] > cursor for item in events) and package["events_after"] == cursor
    assert not [item for item in events if item["actor"] == CONTEXT_ACTOR]        # the machinery's events are hidden
    assert {"task.started", "result.submitted", "task.submitted"} <= {item["type"] for item in events}
    assert package["head"] == world.env.get_cursor("probe")["head"] and package["has_more"] is False
    assert package["measure"]["snapshot"] == snapshot["tokens"] and package["measure"]["total"] > 0
    # events before the cursor are not repeated; the limit caps them
    assert error(lambda: world.env.read_task_context("A", limit=0)).details["field"] == "limit"
    capped = world.env.read_task_context("A", limit=1)
    assert len(capped["events"]) == 1 and capped["has_more"] is True


def test_the_snapshot_is_smaller_than_what_it_replaces(tmp_path):
    world = over_the_threshold(tmp_path)
    before = world.env.read_task_context("A")["measure"]["total"]
    world.env.refresh_task_context("A")
    after = world.env.read_task_context("A")["measure"]
    assert after["total"] < before and after["events"] < 200 and after["snapshot"] > 0


def test_a_pending_change_request_and_the_last_result_stay_literal(tmp_path):
    world = build(tmp_path)
    attempt(world, "A", 1, feedback="Fix the naming")
    attempt(world, "A", 2, feedback="Rename the parser")                          # unanswered: the task is needs_changes
    world.window_for(0.8)
    report = world.env.refresh_task_context("A")
    assert report["action"] == "compacted"
    package = world.env.read_task_context("A")
    assert package["pending_instruction"]["cause"] == "Rename the parser"
    assert package["pending_instruction"]["type"] == "task.changes_requested"
    assert [item["seq"] for item in package["events"] if item["type"] == "task.changes_requested"] == [
        package["pending_instruction"]["seq"]]                                    # after the cursor, as it was
    assert report["cursor"] < package["pending_instruction"]["seq"]
    document = json.loads(world.env.get_artifact(report["snapshot_ref"])["content"])
    assert "Rename the parser" not in json.dumps(document["summary"])
    assert "Fix the naming" in world.adapter.of("summary")[0].prompt             # the answered one is summarized
    assert "Rename the parser" not in world.adapter.of("summary")[0].prompt
    assert [item["ref"] for item in package["last_results"]] == ["result://A/2"]
    assert any(item["name"] == "instruction:A" for item in document["pinned"])


def test_a_second_snapshot_extends_the_first_and_replaces_its_link(tmp_path):
    world = over_the_threshold(tmp_path)
    first = world.env.refresh_task_context("A")
    world.window(HUGE)                                                            # (the hook would compact by itself)
    attempt(world, "A", 4, feedback="One more")
    attempt(world, "A", 5)
    world.window_for(0.8)
    second = world.env.refresh_task_context("A")
    assert second["action"] == "compacted" and second["snapshot_ref"] != first["snapshot_ref"]
    assert second["cursor"] > first["cursor"]
    # one snapshot reference in the task, the newest
    refs = world.env.store.task_context("A")["context_refs"]
    assert snapshot_refs(world) == [second["snapshot_ref"]] and first["snapshot_ref"] not in refs
    assert len(refs) == 1
    document = json.loads(world.env.get_artifact(second["snapshot_ref"])["content"])
    assert document["previous_summary"]["ref"] == first["snapshot_ref"]
    assert {item["ref"] for item in document["covered_results"]} == {f"result://A/{n}" for n in (1, 2, 3, 4)}
    assert {item["ref"] for item in document["covered"]} >= {"result://A/3", "result://A/4"}
    assert "result://A/1" not in {item["ref"] for item in document["covered"]}    # only what is new
    prompt = world.adapter.of("summary")[-1].prompt
    assert "PREVIOUS SUMMARY" in prompt and first["snapshot_ref"] in prompt
    # the first one is still there, intact
    assert world.env.get_artifact(first["snapshot_ref"])["size"] > 0
    assert world.env.get_context_snapshot("task:A")["ref"] == second["snapshot_ref"]
    assert [event["type"] for event in world.context_events(kind="context_compacted")] == ["context_compacted"] * 2


def test_asking_again_with_nothing_new_changes_nothing(tmp_path):
    world = over_the_threshold(tmp_path)
    world.env.refresh_task_context("A")
    events = len(world.events())
    calls = len(world.adapter.calls)
    world.window(500)                                                             # still over the threshold
    again = world.env.refresh_task_context("A")
    assert (again["action"], again["reason"]) == ("none", "up_to_date")
    assert len(world.adapter.calls) == calls
    assert len(world.events()) <= events + 1                                      # at most one new warning
    assert len(world.context_events(kind="context_compacted")) == 1


def test_a_snapshot_is_linked_in_place_and_the_limit_of_references_is_kept(tmp_path):
    world = over_the_threshold(tmp_path)
    refs = [f"doc://notes/{n}.md" for n in range(20)]
    world.env.set_task_context("A", actor="user", context_refs=refs)
    report = world.env.refresh_task_context("A")
    assert report["action"] == "compacted" and report["linked"] == [] and report["unlinked"] == ["A"]
    assert world.env.store.task_context("A")["context_refs"] == refs              # untouched, still 20
    payload = world.context_events(kind="context_compacted")[0]["payload"]
    assert payload["unlinked"] == ["A"] and payload["linked"] == []
    # the snapshot exists and is read through its event even when it could not be linked
    package = world.env.read_task_context("A")
    assert package["snapshot"]["ref"] == report["snapshot_ref"]
    # with one slot free it fits
    world.env.set_task_context("A", actor="user", context_refs=refs[:19])
    world.window(HUGE)
    attempt(world, "A", 4, feedback="Again")
    attempt(world, "A", 5)
    world.window_for(0.8)
    assert world.env.refresh_task_context("A")["linked"] == ["A"]
    assert len(world.env.store.task_context("A")["context_refs"]) == 20


# -------- a group --------
def test_a_group_snapshot_is_linked_to_the_open_tasks_and_frozen_ones_are_left_alone(tmp_path):
    world = build(tmp_path, group_tasks=("A", "B", "C", "D"))
    attempt(world, "A", 1, feedback="Redo")
    attempt(world, "A", 2)
    attempt(world, "B", 1)
    attempt(world, "C", 1)
    world.services.tasks.transition("C", "accept", actor="user")                  # terminal
    attempt(world, "D", 1)
    with world.services.tasks.store.transaction() as conn:                        # validating, as run_gate does
        world.services.tasks._apply(conn, "D", "gate_start", actor="user", gate=True, event="gate.started")
    before = {task: list(world.env.store.task_context(task)["context_refs"]) for task in "ABCD"}
    world.env.set_task_context("B", actor="user", context_refs=["doc://b.md"])
    before["B"] = ["doc://b.md"]
    world.window_for(0.8, scope="group:G")
    report = world.env.refresh_task_context("A", scope="group:G")
    assert report["scope"] == "group:G" and report["action"] == "compacted"
    assert sorted(report["linked"]) == ["A", "B"] and report["unlinked"] == []
    ref = report["snapshot_ref"]
    for task, expected in {"A": [ref], "B": ["doc://b.md", ref], "C": before["C"], "D": before["D"]}.items():
        assert world.env.store.task_context(task)["context_refs"] == expected, task
    document = json.loads(world.env.get_artifact(ref)["content"])
    assert document["scope"] == "group:G"
    covered = {item["ref"] for item in document["covered"]}
    assert "result://C/1" in covered and "result://D/1" not in covered            # C is history; D reads its last result
    assert {"result://A/1", "result://B/1"} & covered == {"result://A/1"}        # B's only result is its last one
    assert {item["name"] for item in document["pinned"]} >= {"contract:A", "contract:B", "result://A/2", "result://B/1"}
    assert "contract:C" not in {item["name"] for item in document["pinned"]}
    # read through the group scope by a task of the group; a task of another group is refused
    package = world.env.read_task_context("B", scope="group:G")
    assert package["snapshot"]["ref"] == ref and package["scope"] == "group:G"
    assert error(lambda: world.env.read_task_context("B", scope="group:OTHER")).code == "invalid_request"
    assert world.env.get_context_snapshot("group:G")["ref"] == ref
    assert world.env.get_context_snapshot("task:A") is None                       # a different scope


def test_a_task_whose_envelope_cannot_be_built_is_left_out_of_the_summary_and_the_links(tmp_path, monkeypatch):
    world = build(tmp_path, group_tasks=("A", "B"))
    attempt(world, "A", 1, feedback="Redo")
    attempt(world, "A", 2)
    attempt(world, "B", 1)
    real = world.env.get_envelope

    def flaky(task_id, **kwargs):
        if task_id == "B":
            raise ServiceError("invalid_state", "This task has legacy release dependencies", status=409)
        return real(task_id, **kwargs)

    monkeypatch.setattr(world.env, "get_envelope", flaky)
    before = world.env.store.task_context("B")
    world.window_for(0.8, scope="group:G")
    report = world.env.refresh_task_context("A", scope="group:G")
    assert report["action"] == "compacted" and report["linked"] == ["A"] and report["unlinked"] == []
    assert world.env.store.task_context("B") == before                            # not linked, not touched
    assert world.env.store.task_context("A")["context_refs"] == [report["snapshot_ref"]]
    document = json.loads(world.env.get_artifact(report["snapshot_ref"])["content"])
    pinned = {item["name"] for item in document["pinned"]}
    assert "contract:A" in pinned and "contract:B" not in pinned and "result://B/1" not in pinned
    assert "result://B/1" in {item["ref"] for item in document["covered"]}        # with no envelope it keeps nothing literal


# -------- every failure ends with the originals in use --------
def test_a_summary_that_does_not_validate_is_not_stored(tmp_path):
    bad = {"objective": "Nothing about the files", "decisions": [], "pending": [], "files_and_artifacts": [],
           "constraints": [], "errors_seen": []}
    world = over_the_threshold(tmp_path, summaries=[bad, bad])
    before = world.env.store.task_context("A")
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["reason"]) == ("skipped", "validation_failed")
    assert len(world.adapter.of("summary")) == 2                                  # T1, then the T2 retry, never T3
    assert world.env.store.task_context("A") == before and snapshot_refs(world) == []
    skipped = world.context_events(kind="context_compaction_skipped")
    assert len(skipped) == 1 and skipped[0]["payload"]["reason"] == "validation_failed"
    assert skipped[0]["payload"]["escalated"] is True and skipped[0]["payload"]["issues"]
    assert world.context_events(kind="context_compacted") == []
    package = world.env.read_task_context("A")
    assert package["snapshot"] is None and package["events_after"] == 0           # the originals are used
    assert world.env.get_context_snapshot("task:A") is None


def test_a_model_that_checks_and_finds_something_missing_stops_the_compaction(tmp_path):
    world = over_the_threshold(tmp_path, checks=[{"verdict": "missing", "items": ["a decision"]}] * 2)
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["reason"]) == ("skipped", "validation_failed")
    assert snapshot_refs(world) == [] and world.env.get_context_snapshot("task:A") is None


def test_without_a_local_model_the_summary_is_never_sent_to_a_cloud_one(tmp_path):
    cloud = {"sum_t1": model("sum-t1", window=8000, level="T1", tier="cloud"),
             "sum_t2": model("sum-t2", window=8000, level="T2", tier="cloud")}
    world = over_the_threshold(tmp_path, models=cloud)
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["reason"]) == ("skipped", "no_eligible_model")
    assert world.adapter.calls == [] and snapshot_refs(world) == []


def test_a_provider_failure_or_a_bug_never_fails_the_call(tmp_path, monkeypatch):
    world = over_the_threshold(tmp_path, summaries=[ProviderUnavailable("the model is down")])
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["reason"]) == ("skipped", "provider_error")

    world = over_the_threshold(tmp_path / "second")

    def broken(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(envelope_services, "summarize_context", broken)
    report = world.env.refresh_task_context("A")
    assert (report["action"], report["reason"]) == ("skipped", "internal_error")
    assert "boom" in report["detail"]
    skipped = world.context_events(kind="context_compaction_skipped")
    assert skipped[0]["payload"]["reason"] == "internal_error" and snapshot_refs(world) == []


def test_what_cannot_shrink_enough_is_a_skip_and_no_model_is_called(tmp_path):
    world = build(tmp_path, profile={"summary": {"max_ratio": 0.05}})
    attempt(world, "A", 1)
    world.window(400)
    report = world.env.refresh_task_context("A")
    assert (report["level"], report["action"], report["reason"]) == ("threshold", "skipped", "no_reduction")
    assert "cannot shrink" in report["detail"] and world.adapter.calls == []
    skipped = world.context_events(kind="context_compaction_skipped")
    assert len(skipped) == 1 and skipped[0]["payload"]["reason"] == "no_reduction"
    assert skipped[0]["payload"]["cursor"]["value"] > 0 and snapshot_refs(world) == []


def test_a_damaged_or_foreign_snapshot_is_ignored_and_the_previous_one_is_used(tmp_path):
    world = over_the_threshold(tmp_path)
    first = world.env.refresh_task_context("A")
    world.window(HUGE)
    attempt(world, "A", 4, feedback="More")
    attempt(world, "A", 5)
    world.window_for(0.8)
    second = world.env.refresh_task_context("A")
    sha = second["snapshot_ref"].removeprefix("blob:sha256:")
    world.env.store.blob_path(sha).write_bytes(b"{}")                              # damaged on disk
    package = world.env.read_task_context("A")
    assert package["snapshot"]["ref"] == first["snapshot_ref"]
    assert any(item["seq"] > first["cursor"] for item in package["events"])      # more events to read, none lost
    world.env.store.blob_path(first["snapshot_ref"].removeprefix("blob:sha256:")).unlink()
    assert world.env.read_task_context("A")["snapshot"] is None


# -------- the hook at the end of an attempt --------
def test_ending_an_attempt_measures_and_compacts_by_itself(tmp_path):
    world = build(tmp_path)
    attempt(world, "A", 1, feedback="Fix the naming")
    attempt(world, "A", 2, feedback="Add a test")
    world.window_for(0.5, "A")
    world.window(int(world.env.read_task_context("A")["measure"]["total"] * 1.05))   # the next result pushes it over
    submitted = attempt(world, "A", 3)
    assert submitted["duplicate"] is False and submitted["task"]["status"] == "submitted"
    assert [item["type"] for item in world.context_events()][-2:] == ["context_warning", "context_compacted"]
    assert len(snapshot_refs(world)) == 1
    # a duplicate submission is not a new attempt
    calls = len(world.adapter.calls)
    again = world.env.submit_result("A", world.env.get_result("result://A/3") | {"schema": "multiagent.task_result/1"},
                                    actor="agent:a")
    assert again["duplicate"] is True and len(world.adapter.calls) == calls


def test_a_failing_refresh_never_fails_the_submission(tmp_path, monkeypatch):
    world = build(tmp_path)

    def broken(self, *args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(envelope_services.EnvelopeServices, "refresh_task_context", broken)
    submitted = attempt(world, "A", 1)
    assert submitted["duplicate"] is False and submitted["result"]["ref"] == "result://A/1"


# -------- input --------
def test_scopes_and_tasks_are_validated(world):
    attempt(world, "A", 1)
    for call in (lambda: world.env.refresh_task_context("A", scope="team:A"),
                 lambda: world.env.read_task_context("A", scope=""),
                 lambda: world.env.read_task_context("A", scope="task:B"),
                 lambda: world.env.get_context_snapshot("")):
        assert error(call).code == "invalid_request"
    long_id = error(lambda: world.env.refresh_task_context("A", model_id="m" * 101))
    assert (long_id.code, long_id.details["field"]) == ("invalid_request", "model_id")
    assert world.context_events() == []
    assert error(lambda: world.env.read_task_context("NOPE")).code == "not_found"
    assert error(lambda: world.env.get_context_snapshot("task:NOPE")).code == "not_found"
    assert error(lambda: world.env.get_context_snapshot("group:NOPE")).code == "not_found"
    assert world.env.get_context_snapshot("task:A") is None
