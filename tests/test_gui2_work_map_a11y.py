from __future__ import annotations

import json
import re
import shutil
import subprocess
import pytest

from multiagent.gui import STATIC_DIR

ROOT = STATIC_DIR.parents[2]
JS = STATIC_DIR / "js" / "work-map.js"
CSS = STATIC_DIR / "app.css"
NODE = shutil.which("node")


def run_node(script: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_labels_cover_all_synthetic_node_kinds_states_and_edge_chips():
    module_url = JS.as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ nodeAccessibleLabel, relationAccessibleLabel, projectRows }} from {json.dumps(module_url)};
      const data = {{
        projects: [{{id:"p", title:"Proyecto", stages:[{{group_id:"s", title:"Etapa", status:"active", tasks:[
          {{id:"t", title:"Tarea", status:"ready", depends_on:["prerequisite"],
            blockers:[{{task_id:"blocked-by", state:"running"}}]}}
        ]}}], attention:{{reviews:[{{id:"review", title:"Revisión", status:"review"}}]}}}}],
        sessions: {{root:{{parent_id:null, name:"Sesión", source:"codex", status:"active",
          children:[{{id:"agent", name:"Agente", source:"copilot", status:"running"}}]}}}},
        links:[{{task_id:"t", session_id:"root"}}]
      }};
      const flatten = (nodes) => nodes.flatMap((node) => [node, ...flatten(node.children || [])]);
      const nodes = flatten(projectRows(data).tree);
      const kinds = new Set(nodes.map((node) => node.kind));
      for (const kind of ["project", "stage", "task", "session", "child", "group"]) assert(kinds.has(kind), kind);
      const leaf = nodes.find((node) => node.kind === "child");
      assert.equal(nodeAccessibleLabel(leaf), "Hijo: Agente. Estado: Trabajando");
      assert.equal(nodeAccessibleLabel(nodes.find((node) => node.id === "task:t")),
        "Tarea: Tarea. Estado: Listo");
      assert.equal(relationAccessibleLabel("Depende de", "prerequisite"), "Depende de: prerequisite");
      assert.equal(relationAccessibleLabel("Bloqueo", "blocked-by", "running"),
        "Bloqueo: blocked-by, estado: En ejecución");
      console.log(JSON.stringify({{ kinds:[...kinds], leaf:nodeAccessibleLabel(leaf) }}));
    '''
    answer = run_node(script)
    assert set(answer["kinds"]) == {"project", "stage", "task", "session", "child", "group"}
    assert answer["leaf"] == "Hijo: Agente. Estado: Trabajando"


def test_rendered_status_text_matches_row_accessible_label_and_keeps_registered_tone():
    module_url = JS.as_uri()
    labels_url = (STATIC_DIR / "js" / "ui" / "labels.js").as_uri()
    chip_url = (STATIC_DIR / "js" / "ui" / "status-chip.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ Node, ListItem }} from {json.dumps(module_url)};
      import {{ StatusChip }} from {json.dumps(chip_url)};
      import {{ registerLabelSet, resetLabelSets }} from {json.dumps(labels_url)};
      const nodes = [
        ...["ready", "doing", "review"].map((status) => ({{id:status,kind:"task",title:status,status,children:[]}})),
        {{id:"session",kind:"session",title:"Session",status:"active",children:[]}},
        {{id:"child",kind:"child",title:"Child",status:"running",children:[]}},
      ];
      function walk(value, result=[]) {{
        if (!value || typeof value !== "object") return result;
        if (Array.isArray(value)) {{ value.forEach((child) => walk(child,result)); return result; }}
        if ([Node,ListItem,StatusChip].includes(value.type)) return walk(value.type(value.props),result);
        if (typeof value.type === "string") result.push(value);
        walk(value.props?.children,result);
        return result;
      }}
      try {{
        registerLabelSet("session", {{active:{{text:"Activa",tone:"success"}}}});
        registerLabelSet("subagent", {{running:{{text:"Trabajando",tone:"warning"}}}});
        for (const component of [Node,ListItem]) for (const node of nodes) {{
          const rendered=walk(component({{node,folded:false,foldedIds:new Map(),onToggleFold:()=>{{}}}}));
          const row=rendered.find((item)=>item.props["data-wm-node"]===node.id);
          const chip=rendered.find((item)=>item.props.class?.includes("status-chip"));
          assert(chip,node.id+" status chip rendered");
          assert.equal(chip.props.children,row.props["aria-label"].split(". Estado: ")[1],node.id+" visible/accessibility text");
          assert(chip.props.class.includes("tone-"),node.id+" tone class retained");
          if(node.kind==="session") assert(chip.props.class.includes("tone-success"));
          if(node.kind==="child") assert(chip.props.class.includes("tone-warning"));
        }}
      }} finally {{ resetLabelSets(); }}
      console.log(JSON.stringify({{checked:10}}));
    '''
    assert run_node(script) == {"checked": 10}


