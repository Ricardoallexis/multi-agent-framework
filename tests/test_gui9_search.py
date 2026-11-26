# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI4-T08: office, project, and recent-conversation search. Pure model tests run in Node."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR
from gui_text import ui_text

JS = STATIC_DIR / "js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
SPANISH_NAVIGATOR = [
    "--import",
    "data:text/javascript,Object.defineProperty(globalThis,'navigator',{value:{language:'es-MX'},configurable:true})",
]


def uri(name: str) -> str:
    return json.dumps((JS / name).as_uri())


def run_node(source: str) -> dict:
    result = subprocess.run(
        [NODE, *SPANISH_NAVIGATOR, "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=STATIC_DIR,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


SETUP = """
  import { dayNumber, groupPaletteResults, itemEntries, KINDS, officeEntries, openEntry, searchPalette, sectionEntries } from %s;
  import { runItems } from %s;
  const now = new Date(2026, 10, 25, 12).getTime();
  const offices = [{ id: "north/office", name: "North Office" }];
  const projects = [{ id: "project-1", name: "Roadmap" }];
  const items = [
    { key: "old", kind: "session", title: "Old notes", project: "Atlas", route: ["sessions", "old"],
      updatedAt: new Date(2026, 10, 1, 12).toISOString() },
    { key: "week", kind: "session", title: "Weekly notes", project: "Atlas", route: ["sessions", "week"],
      updatedAt: new Date(2026, 10, 20, 12).toISOString() },
    { key: "yesterday", kind: "session", title: "Daily notes", project: "Atlas", route: ["sessions", "yesterday"],
      updatedAt: new Date(2026, 10, 24, 12).toISOString() },
    { key: "today", kind: "session", title: "Today notes", project: "Atlas", route: ["sessions", "today"],
      updatedAt: new Date(2026, 10, 25, 11).toISOString() },
    ...runItems([{ id: "waiting", workflow_id: "Release review", status: "waiting_human", project_name: "Atlas",
      updated_at: new Date(2026, 10, 10, 12).toISOString() }]),
  ];
  const navigation = { primary: [{ id: "sessions", label: "Sesiones" }], more: [] };
  const entries = [
    ...sectionEntries(navigation, [], "advanced", {
      offices, projects, areasByOffice: { "north/office": [{ id: "planning area", name: "Planning" }] },
      items: itemEntries(items, "advanced", { recent: true, now }),
    }),
    ...[{ key: "help:test", kind: "help", group: "actions", label: "Ayuda de controles",
      hint: "abrir controles", help: { title: "Ayuda", hint: "Abrir controles", more: [] } }],
  ];
""" % (uri("command-palette-model.js"), uri("sidebar-model.js"))


@needs_node
def test_search_finds_offices_areas_projects_recent_items_and_office_actions():
    out = run_node(SETUP + """
      const byQuery = (query) => searchPalette(entries, query).map((entry) => [entry.kind, entry.label, entry.route]);
      const empty = searchPalette(entries, "");
      console.log(JSON.stringify({
        area: byQuery("planning north"),
        project: byQuery("roadmap"),
        newConversation: byQuery("nueva conversación north"),
        recent: byQuery("atlas").map(([kind, label]) => [kind, label]),
        help: byQuery("abrir controles").map(([kind, label]) => [kind, label]),
        emptyHasHelp: empty.some((entry) => entry.kind === "help"),
        groups: groupPaletteResults(empty).map((group) => group.key),
        kinds: [KINDS.office, KINDS.area, KINDS.project, KINDS.action],
        areaOpen: openEntry(entries.find((entry) => entry.kind === "area")),
        officeOpen: officeEntries(offices)[0].route,
        missingDates: [dayNumber(null), dayNumber("")],
      }));
    """)
    assert out["area"] == [["area", "Planning", ["new/office/north%2Foffice", "planning area"]]]
    assert out["project"] == [["project", "Roadmap", ["project", "project-1"]]]
    assert out["newConversation"] == [["action", "Nueva conversación en North Office", ["new/office", "north/office"]]]
    assert out["recent"][0] == ["run", "Release review"]
    assert out["help"] == [["help", "Ayuda de controles"]]
    assert out["emptyHasHelp"] is False
    assert out["groups"] == ["waiting", "offices", "projects", "recent:today", "recent:yesterday", "recent:week", "recent:older", "actions"]
    assert out["kinds"] == ["Oficina", "Área", "Proyecto", "Acción"]
    assert out["areaOpen"] == {"route": ["new/office/north%2Foffice", "planning area"]}
    assert out["officeOpen"] == ["offices", None]
    assert out["missingDates"] == [None, None]


@needs_node
def test_recent_conversations_are_date_grouped_with_waiting_items_first():
    out = run_node(SETUP + """
      const recent = entries.filter((entry) => entry.group === "waiting" || entry.group === "recent");
      const groups = groupPaletteResults(searchPalette(entries, ""));
      console.log(JSON.stringify({
        order: recent.map((entry) => [entry.label, entry.subgroup]),
        headings: groups.map((group) => group.key),
      }));
    """)
    assert out["order"] == [
        ["Release review", "waiting"],
        ["Today notes", "today"],
        ["Daily notes", "yesterday"],
        ["Weekly notes", "week"],
        ["Old notes", "older"],
    ]
    assert out["headings"] == ["waiting", "offices", "projects", "recent:today", "recent:yesterday", "recent:week", "recent:older", "actions"]


@needs_node
def test_empty_search_caps_each_group_and_keeps_waiting_results_first_above_offices():
    out = run_node(SETUP + """
      const manyOffices = Array.from({ length: 40 }, (_, index) => ({ id: "office-" + index, name: "Office " + index }));
      const manyProjects = Array.from({ length: 8 }, (_, index) => ({ id: "project-" + index, name: "Project " + index }));
      const recentItems = Array.from({ length: 8 }, (_, index) => ({
        key: "recent-" + index, kind: "session", title: "Conversation " + index, project: "Workspace",
        route: ["sessions", "recent-" + index], updatedAt: new Date(2026, 10, 25, index).toISOString(),
      }));
      recentItems.push({
        key: "pending", kind: "run", title: "Needs reply", project: "Workspace", route: ["runs", "pending"],
        attention: true, updatedAt: new Date(2026, 10, 1).toISOString(),
      });
      const manyEntries = sectionEntries(navigation, [], "advanced", {
        offices: manyOffices, projects: manyProjects, areasByOffice: Object.fromEntries(
          manyOffices.map((office) => [office.id, [{ id: "area", name: "Area" }]])),
        items: itemEntries(recentItems, "advanced", { recent: true, now }),
      });
      const results = searchPalette(manyEntries, "", 30);
      console.log(JSON.stringify({
        count: results.length,
        first: results[0] && [results[0].group, results[0].label],
        groups: [...new Set(results.map((entry) => entry.group))],
        offices: results.filter((entry) => entry.group === "offices").length,
        projects: results.filter((entry) => entry.group === "projects").length,
        recent: results.filter((entry) => entry.group === "recent").length,
        actions: results.filter((entry) => entry.group === "actions").length,
      }));
    """)
    assert 0 < out["count"] <= 30
    assert out["first"] == ["waiting", "Needs reply"]
    assert out["groups"] == ["waiting", "offices", "projects", "recent", "actions"]
    assert out["offices"] > 0 and out["projects"] > 0 and out["recent"] > 0 and out["actions"] > 0


def test_sidebar_loads_office_areas_only_while_search_is_open_and_shows_errors():
    sidebar = ui_text(JS / "sidebar.js")
    block = sidebar[sidebar.index("/* GUI4-T08 */"):sidebar.index("return html`<div class=\"side\">")]
    assert 'if (!paletteOpen)' in block
    assert 'apiGet("/gui/api/offices/"' in block
    assert 'status: failed ? "error" : "ready"' in block
    assert "areasByOffice: paletteAreaData.areasByOffice" in block
    assert 'isRegisteredView("offices") ? lists.offices : []' in block
    assert 'isRegisteredView("project") ? lists.projects : []' in block
    assert "previous.status === \"idle\"" in block
    palette = ui_text(JS / "command-palette.js")
    assert "groupPaletteResults(results)" in palette and 'role="option"' in palette
    assert 'areaStatus === "error"' in palette and 'role="alert"' in palette
    assert 'role=${group.titleKey ? "group" : "presentation"}' in palette
    assert 'aria-hidden="true" class="palette-kind palette-group-title"' in palette
    assert 'aria-hidden="true" class="palette-kind palette-subgroup"' in palette
    assert 'role="group"' not in palette
    assert "innerHTML" not in palette and "style=" not in palette


@needs_node
def test_ungrouped_palette_entries_have_no_group_heading():
    out = run_node(SETUP + """
      const groups = groupPaletteResults([
        { key: "section:sessions", kind: "section", label: "Sessions", route: ["sessions", null] },
        { key: "item:one", kind: "session", label: "Conversation", route: ["sessions", "one"] },
      ]);
      console.log(JSON.stringify(groups.map((group) => [group.titleKey, group.items.length])));
    """)
    assert out == [[None, 2]]
