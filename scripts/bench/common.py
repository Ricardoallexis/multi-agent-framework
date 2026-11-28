# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared command-line and measurement helpers for the benchmarks."""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


def stats(samples: list[float], *, total: bool = False) -> dict[str, float]:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    result = {
        "count": len(ordered), "mean_ms": round(statistics.mean(ordered), 4),
        "median_ms": round(statistics.median(ordered), 4),
        "p95_ms": round(ordered[index], 4), "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
    }
    if total:
        result["total_ms"] = round(sum(ordered), 3)
    return result


def connection_stats(samples: list[int]) -> dict[str, float]:
    """Summarize numbers of opened connections without time-unit labels."""
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    return {"samples": len(ordered), "mean": round(statistics.mean(ordered), 2),
            "median": statistics.median(ordered), "p95": ordered[index],
            "min": ordered[0], "max": ordered[-1]}


def arguments(description: str, *, large: bool = False) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--quick", action="store_true", help="Run a small smoke workload")
    if large:
        parser.add_argument("--large", action="store_true", help="Include 1000 and 2000 task graphs")
        parser.add_argument("--max-tasks", type=int, choices=(1000, 2000), default=2000,
                            help="Largest graph with --large (default: 2000)")
    parser.add_argument("--work-dir", type=Path, help="Parent of a temporary working directory")
    parser.add_argument("--out", type=Path, help="Write JSON here instead of standard output")
    return parser.parse_args()


@contextmanager
def workspace(parent: Path | None, prefix: str):
    if parent is not None:
        parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as folder:
        yield Path(folder)


def emit(report: dict, output: Path | None) -> None:
    document = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if output is None:
        print(document, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(document, encoding="utf-8")


def commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@contextmanager
def count_connections():
    """Count SQLite opens during one operation, including nested store calls."""
    original = sqlite3.connect
    count = [0]

    def counted(*args, **kwargs):
        count[0] += 1
        return original(*args, **kwargs)

    with patch.object(sqlite3, "connect", counted):
        yield count
