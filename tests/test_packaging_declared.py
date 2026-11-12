"""Verify that installed package declarations match the source tree."""

from pathlib import Path
import tomllib
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def find_package_issues(
    project_root: Path,
    declared_packages: set[str],
    package_dirs: Mapping[str, str] | None = None,
) -> set[str]:
    """Return packages missing declarations or missing their source directory."""
    package_root = project_root / "multiagent"
    discovered = {
        ".".join(("multiagent", *init_file.parent.relative_to(package_root).parts))
        if init_file.parent != package_root
        else "multiagent"
        for init_file in package_root.rglob("__init__.py")
        if "__pycache__" not in init_file.parts
    }
    issues = discovered - declared_packages

    for package in declared_packages:
        if package_dirs and package in package_dirs:
            package_path = project_root / package_dirs[package]
        elif package == "multiagent":
            package_path = package_root
        elif package.startswith("multiagent."):
            package_path = package_root.joinpath(*package.split(".")[1:])
        else:
            package_path = project_root.joinpath(*package.split("."))
        if not package_path.is_dir():
            issues.add(package)
    return issues


def test_all_source_packages_are_declared() -> None:
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools_config = config["tool"]["setuptools"]
    declared_packages = set(setuptools_config["packages"])
    issues = find_package_issues(
        PROJECT_ROOT,
        declared_packages,
        setuptools_config.get("package-dir"),
    )

    assert not issues, f"Package declarations are incomplete or missing on disk: {sorted(issues)}"


def test_helper_reports_missing_declarations_and_directories(tmp_path: Path) -> None:
    package_root = tmp_path / "multiagent"
    for package in ("alpha", "beta", "resources"):
        package_dir = package_root / package
        package_dir.mkdir(parents=True)
        if package != "resources":
            (package_dir / "__init__.py").touch()

    declared_packages = {"multiagent", "multiagent.alpha", "multiagent.resources"}
    assert find_package_issues(tmp_path, declared_packages) == {"multiagent.beta"}

    declared_packages.add("multiagent.missing")
    assert find_package_issues(tmp_path, declared_packages) == {
        "multiagent.beta",
        "multiagent.missing",
    }
