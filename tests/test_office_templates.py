# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""PLT-T01: pure tests for the two level-1 office templates (cafe and school)."""
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
CAFE = ROOT / "examples" / "offices" / "cafe"
SCHOOL = ROOT / "examples" / "offices" / "school"

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
    cafe = load_office(CAFE)
    school = load_office(SCHOOL)

    assert cafe.id == "cafe"
    assert cafe.name == "Example Café"
    assert cafe.warnings == []
    assert [area.id for area in cafe.areas] == ["menu", "purchasing", "notices"]

    assert school.id == "school"
    assert school.name == "Example School"
    assert school.warnings == []
    assert [area.id for area in school.areas] == ["planning", "follow-up", "notices"]

    expected_workflows = {
        ("cafe", "menu"): ["weekly_menu"],
        ("cafe", "purchasing"): ["order_list"],
        ("cafe", "notices"): ["reply_message", "write_notice"],
        ("school", "planning"): ["lesson_plan"],
        ("school", "follow-up"): ["progress_note"],
        ("school", "notices"): ["reply_message", "write_notice"],
    }

    for office_root, office in ((CAFE, cafe), (SCHOOL, school)):
        assert (office_root / "README.md").is_file()
        for area in office.areas:
            area_dir = office_root.joinpath(*area.path.split("/"))
            assert (area_dir / "README.md").is_file()
            bundle = load_bundle(area_dir, settings)
            workflows = sorted(path.stem for path in (area_dir / "workflows").glob("*.yaml"))
            assert workflows == expected_workflows[(office.id, area.id)]
            for wf_id in workflows:
                bundle.load_workflow(wf_id)


def test_cafe_runs_in_mock(tmp_path: Path):
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
        imported = services.offices.import_folder(CAFE, actor="user:local")
        assert imported["office"]["id"] == "cafe"
        assert imported["office"]["state"] == "ok"
        assert [(t["bundle"], t["status"]) for t in imported["teams"]] == [
            ("cafe__menu", "proposed"),
            ("cafe__purchasing", "proposed"),
            ("cafe__notices", "proposed"),
        ]
        assert services.teams.approved("cafe__menu") is None

        run = services.runs.create(
            RunRequest(
                project_name="Sample cafe test",
                workflow_id="weekly_menu",
                inputs={"order": "Monday to Friday menu with a hot dish and fruit."},
            ),
            bundle="cafe__menu",
        )
        runtime = services.runs._bundle_runtime("cafe__menu")
        assert runtime is not None and runtime.worker is not None
        assert runtime.worker.process_once() is True

        state = services.runs.get(run["id"])
        assert state["status"] == "waiting_human"
        assert state["waiting_reason"] == "review"
        assert state["waiting_step"] == "draft_menu"
        assert services.teams.approved("cafe__menu") is None
    finally:
        services.bundle_scans.close()
        services.runs.close()


def test_templates_are_level_1():
    for office_root in (CAFE, SCHOOL):
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
    for office_root in (CAFE, SCHOOL):
        for path in _all_office_files(office_root):
            text = path.read_text(encoding="utf-8").casefold()
            found = [term for term in FORBIDDEN_PROVIDERS_OR_BRANDS if term in text]
            assert found == [], f"{path.relative_to(ROOT).as_posix()} mentions {found}"


def test_every_step_is_local_default_and_reviewed(settings: Settings):
    for office_root in (CAFE, SCHOOL):
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
    left_dir = CAFE / "areas" / "notices"
    right_dir = SCHOOL / "areas" / "notices"
    assert left_dir.is_dir() and right_dir.is_dir()

    left_files = sorted(path.relative_to(left_dir).as_posix() for path in left_dir.rglob("*") if path.is_file())
    right_files = sorted(path.relative_to(right_dir).as_posix() for path in right_dir.rglob("*") if path.is_file())
    assert left_files and left_files == right_files

    for rel in left_files:
        assert (left_dir / rel).read_bytes() == (right_dir / rel).read_bytes(), f"Differs in {rel}"


def test_school_follow_up_is_local_only():
    cafe = load_office(CAFE)
    school = load_office(SCHOOL)

    for area in cafe.areas:
        assert area.sensitivity == "normal"
        assert area.local_only is False

    by_id = {area.id: area for area in school.areas}
    assert by_id["follow-up"].sensitivity == "sensitive"
    assert by_id["follow-up"].local_only is True
    assert by_id["follow-up"].requires == frozenset({"model.text", "model.local"})
    for area_id in ("planning", "notices"):
        assert by_id[area_id].sensitivity == "normal"
        assert by_id[area_id].local_only is False


def test_default_plans():
    cafe = load_office(CAFE)
    school = load_office(SCHOOL)

    assert cafe.orchestrator.enabled is True
    assert [
        (item.area, item.workflow, item.after)
        for item in cafe.orchestrator.default_plan
    ] == [
        ("menu", "weekly_menu", []),
        ("notices", "write_notice", ["menu"]),
    ]
    assert [
        (rel.from_, rel.to, rel.kind)
        for rel in cafe.relations
    ] == [
        ("menu", "notices", "hands_off"),
        ("purchasing", "menu", "consults"),
    ]

    assert school.orchestrator.enabled is True
    assert [
        (item.area, item.workflow, item.after)
        for item in school.orchestrator.default_plan
    ] == [
        ("planning", "lesson_plan", []),
        ("notices", "write_notice", ["planning"]),
    ]
    assert [
        (rel.from_, rel.to, rel.kind)
        for rel in school.relations
    ] == [
        ("planning", "notices", "hands_off"),
        ("follow-up", "planning", "consults"),
    ]


def test_teams_have_a_reviewer(settings: Settings):
    for office_root in (CAFE, SCHOOL):
        for area, area_dir in _area_dirs(office_root):
            team = load_bundle(area_dir, settings).team
            assert team is not None, f"Missing team.yaml in {area.id}"

            members_by_id = {member.id: member for member in team.members}
            roles_by_role_id = {role.id: role for role in team.roles}
            assert {"drafting", "review"} <= set(roles_by_role_id)

            drafting_members = [
                members_by_id[assign.member]
                for assign in team.assignments
                if assign.role == "drafting"
            ]
            assert len(drafting_members) == 1
            assert drafting_members[0].kind == MemberKind.AGENT

            review_members = [
                members_by_id[assign.member]
                for assign in team.assignments
                if assign.role == "review"
            ]
            assert len(review_members) == 1
            assert review_members[0].kind == MemberKind.PERSON
            assert review_members[0].name == "Reviewer"

            assert any(
                rel.type == RelationshipType.REVIEWS and rel.from_ == "review" and rel.to == "drafting"
                for rel in team.relationships
            )


def test_samples_validate(settings: Settings):
    for office_root in (CAFE, SCHOOL):
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
