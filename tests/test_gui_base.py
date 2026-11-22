# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI base: serving, headers, token, SSE replay, vendored library."""
import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.gui import STATIC_DIR, security
from multiagent.gui.security import CSP, require_json, require_token
from multiagent.gui.token import ensure_token, read_token
from multiagent.version import __version__

EXPECTED_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; "
    "require-trusted-types-for 'script'"
)


@pytest.fixture
def env(tmp_path):
    settings = Settings(
        _env_file=None, local_dir=tmp_path, worker_enabled=False, mock_mode=True,
        gui_token_file=tmp_path / "gui-token", gui_heartbeat_seconds=0.05,
        gui_poll_seconds=0.02, gui_stream_max_seconds=0.4,
    )
    database = Database(settings)
    database.migrate()
    store = Store(database)
    token = ensure_token(settings.gui_token_file)
    services = SimpleNamespace(system=SimpleNamespace(settings=settings, worker=Mock(), store=store),
                               runs=Mock(), definitions=Mock(),
                               gui_settings=SimpleNamespace(mode=lambda: "simple"))   # SIM-T01: /gui/api/session says the mode
    app = create_app(services=services)

    @app.post("/gui/api/_echo", dependencies=[Depends(require_token), Depends(require_json)])
    def echo():
        return {"ok": True}

    with TestClient(app, base_url="http://localhost") as client:
        yield SimpleNamespace(client=client, token=token, database=database, settings=settings)


def auth(env, **extra):
    return {"Authorization": f"Bearer {env.token}", **extra}


def add_events(env, count, start=0):
    with env.database.connect() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for i in range(start, start + count):
            conn.execute("INSERT INTO run_events(run_id,ts,event_type,payload_json) VALUES(?,?,?,?)",
                         ("run-1", "2026-01-01T00:00:00Z", "step_done", json.dumps({"n": i})))
        conn.commit()


def sse_events(text):
    out = []
    for block in text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "id" in fields:
            out.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return out


