# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""S6.B1, S6.B2 / EQP-T04: organization chart model, Mermaid export, service, HTTP and CLI."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from multiagent import cli
from multiagent.api import create_app
from multiagent.config import Settings
from multiagent.services import ApplicationServices, ServiceError
from multiagent.teams.org_chart import build_org_chart, render_mermaid
from multiagent.teams.schema import Team, TeamDefinitionError

ROOT = Path(__file__).resolve().parents[1]
GENERAL_BUNDLE = ROOT / "examples" / "bundles" / "general"
SPEC_REVIEW_BUNDLE = Path(__file__).parent / "fixtures" / "bundles" / "spec_review"


def _settings(local_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        local_dir=local_dir,
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )


def _services(tmp_path: Path, *, bundles: dict[str, Path] | None = None) -> ApplicationServices:
    return ApplicationServices.create(_settings(tmp_path / "local"), bundles=bundles or {})


def _copy_general(tmp_path: Path, name: str = "general") -> Path:
    dest = tmp_path / name
    shutil.copytree(GENERAL_BUNDLE, dest)
    return dest


def _read_team_yaml(bundle_dir: Path) -> dict:
    return yaml.safe_load((bundle_dir / "team.yaml").read_text(encoding="utf-8"))


def _write_team_yaml(bundle_dir: Path, data: dict) -> None:
    (bundle_dir / "team.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _error(call, *args, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        call(*args, **kwargs)
    return caught.value


class _ClientContext:
    def __init__(self, client: TestClient):
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *exc) -> bool:
        return False


def _invoke_cli(monkeypatch, capsys, client: TestClient, args: list[str]) -> str:
    monkeypatch.setattr(cli, "api_client", lambda: _ClientContext(client))
    cli.main(args)
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# Pure module tests (multiagent/teams/org_chart.py)
# ---------------------------------------------------------------------------


def test_single_root_tree_vacants_and_links_separate():
    team = Team.model_validate({
        "schema_version": 1,
        "id": "product_team",
        "roles": [
            {"id": "lead", "purpose": "Owns delivery."},
            {"id": "writer", "purpose": "Writes docs."},
            {"id": "auditor", "purpose": "Audits releases."},
        ],
        "members": [
            {"id": "ana", "kind": "person", "name": "Ana"},
            {"id": "writer_bot", "kind": "agent", "agent": "summary_writer"},
        ],
        "assignments": [
            {"role": "lead", "member": "ana"},
            {"role": "writer", "member": "writer_bot"},
        ],
        "relationships": [
            {"type": "reports_to", "from": "writer", "to": "lead"},
            {"type": "reports_to", "from": "auditor", "to": "lead"},
            {"type": "reviews", "from": "auditor", "to": "writer"},
            {"type": "delegates_to", "from": "lead", "to": "writer"},
        ],
    })

    chart = build_org_chart(team)
    assert chart["team_id"] == "product_team"
    assert chart["root"] == "lead"
    assert chart["roots"] == ["lead"]
    assert chart["forest"] is False

    by_role = {pos["role_id"]: pos for pos in chart["positions"]}
    assert list(by_role) == ["auditor", "lead", "writer"]

    assert by_role["lead"]["status"] == "occupied"
    assert by_role["lead"]["reports_to"] is None
    assert by_role["lead"]["reports"] == ["auditor", "writer"]
    assert by_role["lead"]["members"] == [{"id": "ana", "kind": "person", "name": "Ana"}]

    assert by_role["writer"]["status"] == "occupied"
    assert by_role["writer"]["reports_to"] == "lead"
    assert by_role["writer"]["members"] == [
        {"id": "writer_bot", "kind": "agent", "name": "summary_writer"},
    ]

    assert by_role["auditor"]["status"] == "vacant"
    assert by_role["auditor"]["members"] == []
    assert by_role["auditor"]["reports_to"] == "lead"
    assert by_role["auditor"]["reports"] == []

    # Non-hierarchy links stay separate and never pollute reports_to/reports.
    assert chart["links"] == [
        {"type": "delegates_to", "from": "lead", "to": "writer"},
        {"type": "reviews", "from": "auditor", "to": "writer"},
    ]

    mermaid = render_mermaid(chart)
    assert mermaid.startswith("flowchart TD\n")
    assert 'r0["auditor: vacant"]' in mermaid
    assert 'r1["lead: Ana"]' in mermaid
    assert 'r2["writer: summary_writer"]' in mermaid
    assert "r1 --> r0" in mermaid
    assert "r1 --> r2" in mermaid
    assert "r0 -.->|reviews| r2" in mermaid
    assert "r1 -.->|delegates_to| r2" in mermaid
    assert "classDef occupied " in mermaid
    assert "classDef vacant " in mermaid
    assert "classDef pending " in mermaid
    assert "class r0 vacant" in mermaid
    assert "class r1 occupied" in mermaid


