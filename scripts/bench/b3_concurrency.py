# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark harmless Python child processes, SQLite contention, and wakeup latency."""
from __future__ import annotations

import asyncio
import json
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multiagent.config import Settings
from multiagent.db.database import Database
from multiagent.executors import process
from multiagent.services import ApplicationServices
from multiagent.run_request import RunRequest
from multiagent.db.store import Store
from multiagent.worker import LocalWorker
from multiagent.gui import events
from scripts.bench.common import arguments, commit, emit, stats, workspace

WORK_DIR: Path


def _stats(samples_ms: list[float]) -> dict[str, float]:
    return stats(samples_ms)


def bench_subprocess_supervision(*, quick: bool = False) -> dict[str, object]:
    cwd = WORK_DIR / "proc_cwd"
    cwd.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-c", "print('ok')"]

    seq_ms: list[float] = []
    for _ in range(2 if quick else 15):
        t0 = time.perf_counter()
        out = process.run(cmd, cwd=cwd, stdin_text=None, timeout_seconds=10.0)
        dt = (time.perf_counter() - t0) * 1000.0
        if out.exit_code != 0 or "ok" not in out.stdout_tail:
            raise RuntimeError(f"Unexpected process outcome: {out}")
        seq_ms.append(dt)

    # Supervise harmless Python children with process.run.
    t_par0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(
            lambda _: process.run(cmd, cwd=cwd, stdin_text=None, timeout_seconds=10.0),
            range(2 if quick else 16),
        ))
    par_wall_ms = (time.perf_counter() - t_par0) * 1000.0
    if any(o.exit_code != 0 for o in outcomes):
        raise RuntimeError("Concurrent process run failed")

    return {
        "sequential_15_spawns_ms": _stats(seq_ms),
        "concurrent_16_spawns_8_workers_total_ms": round(par_wall_ms, 2),
        "concurrent_effective_ms_per_spawn": round(par_wall_ms / (2 if quick else 16), 2),
    }


def bench_polling_vs_push_wakeup() -> dict[str, object]:
    """Compare a simulated poll window with a local condition notification."""
    poll_latencies_ms: list[float] = []
    poll_interval = 0.25  # Synthetic interval, not the worker's real 0.75 seconds.
    push_latencies_ms: list[float] = []

    cond = threading.Condition()
    signaled = False
    t_sent = 0.0

    def push_consumer():
        nonlocal signaled
        with cond:
            while not signaled:
                cond.wait()
            dt = (time.perf_counter() - t_sent) * 1000.0
            signaled = False
            push_latencies_ms.append(dt)

    for _ in range(25):
        signaled = False
        th = threading.Thread(target=push_consumer, daemon=True)
        th.start()
        time.sleep(0.002)
        with cond:
            t_sent = time.perf_counter()
            signaled = True
            cond.notify()
        th.join(timeout=2.0)

    # Measure a synthetic poll window and retain the historical derived figures.
    for offset in (0.02, 0.08, 0.14, 0.20):
        tick = threading.Event()
        ready = threading.Event()

        def poller():
            while not tick.is_set():
                time.sleep(poll_interval)
            ready.set()

        p = threading.Thread(target=poller, daemon=True)
        p.start()
        time.sleep(offset)
        ts = time.perf_counter()
        tick.set()
        ready.wait(timeout=2.0)
        poll_latencies_ms.append((time.perf_counter() - ts) * 1000.0)
        p.join(timeout=2.0)

    return {
        "in_process_condition_push_25_ms": _stats(push_latencies_ms),
        "simulated_0_25s_poll_4_samples_ms": _stats(poll_latencies_ms),
        "simulated": {"poll_interval_seconds": poll_interval},
    }


