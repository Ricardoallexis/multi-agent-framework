# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI2-T09: synthetic usage snapshots render known, reset and unavailable values honestly."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("usage-static")
    shutil.copytree(JS, root / "js")
    shutil.copytree(STATIC_DIR / "i18n", root / "i18n")
    shutil.copytree(STATIC_DIR / "vendor", root / "vendor")
    (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    return root


def run_node(root, body):
    script = root / "usage-view.mjs"
    script.write_text(body, encoding="utf-8")
    result = subprocess.run([NODE, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


WALKER = r"""
const expand = (v) => {
  if (v == null || typeof v === "boolean" || typeof v === "string" || typeof v === "number") return v;
  if (Array.isArray(v)) return v.map(expand);
  if (v.type?.name === "Help") return { type: "help-placeholder", props: { topic: v.props?.topic, scope: v.props?.scope } };
  if (typeof v.type === "function") return expand(v.type(v.props || {}));
  return { type: v.type, props: { ...v.props, children: expand(v.props?.children) } };
};
const text = (v) => v == null || typeof v === "boolean" ? "" : Array.isArray(v) ? v.map(text).join("")
  : typeof v === "object" ? text(v.props?.children) : String(v);
const all = (v) => v == null ? [] : Array.isArray(v) ? v.flatMap(all) : typeof v === "object" ? [v, ...all(v.props?.children)] : [];
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_plans_never_invent_a_percent_for_missing_snapshot_or_reset_and_keep_copilot_credits(node_root):
    out = run_node(node_root, WALKER + r"""
      import { PlanCard } from "./js/usage.js";
      const draw = (app) => {
        const view = expand(PlanCard({ app }));
        const bars = all(view).filter((n) => n.props?.role === "progressbar");
        return { text: text(view), bars: bars.map((bar) => ({ now: bar.props["aria-valuenow"] ?? null,
            valueText: bar.props["aria-valuetext"], style: "style" in bar.props })),
            summaries: all(view).filter((n) => n.type === "summary").map((n) => n.props["aria-describedby"]),
            help: all(view).filter((n) => n.type === "help-placeholder").map((n) => n.props.topic) };
      };
      console.log(JSON.stringify({
        claude: draw({ app: "claude", status: "partial", plan: null, source: null, credits: null, notes: ["statusline_snapshot_missing"],
          windows: [{ window: "five_hour", used_percent: 40, state: "limit_reached", resets_at: "2026-10-11T14:30:00Z" }] }),
        reset: draw({ app: "codex", status: "partial", windows: [{ window: "five_hour", used_percent: 60,
          observed_used_percent: 60, state: "window_reset", resets_at: "2026-10-11T14:00:00Z" }] }),
        reached: draw({ app: "codex", status: "ok", windows: [{ window: "weekly", used_percent: 100,
          state: "limit_reached", resets_at: "2026-10-12T00:00:00Z" }] }),
        codexCredits: draw({ app: "codex", status: "ok", credits: { used: 5, cap: 20 }, windows: [] }),
        copilot: draw({ app: "copilot", status: "partial", credits: { used: 5, cap: null }, windows: [{ window: "monthly",
          used_percent: 80, elapsed_percent: 50, state: "limit_reached", resets_at: "2026-11-01T00:00:00Z" }] }),
        copilotUnknown: draw({ app: "copilot", status: "partial", credits: { used: null, cap: 20 }, windows: [{ window: "monthly",
          used_percent: null, elapsed_percent: 25, state: "current", resets_at: "2026-11-01T00:00:00Z" }] }),
        pace: draw({ app: "codex", status: "ok", windows: [{ window: "weekly", used_percent: 20,
          elapsed_percent: 40, state: "current", resets_at: "2026-10-18T12:00:00Z" }] }),
      }));
    """)
    assert "Se alcanzó el límite" in out["claude"]["text"] and "No hay una medición reciente de Claude" in out["claude"]["text"]
    assert "Datos parciales" in out["claude"]["text"] and "Terminada" not in out["reached"]["text"]
    assert out["claude"]["summaries"] == ["help-usage-plan-notes-claude-hint"]
    assert "usage.plan_notes" in out["claude"]["help"]
    assert out["claude"]["bars"] == [] and "porcentaje no disponible" in out["claude"]["text"]
    assert "60" not in out["reset"]["text"] and out["reset"]["bars"] == []
    assert "100 %" in out["reached"]["text"] and out["reached"]["bars"][0]["now"] == 100
    assert "Créditos de Copilot" not in out["codexCredits"]["text"]
    assert "5 créditos" in out["copilot"]["text"] and "sin tope configurado" in out["copilot"]["text"]
    assert "porcentaje no disponible" in out["copilot"]["text"] and out["copilot"]["bars"] == []
    assert "80" not in out["copilot"]["text"] and "Ritmo estimado" not in out["copilot"]["text"]
    assert "consumo no disponible" in out["copilotUnknown"]["text"] and "de 20 créditos" in out["copilotUnknown"]["text"]
    assert "de 0 créditos" not in out["copilotUnknown"]["text"] and " (0 %)" not in out["copilotUnknown"]["text"]
    assert "Ritmo estimado" not in out["copilotUnknown"]["text"]
    assert "Ritmo estimado al reinicio: 50 %" in out["pace"]["text"]
    assert all(not bar["style"] for sample in out.values() for bar in sample["bars"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_tokens_and_costs_show_unknown_values_and_render_untrusted_names_as_text(node_root):
    out = run_node(node_root, WALKER + r"""
      import { TokenRows, CostGroup } from "./js/usage.js";
      const tokens = expand(TokenRows({ label: "week", result: {
        window: { since: "2026-10-04T12:00:00Z", until: "2026-10-11T12:00:00Z" },
        totals: { total: 12, events: 2 }, apps: { claude: { status: "ok", events: 2, warnings: [] } },
        rows: [{ app: "claude", model: "<img src=x onerror=alert(1)>", level: "T2", role: "delegated", total: 12, events: 2 }],
      } }));
      const unknownTokens = expand(TokenRows({ label: "today", result: { totals: { events: 2 }, rows: [] } }));
      const cost = expand(CostGroup({ title: "Total", item: { estimated_cost_usd: null, cost_known_records: 0,
        cost_unknown_records: 2, cost_unknown_reasons: { no_price_evidence: 1, tokens_not_reported: 1 },
        evidence: [{ model: "test-model", currency: "USD", source: "catalog", provenance: "fixture" }] } }));
      const knownCost = expand(CostGroup({ title: "Known", item: { estimated_cost_usd: 1.25, cost_known_records: 1,
        cost_unknown_records: 0, evidence: [] } }));
      console.log(JSON.stringify({ tokenText: text(tokens), tokenElements: all(tokens).map((n) => String(n.type)),
        costText: text(cost), costHasStyle: all(cost).some((n) => "style" in n.props),
        unknownTokenText: text(unknownTokens), knownCostText: text(knownCost),
        summaries: all(cost).filter((n) => n.type === "summary").map((n) => n.props["aria-describedby"]) }));
    """)
    assert "Total de tokens: 12 tokens" in out["tokenText"] and "Delegado" in out["tokenText"] and "T2" in out["tokenText"]
    assert "Total de tokens: no disponible" in out["unknownTokenText"]
    assert "<img src=x onerror=alert(1)>" in out["tokenText"]
    assert "img" not in out["tokenElements"] and "Sin importe conocido" in out["costText"]
    assert "estimado" not in out["costText"] and "estimado" in out["knownCostText"]
    assert "2 sin importe conocido" in out["costText"] and "no_price_evidence" in out["costText"]
    assert out["summaries"] == ["help-usage-cost-reasons-total-hint", "help-usage-cost-evidence-total-hint"]
    assert out["costHasStyle"] is False


def test_usage_view_uses_all_three_gui_routes_and_contains_no_inline_style_or_html_injection():
    source = ui_text((JS / "usage.js"))
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    for path in ("/gui/api/usage/plans", "/gui/api/usage/tokens?", "/gui/api/usage/costs"):
        assert path in source
    for forbidden in ("innerHTML", "dangerouslySetInnerHTML", "__html", "style=", "document.write"):
        assert forbidden not in source
    assert "helpFor(\"usage.view\")" in source and "helpFor(\"usage.period\")" in source
    assert 'class="usage-refresh-control" role="group" aria-label="Actualizar datos de uso"' in source
    assert '<${Help} topic="usage.refresh" compact=${true} />' in source
    assert ".usage-refresh-control" in css and ".usage-refresh-control > .btn" in css
    assert "window_reset" in source and "limit_reached" in source and "credits.cap == null" in source
    assert "cost_unknown_records" in source and "evidence" in source


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_usage_is_a_supported_hash_route(node_root):
    out = run_node(node_root, r"""
      import { parseRoute, routeHref } from "./js/route.js";
      console.log(JSON.stringify({ route: parseRoute("#usage"), href: routeHref("usage"), invalid: parseRoute("#other") }));
    """)
    assert out == {"route": {"section": "usage", "id": None}, "href": "#usage", "invalid": {"section": "sessions", "id": None}}
