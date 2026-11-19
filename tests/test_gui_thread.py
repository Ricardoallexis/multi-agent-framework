# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The run chat uses the thread API, keeps its cursor, and offers only accepted decisions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


JS = Path(__file__).resolve().parents[1] / "multiagent" / "gui" / "static" / "js"
CSS = JS.parent / "new-session.css"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def run_node(name: str, script: str, data: dict | None = None) -> dict:
    uri = json.dumps((JS / name).as_uri())
    prelude = (f"import * as m from {uri};\n"
               "const DATA = JSON.parse(process.env.DATA || 'null');\n"
               "globalThis.fetch = () => { throw new Error('pure module used the network'); };\n")
    done = subprocess.run([NODE, "--input-type=module", "-e", prelude + script], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env={**os.environ, "DATA": json.dumps(data)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
def test_poll_overlaps_the_last_seq_and_merges_synthetic_entries():
    result = run_node("thread-model.js", """
      let s = m.mergeThread(m.createThread(), {cursor: 5, status: 'running', entries: [
        {seq: 5, kind: 'step', step_id: 'write', attempt: 1, text: 'done'}]});
      const after = m.pollAfter(s);
      s = m.mergeThread(s, {cursor: 5, status: 'waiting_human', entries: [
        {seq: 5, kind: 'step', step_id: 'write', attempt: 1, text: 'done'},
        {seq: 5, kind: 'waiting', step_id: 'write', attempt: 1,
          decision: {actions: ['approve', 'changes', 'reject'], type: 'review'}}]});
      const actions = m.actionsFor(s.entries[1], s.status);
      s = m.mergeThread(s, {cursor: 6, status: 'running', entries: [
        {seq: 6, kind: 'step', step_id: 'next', attempt: 1, text: 'done'}]});
      console.log(JSON.stringify({after, kinds: s.entries.map(x => x.kind), actions,
        oldActions: m.actionsFor(s.entries[1], s.status), oldDecision: s.entries[1].decision}));
    """)
    assert result == {"after": 4, "kinds": ["step", "waiting", "step"],
                      "actions": ["approve", "changes", "reject"], "oldActions": [], "oldDecision": None}


@needs_node
def test_tool_approval_and_final_are_distinct_from_same_seq():
    result = run_node("thread-model.js", """
      const s = m.mergeThread(m.createThread(), {cursor: 8, status: 'waiting_human', entries: [
        {seq: 8, kind: 'waiting', step_id: 'tool', attempt: 2,
         decision: {type: 'tool_approval', permission_request_id: 'p-1', actions: ['approve', 'reject', 'human-submit']}}]});
      const done = m.mergeThread(s, {cursor: 8, status: 'rejected', entries: [
        {seq: 8, kind: 'final', status: 'rejected', text: 'Run rejected'}]});
      console.log(JSON.stringify({actions: m.actionsFor(s.entries[0], s.status),
        permission: s.entries[0].decision.permission_request_id,
        kinds: done.entries.map(x => x.kind), terminal: m.isTerminal(done.status)}));
    """)
    assert result == {"actions": ["approve", "reject"], "permission": "p-1",
                      "kinds": ["waiting", "final"], "terminal": True}


@needs_node
def test_new_run_route_reopens_the_thread_and_new_stays_a_new_chat():
    result = run_node("route.js", """
      console.log(JSON.stringify({new: m.parseRoute('#new'), run: m.parseRoute(m.threadHref('id/one')),
        href: m.threadHref('id/one'), old: m.parseRoute('#runs/id%2Fone')}));
    """)
    assert result == {"new": {"section": "new", "id": None},
                      "run": {"section": "new", "id": "id/one"}, "href": "#new/run/id%2Fone",
                      "old": {"section": "runs", "id": "id/one"}}


@needs_node
def test_launch_phase_remembers_the_run_in_the_same_conversation():
    result = run_node("new-session-model.js", """
      const s = m.reduce(m.createConversation(), {type: 'launched', runId: 'run-7'});
      console.log(JSON.stringify({phase: s.phase, runId: s.runId, last: s.messages.at(-1).text}));
    """)
    assert result["phase"] == "launched" and result["runId"] == "run-7"
    assert "Seguimos aquí" in result["last"]


def test_thread_is_reachable_after_launch_and_from_continue():
    chat = source("new-session.js")
    assert "useRoute()" in chat and "if (runId) return html`<${RunThread}" in chat
    assert chat.index("if (runId) return html`<${RunThread}") < chat.index('if (view === "form")')
    assert "location.hash = threadHref(run.id)" in chat
    assert 'href=${threadHref(run.id)}' in chat
    assert 'navigate("runs", run.id)' not in chat[chat.index("async function launch"):chat.index("function openForm")]
    assert '"#new/run/"' in source("route.js")


def test_cards_offer_confirmed_actions_with_existing_routes_and_safe_text():
    thread = source("thread.js")
    cards = source("thread-cards.js")
    model = source("thread-model.js")
    assert '"/gui/api/threads/runs/"' in thread and '"?after=" + after' in thread
    assert "setInterval" in thread and "document.hidden" in thread and "visibilitychange" in thread
    assert "apiPost(runPath(runId, action), token, body)" in thread
    assert "permission_request_id: entry.decision.permission_request_id" in cards
    for action in ("approve", "changes", "reject"):
        assert f'pending === "{action}"' in cards
    assert "comments: comments.map" in cards and "onAction(pending, body)" in cards
    assert "detailHref(runId)" in cards and "Ver detalle" in cards
    for text in (thread, cards, model):
        assert not re.search(r"innerHTML|dangerouslySetInnerHTML|eval\(|new Function\(|document\.write", text)
    for forbidden in ("fetch(", "apiPost", "apiGet", "document.", "window."):
        assert forbidden not in model


def test_each_thread_control_has_help_and_own_css_block():
    cards = source("thread-cards.js")
    thread = source("thread.js")
    help_text = source("help-text.js")
    topics = set(re.findall(r'helpFor\("(thread\.[a-z_]+)"\)', cards + thread))
    topics |= set(re.findall(r'topic="(thread\.[a-z_]+)"', cards + thread))
    assert topics == {"thread.section", "thread.refresh", "thread.detail", "thread.related", "thread.approve", "thread.changes",
                      "thread.reject", "thread.feedback", "thread.comment", "thread.confirm"}
    for topic in topics:
        assert f'"{topic}":' in help_text
        assert f'topic="{topic}"' in cards + thread
    assert cards.count('helpFor("thread.') >= cards.count("<button") + cards.count("<textarea") + cards.count("<select") + cards.count("<input") + cards.count("<a ")
    assert 'helpFor("thread.refresh")' in thread
    css = CSS.read_text(encoding="utf-8")
    assert "/* CHT-T02:" in css and ".thread-list" in css and ".thread-compose-slot" in css
