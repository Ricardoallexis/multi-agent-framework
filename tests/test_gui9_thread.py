# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""EJC-T01: the run thread as one conversation (reading position, folded reviews, free-text changes, quotes, failures).

The pure model and the components without hooks (``ThreadCard``) run in Node (skipped without it); the rest is read as source.
No server, no browser."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from tests.gui_text import ui_text

JS = STATIC_DIR / "js"
CSS = STATIC_DIR / "new-session.css"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

WALK = """
const flat = (v) => {
  const out = {text: [], classes: [], hrefs: []};
  const walk = (n) => {
    if (n === null || n === undefined || n === false || n === true) return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (typeof n !== "object") { out.text.push(String(n)); return; }
    const props = n.props || {};
    if (props.class) out.classes.push(props.class);
    if (typeof n.type === "function") { if (["ReadableValue", "FunctionContext"].includes(n.type.name)) walk(n.type(props)); return; }
    walk(props.children);
  };
  walk(v);
  return {text: out.text.join(" "), classes: out.classes.join(" ")};
};
"""


def node(imports: dict, body: str):
    lines = [f"import * as m{i} from {json.dumps(path.as_uri())};" for i, path in enumerate(imports)]
    result = subprocess.run([NODE, "--input-type=module", "-e", "\n".join(lines) + WALK + body], capture_output=True,
                            text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


MODEL = JS / "thread-model.js"
CARDS = JS / "thread-cards.js"
UI = JS / "i18n.js"
HUMAN = JS / "human-actions.js"


def source(name: str) -> str:
    return ui_text(JS / name)


def raw(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


@needs_node
def test_composer_hidden_when_policy_is_off_and_error_is_explicit():
    result = node({MODEL: 0}, """
      const c = m0.composerState;
      console.log(JSON.stringify({
        off: c({policy: {enabled: false, reason: "off"}, error: null, status: "running"}),
        failed: c({policy: null, error: new Error("net"), status: "running"}),
        loading: c({policy: null, error: null, status: "running"}),
        ready: c({policy: {enabled: true}, error: null, status: "running"}),
        ended: c({policy: {enabled: true}, error: null, status: "completed"}),
      }));
    """)
    assert result == {"off": "hidden", "failed": "error", "loading": "loading", "ready": "ready", "ended": "ended"}


def test_inbox_draws_nothing_when_off_and_reports_a_read_error():
    thread = raw("thread.js")
    assert 'if (shown === "hidden") return null;' in thread
    assert 't("thread.inbox.policy.error", { message: errorText(policyError) })' in thread
    assert "setPolicyError(error)" in thread and "enabled: false, reason: errorText" not in thread


@needs_node
def test_scroll_follows_only_a_reader_at_the_end():
    result = node({MODEL: 0}, """
      const entries = (n) => Array.from({length: n}, (_, i) => ({seq: i + 1, kind: "step", step_id: "s" + i}));
      const before = m0.tailKey(entries(3)), after = m0.tailKey(entries(4));
      console.log(JSON.stringify({
        atEnd: m0.isNearEnd({top: 920, height: 80, total: 1000}),
        nearEnd: m0.isNearEnd({top: 850, height: 80, total: 1000}),
        reading: m0.isNearEnd({top: 100, height: 80, total: 1000}),
        followNew: m0.shouldFollow(true, before, after),
        keepReader: m0.shouldFollow(false, before, after),
        sameTail: m0.shouldFollow(true, before, before),
      }));
    """)
    assert result == {"atEnd": True, "nearEnd": True, "reading": False, "followNew": True, "keepReader": False,
                      "sameTail": False}
    thread = raw("thread.js")
    assert "shouldFollow(following.current, before, tail)" in thread and "scrollIntoView" in thread
    assert 'addEventListener("scroll", onScroll, true)' in thread


@needs_node
def test_earlier_reviews_fold_and_the_current_stop_keeps_its_actions():
    result = node({MODEL: 0}, """
      const decision = {type: "review", actions: ["approve", "changes", "reject"]};
      const page = {status: "waiting_human", cursor: 6, entries: [
        {seq: 1, kind: "launched"}, {seq: 2, kind: "waiting", reason: "review", step_id: "draft", attempt: 1, decision},
        {seq: 3, kind: "user_message", message_id: "m1", text: "shorter"},
        {seq: 4, kind: "step", step_id: "draft", attempt: 2},
        {seq: 5, kind: "waiting", reason: "review", step_id: "draft", attempt: 2, decision}]};
      const state = m0.mergeThread(m0.createThread(), page);
      const groups = m0.groupThread(state.entries, state.status);
      console.log(JSON.stringify({
        types: groups.map((g) => g.type),
        folded: groups.filter((g) => g.type === "earlier").map((g) => g.entries.map((e) => e.attempt)),
        actions: state.entries.filter((e) => e.kind === "waiting").map((e) => m0.actionsFor(e, state.status)),
        later: m0.groupThread(state.entries, "running").filter((g) => g.type === "earlier").length,
      }));
    """)
    assert result["types"] == ["entry", "earlier", "entry", "entry", "entry"]
    assert result["folded"] == [[1]]
    assert result["actions"] == [[], ["approve", "changes", "reject"]]
    assert result["later"] == 2       # with nothing waiting, every earlier stop folds


@needs_node
def test_entries_sharing_a_seq_survive_and_a_terminal_run_keeps_its_closing_message():
    result = node({MODEL: 0}, """
      const first = m0.mergeThread(m0.createThread(), {status: "running", cursor: 4, entries: [
        {seq: 4, kind: "step", step_id: "a"}, {seq: 4, kind: "step", step_id: "b"}]});
      const overlap = m0.mergeThread(first, {status: "running", cursor: 4, entries: [{seq: 4, kind: "step", step_id: "b"}]});
      const failed = m0.mergeThread(overlap, {status: "failed", cursor: 4, entries: []});
      const given = m0.mergeThread(overlap, {status: "failed", cursor: 4, entries: [
        {seq: 4, kind: "final", status: "failed", text: "Run failed", error: "boom: step b"}]});
      console.log(JSON.stringify({
        same: overlap.entries.map((e) => e.step_id),
        closing: failed.entries.slice(-1).map((e) => [e.kind, e.status, e.error]),
        detail: given.entries.slice(-1).map((e) => e.error),
        finals: given.entries.filter((e) => e.kind === "final").length,
      }));
    """)
    assert result["same"] == ["a", "b"]
    assert result["closing"] == [["final", "failed", ""]]
    assert result["detail"] == ["boom: step b"] and result["finals"] == 1


@needs_node
def test_a_failed_function_step_ends_the_thread_with_its_detail():
    result = node({CARDS: 0, UI: 1}, """
      m1.setLanguage("en");
      const final = {seq: 9, kind: "final", status: "failed", text: "Run failed", error: "function_step_failed: bad input"};
      const wait = {seq: 8, kind: "waiting", reason: "function_failure", text: "Waiting: function_failure", step_id: "calc",
        code: "function_error", detail: "division by zero", decision: null};
      const card = (entry, status = "failed") => flat(m0.ThreadCard({entry, status, runId: "r1", token: "t", reviewView: null,
        busy: false, onAction() {}, mode: "simple"}));
      console.log(JSON.stringify({final: card(final), wait: card(wait)}));
    """)
    assert "The run failed." in result["final"]["text"] and "function_step_failed: bad input" in result["final"]["text"]
    assert "thread-failure" in result["final"]["classes"]
    wait = result["wait"]["text"]
    assert "A function step failed and is waiting for you." in wait and "Error code: function_error" in wait
    assert "division by zero" in wait


@needs_node
def test_function_review_shows_inputs_and_outputs_before_the_approval():
    result = node({CARDS: 0, UI: 1}, """
      m1.setLanguage("en");
      const entry = {seq: 5, kind: "waiting", reason: "function_review", text: "Waiting: function_review", step_id: "calc",
        inputs: {amount: "12"}, prior_outputs: {draft: {total: 7}}, decision: null};
      console.log(JSON.stringify(flat(m0.ThreadCard({entry, status: "running", runId: "r1", token: "t", reviewView: null,
        busy: false, onAction() {}, mode: "simple"}))));
    """)
    text = result["text"]
    assert "Run inputs" in text and "amount" in text and "12" in text
    assert "Earlier outputs it will read" in text and "total" in text
    spanish = (JS.parent / "i18n" / "es" / "thread.js").read_text(encoding="utf-8")
    assert "Aprobar permite que la función se ejecute una sola vez." in spanish
    assert 't("thread.function.approve_once")' in raw("thread-cards.js")


@needs_node
def test_no_mode_shows_the_model_call_counter():
    result = node({CARDS: 0, UI: 1}, """
      m1.setLanguage("en");
      const props = {artifact: {data: {answer: "hi"}}, counters: {model_calls: 2, max_model_calls: 9}, steps: ["draft"],
        earlier: null, loading: false, loadError: false, showEarlier() {}};
      console.log(JSON.stringify(["simple", "advanced", undefined].map((mode) => flat(m0.ReviewBody({...props, mode})).text)));
    """)
    for text in result:
        assert "Model calls" not in text and "2 of 9" not in text
    cards = raw("thread-cards.js")
    assert "thread.review.calls" not in cards and 'topic="thread.calls"' not in cards


@needs_node
def test_changes_with_free_text_alone_and_structured_comments_only_in_advanced():
    result = node({MODEL: 0}, """
      const c = [{target: "", kind: "fix", text: "", quote: ""}, {target: "title", kind: "add", text: " longer ", quote: ""}];
      console.log(JSON.stringify({
        free: m0.changesBody("  make it shorter ", [], {structured: false}),
        emptyOnly: m0.changesBody("", [c[0]], {structured: true}),
        nothing: m0.changesBody("", [], {structured: true}),
        simpleIgnores: m0.changesBody("ok", c, {structured: false}),
        advanced: m0.changesBody("ok", c, {structured: true}),
        advancedOnly: m0.changesBody("", [c[1]], {structured: true}),
        half: m0.changesBody("ok", [{target: "", kind: "fix", text: "x", quote: ""}], {structured: true}),
        quoteHalf: m0.changesBody("ok", [{target: "a", kind: "fix", text: "", quote: "q"}], {structured: true}),
        many: m0.changesBody("", Array.from({length: 40}, () => c[1]), {structured: true}).body.comments.length,
      }));
    """)
    assert result["free"] == {"body": {"feedback": "make it shorter"}}
    assert result["emptyOnly"] == result["nothing"] == {"error": "required"}
    assert result["simpleIgnores"] == {"body": {"feedback": "ok"}}
    comment = {"target": "title", "kind": "add", "text": "longer", "quote": None}
    assert result["advanced"] == {"body": {"feedback": "ok", "comments": [comment]}}
    assert result["advancedOnly"] == {"body": {"feedback": "", "comments": [comment]}}
    assert result["half"] == result["quoteHalf"] == {"error": "incomplete"}
    assert result["many"] == 40            # the client sets no limit on corrections


def test_structured_fields_are_advanced_only_in_both_views():
    cards, human = raw("thread-cards.js"), raw("human-actions.js")
    assert 'const structured = showRawOutput(mode);' in cards
    assert 'pending === "changes" && structured && html`<div class="thread-comments">' in cards
    assert "const structured = showRawOutput(currentMode());" in human
    assert "structured && comments.map" in human and "structured && html`<div class=\"action-item\">" in human
    assert "changesBody(feedback, comments, { structured })" in human


@needs_node
def test_quote_is_plain_text_in_front_of_the_message():
    result = node({MODEL: 0}, """
      console.log(JSON.stringify({
        one: m0.quoteInto("", "the <b>total</b>"),
        many: m0.quoteInto("fix it", "line one\\nline two"),
        empty: m0.quoteInto("keep", "   "),
        long: m0.quoteInto("", "x".repeat(5000)).length,
      }));
    """)
    assert result["one"] == "> the <b>total</b>\n\n"          # kept as text; nothing is interpreted
    assert result["many"] == "> line one\n> line two\n\nfix it"
    assert result["empty"] == "keep"
    assert result["long"] <= 1500 + len("> ") + 1 + 2          # the clipped text, its marker and the blank line
    cards = raw("thread-cards.js")
    assert 'helpFor("thread.feedback")' in cards and 't("thread.quote")' in cards and "onQuote(selected)" in cards
    assert "textarea, input, select" in cards                   # a selection inside a form field is not a quote


@needs_node
def test_model_limit_notice_uses_only_the_limit_of_the_run_detail():
    result = node({MODEL: 0, CARDS: 1, UI: 2}, """
      m2.setLanguage("en");
      const final = {seq: 9, kind: "final", status: "failed", text: "Run failed", error: "quota"};
      const draw = (limit, entry = final) => flat(m1.ThreadCard({entry, status: "failed", runId: "r", token: "t",
        reviewView: null, busy: false, onAction() {}, limit, mode: "simple"})).text;
      const base = {code: "limit_reached", resets_at: null, target: "m1"};
      console.log(JSON.stringify({
        none: m0.modelLimitNotice(null), other: m0.modelLimitNotice({code: "x", target: "m1"}),
        drawnNone: draw(null),
        suggested: draw({...base, suggestions: ["m2", "m3"]}),
        only: draw({...base, suggestions: []}),
        cost: draw({...base, suggestions: ["m2"], context_cost: "3k tokens"}),
        notFinal: draw({...base, suggestions: ["m2"]}, {seq: 3, kind: "step", step_id: "a"}),
      }));
    """)
    assert result["none"] is None and result["other"] is None
    assert "usage limit" not in result["drawnNone"] and "usage limit" not in result["notFinal"]
    assert "m1 has reached its usage limit. You can switch to m2." in result["suggested"]
    assert "m3" not in result["suggested"]
    assert "m1 has reached its usage limit." in result["only"] and "switch" not in result["only"]
    assert "Switch to m2; moving this context is estimated to cost 3k tokens." in result["cost"]
    cards = raw("thread-cards.js")
    assert "<button" not in cards[cards.index("const notice ="):cards.index("const notice =") + 200]   # it informs, never switches
    assert not re.search(r"fetch\(|apiGet|apiPost|document\.|window\.", raw("thread-model.js"))



def test_new_texts_exist_in_english_and_spanish_with_the_given_wording():
    en = (STATIC_DIR / "i18n" / "en" / "thread.js").read_text(encoding="utf-8")
    es = (STATIC_DIR / "i18n" / "es" / "thread.js").read_text(encoding="utf-8")
    for key, english, spanish in (
        ("thread.quote", "Quote", "Citar"),
        ("thread.earlier_reviews", "Earlier reviews", "Revisiones anteriores"),
        ("thread.failed", "The run failed.", "La ejecución falló."),
    ):
        assert f'"{key}": "{english}"' in en and f'"{key}": "{spanish}"' in es
    keys = lambda text: set(re.findall(r'^  "([\w.]+)":', text, re.M))
    assert keys(en) == keys(es)


def test_no_executable_html_and_the_css_block_keeps_the_thread_inside_the_width():
    for name in ("thread.js", "thread-model.js", "thread-cards.js", "human-actions.js"):
        assert not re.search(r"innerHTML|dangerouslySetInnerHTML|eval\(|new Function\(|document\.write", raw(name)), name
    css = CSS.read_text(encoding="utf-8")
    assert css.count("/* EJC-T01: thread */") == 1
    block = css[css.index("/* EJC-T01: thread */"):]
    assert ".thread-msg-user" in block and ".thread-earlier-reviews" in block
    assert "width: min(100%, 46rem)" in block                        # a message never exceeds the column
    assert "@media (max-width: 599.98px)" in block                  # the phone widths (360 and 390 px) use one column
    assert "100vw" not in block and not re.search(r"(?<!max-)width:\s*\d{3,}px", block)
    assert ".thread-list > .thread-card { width: 100%; }" in block


def test_user_bubble_keeps_links_and_help_readable():
    """Round 3: inside the person's accent bubble every inherited colour is set from variables; the popover keeps --text."""
    block = CSS.read_text(encoding="utf-8")
    block = block[block.index("/* EJC-T01: thread */"):]
    assert re.search(r"\.thread-msg-user a,[^{]*\.thread-msg-user \.hint-text,[^{]*\.thread-msg-user \.help-toggle \{ color: var\(--on-accent\); \}", block)
    assert ".thread-list > .thread-msg-user .help-more { color: var(--text); }" in block
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block)         # variables only


def test_quote_has_its_own_help_in_both_views():
    cards = raw("thread-cards.js")
    assert 'helpFor("thread.quote")' in cards and '<${Help} topic="thread.quote" compact=${true} />' in cards
    assert 'helpFor("thread.feedback")}\n    onMouseDown' not in cards
    assert "QuoteButton" in raw("human-actions.js")              # the same component, so the help id exists in both views
    assert '"thread.quote": {' in source("help-text.js") and '"help.thread.quote"' in (STATIC_DIR / "i18n" / "es" / "runs.js").read_text(encoding="utf-8")


@needs_node
def test_a_smooth_scroll_started_by_the_page_does_not_turn_following_off():
    result = node({MODEL: 0}, """
      const far = {top: 100, height: 80, total: 1000}, end = {top: 920, height: 80, total: 1000};
      console.log(JSON.stringify({
        during: m0.nextFollowing(true, 1700, 1000, far),     // inside the window: keep what we knew
        after: m0.nextFollowing(true, 1700, 1800, far),      // outside: the person really is above
        back: m0.nextFollowing(false, 0, 1800, end),
      }));
    """)
    assert result == {"during": True, "after": False, "back": True}
    thread = raw("thread.js")
    assert "guardUntil.current = Date.now()" in thread and "nextFollowing(following.current, guardUntil.current" in thread


@needs_node
def test_function_failure_offers_no_approve_anywhere():
    result = node({MODEL: 0}, """
      const entry = {kind: "waiting", reason: "function_failure", decision: {actions: ["changes", "reject"]}};
      console.log(JSON.stringify(m0.actionsFor(entry, "waiting_human")));
    """)
    assert result == ["changes", "reject"]
    human = raw("human-actions.js")
    branch = human[human.index('waiting.reason === "function_review" || waiting.reason === "function_failure"'):]
    branch = branch[:branch.index("} else if (waiting && view.pending_human)")]
    assert "${review && html`" in branch and branch.index("${review && html`") < branch.index('runPath(id, "approve")')


def test_model_limit_texts_in_both_languages():
    en = (STATIC_DIR / "i18n" / "en" / "thread.js").read_text(encoding="utf-8")
    es = (STATIC_DIR / "i18n" / "es" / "thread.js").read_text(encoding="utf-8")
    for key, english, spanish in (
        ("thread.model_limit", "{model} has reached its usage limit. Switch to {suggested_model}; moving this context is estimated to cost {context_cost}.",
         "Se alcanzó el límite de uso de {model}. Cambia a {suggested_model}; trasladar este contexto cuesta aproximadamente {context_cost}."),
        ("thread.model_limit_switch", "{model} has reached its usage limit. You can switch to {suggested_model}.",
         "Se alcanzó el límite de uso de {model}. Puedes cambiar a {suggested_model}."),
        ("thread.model_limit_only", "{model} has reached its usage limit.", "Se alcanzó el límite de uso de {model}."),
    ):
        assert f'"{key}": "{english}"' in en and f'"{key}": "{spanish}"' in es