def test_routes_and_redirect(env):
    redirect = env.client.get("/gui", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/gui/"
    index = env.client.get("/gui/")
    assert index.status_code == 200 and "text/html" in index.headers["content-type"]
    assert "<script>" not in index.text and "style=" not in index.text
    for path in ("/gui/static/app.css", "/gui/static/js/main.js", "/gui/static/js/stream.js",
                 "/gui/static/vendor/htm-preact-standalone.module.js"):
        assert env.client.get(path).status_code == 200, path
    assert env.client.get("/gui/static/nope.js").status_code == 404


@pytest.mark.parametrize("path,status", [
    ("/gui", 307), ("/gui/", 200), ("/gui/static/app.css", 200), ("/gui/missing", 404),
    ("/gui/api/session", 401), ("/gui/api/stream", 401),
])
def test_security_headers_everywhere_under_gui(env, path, status):
    response = env.client.get(path, follow_redirects=False)
    assert response.status_code == status
    assert response.headers["content-security-policy"] == EXPECTED_CSP == CSP
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"


def test_static_assets_have_no_inline_code():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "<style" not in html
    for js in (STATIC_DIR / "js").glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert "innerHTML" not in text and "dangerouslySetInnerHTML" not in text and "eval(" not in text


def test_token_required(env):
    for path in ("/gui/api/session", "/gui/api/stream"):
        assert env.client.get(path).status_code == 401
        assert env.client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert env.client.get(path, headers={"Authorization": f"Basic {env.token}"}).status_code == 401
    assert env.client.get("/gui/api/session", headers=auth(env)).status_code == 200


def test_missing_token_file_rejects_everything(env):
    env.settings.gui_token_file.unlink()
    assert env.client.get("/gui/api/session", headers={"Authorization": "Bearer "}).status_code == 401
    assert env.client.get("/gui/api/session", headers=auth(env)).status_code == 401


def test_constant_time_compare_used(env, monkeypatch):
    calls = []
    real = security.hmac.compare_digest
    monkeypatch.setattr(security.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert env.client.get("/gui/api/session", headers=auth(env)).status_code == 200
    assert calls and calls[0][1] == env.token.encode()


def test_session_endpoint(env):
    assert env.client.get("/gui/api/session", headers=auth(env)).json() == {"mock": True, "version": __version__, "mode": "simple", "instance_label": ""}


def test_non_json_post_is_415(env):
    for content_type in ("text/plain", "application/x-www-form-urlencoded"):
        r = env.client.post("/gui/api/_echo", content="x", headers=auth(env, **{"Content-Type": content_type}))
        assert r.status_code == 415
    assert env.client.post("/gui/api/_echo", json={}, headers=auth(env)).status_code == 200
    assert env.client.post("/gui/api/_echo", json={}).status_code == 401


@pytest.mark.parametrize("host", ["evil.example", "localhost.evil.example", "10.0.0.5"])
def test_host_rejected_on_gui_paths(env, host):
    for path in ("/gui/", "/gui/static/app.css", "/gui/api/session"):
        r = env.client.get(path, headers=auth(env, Host=host))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_host"


def test_origin_rejected_on_mutating_gui_requests(env):
    r = env.client.post("/gui/api/_echo", json={}, headers=auth(env, Origin="http://evil.example"))
    assert r.status_code == 403
    r = env.client.post("/gui/api/_echo", json={}, headers=auth(env, **{"Sec-Fetch-Site": "cross-site"}))
    assert r.status_code == 403
    r = env.client.post("/gui/api/_echo", json={}, headers=auth(env, Origin="http://localhost"))
    assert r.status_code == 200


def test_stream_replays_after_cursor(env):
    add_events(env, 5)
    r = env.client.get("/gui/api/stream", headers=auth(env))
    assert r.headers["content-type"].startswith("text/event-stream")
    events = sse_events(r.text)
    assert [e[0] for e in events] == [1, 2, 3, 4, 5]
    assert events[0][1] == "step_done" and events[0][2]["run_id"] == "run-1" and events[0][2]["payload"] == {"n": 0}
    later = sse_events(env.client.get("/gui/api/stream?after=3", headers=auth(env)).text)
    assert [e[0] for e in later] == [4, 5]


def test_stream_honors_last_event_id(env):
    add_events(env, 4)
    r = env.client.get("/gui/api/stream", headers=auth(env, **{"Last-Event-ID": "2"}))
    assert [e[0] for e in sse_events(r.text)] == [3, 4]
    r = env.client.get("/gui/api/stream?after=3", headers=auth(env, **{"Last-Event-ID": "1"}))
    assert [e[0] for e in sse_events(r.text)] == [4]


def test_stream_rejects_bad_cursor(env):
    assert env.client.get("/gui/api/stream?after=-1", headers=auth(env)).status_code == 422


def test_stream_heartbeat_when_idle(env):
    r = env.client.get("/gui/api/stream", headers=auth(env))
    assert ": heartbeat" in r.text and not sse_events(r.text)


def test_token_file_creation(tmp_path):
    path = tmp_path / "sub" / "gui-token"
    token = ensure_token(path)
    assert len(token) >= 22 and read_token(path) == token
    assert ensure_token(path) == token  # stable across calls
    other = ensure_token(tmp_path / "other")
    assert other != token
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
    path.write_text("short")
    assert read_token(path) is None and ensure_token(path) != "short"


def test_default_token_path_is_under_home():
    settings = Settings(_env_file=None)
    assert settings.gui_token_file == (Path.home() / ".multiagent" / "gui-token").resolve()


def test_vendored_library_hash_matches_readme():
    vendor = STATIC_DIR / "vendor"
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    # A Windows checkout may turn LF into CRLF; the upstream hash is over the LF bytes.
    data = (vendor / "htm-preact-standalone.module.js").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256(data).hexdigest()
    assert digest in readme
    assert (vendor / "LICENSE-htm.txt").is_file() and (vendor / "LICENSE-preact.txt").is_file()
