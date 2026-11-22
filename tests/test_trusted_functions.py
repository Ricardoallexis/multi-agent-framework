# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PAQ-T03: the functions of a package load only with explicit trust, after the scan, once per fingerprint.

Every package is written in ``tmp_path``; nothing outside it is imported.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from multiagent import cli
from multiagent.config import Settings
from multiagent.functions import (function_owner, load_trusted_functions, loaded_functions, register_function,
                                  unregister_owner)
from multiagent.gui import __main__ as gui_main
from multiagent.services import ApplicationServices, ServiceError
from multiagent.services.installed import FUNCTIONS_FIX_TEXT

FIXTURE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"
ACTOR = "user:local"

CONTRACTS = '''from pydantic import BaseModel, ConfigDict


class Collected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: dict


OUTPUT_SCHEMAS = {"Collected": Collected}
'''
WORKFLOW = """id: demo_flow
max_iterations: 1
steps:
  - id: collect
    function: demo.collect
    contract: Collected
"""


def functions_source(marker: Path, *, ids=("demo.collect",), extra: str = "") -> str:
    lines = ["from pathlib import Path", "from multiagent.functions import register_function", "",
             f"with Path({marker.as_posix()!r}).open('a', encoding='utf-8') as stream:", "    stream.write('imported\\n')",
             "", "def collect(context, arguments):", "    return {'steps': {}}", ""]
    lines += [f"register_function({function_id!r}, collect, description='Collect')" for function_id in ids]
    return "\n".join(lines) + "\n" + extra


def make_package(destination: Path, source: str | None) -> Path:
    shutil.copytree(FIXTURE, destination)
    (destination / "contracts.py").write_text(CONTRACTS, encoding="utf-8")
    shutil.rmtree(destination / "workflows")
    (destination / "workflows").mkdir()
    (destination / "workflows" / "demo_flow.yaml").write_text(WORKFLOW, encoding="utf-8")
    if source is not None:
        (destination / "functions.py").write_text(source, encoding="utf-8")
    return destination


def make_settings(root: Path) -> Settings:
    return Settings(_env_file=None, local_dir=root, worker_enabled=False, mock_mode=True,
                    gemini_api_key="", openai_api_key="", openai_enabled=False,
                    gui_token_file=root.parent / "gui-token")


@pytest.fixture
def world(tmp_path):
    opened: list[ApplicationServices] = []

    def start(bundles=None, *, trust=(), root: Path | None = None) -> ApplicationServices:
        services = ApplicationServices.create(make_settings(root or tmp_path / "local"), bundles=bundles,
                                              trust_functions=trust, dry_run=True)
        opened.append(services)
        return services

    yield start, tmp_path
    for services in opened:
        services.bundle_scans.close()
        services.runs.close()
    unregister_owner("demo")
    unregister_owner("other")


def imports(marker: Path) -> int:
    return len(marker.read_text(encoding="utf-8").split()) if marker.exists() else 0


def test_functions_not_imported_without_trust(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(marker))
    services = start({"demo": package})
    with pytest.raises(ServiceError) as caught:
        services.definitions.validate_bundle("demo")
    assert not marker.exists()
    assert function_owner("demo.collect") is None
    issues = caught.value.details["workflows"][0]["issues"]
    assert [issue["code"] for issue in issues] == ["unknown_function"]
    assert issues[0]["fix"] == FUNCTIONS_FIX_TEXT
    # the same entry of an installed package: present, not trusted, nothing loaded
    installed = services.installed.install_folder("copy", package, actor=ACTOR)
    assert installed["functions"] == {"present": True, "trusted": False, "loaded": []}
    assert not marker.exists()


def test_trust_flag_loads_after_require_load(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(marker))
    services = start({"demo": package}, trust=("demo",))
    order: list[str] = []
    real = services.bundle_scans.require_load

    def gate(name):
        order.append("require_load")
        assert not marker.exists(), "the code ran before the scan"
        if len(order) == 1:
            raise ServiceError("bundle_scan_pending", "Approve in the GUI", status=409)
        return real(name)

    services.bundle_scans.require_load = gate
    with pytest.raises(ServiceError) as pending:
        services.definitions.validate_bundle("demo")
    assert pending.value.code == "bundle_scan_pending" and not marker.exists()
    result = services.definitions.validate_bundle("demo")
    assert result["valid"] is True and result["workflows"] == ["demo_flow"]
    assert imports(marker) == 1 and function_owner("demo.collect") == "demo"


def test_loaded_once_per_fingerprint(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(marker))
    services = start({"demo": package}, trust=("demo",))
    services.definitions.validate_bundle("demo")
    services.definitions.validate_bundle("demo")
    assert imports(marker) == 1
    # a new fingerprint: the old functions go and the new file is imported
    (package / "functions.py").write_text(functions_source(marker, ids=("demo.collect", "demo.extra")),
                                          encoding="utf-8")
    services.definitions.validate_bundle("demo")
    assert imports(marker) == 2
    assert loaded_functions("demo") == ["demo.collect", "demo.extra"]
    (package / "functions.py").write_text(functions_source(marker), encoding="utf-8")
    services.definitions.validate_bundle("demo")
    assert imports(marker) == 3 and loaded_functions("demo") == ["demo.collect"]
    assert function_owner("demo.extra") is None


