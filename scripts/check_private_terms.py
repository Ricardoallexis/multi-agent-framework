# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Check commits, diff additions, file names, and pushed refs for private terms.

Offline and read-only. It inspects a git revision range (``origin/main..HEAD``
by default) or ``pre-push`` hook input from standard input, and reports only the
1-based index of the matched term and its location — never the term itself or
the matched line content.

Exit codes:
    0  clean (no private terms found)
    1  at least one private term was found
    2  cannot check (missing/empty terms list or git error)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from collections.abc import Iterator
from pathlib import Path

DEFAULT_TERMS = ".local/privado/terminos-privados.txt"
MISSING_LIST_MESSAGE = "No hay lista de términos privados; no se puede comprobar"

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class GitError(RuntimeError):
    """Raised when invoking git fails or returns an invalid result."""


def normalize(text: str) -> str:
    """Normalize text with NFKD decomposition, no combining marks, and casefold."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _compile_term_pattern(term: str) -> re.Pattern[str] | None:
    norm = normalize(term)
    parts = norm.split()
    if not parts:
        return None
    body = r"\s+".join(re.escape(part) for part in parts)
    prefix = r"(?<![^\W_])" if norm[0].isalnum() else ""
    suffix = r"(?![^\W_])" if norm[-1].isalnum() else ""
    return re.compile(f"{prefix}{body}{suffix}")


def load_terms(path: Path) -> list[str]:
    """Read UTF-8 terms from ``path``, ignoring blank lines and ``#`` comments."""
    raw = path.read_text(encoding="utf-8")
    terms: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        terms.append(stripped)
    return terms


def find_terms(text: str, terms: list[str]) -> list[int]:
    """Return 1-based indices of ``terms`` matched as whole words in ``text``."""
    norm_text = normalize(text)
    found: list[int] = []
    for index, term in enumerate(terms, start=1):
        pattern = _compile_term_pattern(term)
        if pattern is not None and pattern.search(norm_text):
            found.append(index)
    return found


def _redact_terms(text: str, terms: list[str]) -> str:
    """Replace any matched term occurrences in ``text`` so output never leaks them."""
    norm_chars: list[str] = []
    orig_spans: list[tuple[int, int]] = []
    for idx, ch in enumerate(text):
        decomp = "".join(
            c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)
        ).casefold()
        for nc in decomp:
            norm_chars.append(nc)
            orig_spans.append((idx, idx + 1))

    norm_text = "".join(norm_chars)
    intervals: list[tuple[int, int]] = []
    for term in terms:
        pattern = _compile_term_pattern(term)
        if pattern is None:
            continue
        for match in pattern.finditer(norm_text):
            start = orig_spans[match.start()][0]
            end = orig_spans[match.end() - 1][1]
            intervals.append((start, end))

    if not intervals:
        return text

    intervals.sort()
    merged: list[tuple[int, int]] = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append("[redactado]")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _run_git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.quotePath=false",
                "-c",
                "diff.mnemonicPrefix=false",
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise GitError(f"git invocation failed ({type(exc).__name__})") from exc
    if result.returncode != 0:
        raise GitError(f"git command failed with exit code {result.returncode}")
    return result.stdout


def _repo_root(start: Path) -> Path:
    return Path(_run_git(start, "rev-parse", "--show-toplevel").strip())


def _is_zero_sha(sha: str) -> bool:
    return bool(sha) and set(sha) == {"0"}


def _iter_added_lines(diff_text: str) -> Iterator[tuple[str, str]]:
    current_file: str | None = None
    new_line = 0
    in_hunk = False
    for raw_line in diff_text.split("\n"):
        line = raw_line.rstrip("\r")
        if line.startswith("diff --git "):
            current_file = None
            in_hunk = False
            continue
        if line.startswith("+++ "):
            target = line[4:]
            current_file = target[2:] if target.startswith("b/") else None
            in_hunk = False
            continue
        if line.startswith("@@ "):
            match = _HUNK_RE.match(line)
            if match:
                new_line = int(match.group(1))
                in_hunk = True
            else:
                in_hunk = False
            continue
        if not in_hunk or current_file is None:
            continue
        if raw_line.startswith("+"):
            yield f"`{current_file}`, línea {new_line} (línea añadida)", raw_line[1:]
            new_line += 1
        elif raw_line.startswith("-"):
            continue
        elif raw_line.startswith(" "):
            new_line += 1
        elif raw_line.startswith("\\"):
            continue
        else:
            in_hunk = False


