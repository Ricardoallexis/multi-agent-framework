from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from multiagent.gui import STATIC_DIR

JS = STATIC_DIR / "js"
NODE = shutil.which("node")


def run_node(script: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_projection_keeps_real_edges_safe_labels_and_null_tokens():
    module_url = (JS / "work-map.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ projectRows, labelTone, isNodeCollapsed }} from {json.dumps(module_url)};
      const data = {{
        projects: [{{ id: "p", title: "Proyecto privado", visibility: "private", progress: {{done: 1, total: 2, percent: 50}},
          attention: {{reviews: [{{id:"t2",title:"Revisar",status:"review",label:{{key:"review",text:"En revisión",tone:"wait"}},depends_on:["t1"]}}], verifications:[], mine:[], blocked:[]}},
          stages: [{{group_id:"p-stage",title:"Etapa",label:{{key:"doing",text:"En curso",tone:"success"}},progress:{{done:1,total:2,percent:50}},tasks:[
            {{id:"t1",title:"Tarea",state:"doing",status:"doing",label:{{key:"doing",text:"Trabajando",tone:"success"}},depends_on:[],phase:"build"}},
            {{id:"t2",title:"Tarea 2",status:"ready",label:{{key:"ready",text:"Lista",tone:"neutral"}}}}
          ]}}] }}],
        sessions: {{
          "synthetic:root": {{parent_id:null,name:"Sesión raíz",source:"claude",status:"active",tokens:null,children:[
            {{id:"c1",name:"Hijo permitido",label:"PRIVATE SYNTHETIC",source:"claude",status:"running",model:"synthetic-child-model",tokens:null,link_id:"synthetic:kid"}}
          ]}},
          "synthetic:kid": {{parent_id:"synthetic:root",name:"Sesión hija",source:"codex",status:"idle",tokens:null,children:[]}},
          "synthetic:untasked": {{parent_id:null,name:null,source:"copilot",status:"idle",tokens:null,children:[]}}
          ,"synthetic:orphan": {{parent_id:"missing:parent",name:"Huérfana",source:"framework",status:"idle",tokens:null,children:[]}}
        }}, links:[{{task_id:"t1",session_id:"synthetic:root"}},{{task_id:"t2",session_id:"synthetic:root"}}]
      }};
      const all = projectRows(data, false, "");
      const flat = (nodes) => nodes.flatMap(n => [n, ...flat(n.children || [])]);
      const nodes = flat(all.tree);
      assert(nodes.some(n => n.title === "Sesión raíz"));
      assert(nodes.some(n => n.title === "Sesión hija" && n.relation === "aviso"));
      assert(nodes.some(n => n.title === "Sesiones sin tarea"));
      assert(nodes.some(n => n.title === "Huérfana"));
      const kid = nodes.find(n => n.title === "Sesión hija");
      assert.equal(nodes.filter(n => n.title === "Sesión hija").length, 1);
      assert.equal(kid.relation, "aviso");
      assert(nodes.some(n => n.kind === "task" && n.dependsOn.length === 1));
      assert.equal(nodes.find(n => n.id === "task:t1").assignmentEdges.length, 1);
      assert.equal(nodes.find(n => n.id === "task:t2").assignmentEdges.length, 1);
      assert(nodes.some(n => n.title === "Atención"));
      assert(nodes.some(n => n.kind === "session" && n.tokens === null));
      const child = nodes.find(n => n.kind === "child");
      assert.equal(child.model, "synthetic-child-model");
      assert.equal(child.tokens, null);
      assert(!JSON.stringify(all).includes("PRIVATE SYNTHETIC"));
      assert.equal(labelTone({{tone:"wait"}}), "warning");
      assert.equal(labelTone({{tone:"hold"}}), "muted");
      const finished = {{id:"finished", status:"done", children:[{{id:"child"}}]}};
      assert.equal(isNodeCollapsed(finished, true), true);
      assert.equal(isNodeCollapsed(finished, true, new Map([["finished", false]])), false);
      assert.equal(finished.children.length, 1); // projection remains intact for tree toggle
      const searched = projectRows(data, false, "hijo permitido");
      assert(flat(searched.tree).some(n => n.title === "Sesión raíz"));
      const cyc = {{projects:[], links:[{{task_id:"x",session_id:"a"}}], sessions:{{
        a:{{parent_id:"b",name:"A",status:"active",children:[]}}, b:{{parent_id:"a",name:"B",status:"active",children:[]}}
      }}}};
      assert(flat(projectRows(cyc, false, "").tree).length < 10);
      const chainSessions = Object.fromEntries(Array.from({{length:12}}, (_,i) => ["s"+i, {{
        parent_id:i ? "s"+(i-1) : null, name:"S"+i, status:"active", source:"codex", model:"synthetic-model", tokens:null, children:[]
      }}]));
      const chain = projectRows({{projects:[], sessions:chainSessions, links:[{{task_id:"chain",session_id:"s0"}}]}}, false, "");
      assert.equal(flat(chain.tree).filter(n => n.kind === "session").length, 12);
      assert.equal(flat(chain.tree).find(n => n.kind === "session").model, "synthetic-model");
      console.log(JSON.stringify({{nodes:nodes.length, toneWait:labelTone({{tone:"wait"}}), toneHold:labelTone({{tone:"hold"}})}}));
    '''
    answer = run_node(script)
    assert answer["nodes"] > 0
    assert answer["toneWait"] == "warning" and answer["toneHold"] == "muted"


def test_projection_caps_render_nodes_and_reports_exact_remainder():
    module_url = (JS / "work-map.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ projectRows }} from {json.dumps(module_url)};
      const tasks = Array.from({{length:350}}, (_,i) => ({{id:"t"+i,title:"Tarea "+i,status:"ready"}}));
      const result = projectRows({{projects:[{{id:"p",title:"P",stages:[{{group_id:"s",title:"S",tasks}}]}}]}}, false, "");
      const count = (nodes) => nodes.reduce((n,node) => n+1+count(node.children || []),0);
      assert.equal(count(result.tree),300);
      assert.equal(result.total,352); // project, stage, and 350 tasks
      assert.equal(result.remaining,52);
      console.log(JSON.stringify({{rendered:count(result.tree),total:result.total,remaining:result.remaining}}));
    '''
    assert run_node(script) == {"rendered": 300, "total": 352, "remaining": 52}


def test_finished_task_keeps_linked_session_and_child_link_projection():
    module_url = (JS / "work-map.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ projectRows, isNodeCollapsed }} from {json.dumps(module_url)};
      const data = {{
        projects: [{{ id:"p", title:"Proyecto", stages:[{{ group_id:"s", title:"Etapa", tasks:[
          {{ id:"done-task", title:"Tarea terminada", status:"completed" }}
        ] }}] }}],
        sessions: {{
          "synthetic:finished-root": {{ parent_id:null, name:"Sesión asignada", source:"codex", status:"active", children:[
            {{ id:"linked-child", name:"Hijo enlazado", source:"codex", status:"running", link_id:"synthetic:linked-session" }}
          ] }},
          "synthetic:linked-session": {{ parent_id:"synthetic:finished-root", name:"Sesión del hijo", source:"codex", status:"active", children:[] }}
        }},
        links: [{{ task_id:"done-task", session_id:"synthetic:finished-root" }}]
      }};
      const result = projectRows(data, false, "");
      const flat = (nodes) => nodes.flatMap((node) => [node, ...flat(node.children || [])]);
      const nodes = flat(result.tree);
      const task = nodes.find((node) => node.id === "task:done-task");
      assert(task);
      assert.equal(task.status, "completed");
      assert.equal(task.children[0].id, "session:synthetic:finished-root");
      assert.equal(isNodeCollapsed(task, true), true);
      assert.equal(task.children[0].children[0].linkedSessionId, "synthetic:linked-session");
      assert.equal(task.children[0].children[0].children[0].id, "session:synthetic:linked-session");
      assert.equal(task.children[0].children[0].children[0].relation, "aviso");
      assert.equal(flat(projectRows(data, true, "").tree).some((node) => node.id === "task:done-task"), false);
      console.log(JSON.stringify({{ taskChildren: task.children.length, linkedSession: task.children[0].children[0].children[0].id }}));
    '''
    answer = run_node(script)
    assert answer["taskChildren"] == 1
    assert answer["linkedSession"] == "session:synthetic:linked-session"


def test_map_route_mount_privacy_and_mobile_list_are_wired():
    view = (JS / "work-map.js").read_text(encoding="utf-8")
    route = (JS / "route.js").read_text(encoding="utf-8")
    main = (JS / "main.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert 'apiGet("/gui/api/map?only=all", token)' in view
    assert run_node(f'import {{ parseRoute }} from {json.dumps((JS / "route.js").as_uri())}; console.log(JSON.stringify(parseRoute("#map")));') == {"section": "map", "id": None}
    assert 'import { WorkMap } from "./work-map.js"' in main and '<${WorkMap} token=${token} />' in main
    assert 'window.matchMedia?.("(max-width: 640px)")' in view
    assert '"wm-private-badge"' in view and 'role="note"' in view
    assert ".work-map" in css and "font-family:" not in css
    assert "var(--src-claude)" in css and "var(--src-codex)" in css
