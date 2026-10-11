# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI contextual help (GUI-T12): every control and section of the views has a short hint and a "?" explanation.

Static checks over the JavaScript (read as text, like the other GUI view tests): the help texts are short, plain and
complete; every interactive control of the views points at its help (aria-describedby through ``helpFor``) or says
why it has none (``data-help-exempt``); and every topic a view uses exists. A small Node check (skipped when Node is
not installed) runs the entries that are built from data."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")

# The modules that draw the screens: every control they render must have help.
VIEW_FILES = [
    "main.js", "runs.js", "create-run.js", "run-detail.js", "human-actions.js",
    "sessions.js", "session-detail.js", "session-items.js", "open-in-app.js", "subagents-panel.js",
]
CONTROL_TAGS = {"input", "select", "textarea", "button"}

# The only controls without help, and why. A new exemption must be added here on purpose.
ALLOWED_EXEMPT = {
    "navigation": "the section tabs: their label is the section name, and each section has its own help",
    "data-row": "a row of the list of runs: it is data, not a control that does something",
    "confirm": "the two buttons of a confirmation box, which already explains what is going to happen",
}
EXPECTED_EXEMPT = Counter({
    ("main.js", "navigation"): 1,
    ("runs.js", "data-row"): 1,
    ("human-actions.js", "confirm"): 2,
    ("open-in-app.js", "confirm"): 2,
})

# Topics built from data: the expression the view passes, and the HELP keys it can resolve to (None: the entry is
# built by fieldHelpEntry from the workflow input, so there is no key).
DYNAMIC_TOPICS = {
    '"field."+item.name': None,
    '"open."+summary.source': {"open.claude", "open.codex", "open.copilot"},
}

# Sections (headings with an id) and the topic that explains each; run and session titles are data, not sections.
SECTION_TOPICS = {
    "sessions-title": "sessions.list", "children-title": "session.children", "items-title": "session.items",
    "roots-title": "sessions.roots", "preview-title": "preview.panel", "counters-title": "run.counters",
    "steps-title": "run.steps", "history-title": "run.history", "results-title": "run.results",
    "inputs-title": "run.inputs", "human-actions-title": "human.section", "sub-title": "subagents.panel",
}
DATA_HEADINGS = {"run-title", "session-title"}

MAX_TITLE, MAX_HINT, MAX_SENTENCE, MAX_MORE = 60, 110, 220, 700
FORBIDDEN_IN_TEXT = re.compile(r"[<>`]|\$\{|https?:|javascript:|\b(?:the|and|click|please|payload|endpoint)\b", re.I)


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def json_block(name: str) -> dict:
    """``export const NAME = { ... };`` of help-text.js: strict JSON, so it can be read without running the browser."""
    match = re.search(rf"^export const {name} = (\{{.*?^\}});$", read("help-text.js"), re.S | re.M)
    assert match, f"{name} is not a plain JSON object ending in a line that is just '}};'"
    return json.loads(match[1])


HELP = json_block("HELP")
MODE_HINTS = json_block("MODE_HINTS")


# --- the texts ----------------------------------------------------------------------------------------------------

def test_every_entry_has_a_title_a_hint_and_two_to_four_short_sentences():
    assert len(HELP) >= 60
    for key, entry in HELP.items():
        assert re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", key), key
        assert set(entry) == {"title", "hint", "more"}, key
        title, hint, more = entry["title"], entry["hint"], entry["more"]
        assert isinstance(title, str) and title.strip() and len(title) <= MAX_TITLE, key
        assert isinstance(hint, str) and hint.strip() and len(hint) <= MAX_HINT, f"{key}: hint {len(hint)} chars"
        assert isinstance(more, list) and 2 <= len(more) <= 4, f"{key}: {len(more)} sentences"
        for sentence in more:
            assert isinstance(sentence, str) and sentence.strip() == sentence and sentence.endswith("."), (key, sentence)
            assert len(sentence) <= MAX_SENTENCE, f"{key}: sentence of {len(sentence)} chars"
        assert sum(map(len, more)) <= MAX_MORE, f"{key}: explanation of {sum(map(len, more))} chars"
        assert hint.strip() != title.strip(), key


