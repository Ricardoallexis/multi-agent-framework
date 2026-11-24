# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PLT-T01: pure tests for the two level-1 office templates (cafeteria and escuela)."""
from __future__ import annotations

from pathlib import Path

import pytest

from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.contracts_data import load_contracts_file
from multiagent.offices.schema import load_office
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices
from multiagent.services.installed import CODE_FREE_SUFFIXES, package_level
from multiagent.teams.schema import MemberKind, RelationshipType

ROOT = Path(__file__).resolve().parents[1]
CAFETERIA = ROOT / "examples" / "offices" / "cafeteria"
ESCUELA = ROOT / "examples" / "offices" / "escuela"

FORBIDDEN_PROVIDERS_OR_BRANDS = (
    "claude",
    "anthropic",
    "gemini",
    "google",
    "openai",
    "gpt",
    "codex",
    "copilot",
    "github",
    "microsoft",
    "ollama",
    "agy",
    "antigravity",
    "\u00ae",
    "\u2122",
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        local_dir=tmp_path / "local",
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )


def _all_office_files(office_root: Path) -> list[Path]:
    assert office_root.is_dir(), f"Missing office folder: {office_root}"
    files = sorted(path for path in office_root.rglob("*") if path.is_file())
    assert files, f"No files found in {office_root}"
    return files


def _area_dirs(office_root: Path):
    office = load_office(office_root)
    return [(area, office_root.joinpath(*area.path.split("/"))) for area in office.areas]


def test_both_templates_load(settings: Settings):
    cafeteria = load_office(CAFETERIA)
    escuela = load_office(ESCUELA)

    assert cafeteria.id == "cafeteria"
    assert cafeteria.name == "Cafetería de ejemplo"
    assert cafeteria.warnings == []
    assert [area.id for area in cafeteria.areas] == ["menu", "compras", "avisos"]

    assert escuela.id == "escuela"
    assert escuela.name == "Escuela de ejemplo"
    assert escuela.warnings == []
    assert [area.id for area in escuela.areas] == ["planeacion", "seguimiento", "avisos"]

    expected_workflows = {
        ("cafeteria", "menu"): ["menu_semana"],
        ("cafeteria", "compras"): ["lista_pedido"],
        ("cafeteria", "avisos"): ["escribir_aviso", "responder_mensaje"],
        ("escuela", "planeacion"): ["plan_clase"],
        ("escuela", "seguimiento"): ["nota_avance"],
        ("escuela", "avisos"): ["escribir_aviso", "responder_mensaje"],
    }

    for office_root, office in ((CAFETERIA, cafeteria), (ESCUELA, escuela)):
        assert (office_root / "README.md").is_file()
        for area in office.areas:
            area_dir = office_root.joinpath(*area.path.split("/"))
            assert (area_dir / "README.md").is_file()
            bundle = load_bundle(area_dir, settings)
            workflows = sorted(path.stem for path in (area_dir / "workflows").glob("*.yaml"))
            assert workflows == expected_workflows[(office.id, area.id)]
            for wf_id in workflows:
                bundle.load_workflow(wf_id)


def test_cafeteria_runs_in_mock(tmp_path: Path):
    local_dir = tmp_path / "local"
    settings = Settings(
        _env_file=None,
        local_dir=local_dir,
        worker_enabled=False,
        mock_mode=True,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
        gui_token_file=tmp_path / "gui-token",
    )
    services = ApplicationServices.create(settings, dry_run=True)
    try:
        imported = services.offices.import_folder(CAFETERIA, actor="user:local")
        assert imported["office"]["id"] == "cafeteria"
        assert imported["office"]["state"] == "ok"
        assert [(t["bundle"], t["status"]) for t in imported["teams"]] == [
            ("cafeteria__menu", "proposed"),
            ("cafeteria__compras", "proposed"),
            ("cafeteria__avisos", "proposed"),
        ]
        assert services.teams.approved("cafeteria__menu") is None

        run = services.runs.create(
            RunRequest(
                project_name="Cafetería de prueba",
                workflow_id="menu_semana",
                inputs={"pedido": "Menú de lunes a viernes con plato caliente y fruta."},
            ),
            bundle="cafeteria__menu",
        )
        runtime = services.runs._bundle_runtime("cafeteria__menu")
        assert runtime is not None and runtime.worker is not None
        assert runtime.worker.process_once() is True

        state = services.runs.get(run["id"])
        assert state["status"] == "waiting_human"
        assert state["waiting_reason"] == "review"
        assert state["waiting_step"] == "redactar_menu"
        assert services.teams.approved("cafeteria__menu") is None
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_templates_are_level_1():
    for office_root in (CAFETERIA, ESCUELA):
        files = _all_office_files(office_root)
        bad_suffixes = [
            path.relative_to(office_root).as_posix()
            for path in files
            if path.suffix.lower() not in CODE_FREE_SUFFIXES
        ]
        assert bad_suffixes == []
        assert package_level(office_root) == "data"

        office = load_office(office_root)
        for area in office.areas:
            area_dir = office_root.joinpath(*area.path.split("/"))
            assert not (area_dir / "catalog" / "models.yaml").exists()
            assert not (area_dir / "contracts.py").exists()
            assert not (area_dir / "functions.py").exists()


