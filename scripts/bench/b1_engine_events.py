# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark workflow runs, SQLite connections, and run-list scaling with fake adapters."""
from __future__ import annotations

import json
import platform
import sqlite3
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multiagent.adapters.fake import FakeAdapter
from multiagent.artifacts import ArtifactWriter
from multiagent.backends import BackendContext, build_backend
from multiagent.bundles import load_bundle
from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.db.store import Store
from multiagent.model_router import ModelRouter
from multiagent.run_request import RunRequest
from multiagent.run_service import RunService
from multiagent.services import ApplicationServices
from multiagent.worker import LocalWorker
from scripts.bench.common import arguments, commit, connection_stats, count_connections, emit, stats, workspace

WORK_DIR: Path
FIXTURES = ROOT / "tests" / "fixtures" / "backend_conformance"


def _stats(samples_ms: list[float]) -> dict[str, float]:
    return stats(samples_ms, total=True)


def _make_settings(base: Path) -> Settings:
    s = Settings(
        _env_file=None,
        local_dir=base / "local",
        worker_enabled=False,
        gemini_api_key="",
        openai_api_key="",
        openai_enabled=False,
    )
    s.ensure_directories()
    return s


def bench_db_connect_vs_persistent() -> dict[str, object]:
    base = WORK_DIR / "db_conn"
    settings = _make_settings(base)
    db = Database(settings)
    db.migrate()

    # Database.connect opens a connection and sets three PRAGMAs for each query.
    open_close_read_ms: list[float] = []
    for _ in range(300):
        t0 = time.perf_counter()
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        open_close_read_ms.append((time.perf_counter() - t0) * 1000.0)

    # Reuse one connection against the same WAL database.
    persist_conn = sqlite3.connect(str(settings.db_path), timeout=30.0)
    persist_conn.row_factory = sqlite3.Row
    persist_conn.execute("PRAGMA foreign_keys = ON;")
    persist_conn.execute("PRAGMA journal_mode = WAL;")
    persist_conn.execute("PRAGMA synchronous = NORMAL;")
    persist_read_ms: list[float] = []
    try:
        for _ in range(300):
            t0 = time.perf_counter()
            persist_conn.execute("SELECT 1").fetchone()
            persist_read_ms.append((time.perf_counter() - t0) * 1000.0)
    finally:
        persist_conn.close()

    oc_stats = _stats(open_close_read_ms)
    pr_stats = _stats(persist_read_ms)
    speedup = round(oc_stats["mean_ms"] / pr_stats["mean_ms"], 2) if pr_stats["mean_ms"] > 0 else 0.0
    return {
        "database_connect_per_call_300": oc_stats,
        "persistent_connection_300": pr_stats,
        "read_latency_ratio_connect_vs_persistent": speedup,
    }


def bench_backend_runs(backend_name: str, count: int = 25, *, quick: bool = False) -> dict[str, object]:
    base = WORK_DIR / f"backend_{backend_name}"
    settings = _make_settings(base)
    store = Store(Database(settings))
    store.db.migrate()
    bundle = load_bundle(FIXTURES, settings)
    adapter = FakeAdapter(output_schemas=bundle.output_schemas)
    router = ModelRouter(bundle.catalog, {"fake": adapter}, dry_run=True)
    sample = adapter.generate_structured(
        spec=None, prompt="", output_schema=bundle.output_schemas["ContentOutput"],
    ).parsed.model_dump(mode="json")
    ctx = BackendContext(
        store=store,
        settings=settings,
        workflows=bundle.workflows,
        prompts=bundle.prompts,
        catalog=bundle.catalog,
        artifacts=ArtifactWriter(settings),
        router=router,
        output_schemas=bundle.output_schemas,
        sample_outputs={"ContentOutput": sample},
    )
    backend = build_backend(backend_name, ctx)
    service = RunService(settings, store, engine=backend)
    worker = LocalWorker(store, backend)

    create_ms: list[float] = []
    exec_ms: list[float] = []
    get_ms: list[float] = []
    get_connections: list[int] = []
    list_connections: list[int] = []
    create_connections: list[int] = []
    total_events = 0

    tracemalloc.start()
    t_all0 = time.perf_counter()
    for idx in range(count):
        req = RunRequest(
            workflow_id="two_steps",
            project_name=f"Bench {backend_name} {idx}",
            inputs={"topic": f"topic {idx}"},
        )
        t0 = time.perf_counter()
        with count_connections() as opened:
            created = service.create_run(req)
        create_connections.append(opened[0])
        create_ms.append((time.perf_counter() - t0) * 1000.0)

        t1 = time.perf_counter()
        picked = worker.process_once()
        exec_ms.append((time.perf_counter() - t1) * 1000.0)
        if not picked:
            raise RuntimeError(f"Run {created['id']} was not processed")

        t2 = time.perf_counter()
        with count_connections() as opened:
            detail = service.get(created["id"])
        get_connections.append(opened[0])
        get_ms.append((time.perf_counter() - t2) * 1000.0)
        total_events += len(detail["events"])
        if detail["status"] != "completed":
            raise RuntimeError(f"Unexpected status {detail['status']}")

    total_wall_s = time.perf_counter() - t_all0
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Measure Store.list_runs with persisted runs and their related records.
    list_ms: list[float] = []
    for _ in range(5 if quick else 20):
        t0 = time.perf_counter()
        with count_connections() as opened:
            listed = store.list_runs(limit=count)
        list_connections.append(opened[0])
        list_ms.append((time.perf_counter() - t0) * 1000.0)
        if len(listed) != count:
            raise RuntimeError(f"Expected {count} runs, got {len(listed)}")

    result = {
        "backend": backend_name,
        "runs": count,
        "total_events": total_events,
        "runs_per_sec_end_to_end": round(count / total_wall_s, 2),
        "create_run_ms": _stats(create_ms),
        "process_once_two_steps_ms": _stats(exec_ms),
        "service_get_ms": _stats(get_ms),
        "store_list_runs_n25_ms": _stats(list_ms),
        "tracemalloc_peak_kib": round(peak_bytes / 1024.0, 2),
    }
    result["sqlite_connections_per_operation"] = {
        "create_run": connection_stats(create_connections),
        "service_get": connection_stats(get_connections),
        "store_list_runs": connection_stats(list_connections),
    }
    return result


