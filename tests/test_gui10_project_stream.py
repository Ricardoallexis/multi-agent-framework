# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Project change notices use the view's team-event cursor and never include private content."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
import time

import pytest

from multiagent.gui import STATIC_DIR, events
from test_gui_project import auth, env, make_project


@pytest.fixture(autouse=True)
def clear_shutdown():
    events.reset_shutdown()
    yield
    events.reset_shutdown()


def test_reader_uses_the_same_sequence_as_the_project_view(env):
    make_project(env, "one", submitted=False)
    make_project(env, "two", submitted=False)
    store = env.services.tasks.store
    view = env.services.projects.get("one")
    assert store.latest_project_seq("one") == view["last_change"]["seq"]
    before = store.latest_project_seq("one")
    env.services.tasks.update_group("two-INV", actor="user:ana", authorized=False)
    assert store.latest_project_seq("one") == before
    env.services.tasks.transition("one-INV-T01", "claim", actor="agent:writer/m1")
    assert store.latest_project_seq("one") == env.services.projects.get("one")["last_change"]["seq"] > before
    assert store.latest_project_seq("absent") is None


def test_stream_is_silent_until_the_project_changes(env):
    make_project(env, "one", submitted=False)
    store = env.services.tasks.store
    initial = store.latest_project_seq("one")

    async def check():
        checked = threading.Event()

        def read():
            checked.set()
            return store.latest_project_seq("one")

        stream = events.stream_project(read, after=initial, interval=0.01, heartbeat=10, max_seconds=0)
        assert await anext(stream) == ": connected\n\n"
        notice_task = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(checked.wait, 1)
        assert not notice_task.done()
        env.services.tasks.transition("one-INV-T01", "claim", actor="agent:writer/m1")
        notice = await asyncio.wait_for(notice_task, 1)
        assert notice.startswith("event: project\ndata: ")
        assert json.loads(notice.split("data: ", 1)[1]) == {"seq": store.latest_project_seq("one")}
        await stream.aclose()

    asyncio.run(check())


def test_open_project_stream_closes_silently_when_archived(env):
    make_project(env, "one", submitted=False)
    store = env.services.tasks.store
    checked = threading.Event()

    def read():
        checked.set()
        return store.latest_project_seq("one")

    async def check():
        stream = events.stream_project(read, after=store.latest_project_seq("one"),
                                       interval=0.01, heartbeat=10, max_seconds=0)
        assert await anext(stream) == ": connected\n\n"
        next_chunk = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(checked.wait, 1)
        assert not next_chunk.done()
        env.services.projects.archive("one", actor="user:ana",
                                      expected_revision=env.services.projects.get("one")["revision"])
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(next_chunk, 1)

    asyncio.run(check())


def test_open_project_stream_ends_within_a_second_of_shutdown(env):
    make_project(env, "one", submitted=False)
    store = env.services.tasks.store
    checked = threading.Event()

    def read():
        checked.set()
        return store.latest_project_seq("one")

    async def check():
        stream = events.stream_project(read, after=store.latest_project_seq("one"),
                                       interval=10, heartbeat=20, max_seconds=0)
        assert await anext(stream) == ": connected\n\n"
        next_chunk = asyncio.create_task(anext(stream))
        assert await asyncio.to_thread(checked.wait, 1)
        assert not next_chunk.done()
        stopped_at = time.monotonic()
        events.request_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(next_chunk, 2)
        assert time.monotonic() - stopped_at < 1

    asyncio.run(check())


def test_hidden_or_unrelated_projects_have_no_notices(env):
    make_project(env, "one", submitted=False)
    make_project(env, "two", submitted=False)
    store = env.services.tasks.store
    before = store.latest_project_seq("one")
    env.services.projects.archive("one", actor="user:ana", expected_revision=env.services.projects.get("one")["revision"])
    assert store.latest_project_seq("one") is None

    async def check():
        stream = events.stream_project(lambda: store.latest_project_seq("one"), after=before,
                                       interval=0.01, heartbeat=10, max_seconds=0.04)
        assert await anext(stream) == ": connected\n\n"
        assert [chunk async for chunk in stream] == []
        other = events.stream_project(lambda: store.latest_project_seq("two"), after=store.latest_project_seq("two"),
                                      interval=0.01, heartbeat=10, max_seconds=0.04)
        assert await anext(other) == ": connected\n\n"
        assert [chunk async for chunk in other] == []

    asyncio.run(check())


