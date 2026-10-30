# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Check the executor pin manifest, and optionally what is installed against it (AXI-T07).

Offline and read-only. It reads the manifest (``catalog/executor-pins.yaml`` by default) and, with ``--observed``, a
registry of the evidence the operator collected by hand in the maintenance window; it never runs an executor, asks one
for its version, imports a third-party package, uses the network, installs, updates or writes anything.

Without ``--observed`` it validates the manifest: form, the closed list of executors, the acceptance window of every
pin, and it reports the executors that are not pinned yet (an error with ``--require-pins``) and any candidate waiting
for a person. With ``--observed`` it also compares each executor with its pin: a different version or digest, a missing
or malformed value and an executor with no pin are errors. Nothing here accepts a candidate or changes a pin.

Usage::

    python scripts/check_executor_pins.py                                   # validate catalog/executor-pins.yaml
    python scripts/check_executor_pins.py --manifest pins.yaml --observed observed.yaml
    python scripts/check_executor_pins.py --only claude_cli --observed observed.yaml

Exit code 0 when everything checked is fine, 1 on any error, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multiagent import executor_pins as pins  # noqa: E402

DEFAULT_MANIFEST = ROOT / "catalog" / "executor-pins.yaml"


def _read_failure(label: str, exc: Exception) -> list[str]:
    """Errors for a file that could not be read or is invalid: the problems, never the file's content."""
    if isinstance(exc, pins.PinManifestError):
        return [f"{label}: {issue}" for issue in exc.issues]
    return [f"{label}: cannot be read ({type(exc).__name__})"]


def check(manifest_path: Path, observed_path: Path | None = None, *, now: datetime,
          only: tuple[str, ...] | None = None, require_pins: bool = False) -> tuple[list[str], list[str]]:
    """``(errors, notes)`` for the manifest and, with ``observed_path``, for the installed evidence against it."""
    try:
        manifest = pins.load_manifest(manifest_path)
    except (OSError, pins.PinManifestError) as exc:
        return _read_failure("manifest", exc), []
    errors: list[str] = []
    notes: list[str] = []
    selected = tuple(only) if only else pins.EXECUTOR_IDS
    if observed_path is not None:
        try:
            observed = pins.load_observed(observed_path)
        except (OSError, pins.PinManifestError) as exc:
            return _read_failure("observed", exc), []
        for check_ in pins.evaluate(manifest, observed, now, selected):
            if check_.status == pins.MATCHED:
                notes.append(f"{check_.executor_id}: matches its pin")
                continue
            detail = f"{check_.status}: {check_.reason}"
            if check_.status == pins.DRIFT and check_.candidate not in (pins.CANDIDATE_NONE, pins.CANDIDATE_OTHER):
                detail += f"; it is the candidate on record (window {check_.candidate.replace('_', ' ')})"
            errors.append(f"{check_.executor_id}: {detail}")
        return errors, notes
    for executor_id in selected:
        record = manifest.get(executor_id)
        if record is None or record.pin is None:
            (errors if require_pins else notes).append(f"{executor_id}: no pin has been accepted yet")
        else:
            notes.append(f"{executor_id}: pinned (accepted by {record.pin.accepted_by})")
        if record is not None and record.candidate is not None:
            state = pins.window_state(record.candidate.first_observed_at, now).replace("_", " ")
            notes.append(f"{executor_id}: a candidate waits for a person (acceptance window {state})")
    return errors, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="the pin manifest (default: the catalog's)")
    parser.add_argument("--observed", type=Path, help="a registry of the evidence observed by hand; compares it with the pins")
    parser.add_argument("--only", action="append", choices=pins.EXECUTOR_IDS, help="check only this executor (repeatable)")
    parser.add_argument("--require-pins", action="store_true", help="an executor with no pin is an error even without --observed")
    parser.add_argument("--now", help="the moment to judge the windows at, ISO 8601 with a time zone (default: now)")
    args = parser.parse_args(argv)
    if args.now is None:
        now = datetime.now(timezone.utc)
    else:
        try:
            now = datetime.fromisoformat(args.now)
        except ValueError:
            now = None
        if now is None or now.tzinfo is None:
            parser.error("--now must be an ISO 8601 moment with a time zone")
    errors, notes = check(args.manifest, args.observed, now=now, only=tuple(args.only or ()) or None,
                          require_pins=args.require_pins)
    for note in notes:
        print(f"  {note}")
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        print(f"Executor pins: {len(errors)} problem(s).")
        return 1
    print("Executor pins OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