def test_no_provider_or_brand():
    for office_root in (CAFETERIA, ESCUELA):
        for path in _all_office_files(office_root):
            text = path.read_text(encoding="utf-8").casefold()
            found = [term for term in FORBIDDEN_PROVIDERS_OR_BRANDS if term in text]
            assert found == [], f"{path.relative_to(ROOT).as_posix()} mentions {found}"


def test_every_step_is_local_default_and_reviewed(settings: Settings):
    for office_root in (CAFETERIA, ESCUELA):
        for area, area_dir in _area_dirs(office_root):
            bundle = load_bundle(area_dir, settings)
            workflow_paths = sorted((area_dir / "workflows").glob("*.yaml"))
            assert workflow_paths, f"No workflows in {area.id}"
            for wf_path in workflow_paths:
                definition = bundle.load_workflow(wf_path.stem)
                assert len(definition.steps) == 1
                step = definition.steps[0]
                assert step.function is None
                assert step.preferred_model == "local_default"
                assert step.fallback_model is None
                assert step.requires_web is False
                assert step.checkpoint_after is True
                assert step.role is None
                assert definition.inputs, f"{wf_path.stem} must declare its required text input first"
                first_input = definition.inputs[0]
                assert first_input.required is True
                assert first_input.type in ("text", "long_text")


def test_shared_area_is_byte_identical():
    left_dir = CAFETERIA / "areas" / "avisos"
    right_dir = ESCUELA / "areas" / "avisos"
    assert left_dir.is_dir() and right_dir.is_dir()

    left_files = sorted(path.relative_to(left_dir).as_posix() for path in left_dir.rglob("*") if path.is_file())
    right_files = sorted(path.relative_to(right_dir).as_posix() for path in right_dir.rglob("*") if path.is_file())
    assert left_files and left_files == right_files

    for rel in left_files:
        assert (left_dir / rel).read_bytes() == (right_dir / rel).read_bytes(), f"Differs in {rel}"


def test_school_follow_up_is_local_only():
    cafeteria = load_office(CAFETERIA)
    escuela = load_office(ESCUELA)

    for area in cafeteria.areas:
        assert area.sensitivity == "normal"
        assert area.local_only is False

    by_id = {area.id: area for area in escuela.areas}
    assert by_id["seguimiento"].sensitivity == "sensitive"
    assert by_id["seguimiento"].local_only is True
    assert by_id["seguimiento"].requires == frozenset({"model.text", "model.local"})
    for area_id in ("planeacion", "avisos"):
        assert by_id[area_id].sensitivity == "normal"
        assert by_id[area_id].local_only is False


def test_default_plans():
    cafeteria = load_office(CAFETERIA)
    escuela = load_office(ESCUELA)

    assert cafeteria.orchestrator.enabled is True
    assert [
        (item.area, item.workflow, item.after)
        for item in cafeteria.orchestrator.default_plan
    ] == [
        ("menu", "menu_semana", []),
        ("avisos", "escribir_aviso", ["menu"]),
    ]
    assert [
        (rel.from_, rel.to, rel.kind)
        for rel in cafeteria.relations
    ] == [
        ("menu", "avisos", "hands_off"),
        ("compras", "menu", "consults"),
    ]

    assert escuela.orchestrator.enabled is True
    assert [
        (item.area, item.workflow, item.after)
        for item in escuela.orchestrator.default_plan
    ] == [
        ("planeacion", "plan_clase", []),
        ("avisos", "escribir_aviso", ["planeacion"]),
    ]
    assert [
        (rel.from_, rel.to, rel.kind)
        for rel in escuela.relations
    ] == [
        ("planeacion", "avisos", "hands_off"),
        ("seguimiento", "planeacion", "consults"),
    ]


def test_teams_have_a_reviewer(settings: Settings):
    for office_root in (CAFETERIA, ESCUELA):
        for area, area_dir in _area_dirs(office_root):
            team = load_bundle(area_dir, settings).team
            assert team is not None, f"Missing team.yaml in {area.id}"

            members_by_id = {member.id: member for member in team.members}
            roles_by_role_id = {role.id: role for role in team.roles}
            assert {"redaccion", "revision"} <= set(roles_by_role_id)

            redaccion_members = [
                members_by_id[assign.member]
                for assign in team.assignments
                if assign.role == "redaccion"
            ]
            assert len(redaccion_members) == 1
            assert redaccion_members[0].kind == MemberKind.AGENT

            revision_members = [
                members_by_id[assign.member]
                for assign in team.assignments
                if assign.role == "revision"
            ]
            assert len(revision_members) == 1
            assert revision_members[0].kind == MemberKind.PERSON
            assert revision_members[0].name == "Responsable"

            assert any(
                rel.type == RelationshipType.REVIEWS and rel.from_ == "revision" and rel.to == "redaccion"
                for rel in team.relationships
            )


def test_samples_validate(settings: Settings):
    for office_root in (CAFETERIA, ESCUELA):
        for area, area_dir in _area_dirs(office_root):
            contracts_path = area_dir / "contracts.yaml"
            assert contracts_path.is_file(), f"Missing contracts.yaml in {area.id}"
            contracts, samples = load_contracts_file(contracts_path)
            assert contracts
            assert set(samples) == set(contracts)

            bundle = load_bundle(area_dir, settings)
            for name, schema in contracts.items():
                schema.model_validate(samples[name])
                assert name in bundle.output_schemas
                assert bundle.sample_outputs[name] == samples[name]
