# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The single map of human labels for tasks and stages (PRY-T01 section 3); pure, like ``graph``.

``labels.json`` holds the text, tone and explanation of every label, so the services, the GUI
and the tracking tools show the same words. This module holds the rules that choose the label:
the first row of the contract's table that applies wins. It has no database or service imports.

A task label comes from the stored ``state``, the effective ``status`` (``blocked`` or ``ready``
for an ``open`` task), whether its group is authorized and whether a ``submitted`` task counts as
done because the group's legacy release evidence releases it. A stage label comes from the group's
lifecycle, its authorization and the labels of its tasks.

Progress is ``done / (total - cancelled)``; ``percent`` is ``None`` when nothing counts, never a
made-up 0 or 100.

``progress_weighted`` is the finer sibling: each stored state carries a weight from ``labels.json`` (``weights``), so
the bar moves before a task is done. It reads the stored state, not the label, because the label "doing" joins four
states. ``progress`` and its ``percent`` are unchanged.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

LABELS_SCHEMA = "maf.task-labels/1"
# Priority order of the contract's table: the first label whose condition holds is the label.
TASK_KEYS = ("done", "cancelled", "failed", "review", "changes", "doing", "todo", "blocked", "ready")
STAGE_KEYS = ("closed", "verify", "todo", "doing")
_DOING_STATES = ("claimed", "running", "paused", "validating")
_DETAIL_OF_STATE = {"paused": "paused", "validating": "validating"}
# Every stored state of a task: ``weights`` in labels.json gives each one a weight.
STORED_STATES = ("open", "claimed", "running", "paused", "submitted", "validating", "needs_changes",
                 "accepted", "failed", "cancelled")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    data = json.loads(Path(__file__).with_name("labels.json").read_text(encoding="utf-8"))
    if data.get("schema") != LABELS_SCHEMA:
        raise ValueError(f"labels.json must declare schema {LABELS_SCHEMA!r}")
    for scope, keys in (("task", TASK_KEYS), ("stage", STAGE_KEYS)):
        if list(data[scope]) != list(keys):
            raise ValueError(f"labels.json {scope} labels must be, in order: {', '.join(keys)}")
        for key in keys:
            if set(data[scope][key]) != {"text", "tone", "explanation"}:
                raise ValueError(f"labels.json {scope}.{key} needs text, tone and explanation")
    weights = data.get("weights")
    if not isinstance(weights, dict):
        raise ValueError("labels.json needs a weights map: the weight of every stored state")
    missing = [state for state in STORED_STATES if state not in weights]
    unknown = [state for state in weights if state not in STORED_STATES]
    if missing or unknown:
        raise ValueError(f"labels.json weights must name exactly the stored states; missing: {missing}, unknown: {unknown}")
    for state, weight in weights.items():
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 1:
            raise ValueError(f"labels.json weights.{state} must be a number between 0 and 1")
        if abs(weight * 100 - round(weight * 100)) > 1e-9:
            raise ValueError(f"labels.json weights.{state} must have at most two decimals")
    if weights["accepted"] != 1:
        raise ValueError("labels.json weights.accepted must be 1")
    return data


def load() -> dict[str, Any]:
    """The whole map (a copy): ``{schema, task, stage, weights, details}``; ``task`` and ``stage`` map key -> text, tone, explanation."""
    return copy.deepcopy(_load())


def task_label_key(state: str, status: str, *, authorized: bool, released: bool = False) -> str:
    """The key of a task: ``state`` is the stored state, ``status`` the effective one, ``released`` the legacy evidence."""
    if state == "accepted" or (state == "submitted" and released):
        return "done"
    if state == "cancelled":
        return "cancelled"
    if state == "failed":
        return "failed"
    if state == "submitted":
        return "review"
    if state == "needs_changes":
        return "changes"
    if state in _DOING_STATES:
        return "doing"
    if not authorized:
        return "todo"
    if status == "blocked":
        return "blocked"
    return "ready"


def stage_label_key(lifecycle_state: str, authorized: bool, task_keys: Iterable[str]) -> str:
    """The key of a stage from its lifecycle, its authorization and the label keys of its tasks."""
    keys = list(task_keys)
    if lifecycle_state == "closed":
        return "closed"
    if keys and all(key in ("done", "cancelled") for key in keys) and "done" in keys:
        return "verify"
    if not authorized or not keys:
        return "todo"
    return "doing"


def label(scope: str, key: str, state: str = "") -> dict[str, Any]:
    """The view of a label: ``{key, text, tone}`` plus ``detail`` for a paused or validating task."""
    entry = _load()[scope][key]
    view: dict[str, Any] = {"key": key, "text": entry["text"], "tone": entry["tone"]}
    detail = _load()["details"].get(_DETAIL_OF_STATE.get(state, ""))
    if scope == "task" and key == "doing" and detail:
        view["detail"] = detail
    return view


def task_label(state: str, status: str, *, authorized: bool, released: bool = False) -> dict[str, Any]:
    """The view of a task's label (see ``task_label_key`` and ``label``)."""
    return label("task", task_label_key(state, status, authorized=authorized, released=released), state)


def stage_label(lifecycle_state: str, authorized: bool, task_keys: Iterable[str]) -> dict[str, Any]:
    """The view of a stage's label (see ``stage_label_key``)."""
    return label("stage", stage_label_key(lifecycle_state, authorized, task_keys))


def counts(task_keys: Iterable[str]) -> dict[str, int]:
    """How many tasks carry each label, in table order, only for the labels that appear."""
    found = Counter(task_keys)
    return {key: found[key] for key in TASK_KEYS if found[key]}


def progress(task_keys: Iterable[str]) -> dict[str, Any]:
    """``{done, cancelled, total, percent}``: ``total`` counts every task, ``percent`` ignores the cancelled ones."""
    found = Counter(task_keys)
    total = sum(found.values())
    counted = total - found["cancelled"]
    percent = (100 * found["done"] * 2 + counted) // (2 * counted) if counted else None  # half up, integers only
    return {"done": found["done"], "cancelled": found["cancelled"], "total": total, "percent": percent}


def weight_of(state: str, *, released: bool = False) -> float:
    """The weight (0..1) of a stored state, read from ``labels.json``; a ``submitted`` task that is released weighs as ``accepted``.

    A state the file does not list is an error, never a silent 0.
    """
    weights = _load()["weights"]
    if state not in weights:
        raise ValueError(f"No weight for the task state {state!r}")
    return weights[state if not (state == "submitted" and released) else "accepted"]


def progress_weighted(states: Iterable[Any]) -> dict[str, Any]:
    """``{percent, counted}`` from stored states: each item is a state or a ``(state, released)`` pair.

    ``counted`` is every task but the cancelled ones; ``failed`` counts and adds nothing. The sum is made in
    hundredths (integers) and rounded half up like ``progress``; ``percent`` is ``None`` when nothing counts.
    """
    counted = 0
    hundredths = 0
    for item in states:
        state, released = (item, False) if isinstance(item, str) else (item[0], bool(item[1]))
        weight = weight_of(state, released=released)  # raises for an unknown state, cancelled included
        if state == "cancelled":
            continue
        counted += 1
        hundredths += round(weight * 100)
    percent = (hundredths * 2 + counted) // (2 * counted) if counted else None
    return {"percent": percent, "counted": counted}
