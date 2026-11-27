# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ACP-T04: the live thread of a Codex ACP conversation (#new/acp/<id>).

The pure model runs in Node (skipped without it); the components are read as source. No server, no browser, no real agent."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
ENGLISH = ["--import", "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'en-US'},configurable:true})"]


def node(body: str):
    imports = "\n".join([
        f"import * as model from {json.dumps((JS / 'thread-model.js').as_uri())};",
        f"import * as stream from {json.dumps((JS / 'stream.js').as_uri())};",
        f"import * as route from {json.dumps((JS / 'route.js').as_uri())};",
    ])
    result = subprocess.run([NODE, *ENGLISH, "--input-type=module", "-e", imports + "\n" + body], capture_output=True, text=True,
                            encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def raw(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def event(cursor, kind="message", status=None, **data):
    return {"cursor": cursor, "kind": kind, "tool_status": status, "data": data}


def block(item) -> str:
    return f"id: {item['cursor']}\nevent: item\ndata: {json.dumps(item)}"


# --- the order and the cursor ------------------------------------------------------------------------------------------

@needs_node
def test_events_are_drawn_in_cursor_order_whatever_order_they_arrive_in():
    events = [event(3, "tool", "in_progress", title="Edit file", locations=[{"path": "C:\\area\\notes\\plan.md"}]),
              event(1, "message", text="Hello"), event(2, "thought", text="thinking"),
              event(4, "diff", path="C:\\area\\notes\\plan.md", oldText="a", newText="b"),
              event(5, "plan", entries=[{"content": "Write it", "status": "pending"}]),
              event(6, "usage", used=120, size=1000), event(7, "end_turn", stop_reason="end_turn"), event(8, "error", status="failed")]
    result = node(f"""
      const page = {{ events: {json.dumps(events)}, next_cursor: 8, active: false, consent_configured: true }};
      const state = model.mergeAcpPage(model.createAcpThread(), page);
      console.log(JSON.stringify({{ cursor: state.cursor, kinds: model.groupAcpEvents(state.events).map((item) => item.kind),
        active: state.active, loaded: state.loaded }}));
    """)
    assert result == {"cursor": 8, "kinds": ["message", "thought", "tool", "diff", "plan", "usage", "end_turn", "error"],
                      "active": False, "loaded": True}


@needs_node
def test_each_kind_reads_into_its_own_fields_and_usage_is_session_usage_not_quota():
    result = node(f"""
      const view = (e) => model.acpEventView(e);
      console.log(JSON.stringify({{
        tool: view({json.dumps(event(1, "tool", "failed", title="Run", locations=[{"path": "/w/a.txt"}]))}),
        diff: view({json.dumps(event(2, "diff", content=[{"type": "diff", "path": "/w/a.txt", "oldText": "x", "newText": "y"}]))}),
        plan: view({json.dumps(event(3, "plan", entries=[{"content": "One", "status": "pending"}, {"content": ""}]))}),
        usage: view({json.dumps(event(4, "usage", used=5, size=10, cost={"amount": 0.5, "currency": "USD"}))}),
        end: view({json.dumps(event(5, "end_turn", stop_reason="cancelled"))}),
        error: view({json.dumps({"cursor": 6, "kind": "error", "tool_status": None, "data": {"status": "failed"}})}) }}));
    """)
    assert result["tool"]["status"] == "failed" and result["tool"]["paths"] == ["/w/a.txt"] and result["tool"]["title"] == "Run"
    assert result["diff"]["diffs"] == [{"path": "/w/a.txt", "oldText": "x", "newText": "y"}]
    assert [row["text"] for row in result["plan"]["rows"]] == ["One"]
    assert result["usage"]["used"] == 5 and result["usage"]["size"] == 10 and result["usage"]["cost"] == {"amount": 0.5, "currency": "USD"}
    assert result["end"]["stop"] == "cancelled" and result["error"]["text"] == "failed"
    cards = raw("thread-cards.js")
    assert "thread.acp.usage" in cards and "quota" not in cards[cards.index("export function AcpCard"):].lower()   # session usage, not a quota
    assert "stop_reason" not in cards and "view.stop" in cards                                                    # end_turn shows its reason


@needs_node
def test_a_stream_of_message_chunks_reads_as_one_block_and_keeps_the_place_of_the_rest():
    result = node(f"""
      const state = model.mergeAcpEvents(model.createAcpThread(), {json.dumps([event(1, text="Hel"), event(2, text="lo"),
                                                                                   event(3, "tool", "pending", title="T"), event(4, text="Bye")])});
      const groups = model.groupAcpEvents(state.events);
      console.log(JSON.stringify(groups.map((item) => [item.kind, item.view.text || item.view.title, item.cursor])));
    """)
    assert result == [["message", "Hello", 2], ["tool", "T", 3], ["message", "Bye", 4]]


# --- the stream and reconnecting ---------------------------------------------------------------------------------------

@needs_node
def test_reconnecting_from_the_last_cursor_neither_repeats_nor_loses_an_event():
    items = [event(index, text=f"m{index}") for index in range(1, 8)]
    result = node(f"""
      const items = {json.dumps(items)};
      const blocks = (list) => list.map((item) => stream.parseBlock("id: " + item.cursor + "\\nevent: item\\ndata: " + JSON.stringify(item)));
      let state = model.createAcpThread();
      // first connection: the page after=0, then items 3 and 4 over the stream (3 arrives twice)
      state = model.mergeAcpPage(state, {{ events: items.slice(0, 2), next_cursor: 2, active: true, consent_configured: true }});
      for (const block of blocks([items[2], items[2], items[3]])) state = model.applyAcpStreamBlock(state, block);
      const path = model.acpStreamPath("abc", state.cursor);
      // the stream drops; the page after the cursor brings 4 again and 5, 6; the second connection starts from 6
      state = model.mergeAcpPage(state, {{ events: items.slice(3, 6), next_cursor: 6, active: true }});
      for (const block of blocks([items[5], items[6]])) state = model.applyAcpStreamBlock(state, block);
      console.log(JSON.stringify({{ path, cursors: state.events.map((item) => item.cursor), cursor: state.cursor,
        again: model.acpStreamPath("abc", state.cursor) }}));
    """)
    assert result["path"] == "/gui/api/threads/acp/abc/stream?after=4"
    assert result["cursors"] == [1, 2, 3, 4, 5, 6, 7] and result["cursor"] == 7 and result["again"].endswith("after=7")


@needs_node
def test_a_late_or_malformed_block_changes_nothing():
    result = node(f"""
      let state = model.mergeAcpEvents(model.createAcpThread(), {json.dumps([event(1), event(2)])});
      const same = (block) => model.applyAcpStreamBlock(state, block) === state || JSON.stringify(model.applyAcpStreamBlock(state, block)) === JSON.stringify(state);
      console.log(JSON.stringify([same(null), same({{ type: "item", data: "not json" }}), same({{ type: "item", data: JSON.stringify({{ cursor: 0, kind: "message" }}) }}),
        same({{ type: "item", data: JSON.stringify({{ cursor: 9, kind: "unknown" }}) }}), same({{ type: "message", data: "x" }}),
        same(stream.parseBlock(": connected"))]));
    """)
    assert result == [True] * 6


def test_the_thread_follows_from_the_cursor_it_holds_and_reads_the_page_when_a_stream_ends():
    thread = raw("thread.js")
    assert 'acpPath(threadId, "?after=" + current.current.cursor)' in thread          # the page starts at the cursor (0 the first time)
    assert "acpStreamPath(threadId, current.current.cursor)" in thread and '"Last-Event-ID": String(current.current.cursor)' in thread
    assert "readPage()" in thread and "ACP_RECONNECT_MS" in thread
    follow = thread[thread.index("async function follow()"):thread.index("follow();")]
    assert follow.index("readPage()") < follow.index("fetch(acpStreamPath")           # the first thing is the page, then the stream


# --- cancelling and sending --------------------------------------------------------------------------------------------

@needs_node
def test_the_controls_follow_the_state_of_the_session():
    result = node("""
      const base = model.createAcpThread();
      console.log(JSON.stringify([model.acpComposerState(base), model.acpComposerState({ ...base, loaded: true }),
        model.acpComposerState({ ...base, loaded: true, active: true }), model.acpComposerState({ ...base, loaded: true, lost: true, active: true })]));
    """)
    assert result == ["loading", "idle", "active", "lost"]


def test_cancel_runs_while_active_and_send_runs_when_it_is_not():
    thread = raw("thread.js")
    assert 'act("/cancel", {}, () => {})' in thread and 'act("/prompts", { text: text.trim() }' in thread
    active = thread[thread.index('${state === "active" && html`'):thread.index('${state === "idle" && html`')]
    assert "thread.acp.cancel" in active and "<textarea" not in active
    idle = thread[thread.index('${state === "idle" && html`'):]
    assert "thread.acp.send" in idle and "thread.acp.cancel" not in idle
    assert 'apiPost(acpPath(threadId, path), token, body)' in thread
    # a 409 (a turn is already running) is shown as the text of the error, and the session is not given up for it
    assert "setError(failure)" in thread and "errorText(error)" in thread
    assert "isAcpLost(failure)" in thread[thread.index("async function act("):]


# --- untrusted text ----------------------------------------------------------------------------------------------------

@needs_node
def test_what_the_agent_sends_stays_text_and_paths_stay_whole():
    hostile = '<img src=x onerror=alert(1)> <a href="javascript:alert(2)">go</a> $(rm -rf /) `calc`'
    path = "C:\\Users\\someone\\area\\notes\\a very long folder name\\file.md"
    result = node(f"""
      const e = {json.dumps(event(1, "tool", "completed", title=hostile, locations=[{"path": path}], content=[{"type": "content", "content": {"type": "text", "text": hostile}}]))};
      const view = model.acpEventView(e);
      console.log(JSON.stringify({{ title: view.title, text: view.text, paths: view.paths }}));
    """)
    assert result["title"] == hostile and result["text"] == hostile and result["paths"] == [path]


def test_no_html_no_opening_links_and_no_commands_in_the_thread():
    for name in ("thread.js", "thread-cards.js", "thread-model.js"):
        text = raw(name)
        assert not re.search(r"innerHTML|dangerouslySetInnerHTML|__html|insertAdjacentHTML|document\.write|\beval\(|new Function", text), name
    cards = raw("thread-cards.js")
    acp = cards[cards.index("// ACP-T04: one block"):]
    assert "<a " not in acp and "href=" not in acp and "window.open" not in acp           # a path or a link of the agent never opens
    assert "<code>${path}</code>" in acp and "<code>${diff.path}</code>" in acp          # shown as text, whole
    model = raw("thread-model.js")
    assert not re.search(r"fetch\(|apiGet|apiPost|document\.|window\.", model[model.index("// ACP-T04"):])


# --- permissions: said once --------------------------------------------------------------------------------------------

@needs_node
def test_the_permissions_notice_exists_only_while_the_consent_is_not_configured():
    result = node("""
      const base = model.createAcpThread();
      const state = (page) => model.mergeAcpPage(base, page);
      console.log(JSON.stringify([model.acpNeedsConsentNotice(base),
        model.acpNeedsConsentNotice(state({ events: [], next_cursor: 0, active: false, consent_configured: false })),
        model.acpNeedsConsentNotice(state({ events: [], next_cursor: 0, active: false, consent_configured: true }))]));
    """)
    assert result == [False, True, False]


def test_the_permissions_notice_is_drawn_once_with_a_link_to_apps_and_never_asks_on_each_send():
    thread = raw("thread.js"); thread = thread[:thread.index("export function AcpStart")]   # the thread view; the start view has its own notice
    assert thread.count("thread.acp.consent_missing") == 1 and thread.count("acpNeedsConsentNotice(thread)") == 1
    notice = thread[thread.index("acpNeedsConsentNotice(thread) &&"):]
    assert 'href="#apps"' in notice[:400]
    send = thread[thread.index("const send ="):thread.index("const cancel =")]
    assert "consent" not in send.lower()                                          # no question, no check at each message


# --- a session that is gone --------------------------------------------------------------------------------------------

@needs_node
def test_an_error_block_or_a_404_ends_the_conversation_without_retrying():
    result = node("""
      const base = model.mergeAcpPage(model.createAcpThread(), { events: [], next_cursor: 0, active: true, consent_configured: true });
      const lost = model.applyAcpStreamBlock(base, stream.parseBlock('event: error\\ndata: {"code":"not_found","message":"ACP session not found"}'));
      console.log(JSON.stringify({ lost: lost.lost, active: lost.active, lost404: model.isAcpLost({ status: 404 }), other: model.isAcpLost({ status: 409 }),
        none: model.isAcpLost(null), composer: model.acpComposerState(lost) }));
    """)
    assert result == {"lost": True, "active": False, "lost404": True, "other": False, "none": False, "composer": "lost"}


def test_a_lost_session_stops_the_loop_and_says_it_was_deleted_expired_or_never_saved():
    thread = raw("thread.js")
    follow = thread[thread.index("async function follow()"):thread.index("follow();")]
    assert 'if (response.status === 404) { commit({ ...current.current, lost: true, active: false }); return; }' in follow
    assert "if (current.current.lost) return;" in follow and "while (!stopped && !current.current.lost && current.current.active)" in follow
    assert 'state === "lost" && html`<p class="message error" role="alert">${t("thread.acp.lost")}' in thread
    en = (STATIC_DIR / "i18n" / "en" / "thread.js").read_text(encoding="utf-8")
    # ACP-T05: the history is saved to disk, so the old phrase («lives in memory») is false; this is what the notice says now
    es = (STATIC_DIR / "i18n" / "es" / "thread.js").read_text(encoding="utf-8")
    assert '"thread.acp.lost": "This conversation was deleted, expired, or was never saved."' in en and "lives in memory" not in en
    assert '"thread.acp.lost": "Esta conversación se borró, caducó o nunca se guardó."' in es and "vive en memoria" not in es


# --- the route and the screen that draws it ----------------------------------------------------------------------------

@needs_node
def test_the_acp_route_is_a_thread_of_new_and_leaves_the_others_alone():
    result = node("""
      console.log(JSON.stringify([route.parseRoute("#new/acp/abc%20d"), route.acpThreadHref("abc d"), route.parseRoute("#new/run/r1"),
        route.parseRoute("#new/team/t1").kind, route.parseRoute("#new/plan/p1").kind, route.parseRoute("#new")]));
    """)
    assert result[0] == {"section": "new", "id": "abc d", "kind": "acp"} and result[1] == "#new/acp/abc%20d"
    assert result[2] == {"section": "new", "id": "r1"} and result[3:5] == ["team", "plan"] and result[5] == {"section": "new", "id": None}
    session = raw("new-session.js")
    assert 'if (runId && route.kind === "acp") return html`<${AcpThread} key=${runId} token=${token} threadId=${runId} />`;' in session
    assert session.index('route.kind === "acp"') < session.index("if (runId) return html`<${RunThread}")


def test_the_header_is_the_thread_header_with_refresh_and_the_shortcuts_with_their_own_scope():
    thread = raw("thread.js")
    acp = thread[thread.index("export function AcpThread"):]
    assert 'thread.refresh' in acp and "setEpoch((value) => value + 1)" in acp
    assert '<${Shortcuts} mode=${currentMode()} sessionId="" project=${null} scope="acp" />' in acp


# --- texts, help, CSS --------------------------------------------------------------------------------------------------

def test_thread_texts_exist_in_english_and_spanish_and_help_in_both():
    en = (STATIC_DIR / "i18n" / "en" / "thread.js").read_text(encoding="utf-8")
    es = (STATIC_DIR / "i18n" / "es" / "thread.js").read_text(encoding="utf-8")
    keys = lambda text: set(re.findall(r'^  "(thread\.acp\.[\w.]+)":', text, re.M))
    assert keys(en) == keys(es) and len(keys(en)) >= 25
    assert '"thread.acp.cancel": "Cancel turn"' in en and '"thread.acp.cancel": "Cancelar turno"' in es
    runs_es = (STATIC_DIR / "i18n" / "es" / "runs.js").read_text(encoding="utf-8")
    help_en = raw("help-text-runs.js")
    thread = raw("thread.js")
    for topic in ("acp_thread.section", "acp_thread.cancel", "acp_thread.text", "acp_thread.send"):
        assert f'"{topic}": {{' in help_en and f'"help.{topic}": {{' in runs_es, topic
        assert f'topic="{topic}"' in thread
    for topic in ("acp_thread.cancel", "acp_thread.text", "acp_thread.send"):
        assert f'helpFor("{topic}")' in thread, topic


def test_the_css_block_uses_variables_targets_and_forced_colors_without_inline_style():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* ACP-T04:"):]
    assert "var(--target)" in block and "@media (forced-colors: active)" in block and "overflow-wrap: anywhere" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", block)
    for name in ("thread.js", "thread-cards.js"):
        assert "style=" not in raw(name)


# --- ACP-T04 round 3b: the first message is written in the thread view (#new/acp-new/<package>) -------------------------

@needs_node
def test_the_start_route_carries_the_encoded_package_and_does_not_collide_with_a_session_id():
    result = node("""
      console.log(JSON.stringify([route.acpStartHref("office__area one"), route.parseRoute("#new/acp-new/office__area%20one"),
        route.parseRoute("#new/acp/acp-new"), route.parseRoute("#new/acp-new/a%2Fb"), route.parseRoute("#new/acp/abc")]));
    """)
    assert result[0] == "#new/acp-new/office__area%20one"
    assert result[1] == {"section": "new", "id": "office__area one", "kind": "acp-new"}
    assert result[2] == {"section": "new", "id": "acp-new", "kind": "acp"}             # a session literally named acp-new stays a session
    assert result[3]["id"] == "a/b" and result[4]["kind"] == "acp"


def test_new_session_draws_the_start_before_the_thread_and_both_before_the_run():
    session = raw("new-session.js")
    start = 'if (runId && route.kind === "acp-new") return html`<${AcpStart} key=${runId} token=${token} bundle=${runId} />`;'
    assert start in session and 'route.kind === "acp") return html`<${AcpThread}' in session
    assert session.index(start) < session.index('route.kind === "acp")') < session.index("if (runId) return html`<${RunThread}")


@needs_node
def test_the_first_message_body_and_the_started_id():
    result = node("""
      console.log(JSON.stringify([model.acpStartBody("o__a", "  Please summarize  "), model.acpStartBody("o__a", "  "),
        model.acpStartBody("", "hi"), model.acpStartBody("o__a", null), model.acpStartedId({ id: "abc" }), model.acpStartedId({}),
        model.acpStartedId(null), model.acpConsentReadPath("o__a b"), model.ACP_START_PATH]));
    """)
    assert result == [{"bundle": "o__a", "text": "Please summarize"}, None, None, None, "abc", "", "",
                      "/gui/api/connections/codex_acp/consent?bundle=o__a%20b", "/gui/api/threads/acp"]


@needs_node
@pytest.mark.parametrize("reason", ["acp_unavailable", "acp_activation_required", "acp_probe_required", "acp_area_required",
                                    "acp_session_limit", "executor_paused_changed", "executor_stopped_by_person"])
def test_a_409_of_the_start_reads_acp_unavailable_with_its_reason_and_keeps_cli_as_an_option(reason):
    result = node(f"""console.log(JSON.stringify(model.acpStartFailure({{ status: 409, details: {{ reason: {json.dumps(reason)} }} }})));""")
    assert result == {"unavailable": True, "reason": reason, "titleKey": "thread.acp.unavailable", "textKey": "thread.acp.reason." + reason, "message": ""}
    for lang in ("en", "es"):
        text = (STATIC_DIR / "i18n" / lang / "thread.js").read_text(encoding="utf-8")
        assert f'"thread.acp.reason.{reason}":' in text and '"thread.acp.unavailable":' in text and '"thread.acp.choose_cli":' in text


@needs_node
def test_other_start_failures_are_plain_text():
    result = node("""console.log(JSON.stringify([model.acpStartFailure({ status: 500, message: "boom" }), model.acpStartFailure(null),
      model.acpStartFailure({ status: 404 }), model.acpStartFailure({ status: 409, details: { reason: "other" } })]));""")
    assert result[0]["unavailable"] is False and result[0]["message"] == "boom" and result[0]["titleKey"] == ""
    assert result[1]["textKey"] == "thread.acp.start.failed" and result[2]["reason"] == "not_found" and result[3]["unavailable"] is False


def test_the_start_component_posts_once_replaces_the_entry_and_never_switches_transport():
    thread = raw("thread.js")
    start = thread[thread.index("export function AcpStart"):]
    assert start.count("apiPost(") == 1 and "apiPost(ACP_START_PATH, token, body)" in start
    assert "/gui/api/runs" not in start and "threads/runs" not in start and "/setup/console" not in start
    assert start.count("location.replace(acpThreadHref(id))") == 1 and "location.hash" not in start      # no extra history entry
    assert start.index("location.replace(acpThreadHref(id))") < start.index("setFailure(acpStartFailure(error")
    assert 'href="#new"' in start and "thread.acp.choose_cli" in start and "failure.unavailable" in start
    assert "<textarea" in start and "acp_thread.start_text" in start and "acp_thread.start" in start
    # the permissions notice is read once for the package, not on every keystroke or message
    assert start.count("apiGet(acpConsentReadPath(bundle), token)") == 1 and "}, [bundle, token]);" in start
    assert "consent.configured !== true" in start and "thread.acp.consent_missing" in start


def test_the_start_text_is_plain_text():
    start = raw("thread.js")
    start = start[start.index("export function AcpStart"):]
    assert "innerHTML" not in start and "dangerouslySetInnerHTML" not in start and "<code>${bundle}</code>" in start


def test_the_start_texts_and_help_exist_in_both_languages():
    en = (STATIC_DIR / "i18n" / "en" / "thread.js").read_text(encoding="utf-8")
    es = (STATIC_DIR / "i18n" / "es" / "thread.js").read_text(encoding="utf-8")
    for key in ("title", "area", "label", "button", "starting", "failed", "not_found"):
        assert f'"thread.acp.start.{key}":' in en and f'"thread.acp.start.{key}":' in es, key
    runs_en = raw("help-text-runs.js")
    runs_es = (STATIC_DIR / "i18n" / "es" / "runs.js").read_text(encoding="utf-8")
    for topic in ("acp_thread.start_text", "acp_thread.start", "acp_thread.cli"):
        assert f'"{topic}": {{' in runs_en and f'"help.{topic}": {{' in runs_es, topic
        assert f'helpFor("{topic}")' in raw("thread.js") and f'topic="{topic}"' in raw("thread.js")