def _close_app(app: ApplicationServices) -> None:
    app.runs.close()


def bench_facade_cold_start(*, quick: bool = False) -> dict[str, object]:
    cold_no_bundles: list[float] = []
    cold_with_fixtures: list[float] = []
    for idx in range(2 if quick else 5):
        s0 = _make_settings(WORK_DIR / f"cold0_{idx}")
        t0 = time.perf_counter()
        app0 = ApplicationServices.create(s0, dry_run=True)
        _ = app0.tasks.list_groups()
        _close_app(app0)
        cold_no_bundles.append((time.perf_counter() - t0) * 1000.0)

        s1 = _make_settings(WORK_DIR / f"cold5_{idx}")
        bundles = {f"oficina_{k}": FIXTURES for k in range(5)}
        t1 = time.perf_counter()
        app1 = ApplicationServices.create(s1, bundles=bundles, dry_run=True)
        _ = app1.runs.list(limit=10)
        _close_app(app1)
        cold_with_fixtures.append((time.perf_counter() - t1) * 1000.0)

    return {
        "application_services_0_bundles_plus_tasks_ms": _stats(cold_no_bundles),
        "application_services_5_bundles_plus_runs_list_ms": _stats(cold_with_fixtures),
    }


def bench_list_runs_500(*, quick: bool = False) -> dict[str, object]:
    settings = _make_settings(WORK_DIR / "list_500")
    store = Store(Database(settings))
    store.db.migrate()
    count = 10 if quick else 500
    for index in range(count):
        store.create_run(RunRequest(workflow_id="two_steps", project_name="List benchmark",
                                    inputs={"topic": f"topic {index}"}), 10, 3600)
    samples = []
    connections = []
    for _ in range(2 if quick else 10):
        start = time.perf_counter()
        with count_connections() as opened:
            rows = store.list_runs(limit=count)
        samples.append((time.perf_counter() - start) * 1000)
        connections.append(opened[0])
        if len(rows) != count:
            raise RuntimeError("Incomplete run list")
    return {"runs": count, "list_runs_ms": _stats(samples),
            "sqlite_connections": connection_stats(connections)}


def bench_get_across_bundles(*, quick: bool = False) -> list[dict[str, object]]:
    rows = []
    for bundles_count in (0, 5, 20):
        settings = _make_settings(WORK_DIR / f"get_bundles_{bundles_count}")
        bundles = {f"package_{index:02d}": FIXTURES for index in range(bundles_count)}
        app = ApplicationServices.create(settings, bundles=bundles, dry_run=True)
        try:
            if bundles_count:
                for index in ((bundles_count - 1,) if quick else range(bundles_count)):
                    created = app.runs.create(RunRequest(workflow_id="two_steps", project_name="Lookup",
                                                         inputs={"topic": f"sample {index}"}),
                                                  bundle=f"package_{index:02d}")
            else:
                created = app.runs.create(RunRequest(workflow_id="social_post", project_name="Lookup",
                                                     inputs={"topic": "sample"}))
            samples = []
            connections = []
            for _ in range(2 if quick else 10):
                start = time.perf_counter()
                with count_connections() as opened:
                    detail = app.runs.get(created["id"])
                samples.append((time.perf_counter() - start) * 1000)
                connections.append(opened[0])
                if detail["id"] != created["id"]:
                    raise RuntimeError("Incorrect run")
            rows.append({"bundles": bundles_count, "runs_get_ms": _stats(samples),
                         "sqlite_connections": connection_stats(connections)})
        finally:
            _close_app(app)
    return rows


def main() -> None:
    global WORK_DIR
    args = arguments("Benchmark workflow runs and SQLite access")
    with workspace(args.work_dir, "bench-b1-") as WORK_DIR:
        results = {
            "benchmark": "b1_engine_events",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "commit": commit(),
            "work_dir": "custom" if args.work_dir else "system temp",
            "db_connection_overhead": bench_db_connect_vs_persistent(),
            "native_backend_25_runs": bench_backend_runs("native", count=2 if args.quick else 25, quick=args.quick),
            "mock_backend_25_runs": bench_backend_runs("mock", count=2 if args.quick else 25, quick=args.quick),
            "facade_startup": bench_facade_cold_start(quick=args.quick),
            "list_runs_500": bench_list_runs_500(quick=args.quick),
            "runs_get_by_bundle_count": bench_get_across_bundles(quick=args.quick),
        }
    emit(results, args.out)


if __name__ == "__main__":
    main()
