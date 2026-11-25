# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Pure schema and path-safety validation for office.yaml (maf.office/1)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from multiagent.offices.schema import (
    OFFICE_ID_PATTERN,
    OFFICE_SCHEMA,
    RELATION_KINDS,
    RELATION_LABELS,
    SENSITIVITIES,
    OfficeArea,
    OfficeDefinition,
    OfficeDefinitionError,
    Orchestrator,
    PlanItem,
    Relation,
    area_bundle_name,
    load_office,
)


def _write_office(
    root: Path,
    data: dict,
    *,
    area_dirs: tuple[str, ...] = ("areas/reception",),
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel in area_dirs:
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "office.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return root


def _minimal_office_dict() -> dict:
    return {
        "schema": OFFICE_SCHEMA,
        "id": "demo",
        "name": "Demo Office",
        "areas": [
            {
                "id": "reception",
                "name": "Reception",
                "path": "areas/reception",
            }
        ],
    }


def _load_error(root: Path) -> OfficeDefinitionError:
    with pytest.raises(OfficeDefinitionError) as caught:
        load_office(root)
    return caught.value


def test_minimal_office_loads(tmp_path: Path):
    root = _write_office(tmp_path / "demo", _minimal_office_dict())
    office = load_office(root)

    assert isinstance(office, OfficeDefinition)
    assert office.id == "demo"
    assert office.name == "Demo Office"
    assert office.local_only is False
    assert isinstance(office.orchestrator, Orchestrator)
    assert office.orchestrator.enabled is False
    assert office.orchestrator.default_plan == []
    assert len(office.areas) == 1
    assert isinstance(office.areas[0], OfficeArea)
    assert office.areas[0].id == "reception"
    assert office.areas[0].path == "areas/reception"
    assert office.relations == []
    assert office.warnings == []
    assert area_bundle_name("demo", "reception") == "demo__reception"
    assert OFFICE_SCHEMA == "maf.office/1"
    assert OFFICE_ID_PATTERN == r"^[a-z0-9][a-z0-9-]{0,39}$"
    assert SENSITIVITIES == ("normal", "sensitive")


@pytest.mark.parametrize("valid_id", ["a", "estudio-ejemplo", "a" * 40])
def test_ids_follow_the_pattern_valid(tmp_path: Path, valid_id: str):
    data = _minimal_office_dict()
    data["id"] = valid_id
    data["areas"][0]["id"] = valid_id
    root = _write_office(tmp_path / f"ok-{valid_id}", data)
    office = load_office(root)
    assert office.id == valid_id
    assert office.areas[0].id == valid_id


@pytest.mark.parametrize("bad_id", ["Estudio", "a_b", "-a", "a" * 41])
def test_ids_follow_the_pattern(tmp_path: Path, bad_id: str):
    office_data = _minimal_office_dict()
    office_data["id"] = bad_id
    err = _load_error(_write_office(tmp_path / f"bad-off-{len(bad_id)}-{bad_id}", office_data))
    assert any(issue["code"] == "office_invalid" for issue in err.issues)

    area_data = _minimal_office_dict()
    area_data["areas"][0]["id"] = bad_id
    err_area = _load_error(_write_office(tmp_path / f"bad-area-{len(bad_id)}-{bad_id}", area_data))
    assert any(issue["code"] == "office_invalid" for issue in err_area.issues)


@pytest.mark.parametrize("bad_path", ["/abs", "../x", "a/../../x", "a\\b", "C:/x"])
def test_paths_must_stay_inside(tmp_path: Path, bad_path: str):
    data = _minimal_office_dict()
    data["areas"][0]["path"] = bad_path
    root = _write_office(tmp_path / "unsafe", data, area_dirs=())
    err = _load_error(root)
    assert any(issue["code"] == "office_path_unsafe" for issue in err.issues)


def test_paths_must_stay_inside_symlink(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = _write_office(tmp_path / "sym", _minimal_office_dict(), area_dirs=("areas",))
    link_path = root / "areas" / "reception"
    try:
        os.symlink(outside, link_path, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation not permitted on this system: {exc}")

    err = _load_error(root)
    assert any(issue["code"] == "office_path_unsafe" for issue in err.issues)


def test_duplicate_ids_rejected(tmp_path: Path):
    dup_areas = _minimal_office_dict()
    dup_areas["areas"].append({
        "id": "reception",
        "name": "Reception Two",
        "path": "areas/reception2",
    })
    err_areas = _load_error(_write_office(
        tmp_path / "dup-areas",
        dup_areas,
        area_dirs=("areas/reception", "areas/reception2"),
    ))
    assert any(issue["code"] == "office_duplicate_id" for issue in err_areas.issues)

    dup_plan = _minimal_office_dict()
    dup_plan["orchestrator"] = {
        "enabled": True,
        "name": "Coordinación",
        "default_plan": [
            {"area": "reception", "workflow": "wf_one"},
            {"area": "reception", "workflow": "wf_two"},
        ],
    }
    err_plan = _load_error(_write_office(tmp_path / "dup-plan", dup_plan))
    assert any(issue["code"] == "office_duplicate_id" for issue in err_plan.issues)


def test_relation_to_missing_area_is_a_warning(tmp_path: Path):
    data = _minimal_office_dict()
    data["relations"] = [
        {"from": "reception", "to": "proposal", "kind": "hands_off"},
    ]
    office = load_office(_write_office(tmp_path / "rel-warn", data))
    assert len(office.relations) == 1
    assert isinstance(office.relations[0], Relation)
    assert office.relations[0].from_ == "reception"
    assert office.relations[0].to == "proposal"
    assert len(office.warnings) == 1
    assert office.warnings[0]["code"] == "office_relation_unknown_area"
    assert office.warnings[0]["location"] == "relations[0].to"


def test_relation_kinds(tmp_path: Path):
    assert RELATION_KINDS == ("hands_off", "reviews", "consults")
    assert RELATION_LABELS["reviews"] == "revisa"
    assert set(RELATION_LABELS) == set(RELATION_KINDS)

    for kind in RELATION_KINDS:
        data = _minimal_office_dict()
        data["areas"].append({"id": "proposal", "name": "Proposal", "path": "areas/proposal"})
        data["relations"] = [{"from": "reception", "to": "proposal", "kind": kind}]
        office = load_office(_write_office(
            tmp_path / f"rel-{kind}",
            data,
            area_dirs=("areas/reception", "areas/proposal"),
        ))
        assert office.relations[0].kind == kind
        assert office.warnings == []

    bad = _minimal_office_dict()
    bad["relations"] = [{"from": "reception", "to": "reception", "kind": "entrega"}]
    err = _load_error(_write_office(tmp_path / "rel-bad", bad))
    assert any(issue["code"] == "office_invalid" for issue in err.issues)


def test_default_plan_references(tmp_path: Path):
    good = _minimal_office_dict()
    good["orchestrator"] = {
        "enabled": True,
        "name": "Coordinación",
        "default_plan": [
            {"area": "reception", "workflow": "tomar_pedido"},
            {"id": "reception-2", "area": "reception", "workflow": "confirmar", "after": ["reception"]},
        ],
    }
    office = load_office(_write_office(tmp_path / "plan-ok", good))
    assert [item.id for item in office.orchestrator.default_plan] == ["reception", "reception-2"]
    assert isinstance(office.orchestrator.default_plan[0], PlanItem)

    bad_area = _minimal_office_dict()
    bad_area["orchestrator"] = {
        "enabled": True,
        "default_plan": [{"area": "missing", "workflow": "tomar_pedido"}],
    }
    err_area = _load_error(_write_office(tmp_path / "plan-bad-area", bad_area))
    assert any(issue["code"] == "office_invalid" for issue in err_area.issues)

    bad_after = _minimal_office_dict()
    bad_after["orchestrator"] = {
        "enabled": True,
        "default_plan": [{"area": "reception", "workflow": "tomar_pedido", "after": ["missing"]}],
    }
    err_after = _load_error(_write_office(tmp_path / "plan-bad-after", bad_after))
    assert any(issue["code"] == "office_invalid" for issue in err_after.issues)


def test_requires_are_capability_ids(tmp_path: Path):
    good = _minimal_office_dict()
    good["areas"][0]["requires"] = ["model.text", "model.structured"]
    office = load_office(_write_office(tmp_path / "caps-ok", good))
    assert office.areas[0].requires == frozenset({"model.text", "model.structured"})

    bad = _minimal_office_dict()
    bad["areas"][0]["requires"] = ["text_generation"]
    err = _load_error(_write_office(tmp_path / "caps-bad", bad))
    assert any(issue["code"] == "office_invalid" for issue in err.issues)


def test_optional_fields(tmp_path: Path):
    minimal = load_office(_write_office(tmp_path / "opt-min", _minimal_office_dict()))
    assert minimal.areas[0].sensitivity is None
    assert minimal.areas[0].local_only is False
    assert minimal.areas[0].team_sha256 is None
    assert minimal.local_only is False
    assert minimal.description is None

    full = _minimal_office_dict()
    full["description"] = "A test office"
    full["local_only"] = True
    full["areas"][0]["sensitivity"] = "sensitive"
    full["areas"][0]["local_only"] = True
    full["areas"][0]["team_sha256"] = "a" * 64
    loaded_full = load_office(_write_office(tmp_path / "opt-full", full))
    assert loaded_full.local_only is True
    assert loaded_full.description == "A test office"
    assert loaded_full.areas[0].sensitivity == "sensitive"
    assert loaded_full.areas[0].local_only is True
    assert loaded_full.areas[0].team_sha256 == "a" * 64

    bad_sha = _minimal_office_dict()
    bad_sha["areas"][0]["team_sha256"] = "a" * 63
    err = _load_error(_write_office(tmp_path / "opt-bad-sha", bad_sha))
    assert any(issue["code"] == "office_invalid" for issue in err.issues)


def test_schema_and_unknown_keys(tmp_path: Path):
    bad_schema = _minimal_office_dict()
    bad_schema["schema"] = "maf.office/2"
    err_schema = _load_error(_write_office(tmp_path / "bad-schema", bad_schema))
    assert any(issue["code"] == "office_invalid" for issue in err_schema.issues)

    extra_root = _minimal_office_dict()
    extra_root["unexpected"] = True
    err_root = _load_error(_write_office(tmp_path / "extra-root", extra_root))
    assert any(issue["code"] == "office_invalid" for issue in err_root.issues)

    extra_area = _minimal_office_dict()
    extra_area["areas"][0]["unexpected"] = 1
    err_area = _load_error(_write_office(tmp_path / "extra-area", extra_area))
    assert any(issue["code"] == "office_invalid" for issue in err_area.issues)


def test_missing_area_folder(tmp_path: Path):
    root = _write_office(tmp_path / "missing-dir", _minimal_office_dict(), area_dirs=())
    err = _load_error(root)
    assert any(
        issue["code"] == "office_invalid" and issue["location"] == "areas[0].path"
        for issue in err.issues
    )

    file_root = _write_office(tmp_path / "file-not-dir", _minimal_office_dict(), area_dirs=("areas",))
    (file_root / "areas" / "reception").write_text("not a dir", encoding="utf-8")
    err_file = _load_error(file_root)
    assert any(
        issue["code"] == "office_invalid" and issue["location"] == "areas[0].path"
        for issue in err_file.issues
    )


def test_duplicate_yaml_keys_and_missing_schema_rejected(tmp_path: Path):
    no_schema = _minimal_office_dict()
    del no_schema["schema"]
    err_missing = _load_error(_write_office(tmp_path / "no-schema", no_schema))
    assert any(issue["code"] == "office_invalid" and issue["location"] == "schema" for issue in err_missing.issues)

    with_warnings = _minimal_office_dict()
    with_warnings["warnings"] = []
    err_warn_key = _load_error(_write_office(tmp_path / "warn-key", with_warnings))
    assert any(issue["code"] == "office_invalid" and issue["location"] == "warnings" for issue in err_warn_key.issues)

    dup_yaml = tmp_path / "dup-yaml"
    (dup_yaml / "areas" / "reception").mkdir(parents=True)
    (dup_yaml / "office.yaml").write_text(
        "schema: maf.office/1\nid: demo\nid: demo2\nname: Demo\nareas:\n  - id: reception\n    name: Rec\n    path: areas/reception\n",
        encoding="utf-8",
    )
    err_dup = _load_error(dup_yaml)
    assert any(issue["code"] == "office_invalid" for issue in err_dup.issues)


def test_area_bundle_name_rejects_invalid_ids():
    with pytest.raises(ValueError):
        area_bundle_name("bad_office", "reception")
    with pytest.raises(ValueError):
        area_bundle_name("demo", "bad_area")


def test_offices_package_is_pure():
    package_dir = Path(__file__).resolve().parents[1] / "multiagent" / "offices"
    forbidden = ("multiagent.services", "multiagent.gui", "httpx", "urllib", "socket", "fastapi")
    for source in package_dir.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{source} must not import {token}"
