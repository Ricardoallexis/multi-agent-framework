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
    return data


def load() -> dict[str, Any]:
    """The whole map (a copy): ``{schema, task, stage, details}``; ``task`` and ``stage`` map key -> text, tone, explanation."""
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
