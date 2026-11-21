# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI sessions view (GUI-T05): static checks of the client code, how it is served, and that the fields the
client reads are the ones the API sends. No browser and no Node: the JavaScript is read as text."""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.security import CSP
from multiagent.gui.token import ensure_token
from session_fixtures import (
    CLAUDE_ID,
    add_framework_run,
    append,
    make_services,
    make_stores,
    write_claude,
    write_codex,
    write_copilot,
)

JS = STATIC_DIR / "js"
VIEW_FILES = ["sessions.js", "session-detail.js", "session-items.js", "session-text.js", "route.js"]
FORBIDDEN = [
    (r"\binnerHTML\b", "innerHTML"), (r"\bouterHTML\b", "outerHTML"), (r"insertAdjacentHTML", "insertAdjacentHTML"),
    (r"dangerouslySetInnerHTML", "dangerouslySetInnerHTML"), (r"\beval\s*\(", "eval"),
    (r"\bnew\s+Function\b", "new Function"), (r"(?<![\w.])Function\s*\(", "Function()"),
    (r"document\.write", "document.write"), (r"javascript:", "javascript: URL"),
    (r"""\bon[a-z]+\s*=\s*["']""", "inline handler attribute"),
    (r"""setAttribute\(\s*["']on""", "setAttribute on*"),
    (r"""\bset(?:Timeout|Interval)\(\s*["'`]""", "string timer"),
    (r"\bcreateContextualFragment\b", "createContextualFragment"), (r"DOMParser", "DOMParser"),
    (r"\.srcdoc\b", "srcdoc"), (r"\bimport\s*\(\s*[^)'\"`]", "dynamic import of a variable"),
    (r"https?://", "remote URL"), (r"\bnew\s+EventSource\b", "EventSource (cannot send the token)"),
    (r"\bwindow\.open\b", "window.open"), (r"""target\s*=\s*["']?_blank""", "target=_blank"),
    (r"\blocalStorage\b", "localStorage"), (r"\bpostMessage\b", "postMessage"),
]


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


# --- the new files are there and clean ------------------------------------------------------------

def test_view_files_exist_and_are_loaded_only_through_main():
    for name in VIEW_FILES:
        assert (JS / name).is_file(), name
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert re.findall(r"<script[^>]*>", html) == ['<script type="module" src="/gui/static/js/main.js">']


@pytest.mark.parametrize("name", VIEW_FILES + ["stream.js", "main.js", "runs.js", "open-in-app.js"])
def test_view_code_has_no_html_injection_eval_inline_handlers_or_remote_code(name):
    text = read(name)
    for pattern, label in FORBIDDEN:
        assert not re.search(pattern, text), f"{name}: {label}"


def test_session_content_has_no_raw_html_escape_hatch_and_clock_ticks_slowly():
    for path in sorted(JS.glob("*.js")):
        assert "__html" not in path.read_text(encoding="utf-8"), path.name
    tick = int(re.search(r"CLOCK_TICK_MS = (\d+)", read("sessions.js"))[1])
    assert tick >= 10000  # relative times only need a refresh now and then; nothing polls fast
    assert not re.search(r"setInterval\([^;]*,\s*\d{1,4}\s*\)", "".join(read(n) for n in VIEW_FILES))


# --- the view is registered and is the default one --------------------------------------------------

def test_main_registers_sessions_as_the_first_and_default_section():
    main = read("main.js")
    assert 'import { Sessions } from "./sessions.js"' in main
    sections = re.search(r"const SECTIONS = \[(.*?)\];", main, re.S)[1]
    section_ids = re.findall(r'id: "(\w+)"', sections)
    assert section_ids[:2] == ["sessions", "runs"]
    assert len(section_ids) == len(set(section_ids))
    assert "Sesiones" in sections and "pendiente de implementar" not in main
    assert re.search(r"<\$\{Sessions\}[^>]*route=\$\{route\}", main)
    route = read("route.js")
    assert 'export const DEFAULT_SECTION = "sessions"' in route
    assert "useRoute" in main and "onClick=${() => navigate(s.id)}" in read("sidebar.js")


def test_main_keeps_the_runs_view_and_its_link_from_sessions():
    assert 'import { Runs } from "./runs.js"' in read("main.js")
    assert "initialRunId" in read("runs.js") and "initialRunId" in read("main.js")
    detail = read("session-detail.js")
    assert 'routeHref("runs", summary.native_id)' in detail  # framework sessions link to the GUI-T07 detail
    route = read("route.js")
    assert "run=" in route  # the API's own link for a framework session: /gui/#run=<id>


