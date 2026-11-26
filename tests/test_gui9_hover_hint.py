# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T12: the hover hint shows next to a control that helpFor() describes, with help mode on or off."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

HELP = STATIC_DIR / "js" / "help.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_node(script: str):
    result = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# A minimal DOM: listeners keep their capture flag, timers run on a fake clock, the pop can be a popover or not.
HARNESS = """
  import { installHoverHint } from %(help)s;
  function build({ popover = false, focusVisible = true } = {}) {
    const docListeners = {}, winListeners = {};
    let now = 0, nextId = 1; const timers = new Map();
    const win = { innerWidth: 1280, innerHeight: 800,
      setTimeout: (f, ms) => { const id = nextId++; timers.set(id, { f, at: now + ms }); return id; },
      clearTimeout: (id) => timers.delete(id),
      addEventListener: (t, f) => { winListeners[t] = f; }, MutationObserver: class { constructor(f) { win.observed = f; } observe() {} } };
    const advance = (ms) => { now += ms; for (const [id, t] of [...timers]) if (t.at <= now) { timers.delete(id); t.f(); } };
    const hintEl = { classList: { contains: (c) => c === "hint-text" }, textContent: " One line. ",
      getAttribute: (n) => n === "data-hint-title" ? "Title" : null };
    function makeEl() {
      const el = { children: [], classes: new Set(), attrs: {}, popoverOpen: false, hidden: false, isConnected: false, listeners: {}, _text: "",
        style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
        set textContent(v) { this._text = v; if (v === "") this.children = []; }, get textContent() { return this._text; },
        classList: { add(...c) { c.forEach((x) => el.classes.add(x)); }, remove(...c) { c.forEach((x) => el.classes.delete(x)); } },
        setAttribute(k, v) { el.attrs[k] = v; }, hasAttribute: (k) => k in el.attrs,
        appendChild(c) { el.children.push(c); c.isConnected = true; }, contains: (n) => n === el || el.children.includes(n),
        addEventListener(t, f) { el.listeners[t] = f; }, getBoundingClientRect: () => ({ width: 200, height: 60 }),
        matches: (sel) => sel === ":popover-open" ? el.popoverOpen : true };
      if (popover) { el.showPopover = () => { el.popoverOpen = true; el.shown = (el.shown || 0) + 1; }; el.hidePopover = () => { el.popoverOpen = false; }; }
      return el;
    }
    const control = { isConnected: true, getAttribute: (n) => n === "aria-describedby" ? "help-x-hint" : null, closest() { return control; },
      contains: (n) => n === control, matches: (sel) => sel === ":focus-visible" ? focusVisible : true,
      getBoundingClientRect: () => ({ left: 100, right: 130, top: 10, bottom: 40 }) };
    const other = { closest: () => null, contains: () => false };
    const doc = { defaultView: win, body: makeEl(), getElementById: (id) => id === "help-x-hint" ? hintEl : null, createElement: makeEl,
      addEventListener: (t, f, capture) => { docListeners[t] = { f, capture: capture === true }; } };
    doc.body.isConnected = true;
    installHoverHint(doc);
    const pop = () => doc.body.children[0];
    const key = (extra = {}) => { const e = { key: "Escape", isComposing: false, prevented: false, immediate: false,
      preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.immediate = true; }, ...extra };
      docListeners.keydown.f(e); return e; };
    return { doc, win, docListeners, winListeners, advance, control, other, pop, key };
  }
""" % {"help": json.dumps(HELP.as_uri())}


def scenario(body: str):
    return run_node(HARNESS + "\n" + body)


def test_source_installs_a_delegate_with_classes_and_cssom_only():
    source = HELP.read_text(encoding="utf-8")
    assert "installHoverHint" in source and "pointerover" in source and "focusin" in source
    assert '"Escape"' in source and "preventDefault" in source and "stopImmediatePropagation" in source
    assert "isComposing" in source and "hashchange" in source and "popover" in source
    assert 'role", "tooltip"' in source
    for side in ("hint-top", "hint-bottom", "hint-left", "hint-right"):
        assert side in source
    for bad in ("innerHTML", "__html", "eval(", "style=", "setAttribute(\"style\"", "document.write"):
        assert bad not in source, bad
    assert "data-help-mode" not in source and "data-help" not in source.replace("data-help-exempt", "")  # no read of help mode
    assert 'typeof document.addEventListener === "function"' in source


def test_css_block_is_last_and_marked():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "/* GUI4-T12 */" in css
    block = css[css.index("/* GUI4-T12 */"):]
    assert ".hover-hint" in block and "style=" not in block and "!important" not in block
    assert "margin: 0" in block  # a popover would otherwise be centred by the user agent


@needs_node
def test_placement_prefers_below_then_above_and_stays_in_the_viewport():
    out = run_node(f"""
      import {{ placeHint }} from {json.dumps(HELP.as_uri())};
      const view = {{ width: 1280, height: 800 }}, size = {{ width: 200, height: 60 }};
      console.log(JSON.stringify({{
        below: placeHint({{ left: 100, right: 130, top: 10, bottom: 40 }}, size, view),
        above: placeHint({{ left: 100, right: 130, top: 740, bottom: 780 }}, size, view),
        edge: placeHint({{ left: 1250, right: 1280, top: 10, bottom: 40 }}, size, view),
        left_edge: placeHint({{ left: 0, right: 20, top: 10, bottom: 40 }}, size, view),
        narrow: placeHint({{ left: 300, right: 340, top: 10, bottom: 40 }}, size, {{ width: 390, height: 800 }}),
      }}));
    """)
    assert out["below"]["side"] == "bottom" and out["below"]["top"] == 48
    assert out["above"]["side"] == "top" and out["above"]["top"] == 740 - 8 - 60
    assert out["edge"]["left"] == 1280 - 200 and out["left_edge"]["left"] == 0
    assert 0 <= out["narrow"]["left"] and out["narrow"]["left"] + 200 <= 390


