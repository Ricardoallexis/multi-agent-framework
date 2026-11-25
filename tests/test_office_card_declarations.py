# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""OFA-T01: office cards separate validated declarations from untrusted code."""
from __future__ import annotations

import json

import pytest
import yaml

from multiagent.services import ApplicationServices, ServiceError
from test_offices_install import make_settings, write_office


def _services(tmp_path, *, file_tools_root=None):
    settings = make_settings(tmp_path / "local").model_copy(update={"file_tools_root": file_tools_root})
    return ApplicationServices.create(settings, dry_run=True)


def _add_file_tool_workflow(area):
    workflow_path = area / "workflows" / "spec_review.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow["steps"][0]["tools"] = [
        {"tool": "file.read", "arguments": {"path": "notes.txt"}},
        {"capability": "file.write", "arguments": {"path": "draft.txt", "content": "${topic}"}},
    ]
    workflow_path.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")


def test_card_separates_contracts_workflows_roles_and_static_function_ids(tmp_path):
    services = _services(tmp_path)
    try:
        folder = write_office(tmp_path / "office")
        area = folder / "areas" / "reception"
        _add_file_tool_workflow(area)
        contracts = {"schema": "maf.contracts/1", "contracts": {
            "YamlOutput": {"fields": {"answer": {"type": "string"}}},
        }}
        (area / "contracts.yaml").write_text(yaml.safe_dump(contracts), encoding="utf-8")
        contracts_marker = tmp_path / "contracts-ran"
        (area / "contracts.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(contracts_marker)!r}).write_text('ran')\n"
            "class Helper:\n"
            "    pass\n"
            "class PythonOutput:\n"
            "    pass\n"
            "OUTPUT_SCHEMAS = {'PythonOutput': PythonOutput}\n",
            encoding="utf-8",
        )
        functions_marker = tmp_path / "functions-ran"
        (area / "functions.py").write_text(
            "from pathlib import Path\n"
            "from multiagent.functions import register_function\n"
            f"Path({str(functions_marker)!r}).write_text('ran')\n"
            "def helper(context, arguments):\n"
            "    return {}\n"
            "register_function('demo.static', helper, description='Static registration')\n"
            "dynamic_id = 'demo.dynamic'\n"
            "register_function(dynamic_id, helper, description='Dynamic registration')\n",
            encoding="utf-8",
        )
        role_workflow = area / "workflows" / "role_task.yaml"
        workflow = yaml.safe_load((area / "workflows" / "spec_review.yaml").read_text(encoding="utf-8"))
        workflow["id"] = "role_task"
        workflow["steps"][0]["id"] = "role_step"
        workflow["steps"][0]["role"] = "analyst"
        workflow["steps"][0].pop("tools")
        role_workflow.write_text(yaml.safe_dump(workflow, sort_keys=False), encoding="utf-8")

        services.gui_settings.set_mode("advanced")
        card = services.offices.stage_folder(folder)
        area_card = next(item for item in card["areas"] if item["id"] == "reception")
        workflow_declarations = {item["id"]: item for item in area_card["workflow_declarations"]}

        assert area_card["declares"] == {
            "workflows": ["role_task", "spec_review"],
            "contracts": ["YamlOutput"],
            "roles": ["analyst"],
        }
        assert area_card["declares"]["contracts"] == ["YamlOutput"]
        assert workflow_declarations["spec_review"] == {
            "id": "spec_review", "roles": [], "file_tools": ["file.read", "file.write"],
        }
        assert workflow_declarations["role_task"]["roles"] == ["analyst"]
        assert card["code"]["functions"] == ["demo.static"]
        assert card["code"]["functions_unverified"] is True
        assert card["code"]["python_contracts"] == ["PythonOutput"]
        assert card["code"]["python_contracts_unverified"] is True
        assert "Helper" not in card["code"]["python_contracts"]
        assert "helper" not in card["code"]["functions"]
        assert not contracts_marker.exists() and not functions_marker.exists()
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_file_tool_requirement_reports_only_whether_root_is_configured(tmp_path):
    file_tools_root = tmp_path / "private-workspace"
    file_tools_root.mkdir()
    services = _services(tmp_path, file_tools_root=file_tools_root)
    try:
        folder = write_office(tmp_path / "office")
        _add_file_tool_workflow(folder / "areas" / "reception")
        (folder / "areas" / "reception" / "review.py").write_text("value = 1\n", encoding="utf-8")
        services.gui_settings.set_mode("advanced")

        card = services.offices.stage_folder(folder)

        assert card["file_tools_configured"] is True
        assert "file_tools_configured" in card
        assert str(file_tools_root) not in json.dumps(card)
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_file_tool_requirement_is_reported_when_root_is_missing(tmp_path):
    services = _services(tmp_path)
    try:
        folder = write_office(tmp_path / "office")
        _add_file_tool_workflow(folder / "areas" / "reception")
        (folder / "areas" / "reception" / "review.py").write_text("value = 1\n", encoding="utf-8")
        services.gui_settings.set_mode("advanced")

        card = services.offices.stage_folder(folder)

        assert card["file_tools_configured"] is False
        assert any(item["file_tools"] == ["file.read", "file.write"]
                   for area in card["areas"] for item in area["workflow_declarations"])
    finally:
        services.bundle_scans.close()
        services.runs.close()


@pytest.mark.parametrize("filename", ["functions.py", "contracts.py"])
@pytest.mark.parametrize("invalid_source", ["utf8", "syntax"])
def test_invalid_python_office_declaration_returns_422_without_execution(tmp_path, filename, invalid_source):
    services = _services(tmp_path)
    try:
        folder = write_office(tmp_path / "office")
        declaration = folder / "areas" / "reception" / filename
        marker = tmp_path / "executed"
        prefix = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n".encode()
        if invalid_source == "utf8":
            declaration.write_bytes(prefix + b"\xff\n")
        else:
            declaration.write_bytes(prefix + b"if:\n    pass\n")
        services.gui_settings.set_mode("advanced")

        with pytest.raises(ServiceError) as caught:
            services.offices.stage_folder(folder)

        assert caught.value.status == 422
        assert caught.value.details["reason"] == "office_code_invalid"
        assert not marker.exists()
    finally:
        services.bundle_scans.close()
        services.runs.close()
