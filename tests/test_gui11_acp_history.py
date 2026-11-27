# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ACP-T05, the GUI: a saved conversation is read, never continued; it can be deleted; the card of «Apps & agents» lists the saved
conversations and holds the history switch.

The pure models run in Node (skipped without it); the components are read as source. No server, no browser, no real agent."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from multiagent.services.acp_history import AcpHistory, TEXT_LIMIT

JS = STATIC_DIR / "js"
APPS = STATIC_DIR / "extensions" / "apps"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def navigator(language: str) -> list[str]:
    return ["--import", f"data:text/javascript,Object.defineProperty(globalThis,'navigator',{{value:{{language:'{language}'}},configurable:true}})"]


def node(body: str, language: str = "en-US"):
    imports = "\n".join([
        f"import * as model from {json.dumps((JS / 'thread-model.js').as_uri())};",
        f"import * as acp from {json.dumps((APPS / 'acp-model.js').as_uri())};",
        f"import dictionaries from {json.dumps((APPS / 'i18n.js').as_uri())};",
    ])
    result = subprocess.run([NODE, *navigator(language), "--input-type=module", "-e", imports + "\n" + body], capture_output=True,
                            text=True, encoding="utf-8", cwd=STATIC_DIR, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def raw(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def apps(name: str) -> str:
    return (APPS / name).read_text(encoding="utf-8")


def thread_source() -> str:
    source = raw("thread.js")
    return source[source.index("export function AcpThread"):source.index("export function AcpStart")]


def event(cursor, kind="message", status=None, **data):
    return {"cursor": cursor, "kind": kind, "tool_status": status, "data": data}


# --- a saved conversation: read it, never continue it ------------------------------------------------------------------

@needs_node
def test_a_saved_page_is_a_saved_thread_with_its_flags_and_no_composer():
    page = {"id": "a" * 32, "events": [event(1, "prompt", text="Hello"), event(2, "message", text="Hi"), event(3, "end_turn", stop_reason="end_turn")],
            "next_cursor": 3, "active": False, "saved": True, "consent_configured": True, "redacted": True, "incomplete": False}
    result = node(f"""
      const state = model.mergeAcpPage(model.createAcpThread(), {json.dumps(page)});
      const open = model.mergeAcpPage(model.createAcpThread(), {json.dumps({**page, "redacted": False, "incomplete": True})});
      const live = model.mergeAcpPage(model.createAcpThread(), {json.dumps({**page, "saved": False, "active": False})});
      console.log(JSON.stringify([state.saved, state.redacted, state.incomplete, model.acpComposerState(state), open.redacted, open.incomplete,
        model.acpComposerState(live), model.acpComposerState(model.createAcpThread())]));
    """)
    assert result == [True, True, False, "saved", False, True, "idle", "loading"]


def test_a_saved_thread_has_the_banner_the_link_and_no_text_box_or_cancel_button():
    source = thread_source()
    saved = source[source.index('state === "saved" && html`<div class="acp-saved">'):]
    saved = saved[:saved.index("</div>`}") + 8]
    assert 't("thread.acp.saved")' in saved and "thread.acp.saved.redacted" in saved and "thread.acp.saved.incomplete" in saved
    assert "thread.redacted &&" in saved and "thread.incomplete &&" in saved
    assert "acpStartHref(bundle, agent)" in saved and '"#apps"' in saved            # the same area, or Apps when the list did not name it
    assert "<textarea" not in saved
    # the text box exists only for an idle conversation and the cancel button only for a live one
    assert source.count("<textarea") == 1 and source.index("<textarea") > source.index('state === "idle" && html`')
    assert source.count("acp_thread.cancel") == 2 and source.index('state === "active" && html`') < source.index('helpFor("acp_thread.cancel")')
    assert source.index('helpFor("acp_thread.cancel")') < source.index('state === "idle" && html`')


def test_the_package_of_a_saved_conversation_is_read_once_from_the_list_and_nothing_polls():
    source = thread_source()
    assert source.count("apiGet(ACP_LIST_PATH, token)") == 1 and "if (!thread.saved) return undefined;" in source
    assert "}, [thread.saved, threadId, token]);" in source
    assert "setInterval" not in source and source.count("setTimeout") == 1           # the one reconnect pause that was already there
    assert "style=" not in source


@needs_node
def test_the_package_comes_from_the_list_and_an_unknown_one_is_empty():
    answer = {"conversations": [{"id": "x1", "bundle": "office__area"}, {"id": "x2", "unreadable": True}]}
    result = node(f"""console.log(JSON.stringify([model.acpBundleOf({json.dumps(answer)}, "x1"), model.acpBundleOf({json.dumps(answer)}, "x2"),
      model.acpBundleOf({json.dumps(answer)}, "x3"), model.acpBundleOf(null, "x1")]));""")
    assert result == ["office__area", "", "", ""]


# --- a conversation followed live that is now a saved file --------------------------------------------------------------

@needs_node
def test_a_live_cursor_that_meets_a_saved_page_starts_again_from_the_first_event_once():
    live = [event(i, "message", text=f"chunk {i}") for i in range(1, 8)]
    saved_empty = {"events": [], "next_cursor": 7, "active": False, "saved": True, "consent_configured": True, "redacted": False, "incomplete": False}
    saved_all = {"events": [event(1, "prompt", text="Hello"), event(2, "message", text="Hi"), event(3, "end_turn")], "next_cursor": 3,
                 "active": False, "saved": True, "consent_configured": True, "redacted": False, "incomplete": False}
    result = node(f"""
      let state = model.mergeAcpPage(model.createAcpThread(), {{ events: {json.dumps(live)}, next_cursor: 7, active: true, saved: false }});
      const before = [state.cursor, state.saved];
      state = model.mergeAcpPage(state, {json.dumps(saved_empty)});
      const reset = [state.events.length, state.cursor, state.saved, state.reload];
      state = model.mergeAcpPage(state, {json.dumps(saved_all)});
      console.log(JSON.stringify([before, reset, state.events.map((item) => item.kind), state.cursor, state.reload, state.saved]));
    """)
    assert result == [[7, False], [0, 0, True, True], ["prompt", "message", "end_turn"], 3, False, True]


def test_the_thread_reads_once_more_when_the_page_says_reload_and_the_loop_cannot_repeat():
    source = thread_source()
    assert "if (current.current.reload) return readPage();" in source
    # a reset state is already `saved`, so the next merge never resets again
    model = raw("thread-model.js")
    assert "page.saved === true && !state.saved && state.cursor > 0" in model


# --- the person's message: once, live or saved ------------------------------------------------------------------------

@needs_node
def test_a_prompt_is_drawn_once_as_the_person_s_message_live_and_saved():
    events = [event(1, "prompt", text="Please summarize"), event(2, "message", text="Sure"), event(3, "end_turn", stop_reason="end_turn"),
              event(4, "prompt", text="And again"), event(5, "message", text="Done"), event(6, "end_turn", stop_reason="end_turn")]
    result = node(f"""
      const page = {{ events: {json.dumps(events)}, next_cursor: 6, active: false, saved: false }};
      let live = model.mergeAcpPage(model.createAcpThread(), page);
      // the stream repeats the same events: nothing is drawn twice
      for (const item of {json.dumps(events)}) live = model.applyAcpStreamBlock(live, {{ type: "item", data: JSON.stringify(item) }});
      live = model.mergeAcpPage(live, page);
      const saved = model.mergeAcpPage(model.createAcpThread(), {{ ...page, saved: true }});
      const draw = (state) => model.groupAcpEvents(state.events).map((group) => [group.kind, group.view.text]);
      console.log(JSON.stringify([draw(live), draw(saved), model.ACP_KINDS.includes("prompt")]));
    """)
    live, saved, known = result
    assert known is True and live == saved
    assert [item for item in live if item[0] == "prompt"] == [["prompt", "Please summarize"], ["prompt", "And again"]]
    assert [item[1] for item in live if item[0] == "message"] == ["Sure", "Done"]


def test_the_prompt_card_is_the_person_s_message_and_has_its_own_label():
    cards = raw("thread-cards.js")
    assert '(view.kind === "prompt" ? " thread-msg-user" : "")' in cards
    for lang, label in (("en", "You"), ("es", "Tú")):
        text = (STATIC_DIR / "i18n" / lang / "thread.js").read_text(encoding="utf-8")
        assert f'"thread.acp.kind.prompt": "{label}"' in text


# --- what is saved is what was seen: the joined text is clipped ---------------------------------------------------------

@needs_node
def test_two_chunks_that_pass_the_limit_together_are_clipped_once_with_the_same_mark():
    first, second = "a" * 5000, "b" * 5000
    result = node(f"""
      const events = [{{ cursor: 1, kind: "message", tool_status: null, data: {{ text: {json.dumps(first)} }} }},
                      {{ cursor: 2, kind: "message", tool_status: null, data: {{ text: {json.dumps(second)} }} }},
                      {{ cursor: 3, kind: "thought", tool_status: null, data: {{ text: {json.dumps(first)} }} }},
                      {{ cursor: 4, kind: "thought", tool_status: null, data: {{ text: {json.dumps(second)} }} }}];
      const groups = model.groupAcpEvents(events);
      console.log(JSON.stringify([groups.map((group) => group.kind), groups.map((group) => group.view.text), model.ACP_TEXT_LIMIT]));
    """)
    kinds, texts, limit = result
    assert limit == TEXT_LIMIT == 8000 and kinds == ["message", "thought"]
    assert texts[0] == texts[1] == "a" * 5000 + "b" * 3000 + "…"      # not 10 000 characters, and no mark in the middle


@needs_node
def test_a_chunk_over_the_limit_is_not_marked_twice_and_a_short_group_is_untouched():
    result = node("""
      const long = [{ cursor: 1, kind: "message", tool_status: null, data: { text: "x".repeat(9000) } },
                    { cursor: 2, kind: "message", tool_status: null, data: { text: "y".repeat(10) } }];
      const short = [{ cursor: 1, kind: "message", tool_status: null, data: { text: "one " } }, { cursor: 2, kind: "message", tool_status: null, data: { text: "two" } }];
      console.log(JSON.stringify([model.groupAcpEvents(long)[0].view.text, model.groupAcpEvents(short)[0].view.text,
        model.groupAcpEvents(long.slice(0, 1))[0].view.text]));
    """)
    assert result[0] == "x" * 8000 + "…" and result[1] == "one two" and result[2] == "x" * 8000 + "…"


@needs_node
def test_the_screen_and_the_saved_file_show_the_same_joined_text(tmp_path):
    chunks = ["p" * 6000, "q" * 6000, "end"]
    history = AcpHistory(tmp_path)
    identifier = "b" * 32
    history.create(identifier, "sample-area", "office/area")
    live = [{"kind": "message", "tool_status": None, "data": {"text": chunk}} for chunk in chunks] + [{"kind": "end_turn", "tool_status": None, "data": {}}]
    history.append_turn(identifier, live)
    on_disk = history.page(identifier)["events"][0]["data"]["text"]
    numbered = [{"cursor": index, **item} for index, item in enumerate(live, 1)]
    on_screen = node(f"""console.log(JSON.stringify(model.groupAcpEvents({json.dumps(numbered)})[0].view.text));""")
    assert on_screen == on_disk and on_disk.endswith("…") and len(on_disk) == TEXT_LIMIT + 1


@needs_node
def test_the_warning_of_the_history_is_one_line_and_not_a_card():
    events = [event(1, "prompt", text="Hello"), event(2, "end_turn"), {**event(3, "error"), "data": {"status": "history_not_saved"}},
              {**event(4, "error"), "data": {"status": "failed"}}]
    result = node(f"""
      const base = {{ events: {json.dumps(events)}, next_cursor: 4, active: false, saved: false }};
      const warned = model.mergeAcpPage(model.createAcpThread(), {{ ...base, history_warning: "history_not_saved" }});
      const limit = model.mergeAcpPage(model.createAcpThread(), {{ ...base, history_warning: "history_size_limit" }});
      const none = model.mergeAcpPage(model.createAcpThread(), {{ ...base, history_warning: "anything else" }});
      console.log(JSON.stringify([model.acpHistoryWarningKey(warned), model.acpHistoryWarningKey(limit), model.acpHistoryWarningKey(none),
        model.groupAcpEvents(warned.events).map((group) => group.kind)]));
    """)
    assert result == ["thread.acp.history.not_saved", "thread.acp.history.size_limit", "", ["prompt", "end_turn", "error"]]      # only the real error stays a card
    source = thread_source()
    assert "acpHistoryWarningKey(thread) && state !== \"lost\"" in source and 't(acpHistoryWarningKey(thread))' in source


# --- delete this conversation ------------------------------------------------------------------------------------------

def test_delete_this_conversation_asks_first_then_posts_once_to_the_delete_route():
    source = thread_source()
    assert source.count("apiPost(acpDeletePath(threadId), token, {})") == 1
    assert "const remove" not in source and "async function remove()" in source
    ask = source.index("onClick=${() => setConfirming(true)}")
    assert ask < source.index("onClick=${remove}")                              # the first press only opens the question
    block = source[source.index("{confirming && html`"):]
    block = block[:block.index("</div>`}")]
    assert "thread.acp.delete.confirm" in block and block.count('data-help-exempt="confirm"') == 2
    assert 'helpFor("acp_thread.delete")' in source and 'topic="acp_thread.delete"' in source
    assert 'state === "saved" || state === "active" || state === "idle"' in source      # the thread of a live and of a saved conversation
    assert "if (deleted) {" in source and "thread.acp.deleted" in source


@needs_node
def test_the_delete_path_is_the_one_the_server_serves():
    result = node("""console.log(JSON.stringify([model.acpDeletePath("abc 1"), model.ACP_LIST_PATH, model.ACP_DELETE_ALL_PATH,
      acp.acpDeletePath("abc 1"), acp.ACP_LIST_PATH, acp.ACP_DELETE_ALL_PATH]));""")
    assert result == ["/gui/api/threads/acp/abc%201/delete", "/gui/api/threads/acp", "/gui/api/threads/acp/delete-all"] * 2


# --- the card of «Apps & agents»: the list ----------------------------------------------------------------------------

LISTED = {"conversations": [
    {"id": "a" * 32, "bundle": "office__area", "scope": "office/area", "office": "office", "area": "area",
     "started_at": "2026-11-26T12:00:00+00:00", "last_message_at": "2026-11-26T12:01:00+00:00", "message_count": 2, "active": False,
     "unreadable": False, "redacted": True},
    {"id": "b" * 32, "bundle": "other__area", "scope": "other/area", "office": "other", "area": "area",
     "started_at": "2026-11-25T12:00:00+00:00", "last_message_at": "2026-11-25T12:01:00+00:00", "message_count": 4, "active": True,
     "unreadable": True, "redacted": False},
    {"id": "c" * 32, "unreadable": True, "active": False},
    {"bundle": "no-id"}, None]}


@needs_node
def test_the_list_shows_area_date_and_message_count_and_marks_what_cannot_be_read():
    rows = node(f"""console.log(JSON.stringify(acp.historyRows({json.dumps(LISTED)})));""")
    assert [row["id"] for row in rows] == ["a" * 32, "b" * 32, "c" * 32]           # an entry without an id is not a row
    first, second, third = rows
    assert first["label"] == "office / area" and first["count"] == 2 and first["when"] and first["href"] == "#new/acp/" + "a" * 32
    assert first["readable"] is True and first["unreadable"] is False and first["redacted"] is True
    assert second["unreadable"] is True and second["readable"] is True and second["active"] is True       # damaged file, still a link
    assert third["readable"] is False and third["unreadable"] is True and third["label"] == "" and third["when"] == ""


@needs_node
def test_rows_of_one_area_are_found_by_package_and_an_empty_package_matches_nothing():
    result = node(f"""
      const rows = acp.historyRows({json.dumps(LISTED)});
      console.log(JSON.stringify([acp.rowsOfBundle(rows, "office__area").map((row) => row.id), acp.rowsOfBundle(rows, "").length,
        acp.rowsOfBundle(rows, "unknown").length, acp.historyRows(null), acp.historyRows({{ conversations: "x" }})]));
    """)
    assert result == [["a" * 32], 0, 0, [], []]


def test_the_list_is_read_when_the_card_opens_and_after_each_deletion_never_on_a_timer():
    source = apps("acp.js")
    assert source.count("apiGet(ACP_LIST_PATH, token)") == 1 and "}, [token, round]);" in source
    assert "setInterval" not in source and "setTimeout" not in source
    assert source.count("history.reload()") == 3                                  # the switch, delete for the area, delete all
    assert "const history = useHistory(token);" in source and "<${HistorySection} token=${token} history=${history} />" in source
    assert "unreadable_file" in source and "row.readable ? html`<a href=${row.href}>" in source


def test_delete_all_asks_first_and_posts_to_the_delete_all_route_only_from_its_confirmation():
    source = apps("acp.js")
    section = source[source.index("function HistorySection"):]
    assert section.count("apiPost(ACP_DELETE_ALL_PATH, token, {})") == 1
    assert section.index("setConfirming(true)") < section.index("onClick=${deleteAll}")
    confirm = section[section.index("{confirming && html`"):]
    confirm = confirm[:confirm.index("</div>`}")]
    assert "ext.apps.acp.history.delete_all.confirm" in confirm and confirm.count('data-help-exempt="confirm"') == 2
    assert 'helpFor("apps.acp_delete_all")' in section and 'topic="apps.acp_delete_all"' in section
    assert "ext.apps.acp.history.note" in section and "ext.apps.acp.history.redaction" in section   # the fixed notes are always shown


# --- the switch -------------------------------------------------------------------------------------------------------

@needs_node
def test_the_switch_shows_what_the_server_says_and_the_put_keeps_the_write_permission():
    result = node("""
      const on = acp.consentView({ configured: true, allow_write: true, keep_history: true, history_retention_days: 30 });
      const off = acp.consentView({ configured: true, allow_write: false, keep_history: false, history_retention_days: 14 });
      const bare = acp.consentView({ configured: false, allow_write: false });
      console.log(JSON.stringify([on, off, bare, acp.historyBody("o__a", on.allowWrite, false), acp.historyBody("o__a", false, true),
        acp.consentBody("o__a", true), acp.historyStateText(on), acp.historyStateText(off), acp.historyRetentionText(off)]));
    """)
    on, off, bare, turn_off, turn_on, plain, on_text, off_text, retention = result
    assert on == {"configured": True, "allowWrite": True, "keepHistory": True, "retentionDays": 30}
    assert off["keepHistory"] is False and off["retentionDays"] == 14
    assert bare == {"configured": False, "allowWrite": False, "keepHistory": False, "retentionDays": 30}
    assert turn_off == {"bundle": "o__a", "allow_write": True, "keep_history": False}        # the write permission goes along unchanged
    assert turn_on == {"bundle": "o__a", "allow_write": False, "keep_history": True}
    assert plain == {"bundle": "o__a", "allow_write": True}                                  # «Save consent» never sends keep_history
    assert on_text == "The history of this area is saved." and off_text == "The history of this area is not saved."
    assert retention == "A saved conversation is deleted 14 days after its last message."


def test_the_switch_is_a_real_switch_that_waits_for_a_saved_consent_and_puts_the_consent():
    source = apps("acp.js")
    block = source[source.index('class="apps-acp-switch" role="switch" aria-checked=${consent.keepHistory'):]
    block = block[:block.index("</button>")]
    assert 'type="button"' in source[source.index('class="apps-acp-switch" role="switch" aria-checked=${consent.keepHistory') - 30:]
    assert "disabled=${busy || !consent.configured}" in block and 'helpFor("apps.acp_keep_history")' in block
    assert "saveHistory(!consent.keepHistory)" in block and "ext.apps.acp.history.switch" in source
    save = source[source.index("async function saveHistory(next)"):source.index("const ofArea")]
    assert "apiPut(ACP_CONSENT_PATH, token, historyBody(bundle, consent.allowWrite, next))" in save
    assert "if (busy || !consent || !consent.configured) return;" in save
    assert "ext.apps.acp.history.needs_consent" in source


def test_turning_it_off_only_offers_the_deletion_and_deletes_by_id_when_the_person_presses_it():
    source = apps("acp.js")
    assert "setOffer(!view.keepHistory)" in source
    # the deletion is in one function that only a button calls, and it deletes by id every saved conversation of that area
    assert source.count("deleteConversations(") == 2 and source.count("onClick=${deleteArea}") == 1
    assert "deleteConversations(token, ofArea.map((row) => row.id))" in source
    assert "const ofArea = rowsOfBundle(history.rows, bundle);" in source and "offer && ofArea.length > 0" in source
    assert "apiPost(acpDeletePath(id), token, {})" in source
    # a conversation that is already gone is not a failure; any other one stops the loop
    assert "failure.status === 404" in source
    save = source[source.index("async function saveHistory(next)"):source.index("const ofArea")]
    assert "deleteConversations" not in save and "deleteArea" not in save                  # switching never deletes by itself
    assert 'topic="apps.acp_offer_delete"' in source and 'helpFor("apps.acp_offer_delete")' in source


def test_no_agent_path_turns_the_switch_on_or_deletes_anything_and_nothing_is_inline_styled():
    for name in ("acp.js", "acp-model.js"):
        source = apps(name)
        assert "style=" not in source and "innerHTML" not in source and "dangerouslySetInnerHTML" not in source
    assert "document.write" not in thread_source()


# --- texts and help in both languages ---------------------------------------------------------------------------------

THREAD_KEYS = ["thread.acp.saved", "thread.acp.saved.new", "thread.acp.saved.redacted", "thread.acp.saved.incomplete", "thread.acp.delete",
               "thread.acp.delete.confirm", "thread.acp.delete.yes", "thread.acp.delete.keep", "thread.acp.deleting", "thread.acp.deleted",
               "thread.acp.history.not_saved", "thread.acp.history.size_limit", "thread.acp.kind.prompt", "thread.acp.lost"]

AGREED = {
    "thread.acp.saved": ("Saved conversation. You can read it, but cannot continue the agent session. Start a new conversation in this area.",
                         "Conversación guardada. Puedes leerla, pero no continuar la sesión del agente. Inicia otra conversación en esta área."),
    "thread.acp.delete": ("Delete this conversation", "Borrar esta conversación"),
    "thread.acp.delete.confirm": ("This cannot be undone.", "Esto no se puede deshacer."),
    "thread.acp.history.not_saved": ("This turn was not saved.", "Este turno no se guardó."),
    "thread.acp.history.size_limit": ("History reached the 2 MB limit and will not save more turns.",
                                      "El historial llegó al límite de 2 MB y no guardará más turnos."),
    "thread.acp.lost": ("This conversation was deleted, expired, or was never saved.", "Esta conversación se borró, caducó o nunca se guardó."),
}


def thread_dictionary(language: str) -> dict:
    text = (STATIC_DIR / "i18n" / language / "thread.js").read_text(encoding="utf-8")
    return dict(re.findall(r'^  "(thread\.acp\.[\w.]+)": "(.*)",$', text, re.M))


def test_the_thread_texts_exist_in_both_languages_with_the_agreed_wording():
    en, es = thread_dictionary("en"), thread_dictionary("es")
    assert {key for key in en} == {key for key in es}
    for key in THREAD_KEYS:
        assert key in en and key in es, key
    for key, (english, spanish) in AGREED.items():
        assert en[key] == english and es[key] == spanish, key
    assert "memory" not in en["thread.acp.lost"] and "memoria" not in es["thread.acp.lost"]
    for key in ("thread.acp.saved.redacted",):
        assert "patterns" in en[key] and "patrones" in es[key]                      # the limit of the detector is said


HISTORY_KEYS = ["title", "loading", "failed", "none", "count", "unreadable", "unreadable_file", "live", "redacted", "delete_all",
                "delete_all.confirm", "delete_all.yes", "deleting", "deleted", "delete_failed", "switch", "state_on", "state_off",
                "retention", "needs_consent", "offer", "offer_delete", "offer_keep", "removed", "note", "redaction"]


@needs_node
def test_the_card_texts_exist_in_both_languages_with_the_agreed_wording():
    result = node("console.log(JSON.stringify(dictionaries));")
    en, es = result["en"], result["es"]
    for key in HISTORY_KEYS:
        assert f"ext.apps.acp.history.{key}" in en and f"ext.apps.acp.history.{key}" in es, key
    assert en["ext.apps.acp.history.switch"] == "Keep the history of this area" and es["ext.apps.acp.history.switch"] == "Guardar el historial de esta área"
    assert en["ext.apps.acp.history.delete_all"] == "Delete all conversations" and es["ext.apps.acp.history.delete_all"] == "Borrar todas las conversaciones"
    assert en["ext.apps.acp.history.note"] == ("History stays on this device for 30 days. It does not resume the agent, share data, or encrypt the file; "
                                               "your operating system account protects it.")
    assert es["ext.apps.acp.history.note"] == ("El historial queda en este equipo durante 30 días. No reanuda al agente, comparte datos ni cifra el archivo; "
                                               "lo protege tu cuenta del sistema operativo.")
    assert en["ext.apps.acp.history.redaction"] == ("Recognized secrets are redacted before saving. Detection uses patterns and cannot guarantee "
                                                    "that every secret is found.")
    assert es["ext.apps.acp.history.redaction"].startswith("Los secretos reconocidos se tachan antes de guardar. La detección usa patrones")
    for key in ("offer", "retention", "count", "deleted"):                           # placeholders are the same in both languages
        assert re.findall(r"\{\w+\}", en[f"ext.apps.acp.history.{key}"]) == re.findall(r"\{\w+\}", es[f"ext.apps.acp.history.{key}"]), key


@needs_node
def test_card_texts_are_evaluated_when_drawn_in_the_language_of_the_page():
    spanish = node("""console.log(JSON.stringify([acp.historyStateText({ keepHistory: true }), acp.historyRetentionText({ retentionDays: 30 })]));""", "es-MX")
    assert spanish == ["El historial de esta área se guarda.", "Una conversación guardada se borra 30 días después de su último mensaje."]
    english = node("""console.log(JSON.stringify([acp.historyStateText({ keepHistory: false }), acp.historyRetentionText({ retentionDays: 30 })]));""")
    assert english == ["The history of this area is not saved.", "A saved conversation is deleted 30 days after its last message."]


CARD_TOPICS = ["apps.acp_history", "apps.acp_keep_history", "apps.acp_offer_delete", "apps.acp_delete_all"]


def test_every_new_control_has_its_help_in_both_languages():
    help_source, spanish, source = apps("help.js"), apps("i18n.js"), apps("acp.js")
    for topic in CARD_TOPICS:
        assert f'"{topic}": {{' in help_source and f'"help.{topic}": {{' in spanish, topic
        assert f'topic="{topic}"' in source, topic
    for topic in ("apps.acp_keep_history", "apps.acp_offer_delete", "apps.acp_delete_all"):
        assert f'helpFor("{topic}")' in source, topic
    runs_en, runs_es = raw("help-text-runs.js"), (STATIC_DIR / "i18n" / "es" / "runs.js").read_text(encoding="utf-8")
    assert '"acp_thread.delete": {' in runs_en and '"help.acp_thread.delete": {' in runs_es


def test_the_css_of_the_card_uses_variables_and_targets_and_the_narrow_screens_fit():
    css = apps("style.css")
    block = css[css.index("/* ACP-T05:"):css.index("/* EXA-T06")]
    assert "var(--target)" in block and "min-width: 0" in block and "overflow-wrap: anywhere" in block
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", block) and "style=" not in block
    assert "width:" not in block.replace("min-width", "")                              # no fixed width that could overflow at 375 or 390 px
