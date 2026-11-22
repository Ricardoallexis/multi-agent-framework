# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI contextual help (GUI-T12): every control and section of the views has a short hint and a "?" explanation.

Static checks over the JavaScript (read as text, like the other GUI view tests): the help texts are short, plain and
complete; every interactive control of the views points at its help (aria-describedby through ``helpFor``) or says
why it has none (``data-help-exempt``); and every topic a view uses exists. A small Node check (skipped when Node is
not installed) runs the entries that are built from data.

The views that tasks add live in ``static/extensions/<name>/`` (FLU-T04, ``multiagent/gui/extensions.py``). Their files are
found by that convention and held to the same rules by ``check_extensions``: no list in this file changes when a task adds a
view, and tests/test_gui_extensions.py runs the same function over two example views."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter

import pytest

from multiagent.gui import STATIC_DIR
from multiagent.gui.extensions import HELP_FILE, extension_dirs

JS = STATIC_DIR / "js"
NODE = shutil.which("node")

# The modules that draw the screens: every control they render must have help.
VIEW_FILES = [
    "main.js", "runs.js", "create-run.js", "run-detail.js", "human-actions.js", "usage.js", "evaluation.js", "teams.js",
    "plans.js", "sessions.js", "session-detail.js", "session-items.js", "open-in-app.js", "subagents-panel.js", "agent-tree.js",
]
# The app shell (GUI2-T02: top bar, sidebar, right panel) draws controls too, under the same rules.
VIEW_FILES += ["shell.js", "sidebar.js", "right-panel.js", "panel-slots.js", "subagents-mount.js"]
# The shared components with controls (GUI2-T11): StatusChip, Progress and LevelBadge draw no control; Tabs draws the tab buttons.
VIEW_FILES += ["ui/tabs.js", "work-map.js"]
# The right-panel sections «Cambios», «Tokens» and «Fuentes» (GUI2-T05).
VIEW_FILES += ["panel-parts.js", "panel-changes.js", "panel-tokens.js", "panel-sources.js"]
CONTROL_TAGS = {"input", "select", "textarea", "button", "summary"}
VIEW_FILES += ["external-output.js"]   # HIL2-T02: «Salida externa» of the run detail
VIEW_FILES += ["new-session.js"]       # the guided chat of «Nueva sesión» (GUI2-T03); new-session-model.js draws nothing
VIEW_FILES += ["thread.js", "thread-cards.js"]  # CHT-T02: the run conversation and its decision cards
VIEW_FILES += ["pending.js"]           # RWD-T03: the «Pendientes» inbox of the top bar
VIEW_FILES += ["team-thread.js", "team-card.js"]   # CHT-T05: the chat thread of a team version and its card
VIEW_FILES += ["command-palette.js", "customize.js"]   # GUI3-T07: Ctrl+K and «Personalizar»
VIEW_FILES += ["plan-thread.js"]   # CHT-T06: the chat thread of a plan proposal, its decision and its messages
VIEW_FILES += ["document-card.js"]   # ARR3-T06: the «Documento» card of a completed run
VIEW_FILES += ["packages.js"]      # PAQ-T02: the «Paquetes» view; packages-model.js draws nothing
VIEW_FILES += ["plan-draft.js"]   # ARR3-T02: the plan draft card of «Nueva sesión»

# The only controls without help, and why. A new exemption must be added here on purpose.
ALLOWED_EXEMPT = {
    "navigation": "the section tabs: their label is the section name, and each section has its own help",
    "data-row": "a row of the list of runs: it is data, not a control that does something",
    "confirm": "the two buttons of a confirmation box, which already explains what is going to happen",
    "component": "the tab buttons of the shared Tabs component: each view passes its help as aria-describedby (helpFor) "
                 "and tests/test_gui2_components.py checks that every use of Tabs does",
}
EXPECTED_EXEMPT = Counter({
    ("sidebar.js", "navigation"): 3,
    ("runs.js", "data-row"): 1,
    ("human-actions.js", "confirm"): 2,
    ("open-in-app.js", "confirm"): 2,
    ("ui/tabs.js", "component"): 1,
})

# Topics built from data: the expression the view passes, and the HELP keys it can resolve to (None: the entry is
# built by fieldHelpEntry from the workflow input, so there is no key).
DYNAMIC_TOPICS = {
    '"field."+item.name': None,
    '"open."+summary.source': {"open.claude", "open.codex", "open.copilot"},
    "entry.topic": None,    # right-panel.js: the help key an extension gave to registerSlot (extensions are checked by check_extensions)
}