def test_topics_make_unique_element_ids():
    # help.js turns every run of non-alphanumerics of the topic into "-": two keys must not end up with the same id
    slugs = [re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-") for key in HELP]
    assert len(set(slugs)) == len(slugs)
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    for name in ("help-block", "hint-row", "hint-text", "help-toggle", "help-more"):
        assert f".{name}" in css and name in read("help.js"), name
    assert "style=" not in css and "@import" not in css


def test_texts_are_plain_spanish_without_markup():
    for key, entry in HELP.items():
        for text in [entry["title"], entry["hint"], *entry["more"]]:
            assert not FORBIDDEN_IN_TEXT.search(text), (key, text)
    # accents and the Spanish quotes are in use: the texts are really Spanish, not placeholders
    joined = " ".join(entry["hint"] + " ".join(entry["more"]) for entry in HELP.values())
    assert "«" in joined and re.search(r"[áéíóúñ]", joined)


def test_each_execution_mode_has_a_hint_and_matches_the_form():
    form_modes = re.search(r"const MODES = \[(.*?)\];", read("create-run.js"))[1]
    modes = re.findall(r'"(\w+)"', form_modes)
    labels = re.search(r"export const MODE_LABELS = \{(.*?)\};", read("text.js"), re.S)[1]
    assert modes == ["auto", "local", "cloud", "human_guided"]
    assert re.findall(r"^\s*(\w+):", labels, re.M) == modes
    assert list(MODE_HINTS) == modes
    for mode, hint in MODE_HINTS.items():
        assert hint.strip() and len(hint) <= MAX_HINT and not FORBIDDEN_IN_TEXT.search(hint), mode
    # "create.mode" explains every mode, one sentence each
    assert len(HELP["create.mode"]["more"]) == len(modes)


# --- the component ------------------------------------------------------------------------------------------------

def test_help_component_is_accessible_and_plain_text():
    source = read("help.js")
    assert 'aria-expanded=${open ? "true" : "false"}' in source and "aria-controls=" in source
    assert '"aria-describedby"' in source                       # what helpFor gives the control it explains
    assert 'type="button"' in source and "<button" in source    # a real button: Enter and Space work by themselves
    assert "hidden=${!open}" in source                          # collapsed by default
    assert "useState(false)" in source
    assert "Escape" in source and "preventDefault" in source    # Esc closes the help (and not the session behind it)
    assert re.search(r"aria-label=\$\{[^}]*data\.title", source)
    for name in ("help.js", "help-text.js"):
        text = read(name)
        for bad in ("innerHTML", "dangerouslySetInnerHTML", "__html", "eval(", "style=", "document.write"):
            assert bad not in text, f"{name}: {bad}"


# --- every control of the views ------------------------------------------------------------------------------------

def opening_tags(source: str):
    """(tag name, text of its opening tag) of every <input>, <select>, <textarea>, <button> and <a class="btn">.

    The tag ends at the first '>' that is not inside a ${...} expression or a quoted attribute."""
    for match in re.finditer(r"<(input|select|textarea|button|a)\b", source):
        depth, quote, index = 0, "", match.end()
        while index < len(source):
            char = source[index]
            if depth:
                if quote:
                    if char == "\\":
                        index += 1
                    elif char == quote:
                        quote = ""
                elif char in "\"'`":
                    quote = char
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
            elif quote:
                quote = "" if char == quote else quote
            elif char == '"':
                quote = '"'
            elif char == "$" and source[index + 1:index + 2] == "{":
                depth, index = 1, index + 1
            elif char == ">":
                break
            index += 1
        text = source[match.start():index + 1]
        assert len(text) < 1500, f"runaway tag: {text[:80]!r}"
        name = match[1]
        if name in CONTROL_TAGS or (name == "a" and 'class="btn' in text):
            yield name, text


def controls():
    for name in VIEW_FILES:
        for tag, text in opening_tags(read(name)):
            yield name, tag, text


def test_every_control_points_at_its_help_or_says_why_it_has_none():
    found, exempt = Counter(), Counter()
    for file, tag, text in controls():
        found[file] += 1
        reason = re.search(r'data-help-exempt="([^"]+)"', text)
        if reason:
            assert reason[1] in ALLOWED_EXEMPT, (file, text)
            exempt[(file, reason[1])] += 1
            assert "helpFor(" not in text, f"{file}: exempt and described at once: {text[:80]}"
        else:
            assert "helpFor(" in text, f"{file}: control without help: {text[:100]}"
    assert exempt == EXPECTED_EXEMPT
    assert sum(found.values()) >= 45                      # the scanner really sees the controls of the views
    for file in ("create-run.js", "human-actions.js", "sessions.js", "session-detail.js"):
        assert found[file] >= 3, file
    assert found["run-detail.js"] >= 1 and found["open-in-app.js"] >= 3 and found["runs.js"] >= 2


def topics_in(source: str):
    """(topics given to helpFor, topics given to <Help>) of one module, as written in the code without spaces."""
    helped = [re.sub(r"\s+", "", t) for t in re.findall(r"helpFor\(\s*([^,)]+?(?:\s*\+\s*[\w.]+)?)\s*[,)]", source)]
    shown = [re.sub(r"\s+", "", f'"{a}"' if a else b)
             for a, b in re.findall(r'<\$\{Help\}[^>]*?\btopic=(?:"([^"]+)"|\$\{([^}]+)\})', source)]
    return helped, shown


def resolve(expression: str) -> set[str] | None:
    if expression.startswith('"') and expression.endswith('"') and "+" not in expression:
        return {expression.strip('"')}
    assert expression in DYNAMIC_TOPICS, f"unexpected topic expression {expression}; add it to DYNAMIC_TOPICS"
    return DYNAMIC_TOPICS[expression]


def test_every_topic_exists_and_has_a_hint_on_the_page():
    used = set()
    for name in VIEW_FILES:
        helped, shown = topics_in(read(name))
        for expression in helped:
            assert expression in shown, f"{name}: helpFor({expression}) points at a hint that this module never draws"
        for expression in helped + shown:
            keys = resolve(expression)
            for key in keys or ():
                assert key in HELP, f"{name}: no help text for {key}"
            used |= keys or set()
    assert not set(HELP) - used, f"help texts nobody uses: {sorted(set(HELP) - used)}"


def test_every_section_has_its_help():
    seen = set()
    for name in VIEW_FILES:
        source = read(name)
        for heading in re.finditer(r'<h[34] id="([\w-]+)"', source):
            ident = heading[1]
            if ident in DATA_HEADINGS:
                continue
            assert ident in SECTION_TOPICS, f"{name}: section {ident} has no help (add it to SECTION_TOPICS)"
            seen.add(ident)
            after = source[heading.end():heading.end() + 900]
            assert f'topic="{SECTION_TOPICS[ident]}"' in after, f"{name}: {ident} is not followed by its help"
    assert seen == set(SECTION_TOPICS)


def test_the_places_the_user_asked_about_are_covered():
    topics = set()
    for name in VIEW_FILES:
        topics |= set(topics_in(read(name))[1]) | set(topics_in(read(name))[0])
    topics = {t.strip('"') for t in topics}
    asked = [
        "create.bundle", "create.workflow", "create.project", "create.mode", "create.inputs.required", "preview.panel",
        "create.submit", "counter.model_calls", "counter.human_steps", "counter.steps", "counter.time", "run.skipped",
        "human.approve", "human.changes", "human.reject", "human.regenerate", "human.cancel", "human.submit",
        "human.response", "run.history", "sessions.source", "sessions.surface", "sessions.cwd", "sessions.since",
        "sessions.until", "sessions.text", "sessions.subagents", "sessions.more", "session.live", "session.pause",
        "session.reasoning", "sessions.empty", "sessions.roots", "open.copy",
    ]
    assert not [topic for topic in asked if topic not in topics]
    # one explanation per execution mode, and one per app that a session can be opened in
    mode_text = " ".join(HELP["create.mode"]["more"])
    for label in ("Automático", "Modelo local", "Modelo en la nube", "Guiado por una persona"):
        assert label in mode_text
    assert {"open.claude", "open.codex", "open.copilot"} <= set(HELP)


def test_views_keep_the_old_always_visible_help_paragraphs_out():
    # the long paragraphs moved behind the "?" button; the views keep only short status lines
    assert "Así quedará el prompt de cada paso" not in read("create-run.js")
    assert "Solo cuentan las llamadas que el framework hace" not in read("run-detail.js")


# --- the entries built from data (needs Node; the rest of the file does not) -------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_entries_built_from_data_follow_the_same_rules():
    script = """
      import { HELP, MODE_HINTS, fieldHelpEntry, modeHelpEntry } from %s;
      const base = { name: "audiencia", label: "Audiencia", used_in: ["a"], prompt_line: "x", skipped_only: false };
      const items = [
        { ...base, source: "declared", type: "text", required: true, example: "Soporte" },
        { ...base, source: "declared", type: "long_text", required: false, example: "Texto largo." },
        { ...base, source: "declared", type: "choice", required: false, example: "uno" },
        { ...base, source: "derived", type: "text", required: true, example: null },
        { ...base, source: "derived", type: "text", required: false, skipped_only: true },
      ];
      console.log(JSON.stringify({
        help: HELP, modeHints: MODE_HINTS,
        fields: items.map(fieldHelpEntry),
        modes: Object.keys(MODE_HINTS).map((mode) => modeHelpEntry(mode)),
      }));
    """ % json.dumps((JS / "help-text.js").as_uri())
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    out = json.loads(run.stdout)
    assert out["help"] == HELP and out["modeHints"] == MODE_HINTS      # the module says what the file text says
    entries = out["fields"] + out["modes"]
    for entry in entries:
        assert entry["title"].strip() and 0 < len(entry["hint"]) <= MAX_HINT, entry
        assert 2 <= len(entry["more"]) <= 4 and all(s.endswith(".") and len(s) <= MAX_SENTENCE for s in entry["more"]), entry
        assert not FORBIDDEN_IN_TEXT.search(" ".join([entry["title"], entry["hint"], *entry["more"]])), entry
    required, long_text, choice, derived, skipped = out["fields"]
    assert "obligatoria" in required["hint"].lower() and "Ejemplo: Soporte." in required["more"]
    assert "varias líneas" in long_text["more"][0] and "Ejemplo: Texto largo." in long_text["more"]   # no double period
    assert "opciones" in choice["more"][0] and not any(s.startswith("Ejemplo") for s in choice["more"])
    assert derived["more"][0] == "Admite texto libre." and not any(s.startswith("Ejemplo") for s in derived["more"])
    assert "no se ejecutan" in skipped["more"][1]
    assert [e["hint"] for e in out["modes"]] == list(MODE_HINTS.values())
    assert all(e["more"] == HELP["create.mode"]["more"] for e in out["modes"])
