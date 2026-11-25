# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/check_private_terms.py (PRV-T02).

All tests use temporary git repositories and temporary term lists with the
invented term ``Zorbalandia``. The default ``.local/privado/`` path is never
opened or created.
"""
from __future__ import annotations

import builtins
import importlib.util
import io
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_private_terms.py"
ZERO_SHA = "0" * 40


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_private_terms", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_git(command: list[str]) -> subprocess.CompletedProcess[str]:
    for attempt in range(6):
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        # Windows: a virus scanner or an indexer may hold a new object file for a moment ("Permission denied").
        if result.returncode == 0 or "Permission denied" not in result.stderr:
            break
        time.sleep(0.4 * (attempt + 1))
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
    return result


def _git(repo: Path, *args: str) -> str:
    result = _run_git(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Prueba",
            "-c",
            "user.email=prueba@example.invalid",
            *args,
        ]
    )
    return result.stdout.strip()


def _init_repo(tmp_path: Path, name: str = "repo") -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for private terms tests")
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    _run_git(["git", "init", "-b", "main", str(repo)])
    return repo


def _commit_file(repo: Path, rel_path: str, content: str, message: str) -> str:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write_terms(tmp_path: Path, lines: list[str], filename: str = "terms.txt") -> Path:
    path = tmp_path / filename
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_clean_range_exits_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "Public intro.\n", "initial commit")
    c2 = _commit_file(repo, "docs/guide.md", "Generic workflow guide.\n", "add guide")
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])

    assert code == 0


def test_added_line_found_case_and_accents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "docs/notes.md", "line 1\n", "base")
    c2 = _commit_file(
        repo,
        "docs/notes.md",
        "line 1\nfirst zorbalandia mention\nsecond Zorbalándia mention\n",
        "add notes",
    )
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])
    captured = capsys.readouterr()

    assert code == 1
    assert "docs/notes.md" in captured.out
    assert "term no. 1" in captured.out


def test_whole_word_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "docs/notes.md", "base\n", "base")
    c2 = _commit_file(repo, "docs/notes.md", "base\nZorbalandias plural\n", "add plural")
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])

    assert code == 0


def test_removed_line_not_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "docs/notes.md", "keep\nZorbalandia\n", "base")
    c2 = _commit_file(repo, "docs/notes.md", "keep\n", "remove line")
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])

    assert code == 0


def test_file_name_and_commit_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "base\n", "base")
    c2 = _commit_file(repo, "zorbalandia.md", "clean body\n", "arreglo para Zorbalandia")
    short_c2 = _git(repo, "rev-parse", "--short", c2)
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])
    captured = capsys.readouterr()

    assert code == 1
    assert "nombre de archivo" in captured.out
    assert f"mensaje del commit `{short_c2}`" in captured.out


def test_branch_name_in_hook_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "base\n", "base")
    c2 = _commit_file(repo, "docs/ok.md", "clean\n", "clean change")
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(f"refs/heads/zorbalandia-x {c2} refs/heads/zorbalandia-x {c1}\n"),
    )

    code = checker.main(["--terms", str(terms), "--hook"])
    captured = capsys.readouterr()

    assert code == 1
    assert "referencia" in captured.out


def test_new_branch_and_delete_in_hook_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "Zorbalandia on remote\n", "already pushed")
    # Simulate origin/main pointing at c1 so --not --remotes excludes c1.
    _git(repo, "update-ref", "refs/remotes/origin/main", c1)

    _git(repo, "checkout", "-b", "feat-clean", c1)
    c_clean = _commit_file(repo, "docs/clean.md", "nothing private\n", "clean commit")

    _git(repo, "checkout", "-b", "feat-bad", c1)
    c_bad = _commit_file(repo, "docs/bad.md", "hello Zorbalandia\n", "bad commit")
    monkeypatch.chdir(repo)

    # New clean branch ignores c1 already on origin/main.
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(f"refs/heads/feat-clean {c_clean} refs/heads/feat-clean {ZERO_SHA}\n"),
    )
    assert checker.main(["--terms", str(terms), "--hook"]) == 0

    # New branch with the term in its new commit fails.
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(f"refs/heads/feat-bad {c_bad} refs/heads/feat-bad {ZERO_SHA}\n"),
    )
    assert checker.main(["--terms", str(terms), "--hook"]) == 1

    # Deletion is not checked even if ref name contains the term.
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(f"(delete) {ZERO_SHA} refs/heads/zorbalandia-old {c_bad}\n"),
    )
    assert checker.main(["--terms", str(terms), "--hook"]) == 0


def test_multiword_term(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Nueva Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "base\n", "base")
    c2 = _commit_file(repo, "docs/place.md", "visit nueva   zorbalandia soon\n", "add place")
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])

    assert code == 1


def test_no_list_or_empty_list_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    _commit_file(repo, "README.md", "base\n", "base")
    monkeypatch.chdir(repo)

    missing = tmp_path / "does-not-exist.txt"
    code_missing = checker.main(["--terms", str(missing)])
    out_missing = capsys.readouterr()
    assert code_missing == 2
    assert "There is no private terms list; cannot check" in (out_missing.out + out_missing.err)

    only_comments = _write_terms(tmp_path, ["# comment", "", "   ", "# another"], "empty.txt")
    code_empty = checker.main(["--terms", str(only_comments)])
    out_empty = capsys.readouterr()
    assert code_empty == 2
    assert "There is no private terms list; cannot check" in (out_empty.out + out_empty.err)


def test_output_never_contains_the_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "base\n", "base")
    c2 = _commit_file(
        repo,
        "zorbalandia.md",
        "line with Zorbalándia\n",
        "arreglo para Zorbalandia",
    )
    monkeypatch.chdir(repo)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])
    captured = capsys.readouterr()
    assert code == 1
    assert "zorbalandia" not in checker.normalize(captured.out)
    assert "zorbalandia" not in checker.normalize(captured.err)

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(f"refs/heads/zorbalandia-x {c2} refs/heads/zorbalandia-x {c1}\n"),
    )
    code_hook = checker.main(["--terms", str(terms), "--hook"])
    captured_hook = capsys.readouterr()
    assert code_hook == 1
    assert "zorbalandia" not in checker.normalize(captured_hook.out)
    assert "zorbalandia" not in checker.normalize(captured_hook.err)


def test_default_path_is_not_read_in_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checker = _load_checker()
    repo = _init_repo(tmp_path)
    terms = _write_terms(tmp_path, ["Zorbalandia"])
    c1 = _commit_file(repo, "README.md", "base\n", "base")
    c2 = _commit_file(repo, "docs/ok.md", "clean\n", "clean")
    monkeypatch.chdir(repo)

    opened: list[str] = []
    real_open = builtins.open

    def spy_open(file, *args, **kwargs):
        opened.append(Path(file).as_posix() if isinstance(file, (str, Path)) else str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)

    code = checker.main(["--terms", str(terms), "--range", f"{c1}..{c2}"])

    assert code == 0
    assert not any(".local/privado" in entry for entry in opened)