# Sections (headings with an id) and the topic that explains each; run and session titles are data, not sections.
SECTION_TOPICS = {
    "work-map-title": "work_map.refresh",
    "sessions-title": "sessions.list", "items-title": "session.items",
    "roots-title": "sessions.roots", "preview-title": "preview.panel", "counters-title": "run.counters",
    "steps-title": "run.steps", "history-title": "run.history", "results-title": "run.results",
    "inputs-title": "run.inputs", "human-actions-title": "human.section", "external-output-title": "external.section",
    "panel-changes-title": "panel.changes", "panel-subagents-title": "subagents.panel",
    "panel-tokens-title": "panel.tokens", "panel-sources-title": "panel.sources",
    "agent-tree-title": "agent_tree.view", "at-log-title": "agent_tree.log", "usage-title": "usage.section",
    "usage-plans-title": "usage.plans", "usage-global-title": "usage.global", "usage-costs-title": "usage.costs",
    "evaluation-title": "evaluation.section", "teams-title": "teams.section", "plans-title": "plans.section",
}
DATA_HEADINGS = {"run-title", "session-title", "at-detail-title"}   # the last one is the name of the chosen agent

MAX_TITLE, MAX_HINT, MAX_SENTENCE, MAX_MORE = 60, 110, 220, 700
FORBIDDEN_IN_TEXT = re.compile(r"[<>`]|\$\{|https?:|javascript:|\b(?:the|and|click|please|payload|endpoint)\b", re.I)


def read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def json_block(name: str, source: str | None = None) -> dict:
    """``export const NAME = { ... };`` of help-text.js (or of ``source``): strict JSON, readable without the browser."""
    match = re.search(rf"^export const {name} = (\{{.*?^\}});$", read("help-text.js") if source is None else source, re.S | re.M)
    assert match, f"{name} is not a plain JSON object ending in a line that is just '}};'"
    return json.loads(match[1])


HELP = json_block("HELP")
MODE_HINTS = json_block("MODE_HINTS")


# --- the texts ----------------------------------------------------------------------------------------------------

def test_every_entry_has_a_title_a_hint_and_two_to_four_short_sentences():
    assert len(HELP) >= 60
    check_help_entries(HELP)


def check_help_entries(entries: dict) -> None:
    for key, entry in entries.items():
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
            # GUI2-T09 added summaries for source notes and price details; keep this new disclosure surface held to
            # the same help rule without changing the existing views' long-standing summary exception.
            if tag == "summary" and name != "usage.js":
                continue
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
    helped = [re.sub(r"\s+", "", t).replace("'", '"') for t in re.findall(r"helpFor\(\s*([^,)]+?(?:\s*\+\s*[\w.]+)?)\s*[,)]", source)]
    shown = [re.sub(r"\s+", "", f'"{a or b}"' if a or b else dynamic)
             for a, b, dynamic in re.findall(r'''<\$\{Help\}[^>]*?\btopic=(?:"([^"]+)"|'([^']+)'|\$\{([^}]+)\})''', source)]
    return helped, shown


def resolve(expression: str) -> set[str] | None:
    if expression.startswith('"') and expression.endswith('"') and "+" not in expression:
        return {expression.strip('"')}
    assert expression in DYNAMIC_TOPICS, f"unexpected topic expression {expression}; add it to DYNAMIC_TOPICS"
    return DYNAMIC_TOPICS[expression]


def test_topics_in_accepts_single_quoted_help_for_topic():
    helped, shown = topics_in("helpFor('sample.action'); <${Help} topic='sample.action' />")
    assert helped == shown == ['"sample.action"']
    assert resolve(helped[0]) == {"sample.action"}


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


def test_human_prompt_notes_and_preflight_have_separate_help():
    source = read("human-actions.js")
    for control, topic in (("human-prompt-used", "human.prompt_used"),
                           ("human-notes", "human.notes"), ("Validar sin enviar", "human.check")):
        assert control in source
        assert f'helpFor("{topic}")' in source
        assert f'<${{Help}} topic="{topic}"' in source
        assert HELP[topic]["hint"]


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


# --- the views that tasks add: static/extensions/<name>/ (FLU-T04) ------------------------------------------------------
# Found by the folder convention, not by a list here: a new view is one more folder and this file stays as it is.

# A heading that shows data (the name of a project, say) is not a section: it says so, and why, on the heading itself.
ALLOWED_HEADING_EXEMPT = {"data-title": "the heading is data (a name the user or a source gave), not a section"}


def extension_help(static_dir=STATIC_DIR) -> dict:
    """The help entries of every ``extensions/<name>/help.js``, all keys under ``<name>.`` so two views never share one."""
    merged: dict = {}
    for folder in extension_dirs(static_dir):
        path = folder / HELP_FILE
        if not path.is_file():
            continue
        entries = json_block("HELP", path.read_text(encoding="utf-8"))
        for key in entries:
            assert key.startswith(folder.name + "."), f"{folder.name}/{HELP_FILE}: key {key} must start with '{folder.name}.'"
        assert not set(entries) & set(HELP), f"{folder.name}: help keys of help-text.js reused: {sorted(set(entries) & set(HELP))}"
        merged.update(entries)
    return merged