def bench_sqlite_wal_worker_contention(*, quick: bool = False) -> dict[str, object]:
    """Measure concurrent TaskServices writes against one WAL database."""
    out_rows: list[dict[str, object]] = []
    for n_workers in ((2,) if quick else (1, 4, 8)):
        base = WORK_DIR / f"wal_{n_workers}"
        settings = Settings(
            _env_file=None,
            local_dir=base / "local",
            worker_enabled=False,
            gemini_api_key="",
            openai_api_key="",
            openai_enabled=False,
        )
        app = ApplicationServices.create(settings, dry_run=True)
        tasks = app.tasks
        total_tasks = 4 if quick else 48
        tasks.create_group("G", "Contention", actor="user", authorized=True)
        for i in range(total_tasks):
            tasks.create_task("G", f"T{i:03d}", f"Task {i}", actor="user")

        errors: list[str] = []
        lat_ms: list[float] = []
        lock = threading.Lock()
        idx_box = [0]

        def worker_fn(wid: int) -> None:
            actor = f"agent:w{wid}"
            while True:
                with lock:
                    if idx_box[0] >= total_tasks:
                        return
                    tid = f"T{idx_box[0]:03d}"
                    idx_box[0] += 1
                t0 = time.perf_counter()
                try:
                    tasks.transition(tid, "claim", actor=actor)
                    tasks.transition(tid, "start", actor=actor)
                    tasks.transition(tid, "submit", actor=actor)
                    tasks.transition(tid, "accept", actor="user")
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        errors.append(f"{tid}:{type(exc).__name__}:{exc}")
                finally:
                    dt = (time.perf_counter() - t0) * 1000.0
                    with lock:
                        lat_ms.append(dt)

        t0 = time.perf_counter()
        threads = [threading.Thread(target=worker_fn, args=(w,)) for w in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
        wall_s = time.perf_counter() - t0
        app.runs.close()

        out_rows.append({
            "threads": n_workers,
            "tasks_completed": total_tasks,
            "transitions_total": total_tasks * 4,
            "errors": errors,
            "error_count": len(errors),
            "wall_ms": round(wall_s * 1000.0, 2),
            "tasks_per_sec": round(total_tasks / wall_s, 2),
            "per_task_4_transitions_ms": _stats(lat_ms),
        })
    return {"sqlite_wal_contention": out_rows}


def bench_local_worker_pickup(*, quick: bool = False) -> dict[str, float]:
    """Measure commit to claim using the real worker loop and a no-op engine."""
    settings = Settings(_env_file=None, local_dir=WORK_DIR / "pickup" / "local",
                        worker_enabled=False, gemini_api_key="", openai_api_key="", openai_enabled=False)
    settings.ensure_directories()
    database = Database(settings)
    database.migrate()
    store = Store(database)
    committed = {}
    picked = {}
    ready = threading.Event()
    original_claim = store.claim_queued_run

    def claim(*args, **kwargs):
        run = original_claim(*args, **kwargs)
        if run:
            picked[run["id"]] = time.perf_counter()
            ready.set()
        return run

    store.claim_queued_run = claim
    database.event_listeners.append(lambda run_id, kind, payload, seq:
                                    committed.setdefault(run_id, time.perf_counter()) if kind == "workflow_started" else None)

    class NoOpEngine:
        def check_approval_deadlines(self):
            pass

        def process_run(self, run_id):
            with store.db.connect() as conn:
                conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
                conn.commit()

    worker = LocalWorker(store, NoOpEngine())
    samples = []
    worker.start()
    try:
        for index in range(2 if quick else 5):
            ready.clear()
            run = store.create_run(RunRequest(workflow_id="two_steps", project_name="Pickup",
                                              inputs={"topic": f"sample {index}"}), 10, 3600)
            deadline = time.monotonic() + 3
            while run["id"] not in picked and time.monotonic() < deadline:
                ready.wait(timeout=0.1)
            if run["id"] not in picked:
                raise RuntimeError("Worker did not claim the queued run")
            samples.append((picked[run["id"]] - committed[run["id"]]) * 1000)
    finally:
        worker.stop()
    return _stats(samples)


def bench_sse_delivery(*, quick: bool = False) -> dict[str, float]:
    """Measure event commit to the next line from the real SSE generator."""
    settings = Settings(_env_file=None, local_dir=WORK_DIR / "sse" / "local",
                        worker_enabled=False, gemini_api_key="", openai_api_key="", openai_enabled=False)
    app = ApplicationServices.create(settings, dry_run=True)
    store = app.system.store
    run = store.create_run(RunRequest(workflow_id="social_post", project_name="Stream",
                                      inputs={"topic": "sample"}), 10, 3600)

    async def measure():
        samples = []
        for index in range(2 if quick else 5):
            with store.db.connect() as conn:
                cursor = conn.execute("SELECT COALESCE(MAX(id), 0) FROM run_events").fetchone()[0]
            source = events.stream(store, after=cursor, heartbeat=30, poll=0.5, max_seconds=3)
            await anext(source)  # connected
            pending = asyncio.create_task(anext(source))
            await asyncio.sleep(0.03)
            store.db.log_event(run["id"], "benchmark_ping", {"sample": index})
            committed_at = time.perf_counter()
            try:
                line = await asyncio.wait_for(pending, timeout=2)
                if "event: benchmark_ping" not in line:
                    raise RuntimeError("Unexpected SSE event")
                samples.append((time.perf_counter() - committed_at) * 1000)
            finally:
                await source.aclose()
        return _stats(samples)

    try:
        return asyncio.run(measure())
    finally:
        app.runs.close()


def main() -> None:
    global WORK_DIR
    args = arguments("Benchmark concurrency and wakeup latency")
    with workspace(args.work_dir, "bench-b3-") as WORK_DIR:
        results = {
            "benchmark": "b3_concurrency",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "commit": commit(),
            "work_dir": "custom" if args.work_dir else "system temp",
            "subprocess_supervision": bench_subprocess_supervision(quick=args.quick),
            "polling_vs_push": bench_polling_vs_push_wakeup(),
            **bench_sqlite_wal_worker_contention(quick=args.quick),
            "local_worker_commit_to_pickup_ms": bench_local_worker_pickup(quick=args.quick),
            "events_stream_commit_to_line_ms": bench_sse_delivery(quick=args.quick),
            "derived": {"expected_mean_delay_local_worker_0_75s_ms": 375.0,
                        "expected_mean_delay_sse_0_50s_ms": 250.0},
        }
    emit(results, args.out)


if __name__ == "__main__":
    main()