def targets(repo: Path, rev_range: str) -> Iterator[tuple[str, str]]:
    """Yield ``(where, text)`` pairs for commits, file names, and added lines."""
    rev_args = rev_range.split()
    if not rev_args:
        raise GitError("empty revision range")
    commits_raw = _run_git(repo, "rev-list", "--reverse", *rev_args)
    commits = [line.strip() for line in commits_raw.splitlines() if line.strip()]
    seen_files: set[str] = set()

    for sha in commits:
        msg_raw = _run_git(repo, "log", "-n", "1", "--format=%h%n%B", sha)
        short_sha, _, message = msg_raw.partition("\n")
        short_sha = short_sha.strip()
        yield f"el mensaje del commit `{short_sha}`", message

        names_raw = _run_git(
            repo,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-status",
            "-r",
            "-M",
            "-m",
            "--first-parent",
            sha,
        )
        for line in names_raw.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            status = fields[0]
            if status.startswith("D"):
                continue
            if (status.startswith("R") or status.startswith("C")) and len(fields) >= 3:
                path = fields[2]
            elif len(fields) >= 2:
                path = fields[1]
            else:
                continue
            if path not in seen_files:
                seen_files.add(path)
                yield f"el nombre de archivo `{path}`", path

        diff_raw = _run_git(
            repo,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "-p",
            "-U0",
            "-M",
            "-m",
            "--first-parent",
            "--no-color",
            sha,
        )
        yield from _iter_added_lines(diff_raw)


def hook_ranges(stdin_text: str) -> list[tuple[str, str]]:
    """Parse ``pre-push`` standard input into ``(ref_name, rev_range)`` pairs."""
    ranges: list[tuple[str, str]] = []
    for raw_line in stdin_text.splitlines():
        parts = raw_line.strip().split()
        if not parts:
            continue
        if len(parts) != 4:
            raise ValueError("invalid pre-push stdin line")
        local_ref, local_sha, remote_ref, remote_sha = parts
        if _is_zero_sha(local_sha):
            continue
        rev_range = (
            f"{local_sha} --not --remotes"
            if _is_zero_sha(remote_sha)
            else f"{remote_sha}..{local_sha}"
        )
        ref_name = remote_ref or local_ref
        ranges.append((ref_name, rev_range))
    return ranges


def _load_terms_or_report(terms_path: Path) -> list[str] | None:
    try:
        terms = load_terms(terms_path)
    except (OSError, UnicodeDecodeError):
        print(MISSING_LIST_MESSAGE, file=sys.stderr)
        return None
    if not terms:
        print(MISSING_LIST_MESSAGE, file=sys.stderr)
        return None
    return terms


def _report_findings(where: str, text: str, terms: list[str]) -> bool:
    matches = find_terms(text, terms)
    if not matches:
        return False
    safe_where = _redact_terms(where, terms)
    for term_num in matches:
        print(f"término n.º {term_num} de la lista en {safe_where}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--terms",
        type=Path,
        default=None,
        help="path to the private terms file (default: .local/privado/terminos-privados.txt)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--range",
        default=None,
        help="git revision range to check (default: origin/main..HEAD)",
    )
    mode.add_argument(
        "--hook",
        action="store_true",
        help="read pre-push ref updates from standard input",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    if args.terms is not None:
        terms = _load_terms_or_report(args.terms)
        if terms is None:
            return 2
        try:
            repo = _repo_root(cwd)
        except GitError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            repo = _repo_root(cwd)
        except GitError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        terms = _load_terms_or_report(repo / DEFAULT_TERMS)
        if terms is None:
            return 2

    found_any = False
    try:
        if args.hook:
            entries = hook_ranges(sys.stdin.read())
            for ref_name, rev_range in entries:
                if _report_findings(f"el nombre de referencia `{ref_name}`", ref_name, terms):
                    found_any = True
                for where, text in targets(repo, rev_range):
                    if _report_findings(where, text, terms):
                        found_any = True
        else:
            rev_range = args.range if args.range is not None else "origin/main..HEAD"
            for where, text in targets(repo, rev_range):
                if _report_findings(where, text, terms):
                    found_any = True
    except (GitError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return 1 if found_any else 0


if __name__ == "__main__":
    sys.exit(main())
