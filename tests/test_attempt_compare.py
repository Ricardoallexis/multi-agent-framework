# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Deterministic field-by-field and unified-diff tests for attempt_compare."""
from __future__ import annotations

from multiagent.attempt_compare import compare_outputs


def test_equal_outputs_produce_no_changes():
    sample = {
        "title": "Same title",
        "items": [{"title": "One", "count": 2}],
        "flags": [True, False],
    }
    assert compare_outputs(sample, {"flags": [True, False], "items": [{"count": 2, "title": "One"}], "title": "Same title"}) == []


def test_added_removed_and_changed_fields_are_reported_deterministically():
    a = {"keep": "same", "removed": "old", "changed": 1, "flag": True}
    b = {"keep": "same", "added": "new", "changed": 2, "flag": 1}

    first = compare_outputs(a, b)
    second = compare_outputs(a, b)
    assert first == second
    assert first == [
        {"path": "added", "change": "added", "before": None, "after": "new"},
        {"path": "changed", "change": "changed", "before": 1, "after": 2},
        {"path": "flag", "change": "changed", "before": True, "after": 1},
        {"path": "removed", "change": "removed", "before": "old", "after": None},
    ]


def test_nested_list_paths_and_list_length_changes():
    a = {
        "items": [
            {"title": "First"},
            {"title": "Second"},
            {"title": "Old third", "tags": ["a", "b"]},
            {"title": "Extra"},
        ]
    }
    b = {
        "items": [
            {"title": "First"},
            {"title": "Second"},
            {"title": "New third", "tags": ["a", "c", "d"]},
        ]
    }

    assert compare_outputs(a, b) == [
        {"path": "items[2].tags[1]", "change": "changed", "before": "b", "after": "c"},
        {"path": "items[2].tags[2]", "change": "added", "before": None, "after": "d"},
        {"path": "items[2].title", "change": "changed", "before": "Old third", "after": "New third"},
        {"path": "items[3]", "change": "removed", "before": {"title": "Extra"}, "after": None},
    ]


def test_long_and_multiline_text_includes_unified_diff():
    a = {
        "short": "one line",
        "multiline": "line 1\nline 2\nline 3",
        "over_200": "a" * 205,
    }
    b = {
        "short": "another line",
        "multiline": "line 1\nline 2 edited\nline 3",
        "over_200": "a" * 204 + "b",
    }

    changes = {item["path"]: item for item in compare_outputs(a, b)}
    assert "diff" not in changes["short"]

    multiline_diff = changes["multiline"]["diff"]
    assert multiline_diff.startswith("--- before\n+++ after\n@@")
    assert "-line 2\n+line 2 edited" in multiline_diff

    long_diff = changes["over_200"]["diff"]
    assert long_diff.startswith("--- before\n+++ after\n@@")
    assert f"-{'a' * 205}" in long_diff
    assert f"+{'a' * 204}b" in long_diff
