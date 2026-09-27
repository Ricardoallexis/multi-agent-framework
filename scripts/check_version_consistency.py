# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Check that every *current* version declaration matches the canonical version.

The canonical source is ``multiagent/version.py``: it defines the product release
(``M<generation>-B<backend>-F<frontend>[-<stage>]``) and the PEP 440 package version.
``pyproject.toml`` already reads the package version from it. The two versions are
different by design; this script checks each against its own rules and checks that
they agree on the stage (alpha -> ``aN``, beta -> ``bN``, rcN -> ``rcN``, stable -> none).

Current declarations that must match:

* any Markdown line carrying ``<!-- version:release -->`` or ``<!-- version:package -->``
  (README's header uses them); the marker is required in README;
* the newest released section of CHANGELOG.md (``## [<release>] - <date>``) and its
  ``Python package: `<version>``` line;
* the last row of the "Milestones" table in PROGRESS.md;
* ``pyproject.toml`` must keep reading the version from ``multiagent.version``;
* on a tag build (``GITHUB_REF_TYPE=tag`` or ``--tag``), the tag must equal the release.

Historical references (older CHANGELOG sections, earlier milestones, the first baseline in
the roadmap, documented tag examples) are not checked. To avoid silent drift, a line that
calls a version "current release/version" without a marker is reported.

Usage::

    python scripts/check_version_consistency.py          # exit 1 on any inconsistency
    python scripts/check_version_consistency.py --tag M1-B03-F00-alpha
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RELEASE = re.compile(r"^M(?P<m>\d+)-B(?P<b>\d{2})-F(?P<f>\d{2})(?:-(?P<stage>alpha|beta|rc\d+))?$")
RELEASE_TOKEN = re.compile(r"M\d+-B\d{2}-F\d{2}(?:-(?:alpha|beta|rc\d+))?")
# PEP 440 subset used by this project: X.Y.Z with an optional aN / bN / rcN pre-release.
PACKAGE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?:(?P<pre>a|b|rc)(?P<n>\d+))?$")
PACKAGE_TOKEN = re.compile(r"\b\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?\b")
RELEASE_MARKER = "<!-- version:release -->"
PACKAGE_MARKER = "<!-- version:package -->"
UNMARKED_CURRENT = re.compile(r"(?i)\bcurrent (?:public )?(?:release|version)\b")
INLINE_CODE = re.compile(r"`[^`]*`")
SKIP_DIRS = {".git", ".venv", "venv", ".local", "node_modules", "build", "dist", "__pycache__"}


@dataclass(frozen=True)
class Canonical:
    release: str
    package: str
    stage: str


def read_canonical(root: Path) -> Canonical:
    """Read multiagent/version.py without importing it (works on any checkout)."""
    tree = ast.parse((root / "multiagent" / "version.py").read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue  # computed values such as __version__ are rebuilt below
    stage = str(values.get("STAGE", ""))
    release = f"M{values['PLATFORM_GENERATION']}-B{int(values['BACKEND_REVISION']):02d}-F{int(values['FRONTEND_REVISION']):02d}"
    if stage:
        release += f"-{stage}"
    return Canonical(release=release, package=str(values["__package_version__"]), stage=stage)


def check_canonical(canonical: Canonical) -> list[str]:
    errors = []
    if not RELEASE.match(canonical.release):
        errors.append(f"canonical release {canonical.release!r} does not match M<g>-B<bb>-F<ff>[-alpha|-beta|-rcN]")
    package = PACKAGE.match(canonical.package)
    if not package:
        errors.append(f"canonical package version {canonical.package!r} is not a supported PEP 440 version (X.Y.Z[aN|bN|rcN])")
        return errors
    expected_pre = {"alpha": "a", "beta": "b", "": None}.get(canonical.stage, "rc" if canonical.stage.startswith("rc") else "?")
    if package.group("pre") != expected_pre:
        errors.append(
            f"stage mismatch: release stage {canonical.stage or 'stable'!r} requires a package "
            f"{'pre-release ' + expected_pre + 'N' if expected_pre else 'final version'}, found {canonical.package!r}"
        )
    elif expected_pre == "rc" and package.group("n") != canonical.stage[2:]:
        errors.append(f"stage mismatch: release {canonical.stage!r} but package {canonical.package!r}")
    return errors


def markdown_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*.md"):
        if not SKIP_DIRS.intersection(path.relative_to(root).parts):
            files.append(path)
    return sorted(files)


def check_markers(root: Path, canonical: Canonical) -> list[str]:
    errors = []
    readme_markers = set()
    for path in markdown_files(root):
        rel = path.relative_to(root).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{rel}:{number}"
            # A marker counts only outside inline code, so documentation can mention it.
            prose = INLINE_CODE.sub("", line)
            if RELEASE_MARKER in prose:
                found = RELEASE_TOKEN.findall(line)
                if rel == "README.md":
                    readme_markers.add("release")
                if found != [canonical.release]:
                    errors.append(f"{where}: current release is {', '.join(found) or 'missing'} but canonical release is {canonical.release}")
            if PACKAGE_MARKER in prose:
                found = PACKAGE_TOKEN.findall(line)
                if rel == "README.md":
                    readme_markers.add("package")
                if found != [canonical.package]:
                    errors.append(f"{where}: current Python package is {', '.join(found) or 'missing'} but canonical package version is {canonical.package}")
            if (UNMARKED_CURRENT.search(line) and RELEASE_TOKEN.search(line)
                    and RELEASE_MARKER not in prose and rel != "CHANGELOG.md"):
                errors.append(f"{where}: unmarked current-version declaration; add {RELEASE_MARKER} or reword it as historical")
    for kind in ("release", "package"):
        if kind not in readme_markers:
            errors.append(f"README.md: missing the current {kind} declaration marker <!-- version:{kind} -->")
    return errors


def check_changelog(root: Path, canonical: Canonical) -> list[str]:
    path = root / "CHANGELOG.md"
    if not path.exists():
        return ["CHANGELOG.md: file not found"]
    text = path.read_text(encoding="utf-8")
    sections = list(re.finditer(r"^## \[(?P<name>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$", text, re.M))
    released = [s for s in sections if s.group("name").lower() != "unreleased"]
    if not released:
        return ["CHANGELOG.md: no released version section found"]
    newest = released[0]
    errors = []
    if newest.group("name") != canonical.release:
        errors.append(f"CHANGELOG.md: newest release section is {newest.group('name')} but canonical release is {canonical.release}")
    if not newest.group("date"):
        errors.append(f"CHANGELOG.md: section [{newest.group('name')}] has no date (## [<release>] - YYYY-MM-DD)")
    following = sections[sections.index(newest) + 1].start() if sections.index(newest) + 1 < len(sections) else len(text)
    body = text[newest.end():following]
    package = re.search(r"Python package: `([^`]+)`", body)
    if not package:
        errors.append(f"CHANGELOG.md: section [{newest.group('name')}] does not state its Python package version")
    elif package.group(1) != canonical.package:
        errors.append(f"CHANGELOG.md: newest release states Python package {package.group(1)} but canonical package version is {canonical.package}")
    return errors


def check_milestones(root: Path, canonical: Canonical) -> list[str]:
    path = root / "PROGRESS.md"
    if not path.exists():
        return []
    match = re.search(r"^## Milestones\s*\n(?P<body>.*?)(?=^## |\Z)", path.read_text(encoding="utf-8"), re.M | re.S)
    if not match:
        return []
    rows = [line for line in match.group("body").splitlines() if line.startswith("|") and RELEASE_TOKEN.search(line)]
    if not rows:
        return []
    last = RELEASE_TOKEN.search(rows[-1]).group(0)
    if last != canonical.release:
        return [f"PROGRESS.md: latest milestone is {last} but canonical release is {canonical.release}"]
    return []


def check_pyproject(root: Path) -> list[str]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not re.search(r'^version\s*=\s*\{\s*attr\s*=\s*"multiagent\.version\.__package_version__"\s*\}', text, re.M):
        return ['pyproject.toml: the package version must be read from multiagent.version.__package_version__ (dynamic = ["version"])']
    if re.search(r'^version\s*=\s*"', text, re.M):
        return ["pyproject.toml: a literal version duplicates multiagent/version.py"]
    return []


def check_tag(canonical: Canonical, tag: str | None) -> list[str]:
    if not tag:
        return []
    if tag != canonical.release:
        return [f"git tag {tag} does not match canonical release {canonical.release}"]
    return []


def check(root: Path = ROOT, tag: str | None = None) -> tuple[Canonical | None, list[str]]:
    try:
        canonical = read_canonical(root)
    except (OSError, KeyError, SyntaxError) as exc:
        return None, [f"multiagent/version.py: cannot read the canonical version ({exc})"]
    errors = check_canonical(canonical)
    errors += check_pyproject(root)
    errors += check_markers(root, canonical)
    errors += check_changelog(root, canonical)
    errors += check_milestones(root, canonical)
    errors += check_tag(canonical, tag)
    return canonical, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="release tag being built (defaults to GITHUB_REF_NAME on tag builds)")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    tag = args.tag
    if tag is None and os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME")
    canonical, errors = check(args.root, tag)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"Version consistency: {len(errors)} problem(s). Canonical source: multiagent/version.py")
        return 1
    print(f"Version consistency OK: release {canonical.release}, Python package {canonical.package}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
