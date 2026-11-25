# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""VOF-T03: office routes, scoped workflows, privacy choices and plan fallback in «Nueva sesión»."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from tests.gui_text import ui_text
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from multiagent.services import ApplicationServices

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def file_url(path: Path) -> str:
    return json.dumps(path.as_uri())


def run_node(script: str):
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@needs_node
def test_parse_office_route():
    out = run_node(f"""
      import {{ officeHref, parseRoute }} from {file_url(JS / "route.js")};
      console.log(JSON.stringify({{
        office: parseRoute("#new/office/cafe"),
        area: parseRoute("#new/office/cafe/menu"),
        href: officeHref("cafe", "menu"),
      }}));
    """)
    assert out == {
        "office": {"section": "new", "id": None, "kind": "office", "office": "cafe", "area": None},
        "area": {"section": "new", "id": None, "kind": "office", "office": "cafe", "area": "menu"},
        "href": "#new/office/cafe/menu",
    }


def test_office_actions_link_to_scoped_routes():
    source = (STATIC_DIR / "extensions" / "offices" / "index.js").read_text(encoding="utf-8")
    assert 'href=${officeHref(officeId, area)}' in source
    assert "<${OfficeAskButton} officeId=${office.id} />" in source
    assert "<${OfficeAskButton} officeId=${office.id} area=${row.id} />" in source
    assert "<OfficeAskButton" not in source      # a bare tag is an empty HTML element in htm, not the component


def test_a_late_reply_never_lands_on_another_office():
    # Copilot's review of VOF-T03: a reply that came back after the route changed set the cloud question on the new
    # screen, and «Enviar» there posted confirm_cloud: true with the other office. No render harness here, so the
    # wiring is checked: each handler keeps its route key and checks it after every await.
    source = ui_text((JS / "new-session.js")).replace("\r\n", "\n")
    assert "const replyIsStale = (startKey) => !alive.current || officeRouteKey.current !== startKey;" in source
    for name in ("submit", "answerCloud", "resendSecret"):
        body = source.split(f"  async function {name}(", 1)[1].split("\n  }\n", 1)[0]
        assert "const startKey = officeRouteKey.current;" in body, name
        assert body.count("replyIsStale(startKey)") >= body.count("await ") + 1, name
        assert "if (!alive.current) return;" not in body, name
    assert source.count("setPendingCloud({") == source.count("routeKey: startKey })") == 2
    assert "if (askedFor !== startKey) return;" in source
    # VOF-T04: after the one-time level question the cloud question goes through one helper that adds the same route
    # key, and every call hands it the key its handler started with.
    assert "const pending = { ...question, routeKey: startKey };" in source
    assert source.count("queuePendingCloud({") == source.count("}, startKey);") == 3
    body = source.split("  async function chooseOfficeLevel(", 1)[1].split("\n  }\n", 1)[0]
    assert "const startKey = officeRouteKey.current;" in body
    assert "const stale = () => replyIsStale(startKey) || startKey !== pending.routeKey" in body
    assert body.count("stale()") >= body.count("await ") + 1


def test_components_use_the_htm_form():
    # htm only calls a component written as <${Name} …>; <Name …> silently draws an empty, unknown element
    bare = re.compile(r"<[A-Z][A-Za-z0-9]*[\s/>]")
    found = []
    for path in sorted([*(STATIC_DIR / "js").rglob("*.js"), *(STATIC_DIR / "extensions").rglob("*.js")]):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0] if not line.lstrip().startswith("*") else ""
            if bare.search(code):
                found.append(f"{path.relative_to(STATIC_DIR).as_posix()}:{number}")
    assert found == []


@needs_node
def test_other_new_routes_unchanged():
    out = run_node(f"""
      import {{ parseRoute }} from {file_url(JS / "route.js")};
      console.log(JSON.stringify(["#new/team/v-1", "#new/plan/p-1", "#new/run/r-1"].map(parseRoute)));
    """)
    assert out == [
        {"section": "new", "id": "v-1", "kind": "team"},
        {"section": "new", "id": "p-1", "kind": "plan"},
        {"section": "new", "id": "r-1"},
    ]


@needs_node
def test_selector_hidden_without_offices():
    out = run_node(f"""
      import {{ officeOptions }} from {file_url(JS / "new-session-model.js")};
      console.log(JSON.stringify(officeOptions({{ offices: [] }})));
    """)
    assert out == [{"value": "", "label": "Ninguna"}]
    source = ui_text((JS / "new-session.js"))
    assert 'officeList.offices.length > 0 && html`<div class="ns-office-select">' in source


