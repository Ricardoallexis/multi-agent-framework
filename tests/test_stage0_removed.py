"""The retired Stage 0 entry points stay unavailable."""
from fastapi.testclient import TestClient
import pytest

from multiagent.api import create_app
from multiagent.cli import build_parser
from multiagent.config import Settings
from multiagent.services import ApplicationServices


def test_stage0_route_and_command_are_removed(tmp_path):
    settings = Settings(_env_file=None, local_dir=tmp_path, worker_enabled=False)
    services = ApplicationServices.create(settings, dry_run=True)
    with TestClient(create_app(services=services), base_url="http://localhost") as client:
        assert client.get("/ui/", follow_redirects=False).status_code == 404
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["ui"])
    assert exc.value.code == 2
