# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""AVZ-T03: the summary of the project view (figures, bar by pieces, table of stages) and the stages that can be hidden."""
from __future__ import annotations

import json
import re

import pytest

from multiagent.gui import STATIC_DIR
from test_gui_project import PROJECT_DIR, auth, env, make_project, needs_node, run_node  # noqa: F401  (env is a fixture)

JS = STATIC_DIR / "js"


def preamble(language: str = "es") -> str:
    return f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      await import({json.dumps((PROJECT_DIR / "i18n.js").as_uri())});
      ui.setLanguage({json.dumps(language)});
      const m = await import({json.dumps((PROJECT_DIR / "model.js").as_uri())});
    """


def label(key: str, tone: str = "neutral") -> dict:
    return {"key": key, "text": key.title(), "tone": tone}


def task(identifier: str, key: str, group: str) -> dict:
    return {"id": identifier, "group_id": group, "title": identifier, "label": label(key)}


def stage(code: str, keys: list[str], stage_key: str = "doing") -> dict:
    group = "p-" + code
    tasks = [task(f"{group}-T{i:02d}", key, group) for i, key in enumerate(keys, 1)]
    done = keys.count("done")
    return {"group_id": group, "code": code, "title": "Stage " + code, "label": label(stage_key), "tasks": tasks,
            "progress": {"done": done, "cancelled": keys.count("cancelled"), "total": len(keys), "percent": None},
            "progress_weighted": {"percent": 50, "counted": len(keys)}, "planned_start": None, "planned_end": None,
            "last_change": {"seq": 3, "created_at": "2026-11-20T10:00:00Z"}}


def project(stages: list[dict], **extra) -> dict:
    keys = [t["label"]["key"] for s in stages for t in s["tasks"]]
    base = {"id": "p", "stages": stages, "counts": {k: keys.count(k) for k in set(keys)},
            "progress": {"done": keys.count("done"), "cancelled": keys.count("cancelled"), "total": len(keys), "percent": None},
            "progress_weighted": {"percent": 40, "counted": len(keys) - keys.count("cancelled")},
            "remaining": {"tasks": 5, "layers": 3}, "attention": {}}
    return {**base, **extra}


@needs_node
def test_figures_and_pieces_come_from_the_answer(env):
    make_project(env)
    answer = env.client.get("/gui/api/projects/snt", headers=auth(env)).json()
    out = run_node(f"""{preamble()}
      const answer = {json.dumps(answer)};
      console.log(JSON.stringify({{ figures: m.overviewFigures(answer), pieces: m.projectSegments(answer),
        rows: answer.stages.map((s) => m.stageRow(s)), empty: m.overviewFigures(null) }}));
    """)
    figures = out["figures"]
    assert figures["percent"] == answer["progress_weighted"]["percent"]
    assert (figures["leftTasks"], figures["leftSteps"]) == (answer["remaining"]["tasks"], answer["remaining"]["layers"])
    assert figures["stages"] == len(answer["stages"]) and figures["done"] == answer["progress"]["done"]
    assert figures["counted"] == answer["progress"]["total"] - answer["progress"]["cancelled"]
    pieces = out["pieces"]
    total = sum(len(s["tasks"]) for s in answer["stages"])
    assert pieces["unit"] == "tasks" and len(pieces["segments"]) == total == pieces["total"]
    assert [p["id"] for p in pieces["segments"]] == [t["id"] for s in answer["stages"] for t in s["tasks"]]
    assert all(p["state"] and p["tone"] for p in pieces["segments"])
    assert re.search(r"\d+ de \d+ tareas hechas", pieces["summary"])                 # the sentence of the screen reader
    assert [r["id"] for r in out["rows"]] == [s["group_id"] for s in answer["stages"]]
    # What the core did not give is «sin dato», never a made-up number.
    assert out["empty"]["percent"] is None and out["empty"]["leftTasks"] is None and out["empty"]["stages"] == 0


@needs_node
def test_figures_without_the_new_fields_show_no_data():
    plain = project([stage("A", ["done", "todo"])])
    del plain["progress_weighted"], plain["remaining"]
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify({{ f: m.overviewFigures({json.dumps(plain)}) }}));
    """)
    assert out["f"]["percent"] is None and out["f"]["leftTasks"] is None and out["f"]["leftSteps"] is None
    assert out["f"]["done"] == 1 and out["f"]["counted"] == 2