@needs_node
def test_orchestrator_sends_the_office():
    out = run_node(f"""
      import {{ officeRequestBody }} from {file_url(JS / "new-session-model.js")};
      console.log(JSON.stringify(officeRequestBody("Menu and notice", "cafe")));
    """)
    assert out == {
        "text": "Menu and notice",
        "confirm_cloud": False,
        "approval_id": None,
        "office": "cafe",
    }
    source = ui_text((JS / "new-session.js"))
    assert "officeRequestBody(text, officeId, confirmCloud)" in source
    assert "(entries.length > 0 || officeId)" in source


@needs_node
def test_single_session_offers_office_workflows():
    out = run_node(f"""
      import {{ officeWorkflowGroups }} from {file_url(JS / "new-session-model.js")};
      const areas = [{{ id: "menu", name: "Menu" }}, {{ id: "notices", name: "Notices and replies" }}];
      const catalog = [
        {{ id: "weekly_menu", bundle: "cafe__menu", label: "Weekly menu" }},
        {{ id: "write_notice", bundle: "cafe__notices", label: "Write a notice" }},
        {{ id: "general_only", bundle: "general", label: "General" }},
      ];
      console.log(JSON.stringify({{
        all: officeWorkflowGroups(areas, catalog),
        one: officeWorkflowGroups("menu", catalog),
      }}));
    """)
    assert out == {
        "all": [
            {"area": "menu", "items": [{"id": "weekly_menu", "label": "Weekly menu"}]},
            {"area": "notices", "items": [{"id": "write_notice", "label": "Write a notice"}]},
        ],
        "one": [{"area": "menu", "items": [{"id": "weekly_menu", "label": "Weekly menu"}]}],
    }
    source = ui_text((JS / "new-session.js"))
    assert "OfficeWorkflowPicker" in source and "groups=${officeGroups}" in source
    assert 'item.bundle === officeId + "__" + area' in source
    assert "officeArea || (!routeChanged && sessionKind === \"single\")" in source
    assert 'if (officeId && officeArea && next === "orchestrator")' in source


@needs_node
def test_office_without_areas_does_not_load_the_global_catalog():
    out = run_node(f"""
      import {{ loadCatalog }} from {file_url(JS / "new-session-model.js")};
      const paths = [];
      const entries = await loadCatalog({{
        get: async (path) => {{ paths.push(path); return []; }},
        bundles: [],
      }});
      console.log(JSON.stringify({{ entries, paths }}));
    """)
    assert out == {"entries": [], "paths": []}


@needs_node
def test_office_level_words_match_the_offices_view():
    out = run_node(f"""
      const mafUi = await import({file_url(STATIC_DIR / "js" / "i18n.js")});
      await import({file_url(STATIC_DIR / "extensions" / "offices" / "i18n.js")});
      mafUi.setLanguage("es");
      const {{ OFFICE_LEVEL_TEXT, officeLevelText }} = await import({file_url(JS / "new-session-model.js")});
      const {{ LEVEL_TEXT }} = await import({file_url(STATIC_DIR / "extensions" / "offices" / "model.js")});
      const scene = (...classes) => ({{ areas: classes.map((data_class) => ({{ data_class }})) }});
      console.log(JSON.stringify({{
        same: JSON.stringify(OFFICE_LEVEL_TEXT) === JSON.stringify(LEVEL_TEXT),
        normal: officeLevelText(scene("non_sensitive")),
        strictest: officeLevelText(scene("non_sensitive", "restricted", "sensitive")),
        sensitive: officeLevelText(scene("non_sensitive", "sensitive")),
        none: officeLevelText(scene(null)),
      }}));
    """)
    assert out == {"same": True, "normal": "Normal", "strictest": "Solo local", "sensitive": "Sensible",
                   "none": "Sin nivel"}


@needs_node
def test_no_cloud_for_a_protected_office():
    out = run_node(f"""
      import {{
        officeAllowsCloud, officeConnectionData, officeExecutionModes, officeLimitNotice, officeModelOptions,
        officeModelValue,
      }} from {file_url(JS / "new-session-model.js")};
      const scene = {{ areas: [{{ data_class: "restricted" }}, {{ data_class: "non_sensitive" }}] }};
      const routing = {{ catalog_models: [
        {{ id: "local", provider: "ollama" }}, {{ id: "cloud", provider: "openai" }},
      ] }};
      const step = {{ step_id: "answer", model: "cloud", model_options: ["local", "cloud"] }};
      const connections = officeConnectionData({{ connections: [
        {{ id: "ollama" }}, {{ id: "openai" }}, {{ id: "claude_cli" }},
      ] }}, scene);
      const notice = officeLimitNotice({{ suggestions: [
        {{ id: "openai", kind: "model" }}, {{ id: "ollama", kind: "model" }},
        {{ id: "claude_cli", kind: "executor" }},
      ] }}, scene);
      console.log(JSON.stringify({{
        allowsCloud: officeAllowsCloud(scene),
        connections: connections.connections.map((item) => item.id),
        suggestions: notice.suggestions.map((item) => item.id),
        modes: officeExecutionModes(["auto", "local", "cloud"], officeAllowsCloud(scene)),
        options: officeModelOptions(step, routing, officeAllowsCloud(scene)),
        selected: officeModelValue(step, {{}}, routing, officeAllowsCloud(scene)),
      }}));
    """)
    assert out == {
        "allowsCloud": False,
        "connections": ["ollama"],
        "suggestions": ["ollama"],
        "modes": ["auto", "local"],
        "options": ["local"],
        "selected": "",
    }


