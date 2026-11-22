# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI3-T02: help mode. The "?" blocks hide until the user asks for them (button or F1).

Static checks over the shell, the stylesheet and the views (read as text) and a Node check of the pure helpers of
``shell-state.js`` (skipped when Node is not installed). No TestClient, no browser."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")


def read(path) -> str:
    return path.read_text(encoding="utf-8")


def help_mode_block() -> str:
    css = read(STATIC_DIR / "app.css")
    start = css.index("GUI3-T02 help mode")
    following = re.search(r"/\* [A-Z0-9]+-T\d+[ :*]", css[start + 1:])       # the next block marker: later cards add their own rules
    return css[start:start + 1 + following.start()] if following else css[start:]


def run_node(script: str):
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_switch_is_a_button_with_aria_pressed_and_its_help():
    shell = read(JS / "shell.js")
    match = re.search(r'<button[^>]*aria-pressed=\$\{helpOn[^>]*?>(?:<\$\{Icon\} name="help" />)?Ayuda</button>', shell, re.S)
    assert match, "the help-mode switch must be a button named Ayuda with aria-pressed"
    assert 'helpFor("shell.help_mode")' in match.group(0)
    assert '<${Help} topic="shell.help_mode"' in shell
    assert "shell.help_mode" in read(JS / "help-text.js")
    assert 'class="app" data-help=${helpOn ? "on" : "off"}' in shell


def test_f1_toggles_the_mode_with_prevent_default_and_nothing_else_is_captured():
    shell = read(JS / "shell.js")
    handler = shell[shell.index("const onHelpKey"):shell.index('removeEventListener("keydown", onHelpKey)')]
    assert "isHelpKey(event)" in handler and "event.preventDefault()" in handler and "toggleHelp()" in handler
    state = read(JS / "shell-state.js")
    assert len(re.findall(r'event\.key === "', state)) == 1       # F1 is the only key shell-state.js looks at
    assert '"F1"' in state
    assert "writeHelpMode(next)" in shell                          # the mode is stored on every change


def test_mode_is_remembered_only_through_shell_state_read_and_write_stored():
    state = read(JS / "shell-state.js")
    assert "multiagent.gui.help-mode" in state
    assert "readStored(HELP_MODE_KEY)" in state and "writeStored(HELP_MODE_KEY" in state
    assert len(re.findall(r"window\.localStorage", state)) == 2   # still one read and one write
    assert "localStorage" not in read(JS / "shell.js")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_mode_default_off_round_trip_blocked_storage_and_f1_filter():
    url = (JS / "shell-state.js").resolve().as_uri()
    out = run_node("""
      const store = new Map();
      globalThis.window = { localStorage: { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, String(v)) } };
      const s = await import(%s);
      const first = s.readHelpMode();
      s.writeHelpMode(true); const on = s.readHelpMode(); const raw = store.get("multiagent.gui.help-mode");
      s.writeHelpMode(false); const off = s.readHelpMode();
      globalThis.window = { get localStorage() { throw new Error("blocked"); } };
      let blocked = null, error = null;
      try { s.writeHelpMode(true); blocked = s.readHelpMode(); } catch (e) { error = String(e); }
      const keys = [
        s.isHelpKey({ key: "F1" }), s.isHelpKey({ key: "F1", ctrlKey: true }), s.isHelpKey({ key: "F1", shiftKey: true }),
        s.isHelpKey({ key: "F2" }), s.isHelpKey({ key: "?" }), s.isHelpKey(null),
      ];
      console.log(JSON.stringify({ first, on, raw, off, blocked, error, keys }));
    """ % json.dumps(url))
    assert out == {"first": False, "on": True, "raw": "on", "off": False, "blocked": False, "error": None,
                   "keys": [True, False, False, False, False, False]}