def test_project_route_requires_token_and_has_no_content(env, monkeypatch):
    make_project(env, "one", submitted=False)
    path = "/gui/api/stream?project=one&after=0"
    assert env.client.get(path).status_code == 401
    assert env.client.get(path + "oops", headers=auth(env)).status_code == 422
    missing = env.client.get("/gui/api/stream?project=absent", headers=auth(env))
    assert missing.status_code == 200 and missing.text == ""

    for suffix in ("&session=framework%3Aone", "&only=children"):
        invalid = env.client.get(path + suffix, headers=auth(env))
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "invalid_request"
        assert invalid.json()["detail"]["details"] == {"field": "project"}

    cursors = []

    async def one_notice(read, **options):
        cursors.append(options["after"])
        if options["after"] < read():
            yield events.format_notice("project", {"seq": read()})

    monkeypatch.setattr(events, "stream_project", one_notice)
    response = env.client.get(path, headers=auth(env))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert json.loads(response.text.split("data: ", 1)[1]) == {"seq": env.services.projects.get("one")["last_change"]["seq"]}

    resumed_at = env.services.tasks.store.latest_project_seq("one") + 1
    newer = env.client.get(path, headers={**auth(env), "Last-Event-ID": str(resumed_at)})
    assert newer.status_code == 200 and newer.text == ""
    assert cursors == [0, resumed_at]