@needs_node
def test_fallback_line():
    out = run_node(f"""
      import {{ PlanDraftBody }} from {file_url(JS / "plan-draft.js")};
      const texts = (node) => {{
        const found = [];
        const walk = (item) => {{
          if (item === null || item === undefined || item === false || item === true) return;
          if (Array.isArray(item)) {{ item.forEach(walk); return; }}
          if (typeof item !== "object") {{ found.push(String(item)); return; }}
          walk(item.props && item.props.children);
        }};
        walk(node);
        return found;
      }};
      const draft = {{ goal: "Menu", tasks: [], fallback_reason: "office_not_normal" }};
      const mockDraft = {{ goal: "Menu", tasks: [], fallback_reason: "mock" }};
      const oldDraft = {{ goal: "Menu", tasks: [] }};
      console.log(JSON.stringify({{
        fallback: texts(PlanDraftBody({{ draft, mode: "simple", catalog: [] }})),
        mock: texts(PlanDraftBody({{ draft: mockDraft, mode: "simple", catalog: [] }})),
        unchanged: texts(PlanDraftBody({{ draft: oldDraft, mode: "simple", catalog: [] }})),
      }}));
    """)
    assert "Esta oficina tiene áreas que no salen de tu equipo: preparé su plan por defecto" in out["fallback"]
    assert "En modo de prueba (sin gasto) uso el plan por defecto de la oficina" in out["mock"]
    assert not any("plan por defecto" in text for text in out["unchanged"])


@needs_node
def test_unknown_office_and_level_required_without_privacy_screen():
    out = run_node(f"""
      import {{ officeLevelRequiredText }} from {file_url(JS / "new-session-model.js")};
      console.log(JSON.stringify([
        officeLevelRequiredText({{ details: {{ reason: "office_level_required" }} }}),
        officeLevelRequiredText({{ details: {{ reason: "other" }} }}),
      ]));
    """)
    assert out == [
        "Esta área todavía no tiene nivel de privacidad: elígelo en «Privacidad»",
        "",
    ]
    source = ui_text((JS / "new-session.js"))
    assert 'if (!officeList.offices.some((office) => office.id === officeId))' in source
    assert 'navigate("new")' in source and "Esa oficina ya no está." in source
    assert "officeLevelRequiredText(failure)" in source and "dispatch({ type: \"notice\", text }, { keepDraft: true })" in source


class NewSessionDouble:
    def __init__(self):
        self.calls = []

    def interpret(self, text, *, confirm_cloud=False, approval_id=None, office=None):
        self.calls.append(("interpret", text, confirm_cloud, approval_id, office))
        return {"text": text, "confirm_cloud": confirm_cloud, "approval_id": approval_id, "office": office}

    def draft_plan(self, text, *, confirm_cloud=False, approval_id=None, office=None):
        self.calls.append(("plan", text, confirm_cloud, approval_id, office))
        return {"text": text, "confirm_cloud": confirm_cloud, "approval_id": approval_id, "office": office}


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, bundles={}, dry_run=True)
    double = NewSessionDouble()
    services.new_session = double
    token = ensure_token(settings.gui_token_file)
    with TestClient(create_app(services=services), base_url="http://127.0.0.1") as client:
        yield SimpleNamespace(client=client, services=services, double=double, auth={"Authorization": f"Bearer {token}"})
    services.runs.close()


def test_interpret_and_plan_forward_office_to_service(env):
    interpret = env.client.post(
        "/gui/api/new-session/interpret",
        headers=env.auth,
        json={"text": "Menu and notice", "office": "cafe"},
    )
    plan = env.client.post(
        "/gui/api/new-session/plan",
        headers=env.auth,
        json={"text": "Menu and notice", "confirm_cloud": True, "office": "cafe"},
    )
    assert interpret.status_code == plan.status_code == 200
    assert interpret.json() == {
        "text": "Menu and notice", "confirm_cloud": False, "approval_id": None, "office": "cafe",
    }
    assert plan.json() == {
        "text": "Menu and notice", "confirm_cloud": True, "approval_id": None, "office": "cafe",
    }
    assert env.double.calls == [
        ("interpret", "Menu and notice", False, None, "cafe"),
        ("plan", "Menu and notice", True, None, "cafe"),
    ]
