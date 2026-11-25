# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The current review card shows its exact artifact and readable, safe output."""
from __future__ import annotations
from tests.gui_text import ui_text

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "multiagent" / "gui" / "static" / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def source(name):
    return ui_text((JS / name))


def node_module(name, script):
    uri = json.dumps((JS / name).as_uri())
    command = [NODE, "--input-type=module", "-e", f"import * as m from {uri};\n" + script]
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60, env=os.environ.copy())
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_readable_object_list_of_objects_long_text_null_and_depth():
    result = node_module("readable.js", """
      const data = {title: 'Report', rows: [{name: 'A', count: 1}, {name: 'B', count: null}],
        note: 'x'.repeat(241), nested: {a: {b: {c: 1}}}};
      console.log(JSON.stringify(m.readable(data)));
    """)
    fields = {item["name"]: item["value"] for item in result["fields"]}
    assert result["kind"] == "fields"
    assert fields["title"] == {"kind": "text", "text": "Report"}
    assert fields["rows"]["kind"] == "table"
    assert fields["rows"]["columns"] == ["name", "count"]
    assert fields["rows"]["rows"][1][1] == {"kind": "text", "text": "—"}
    assert fields["note"]["kind"] == "long"
    assert fields["nested"]["fields"][0]["value"]["fields"][0]["value"] == {"kind": "text", "text": "{Objeto}"}


@needs_node
def test_review_selects_only_the_waiting_attempt_and_prior_steps():
    result = node_module("thread-model.js", """
      const view = {steps: [{id: 'draft'}, {id: 'review'}, {id: 'publish'}], artifacts: [
        {step_id: 'draft', attempt: 1, data: {text: 'old'}},
        {step_id: 'review', attempt: 1, data: {text: 'wrong'}},
        {step_id: 'review', attempt: 2, data: {text: 'current'}},
        {step_id: 'publish', attempt: 1, data: {text: 'future'}}]};
      const entry = {step_id: 'review', attempt: 2};
      console.log(JSON.stringify({artifact: m.reviewArtifact(view, entry), previous: m.previousStepIds(view, entry),
        missing: m.reviewArtifact(view, {...entry, attempt: 3})}));
    """)
    assert result["artifact"]["data"] == {"text": "current"}
    assert result["previous"] == ["draft"]
    assert result["missing"] is None


def test_review_is_reachable_uses_attempts_on_request_and_has_safe_help():
    thread = source("thread.js")
    cards = source("thread-cards.js")
    readable = source("readable.js")
    help_text = source("help-text.js")
    css = (JS.parent / "new-session.css").read_text(encoding="utf-8")
    assert 'apiGet(runPath(runId), token)' in thread
    assert 'reviewView=${reviewView}' in thread
    assert 'reviewArtifact(view, entry)' in cards
    assert 'runPath(runId, "steps/" + encodeURIComponent(stepId) + "/attempts")' in cards
    assert 'onClick=${showEarlier}' in cards
    assert 'counters.model_calls' in cards and 'counters.max_model_calls' in cards
    for topic in ("output", "json", "previous", "calls"):
        assert f'"thread.{topic}":' in help_text
        assert f'topic="thread.{topic}"' in cards
    assert 'helpFor("thread.previous")' in cards and 'helpFor("thread.json")' in cards
    assert not re.search(r"innerHTML|dangerouslySetInnerHTML|eval\(|new Function\(|document\.write", cards + readable)
    assert not re.search(r"fetch\(|apiGet|document\.|window\.", readable)
    assert "/* CHT-T03:" in css and 'class="usage-table-wrap"' in cards
    assert ".thread-review .usage-table-wrap { min-width: 0; max-width: 100%; overflow: auto; }" in css
    assert ".thread-readable-text { max-height: 20rem; overflow: auto;" in css
    assert ".thread-decision button.btn, .thread-head button.btn { min-height: var(--target); }" in css
    assert ".thread-decision button, .thread-head button { min-height: var(--target); }" not in css