@needs_node
def test_the_bar_has_one_piece_per_task_and_leaves_the_cancelled_aside():
    data = project([stage("A", ["done", "done", "review", "blocked", "cancelled"]), stage("B", ["todo"], "todo")])
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify(m.projectSegments({json.dumps(data)})));
    """)
    assert out["unit"] == "tasks" and out["total"] == 5 and out["done"] == 2
    assert [p["state"] for p in out["segments"]] == ["done", "done", "review", "blocked", "todo"]
    assert out["summary"].startswith("2 de 5 tareas hechas") and "1 Review" in out["summary"] and "1 Blocked" in out["summary"]


@needs_node
def test_more_than_sixty_tasks_group_the_bar_by_stage():
    big = project([stage("A", ["done"] * 40, "closed"), stage("B", ["doing"] * 21, "doing"), stage("C", ["todo"] * 3, "todo")])
    small = project([stage("A", ["done"] * 40, "closed"), stage("B", ["doing"] * 20, "doing")])       # exactly 60: one piece per task
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify({{ big: m.projectSegments({json.dumps(big)}), small: m.projectSegments({json.dumps(small)}),
        limit: m.SEGMENT_LIMIT }}));
    """)
    assert out["limit"] == 60
    assert out["big"]["unit"] == "stages" and [p["id"] for p in out["big"]["segments"]] == ["p-A", "p-B", "p-C"]
    assert out["big"]["done"] == 1 and out["big"]["summary"].startswith("1 de 3 etapas cerradas")
    assert out["small"]["unit"] == "tasks" and len(out["small"]["segments"]) == 60


@needs_node
def test_a_long_stage_keeps_its_small_bar_within_the_limit_and_every_state():
    long_stage = stage("A", ["done"] * 90 + ["review"] * 5 + ["todo"] * 5)
    out = run_node(f"""{preamble()}
      const row = m.stageRow({json.dumps(long_stage)});
      console.log(JSON.stringify({{ n: row.segments.length, states: [...new Set(row.segments.map((p) => p.state))],
        ids: new Set(row.segments.map((p) => p.id)).size, done: row.done, counted: row.counted }}));
    """)
    assert out["n"] <= 60 and out["ids"] == out["n"]
    assert set(out["states"]) == {"done", "review", "todo"}          # a rare state keeps at least one piece
    assert (out["done"], out["counted"]) == (90, 100)


@needs_node
def test_each_attention_row_starts_with_its_verb_and_a_hidden_stage_does_not_hide_it():
    a = stage("A", ["review", "done"])
    b = stage("B", ["blocked", "ready"], "doing")
    c = stage("C", ["done"], "verify")
    data = project([a, b, c], attention={
        "reviews": [a["tasks"][0]], "verifications": [{k: v for k, v in c.items() if k != "tasks"}],
        "mine": [b["tasks"][1]], "blocked": [{**b["tasks"][0], "blockers": []}]})
    out = run_node(f"""{preamble()}
      const data = {json.dumps(data)};
      const rows = (hidden) => m.attentionRows(data, hidden).map((r) => [r.kind, r.verb, r.id, r.stageId, r.hidden]);
      console.log(JSON.stringify({{ shown: rows([]), hidden: rows(["p-A", "p-B", "p-C"]), none: m.attentionRows({{}}, []) }}));
    """)
    assert [row[:2] for row in out["shown"]] == [["review", "Revisar"], ["verify", "Verificar"], ["mine", "Responder"], ["blocked", "Desbloquear"]]
    assert [row[4] for row in out["shown"]] == [False] * 4
    # Hiding every stage leaves the same four rows; they only say that their stage is hidden.
    assert [row[:4] for row in out["hidden"]] == [row[:4] for row in out["shown"]]
    assert [row[4] for row in out["hidden"]] == [True] * 4
    assert out["none"] == []


