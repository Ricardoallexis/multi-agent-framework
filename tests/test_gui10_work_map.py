from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
MODEL = (JS / "work-map-model.js").as_uri()
NODE = shutil.which("node")

DATA = """
const data = {
  projects: [
    { id: "p1", title: "Alpha", archived: false, stages: [
      { group_id: "s1", title: "Build", tasks: [
        { id: "t1", title: "One", status: "doing", label: { key: "doing" }, depends_on: [] },
        { id: "t2", title: "Two", status: "review", label: { key: "review" }, depends_on: ["t1"] },
        { id: "t3", title: "Three", status: "blocked", label: { key: "blocked" }, depends_on: ["t1"] },
        { id: "t4", title: "Four", status: "done", label: { key: "done" }, depends_on: [] },
        { id: "t9", title: "Nine", status: "cancelled", label: { key: "cancelled" }, depends_on: [] } ] },
      { group_id: "s2", title: "Ship", tasks: [
        { id: "t5", title: "Five", status: "ready", label: { key: "ready" }, depends_on: ["t2"] } ] } ],
      attention: { reviews: [{ id: "t2", status: "review" }], mine: [], blocked: [{ id: "t3", status: "blocked" }], verifications: [] } },
    { id: "p2", title: "Beta", archived: false, stages: [
      { group_id: "s3", title: "Build", tasks: [
        { id: "t6", title: "Six", status: "ready", label: { key: "ready" }, depends_on: [] },
        { id: "t7", title: "Seven", status: "completed", label: { key: "completed" }, depends_on: [] } ] } ] },
    { id: "p3", title: "Old", archived: true, stages: [] } ],
  sessions: {
    "claude:a": { parent_id: null, source: "claude", status: "active", tokens: { input: 100, output: 20 }, children: [] },
    "codex:b": { parent_id: null, source: "codex", status: "active", tokens: { input_tokens: 5, output_tokens: 5 }, children: [] },
    "codex:c": { parent_id: "codex:b", source: "codex", status: "idle", tokens: null, children: [] },
    "claude:free": { parent_id: null, source: "claude", status: "idle", tokens: null, children: [] } },
  links: [ { task_id: "t1", session_id: "claude:a" }, { task_id: "t6", session_id: "codex:b" } ],
};
"""


