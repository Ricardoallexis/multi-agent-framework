from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.gui import STATIC_DIR
from multiagent.gui.token import ensure_token
from session_fixtures import add_framework_run, make_services, make_stores

JS = STATIC_DIR / "js"
NODE = shutil.which("node")


def run_node(script: str) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    result = subprocess.run([NODE, "--input-type=module", "--eval", script], capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_snapshots_reconcile_by_identity_and_projection_keys_are_stable():
    module_url = (JS / "work-map.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ reconcileMapSnapshot, projectRows }} from {json.dumps(module_url)};
      const before = {{
        version: "v1", projects: [{{id:"p", title:"Proyecto", stages:[{{group_id:"s", tasks:[{{id:"t", title:"Tarea"}}]}}]}}],
        sessions: {{
          root: {{parent_id:null, name:"Raíz", status:"active", children:[{{id:"agent", name:"Agente", status:"running"}}]}},
          sibling: {{parent_id:null, name:"Hermana", status:"idle", children:[]}}
        }},
        links: [{{task_id:"t", session_id:"root"}}]
      }};
      const identical = reconcileMapSnapshot(before, JSON.parse(JSON.stringify(before)));
      assert.strictEqual(identical, before);
      const changed = JSON.parse(JSON.stringify(before));
      changed.sessions.root.children[0].status = "done";
      const reconciled = reconcileMapSnapshot(before, changed);
      assert.notStrictEqual(reconciled, before);
      assert.notStrictEqual(reconciled.sessions.root, before.sessions.root);
      assert.strictEqual(reconciled.sessions.sibling, before.sessions.sibling);
      assert.strictEqual(reconciled.projects, before.projects);
      const modified = JSON.parse(JSON.stringify(changed));
      delete modified.sessions.sibling;
      modified.sessions.added = {{parent_id:null, name:"Nueva", status:"active", children:[]}};
      const withChanges = reconcileMapSnapshot(reconciled, modified);
      assert(!Object.hasOwn(withChanges.sessions, "sibling"));
      assert(Object.hasOwn(withChanges.sessions, "added"));
      const projection = projectRows({{
        projects: [], links: [], sessions: {{
          root: {{parent_id:null, name:"Raíz", status:"active", children:[{{id:"agent", name:"Agente", status:"running"}}]}}
        }}
      }}, false, "");
      const flatten = (nodes) => nodes.flatMap((node) => [node, ...flatten(node.children || [])]);
      const nodes = flatten(projection.tree);
      assert(nodes.some((node) => node.id === "session:root"));
      assert(nodes.some((node) => node.id === "child:root:agent"));
      const status = (visible, total, remainder) => visible + "|" + total + "|" + remainder;
      assert.equal(status(4, 5, 0), status(4, 5, 0));
      assert.notEqual(status(4, 5, 0), status(5, 6, 0));
      console.log(JSON.stringify({{ stable: identical === before, added: withChanges.sessions.added.name === "Nueva" }}));
    '''
    assert run_node(script) == {"stable": True, "added": True}


def test_child_events_deduplicate_coalesce_and_cleanup_with_a_bounded_stream_count():
    module_url = (JS / "work-map.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ followMapSessions, createRefreshQueue, mapCountAnnouncement }} from {json.dumps(module_url)};
      const subscriptions = [];
      const changes = [];
      const versions = new Map();
      let timerCallback;
      let timerCount = 0;
      let canceled = false;
      let fetched = 0;
      const queue = createRefreshQueue(() => fetched++, 60, {{
        setTimeoutFn(callback) {{ timerCount++; timerCallback = callback; return 17; }},
        clearTimeoutFn(id) {{ canceled = id === 17; }}
      }});
      const stop = followMapSessions({{
        token: "synthetic-token",
        versions,
        sessionIds: Array.from({{length:305}}, (_, index) => "session:" + index),
        follow(options) {{
          options.onState("connected");
          subscriptions.push(options);
          return () => {{ options.stopped = true; }};
        }},
        onChange: (session, event) => {{ changes.push([session, event.version]); queue.schedule(); }}
      }});
      assert.equal(subscriptions.length, 300);
      subscriptions[0].onChange({{version:"v1"}});
      subscriptions[0].onChange({{version:"v1"}});
      subscriptions[0].onChange({{version:"v2"}});
      assert.deepEqual(changes, [["session:0", "v1"], ["session:0", "v2"]]);
      const reopen = followMapSessions({{
        token: "synthetic-token", versions, sessionIds: ["session:0"],
        follow(options) {{ options.onChange({{version:"v2"}}); return () => {{}}; }},
        onChange: (session, event) => changes.push([session, event.version])
      }});
      assert.equal(changes.length, 2);
      reopen();
      assert.equal(timerCount, 1); // initial and changed notices batch to one map fetch; duplicate versions add none
      queue.schedule();
      queue.schedule();
      assert.equal(timerCount, 1);
      timerCallback();
      assert.equal(fetched, 1);
      queue.schedule();
      queue.stop();
      assert(canceled);
      stop();
      assert(subscriptions.every((subscription) => subscription.stopped));
      const removed = followMapSessions({{
        token: "synthetic-token", versions, sessionIds: ["new-session"],
        follow: () => () => {{}}, onChange: () => {{}}
      }});
      assert(!versions.has("session:0"));
      removed();
      assert.equal(mapCountAnnouncement(3, 5, 0), mapCountAnnouncement(3, 5, 0));
      assert.notEqual(mapCountAnnouncement(3, 5, 0), mapCountAnnouncement(4, 5, 0));
      console.log(JSON.stringify({{ streams: subscriptions.length, changes: changes.length, coalesced: timerCount }}));
    '''
    assert run_node(script) == {"streams": 300, "changes": 2, "coalesced": 2}


def test_refresh_coalescing_fallback_auth_block_and_unmount_cleanup():
    module_url = (JS / "work-map.js").as_uri()
    script = f'''
      import assert from "node:assert/strict";
      import {{ createMapRefreshCoordinator }} from {json.dumps(module_url)};
      const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
      let resolveFirst;
      let calls = 0;
      let timerCallback;
      let timerCleared = false;
      const received = [];
      const coordinator = createMapRefreshCoordinator({{
        interval: 10000,
        setIntervalFn(callback, interval) {{ assert.equal(interval, 10000); timerCallback = callback; return 3; }},
        clearIntervalFn(id) {{ timerCleared = id === 3; }},
        fetchMap() {{
          calls++;
          if (calls === 1) return new Promise((resolve) => {{ resolveFirst = resolve; }});
          return Promise.resolve({{version:"v" + calls}});
        }},
        onData: (value) => received.push(value.version),
        onError: (error) => {{ throw error; }},
        onLoading: () => {{}}
      }});
      coordinator.start();
      await tick();
      coordinator.refresh();
      coordinator.refresh();
      resolveFirst({{version:"v1"}});
      await tick();
      await tick();
      assert.equal(calls, 2);
      assert.deepEqual(received, ["v1", "v2"]);
      timerCallback();
      await tick();
      assert.equal(calls, 3); // ten-second fallback uses the same coalescing request path
      coordinator.stop();
      assert(timerCleared);

      let authCalls = 0;
      let authTick;
      let retry;
      const authBlocked = createMapRefreshCoordinator({{
        setIntervalFn(callback) {{ authTick = callback; return 4; }},
        clearIntervalFn() {{}},
        fetchMap() {{
          authCalls++;
          if (authCalls === 1) return Promise.reject({{status:401}});
          return Promise.resolve({{version:"recovered"}});
        }},
        onData: (value) => {{ retry = value.version; }},
        onError: () => {{}},
        onLoading: () => {{}}
      }});
      authBlocked.start();
      await tick();
      authTick();
      await tick();
      assert.equal(authCalls, 1);
      authBlocked.refresh({{manual:true}});
      await tick();
      assert.equal(authCalls, 2);
      assert.equal(retry, "recovered");
      authBlocked.stop();

      let resolveUnmounted;
      let staleWrites = 0;
      const unmounted = createMapRefreshCoordinator({{
        setIntervalFn: () => 5, clearIntervalFn: () => {{}},
        fetchMap: () => new Promise((resolve) => {{ resolveUnmounted = resolve; }}),
        onData: () => staleWrites++,
        onError: () => staleWrites++,
        onLoading: () => {{}}
      }});
      unmounted.start();
      await tick();
      unmounted.stop();
      resolveUnmounted({{version:"stale"}});
      await tick();
      assert.equal(staleWrites, 0);
      console.log(JSON.stringify({{ coalescedCalls: calls, authCalls, staleWrites }}));
    '''
    assert run_node(script) == {"coalescedCalls": 3, "authCalls": 2, "staleWrites": 0}


def test_children_stream_route_uses_temporary_framework_data_without_executors(tmp_path):
    services = make_services(make_stores(tmp_path))
    try:
        run_id = add_framework_run(services, "gui2-t14-synthetic", events=1, model="synthetic-model")
        token = ensure_token(services.settings.gui_token_file)
        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            headers = {"Authorization": f"Bearer {token}"}
            children = client.get(f"/gui/api/sessions/framework:{run_id}/children", headers=headers)
            response = client.get("/gui/api/stream", params={
                "session": f"framework:{run_id}", "only": "children",
            }, headers=headers)
        assert children.status_code == 200
        assert response.status_code == 200
        block = next(block for block in response.text.split("\n\n") if block.startswith("event: children"))
        event = json.loads(block.split("data: ", 1)[1])
        answer = children.json()
        assert event == {"version": answer["version"], "counts": answer["counts"]}
        assert "event: item" not in response.text
    finally:
        services.runs.close()
