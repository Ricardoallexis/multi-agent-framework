# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Smoke checks for the benchmark command-line contract."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_quick_benchmarks(tmp_path: Path):
    expected = {
        "b1_engine_events": ("db_connection_overhead", "list_runs_500", "runs_get_by_bundle_count"),
        "b2_task_graph": ("task_graph_scaling", "envelopes_and_blobs", "security_guards_cpu"),
        "b3_concurrency": ("polling_vs_push", "local_worker_commit_to_pickup_ms", "events_stream_commit_to_line_ms"),
    }
    before = subprocess.check_output(["git", "status", "--short", "--untracked-files=all"], cwd=ROOT)
    for name, keys in expected.items():
        output = tmp_path / f"{name}.json"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "bench" / f"{name}.py"),
                        "--quick", "--work-dir", str(tmp_path), "--out", str(output)],
                       cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True, timeout=90)
        report = json.loads(output.read_text(encoding="utf-8"))
        assert all(key in report for key in keys)
        assert report["work_dir"] == "custom"
        if name == "b1_engine_events":
            assert report["list_runs_500"]["runs"] > 0
            assert report["runs_get_by_bundle_count"][-1]["bundles"] == 20
            assert report["mock_backend_25_runs"]["sqlite_connections_per_operation"]["service_get"]["mean"] > 0
        elif name == "b2_task_graph":
            assert report["task_graph_scaling"][-1]["sqlite_connections_per_operation"]["list_tasks"] > 0
            assert report["envelopes_and_blobs"]["sqlite_connections_per_operation"]["transition"]["mean"] > 0
        else:
            assert report["local_worker_commit_to_pickup_ms"]["count"] > 0
            assert report["events_stream_commit_to_line_ms"]["count"] > 0
            assert "expected_mean_delay_sse_0_50s_ms" in report["derived"]
    after = subprocess.check_output(["git", "status", "--short", "--untracked-files=all"], cwd=ROOT)
    assert after == before
    old_file, new_file = tmp_path / "before.json", tmp_path / "after.json"
    old_file.write_text('{"timing_ms": 2}', encoding="utf-8")
    new_file.write_text('{"timing_ms": 4}', encoding="utf-8")
    comparison = subprocess.check_output([sys.executable, str(ROOT / "scripts" / "bench" / "compare.py"),
                                          str(old_file), str(new_file)], cwd=ROOT, text=True)
    assert "| timing_ms | 2 | 4 | 2.00x |" in comparison
    assert "Warning" not in comparison
