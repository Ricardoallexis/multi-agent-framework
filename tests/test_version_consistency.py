# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""The version check compares current declarations with multiagent/version.py and ignores history."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_version_consistency.py"
spec = importlib.util.spec_from_file_location("check_version_consistency", SCRIPT)
checker = importlib.util.module_from_spec(spec)
# Registered like a normal import: @dataclass looks the module up in sys.modules.
sys.modules[spec.name] = checker
spec.loader.exec_module(checker)

VERSION_PY = '''
PLATFORM_GENERATION = 1
BACKEND_REVISION = {backend}
FRONTEND_REVISION = 0
STAGE = "{stage}"
__package_version__ = "{package}"
__version__ = "computed at import"
'''
README = """# Project

**Current release:** `{release}` <!-- version:release -->

**Python package:** `{package}` <!-- version:package -->

Released first as `M1-B01-F00-alpha`.
"""
CHANGELOG = """# Changelog

## [Unreleased]

## [{release}] - 2026-10-01

Python package: `{package}`.

## [M1-B02-F00-alpha] - 2026-09-27

Python package: `0.2.0a1`.

## [M1-B01-F00-alpha] - 2026-09-23
"""
PROGRESS = """# Progress

## Milestones

| Date | Milestone |
| --- | --- |
| 2026-09-23 | `M1-B01-F00-alpha` — first public baseline. |
| 2026-10-01 | `{release}` — next release. |
"""
ROADMAP = """# Roadmap

## First public baseline — `M1-B01-F00-alpha`

- [x] Engine.
"""
PYPROJECT = """[project]
name = "demo"
dynamic = ["version"]

[tool.setuptools.dynamic]
version = {attr = "multiagent.version.__package_version__"}
"""


def make_repo(root: Path, *, release="M1-B03-F00-alpha", package="0.3.0a1", backend=3, stage="alpha") -> Path:
    (root / "multiagent").mkdir(parents=True)
    (root / "multiagent" / "version.py").write_text(VERSION_PY.format(backend=backend, stage=stage, package=package), encoding="utf-8")
    (root / "README.md").write_text(README.format(release=release, package=package), encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG.format(release=release, package=package), encoding="utf-8")
    (root / "PROGRESS.md").write_text(PROGRESS.format(release=release), encoding="utf-8")
    (root / "ROADMAP.md").write_text(ROADMAP, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    return root


def errors(root: Path, tag: str | None = None) -> list[str]:
    return checker.check(root, tag)[1]


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_consistent_versions_pass_including_history(tmp_path):
    root = make_repo(tmp_path)
    assert errors(root) == []
    assert checker.main(["--root", str(root)]) == 0


def test_readme_release_out_of_sync_fails(tmp_path):
    root = make_repo(tmp_path)
    replace(root / "README.md", "`M1-B03-F00-alpha` <!--", "`M1-B02-F00-alpha` <!--")
    assert errors(root) == ["README.md:3: current release is M1-B02-F00-alpha but canonical release is M1-B03-F00-alpha"]
    assert checker.main(["--root", str(root)]) == 1


def test_readme_package_out_of_sync_fails(tmp_path):
    root = make_repo(tmp_path)
    replace(root / "README.md", "`0.3.0a1` <!--", "`0.2.0a1` <!--")
    assert errors(root) == ["README.md:5: current Python package is 0.2.0a1 but canonical package version is 0.3.0a1"]


def test_canonical_package_out_of_sync_with_changelog_fails(tmp_path):
    root = make_repo(tmp_path)
    replace(root / "multiagent" / "version.py", '"0.3.0a1"', '"0.3.0a2"')
    replace(root / "README.md", "`0.3.0a1`", "`0.3.0a2`")
    assert errors(root) == ["CHANGELOG.md: newest release states Python package 0.3.0a1 but canonical package version is 0.3.0a2"]


def test_version_bumped_without_changelog_section_fails(tmp_path):
    root = make_repo(tmp_path)
    replace(root / "CHANGELOG.md", "## [M1-B03-F00-alpha] - 2026-10-01\n\nPython package: `0.3.0a1`.\n\n", "")
    problems = errors(root)
    assert "CHANGELOG.md: newest release section is M1-B02-F00-alpha but canonical release is M1-B03-F00-alpha" in problems


def test_historical_changelog_sections_and_first_baseline_pass(tmp_path):
    root = make_repo(tmp_path)
    # Older sections, the first baseline in the roadmap, and earlier milestones are history.
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "M1-B01-F00-alpha" in text and "0.2.0a1" in text
    assert "M1-B01-F00-alpha" in (root / "ROADMAP.md").read_text(encoding="utf-8")
    assert errors(root) == []


def test_latest_milestone_must_match(tmp_path):
    root = make_repo(tmp_path)
    replace(root / "PROGRESS.md", "| 2026-10-01 | `M1-B03-F00-alpha` — next release. |\n", "")
    assert errors(root) == ["PROGRESS.md: latest milestone is M1-B01-F00-alpha but canonical release is M1-B03-F00-alpha"]


def test_stage_and_package_prerelease_must_agree(tmp_path):
    root = make_repo(tmp_path, package="0.3.0b1")
    assert any("stage mismatch" in problem for problem in errors(root))


def test_stable_release_has_no_stage_suffix(tmp_path):
    root = make_repo(tmp_path, release="M1-B03-F00", package="1.0.0", stage="")
    assert errors(root) == []


def test_unmarked_current_release_declaration_fails(tmp_path):
    root = make_repo(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "GUIDE.md").write_text("The current release is `M1-B03-F00-alpha`.\n", encoding="utf-8")
    assert errors(root) == ["docs/GUIDE.md:1: unmarked current-version declaration; add <!-- version:release --> or reword it as historical"]


def test_marker_mentioned_in_inline_code_is_documentation(tmp_path):
    root = make_repo(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "VERSIONING.md").write_text(
        "Lines marked `<!-- version:release -->` are checked; examples: `M1-B01-F00-alpha`.\n", encoding="utf-8")
    assert errors(root) == []


def test_missing_readme_markers_fail(tmp_path):
    root = make_repo(tmp_path)
    (root / "README.md").write_text("# Project\n", encoding="utf-8")
    assert sorted(errors(root)) == [
        "README.md: missing the current package declaration marker <!-- version:package -->",
        "README.md: missing the current release declaration marker <!-- version:release -->",
    ]


def test_literal_version_in_pyproject_fails(tmp_path):
    root = make_repo(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.3.0a1"\n', encoding="utf-8")
    assert any(problem.startswith("pyproject.toml:") for problem in errors(root))


@pytest.mark.parametrize(("tag", "ok"), [("M1-B03-F00-alpha", True), ("M1-B02-F00-alpha", False)])
def test_tag_must_match_release(tmp_path, tag, ok):
    root = make_repo(tmp_path)
    assert (errors(root, tag) == []) is ok


def test_tag_is_read_from_github_on_tag_builds(tmp_path, monkeypatch):
    root = make_repo(tmp_path)
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "M9-B99-F99-alpha")
    assert checker.main(["--root", str(root)]) == 1
    monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
    assert checker.main(["--root", str(root)]) == 0


def test_this_repository_is_consistent():
    canonical, problems = checker.check()
    assert problems == []
    from multiagent import version

    assert (canonical.release, canonical.package) == (version.__version__, version.__package_version__)
