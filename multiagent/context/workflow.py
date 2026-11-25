# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Context compaction of workflow runs: what a step's context is made of, what can be replaced by a
summary, and the events that record it (CMP-T04, contract CMP-T01 sections 5, 8 and 10).

This module is pure: no database, models or events are touched here. The workflow engine reads the run,
calls the functions below, runs the summary (``summarize_context``, CMP-T03) and writes the events and blobs.

The context of a step as ordered parts
--------------------------------------
:func:`build_context_parts` returns the context of one step as an ordered tuple of :class:`ContextPart`:
the run inputs, the output of every earlier step (``{step}_output``), ``previous_output``,
``revision_feedback``, the ``current_date`` section (WFL-T03: today's date, never summarized) and, once the
run was compacted, the ``context_summary`` section. Each part says what it
is (``kind``), where it goes (a template variable or a section appended to the prompt) and, when it comes
from a step, its reference and hash. :class:`StepContext` turns the parts into the template variables
(:meth:`StepContext.variables`) and the sections appended to the prompt (:meth:`StepContext.sections`).
This is the one seam where the context is assembled: whatever must treat a part differently (wrap an
untrusted source, BDI-T02) works on the parts before they are turned into text.

What is compacted
-----------------
After a step finishes with ``compaction_pending`` the outputs of earlier steps are replaced, for the steps that
come next, by a mark ``[resumido · blob:sha256:<hash>]`` and a ``## Context summary`` section is appended
to the prompt, after the active skills. Never replaced (contract section 5): the run inputs, the template,
the skills and the output contract, the last ``keep_recent`` outputs (so the last step's output and
``previous_output`` stay literal) and the pending ``revision_feedback``. A revision only ever shows the
previous attempt of its own step, and ``revision_feedback`` is cleared once the step finishes, so resolved
revisions never accumulate in the context; the older step outputs are what grows.

The originals stay where they were (``run_steps``) and are also stored as blobs, so each mark names a
reference that can be read back. The state of a compaction is the latest ``context_compacted`` event of the
run (:func:`resolve_compaction_state`); it applies only while every output it replaced still has the hash
the event recorded, and without a context profile (or with it disabled) the originals are used.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from ..call_guards import SCOPE_RUN_STEP
from ..tasks.envelopes import EnvelopeError, blob_ref
from .summary import (
    SUMMARY_SCHEMA,
    PinnedItem,
    PreviousSummary,
    SummaryResult,
    SummarySchemaError,
    SummarySource,
    ValidationIssue,
    check_pinned_unchanged,
    content_sha256,
    content_text,
    render_summary_for_prompt,
    summary_artifact,
)
from ..schema_utils import estimate_tokens_conservative

_logger = logging.getLogger(__name__)

EVENT_CONTEXT_COMPACTED = "context_compacted"
EVENT_CONTEXT_COMPACTION_SKIPPED = "context_compaction_skipped"
# A failure of our own (not of a model, a budget or the validation) that stopped a compaction.
REASON_INTERNAL_ERROR = "internal_error"

SUMMARY_MEDIA_TYPE = "application/vnd.multiagent.context-summary+json"
ORIGINAL_MEDIA_TYPE = "application/json"
SUMMARY_SECTION = "context_summary"
CURRENT_DATE_SECTION = "current_date"

KIND_INPUT = "input"
KIND_STEP_OUTPUT = "step_output"
KIND_PREVIOUS_OUTPUT = "previous_output"
KIND_REVISION_FEEDBACK = "revision_feedback"
KIND_CONTEXT_SUMMARY = "context_summary"
KIND_CURRENT_DATE = "current_date"       # WFL-T03: today's date and its UTC offset, from the clock of this machine
KIND_PROJECT_MEMORY = "project_memory"   # S12.A1: an entry of the project memory, already quoted as data
PLACEMENT_VARIABLE = "variable"   # fills a ``{name}`` of the prompt template
PLACEMENT_SECTION = "section"     # appended to the prompt after the template and the active skills

PINNED_INPUTS = "request.inputs"


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def local_now() -> datetime:
    """The clock of the prompt date: the local time of this machine, with its UTC offset. Tests replace it."""
    return datetime.now().astimezone()


def current_date_text(now: datetime | None = None) -> str:
    """The sentence that tells a model today's date: ISO date, weekday and the local offset from UTC.

    ``now`` must carry a time zone; without it the local clock (:func:`local_now`) is used. The weekday is
    spelled here, not by the locale, so the text does not depend on the language of the machine.
    """
    moment = now if now is not None else local_now()
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.astimezone()
    minutes = int(moment.utcoffset().total_seconds() // 60)
    sign = "-" if minutes < 0 else "+"
    hours, rest = divmod(abs(minutes), 60)
    return (f"Today's date: {moment.date().isoformat()} ({_WEEKDAYS[moment.weekday()]}), "
            f"local time UTC{sign}{hours:02d}:{rest:02d}.")


def step_ref(step_id: str, attempt: int) -> str:
    """The reference of one attempt of a step, as the summary cites it (``step:research:1``)."""
    return f"step:{step_id}:{attempt}"


def output_text(output: Any) -> str:
    """A step output as the prompt shows it."""
    return json.dumps(output, ensure_ascii=False, indent=2)


def summarized_mark(sha256: str) -> str:
    """What replaces an output that the summary now stands for; the blob holds the original."""
    return f"[resumido · {blob_ref(sha256)}]"


# --------------------------------------------------------------------------------------------------
# The context of a step
# --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ContextPart:
    """One piece of a step's context.

    ``name`` is the template variable (``research_output``) or the section name. ``value`` is what the model
    sees (a string, except an input the run was started with, which keeps the type it came in). ``ref`` and
    ``sha256`` identify the original when the part comes from a step. ``summarized`` is true when ``value`` is
    the mark that stands for an original now covered by the summary.
    """

    name: str
    value: Any
    kind: str
    placement: str = PLACEMENT_VARIABLE
    ref: str | None = None
    sha256: str | None = None
    summarized: bool = False


@dataclass(frozen=True)
class StepContext:
    """The context of one step: its parts in order. The prompt is built from these two views of it."""

    parts: tuple[ContextPart, ...]

    def variables(self) -> dict[str, Any]:
        """The template variables, in the order of the parts (a later part of the same name wins)."""
        return {part.name: part.value for part in self.parts if part.placement == PLACEMENT_VARIABLE}

    def sections(self) -> list[str]:
        """The texts appended to the prompt after the template and the active skills, in order."""
        return [str(part.value) for part in self.parts if part.placement == PLACEMENT_SECTION]

    def part(self, name: str) -> ContextPart | None:
        return next((part for part in self.parts if part.name == name), None)

    @property
    def summary_ref(self) -> str | None:
        part = self.part(SUMMARY_SECTION)
        return part.ref if part is not None and part.kind == KIND_CONTEXT_SUMMARY else None

    def with_parts(self, parts: Sequence[ContextPart]) -> "StepContext":
        return StepContext(tuple(parts))


@dataclass(frozen=True)
class OutputRecord:
    """The latest completed output of a step, with what identifies it."""

    step_id: str
    attempt: int
    output: Mapping[str, Any]
    text: str = field(init=False)      # the output as the prompt shows it
    sha256: str = field(init=False)    # sha256 of its canonical JSON: the hash of the blob that keeps it

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", output_text(self.output))
        object.__setattr__(self, "sha256", content_sha256(self.output))

    @property
    def ref(self) -> str:
        return step_ref(self.step_id, self.attempt)


@dataclass(frozen=True)
class ReplacedOutput:
    """An output the summary stands for: step, attempt and the hash it had when it was summarized."""

    step_id: str
    attempt: int
    sha256: str

    @property
    def ref(self) -> str:
        return step_ref(self.step_id, self.attempt)

    @property
    def blob(self) -> str:
        return blob_ref(self.sha256)

    def to_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "sha256": self.sha256, "step_id": self.step_id, "attempt": self.attempt,
                "blob": self.blob}


@dataclass(frozen=True)
class CompactionState:
    """A compaction in force: the summary and the outputs it replaces."""

    summary_ref: str
    summary_sha256: str
    summary: Mapping[str, Any]
    replaced: tuple[ReplacedOutput, ...]

    def replaced_step(self, step_id: str) -> ReplacedOutput | None:
        return next((item for item in self.replaced if item.step_id == step_id), None)

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(item.step_id for item in self.replaced)


def _state_applies(state: CompactionState | None,
                   prior_outputs: Sequence[tuple[str, OutputRecord | None]]) -> bool:
    """True when ``state`` can be used for a step whose earlier outputs are ``prior_outputs``.

    Every output it replaced must be an earlier output of this step, with the attempt and hash recorded when
    it was summarized; otherwise the summary would talk about something that is not (or no longer) there.
    """
    if state is None:
        return False
    records = dict(prior_outputs)
    for item in state.replaced:
        record = records.get(item.step_id)
        if record is None or record.attempt != item.attempt or record.sha256 != item.sha256:
            return False
    return True


def build_context_parts(*, inputs: Mapping[str, Any], prior_outputs: Sequence[tuple[str, OutputRecord | None]],
                        previous_output: Mapping[str, Any] | None, revision_feedback: str,
                        state: CompactionState | None = None,
                        now: datetime | None = None) -> tuple[ContextPart, ...]:
    """The ordered context of one step of a generic workflow run.

    ``prior_outputs`` lists the earlier steps in workflow order with their latest completed output (``None``
    for a step that has none, which the prompt shows as an empty text). Without ``state``, or when it does not
    apply (:func:`_state_applies`), this is exactly the context the engine always built, plus the
    ``current_date`` section (WFL-T03): today's date, from ``now`` or the local clock. It is a section, never a
    template variable, so it is not an input a person is asked for; it is not a step output, so a compaction
    never replaces or summarizes it.
    """
    active = state if _state_applies(state, prior_outputs) else None
    parts: list[ContextPart] = [ContextPart(name, value, KIND_INPUT) for name, value in inputs.items()]
    for step_id, record in prior_outputs:
        name = f"{step_id}_output"
        if record is None:
            parts.append(ContextPart(name, "", KIND_STEP_OUTPUT))
        elif active is not None and active.replaced_step(step_id) is not None:
            parts.append(ContextPart(name, summarized_mark(record.sha256), KIND_STEP_OUTPUT, ref=record.ref,
                                     sha256=record.sha256, summarized=True))
        else:
            parts.append(ContextPart(name, record.text, KIND_STEP_OUTPUT, ref=record.ref, sha256=record.sha256))
    parts.append(ContextPart("previous_output", output_text(previous_output) if previous_output is not None else "",
                             KIND_PREVIOUS_OUTPUT))
    parts.append(ContextPart("revision_feedback", revision_feedback, KIND_REVISION_FEEDBACK))
    parts.append(ContextPart(CURRENT_DATE_SECTION, current_date_text(now), KIND_CURRENT_DATE, PLACEMENT_SECTION))
    if active is not None:
        parts.append(ContextPart(
            SUMMARY_SECTION, render_summary_for_prompt(active.summary, ref=active.summary_ref),
            KIND_CONTEXT_SUMMARY, PLACEMENT_SECTION, ref=active.summary_ref, sha256=active.summary_sha256))
    return tuple(parts)


# --------------------------------------------------------------------------------------------------
# The state of a compaction, from the events of the run
# --------------------------------------------------------------------------------------------------

def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def latest_compaction_event(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The payload of the last ``context_compacted`` event of a workflow run, or ``None``."""
    for event in reversed(list(events)):
        if event.get("event_type") != EVENT_CONTEXT_COMPACTED:
            continue
        payload = event.get("payload") or {}
        if payload.get("scope") == SCOPE_RUN_STEP:
            return payload
    return None


def resolve_compaction_state(
        events: Sequence[Mapping[str, Any]], *,
        current: Callable[[str], OutputRecord | None],
        read_summary: Callable[[str], Any]) -> CompactionState | None:
    """The compaction in force for a run, or ``None`` (then the originals are used).

    It is the latest ``context_compacted`` event, accepted only if its summary is intact and every output it
    replaced still is what was summarized. ``current(step_id)`` returns the latest completed output of a step;
    ``read_summary(sha256)`` the stored summary (parsed JSON) or ``None``. Anything doubtful means no
    compaction: it never fails and never guesses.
    """
    payload = latest_compaction_event(events)
    if payload is None:
        return None
    replaced_raw, sha = payload.get("replaced"), payload.get("summary_sha256")
    if not _is_sha256(sha) or not isinstance(replaced_raw, list) or not replaced_raw:
        _logger.warning("Ignoring a context_compacted event that does not describe its summary")
        return None
    try:
        if payload.get("summary_ref") != blob_ref(sha):
            raise EnvelopeError("summary_ref", "summary_ref does not match summary_sha256")
    except EnvelopeError:
        _logger.warning("Ignoring a context_compacted event with a summary_ref that does not match its hash")
        return None
    replaced: list[ReplacedOutput] = []
    for item in replaced_raw:
        if (not isinstance(item, Mapping) or not isinstance(item.get("step_id"), str)
                or type(item.get("attempt")) is not int or not _is_sha256(item.get("sha256"))):
            _logger.warning("Ignoring a context_compacted event with a malformed replaced list")
            return None
        entry = ReplacedOutput(item["step_id"], item["attempt"], item["sha256"])
        record = current(entry.step_id)
        if record is None or record.attempt != entry.attempt or record.sha256 != entry.sha256:
            _logger.info("The compaction of step %r is stale (its output changed); using the originals",
                         entry.step_id)
            return None
        replaced.append(entry)
    try:
        stored = read_summary(sha)
        artifact = summary_artifact(stored) if stored is not None else None
    except (SummarySchemaError, ValueError, TypeError, OSError):
        artifact = None
    if artifact is None or content_sha256(artifact) != sha:
        _logger.warning("The stored context summary %s is missing or changed; using the originals", sha)
        return None
    return CompactionState(summary_ref=blob_ref(sha), summary_sha256=sha, summary=artifact,
                           replaced=tuple(replaced))


# --------------------------------------------------------------------------------------------------
# Planning a compaction
# --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CompactionPlan:
    """What a compaction would summarize and keep.

    ``sources`` are the outputs to summarize now (empty: ``detail`` says why there is nothing to do) and
    ``previous`` the summary they extend. ``kept`` stay literal and are pinned together with the run inputs.
    ``replaced`` is every output the marks will stand for after this compaction (the ones already replaced plus
    ``sources``).
    """

    sources: tuple[SummarySource, ...]
    pinned: tuple[PinnedItem, ...]
    previous: PreviousSummary | None
    kept: tuple[OutputRecord, ...]
    replaced: tuple[ReplacedOutput, ...]
    new: tuple[ReplacedOutput, ...]
    tokens_before: int
    detail: str = ""


def plan_compaction(outputs: Sequence[OutputRecord], *, inputs: Mapping[str, Any], keep_recent: int,
                    state: CompactionState | None) -> CompactionPlan:
    """Choose what to summarize among ``outputs`` (the completed outputs so far, in workflow order).

    The last ``keep_recent`` outputs and the run inputs are pinned. Older outputs not yet replaced become the
    sources. An output with the same hash as a pinned one stays literal (a pinned item cannot also be summarized).
    If a pinned output was already replaced (``keep_recent`` grew), the previous summary is not extended: the
    summary is made again from the originals.
    """
    keep = max(1, int(keep_recent))
    kept = tuple(outputs[-keep:])
    older = list(outputs[:-keep])
    kept_ids = {record.step_id for record in kept}
    extends = state is not None and not (kept_ids & set(state.step_ids))
    base = state if extends else None
    already = {item.step_id for item in base.replaced} if base is not None else set()
    pinned_hashes = {record.sha256 for record in kept}
    fresh = [record for record in older if record.step_id not in already and record.sha256 not in pinned_hashes]
    pinned = (PinnedItem(PINNED_INPUTS, dict(inputs)),
              *(PinnedItem(f"output:{record.step_id}", record.text) for record in kept))
    previous = (PreviousSummary(base.summary_ref, base.summary) if base is not None else None)
    new = tuple(ReplacedOutput(record.step_id, record.attempt, record.sha256) for record in fresh)
    replaced = (*(base.replaced if base is not None else ()), *new)
    if not fresh:
        return CompactionPlan((), pinned, previous, kept, replaced, new, 0,
                              f"no output older than the last {keep} is left to summarize")
    sources = tuple(SummarySource(record.ref, record.output, kind="step_output") for record in fresh)
    tokens_before = sum(source.tokens for source in sources) + (previous.tokens if previous is not None else 0)
    return CompactionPlan(sources, pinned, previous, kept, replaced, new, tokens_before)


def minimum_summary_tokens() -> int:
    """Tokens of the smallest valid summary: below this the size check can never pass."""
    empty = {"schema": SUMMARY_SCHEMA, "objective": "x", "decisions": [], "pending": [], "files_and_artifacts": [],
             "constraints": [], "errors_seen": []}
    return estimate_tokens_conservative(content_text(empty))


def check_pinned_in_context(plan: CompactionPlan, context: StepContext) -> list[ValidationIssue]:
    """Problems with the pinned items in the context the next steps will get (empty when all are intact).

    The context is built by the same function the steps use, so this checks what the prompts will really
    carry: the run inputs and the last outputs must have the hashes they had before the summary was applied.
    """
    variables = context.variables()
    actual: dict[str, Any] = {}
    for item in plan.pinned:
        if item.name == PINNED_INPUTS:
            names = list(item.content) if isinstance(item.content, Mapping) else []
            actual[item.name] = {name: variables.get(name) for name in names}
        elif item.name.startswith("output:"):
            actual[item.name] = variables.get(f"{item.name.removeprefix('output:')}_output")
    return check_pinned_unchanged(plan.pinned, actual)


def next_state(plan: CompactionPlan, result: SummaryResult) -> CompactionState:
    """The state after a successful compaction (``result.ok``)."""
    assert result.ok and result.summary is not None and result.summary_sha256 is not None
    return CompactionState(summary_ref=blob_ref(result.summary_sha256), summary_sha256=result.summary_sha256,
                           summary=result.summary, replaced=plan.replaced)


# --------------------------------------------------------------------------------------------------
# Events (contract section 10)
# --------------------------------------------------------------------------------------------------

def _cost_currency(result: SummaryResult) -> str | None:
    currencies = {call.cost_currency for call in result.calls}
    return currencies.pop() if len(currencies) == 1 else None


def compacted_event(*, run_id: str, step_id: str, attempt: int, index: int, keep_recent: int,
                    plan: CompactionPlan, result: SummaryResult) -> dict[str, Any]:
    """The ``context_compacted`` payload. It is also the durable state (:func:`resolve_compaction_state`):
    ``replaced`` lists every output the marks stand for (cumulative), ``covered_count`` only those summarized now."""
    assert result.ok and result.summary_sha256 is not None
    metadata = result.metadata or {}
    return {
        "scope": SCOPE_RUN_STEP, "run_id": run_id, "step_id": step_id, "attempt": attempt, "after_step_id": step_id,
        "cursor": {"kind": "step_index", "value": index},
        **result.event_fields(),
        "cost_currency": _cost_currency(result),
        "summary_ref": blob_ref(result.summary_sha256), "summary_sha256": result.summary_sha256,
        "replaced": [item.to_dict() for item in plan.replaced],
        "previous_summary": metadata.get("previous_summary"),
        "prompt_sha256": (metadata.get("producer") or {}).get("prompt_sha256"),
        "validation": result.report.to_dict() if result.report is not None else None,
        "keep_recent": keep_recent,
    }


def skipped_event(*, run_id: str, step_id: str, attempt: int, index: int, reason: str, detail: str | None,
                  result: SummaryResult | None = None, issues: Sequence[ValidationIssue] = ()) -> dict[str, Any]:
    """The ``context_compaction_skipped`` payload: why nothing was compacted, and what the attempt cost.

    With a ``result`` that failed, its reason, detail, issues and accounting are used. With a ``result`` that was
    valid but not applied (``issues`` say why) only the accounting of its calls is kept.
    """
    payload: dict[str, Any] = {
        "scope": SCOPE_RUN_STEP, "run_id": run_id, "step_id": step_id, "attempt": attempt, "after_step_id": step_id,
        "cursor": {"kind": "step_index", "value": index},
    }
    if result is None:
        payload.update({"reason": reason, "detail": detail, "summary_calls": 0})
    else:
        fields = result.event_fields()
        if result.ok:
            fields = {key: fields[key] for key in _ACCOUNTING_FIELDS}
        payload.update(fields)
        payload["escalated"] = result.escalated        # whether the retry level was tried
        payload["cost_currency"] = _cost_currency(result)
        payload["reason"], payload["detail"] = result.reason or reason, result.detail or detail
    if issues:
        payload["issues"] = [issue.to_dict() for issue in issues]
    return payload


_ACCOUNTING_FIELDS = ("tokens_before", "summary_calls", "tokens_in", "tokens_out", "estimated_cost", "cost_basis",
                      "duration_ms")


__all__ = [
    "CURRENT_DATE_SECTION", "EVENT_CONTEXT_COMPACTED", "EVENT_CONTEXT_COMPACTION_SKIPPED", "KIND_CONTEXT_SUMMARY",
    "KIND_CURRENT_DATE", "KIND_INPUT",
    "KIND_PREVIOUS_OUTPUT", "KIND_REVISION_FEEDBACK", "KIND_STEP_OUTPUT", "ORIGINAL_MEDIA_TYPE",
    "PINNED_INPUTS", "PLACEMENT_SECTION", "PLACEMENT_VARIABLE", "REASON_INTERNAL_ERROR", "SUMMARY_MEDIA_TYPE",
    "SUMMARY_SECTION", "CompactionPlan", "CompactionState", "ContextPart", "OutputRecord", "ReplacedOutput",
    "StepContext", "build_context_parts", "check_pinned_in_context", "compacted_event", "current_date_text",
    "latest_compaction_event", "local_now", "minimum_summary_tokens", "next_state", "output_text", "plan_compaction", "resolve_compaction_state",
    "skipped_event", "step_ref", "summarized_mark",
]
