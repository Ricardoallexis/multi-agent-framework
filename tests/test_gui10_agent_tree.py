# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""ARB-T01: header figures and per-level bars of the agent tree. The pure model runs under Node when it is installed;
the rest are static checks of the view, the dictionaries and the stylesheet. Synthetic data only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
MODEL = json.dumps((JS / "agent-tree-model.js").as_uri())
I18N = STATIC_DIR / "i18n"


I18N_JS = json.dumps((JS / "i18n.js").as_uri())


def run_node(script: str, language: str = "en"):
    # imports are hoisted, so the language is set after them, before the script body runs
    script = f'import {{ setLanguage }} from {I18N_JS}; setLanguage("{language}"); ' + script
    run = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


KIDS = """
  const kid = (id, level, status, tokens) => ({ id, source: "claude", kind: "subagent", name: id, label: id, model: "m", level,
    status, note: null, started_at: null, last_activity_at: null, tokens, link_id: null });
  const full = [
    kid("a", "T1", "running", { input: 100, output: 100, cache_read: 300 }),
    kid("b", "T1", "done", { input: 200, output: 100, cache_read: 100 }),
    kid("c", "T2", "running", { input: 300, output: 100, cache_read: 0 }),
    kid("d", "T3", "done", { total: 400 }),
  ];
  const summary = { tokens: { input: 600, output: 400, cache_read: 500 } };
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_header_figures_with_every_token_figure_known():
    out = run_node(f"""
      import {{ headerStats, headerTexts }} from {MODEL};
      {KIDS}
      const stats = headerStats(full, summary);
      console.log(JSON.stringify({{ stats, texts: headerTexts(stats) }}));
    """)
    # 2 of 4 active; tokens 1000 (orchestrator) + 200 + 300 + 400 + 400 = 2300; cache 900 / (input 1200 + 900) = 43 %
    assert out["stats"]["active"] == 2 and out["stats"]["total"] == 4
    assert out["stats"]["tokens"] == {"total": 2300, "partial": False}
    # the worker that gives only a total has no cache data, so the share is marked partial
    assert out["stats"]["cache"] == {"percent": 43, "partial": True}
    assert out["texts"]["active"] == "2 of 4 active"
    assert "partial" not in out["texts"]["tokens"] and "partial" in out["texts"]["cache"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_partial_tokens_are_marked_and_never_an_exact_total():
    out = run_node(f"""
      import {{ headerStats, headerTexts }} from {MODEL};
      {KIDS}
      const gaps = [...full, kid("e", "T2", "running", null)];
      const stats = headerStats(gaps, summary);
      const none = headerStats([kid("x", "T1", "running", null)], null);
      console.log(JSON.stringify({{ stats, text: headerTexts(stats).tokens, none, noneText: headerTexts(none) }}));
    """)
    assert out["stats"]["tokens"] == {"total": 2300, "partial": True}
    assert "partial" in out["text"]
    assert out["none"]["tokens"] == {"total": None, "partial": False}
    assert out["noneText"]["tokens"] == "no data" and out["noneText"]["cache"] == "no data"   # never an invented 0


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_no_cache_read_anywhere_says_no_data():
    out = run_node(f"""
      import {{ headerStats, headerTexts }} from {MODEL};
      {KIDS}
      const bare = [kid("a", "T1", "running", {{ input: 10, output: 5, cache_read: null }}), kid("b", "T2", "done", {{ input: 1, output: 1 }})];
      const stats = headerStats(bare, {{ tokens: {{ input: 4, output: 4, cache_read: null }} }});
      console.log(JSON.stringify({{ stats, texts: headerTexts(stats) }}));
    """)
    assert out["stats"]["cache"] == {"percent": None, "partial": False}
    assert out["texts"]["cache"] == "no data"
    assert out["stats"]["tokens"] == {"total": 25, "partial": False}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_level_shares_add_up_to_100_with_steps_of_five():
    out = run_node(f"""
      import {{ levelShares, sharesOf, barStep }} from {MODEL};
      {KIDS}
      const shares = levelShares(full);
      const thirds = levelShares([kid("a", "T1", "running", {{ total: 1 }}), kid("b", "T2", "running", {{ total: 1 }}), kid("c", "T3", "running", {{ total: 1 }})]);
      const gap = levelShares([kid("a", "T1", "running", {{ total: 10 }}), kid("b", "T1", "running", null), kid("c", "unmapped", "running", {{ total: 10 }})]);
      const empty = levelShares([kid("a", "T1", "running", null)]);
      console.log(JSON.stringify({{ shares, thirds, gap, empty, none: levelShares([]), zero: sharesOf([0, 0]),
        steps: [0, 2, 3, 47, 98, 100, null].map(barStep) }}));
    """)
    shares = out["shares"]
    assert [s["level"] for s in shares] == ["T1", "T2", "T3"]
    assert [s["percent"] for s in shares] == [38, 31, 31]     # 500, 400, 400 of the workers' 1300 tokens
    assert sum(s["percent"] for s in out["thirds"]) == 100
    assert [(s["level"], s["partial"]) for s in out["gap"]] == [("T1", True), ("unknown", False)]
    assert out["empty"][0]["percent"] is None and out["empty"][0]["partial"] is True
    assert out["none"] == [] and out["zero"] == [None, None]
    assert out["steps"] == [0, 0, 5, 45, 100, 100, 0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_run_becomes_a_tree_of_its_steps():
    out = run_node(f"""
      import {{ runStepChildren, headerStats, groupByLevel, bucketOf, describeNode }} from {MODEL};
      const run = {{ id: "r1", steps: [
        {{ id: "draft", index: 0, agent: "writer", state: "done", attempts: 1, executor: {{ kind: "model", provider: "p", model: "gpt-x" }} }},
        {{ id: "check", index: 1, function: "validate", state: "running", attempts: 1, executor: null }},
        {{ id: "approve", index: 2, agent: "person", state: "waiting_human", attempts: 1, executor: null }},
        {{ id: "extra", index: 3, agent: "writer", state: "skipped", attempts: 0, executor: null }},
        {{ id: "later", index: 4, agent: "writer", state: "pending", attempts: 0, executor: null }},
      ] }};
      const kids = runStepChildren(run);
      const grouped = groupByLevel(kids);
      console.log(JSON.stringify({{ ids: kids.map((k) => k.id), status: kids.map((k) => k.status), buckets: kids.map(bucketOf),
        model: kids[0].model, label: kids[1].label, tokens: kids.map((k) => k.tokens), stats: headerStats(kids, null),
        groups: Object.keys(grouped).filter((k) => grouped[k].length), node: describeNode(kids[0]).title,
        none: [runStepChildren(null), runStepChildren({{}})] }}));
    """)
    assert out["ids"] == ["step:draft", "step:check", "step:approve", "step:later"]   # the skipped step did not run
    assert out["status"] == ["done", "running", "idle", "idle"]
    assert out["buckets"] == ["done", "active", "waiting", "active"]
    assert out["model"] == "gpt-x" and out["label"] == "validate"
    assert out["tokens"] == [None] * 4 and out["stats"]["tokens"]["total"] is None   # no tokens per step: unknown, never 0
    assert out["groups"] == ["unknown"] and out["node"] == "draft"
    assert out["none"] == [[], []]


def test_view_uses_the_figures_the_bars_and_the_run_fallback_without_inline_styles():
    view = (JS / "agent-tree.js").read_text(encoding="utf-8")
    model = (JS / "agent-tree-model.js").read_text(encoding="utf-8")
    sheet = (STATIC_DIR / "agent-tree.css").read_text(encoding="utf-8")
    for source in (view, model):
        assert "style=" not in source and "innerHTML" not in source
    assert "headerStats(children, summary)" in view and "<${LevelBar}" in view and "runStepChildren(run)" in view
    assert '"/gui/api/runs/"' in view                                     # the run the panel already loads: no new route
    assert "filterChildren(children, onlyActive)" in view and 'aria-live="polite"' in view   # unchanged
    assert 'routeHref("sessions", (summary && summary.parent_id) || sessionId)' in view and "agent_tree.back_orchestrator" in view
    for step in range(0, 101, 5):
        assert f".at-w-{step} {{ width: {step}%; }}" in sheet


def test_new_texts_exist_in_english_and_spanish_and_old_spanish_is_untouched():
    def keys(language: str) -> dict[str, str]:
        text = (I18N / language / "agent-tree.js").read_text(encoding="utf-8")
        return dict(re.findall(r'^  "([^"]+)": "(.*)",$', text, flags=re.M))

    en, es = keys("en"), keys("es")
    new = [key for key in en if key.startswith(("agent_tree.stats.", "agent_tree.bar.")) or key == "agent_tree.back_orchestrator"]
    assert len(new) >= 14 and set(new) <= set(es)
    assert "agent_tree.back" in es and es["agent_tree.back_orchestrator"] == "Volver al orquestador"   # the old key stays
    source = (JS / "agent-tree-model.js").read_text(encoding="utf-8") + (JS / "agent-tree.js").read_text(encoding="utf-8")
    assert set(re.findall(r'"(agent_tree\.(?:stats|bar)\.[a-z_]+)"', source)) <= set(en)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_level_without_token_data_has_no_bar_even_when_others_report():
    out = run_node(f"""
      import {{ levelShares }} from {MODEL};
      {KIDS}
      const shares = levelShares([kid("a", "T1", "running", {{ total: 100 }}), kid("b", "T2", "running", null), kid("c", "T2", "done", null)]);
      console.log(JSON.stringify(shares));
    """)
    assert [(s["level"], s["percent"], s["step"], s["partial"]) for s in out] == [("T1", 100, 100, False), ("T2", None, 0, True)]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_framework_session_tokens_are_not_counted_twice():
    out = run_node(f"""
      import {{ headerStats }} from {MODEL};
      {KIDS}
      const step = (id, tokens) => ({{ ...kid(id, null, "done", tokens), source: "framework", kind: "step" }});
      const steps = [step("s1", {{ input: 100, output: 50 }}), step("s2", {{ input: 200, output: 50 }})];
      const run = {{ source: "framework", tokens: {{ input: 300, output: 100 }} }};
      console.log(JSON.stringify({{ withSteps: headerStats(steps, run), noSteps: headerStats([], run),
        other: headerStats([kid("a", "T1", "done", {{ total: 10 }})], {{ source: "claude", tokens: {{ total: 20 }} }}) }}));
    """)
    assert out["withSteps"]["tokens"] == {"total": 400, "partial": False}     # the steps only: 150 + 250, not 400 + 400
    assert out["noSteps"]["tokens"] == {"total": 400, "partial": False}       # the run's own total when there are no steps
    assert out["other"]["tokens"]["total"] == 30                             # other sources: orchestrator + workers


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_cache_share_follows_each_apps_input_convention():
    out = run_node(f"""
      import {{ headerStats }} from {MODEL};
      {KIDS}
      const mk = (source, tokens) => ({{ ...kid("x", "T1", "done", tokens), source }});
      const share = (source, tokens) => headerStats([mk(source, tokens)], null).cache.percent;
      console.log(JSON.stringify({{
        claude: share("claude", {{ input: 100, output: 1, cache_read: 300 }}),          // 300 / (100 + 300)
        antigravity: share("antigravity", {{ input: 100, output: 1, cache_read: 100 }}), // 100 / 200
        codex: share("codex", {{ input: 400, output: 1, cache_read: 300 }}),            // 300 / 400
        codexBounded: share("codex", {{ input: 100, output: 1, cache_read: 300 }}),     // never above 100 %
        framework: share("framework", {{ input: 200, output: 1, cache_read: 50 }}),     // 50 / 200
      }}));
    """)
    assert out == {"claude": 75, "antigravity": 50, "codex": 75, "codexBounded": 100, "framework": 25}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_header_texts_in_spanish():
    out = run_node(f"""
      import {{ headerStats, headerTexts }} from {MODEL};
      {KIDS}
      const partial = headerTexts(headerStats([...full, kid("e", "T2", "running", null)], summary));
      const none = headerTexts(headerStats([kid("x", "T1", "running", null)], null));
      console.log(JSON.stringify({{ partial, none }}));
    """, language="es")
    assert out["partial"]["active"] == "3 de 5 activos" and out["partial"]["tokens"].endswith("parcial")
    assert out["none"]["tokens"] == "sin dato" and out["none"]["cache"] == "sin dato"