@needs_node
def test_hiding_never_changes_a_figure_and_only_closed_stages_are_hidden_with_one_tap():
    stages = [stage("A", ["done"], "closed"), stage("B", ["doing", "todo"], "doing"), stage("C", ["done"], "verify"),
              stage("D", ["done", "done"], "closed")]
    data = project(stages)
    out = run_node(f"""{preamble()}
      const data = {json.dumps(data)};
      const before = JSON.stringify([m.overviewFigures(data), m.projectSegments(data)]);
      const hidden = m.hideStage(data, [], "p-B");
      const ids = (list) => list.map((s) => s.group_id);
      console.log(JSON.stringify({{
        sameFigures: before === JSON.stringify([m.overviewFigures(data), m.projectSegments(data)]),
        one: hidden, visible: ids(m.visibleStages(data, hidden)),
        finished: m.hideFinished(data, []), withOne: m.hideFinished(data, ["p-B"]), canHide: m.hasFinished(data, []),
        allClosedHidden: m.hasFinished(data, ["p-A", "p-D"]), stale: m.hiddenIn(data, ["p-B", "gone"]),
        unknown: m.hideStage(data, ["p-B"], "nope"), showAll: m.visibleStages(data, []).length,
      }}));
    """)
    assert out["sameFigures"]
    assert out["one"] == ["p-B"] and out["visible"] == ["p-A", "p-C", "p-D"]
    assert out["finished"] == ["p-A", "p-D"]                         # «verify» is not closed: it stays
    assert sorted(out["withOne"]) == ["p-A", "p-B", "p-D"]
    assert out["canHide"] is True and out["allClosedHidden"] is False
    assert out["stale"] == ["p-B"] and out["unknown"] == ["p-B"] and out["showAll"] == 4


@needs_node
def test_hidden_stages_are_stored_per_project_and_a_failing_storage_is_no_error():
    script = f"""
      const store = {{}};
      globalThis.window = {{ localStorage: {{ getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => {{ store[k] = String(v); }} }} }};
      const s = await import({json.dumps((JS / "shell-state.js").as_uri())});
      const empty = s.readHiddenStages("p");
      s.writeHiddenStages("p", ["a", "b", "a", 7]);
      s.writeHiddenStages("q", ["z"]);
      const saved = [s.readHiddenStages("p"), s.readHiddenStages("q")];
      s.writeHiddenStages("p", []);
      const cleared = [s.readHiddenStages("p"), s.readHiddenStages("q"), Object.keys(JSON.parse(Object.values(store)[0]))];
      store[Object.keys(store)[0]] = "{{not json";
      const garbage = s.readHiddenStages("q");
      store[Object.keys(store)[0]] = JSON.stringify({{ q: "text", r: ["x", 1] }});
      const odd = [s.readHiddenStages("q"), s.readHiddenStages("r")];
      globalThis.window = {{ localStorage: {{ getItem() {{ throw new Error("denied"); }}, setItem() {{ throw new Error("denied"); }} }} }};
      let failed = null;
      try {{ s.writeHiddenStages("p", ["a"]); }} catch (err) {{ failed = String(err); }}
      const blocked = s.readHiddenStages("p");
      delete globalThis.window;
      const noWindow = s.readHiddenStages("p");
      console.log(JSON.stringify({{ empty, saved, cleared, garbage, odd, failed, blocked, noWindow }}));
    """
    out = run_node(script)
    assert out["empty"] == [] and out["saved"] == [["a", "b"], ["z"]]
    assert out["cleared"] == [[], ["z"], ["q"]]                     # an empty choice leaves no entry for that project
    assert out["garbage"] == [] and out["odd"] == [[], ["x"]]
    assert out["failed"] is None and out["blocked"] == [] and out["noWindow"] == []