def test_route_hrefs_are_hash_only():
    route = read("route.js")
    assert re.search(r'return "#" \+ section', route)
    assert "location.hash = next" in route


def test_the_open_in_app_control_lives_in_its_own_file_and_fills_the_slot():
    text = "".join(read(name) for name in VIEW_FILES)
    assert text.count('id="open-in-app-slot"') == 1  # the header slot that GUI-T06 fills
    assert "<${OpenInApp}" in read("session-detail.js")
    for other in ("sessions.js", "session-items.js", "session-text.js", "route.js"):
        for forbidden in ("codex://", "ghapp://", "open_in_app", "openInApp"):
            assert forbidden not in read(other), (other, forbidden)  # links are built in open-in-app.js only


# --- accessibility and texts ---------------------------------------------------------------------

def test_every_filter_control_has_a_label():
    text = read("sessions.js")
    controls = re.findall(r'<(?:input|select)\b[^>]*\bid="(sf-[a-z]+)"', text)
    assert sorted(controls) == ["sf-cwd", "sf-since", "sf-source", "sf-surface", "sf-text", "sf-until"]
    for control in controls:
        assert f'<label for="{control}">' in text, control
    assert 'role="search"' in text and 'aria-label="Filtrar sesiones"' in text
    assert "Incluir subagentes" in text  # the checkbox sits inside its own label


def test_list_rows_are_links_and_escape_goes_back():
    sessions = read("sessions.js")
    assert re.search(r'<a class="session-row" href=\$\{routeHref\("sessions", session\.id\)\}', sessions)
    detail = read("session-detail.js")
    assert 'event.key === "Escape"' in detail and 'document.addEventListener("keydown"' in detail
    assert "removeEventListener" in detail  # the listener dies with the detail
    assert "focus()" in sessions and "focus()" in detail  # focus moves to the detail and back to the row
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert ".session-row:focus-visible" in css and "[hidden] { display: none !important; }" in css


def test_live_indicator_is_announced_and_can_be_paused():
    detail = read("session-detail.js")
    assert re.search(r'role="status" aria-live="polite">\$\{followText\}', detail)
    for text in ("En vivo", "Pausar seguimiento", "Reanudar seguimiento", "En pausa", "Reconectando"):
        assert text in detail, text
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css


def test_texts_are_spanish_and_cover_the_required_states():
    items, text, sessions, detail = (read(n) for n in ("session-items.js", "session-text.js", "sessions.js", "session-detail.js"))
    assert "Razonamiento omitido" in items
    assert "desconocido" in text and "Sin título" in text
    assert "gui_session_roots" in text and "GUI_SESSION_ROOTS" in text  # how to configure the allowed folders
    assert "ROOTS_HELP" in sessions and "data.hint" in sessions  # the API's hint is shown, not swallowed
    assert "Cargar más" in sessions and "Cargar más" in detail
    assert "Resultado de herramienta" in items and "<details>" in items  # tool results start collapsed
    assert "recortado" in items  # the truncated flag
    for label in ("Claude", "Codex", "Copilot", "Framework"):
        assert f'label: "{label}"' in text
    assert "Load more" not in sessions + detail and "Back to list" not in detail


def test_tool_calls_and_kinds_the_api_can_send_are_all_handled():
    items = read("session-items.js")
    kinds = ["user_message", "assistant_message", "reasoning_omitted", "tool_call", "tool_result", "system", "subagent",
             "compaction", "usage"]
    for kind in kinds:
        assert f'case "{kind}"' in items, kind
    assert "default:" in items  # unknown kinds become a small note and never break the page


def test_session_stream_support_in_the_client():
    stream = read("stream.js")
    assert '"session=" + encodeURIComponent(session)' in stream
    assert "Last-Event-ID" in stream and "authHeaders(token)" in stream
    detail = read("session-detail.js")
    assert "startStream({" in detail and "session: sessionId" in detail and "after: cursor.current" in detail
    assert 'event.type === "item"' in detail and 'event.type === "notice"' in detail and 'event.type === "error"' in detail


# --- how it is served ----------------------------------------------------------------------------

@pytest.fixture
def world(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores)
    write_claude(stores)
    write_codex(stores)
    write_copilot(stores)
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, services=services, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


