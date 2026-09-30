-- ORQ-2 (contract in ORQ-T10): group default gates and stored gate results.
-- Additive only. A task's own gate lives in tasks.gate_json (migration 004).
ALTER TABLE task_groups ADD COLUMN default_gate_json TEXT NOT NULL DEFAULT '[]';

-- One row per check of one gate run; output_excerpt keeps the last 4000 characters.
CREATE TABLE IF NOT EXISTS gate_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    attempt INTEGER NOT NULL,
    run INTEGER NOT NULL,
    position INTEGER NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    exit_code INTEGER,
    duration_ms INTEGER,
    output_excerpt TEXT NOT NULL DEFAULT '',
    report_ref TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    UNIQUE (task_id, attempt, run, position)
);
CREATE INDEX IF NOT EXISTS idx_gate_results_task ON gate_results(task_id, attempt, run);