def test_forest_and_empty_team():
    forest_team = Team.model_validate({
        "schema_version": 1,
        "id": "two_roots",
        "roles": [
            {"id": "ops_lead", "purpose": "Operations."},
            {"id": "eng_lead", "purpose": "Engineering."},
            {"id": "qa", "purpose": "Quality."},
        ],
        "relationships": [
            {"type": "reports_to", "from": "qa", "to": "eng_lead"},
            # Mutual non-hierarchy links do not cause cycle errors or alter roots.
            {"type": "reviews", "from": "ops_lead", "to": "eng_lead"},
            {"type": "reviews", "from": "eng_lead", "to": "ops_lead"},
        ],
    })
    chart = build_org_chart(forest_team)
    assert chart["forest"] is True
    assert chart["root"] is None
    assert chart["roots"] == ["eng_lead", "ops_lead"]

    empty_chart = build_org_chart(Team(id="empty"))
    assert empty_chart["team_id"] == "empty"
    assert empty_chart["forest"] is False
    assert empty_chart["root"] is None
    assert empty_chart["roots"] == []
    assert empty_chart["positions"] == []
    assert empty_chart["links"] == []
    assert render_mermaid(empty_chart).startswith("flowchart TD\n")


def test_multiple_managers_and_cycle_validation():
    two_bosses = Team.model_validate({
        "schema_version": 1,
        "id": "bad_tree",
        "roles": [
            {"id": "boss_a", "purpose": "A."},
            {"id": "boss_b", "purpose": "B."},
            {"id": "worker", "purpose": "W."},
        ],
        "relationships": [
            {"type": "reports_to", "from": "worker", "to": "boss_a"},
            {"type": "reports_to", "from": "worker", "to": "boss_b"},
        ],
    })
    with pytest.raises(TeamDefinitionError) as caught_mm:
        build_org_chart(two_bosses)
    assert [issue.code for issue in caught_mm.value.issues] == ["multiple_managers"]

    # Build cyclic input without passing through Team schema validation.
    cyclic_raw = {
        "schema_version": 1,
        "id": "cyclic_team",
        "roles": [
            {"id": "alpha", "purpose": "A."},
            {"id": "beta", "purpose": "B."},
        ],
        "members": [],
        "assignments": [],
        "relationships": [
            {"type": "reports_to", "from": "alpha", "to": "beta"},
            {"type": "reports_to", "from": "beta", "to": "alpha"},
        ],
    }
    with pytest.raises(TeamDefinitionError) as caught_cycle:
        build_org_chart(cyclic_raw)
    assert "reports_to_cycle" in [issue.code for issue in caught_cycle.value.issues]


def test_determinism_and_mermaid_escaping_for_unusual_role_ids():
    raw_a = {
        "id": "escape_team",
        "roles": [
            {"id": 'role "1" <a&b>\nline2', "purpose": "Weird id."},
            {"id": "plain role", "purpose": "Spaces."},
        ],
        "members": [
            {"id": "m2", "kind": "person", "name": 'Bob <"ops">'},
            {"id": "m1", "kind": "agent", "agent": "bot_a"},
        ],
        "assignments": [
            {"role": 'role "1" <a&b>\nline2', "member": "m2"},
            {"role": 'role "1" <a&b>\nline2', "member": "m1"},
        ],
        "relationships": [
            {"type": "reports_to", "from": "plain role", "to": 'role "1" <a&b>\nline2'},
            {"type": "delegates_to", "from": 'role "1" <a&b>\nline2', "to": "plain role"},
        ],
    }
    raw_b = {
        "id": "escape_team",
        "roles": list(reversed(raw_a["roles"])),
        "members": list(reversed(raw_a["members"])),
        "assignments": list(reversed(raw_a["assignments"])),
        "relationships": list(reversed(raw_a["relationships"])),
    }

    chart_a = build_org_chart(raw_a)
    chart_b = build_org_chart(raw_b)
    assert json.dumps(chart_a, sort_keys=True) == json.dumps(chart_b, sort_keys=True)

    mermaid_a = render_mermaid(chart_a)
    mermaid_b = render_mermaid(chart_b)
    assert mermaid_a == mermaid_b

    # Node IDs are safe synthetic r0, r1 rather than raw role IDs.
    assert "r0[" in mermaid_a
    assert "r1[" in mermaid_a
    assert "r1 --> r0" in mermaid_a
    assert "r1 -.->|delegates_to| r0" in mermaid_a
    # Quotes, <, >, & and newlines inside ["..."] are escaped so every node stays on one line.
    node_line = next(line for line in mermaid_a.splitlines() if line.strip().startswith('r1["'))
    assert "&quot;" in node_line
    assert "&lt;" in node_line
    assert "&gt;" in node_line
    assert "&amp;" in node_line
    assert "\\n" in node_line
    assert "\n" not in node_line
    assert '<a&b>' not in node_line


