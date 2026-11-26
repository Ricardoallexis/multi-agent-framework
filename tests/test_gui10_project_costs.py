# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PRY-T07: the route of the project costs and the models of the summary's cost figures and of the folded timeline."""
from __future__ import annotations

import json
import re

from multiagent.gui import STATIC_DIR
from test_gui_project import PROJECT_DIR, auth, env, make_project, needs_node, run_node  # noqa: F401  (env is a fixture)
from test_project_costs import INV, call, run_for
from usage_fixtures import priced

JS = STATIC_DIR / "js"


def preamble(language: str = "es") -> str:
    return f"""
      const ui = await import({json.dumps((JS / "i18n.js").as_uri())});
      await import({json.dumps((PROJECT_DIR / "i18n.js").as_uri())});
      ui.setLanguage({json.dumps(language)});
      const m = await import({json.dumps((PROJECT_DIR / "model.js").as_uri())});
    """


def stage(code: str, *, start=None, end=None, closed=None, label: str = "doing") -> dict:
    return {"group_id": "p-" + code, "code": code, "title": "Stage " + code, "label": {"key": label, "text": label, "tone": "neutral"},
            "planned_start": start, "planned_end": end, "closed_at": closed, "tasks": []}


# --- the route --------------------------------------------------------------------------------------------------------

def test_the_route_answers_the_sums_of_the_service(env):
    make_project(env, submitted=False)
    call(env, run_for(env, "snt", INV), 1000, 500, cost=priced(1000, 500))
    call(env, run_for(env, "snt"), 10, 5)
    answer = env.client.get("/gui/api/projects/snt/costs", headers=auth(env))
    assert answer.status_code == 200
    body = answer.json()
    assert body == env.services.usage.project_costs("snt")
    assert set(body) == {"project_id", "cost_is_estimate", "totals", "stages", "tasks", "activities", "unassigned"}
    assert body["totals"]["tokens_total"] == 1515 and body["unassigned"]["tokens_total"] == 15
    assert [row["group_id"] for row in body["stages"]] == ["snt-INV", "snt-CON"]
    assert body["tasks"][0]["task_id"] == INV