def test_function_conflict(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(
        marker, ids=("demo.collect", "core.merge_outputs")))
    services = start({"demo": package}, trust=("demo",))
    with pytest.raises(ServiceError) as caught:
        services.definitions.validate_bundle("demo")
    error = caught.value
    assert (error.status, error.code) == (409, "invalid_state")
    assert error.details["reason"] == "function_conflict"
    assert error.details["function_id"] == "core.merge_outputs" and error.details["owner"] == "core"
    assert function_owner("demo.collect") is None  # what this import had registered is gone
    assert function_owner("core.merge_outputs") == "core"


def test_import_failure_has_no_trace(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(
        marker, extra='raise RuntimeError("secreto")\n'))
    services = start({"demo": package}, trust=("demo",))
    with pytest.raises(ServiceError) as caught:
        services.definitions.validate_bundle("demo")
    error = caught.value
    assert error.details["reason"] == "functions_import_failed" and error.details["error_type"] == "RuntimeError"
    text = json.dumps(error.to_payload()) + str(error)
    assert "RuntimeError" in text and "secreto" not in text and "Traceback" not in text
    assert function_owner("demo.collect") is None


def test_reinstall_resets_trust(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(marker))
    services = start()
    entry = services.installed.install_folder("demo", package, actor=ACTOR, trust_functions=True)
    assert entry["trust_functions"] is True and entry["functions"]["trusted"] is True
    assert services.definitions.validate_bundle("demo")["valid"] is True
    assert services.installed.get("demo")["functions"]["loaded"] == ["demo.collect"]
    (package / "functions.py").write_text(functions_source(marker, ids=("demo.collect", "demo.more")),
                                          encoding="utf-8")
    again = services.installed.reinstall("demo", package, actor=ACTOR)
    assert again["trust_functions"] is False
    assert again["functions"] == {"present": True, "trusted": False, "loaded": []}
    with pytest.raises(ServiceError):
        services.definitions.validate_bundle("demo")
    assert imports(marker) == 1  # the new copy was never imported


def test_registry_trust_survives_a_restart_and_loads(world):
    start, tmp = world
    marker = tmp / "marker.txt"
    package = make_package(tmp / "demo", functions_source(marker))
    first = start(root=tmp / "shared")
    first.installed.install_folder("demo", package, actor=ACTOR, trust_functions=True)
    unregister_owner("demo")
    second = start(root=tmp / "shared")
    assert second.installed.get("demo")["functions"]["loaded"] == []
    assert second.definitions.validate_bundle("demo")["valid"] is True
    assert imports(marker) == 1


def test_trust_flag_for_an_unregistered_name_stops_the_start(world):
    start, tmp = world
    with pytest.raises(ValueError, match="unknown bundle for --trust-functions: nada"):
        start(trust=("nada",))


def test_cli_trust_functions_unknown_name(tmp_path, monkeypatch, capsys):
    # The token goes under a fake home: on Linux tmp_path sits in /tmp, outside the real home, and the
    # token check would stop the start before the trust check this test is about.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.delenv("GUI_TOKEN_OUTSIDE_HOME", raising=False)
    monkeypatch.setenv("LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("GUI_TOKEN_FILE", str(home / ".multiagent" / "gui-token"))
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setattr(cli, "_check_port_free", lambda host, port: None)
    monkeypatch.setattr(gui_main, "serve", lambda *args, **kwargs: pytest.fail("the server must not start"))
    with pytest.raises(SystemExit) as caught:
        cli.main(["gui", "--no-browser", "--trust-functions", "nada"])
    assert caught.value.code == 1
    assert "ERROR: unknown bundle for --trust-functions: nada" in capsys.readouterr().err


def test_cli_trust_functions_is_repeatable(monkeypatch):
    seen = {}
    monkeypatch.setattr(gui_main, "run", lambda **kwargs: seen.update(kwargs))
    cli.main(["gui", "--no-browser", "--trust-functions", "a", "--trust-functions", "b"])
    assert seen["trust_functions"] == ("a", "b")
    cli.main(["gui", "--no-browser"])
    assert seen["trust_functions"] == ()


def test_owner_rules():
    assert function_owner("core.merge_outputs") == "core"
    assert unregister_owner("core") == []
    assert function_owner("core.merge_outputs") == "core"
    register_function("other.thing", lambda context, arguments: {}, description="Thing", owner="other")
    try:
        assert function_owner("other.thing") == "other"
        with pytest.raises(ValueError, match="already registered.*other"):
            register_function("other.thing", lambda context, arguments: {}, description="Again")
    finally:
        assert unregister_owner("other") == ["other.thing"]
    assert function_owner("other.thing") is None


def test_package_cannot_register_as_core(tmp_path):
    marker = tmp_path / "marker.txt"
    package = make_package(tmp_path / "demo", functions_source(
        marker, extra="register_function('demo.claim', collect, description='Claim', owner='core')\n"))
    try:
        ids = load_trusted_functions("demo", package, "f1")
        assert ids == ["demo.claim", "demo.collect"]
        assert function_owner("demo.claim") == "demo"
        assert load_trusted_functions("demo", package, "f1") == ids and imports(marker) == 1
    finally:
        unregister_owner("demo")
