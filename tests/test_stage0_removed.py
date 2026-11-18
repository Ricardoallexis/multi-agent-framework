# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The retired /ui/ route, multiagent.ui package and 'multiagent ui' command stay unavailable."""
import importlib.util

from fastapi.testclient import TestClient
import pytest

from multiagent.api import create_app
from multiagent.cli import build_parser
from multiagent.config import Settings
from multiagent.services import ApplicationServices


def test_stage0_route_and_command_are_removed(tmp_path):
    spec = importlib.util.find_spec("multiagent.ui")
    assert spec is None or spec.origin is None   # a leftover __pycache__ folder is at most an empty namespace
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        assert client.get("/ui", follow_redirects=False).status_code == 404
        assert client.get("/ui/", follow_redirects=False).status_code == 404
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307 and root.headers["location"] == "/gui/"
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["ui"])
    assert exc.value.code == 2