def check_extensions(static_dir=STATIC_DIR) -> dict:
    """Hold every extension folder to the rules the built-in views follow; returns what it saw (for the tests of the check).

    Help texts short and plain, a help or an exemption on every control, every topic defined and used, every section with its
    help, ids that cannot meet another view's, and an entry module that registers something."""
    known = {**HELP, **extension_help(static_dir)}
    seen = {"controls": 0, "sections": 0, "extensions": 0}
    for folder in extension_dirs(static_dir):
        seen["extensions"] += 1
        prefix = folder.name.replace("_", "-") + "-"
        files = sorted(folder.rglob("*.js"))
        sources = {path: path.read_text(encoding="utf-8") for path in files}
        entry = re.sub(r"^\s*//.*$", "", sources[folder / "index.js"], flags=re.M)      # a comment registers nothing
        assert re.search(r"\bregister(?:View|Slot)\(", entry), f"{folder.name}/index.js registers no view and no slot"
        owner = re.escape(folder.name)
        owner_field = rf'\bowner\s*:\s*["\']{owner}["\']'
        for kind in ("View", "Slot", "UsageSection"):
            for call in re.finditer(rf'\bregister{kind}\s*\(([^;]+)\)', entry):
                assert re.search(owner_field, call[1]), f"{folder.name}/index.js: register{kind} must pass owner {folder.name}"
        for call in re.finditer(r'\bregisterHelp\s*\(([^;]+)\)', entry):
            assert re.search(rf',\s*["\']{owner}["\']\s*$', call[1]), \
                f"{folder.name}/index.js: registerHelp must pass owner {folder.name}"
        own_help = folder / HELP_FILE
        assert own_help.is_file() == bool(re.search(r"\bregisterHelp\(", entry)), \
            f"{folder.name}: help.js and registerHelp() go together (one without the other)"
        own = json_block("HELP", own_help.read_text(encoding="utf-8")) if own_help.is_file() else {}
        check_help_entries(own)
        used: set[str] = set()
        for path, source in sources.items():
            label = f"{folder.name}/{path.relative_to(folder).as_posix()}"
            for tag, text in opening_tags(source):
                if tag == "summary":
                    continue            # the same exception as the built-in views
                seen["controls"] += 1
                reason = re.search(r'data-help-exempt="([^"]+)"', text)
                if reason:
                    assert reason[1] in ALLOWED_EXEMPT, f"{label}: {text[:80]}"
                    assert "helpFor(" not in text, f"{label}: exempt and described at once: {text[:80]}"
                else:
                    assert "helpFor(" in text, f"{label}: control without help: {text[:100]}"
            helped, shown = topics_in(source)
            for expression in helped:
                assert expression in shown, f"{label}: helpFor({expression}) points at a hint this module never draws"
            for expression in helped + shown:
                assert expression.startswith('"') and expression.endswith('"') and "+" not in expression, \
                    f"{label}: topics in extensions are plain strings, got {expression}"
                assert expression.strip('"') in known, f"{label}: no help text for {expression.strip(chr(34))}"
                used.add(expression.strip('"'))
            for slot_topic in re.findall(r'\btopic:\s*"([^"]+)"', source):     # registerSlot(name, Component, { topic: "..." })
                assert slot_topic in known, f"{label}: no help text for {slot_topic}"
                used.add(slot_topic)
            for heading in re.finditer(r'<h[34]\b([^>]*?)\bid="([\w-]+)"([^>]*)>', source):
                seen["sections"] += 1
                ident, attributes = heading[2], heading[1] + heading[3]
                assert ident.startswith(prefix), f"{label}: heading id {ident} must start with '{prefix}' (ids are shared by the page)"
                exempt = re.search(r'data-help-exempt="([^"]+)"', attributes)
                if exempt:
                    assert exempt[1] in ALLOWED_HEADING_EXEMPT, f"{label}: {ident}"
                    continue
                after = source[heading.end():heading.end() + 900]
                topic = re.search(r'''<\$\{Help\}[^>]*?\btopic=["']([^"']+)["']''', after)
                # the help of a heading comes before the next control or heading, so it is the heading's own and not a later one's
                assert topic and topic[1] in known and not re.search(r"<(?:button|input|select|textarea|h[34])\b", after[:topic.start()]), \
                    f"{label}: heading {ident} is not followed by its help"
                used.add(topic[1])
        assert not set(own) - used, f"{folder.name}: help texts nobody uses: {sorted(set(own) - used)}"
    return seen


def test_extension_folders_follow_the_help_rules():
    check_extensions()


def test_apps_certificate_help_keys_exist_and_are_used():
    """ARR3-T07: «Probar conexión» and the certificate button point at their help."""
    apps = STATIC_DIR / "extensions" / "apps"
    help_source = (apps / HELP_FILE).read_text(encoding="utf-8")
    view_source = (apps / "index.js").read_text(encoding="utf-8")
    for key in ("apps.tls_fix", "apps.probe"):
        assert f'"{key}": {{' in help_source
        assert f'helpFor("{key}"' in view_source and f'topic="{key}"' in view_source


def test_packages_help_keys_exist_and_are_used():
    """PAQ-T02: the five keys of «Paquetes» exist and the view uses each one as a hint and on a control."""
    source = read("packages.js")
    for key in ("packages.install_zip", "packages.example", "packages.trust_functions", "packages.remove", "packages.degraded"):
        assert key in HELP, key
        assert f'helpFor("{key}"' in source and f'topic="{key}"' in source, key