def test_the_route_errors(env):
    make_project(env, submitted=False)
    missing = env.client.get("/gui/api/projects/nope/costs", headers=auth(env))
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "not_found"
    assert env.client.get("/gui/api/projects/snt/costs").status_code == 401                         # no token
    assert env.client.get("/gui/api/projects/snt/costs", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert env.client.post("/gui/api/projects/snt/costs", headers=auth(env), json={}).status_code == 405    # read-only


def test_a_project_with_no_records_answers_with_no_numbers(env):
    make_project(env, submitted=False)
    body = env.client.get("/gui/api/projects/snt/costs", headers=auth(env)).json()
    assert body["totals"]["tokens_total"] is None and body["totals"]["cost_state"] == "none" and body["tasks"] == []


# --- the model of the cost figures ------------------------------------------------------------------------------------

@needs_node
def test_cost_summary_says_partial_and_no_price_never_zero():
    complete = {"records": 2, "tokens_total": 1500, "tokens_partial": False, "estimated_cost_usd": 0.0123, "cost_other_currencies": {},
                "cost_state": "complete"}
    partial = {**complete, "tokens_partial": True, "cost_state": "partial"}
    unpriced = {"records": 3, "tokens_total": 150, "tokens_partial": False, "estimated_cost_usd": None, "cost_other_currencies": {},
                "cost_state": "unknown"}
    euros = {**unpriced, "cost_other_currencies": {"EUR": 1.5}, "cost_state": "partial"}
    both = {**complete, "cost_other_currencies": {"EUR": 1.5}}
    nothing = {"records": 0, "tokens_total": None, "estimated_cost_usd": None, "cost_state": "none"}
    out = run_node(f"""{preamble()}
      const c = {json.dumps({"complete": complete, "partial": partial, "unpriced": unpriced, "euros": euros, "both": both, "nothing": nothing})};
      const all = Object.fromEntries(Object.entries(c).map(([k, v]) => [k, m.costSummary(v)]));
      ui.setLanguage("en"); const en = m.costSummary(c.unpriced); ui.setLanguage("es");
      console.log(JSON.stringify({{ all, empty: m.costSummary(null), bad: m.costSummary("x"), en,
        stages: [...m.stageCosts({{ stages: [{{ group_id: "a", records: 1 }}] }}).keys()], none: [...m.stageCosts(null).keys()] }}));
    """)
    done = out["all"]["complete"]
    assert done["tokens"] == "1,500" and "0.0123" in done["money"] and done["tokensNote"] == "" and done["moneyNote"] == ""
    assert out["all"]["partial"]["tokensNote"].startswith("Parcial") and out["all"]["partial"]["moneyNote"].startswith("Parcial")
    unpriced = out["all"]["unpriced"]
    assert unpriced["money"] == "Sin precio conocido" and unpriced["tokenCount"] == 150      # tokens still shown, no money
    assert out["en"]["money"] == "No known price"
    assert "EUR" in out["all"]["euros"]["money"] and "USD" not in out["all"]["euros"]["money"]    # not converted
    assert " · " in out["all"]["both"]["money"] and "USD" in out["all"]["both"]["money"] and "EUR" in out["all"]["both"]["money"]
    for key in ("nothing",):
        assert out["all"][key]["money"] == "Sin dato" and out["all"][key]["tokens"] == "Sin dato"
    assert out["empty"]["money"] == "Sin dato" and out["bad"]["tokens"] == "Sin dato"
    assert out["stages"] == ["a"] and out["none"] == []


# --- the model of the timeline ----------------------------------------------------------------------------------------

@needs_node
def test_timeline_with_planned_dates_places_steps_and_marks_the_late_stage():
    project = {"id": "p", "stages": [
        stage("A", start="2026-11-01", end="2026-11-11", closed="2026-11-10T15:00:00Z", label="closed"),
        stage("B", start="2026-11-11", end="2026-11-21", closed="2026-11-26T09:00:00Z", label="closed"),     # five days late
        stage("C", start="2026-11-21", end="2026-12-01"),                                                    # open
    ]}
    out = run_node(f"""{preamble()}
      const project = {json.dumps(project)};
      console.log(JSON.stringify({{ all: m.timelineModel(project), hidden: m.timelineModel(project, ["p-B"]), steps: m.TIMELINE_STEPS }}));
    """)
    view, rows = out["all"], {row["code"]: row for row in out["all"]["rows"]}
    assert view["hasPlan"] and view["hasReal"] and out["steps"] == 20
    assert view["from"] == "1 nov 2026" and view["to"] == "1 dic 2026"
    assert rows["A"]["mark"] == {"kind": "range", "at": 0, "width": 7} and rows["A"]["status"] == "ontime"
    assert rows["A"]["closedStep"] == 6
    assert rows["B"]["status"] == "late" and "5 días después" in rows["B"]["statusText"]
    assert rows["B"]["closedStep"] > rows["B"]["mark"]["at"] + rows["B"]["mark"]["width"]       # past its planned end
    assert rows["C"]["status"] == "none" and rows["C"]["closedStep"] is None and rows["C"]["mark"]["at"] + rows["C"]["mark"]["width"] == 20
    assert "Todavía no cerrada" in rows["C"]["realText"]
    for row in view["rows"]:                                                                      # every step is one the CSS draws
        steps = [row["closedStep"], row["mark"]["at"], row["mark"]["width"]]
        assert all(0 <= s <= 20 for s in steps if s is not None)
        assert row["text"].startswith(row["code"] + " · ") and row["text"].endswith(".")          # the sentence for a screen reader
    assert [row["code"] for row in out["hidden"]["rows"]] == ["A", "C"]                           # hidden stages leave the timeline


@needs_node
def test_timeline_without_planned_dates_shows_only_what_is_real():
    project = {"id": "p", "stages": [stage("A", closed="2026-11-10T15:00:00Z", label="closed"), stage("B"),
                                     stage("C", end="2026-11-30")]}
    out = run_node(f"""{preamble()}
      const project = {json.dumps(project)};
      console.log(JSON.stringify({{ some: m.timelineModel(project), real: m.timelineModel({{ stages: project.stages.slice(0, 2) }}),
        none: m.timelineModel({{ stages: [project.stages[1]] }}), empty: m.timelineModel(null) }}));
    """)
    real = {row["code"]: row for row in out["real"]["rows"]}
    assert not out["real"]["hasPlan"] and out["real"]["hasReal"]
    assert real["A"]["mark"] is None and real["A"]["closedStep"] == 0 and real["A"]["status"] == "none"   # nothing to be late against
    assert real["A"]["planText"] == "Sin fechas previstas" and "10 nov 2026" in real["A"]["realText"]
    assert real["B"]["mark"] is None and real["B"]["closedStep"] is None
    tick = {row["code"]: row for row in out["some"]["rows"]}["C"]
    assert tick["mark"] == {"kind": "tick", "at": 20, "width": 0} and "Fin previsto" in tick["planText"]    # a lone planned end is a tick
    assert out["none"]["rows"][0]["mark"] is None and out["none"]["from"] == "" and not out["none"]["hasReal"]
    assert out["empty"]["rows"] == [] and out["empty"]["from"] == ""


@needs_node
def test_timeline_texts_exist_in_english_and_spanish():
    project = {"id": "p", "stages": [stage("A", start="2026-11-01", end="2026-11-02", closed="2026-11-03", label="closed")]}
    out = run_node(f"""{preamble("en")}
      const project = {json.dumps(project)};
      const en = m.timelineModel(project).rows[0];
      ui.setLanguage("es");
      console.log(JSON.stringify({{ en, es: m.timelineModel(project).rows[0] }}));
    """)
    assert out["en"]["statusText"] == "Closed 1 day after its planned end" and out["en"]["planText"].startswith("Planned from")
    assert out["es"]["statusText"] == "Cerrada 1 día después de su fin previsto" and out["es"]["planText"].startswith("Previsto del")


# --- the page ---------------------------------------------------------------------------------------------------------

def test_the_page_is_folded_by_default_and_draws_positions_with_classes():
    parts = (PROJECT_DIR / "parts.js").read_text(encoding="utf-8")
    css = (PROJECT_DIR / "style.css").read_text(encoding="utf-8")
    assert "<details" in parts and not re.search(r"<details[^>]*\bopen\b", parts)                  # folded until the person opens it
    assert "style=" not in parts and "style=" not in (PROJECT_DIR / "index.js").read_text(encoding="utf-8")
    assert 'role="img"' in parts and "aria-label=${row.text}" in parts                            # the track has its sentence
    for step in range(21):
        assert f".project-tl-at{step} " in css and f".project-tl-w{step} " in css
    assert ".project-tl-late" in css and "forced-colors: active" in css
    assert "/costs" in (PROJECT_DIR / "index.js").read_text(encoding="utf-8")


def test_the_table_shows_both_notes_when_tokens_and_price_are_partial():
    parts = (PROJECT_DIR / "parts.js").read_text(encoding="utf-8")
    cell = parts[parts.index('class="project-cell-cost"'):]
    cell = cell[:cell.index("</td>")]
    assert "cost.tokensNote ?" in cell and "cost.moneyNote ?" in cell and "||" not in cell        # one note must not hide the other


@needs_node
def test_a_group_with_both_partial_signals_has_both_notes():
    group = {"records": 3, "tokens_total": 100, "tokens_partial": True, "estimated_cost_usd": 0.5, "cost_other_currencies": {},
             "cost_state": "partial"}
    out = run_node(f"""{preamble()}
      console.log(JSON.stringify(m.costSummary({json.dumps(group)})));
    """)
    assert out["tokensNote"] and out["moneyNote"] and out["tokensNote"] != out["moneyNote"]