@needs_node
def test_focus_shows_and_escape_closes_only_the_hint():
    out = scenario("""
      const d = build(); const { docListeners, control } = d;
      docListeners.focusin.f({ target: control });
      const p = d.pop();
      const shown = { hidden: p.hidden, role: p.attrs.role, title: p.children[0]._text, line: p.children[1]._text,
        side: [...p.classes].filter((c) => c.startsWith("hint-")), top: p.style.props.top, describedby: control.getAttribute("aria-describedby") };
      const composing = d.key({ isComposing: true }); const stillOpen = !p.hidden;
      const e = d.key();
      console.log(JSON.stringify({ shown, composing: composing.immediate, stillOpen, afterEscape: p.hidden, e: [e.prevented, e.immediate],
        capture: docListeners.keydown.capture }));
    """)
    assert out["shown"] == {"hidden": False, "role": "tooltip", "title": "Title", "line": "One line.", "side": ["hint-bottom"],
                            "top": "48px", "describedby": "help-x-hint"}
    assert out["composing"] is False and out["stillOpen"] is True  # an IME Escape is left alone
    assert out["afterEscape"] is True and out["e"] == [True, True]
    assert out["capture"] is True  # a capture listener on the document, ahead of the shell's own


@needs_node
def test_mouse_waits_400_ms_and_leaves_after_150_ms_unless_over_the_hint():
    out = scenario("""
      const d = build(); const L = d.docListeners;
      L.pointerover.f({ pointerType: "mouse", target: d.control });
      d.advance(399); const before = d.pop() === undefined;
      d.advance(1); const p = d.pop(); const after = p.hidden;
      L.pointerout.f({ pointerType: "mouse", target: d.control, relatedTarget: d.other });
      p.listeners.pointerenter(); d.advance(500); const overHint = p.hidden;
      p.listeners.pointerleave(); d.advance(149); const grace = p.hidden; d.advance(1);
      console.log(JSON.stringify({ before, after, overHint, grace, closed: p.hidden }));
    """)
    assert out == {"before": True, "after": False, "overHint": False, "grace": False, "closed": True}


@needs_node
def test_touch_hold_500_ms_opens_and_a_touch_outside_closes_but_not_on_the_hint():
    out = scenario("""
      const d = build(); const L = d.docListeners;
      L.pointerover.f({ pointerType: "touch", target: d.control }); d.advance(1000); const noHover = d.pop() === undefined;
      L.pointerdown.f({ pointerType: "touch", target: d.control });
      d.advance(499); const early = d.pop() === undefined;
      L.pointerup.f({}); d.advance(10); const released = d.pop() === undefined;
      L.pointerdown.f({ pointerType: "touch", target: d.control }); d.advance(500); const p = d.pop(); const open = !p.hidden;
      L.pointerdown.f({ pointerType: "touch", target: p }); const onHint = !p.hidden;
      L.pointerdown.f({ pointerType: "touch", target: d.other }); const outside = p.hidden;
      console.log(JSON.stringify({ noHover, early, released, open, onHint, outside }));
    """)
    assert out == {"noHover": True, "early": True, "released": True, "open": True, "onHint": True, "outside": True}


@needs_node
def test_focus_visible_false_does_not_show():
    out = scenario("""
      const d = build({ focusVisible: false }); d.docListeners.focusin.f({ target: d.control });
      console.log(JSON.stringify({ none: d.pop() === undefined }));
    """)
    assert out == {"none": True}


@needs_node
def test_popover_is_used_when_the_browser_has_it_and_closed_with_the_hint():
    out = scenario("""
      const d = build({ popover: true }); d.docListeners.focusin.f({ target: d.control });
      const p = d.pop(); const open = { attr: p.attrs.popover, open: p.popoverOpen, shown: p.shown };
      d.docListeners.focusin.f({ target: d.control }); // shown again: showPopover is not called twice
      d.key();
      console.log(JSON.stringify({ open, again: p.shown, closed: p.popoverOpen, hidden: p.hidden }));
    """)
    assert out == {"open": {"attr": "manual", "open": True, "shown": 1}, "again": 1, "closed": False, "hidden": True}


@needs_node
def test_without_popover_support_the_pop_stays_a_plain_element():
    out = scenario("""
      const d = build(); d.docListeners.focusin.f({ target: d.control });
      console.log(JSON.stringify({ attr: "popover" in d.pop().attrs, hidden: d.pop().hidden }));
    """)
    assert out == {"attr": False, "hidden": False}


@needs_node
def test_hint_closes_when_its_control_leaves_the_document_or_the_hash_changes():
    out = scenario("""
      const d = build(); d.docListeners.focusin.f({ target: d.control }); const p = d.pop();
      d.win.observed(); const kept = !p.hidden;
      d.control.isConnected = false; d.win.observed(); const orphan = p.hidden;
      const e = build(); e.docListeners.focusin.f({ target: e.control }); const q = e.pop();
      e.winListeners.hashchange(); console.log(JSON.stringify({ kept, orphan, hash: q.hidden }));
    """)
    assert out == {"kept": True, "orphan": True, "hash": True}


@needs_node
def test_installing_twice_on_the_same_document_adds_no_second_set_of_listeners():
    out = scenario("""
      const d = build(); const before = d.docListeners.keydown.f;
      installHoverHint(d.doc);
      console.log(JSON.stringify({ same: d.docListeners.keydown.f === before, flag: d.doc.__hoverHintInstalled === true }));
    """)
    assert out == {"same": True, "flag": True}