def test_projection_order_additions_removals_and_finished_fold_keep_stable_nodes():
    module_url = JS.as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ projectRows, reconcileMapSnapshot, isNodeCollapsed }} from {json.dumps(module_url)};
      const snapshot = (tasks) => projectRows({{projects:[{{id:"p", title:"Proyecto",
        stages:[{{group_id:"s", title:"Etapa", tasks}}]}}]}});
      const before = snapshot([
        {{id:"first", title:"Primera"}}, {{id:"keep", title:"Conservar"}}
      ]);
      const kept = before.tree[0].children[0].children[1];
      const after = reconcileMapSnapshot(before, snapshot([
        {{id:"keep", title:"Conservar"}}, {{id:"added", title:"Añadida"}}
      ]));
      const tasks = after.tree[0].children[0].children;
      assert.deepEqual(tasks.map((node) => node.id), ["task:keep", "task:added"]);
      assert.strictEqual(tasks[0], kept);
      const completed = {{id:"completed", status:"completed", children:[{{id:"leaf"}}]}};
      assert.equal(isNodeCollapsed(completed, true), true);
      assert.equal(completed.children[0].id, "leaf");
      console.log(JSON.stringify({{order:tasks.map((node) => node.id), retained:tasks[0] === kept}}));
    '''
    answer = run_node(script)
    assert answer == {"order": ["task:keep", "task:added"], "retained": True}


def test_rendered_nodes_and_edges_are_focusable_with_native_actions():
    module_url = JS.as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ Node, ListItem, restoreMapFocus }} from {json.dumps(module_url)};
      const nodes = ["project", "stage", "task", "session", "child", "group"].map((kind) => ({{
        id:kind, kind, title:kind, status:"active", children:[],
        ...(kind === "task" ? {{dependsOn:["before"], blockers:[{{task_id:"blocked",state:"running"}}], assignmentEdges:[{{sessionId:"assigned",title:"Assigned"}}]}} : {{}}),
        ...(kind === "session" ? {{sessionId:"sid"}} : {{}}),
        ...(kind === "child" ? {{linkedSessionId:"child-session"}} : {{}})
      }}));
      function walk(value, result=[]) {{
        if (!value || typeof value !== "object") return result;
        if (Array.isArray(value)) {{ value.forEach((child) => walk(child,result)); return result; }}
        if (value.type === Node || value.type === ListItem) return walk(value.type(value.props),result);
        if (typeof value.type === "string") result.push(value);
        walk(value.props?.children,result);
        return result;
      }}
      for (const component of [Node, ListItem]) {{
        for (const node of nodes) {{
          const rendered=walk(component({{node,folded:false,foldedIds:new Map(),onToggleFold:()=>{{}}}}));
          const row=rendered.find((item)=>item.props["data-wm-node"]===node.id);
          assert.equal(row.props.tabindex,"0");
          assert(row.props["aria-label"].includes(node.title));
          assert(row.props["aria-label"].includes("Estado:"));
          assert(!row.props.onKeyDown);
          for(const button of rendered.filter((item)=>item.type==="button")) {{
            assert.equal(button.props.type,"button");
            assert.equal(typeof button.props.onClick,"function");
            assert(!button.props.onKeyDown);
          }}
          if(node.kind==="task") {{
            const chips=rendered.filter((item)=>item.props.class==="wm-relation");
            assert.equal(chips.length,2);
            assert(chips.every((item)=>item.props.role==="note" && item.props.tabindex==="0"));
            assert(chips[1].props["aria-label"].includes("En ejecución"));
          }}
        }}
      }}
      let toggled;
      const tree={{id:"parent",kind:"project",title:"Parent",children:[nodes[2]]}};
      const expanded=walk(Node({{node:tree,folded:false,foldedIds:new Map(),onToggleFold:(id,collapsed)=>toggled=[id,collapsed]}}));
      expanded.find((item)=>item.props.class==="wm-fold").props.onClick();
      assert.deepEqual(toggled,["parent",false]);
      assert(expanded.some((item)=>item.props["data-wm-node"]==="task"));
      const collapsed=walk(Node({{node:tree,folded:false,foldedIds:new Map([["parent",true]]),onToggleFold:()=>{{}}}}));
      assert(!collapsed.some((item)=>item.props["data-wm-node"]==="task"));
      let focused;
      const body={{}}, outside={{}};
      const parent={{getAttribute:()=>"parent",focus:()=>focused="parent"}};
      const root={{querySelectorAll:()=>[parent],focus:()=>focused="root"}};
      assert(restoreMapFocus(root,{{id:"removed",ancestors:["parent"]}},{{body,activeElement:body}}));
      assert.equal(focused,"parent");
      assert(!restoreMapFocus(root,{{id:"removed",ancestors:["parent"]}},{{body,activeElement:outside}}));
      assert(!restoreMapFocus(root,{{id:"removed",ancestors:["parent"]}},{{body,activeElement:body,hasFocus:()=>false}}));
      assert(restoreMapFocus(root,{{id:"removed",ancestors:[]}},{{body,activeElement:body}}));
      assert.equal(focused,"root");
      console.log(JSON.stringify({{kinds:nodes.length,folded:true,focus:true}}));
    '''
    assert run_node(script) == {"kinds": 6, "folded": True, "focus": True}


