# Local performance benchmarks

Run from the repository root with `PYTHONPATH` set to that root. These scripts use synthetic inputs, fake adapters, and temporary directories. They print JSON to standard output by default; `--out PATH` writes JSON to a file. `--work-dir PATH` selects the parent directory for temporary work. `--quick` runs a smoke workload. B2 also accepts `--large` to measure 1000 and 2000 task graphs, and `--large --max-tasks 1000` for a bounded run.

```sh
python scripts/bench/b1_engine_events.py --out b1.json
python scripts/bench/b2_task_graph.py --out b2.json
python scripts/bench/b3_concurrency.py --out b3.json
python scripts/bench/compare.py before.json after.json
```

Every `*_ms` statistics object reports count, mean, median, p95, minimum, and maximum in milliseconds. B1 reports SQLite open/close overhead, native and Mock two-step runs, facade startup, run listing at 500 rows, run lookup across 0/5/20 registered packages, and SQLite connections opened by each operation. B2 reports task creation, listing, envelope lookup, SQLite connection counts, blob and result storage, and three text guard CPU costs. B3 reports harmless Python process supervision, WAL contention, a synthetic 0.25-second poll, in-process condition signaling, a real `LocalWorker` pickup delay, and delivery through the real `events.stream` generator. The historical `expected_mean_delay_*` values are calculated references in `derived`; they are not observations.

Each report records whether it used the system temporary folder or a `--work-dir`. Compare only reports made with the same folder: on the Windows machine below, the system temporary folder roughly doubled the timings that write to disk (for example `process_once` and one-thread WAL throughput), while a folder on the repository's drive matched the historical values within 25 percent. `compare.py` warns when the two folders differ.

The historical comparison was measured on 2026-11-27 at commit `b1c9375`, Windows 11 build 26300, Python 3.12.10. Its JSON reports are preserved outside the tracked benchmark directory. Results describe that machine and workload; they are not performance promises.

| Historical metric | Value |
| --- | ---: |
| B1 native end-to-end throughput | 3.98 runs/s |
| B1 Mock end-to-end throughput | 5.10 runs/s |
| B1 native `store_list_runs_n25_ms.mean_ms` | 3.0252 ms |
| B2 500-task `create_task_ms.mean_ms` | 48.4499 ms |
| B2 500-task `list_tasks_ms` | 545.958 ms |
| B3 1-thread WAL throughput | 23.93 tasks/s |
| B3 4-thread WAL throughput | 43.74 tasks/s |
| B3 8-thread WAL throughput | 31.34 tasks/s |