def test_hide_rule_only_applies_under_help_off_and_the_exceptions_exist():
    block = help_mode_block()
    assert '[data-help="off"] .help-block { display: none; }' in block
    for line in block.splitlines():
        if "{" in line and ".help-block" in line.split("{")[0]:
            head = re.sub(r"\([^)]*\)", "", line.split("{")[0])      # commas inside :is() and :not() do not separate selectors
            selectors = [part for part in head.split(",") if part.strip()]
            assert all(part.strip().startswith('[data-help="off"]') for part in selectors), line
    assert "h2 + .help-block" in block                                 # the help that follows the screen title
    assert ".help-block.help-always" in block and ".help-always .help-block" in block
    assert ":is(label, input, select, textarea) + .help-block:not(.compact)" in block   # the one-line hint of a field
    assert ".help-toggle" in block and ".help-more" in block            # the field hint loses its "?" and its explanation
    assert 'data-help="on"' not in block                               # help on changes nothing: everything shows as today
    assert "visibility" not in block and "opacity" not in block       # the nodes stay in the DOM, never merely invisible


def test_target_of_the_question_mark_is_still_44_px():
    css = read(STATIC_DIR / "app.css")
    assert re.search(r'\.help-toggle::after \{ content: ""; position: absolute; inset: -10px; \}', css)
    assert re.search(r"\.help-toggle \{[^}]*width: 1\.5rem; height: 1\.5rem", css)


def test_help_still_draws_the_hint_element_with_its_id_and_only_adds_the_always_class():
    help_js = read(JS / "help.js")
    assert 'id=${id + "-hint"} class="hint-text"' in help_js             # the element aria-describedby points at
    assert "export const helpHintId" in help_js
    assert 'aria-controls=${id + "-more"}' in help_js and 'aria-expanded=${open ? "true" : "false"}' in help_js
    assert 'always ? " help-always" : ""' in help_js
    assert "display" not in help_js and "style=" not in help_js          # hiding is the stylesheet's job, never inline


# The 13 views: where the help of the title stays visible with the mode off.
OWN_TITLE_HELPS = {   # the view draws it, so it does not follow the <h2>: it carries help-always
    "sessions.js": "sessions.list", "runs.js": "runs.section", "usage.js": "usage.section",
    "evaluation.js": "evaluation.section", "teams.js": "teams.section", "plans.js": "plans.section",
}


@pytest.mark.parametrize("file,topic", sorted(OWN_TITLE_HELPS.items()))
def test_own_views_mark_the_help_of_their_title_as_always_visible(file, topic):
    assert f'<${{Help}} topic="{topic}" always=${{true}} />' in read(JS / file)


def test_extension_views_get_their_title_help_right_after_the_h2():
    main = read(JS / "main.js")
    # SIM-T02 put the «only in advanced» notice between the title and the help, and the help is not drawn while it shows
    assert re.search(r"</h2>`\}\s*\n\s*\$\{blocked && html`<\$\{AdvancedOnly\} token=\$\{token\} />`\}\s*\n\s*\$\{!blocked && entry\?\.topic && html`<\$\{Help\} key=\$\{entry\.id\} topic=\$\{entry\.topic\} />`\}", main)
    topics = set()
    for path in sorted((STATIC_DIR / "extensions").glob("*/index.js")):
        topics.update(m.group(1) for m in re.finditer(r'topic: "([a-z_]+\.view)"', read(path)))
    assert topics == {"approvals.view", "apps.view", "continuity.view", "document_alerts.view", "project.view", "task_waiting.view"}


def test_approval_cards_sit_in_a_help_always_container():
    folder = STATIC_DIR / "extensions" / "approvals"
    assert 'class="approvals-list help-always"' in read(folder / "index.js")
    assert 'class="approvals-list approvals-scan-live help-always"' in read(folder / "bundle-scan.js")


def test_form_field_hints_follow_their_label_or_control():
    """The selector of the field hint (label/input/select/textarea + .help-block) fits the real markup: the filter form
    of the session list and the notes of plans and teams put the non-compact <Help> right after the label."""
    sessions = read(JS / "sessions.js")
    for topic in ("source", "surface", "text", "cwd", "since", "until"):
        assert re.search(r'</label>\s*\n\s*<\$\{Help\} topic="sessions\.%s" />' % topic, sessions), topic
    assert re.search(r'</label><\$\{Help\} topic="plans\.note" />', read(JS / "plans.js"))
    assert re.search(r'</label><\$\{Help\} topic="teams\.note" />', read(JS / "teams.js"))