def _matching_brace(source: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError("CSS block has no matching closing brace")


def _media_ranges(source: str, media_name: str) -> list[tuple[int, int]]:
    ranges = []
    for match in re.finditer(r"@media\s*\(([^)]*)\)\s*\{", source):
        if media_name in match.group(1):
            ranges.append((match.start(), _matching_brace(source, match.end() - 1)))
    return ranges


def _blocks(source: str, selector: str) -> list[tuple[int, str]]:
    blocks = []
    for match in re.finditer(re.escape(selector) + r"\s*\{", source):
        end = _matching_brace(source, match.end() - 1)
        blocks.append((match.start(), source[match.end():end]))
    return blocks


def _declarations(block: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block))


def _theme_tokens(css: str, dark: bool) -> dict[str, str]:
    dark_ranges = _media_ranges(css, "prefers-color-scheme: dark")
    tokens: dict[str, str] = {}
    for position, block in _blocks(css, ":root"):
        inside_dark = any(start < position < end for start, end in dark_ranges)
        if not inside_dark or dark:
            tokens.update(_declarations(block))
    selector_blocks = _blocks(css, ".work-map")
    scoped = {}
    for position, block in selector_blocks:
        inside_dark = any(start < position < end for start, end in dark_ranges)
        if not inside_dark or dark:
            scoped.update(_declarations(block))
    tokens.update(scoped)
    return tokens


