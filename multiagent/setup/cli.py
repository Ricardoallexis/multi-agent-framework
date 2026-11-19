"""CLI wiring kept apart from the shared CLI module."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from ..config import LOCAL_ROOT

from .detect import CONTEXTS, detect


def guided_command(args) -> None:
    from .guided import guide_step

    print(json.dumps(guide_step(args.app, args.setup_action, context="user_console"), ensure_ascii=False, indent=2))


def command(args) -> None:
    # Use the existing LOCAL_DIR override/default without loading .env (which can
    # contain provider credentials). Never inspect an existing evidence file.
    folder = Path(os.environ.get("LOCAL_DIR") or LOCAL_ROOT).resolve() / "setup-evidence"
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or folder.is_junction():
        raise ValueError("El directorio local de evidencia debe ser una carpeta propia.")
    destination = folder / (uuid4().hex + ".jsonl")

    def announce(plan):
        print(json.dumps({"before_action": plan}, ensure_ascii=False), file=sys.stderr, flush=True)

    # Exclusive creation and flush after every step; failure stops further probes.
    with destination.open("x", encoding="utf-8") as evidence:
        def record(event):
            evidence.write(json.dumps(event, ensure_ascii=False) + "\n")
            evidence.flush()

        report = detect(context=args.context, claude_path=args.claude_path,
                        include_antigravity=args.include_antigravity,
                        before_step=announce, after_step=record)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("setup", help="CLI detection and session status with local evidence (Windows)")
    parser.add_argument("--context", choices=CONTEXTS, default="user_console")
    parser.add_argument("--claude-path", help="Explicit absolute Claude executable outside the working folder")
    parser.add_argument("--include-antigravity", action="store_true", help="Informational detection only")
    parser.set_defaults(func=command)
    steps = parser.add_subparsers(dest="setup_action")
    for name in ("install", "login"):
        item = steps.add_parser(name, help=f"Open a visible console for the {name} step, then recheck")
        item.add_argument("app", choices=("claude", "codex", "copilot") + (("agy",) if name == "login" else ()))
        item.set_defaults(func=guided_command)