def test_extension_subscribes_cleans_up_and_polls_on_failure():
    source = (STATIC_DIR / "extensions" / "project" / "index.js").read_text(encoding="utf-8")
    assert 'import { parseBlock } from "../../js/stream.js"' in source
    assert '"/gui/api/stream?project=" + enc(projectId)' in source
    assert 'if (controller) controller.abort()' in source
    assert 'stop();' in source and 'fallback.stop();' in source and 'refreshQueue.stop();' in source
    assert 'setInterval(refresh, PROJECT_POLL_MS)' in source and 'const PROJECT_POLL_MS = 10000' in source
    assert 'const PROJECT_REFRESH_MS = 1000' in source
    assert 'onClick=${() => { loadProject(selected).then(announce)' in source


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_project_stream_client_uses_project_cursor_and_can_stop():
    module = (STATIC_DIR / "extensions" / "project" / "index.js").as_uri()
    script = f"""
      import {{ followProject }} from {json.dumps(module)};
      let request;
      let received;
      const changed = new Promise((resolve) => {{ received = resolve; }});
      globalThis.fetch = async (url, init) => {{
        request = {{ url, signal: init.signal, headers: init.headers }};
        const bytes = new TextEncoder().encode('event: project\\ndata: {{"seq":8}}\\n\\n');
        let first = true;
        return {{ ok: true, body: {{ getReader: () => ({{ read: () => {{
          if (first) {{ first = false; return Promise.resolve({{ done: false, value: bytes }}); }}
          return new Promise(() => {{}});
        }} }}) }} }};
      }};
      const changes = [];
      const stop = followProject({{ token: 'synthetic', projectId: 'a b', after: 7,
        onChange: (seq) => {{ changes.push(seq); received(); }}, onState: () => {{}} }});
      await changed;
      stop();
      console.log(JSON.stringify({{ url: request.url, aborted: request.signal.aborted,
        headers: request.headers, changes }}));
    """
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=STATIC_DIR,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "url": "/gui/api/stream?project=a%20b&after=7", "aborted": True,
        "headers": {"Authorization": "Bearer synthetic", "Accept": "text/event-stream", "Last-Event-ID": "7"},
        "changes": [8],
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_project_stream_reconnects_with_new_cursor_and_stops_after_missing_project():
    module = (STATIC_DIR / "extensions" / "project" / "index.js").as_uri()
    script = f"""
      import {{ followProject }} from {json.dumps(module)};
      const requests = [];
      const states = [];
      const changes = [];
      const delays = [];
      let releaseDelay;
      let reconnecting;
      const firstClosed = new Promise((resolve) => {{ reconnecting = resolve; }});
      let secondClosed;
      const secondReconnecting = new Promise((resolve) => {{ secondClosed = resolve; }});
      globalThis.setTimeout = (fn, delay) => {{ releaseDelay = fn; delays.push(delay); return 1; }};
      globalThis.clearTimeout = () => {{ releaseDelay = null; }};
      globalThis.fetch = async (url, init) => {{
        requests.push({{ url, headers: init.headers }});
        if (requests.length === 3) return {{ status: 200, ok: true,
          body: {{ getReader: () => ({{ read: async () => ({{ done: true }}) }}) }} }};
        const blocks = requests.length === 1
          ? [': connected\\n\\n', 'event: project\\ndata: {{"seq":8}}\\n\\n']
          : [': connected\\n\\n', 'event: project\\ndata: {{"seq":9}}\\n\\n'];
        return {{ status: 200, ok: true, body: {{ getReader: () => ({{
          read: async () => blocks.length
            ? {{ done: false, value: new TextEncoder().encode(blocks.shift()) }} : {{ done: true }}
        }}) }} }};
      }};
      const stop = followProject({{ token: 'synthetic', projectId: 'one', after: 7,
        onChange: (seq) => changes.push(seq), onState: (state) => {{
          states.push(state);
          if (state === 'reconnecting' && requests.length === 1) reconnecting();
          if (state === 'reconnecting' && requests.length === 2) secondClosed();
        }} }});
      await firstClosed;
      if (!releaseDelay) throw Error('no reconnection scheduled');
      releaseDelay();
      await secondReconnecting;
      if (!releaseDelay) throw Error('second reconnection not scheduled');
      releaseDelay();
      await new Promise((resolve) => {{
        const check = () => states.includes('unavailable') ? resolve() : queueMicrotask(check);
        check();
      }});
      if (delays.length !== 2) throw Error('missing project retried');
      stop();
      console.log(JSON.stringify({{ requests, states, changes }}));
    """
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=STATIC_DIR,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [request["url"] for request in data["requests"]] == [
        "/gui/api/stream?project=one&after=7", "/gui/api/stream?project=one&after=8",
        "/gui/api/stream?project=one&after=9",
    ]
    assert [request["headers"]["Last-Event-ID"] for request in data["requests"]] == ["7", "8", "9"]
    assert data["changes"] == [8, 9]
    assert data["states"] == ["connected", "reconnecting", "connected", "reconnecting", "unavailable"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_project_stream_network_failure_reconnects_and_polling_stops():
    module = (STATIC_DIR / "extensions" / "project" / "index.js").as_uri()
    script = f"""
      import {{ followProject, projectPollFallback, projectRefreshQueue }} from {json.dumps(module)};
      let nextTimer = 0;
      const timers = new Map();
      let nextInterval = 0;
      const intervals = new Map();
      let now = 10000;
      Date.now = () => now;
      globalThis.setTimeout = (fn, delay) => {{ const id = ++nextTimer; timers.set(id, {{ fn, delay }}); return id; }};
      globalThis.clearTimeout = (id) => timers.delete(id);
      globalThis.setInterval = (fn, delay) => {{ const id = ++nextInterval; intervals.set(id, {{ fn, delay }}); return id; }};
      globalThis.clearInterval = (id) => intervals.delete(id);
      let calls = 0;
      let failed;
      const failure = new Promise((resolve) => {{ failed = resolve; }});
      let connected;
      const recovery = new Promise((resolve) => {{ connected = resolve; }});
      const seq = {{ current: {{ id: 'one', seq: 7 }} }};
      const refreshes = [];
      const queue = projectRefreshQueue(seq, () => refreshes.push(now));
      const fallback = projectPollFallback(queue.refresh);
      const states = [];
      globalThis.fetch = async () => {{
        calls++;
        if (calls === 1) throw Error('network');
        let first = true;
        return {{ status: 200, ok: true, body: {{ getReader: () => ({{ read: () => {{
          if (first) {{ first = false; return Promise.resolve({{ done: false,
            value: new TextEncoder().encode(': connected\\n\\nevent: project\\ndata: {{"seq":8}}\\n\\n') }}); }}
          return new Promise(() => {{}});
        }} }}) }} }};
      }};
      const stop = followProject({{ token: 'synthetic', projectId: 'one', after: 7,
        onChange: queue.queue, onState: (state) => {{
          states.push(state);
          fallback.onState(state);
          if (state === 'failed') failed();
          if (state === 'connected') connected();
        }} }});
      await failure;
      if (intervals.size !== 1 || [...intervals.values()][0].delay !== 10000) throw Error('poll not active');
      const retry = [...timers.values()][0];
      if (retry.delay !== 1000) throw Error('bad retry delay');
      retry.fn();
      await recovery;
      if (intervals.size !== 0) throw Error('poll still active');
      await new Promise((resolve) => {{
        const check = () => seq.current.seq === 8 ? resolve() : queueMicrotask(check);
        check();
      }});
      const queued = [...timers.values()].at(-1);
      if (queued.delay !== 0) throw Error('first refresh delayed');
      queued.fn();
      now = 10050;
      queue.queue(9);
      const second = [...timers.values()].at(-1);
      if (second.delay !== 950) throw Error('refresh not limited to one second');
      seq.current.seq = 10; // a completed read after a review updates the shared cursor
      queue.queue(10);
      if (timers.size !== 3) throw Error('review event scheduled another refresh');
      fallback.onState('failed');
      fallback.onState('unavailable');
      if (intervals.size !== 0) throw Error('unavailable project kept polling');
      stop(); queue.stop(); fallback.stop();
      console.log(JSON.stringify({{ calls, states, refreshes, intervals: intervals.size }}));
    """
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=STATIC_DIR,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"calls": 2, "states": ["failed", "connected"],
                                         "refreshes": [10000], "intervals": 0}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_project_stream_stops_on_unauthorized_response():
    module = (STATIC_DIR / "extensions" / "project" / "index.js").as_uri()
    script = f"""
      import {{ followProject }} from {json.dumps(module)};
      let count = 0;
      let finished;
      const terminal = new Promise((resolve) => {{ finished = resolve; }});
      globalThis.fetch = async () => {{ count++; return {{ status: 401, ok: false }}; }};
      globalThis.setTimeout = () => {{ throw Error('unauthorized stream retried'); }};
      const stop = followProject({{ token: 'synthetic', projectId: 'one', after: 0,
        onChange: () => {{}}, onState: (state) => {{ if (state === 'unavailable') finished(); }} }});
      await terminal;
      stop();
      console.log(JSON.stringify({{ count }}));
    """
    result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script], cwd=STATIC_DIR,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"count": 1}
