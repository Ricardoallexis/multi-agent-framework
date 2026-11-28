# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark task graphs, envelopes, blobs, and text guards on synthetic data."""
from __future__ import annotations

import json
import platform
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multiagent import document_trust, egress_guard, secret_gate
from multiagent.config import Settings
from multiagent.services import ApplicationServices
from scripts.bench.common import arguments, commit, connection_stats, count_connections, emit, stats, workspace

WORK_DIR: Path


def _stats(samples_ms: list[float]) -> dict[str, float]:
    return stats(samples_ms)


def _close_app(app: ApplicationServices) -> None:
    app.runs.close()


def bench_task_graph_scaling(*, quick: bool = False, large: bool = False,
                             max_tasks: int = 2000) -> list[dict[str, object]]:
    """Measure layered DAG creation, listing, and envelope reads at increasing sizes."""
    rows: list[dict[str, object]] = []
    sizes = (10, 50) if quick else (tuple(n for n in (10, 50, 200, 500, 1000, 2000)
                                     if n <= max_tasks) if large else (10, 50, 200, 500))
    for n in sizes:
        base = WORK_DIR / f"dag_{n}"
        settings = Settings(
            _env_file=None,
            local_dir=base / "local",
            worker_enabled=False,
            gemini_api_key="",
            openai_api_key="",
            openai_enabled=False,
        )
        tracemalloc.start()
        t_init0 = time.perf_counter()
        app = ApplicationServices.create(settings, dry_run=True)
        tasks = app.tasks
        tasks.create_group("G", f"Scale {n}", actor="user", authorized=True)

        create_ms: list[float] = []
        create_connections: list[int] = []
        width = 5
        for i in range(n):
            deps = []
            if i >= width:
                deps = [f"T{i - width:04d}"]
            t0 = time.perf_counter()
            with count_connections() as opened:
                tasks.create_task("G", f"T{i:04d}", f"Task {i}", actor="user", depends_on=deps)
            create_connections.append(opened[0])
            create_ms.append((time.perf_counter() - t0) * 1000.0)

        t_list0 = time.perf_counter()
        with count_connections() as opened:
            listed = tasks.list_tasks(group_id="G")
        list_connections = opened[0]
        list_ms = (time.perf_counter() - t_list0) * 1000.0

        env_ms: list[float] = []
        for k in (0, n // 2, n - 1):
            t0 = time.perf_counter()
            _ = app.envelopes.get_envelope(f"T{k:04d}")
            env_ms.append((time.perf_counter() - t0) * 1000.0)

        total_ms = (time.perf_counter() - t_init0) * 1000.0
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        _close_app(app)

        rows.append({
            "tasks_n": n,
            "listed_count": len(listed),
            "create_task_ms": _stats(create_ms),
            "list_tasks_ms": round(list_ms, 3),
            "get_envelope_sample_ms": _stats(env_ms),
            "total_build_and_read_ms": round(total_ms, 2),
            "tracemalloc_peak_kib": round(peak_bytes / 1024.0, 2),
            "sqlite_connections_per_operation": {
                "create_task": connection_stats(create_connections),
                "list_tasks": list_connections,
            },
        })
    return rows


def bench_envelopes_and_blobs() -> dict[str, object]:
    """Run 50 tasks through blob storage, result submission, and acceptance."""
    base = WORK_DIR / "env_blobs"
    settings = Settings(
        _env_file=None,
        local_dir=base / "local",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    app = ApplicationServices.create(settings, dry_run=True)
    app.tasks.create_group("ENV", "Envelopes", actor="user", authorized=True)
    payload_bytes = (b"diff --git a/mod.py b/mod.py\n+line\n" * 480)[:16384]

    blob_ms: list[float] = []
    submit_ms: list[float] = []
    transition_connections: list[int] = []
    for i in range(50):
        tid = f"E{i:03d}"
        app.tasks.create_task("ENV", tid, f"Env task {i}", actor="user")
        with count_connections() as opened:
            app.tasks.transition(tid, "claim", actor="agent:bench")
        transition_connections.append(opened[0])
        app.tasks.transition(tid, "start", actor="agent:bench")

        content = payload_bytes + f"\n# {i}\n".encode("utf-8")
        t0 = time.perf_counter()
        blob = app.envelopes.put_blob(content, actor="agent:bench", media_type="text/x-diff")
        blob_ms.append((time.perf_counter() - t0) * 1000.0)

        t1 = time.perf_counter()
        app.envelopes.submit_result(
            tid,
            {
                "schema": "multiagent.task_result/1",
                "base_commit": "abcdef1",
                "patch": {"blob": f"sha256:{blob['sha256']}", "files": ["src/mod.py"]},
                "summary": f"Completed benchmark task {i}",
                "decisions": ["kept interface stable"],
                "contract_changes": "",
                "tests_not_run": [],
                "requests": [],
            },
            actor="agent:bench",
        )
        submit_ms.append((time.perf_counter() - t1) * 1000.0)
        app.tasks.transition(tid, "accept", actor="user")

    _close_app(app)
    return {
        "tasks_with_16kib_blobs": 50,
        "put_blob_ms": _stats(blob_ms),
        "submit_result_ms": _stats(submit_ms),
        "sqlite_connections_per_operation": {"transition": connection_stats(transition_connections)},
    }


def bench_security_guards_cpu() -> list[dict[str, object]]:
    """Measure CPU time of three text guards at 1, 10, and 50 KiB."""
    prov = document_trust.Provenance(
        kind=document_trust.SourceKind.DOCUMENT.value,
        ref="doc://bench.md",
        label="Benchmark",
    )
    sample_block = (
        "Monthly architecture review with no real secrets, contact info@example.com "
        "and a technical sample paragraph for deterministic screening.\n\n"
    )
    results: list[dict[str, object]] = []
    for kb in (1, 10, 50):
        target_len = kb * 1024
        text = (sample_block * ((target_len // len(sample_block)) + 1))[:target_len]
        egress_ms: list[float] = []
        secret_ms: list[float] = []
        trust_ms: list[float] = []
        for _ in range(15):
            t0 = time.perf_counter()
            _ = egress_guard.findings(text, include_entropy=True)
            egress_ms.append((time.perf_counter() - t0) * 1000.0)

            t1 = time.perf_counter()
            _ = secret_gate.scan_text(text)
            secret_ms.append((time.perf_counter() - t1) * 1000.0)

            t2 = time.perf_counter()
            _ = document_trust.screen(text, prov, policy=document_trust.DetectionPolicy.QUARANTINE_WARN)
            trust_ms.append((time.perf_counter() - t2) * 1000.0)

        results.append({
            "size_kb": kb,
            "chars": len(text),
            "egress_findings_with_entropy_ms": _stats(egress_ms),
            "secret_gate_scan_ms": _stats(secret_ms),
            "document_trust_screen_ms": _stats(trust_ms),
        })
    return results


def main() -> None:
    global WORK_DIR
    args = arguments("Benchmark task graph and guard workloads", large=True)
    with workspace(args.work_dir, "bench-b2-") as WORK_DIR:
        results = {
            "benchmark": "b2_task_graph",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "commit": commit(),
            "work_dir": "custom" if args.work_dir else "system temp",
            "task_graph_scaling": bench_task_graph_scaling(quick=args.quick, large=args.large,
                                                            max_tasks=args.max_tasks),
            "envelopes_and_blobs": bench_envelopes_and_blobs(),
            "security_guards_cpu": bench_security_guards_cpu(),
        }
    emit(results, args.out)


if __name__ == "__main__":
    main()