def _resolve_color(name: str, tokens: dict[str, str], seen: frozenset[str] = frozenset()) -> str:
    if name in seen:
        raise AssertionError(f"CSS variable cycle at {name}")
    value = tokens[name]
    reference = re.fullmatch(r"\s*var\((--[\w-]+)\)\s*", value)
    return _resolve_color(reference.group(1), tokens, seen | {name}) if reference else value.strip()


def _rgb(value: str) -> tuple[int, int, int]:
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        value = "#" + "".join(channel * 2 for channel in value[1:])
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"Expected a concrete six-digit CSS color, got {value!r}"
    return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))


def _luminance(color: str) -> float:
    channels = []
    for channel in _rgb(color):
        value = channel / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _ratio(foreground: str, background: str) -> float:
    high, low = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_map_palette_meets_wcag_contrast_and_writes_calculated_table():
    css = CSS.read_text(encoding="utf-8")
    report = ["# GUI2-T15: tabla de contraste", "",
              "Ratios WCAG 2.1 calculados desde los tokens CSS efectivos; redondeo a dos decimales.", ""]
    text_tokens = [
        "--text", "--muted", "--accent", "--ok", "--warn", "--bad",
        "--src-claude", "--src-codex", "--src-copilot", "--src-framework",
    ]
    for dark, theme in ((False, "Claro"), (True, "Oscuro")):
        tokens = _theme_tokens(css, dark)
        surfaces = {name: _resolve_color(name, tokens) for name in ("--bg", "--panel", "--chrome")}
        report.extend([f"## Tema {theme}", "", "| Token de texto | Superficie | Ratio |", "|---|---|---:|"])
        for token in text_tokens:
            for surface, background in surfaces.items():
                ratio = _ratio(_resolve_color(token, tokens), background)
                assert ratio >= 4.5, f"{theme}: {token} on {surface} is {ratio:.2f}:1"
                report.append(f"| `{token}` | `{surface}` | {ratio:.2f}:1 |")
        line = _resolve_color("--wm-line", tokens)
        report.extend(["", "| Línea/control | Superficie | Ratio |", "|---|---|---:|"])
        for surface in ("--bg", "--panel"):
            ratio = _ratio(line, surfaces[surface])
            assert ratio >= 3, f"{theme}: --wm-line on {surface} is {ratio:.2f}:1"
            report.append(f"| `--wm-line` | `{surface}` | {ratio:.2f}:1 |")
        accent = _resolve_color("--accent", tokens)
        on_accent = _resolve_color("--on-accent", tokens)
        ratio = _ratio(on_accent, accent)
        assert ratio >= 4.5, f"{theme}: --on-accent on --accent is {ratio:.2f}:1"
        report.append(f"| `--on-accent` | `--accent` | {ratio:.2f}:1 |")
        report.append("")
    destination = ROOT / ".local" / "CONTRASTE-T15.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(report) + "\n", encoding="utf-8")


def test_map_css_reduces_motion_and_uses_text_and_nondecorative_relational_marks():
    css = CSS.read_text(encoding="utf-8")
    assert re.search(
        r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[^{}]*"
        r"\.work-map,\s*\.work-map \*,\s*\.work-map \*::before,\s*\.work-map \*::after"
        r"\s*\{[^{}]*animation:\s*none\s*!important;[^{}]*"
        r"scroll-behavior:\s*auto\s*!important;[^{}]*transition:\s*none\s*!important",
        css,
        re.S,
    )
    assert ".work-map :focus-visible { outline: 3px solid var(--accent);" in css
    assert "border-left: 1px solid var(--wm-line)" in css
    assert ".work-map .progress-track { border-color: var(--wm-line); }" in css
    assert ".wm-relation { " in css and "color: var(--muted)" in css
    assert "min-height: var(--target)" in css
    assert ".work-map { min-width: 0; --wm-line: #85837b; }" in css
    assert "@media (prefers-color-scheme: dark) { .work-map { --wm-line: #77766f; } }" in css