# ---------------------------------------------------------------------------
# Service source resolution, pending overlay and parity across Python/HTTP/CLI
# ---------------------------------------------------------------------------


def test_service_sources_and_pending_overlay(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir, "plain": SPEC_REVIEW_BUNDLE})
    try:
        # Unknown bundle and bundle without team.yaml -> not_found 404.
        assert _error(services.teams.org_chart, "missing").code == "not_found"
        assert _error(services.teams.org_chart, "plain").code == "not_found"

        # 1. No DB versions yet -> validated team.yaml, all positions pending.
        from_yaml = services.teams.org_chart("case")
        assert from_yaml["bundle"] == "case"
        assert from_yaml["team_id"] == "general_team"
        assert from_yaml["source"] == "team.yaml"
        assert from_yaml["version_id"] is None
        assert from_yaml["version"] is None
        assert from_yaml["pending_version_id"] is None
        assert from_yaml["root"] == "lead"
        assert all(pos["status"] == "pending" for pos in from_yaml["positions"])

        # 2. Draft version with no approved/proposed -> source == "draft", all pending.
        draft_v1 = services.teams.propose("case", actor="user:ana", channel="python", draft=True)
        from_draft = services.teams.org_chart("case")
        assert from_draft["source"] == "draft"
        assert from_draft["version_id"] == draft_v1["id"]
        assert from_draft["version"] == 1
        assert all(pos["status"] == "pending" for pos in from_draft["positions"])

        # 3. Submitted (proposed) version with no approved -> source == "proposed", all pending.
        services.teams.submit(draft_v1["id"], "user:ana")
        from_proposed = services.teams.org_chart("case")
        assert from_proposed["source"] == "proposed"
        assert from_proposed["version_id"] == draft_v1["id"]
        assert all(pos["status"] == "pending" for pos in from_proposed["positions"])

        # 4. Approved v1 -> occupied/vacant, no pending_version_id.
        services.teams.approve(draft_v1["id"], actor="user:ana", channel="python")
        from_approved = services.teams.org_chart("case")
        assert from_approved["source"] == "approved"
        assert from_approved["version_id"] == draft_v1["id"]
        assert from_approved["pending_version_id"] is None
        assert all(pos["status"] == "occupied" for pos in from_approved["positions"])

        # 5. Propose v2 that adds a role, changes an occupant, and removes a role.
        data = _read_team_yaml(bundle_dir)
        data["roles"] = [role for role in data["roles"] if role["id"] != "reviewer"]
        data["roles"].append({
            "id": "auditor",
            "purpose": "Audits final deliverables.",
            "rules": ["Verify evidence."],
            "skills": [],
            "contracts": ["ReviewReport"],
            "capabilities": ["doc.review"],
            "permissions": [],
        })
        data["assignments"] = [
            item for item in data["assignments"]
            if not (item["role"] == "reviewer" or (item["role"] == "writer" and item["member"] == "advisor_agent"))
        ]
        data["relationships"] = [
            rel for rel in data["relationships"]
            if rel["from"] != "reviewer" and rel["to"] != "reviewer"
        ]
        data["relationships"].append({"type": "reports_to", "from": "auditor", "to": "lead"})
        _write_team_yaml(bundle_dir, data)

        v2 = services.teams.propose("case", actor="agent:codex", channel="python")

        overlaid = services.teams.org_chart("case")
        assert overlaid["source"] == "approved"
        assert overlaid["version_id"] == draft_v1["id"]
        assert overlaid["version"] == 1
        assert overlaid["pending_version_id"] == v2["id"]

        by_role = {pos["role_id"]: pos for pos in overlaid["positions"]}
        assert by_role["auditor"]["status"] == "pending"
        assert by_role["auditor"]["pending_removal"] is False
        assert by_role["writer"]["status"] == "pending"
        assert [m["id"] for m in by_role["writer"]["members"]] == ["writer_agent"]
        assert by_role["reviewer"]["pending_removal"] is True
        assert by_role["reviewer"]["status"] == "occupied"
        assert by_role["lead"]["status"] == "occupied"
        assert by_role["analyst"]["status"] == "occupied"
        assert "(pending removal)" in overlaid["mermaid"]

        # 6. Explicit version_id returns that version without pending overlay.
        explicit_v1 = services.teams.org_chart("case", version_id=draft_v1["id"])
        assert explicit_v1["source"] == "version"
        assert explicit_v1["version_id"] == draft_v1["id"]
        assert explicit_v1["pending_version_id"] is None
        assert all(pos["status"] == "occupied" and not pos["pending_removal"] for pos in explicit_v1["positions"])

        explicit_v2 = services.teams.org_chart("case", version_id=v2["id"])
        assert explicit_v2["source"] == "version"
        assert explicit_v2["version_id"] == v2["id"]
        assert explicit_v2["pending_version_id"] is None
        assert all(pos["status"] == "pending" for pos in explicit_v2["positions"])
        assert "reviewer" not in {pos["role_id"] for pos in explicit_v2["positions"]}

        # Invalid or missing version_id -> stable errors.
        assert _error(services.teams.org_chart, "case", version_id="bad").code == "invalid_request"
        assert _error(services.teams.org_chart, "case", version_id="tmv-000000000000").code == "not_found"
    finally:
        services.runs.close()


