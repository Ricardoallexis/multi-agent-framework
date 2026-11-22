# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/install_git_hooks.py (PRV-T02).

All tests operate inside temporary git repositories in ``tmp_path`` and never
touch ``.git`` of the real repository or ``.local/privado/``.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install_git_hooks.py"


def _load_installer():
    spec = importlib.util.spec_from_file_location("install_git_hooks", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Prueba",
            "-c",
            "user.email=prueba@example.invalid",
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # the hook prints accented Spanish in the console code page on Windows
        check=check,
    )


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for git hook installer tests")
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True,
        text=True,
        check=True,
    )
    return repo


def test_installs_hook_with_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load_installer()
    repo = _init_repo(tmp_path)
    terms = tmp_path / "terms.txt"
    terms.write_text("Zorbalandia\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    code = installer.main(["--terms", str(terms)])

    assert code == 0
    hook_path = repo / ".git" / "hooks" / "pre-push"
    assert hook_path.exists()
    content = hook_path.read_text(encoding="utf-8")
    assert content.startswith("#!/bin/sh\n")
    assert installer.HOOK_MARKER in content
    assert sys.executable in content
    assert str(terms.resolve()) in content


def test_replaces_own_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load_installer()
    repo = _init_repo(tmp_path)
    terms_a = tmp_path / "terms-a.txt"
    terms_b = tmp_path / "terms-b.txt"
    terms_a.write_text("Zorbalandia\n", encoding="utf-8")
    terms_b.write_text("Zorbalandia\n", encoding="utf-8")
    monkeypatch.chdir(repo)

    assert installer.main(["--terms", str(terms_a)]) == 0
    assert installer.main(["--terms", str(terms_b)]) == 0

    hook_path = repo / ".git" / "hooks" / "pre-push"
    content = hook_path.read_text(encoding="utf-8")
    assert content.count(installer.HOOK_MARKER) == 1
    assert str(terms_b.resolve()) in content
    assert str(terms_a.resolve()) not in content


def test_foreign_hook_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = _load_installer()
    repo = _init_repo(tmp_path)
    terms = tmp_path / "terms.txt"
    terms.write_text("Zorbalandia\n", encoding="utf-8")
    hook_path = repo / ".git" / "hooks" / "pre-push"
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    foreign = "#!/bin/sh\necho custom hook\n"
    hook_path.write_text(foreign, encoding="utf-8", newline="\n")
    monkeypatch.chdir(repo)

    code = installer.main(["--terms", str(terms)])
    captured = capsys.readouterr()

    assert code == 1
    assert hook_path.read_text(encoding="utf-8") == foreign
    assert (captured.out + captured.err).strip() != ""


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh is required to run pre-push hooks")
def test_hook_runs_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    installer = _load_installer()
    if shutil.which("git") is None:
        pytest.skip("git is required for git hook installer tests")

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], capture_output=True, text=True, check=True)
    repo = _init_repo(tmp_path, "work")
    _git(repo, "remote", "add", "origin", str(bare))

    (repo / "README.md").write_text("Public intro.\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "push", "-u", "origin", "main")

    terms = tmp_path / "terms.txt"
    terms.write_text("Zorbalandia\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert installer.main(["--terms", str(terms)]) == 0

    (repo / "notes.md").write_text("Secret Zorbalandia note\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    _git(repo, "commit", "-m", "add note")
    blocked = _git(repo, "push", "origin", "main", check=False)

    assert blocked.returncode != 0