@needs_node
def test_segment_bar_counts_states_and_gives_a_default_sentence():
    out = run_node(f"""
      const bar = await import({json.dumps((JS / "segment-bar.js").as_uri())});
      const parts = [{{ id: 1, state: "done", text: "Done" }}, {{ id: 2, state: "done", text: "Done" }}, {{ id: 3, state: "blocked", text: "Blocked" }}];
      console.log(JSON.stringify({{ counts: bar.segmentCounts(parts), text: bar.defaultSummary(parts), none: bar.segmentCounts(null) }}));
    """)
    assert out["counts"] == [{"state": "done", "text": "Done", "count": 2}, {"state": "blocked", "text": "Blocked", "count": 1}]
    assert out["text"] == "2 Done, 1 Blocked" and out["none"] == []


def test_segment_bar_is_one_image_with_equal_pieces_and_no_inline_style():
    source = (JS / "segment-bar.js").read_text(encoding="utf-8")
    code = re.sub(r"^\s*//.*$", "", source, flags=re.M)
    assert "style=" not in code and "innerHTML" not in code and "localStorage" not in code
    assert 'role="img"' in code and "aria-label" in code and "data-state" in code and "tone-" in code
    assert code.count("aria-hidden") == 1                            # the pieces; the bar itself is the one thing read out
    css = (PROJECT_DIR / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.project-segbar > \[data-state\]\s*\{[^}]*flex:\s*1 1 0", css)          # every piece the same width
    assert "style=" not in (PROJECT_DIR / "parts.js").read_text(encoding="utf-8")


def test_the_new_texts_exist_in_both_languages_and_the_page_uses_them():
    i18n = (PROJECT_DIR / "i18n.js").read_text(encoding="utf-8")
    en, es = i18n.split("const es = {", 1)
    sources = "".join((PROJECT_DIR / name).read_text(encoding="utf-8") for name in ("parts.js", "model.js"))
    used = {key for key in re.findall(r'"(ext\.project\.(?:verb|figures|segments|table)\.[\w.]+)"', sources)}
    used |= {key for key in re.findall(r'"(ext\.project\.stages\.(?:hide|hide_stage|hidden_count|show|hide_done|all_hidden))"', sources)}
    used |= {"ext.project.attention.stage_hidden"}
    used |= {f"ext.project.verb.{kind}" for kind in ("review", "verify", "mine", "blocked")}     # built from the kind of the row
    assert len(used) >= 30
    for key in sorted(used):
        assert f'"{key}"' in en and f'"{key}"' in es, key
    for topic in ("stage_table", "stage_hide", "stages_show", "stages_hide_done"):
        assert f'"help.project.{topic}"' in es, topic


def test_the_overview_keeps_what_was_there_and_the_table_shows_in_both_modes():
    parts = (PROJECT_DIR / "parts.js").read_text(encoding="utf-8")
    overview = parts[parts.index("export function Overview"):parts.index("function PhaseBlock")]
    for kept in ("project.progress", "project.attention", "project.working_now", "project.milestones", "<${Progress}", "project-counts"):
        assert kept in overview, kept
    assert 'useMode() === "advanced"' in overview and "<${StageTable}" in overview
    table = parts[parts.index("function StageTable"):parts.index("export function Overview")]
    assert table.count("advanced ?") == 2                            # the technical columns, in the head and in each row
    assert "ext.project.table.stage" in table and "ext.project.table.state" in table and "SegmentBar" in table
    index = (PROJECT_DIR / "index.js").read_text(encoding="utf-8")
    assert all(tab in index for tab in ('id: "overview"', 'id: "stages"', 'id: "list"')) and "ext.project.refresh" in index
    stages = parts[parts.index("export function StagesPanel"):parts.index("export function ListPanel")]
    assert "<${HiddenBar}" in stages and "<${HideStage}" in stages and "visibleStages(" in stages


def test_left_note_is_empty_when_nothing_is_left():
    parts = (PROJECT_DIR / "parts.js").read_text(encoding="utf-8")
    assert "figures.leftSteps === null || figures.leftTasks === 0" in parts


def test_segment_bar_has_a_forced_colors_rule():
    css = (PROJECT_DIR / "style.css").read_text(encoding="utf-8")
    block = css[css.index("@media (forced-colors: active)"):]
    assert ".project-segbar > [data-state]" in block and "Highlight" in block and "Canvas;" in block
    assert "!important" not in block
