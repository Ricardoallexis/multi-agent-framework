# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""UI policy and resource compatibility, without workers or provider calls."""
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from multiagent.api import create_app
from multiagent.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False)
    definitions = Mock()
    definitions.list_workflows.return_value = []
    services = SimpleNamespace(
        system=SimpleNamespace(settings=settings, worker=Mock()),
        runs=Mock(), definitions=definitions,
    )
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        yield client


@pytest.mark.parametrize("method,path,status", [
    ("GET", "/ui", 307),
    ("GET", "/ui/", 200),
    ("GET", "/ui/static/app.css", 200),
    ("GET", "/ui/static/js/main.js", 200),
    ("GET", "/ui/static/js/create-run.js", 200),
    ("GET", "/ui/static/js/run-view.js", 200),
    ("GET", "/ui/static/js/human-actions.js", 200),
    ("GET", "/ui/static/js/api.js", 200),
    ("GET", "/ui/static/missing.js", 404),
    ("GET", "/ui/missing", 404),
    ("POST", "/ui/", 405),
])
def test_ui_responses_carry_restrictive_policy(client, method, path, status):
    response = client.request(method, path, follow_redirects=False)
    assert response.status_code == status
    policy = response.headers["content-security-policy"]
    directives = dict(
        (parts[0], parts[1:])
        for item in policy.split(";") if (parts := item.strip().split())
    )
    for name in ("default-src", "script-src", "style-src", "connect-src", "img-src", "font-src", "form-action"):
        assert directives[name] == ["'self'"]
    for name in ("object-src", "frame-src", "frame-ancestors", "base-uri"):
        assert directives[name] == ["'none'"]
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_ui_policy_also_wraps_host_guard_errors(client):
    response = client.get("/ui/", headers={"host": "untrusted.example"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_host"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/api/v1/workflows", "/docs", "/openapi.json", "/ui-other"])
def test_policy_is_scoped_to_ui_routes(client, path):
    response = client.get(path)
    assert "content-security-policy" not in response.headers


class PageResources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.stylesheets = []
        self.script_depth = 0
        self.inline_code = []
        self.unsafe_attributes = []
        self.style_elements = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.unsafe_attributes.extend(
            name for name in attrs if name.startswith("on") or name == "style"
        )
        if tag == "script":
            self.scripts.append(attrs)
            self.script_depth += 1
        if tag == "style":
            self.style_elements += 1
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "script":
            self.script_depth -= 1

    def handle_data(self, data):
        if self.script_depth and data.strip():
            self.inline_code.append(data)


def test_page_loads_external_modules_and_styles_under_policy(client):
    page = PageResources()
    page.feed(client.get("/ui/").text)
    assert not page.inline_code
    assert not page.unsafe_attributes
    assert not page.style_elements
    assert page.scripts == [{"type": "module", "src": "/ui/static/js/main.js"}]
    assert page.stylesheets
    for script in page.scripts:
        response = client.get(script["src"])
        assert response.status_code == 200
        assert response.headers["content-type"].split(";")[0] in {
            "text/javascript", "application/javascript",
        }
    for path in page.stylesheets:
        assert path.startswith("/ui/static/")
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")
