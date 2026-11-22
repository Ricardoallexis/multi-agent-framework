# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Install the pre-push private-terms git hook in the active repository.

Writes ``pre-push`` inside the directory returned by
``git rev-parse --git-path hooks`` using ``sys.executable`` (the active
environment's signed Python binary, never a generated ``.exe`` wrapper).
Existing hooks carrying ``HOOK_MARKER`` are updated in place; foreign hooks
without the marker are never modified and exit with code 1.
"""
from __future__ import annotations

import argparse
import stat
import subprocess
import sys
from pathlib import Path

HOOK_MARKER = "# maf-private-terms-hook/1"
SCRIPT_ROOT = Path(__file__).resolve().parents[1]


def _resolve_hooks_dir(repo_cwd: Path) -> Path:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_cwd),
                "-c",
                "core.fsmonitor=false",
                "rev-parse",
                "--git-path",
                "hooks",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"git invocation failed ({type(exc).__name__})") from exc
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed with exit code {result.returncode}")
    raw_path = Path(result.stdout.strip())
    return raw_path if raw_path.is_absolute() else (repo_cwd / raw_path)


def build_hook_script(python_executable: str, checker_script: Path, terms_path: Path) -> str:
    """Render the POSIX ``pre-push`` hook script."""
    return (
        "#!/bin/sh\n"
        f"{HOOK_MARKER}\n"
        f'exec "{python_executable}" "{checker_script}" --hook --terms "{terms_path}"\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--terms",
        type=Path,
        required=True,
        help="path to the private terms file",
    )
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    terms_path = args.terms if args.terms.is_absolute() else (cwd / args.terms)
    terms_path = terms_path.resolve()
    checker_script = (SCRIPT_ROOT / "scripts" / "check_private_terms.py").resolve()

    try:
        hooks_dir = _resolve_hooks_dir(cwd)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-push"
    if hook_path.exists() or hook_path.is_symlink():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if HOOK_MARKER not in existing:
            print(
                "ERROR: an existing pre-push hook does not carry the private-terms marker; leaving it untouched.",
                file=sys.stderr,
            )
            return 1

    content = build_hook_script(sys.executable, checker_script, terms_path)
    hook_path.write_text(content, encoding="utf-8", newline="\n")
    try:
        mode = hook_path.stat().st_mode
        hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    print(f"Installed pre-push hook at {hook_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