@pytest.mark.parametrize("name", VIEW_FILES + ["stream.js", "main.js", "runs.js", "api.js", "bus.js", "text.js"])
def test_client_files_are_served_with_the_csp(world, name):
    response = world.client.get(f"/gui/static/js/{name}")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert response.headers["content-security-policy"] == CSP
    assert "require-trusted-types-for 'script'" in CSP and "script-src 'self'" in CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.text == (JS / name).read_text(encoding="utf-8").replace("\r\n", "\n") or response.content


def test_the_page_and_its_stylesheet_are_served_without_a_token(world):
    page = world.client.get("/gui/")
    assert page.status_code == 200 and "/gui/static/js/main.js" in page.text
    assert page.headers["content-security-policy"] == CSP
    css = world.client.get("/gui/static/app.css")
    assert css.status_code == 200 and ".session-row" in css.text


# --- the client reads the fields the API sends -----------------------------------------------------

def used(text: str, name: str) -> set[str]:
    return set(re.findall(rf"{name}\.([a-z_]+)", text))


def test_fields_read_by_the_client_exist_in_the_api_answers(world):
    run_id = add_framework_run(world.services, model="m1", events=2)
    path = world.services.settings.gui_claude_home / "projects" / "proj" / f"{CLAUDE_ID}.jsonl"
    append(path, [{"type": "assistant", "timestamp": "2026-10-01T10:00:09.000Z", "message": {
        "id": "m9", "role": "assistant", "model": "claude-test",
        "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}}])
    listing = world.client.get("/gui/api/sessions", headers=world.auth).json()
    summary = world.client.get(f"/gui/api/sessions/claude:{CLAUDE_ID}", headers=world.auth).json()
    run_summary = world.client.get(f"/gui/api/sessions/framework:{run_id}", headers=world.auth).json()
    items = world.client.get(f"/gui/api/sessions/claude:{CLAUDE_ID}/items", headers=world.auth).json()
    run_items = world.client.get(f"/gui/api/sessions/framework:{run_id}/items", headers=world.auth).json()
    sessions, detail, items_js, text = (read(n) for n in ("sessions.js", "session-detail.js", "session-items.js", "session-text.js"))
    tool = next(i["tool"] for i in items["items"] if i["kind"] == "tool_call")

    assert set(listing) == {"sessions", "next_cursor", "warnings", "hint"}
    assert set(items) == {"items", "next_cursor", "done", "warnings"} == set(run_items)
    assert used(sessions, "page") <= set(listing)
    assert used(sessions, "session") <= set(summary) and used(text, "session") <= set(summary)
    assert used(detail, "summary") <= set(summary) and used(detail, "summary") <= set(run_summary)
    assert used(detail, "child") <= set(summary)
    assert used(items_js, "item") <= set(items["items"][0]) and used(items_js, "item") <= set(run_items["items"][0])
    assert used(items_js, "tool") <= set(tool)


def test_framework_stream_events_carry_what_the_client_turns_into_items(world):
    run_id = add_framework_run(world.services, events=1)
    stream = world.client.get(f"/gui/api/stream?session=framework:{run_id}", headers=world.auth).text
    block = next(b for b in stream.split("\n\n") if "event: workflow_started" in b)
    event = json.loads(next(line for line in block.splitlines() if line.startswith("data: "))[6:])
    assert {"id", "ts", "type", "payload"} <= set(event)
    detail = read("session-detail.js")
    converter = detail[detail.index("function frameworkItem"):detail.index("const FOLLOW_TEXT")]
    assert used(converter, "event") <= set(event)


def test_the_framework_link_the_api_gives_is_the_one_the_router_understands(world):
    run_id = add_framework_run(world.services)
    summary = world.client.get(f"/gui/api/sessions/framework:{run_id}", headers=world.auth).json()
    assert summary["open_in_app"]["value"] == f"/gui/#run={run_id}"
    assert summary["native_id"] == run_id and summary["cwd"] is None  # what the header relies on for runs


def test_empty_roots_page_has_the_hint_the_client_shows(tmp_path):
    stores = make_stores(tmp_path)
    services = make_services(stores, gui_session_roots=[])
    token = ensure_token(services.settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        page = client.get("/gui/api/sessions", headers={"Authorization": f"Bearer {token}"}).json()
    services.runs.close()
    assert page["sessions"] == [] and page["hint"] and "gui_session_roots" in page["hint"]
    help_text = read("session-text.js")
    assert "gui_session_roots" in help_text and "GUI_SESSION_ROOTS" in help_text