def run_node(body: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    script = f"""
      import assert from "node:assert/strict";
      import * as m from {json.dumps(MODEL)};
      {DATA}
      {body}
    """
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_metrics_count_tasks_projects_and_tokens_from_the_loaded_rows():
    out = run_node("""
      const x = m.mapMetrics(data);
      const none = m.mapMetrics({ projects: [], sessions: { s: { tokens: null } } });
      console.log(JSON.stringify({ x, none, absent: m.mapMetrics(null) }));
    """)
    # t9 is cancelled (not counted); t3 is blocked both ways but counted once; two projects are active, one archived
    assert out["x"] == {"projects": 2, "doing": 1, "review": 1, "blocked": 1, "done": 2, "total": 7, "tokens": 130}
    assert out["none"]["tokens"] is None and out["none"]["total"] == 0   # a figure that is not known is not invented
    assert out["absent"] is None


def test_each_filter_and_their_combination_change_rows_and_figures_together():
    out = run_node("""
      const ids = (d) => d.projects.flatMap((p) => p.stages.flatMap((s) => s.tasks.map((t) => t.id)));
      const by = (f) => { const d = m.applyMapFilters(data, f); return { ids: ids(d), sessions: Object.keys(d.sessions).sort(), metrics: m.mapMetrics(d) }; };
      console.log(JSON.stringify({
        none: m.applyMapFilters(data, {}) === data,
        project: by({ project: "p2" }),
        stage: by({ stage: "s2" }),
        app: by({ app: "codex" }),
        projectStage: by({ project: "p1", stage: "s1" }),
        projectApp: by({ project: "p1", app: "codex" }),
        stageApp: by({ stage: "s3", app: "codex" }),
        leftApp: by({ left: true, app: "claude" }),
      }));
    """)
    assert out["none"] is True
    assert out["project"]["ids"] == ["t6", "t7"] and out["project"]["sessions"] == ["codex:b", "codex:c"]
    assert out["project"]["metrics"]["total"] == 2 and out["project"]["metrics"]["projects"] == 1
    assert out["project"]["metrics"]["tokens"] == 10
    assert out["stage"]["ids"] == ["t5"] and out["stage"]["sessions"] == []
    assert out["app"]["ids"] == ["t6"] and out["app"]["sessions"] == ["codex:b", "codex:c"]
    assert out["projectStage"]["ids"] == ["t1", "t2", "t3", "t4", "t9"]
    assert out["projectStage"]["sessions"] == ["claude:a"]
    assert out["projectApp"]["ids"] == []   # nothing of project p1 was done by codex
    # the codex sessions belong to project p2: they must not leak into p1, nor their tokens into the figures
    assert out["projectApp"]["sessions"] == [] and out["projectApp"]["metrics"]["tokens"] is None
    assert out["stageApp"]["ids"] == ["t6"] and out["stageApp"]["sessions"] == ["codex:b", "codex:c"]
    assert out["stageApp"]["metrics"]["tokens"] == 10
    assert out["leftApp"]["ids"] == ["t1"] and out["leftApp"]["sessions"] == ["claude:a"]
    assert out["leftApp"]["metrics"]["tokens"] == 120


def test_filter_options_and_stale_values():
    out = run_node("""
      data.sessions["claude:a"].children = [{ id: "c", source: "copilot", status: "running" }];
      const o = m.mapFilterOptions(data);
      console.log(JSON.stringify({
        o,
        stale: m.sanitizeFilters({ project: "gone", stage: "s9", app: "vim", left: true }, o),
        scopedStage: m.sanitizeFilters({ project: "p2", stage: "s1" }, o),
        keep: m.sanitizeFilters({ project: "p1", stage: "s1", app: "codex" }, o),
      }));
    """)
    assert [p["value"] for p in out["o"]["projects"]] == ["p1", "p2", "p3"]
    assert out["o"]["apps"] == ["claude", "codex"]   # a subagent's app alone is not offered: the filter could not find it
    assert {s["value"] for s in out["o"]["stages"]} == {"s1", "s2", "s3"}
    assert out["stale"] == {"project": "", "stage": "", "app": "", "left": True}
    assert out["scopedStage"]["stage"] == ""
    assert out["keep"] == {"project": "p1", "stage": "s1", "app": "codex", "left": False}


def test_what_is_left_drops_finished_tasks_and_orders_by_what_they_hold_up():
    out = run_node("""
      const left = m.applyMapFilters(data, { left: true });
      const flat = left.projects.flatMap((p) => p.stages.flatMap((s) => s.tasks.map((t) => ({
        kind: "task", id: "task:" + t.id, status: t.status, dependsOn: t.depends_on, blockers: [] }))));
      // the same task twice (stage and attention group) is listed once
      const ordered = m.orderRemaining([...flat, flat[0]]);
      console.log(JSON.stringify({ ids: ordered.map((n) => n.id), holds: ordered.map((n) => n.blocks),
        stages: left.projects.map((p) => p.stages.map((s) => s.group_id)) }));
    """)
    # t1 holds up t2 and t3, t2 holds up t5, the rest hold nothing and keep the map's order; t4, t7 (done) and t9 are out
    assert out["ids"] == ["task:t1", "task:t2", "task:t3", "task:t5", "task:t6"]
    assert out["holds"] == [2, 1, 0, 0, 0]
    assert out["stages"] == [["s1", "s2"], ["s3"]]


def test_blockers_count_as_holding_up_and_a_pair_counts_once():
    out = run_node("""
      const nodes = [
        { kind: "task", id: "task:a", status: "ready", dependsOn: [], blockers: [] },
        { kind: "task", id: "task:b", status: "blocked", dependsOn: ["a"], blockers: [{ task_id: "a", state: "doing" }] },
        { kind: "task", id: "task:c", status: "blocked", dependsOn: [], blockers: [{ task_id: "b" }] }];
      console.log(JSON.stringify(m.orderRemaining(nodes).map((n) => [n.id, n.blocks])));
    """)
    assert out == [["task:a", 1], ["task:b", 1], ["task:c", 0]]


def test_address_keeps_the_filters_and_rejects_garbage():
    out = run_node("""
      const f = { project: "p 1&x", stage: "s/2", app: "Codex", left: true };
      const hash = m.mapAddress(f);
      console.log(JSON.stringify({
        hash, back: m.parseMapAddress(hash), empty: m.mapAddress({}), plain: m.parseMapAddress("#map"),
        other: m.parseMapAddress("#sessions/x"), bad: m.parseMapAddress("#map/%E0%A4%A"),
        long: m.parseMapAddress("#map/" + encodeURIComponent("project=" + "x".repeat(500))).project.length,
        same: m.sameFilters(f, m.parseMapAddress(hash)),
      }));
    """)
    assert out["hash"].startswith("#map/") and "&" not in out["hash"][5:] and "/" not in out["hash"][5:]
    assert out["back"] == {"project": "p 1&x", "stage": "s/2", "app": "codex", "left": True}
    assert out["empty"] == "#map" and out["plain"] == {"project": "", "stage": "", "app": "", "left": False}
    assert out["other"]["project"] == "" and out["bad"]["left"] is False
    assert out["long"] == 200 and out["same"] is True


def test_layout_opens_as_a_list_and_remembers_the_choice_without_failing_on_blocked_storage():
    if NODE is None:
        pytest.skip("node is not installed")
    shell = (JS / "shell-state.js").as_uri()
    script = f"""
      const store = new Map();
      globalThis.window = {{ localStorage: {{ getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) }} }};
      const s = await import({json.dumps(shell)});
      const m = await import({json.dumps(MODEL)});
      const first = s.readMapLayout();
      s.writeMapLayout("tree");
      const remembered = s.readMapLayout();
      const key = [...store.keys()][0];
      store.set(key, "nonsense");
      const junk = s.readMapLayout();
      globalThis.window = {{ get localStorage() {{ throw new Error("blocked"); }} }};
      const blocked = s.readMapLayout();
      s.writeMapLayout("list");
      console.log(JSON.stringify({{ first, remembered, junk, blocked, key,
        open: m.initialLayout(first, false), tree: m.initialLayout("tree", false), narrow: m.initialLayout("tree", true),
        list: m.initialLayout("list", false) }}));
    """
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["first"] is None and out["remembered"] == "tree" and out["junk"] is None and out["blocked"] is None
    assert out["open"] == "list" and out["tree"] == "tree" and out["narrow"] == "list" and out["list"] == "list"
    assert out["key"].startswith("multiagent.gui.")
    # localStorage lives only in shell-state.js (tests/test_gui2_layout.py)
    assert "localStorage" not in (JS / "work-map-model.js").read_text(encoding="utf-8")
    assert "localStorage" not in (JS / "work-map.js").read_text(encoding="utf-8")


def test_what_is_left_shows_failed_and_stopped_tasks_even_with_only_active_on():
    module = (JS / "work-map.js").as_uri()
    out = run_node(f"""
      const {{ projectRows }} = await import({json.dumps(module)});
      const d = {{ projects: [{{ id: "p", title: "P", stages: [{{ group_id: "s", title: "S", tasks: [
        {{ id: "f", title: "Failed", status: "failed", label: {{ key: "failed" }} }},
        {{ id: "x", title: "Stopped", status: "stopped", label: {{ key: "stopped" }} }},
        {{ id: "d", title: "Done", status: "done", label: {{ key: "done" }} }}] }}] }}], sessions: {{}}, links: [] }};
      const ids = (tree) => tree.flatMap((n) => [n.id, ...ids(n.children || [])]).filter((id) => id.startsWith("task:"));
      const left = m.applyMapFilters(d, {{ left: true }});
      console.log(JSON.stringify({{
        plain: ids(projectRows(d, true, "").tree), left: ids(projectRows(left, false, "").tree),
        figures: m.mapMetrics(left).total }}));
    """)
    assert out["plain"] == []                       # "only active" hides finished-looking tasks, as before
    assert out["left"] == ["task:f", "task:x"] and out["figures"] == 2   # the list and the figures agree
    view = (JS / "work-map.js").read_text(encoding="utf-8")
    assert "projectRows(scoped, onlyActive && !active.left" in view


def test_view_wiring_keeps_the_tree_and_its_controls_and_adds_metrics_and_filters():
    view = (JS / "work-map.js").read_text(encoding="utf-8")
    assert 'from "./work-map-model.js"' in view and "initialLayout(readMapLayout()" in view
    assert 'class="wm-metrics"' in view and 'id="wm-project"' in view and 'id="wm-stage"' in view and 'id="wm-app"' in view
    assert "sanitizeFilters({ ...active, [key]: value }, options)" in view   # no stale stage in the address
    assert "location.hash = mapAddress(next)" in view and 'addEventListener("hashchange"' in view
    # nothing is removed: the tree, both layout buttons, both checkboxes and the screen reader announcement stay
    assert 'setLayout("tree")' in view and 'setLayout("list")' in view and "<${Node}" in view
    assert 'type="checkbox"' in view and view.count('type="checkbox"') == 2
    assert 'role="status" aria-live="polite"' in view and "mapCountAnnouncement(" in view
    assert 'apiGet("/gui/api/map?only=all", token)' in view   # the filters are not sent to the server
    for source in ("work-map.js", "work-map-model.js"):
        assert "style=" not in (JS / source).read_text(encoding="utf-8"), source
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    for selector in (".wm-metrics", ".wm-metric", ".wm-filters", ".wm-left"):
        assert selector in css, selector
    assert "style=" not in css


def test_new_texts_exist_in_both_languages_and_the_new_controls_have_help():
    if NODE is None:
        pytest.skip("node is not installed")
    keys = sorted(set(re.findall(r'"(work_map\.(?:metrics?\.|filters?\.|blocks_)[a-z_.]*)"',
                                 (JS / "work-map.js").read_text(encoding="utf-8"))))
    assert "work_map.metric.tokens" in keys and "work_map.filter.left" in keys and "work_map.blocks_count" in keys
    en = (STATIC_DIR / "i18n" / "en" / "work-map.js").as_uri()
    es = (STATIC_DIR / "i18n" / "es" / "work-map.js").as_uri()
    es_help = (STATIC_DIR / "i18n" / "es" / "teams.js").as_uri()
    script = f"""
      import en from {json.dumps(en)}; import es from {json.dumps(es)}; import esHelp from {json.dumps(es_help)};
      console.log(JSON.stringify({{ en: Object.keys(en), es: Object.keys(es), esHelp: Object.keys(esHelp), enValues: en, esValues: es }}));
    """
    run = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stderr
    dicts = json.loads(run.stdout)
    for key in keys:
        assert key in dicts["en"] and key in dicts["es"], key
    assert dicts["esValues"]["work_map.filter.left"] == "Qué falta"
    help_keys = {"work_map.filter_project", "work_map.filter_stage", "work_map.filter_app", "work_map.filter_left"}
    help_text = (JS / "help-text-teams.js").read_text(encoding="utf-8")
    view = (JS / "work-map.js").read_text(encoding="utf-8")
    for topic in help_keys:
        assert f'"{topic}"' in help_text and f'helpFor("{topic}")' in view and f"help.{topic}" in dicts["esHelp"], topic
