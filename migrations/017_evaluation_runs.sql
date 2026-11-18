-- S10.C2: saved evaluation runs and one baseline per evaluation set. Dates are UTC ISO 8601 strings.
CREATE TABLE IF NOT EXISTS evaluation_runs (
    id TEXT PRIMARY KEY,
    set_name TEXT NOT NULL,
    set_version TEXT NOT NULL,
    set_sha256 TEXT NOT NULL,
    live INTEGER NOT NULL CHECK (live IN (0,1)),
    fingerprint_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    score REAL NOT NULL,
    actor TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_runs_set ON evaluation_runs(set_name, created_at);

CREATE TABLE IF NOT EXISTS evaluation_baselines (
    set_name TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES evaluation_runs(id),
    actor TEXT,
    created_at TEXT NOT NULL
);