def test_service_rejects_multiple_managers_with_422_invalid_definition(tmp_path: Path):
    bundle_dir = _copy_general(tmp_path)
    data = _read_team_yaml(bundle_dir)
    data["relationships"].append({"type": "reports_to", "from": "writer", "to": "reviewer"})
    _write_team_yaml(bundle_dir, data)

    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        err = _error(services.teams.org_chart, "case")
        assert (err.code, err.status) == ("invalid_definition", 422)
        assert err.details["issues"][0]["code"] == "multiple_managers"
    finally:
        services.runs.close()


def test_org_chart_parity_python_http_and_cli(tmp_path: Path, monkeypatch, capsys):
    bundle_dir = _copy_general(tmp_path)
    services = _services(tmp_path, bundles={"case": bundle_dir})
    try:
        v1 = services.teams.propose("case", actor="user:ana", channel="python")
        services.teams.approve(v1["id"], actor="user:ana", channel="python")

        data = _read_team_yaml(bundle_dir)
        data["roles"].append({"id": "auditor", "purpose": "Audits.", "rules": []})
        data["relationships"].append({"type": "reports_to", "from": "auditor", "to": "lead"})
        _write_team_yaml(bundle_dir, data)
        v2 = services.teams.propose("case", actor="agent:codex", channel="python")

        with TestClient(create_app(services=services), base_url="http://localhost") as client:
            # Default (approved + pending v2)
            py_default = services.teams.org_chart("case")
            http_default = client.get("/api/v1/teams/case/org-chart")
            assert http_default.status_code == 200
            assert http_default.json() == py_default

            cli_default_json = json.loads(_invoke_cli(monkeypatch, capsys, client, ["team", "org-chart", "case"]))
            assert cli_default_json == py_default

            cli_default_mermaid = _invoke_cli(
                monkeypatch, capsys, client, ["team", "org-chart", "case", "--mermaid"],
            )
            assert cli_default_mermaid.rstrip("\n") == py_default["mermaid"]

            # Explicit --version
            py_ver = services.teams.org_chart("case", version_id=v2["id"])
            http_ver = client.get("/api/v1/teams/case/org-chart", params={"version_id": v2["id"]})
            assert http_ver.status_code == 200
            assert http_ver.json() == py_ver

            cli_ver_json = json.loads(
                _invoke_cli(monkeypatch, capsys, client, ["team", "org-chart", "case", "--version", v2["id"]]),
            )
            assert cli_ver_json == py_ver

            cli_ver_mermaid = _invoke_cli(
                monkeypatch, capsys, client, ["team", "org-chart", "case", "--version", v2["id"], "--mermaid"],
            )
            assert cli_ver_mermaid.rstrip("\n") == py_ver["mermaid"]

            # Direct _api_json double verification for CLI routing
            recorded: list[tuple[str, str, dict | None]] = []

            def fake_api_json(method: str, path: str, *, params=None, **_kwargs):
                recorded.append((method, path, params))
                return py_ver

            monkeypatch.setattr(cli, "_api_json", fake_api_json)
            cli.main(["team", "org-chart", "case"])
            cli.main(["team", "org-chart", "case", "--version", v2["id"], "--mermaid"])
            assert recorded == [
                ("GET", "/api/v1/teams/case/org-chart", None),
                ("GET", "/api/v1/teams/case/org-chart", {"version_id": v2["id"]}),
            ]
    finally:
        services.runs.close()
